#!/usr/bin/env python3
"""Fail if a shadow-only StockScout Next change mutates protected core fields.

Usage:
    python audit_next_core_invariance.py before.json after.json

This audit is intentionally strict. It is designed for confirmation/UI work where
all protected StockScout outputs must remain byte-for-value identical per ticker.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROTECTED_FIELDS = (
    "opportunityScore",
    "opportunityPotential",
    "opportunityTiming",
    "opportunityRank",
    "opportunityTier",
    "emergingLeaderScore",
    "maClusterScore",
    "maClusterPhase",
    "maClusterTier",
    "groupRank",
    "groupConfidence",
    "fundamentalEvidenceScore",
    "stage",
    "rsRank",
    "leadershipScore",
)


def _load(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("universe") or []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            raise AssertionError("Universe row missing ticker")
        if ticker in out:
            raise AssertionError(f"Duplicate ticker: {ticker}")
        out[ticker] = row
    return out


def compare_payloads(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    b = _rows(before)
    a = _rows(after)

    if set(b) != set(a):
        missing = sorted(set(b) - set(a))
        added = sorted(set(a) - set(b))
        errors.append(f"Universe changed: missing={missing[:10]} added={added[:10]}")

    for ticker in sorted(set(b) & set(a)):
        br = b[ticker]
        ar = a[ticker]
        for field in PROTECTED_FIELDS:
            if br.get(field) != ar.get(field):
                errors.append(
                    f"{ticker} {field}: before={br.get(field)!r} after={ar.get(field)!r}"
                )

    before_shards = before.get("chartShards") or {}
    after_shards = after.get("chartShards") or {}
    if before_shards != after_shards:
        errors.append("chartShards mapping changed")

    return errors


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python audit_next_core_invariance.py before.json after.json")

    before = _load(sys.argv[1])
    after = _load(sys.argv[2])
    errors = compare_payloads(before, after)
    if errors:
        print("NEXT CORE INVARIANCE: FAIL")
        for error in errors[:100]:
            print(" -", error)
        if len(errors) > 100:
            print(f" - ... {len(errors) - 100} more differences")
        raise SystemExit(1)

    print(
        "NEXT CORE INVARIANCE: PASS "
        f"({len(before.get('universe') or [])} rows; {len(PROTECTED_FIELDS)} protected fields)"
    )


if __name__ == "__main__":
    main()
