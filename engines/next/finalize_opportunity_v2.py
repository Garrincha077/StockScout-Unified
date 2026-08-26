#!/usr/bin/env python3
"""Finalize derived fields after Opportunity v2 becomes the canonical score.

Group leadership is computed earlier because Opportunity v2 needs groupRank and
confidence. Once Opportunity v2 replaces the old compatibility opportunityScore,
row/group presentation fields that depended on the previous score must be
refreshed. Opportunity v2 already contains a bounded group modifier, so the old
leadershipScore must not add group influence a second time.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median
from typing import Any

DATA = Path("frontend/public/data/latest.json")
MODEL = "stockscout-opportunity-v2-potential-timing"


def finite(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def refresh_group_summaries(rows: list[dict], groups: list[dict], proxy_field: str) -> None:
    for group in groups:
        proxy = str(group.get("ticker") or "")
        members = [row for row in rows if str(row.get(proxy_field) or "") == proxy]
        scored = [row for row in members if finite(row.get("opportunityScore")) is not None]
        scores = [float(row["opportunityScore"]) for row in scored]
        ranked = sorted(
            scored,
            key=lambda row: (
                finite(row.get("opportunityScore")) or 0.0,
                finite(row.get("opportunityTiming")) or 0.0,
                finite(row.get("rsRank")) or 0.0,
            ),
            reverse=True,
        )
        group["medianOpportunity"] = round(median(scores), 1) if scores else 0.0
        group["topTickers"] = [str(row.get("ticker")) for row in ranked[:8] if row.get("ticker")]
        group["opportunityModel"] = MODEL


def finalize(payload: dict) -> dict:
    if payload.get("opportunityModel") != MODEL:
        return payload

    rows = payload.get("universe") or []
    for row in rows:
        opportunity = finite(row.get("opportunityScore"))
        if opportunity is None:
            continue
        # Compatibility field only. Opportunity v2 already includes the bounded
        # group modifier, so adding another group adjustment here would double-count.
        row["leadershipScore"] = round(opportunity, 1)

    groups = payload.get("groups")
    if isinstance(groups, dict):
        sectors = groups.get("sectors")
        industries = groups.get("industries")
        if isinstance(sectors, list):
            refresh_group_summaries(rows, sectors, "sectorProxyTicker")
        if isinstance(industries, list):
            refresh_group_summaries(rows, industries, "industryProxyTicker")
        groups["leadershipScoreMode"] = "opportunity-v2-alias"
        groups["opportunityIntegration"] = (
            "Opportunity v2 already includes the bounded group modifier; "
            "leadershipScore is retained only as a compatibility alias."
        )

    market = payload.setdefault("market", {})
    market["groupLeadershipScoreMode"] = "opportunity-v2-alias"
    return payload


def main(path: Path = DATA) -> None:
    if not path.exists():
        raise SystemExit(f"Missing canonical dataset: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    finalize(payload)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    temp.replace(path)
    print("Opportunity v2 finalization: leadership compatibility + group summaries refreshed")


if __name__ == "__main__":
    main()
