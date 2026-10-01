"""Fail fast when Yahoo has not published the selected EOD session broadly enough.

This is an operational readiness gate only. It never changes the orchestrator-selected
session and never supplies scanner data.
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

SAMPLE = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "JPM", "XOM", "COST")
MIN_READY_FRACTION = 0.8
RETRY_DELAYS = (30, 60, 120)


def _last_date(raw: pd.DataFrame, ticker: str) -> date | None:
    if raw is None or raw.empty:
        return None
    try:
        frame = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
    except KeyError:
        return None
    if frame.empty:
        return None
    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.droplevel(0, axis=1)
    required = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame.columns]
    if required:
        frame = frame.dropna(subset=required, how="any")
    if frame.empty:
        return None
    return pd.Timestamp(frame.index.max()).date()


def readiness(raw: pd.DataFrame, session: date) -> tuple[bool, dict[str, date | None]]:
    dates = {ticker: _last_date(raw, ticker) for ticker in SAMPLE}
    ready = sum(value == session for value in dates.values())
    return dates["SPY"] == session and ready / len(SAMPLE) >= MIN_READY_FRACTION, dates


def fetch(session: date) -> pd.DataFrame:
    return yf.download(
        list(SAMPLE),
        start=(session - timedelta(days=7)).isoformat(),
        end=(session + timedelta(days=1)).isoformat(),
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
        timeout=20,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-date", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    session = args.session_date

    for attempt in range(len(RETRY_DELAYS) + 1):
        raw = fetch(session)
        ok, dates = readiness(raw, session)
        ready = sum(value == session for value in dates.values())
        print(f"Next provider readiness: session={session} ready={ready}/{len(SAMPLE)} SPY={dates['SPY']}")
        if ok:
            return 0
        if attempt < len(RETRY_DELAYS):
            delay = RETRY_DELAYS[attempt]
            print(f"Yahoo is not ready for {session}; retrying unchanged session in {delay}s")
            time.sleep(delay)

    print(f"Yahoo is not ready for selected session {session}; refusing to start full Next scan")
    return 75


if __name__ == "__main__":
    raise SystemExit(main())
