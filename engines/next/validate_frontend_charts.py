#!/usr/bin/env python3
"""Validate terminal chart-shard integrity.

By default this remains a diagnostic command that warns on partial coverage.
Publication workflows can opt into fail-closed behavior with ``--strict`` and a
minimum coverage threshold, while keeping the same deterministic shard audit.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

SHARD_COUNT = 128
DEFAULT_MINIMUM_COVERAGE = 0.95


def shard_for(ticker: str) -> str:
    ticker = ticker.strip().upper()
    value = sum((idx + 1) * ord(ch) for idx, ch in enumerate(ticker)) % SHARD_COUNT
    return f"{value:03d}.json"


def warn(message: str) -> None:
    print(f"::warning::{message}")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warn(f"Invalid chart JSON {path}: {exc}")
        return None


def validate(root: Path, *, strict: bool = False, minimum_coverage: float = DEFAULT_MINIMUM_COVERAGE) -> float:
    data_dir = root / "data"
    payload_path = data_dir / "core.json"
    if not payload_path.exists():
        payload_path = data_dir / "latest.json"
    if not payload_path.exists():
        message = f"No frontend payload found under {data_dir}"
        if strict:
            raise SystemExit(message)
        warn(message + "; skipping chart diagnostics")
        return 0.0

    payload = read_json(payload_path)
    if not isinstance(payload, dict):
        if strict:
            raise SystemExit(f"Invalid frontend payload: {payload_path}")
        return 0.0

    universe = payload.get("universe") or []
    tickers = [str(row.get("ticker", "")).strip().upper() for row in universe if isinstance(row, dict) and row.get("ticker")]
    if not tickers:
        message = f"No tickers in {payload_path}"
        if strict:
            raise SystemExit(message)
        warn(message + "; skipping chart diagnostics")
        return 0.0

    mapping = {
        str(k).strip().upper(): str(v)
        for k, v in (payload.get("chartShards") or {}).items()
    }

    by_shard: dict[str, list[str]] = defaultdict(list)
    mapping_mismatches: list[str] = []
    for ticker in tickers:
        expected = shard_for(ticker)
        shard = mapping.get(ticker) or expected
        if shard != expected:
            mapping_mismatches.append(f"{ticker}:{shard}!={expected}")
            shard = expected
        by_shard[shard].append(ticker)

    charts_dir = data_dir / "charts"
    if not charts_dir.is_dir():
        coverage = 0.0
        message = f"Chart directory missing: {charts_dir}. Chart coverage: 0/{len(tickers)} (0.0%)."
        if strict and coverage < minimum_coverage:
            raise SystemExit(message)
        warn(message)
        return coverage

    missing_files: list[str] = []
    missing_tickers: list[str] = []
    empty_series: list[str] = []
    invalid_shards: list[str] = []
    verified = 0

    for shard, names in sorted(by_shard.items()):
        path = charts_dir / shard
        if not path.exists():
            missing_files.append(shard)
            continue
        shard_payload = read_json(path)
        if not isinstance(shard_payload, dict):
            invalid_shards.append(shard)
            continue
        for ticker in names:
            if ticker not in shard_payload:
                missing_tickers.append(f"{ticker}@{shard}")
                continue
            bars = shard_payload[ticker]
            if not isinstance(bars, list) or not bars:
                empty_series.append(f"{ticker}@{shard}")
                continue
            verified += 1

    coverage = verified / len(tickers)
    details = []
    if mapping_mismatches:
        details.append(f"mapping mismatches={len(mapping_mismatches)} ({', '.join(mapping_mismatches[:5])})")
    if missing_files:
        details.append(f"missing shard files={len(missing_files)} ({', '.join(missing_files[:8])})")
    if invalid_shards:
        details.append(f"invalid shards={len(invalid_shards)} ({', '.join(invalid_shards[:8])})")
    if missing_tickers:
        details.append(f"missing tickers={len(missing_tickers)} ({', '.join(missing_tickers[:8])})")
    if empty_series:
        details.append(f"empty series={len(empty_series)} ({', '.join(empty_series[:8])})")

    print(
        f"Chart coverage: {verified:,}/{len(tickers):,} tickers "
        f"({coverage:.1%}) across {len(by_shard)} deterministic shards under {root}"
    )
    if details:
        warn("Partial chart coverage. " + "; ".join(details))

    if strict and (coverage < minimum_coverage or mapping_mismatches or invalid_shards):
        raise SystemExit(
            f"Chart publication gate failed: coverage {coverage:.2%}, minimum {minimum_coverage:.2%}, "
            f"mapping_mismatches={len(mapping_mismatches)}, invalid_shards={len(invalid_shards)}"
        )
    return coverage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="frontend/public")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--minimum-coverage", type=float, default=DEFAULT_MINIMUM_COVERAGE)
    args = parser.parse_args()
    if not 0 <= args.minimum_coverage <= 1:
        raise SystemExit("--minimum-coverage must be between 0 and 1")
    validate(Path(args.root), strict=args.strict, minimum_coverage=args.minimum_coverage)


if __name__ == "__main__":
    main()
