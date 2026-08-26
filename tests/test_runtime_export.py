from __future__ import annotations

import json

import pytest

from stockscout_eod.cli import main
from stockscout_eod.contracts import RawScanEnvelopeV1, wire_dump
from stockscout_eod.health import PublicPayloadError, assert_public_safe, evaluate_scan_health
from stockscout_eod.jsonio import atomic_write_json
from stockscout_eod.runner import load_raw_scan
from stockscout_eod.runtime_export import (
    HEALTH_CHECK_CODES,
    RUNTIME_SCAN_EXPORT_KEYS,
    build_runtime_scan_export,
    write_runtime_diagnostic,
    write_runtime_scan_export,
)


def _scan() -> RawScanEnvelopeV1:
    return RawScanEnvelopeV1(
        runId="2026-08-21-eod-test",
        sessionDate="2026-08-21",
        generatedAt="2026-08-21T20:30:00Z",
        priceMode="split_only",
        candidates=[{"ticker": "AAA", "as_of": "2026-08-21", "price": 100.0}],
        excluded=[{"ticker": "BBB", "as_of": "2026-08-21", "price": 50.0}],
        stats={
            "universe_size": 2,
            "coverage_pct": 100.0,
            "data_status": "OK",
            "tickers_failed_all_providers": 0,
            "market_data_latest_bar": "2026-08-21",
            "market_data_fresh_published_pct": 100.0,
        },
        stageRows=[
            {
                "api_token": "stage-secret-never-exported",
                "duckdb_path": r"C:\Users\operator\market.duckdb",
            }
        ],
        market={"regime": {"state": "confirmed_uptrend"}},
        provenance={"mode": "production", "primaryProvider": "fixture"},
        versions={"engine": "fixture", "ranking": "frozen"},
    )


def test_runtime_export_is_an_exact_allowlist_without_stage_rows(tmp_path) -> None:
    output = tmp_path / "runtime-scan.json"
    payload = write_runtime_scan_export(_scan(), output)

    assert tuple(payload) == RUNTIME_SCAN_EXPORT_KEYS
    assert "stageRows" not in payload
    assert_public_safe(payload)
    serialized = output.read_text(encoding="utf-8")
    assert "stageRows" not in serialized
    assert "stage-secret-never-exported" not in serialized
    assert "market.duckdb" not in serialized
    assert load_raw_scan(output).stage_rows == []


def test_runtime_export_validates_every_exported_section_before_writing(tmp_path) -> None:
    scan = _scan().model_copy(deep=True)
    scan.versions["api_token"] = "must-fail-closed"
    output = tmp_path / "runtime-scan.json"

    with pytest.raises(PublicPayloadError, match=r"versions\.api_token"):
        write_runtime_scan_export(scan, output)

    assert not output.exists()
    health = evaluate_scan_health(scan, min_universe=2)
    safety = next(check for check in health.checks if check.code == "public_payload_safety")
    assert safety.passed is False


def test_runtime_diagnostic_contains_only_fixed_codes_and_counters(tmp_path) -> None:
    scan = _scan().model_copy(deep=True)
    scan.candidates[0]["authorization"] = "Bearer candidate-secret"
    input_path = tmp_path / "unsafe-scan.json"
    output_path = tmp_path / "diagnostic.json"
    atomic_write_json(input_path, wire_dump(scan))

    diagnostic = write_runtime_diagnostic(
        input_path,
        output_path,
        min_universe=2,
    )

    assert diagnostic["status"] == "failed"
    assert tuple(diagnostic) == ("schemaVersion", "status", "checks", "counters")
    assert tuple(diagnostic["counters"]) == (
        "candidates",
        "excluded",
        "publishedRows",
        "universe",
        "failedTickers",
        "failedChecks",
        "coveragePct",
    )
    assert {check["code"] for check in diagnostic["checks"]} == set(HEALTH_CHECK_CODES)
    assert all(set(check) == {"code", "passed"} for check in diagnostic["checks"])
    safety = next(
        check for check in diagnostic["checks"] if check["code"] == "public_payload_safety"
    )
    assert safety["passed"] is False
    assert_public_safe(diagnostic)
    serialized = output_path.read_text(encoding="utf-8")
    assert "candidate-secret" not in serialized
    assert "authorization" not in serialized
    assert "stageRows" not in serialized
    assert "legacy" not in serialized.lower()


def test_runtime_diagnostic_for_missing_or_invalid_scan_is_bounded(tmp_path) -> None:
    missing = write_runtime_diagnostic(
        tmp_path / "missing.json", tmp_path / "missing-diagnostic.json"
    )
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("not-json private material", encoding="utf-8")
    invalid = write_runtime_diagnostic(
        invalid_path, tmp_path / "invalid-diagnostic.json"
    )

    for diagnostic in (missing, invalid):
        assert diagnostic["status"] == "failed"
        assert diagnostic["checks"] == [{"code": "scan_unavailable", "passed": False}]
        assert diagnostic["counters"]["publishedRows"] == 0
        assert_public_safe(diagnostic)


def test_runtime_export_and_diagnostic_cli_commands_write_contracts(tmp_path) -> None:
    raw_path = tmp_path / "scan.json"
    export_path = tmp_path / "runtime.json"
    diagnostic_path = tmp_path / "diagnostic.json"
    atomic_write_json(raw_path, wire_dump(_scan()))

    assert main(
        [
            "export-runtime-scan",
            "--input",
            str(raw_path),
            "--output",
            str(export_path),
        ]
    ) == 0
    assert "stageRows" not in json.loads(export_path.read_text(encoding="utf-8"))
    assert main(
        [
            "write-runtime-diagnostic",
            "--input",
            str(raw_path),
            "--output",
            str(diagnostic_path),
            "--min-universe",
            "2",
        ]
    ) == 0
    assert json.loads(diagnostic_path.read_text(encoding="utf-8"))["status"] == "healthy"


def test_build_runtime_scan_export_rejects_unsafe_top_level_identity() -> None:
    scan = _scan().model_copy(update={"run_id": r"C:\Users\operator\private-run"})
    with pytest.raises(PublicPayloadError, match="runId"):
        build_runtime_scan_export(scan)
