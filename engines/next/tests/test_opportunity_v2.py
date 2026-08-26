from compute_opportunity_v2 import apply, modifier, score_row


def base_row(**updates):
    row = {
        "ticker": "TEST",
        "stage": 2,
        "stage2AgeWeeks": 6,
        "emergingArchetype": "Neglected Emerging",
        "emergingLeaderScore": 80,
        "resetScore": 88,
        "neglectHistoryScore": 85,
        "stageFreshnessScore": 100,
        "reawakeningStructureScore": 70,
        "dryResetScore": 75,
        "recoveryScore": 70,
        "distance10w": 2,
        "distance30w": 4,
        "breakoutPct": 1,
        "rsRank": 88,
        "rsAcceleration": 0.65,
        "maClusterScore": 85,
        "maClusterPhase": "READY",
        "maClusterVolumePace": 1.5,
        "maClusterPricePct": 1,
        "volumeRatio": 1.8,
        "groupRank": 75,
        "groupConfidence": 80,
        "fundamentalEvidenceScore": 75,
        "fundamentalEvidenceConfidence": 80,
        "extended": False,
    }
    row.update(updates)
    return row


def test_timing_changes_opportunity_without_destroying_potential():
    strong = score_row(base_row())
    sleeping = score_row(
        base_row(
            rsRank=58,
            rsAcceleration=-0.05,
            maClusterScore=45,
            maClusterPhase="WATCH",
            volumeRatio=0.8,
            maClusterVolumePace=0.8,
            breakoutPct=-10,
        )
    )
    assert abs(strong["opportunityPotential"] - sleeping["opportunityPotential"]) < 5
    assert strong["opportunityTiming"] > sleeping["opportunityTiming"] + 20
    assert strong["opportunityScore"] > sleeping["opportunityScore"] + 8


def test_extended_setup_is_capped():
    out = score_row(base_row(extended=True, distance10w=16, distance30w=24))
    assert out["opportunityScore"] <= 50
    assert "Extended cap" in out["opportunityReasons"]


def test_confirmation_modifiers_are_bounded_and_missing_is_neutral():
    assert modifier(100, 100) == 5
    assert modifier(0, 100) == -5
    assert modifier(None, 100) == 0
    assert modifier(90, None) == 0


def test_apply_assigns_rank_tier_and_preserves_emerging_score():
    rows = [
        base_row(ticker="A"),
        base_row(ticker="B", rsRank=65, rsAcceleration=0.05, volumeRatio=1.0),
        base_row(ticker="C", stage=3, rsRank=40, rsAcceleration=-0.5),
    ]
    payload = apply({"universe": rows, "market": {}})
    assert payload["opportunityModel"].startswith("stockscout-opportunity-v2")
    assert all(1 <= row["opportunityRank"] <= 99 for row in rows)
    assert rows[0]["opportunityRank"] > rows[-1]["opportunityRank"]
    assert rows[-1]["opportunityScore"] <= 45
    assert rows[0]["emergingLeaderScore"] == 80
    assert rows[0]["opportunityTier"] in {"PRIME", "READY", "WATCH", "EARLY", "PASS"}
