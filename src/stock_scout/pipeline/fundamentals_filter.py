"""Conviction / risk-context annotation for top candidates (Faza G).

For the top-N candidates (the ones that go to the AI ranker) we enrich each
with a few high-signal fundamentals that are too expensive to pull for the
whole universe:

  * insider buying (FMP /insider-trading) — open-market purchases are a strong
    conviction tell;
  * float + short interest (yfinance .info) — low float = sharper moves; high
    short % = squeeze potential / crowded-risk context.

Per the project's "ranking, not exclusion" philosophy nothing is filtered out
here — insider buying grants a small score bonus, and the float / short
numbers are surfaced in the UI for context. Everything is best-effort, cached
with a 24h TTL, and never fatal: a provider hiccup just leaves the fields None.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from stock_scout.scoring.models import Candidate, Flag, Reason
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

_CACHE_TTL_HOURS = 24


def _load_cache(path: Path) -> dict[str, tuple[dict, datetime]]:
    if not path.exists():
        return {}
    try:
        df = pd.read_parquet(path)
        out: dict[str, tuple[dict, datetime]] = {}
        for _, row in df.iterrows():
            ticker = str(row.get("ticker", "")).upper()
            ts = row.get("fetched_at")
            if not ticker or pd.isna(ts):
                continue
            fetched = pd.Timestamp(ts).to_pydatetime()
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)

            def _maybe(key: str):
                v = row.get(key)
                return None if v is None or pd.isna(v) else v

            payload = {
                "insider_buying": None if pd.isna(row.get("insider_buying")) else bool(row.get("insider_buying")),
                "float_shares": _maybe("float_shares"),
                "short_pct_float": _maybe("short_pct_float"),
            }
            out[ticker] = (payload, fetched)
        return out
    except Exception as e:  # noqa: BLE001
        log.debug("fundamentals_cache.load_failed", error=str(e))
        return {}


def _save_cache(path: Path, data: dict[str, tuple[dict, datetime]]) -> None:
    if not data:
        return
    rows = []
    for t, (payload, fetched) in data.items():
        rows.append(
            {
                "ticker": t,
                "insider_buying": payload.get("insider_buying"),
                "float_shares": payload.get("float_shares"),
                "short_pct_float": payload.get("short_pct_float"),
                "fetched_at": pd.Timestamp(fetched),
            }
        )
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def annotate_fundamentals(
    candidates: list[Candidate],
    fmp_provider,
    yfinance_provider,
    cache_path: Path,
    top_n: int = 60,
) -> int:
    """Annotate top-N candidates with insider buying + float + short interest.
    Mutates candidates in place. Returns the number with insider buying.
    """
    if not candidates:
        return 0
    top = candidates[:top_n]
    cache = _load_cache(cache_path)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=_CACHE_TTL_HOURS)

    def _fetch(ticker: str) -> tuple[str, dict]:
        cached = cache.get(ticker.upper())
        if cached and cached[1] >= cutoff:
            return ticker, cached[0]
        payload: dict = {"insider_buying": None, "float_shares": None, "short_pct_float": None}
        # Insider buying via FMP (best-effort).
        try:
            if fmp_provider is not None:
                ins = fmp_provider.get_insider_buys(ticker)
                if ins is not None:
                    payload["insider_buying"] = bool(ins.get("insider_buying"))
        except Exception as e:  # noqa: BLE001
            log.debug("fundamentals.insider_failed", ticker=ticker, error=str(e))
        # Float + short interest via yfinance .info (best-effort).
        try:
            if yfinance_provider is not None:
                stats = yfinance_provider.get_share_stats(ticker)
                if stats is not None:
                    payload["float_shares"] = stats.get("float_shares")
                    payload["short_pct_float"] = stats.get("short_pct_float")
        except Exception as e:  # noqa: BLE001
            log.debug("fundamentals.share_stats_failed", ticker=ticker, error=str(e))
        cache[ticker.upper()] = (payload, now)
        return ticker, payload

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(_fetch, c.ticker): c.ticker for c in top}
        for fut in as_completed(futures):
            try:
                tk, payload = fut.result()
                results[tk.upper()] = payload
            except Exception as e:  # noqa: BLE001
                log.debug("fundamentals.task_failed", error=str(e))

    insider_count = 0
    for c in top:
        payload = results.get(c.ticker.upper())
        if not payload:
            continue
        c.insider_buying = payload.get("insider_buying")
        c.float_shares = payload.get("float_shares")
        c.short_pct_float = payload.get("short_pct_float")
        if c.insider_buying:
            insider_count += 1
            # Small conviction bonus (ranking, not exclusion).
            c.score = round(min(100.0, c.score + 4.0), 1)
            c.reasons.append(
                Reason(text="insider_buying(open_market_purchases)", weight=1.0, category="conviction")
            )
        # High short interest is risk context, not a disqualifier — flag it.
        if c.short_pct_float is not None and c.short_pct_float >= 20.0:
            c.flags.append(
                Flag(code=f"high_short_interest:{c.short_pct_float:.0f}%", severity="warning")
            )

    _save_cache(cache_path, cache)
    log.info("fundamentals_filter.done", annotated=len(results), insider_buying=insider_count)
    return insider_count
