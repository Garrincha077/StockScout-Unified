#!/usr/bin/env python3
"""Verify downloaded Next chart shards against its immutable publication manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _encoded(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def verify(canonical_path: Path, manifest_path: Path, chart_dir: Path) -> dict[str, Any]:
    canonical_bytes = canonical_path.read_bytes()
    canonical = json.loads(canonical_bytes)
    manifest = json.loads(manifest_path.read_bytes())
    session = str((canonical.get("market") or {}).get("scanDate") or "")
    if (manifest.get("marketSession") or {}).get("date") != session:
        raise ValueError("Next chart manifest session does not match canonical scan")
    source = (manifest.get("provenance") or {}).get("source") or {}
    if source.get("sha256") != _sha256(canonical_bytes):
        raise ValueError("Next canonical hash does not match chart manifest")

    chart_contract = (manifest.get("assets") or {}).get("charts") or {}
    expected_count = int(chart_contract.get("shardCount") or 0)
    chart_files = sorted(chart_dir.glob("*.json"))
    if len(chart_files) != expected_count:
        raise ValueError(
            f"Next chart shard count mismatch: {len(chart_files)} != {expected_count}"
        )
    file_bytes = [(f"charts/{path.name}", path.read_bytes()) for path in chart_files]
    aggregate_input = _encoded(
        [{"path": name, "sha256": _sha256(data)} for name, data in file_bytes]
    )
    aggregate_sha = _sha256(aggregate_input)
    aggregate_bytes = sum(len(data) for _, data in file_bytes)
    if aggregate_sha != chart_contract.get("sha256"):
        raise ValueError("Next chart aggregate hash mismatch")
    if aggregate_bytes != int(chart_contract.get("bytes") or -1):
        raise ValueError("Next chart aggregate byte count mismatch")

    mapping = {
        str(ticker).strip().upper(): str(shard)
        for ticker, shard in (canonical.get("chartShards") or {}).items()
    }
    universe = {
        str(row.get("ticker") or "").strip().upper()
        for row in canonical.get("universe") or []
        if isinstance(row, dict)
    }
    if set(mapping) != universe:
        raise ValueError("Next chart mapping does not cover the canonical universe")
    available: set[str] = set()
    known_files = {path.name for path in chart_files}
    for path in chart_files:
        shard = json.loads(path.read_bytes())
        if not isinstance(shard, dict):
            raise ValueError(f"Next chart shard is not an object: {path.name}")
        for ticker, bars in shard.items():
            ticker_text = str(ticker).strip().upper()
            if mapping.get(ticker_text) != path.name:
                raise ValueError(f"Next chart ticker is in the wrong shard: {ticker_text}")
            if not isinstance(bars, list) or not bars:
                raise ValueError(f"Next chart ticker has no bars: {ticker_text}")
            available.add(ticker_text)
    if any(shard not in known_files for shard in mapping.values()) or available != universe:
        raise ValueError("Next chart payload coverage is incomplete")

    return {
        "sessionDate": session,
        "shards": len(chart_files),
        "tickers": len(available),
        "bytes": aggregate_bytes,
        "sha256": aggregate_sha,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--chart-dir", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.canonical, args.manifest, args.chart_dir)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
