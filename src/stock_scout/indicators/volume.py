from __future__ import annotations

import numpy as np
import pandas as pd


def avg_volume(volume: pd.Series, window: int = 50) -> pd.Series:
    return volume.rolling(window=window, min_periods=window).mean()


def avg_dollar_volume(close: pd.Series, volume: pd.Series, window: int = 50) -> pd.Series:
    dv = (close * volume).rolling(window=window, min_periods=window).mean()
    return dv


def volume_ratio(volume: pd.Series, window: int = 50) -> pd.Series:
    """Current volume / N-day average volume."""
    avg = avg_volume(volume, window)
    return volume / avg


def volume_dryup_score(volume: pd.Series, base_window: int = 10, avg_window: int = 50) -> pd.Series:
    """0-100 score: how dry has volume been recently versus its longer-term average.
    100 = recent volume is 50% or less of the longer average; 0 = recent volume meets/exceeds it.
    No lookahead.
    """
    avg = avg_volume(volume, avg_window)
    recent = volume.rolling(window=base_window, min_periods=base_window).mean()
    ratio = recent / avg
    # Map ratio to score: <=0.5 -> 100, >=1.0 -> 0, linear between
    score = (1.0 - ratio.clip(0.5, 1.0)).mul(200.0)  # 0.5 -> 100, 1.0 -> 0
    return score.clip(0.0, 100.0)


def up_down_volume_ratio(close: pd.Series, volume: pd.Series, window: int = 50) -> pd.Series:
    """Accumulation gauge: Σ volume on up-days / Σ volume on down-days over a
    trailing window. >1.0 means more volume traded on advancing days
    (institutional accumulation); <1.0 means distribution. Canonical
    Wyckoff / O'Neil "U/D volume" signal. No lookahead.
    """
    change = close.diff()
    up_vol = volume.where(change > 0, 0.0)
    down_vol = volume.where(change < 0, 0.0)
    up_sum = up_vol.rolling(window=window, min_periods=window).sum()
    down_sum = down_vol.rolling(window=window, min_periods=window).sum()
    return up_sum / down_sum.replace(0, np.nan)


def volume_expansion_ratio(volume: pd.Series, fast: int = 5, slow: int = 50) -> pd.Series:
    """Recent (fast-day avg) volume relative to its longer base (slow-day avg).
    >1.5 on an up-day = breakout volume confirmation; <1.0 = volume drying up.
    No lookahead.
    """
    fast_avg = volume.rolling(window=fast, min_periods=fast).mean()
    slow_avg = volume.rolling(window=slow, min_periods=slow).mean()
    return fast_avg / slow_avg.replace(0, np.nan)


def pocket_pivot(close: pd.Series, volume: pd.Series, lookback: int = 10) -> pd.Series:
    """Pocket pivot (Gil Morales / Chris Kacher): the earliest institutional
    footprint. True on an UP-day whose volume EXCEEDS the largest DOWN-day
    volume of the prior `lookback` days. Signals accumulation inside a base,
    BEFORE the conventional breakout. Returns a boolean Series. No lookahead.
    """
    change = close.diff()
    is_up = change > 0
    down_vol = volume.where(change < 0, 0.0)
    # Largest down-day volume over the PRIOR `lookback` days (today excluded).
    max_down_prior = down_vol.shift(1).rolling(window=lookback, min_periods=1).max()
    pp = is_up & (volume > max_down_prior) & (max_down_prior > 0)
    return pp.fillna(False)
