#!/usr/bin/env python3
"""Derive versioned, bounded frontend assets from the canonical snapshot.

``latest.json`` remains the complete, immutable audit/recovery snapshot. The
browser never needs that file: this module publishes a bounded StockScout core,
a compact LEGACY index, deterministic LEGACY detail shards, and a manifest that
cryptographically ties every client asset to the same source snapshot.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from build_legacy_confirmation_sidecar import build_sidecar

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "frontend" / "public" / "data"
LATEST = DATA_DIR / "latest.json"
CORE = DATA_DIR / "core.json"
MANIFEST = DATA_DIR / "manifest.json"
LEGACY_SIDECAR = DATA_DIR / "shadow" / "legacy-confirmation.json"
FILTER_ENGINE = ROOT / "frontend" / "src" / "deepvue" / "filterEngine.ts"

MODEL = "stockscout-client-core-v2"
MANIFEST_VERSION = 2
LEGACY_SHARD_COUNT = 128
# Keep a hard client-payload ceiling while allowing the three compact
# Fundamental Evidence dimensions used by the existing detail panel.
MAX_CORE_BYTES = 8_050_000

# These fields are rendered outside Filter Builder. Filter Builder's explicit
# fieldDefs list is read below and merged into this list, so adding a new filter
# without publishing its value fails tests instead of silently expanding core.
CORE_EXTRA_FIELDS = {
    "ticker", "price", "stageName", "primarySetup",
    "setupTags", "change20d",
    "originalBuyScore", "originalRR", "originalTTPasses",
    "originalVcpQuality", "originalAdVolumeRatio", "originalRiskPct",
    "originalBreakoutVolumeConfirmed", "originalSellScore",
    "originalRunSellSignal", "ema10d", "ema20d", "sma10w", "sma20w",
    "vcpScore", "rsFromHigh", "structureScore", "baseScore", "triggerScore",
    "fundamentalDims",
}

LEGACY_INDEX_FIELDS = {
    "ticker", "price", "stage", "stageName", "originalBuyScore",
    "originalBuy", "originalMarketQualifiedBuy", "originalRunBuySignal",
    "originalRR", "originalStopLoss", "originalRiskPct",
    "originalRewardTarget", "originalEntryQuality", "originalTTScore",
    "originalTTPasses", "originalVcpQuality", "originalAdVolumeRatio",
    "originalBreakoutType", "originalBreakoutLevel",
    "originalBreakoutVolumeConfirmed", "originalSellScore", "originalSell",
    "originalRunSellSignal", "originalMarketQualifiedSell",
    "originalSellSeverity", "phaseConfidence",
}


def encoded(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def frontend_field_ids(path: Path = FILTER_ENGINE) -> set[str]:
    """Return the explicit Filter Builder field ids from its source contract."""
    source = path.read_text(encoding="utf-8")
    try:
        block = source.split("export const fieldDefs", 1)[1].split(
            "export const opsByKind", 1
        )[0]
    except IndexError as exc:
        raise SystemExit(f"Unable to locate frontend fieldDefs in {path}") from exc
    fields = set(re.findall(r"\{id:'([^']+)'", block))
    if not fields:
        raise SystemExit(f"No frontend fieldDefs found in {path}")
    return fields


def projected_row(row: Mapping[str, Any], fields: set[str]) -> dict[str, Any]:
    # Missing and null are equivalent to the client, so omit nulls to keep the
    # explicit field contract below the hard publication budget.
    return {key: value for key, value in row.items() if key in fields and value is not None}


def build_core_payload(
    payload: dict[str, Any],
    confirmation_by_ticker: Mapping[str, Mapping[str, Any]] | None = None,
    fields: set[str] | None = None,
) -> dict[str, Any]:
    """Build the bounded StockScout payload without source-detail objects."""
    core = copy.deepcopy(payload)
    confirmations = confirmation_by_ticker or {}
    published_fields = set(fields or (frontend_field_ids() | CORE_EXTRA_FIELDS))
    rows = []
    for row in payload.get("universe") or []:
        if not isinstance(row, dict):
            continue
        projected = projected_row(row, published_fields)
        fundamental_dims = [
            row.get("fundamentalGrowthScore"),
            row.get("fundamentalMarginScore"),
            row.get("fundamentalInventoryScore"),
        ]
        if any(value is not None for value in fundamental_dims):
            projected["fundamentalDims"] = fundamental_dims
        ticker = str(row.get("ticker") or "").strip().upper()
        confirmation = confirmations.get(ticker)
        if confirmation:
            projected["legacyConfirmationStatus"] = confirmation.get("status")
            projected["legacyConfirmationReasons"] = list(confirmation.get("reasons") or [])
        rows.append(projected)
    core["universe"] = rows
    core["clientPayloadModel"] = MODEL
    core["legacyIndexFile"] = "legacy/index.json"
    core["legacyConfirmationFile"] = "shadow/legacy-confirmation.json"

    layers = core.get("layers")
    if isinstance(layers, dict):
        legacy = layers.get("legacy")
        if isinstance(legacy, dict):
            legacy.pop("lazyFile", None)
            legacy["indexFile"] = "legacy/index.json"
            legacy["detailsPath"] = "legacy/details"
            legacy["confirmationFile"] = "shadow/legacy-confirmation.json"
        layers.pop("sharedEvidenceFile", None)
    return core


def build_legacy_index(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "model": "legacy-client-index-v1",
        "generatedAt": payload.get("generatedAt"),
        "market": copy.deepcopy(payload.get("market") or {}),
        "layers": copy.deepcopy(payload.get("layers") or {}),
        "originalEngineModel": payload.get("originalEngineModel"),
        "legacyCompleteSourceCaptureModel": payload.get(
            "legacyCompleteSourceCaptureModel"
        ),
        "universe": [
            projected_row(row, LEGACY_INDEX_FIELDS)
            for row in payload.get("universe") or []
            if isinstance(row, dict)
        ],
    }


def shard_number(ticker: str, count: int = LEGACY_SHARD_COUNT) -> int:
    normalized = ticker.strip().upper()
    value = sum((index + 1) * ord(char) for index, char in enumerate(normalized))
    return value % max(1, count)


def build_legacy_detail_shards(
    payload: Mapping[str, Any], count: int = LEGACY_SHARD_COUNT
) -> list[dict[str, Any]]:
    shards: list[dict[str, Any]] = [dict() for _ in range(count)]
    for row in payload.get("universe") or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        detail = projected_row(row, LEGACY_INDEX_FIELDS)
        detail["originalEngine"] = copy.deepcopy(row.get("originalEngine"))
        shards[shard_number(ticker, count)][ticker] = detail
    return shards


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)


def aggregate_asset(files: list[tuple[str, bytes]]) -> dict[str, Any]:
    digest_input = encoded([{"path": path, "sha256": sha256(data)} for path, data in files])
    return {
        "sha256": sha256(digest_input),
        "bytes": sum(len(data) for _, data in files),
    }


def chart_asset(data_dir: Path, payload: Mapping[str, Any], universe_size: int) -> dict[str, Any]:
    chart_dir = data_dir / "charts"
    files = sorted(chart_dir.glob("*.json")) if chart_dir.exists() else []
    file_bytes = [(f"charts/{path.name}", path.read_bytes()) for path in files]
    aggregate = aggregate_asset(file_bytes)
    published_tickers: set[str] = set()
    for _, data in file_bytes:
        parsed = json.loads(data)
        if isinstance(parsed, dict):
            published_tickers.update(str(ticker).upper() for ticker in parsed)
    canonical_tickers = {
        str(row.get("ticker") or "").upper() for row in payload.get("universe") or []
    }
    covered = len(published_tickers & canonical_tickers)
    return {
        "path": "charts",
        "pattern": "{shard}.json",
        "shardCount": int(payload.get("chartShardCount") or 128),
        **aggregate,
        "coverage": covered,
        "coveragePct": round(100 * covered / max(1, universe_size), 2),
    }


def publish(
    latest: Path = LATEST,
    core_path: Path = CORE,
    manifest: Path = MANIFEST,
    legacy_sidecar_path: Path = LEGACY_SIDECAR,
    legacy_index_path: Path | None = None,
    legacy_details_dir: Path | None = None,
) -> dict[str, Any]:
    if not latest.exists():
        raise SystemExit(f"Missing validated frontend payload: {latest}")

    full_bytes = latest.read_bytes()
    payload = json.loads(full_bytes)
    if not isinstance(payload, dict) or not payload.get("universe"):
        raise SystemExit("Validated frontend payload has no universe")

    data_dir = core_path.parent
    index_path = legacy_index_path or data_dir / "legacy" / "index.json"
    details_dir = legacy_details_dir or data_dir / "legacy" / "details"
    sidecar = build_sidecar(payload)
    sidecar_bytes = encoded(sidecar)
    by_ticker = sidecar.get("byTicker") or {}
    fields = frontend_field_ids() | CORE_EXTRA_FIELDS
    core = build_core_payload(payload, by_ticker, fields)
    core_bytes = encoded(core)
    legacy_index = build_legacy_index(payload)
    legacy_index_bytes = encoded(legacy_index)
    legacy_shards = build_legacy_detail_shards(payload)
    detail_files = [
        (f"legacy/details/{index:03d}.json", encoded(shard))
        for index, shard in enumerate(legacy_shards)
    ]

    universe = payload.get("universe") or []
    canonical_tickers = [str(row.get("ticker") or "").upper() for row in universe]
    core_tickers = [str(row.get("ticker") or "").upper() for row in core["universe"]]
    index_tickers = [str(row.get("ticker") or "").upper() for row in legacy_index["universe"]]
    detail_tickers = [ticker for shard in legacy_shards for ticker in shard]
    if len(set(canonical_tickers)) != len(canonical_tickers):
        raise SystemExit("Canonical payload contains duplicate tickers")
    if set(core_tickers) != set(canonical_tickers) or len(core_tickers) != len(canonical_tickers):
        raise SystemExit("Core payload changed ticker identity or cardinality")
    if set(index_tickers) != set(canonical_tickers) or len(index_tickers) != len(canonical_tickers):
        raise SystemExit("LEGACY index changed ticker identity or cardinality")
    if set(detail_tickers) != set(canonical_tickers) or len(detail_tickers) != len(canonical_tickers):
        raise SystemExit("LEGACY details changed ticker identity or cardinality")
    if sidecar.get("total") != len(universe) or len(by_ticker) != len(universe):
        raise SystemExit("LEGACY sidecar changed universe cardinality")
    if sidecar.get("source", {}).get("generatedAt") != payload.get("generatedAt"):
        raise SystemExit("LEGACY sidecar snapshot does not match canonical payload")
    if core.get("chartShards") != payload.get("chartShards"):
        raise SystemExit("Core payload changed chart-shard mapping")
    if len(core_bytes) > MAX_CORE_BYTES:
        raise SystemExit(
            f"Core payload exceeds {MAX_CORE_BYTES:,} byte budget: {len(core_bytes):,}"
        )

    for row in core.get("universe") or []:
        ticker = str(row.get("ticker") or "").upper()
        confirmation = by_ticker.get(ticker) or {}
        if row.get("legacyConfirmationStatus") != confirmation.get("status"):
            raise SystemExit(f"LEGACY confirmation mismatch in core projection: {ticker}")

    detail_aggregate = aggregate_asset(detail_files)
    source_sha = sha256(full_bytes)
    universe_size = len(canonical_tickers)
    common_coverage = {"coverage": universe_size, "coveragePct": 100.0}
    session_date = (payload.get("market") or {}).get("scanDate")
    chart = chart_asset(data_dir, payload, universe_size)
    manifest_payload = {
        "manifestVersion": MANIFEST_VERSION,
        "model": MODEL,
        "generatedAt": payload.get("generatedAt"),
        "marketSession": {
            "date": session_date,
            "status": "closed" if session_date else "unknown",
            "timezone": "America/New_York",
        },
        "universe": universe_size,
        "provenance": {
            "source": {"kind": "canonical-audit", "path": "latest.json", "sha256": source_sha, "bytes": len(full_bytes)},
            "publication": {"kind": "frontend-projection", "model": MODEL, "sourceSha256": source_sha},
        },
        "assets": {
            "core": {"path": "core.json", "sha256": sha256(core_bytes), "bytes": len(core_bytes), **common_coverage},
            "legacyIndex": {"path": "legacy/index.json", "sha256": sha256(legacy_index_bytes), "bytes": len(legacy_index_bytes), **common_coverage},
            "legacyDetails": {"path": "legacy/details", "pattern": "{shard}.json", "shardCount": LEGACY_SHARD_COUNT, **detail_aggregate, **common_coverage},
            "legacyConfirmation": {"path": "shadow/legacy-confirmation.json", "sha256": sha256(sidecar_bytes), "bytes": len(sidecar_bytes), **common_coverage},
            "charts": chart,
        },
        "frontendFields": sorted(fields | {"legacyConfirmationStatus", "legacyConfirmationReasons"}),
        "legacyConfirmationCounts": sidecar.get("counts") or {},
    }

    atomic_write(core_path, core_bytes)
    atomic_write(index_path, legacy_index_bytes)
    for relative, data in detail_files:
        atomic_write(details_dir / Path(relative).name, data)
    atomic_write(legacy_sidecar_path, sidecar_bytes)
    atomic_write(manifest, encoded(manifest_payload) + b"\n")
    if latest.read_bytes() != full_bytes:
        raise SystemExit("Invariant violation: client projection modified canonical latest.json")

    print(
        f"Frontend v2: core={len(core_bytes)/1_000_000:.2f} MB, "
        f"canonical={len(full_bytes)/1_000_000:.2f} MB; "
        f"LEGACY index={len(legacy_index_bytes)/1_000_000:.2f} MB, "
        f"details={detail_aggregate['bytes']/1_000_000:.2f} MB"
    )
    return manifest_payload


if __name__ == "__main__":
    publish()
