from calibrate_frontend_setups import calibrate


def base_row(**overrides):
    row = {
        "ticker": "TEST",
        "stage": 2,
        "stageName": "Uptrend/Breakout",
        "stage2AgeWeeks": 4,
        "rsRank": 82,
        "rsAcceleration": 0.8,
        "volumeRatio": 1.8,
        "distance10w": 4.0,
        "distance30w": 7.0,
        "return3m": 10.0,
        "prior9mReturn": 5.0,
        "trendTemplatePasses": 7,
        "breakoutPct": 1.0,
        "rsFromHigh": -1.0,
        "from52wHigh": -8.0,
        "vcpScore": 70.0,
        "contraction": 65.0,
        "atrCompression": 40.0,
        "tightRange20": 8.0,
        "volumeDryUp": 0.9,
        "slope150": 0.2,
        "baseWeeks": 24,
        "fundamentalSupport": True,
    }
    row.update(overrides)
    return row


def test_early_neglected_label_is_recognized_without_scoring_side_effects():
    row = base_row()
    calibrate(row)

    assert row["extended"] is False
    assert "Neglected → Leader" in row["setupTags"]
    assert "Fresh Breakout" in row["setupTags"]
    assert row["primarySetup"] == "Neglected → Leader"
    assert "opportunityScore" not in row
    assert "confluence" not in row


def test_extended_move_cannot_be_fresh_or_neglected():
    row = base_row(distance10w=22.0, distance30w=28.0, breakoutPct=12.0, volumeRatio=4.0)
    calibrate(row)

    assert row["extended"] is True
    assert "Fresh Breakout" not in row["setupTags"]
    assert "Neglected → Leader" not in row["setupTags"]
    assert "⚠ Extended" in row["setupTags"]
    assert row["primarySetup"] == "Extended Stage 2"


def test_large_post_breakout_move_is_not_labeled_fresh_breakout():
    row = base_row(breakoutPct=8.5)
    calibrate(row)

    assert row["extended"] is False
    assert "Fresh Breakout" not in row["setupTags"]


def test_mature_stage2_is_not_transition_or_fresh_stage2():
    row = base_row(stage2AgeWeeks=26, prior9mReturn=40)
    calibrate(row)

    assert "S1→S2 Transition" not in row["setupTags"]
    assert "Fresh Stage 2" not in row["setupTags"]
