"""Fail fast when Yahoo has not published the selected EOD session broadly enough.

This is an operational readiness gate only. It never changes the orchestrator-selected
session and never supplies scanner data.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from market_session_repair import download_session_bars, session_bar, ticker_frame

SAMPLE = ("SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "JPM", "XOM", "COST")
MIN_READY_FRACTION = 0.8
RETRY_DELAYS = (30, 60, 120)


def _last_date(raw: pd.DataFrame, ticker: str) -> date | None:
    if raw is None or raw.empty:
        return None
    frame = ticker_frame(raw, ticker)
    if frame.empty:
        return None
    required = ("Open", "High", "Low", "Close", "Volume")
    if any(column not in frame.columns for column in required):
        return None
    frame = frame.loc[:, list(required)].apply(pd.to_numeric, errors="coerce")
    frame = frame.loc[np.isfinite(frame).all(axis=1)]
    if frame.empty:
        return None
    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        index = index.tz_convert("America/New_York")
    return pd.Timestamp(index.max()).date()


def readiness(raw: pd.DataFrame, session: date) -> tuple[bool, dict[str, date | None]]:
    if raw is None or not isinstance(raw.columns, pd.MultiIndex):
        return False, dict.fromkeys(SAMPLE)
    dates = {
        ticker: session if not session_bar(ticker_frame(raw, ticker), session).empty else _last_date(raw, ticker)
        for ticker in SAMPLE
    }
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
    parser.add_argument("--output", type=Path, default=Path("data/logs/market_readiness.json"))
    args = parser.parse_args()
    session = args.session_date
    attempts = []
    status = "provider_not_ready"

    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            raw = fetch(session)
        except Exception as exc:
            print(f"Next provider readiness download failed: {type(exc).__name__}: {exc}", flush=True)
            raw = pd.DataFrame()
        ok, dates = readiness(raw, session)
        initial_dates = dict(dates)
        repaired = {}
        if not ok:
            pending = [ticker for ticker, value in dates.items() if value != session]
            try:
                repaired = download_session_bars(pending, session)
            except Exception as exc:
                print(f"Next exact-session probe failed: {type(exc).__name__}: {exc}", flush=True)
            dates.update({ticker: session for ticker in repaired})
        ready = sum(value == session for value in dates.values())
        ok = dates["SPY"] == session and ready / len(SAMPLE) >= MIN_READY_FRACTION
        attempts.append({
            "attempt": attempt + 1,
            "historyDates": {key: value.isoformat() if value else None for key, value in initial_dates.items()},
            "dates": {key: value.isoformat() if value else None for key, value in dates.items()},
            "exactSessionRecovered": sorted(repaired),
            "readyCount": ready,
        })
        print(f"Next provider readiness: session={session} ready={ready}/{len(SAMPLE)} SPY={dates['SPY']} exact_day_recovered={len(repaired)}", flush=True)
        if ok:
            status = "ready"
            break
        if attempt < len(RETRY_DELAYS):
            delay = RETRY_DELAYS[attempt]
            print(f"Yahoo is not ready for {session}; retrying unchanged session in {delay}s", flush=True)
            time.sleep(delay)

    diagnostic = {
        "schema": "stockscout-next-readiness/v1",
        "sessionDate": session.isoformat(),
        "status": status,
        "retryable": status == "provider_not_ready",
        "sampleCount": len(SAMPLE),
        "minimumReadyFraction": MIN_READY_FRACTION,
        "attempts": attempts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(diagnostic, indent=2) + "\n", encoding="utf-8")
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(f"### Next provider readiness\n\nSession: `{session}`; status: `{status}`; complete sample: {ready}/{len(SAMPLE)}.\n\n")
            handle.write("| Ticker | Last complete session |\n| --- | --- |\n")
            for ticker, value in dates.items():
                handle.write(f"| {ticker} | {value or 'missing'} |\n")
    if status == "ready":
        return 0
    print(f"NEXT_PROVIDER_NOT_READY session={session}; refusing to start full Next scan", flush=True)
    return 75


if __name__ == "__main__":
    raise SystemExit(main())
