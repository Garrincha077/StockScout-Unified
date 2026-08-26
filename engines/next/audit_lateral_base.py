#!/usr/bin/env python3
"""Guardrails for observational Lateral Base v1 output."""
from __future__ import annotations

import json
import math
from pathlib import Path

DATA = Path("frontend/public/data/latest.json")
MODEL = "lateral-base-v1-observational"
REQUIRED = (
    "lateralBaseScore",
    "contractionQuality",
    "launchReadiness",
    "neglectedLaunchScore",
    "lateralBaseCandidate",
    "lateralBaseReasons",
)


def finite(v):
    try:
        return math.isfinite(float(v))
    except Exception:
        return False


def main():
    if not DATA.exists():
        print("LATERAL BASE AUDIT: latest.json missing")
        return 1
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    if payload.get("lateralBaseModel") != MODEL:
        print(f"LATERAL BASE AUDIT: wrong model {payload.get('lateralBaseModel')!r}")
        return 1
    rows = payload.get("universe") or []
    if not rows:
        print("LATERAL BASE AUDIT: empty universe")
        return 1

    failures = []
    coverage = {k: 0 for k in REQUIRED}
    for row in rows:
        ticker = row.get("ticker", "?")
        for key in REQUIRED:
            if key in row:
                coverage[key] += 1
        for key in ("lateralBaseScore", "contractionQuality", "launchReadiness", "neglectedLaunchScore"):
            value = row.get(key)
            if not finite(value) or not (0 <= float(value) <= 100):
                failures.append(f"{ticker}: invalid {key}={value!r}")
        if row.get("lateralBaseCandidate") and bool(row.get("extended")):
            failures.append(f"{ticker}: extended candidate")
        if row.get("lateralBaseCandidate") and float(row.get("lateralBaseScore", 0) or 0) < 60:
            failures.append(f"{ticker}: candidate with weak base")
        if row.get("lateralBaseCandidate") and float(row.get("contractionQuality", 0) or 0) < 50:
            failures.append(f"{ticker}: candidate with weak contraction")
        if row.get("lateralBaseCandidate") and float(row.get("launchReadiness", 0) or 0) < 55:
            failures.append(f"{ticker}: candidate with weak launch")
        if not isinstance(row.get("lateralBaseReasons"), list):
            failures.append(f"{ticker}: reasons not list")

    minimum = math.ceil(len(rows) * 0.95)
    for key, count in coverage.items():
        if count < minimum:
            failures.append(f"coverage {key}: {count}/{len(rows)} < 95%")

    print(
        f"LATERAL BASE AUDIT: rows={len(rows):,}, "
        f"candidates={sum(bool(r.get('lateralBaseCandidate')) for r in rows):,}, "
        f"coverage_min={min(coverage.values())}/{len(rows)}"
    )
    if failures:
        for failure in failures[:25]:
            print(" -", failure)
        if len(failures) > 25:
            print(f" - ... {len(failures)-25} more")
        return 1
    print("LATERAL BASE AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
