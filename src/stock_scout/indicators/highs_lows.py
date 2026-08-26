from __future__ import annotations

import numpy as np
import pandas as pd


def rolling_high(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    """Rolling max. No lookahead.

    `min_periods` defaults to 1, which makes an incomplete window report the max
    of whatever exists — so a 60-bar listing gets a "52-week high" drawn from 60
    bars. Pass `min_periods=window` to get NaN instead of a shorter-window
    answer dressed up as the real one.
    """
    return series.rolling(window=window, min_periods=min_periods or 1).max()


def rolling_low(series: pd.Series, window: int, min_periods: int | None = None) -> pd.Series:
    return series.rolling(window=window, min_periods=min_periods or 1).min()


def distance_to_52w_high_pct(
    close: pd.Series, window: int = 252, min_periods: int | None = None
) -> pd.Series:
    """Returns percent below 52w high (negative number when below, 0 when at high).

    Result is computed as (close - high) / high * 100, so:
        -10.0 means 10% below the 52w high.
    """
    high = rolling_high(close, window, min_periods)
    return (close - high) / high.replace(0, np.nan) * 100.0


def distance_to_52w_low_pct(
    close: pd.Series, window: int = 252, min_periods: int | None = None
) -> pd.Series:
    """Returns percent above 52w low (positive number above the low)."""
    low = rolling_low(close, window, min_periods)
    return (close - low) / low.replace(0, np.nan) * 100.0


def count_resistance_touches(
    highs: pd.Series,
    closes: pd.Series,
    level: float,
    tolerance_pct: float = 1.0,
) -> int:
    """Count bars where the bar's high came within `tolerance_pct` of `level`
    and the close stayed at or below the level — i.e. the level was tested
    and held as resistance.

    Used by GLB / lateral-resistance detection to require a tested level
    (Wish: a real GLB is contested, not just statistically flat)."""
    if level <= 0 or len(highs) == 0:
        return 0
    band_low = level * (1 - tolerance_pct / 100.0)
    band_high = level * (1 + tolerance_pct / 100.0)
    touched = (highs >= band_low) & (highs <= band_high) & (closes <= level)
    return int(touched.sum())


def close_location_value(df: pd.DataFrame, window: int = 1) -> pd.Series:
    """Bar's close position within its high-low range: 1.0 = at high, 0.0 = at low.
    With window>1, uses range over the trailing window."""
    if window <= 1:
        rng = df["high"] - df["low"]
        return (df["close"] - df["low"]) / rng.replace(0, np.nan)
    rolling_h = df["high"].rolling(window=window, min_periods=window).max()
    rolling_l = df["low"].rolling(window=window, min_periods=window).min()
    return (df["close"] - rolling_l) / (rolling_h - rolling_l).replace(0, np.nan)
