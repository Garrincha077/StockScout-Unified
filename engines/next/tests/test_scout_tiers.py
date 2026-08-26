from calibrate_frontend_setups import assign_scout_tier


def row(**overrides):
    base = {
        "maClusterPhase": "READY",
        "maClusterTierLabel": "B · READY",
        "emergingLeaderScore": 60.0,
        "opportunityScore": 60.0,
        "rsRank": 80,
        "rsAcceleration": 0.5,
        "stage": 1,
        "extended": False,
    }
    base.update(overrides)
    return base


def test_ready_strong_stock_is_scout_a():
    r = row()
    assign_scout_tier(r)
    assert r["scoutTier"] == "A"
    assert r["scoutTierRank"] == 3
    assert r["scoutQualityConfirmed"] is True
    assert r["scoutTierLabel"] == "A · READY"


def test_entry_geometry_does_not_make_weak_stock_scout_a():
    r = row(
        maClusterPhase="ENTRY",
        maClusterTierLabel="A · ENTRY",
        emergingLeaderScore=30,
        rsRank=32,
        rsAcceleration=-0.4,
    )
    assign_scout_tier(r)
    assert r["scoutTier"] == "C"
    assert r["scoutQualityConfirmed"] is False


def test_strong_watch_is_scout_b_until_trigger_ready():
    r = row(maClusterPhase="WATCH", maClusterTierLabel="C · WATCH")
    assign_scout_tier(r)
    assert r["scoutTier"] == "B"
    assert r["scoutQualityConfirmed"] is True
    assert "waiting for trigger" in r["scoutTierReasons"][-1].lower()


def test_moderately_confirmed_ready_is_scout_b():
    r = row(emergingLeaderScore=49, rsRank=61, rsAcceleration=0.1)
    assign_scout_tier(r)
    assert r["scoutTier"] == "B"
    assert r["scoutQualityConfirmed"] is False


def test_no_active_cluster_has_no_scout_tier():
    r = row(maClusterPhase="NONE", maClusterTierLabel="—")
    assign_scout_tier(r)
    assert r["scoutTier"] is None
    assert r["scoutTierRank"] == 0
