#!/usr/bin/env python3
"""Build StockScout Opportunity v2 from setup potential + current timing.

The existing Emerging Leader model remains intact as a discovery model.
Opportunity v2 answers a narrower question: how compelling is this stock now?
It separates structural Potential from current Timing, then allows only bounded
Group/Fundamental confirmation modifiers. Mature, extended or deteriorating
setups are capped rather than hidden inside an opaque blend.

Calibration note: the raw Timing composite is mapped to the useful 0-100 range
with ``22 + 1.15 * raw``. On the 2026-08-19 2,012-stock validation universe this
produced only 3 PRIME (90+) names and 45 READY (80+) names, keeping the top band
intentionally scarce while preserving a broad WATCH layer.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median
from typing import Any

DATA = Path("frontend/public/data/latest.json")
MODEL = "stockscout-opportunity-v2-potential-timing"


def finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else float(default)
    except Exception:
        return float(default)


def optional(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def ramp(value: Any, low: float, high: float) -> float:
    value = finite(value)
    if high <= low:
        return 0.0
    return clamp((value - low) / (high - low) * 100.0)


def band(value: Any, lo: float, sweet_lo: float, sweet_hi: float, hi: float) -> float:
    value = finite(value, lo)
    if sweet_lo <= value <= sweet_hi:
        return 100.0
    if value < sweet_lo:
        return clamp((value - lo) / max(1e-9, sweet_lo - lo) * 100.0)
    return clamp((hi - value) / max(1e-9, hi - sweet_hi) * 100.0)


def weighted_available(items: list[tuple[float | None, float]], default: float = 50.0) -> float:
    usable = [(value, weight) for value, weight in items if value is not None]
    if not usable:
        return float(default)
    total = sum(weight for _, weight in usable)
    return sum(float(value) * weight for value, weight in usable) / total


def room_to_run(row: dict) -> float:
    return (
        0.45 * band(row.get("distance10w", row.get("distance50")), -12, -4, 7, 14)
        + 0.35 * band(row.get("distance30w"), -18, -5, 14, 25)
        + 0.20 * band(row.get("breakoutPct"), -20, -7, 4, 12)
    )


def potential_score(row: dict) -> tuple[float, str]:
    """Structural setup quality, intentionally less sensitive to today's ignition."""
    fresh = finite(row.get("stageFreshnessScore"), 50)
    room = room_to_run(row)
    neglected = (
        0.40 * finite(row.get("resetScore"), 50)
        + 0.30 * finite(row.get("neglectHistoryScore"), 50)
        + 0.15 * fresh
        + 0.15 * room
    )
    reawakening = (
        0.40 * finite(row.get("reawakeningStructureScore"), 50)
        + 0.25 * finite(row.get("dryResetScore"), 50)
        + 0.20 * finite(row.get("recoveryScore"), 50)
        + 0.15 * fresh
    )
    archetype = str(row.get("emergingArchetype") or "")
    if archetype == "Reset Reawakening":
        return clamp(reawakening), archetype
    if archetype == "Neglected Emerging":
        return clamp(neglected), archetype
    if reawakening > neglected:
        return clamp(reawakening), "Reset Reawakening"
    return clamp(neglected), "Neglected Emerging"


def timing_score(row: dict) -> tuple[float, dict[str, float]]:
    """Current ignition/readiness: RS, MA cluster, volume and trigger proximity."""
    rs = 0.55 * ramp(row.get("rsRank"), 55, 95) + 0.45 * ramp(
        row.get("rsAcceleration"), -0.10, 0.90
    )
    phase_score = {
        "ENTRY": 100.0,
        "READY": 82.0,
        "WATCH": 62.0,
    }.get(str(row.get("maClusterPhase") or "").upper(), 42.0)
    ma = weighted_available(
        [(optional(row.get("maClusterScore")), 0.65), (phase_score, 0.35)]
    )
    volume = weighted_available(
        [
            (ramp(row.get("volumeRatio", 1.0), 0.75, 2.25), 0.70),
            (ramp(row.get("maClusterVolumePace", 1.0), 0.75, 1.75), 0.30),
        ]
    )
    trigger = weighted_available(
        [
            (band(row.get("breakoutPct"), -15, -5, 4, 10), 0.50),
            (
                band(row.get("distance10w", row.get("distance50")), -10, -4, 8, 14),
                0.30,
            ),
            (
                band(row.get("maClusterPricePct"), -8, -2, 4, 9)
                if optional(row.get("maClusterPricePct")) is not None
                else None,
                0.20,
            ),
        ]
    )
    raw = 0.35 * rs + 0.25 * ma + 0.20 * volume + 0.20 * trigger
    calibrated = clamp(22.0 + 1.15 * raw)
    return calibrated, {
        "raw": round(clamp(raw), 1),
        "rs": round(clamp(rs), 1),
        "ma": round(clamp(ma), 1),
        "volume": round(clamp(volume), 1),
        "trigger": round(clamp(trigger), 1),
    }


def modifier(score: Any, confidence: Any, max_points: float = 5.0) -> float:
    s = optional(score)
    c = optional(confidence)
    if s is None or c is None:
        return 0.0
    out = ((s - 50.0) / 50.0) * max_points * clamp(c, 0, 100) / 100.0
    return max(-max_points, min(max_points, out))


def score_row(row: dict) -> dict:
    potential, path = potential_score(row)
    timing, timing_parts = timing_score(row)
    group_mod = modifier(row.get("groupRank"), row.get("groupConfidence"))
    fund_mod = modifier(
        row.get("fundamentalEvidenceScore"), row.get("fundamentalEvidenceConfidence")
    )

    penalty = 0.0
    caps: list[tuple[float, str]] = []
    reasons: list[str] = [f"{path} potential", f"Timing {timing:.0f}"]
    stage = int(finite(row.get("stage")))
    stage2_age = finite(row.get("stage2AgeWeeks"))
    rs_accel = finite(row.get("rsAcceleration"))
    d10 = finite(row.get("distance10w", row.get("distance50")))
    d30 = finite(row.get("distance30w"))
    extended = bool(row.get("extended")) or d10 > 12 or d30 > 22

    if stage == 2 and stage2_age > 30:
        penalty += 10
        reasons.append("Mature Stage 2 -10")
    elif stage == 2 and stage2_age > 24:
        penalty += 5
        reasons.append("Maturing Stage 2 -5")
    if stage not in (1, 2):
        caps.append((45, "Stage 3/4 cap"))
    if rs_accel <= -0.25:
        caps.append((50, "RS deterioration cap"))
    elif rs_accel <= 0:
        caps.append((65, "RS not accelerating cap"))
    if extended:
        caps.append((50, "Extended cap"))
    elif d10 > 10 or d30 > 19:
        caps.append((60, "Late extension cap"))
    if d10 < -15:
        caps.append((55, "Below structure cap"))

    raw = 0.55 * potential + 0.45 * timing + group_mod + fund_mod - penalty
    final = clamp(raw)
    for cap_value, cap_reason in caps:
        if final > cap_value:
            final = cap_value
            reasons.append(cap_reason)

    return {
        "opportunityPotential": round(potential, 1),
        "opportunityTiming": round(timing, 1),
        "opportunityTimingRaw": timing_parts["raw"],
        "opportunityTimingRS": timing_parts["rs"],
        "opportunityTimingMA": timing_parts["ma"],
        "opportunityTimingVolume": timing_parts["volume"],
        "opportunityTimingTrigger": timing_parts["trigger"],
        "opportunityGroupModifier": round(group_mod, 1),
        "opportunityFundModifier": round(fund_mod, 1),
        "opportunityPenalty": round(penalty, 1),
        "opportunityRawScore": round(clamp(raw), 1),
        "opportunityScore": round(final, 1),
        "opportunityReasons": reasons[:6],
    }


def assign_ranks(rows: list[dict]) -> None:
    ordered = sorted(rows, key=lambda row: finite(row.get("opportunityScore")))
    n = len(ordered)
    i = 0
    while i < n:
        score = finite(ordered[i].get("opportunityScore"))
        j = i + 1
        while j < n and finite(ordered[j].get("opportunityScore")) == score:
            j += 1
        avg_pos = (i + j - 1) / 2.0
        rank = 99 if n <= 1 else int(round(1 + 98 * avg_pos / (n - 1)))
        for k in range(i, j):
            ordered[k]["opportunityRank"] = rank
        i = j


def tier(score: float) -> str:
    if score >= 90:
        return "PRIME"
    if score >= 80:
        return "READY"
    if score >= 70:
        return "WATCH"
    if score >= 55:
        return "EARLY"
    return "PASS"


def apply(payload: dict) -> dict:
    rows = payload.get("universe") or []
    for row in rows:
        base_emerging = finite(
            row.get("emergingLeaderScore", row.get("opportunityScore", row.get("score")))
        )
        row["emergingLeaderScore"] = round(base_emerging, 1)
        row.update(score_row(row))
    assign_ranks(rows)

    counts = {name: 0 for name in ("PRIME", "READY", "WATCH", "EARLY", "PASS")}
    for row in rows:
        row["opportunityTier"] = tier(finite(row.get("opportunityScore")))
        counts[row["opportunityTier"]] += 1
        stockscout = row.get("stockscout") if isinstance(row.get("stockscout"), dict) else {}
        stockscout["opportunityV2"] = {
            "model": MODEL,
            "score": row["opportunityScore"],
            "rank": row["opportunityRank"],
            "tier": row["opportunityTier"],
            "potential": row["opportunityPotential"],
            "timing": row["opportunityTiming"],
            "groupModifier": row["opportunityGroupModifier"],
            "fundamentalModifier": row["opportunityFundModifier"],
        }
        row["stockscout"] = stockscout

    payload["opportunityModel"] = MODEL
    market = payload.setdefault("market", {})
    scores = [finite(row.get("opportunityScore")) for row in rows]
    potentials = [finite(row.get("opportunityPotential")) for row in rows]
    timings = [finite(row.get("opportunityTiming")) for row in rows]
    market["opportunityV2"] = {
        "model": MODEL,
        "rows": len(rows),
        "weights": {"potential": 0.55, "timing": 0.45},
        "timingCalibration": {"intercept": 22.0, "slope": 1.15},
        "maxGroupModifier": 5,
        "maxFundamentalModifier": 5,
        "tierCounts": counts,
        "medianScore": round(median(scores), 1) if scores else 0,
        "medianPotential": round(median(potentials), 1) if potentials else 0,
        "medianTiming": round(median(timings), 1) if timings else 0,
    }
    if isinstance(market.get("fundamentalEvidence"), dict):
        market["fundamentalEvidence"]["usedByOpportunityV2AsBoundedModifier"] = True
    market["groupUsedByOpportunityV2AsBoundedModifier"] = True
    return payload


def main(path: Path = DATA) -> None:
    if not path.exists():
        raise SystemExit(f"Missing canonical dataset: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    apply(payload)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    temp.replace(path)
    stats = payload.get("market", {}).get("opportunityV2", {})
    print(
        f"Opportunity v2 ready: median={stats.get('medianScore')}; "
        f"potential={stats.get('medianPotential')}; timing={stats.get('medianTiming')}; "
        f"tiers={stats.get('tierCounts')}"
    )


if __name__ == "__main__":
    main()
