#!/usr/bin/env python3
"""Integration audit for the canonical StockScout model stack.

This gate checks the *combined* post-enrichment dataset rather than assuming old
compatibility aliases still describe the active model. In Opportunity v2,
Emerging Leader remains structural discovery evidence while opportunityScore is a
separate Potential + Timing score with bounded group/fundamental modifiers.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median
from typing import Any

DATA = Path("frontend/public/data/latest.json")
OPPORTUNITY_MODEL = "stockscout-opportunity-v2-potential-timing"
EMERGING_MODEL = "emerging-leader-v1-dual-archetype"
GROUP_MODEL = "behavioral-proxy-v2-confidence"
LATERAL_MODEL = "lateral-base-v1-observational"
OPP_TIERS = ((90, "PRIME"), (80, "READY"), (70, "WATCH"), (55, "EARLY"))


def finite(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def expected_tier(score: float) -> str:
    for floor, label in OPP_TIERS:
        if score >= floor:
            return label
    return "PASS"


def expected_group_summary(rows: list[dict], proxy_field: str, proxy: str) -> tuple[float, list[str]]:
    members = [row for row in rows if str(row.get(proxy_field) or "") == proxy]
    scored = [row for row in members if finite(row.get("opportunityScore")) is not None]
    scores = [float(row["opportunityScore"]) for row in scored]
    ranked = sorted(
        scored,
        key=lambda row: (
            finite(row.get("opportunityScore")) or 0.0,
            finite(row.get("opportunityTiming")) or 0.0,
            finite(row.get("rsRank")) or 0.0,
        ),
        reverse=True,
    )
    return (
        round(median(scores), 1) if scores else 0.0,
        [str(row.get("ticker")) for row in ranked[:8] if row.get("ticker")],
    )


def main() -> int:
    if not DATA.exists():
        print(f"Missing {DATA}")
        return 1
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    errors: list[str] = []
    if not rows:
        errors.append("empty universe")

    opportunity_model = payload.get("opportunityModel")
    if opportunity_model != OPPORTUNITY_MODEL:
        errors.append(f"unexpected opportunity model: {opportunity_model}")

    opp_required = (
        "opportunityScore", "opportunityPotential", "opportunityTiming",
        "opportunityRank", "opportunityTier", "opportunityGroupModifier",
        "opportunityFundModifier", "opportunityPenalty", "leadershipScore",
    )
    emerging_required = (
        "emergingLeaderScore", "emergingArchetype", "neglectedEmergingScore",
        "resetReawakeningScore", "resetScore", "rsTurnScore",
        "neglectHistoryScore", "triggerReadinessScore",
        "reawakeningStructureScore", "ignitionScore", "stageFreshnessScore",
        "emergingEvidenceCount", "emergingLeaderCandidate", "aPlusEmergingSetup",
        "emergingReasons",
    )
    opp_covered = emerging_covered = 0

    for row in rows:
        ticker = str(row.get("ticker") or "?")
        if all(key in row for key in opp_required):
            opp_covered += 1
        else:
            errors.append(f"{ticker}: missing Opportunity v2 fields")
            continue

        score = finite(row.get("opportunityScore"))
        potential = finite(row.get("opportunityPotential"))
        timing = finite(row.get("opportunityTiming"))
        rank = finite(row.get("opportunityRank"))
        group_mod = finite(row.get("opportunityGroupModifier"))
        fund_mod = finite(row.get("opportunityFundModifier"))
        leadership = finite(row.get("leadershipScore"))
        if score is None or not 0 <= score <= 100:
            errors.append(f"{ticker}: invalid opportunityScore={row.get('opportunityScore')!r}")
            continue
        if potential is None or not 0 <= potential <= 100 or timing is None or not 0 <= timing <= 100:
            errors.append(f"{ticker}: invalid Potential/Timing")
        if rank is None or not 1 <= rank <= 99:
            errors.append(f"{ticker}: invalid opportunityRank={rank}")
        if row.get("opportunityTier") != expected_tier(score):
            errors.append(f"{ticker}: tier mismatch {row.get('opportunityTier')} for {score}")
        if group_mod is None or abs(group_mod) > 5.01:
            errors.append(f"{ticker}: group modifier outside +/-5: {group_mod}")
        if fund_mod is None or abs(fund_mod) > 5.01:
            errors.append(f"{ticker}: fundamental modifier outside +/-5: {fund_mod}")
        if leadership is None or abs(leadership - score) > 0.11:
            errors.append(f"{ticker}: leadershipScore must alias final Opportunity v2, got {leadership} vs {score}")

        nested = ((row.get("stockscout") or {}).get("opportunityV2") or {})
        if nested:
            if nested.get("model") != OPPORTUNITY_MODEL:
                errors.append(f"{ticker}: nested Opportunity model mismatch")
            if finite(nested.get("score")) is None or abs(float(nested.get("score")) - score) > 0.11:
                errors.append(f"{ticker}: nested Opportunity score mismatch")
            if nested.get("tier") != row.get("opportunityTier"):
                errors.append(f"{ticker}: nested Opportunity tier mismatch")
        else:
            errors.append(f"{ticker}: missing stockscout.opportunityV2")

        if all(key in row for key in emerging_required):
            emerging_covered += 1
            emerging = finite(row.get("emergingLeaderScore"))
            neglected = finite(row.get("neglectedEmergingScore"))
            reawakening = finite(row.get("resetReawakeningScore"))
            evidence = finite(row.get("emergingEvidenceCount"))
            confluence = finite(row.get("confluence"))
            archetype = str(row.get("emergingArchetype") or "")
            if emerging is None or not 0 <= emerging <= 100:
                errors.append(f"{ticker}: invalid emergingLeaderScore")
            if neglected is None or reawakening is None or emerging is None or abs(emerging - max(neglected, reawakening)) > 1e-9:
                errors.append(f"{ticker}: Emerging score is not strongest archetype")
            if archetype not in {"Neglected Emerging", "Reset Reawakening"}:
                errors.append(f"{ticker}: invalid emerging archetype {archetype}")
            if evidence is None or evidence < 0 or evidence > 5 or int(evidence) != evidence:
                errors.append(f"{ticker}: invalid emerging evidence count")
            if confluence is None or evidence is None or confluence != evidence:
                errors.append(f"{ticker}: confluence compatibility alias diverged")
            if emerging is not None:
                stage = int(finite(row.get("stage")) or 0)
                accel = finite(row.get("rsAcceleration")) or 0.0
                if bool(row.get("extended")) and emerging > 35.01:
                    errors.append(f"{ticker}: extended Emerging score cap violated")
                if accel <= -0.25 and emerging > 55.01:
                    errors.append(f"{ticker}: negative-RS Emerging score cap violated")
                if stage not in (1, 2) and emerging > 30.01:
                    errors.append(f"{ticker}: off-regime Emerging score cap violated")
        else:
            errors.append(f"{ticker}: missing Emerging Leader fields")

        if len(errors) >= 80:
            break

    if rows and opp_covered / len(rows) < 0.95:
        errors.append(f"Opportunity v2 field coverage too low: {opp_covered}/{len(rows)}")
    if payload.get("emergingLeaderModel") == EMERGING_MODEL and rows and emerging_covered / len(rows) < 0.95:
        errors.append(f"Emerging Leader field coverage too low: {emerging_covered}/{len(rows)}")

    groups = payload.get("groups") or {}
    if (payload.get("market") or {}).get("groupModel") == GROUP_MODEL:
        if groups.get("leadershipScoreMode") != "opportunity-v2-alias":
            errors.append(f"group leadershipScoreMode not finalized: {groups.get('leadershipScoreMode')}")
        for label, proxy_field in (("sectors", "sectorProxyTicker"), ("industries", "industryProxyTicker")):
            for group in groups.get(label) or []:
                proxy = str(group.get("ticker") or "")
                expected_med, expected_top = expected_group_summary(rows, proxy_field, proxy)
                actual_med = finite(group.get("medianOpportunity"))
                if actual_med is None or abs(actual_med - expected_med) > 0.11:
                    errors.append(f"{label}:{proxy}: stale medianOpportunity {actual_med} vs {expected_med}")
                if list(group.get("topTickers") or [])[:8] != expected_top:
                    errors.append(f"{label}:{proxy}: stale topTickers")
                if group.get("opportunityModel") != OPPORTUNITY_MODEL:
                    errors.append(f"{label}:{proxy}: group summary model not finalized")
                if len(errors) >= 80:
                    break

        from audit_group_leadership import main as audit_group
        if audit_group():
            errors.append("Group Leadership v2 guardrail failed")

    if payload.get("lateralBaseModel") == LATERAL_MODEL:
        from audit_lateral_base import main as audit_lateral
        if audit_lateral():
            errors.append("Lateral Base guardrail failed")

    market = payload.get("market") or {}
    if payload.get("emergingLeaderModel") == EMERGING_MODEL:
        expected_counts = {
            "emergingLeaderCandidates": sum(bool(r.get("emergingLeaderCandidate")) for r in rows),
            "neglectedEmergingCandidates": sum(bool(r.get("emergingLeaderCandidate")) and r.get("emergingArchetype") == "Neglected Emerging" for r in rows),
            "resetReawakeningCandidates": sum(bool(r.get("emergingLeaderCandidate")) and r.get("emergingArchetype") == "Reset Reawakening" for r in rows),
            "aPlusEmergingSetups": sum(bool(r.get("aPlusEmergingSetup")) for r in rows),
        }
        for key, expected in expected_counts.items():
            try:
                actual = int(market.get(key, -1))
            except Exception:
                actual = -1
            if actual != expected:
                errors.append(f"market {key} mismatch: {actual} vs {expected}")

    print(
        f"Pipeline model audit: rows={len(rows):,} Opportunity={opp_covered:,} "
        f"Emerging={emerging_covered:,} errors={len(errors)}"
    )
    if errors:
        print("PIPELINE MODEL AUDIT FAILED")
        for error in errors[:50]:
            print(f" - {error}")
        return 1
    print("PIPELINE MODEL AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
