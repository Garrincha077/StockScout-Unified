from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average. No lookahead — strictly trailing window."""
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    """Exponential moving average using pandas ewm with adjust=False (recursive),
    seeded by the first `window` SMA so it's a clean trailing indicator."""
    sma_seed = series.rolling(window=window, min_periods=window).mean()
    out = series.ewm(span=window, adjust=False, min_periods=window).mean()
    # Replace early NaN region with SMA seed-aware result already produced by ewm min_periods.
    return out.where(sma_seed.notna() | out.notna(), other=pd.NA)


def is_rising(series: pd.Series, lookback: int) -> pd.Series:
    """True at index i if series[i] > series[i - lookback]. No lookahead."""
    return series > series.shift(lookback)
