from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import duckdb

from stockscout_eod.health import evaluate_scan_health
from stockscout_unified.reuse import (
    build_bottom_charts_from_store,
    load_completed_bottom_snapshot,
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cloud_payload(records: list[dict]) -> dict:
    wrapped = []
    for index, record in enumerate(records):
        digest = hashlib.sha256(_canonical(record).encode()).hexdigest()
        wrapped.append(
            {
                "id": f"scan:2026-08-25:candidate:{record['ticker']}",
                "ticker": record["ticker"],
                "source": "excluded" if record.get("excluded_reason") else "candidate",
                "scan_order": index,
                "record_hash": digest,
                "record": record,
            }
        )
    lines = "\n".join(
        f"{item['source']}:{item['ticker']}:{item['record_hash']}" for item in wrapped
    )
    without_hash = {
        "schema_version": "stockscout-full-scan-cloud-v2",
        "run": {
            "run_id": "2026-08-25",
            "scan_date": "2026-08-25",
            "market_data_date": "2026-08-25",
            "market_regime": "under_pressure",
            "completed_at": "2026-08-25T21:19:05+00:00",
            "valid_count": len(records),
            "coverage_pct": "100",
            "data_status": "OK",
            "partial": False,
            "release_eligible": True,
        },
        "record_count": len(wrapped),
        "records_hash": hashlib.sha256(lines.encode()).hexdigest(),
        "records": wrapped,
        "field_catalog": [],
        "compatibility": {},
    }
    return {
        **without_hash,
        "payload_hash": hashlib.sha256(_canonical(without_hash).encode()).hexdigest(),
    }


def _write_cloud(path: Path, records: list[dict]) -> None:
    path.write_bytes(gzip.compress(_canonical(_cloud_payload(records)).encode(), mtime=0))


def test_completed_cloud_snapshot_is_verified_and_preserves_mode_order(tmp_path) -> None:
    source = tmp_path / "latest.json.gz"
    _write_cloud(
        source,
        [
            {"ticker": "BBB", "as_of": "2026-08-25", "provider_used": "local"},
            {"ticker": "AAA", "as_of": "2026-08-25", "provider_used": "yfinance"},
            {
                "ticker": "ZZZ",
                "as_of": "2026-08-25",
                "provider_used": "local",
                "excluded_reason": "fixture",
            },
        ],
    )

    scan = load_completed_bottom_snapshot(source, run_id="2026-08-25-reuse-1")

    assert [row["ticker"] for row in scan.candidates] == ["BBB", "AAA"]
    assert [row["ticker"] for row in scan.excluded] == ["ZZZ"]
    assert scan.price_mode == "split_only"
    assert scan.provenance["reuseOnly"] is True
    assert evaluate_scan_health(scan, min_universe=3).status == "healthy"


def test_completed_cloud_snapshot_rejects_tampering(tmp_path) -> None:
    source = tmp_path / "latest.json.gz"
    payload = _cloud_payload(
        [{"ticker": "AAA", "as_of": "2026-08-25", "provider_used": "local"}]
    )
    payload["records"][0]["record"]["ticker"] = "TAMPERED"
    source.write_bytes(gzip.compress(_canonical(payload).encode(), mtime=0))

    try:
        load_completed_bottom_snapshot(source, run_id="2026-08-25-reuse-1")
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("tampered cloud input was accepted")


def test_alignment_gate_allows_only_the_configured_stale_budget(tmp_path) -> None:
    source = tmp_path / "latest.json.gz"
    records = [
        {"ticker": f"T{index}", "as_of": "2026-08-25", "provider_used": "local"}
        for index in range(9)
    ]
    records.append({"ticker": "STALE", "as_of": "2026-08-24", "provider_used": "local"})
    _write_cloud(source, records)
    scan = load_completed_bottom_snapshot(source, run_id="2026-08-25-reuse-1")

    assert evaluate_scan_health(scan, min_universe=10, min_coverage_pct=90).status == "healthy"
    assert evaluate_scan_health(scan, min_universe=10, min_coverage_pct=95).status == "failed"


def test_chart_reuse_reads_only_split_only_store_without_fetching(tmp_path) -> None:
    source = tmp_path / "latest.json.gz"
    _write_cloud(
        source,
        [
            {"ticker": "AAA", "as_of": "2026-08-25", "provider_used": "local"},
            {"ticker": "BBB", "as_of": "2026-08-25", "provider_used": "local"},
        ],
    )
    scan = load_completed_bottom_snapshot(source, run_id="2026-08-25-reuse-1")
    store = tmp_path / "market.duckdb"
    connection = duckdb.connect(str(store))
    connection.execute(
        """
        CREATE TABLE ohlcv_daily (
          ticker VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
          close DOUBLE, volume BIGINT, basis VARCHAR
        )
        """
    )
    connection.execute(
        """
        INSERT INTO ohlcv_daily VALUES
          ('AAA', '2026-08-24', 10, 12, 9, 11, 1000, 'split_only'),
          ('AAA', '2026-08-25', 11, 13, 10, 12, 1200, 'split_only'),
          ('BBB', '2026-08-25', 20, 21, 19, 20.5, 800, 'split_only')
        """
    )
    connection.close()

    manifest = build_bottom_charts_from_store(
        scan,
        market_store=store,
        output_dir=tmp_path / "charts",
        storage_base_url=(
            "https://garrincha077.github.io/StockScout-Unified/data/modes/"
            "bottom-fishing/runs/2026-08-25-reuse-1/charts"
        ),
    )

    assert manifest.coverage_pct == 100.0
    assert manifest.price_mode == "split_only"
    assert set(manifest.shards_by_ticker) == {"AAA", "BBB"}
    shard = manifest.shards_by_ticker["AAA"]
    payload = json.loads(
        gzip.decompress(
            (tmp_path / "charts" / scan.run_id / "shards" / f"{shard}.json.gz").read_bytes()
        )
    )
    assert len(payload["AAA"]["daily"]) == 2
