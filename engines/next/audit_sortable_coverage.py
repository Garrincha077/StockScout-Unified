#!/usr/bin/env python3
"""Audit StockScout field coverage before publishing a nightly dataset.

The audit distinguishes required core technical fields from advisory/filter fields.
Missing values remain missing: this script never coerces null/NaN to zero.
It writes a compact report into ``market.dataQuality`` and to
``data/daily_scans/latest_data_quality.json``.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "frontend" / "public" / "data" / "latest.json"
FILTER_ENGINE = ROOT / "frontend" / "src" / "deepvue" / "filterEngine.ts"
REPORT = ROOT / "data" / "daily_scans" / "latest_data_quality.json"
MODEL = "sortable-coverage-v1"

# Global coverage gate: fields that should exist for essentially every analyzed ticker.
CORE_CRITICAL_FIELDS = (
    "price", "stage", "opportunityScore", "rsRank", "rsAcceleration",
    "volumeRatio", "distance10w", "distance30w", "trendTemplatePasses",
    "atrCompression", "tightRange20", "return3m",
)

# MA values are eligibility-aware. A recent IPO may legitimately lack 20 weeks of
# history, so these are reported row-by-row but gated by market.maCrossCoverage,
# which uses only tickers with enough history for that indicator family.
MA_FIELDS = (
    "ema10d", "ema20d", "ema10d20dSpreadPct", "ema10d20dState",
    "sma10w", "sma20w", "sma10w20wSpreadPct", "sma10w20wState",
)
CRITICAL_FIELDS = CORE_CRITICAL_FIELDS  # compatibility for tests/importers

MIN_CRITICAL_FIELD_COVERAGE_PCT = 98.0
MIN_ALL_CRITICAL_COMPLETE_PCT = 95.0
MIN_MA_ELIGIBLE_COVERAGE_PCT = 99.0
FIELD_RE = re.compile(r"\{id:'([^']+)',label:'([^']+)',kind:'(number|text|boolean)'")


def valid(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        return bool(value.strip())
    return True


def frontend_fields(path: Path = FILTER_ENGINE) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    return [{"id": f, "label": l, "kind": k} for f, l, k in FIELD_RE.findall(text)]


def field_coverage(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    present = sum(valid(row.get(field)) for row in rows)
    total = len(rows)
    return {
        "present": present,
        "missing": total - present,
        "coveragePct": round(100.0 * present / total, 2) if total else 0.0,
        "sampleMissing": [str(r.get("ticker") or "") for r in rows if not valid(r.get(field))][:12],
    }


def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [r for r in (payload.get("universe") or []) if isinstance(r, dict)]
    filterable_ids = [f["id"] for f in frontend_fields()]
    coverage = {field: field_coverage(rows, field) for field in filterable_ids}
    critical = {field: field_coverage(rows, field) for field in CORE_CRITICAL_FIELDS}
    ma_fields = {field: field_coverage(rows, field) for field in MA_FIELDS}
    complete_rows = sum(all(valid(row.get(field)) for field in CORE_CRITICAL_FIELDS) for row in rows)
    complete_pct = round(100.0 * complete_rows / len(rows), 2) if rows else 0.0

    ma_cov = (payload.get("market") or {}).get("maCrossCoverage") or {}
    ma_daily = float(((ma_cov.get("daily") or {}).get("coveragePct") or 0.0))
    ma_weekly = float(((ma_cov.get("weekly") or {}).get("coveragePct") or 0.0))

    failures: list[str] = []
    for field, stats in critical.items():
        if stats["coveragePct"] < MIN_CRITICAL_FIELD_COVERAGE_PCT:
            failures.append(f"critical field {field} coverage {stats['coveragePct']:.2f}% < {MIN_CRITICAL_FIELD_COVERAGE_PCT:.2f}%")
    if complete_pct < MIN_ALL_CRITICAL_COMPLETE_PCT:
        failures.append(f"all-critical-complete {complete_pct:.2f}% < {MIN_ALL_CRITICAL_COMPLETE_PCT:.2f}%")
    if ma_daily < MIN_MA_ELIGIBLE_COVERAGE_PCT:
        failures.append(f"daily MA eligible coverage {ma_daily:.2f}% < {MIN_MA_ELIGIBLE_COVERAGE_PCT:.2f}%")
    if ma_weekly < MIN_MA_ELIGIBLE_COVERAGE_PCT:
        failures.append(f"weekly MA eligible coverage {ma_weekly:.2f}% < {MIN_MA_ELIGIBLE_COVERAGE_PCT:.2f}%")

    lowest = sorted(({"field": field, **stats} for field, stats in coverage.items()), key=lambda x: (x["coveragePct"], x["field"]))[:20]
    return {
        "model": MODEL,
        "rows": len(rows),
        "filterableFieldCount": len(filterable_ids),
        "criticalFieldCount": len(CORE_CRITICAL_FIELDS),
        "maFieldCount": len(MA_FIELDS),
        "allCriticalComplete": complete_rows,
        "allCriticalCompletePct": complete_pct,
        "thresholds": {
            "criticalFieldCoveragePct": MIN_CRITICAL_FIELD_COVERAGE_PCT,
            "allCriticalCompletePct": MIN_ALL_CRITICAL_COMPLETE_PCT,
            "maEligibleCoveragePct": MIN_MA_ELIGIBLE_COVERAGE_PCT,
        },
        "critical": critical,
        "maFields": ma_fields,
        "lowestFilterableCoverage": lowest,
        "maCrossCoverage": ma_cov,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
    }


def audit(dataset_path: Path = DATASET, report_path: Path = REPORT, fail_on_gate: bool = True) -> dict[str, Any]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    report = build_report(payload)
    payload.setdefault("market", {})["dataQuality"] = report
    tmp = dataset_path.with_suffix(dataset_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(dataset_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Data quality {report['status']}: rows={report['rows']} core-complete={report['allCriticalCompletePct']:.2f}% filterable-fields={report['filterableFieldCount']}")
    ma_cov = report.get("maCrossCoverage") or {}
    print("  MA eligible coverage:", "daily", (ma_cov.get("daily") or {}).get("coveragePct"), "weekly", (ma_cov.get("weekly") or {}).get("coveragePct"))
    for item in report["lowestFilterableCoverage"][:10]:
        print(f"  {item['field']}: {item['coveragePct']:.2f}% ({item['missing']} missing)")
    for failure in report["failures"]:
        print(f"  FAIL: {failure}")
    if fail_on_gate and report["failures"]:
        raise SystemExit(2)
    return report


if __name__ == "__main__":
    audit()
