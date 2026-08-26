"""Read-only adapter for observed Ryan/LEGACY output.

This module deliberately refuses to reconstruct a legacy verdict from
StockScout fields.  If the frozen engine did not emit a record, the public
answer is ``UNAVAILABLE`` rather than a guessed confirmation.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

VALID_STATUSES = frozenset({"CONFIRMED", "EARLY", "NEUTRAL", "CONFLICT", "RISK"})


def unavailable_confirmation() -> dict[str, Any]:
    return {
        "model": "legacy-confirmation-shadow-v1",
        "version": "shadow-v1",
        "status": "UNAVAILABLE",
        "available": False,
        "provenance": "unavailable",
        "affectsStockScout": False,
        "reasons": ["LEGACY_CAPTURE_MISSING"],
    }


def observed_confirmation(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return unavailable_confirmation()
    status = str(
        value.get("status")
        or value.get("confirmationStatus")
        or value.get("legacyConfirmationStatus")
        or ""
    ).upper()
    if status not in VALID_STATUSES:
        return unavailable_confirmation()
    return {
        "model": "legacy-confirmation-shadow-v1",
        "version": "shadow-v1",
        "status": status,
        "available": True,
        "provenance": "observed-frozen-output",
        "sourceModel": deepcopy(value.get("sourceModel") or value.get("model")),
        "affectsStockScout": False,
        "reasons": deepcopy(value.get("reasons") or []),
        "evidence": deepcopy(value.get("evidence") or {}),
    }
