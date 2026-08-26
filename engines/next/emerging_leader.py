#!/usr/bin/env python3
"""Transparent emerging-leader discovery for StockScout.

Two distinct archetypes can reach the top:

1) Neglected Emerging — a real lateral reset/base after a long period without
   sustained leadership, with RS beginning to turn before the move is extended.
2) Reset Reawakening — a stock that previously moved, then reset deeply, dried up,
   regained longer-term structure and suddenly re-ignites in RS/volume. This path
   intentionally does not require the local `baseWeeks` detector to see a long base.

The two path scores are exposed separately. `emergingLeaderScore` is the stronger
of the two after common risk caps. Group leadership and fundamentals remain separate
confirmation layers so the core stock score stays interpretable.

`opportunityScore` and `confluence` remain compatibility aliases for older UI/data
consumers. New UI should display Emerging Score and Evidence 0-5 instead.
"""
from __future__ import annotations

import math

MODEL = "emerging-leader-v1-dual-archetype"


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def optional_finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(value)))


def ramp(value, low, high):
    value = finite(value)
    if high <= low:
        return 0.0
    return clamp((value - low) / (high - low) * 100.0)


def inverse_ramp(value, good, bad):
    value = finite(value, bad)
    if bad <= good:
        return 0.0
    return clamp((bad - value) / (bad - good) * 100.0)


def band_score(value, lo, sweet_lo, sweet_hi, hi):
    value = finite(value, lo)
    if sweet_lo <= value <= sweet_hi:
        return 100.0
    if value < sweet_lo:
        return clamp((value - lo) / max(1e-9, sweet_lo - lo) * 100.0)
    return clamp((hi - value) / max(1e-9, hi - sweet_hi) * 100.0)


def weighted_available(items, default=50.0):
    usable = [(value, weight) for value, weight in items if value is not None]
    if not usable:
        return float(default)
    total = sum(weight for _, weight in usable)
    return sum(value * weight for value, weight in usable) / total


def neglect_component(value, good, bad):
    value = optional_finite(value)
    if value is None:
        return None
    return inverse_ramp(value, good, bad)


def stage_freshness(stage, stage2_age):
    if stage == 1:
        return 100.0
    if stage == 2 and stage2_age <= 12:
        return 100.0
    if stage == 2 and stage2_age <= 24:
        return 55.0
    if stage == 2:
        return 15.0
    return 0.0


def score_row(row: dict) -> dict:
    stage = int(finite(row.get("stage")))
    stage2_age = finite(row.get("stage2AgeWeeks"))
    base_weeks = finite(row.get("baseWeeks"))
    lateral = finite(row.get("lateralBaseScore"))
    contraction = finite(row.get("contractionQuality"))
    rs_rank = finite(row.get("rsRank"))
    rs_accel = finite(row.get("rsAcceleration"))
    rs_from_high = finite(row.get("rsFromHigh"), -100.0)
    breakout = finite(row.get("breakoutPct"), -100.0)
    d10 = finite(row.get("distance10w"), finite(row.get("distance50")))
    d30 = finite(row.get("distance30w"))
    volume_ratio = finite(row.get("volumeRatio"), 1.0)
    volume_dry = finite(row.get("volumeDryUp"), 1.0)
    return3m = finite(row.get("return3m"))
    from_high = finite(row.get("from52wHigh"), breakout)
    extended = bool(row.get("extended")) or d10 > 12 or d30 > 22
    fresh = stage_freshness(stage, stage2_age)

    # ------------------------------------------------------------------
    # Path A: NEGLECTED EMERGING
    # ------------------------------------------------------------------
    reset_score = 0.60 * lateral + 0.40 * contraction
    rs_turn_score = (
        0.45 * ramp(rs_rank, 55, 95)
        + 0.40 * ramp(rs_accel, -0.15, 0.90)
        + 0.15 * ramp(rs_from_high, -18, -2)
    )
    neglect_history_score = weighted_available(
        [
            (neglect_component(row.get("return5y"), 50, 250), 0.35),
            (neglect_component(row.get("return3y"), 30, 150), 0.30),
            (neglect_component(row.get("return2y"), 20, 100), 0.20),
            (neglect_component(row.get("prior9mReturn"), 20, 80), 0.15),
        ]
    )
    trigger_readiness_score = (
        0.45 * band_score(breakout, -15, -6, 4, 10)
        + 0.30 * band_score(d10, -10, -4, 8, 14)
        + 0.15 * ramp(volume_ratio, 0.60, 2.00)
        + 0.10 * band_score(d30, -15, -5, 15, 24)
    )
    neglected_score = (
        0.30 * reset_score
        + 0.25 * rs_turn_score
        + 0.20 * neglect_history_score
        + 0.15 * trigger_readiness_score
        + 0.10 * fresh
    )
    if base_weeks < 8:
        neglected_score = min(neglected_score - 12, 50)
    elif base_weeks < 12:
        neglected_score -= 5
    if d10 < -12:
        neglected_score = min(neglected_score - 8, 55)
    if breakout < -25:
        neglected_score = min(neglected_score, 55)
    neglected_score = clamp(neglected_score)

    neglected_flags = {
        "Base reset": bool(base_weeks >= 16 and reset_score >= 55),
        "RS waking": bool(rs_rank >= 65 and rs_accel > 0),
        "Near trigger": bool(trigger_readiness_score >= 65),
        "Neglected history": bool(neglect_history_score >= 65),
        "Fresh stage": bool(stage == 1 or (stage == 2 and stage2_age <= 12)),
    }
    neglected_evidence = sum(neglected_flags.values())
    neglected_candidate = bool(
        neglected_score >= 60
        and neglected_evidence >= 4
        and stage in (1, 2)
        and not extended
        and base_weeks >= 12
        and rs_rank >= 60
        and rs_accel > 0
        and reset_score >= 45
        and trigger_readiness_score >= 55
    )

    # ------------------------------------------------------------------
    # Path B: RESET REAWAKENING
    # ------------------------------------------------------------------
    # This catches patterns such as a prior leader that went through a deep reset,
    # dried up, regained the 30W area and then wakes sharply in RS and volume. The
    # existing baseWeeks detector can legitimately be only 1-2 weeks in this setup.
    reset_depth_score = band_score(from_high, -80, -65, -20, -5)
    long_term_position_score = band_score(d30, -15, -5, 18, 28)
    reawakening_structure_score = (
        0.45 * reset_depth_score
        + 0.35 * long_term_position_score
        + 0.20 * contraction
    )
    ignition_score = (
        0.40 * ramp(rs_rank, 60, 95)
        + 0.35 * ramp(rs_accel, 0.0, 1.0)
        + 0.25 * ramp(volume_ratio, 0.8, 3.0)
    )
    recovery_score = (
        0.65 * ramp(return3m, -5, 25)
        + 0.35 * long_term_position_score
    )
    dry_reset_score = (
        0.55 * inverse_ramp(volume_dry, 0.80, 1.30)
        + 0.45 * contraction
    )
    reawakening_score = (
        0.30 * ignition_score
        + 0.25 * reawakening_structure_score
        + 0.20 * recovery_score
        + 0.15 * dry_reset_score
        + 0.10 * fresh
    )

    reawakening_flags = {
        "Deep reset": bool(-75 <= from_high <= -18 and long_term_position_score >= 60),
        "RS ignition": bool(rs_rank >= 75 and rs_accel >= 0.30),
        "Volume wake-up": bool(volume_ratio >= 1.5),
        "Structural recovery": bool(d30 >= -8 and return3m > 0),
        "Fresh stage": bool(stage == 1 or (stage == 2 and stage2_age <= 12)),
    }
    reawakening_evidence = sum(reawakening_flags.values())
    reawakening_candidate = bool(
        reawakening_score >= 65
        and reawakening_evidence >= 4
        and stage in (1, 2)
        and not extended
        and rs_rank >= 70
        and rs_accel > 0
        and volume_ratio >= 1.2
        and d30 >= -10
        and return3m > -2
        and from_high <= -15
        and reawakening_structure_score >= 55
    )

    # Shared safety caps. A dual-path model should widen discovery, not permit
    # obvious deterioration or mature/chased trends to dominate.
    if rs_rank < 50:
        neglected_score = min(neglected_score - 10, 45)
        reawakening_score = min(reawakening_score - 10, 45)
    elif rs_rank < 55:
        neglected_score -= 10
        reawakening_score -= 10
    if rs_accel <= -0.25:
        neglected_score = min(neglected_score - 15, 55)
        reawakening_score = min(reawakening_score - 15, 55)
    elif rs_accel <= 0:
        neglected_score -= 15
        reawakening_score -= 15
    if stage == 2 and stage2_age > 24:
        neglected_score -= 10
        reawakening_score -= 10
    if stage not in (1, 2):
        neglected_score = min(neglected_score, 30)
        reawakening_score = min(reawakening_score, 30)
    if extended:
        neglected_score = min(neglected_score - 20, 35)
        reawakening_score = min(reawakening_score - 20, 35)

    neglected_score = round(clamp(neglected_score), 1)
    reawakening_score = round(clamp(reawakening_score), 1)

    # Pick the stronger explanatory path, not an opaque blend of both.
    if reawakening_score > neglected_score:
        archetype = "Reset Reawakening"
        score = reawakening_score
        evidence_flags = reawakening_flags
        evidence_count = reawakening_evidence
        candidate = reawakening_candidate
    else:
        archetype = "Neglected Emerging"
        score = neglected_score
        evidence_flags = neglected_flags
        evidence_count = neglected_evidence
        candidate = neglected_candidate

    # A+ remains deliberately strict. For Reset Reawakening, require all five
    # evidence pillars because it can otherwise be a volatile false wake-up.
    a_plus = bool(candidate and score >= 72 and evidence_count == 5)

    reasons = []
    for label, passed in evidence_flags.items():
        if passed:
            reasons.append(label)
    if archetype == "Neglected Emerging":
        reasons.append(f"Reset {reset_score:.0f} / Trigger {trigger_readiness_score:.0f}")
    else:
        reasons.append(f"Ignition {ignition_score:.0f} / Reset {reawakening_structure_score:.0f}")
    if extended:
        reasons.append("Extended cap")
    elif rs_accel <= 0:
        reasons.append("RS not accelerating")

    return {
        "emergingLeaderScore": score,
        "opportunityScore": score,
        "emergingArchetype": archetype,
        "neglectedEmergingScore": neglected_score,
        "resetReawakeningScore": reawakening_score,
        "resetScore": round(clamp(reset_score), 1),
        "rsTurnScore": round(clamp(rs_turn_score), 1),
        "neglectHistoryScore": round(clamp(neglect_history_score), 1),
        "triggerReadinessScore": round(clamp(trigger_readiness_score), 1),
        "reawakeningStructureScore": round(clamp(reawakening_structure_score), 1),
        "ignitionScore": round(clamp(ignition_score), 1),
        "recoveryScore": round(clamp(recovery_score), 1),
        "dryResetScore": round(clamp(dry_reset_score), 1),
        "stageFreshnessScore": round(clamp(fresh), 1),
        "emergingEvidenceCount": int(evidence_count),
        "emergingEvidenceFlags": evidence_flags,
        "emergingLeaderCandidate": bool(candidate),
        "aPlusEmergingSetup": a_plus,
        "emergingReasons": reasons[:6],
        "confluence": int(evidence_count),
        "perfect": a_plus,
    }
