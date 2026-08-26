from lateral_base import band_score, inverse_ramp, score_row


def base_row(**overrides):
    row = {
        "stage": 1,
        "stage2AgeWeeks": 0,
        "baseWeeks": 28,
        "baseDepthPct": 22,
        "tightRange20": 8,
        "tightRange60": 18,
        "atrCompression": 55,
        "contraction": 50,
        "vcpScore": 45,
        "volumeDryUp": 0.82,
        "distance10w": 2,
        "distance30w": 4,
        "rsRank": 78,
        "rsAcceleration": 0.45,
        "rsFromHigh": -3,
        "breakoutPct": -1,
        "volumeRatio": 1.25,
        "return3m": 10,
        "prior9mReturn": 5,
        "extended": False,
    }
    row.update(overrides)
    return row


def test_band_score_prefers_sweet_spot():
    assert band_score(20, 6, 16, 52, 90) == 100
    assert band_score(3, 6, 16, 52, 90) == 0
    assert band_score(100, 6, 16, 52, 90) == 0


def test_inverse_ramp_rewards_tightness():
    assert inverse_ramp(6, 6, 18) == 100
    assert inverse_ramp(18, 6, 18) == 0


def test_good_lateral_base_is_candidate():
    scored = score_row(base_row())
    assert scored["lateralBaseCandidate"] is True
    assert scored["lateralBaseScore"] >= 60
    assert scored["contractionQuality"] >= 50
    assert scored["launchReadiness"] >= 55


def test_extended_stock_is_never_candidate_and_launch_is_penalized():
    clean = score_row(base_row())
    extended = score_row(base_row(distance10w=18, extended=True))
    assert extended["lateralBaseCandidate"] is False
    assert extended["launchReadiness"] < clean["launchReadiness"] * 0.6


def test_weak_short_base_does_not_pass():
    scored = score_row(base_row(baseWeeks=5, tightRange20=22, tightRange60=48, atrCompression=0, contraction=0, vcpScore=0, volumeDryUp=1.3))
    assert scored["lateralBaseCandidate"] is False
    assert scored["lateralBaseScore"] < 50


def test_model_is_observational_only():
    row = base_row(opportunityScore=77, confluence=8)
    before = (row["opportunityScore"], row["confluence"])
    score_row(row)
    assert (row["opportunityScore"], row["confluence"]) == before
