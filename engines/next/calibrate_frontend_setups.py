#!/usr/bin/env python3
"""Calibrate transparent StockScout setup labels and emerging-leader discovery.

LEGACY remains untouched. This layer only consumes already-exported STOCKSCOUT
features. The main ranking is produced by `emerging_leader.py`; setup tags are
explanatory labels and no longer award arbitrary score bonuses.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from compute_ma_cluster_breakout import apply_to_payload as apply_ma_cluster
from emerging_leader import MODEL as EMERGING_MODEL, score_row as score_emerging_leader
from lateral_base import MODEL as LATERAL_BASE_MODEL, score_row as score_lateral_base

DATA = Path("frontend/public/data/latest.json")


def n(row, key, default=0.0):
    try:
        value = row.get(key, default)
        value = default if value is None else float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(value)))


def calibrate(row: dict):
    """Assign transparent setup labels only; scoring happens after Lateral Base."""
    stage = int(n(row, "stage"))
    age = n(row, "stage2AgeWeeks")
    rs = n(row, "rsRank")
    accel = n(row, "rsAcceleration")
    vol = n(row, "volumeRatio", 1.0)
    d10 = n(row, "distance10w", n(row, "distance50"))
    d30 = n(row, "distance30w")
    ret3 = n(row, "return3m")
    prior9 = n(row, "prior9mReturn")
    tt = n(row, "trendTemplatePasses")
    breakout = n(row, "breakoutPct")
    rs_from_high = n(row, "rsFromHigh")
    price_from_high = n(row, "from52wHigh")
    vcp = n(row, "vcpScore")
    contraction = n(row, "contraction")
    atr_comp = n(row, "atrCompression")
    range20 = n(row, "tightRange20", 100)
    volume_dry = n(row, "volumeDryUp", 1)
    slope150 = n(row, "slope150")
    base_weeks = n(row, "baseWeeks")
    cluster_phase = str(row.get("maClusterPhase") or "NONE")
    cluster_tier = row.get("maClusterTier")
    cluster_entry = cluster_phase == "ENTRY"
    cluster_ready = cluster_phase == "READY"
    cluster_watch = cluster_phase == "WATCH"

    extended = d10 > 12 or d30 > 22
    row["extended"] = bool(extended)

    tags: list[str] = []
    neglected = (
        stage in (1, 2)
        and prior9 <= 40
        and -5 <= ret3 <= 35
        and rs >= 60
        and accel > 0
        and -8 <= d10 <= 10
        and d30 <= 18
        and base_weeks >= 12
        and (stage == 1 or age <= 12)
        and not extended
    )
    transition = (
        stage in (1, 2)
        and (stage == 1 or age <= 6)
        and tt >= 5
        and rs >= 60
        and accel > 0
        and -8 <= d10 <= 10
        and d30 <= 18
        and not extended
    )
    fresh_stage2 = (
        stage == 2
        and age <= 10
        and tt >= 6
        and rs >= 65
        and -8 <= d10 <= 10
        and not extended
        and (accel > 0 or (rs >= 85 and rs_from_high >= -3))
    )
    fresh_breakout = (
        stage in (1, 2)
        and -1.5 <= breakout <= 5
        and vol >= 1.5
        and rs >= 70
        and accel > 0
        and not extended
    )
    rs_before_price = (
        stage in (1, 2)
        and rs >= 85
        and rs_from_high >= -2
        and price_from_high <= -5
        and -8 <= d10 <= 10
        and not extended
    )
    tight_vcp = (
        stage in (1, 2)
        and max(vcp, contraction, atr_comp) >= 40
        and range20 <= 12
        and volume_dry <= 1.05
        and rs >= 55
        and accel >= -0.2
        and not extended
    )
    volume_wakeup = (
        stage in (1, 2)
        and vol >= 1.8
        and rs >= 65
        and accel > 0
        and ret3 > 0
        and d10 <= 12
        and breakout <= 8
        and not extended
    )
    pullback_10w = (
        stage == 2
        and -3 <= d10 <= 5
        and rs >= 70
        and slope150 > 0
        and age <= 20
        and rs_from_high >= -8
        and not extended
    )
    long_base = (
        stage in (1, 2)
        and base_weeks >= 20
        and -6 <= breakout <= 5
        and rs >= 60
        and accel > 0
        and not extended
    )

    if cluster_entry:
        tags.extend(["MA Cluster ENTRY", f"MA Cluster {cluster_tier} ENTRY"])
    elif cluster_ready:
        tags.extend(["MA Cluster Ready", f"MA Cluster {cluster_tier} Ready"])
    elif cluster_watch:
        tags.extend(["MA Cluster Watch", "MA Cluster C Watch"])
    if neglected:
        tags.append("Neglected → Leader")
    if transition:
        tags.append("S1→S2 Transition")
    if long_base:
        tags.append("Long Base Breakout")
    if fresh_breakout:
        tags.append("Fresh Breakout")
    if rs_before_price:
        tags.append("RS Before Price")
    if tight_vcp:
        tags.append("Tight / VCP")
    if pullback_10w:
        tags.append("10W Pullback")
    if fresh_stage2:
        tags.append("Fresh Stage 2")
    if volume_wakeup:
        tags.append("Volume Wake-Up")

    positive = list(tags)
    if extended:
        tags.append("⚠ Extended")

    order = [
        "MA Cluster ENTRY",
        "MA Cluster Ready",
        "MA Cluster Watch",
        "Neglected → Leader",
        "S1→S2 Transition",
        "Long Base Breakout",
        "Fresh Breakout",
        "RS Before Price",
        "Tight / VCP",
        "10W Pullback",
        "Fresh Stage 2",
        "Volume Wake-Up",
    ]
    primary = next((x for x in order if x in positive), None)
    if not primary:
        if stage == 2 and extended:
            primary = "Extended Stage 2"
        elif stage == 2 and rs >= 80:
            primary = "Trend Leader"
        elif stage == 2:
            primary = "Stage 2"
        elif stage == 1:
            primary = "Base Building"
        else:
            primary = str(row.get("stageName") or row.get("setup") or "Other")
        if not tags:
            tags.append(primary)

    row.update({
        "setupTags": tags,
        "setupMatchCount": len(positive),
        "primarySetup": primary,
        "earlyStage2": bool(stage == 2 and age <= 10 and rs >= 65 and not extended),
        "wakingUp": bool(stage in (1, 2) and accel > 0 and vol >= 1.2 and ret3 > 0 and not extended),
    })


def refresh_leadership_alias(row: dict):
    """Keep stored Group v2 confirmation aligned during calibration-only validation."""
    if row.get("groupRank") is None:
        return
    individual = n(row, "opportunityScore", n(row, "score"))
    group_rank = n(row, "groupRank", 50.0)
    row["leadershipScore"] = int(round(clamp(individual + (group_rank - 50.0) * 0.10)))


def assign_scout_tier(row: dict):
    """Combine stock quality with MA-cluster timing without hiding either layer.

    Timing Tier (maClusterTier) describes only entry geometry. Scout Tier is the
    user-facing priority tier: it requires the stock itself to have enough Emerging
    + RS evidence before a technically pretty MA cluster is promoted.
    """
    phase = str(row.get("maClusterPhase") or "NONE")
    if phase not in {"WATCH", "READY", "ENTRY"}:
        row.update({
            "scoutTier": None,
            "scoutTierRank": 0,
            "scoutTierLabel": "—",
            "scoutQualityConfirmed": False,
            "scoutTierReasons": [],
        })
        return

    emerging = n(row, "emergingLeaderScore", n(row, "opportunityScore"))
    rs = n(row, "rsRank")
    accel = n(row, "rsAcceleration")
    stage = int(n(row, "stage"))
    extended = bool(row.get("extended"))

    strong_quality = bool(
        emerging >= 55
        and rs >= 70
        and accel > 0
        and stage in (1, 2)
        and not extended
    )
    confirmed_quality = bool(
        emerging >= 45
        and rs >= 55
        and accel >= 0
        and stage in (1, 2)
        and not extended
    )

    if phase in {"READY", "ENTRY"} and strong_quality:
        tier = "A"
    elif (phase in {"READY", "ENTRY"} and confirmed_quality) or (phase == "WATCH" and strong_quality):
        tier = "B"
    else:
        tier = "C"

    rank = {"C": 1, "B": 2, "A": 3}[tier]
    label = f"{tier} · {phase}"
    reasons = [
        f"Timing {row.get('maClusterTierLabel') or phase}",
        f"Emerging {emerging:.0f}",
        f"RS {rs:.0f} {'↑' if accel > 0 else '↔' if accel == 0 else '↓'}",
    ]
    if tier == "A":
        reasons.append("Quality + trigger zone confirmed")
    elif tier == "B" and phase == "WATCH":
        reasons.append("Quality confirmed; waiting for trigger")
    elif tier == "B":
        reasons.append("Moderate stock-quality confirmation")
    else:
        reasons.append("Timing present; stock quality not confirmed")

    row.update({
        "scoutTier": tier,
        "scoutTierRank": rank,
        "scoutTierLabel": label,
        "scoutQualityConfirmed": bool(strong_quality),
        "scoutTierReasons": reasons,
    })


def main():
    if not DATA.exists():
        print("Setup calibration skipped: latest.json missing")
        return

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    # Exact weekly 10W/30W timing is derived from existing 5Y chart shards before
    # labels and emerging-leader scoring are assigned.
    apply_ma_cluster(payload)
    rows = payload.get("universe") or []

    for row in rows:
        calibrate(row)
        row.update(score_lateral_base(row))
        row.update(score_emerging_leader(row))
        refresh_leadership_alias(row)
        if row.get("emergingLeaderCandidate"):
            archetype = str(row.get("emergingArchetype") or "Neglected Emerging")
            discovery_tag = "Reset → Reawakening" if archetype == "Reset Reawakening" else "Neglected → Emerging Leader"
            tags = list(row.get("setupTags") or [])
            if discovery_tag not in tags:
                tags.insert(0, discovery_tag)
            row["setupTags"] = tags
            row["setupMatchCount"] = len([t for t in tags if not str(t).startswith("⚠")])
            if row.get("maClusterPhase") == "ENTRY":
                row["primarySetup"] = f"MA Cluster {row.get('maClusterTier')} · ENTRY"
            elif row.get("maClusterPhase") == "READY":
                row["primarySetup"] = f"MA Cluster {row.get('maClusterTier')} · READY"
            elif row.get("maClusterPhase") == "WATCH":
                row["primarySetup"] = "MA Cluster C · WATCH"
            else:
                row["primarySetup"] = discovery_tag
        elif row.get("maClusterPhase") in {"ENTRY", "READY", "WATCH"}:
            row["primarySetup"] = row.get("maClusterTierLabel") or row.get("primarySetup")

        assign_scout_tier(row)
        if row.get("scoutTier") in {"A", "B"}:
            tags = list(row.get("setupTags") or [])
            scout_tag = f"Scout Tier {row.get('scoutTier')}"
            if scout_tag not in tags:
                tags.insert(0, scout_tag)
            row["setupTags"] = tags
            row["setupMatchCount"] = len([t for t in tags if not str(t).startswith("⚠")])
            row["primarySetup"] = f"Scout {row.get('scoutTierLabel')}"

    market = payload.setdefault("market", {})
    emerging = [r for r in rows if r.get("emergingLeaderCandidate")]
    neglected_emerging = [r for r in emerging if r.get("emergingArchetype") == "Neglected Emerging"]
    reawakening = [r for r in emerging if r.get("emergingArchetype") == "Reset Reawakening"]
    a_plus = [r for r in rows if r.get("aPlusEmergingSetup")]

    market["perfectSetups"] = len(a_plus)
    market["aPlusEmergingSetups"] = len(a_plus)
    market["emergingLeaderCandidates"] = len(emerging)
    market["neglectedEmergingCandidates"] = len(neglected_emerging)
    market["resetReawakeningCandidates"] = len(reawakening)
    market["neglectedLeaders"] = len(neglected_emerging)
    market["transitions"] = sum("S1→S2 Transition" in (r.get("setupTags") or []) for r in rows)
    market["freshBreakouts"] = sum("Fresh Breakout" in (r.get("setupTags") or []) for r in rows)
    market["highEvidence"] = sum(int(r.get("emergingEvidenceCount", 0) or 0) >= 4 for r in rows)
    market["highConfluence"] = market["highEvidence"]
    market["extendedCount"] = sum(bool(r.get("extended")) for r in rows)
    market["scoutTierCounts"] = {
        tier: sum(r.get("scoutTier") == tier for r in rows)
        for tier in ("A", "B", "C")
    }
    market["scoutTierATop"] = [
        r.get("ticker") for r in sorted(
            [r for r in rows if r.get("scoutTier") == "A"],
            key=lambda r: (n(r, "maClusterPhase") == "ENTRY", n(r, "emergingLeaderScore"), n(r, "rsRank"), n(r, "maClusterScore")),
            reverse=True,
        )[:15]
    ]

    lateral_candidates = [r for r in rows if r.get("lateralBaseCandidate")]
    market["lateralBaseCandidates"] = len(lateral_candidates)
    market["lateralBaseAvgScore"] = round(sum(n(r, "lateralBaseScore") for r in rows) / max(1, len(rows)), 1)
    market["lateralBaseTop"] = [
        r.get("ticker") for r in sorted(
            lateral_candidates,
            key=lambda r: (n(r, "neglectedLaunchScore"), n(r, "launchReadiness"), n(r, "lateralBaseScore")),
            reverse=True,
        )[:10]
    ]
    market["emergingLeaderTop"] = [
        r.get("ticker") for r in sorted(
            emerging,
            key=lambda r: (n(r, "emergingLeaderScore"), n(r, "emergingEvidenceCount"), n(r, "rsRank")),
            reverse=True,
        )[:15]
    ]

    payload["calibrationModel"] = EMERGING_MODEL
    payload["featureModel"] = "data-first-v3-emerging-leader"
    payload["lateralBaseModel"] = LATERAL_BASE_MODEL
    payload["emergingLeaderModel"] = EMERGING_MODEL
    payload["version"] = max(8, int(payload.get("version", 1) or 1))

    DATA.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(
        f"Calibrated {len(rows):,} rows: emerging={len(emerging)} "
        f"(neglected={len(neglected_emerging)}, reawakening={len(reawakening)}), "
        f"A+={len(a_plus)}, MA-watch={market.get('maClusterWatchCount',0)}, "
        f"MA-ready={market.get('maClusterReadyCount',0)}, MA-entry={market.get('maClusterEntryCount',0)}, "
        f"MA-tiers={market.get('maClusterTierCounts',{})}, Scout-tiers={market.get('scoutTierCounts',{})}, "
        f"evidence4+={market['highEvidence']}, extended={market['extendedCount']}"
    )


if __name__ == "__main__":
    main()
