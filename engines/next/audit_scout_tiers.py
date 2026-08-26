#!/usr/bin/env python3
"""Audit quality-confirmed Scout Tier invariants.

Timing Tier describes the 10W/30W entry geometry only. Scout Tier combines that
phase with transparent Emerging + RS quality gates so a technically pretty cluster
with weak leadership cannot be presented as a top-priority stock.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA = Path("frontend/public/data/latest.json")


def num(row, key, default=0.0):
    try:
        value = row.get(key, default)
        return float(default if value is None else value)
    except Exception:
        return float(default)


def expected_tier(row):
    phase = str(row.get("maClusterPhase") or "NONE")
    if phase not in {"WATCH", "READY", "ENTRY"}:
        return None
    emerging = num(row, "emergingLeaderScore", num(row, "opportunityScore"))
    rs = num(row, "rsRank")
    accel = num(row, "rsAcceleration")
    stage = int(num(row, "stage"))
    extended = bool(row.get("extended"))
    strong = emerging >= 55 and rs >= 70 and accel > 0 and stage in (1, 2) and not extended
    confirmed = emerging >= 45 and rs >= 55 and accel >= 0 and stage in (1, 2) and not extended
    if phase in {"READY", "ENTRY"} and strong:
        return "A"
    if (phase in {"READY", "ENTRY"} and confirmed) or (phase == "WATCH" and strong):
        return "B"
    return "C"


def main():
    if not DATA.exists():
        print(f"Missing {DATA}")
        return 1
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    if not rows:
        print("No universe rows")
        return 1

    errors = []
    ranks = {None: 0, "C": 1, "B": 2, "A": 3}
    for row in rows:
        ticker = str(row.get("ticker") or "?")
        expected = expected_tier(row)
        actual = row.get("scoutTier")
        rank = row.get("scoutTierRank")
        phase = str(row.get("maClusterPhase") or "NONE")
        if actual != expected:
            errors.append(f"{ticker}: Scout tier {actual} != expected {expected}")
            continue
        if rank != ranks[expected]:
            errors.append(f"{ticker}: Scout rank {rank} invalid for {expected}")
        if expected is None:
            if row.get("scoutTierLabel") != "—":
                errors.append(f"{ticker}: inactive row has Scout label")
            continue
        if row.get("scoutTierLabel") != f"{expected} · {phase}":
            errors.append(f"{ticker}: Scout label mismatch {row.get('scoutTierLabel')}")
        if expected == "A":
            if phase not in {"READY", "ENTRY"}:
                errors.append(f"{ticker}: Scout A cannot be {phase}")
            if num(row, "emergingLeaderScore") < 55 or num(row, "rsRank") < 70 or num(row, "rsAcceleration") <= 0:
                errors.append(f"{ticker}: Scout A quality gate violated")
        if expected == "B" and phase == "WATCH":
            if not bool(row.get("scoutQualityConfirmed")):
                errors.append(f"{ticker}: Scout B WATCH must be quality-confirmed")
        if expected == "C" and bool(row.get("scoutQualityConfirmed")) and phase in {"READY", "ENTRY"}:
            errors.append(f"{ticker}: strong READY/ENTRY incorrectly left Scout C")

    market = payload.get("market") or {}
    reported = market.get("scoutTierCounts") or {}
    actual_counts = {tier: sum(r.get("scoutTier") == tier for r in rows) for tier in ("A", "B", "C")}
    for tier, count in actual_counts.items():
        if int(reported.get(tier, -1)) != count:
            errors.append(f"Market Scout Tier {tier} count mismatch")

    print(f"Scout Tier audit: rows={len(rows)} counts={actual_counts} errors={len(errors)}")
    top = sorted(
        [r for r in rows if r.get("scoutTier") in {"A", "B"}],
        key=lambda r: (num(r, "scoutTierRank"), num(r, "emergingLeaderScore"), num(r, "rsRank"), num(r, "maClusterScore")),
        reverse=True,
    )[:20]
    for r in top:
        print(
            f"  {str(r.get('ticker') or '?'):5s} Scout {r.get('scoutTierLabel')} "
            f"timing={r.get('maClusterTierLabel')} Emerging={num(r,'emergingLeaderScore'):.1f} "
            f"RS={num(r,'rsRank'):.0f} accel={num(r,'rsAcceleration'):+.3f}"
        )
    for error in errors[:30]:
        print(f"  ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
