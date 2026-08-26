from emerging_leader import score_row
from lateral_base import score_row as score_lateral_base


def base_row(**overrides):
    row = {
        "ticker": "TEST",
        "stage": 2,
        "stage2AgeWeeks": 4,
        "rsRank": 75,
        "rsAcceleration": 0.55,
        "rsFromHigh": -3.0,
        "volumeRatio": 1.2,
        "distance10w": 4.0,
        "distance30w": 8.0,
        "breakoutPct": -2.0,
        "from52wHigh": -2.0,
        "return3m": 8.0,
        "baseWeeks": 36,
        "baseDepthPct": 20.0,
        "tightRange20": 8.0,
        "tightRange60": 20.0,
        "atrCompression": 55.0,
        "contraction": 60.0,
        "vcpScore": 65.0,
        "volumeDryUp": 0.82,
        "return5y": 10.0,
        "return3y": 5.0,
        "return2y": 5.0,
        "prior9mReturn": 10.0,
        "extended": False,
    }
    row.update(overrides)
    return row


def score_full(row):
    row.update(score_lateral_base(row))
    row.update(score_row(row))
    return row


def test_true_neglected_base_scores_high_and_is_candidate():
    row = score_full(base_row())
    assert row["emergingArchetype"] == "Neglected Emerging"
    assert row["emergingLeaderScore"] >= 68
    assert row["emergingEvidenceCount"] >= 4
    assert row["emergingLeaderCandidate"] is True
    assert row["opportunityScore"] == row["emergingLeaderScore"]
    assert row["confluence"] == row["emergingEvidenceCount"]


def test_wyy_style_deep_reset_can_reawaken_without_local_base():
    row = score_full(base_row(
        stage=1,
        stage2AgeWeeks=0,
        baseWeeks=1,
        baseDepthPct=500,
        tightRange20=36,
        tightRange60=176,
        distance10w=-25,
        distance30w=13,
        breakoutPct=-61,
        from52wHigh=-61,
        rsFromHigh=-56,
        rsRank=94,
        rsAcceleration=2.9,
        volumeRatio=3.4,
        volumeDryUp=0.39,
        contraction=64,
        atrCompression=37,
        return3m=14,
        return5y=64,
        return3y=422,
        return2y=163,
        prior9mReturn=148,
    ))
    assert row["emergingArchetype"] == "Reset Reawakening"
    assert row["resetReawakeningScore"] >= 80
    assert row["emergingEvidenceCount"] == 5
    assert row["emergingLeaderCandidate"] is True
    assert row["aPlusEmergingSetup"] is True


def test_random_spike_in_broken_structure_is_not_reawakening_candidate():
    row = score_full(base_row(
        stage=1,
        baseWeeks=1,
        baseDepthPct=80,
        tightRange20=40,
        tightRange60=80,
        distance10w=-30,
        distance30w=-22,
        breakoutPct=-65,
        from52wHigh=-65,
        rsFromHigh=-60,
        rsRank=92,
        rsAcceleration=2.5,
        volumeRatio=3.2,
        volumeDryUp=1.15,
        contraction=20,
        atrCompression=10,
        return3m=-18,
        return5y=-60,
        return3y=-70,
        return2y=-55,
        prior9mReturn=-40,
    ))
    assert row["emergingLeaderCandidate"] is False
    assert row["emergingEvidenceFlags"].get("Structural recovery") is False


def test_extended_setup_cannot_rank_as_emerging_leader():
    row = score_full(base_row(distance10w=22, distance30w=28, extended=True))
    assert row["emergingLeaderScore"] <= 35
    assert row["emergingLeaderCandidate"] is False
    assert row["aPlusEmergingSetup"] is False


def test_negative_rs_acceleration_cannot_be_candidate():
    row = score_full(base_row(rsAcceleration=-0.4))
    assert row["emergingLeaderScore"] <= 55
    assert row["emergingLeaderCandidate"] is False


def test_mature_stage2_loses_fresh_stage_evidence():
    row = score_full(base_row(stage2AgeWeeks=30))
    assert row["stageFreshnessScore"] == 15.0
    assert row["emergingEvidenceFlags"]["Fresh stage"] is False
    assert row["aPlusEmergingSetup"] is False
