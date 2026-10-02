"""Read complete adjusted Yahoo bars for one immutable NYSE session.

A separate exact-day request can recover a missing/partial terminal row from a
wide history response. Never derive an EOD close from a quote or another date.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

OHLCV = ("Open", "High", "Low", "Close", "Volume")


def ticker_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if not isinstance(raw.columns, pd.MultiIndex):
        return raw.copy()
    for level in range(raw.columns.nlevels):
        if ticker in raw.columns.get_level_values(level):
            return raw.xs(ticker, axis=1, level=level).copy()
    return pd.DataFrame()


def session_bar(frame: pd.DataFrame, session: date) -> pd.DataFrame:
    """Return only complete numeric OHLCV rows on the requested local date."""
    if frame is None or frame.empty or any(field not in frame for field in OHLCV):
        return pd.DataFrame()
    frame = frame.loc[:, list(OHLCV)].copy()
    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        index = index.tz_convert("America/New_York").tz_localize(None)
    frame.index = index
    frame = frame.loc[frame.index.date == session]
    numeric = frame.apply(pd.to_numeric, errors="coerce")
    complete = np.isfinite(numeric).all(axis=1)
    return numeric.loc[complete].sort_index().tail(1)


def download_session_bars(tickers: list[str], session: date) -> dict[str, pd.DataFrame]:
    if not tickers:
        return {}
    raw = yf.download(
        tickers,
        start=session.isoformat(),
        end=(session + timedelta(days=1)).isoformat(),
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=20,
    )
    result = {}
    for ticker in tickers:
        # A flat response can identify exactly one requested symbol only.
        if raw is not None and not isinstance(raw.columns, pd.MultiIndex) and len(tickers) != 1:
            continue
        bar = session_bar(ticker_frame(raw, ticker), session)
        if not bar.empty:
            result[ticker] = bar
    return result


def replace_session_bar(history: pd.DataFrame, bar: pd.DataFrame, session: date) -> pd.DataFrame:
    """Keep the historical window and replace only the validated terminal bar."""
    valid = session_bar(bar, session)
    if history is None or history.empty or valid.empty:
        return history
    history = history.copy()
    history.index = pd.DatetimeIndex(history.index)
    if history.index.tz is not None:
        history.index = history.index.tz_convert("America/New_York").tz_localize(None)
    before = history.loc[history.index.date < session]
    return pd.concat([before, valid]).sort_index()
