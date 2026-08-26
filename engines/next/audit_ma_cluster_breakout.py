#!/usr/bin/env python3
"""Audit weekly 10W/30W MA-cluster timing and tier invariants."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

DATA = Path("frontend/public/data/latest.json")
MODEL = "weekly-ma-cluster-breakout-v2-tiers"


def finite(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def main():
    if not DATA.exists():
        print(f"Missing {DATA}")
        return 1

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    model = payload.get("maClusterModel") or (payload.get("market") or {}).get("maClusterModel")
    if model != MODEL:
        print(f"MA Cluster model mismatch: {model}")
        return 1
    if not rows:
        print("No universe rows")
        return 1

    errors = []
    phases = {"NONE", "WATCH", "READY", "ENTRY"}
    tiers = {None, "A", "B", "C"}
    expected_rank = {None: 0, "C": 1, "B": 2, "A": 3}

    for row in rows:
        ticker = str(row.get("ticker") or "?")
        phase = row.get("maClusterPhase")
        tier = row.get("maClusterTier")
        rank = row.get("maClusterTierRank")
        score = finite(row.get("maClusterScore"))

        if phase not in phases:
            errors.append(f"{ticker}: invalid phase {phase}")
            continue
        if tier not in tiers:
            errors.append(f"{ticker}: invalid tier {tier}")
            continue
        if rank != expected_rank[tier]:
            errors.append(f"{ticker}: tier rank mismatch {tier}/{rank}")
        if score is None or not 0 <= score <= 100:
            errors.append(f"{ticker}: invalid cluster score {score}")

        entry = bool(row.get("maClusterEntrySignal"))
        ready = bool(row.get("maClusterReady"))
        watch = bool(row.get("maClusterWatch"))
        spread = finite(row.get("maClusterSpreadPct"))
        price_pct = finite(row.get("maClusterPricePct"))
        volume = finite(row.get("maClusterVolumePace"))
        turn_count = finite(row.get("maClusterTurnCount"))
        slope10 = finite(row.get("ma10wSlope4w"))
        slope30 = finite(row.get("ma30wSlope4w"))

        if phase == "NONE":
            if tier is not None or rank != 0 or entry:
                errors.append(f"{ticker}: NONE phase carries active tier/entry")
        elif phase == "WATCH":
            if tier != "C" or not watch:
                errors.append(f"{ticker}: WATCH must be Tier C and watch=true")
        elif phase == "READY":
            if tier not in {"A", "B"} or not ready:
                errors.append(f"{ticker}: READY must be Tier A/B and ready=true")
        elif phase == "ENTRY":
            if tier not in {"A", "B"} or not entry:
                errors.append(f"{ticker}: ENTRY must be Tier A/B and entry=true")
            if spread is None or spread > 3.5001:
                errors.append(f"{ticker}: ENTRY spread too wide {spread}")
            if price_pct is None or not -1.0001 <= price_pct <= 8.0001:
                errors.append(f"{ticker}: ENTRY chase/proximity invalid {price_pct}")
            if volume is None or volume < 1.3999:
                errors.append(f"{ticker}: ENTRY lacks volume confirmation {volume}")
            if turn_count is None or turn_count < 1:
                errors.append(f"{ticker}: ENTRY lacks MA turn")

        if tier == "A" and phase == "ENTRY":
            if spread is None or spread > 2.0001:
                errors.append(f"{ticker}: A ENTRY not very tight ({spread})")
            if volume is None or volume < 1.7999:
                errors.append(f"{ticker}: A ENTRY volume below 1.8x ({volume})")
            if turn_count != 2 or slope10 is None or slope10 < 0 or slope30 is None or slope30 < 0:
                errors.append(f"{ticker}: A ENTRY requires both MAs rising")
            if price_pct is None or not 0 <= price_pct <= 5.0001:
                errors.append(f"{ticker}: A ENTRY price outside ideal launch zone ({price_pct})")

        if tier == "C" and entry:
            errors.append(f"{ticker}: Tier C cannot be ENTRY")

    market = payload.get("market") or {}
    phase_counts = {
        "WATCH": sum(r.get("maClusterPhase") == "WATCH" for r in rows),
        "READY": sum(r.get("maClusterPhase") == "READY" for r in rows),
        "ENTRY": sum(r.get("maClusterPhase") == "ENTRY" for r in rows),
    }
    if int(market.get("maClusterWatchCount", -1)) != phase_counts["WATCH"]:
        errors.append("Market WATCH count mismatch")
    if int(market.get("maClusterReadyCount", -1)) != phase_counts["READY"]:
        errors.append("Market READY count mismatch")
    if int(market.get("maClusterEntryCount", -1)) != phase_counts["ENTRY"]:
        errors.append("Market ENTRY count mismatch")

    tier_counts = market.get("maClusterTierCounts") or {}
    for tier in ("A", "B", "C"):
        actual = sum(r.get("maClusterTier") == tier for r in rows)
        if int(tier_counts.get(tier, -1)) != actual:
            errors.append(f"Market Tier {tier} count mismatch")

    scored = [r for r in rows if finite(r.get("maClusterSpreadPct")) is not None]
    coverage = len(scored) / max(1, len(rows)) * 100.0
    reported_coverage = int(market.get("maClusterCoverage", 0) or 0)
    if reported_coverage != len(scored):
        errors.append(f"MA Cluster coverage count mismatch ({reported_coverage} vs {len(scored)})")
    if os.getenv("MA_CLUSTER_REQUIRE_COVERAGE", "0") == "1" and coverage < 95.0:
        errors.append(f"MA Cluster full-data coverage too low: {coverage:.1f}%")

    print(
        f"MA Cluster audit: rows={len(rows)} coverage={len(scored)}/{len(rows)} ({coverage:.1f}%) "
        f"WATCH={phase_counts['WATCH']} READY={phase_counts['READY']} ENTRY={phase_counts['ENTRY']} "
        f"tiers={tier_counts} errors={len(errors)}"
    )

    # Calibration diagnostics: show the closest real-world near misses even when
    # no stock currently passes the hard phase gates.
    by_score = sorted(
        scored,
        key=lambda r: (
            finite(r.get("maClusterScore")) or 0.0,
            -(finite(r.get("maClusterSpreadPct")) or 999.0),
        ),
        reverse=True,
    )[:15]
    print("MA Cluster top timing scores:")
    for r in by_score:
        print(
            f"  {str(r.get('ticker') or '?'):5s} score={finite(r.get('maClusterScore')) or 0:4.1f} "
            f"spread={finite(r.get('maClusterSpreadPct')) or 0:4.1f}% "
            f"p={finite(r.get('maClusterPricePct')) or 0:+5.1f}% "
            f"s10={finite(r.get('ma10wSlope4w')) or 0:+5.1f}% "
            f"s30={finite(r.get('ma30wSlope4w')) or 0:+5.1f}% "
            f"vol={finite(r.get('maClusterVolumePace')) or 0:4.2f}x "
            f"phase={r.get('maClusterPhase')} tier={r.get('maClusterTier')}"
        )

    nearest = sorted(
        scored,
        key=lambda r: (
            finite(r.get("maClusterSpreadPct")) or 999.0,
            abs(finite(r.get("maClusterPricePct")) or 999.0),
        ),
    )[:15]
    print("MA Cluster narrowest 10W/30W spreads:")
    for r in nearest:
        print(
            f"  {str(r.get('ticker') or '?'):5s} spread={finite(r.get('maClusterSpreadPct')) or 0:4.1f}% "
            f"p={finite(r.get('maClusterPricePct')) or 0:+5.1f}% "
            f"s10={finite(r.get('ma10wSlope4w')) or 0:+5.1f}% "
            f"s30={finite(r.get('ma30wSlope4w')) or 0:+5.1f}% "
            f"vol={finite(r.get('maClusterVolumePace')) or 0:4.2f}x"
        )

    for error in errors[:30]:
        print(f"  ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
