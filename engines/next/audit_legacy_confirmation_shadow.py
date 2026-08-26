#!/usr/bin/env python3
"""Read-only canonical audit for the StockScout Next LEGACY shadow projection.

Usage:
    python audit_legacy_confirmation_shadow.py [frontend/public/data/latest.json]

The audit enriches only an in-memory copy, then proves protected StockScout fields
and chart mapping are unchanged. It never writes the canonical dataset.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from audit_next_core_invariance import compare_payloads
from compute_legacy_confirmation import (
    LEGACY_CONFIRMATION_STATUSES,
    enrich_payload_with_legacy_confirmation,
)

DEFAULT_INPUT = Path("frontend/public/data/latest.json")


def audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    enriched = enrich_payload_with_legacy_confirmation(payload)
    errors = compare_payloads(payload, enriched)
    if errors:
        raise AssertionError("; ".join(errors[:20]))

    rows = enriched.get("universe") or []
    counts = Counter((row.get("legacyConfirmation") or {}).get("status") for row in rows)
    allowed = set(LEGACY_CONFIRMATION_STATUSES) | {"UNAVAILABLE"}
    unknown = sorted(status for status in counts if status not in allowed)
    if unknown:
        raise AssertionError(f"Unknown LEGACY confirmation statuses: {unknown}")

    for row in rows:
        confirmation = row.get("legacyConfirmation") or {}
        if confirmation.get("affectsStockScout") is not False:
            raise AssertionError(f"{row.get('ticker')}: affectsStockScout must be false")
        emitted_sell = bool(row.get("originalRunSellSignal"))
        emitted_buy = bool(row.get("originalRunBuySignal"))
        status = confirmation.get("status")
        if emitted_sell and status != "RISK":
            raise AssertionError(f"{row.get('ticker')}: emitted SELL must project to RISK")
        if emitted_buy and not emitted_sell and status != "CONFIRMED":
            raise AssertionError(f"{row.get('ticker')}: emitted BUY must project to CONFIRMED")

    summary = enriched.get("legacyConfirmationSummary") or {}
    if summary.get("total") != len(rows):
        raise AssertionError("legacyConfirmationSummary total mismatch")

    return {
        "rows": len(rows),
        "counts": dict(sorted(counts.items())),
        "invarianceErrors": len(errors),
        "chartShardsIdentical": (payload.get("chartShards") or {})
        == (enriched.get("chartShards") or {}),
        "available": summary.get("available"),
    }


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    if not path.exists():
        raise SystemExit(f"Missing canonical payload: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = audit_payload(payload)
    print("LEGACY CONFIRMATION SHADOW AUDIT: PASS")
    print(f"Rows: {result['rows']:,}; available: {result['available']:,}")
    print("Status counts:", result["counts"])
    print("Core invariance errors:", result["invarianceErrors"])
    print("Chart shards identical:", result["chartShardsIdentical"])


if __name__ == "__main__":
    main()
