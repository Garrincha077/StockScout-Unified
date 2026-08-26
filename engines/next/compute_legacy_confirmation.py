"""Read-only LEGACY confirmation projection for StockScout Next shadow mode.

The frozen/original engine remains authoritative for its own outputs. This module
only classifies already-captured boolean/emission evidence and exposes a compact,
append-only explanation layer. It never mutates StockScout Core fields or computes
a blended confirmation score.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

LEGACY_CONFIRMATION_VERSION = "shadow-v1"
LEGACY_CONFIRMATION_MODEL = "legacy-confirmation-shadow-v1"
LEGACY_CONFIRMATION_STATUSES = frozenset(
    {"CONFIRMED", "EARLY", "NEUTRAL", "CONFLICT", "RISK"}
)
OBSERVED_FIELDS = ("score", "positionPct", "daysFromLow", "mature", "pathReason")


def _normalize_status(value: Any) -> str:
    if not isinstance(value, str):
        return "UNAVAILABLE"
    status = value.strip().upper()
    return status if status in LEGACY_CONFIRMATION_STATUSES else "UNAVAILABLE"


def compute_legacy_confirmation(
    observed: Mapping[str, Any] | None,
    *,
    source: str = "frozen-legacy",
) -> dict[str, Any]:
    """Normalize an already-observed confirmation record without reconstructing it."""
    result: dict[str, Any] = {
        "status": "UNAVAILABLE",
        "available": False,
        "version": LEGACY_CONFIRMATION_VERSION,
        "provenance": "unavailable",
        "source": None,
        "score": None,
        "positionPct": None,
        "daysFromLow": None,
        "mature": None,
        "pathReason": None,
    }
    if not observed:
        return result

    result["provenance"] = "observed"
    result["source"] = source
    status = _normalize_status(
        observed.get("status")
        or observed.get("confirmationStatus")
        or observed.get("legacyConfirmationStatus")
    )
    result["status"] = status
    result["available"] = status != "UNAVAILABLE"

    for field in OBSERVED_FIELDS:
        if field in observed:
            result[field] = deepcopy(observed[field])

    return result


def attach_legacy_confirmation(
    candidate: Mapping[str, Any],
    observed: Mapping[str, Any] | None,
    *,
    source: str = "frozen-legacy",
) -> dict[str, Any]:
    """Return a deep-copied candidate with one normalized observed projection."""
    enriched = deepcopy(dict(candidate))
    enriched["legacyConfirmation"] = compute_legacy_confirmation(observed, source=source)
    return enriched


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bool(*values: Any) -> bool:
    return any(value is True for value in values)


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return deepcopy(value)
    return None


def _market_evidence(market: Mapping[str, Any] | None) -> dict[str, Any]:
    original_gate = _mapping(_mapping(market).get("originalSignalGate"))
    if not original_gate:
        return {
            "available": False,
            "ref": "market.originalSignalGate",
            "buyEnabled": None,
            "sellEnabled": None,
            "spyPhase": None,
            "spyTrend": None,
        }
    gate = _mapping(original_gate.get("gate"))
    spy = _mapping(original_gate.get("spy"))
    return {
        "available": True,
        "ref": "market.originalSignalGate",
        "buyEnabled": deepcopy(gate.get("should_generate_buys")),
        "sellEnabled": deepcopy(gate.get("should_generate_sells")),
        "spyPhase": deepcopy(spy.get("phase")),
        "spyTrend": deepcopy(spy.get("trend")),
    }


def project_captured_legacy_confirmation(
    candidate: Mapping[str, Any],
    *,
    market: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify one candidate from already-captured frozen LEGACY outputs only.

    Status precedence is intentionally explicit and non-numeric:
    RISK (emitted original SELL) > CONFIRMED (emitted original BUY) >
    CONFLICT (raw BUY/SELL not emitted) > EARLY (frozen TT/VCP/breakout
    boolean evidence) > NEUTRAL. Missing original-engine capture is UNAVAILABLE.
    """
    row = _mapping(candidate)
    engine = _mapping(row.get("originalEngine"))
    if not engine:
        return {
            "model": LEGACY_CONFIRMATION_MODEL,
            "version": LEGACY_CONFIRMATION_VERSION,
            "status": "UNAVAILABLE",
            "available": False,
            "provenance": "unavailable",
            "sourceModel": None,
            "captureModel": None,
            "classificationBasis": "captured-frozen-boolean-outputs-only",
            "affectsStockScout": False,
            "reasons": ["LEGACY_CAPTURE_MISSING"],
            "evidence": {"market": _market_evidence(market)},
        }

    buy = _mapping(engine.get("buy"))
    sell = _mapping(engine.get("sell"))
    minervini = _mapping(engine.get("minervini"))
    vcp = _mapping(engine.get("vcp"))
    breakout = _mapping(engine.get("breakout"))

    emitted_sell = _bool(row.get("originalRunSellSignal"), sell.get("emittedByOriginalRun"))
    emitted_buy = _bool(row.get("originalRunBuySignal"), buy.get("emittedByOriginalRun"))
    raw_sell = _bool(row.get("originalSell"), sell.get("isSell"))
    raw_buy = _bool(row.get("originalBuy"), buy.get("isBuy"))
    market_qualified_buy = _bool(row.get("originalMarketQualifiedBuy"), buy.get("marketQualified"))
    tt_pass = minervini.get("passes") is True
    vcp_detected = vcp.get("isVcp") is True
    breakout_detected = breakout.get("is_breakout") is True

    reasons: list[str] = []
    if emitted_sell:
        status = "RISK"
        reasons.append("ORIGINAL_RUN_SELL")
        if emitted_buy:
            reasons.append("ORIGINAL_RUN_BUY_ALSO_PRESENT")
    elif emitted_buy:
        status = "CONFIRMED"
        reasons.append("ORIGINAL_RUN_BUY")
    elif (raw_buy and not market_qualified_buy) or raw_sell:
        status = "CONFLICT"
        if raw_buy and not market_qualified_buy:
            reasons.append("RAW_BUY_MARKET_BLOCKED")
        if raw_sell:
            reasons.append("RAW_SELL_NOT_EMITTED")
    elif tt_pass or vcp_detected or breakout_detected:
        status = "EARLY"
        if tt_pass:
            reasons.append("TREND_TEMPLATE_PASS")
        if vcp_detected:
            reasons.append("VCP_DETECTED")
        if breakout_detected:
            reasons.append("BREAKOUT_DETECTED")
    else:
        status = "NEUTRAL"
        reasons.append("NO_FROZEN_CONFIRMATION_TRIGGER")

    failed_breakout = any(
        "failed breakout" in str(reason).lower() for reason in (sell.get("reasons") or [])
    )

    return {
        "model": LEGACY_CONFIRMATION_MODEL,
        "version": LEGACY_CONFIRMATION_VERSION,
        "status": status,
        "available": True,
        "provenance": "captured-frozen-output",
        "sourceModel": engine.get("model"),
        "captureModel": engine.get("completeSourceCaptureModel"),
        "classificationBasis": "captured-frozen-boolean-outputs-only",
        "affectsStockScout": False,
        "reasons": reasons,
        "evidence": {
            "market": _market_evidence(market),
            "buy": {
                "raw": raw_buy,
                "marketQualified": market_qualified_buy,
                "emittedByOriginalRun": emitted_buy,
                "score": _first(row.get("originalBuyScore"), buy.get("score")),
                "entryQuality": _first(row.get("originalEntryQuality"), buy.get("entryQuality")),
                "riskReward": _first(row.get("originalRR"), buy.get("riskReward")),
                "riskPct": _first(row.get("originalRiskPct"), buy.get("riskPct")),
                "stopLoss": _first(row.get("originalStopLoss"), buy.get("stopLoss")),
                "rewardTarget": _first(row.get("originalRewardTarget"), buy.get("rewardTarget")),
            },
            "trendTemplate": {
                "passes": tt_pass,
                "score": _first(row.get("originalTTScore"), minervini.get("score")),
                "passed": _first(row.get("originalTTPasses"), minervini.get("passed")),
                "total": deepcopy(minervini.get("total")),
                "criteria": deepcopy(minervini.get("criteria")),
            },
            "vcp": {
                "isVcp": vcp_detected,
                "quality": _first(row.get("originalVcpQuality"), vcp.get("quality")),
                "contractionCount": deepcopy(vcp.get("contractionCount")),
                "contractionQuality": deepcopy(vcp.get("contractionQuality")),
                "volumeQuality": deepcopy(vcp.get("volumeQuality")),
                "baseLengthWeeks": deepcopy(vcp.get("baseLengthWeeks")),
                "breakoutVolumeRatio": deepcopy(vcp.get("breakoutVolumeRatio")),
                "qualityFactors": deepcopy(vcp.get("qualityFactors")),
                "contractions": deepcopy(vcp.get("contractions")),
            },
            "adVolume": {
                "ratio": _first(row.get("originalAdVolumeRatio"), buy.get("adVolumeRatio")),
                "avgUp": deepcopy(buy.get("avgVolumeUpDays")),
                "avgDown": deepcopy(buy.get("avgVolumeDownDays")),
            },
            "breakout": {
                "detected": breakout_detected,
                "type": _first(row.get("originalBreakoutType"), breakout.get("breakout_type")),
                "level": _first(row.get("originalBreakoutLevel"), breakout.get("breakout_level")),
                "volumeConfirmed": _first(
                    row.get("originalBreakoutVolumeConfirmed"), breakout.get("volume_confirmed")
                ),
                "volumeRatio": deepcopy(breakout.get("volume_ratio")),
            },
            "sell": {
                "raw": raw_sell,
                "emittedByOriginalRun": emitted_sell,
                "score": _first(row.get("originalSellScore"), sell.get("score")),
                "severity": _first(row.get("originalSellSeverity"), sell.get("severity")),
                "breakdownLevel": deepcopy(sell.get("breakdownLevel")),
                "failedBreakoutMentioned": failed_breakout,
                "reasons": deepcopy(sell.get("reasons") or []),
            },
        },
    }


def compact_legacy_confirmation(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Drop duplicated evidence while keeping pointers to its canonical sources."""
    return {
        "model": deepcopy(projection.get("model")),
        "version": deepcopy(projection.get("version")),
        "status": deepcopy(projection.get("status")),
        "available": deepcopy(projection.get("available")),
        "provenance": deepcopy(projection.get("provenance")),
        "sourceModel": deepcopy(projection.get("sourceModel")),
        "captureModel": deepcopy(projection.get("captureModel")),
        "classificationBasis": deepcopy(projection.get("classificationBasis")),
        "affectsStockScout": False,
        "reasons": deepcopy(projection.get("reasons") or []),
        "evidenceRefs": {
            "market": "market.originalSignalGate",
            "candidate": "originalEngine",
        },
    }


def enrich_candidate_from_captured_legacy(
    candidate: Mapping[str, Any],
    *,
    market: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy with a compact, append-only confirmation projection."""
    enriched = deepcopy(dict(candidate))
    detailed = project_captured_legacy_confirmation(candidate, market=market)
    enriched["legacyConfirmation"] = compact_legacy_confirmation(detailed)
    return enriched


def enrich_payload_with_legacy_confirmation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Append the shadow projection to every universe row and add a compact summary."""
    enriched = deepcopy(dict(payload))
    market = _mapping(enriched.get("market"))
    universe = enriched.get("universe") or []
    counts = {status: 0 for status in sorted(LEGACY_CONFIRMATION_STATUSES | {"UNAVAILABLE"})}

    for index, row in enumerate(universe):
        projected_row = enrich_candidate_from_captured_legacy(row, market=market)
        universe[index] = projected_row
        status = projected_row["legacyConfirmation"]["status"]
        counts[status] = counts.get(status, 0) + 1

    enriched["legacyConfirmationSummary"] = {
        "model": LEGACY_CONFIRMATION_MODEL,
        "version": LEGACY_CONFIRMATION_VERSION,
        "affectsStockScout": False,
        "total": len(universe),
        "available": len(universe) - counts.get("UNAVAILABLE", 0),
        "counts": counts,
    }
    return enriched
