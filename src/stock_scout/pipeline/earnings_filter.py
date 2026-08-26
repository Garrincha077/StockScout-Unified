"""Earnings-proximity annotation for top candidates.

Calls yfinance `Ticker.calendar` for the top N candidates that go to the AI
ranker. Annotates each candidate with `next_earnings_date` (if known) and a
warning flag when earnings falls within the next 5 trading days. If earnings
is the very next trading day, the candidate is excluded outright (Minervini
"never hold through earnings" rule).

Results are cached in `data/earnings_cache.parquet` with a 24h TTL so a smoke
run doesn't burn yfinance budget repeatedly.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from stock_scout.scoring.models import Candidate, Flag
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

_CACHE_TTL_HOURS = 24


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5


def _trading_days_between(start: date, end: date) -> int:
    """Count trading days strictly between two dates (exclusive of start)."""
    if end <= start:
        return 0
    cur = start
    n = 0
    while cur < end:
        cur += timedelta(days=1)
        if _is_trading_day(cur):
            n += 1
    return n


def _load_cache(path: Path) -> dict[str, tuple[date | None, datetime]]:
    if not path.exists():
        return {}
    try:
        df = pd.read_parquet(path)
        out: dict[str, tuple[date | None, datetime]] = {}
        for _, row in df.iterrows():
            ts = row.get("fetched_at")
            ed = row.get("earnings_date")
            ticker = str(row.get("ticker", "")).upper()
            if not ticker:
                continue
            if pd.isna(ts):
                continue
            fetched = pd.Timestamp(ts).to_pydatetime()
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            edate = None if pd.isna(ed) else pd.Timestamp(ed).date()
            out[ticker] = (edate, fetched)
        return out
    except Exception as e:  # noqa: BLE001
        log.debug("earnings_cache.load_failed", error=str(e))
        return {}


def _save_cache(path: Path, data: dict[str, tuple[date | None, datetime]]) -> None:
    if not data:
        return
    rows = [
        {
            "ticker": t,
            "earnings_date": pd.Timestamp(ed) if ed else pd.NaT,
            "fetched_at": pd.Timestamp(fetched),
        }
        for t, (ed, fetched) in data.items()
    ]
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def annotate_earnings(
    candidates: list[Candidate],
    yfinance_provider,
    cache_path: Path,
    top_n: int = 60,
    proximity_window_days: int = 5,
) -> int:
    """Annotate top candidates with earnings proximity. Mutates in place.

    Returns the number of candidates flagged within the proximity window.
    """
    if not candidates:
        return 0
    top = candidates[:top_n]
    cache = _load_cache(cache_path)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=_CACHE_TTL_HOURS)

    def _fetch(ticker: str) -> tuple[str, date | None]:
        cached = cache.get(ticker.upper())
        if cached and cached[1] >= cutoff:
            return ticker, cached[0]
        try:
            ed = yfinance_provider.get_next_earnings_date(ticker)
        except Exception as e:  # noqa: BLE001
            log.debug("earnings.fetch_failed", ticker=ticker, error=str(e))
            ed = None
        cache[ticker.upper()] = (ed, now)
        return ticker, ed

    # Throttle: yfinance Ticker.calendar gets rate-limited fast. 2 workers
    # max, sequential is also acceptable for ≤60 tickers.
    results: dict[str, date | None] = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {ex.submit(_fetch, c.ticker): c.ticker for c in top}
        for fut in as_completed(futures):
            try:
                tk, ed = fut.result()
                results[tk.upper()] = ed
            except Exception as e:  # noqa: BLE001
                log.debug("earnings.task_failed", error=str(e))

    today = date.today()
    flagged = 0
    for c in top:
        ed = results.get(c.ticker.upper())
        if ed is None:
            continue
        c.next_earnings_date = ed.isoformat()
        td = _trading_days_between(today, ed)
        if td == 0:
            # Today is earnings day — too late to enter, skip
            c.flags.append(
                Flag(code=f"earnings_today:{ed.isoformat()}", severity="error")
            )
            c.data_status = "EARNINGS_TODAY"
            flagged += 1
        elif td == 1:
            # Next trading day is earnings — Minervini rule: don't hold through.
            c.flags.append(
                Flag(code=f"earnings_next_trading_day:{ed.isoformat()}", severity="error")
            )
            flagged += 1
        elif 0 < td <= proximity_window_days:
            c.flags.append(
                Flag(
                    code=f"earnings_within_{proximity_window_days}d:{ed.isoformat()}",
                    severity="warning",
                )
            )
            flagged += 1

    _save_cache(cache_path, cache)
    log.info(
        "earnings_filter.done",
        annotated=sum(1 for c in top if c.next_earnings_date),
        flagged=flagged,
    )
    return flagged
