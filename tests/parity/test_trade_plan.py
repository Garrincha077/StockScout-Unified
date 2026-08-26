from __future__ import annotations

from copy import deepcopy

import pytest

from stock_scout.scoring.trade_plan import (
    TRADE_PLAN_VERSION,
    calculate_trade_plan,
    derive_trade_plan,
    derive_trade_readiness,
    project_trade_readiness,
)


def _plan(**overrides):
    inputs = {
        "price": 100.0,
        "atr20": 2.0,
        "trigger_reference_level": 100.0,
        "structural_invalidation_level": 92.0,
    }
    inputs.update(overrides)
    return calculate_trade_plan(**inputs)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("price", "missing_price"),
        ("atr20", "missing_atr"),
        ("trigger_reference_level", "missing_trigger"),
        (
            "structural_invalidation_level",
            "missing_structural_invalidation",
        ),
    ],
)
def test_missing_required_inputs_take_precedence(field, reason):
    plan = _plan(**{field: None})

    assert plan.status == "insufficient_data"
    assert reason in plan.reason_codes
    assert plan.tactical_stop_level is None


def test_an_invalidation_at_or_above_price_is_not_a_valid_trade_boundary():
    plan = _plan(
        price=95.0,
        trigger_reference_level=100.0,
        structural_invalidation_level=96.0,
    )

    assert plan.status == "insufficient_data"
    assert plan.reason_codes == ["invalid_structural_invalidation"]
    assert plan.structural_invalidation_level == pytest.approx(96.0)
    assert plan.entry_risk_pct is None


def test_more_than_ten_percent_risk_is_not_tradeable_before_trigger_state():
    plan = _plan(
        price=95.0,
        trigger_reference_level=100.0,
        structural_invalidation_level=89.0,
    )

    assert plan.trigger_state == "pending"
    assert plan.entry_reference_level == pytest.approx(100.0)
    assert plan.entry_risk_pct == pytest.approx(11.0)
    assert plan.status == "not_tradeable"
    assert plan.reason_codes == ["entry_risk_above_limit"]
    assert plan.tactical_stop_level is None


def test_exactly_ten_percent_risk_is_allowed_and_waits_for_trigger():
    plan = _plan(
        price=95.0,
        trigger_reference_level=100.0,
        structural_invalidation_level=90.0,
    )

    assert plan.entry_risk_pct == pytest.approx(10.0)
    assert plan.status == "trigger_pending"
    assert plan.trigger_state == "pending"
    assert plan.reason_codes == ["trigger_pending"]


def test_a_fresh_cross_is_entry_ready_and_exposes_the_raw_stop_for_sizing():
    plan = _plan(
        price=100.5,
        trigger_reference_level=100.0,
        structural_invalidation_level=92.0,
    )

    assert plan.status == "entry_ready"
    assert plan.trigger_state == "fresh"
    assert plan.entry_reference_level == pytest.approx(100.5)
    assert plan.extension_atr == pytest.approx(0.25)
    assert plan.tactical_stop_level == pytest.approx(92.0)
    assert plan.tactical_risk_pct == plan.entry_risk_pct
    assert plan.source == "primary_detector"
    assert plan.version == TRADE_PLAN_VERSION


def test_more_than_half_an_atr_above_trigger_waits_for_retest():
    plan = _plan(
        price=102.0,
        trigger_reference_level=100.0,
        structural_invalidation_level=93.0,
    )

    assert plan.status == "wait_for_retest"
    assert plan.trigger_state == "extended"
    assert plan.extension_atr == pytest.approx(1.0)
    assert plan.tactical_stop_level is None
    assert plan.tactical_risk_pct is None


def test_extension_gate_uses_raw_value_not_rounded_display_value():
    plan = _plan(
        price=101.008,
        trigger_reference_level=100.0,
        structural_invalidation_level=93.0,
    )

    assert plan.extension_atr == pytest.approx(0.5)
    assert plan.trigger_state == "extended"
    assert plan.status == "wait_for_retest"
    assert plan.tactical_stop_level is None


def test_legacy_derivation_uses_only_the_primary_setup_raw_levels_read_only():
    candidate = {
        "ticker": "RAW",
        "price": 100.5,
        "atr20": 2.0,
        "primary_setup": "glb",
        # These legacy headline values are deliberately different.
        "trigger_level": 99.0,
        "invalidation_level": 75.0,
        "setups": {
            "glb": {
                "trigger_level": 100.0,
                "invalidation_level": 92.0,
            }
        },
    }
    before = deepcopy(candidate)

    plan = derive_trade_plan(candidate)

    assert candidate == before
    assert plan.status == "entry_ready"
    assert plan.trigger_reference_level == pytest.approx(100.0)
    assert plan.structural_invalidation_level == pytest.approx(92.0)
    assert plan.source == "legacy_primary_setup"


def test_legacy_derivation_does_not_guess_from_headline_levels():
    plan = derive_trade_plan(
        {
            "ticker": "OLD",
            "price": 100.0,
            "atr20": 2.0,
            "trigger_level": 101.0,
            "invalidation_level": 90.0,
        }
    )

    assert plan.status == "insufficient_data"
    assert plan.trigger_reference_level is None
    assert plan.structural_invalidation_level is None
    assert "missing_trigger" in plan.reason_codes
    assert "missing_structural_invalidation" in plan.reason_codes


def test_a_valid_persisted_plan_wins_over_legacy_detector_fields():
    persisted = _plan().model_dump()
    candidate = {
        "price": 50.0,
        "atr20": 1.0,
        "primary_setup": "glb",
        "setups": {
            "glb": {"trigger_level": 500.0, "invalidation_level": 1.0}
        },
        "trade_plan": persisted,
    }

    assert derive_trade_plan(candidate).model_dump() == persisted


def test_flat_readiness_fields_are_a_read_only_projection_of_the_trade_plan():
    candidate = {
        "ticker": "NEAR",
        "price": 99.0,
        "atr20": 2.0,
        "primary_setup": "glb",
        "setups": {
            "glb": {
                "trigger_level": 100.0,
                "invalidation_level": 92.0,
            }
        },
    }
    before = deepcopy(candidate)

    plan, fields = derive_trade_readiness(candidate)
    projected = project_trade_readiness(candidate)

    assert plan.status == "trigger_pending"
    assert fields == {
        "trade_status": "trigger_pending",
        "trigger_state": "pending",
        "entry_risk_pct": pytest.approx(8.0),
        "extension_atr": None,
        "distance_to_trigger_pct": pytest.approx(-1.0),
        "distance_to_trigger_atr": pytest.approx(-0.5),
    }
    assert projected["trade_plan"]["status"] == fields["trade_status"]
    assert projected["distance_to_trigger_pct"] == fields["distance_to_trigger_pct"]
    assert candidate == before
