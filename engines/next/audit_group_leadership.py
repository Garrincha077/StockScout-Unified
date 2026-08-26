#!/usr/bin/env python3
"""Audit StockScout confidence-weighted behavioral group leadership v2."""
from __future__ import annotations

import json
import math
from pathlib import Path

DATA = Path("frontend/public/data/latest.json")
EXPECTED_MODEL = "behavioral-proxy-v2-confidence"


def finite(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def main() -> int:
    if not DATA.exists():
        raise SystemExit("Group leadership audit failed: canonical dataset missing")
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    market = payload.get("market") or {}
    groups = payload.get("groups") or {}
    errors: list[str] = []

    if market.get("groupModel") != EXPECTED_MODEL or groups.get("method") != EXPECTED_MODEL:
        errors.append(f"group model mismatch: market={market.get('groupModel')} groups={groups.get('method')}")
    if not rows:
        errors.append("empty universe")

    required = (
        "groupRank", "groupRS", "groupConfidence", "groupLeadership", "leadershipScore",
        "sectorProxyConfidence", "industryProxyConfidence",
        "sectorCorrelationStability", "industryCorrelationStability",
    )
    covered = 0
    low_conf = 0
    max_adjustment = 0.0
    for row in rows:
        ticker = row.get("ticker", "?")
        if all(k in row for k in required):
            covered += 1
        else:
            missing = [k for k in required if k not in row]
            errors.append(f"{ticker}: missing {','.join(missing)}")
            if len(errors) >= 20:
                break
            continue
        for field in ("groupRank", "groupRS", "groupConfidence", "leadershipScore", "sectorProxyConfidence", "industryProxyConfidence"):
            if not finite(row.get(field)):
                errors.append(f"{ticker}: non-finite {field}={row.get(field)!r}")
        group_rank = float(row["groupRank"])
        group_conf = float(row["groupConfidence"])
        if not 1 <= group_rank <= 99:
            errors.append(f"{ticker}: groupRank outside 1..99: {group_rank}")
        if not 0 <= group_conf <= 100:
            errors.append(f"{ticker}: groupConfidence outside 0..100: {group_conf}")
        if float(row["groupLeadership"]) != group_rank:
            errors.append(f"{ticker}: compatibility groupLeadership != groupRank")
        for prefix in ("sector", "industry"):
            conf = float(row[f"{prefix}ProxyConfidence"])
            stability = float(row[f"{prefix}CorrelationStability"])
            if not 0 <= conf <= 100:
                errors.append(f"{ticker}: {prefix} confidence outside 0..100")
            if not 0 <= stability <= 100:
                errors.append(f"{ticker}: {prefix} stability outside 0..100")
            if not row.get(f"{prefix}ProxyTicker") and conf != 0:
                errors.append(f"{ticker}: unclassified {prefix} has non-zero confidence {conf}")
        individual = row.get("opportunityScore", row.get("score", 0))
        if finite(individual):
            adjustment = abs(float(row["leadershipScore"]) - float(individual))
            max_adjustment = max(max_adjustment, adjustment)
            if adjustment > 5.6:
                errors.append(f"{ticker}: leadership adjustment too large: {adjustment:.2f}")
        if group_conf <= 15:
            low_conf += 1
            if abs(group_rank - 50) > 8:
                errors.append(f"{ticker}: low-confidence group rank not neutral enough: rank={group_rank} conf={group_conf}")
        if len(errors) >= 20:
            break

    coverage = covered / len(rows) * 100.0 if rows else 0.0
    if rows and coverage < 95:
        errors.append(f"group v2 field coverage too low: {coverage:.1f}%")
    print(f"Group leadership audit: model={market.get('groupModel')} coverage={coverage:.1f}% low_conf={low_conf:,} max_adjustment={max_adjustment:.2f}")
    if errors:
        print("AUDIT FAILED")
        for error in errors[:20]:
            print(f"- {error}")
        return 1
    print("AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
