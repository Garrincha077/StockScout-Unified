"""Ryan Hamby's phase and Minervini confirmation primitives.

Adapted from ``RyanJHamby/stock-screener``
``src/screening/phase_indicators.py`` at commit
``c2737ffa2e22409f40de955f519c40079826ecaf``. Copyright (c) 2024 Ryan
Hamby, used under the MIT License included in ``NOTICE``.

This module is deliberately isolated from StockScout scoring.  It emits only
shadow confirmation evidence and cannot influence ranking or trade sizing.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def calculate_sma(prices: pd.Series, period: int) -> pd.Series:
    if len(prices) < period:
        return pd.Series([np.nan] * len(prices), index=prices.index)
    return prices.rolling(window=period, min_periods=period).mean()


def calculate_slope(series: pd.Series, periods: int = 20) -> float:
    if len(series) < periods or series.isna().all():
        return 0.0
    recent = series.iloc[-periods:].dropna()
    if len(recent) < 2:
        return 0.0
    x = np.arange(len(recent))
    values = recent.to_numpy(dtype=float)
    if np.std(x) == 0:
        return 0.0
    average = float(np.mean(values))
    if average == 0:
        return 0.0
    return float(np.polyfit(x, values, 1)[0] / average * 100.0)


def detect_volatility_contraction(prices: pd.Series, window: int = 20) -> dict[str, Any]:
    if len(prices) < window * 2:
        return {
            "is_contracting": False,
            "contraction_quality": 0.0,
            "current_volatility": 0.0,
        }
    volatility = prices.rolling(window=window).std()
    if len(volatility.dropna()) < 2:
        return {
            "is_contracting": False,
            "contraction_quality": 0.0,
            "current_volatility": 0.0,
        }
    current = float(volatility.iloc[-1])
    average = float(volatility.iloc[-window * 2 : -window].mean())
    ratio = current / average if average > 0 else 1.0
    quality = max(0.0, min(100.0, (1.0 - ratio) * 100.0))
    return {
        "is_contracting": ratio < 0.7,
        "contraction_quality": round(quality, 2),
        "current_volatility": round(current, 2),
        "contraction_ratio": round(ratio, 2),
    }


def calculate_distance_from_sma(price: float, sma: float) -> float:
    return ((price - sma) / sma) * 100.0 if sma else 0.0


def classify_phase(price_data: pd.DataFrame, current_price: float) -> dict[str, Any]:
    """Return Ryan's frozen Phase 1-4 classification."""

    if len(price_data) < 200:
        return {
            "phase": 0,
            "phase_name": "Insufficient Data",
            "confidence": 0.0,
            "reasons": ["Need at least 200 days of data"],
        }
    close = price_data["Close"]
    high = price_data["High"]
    low = price_data["Low"]
    volume = price_data.get("Volume", pd.Series(dtype=float))
    sma_50 = calculate_sma(close, 50)
    sma_150 = calculate_sma(close, 150)
    sma_200 = calculate_sma(close, 200)
    if sma_50.isna().all() or sma_200.isna().all():
        return {
            "phase": 0,
            "phase_name": "Insufficient Data",
            "confidence": 0.0,
            "reasons": ["Cannot calculate SMAs"],
        }

    sma_50_value = float(sma_50.iloc[-1])
    sma_150_value = float(sma_150.iloc[-1]) if not sma_150.isna().all() else 0.0
    sma_200_value = float(sma_200.iloc[-1])
    lookback = min(252, len(close))
    high_52w = float(high.iloc[-lookback:].max())
    low_52w = float(low.iloc[-lookback:].min())
    slope_50 = calculate_slope(sma_50, 20)
    slope_200 = calculate_slope(sma_200, 20)
    contraction = detect_volatility_contraction(close, 20)
    if len(volume) > 20:
        average_volume = float(volume.iloc[-20:].mean())
        current_volume = float(volume.iloc[-1])
        volume_ratio = current_volume / average_volume if average_volume > 0 else 1.0
    else:
        volume_ratio = 1.0

    reasons: list[str] = []
    if (
        current_price < sma_50_value
        and current_price < sma_200_value
        and sma_50_value < sma_200_value
    ):
        phase, phase_name, confidence = 4, "Downtrend", 70.0
        reasons.extend(["price_below_50_and_200", "sma_50_below_200"])
        if slope_50 < 0 and slope_200 < 0:
            reasons.append("both_smas_declining")
            confidence += 20
        if slope_50 < 0:
            confidence += 10
    elif current_price > sma_50_value and sma_50_value > sma_200_value and slope_50 > 0:
        phase, phase_name, confidence = 2, "Uptrend/Breakout", 70.0
        reasons.extend(["price_above_50", "sma_50_above_200", "sma_50_rising"])
        if slope_200 > 0:
            reasons.append("sma_200_rising")
            confidence += 15
        if volume_ratio > 1.2:
            reasons.append("volume_expansion")
            confidence += 15
    elif current_price > sma_50_value and calculate_distance_from_sma(
        current_price, sma_50_value
    ) > 25:
        phase, phase_name, confidence = 3, "Distribution/Top", 60.0
        reasons.append("price_extended_above_50")
        if slope_50 < 0.05:
            reasons.append("sma_50_flattening")
            confidence += 20
        if abs(slope_50) < abs(slope_200) * 0.5:
            reasons.append("momentum_weakening")
            confidence += 20
    else:
        phase, phase_name, confidence = 1, "Base Building", 50.0
        reasons.append("consolidation")
        if abs(slope_50) < 0.1:
            reasons.append("sma_50_flat")
            confidence += 15
        if abs(slope_200) < 0.05:
            reasons.append("sma_200_flat")
            confidence += 10
        if contraction["is_contracting"]:
            reasons.append("volatility_contracting")
            confidence += 15
        if volume_ratio < 1.0:
            reasons.append("volume_below_average")
            confidence += 10

    return {
        "phase": phase,
        "phase_name": phase_name,
        "confidence": min(confidence, 100.0),
        "reasons": reasons,
        "sma_50": round(sma_50_value, 2),
        "sma_150": round(sma_150_value, 2),
        "sma_200": round(sma_200_value, 2),
        "slope_50": round(slope_50, 4),
        "slope_200": round(slope_200, 4),
        "distance_from_50sma": round(
            calculate_distance_from_sma(current_price, sma_50_value), 2
        ),
        "distance_from_200sma": round(
            calculate_distance_from_sma(current_price, sma_200_value), 2
        ),
        "week_52_high": round(high_52w, 2),
        "week_52_low": round(low_52w, 2),
        "volatility_contraction": contraction,
    }


def validate_minervini_trend_template(
    current_price: float,
    phase_info: dict[str, Any],
    sma_200_series: pd.Series,
) -> dict[str, Any]:
    """Evaluate the eight criteria from Ryan's frozen trend template."""

    sma_50 = float(phase_info.get("sma_50") or 0.0)
    sma_150 = float(phase_info.get("sma_150") or 0.0)
    sma_200 = float(phase_info.get("sma_200") or 0.0)
    high_52w = float(phase_info.get("week_52_high") or 0.0)
    low_52w = float(phase_info.get("week_52_low") or 0.0)
    if len(sma_200_series) >= 20:
        sma_200_rising = bool(sma_200_series.iloc[-1] > sma_200_series.iloc[-20])
    else:
        sma_200_rising = float(phase_info.get("slope_200") or 0.0) > 0

    distance_low = (current_price - low_52w) / low_52w * 100.0 if low_52w > 0 else 0.0
    distance_high = (high_52w - current_price) / high_52w * 100.0 if high_52w > 0 else 100.0
    criteria: dict[str, bool | float] = {
        "price_above_150_200": current_price > sma_150 and current_price > sma_200,
        "sma_150_above_200": sma_150 > sma_200,
        "sma_200_rising": sma_200_rising,
        "sma_50_above_150": sma_50 > sma_150,
        "price_above_50": current_price > sma_50,
        "price_30pct_above_52w_low": distance_low >= 30,
        "distance_from_52w_low_pct": round(distance_low, 1),
        "price_near_52w_high": distance_high <= 25,
        "distance_from_52w_high_pct": round(distance_high, 1),
        "confirmed_stage_2": phase_info.get("phase") == 2,
    }
    criterion_names = (
        "price_above_150_200",
        "sma_150_above_200",
        "sma_200_rising",
        "sma_50_above_150",
        "price_above_50",
        "price_30pct_above_52w_low",
        "price_near_52w_high",
        "confirmed_stage_2",
    )
    passed = sum(criteria[name] is True for name in criterion_names)
    return {
        "passes_template": passed >= 7,
        "criteria_passed": passed,
        "criteria_total": 8,
        "template_score": int(passed / 8 * 100),
        "criteria_details": criteria,
    }
