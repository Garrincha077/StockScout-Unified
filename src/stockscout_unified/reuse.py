"""Build a publishable Bottom snapshot from an already completed StockScout run.

This module never fetches market data and never executes a detector.  It is a
recovery path for a GitHub workflow that failed after the source scans had
already completed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import duckdb

from stockscout_eod.charts import CHART_BUCKETS, MAX_SHARD_BYTES
from stockscout_eod.contracts import (
    ChartManifestV1,
    ChartPayloadV1,
    ChartShardV1,
    RawScanEnvelopeV1,
    wire_dump,
)
from stockscout_eod.jsonio import atomic_write_json, canonical_json_bytes, sha256_bytes

from .contracts import MODE_SPECS

CLOUD_SCHEMA = "stockscout-full-scan-cloud-v2"


def _canonical_cloud_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _verify_cloud_payload(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != CLOUD_SCHEMA:
        raise ValueError(f"unsupported StockScout cloud schema: {payload.get('schema_version')}")
    expected_payload_hash = str(payload.get("payload_hash") or "")
    without_hash = {key: value for key, value in payload.items() if key != "payload_hash"}
    actual_payload_hash = hashlib.sha256(
        _canonical_cloud_json(without_hash).encode("utf-8")
    ).hexdigest()
    if actual_payload_hash != expected_payload_hash:
        raise ValueError("StockScout cloud payload hash mismatch")

    records = payload.get("records") or []
    if len(records) != int(payload.get("record_count") or -1):
        raise ValueError("StockScout cloud record count mismatch")
    record_lines: list[str] = []
    for item in records:
        record = item.get("record")
        if not isinstance(record, dict):
            raise ValueError("StockScout cloud record is not an object")
        digest = hashlib.sha256(_canonical_cloud_json(record).encode("utf-8")).hexdigest()
        if digest != item.get("record_hash"):
            raise ValueError(f"StockScout cloud record hash mismatch: {item.get('ticker')}")
        record_lines.append(f"{item.get('source')}:{item.get('ticker')}:{digest}")
    records_hash = hashlib.sha256("\n".join(record_lines).encode("utf-8")).hexdigest()
    if records_hash != payload.get("records_hash"):
        raise ValueError("StockScout cloud aggregate records hash mismatch")


def load_completed_bottom_snapshot(
    cloud_snapshot: str | Path,
    *,
    run_id: str,
) -> RawScanEnvelopeV1:
    """Convert the immutable full-scan cloud payload into the engine wire contract."""
    source = Path(cloud_snapshot)
    raw = source.read_bytes()
    decoded = gzip.decompress(raw) if source.suffix.lower() == ".gz" else raw
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ValueError("StockScout cloud snapshot must be an object")
    _verify_cloud_payload(payload)

    run = payload.get("run") or {}
    session_date = str(run.get("scan_date") or run.get("run_id") or "")
    date.fromisoformat(session_date)
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    as_of_counts: Counter[str] = Counter()
    providers: Counter[str] = Counter()
    for item in payload.get("records") or []:
        record = dict(item["record"])
        ticker = str(record.get("ticker") or item.get("ticker") or "").strip().upper()
        if not ticker or ticker != str(item.get("ticker") or "").strip().upper():
            raise ValueError("StockScout cloud ticker identity mismatch")
        record["ticker"] = ticker
        record["_scan_order"] = int(item.get("scan_order") or 0)
        record["_scan_source"] = str(item.get("source") or "candidate")
        as_of_counts[str(record.get("as_of") or "")] += 1
        providers[str(record.get("provider_used") or "unknown")] += 1
        (excluded if item.get("source") == "excluded" else candidates).append(record)

    candidates.sort(key=lambda row: int(row.get("_scan_order") or 0))
    excluded.sort(key=lambda row: int(row.get("_scan_order") or 0))
    total = len(candidates) + len(excluded)
    aligned = as_of_counts.get(session_date, 0)
    fresh_pct = round(100.0 * aligned / max(1, total), 2)
    completed_at = str(run.get("completed_at") or "")
    if not completed_at:
        completed_at = datetime.now(tz=UTC).isoformat()

    stats = {
        "universe_size": int(run.get("valid_count") or total),
        "coverage_pct": float(run.get("coverage_pct") or 0.0),
        "data_status": str(run.get("data_status") or "FAILED"),
        "tickers_failed_all_providers": 0,
        "market_data_latest_bar": str(run.get("market_data_date") or session_date),
        "market_data_fresh_published_pct": fresh_pct,
        "market_data_stale_published_rows": total - aligned,
        "source_partial": bool(run.get("partial")),
        "source_partial_reason": run.get("partial_reason"),
        "source_release_eligible": bool(run.get("release_eligible")),
    }
    return RawScanEnvelopeV1(
        runId=run_id,
        sessionDate=session_date,
        generatedAt=completed_at,
        priceMode="split_only",
        candidates=candidates,
        excluded=excluded,
        stats=stats,
        stageRows=[],
        market={"regime": {"state": run.get("market_regime")}},
        provenance={
            "mode": "production",
            "engineSource": "verified-stockscout-cloud-snapshot",
            "sourceRunId": run.get("run_id"),
            "sourcePayloadHash": payload.get("payload_hash"),
            "sourceRecordsHash": payload.get("records_hash"),
            "primaryProvider": "+".join(sorted(providers)) or "unknown",
            "providerCounts": dict(sorted(providers.items())),
            "aiRanking": False,
            "weeklyBars": "resampled_split_only_daily",
            "reuseOnly": True,
        },
        versions={"engine": MODE_SPECS["bottom-fishing"].source_commit},
    )


def _bucket(ticker: str) -> str:
    return f"{int(sha256_bytes(ticker.encode('utf-8'))[:8], 16) % CHART_BUCKETS:03d}"


def build_bottom_charts_from_store(
    scan: RawScanEnvelopeV1,
    *,
    market_store: str | Path,
    output_dir: str | Path,
    storage_base_url: str,
) -> ChartManifestV1:
    """Export chart shards from the existing split-only DuckDB, read-only."""
    store_path = Path(market_store).resolve()
    if not store_path.is_file():
        raise FileNotFoundError(f"market store is missing: {store_path}")
    parsed_url = urlsplit(storage_base_url.strip())
    expected_suffix = f"/runs/{scan.run_id}/charts"
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
        or not parsed_url.path.endswith(expected_suffix)
    ):
        raise ValueError("chart storage URL must be an HTTPS run-scoped Pages URL")
    output_root = Path(output_dir).resolve() / scan.run_id
    shard_dir = output_root / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)

    all_rows = [*scan.candidates, *scan.excluded]
    tickers = sorted({str(row.get("ticker") or "").strip().upper() for row in all_rows})
    by_bucket: dict[str, list[str]] = {
        f"{index:03d}": [] for index in range(CHART_BUCKETS)
    }
    for ticker in tickers:
        if ticker:
            by_bucket[_bucket(ticker)].append(ticker)

    session = date.fromisoformat(scan.session_date)
    start = session - timedelta(days=round(5 * 365.25))
    available = 0
    shards_by_ticker: dict[str, str] = {}
    shard_records: list[ChartShardV1] = []
    connection = duckdb.connect(str(store_path), read_only=True)
    try:
        basis_rows = connection.execute(
            "SELECT DISTINCT basis FROM ohlcv_daily WHERE basis IS DISTINCT FROM 'split_only'"
        ).fetchall()
        if basis_rows:
            raise ValueError(f"market store contains a non-split-only price basis: {basis_rows}")
        ticker_map = [(ticker, bucket) for bucket, rows in by_bucket.items() for ticker in rows]
        connection.execute("CREATE TEMP TABLE selected_tickers (ticker VARCHAR, bucket VARCHAR)")
        connection.executemany("INSERT INTO selected_tickers VALUES (?, ?)", ticker_map)
        cursor = connection.execute(
            """
            WITH daily AS (
              SELECT selected_tickers.bucket, bars.ticker, bars.date,
                     bars.open, bars.high, bars.low, bars.close, bars.volume
              FROM ohlcv_daily AS bars
              JOIN selected_tickers USING (ticker)
              WHERE bars.date >= ? AND bars.date <= ? AND bars.basis = 'split_only'
                AND isfinite(bars.open) AND isfinite(bars.high)
                AND isfinite(bars.low) AND isfinite(bars.close)
                AND bars.volume IS NOT NULL
            ),
            weekly AS (
              SELECT bucket, ticker, max(date) AS date,
                     arg_min(open, date) AS open,
                     max(high) AS high,
                     min(low) AS low,
                     arg_max(close, date) AS close,
                     sum(volume) AS volume
              FROM daily
              GROUP BY bucket, ticker, date_trunc('week', date)
            ),
            daily_packed AS (
              SELECT bucket, ticker,
                     list([epoch(date), round(open, 6), round(high, 6), round(low, 6),
                           round(close, 6), volume::DOUBLE] ORDER BY date) AS bars
              FROM daily GROUP BY bucket, ticker
            ),
            weekly_packed AS (
              SELECT bucket, ticker,
                     list([epoch(date), round(open, 6), round(high, 6), round(low, 6),
                           round(close, 6), volume::DOUBLE] ORDER BY date) AS bars
              FROM weekly GROUP BY bucket, ticker
            )
            SELECT daily_packed.bucket, daily_packed.ticker, daily_packed.bars,
                   weekly_packed.bars
            FROM daily_packed
            JOIN weekly_packed USING (bucket, ticker)
            ORDER BY daily_packed.bucket, daily_packed.ticker
            """,
            [start, session],
        )

        def write_shard(bucket_name: str, shard: dict[str, Any]) -> None:
            compressed = gzip.compress(canonical_json_bytes(shard), compresslevel=9, mtime=0)
            if len(compressed) > MAX_SHARD_BYTES:
                raise ValueError(f"chart shard {bucket_name} exceeds 5 MiB")
            (shard_dir / f"{bucket_name}.json.gz").write_bytes(compressed)
            shard_records.append(
                ChartShardV1(
                    name=bucket_name,
                    sha256=sha256_bytes(compressed),
                    bytes=len(compressed),
                    tickerCount=len(shard),
                )
            )

        current_bucket: str | None = None
        current_shard: dict[str, Any] = {}
        while row := cursor.fetchone():
            bucket_name, ticker, daily_rows, weekly_rows = row
            if current_bucket is not None and bucket_name != current_bucket:
                write_shard(current_bucket, current_shard)
                current_shard = {}
            current_bucket = str(bucket_name)
            ticker_text = str(ticker).upper()
            current_shard[ticker_text] = wire_dump(
                ChartPayloadV1(
                    ticker=ticker_text,
                    asOf=scan.session_date,
                    priceMode="split_only",
                    daily=daily_rows,
                    weekly=weekly_rows,
                )
            )
            available += 1
            shards_by_ticker[ticker_text] = current_bucket
        if current_bucket is not None:
            write_shard(current_bucket, current_shard)
        written = {record.name for record in shard_records}
        for bucket_name in by_bucket:
            if bucket_name not in written:
                write_shard(bucket_name, {})
    finally:
        connection.close()

    requested = len(tickers)
    coverage = round(100.0 * available / max(1, requested), 2)
    manifest = ChartManifestV1(
        runId=scan.run_id,
        sessionDate=scan.session_date,
        generatedAt=scan.generated_at,
        priceMode="split_only",
        requested=requested,
        available=available,
        coveragePct=coverage,
        storageBaseUrl=storage_base_url,
        shards=shard_records,
        shardsByTicker=shards_by_ticker,
    )
    atomic_write_json(output_root / "manifest.json", wire_dump(manifest))
    return manifest


def rebind_bottom_checkpoint(
    checkpoint_dir: str | Path,
    *,
    output_dir: str | Path,
    run_id: str,
    expected_session_date: str,
    storage_base_url: str,
) -> tuple[Path, ChartManifestV1]:
    """Reuse a verified Bottom scan/charts checkpoint under a fresh run identity.

    The expensive detector output and chart shard bytes remain unchanged.  Only
    run-scoped metadata is rebound after strict session, price-basis, coverage,
    and shard-hash validation.
    """
    checkpoint_root = Path(checkpoint_dir).resolve()
    scan = RawScanEnvelopeV1.model_validate_json((checkpoint_root / "bottom.json").read_bytes())
    if scan.session_date != expected_session_date:
        raise ValueError(
            f"Bottom checkpoint session {scan.session_date} does not match {expected_session_date}"
        )
    if scan.price_mode != "split_only":
        raise ValueError("Bottom checkpoint price basis is not split_only")

    source_run_id = scan.run_id
    source_chart_root = checkpoint_root / "bottom-charts" / source_run_id
    charts = ChartManifestV1.model_validate_json((source_chart_root / "manifest.json").read_bytes())
    if charts.run_id != source_run_id or charts.session_date != expected_session_date:
        raise ValueError("Bottom checkpoint chart identity mismatch")
    if charts.price_mode != "split_only":
        raise ValueError("Bottom checkpoint charts are not split_only")
    if charts.coverage_pct < 95.0:
        raise ValueError(
            f"Bottom checkpoint chart coverage is {charts.coverage_pct:.2f}%, expected at least 95%"
        )

    parsed_url = urlsplit(storage_base_url.strip())
    expected_suffix = f"/runs/{run_id}/charts"
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
        or not parsed_url.path.endswith(expected_suffix)
    ):
        raise ValueError("chart storage URL must be an HTTPS run-scoped Pages URL")

    source_shards = source_chart_root / "shards"
    for shard in charts.shards:
        shard_path = source_shards / f"{shard.name}.json.gz"
        payload = shard_path.read_bytes()
        if len(payload) != shard.bytes or sha256_bytes(payload) != shard.sha256:
            raise ValueError(f"Bottom checkpoint chart shard hash mismatch: {shard.name}")

    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    raw_payload = wire_dump(scan)
    raw_payload["runId"] = run_id
    provenance = dict(raw_payload.get("provenance") or {})
    provenance["checkpointReuse"] = True
    provenance["checkpointSourceRunId"] = source_run_id
    raw_payload["provenance"] = provenance
    rebound_scan = RawScanEnvelopeV1.model_validate(raw_payload)
    raw_path = output_root / "bottom.json"
    atomic_write_json(raw_path, wire_dump(rebound_scan))

    destination_chart_root = output_root / "bottom-charts" / run_id
    destination_shards = destination_chart_root / "shards"
    destination_shards.mkdir(parents=True, exist_ok=True)
    for shard in charts.shards:
        shutil.copyfile(
            source_shards / f"{shard.name}.json.gz",
            destination_shards / f"{shard.name}.json.gz",
        )

    chart_payload = wire_dump(charts)
    chart_payload["runId"] = run_id
    chart_payload["storageBaseUrl"] = storage_base_url
    rebound_charts = ChartManifestV1.model_validate(chart_payload)
    atomic_write_json(destination_chart_root / "manifest.json", wire_dump(rebound_charts))
    return raw_path, rebound_charts


def prepare_bottom_reuse(
    *,
    cloud_snapshot: str | Path,
    market_store: str | Path,
    output_dir: str | Path,
    run_id: str,
    storage_base_url: str,
) -> tuple[Path, ChartManifestV1]:
    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    scan = load_completed_bottom_snapshot(cloud_snapshot, run_id=run_id)
    raw_path = output_root / "bottom.json"
    atomic_write_json(raw_path, wire_dump(scan))
    charts = build_bottom_charts_from_store(
        scan,
        market_store=market_store,
        output_dir=output_root / "bottom-charts",
        storage_base_url=storage_base_url,
    )
    return raw_path, charts
