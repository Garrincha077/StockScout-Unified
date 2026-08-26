from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from stock_scout.indicators.volatility import atr


@dataclass
class PivotHigh:
    index: pd.Timestamp
    price: float
    bars_ago: int


def find_pivot_high(close: pd.Series, lookback: int = 30, left_bars: int = 5, right_bars: int = 5) -> PivotHigh | None:
    """Find the most recent local high (a bar whose close is the max within
    [i-left_bars, i+right_bars]) in the last `lookback` bars. Returns the most
    recent confirmed pivot — i.e. the rightmost candidate must have at least
    `right_bars` bars of subsequent data."""
    if len(close) < (left_bars + right_bars + 1):
        return None
    window = close.tail(lookback + right_bars)
    # Walk from newest to oldest, skipping the last `right_bars` (unconfirmed).
    values = window.values
    idx = window.index
    n = len(values)
    for i in range(n - right_bars - 1, left_bars - 1, -1):
        left = values[i - left_bars : i]
        right = values[i + 1 : i + right_bars + 1]
        if values[i] >= left.max() and values[i] >= right.max():
            bars_ago = (n - 1) - i
            return PivotHigh(index=idx[i], price=float(values[i]), bars_ago=int(bars_ago))
    return None


def range_contraction(df: pd.DataFrame, recent_window: int = 10, long_window: int = 50) -> pd.Series:
    """Ratio of recent ATR(recent_window) / long ATR(long_window). Values < 1 indicate contraction."""
    atr_short = atr(df, recent_window)
    atr_long = atr(df, long_window)
    return atr_short / atr_long.replace(0, np.nan)


def vcp_score(df: pd.DataFrame, lookback: int = 50) -> pd.Series:
    """Heuristic VCP-style contraction score (0-100). High = strong contraction
    with successively tighter pullbacks. Cheap, approximate — full VCP detection
    is non-trivial and out of scope for the screener."""
    if len(df) < lookback:
        return pd.Series(index=df.index, dtype=float)
    # Rolling daily range as % of close, smoothed.
    daily_range = (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    smooth = daily_range.rolling(window=5, min_periods=5).mean()
    # Contraction over lookback: compare end-of-window mean range to start-of-window mean range.
    half = lookback // 2
    early = smooth.rolling(window=half, min_periods=half).mean().shift(half)
    late = smooth.rolling(window=half, min_periods=half).mean()
    ratio = late / early
    # Map: ratio <= 0.5 -> 100, ratio >= 1.0 -> 0
    score = (1.0 - ratio.clip(0.5, 1.0)).mul(200.0)
    return score.clip(0.0, 100.0)


@dataclass
class GLBLevel:
    level: float          # The GLB price level
    set_at: pd.Timestamp  # When that high was set
    days_held: int        # How many bars since the high without exceeding it


def find_glb_level(close: pd.Series, min_days_without_new_high: int = 63) -> GLBLevel | None:
    """Eric Wish Green Line Breakout.

    The GLB is the all-time-high (within the data we have) that has NOT been
    exceeded for at least `min_days_without_new_high` bars (default ~3 months).
    The level "ages in" — a new high resets it. Once a high holds for the
    required period without being broken, it becomes the GLB. If price later
    exceeds it, the new high (after holding) becomes the GLB.

    Returns the most recent valid GLB level as-of the last bar of `close`.
    """
    if len(close) < min_days_without_new_high + 2:
        return None
    values = close.values
    idx = close.index
    n = len(values)

    last_glb: GLBLevel | None = None
    # Iterate forward; at each bar i, check whether close[i] is the max over
    # [..., i] and has been the max for >= min_days. Track each candidate.
    running_max = values[0]
    running_max_idx = 0
    for i in range(1, n):
        if values[i] > running_max:
            # If the previous running max held for long enough, it became a GLB
            # at the moment it was about to be broken.
            held = i - running_max_idx
            if held >= min_days_without_new_high:
                last_glb = GLBLevel(
                    level=float(running_max),
                    set_at=idx[running_max_idx],
                    days_held=int(held),
                )
            running_max = values[i]
            running_max_idx = i

    # Check the final running max — if it has held to end of series for long enough.
    final_held = (n - 1) - running_max_idx
    if final_held >= min_days_without_new_high:
        last_glb = GLBLevel(
            level=float(running_max),
            set_at=idx[running_max_idx],
            days_held=int(final_held),
        )

    return last_glb
