"""Publish isolated mode snapshots and atomically activate the unified pointer."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from stockscout_eod.contracts import (
    AssetDescriptorV1,
    HealthCheckV1,
    HealthV1,
    ScanCountsV1,
    ScanManifestV1,
    wire_dump,
)
from stockscout_eod.health import assert_public_safe
from stockscout_eod.jsonio import atomic_write_json, canonical_json_bytes, sha256_bytes, write_json

from .contracts import MODE_SPECS, ModeId, ModePointerV1, UnifiedManifestV1

DETAIL_BUCKETS = 128
HISTORY_LIMIT = 20
PUBLIC_BASE_URL = "https://garrincha077.github.io/StockScout-Unified"
GROUP_MODEL = "behavioral-proxy-v2-confidence"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if parsed == parsed and abs(parsed) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _bucket(ticker: str) -> str:
    value = sum((index + 1) * ord(char) for index, char in enumerate(ticker.upper()))
    return f"{value % DETAIL_BUCKETS:03d}"


def _descriptor(path: str, payload: bytes, count: int) -> AssetDescriptorV1:
    return AssetDescriptorV1(path=path, sha256=sha256_bytes(payload), bytes=len(payload), count=count)


def _aggregate_descriptor(path: str, files: list[tuple[str, bytes]], count: int) -> AssetDescriptorV1:
    aggregate = canonical_json_bytes(
        [{"path": name, "sha256": sha256_bytes(payload), "bytes": len(payload)} for name, payload in files]
    )
    return AssetDescriptorV1(
        path=path,
        sha256=sha256_bytes(aggregate),
        bytes=sum(len(payload) for _, payload in files),
        count=count,
        pattern="{bucket}.json",
        bucketCount=DETAIL_BUCKETS,
    )


def _document_id(run_id: str, mode: ModeId, ticker: str) -> str:
    return f"scan:{run_id}:mode:{mode}:candidate:{ticker}"


def _canonical_url(run_id: str, mode: ModeId, ticker: str) -> str:
    return (
        f"{PUBLIC_BASE_URL}/ticker/{quote(ticker, safe='.-')}"
        f"?run={quote(run_id, safe='._-')}&mode={mode}"
    )


def _safe_detail(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep complete scalar evidence and bounded detector objects, never embedded bars/caches."""
    blocked = {
        "bars", "ohlcv", "chart_data", "chartdata", "chart_payload", "chartpayload",
        "daily_bars", "weekly_bars", "provider_cache", "duckdb_path", "price_data",
        "rs_series", "historical_data", "history_frame", "raw_frame", "dataframe",
    }

    def clean(value: Any, key: str = "") -> Any:
        normalized = key.lower().replace("-", "_")
        if normalized in blocked:
            return None
        if isinstance(value, Mapping):
            return {
                str(child_key): cleaned
                for child_key, child_value in value.items()
                if (cleaned := clean(child_value, str(child_key))) is not None
            }
        if isinstance(value, list):
            return [clean(item) for item in value[:500]]
        if isinstance(value, tuple):
            return [clean(item) for item in value[:500]]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    result = cast(dict[str, Any], clean(dict(row)))
    assert_public_safe(result)
    return result


def _validated_groups(value: Any, *, universe: int) -> dict[str, Any]:
    """Validate the aggregate Next group board before making it public."""
    if not isinstance(value, Mapping):
        raise ValueError("next group aggregate is missing")
    if value.get("method") != GROUP_MODEL:
        raise ValueError(f"next group model must be {GROUP_MODEL}")
    result = _safe_detail(value)
    for coverage_key in ("sectorCoverage", "industryCoverage"):
        coverage = result.get(coverage_key)
        if not isinstance(coverage, int) or isinstance(coverage, bool) or not 0 < coverage <= universe:
            raise ValueError(f"next groups {coverage_key} must be within 1..{universe}")
    for collection_key in ("sectors", "industries"):
        collection = result.get(collection_key)
        if not isinstance(collection, list) or not collection:
            raise ValueError(f"next groups {collection_key} is empty")
        for index, row in enumerate(collection):
            if not isinstance(row, Mapping):
                raise ValueError(f"next groups {collection_key}[{index}] is invalid")
            if not str(row.get("ticker") or "").strip() or not str(row.get("name") or "").strip():
                raise ValueError(f"next groups {collection_key}[{index}] has no ticker or name")
            rank = _number(row.get("rank"), -1)
            if not 0 <= rank <= 100:
                raise ValueError(f"next groups {collection_key}[{index}] rank is outside 0..100")
            if not isinstance(row.get("topTickers"), list):
                raise ValueError(f"next groups {collection_key}[{index}] has no topTickers")
    return result


def _validated_context(path: str | Path, *, kind: str) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schemaVersion") != 1:
        raise ValueError(f"{kind} context must use schemaVersion 1")
    if kind == "factorRegime":
        factors = payload.get("factors")
        impact = (payload.get("method") or {}).get("stockScoutImpact") if isinstance(payload.get("method"), Mapping) else None
        if not isinstance(factors, list) or len(factors) != 6 or not str(impact or "").startswith("none;"):
            raise ValueError("factor regime must contain six read-only factors")
    elif kind == "gmliContext":
        contract = payload.get("consumerContract")
        if (
            payload.get("status") != "OK"
            or not isinstance(contract, Mapping)
            or contract.get("mode") != "READ_ONLY_SIDECAR"
            or contract.get("mutatesStockScoutScoring") is not False
        ):
            raise ValueError("GMLI context violates its read-only consumer contract")
    else:  # pragma: no cover - internal misuse guard
        raise ValueError(f"unsupported context kind: {kind}")
    result = _safe_detail(payload)
    assert_public_safe(result)
    return result


def _summary(row: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if value is None or isinstance(value, (str, int, float, bool)) or (isinstance(value, list) and len(value) <= 30 and all(
            item is None or isinstance(item, (str, int, float, bool)) for item in value
        )):
            result[str(key)] = value
    for key in ("tradePlan", "trade_plan", "legacyConfirmation"):
        value = row.get(key)
        if isinstance(value, Mapping):
            result[key] = _safe_detail(value)
    plan = row.get("trade_plan") or row.get("tradePlan")
    if isinstance(plan, Mapping):
        projections = {
            "trade_status": plan.get("status"),
            "trigger_state": plan.get("trigger_state") or plan.get("triggerState"),
            "entry_risk_pct": plan.get("entry_risk_pct") or plan.get("entryRiskPct"),
            "extension_atr": plan.get("extension_atr") or plan.get("extensionAtr"),
        }
        result.update({key: value for key, value in projections.items() if value is not None})
        price = _number(row.get("price"), float("nan"))
        trigger = _number(
            plan.get("trigger_reference_level") or plan.get("triggerReferenceLevel"),
            float("nan"),
        )
        atr = _number(row.get("atr20"), float("nan"))
        if price == price and trigger == trigger and trigger > 0:
            result["distance_to_trigger_pct"] = round((price - trigger) / trigger * 100.0, 4)
            if atr == atr and atr > 0:
                result["distance_to_trigger_atr"] = round((price - trigger) / atr, 4)
    return result


def attach_bottom_screener_asset(
    *,
    manifest: ScanManifestV1,
    public_dir: str | Path,
    raw_scan: Mapping[str, Any],
) -> ScanManifestV1:
    """Attach a lazy, hash-verified Bottom field registry without changing ranking."""
    if manifest.mode != "bottom-fishing":
        raise ValueError("bottomScreener is reserved for Bottom Fishing")
    candidates = [row for row in raw_scan.get("candidates") or [] if isinstance(row, Mapping)]
    if len(candidates) != manifest.counts.candidates:
        raise ValueError("bottomScreener candidate count does not match the published Bottom manifest")
    rows = [_summary(row) for row in candidates]
    fields = sorted({str(key) for row in rows for key in row})
    if len(fields) < 60:
        raise ValueError(f"bottomScreener exposes only {len(fields)} scalar fields; expected at least 60")
    payload = {
        "schemaVersion": "stockscout-unified/bottom-screener-v1",
        "runId": manifest.run_id,
        "sessionDate": manifest.session_date,
        "priceBasis": "split_only",
        "ranking": manifest.versions.get("ranking"),
        "fields": fields,
        "rows": rows,
    }
    mode_root = Path(public_dir).resolve() / "data" / "modes" / "bottom-fishing"
    relative = f"runs/{manifest.run_id}/bottom-screener.json"
    encoded = write_json(mode_root / relative, payload)
    assets = {**manifest.assets, "bottomScreener": _descriptor(relative, encoded, len(rows))}
    updated = manifest.model_copy(update={"assets": assets})
    assert_public_safe(wire_dump(updated))
    atomic_write_json(mode_root / "manifest.json", wire_dump(updated))
    return updated


def _ryan_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            bool(row.get("originalRunBuySignal")),
            bool(row.get("originalMarketQualifiedBuy")),
            _number(row.get("originalBuyScore")),
            _number(row.get("originalRR")),
            str(row.get("ticker") or ""),
        ),
        reverse=True,
    )
    projected: list[dict[str, Any]] = []
    for row in ordered:
        ticker = str(row.get("ticker") or "").strip().upper()
        original = row.get("originalEngine") if isinstance(row.get("originalEngine"), Mapping) else {}
        buy = original.get("buy") if isinstance(original, Mapping) and isinstance(original.get("buy"), Mapping) else {}
        tags: list[str] = []
        if row.get("originalRunBuySignal"):
            tags.append("Original buy")
        if row.get("originalRunSellSignal"):
            tags.append("Original sell")
        if row.get("originalTTPasses") and _number(row.get("originalTTPasses")) >= 7:
            tags.append("Minervini trend template")
        if _number(row.get("originalVcpQuality")) > 0:
            tags.append("VCP")
        reason = buy.get("sourceReason") or buy.get("reason") if isinstance(buy, Mapping) else None
        base = {
            key: value
            for key, value in row.items()
            if key.startswith("original")
            or key in {
                "ticker", "price", "stage", "stageName", "phaseConfidence", "sector", "industry",
                "rsRank", "rsScore", "rs3m", "rs6m", "volumeRatio", "change20d", "return3m",
                "return6m", "from52wHigh", "ema10d", "ema20d", "sma10w", "sma20w",
            }
        }
        base.update(
            {
                "ticker": ticker,
                "score": _number(row.get("originalBuyScore")),
                "setup": "Ryan Original buy" if row.get("originalRunBuySignal") else str(row.get("stageName") or "Ryan watch"),
                "primarySetup": "ryan_original_buy" if row.get("originalRunBuySignal") else "ryan_original_watch",
                "setupTags": tags,
                "reasons": [str(reason)] if reason else [],
                "tradeStatus": "insufficient_data",
                "riskLevel": "original_methodology",
            }
        )
        projected.append(base)
    return projected


def _history(mode_root: Path, current: dict[str, Any]) -> list[dict[str, Any]]:
    prior: list[dict[str, Any]] = []
    manifest_path = mode_root / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            history_path = mode_root / str((manifest.get("assets") or {}).get("history", {}).get("path") or "")
            payload = json.loads(history_path.read_text(encoding="utf-8"))
            prior = list(payload.get("sessions") or [])
        except (OSError, ValueError, TypeError):
            prior = []
    deduped = [current]
    deduped.extend(item for item in prior if item.get("runId") != current["runId"])
    return deduped[:HISTORY_LIMIT]


def publish_adjusted_mode(
    *,
    mode: ModeId,
    canonical_path: str | Path,
    chart_dir: str | Path,
    public_dir: str | Path,
    run_id: str,
    session_date: str,
    min_chart_coverage_pct: float = 95.0,
    factor_regime_path: str | Path | None = None,
    gmli_context_path: str | Path | None = None,
    source_commit: str | None = None,
) -> ScanManifestV1:
    """Project the Next canonical audit snapshot into either Next or Ryan mode."""
    if mode not in {"next", "ryan-original"}:
        raise ValueError("publish_adjusted_mode only accepts next or ryan-original")
    if source_commit is not None and mode != "next":
        raise ValueError("source_commit override is reserved for immutable Next recovery")
    effective_source_commit = source_commit or MODE_SPECS[mode].source_commit
    if not re.fullmatch(r"[0-9a-f]{40}", effective_source_commit):
        raise ValueError("adjusted source commit must be a full lowercase Git SHA")
    canonical = json.loads(Path(canonical_path).read_text(encoding="utf-8"))
    source_rows = [dict(row) for row in canonical.get("universe") or [] if isinstance(row, Mapping)]
    if not source_rows:
        raise ValueError("adjusted canonical snapshot has no universe")
    market_date = str((canonical.get("market") or {}).get("scanDate") or session_date)
    if market_date != session_date:
        raise ValueError(f"{mode} market date {market_date} does not match {session_date}")
    generated_at = str(canonical.get("generatedAt") or datetime.now(tz=UTC).isoformat())
    rows = source_rows if mode == "next" else _ryan_rows(source_rows)
    chart_mapping = {
        str(ticker).strip().upper(): str(shard)
        for ticker, shard in (canonical.get("chartShards") or {}).items()
    }
    tickers = [str(row.get("ticker") or "").strip().upper() for row in rows]
    if not all(tickers) or len(tickers) != len(set(tickers)):
        raise ValueError(f"{mode} contains missing or duplicate tickers")

    public_root = Path(public_dir).resolve()
    mode_root = public_root / "data" / "modes" / mode
    runs_root = mode_root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    final_run = runs_root / run_id
    if final_run.exists():
        raise FileExistsError(f"immutable {mode} run already exists: {run_id}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=runs_root))
    try:
        summaries: list[dict[str, Any]] = []
        details: dict[str, dict[str, Any]] = {}
        for index, source in enumerate(rows):
            ticker = str(source.get("ticker") or "").strip().upper()
            summary = _summary(source)
            summary.update(
                {
                    "id": _document_id(run_id, mode, ticker),
                    "canonicalUrl": _canonical_url(run_id, mode, ticker),
                    "ticker": ticker,
                    "mode": mode,
                    "priceBasis": "split_div",
                    "asOf": session_date,
                    "scanOrder": index,
                    "excluded": False,
                }
            )
            summaries.append(summary)
            detail = _safe_detail(source)
            detail.update(summary)
            details[ticker] = detail

        run_relative = f"runs/{run_id}"
        core = {
            "schemaVersion": "stockscout-unified/core-v1",
            "runId": run_id,
            "sessionDate": session_date,
            "marketDataDate": session_date,
            "generatedAt": generated_at,
            "mode": mode,
            "priceBasis": "split_div",
            "market": _safe_detail(canonical.get("market") or {}),
            "universe": summaries,
            "detailShards": {ticker: _bucket(ticker) for ticker in tickers},
            "chartShards": chart_mapping,
        }
        if mode == "next":
            core["groups"] = _validated_groups(canonical.get("groups"), universe=len(rows))
        core_bytes = write_json(temporary / "core.json", core)
        excluded_bytes = write_json(
            temporary / "excluded.json",
            {"schemaVersion": "stockscout-unified/excluded-v1", "runId": run_id, "rows": []},
        )
        current_history = {
            "runId": run_id,
            "sessionDate": session_date,
            "generatedAt": generated_at,
            "status": "healthy",
            "candidateCount": len(summaries),
            "excludedCount": 0,
        }
        history = {"schemaVersion": "stockscout-unified/history-v1", "sessions": _history(mode_root, current_history)}
        history_bytes = write_json(temporary / "history.json", history)

        buckets: dict[str, dict[str, Any]] = {f"{index:03d}": {} for index in range(DETAIL_BUCKETS)}
        for ticker, detail in details.items():
            buckets[_bucket(ticker)][ticker] = detail
        detail_files: list[tuple[str, bytes]] = []
        for bucket, payload in buckets.items():
            data = write_json(temporary / "details" / f"{bucket}.json", payload)
            detail_files.append((f"{bucket}.json", data))

        source_chart_root = Path(chart_dir)
        chart_files: list[tuple[str, bytes]] = []
        for shard in sorted(set(chart_mapping.values())):
            source = source_chart_root / shard
            if not source.exists():
                continue
            destination = temporary / "charts" / shard
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            chart_files.append((shard, destination.read_bytes()))
        available = sum(1 for ticker in tickers if chart_mapping.get(ticker) in {name for name, _ in chart_files})
        chart_coverage = round(100.0 * available / max(1, len(tickers)), 2)
        if chart_coverage < min_chart_coverage_pct:
            raise ValueError(
                f"{mode} chart coverage is {chart_coverage:.2f}%, expected at least {min_chart_coverage_pct:.2f}%"
            )

        health = HealthV1(
            status="healthy",
            coveragePct=100.0,
            checks=[
                HealthCheckV1(code="session_date_alignment", passed=True, detail=session_date),
                HealthCheckV1(code="price_basis", passed=True, detail="split_div"),
                HealthCheckV1(code="unique_tickers", passed=True, detail=f"total={len(tickers)}"),
                HealthCheckV1(code="chart_coverage", passed=True, detail=f"coverage={chart_coverage:.2f}% required>={min_chart_coverage_pct:.2f}%"),
            ],
        )
        assets: dict[str, AssetDescriptorV1] = {
            "core": _descriptor(f"{run_relative}/core.json", core_bytes, len(summaries)),
            "excluded": _descriptor(f"{run_relative}/excluded.json", excluded_bytes, 0),
            "history": _descriptor(f"{run_relative}/history.json", history_bytes, len(history["sessions"])),
            "details": _aggregate_descriptor(f"{run_relative}/details", detail_files, len(details)),
            "charts": AssetDescriptorV1(
                path=f"{run_relative}/charts",
                sha256=sha256_bytes(canonical_json_bytes([
                    {"path": name, "sha256": sha256_bytes(data), "bytes": len(data)}
                    for name, data in chart_files
                ])),
                bytes=sum(len(data) for _, data in chart_files),
                count=available,
                pattern="{bucket}.json",
                bucketCount=int(canonical.get("chartShardCount") or 128),
                coveragePct=chart_coverage,
            ),
        }
        if mode == "next":
            for name, source in (("factorRegime", factor_regime_path), ("gmliContext", gmli_context_path)):
                if source is None:
                    continue
                context = _validated_context(source, kind=name)
                filename = "factor-regime.json" if name == "factorRegime" else "gmli-context.json"
                payload = write_json(temporary / "contexts" / filename, context)
                count = len(context.get("factors") or []) if name == "factorRegime" else 1
                assets[name] = _descriptor(f"{run_relative}/contexts/{filename}", payload, count)
        manifest = ScanManifestV1(
            mode=mode,
            runId=run_id,
            sessionDate=session_date,
            marketDataDate=session_date,
            generatedAt=generated_at,
            status="healthy",
            priceMode="split_div",
            chartStatus="ready",
            counts=ScanCountsV1(universe=len(rows), candidates=len(rows), excluded=0, failed=0),
            health=health,
            provenance={
                "source": "vendored-stockscreener-next",
                "sourceCommit": effective_source_commit,
                "ranking": MODE_SPECS[mode].ranking,
                "priceBasis": "split_div",
                "affectsOtherModes": False,
            },
            versions={
                "ranking": MODE_SPECS[mode].ranking,
                "detectors": effective_source_commit,
                "tradePlan": "not-applicable",
            },
            assets=assets,
        )
        assert_public_safe(wire_dump(manifest))
        os.replace(temporary, final_run)
        atomic_write_json(mode_root / "manifest.json", wire_dump(manifest))
        return manifest
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def activate_unified(*, public_dir: str | Path, run_id: str, session_date: str) -> UnifiedManifestV1:
    """Activate only when all three immutable mode manifests agree and are healthy."""
    data_root = Path(public_dir).resolve() / "data"
    pointers: dict[ModeId, ModePointerV1] = {}
    generated: list[str] = []
    for mode, spec in MODE_SPECS.items():
        path = data_root / "modes" / mode / "manifest.json"
        payload = path.read_bytes()
        manifest = ScanManifestV1.model_validate_json(payload)
        if manifest.mode != mode or manifest.run_id != run_id or manifest.session_date != session_date:
            raise ValueError(f"{mode} manifest identity does not match unified run")
        if manifest.status != "healthy" or manifest.health.status != "healthy":
            raise ValueError(f"{mode} is not healthy")
        if manifest.price_mode != spec.price_basis:
            raise ValueError(f"{mode} price basis is {manifest.price_mode}, expected {spec.price_basis}")
        generated.append(manifest.generated_at)
        chart = manifest.assets.get("charts")
        pointers[mode] = ModePointerV1(
            mode=mode,
            label=spec.label,
            priceBasis=spec.price_basis,
            status="healthy",
            manifestPath=f"modes/{mode}/manifest.json",
            manifestSha256=sha256_bytes(payload),
            manifestBytes=len(payload),
            candidates=manifest.counts.candidates,
            excluded=manifest.counts.excluded,
            chartCoveragePct=chart.coverage_pct if chart and chart.coverage_pct is not None else 0.0,
            sourceCommit=spec.source_commit,
            ranking=spec.ranking,
        )
    manifest = UnifiedManifestV1(
        runId=run_id,
        sessionDate=session_date,
        generatedAt=max(generated),
        status="healthy",
        modes=pointers,
    )
    atomic_write_json(data_root / "manifest.json", manifest.model_dump(mode="json", by_alias=True))
    # Pages retains one full immutable run per mode. Compact history has already
    # been copied into the active run's history.json, so restored prior run
    # directories are no longer needed in the deployment artifact.
    for mode in MODE_SPECS:
        runs_root = (data_root / "modes" / mode / "runs").resolve()
        for child in runs_root.iterdir():
            if child.name == run_id or not child.is_dir() or child.parent.resolve() != runs_root:
                continue
            shutil.rmtree(child)
    return manifest
