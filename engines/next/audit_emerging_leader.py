#!/usr/bin/env python3
"""Audit canonical dual-archetype Emerging Leader score invariants."""
from __future__ import annotations

import json
import math
from pathlib import Path

DATA = Path("frontend/public/data/latest.json")
MODEL = "emerging-leader-v1-dual-archetype"
OPPORTUNITY_V2_MODEL = "stockscout-opportunity-v2-potential-timing"
REQUIRED = (
    "emergingLeaderScore",
    "emergingArchetype",
    "neglectedEmergingScore",
    "resetReawakeningScore",
    "resetScore",
    "rsTurnScore",
    "neglectHistoryScore",
    "triggerReadinessScore",
    "reawakeningStructureScore",
    "ignitionScore",
    "stageFreshnessScore",
    "emergingEvidenceCount",
    "emergingLeaderCandidate",
    "aPlusEmergingSetup",
    "emergingReasons",
)


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def main():
    if not DATA.exists():
        print(f"Missing {DATA}")
        return 1

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    if payload.get("emergingLeaderModel") != MODEL:
        print(f"Emerging Leader model mismatch: {payload.get('emergingLeaderModel')}")
        return 1
    if not rows:
        print("No universe rows")
        return 1

    opportunity_v2_active = payload.get("opportunityModel") == OPPORTUNITY_V2_MODEL
    errors = []
    present = 0
    for row in rows:
        ticker = str(row.get("ticker") or "?")
        if all(key in row for key in REQUIRED):
            present += 1
        else:
            errors.append(f"{ticker}: missing {[key for key in REQUIRED if key not in row]}")
            continue

        score = finite(row.get("emergingLeaderScore"))
        opp = finite(row.get("opportunityScore"))
        evidence = finite(row.get("emergingEvidenceCount"))
        confluence = finite(row.get("confluence"))
        neglected_score = finite(row.get("neglectedEmergingScore"))
        reawakening_score = finite(row.get("resetReawakeningScore"))
        archetype = str(row.get("emergingArchetype"))
        numeric_fields = [
            neglected_score,
            reawakening_score,
            finite(row.get("resetScore")),
            finite(row.get("rsTurnScore")),
            finite(row.get("neglectHistoryScore")),
            finite(row.get("triggerReadinessScore")),
            finite(row.get("reawakeningStructureScore")),
            finite(row.get("ignitionScore")),
            finite(row.get("stageFreshnessScore")),
        ]
        if score is None or not 0 <= score <= 100:
            errors.append(f"{ticker}: invalid emerging score {score}")
            continue
        if any(v is None or not 0 <= v <= 100 for v in numeric_fields):
            errors.append(f"{ticker}: invalid component score")
        if archetype not in ("Neglected Emerging", "Reset Reawakening"):
            errors.append(f"{ticker}: invalid archetype {archetype}")
        if neglected_score is not None and reawakening_score is not None:
            if abs(score - max(neglected_score, reawakening_score)) > 1e-9:
                errors.append(f"{ticker}: selected score is not strongest archetype")
        # Before Opportunity v2, opportunityScore was a compatibility alias for
        # emergingLeaderScore. Under Opportunity v2 it is intentionally independent.
        if not opportunity_v2_active and (opp is None or abs(score - opp) > 1e-9):
            errors.append(f"{ticker}: opportunity compatibility alias diverged")
        if opportunity_v2_active and (opp is None or not 0 <= opp <= 100):
            errors.append(f"{ticker}: invalid Opportunity v2 score {opp}")
        if evidence is None or evidence < 0 or evidence > 5 or int(evidence) != evidence:
            errors.append(f"{ticker}: invalid evidence count {evidence}")
        if confluence is None or evidence is None or confluence != evidence:
            errors.append(f"{ticker}: confluence compatibility alias diverged")

        extended = bool(row.get("extended"))
        base_weeks = finite(row.get("baseWeeks")) or 0.0
        accel = finite(row.get("rsAcceleration")) or 0.0
        rs = finite(row.get("rsRank")) or 0.0
        volume = finite(row.get("volumeRatio")) or 0.0
        stage = int(finite(row.get("stage")) or 0)
        age = finite(row.get("stage2AgeWeeks")) or 0.0
        d30 = finite(row.get("distance30w")) or 0.0
        return3m = finite(row.get("return3m")) or 0.0
        from_high = finite(row.get("from52wHigh"))
        reset = finite(row.get("resetScore")) or 0.0
        trigger = finite(row.get("triggerReadinessScore")) or 0.0
        reawakening_structure = finite(row.get("reawakeningStructureScore")) or 0.0

        if extended and score > 35.01:
            errors.append(f"{ticker}: extended score cap violated ({score})")
        if accel <= -0.25 and score > 55.01:
            errors.append(f"{ticker}: negative-RS-acceleration cap violated ({score})")
        if stage not in (1, 2) and score > 30.01:
            errors.append(f"{ticker}: off-regime score cap violated ({score})")

        candidate = bool(row.get("emergingLeaderCandidate"))
        if candidate:
            common = score >= 60 and evidence is not None and evidence >= 4 and stage in (1, 2) and not extended and accel > 0
            if archetype == "Neglected Emerging":
                gate_ok = common and base_weeks >= 12 and rs >= 60 and reset >= 45 and trigger >= 55
            else:
                gate_ok = (
                    common
                    and score >= 65
                    and rs >= 70
                    and volume >= 1.2
                    and d30 >= -10
                    and return3m > -2
                    and from_high is not None and from_high <= -15
                    and reawakening_structure >= 55
                )
            if not gate_ok:
                errors.append(f"{ticker}: {archetype} candidate violates gate")

        a_plus = bool(row.get("aPlusEmergingSetup"))
        if a_plus and not (candidate and score >= 72 and evidence == 5):
            errors.append(f"{ticker}: A+ setup violates gate")
        if stage == 2 and age > 12 and bool((row.get("emergingEvidenceFlags") or {}).get("Fresh stage")):
            errors.append(f"{ticker}: mature Stage 2 marked fresh")

    coverage = present / len(rows) * 100.0
    if coverage < 95:
        errors.append(f"Required-field coverage too low: {coverage:.1f}%")

    market = payload.get("market") or {}
    candidate_count = sum(bool(r.get("emergingLeaderCandidate")) for r in rows)
    neglected_count = sum(bool(r.get("emergingLeaderCandidate")) and r.get("emergingArchetype") == "Neglected Emerging" for r in rows)
    reawakening_count = sum(bool(r.get("emergingLeaderCandidate")) and r.get("emergingArchetype") == "Reset Reawakening" for r in rows)
    a_plus_count = sum(bool(r.get("aPlusEmergingSetup")) for r in rows)
    if int(market.get("emergingLeaderCandidates", -1)) != candidate_count:
        errors.append("Market emergingLeaderCandidates count mismatch")
    if int(market.get("neglectedEmergingCandidates", -1)) != neglected_count:
        errors.append("Market neglectedEmergingCandidates count mismatch")
    if int(market.get("resetReawakeningCandidates", -1)) != reawakening_count:
        errors.append("Market resetReawakeningCandidates count mismatch")
    if int(market.get("aPlusEmergingSetups", -1)) != a_plus_count:
        errors.append("Market aPlusEmergingSetups count mismatch")

    print(
        f"Emerging Leader audit: rows={len(rows)} coverage={coverage:.1f}% "
        f"candidates={candidate_count} neglected={neglected_count} "
        f"reawakening={reawakening_count} A+={a_plus_count} "
        f"opportunity_v2={opportunity_v2_active} errors={len(errors)}"
    )
    for error in errors[:30]:
        print(f"  ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
