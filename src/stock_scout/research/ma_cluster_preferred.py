"""Point-in-time preference fit for tight-MA volume thrusts.

This module deliberately does not feed ``score``, ``focus_score`` or position
sizing.  It describes how closely a current daily/weekly bar resembles the
operator's preferred historical chart morphologies using only data known at
that bar's close.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

PREFERRED_PROFILE_VERSION = 1
PREFERRED_PROFILE_SOURCE = "ma_cluster_preferred_research_v1"

_THRESHOLDS = {
    "pattern_risk_pct": ("lte", 8.06),
    "ma_width_pct": ("lte", 5.21),
    "distance_to_prior_52w_high_pct": ("lte", -9.42),
    "signal_range_atr20": ("lte", 1.66),
    "signal_volume_ratio_50": ("lte", 1.47),
    "prior_up_volume_share_20": ("gte", 50.5),
}


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if pd.notna(out) else None


def _prior_atr20(frame: pd.DataFrame, signal_pos: int) -> float | None:
    if signal_pos < 21:
        return None
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    true_range = pd.concat(
        [(high - low), (high - close.shift(1)).abs(), (low - close.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    prior = true_range.iloc[max(0, signal_pos - 20) : signal_pos].dropna()
    if len(prior) < 20:
        return None
    value = float(prior.mean())
    return value if value > 0 else None


def _prior_up_volume_share(frame: pd.DataFrame, signal_pos: int) -> float | None:
    if signal_pos < 21:
        return None
    prior = frame.iloc[signal_pos - 20 : signal_pos]
    volume = prior["volume"].astype(float)
    denominator = float(volume.sum())
    if denominator <= 0:
        return None
    close = frame["close"].astype(float)
    up = close > close.shift(1)
    numerator = float(volume[up.iloc[signal_pos - 20 : signal_pos].to_numpy()].sum())
    return numerator / denominator * 100.0


def _archetypes(
    *,
    metrics: dict[str, float | None],
    assessment: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str, float, dict[str, float]]:
    drawdowns = [
        abs(float(v))
        for v in (context.get("drawdown_5y_pct"), context.get("long_term_drawdown_pct"))
        if _finite(v) is not None
    ]
    deep_drawdown = bool(drawdowns and max(drawdowns) >= 60.0)
    long_base = bool(
        (_finite(context.get("base_age_weeks")) or 0.0) >= 52.0
        or (_finite(context.get("weekly_base_length_bars")) or 0.0) >= 20.0
        or (_finite(context.get("monthly_base_length_bars")) or 0.0) >= 12.0
    )
    below_old_high = (
        metrics["distance_to_prior_52w_high_pct"] is not None
        and float(metrics["distance_to_prior_52w_high_pct"]) <= -9.42
    )
    recovery_checks = [deep_drawdown, long_base, bool(assessment.get("above_bundle")), below_old_high]

    extension_atr = metrics.get("extension_atr")
    tight_checks = [
        bool(metrics.get("pattern_risk_pct") is not None and metrics["pattern_risk_pct"] <= 8.06),
        bool(metrics.get("ma_width_pct") is not None and metrics["ma_width_pct"] <= 5.21),
        bool(metrics.get("signal_range_atr20") is not None and metrics["signal_range_atr20"] <= 1.66),
        bool(extension_atr is not None and extension_atr <= 0.5),
    ]

    rs3 = _finite(context.get("rs_score_3m"))
    rs6 = _finite(context.get("rs_score_6m"))
    rs_improving = bool(
        context.get("rs_turning_up") is True
        or context.get("rs_line_at_52w_high") is True
        or (rs3 is not None and rs6 is not None and rs3 > rs6 and rs3 > 0)
    )
    slope = _finite(context.get("weekly_30w_slope_pct"))
    rvol = _finite(assessment.get("relative_volume"))
    extension_pct = _finite(assessment.get("extension_above_bundle_pct"))
    fresh_checks = [
        True,  # The assessment is always anchored to the current signal bar.
        rs_improving,
        bool(slope is not None and slope >= 0.0),
        bool(rvol is not None and rvol >= 1.25),
        bool(extension_pct is not None and extension_pct <= 7.0),
    ]

    scores = {
        "recovery_reclaim": round(100.0 * sum(recovery_checks) / len(recovery_checks), 1),
        "tight_efficient": round(100.0 * sum(tight_checks) / len(tight_checks), 1),
        "fresh_momentum": round(100.0 * sum(fresh_checks) / len(fresh_checks), 1),
    }
    eligible = {
        "recovery_reclaim": sum(recovery_checks) >= 3,
        "tight_efficient": sum(tight_checks) >= 3,
        "fresh_momentum": sum(fresh_checks) >= 4,
    }
    order = ("recovery_reclaim", "tight_efficient", "fresh_momentum")
    choices = [name for name in order if eligible[name]]
    if not choices:
        return "balanced", max(scores.values(), default=0.0), scores
    archetype = sorted(choices, key=lambda name: (-scores[name], order.index(name)))[0]
    return archetype, scores[archetype], scores


def build_ma_cluster_research_profile(
    frame: pd.DataFrame,
    *,
    timeframe: str,
    assessment: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an auditable profile using only bars through the signal close."""
    context = context or {}
    unavailable = {
        "version": PREFERRED_PROFILE_VERSION,
        "timeframe": timeframe,
        "points": None,
        "score": None,
        "coverage": 0,
        "components": {},
        "metrics": {},
        "archetype": None,
        "archetype_confidence": None,
        "archetype_scores": {},
        "warnings": ["insufficient_research_data"],
        "search_text": "preferred breakout research fit",
        "source": PREFERRED_PROFILE_SOURCE,
    }
    if not assessment.get("available"):
        return unavailable
    clean = frame.dropna(subset=["open", "high", "low", "close", "volume"]).copy()
    signal_pos = len(clean) - 1
    if signal_pos < 1:
        return unavailable

    signal = clean.iloc[signal_pos]
    trigger = float(signal["high"]) * 1.001
    stop = _finite(assessment.get("research_stop_level"))
    risk = (trigger - stop) / trigger * 100.0 if stop is not None and 0 < stop < trigger else None

    prior_high_rows = clean["high"].astype(float).iloc[max(0, signal_pos - 252) : signal_pos]
    prior_high = float(prior_high_rows.max()) if len(prior_high_rows) >= 252 else None
    signal_close = float(signal["close"])
    distance = (signal_close / prior_high - 1.0) * 100.0 if prior_high and prior_high > 0 else None

    atr20 = _prior_atr20(clean, signal_pos)
    signal_range = float(signal["high"]) - float(signal["low"])
    range_atr = signal_range / atr20 if atr20 and atr20 > 0 else None
    bundle_top = _finite(assessment.get("bundle_top"))
    extension_atr = (signal_close - bundle_top) / atr20 if atr20 and bundle_top is not None else None

    prior_volume = clean["volume"].astype(float).iloc[max(0, signal_pos - 50) : signal_pos]
    avg_volume50 = float(prior_volume.mean()) if len(prior_volume) >= 50 else None
    volume_ratio50 = float(signal["volume"]) / avg_volume50 if avg_volume50 and avg_volume50 > 0 else None

    metrics: dict[str, float | None] = {
        "pattern_risk_pct": _finite(risk),
        "ma_width_pct": _finite(assessment.get("ma_width_pct")),
        "distance_to_prior_52w_high_pct": _finite(distance),
        "signal_range_atr20": _finite(range_atr),
        "signal_volume_ratio_50": _finite(volume_ratio50),
        "prior_up_volume_share_20": _finite(_prior_up_volume_share(clean, signal_pos)),
        "extension_atr": _finite(extension_atr),
        "trigger_reference_level": _finite(trigger),
        "research_stop_level": stop,
    }
    scored_metrics = {key: metrics.get(key) for key in _THRESHOLDS}
    coverage = sum(value is not None for value in scored_metrics.values())
    components: dict[str, bool | None] = {}
    for key, (operator, threshold) in _THRESHOLDS.items():
        value = metrics.get(key)
        components[key] = None if value is None else (value <= threshold if operator == "lte" else value >= threshold)
    points = sum(value is True for value in components.values()) if coverage == len(_THRESHOLDS) else None
    score = round(points / len(_THRESHOLDS) * 100.0, 1) if points is not None else None
    archetype, confidence, archetype_scores = _archetypes(
        metrics=metrics, assessment=assessment, context=context
    )
    warnings: list[str] = []
    if coverage < len(_THRESHOLDS):
        warnings.append("insufficient_research_data")
    if distance is not None and distance < -25.0:
        warnings.append("far_below_prior_52w_high")
    return {
        "version": PREFERRED_PROFILE_VERSION,
        "timeframe": timeframe,
        "points": points,
        "score": score,
        "coverage": coverage,
        "components": components,
        "metrics": {key: round(value, 4) if value is not None else None for key, value in metrics.items()},
        "archetype": archetype,
        "archetype_confidence": confidence,
        "archetype_scores": archetype_scores,
        "warnings": warnings,
        "search_text": f"preferred breakout research fit {archetype.replace('_', ' ')}",
        "source": PREFERRED_PROFILE_SOURCE,
    }


def choose_ma_cluster_research_profile(
    profiles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Choose the strongest complete timeframe profile without changing scan rank."""
    available = [profile for profile in profiles if isinstance(profile, dict)]
    if not available:
        return None
    return sorted(
        available,
        key=lambda profile: (
            profile.get("score") is None,
            -float(profile.get("score") or 0.0),
            0 if profile.get("timeframe") == "daily" else 1,
        ),
    )[0]
