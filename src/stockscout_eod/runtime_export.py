"""Allowlisted cross-job scan export and non-sensitive run diagnostics."""
from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from stockscout_eod.contracts import RawScanEnvelopeV1
from stockscout_eod.jsonio import atomic_write_json

RUNTIME_SCAN_EXPORT_KEYS = (
    "schemaVersion",
    "runId",
    "sessionDate",
    "generatedAt",
    "priceMode",
    "candidates",
    "excluded",
    "stats",
    "market",
    "provenance",
    "versions",
)

HEALTH_CHECK_CODES = (
    "production_provenance",
    "provider_provenance",
    "session_date_alignment",
    "market_data_freshness",
    "engine_data_status",
    "coverage",
    "universe",
    "rows_present",
    "unique_tickers",
    "failed_ticker_budget",
    "public_payload_safety",
)
_HEALTH_CHECK_CODE_SET = frozenset(HEALTH_CHECK_CODES)
_DIAGNOSTIC_COUNTER_KEYS = (
    "candidates",
    "excluded",
    "publishedRows",
    "universe",
    "failedTickers",
    "failedChecks",
    "coveragePct",
)


def build_runtime_scan_export(scan: RawScanEnvelopeV1) -> dict[str, Any]:
    """Return the exact safe scan envelope consumed after the build job.

    ``stageRows`` is intentionally absent. It is an internal engine by-product,
    is not consumed by cloud publication or Telegram, and must never cross the
    GitHub job boundary.
    """

    payload: dict[str, Any] = {
        "schemaVersion": scan.schema_version,
        "runId": scan.run_id,
        "sessionDate": scan.session_date,
        "generatedAt": scan.generated_at,
        "priceMode": scan.price_mode,
        "candidates": scan.candidates,
        "excluded": scan.excluded,
        "stats": scan.stats,
        "market": scan.market,
        "provenance": scan.provenance,
        "versions": scan.versions,
    }
    if tuple(payload) != RUNTIME_SCAN_EXPORT_KEYS:
        raise AssertionError("runtime scan export allowlist changed unexpectedly")

    # Import lazily so health.py can use this central allowlist without a cycle.
    from stockscout_eod.health import assert_public_safe

    assert_public_safe(payload)
    validated = RawScanEnvelopeV1.model_validate(payload)
    if validated.stage_rows:
        raise AssertionError("runtime scan export unexpectedly contains stage rows")
    return payload


def write_runtime_scan_export(
    scan: RawScanEnvelopeV1, output_path: str | Path
) -> dict[str, Any]:
    """Validate the complete export before atomically making it visible."""

    payload = build_runtime_scan_export(scan)
    atomic_write_json(output_path, payload)
    return payload


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return max(0, int(value))
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return 0


def _diagnostic(
    *,
    status: str,
    checks: list[dict[str, bool | str]],
    counters: Mapping[str, int | float],
) -> dict[str, Any]:
    if status not in {"healthy", "failed"}:
        raise ValueError("diagnostic status must be fixed")
    if any(check.get("code") not in _HEALTH_CHECK_CODE_SET | {
        "health_check_contract",
        "health_evaluation_error",
        "scan_unavailable",
    } for check in checks):
        raise ValueError("diagnostic check code is not allowlisted")
    if tuple(counters) != _DIAGNOSTIC_COUNTER_KEYS:
        raise ValueError("diagnostic counter contract changed unexpectedly")

    payload: dict[str, Any] = {
        "schemaVersion": "stockscout-eod/diagnostic-v1",
        "status": status,
        "checks": checks,
        "counters": dict(counters),
    }
    from stockscout_eod.health import assert_public_safe

    assert_public_safe(payload)
    return payload


def _empty_diagnostic(code: str) -> dict[str, Any]:
    return _diagnostic(
        status="failed",
        checks=[{"code": code, "passed": False}],
        counters={
            "candidates": 0,
            "excluded": 0,
            "publishedRows": 0,
            "universe": 0,
            "failedTickers": 0,
            "failedChecks": 1,
            "coveragePct": 0.0,
        },
    )


def build_runtime_diagnostic(
    scan: RawScanEnvelopeV1,
    *,
    min_coverage_pct: float = 90.0,
    min_universe: int = 1000,
    allow_fixture: bool = False,
) -> dict[str, Any]:
    """Reduce health output to fixed codes and numeric counters only."""

    from stockscout_eod.health import evaluate_scan_health

    health = evaluate_scan_health(
        scan,
        min_coverage_pct=min_coverage_pct,
        min_universe=min_universe,
        allow_fixture=allow_fixture,
    )
    observed_codes = {check.code for check in health.checks}
    contract_ok = observed_codes == _HEALTH_CHECK_CODE_SET
    checks = [
        {"code": check.code, "passed": check.passed}
        for check in health.checks
        if check.code in _HEALTH_CHECK_CODE_SET
    ]
    if not contract_ok:
        checks.append({"code": "health_check_contract", "passed": False})

    stats = scan.stats
    universe = _nonnegative_int(
        stats.get("universe_size") or stats.get("universe_pre_negcache")
    )
    failed_tickers = _nonnegative_int(stats.get("tickers_failed_all_providers"))
    failed_checks = sum(not bool(check["passed"]) for check in checks)
    candidates = len(scan.candidates)
    excluded = len(scan.excluded)
    status = "healthy" if health.status == "healthy" and contract_ok else "failed"
    return _diagnostic(
        status=status,
        checks=checks,
        counters={
            "candidates": candidates,
            "excluded": excluded,
            "publishedRows": candidates + excluded,
            "universe": universe,
            "failedTickers": failed_tickers,
            "failedChecks": failed_checks,
            "coveragePct": health.coverage_pct,
        },
    )


def write_runtime_diagnostic(
    input_path: str | Path,
    output_path: str | Path,
    *,
    min_coverage_pct: float = 90.0,
    min_universe: int = 1000,
    allow_fixture: bool = False,
) -> dict[str, Any]:
    """Always write a bounded diagnostic without copying raw failure inputs."""

    try:
        scan = RawScanEnvelopeV1.model_validate_json(
            Path(input_path).read_text(encoding="utf-8")
        )
    except Exception:
        diagnostic = _empty_diagnostic("scan_unavailable")
    else:
        try:
            diagnostic = build_runtime_diagnostic(
                scan,
                min_coverage_pct=min_coverage_pct,
                min_universe=min_universe,
                allow_fixture=allow_fixture,
            )
        except Exception:
            diagnostic = _empty_diagnostic("health_evaluation_error")

    atomic_write_json(output_path, diagnostic)
    return diagnostic
