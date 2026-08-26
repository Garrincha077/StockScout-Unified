from datetime import date, timedelta

from compute_ma_cluster_breakout import score_bars


def make_bars(weeks=45, breakout=False, volume_mult=1.0, spread_bias=0.0):
    bars = []
    start = date(2025, 1, 6)  # Monday
    closes = []
    for w in range(weeks):
        # Long flattening structure with only a mild positive drift near the end.
        base = 100.0 + max(0, w - 28) * 0.12
        closes.append(base)
    if spread_bias:
        # Distort the shorter MA so 10W/30W are not compressed.
        for i in range(max(0, weeks - 10), weeks):
            closes[i] += spread_bias
    if breakout:
        closes[-2] = 100.5
        closes[-1] = 104.0

    for w, close in enumerate(closes):
        monday = start + timedelta(days=7 * w)
        for d in range(5):
            day = monday + timedelta(days=d)
            c = close * (1.0 + (d - 2) * 0.0005)
            volume = 1_000_000.0
            if breakout and w == weeks - 1:
                volume *= volume_mult
            bars.append([
                day.isoformat(),
                c * 0.997,
                c * 1.006,
                c * 0.994,
                c,
                volume,
                1.0,
            ])
    return bars


def test_entry_requires_cross_of_compressed_cluster_on_volume():
    result = score_bars(make_bars(breakout=True, volume_mult=2.0))
    assert result["maClusterTight"] is True
    assert result["maClusterEntrySignal"] is True
    assert result["maClusterPhase"] == "ENTRY"
    assert result["maClusterTier"] in {"A", "B"}
    assert result["maClusterTierRank"] >= 2
    assert result["maClusterVolumePace"] >= 1.4
    assert 0 <= result["maClusterPricePct"] <= 8


def test_strong_clean_entry_can_be_tier_a():
    result = score_bars(make_bars(breakout=True, volume_mult=2.5))
    assert result["maClusterEntrySignal"] is True
    assert result["maClusterVeryTight"] is True
    assert result["maClusterTurnCount"] == 2
    assert result["maClusterVolumePace"] >= 1.8
    assert result["maClusterTier"] == "A"
    assert result["maClusterTierLabel"] == "A · ENTRY"


def test_low_volume_breakout_is_not_entry_or_a_entry():
    result = score_bars(make_bars(breakout=True, volume_mult=1.05))
    assert result["maClusterEntrySignal"] is False
    assert result["maClusterTierLabel"] != "A · ENTRY"


def test_wide_10w_30w_spread_is_not_entry():
    result = score_bars(make_bars(breakout=True, volume_mult=2.0, spread_bias=12.0))
    assert result["maClusterTight"] is False
    assert result["maClusterEntrySignal"] is False
    assert result["maClusterPhase"] != "ENTRY"


def test_ready_or_watch_state_can_exist_before_breakout():
    result = score_bars(make_bars(breakout=False))
    assert result["maClusterEntrySignal"] is False
    assert result["maClusterPhase"] in {"READY", "WATCH", "NONE"}
    if result["maClusterPhase"] == "READY":
        assert result["maClusterTier"] in {"A", "B"}
    if result["maClusterPhase"] == "WATCH":
        assert result["maClusterTier"] == "C"


def test_tier_rank_and_phase_are_consistent():
    for result in (
        score_bars(make_bars(breakout=True, volume_mult=2.0)),
        score_bars(make_bars(breakout=False)),
        score_bars([]),
    ):
        expected = {None: 0, "C": 1, "B": 2, "A": 3}[result["maClusterTier"]]
        assert result["maClusterTierRank"] == expected
        if result["maClusterPhase"] == "ENTRY":
            assert result["maClusterTier"] in {"A", "B"}
        if result["maClusterTier"] == "C":
            assert result["maClusterPhase"] == "WATCH"
