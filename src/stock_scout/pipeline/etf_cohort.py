"""Weinstein stage analysis over the ETF cohort, ranked separately from stocks.

ETFs get their own section rather than joining the screener, and their own
relative-strength distribution. That separation is not cosmetic: `percentile_of`
ranks each ticker against the population it is given, so folding ~4.7k ETFs into
the stock cross-section would roughly halve every stock's rs_rating and silently
move every threshold built on it (the Minervini gate and a dozen trader presets
all key off rs_rating >= 70/80/85/90).

Leveraged and inverse products never reach here — they are dropped at archive
import — and volatility products are carried but not shown by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

import pandas as pd

from stock_scout.config.schema import Settings
from stock_scout.data.market_store import MarketDataStore
from stock_scout.indicators.momentum import percentile_of, weighted_rs_score
from stock_scout.pipeline.stage_classifier import classify_stage
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

BENCHMARK = "SPY"
# Enough weekly bars for the 30w SMA plus its slope lookback; below that
# classify_stage declines to guess.
_MIN_DAILY_BARS = 220


@dataclass
class EtfCohortResult:
    as_of: date | None
    rows: list[dict[str, Any]]
    evaluated: int = 0
    skipped_short_history: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "count": len(self.rows),
            "evaluated": self.evaluated,
            "skipped_short_history": self.skipped_short_history,
            "rows": self.rows,
        }


def _weekly(daily: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    close = daily["close"].resample("W-FRI").last().dropna()
    volume = daily["volume"].resample("W-FRI").sum().reindex(close.index)
    return close, volume


def scannable_etfs(store: MarketDataStore, kinds: Iterable[str] = ("plain",)) -> list[str]:
    """ETF tickers of the requested kinds that have bars in the store."""
    wanted = [str(k) for k in kinds]
    if not wanted:
        return []
    placeholders = ", ".join("?" for _ in wanted)
    with store._lock:  # noqa: SLF001 - same package, avoids a redundant public API
        rows = store._conn.execute(
            f"""
            SELECT DISTINCT k.ticker FROM etf_kind k
            WHERE k.kind IN ({placeholders})
              AND EXISTS (SELECT 1 FROM ohlcv_daily o WHERE o.ticker = k.ticker)
            ORDER BY k.ticker
            """,
            wanted,
        ).fetchall()
    return [str(r[0]) for r in rows]


def compute_etf_cohort(
    settings: Settings,
    *,
    kinds: Iterable[str] = ("plain",),
    min_dollar_volume: float | None = None,
    limit: int = 0,
) -> EtfCohortResult:
    """Stage-classify the ETF cohort and rank RS within it."""
    store = MarketDataStore.from_settings(settings, read_only=True, lock_timeout_seconds=30.0)
    try:
        tickers = scannable_etfs(store, kinds)
        if limit and limit > 0:
            tickers = tickers[:limit]
        as_of = store.latest_bar_date_overall()

        bench = store.read_ohlcv(BENCHMARK)
        bench_close = bench["close"] if not bench.empty else pd.Series(dtype="float64")
        # Explicit None, not `series or None`: a Series has no truth value.
        bench_weekly = (
            bench_close.resample("W-FRI").last().dropna() if not bench_close.empty else None
        )
        if bench_weekly is not None and bench_weekly.empty:
            bench_weekly = None

        names = {
            str(t): n
            for t, n in store._conn.execute(  # noqa: SLF001
                "SELECT ticker, name FROM securities"
            ).fetchall()
        }
        kind_by_ticker = {
            str(t): str(k)
            for t, k in store._conn.execute(  # noqa: SLF001
                "SELECT ticker, kind FROM etf_kind"
            ).fetchall()
        }

        result = EtfCohortResult(as_of=as_of, rows=[])
        cohort_rs: list[float] = []
        pending: list[dict[str, Any]] = []

        for chunk_start in range(0, len(tickers), 500):
            chunk = tickers[chunk_start : chunk_start + 500]
            frames = store.read_ohlcv_bulk(chunk)
            for ticker in chunk:
                daily = frames.get(ticker)
                if daily is None or len(daily) < _MIN_DAILY_BARS:
                    result.skipped_short_history += 1
                    continue
                wclose, wvolume = _weekly(daily)
                stage = classify_stage(wclose, wvolume, bench_weekly)
                if stage is None:
                    result.skipped_short_history += 1
                    continue
                result.evaluated += 1

                rs = None
                if not bench_close.empty:
                    series = weighted_rs_score(daily["close"], bench_close)
                    if not series.empty and pd.notna(series.iloc[-1]):
                        rs = float(series.iloc[-1])
                        cohort_rs.append(rs)

                tail = daily.tail(50)
                row = {
                    "ticker": ticker,
                    "name": names.get(ticker),
                    "etf_kind": kind_by_ticker.get(ticker, "unknown"),
                    "last_close": float(daily["close"].iloc[-1]),
                    "bars": int(len(daily)),
                    "first_bar": daily.index[0].date().isoformat(),
                    "avg_dollar_volume_50d": float((tail["close"] * tail["volume"]).mean()),
                    "rs_score_weighted": rs,
                }
                for key in (
                    "stage", "substage", "stage_origin", "stage_range_state",
                    "stage_quality_score", "stage_trade_bias", "confidence",
                    "mansfield_rs", "ext_pct", "ma_direction",
                    "long_term_context", "stage_context_years",
                    "upper_long_term_window_weeks", "range_52w_pos",
                ):
                    row[key] = stage.get(key)
                pending.append(row)

        # RS rating is a percentile within THIS cohort, never against stocks.
        for row in pending:
            rs = row.get("rs_score_weighted")
            row["rs_rating"] = (
                percentile_of(rs, cohort_rs, min_population=30) if rs is not None else None
            )
        if min_dollar_volume:
            pending = [r for r in pending if r["avg_dollar_volume_50d"] >= float(min_dollar_volume)]
        pending.sort(key=lambda r: (r.get("rs_rating") is None, -(r.get("rs_rating") or 0.0)))
        result.rows = pending
        return result
    finally:
        store.close()
