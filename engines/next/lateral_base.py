#!/usr/bin/env python3
"""Observational Lateral Base / Contraction model for StockScout.

This module intentionally does not alter Opportunity, Confluence, LEGACY, or the
original scanner. It converts already-exported transparent StockScout features
into three bounded diagnostics:

- lateralBaseScore: maturity + depth + lateral tightness
- contractionQuality: ATR/range/volume contraction quality
- launchReadiness: RS/price/volume readiness without rewarding extension

The combined candidate flag is for discovery only until validated empirically.
"""
from __future__ import annotations

import math

MODEL = "lateral-base-v1-observational"


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, value))


def ramp(value, low, high):
    """0 below low, 100 at/above high."""
    value = finite(value)
    if high <= low:
        return 0.0
    return clamp((value - low) / (high - low) * 100.0)


def inverse_ramp(value, good, bad):
    """100 at/below good, 0 at/above bad."""
    value = finite(value)
    if bad <= good:
        return 0.0
    return clamp((bad - value) / (bad - good) * 100.0)


def band_score(value, lo, sweet_lo, sweet_hi, hi):
    """Prefer a band while degrading smoothly outside it."""
    value = finite(value)
    if sweet_lo <= value <= sweet_hi:
        return 100.0
    if value < sweet_lo:
        return clamp((value - lo) / max(1e-9, sweet_lo - lo) * 100.0)
    return clamp((hi - value) / max(1e-9, hi - sweet_hi) * 100.0)


def score_row(row: dict) -> dict:
    stage = int(finite(row.get("stage")))
    age = finite(row.get("stage2AgeWeeks"))
    base_weeks = finite(row.get("baseWeeks"))
    depth = finite(row.get("baseDepthPct"), 100.0)
    range20 = finite(row.get("tightRange20"), 100.0)
    range60 = finite(row.get("tightRange60"), 100.0)
    atr_comp = finite(row.get("atrCompression"))
    contraction = finite(row.get("contraction"))
    vcp = finite(row.get("vcpScore"))
    volume_dry = finite(row.get("volumeDryUp"), 1.0)
    d10 = finite(row.get("distance10w"), finite(row.get("distance50")))
    d30 = finite(row.get("distance30w"))
    rs = finite(row.get("rsRank"))
    accel = finite(row.get("rsAcceleration"))
    rs_high = finite(row.get("rsFromHigh"), -100.0)
    breakout = finite(row.get("breakoutPct"), -100.0)
    vol_ratio = finite(row.get("volumeRatio"), 1.0)
    ret3 = finite(row.get("return3m"))
    prior9 = finite(row.get("prior9mReturn"))
    extended = bool(row.get("extended")) or d10 > 12 or d30 > 22

    # 1) Base structure: long enough to matter, but not simply rewarding ancient drift.
    maturity = band_score(base_weeks, 6, 16, 52, 90)
    depth_quality = band_score(depth, 4, 8, 30, 55)
    lateral_tightness = 0.60 * inverse_ramp(range60, 12, 40) + 0.40 * inverse_ramp(range20, 6, 18)
    lateral_base = 0.40 * maturity + 0.30 * depth_quality + 0.30 * lateral_tightness

    # 2) Contraction: combines three independent manifestations; dry-up is best around <=0.85.
    atr_quality = clamp(max(atr_comp, contraction, vcp))
    short_tightness = inverse_ramp(range20, 6, 18)
    dry_quality = inverse_ramp(volume_dry, 0.80, 1.20)
    contraction_quality = 0.45 * atr_quality + 0.30 * short_tightness + 0.25 * dry_quality

    # 3) Launch readiness: strength improving near a trigger, but do not reward extension.
    rs_level = ramp(rs, 55, 90)
    rs_accel = ramp(accel, -0.10, 0.80)
    rs_near_high = ramp(rs_high, -12, -1)
    trigger_nearness = band_score(breakout, -12, -3, 4, 10)
    volume_confirm = ramp(vol_ratio, 0.80, 2.00)
    ma_position = band_score(d10, -10, -4, 8, 15)
    early_regime = 100.0 if stage == 1 else (100.0 if stage == 2 and age <= 12 else 55.0 if stage == 2 and age <= 24 else 20.0)
    launch_readiness = (
        0.22 * rs_level + 0.18 * rs_accel + 0.12 * rs_near_high +
        0.18 * trigger_nearness + 0.12 * volume_confirm + 0.10 * ma_position +
        0.08 * early_regime
    )

    # Neglect/awakening is diagnostic only; it helps explain candidates but does not
    # feed Opportunity/Confluence. Reward subdued prior 9M plus constructive recent turn.
    neglect = inverse_ramp(max(0.0, prior9), 5, 35)
    awakening = 0.55 * ramp(ret3, 0, 20) + 0.45 * rs_accel
    neglected_launch = 0.45 * neglect + 0.30 * lateral_base + 0.25 * awakening

    if extended:
        launch_readiness *= 0.35
        neglected_launch *= 0.50

    candidate = (
        not extended
        and stage in (1, 2)
        and base_weeks >= 12
        and lateral_base >= 60
        and contraction_quality >= 50
        and launch_readiness >= 55
        and rs >= 60
    )

    reasons = []
    if base_weeks >= 20:
        reasons.append(f"Base {int(round(base_weeks))}w")
    if range20 <= 10:
        reasons.append(f"20D range {range20:.1f}%")
    if atr_quality >= 50:
        reasons.append(f"Contraction {atr_quality:.0f}")
    if volume_dry <= 0.95:
        reasons.append(f"Vol dry-up {volume_dry:.2f}x")
    if rs >= 70:
        reasons.append(f"RS {rs:.0f}")
    if accel > 0:
        reasons.append("RS accelerating")
    if -3 <= breakout <= 5:
        reasons.append("Near breakout")
    if extended:
        reasons.append("Extended penalty")

    return {
        "lateralBaseScore": round(clamp(lateral_base), 1),
        "contractionQuality": round(clamp(contraction_quality), 1),
        "launchReadiness": round(clamp(launch_readiness), 1),
        "neglectedLaunchScore": round(clamp(neglected_launch), 1),
        "lateralBaseCandidate": bool(candidate),
        "lateralBaseReasons": reasons[:6],
    }
