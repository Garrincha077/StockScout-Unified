"""Compact public chart staging and trusted OIDC publication."""
from __future__ import annotations

import base64
import gzip
import json
import math
from datetime import UTC, date, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pandas as pd

from stock_scout.config.loader import load_config
from stock_scout.data.cache import ParquetCache
from stockscout_eod.contracts import (
    ChartManifestV1,
    ChartPayloadV1,
    ChartShardV1,
    RawScanEnvelopeV1,
    wire_dump,
)
from stockscout_eod.github_oidc import github_oidc_token
from stockscout_eod.jsonio import canonical_json_bytes, sha256_bytes, write_json

CHART_BUCKETS = 128
MAX_SHARD_BYTES = 5 * 1024 * 1024


def _bucket(ticker: str) -> str:
    return f"{int(sha256_bytes(ticker.encode('utf-8'))[:8], 16) % CHART_BUCKETS:03d}"


def _providers(row: dict[str, Any], settings: Any) -> list[str]:
    values = [
        row.get("provider_used"),
        settings.providers.primary_data_provider,
        settings.providers.fallback_provider,
        settings.providers.tertiary_fallback_provider,
        settings.providers.deep_history_provider,
    ]
    return list(dict.fromkeys(str(value) for value in values if value))


def _read_first(
    cache: ParquetCache,
    providers: list[str],
    ticker: str,
    frequency: str,
) -> pd.DataFrame:
    for provider in providers:
        frame = cache.read(provider, ticker, frequency)
        if not frame.empty:
            return frame
    return pd.DataFrame()


def _derive_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return daily
    return daily.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["open", "high", "low", "close"])


def _compact(frame: pd.DataFrame, *, start: date, end: date) -> list[list[int | float]]:
    if frame.empty:
        return []
    subset = frame[(frame.index.date >= start) & (frame.index.date <= end)]
    rows: list[list[int | float]] = []
    for timestamp, row in subset.iterrows():
        values = [row.get(name) for name in ("open", "high", "low", "close", "volume")]
        try:
            numeric = [float(value) for value in values]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in numeric):
            continue
        point = pd.Timestamp(timestamp)
        point = point.tz_localize(UTC) if point.tzinfo is None else point.tz_convert(UTC)
        rows.append(
            [
                int(point.timestamp()),
                round(numeric[0], 6),
                round(numeric[1], 6),
                round(numeric[2], 6),
                round(numeric[3], 6),
                round(numeric[4]),
            ]
        )
    return rows


def _validated_storage_base_url(value: str, run_id: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    expected_suffixes = (
        f"/storage/v1/object/public/stockscout-eod-charts/{run_id}",
        f"/runs/{run_id}/charts",
    )
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith(expected_suffixes)
    ):
        raise ValueError(
            "chart storage URL must be an HTTPS run-scoped URL for this run"
        )
    return normalized


def build_chart_staging(
    scan: RawScanEnvelopeV1,
    *,
    config_path: str | Path,
    output_dir: str | Path,
    storage_base_url: str,
) -> ChartManifestV1:
    destination = Path(output_dir).resolve()
    lowered_parts = {part.lower().replace("-", "_") for part in destination.parts}
    if "public" in lowered_parts or "frontend_public" in lowered_parts:
        raise ValueError("chart staging must stay outside the Pages public directory")
    public_storage_url = _validated_storage_base_url(storage_base_url, scan.run_id)

    settings = load_config(config_path)
    cache = ParquetCache(settings.project_root / settings.cache.base_dir)
    all_rows = [*scan.candidates, *scan.excluded]
    requested = len(all_rows)
    available = 0
    shards_by_ticker: dict[str, str] = {}
    shards: dict[str, dict[str, Any]] = {f"{index:03d}": {} for index in range(CHART_BUCKETS)}
    session = date.fromisoformat(scan.session_date)
    daily_start = session - timedelta(days=round(5 * 365.25))

    for row in all_rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        providers = _providers(row, settings)
        daily = _read_first(cache, providers, ticker, "daily")
        weekly = _derive_weekly(daily)
        daily_rows = _compact(daily, start=daily_start, end=session)
        weekly_rows = _compact(weekly, start=daily_start, end=session)
        if not daily_rows:
            continue
        available += 1
        payload = ChartPayloadV1(
            ticker=ticker,
            asOf=scan.session_date,
            priceMode=scan.price_mode,
            daily=daily_rows,
            weekly=weekly_rows,
        )
        bucket = _bucket(ticker)
        shards[bucket][ticker] = wire_dump(payload)
        shards_by_ticker[ticker] = bucket

    shard_records: list[ChartShardV1] = []
    shard_dir = destination / scan.run_id / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in shards.items():
        raw = canonical_json_bytes(rows)
        compressed = gzip.compress(raw, compresslevel=9, mtime=0)
        if len(compressed) > MAX_SHARD_BYTES:
            raise ValueError(f"chart shard {name} exceeds 5 MiB")
        filename = f"{name}.json.gz"
        (shard_dir / filename).write_bytes(compressed)
        shard_records.append(
            ChartShardV1(
                name=name,
                sha256=sha256_bytes(compressed),
                bytes=len(compressed),
                tickerCount=len(rows),
            )
        )

    coverage = round(100.0 * available / max(1, requested), 2)
    manifest = ChartManifestV1(
        runId=scan.run_id,
        sessionDate=scan.session_date,
        generatedAt=scan.generated_at,
        priceMode=scan.price_mode,
        requested=requested,
        available=available,
        coveragePct=coverage,
        storageBaseUrl=public_storage_url,
        shards=shard_records,
        shardsByTicker=shards_by_ticker,
    )
    write_json(destination / scan.run_id / "manifest.json", wire_dump(manifest))
    return manifest


def _post_json(
    endpoint: str,
    token: str,
    payload: dict[str, Any],
    *,
    timeout: int = 90,
) -> dict[str, Any]:
    request = Request(
        endpoint,
        data=canonical_json_bytes(payload),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "StockScout-EOD/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"chart publish failed with HTTP {response.status}")
            result = json.loads(response.read())
    except HTTPError as error:
        detail = "request rejected"
        try:
            body = json.loads(error.read(2048))
            if isinstance(body, dict):
                message = body.get("error") or body.get("message")
                if isinstance(message, str) and message.strip():
                    detail = " ".join(message.split())[:500]
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
        raise RuntimeError(
            f"chart publisher rejected request (HTTP {error.code}): {detail}"
        ) from error
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError("chart publisher returned an invalid response")
    data = result.get("data")
    return data if isinstance(data, dict) else {}


def _post_blob(endpoint: str, token: str, payload: dict[str, Any]) -> None:
    _post_json(endpoint, token, payload)


def publish_chart_staging(
    *,
    staging_dir: str | Path,
    run_id: str,
    endpoint: str,
    audience: str = "stockscout-eod-publish",
) -> ChartManifestV1:
    root = Path(staging_dir).resolve() / run_id
    manifest_path = root / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = ChartManifestV1.model_validate_json(manifest_bytes)
    if manifest.run_id != run_id:
        raise ValueError("chart manifest runId mismatch")
    token = github_oidc_token(audience)

    for shard in manifest.shards:
        path = root / "shards" / f"{shard.name}.json.gz"
        content = path.read_bytes()
        if len(content) != shard.bytes or sha256_bytes(content) != shard.sha256:
            raise ValueError(f"chart shard integrity mismatch: {shard.name}")
        _post_blob(
            endpoint,
            token,
            {
                "action": "put_blob",
                "kind": "chart-shard",
                "runId": run_id,
                "shard": shard.name,
                "contentHash": shard.sha256,
                "contentBase64": base64.b64encode(content).decode("ascii"),
            },
        )

    _post_blob(
        endpoint,
        token,
        {
            "action": "put_blob",
            "kind": "chart-manifest",
            "runId": run_id,
            "contentHash": sha256_bytes(manifest_bytes),
            "contentBase64": base64.b64encode(manifest_bytes).decode("ascii"),
        },
    )
    return manifest


def promote_chart_run(
    *,
    endpoint: str,
    run_id: str,
    audience: str = "stockscout-eod-publish",
) -> dict[str, Any]:
    """Promote one legacy owner-prefixed active run to the public canonical path."""

    token = github_oidc_token(audience)
    return _post_json(
        endpoint,
        token,
        {"action": "promote_chart_run", "runId": run_id},
        timeout=300,
    )
