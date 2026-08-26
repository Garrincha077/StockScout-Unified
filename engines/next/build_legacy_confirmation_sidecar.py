#!/usr/bin/env python3
"""Build a compact StockScout Next LEGACY-confirmation sidecar.

The sidecar is intentionally separate from the canonical screener payload. It
contains only ticker-level shadow status/reason metadata; drill-down evidence stays
in the existing frozen `originalEngine` capture inside latest.json.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from compute_legacy_confirmation import (
    LEGACY_CONFIRMATION_MODEL,
    LEGACY_CONFIRMATION_VERSION,
    project_captured_legacy_confirmation,
)

DEFAULT_INPUT = Path("frontend/public/data/latest.json")
DEFAULT_OUTPUT = Path("frontend/public/data/shadow/legacy-confirmation.json")


def build_sidecar(payload: Mapping[str, Any]) -> dict[str, Any]:
    market = payload.get("market") if isinstance(payload.get("market"), Mapping) else {}
    rows = payload.get("universe") or []
    by_ticker: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()

    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            raise AssertionError("Universe row missing ticker")
        if ticker in by_ticker:
            raise AssertionError(f"Duplicate ticker: {ticker}")
        projection = project_captured_legacy_confirmation(row, market=market)
        status = projection["status"]
        counts[status] += 1
        by_ticker[ticker] = {
            "status": status,
            "available": projection["available"],
            "reasons": projection["reasons"],
        }

    market_gate = (market.get("originalSignalGate") or {}).get("gate") or {}
    return {
        "model": LEGACY_CONFIRMATION_MODEL,
        "version": LEGACY_CONFIRMATION_VERSION,
        "affectsStockScout": False,
        "source": {
            "generatedAt": payload.get("generatedAt"),
            "originalEngineModel": payload.get("originalEngineModel")
            or market.get("originalEngineModel"),
            "legacyCaptureModel": payload.get("legacyCompleteSourceCaptureModel")
            or market.get("legacyCompleteSourceCaptureModel"),
            "marketGateRef": "market.originalSignalGate",
            "buyEnabled": market_gate.get("should_generate_buys"),
            "sellEnabled": market_gate.get("should_generate_sells"),
        },
        "total": len(by_ticker),
        "counts": dict(sorted(counts.items())),
        "byTicker": by_ticker,
    }


def write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def main() -> None:
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT
    if not input_path.exists():
        raise SystemExit(f"Missing canonical payload: {input_path}")
    canonical = json.loads(input_path.read_text(encoding="utf-8"))
    sidecar = build_sidecar(canonical)
    write_atomic(output_path, sidecar)
    print(
        f"LEGACY confirmation sidecar: {sidecar['total']:,} tickers; "
        f"counts={sidecar['counts']}; output={output_path}"
    )


if __name__ == "__main__":
    main()
