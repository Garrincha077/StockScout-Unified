from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from stock_scout.indicators.moving_averages import ema, sma


@dataclass(frozen=True)
class WeeklyStructuralStop:
    """Audit record for a weekly-chart thesis boundary.

    ``support_level`` is the actual weekly price structure. ``stop_level`` is
    placed below it using the larger of a small percentage buffer and a
    fraction of weekly ATR, so the stop is not sitting directly on support.
    """

    stop_level: float
    support_level: float
    support_source: str
    support_week: str | None
    stop_distance_pct: float
    weekly_atr14: float | None
    weekly_ema10: float | None
    weekly_sma30: float | None
    completed_weeks: int


def _timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.normalize()


def _week_end(value: Any) -> pd.Timestamp:
    return _timestamp(value).to_period("W-FRI").end_time.normalize()


def completed_weekly_history(
    weekly: pd.DataFrame,
    *,
    as_of: Any,
    cutoff_date: Any | None = None,
) -> pd.DataFrame:
    """Return only fully completed weekly bars available before the setup.

    A Monday-to-Thursday week resampled with ``W-FRI`` is labelled with a
    future Friday. It must not be used as confirmed weekly structure. When a
    breakout date is provided, the breakout week itself is excluded so the
    stop is based on structure that existed before the launch.
    """

    if weekly is None or weekly.empty:
        return pd.DataFrame()
    required = {"high", "low", "close"}
    if not required.issubset(weekly.columns):
        return pd.DataFrame()

    work = weekly.copy()
    work.index = pd.DatetimeIndex([_timestamp(v) for v in work.index])
    work = work[~work.index.duplicated(keep="last")].sort_index()
    period_end = pd.DatetimeIndex([_week_end(v) for v in work.index])
    as_of_ts = _timestamp(as_of)
    mask = period_end <= as_of_ts
    if cutoff_date is not None:
        mask &= period_end < _week_end(cutoff_date)
    work = work.loc[mask].copy()
    work.index = period_end[mask]
    return work.dropna(subset=["high", "low", "close"])


def _latest_confirmed_pivot_low(
    lows: pd.Series,
    *,
    lookback_weeks: int,
    left_weeks: int,
    right_weeks: int,
) -> tuple[pd.Timestamp, float] | None:
    if len(lows) < left_weeks + right_weeks + 1:
        return None
    first = max(left_weeks, len(lows) - max(lookback_weeks, 1))
    last = len(lows) - right_weeks - 1
    for pos in range(last, first - 1, -1):
        value = float(lows.iloc[pos])
        left = lows.iloc[pos - left_weeks : pos]
        right = lows.iloc[pos + 1 : pos + 1 + right_weeks]
        if left.empty or right.empty:
            continue
        if value <= float(left.min()) and value < float(right.min()):
            return pd.Timestamp(lows.index[pos]), value
    return None


def _weekly_atr14(history: pd.DataFrame) -> float | None:
    if len(history) < 5:
        return None
    high = history["high"].astype(float)
    low = history["low"].astype(float)
    close = history["close"].astype(float)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    sample = true_range.dropna().tail(14)
    if sample.empty:
        return None
    value = float(sample.mean())
    return value if value > 0 else None


def derive_weekly_structural_stop(
    weekly: pd.DataFrame,
    *,
    reference_price: float,
    as_of: Any,
    cutoff_date: Any | None = None,
    structure_lookback_weeks: int = 20,
    base_lookback_weeks: int = 8,
    pivot_left_weeks: int = 2,
    pivot_right_weeks: int = 1,
    min_support_gap_pct: float = 1.5,
    stop_buffer_pct: float = 0.5,
    stop_atr_fraction: float = 0.15,
) -> WeeklyStructuralStop | None:
    """Derive a weekly-chart invalidation below confirmed support.

    Priority is the most recent confirmed weekly pivot low. If that pivot is
    too close to the entry to survive normal weekly noise, the recent weekly
    base floor is used instead. Moving averages are recorded for context but
    never substituted for price structure.
    """

    if reference_price <= 0:
        return None
    history = completed_weekly_history(weekly, as_of=as_of, cutoff_date=cutoff_date)
    if len(history) < max(6, pivot_left_weeks + pivot_right_weeks + 1):
        return None

    lows = history["low"].astype(float)
    pivot = _latest_confirmed_pivot_low(
        lows,
        lookback_weeks=structure_lookback_weeks,
        left_weeks=pivot_left_weeks,
        right_weeks=pivot_right_weeks,
    )
    base_window = lows.tail(max(3, base_lookback_weeks))
    base_week = pd.Timestamp(base_window.idxmin())
    base_level = float(base_window.min())

    support_week: pd.Timestamp
    support_level: float
    source: str
    if pivot is not None:
        pivot_week, pivot_level = pivot
        pivot_gap_pct = (reference_price - pivot_level) / reference_price * 100.0
        if pivot_level < reference_price and pivot_gap_pct >= min_support_gap_pct:
            support_week, support_level, source = pivot_week, pivot_level, "weekly_pivot_low"
        else:
            support_week, support_level, source = base_week, base_level, "weekly_base_floor"
    else:
        support_week, support_level, source = base_week, base_level, "weekly_base_floor"

    if support_level <= 0 or support_level >= reference_price:
        return None

    close = history["close"].astype(float)
    ema10 = ema(close, 10)
    sma30 = sma(close, 30)
    ema10_value = float(ema10.iloc[-1]) if len(ema10) and pd.notna(ema10.iloc[-1]) else None
    sma30_value = float(sma30.iloc[-1]) if len(sma30) and pd.notna(sma30.iloc[-1]) else None
    atr14 = _weekly_atr14(history)
    pct_buffer = support_level * max(0.0, stop_buffer_pct) / 100.0
    atr_buffer = (atr14 or 0.0) * max(0.0, stop_atr_fraction)
    buffer = max(pct_buffer, atr_buffer)
    stop_level = max(0.01, support_level - buffer)
    if stop_level >= reference_price:
        return None
    stop_distance_pct = (reference_price - stop_level) / reference_price * 100.0

    return WeeklyStructuralStop(
        stop_level=stop_level,
        support_level=support_level,
        support_source=source,
        support_week=support_week.date().isoformat(),
        stop_distance_pct=stop_distance_pct,
        weekly_atr14=atr14,
        weekly_ema10=ema10_value,
        weekly_sma30=sma30_value,
        completed_weeks=len(history),
    )
