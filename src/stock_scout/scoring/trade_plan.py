"""A single, conservative interpretation of setup entry and risk levels.

Detectors own market structure. This module does not move their trigger or
invalidation to make a trade look convenient; it only says whether those raw
levels currently describe an implementable entry.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from stock_scout.scoring.models import TradePlan

TRADE_PLAN_VERSION = 1
MAX_ENTRY_RISK_PCT = 10.0
MAX_FRESH_EXTENSION_ATR = 0.5


def _positive_finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result <= 0:
        return None
    return result


def _missing_or_invalid_reason(value: Any, field: str) -> str:
    return f"missing_{field}" if value is None else f"invalid_{field}"


def calculate_trade_plan(
    *,
    price: Any,
    atr20: Any,
    trigger_reference_level: Any,
    structural_invalidation_level: Any,
    source: str = "primary_detector",
) -> TradePlan:
    """Classify implementation readiness without changing detector levels.

    State precedence is intentional: incomplete/invalid inputs, excessive
    structural risk, a pending trigger, a fresh cross, then an extended cross.
    The 10% threshold is a readiness gate, not a replacement stop.
    """
    price_value = _positive_finite(price)
    atr_value = _positive_finite(atr20)
    trigger_value = _positive_finite(trigger_reference_level)
    invalidation_value = _positive_finite(structural_invalidation_level)

    reasons: list[str] = []
    for raw, parsed, field in (
        (price, price_value, "price"),
        (atr20, atr_value, "atr"),
        (trigger_reference_level, trigger_value, "trigger"),
        (
            structural_invalidation_level,
            invalidation_value,
            "structural_invalidation",
        ),
    ):
        if parsed is None:
            reasons.append(_missing_or_invalid_reason(raw, field))

    trigger_state = "unavailable"
    entry_reference: float | None = None
    extension_atr: float | None = None
    raw_extension_atr: float | None = None
    if price_value is not None and trigger_value is not None:
        entry_reference = trigger_value if price_value < trigger_value else price_value
        if price_value < trigger_value:
            trigger_state = "pending"
        elif atr_value is not None:
            raw_extension_atr = (price_value - trigger_value) / atr_value
            extension_atr = round(raw_extension_atr, 2)
            trigger_state = (
                "fresh"
                if raw_extension_atr <= MAX_FRESH_EXTENSION_ATR
                else "extended"
            )

    raw_entry_risk: float | None = None
    entry_risk_pct: float | None = None
    if (
        price_value is not None
        and entry_reference is not None
        and invalidation_value is not None
    ):
        # A structural invalidation already at/above today's price is not a
        # usable thesis boundary, even while an overhead trigger is pending.
        if invalidation_value >= price_value:
            reasons.append("invalid_structural_invalidation")
        else:
            raw_entry_risk = (
                (entry_reference - invalidation_value) / entry_reference * 100.0
            )
            entry_risk_pct = round(raw_entry_risk, 2)

    base: dict[str, Any] = {
        "trigger_state": trigger_state,
        "trigger_reference_level": trigger_value,
        "entry_reference_level": entry_reference,
        "structural_invalidation_level": invalidation_value,
        "entry_risk_pct": entry_risk_pct,
        "extension_atr": extension_atr,
        "tactical_stop_level": None,
        "tactical_risk_pct": None,
        "source": source,
        "version": TRADE_PLAN_VERSION,
    }

    if reasons:
        return TradePlan(
            status="insufficient_data",
            reason_codes=reasons,
            **base,
        )

    # The early return above establishes the complete-data invariant for every
    # branch below (and makes it explicit to static type checkers).
    assert price_value is not None
    assert trigger_value is not None
    assert invalidation_value is not None
    assert raw_entry_risk is not None
    assert entry_risk_pct is not None

    if raw_entry_risk > MAX_ENTRY_RISK_PCT:
        return TradePlan(
            status="not_tradeable",
            reason_codes=["entry_risk_above_limit"],
            **base,
        )

    if price_value < trigger_value:
        return TradePlan(
            status="trigger_pending",
            reason_codes=["trigger_pending"],
            **base,
        )

    assert raw_extension_atr is not None
    if raw_extension_atr <= MAX_FRESH_EXTENSION_ATR:
        return TradePlan(
            status="entry_ready",
            reason_codes=["entry_ready"],
            **{
                **base,
                "tactical_stop_level": invalidation_value,
                "tactical_risk_pct": entry_risk_pct,
            },
        )

    return TradePlan(
        status="wait_for_retest",
        reason_codes=["extension_above_half_atr"],
        **base,
    )


def derive_trade_plan(
    candidate: Mapping[str, Any],
    *,
    source_if_derived: str = "legacy_primary_setup",
) -> TradePlan:
    """Return a persisted plan or derive one read-only for a legacy row.

    Legacy headline levels are deliberately ignored: historical
    ``invalidation_level`` values may have been clamped to a made-up percentage.
    Only ``setups[primary_setup]`` is a reliable record of detector structure.
    """
    persisted = candidate.get("trade_plan")
    if isinstance(persisted, TradePlan):
        return persisted
    if persisted is not None:
        try:
            return TradePlan.model_validate(persisted)
        except (ValidationError, TypeError, ValueError):
            # A malformed persisted object is no safer than no object. Fall
            # through to the detector record rather than failing the API row.
            pass

    primary_setup = candidate.get("primary_setup")
    setups = candidate.get("setups")
    raw_setup: Mapping[str, Any] | None = None
    if primary_setup and isinstance(setups, Mapping):
        candidate_setup = setups.get(primary_setup)
        if isinstance(candidate_setup, Mapping):
            raw_setup = candidate_setup

    return calculate_trade_plan(
        price=candidate.get("price"),
        atr20=candidate.get("atr20"),
        trigger_reference_level=(
            raw_setup.get("trigger_level") if raw_setup is not None else None
        ),
        structural_invalidation_level=(
            raw_setup.get("invalidation_level") if raw_setup is not None else None
        ),
        source=source_if_derived,
    )


def derive_trade_readiness(
    candidate: Mapping[str, Any],
    *,
    source_if_derived: str = "legacy_primary_setup",
) -> tuple[TradePlan, dict[str, str | float | None]]:
    """Return the authoritative plan plus its flat, read-only query fields.

    ``trade_plan`` remains the single source of readiness semantics.  The flat
    values are deliberately a projection of it, for list filtering and SQL
    queries that cannot conveniently inspect a nested Pydantic object.  This
    function never writes into ``candidate``; callers may safely use it while
    browsing a historical report.
    """
    plan = derive_trade_plan(candidate, source_if_derived=source_if_derived)
    price = _positive_finite(candidate.get("price"))
    atr20 = _positive_finite(candidate.get("atr20"))
    trigger = plan.trigger_reference_level

    distance_to_trigger_pct: float | None = None
    distance_to_trigger_atr: float | None = None
    if price is not None and trigger is not None and trigger > 0:
        distance_to_trigger_pct = round((price - trigger) / trigger * 100.0, 2)
        if atr20 is not None:
            distance_to_trigger_atr = round((price - trigger) / atr20, 2)

    return plan, {
        "trade_status": plan.status,
        "trigger_state": plan.trigger_state,
        "entry_risk_pct": plan.entry_risk_pct,
        "extension_atr": plan.extension_atr,
        "distance_to_trigger_pct": distance_to_trigger_pct,
        "distance_to_trigger_atr": distance_to_trigger_atr,
    }


def project_trade_readiness(
    candidate: Mapping[str, Any],
    *,
    source_if_derived: str = "legacy_primary_setup",
) -> dict[str, Any]:
    """Copy a candidate with flat readiness fields for in-memory filtering.

    Keeping this non-mutating is important: the pipeline evaluates alerts
    against freshly written reports, while users must still be able to open
    historical reports without a browse operation changing their evidence.
    """
    plan, fields = derive_trade_readiness(
        candidate,
        source_if_derived=source_if_derived,
    )
    return {**candidate, "trade_plan": plan.model_dump(), **fields}
