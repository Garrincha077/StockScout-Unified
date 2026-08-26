from compute_group_leadership import correlation_confidence, leadership_score, neutralize_rank


def test_confidence_is_zero_at_assignment_floor():
    confidence, stability = correlation_confidence(0.10, 0.10, 0.10, 126, 0.10)
    assert confidence == 0.0
    assert stability == 100.0


def test_stable_strong_proxy_gets_high_confidence():
    confidence, stability = correlation_confidence(0.60, 0.62, 0.58, 126, 0.10)
    assert confidence >= 90
    assert stability >= 90


def test_unstable_proxy_is_discounted():
    stable, _ = correlation_confidence(0.45, 0.46, 0.44, 126, 0.10)
    unstable, _ = correlation_confidence(0.45, 0.50, -0.05, 126, 0.10)
    assert unstable < stable * 0.6


def test_low_confidence_rank_tends_to_neutral():
    assert neutralize_rank(99, 0) == 50
    assert neutralize_rank(1, 0) == 50
    assert 51 <= neutralize_rank(99, 10) <= 55
    assert 45 <= neutralize_rank(1, 10) <= 49


def test_leadership_adjustment_is_neutral_and_bounded():
    assert leadership_score(80, 50) == 80
    assert abs(leadership_score(80, 99) - 80) <= 5
    assert abs(leadership_score(80, 1) - 80) <= 5
