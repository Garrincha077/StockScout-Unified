#!/usr/bin/env python3
"""Apply observational Lateral Base v1 diagnostics to StockScout latest.json."""
from __future__ import annotations

import json
from pathlib import Path

from lateral_base import MODEL, score_row

DATA = Path("frontend/public/data/latest.json")


def main():
    if not DATA.exists():
        print("Lateral Base skipped: latest.json missing")
        return
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    for row in rows:
        row.update(score_row(row))

    market = payload.setdefault("market", {})
    candidates = [r for r in rows if r.get("lateralBaseCandidate")]
    market["lateralBaseCandidates"] = len(candidates)
    market["lateralBaseAvgScore"] = round(sum(float(r.get("lateralBaseScore", 0) or 0) for r in rows) / max(1, len(rows)), 1)
    market["lateralBaseTop"] = [
        r.get("ticker") for r in sorted(
            candidates,
            key=lambda r: (
                float(r.get("neglectedLaunchScore", 0) or 0),
                float(r.get("launchReadiness", 0) or 0),
                float(r.get("lateralBaseScore", 0) or 0),
            ),
            reverse=True,
        )[:10]
    ]
    payload["lateralBaseModel"] = MODEL
    payload["version"] = max(7, int(payload.get("version", 1) or 1))
    DATA.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(
        f"Lateral Base v1: rows={len(rows):,}, candidates={len(candidates):,}, "
        f"avg={market['lateralBaseAvgScore']}"
    )


if __name__ == "__main__":
    main()
