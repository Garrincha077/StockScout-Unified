#!/usr/bin/env python3
"""Compare today's enriched terminal dataset with the previous canonical snapshot.

This is intentionally cheap: no market data is fetched and no screening is repeated.
The resulting change fields are embedded in latest.json so the frontend can surface
"what changed today" without downloading a second dataset.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

DEFAULT_PREVIOUS = Path("data/batch_results/previous_frontend.json")
DEFAULT_CURRENT = Path("frontend/public/data/latest.json")

KEY_SETUPS = {
    "Neglected → Leader",
    "S1→S2 Transition",
    "Fresh Breakout",
    "Long Base Breakout",
    "RS Before Price",
    "Tight / VCP",
    "10W Pullback",
    "Fresh Stage 2",
    "Volume Wake-Up",
}


def number(value, default=0.0):
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def rounded_delta(current, previous, digits=2):
    return round(number(current) - number(previous), digits)


def tags(row):
    return {str(x) for x in (row.get("setupTags") or []) if x}


def main(previous_path: Path = DEFAULT_PREVIOUS, current_path: Path = DEFAULT_CURRENT):
    if not current_path.exists():
        raise SystemExit(f"Current dataset missing: {current_path}")

    current = json.loads(current_path.read_text(encoding="utf-8"))
    previous = {}
    if previous_path.exists():
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Previous snapshot unreadable ({exc}); treating this as first snapshot")

    prev_rows = {
        str(row.get("ticker", "")).upper(): row
        for row in (previous.get("universe") or [])
        if row.get("ticker")
    }
    opportunity_comparable = bool(
        previous.get("opportunityModel")
        and previous.get("opportunityModel") == current.get("opportunityModel")
    )

    changed_count = 0
    new_setup_count = 0
    stage_change_count = 0
    rs_mover_count = 0
    score_mover_count = 0

    for row in current.get("universe") or []:
        ticker = str(row.get("ticker", "")).upper()
        prev = prev_rows.get(ticker)
        if not prev:
            row.update({
                "changedToday": True,
                "newUniverseMember": True,
                "changeImpact": 10,
                "opportunityDelta": 0,
                "rsRankDelta": 0,
                "confluenceDelta": 0,
                "volumeRatioDelta": 0,
                "freshnessDelta": 0,
                "stageFrom": None,
                "stageTo": row.get("stage"),
                "stageChanged": False,
                "newSetupTags": list(tags(row)),
                "lostSetupTags": [],
                "changeLabels": ["New in universe"],
            })
            changed_count += 1
            continue

        opportunity_delta = (
            rounded_delta(
                row.get("opportunityScore", row.get("score")),
                prev.get("opportunityScore", prev.get("score")),
                1,
            )
            if opportunity_comparable
            else 0
        )
        rs_delta = rounded_delta(row.get("rsRank"), prev.get("rsRank"), 0)
        confluence_delta = rounded_delta(row.get("confluence"), prev.get("confluence"), 0)
        volume_delta = rounded_delta(row.get("volumeRatio"), prev.get("volumeRatio"), 2)
        freshness_delta = rounded_delta(row.get("freshnessScore"), prev.get("freshnessScore"), 1)
        stage_from = int(number(prev.get("stage"), 0)) or None
        stage_to = int(number(row.get("stage"), 0)) or None
        stage_changed = stage_from is not None and stage_to is not None and stage_from != stage_to

        old_tags = tags(prev)
        new_tags = tags(row)
        added = sorted(new_tags - old_tags)
        removed = sorted(old_tags - new_tags)
        positive_added = [x for x in added if x in KEY_SETUPS]

        labels: list[str] = []
        if stage_changed:
            labels.append(f"Stage {stage_from}→{stage_to}")
        for setup in positive_added[:3]:
            labels.append(f"+ {setup}")
        for setup in [x for x in removed if x in KEY_SETUPS][:2]:
            labels.append(f"− {setup}")
        if abs(rs_delta) >= 5:
            labels.append(f"RS {rs_delta:+.0f}")
        if abs(opportunity_delta) >= 5:
            labels.append(f"Score {opportunity_delta:+.0f}")
        if abs(volume_delta) >= 0.5:
            labels.append(f"Vol {volume_delta:+.1f}x")
        if abs(confluence_delta) >= 1:
            labels.append(f"Confluence {confluence_delta:+.0f}")

        newly_extended = bool(row.get("extended")) and not bool(prev.get("extended"))
        if newly_extended:
            labels.append("⚠ Became extended")

        impact = 0.0
        impact += opportunity_delta * 0.55
        impact += rs_delta * 0.35
        impact += confluence_delta * 4.0
        impact += max(-3.0, min(3.0, volume_delta)) * 3.0
        impact += freshness_delta * 0.15
        if stage_from == 1 and stage_to == 2:
            impact += 14
        elif stage_changed:
            impact += 3 if (stage_to or 9) < (stage_from or 9) else -3
        impact += len(positive_added) * 9
        if newly_extended:
            impact -= 8
        impact = round(max(-100, min(100, impact)), 1)

        significant = bool(
            stage_changed
            or positive_added
            or abs(opportunity_delta) >= 4
            or abs(rs_delta) >= 5
            or abs(confluence_delta) >= 1
            or abs(volume_delta) >= 0.5
            or newly_extended
        )

        row.update({
            "changedToday": significant,
            "newUniverseMember": False,
            "changeImpact": impact,
            "opportunityDelta": opportunity_delta,
            "rsRankDelta": int(rs_delta),
            "confluenceDelta": int(confluence_delta),
            "volumeRatioDelta": volume_delta,
            "freshnessDelta": freshness_delta,
            "stageFrom": stage_from,
            "stageTo": stage_to,
            "stageChanged": stage_changed,
            "newSetupTags": added,
            "lostSetupTags": removed,
            "changeLabels": labels,
        })

        if significant:
            changed_count += 1
        if positive_added:
            new_setup_count += 1
        if stage_changed:
            stage_change_count += 1
        if abs(rs_delta) >= 5:
            rs_mover_count += 1
        if abs(opportunity_delta) >= 5:
            score_mover_count += 1

    market = current.setdefault("market", {})
    market["dailyChanges"] = {
        "changed": changed_count,
        "newSetups": new_setup_count,
        "stageChanges": stage_change_count,
        "rsMovers": rs_mover_count,
        "scoreMovers": score_mover_count,
        "previousGeneratedAt": previous.get("generatedAt"),
        "opportunityComparable": opportunity_comparable,
    }
    current["version"] = max(5, int(current.get("version", 1) or 1))
    current["featureModel"] = "data-first-v2-daily-diff"
    current_path.write_text(
        json.dumps(current, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Daily diff: {changed_count:,} changed, {new_setup_count:,} new setups, "
        f"{stage_change_count:,} stage changes, {rs_mover_count:,} RS movers; "
        f"Opportunity comparable={opportunity_comparable}"
    )


if __name__ == "__main__":
    prev = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PREVIOUS
    cur = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_CURRENT
    main(prev, cur)
