#!/usr/bin/env python3
"""Phase 6 empirical cohort observation helpers for StockScout Next.

This module is deliberately observation-only. It classifies rows from an already
computed canonical StockScout snapshot into the four roadmap cohorts and builds
compact, append-friendly observation records. It must not mutate canonical rows,
recompute StockScout scores, or reinterpret frozen LEGACY methodology.

Cohort definitions are transparent and reuse existing product contracts:
- "strong StockScout" is exactly the Phase 5 Prime / Ready Opportunities rule:
  opportunityScore >= 80, opportunityRank >= 90, and not extended.
- "low StockScout Opportunity" maps to the existing canonical PASS tier. The
  current Opportunity v2 tier contract defines PASS as score < 55. We consume
  the tier instead of recomputing or introducing a new threshold.
- LEGACY status is derived from the existing read-only confirmation sidecar
  adapter when canonical rows do not already carry the client projection.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from typing import Any, Iterable, Mapping

from build_legacy_confirmation_sidecar import build_sidecar

MODEL = "stockscout-phase6-empirical-cohorts-v1"
STRONG_OPPORTUNITY_MIN = 80.0
STRONG_RANK_MIN = 90.0
LOW_OPPORTUNITY_TIERS = frozenset({"PASS"})

COHORT_CONFIRMED = "PRIME_READY_CONFIRMED"
COHORT_EARLY = "PRIME_READY_EARLY"
COHORT_LEGACY_BUY_LOW = "LEGACY_BUY_LOW_STOCKSCOUT"
COHORT_RISK = "PRIME_READY_RISK"
COHORT_NAMES = (
    COHORT_CONFIRMED,
    COHORT_EARLY,
    COHORT_LEGACY_BUY_LOW,
    COHORT_RISK,
)


def finite(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def normalized_status(row: Mapping[str, Any], override: Any = None) -> str:
    raw = override if override is not None else row.get("legacyConfirmationStatus")
    return str(raw or "").strip().upper()


def is_strong_stockscout(row: Mapping[str, Any]) -> bool:
    """Reuse the exact Phase 5 strong StockScout membership contract."""
    score = finite(row.get("opportunityScore"))
    rank = finite(row.get("opportunityRank"))
    return (
        score is not None
        and score >= STRONG_OPPORTUNITY_MIN
        and rank is not None
        and rank >= STRONG_RANK_MIN
        and row.get("extended") is False
    )


def is_low_stockscout(row: Mapping[str, Any]) -> bool:
    """Use the existing Opportunity v2 PASS tier as the explicit low cohort."""
    return str(row.get("opportunityTier") or "").strip().upper() in LOW_OPPORTUNITY_TIERS


def has_legacy_buy(row: Mapping[str, Any]) -> bool:
    """Consume the already-captured frozen original-run BUY output only."""
    return row.get("originalRunBuySignal") is True


def classify_row(row: Mapping[str, Any], legacy_status: Any = None) -> tuple[str, ...]:
    """Return zero or more roadmap cohort memberships for one row.

    ``legacy_status`` is an observation-layer override supplied by the existing
    frozen LEGACY confirmation adapter. It never writes back to the StockScout row.
    """
    cohorts: list[str] = []
    status = normalized_status(row, legacy_status)
    strong = is_strong_stockscout(row)

    if strong and status == "CONFIRMED":
        cohorts.append(COHORT_CONFIRMED)
    if strong and status == "EARLY":
        cohorts.append(COHORT_EARLY)
    if has_legacy_buy(row) and is_low_stockscout(row):
        cohorts.append(COHORT_LEGACY_BUY_LOW)
    if strong and status == "RISK":
        cohorts.append(COHORT_RISK)
    return tuple(cohorts)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _resolved_status(row: Mapping[str, Any], confirmation: Mapping[str, Any] | None) -> str:
    captured = normalized_status({}, (confirmation or {}).get("status"))
    if captured and captured != "UNAVAILABLE":
        return captured
    # Fail open for an already-projected core payload. Canonical latest.json uses
    # the captured adapter path above; this fallback does not reconstruct LEGACY.
    return normalized_status(row)


def _observation(
    row: Mapping[str, Any], cohort: str, generated_at: str, legacy_status: str
) -> dict[str, Any]:
    return {
        "generatedAt": generated_at,
        "ticker": str(row.get("ticker") or ""),
        "cohort": cohort,
        "entryPrice": finite(row.get("price")),
        "opportunityScore": finite(row.get("opportunityScore")),
        "opportunityRank": finite(row.get("opportunityRank")),
        "opportunityTier": str(row.get("opportunityTier") or ""),
        "extended": bool(row.get("extended")),
        "legacyConfirmationStatus": legacy_status,
        "originalRunBuySignal": row.get("originalRunBuySignal") is True,
        "originalRunSellSignal": row.get("originalRunSellSignal") is True,
        "stage": row.get("stage"),
        "rsRank": finite(row.get("rsRank")),
        "breakoutPct": finite(row.get("breakoutPct")),
        "originalBreakoutVolumeConfirmed": row.get("originalBreakoutVolumeConfirmed") is True,
    }


def build_snapshot_observations(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic Phase 6 observation projection without mutation."""
    before = deepcopy(payload)
    generated_at = str(payload.get("generatedAt") or "")
    rows = payload.get("universe") or []
    sidecar = build_sidecar(payload)
    confirmations = sidecar.get("byTicker") or {}
    observations: list[dict[str, Any]] = []
    counts = {name: 0 for name in COHORT_NAMES}

    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        status = _resolved_status(row, confirmations.get(ticker))
        for cohort in classify_row(row, status):
            observations.append(_observation(row, cohort, generated_at, status))
            counts[cohort] += 1

    cohort_order = {name: index for index, name in enumerate(COHORT_NAMES)}
    observations.sort(key=lambda item: (cohort_order[item["cohort"]], item["ticker"]))
    result = {
        "model": MODEL,
        "generatedAt": generated_at,
        "source": {
            "rows": len(rows),
            "semanticSha256": _canonical_sha256(payload),
            "legacyConfirmationModel": sidecar.get("model"),
        },
        "definitions": {
            "strongStockScout": {
                "opportunityScoreMin": STRONG_OPPORTUNITY_MIN,
                "opportunityRankMin": STRONG_RANK_MIN,
                "extended": False,
                "source": "Phase 5 Prime / Ready Opportunities contract",
            },
            "lowStockScout": {
                "opportunityTiers": sorted(LOW_OPPORTUNITY_TIERS),
                "source": "canonical Opportunity v2 tier contract; PASS is score <55",
            },
            "legacyBuy": {
                "field": "originalRunBuySignal",
                "value": True,
                "source": "captured frozen LEGACY output",
            },
            "legacyConfirmation": {
                "source": "existing read-only confirmation sidecar adapter",
                "affectsStockScout": False,
            },
        },
        "cohortCounts": counts,
        "observations": observations,
    }
    if payload != before:
        raise AssertionError("Phase 6 observation builder mutated canonical payload")
    return result


def append_unique_observations(
    existing: Iterable[dict[str, Any]], incoming: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge observations idempotently by snapshot+ticker+cohort."""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in list(existing) + list(incoming):
        key = (
            str(item.get("generatedAt") or ""),
            str(item.get("ticker") or ""),
            str(item.get("cohort") or ""),
        )
        if not all(key):
            continue
        merged[key] = deepcopy(item)
    return [merged[key] for key in sorted(merged)]
