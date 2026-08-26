"""Pre-fetch universe trimming.

Goal: avoid burning yfinance fetch budget on tickers that have already been
shown (via earlier cache snapshots) to be illiquid / penny / cap'd too low to
trade. Keeps a negative-cache parquet so the filter is replayable and decays
gracefully (re-check stale entries after `recheck_after_days`).

Inputs (in priority order):
  1. Existing daily OHLCV cache at `data/cache/{primary_provider}/daily/*.parquet`.
     Cheap, no API cost. For every cached ticker we can compute
     `avg_volume_50d`, `avg_dollar_volume_50d`, `last_price`, `bars_available`.
  2. (Future) FMP `/stable/company-screener` if a paid plan is configured. The
     free tier returns HTTP 402 for this endpoint, so we treat it as optional.

Output: a parquet at `data/universe/excluded_illiquid.parquet` with rows
  (ticker, last_seen_avg_vol_50d, last_seen_avg_dvol_50d, last_seen_price,
   last_seen_bars, last_seen_end_date, reason, evaluated_at).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from stock_scout.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class PreFetchFilterConfig:
    """Defaults match the post-fetch prefilter so we never drop a ticker the
    deterministic prefilter would have kept, but reject ones it would not.

    Negative-cache decay: an excluded ticker is rechecked after
    `recheck_after_days` to allow recovery (an illiquid microcap may grow).
    """

    min_price_usd: float = 5.0
    min_avg_volume_50d: int = 300_000
    min_avg_dollar_volume_50d: float = 5_000_000
    min_bars_available: int = 250
    recheck_after_days: int = 7


@dataclass
class FilterResult:
    keep: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)  # rows for the negative cache
    summary: dict = field(default_factory=dict)


def _avg_volume_from_parquet(path: Path, lookback: int = 50) -> tuple[float, float, float, int, str | None]:
    """Return (avg_vol_50d, avg_dollar_vol_50d, last_close, bars_available, end_date_iso).

    Reads the parquet (date / close / volume cols) — handles both "date" and
    legacy "Date" index-column naming."""
    df = pd.read_parquet(path)
    if df.empty:
        return 0.0, 0.0, 0.0, 0, None
    df.columns = [str(c).lower() for c in df.columns]
    if "date" not in df.columns:
        # Index-based file (rare); use the index as date
        df = df.reset_index().rename(columns={df.index.name or "index": "date"})
        df.columns = [str(c).lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    tail = df.tail(lookback)
    avg_vol = float(tail["volume"].mean()) if len(tail) else 0.0
    avg_dvol = float((tail["close"] * tail["volume"]).mean()) if len(tail) else 0.0
    last_close = float(tail["close"].iloc[-1]) if len(tail) else 0.0
    end_date = tail["date"].iloc[-1].date().isoformat() if len(tail) else None
    return avg_vol, avg_dvol, last_close, int(len(df)), end_date


def build_negative_cache_from_existing(
    cache_dir: Path,
    provider: str,
    cfg: PreFetchFilterConfig,
) -> pd.DataFrame:
    """Walk daily cache for `provider` and return a DataFrame of tickers that
    fall below the configured thresholds. This is the cheap path — no network
    calls; it just reads parquet stats."""
    daily_dir = cache_dir / provider / "daily"
    if not daily_dir.exists():
        log.info("universe_filter.no_cache", provider=provider, path=str(daily_dir))
        return pd.DataFrame()

    rows: list[dict] = []
    files = sorted(daily_dir.glob("*.parquet"))
    log.info("universe_filter.scanning", provider=provider, n_files=len(files))
    now_iso = datetime.now(timezone.utc).isoformat()
    for fp in files:
        ticker = fp.stem
        try:
            avg_vol, avg_dvol, last_close, bars, end_date = _avg_volume_from_parquet(fp)
        except Exception as e:  # noqa: BLE001
            log.debug("universe_filter.parquet_read_failed", ticker=ticker, error=str(e))
            continue

        reasons: list[str] = []
        if bars < cfg.min_bars_available:
            reasons.append(f"bars<{cfg.min_bars_available}")
        if last_close < cfg.min_price_usd:
            reasons.append(f"price<{cfg.min_price_usd}")
        if avg_vol < cfg.min_avg_volume_50d:
            reasons.append(f"avg_vol_50d<{cfg.min_avg_volume_50d}")
        if avg_dvol < cfg.min_avg_dollar_volume_50d:
            reasons.append(f"avg_$vol_50d<{cfg.min_avg_dollar_volume_50d:.0f}")
        if not reasons:
            continue

        rows.append(
            {
                "ticker": ticker,
                "last_seen_avg_vol_50d": round(avg_vol, 1),
                "last_seen_avg_dvol_50d": round(avg_dvol, 1),
                "last_seen_price": round(last_close, 2),
                "last_seen_bars": bars,
                "last_seen_end_date": end_date or "",
                "reason": ",".join(reasons),
                "evaluated_at": now_iso,
            }
        )

    return pd.DataFrame(rows)


def load_negative_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path)
        return df
    except Exception as e:  # noqa: BLE001
        log.warning("universe_filter.load_failed", path=str(path), error=str(e))
        return pd.DataFrame()


def save_negative_cache(path: Path, df: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def _is_decayed(evaluated_at_iso: str, recheck_after_days: int) -> bool:
    try:
        ts = datetime.fromisoformat(evaluated_at_iso)
    except Exception:  # noqa: BLE001
        return True
    age = datetime.now(timezone.utc) - ts
    return age > timedelta(days=recheck_after_days)


def apply_negative_cache(
    universe: list[str],
    neg_cache: pd.DataFrame,
    force_include: set[str] | None = None,
    recheck_after_days: int = 7,
) -> FilterResult:
    """Subtract negative-cache tickers from `universe`, honouring force_include
    and decaying stale rejections."""
    force = {t.upper() for t in (force_include or set())}
    res = FilterResult()
    if neg_cache.empty:
        res.keep = sorted(set(universe))
        res.summary = {"input": len(universe), "kept": len(res.keep), "excluded": 0, "decayed": 0}
        return res

    # Index for fast lookup. `to_dict(orient="records")` is far faster than
    # iterrows() and avoids per-row Series boxing — the negative cache can hold
    # thousands of illiquid names, and this runs on every scan.
    excluded_map: dict[str, dict] = {}
    decayed = 0
    for row in neg_cache.to_dict(orient="records"):
        t = str(row.get("ticker", "")).upper()
        if not t:
            continue
        if _is_decayed(str(row.get("evaluated_at", "")), recheck_after_days):
            decayed += 1
            continue
        excluded_map[t] = row

    keep: list[str] = []
    excluded_now: list[dict] = []
    for t in universe:
        t_up = t.upper()
        if t_up in force:
            keep.append(t)
            continue
        if t_up in excluded_map:
            excluded_now.append(excluded_map[t_up])
            continue
        keep.append(t)

    res.keep = sorted(set(keep))
    res.excluded = excluded_now
    res.summary = {
        "input": len(universe),
        "kept": len(res.keep),
        "excluded": len(excluded_now),
        "decayed": decayed,
        "force_included": len(force),
    }
    return res
