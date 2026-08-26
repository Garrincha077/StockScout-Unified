from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """True range = max(high-low, abs(high - prev_close), abs(low - prev_close)).
    No lookahead — uses prev close only."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range — simple moving mean of true range (Wilder smoothing
    is also common; SMA is easier to reason about and good enough here)."""
    tr = true_range(df)
    return tr.rolling(window=window, min_periods=window).mean()


def adr_pct(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Average daily range as percentage of close (Mark Minervini-style)."""
    daily_pct = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    return daily_pct.rolling(window=window, min_periods=window).mean() * 100.0
