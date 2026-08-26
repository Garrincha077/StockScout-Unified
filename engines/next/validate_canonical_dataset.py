#!/usr/bin/env python3
"""Hard invariants for the publishable StockScout canonical dataset."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

DATA = Path("frontend/public/data/latest.json")
CHART_DIR = Path("frontend/public/data/charts")
MIN_LAYER_COVERAGE = 0.90
MIN_CHART_COVERAGE = 0.95
MAX_PRICE_CHART_DIFF_PCT = 0.75


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def find_nonfinite(value: Any, path: str = "$", found: list[str] | None = None) -> list[str]:
    found = found if found is not None else []
    if isinstance(value, float) and not math.isfinite(value):
        found.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            find_nonfinite(item, f"{path}.{key}", found)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            find_nonfinite(item, f"{path}[{idx}]", found)
    return found


def pct_diff(a: float, b: float) -> float:
    if not b:
        return 0.0 if not a else 100.0
    return abs(a / b - 1.0) * 100.0


def load_chart_last_closes(mapping: dict[str, str]) -> dict[str, float]:
    by_shard: dict[str, list[str]] = {}
    for ticker, shard in mapping.items():
        by_shard.setdefault(str(shard), []).append(str(ticker).upper())
    closes: dict[str, float] = {}
    for shard, tickers in by_shard.items():
        path = CHART_DIR / shard
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ticker in tickers:
            bars = payload.get(ticker) or []
            if bars and len(bars[-1]) >= 5 and finite_number(bars[-1][4]):
                closes[ticker] = float(bars[-1][4])
    return closes


def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"Missing canonical dataset: {DATA}")
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    failures: list[str] = []
    warnings: list[str] = []

    if len(rows) < 100:
        failures.append(f"implausibly small universe: {len(rows)}")

    tickers = [str(row.get("ticker", "")).upper() for row in rows if row.get("ticker")]
    if len(tickers) != len(rows):
        failures.append(f"rows without ticker: {len(rows) - len(tickers)}")
    duplicate_count = len(tickers) - len(set(tickers))
    if duplicate_count:
        failures.append(f"duplicate tickers: {duplicate_count}")

    nonfinite = find_nonfinite(payload)
    if nonfinite:
        failures.append(f"non-finite numeric values: {len(nonfinite)}; first={nonfinite[:5]}")

    invalid_stage = [r.get("ticker") for r in rows if int(r.get("stage", 0) or 0) not in (1, 2, 3, 4)]
    if invalid_stage:
        failures.append(f"invalid stage rows: {len(invalid_stage)}; first={invalid_stage[:10]}")
    invalid_price = [r.get("ticker") for r in rows if not finite_number(r.get("price")) or float(r.get("price", 0)) <= 0]
    if invalid_price:
        failures.append(f"invalid price rows: {len(invalid_price)}; first={invalid_price[:10]}")

    # The VCP value is shared evidence from the same source detector. This catches
    # the historical bug where StockScout looked for `quality` instead of
    # upstream `vcp_quality` and silently turned non-zero VCP scores into zero.
    vcp_pairs = 0
    vcp_mismatches: list[str] = []
    for row in rows:
        legacy_vcp = row.get("originalVcpQuality")
        scout_vcp = row.get("vcpScore")
        if finite_number(legacy_vcp) and finite_number(scout_vcp):
            vcp_pairs += 1
            if abs(float(legacy_vcp) - float(scout_vcp)) > 0.11:
                vcp_mismatches.append(
                    f"{row.get('ticker')} legacy={float(legacy_vcp):.1f} stockscout={float(scout_vcp):.1f}"
                )
    if vcp_pairs and vcp_mismatches:
        failures.append(f"VCP mapping mismatch: {len(vcp_mismatches)}/{vcp_pairs}; first={vcp_mismatches[:10]}")

    layers = payload.get("layers") or {}
    if layers:
        coverage_specs = {
            "LEGACY": sum(bool(r.get("originalEngine")) for r in rows),
            "STOCKSCOUT": sum(bool(r.get("stockscout")) for r in rows),
            "rich technical": sum(bool((r.get("richData") or {}).get("technical")) for r in rows),
        }
        for label, count in coverage_specs.items():
            coverage = count / max(1, len(rows))
            if coverage < MIN_LAYER_COVERAGE:
                failures.append(f"{label} coverage too low: {count}/{len(rows)} ({coverage:.1%})")

    mapping = payload.get("chartShards") or {}
    mapped = sum(1 for ticker in tickers if ticker in mapping)
    mapped_coverage = mapped / max(1, len(rows))
    if mapped_coverage < MIN_CHART_COVERAGE:
        failures.append(f"chart mapping coverage too low: {mapped}/{len(rows)} ({mapped_coverage:.1%})")

    if CHART_DIR.exists() and mapping:
        closes = load_chart_last_closes(mapping)
        chart_coverage = len(closes) / max(1, len(rows))
        if chart_coverage < MIN_CHART_COVERAGE:
            failures.append(f"chart file coverage too low: {len(closes)}/{len(rows)} ({chart_coverage:.1%})")
        price_mismatch: list[str] = []
        by_ticker = {str(r.get("ticker", "")).upper(): r for r in rows}
        for ticker, close in closes.items():
            row = by_ticker.get(ticker)
            if not row or not finite_number(row.get("price")):
                continue
            diff = pct_diff(float(row["price"]), close)
            if diff > MAX_PRICE_CHART_DIFF_PCT:
                price_mismatch.append(f"{ticker} row={row['price']} chart={close} diff={diff:.2f}%")
        if price_mismatch:
            failures.append(f"row/chart price mismatch: {len(price_mismatch)}; first={price_mismatch[:10]}")

    fundamental_ages = [
        r.get("fundamentalsAgeDays") for r in rows if finite_number(r.get("fundamentalsAgeDays"))
    ]
    if fundamental_ages:
        stale = sum(float(x) > 14 for x in fundamental_ages)
        if stale:
            warnings.append(f"fundamentals older than 14d: {stale}/{len(fundamental_ages)}")

    print(
        f"Canonical invariant audit: rows={len(rows):,}; VCP pairs={vcp_pairs:,}; "
        f"mapped charts={mapped:,}; layers={list(layers.keys()) if layers else 'legacy snapshot'}"
    )
    for warning in warnings:
        print(f"WARNING: {warning}")
    if failures:
        print("CANONICAL DATASET INVALID")
        for failure in failures:
            print(f" - {failure}")
        raise SystemExit(1)
    print("Canonical dataset invariants OK")


if __name__ == "__main__":
    main()
