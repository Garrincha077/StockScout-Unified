"""Build and atomically activate immutable public scan assets."""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, url2pathname, urlopen

from stock_scout.scoring.focus_blend import blend_from_parts, in_stage_2
from stock_scout.scoring.trade_plan import derive_trade_plan
from stockscout_eod.contracts import (
    AssetDescriptorV1,
    CandidateDetailV1,
    CandidateSummaryV1,
    ChartManifestV1,
    RawScanEnvelopeV1,
    ScanCountsV1,
    ScanManifestV1,
    TradePlanV1,
    wire_dump,
)
from stockscout_eod.health import assert_public_safe, evaluate_scan_health, require_healthy
from stockscout_eod.jsonio import atomic_write_json, canonical_json_bytes, sha256_bytes, write_json
from stockscout_eod.legacy import observed_confirmation

DETAIL_BUCKETS = 128
HISTORY_LIMIT = 252
DEFAULT_PUBLIC_BASE_URL = "https://garrincha077.github.io/StockScout-Unified"


def _descriptor(path: str, payload: bytes, count: int) -> AssetDescriptorV1:
    return AssetDescriptorV1(
        path=path,
        sha256=sha256_bytes(payload),
        bytes=len(payload),
        count=count,
    )


def _load_chart_manifest(
    path: str | Path | None,
    *,
    scan: RawScanEnvelopeV1,
    status: str,
) -> tuple[ChartManifestV1 | None, bytes | None]:
    if status not in {"ready", "stale", "missing"}:
        raise ValueError("chart_status must be ready, stale, or missing")
    if path is None:
        if status == "ready":
            raise ValueError("chart_status=ready requires a chart manifest")
        return None, None
    payload = Path(path).read_bytes()
    manifest = ChartManifestV1.model_validate_json(payload)
    if (
        manifest.run_id != scan.run_id
        or manifest.session_date != scan.session_date
        or manifest.generated_at != scan.generated_at
    ):
        raise ValueError("chart manifest does not belong to the scan being published")
    if manifest.requested != len(scan.candidates) + len(scan.excluded):
        raise ValueError("chart manifest requested count does not match the scan")
    if manifest.available != len(manifest.shards_by_ticker):
        raise ValueError("chart manifest available count does not match ticker mapping")
    storage_url = urlsplit(manifest.storage_base_url)
    expected_suffixes = (
        f"/storage/v1/object/public/stockscout-eod-charts/{scan.run_id}",
        f"/runs/{scan.run_id}/charts",
    )
    if (
        storage_url.scheme != "https"
        or not storage_url.hostname
        or storage_url.username
        or storage_url.password
        or storage_url.query
        or storage_url.fragment
        or not storage_url.path.endswith(expected_suffixes)
    ):
        raise ValueError("chart manifest storage URL is not a safe public run URL")
    shard_names = {shard.name for shard in manifest.shards}
    if len(shard_names) != len(manifest.shards):
        raise ValueError("chart manifest contains duplicate shard names")
    if not set(manifest.shards_by_ticker.values()).issubset(shard_names):
        raise ValueError("chart manifest ticker mapping references an unknown shard")
    if status == "ready" and manifest.coverage_pct != 100.0:
        raise ValueError("chart_status=ready requires 100% chart coverage")
    return manifest, payload


def _bucket(ticker: str, count: int = DETAIL_BUCKETS) -> str:
    digest = sha256_bytes(ticker.strip().upper().encode("utf-8"))
    return f"{int(digest[:8], 16) % count:03d}"


def _trade_plan(row: Mapping[str, Any]) -> TradePlanV1 | None:
    raw = row.get("trade_plan") or row.get("tradePlan")
    if not isinstance(raw, Mapping):
        raw = derive_trade_plan(row).model_dump(mode="json")
    try:
        return TradePlanV1.model_validate(raw)
    except (TypeError, ValueError):
        return None


def _legacy_map(sidecar: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not sidecar:
        return {}
    raw = sidecar.get("candidates") or sidecar.get("byTicker") or sidecar
    if isinstance(raw, Mapping):
        return {
            str(ticker).strip().upper(): value
            for ticker, value in raw.items()
            if isinstance(value, Mapping)
        }
    if isinstance(raw, list):
        return {
            str(row.get("ticker") or "").strip().upper(): row
            for row in raw
            if isinstance(row, Mapping) and row.get("ticker")
        }
    return {}


def _setup_projection(row: Mapping[str, Any]) -> tuple[list[str], dict[str, str | None]]:
    setups = row.get("setups")
    if not isinstance(setups, Mapping):
        return [], {}
    names: list[str] = []
    states: dict[str, str | None] = {}
    for name, value in setups.items():
        if not isinstance(value, Mapping):
            continue
        if value.get("triggered") is True:
            names.append(str(name))
        states[str(name)] = value.get("sub_state") or value.get("subState")
    return sorted(names), states


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _distance(price: Any, average: Any) -> float | None:
    price_value = _number(price)
    average_value = _number(average)
    if price_value is None or average_value is None or average_value <= 0:
        return None
    return round((price_value - average_value) / average_value * 100.0, 2)


def _trend_template_passes(row: Mapping[str, Any]) -> int | None:
    setups = row.get("setups")
    minervini = setups.get("minervini") if isinstance(setups, Mapping) else None
    raw = minervini.get("raw_features") if isinstance(minervini, Mapping) else None
    conditions = raw.get("conditions") if isinstance(raw, Mapping) else None
    if not isinstance(conditions, Mapping):
        return None
    return sum(value is True for value in conditions.values())


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _flat_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    return {_camel_key(str(key)): value for key, value in row.items()}


def _candidate_blend(row: Mapping[str, Any]) -> float:
    """Evaluate the frozen blend against its serialized Candidate inputs.

    ``candidate_blend_score`` intentionally consumes a ``Candidate`` object via
    attributes.  Public scan envelopes contain the same values serialized as
    mappings, so passing the mapping directly would silently turn every blend
    input into zero.  Calling the frozen ``blend_from_parts`` primitive keeps
    the weights and category mapping identical without changing scan order.
    """

    breakdown = row.get("score_breakdown")
    setup_quality = (
        breakdown.get("setup_quality") if isinstance(breakdown, Mapping) else 0.0
    )
    return blend_from_parts(
        score=row.get("score"),
        rs_rating=row.get("rs_rating"),
        setup_quality=setup_quality,
        actionability=row.get("actionability"),
    )


def _headline_order(rows: list[dict[str, Any]], *, limit: int = 10) -> dict[str, int]:
    pool = [row for row in rows if in_stage_2(row)]
    if not pool:
        pool = list(rows)
    ranked = sorted(pool, key=_candidate_blend, reverse=True)[:limit]
    return {
        str(row.get("ticker") or "").strip().upper(): index + 1
        for index, row in enumerate(ranked)
    }


def _summary(
    row: Mapping[str, Any],
    *,
    scan: RawScanEnvelopeV1,
    scan_order: int,
    excluded: bool,
    headline_order: Mapping[str, int],
    confirmation: Mapping[str, Any],
    public_base_url: str,
    document_mode: str | None = None,
) -> CandidateSummaryV1:
    ticker = str(row.get("ticker") or "").strip().upper()
    plan = _trade_plan(row)
    setup_names, setup_states = _setup_projection(row)
    score = _number(row.get("score")) or 0.0
    breakdown = row.get("score_breakdown")
    breakdown = breakdown if isinstance(breakdown, Mapping) else {}
    setups = row.get("setups")
    setups = setups if isinstance(setups, Mapping) else {}
    setup_scores = [
        _number(value.get("score"))
        for value in setups.values()
        if isinstance(value, Mapping) and value.get("triggered") is True
    ]
    trigger_scores = [value for value in setup_scores if value is not None]
    price = _number(row.get("price"))
    atr = _number(row.get("atr20"))
    trigger = plan.trigger_reference_level if plan else None
    breakout_pct = _distance(price, trigger)
    return CandidateSummaryV1(
        id=_candidate_id(scan.run_id, ticker, document_mode),
        mode=document_mode,
        priceBasis=scan.price_mode,
        canonicalUrl=_canonical_url(scan.run_id, ticker, public_base_url, document_mode),
        ticker=ticker,
        asOf=str(row.get("as_of") or scan.session_date),
        scanOrder=scan_order,
        score=score,
        focusScore=row.get("focus_score"),
        focusBlend=round(_candidate_blend(row), 4),
        headlineRank=headline_order.get(ticker),
        price=price,
        sector=row.get("sector"),
        industry=row.get("industry"),
        actionability=str(row.get("actionability") or "watch"),
        primarySetup=row.get("primary_setup"),
        setupNames=setup_names,
        setupTags=setup_names,
        setupStates=setup_states,
        tradePlan=plan,
        tradeStatus=plan.status if plan else None,
        entryRiskPct=plan.entry_risk_pct if plan else None,
        tacticalStopLevel=plan.tactical_stop_level if plan else None,
        riskLevel=str(row.get("risk_level") or "none"),
        riskReasons=row.get("risk_reasons") or [],
        excluded=excluded,
        excludedReason=row.get("excluded_reason") if excluded else None,
        dataStatus=str(row.get("data_status") or "OK"),
        legacyConfirmationStatus=str(confirmation["status"]),
        setup=row.get("primary_setup"),
        setupMatchCount=len(setup_names),
        opportunityScore=score,
        opportunityRank=scan_order + 1,
        opportunityTier=str(row.get("actionability") or "watch"),
        stage=row.get("weinstein_stage"),
        stageName=row.get("weinstein_substage"),
        rsRank=row.get("rs_rating"),
        rsScore=(
            row.get("rs_score_weighted")
            or row.get("rs_score_6m")
            or row.get("rs_score_3m")
        ),
        rs3m=row.get("rs_score_3m"),
        rs6m=row.get("rs_score_6m"),
        rsNewHigh=row.get("rs_new_high_before_price"),
        change20d=row.get("ret_1m_pct"),
        return3m=row.get("ret_3m_pct"),
        return6m=row.get("ret_6m_pct"),
        volumeRatio=row.get("volume_ratio_50d") or row.get("rvol_today"),
        avgDollarVolume50=row.get("avg_dollar_volume_50d"),
        atrPct=round(atr / price * 100.0, 2) if atr is not None and price else None,
        distance50=_distance(price, row.get("sma50")),
        distance200=_distance(price, row.get("sma200")),
        distance30w=row.get("weinstein_ext_pct"),
        from52wHigh=row.get("distance_to_52w_high_pct"),
        trendTemplatePasses=_trend_template_passes(row),
        baseWeeks=(row.get("weekly_base_length_bars") or row.get("base_age_weeks")),
        baseDepthPct=row.get("base_depth_pct"),
        breakoutPct=breakout_pct,
        extended=(
            (plan is not None and plan.status == "wait_for_retest")
            or row.get("actionability") == "extended_too_late"
        ),
        sma50=row.get("sma50"),
        sma150=row.get("sma150"),
        sma200=row.get("sma200"),
        structureScore=breakdown.get("trend"),
        baseScore=row.get("base_quality_score"),
        triggerScore=max(trigger_scores) if trigger_scores else None,
        accumulationScore=row.get("accumulation_score"),
        longBaseScore=row.get("long_base_score"),
        crashBaseScore=row.get("crash_base_score"),
        rwbSqueezeScore=row.get("rwb_squeeze_score"),
        emaStackLaunchScore=row.get("ema_stack_launch_score"),
        maClusterScore=row.get("ma_cluster_score"),
        smaCompressionPct=row.get("sma_compression_pct"),
        maClusterWidthPct=row.get("ma_cluster_width_pct"),
        fundamentalEvidenceScore=row.get("fundamental_evidence_score"),
        fundamentalEvidenceConfidence=row.get("fundamental_evidence_confidence"),
        changedToday=row.get("changed_today"),
        changeImpact=row.get("change_impact"),
        changeLabels=row.get("change_labels") or [],
    )


def _candidate_id(run_id: str, ticker: str, mode: str | None = None) -> str:
    if mode:
        return f"scan:{run_id}:mode:{mode}:candidate:{ticker}"
    return f"scan:{run_id}:candidate:{ticker}"


def _canonical_url(
    run_id: str, ticker: str, public_base_url: str, mode: str | None = None
) -> str:
    base = public_base_url.strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("public_base_url must be an absolute HTTPS URL")
    query = f"run={quote(run_id, safe='._-')}"
    if mode:
        query += f"&mode={quote(mode, safe='-')}"
    return f"{base}/ticker/{quote(ticker, safe='.-')}?{query}"


def _build_rows(
    scan: RawScanEnvelopeV1,
    legacy: Mapping[str, Any] | None,
    *,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    document_mode: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    legacy_by_ticker = _legacy_map(legacy)
    headline_order = _headline_order(scan.candidates)
    summaries: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    excluded_rows: list[dict[str, Any]] = []
    legacy_rows: dict[str, Any] = {}

    for scan_order, source in enumerate(scan.candidates):
        row = dict(source)
        ticker = str(row.get("ticker") or "").strip().upper()
        confirmation = observed_confirmation(
            legacy_by_ticker.get(ticker)
            or (row.get("legacyConfirmation") if isinstance(row.get("legacyConfirmation"), Mapping) else None)
        )
        summary = _summary(
            row,
            scan=scan,
            scan_order=scan_order,
            excluded=False,
            headline_order=headline_order,
            confirmation=confirmation,
            public_base_url=public_base_url,
            document_mode=document_mode,
        )
        summary_row = wire_dump(summary)
        detail_payload = {
            **_flat_candidate(row),
            **summary_row,
            "setupHits": row.get("setups") or {},
            "legacyConfirmation": confirmation,
        }
        detail = CandidateDetailV1.model_validate(detail_payload)
        summaries.append(summary_row)
        details[ticker] = wire_dump(detail)
        legacy_rows[ticker] = confirmation

    offset = len(summaries)
    for index, source in enumerate(scan.excluded):
        row = dict(source)
        ticker = str(row.get("ticker") or "").strip().upper()
        confirmation = observed_confirmation(legacy_by_ticker.get(ticker))
        summary = _summary(
            row,
            scan=scan,
            scan_order=offset + index,
            excluded=True,
            headline_order=headline_order,
            confirmation=confirmation,
            public_base_url=public_base_url,
            document_mode=document_mode,
        )
        summary_row = wire_dump(summary)
        excluded_rows.append(
            wire_dump(
                CandidateDetailV1.model_validate(
                    {
                        **_flat_candidate(row),
                        **summary_row,
                        "setupHits": row.get("setups") or {},
                        "legacyConfirmation": confirmation,
                    }
                )
            )
        )
        legacy_rows[ticker] = confirmation

    return summaries, details, excluded_rows, legacy_rows


def _load_json_location(location: str) -> tuple[dict[str, Any], str]:
    if location.startswith(("https://", "http://")):
        request = Request(location, headers={"User-Agent": "StockScout-EOD/0.1"})
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")), location
    if location.startswith("file://"):
        parsed = urlsplit(location)
        path = Path(url2pathname(parsed.path)).resolve()
        return json.loads(path.read_text(encoding="utf-8")), path.as_uri()
    path = Path(location).resolve()
    return json.loads(path.read_text(encoding="utf-8")), path.as_uri()


def _previous_history(previous_manifest: str | None) -> list[dict[str, Any]]:
    if not previous_manifest:
        return []
    try:
        manifest, base = _load_json_location(previous_manifest)
        descriptor = (manifest.get("assets") or {}).get("history") or {}
        asset_path = descriptor.get("path")
        if not asset_path:
            return []
        history, _ = _load_json_location(urljoin(base, str(asset_path)))
        payload = canonical_json_bytes(history)
        expected = descriptor.get("sha256")
        if expected and sha256_bytes(payload) != expected:
            return []
        sessions = history.get("sessions") if isinstance(history, Mapping) else None
        return [dict(row) for row in sessions or [] if isinstance(row, Mapping)]
    except (OSError, ValueError, TimeoutError):
        return []


def _previous_summaries(previous_manifest: str | None) -> dict[str, dict[str, Any]]:
    if not previous_manifest:
        return {}
    try:
        manifest, base = _load_json_location(previous_manifest)
        descriptor = (manifest.get("assets") or {}).get("core") or {}
        asset_path = descriptor.get("path")
        if not asset_path:
            return {}
        core, _ = _load_json_location(urljoin(base, str(asset_path)))
        payload = canonical_json_bytes(core)
        expected = descriptor.get("sha256")
        if expected and sha256_bytes(payload) != expected:
            return {}
        rows = core.get("universe") if isinstance(core, Mapping) else None
        return {
            str(row.get("ticker") or "").strip().upper(): dict(row)
            for row in rows or []
            if isinstance(row, Mapping) and row.get("ticker")
        }
    except (OSError, ValueError, TimeoutError):
        return {}


def _annotate_changes(
    summaries: list[dict[str, Any]],
    details: dict[str, dict[str, Any]],
    previous_manifest: str | None,
) -> None:
    previous = _previous_summaries(previous_manifest)
    if not previous:
        return
    for row in summaries:
        ticker = str(row.get("ticker") or "").strip().upper()
        prior = previous.get(ticker)
        labels: list[str] = []
        impact = 0.0
        is_new = prior is None
        if is_new:
            labels.append("New candidate")
            impact = 100.0
        else:
            for field, label, weight in (
                ("primarySetup", "Primary setup changed", 40.0),
                ("tradeStatus", "Trade status changed", 35.0),
                ("actionability", "Actionability changed", 25.0),
                ("riskLevel", "Risk flag changed", 20.0),
            ):
                if row.get(field) != prior.get(field):
                    labels.append(label)
                    impact += weight
            current_blend = _number(row.get("focusBlend"))
            prior_blend = _number(prior.get("focusBlend"))
            if (
                current_blend is not None
                and prior_blend is not None
                and abs(current_blend - prior_blend) >= 5.0
            ):
                labels.append(f"Focus blend {current_blend - prior_blend:+.1f}")
                impact += min(20.0, abs(current_blend - prior_blend))
        row["newUniverseMember"] = is_new
        row["changedToday"] = bool(labels)
        row["changeLabels"] = labels
        row["changeImpact"] = round(min(100.0, impact), 2)
        if ticker in details:
            details[ticker].update(
                {
                    "newUniverseMember": row["newUniverseMember"],
                    "changedToday": row["changedToday"],
                    "changeLabels": labels,
                    "changeImpact": row["changeImpact"],
                }
            )


def _history_payload(
    scan: RawScanEnvelopeV1,
    health_status: str,
    previous_manifest: str | None,
    limit: int = HISTORY_LIMIT,
) -> dict[str, Any]:
    prior = _previous_history(previous_manifest)
    current = {
        "runId": scan.run_id,
        "sessionDate": scan.session_date,
        "marketDataDate": str(scan.stats.get("market_data_latest_bar") or scan.session_date),
        "generatedAt": scan.generated_at,
        "status": health_status,
        "coveragePct": float(scan.stats.get("coverage_pct") or 0.0),
        "candidateCount": len(scan.candidates),
        "excludedCount": len(scan.excluded),
        "primaryProvider": scan.provenance.get("primaryProvider"),
    }
    by_session = {
        str(row.get("sessionDate")): row for row in prior if row.get("sessionDate")
    }
    by_session[scan.session_date] = current
    sessions = sorted(by_session.values(), key=lambda row: str(row["sessionDate"]), reverse=True)
    return {
        "schemaVersion": "stockscout-eod/history-v1",
        "generatedAt": scan.generated_at,
        "sessions": sessions[:limit],
    }


def _details_descriptor(
    run_relative: str,
    written: list[tuple[str, bytes]],
    count: int,
) -> AssetDescriptorV1:
    combined = canonical_json_bytes(
        [{"path": path, "sha256": sha256_bytes(payload), "bytes": len(payload)} for path, payload in written]
    )
    return AssetDescriptorV1(
        path=f"{run_relative}/details",
        sha256=sha256_bytes(combined),
        bytes=sum(len(payload) for _, payload in written),
        count=count,
        pattern="{bucket}.json",
        bucketCount=DETAIL_BUCKETS,
    )


def build_public_snapshot(
    scan: RawScanEnvelopeV1,
    *,
    public_dir: str | Path,
    legacy_sidecar: Mapping[str, Any] | None = None,
    previous_manifest: str | None = None,
    min_coverage_pct: float = 90.0,
    min_universe: int = 1000,
    allow_fixture: bool = False,
    chart_status: str = "missing",
    chart_manifest: str | Path | None = None,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    document_mode: str | None = None,
    data_subdir: str = "data",
    history_limit: int = HISTORY_LIMIT,
) -> ScanManifestV1:
    if not 1 <= history_limit <= HISTORY_LIMIT:
        raise ValueError(f"history_limit must be between 1 and {HISTORY_LIMIT}")
    health = evaluate_scan_health(
        scan,
        min_coverage_pct=min_coverage_pct,
        min_universe=min_universe,
        allow_fixture=allow_fixture,
    )
    require_healthy(health)
    charts, chart_manifest_bytes = _load_chart_manifest(
        chart_manifest, scan=scan, status=chart_status
    )
    summaries, details, excluded_rows, legacy_rows = _build_rows(
        scan,
        legacy_sidecar,
        public_base_url=public_base_url,
        document_mode=document_mode,
    )
    _annotate_changes(summaries, details, previous_manifest)
    assert_public_safe(
        {
            "summaries": summaries,
            "details": details,
            "excluded": excluded_rows,
            "legacy": legacy_rows,
        }
    )

    public_root = Path(public_dir).resolve()
    data_root = public_root / data_subdir
    runs_root = data_root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    final_run = runs_root / scan.run_id
    if final_run.exists():
        raise FileExistsError(f"immutable run already exists: {scan.run_id}")
    temporary_run = Path(tempfile.mkdtemp(prefix=f".{scan.run_id}-", dir=runs_root))
    run_relative = f"runs/{scan.run_id}"

    try:
        core = {
            "schemaVersion": "stockscout-eod/core-v1",
            "runId": scan.run_id,
            "sessionDate": scan.session_date,
            "marketDataDate": str(scan.stats.get("market_data_latest_bar") or scan.session_date),
            "generatedAt": scan.generated_at,
            "market": scan.market,
            "universe": summaries,
            "detailShards": {ticker: _bucket(ticker) for ticker in sorted(details)},
        }
        core_bytes = write_json(temporary_run / "core.json", core)
        excluded_bytes = write_json(
            temporary_run / "excluded.json",
            {
                "schemaVersion": "stockscout-eod/excluded-v1",
                "runId": scan.run_id,
                "rows": excluded_rows,
            },
        )
        history = _history_payload(scan, health.status, previous_manifest, history_limit)
        history_bytes = write_json(temporary_run / "history.json", history)
        legacy_bytes = write_json(
            temporary_run / "shadow" / "legacy-confirmation.json",
            {
                "schemaVersion": "stockscout-eod/legacy-confirmation-v1",
                "runId": scan.run_id,
                "affectsRanking": False,
                "candidates": legacy_rows,
            },
        )
        chart_index_bytes: bytes | None = None
        if charts is not None and chart_manifest_bytes is not None:
            chart_index_bytes = write_json(
                temporary_run / "charts" / "manifest.json",
                json.loads(chart_manifest_bytes),
            )

        bucket_rows: dict[str, dict[str, Any]] = {
            f"{index:03d}": {} for index in range(DETAIL_BUCKETS)
        }
        for ticker, detail in details.items():
            bucket_rows[_bucket(ticker)][ticker] = detail
        detail_files: list[tuple[str, bytes]] = []
        for bucket_name, rows in bucket_rows.items():
            path = f"{bucket_name}.json"
            payload = write_json(temporary_run / "details" / path, rows)
            detail_files.append((path, payload))

        assets = {
            "core": _descriptor(f"{run_relative}/core.json", core_bytes, len(summaries)),
            "excluded": _descriptor(
                f"{run_relative}/excluded.json", excluded_bytes, len(excluded_rows)
            ),
            "history": _descriptor(
                f"{run_relative}/history.json", history_bytes, len(history["sessions"])
            ),
            "details": _details_descriptor(run_relative, detail_files, len(details)),
            "legacyConfirmation": _descriptor(
                f"{run_relative}/shadow/legacy-confirmation.json",
                legacy_bytes,
                len(legacy_rows),
            ),
        }
        if charts is not None and chart_index_bytes is not None:
            assets["charts"] = AssetDescriptorV1(
                path=f"{run_relative}/charts/manifest.json",
                sha256=sha256_bytes(chart_index_bytes),
                bytes=len(chart_index_bytes),
                count=charts.available,
                coveragePct=charts.coverage_pct,
                pattern="shards/{bucket}.json.gz",
                bucketCount=len(charts.shards),
            )
        failed = int(scan.stats.get("tickers_failed_all_providers") or 0)
        universe = int(
            scan.stats.get("universe_size")
            or scan.stats.get("universe_pre_negcache")
            or len(summaries) + len(excluded_rows)
        )
        manifest = ScanManifestV1(
            mode=document_mode,
            runId=scan.run_id,
            sessionDate=scan.session_date,
            marketDataDate=str(scan.stats.get("market_data_latest_bar") or scan.session_date),
            generatedAt=scan.generated_at,
            status="healthy",
            priceMode=scan.price_mode,
            chartStatus=chart_status,
            counts=ScanCountsV1(
                universe=universe,
                candidates=len(summaries),
                excluded=len(excluded_rows),
                failed=failed,
            ),
            health=health,
            provenance={
                **scan.provenance,
                "publication": "github-pages-derived-snapshot",
                "rawOhlcvPublished": charts is not None,
                "chartPublication": (
                    "public-supabase-eod" if charts is not None else "unavailable"
                ),
            },
            versions=scan.versions,
            assets=assets,
        )
        assert_public_safe(wire_dump(manifest))
        os.replace(temporary_run, final_run)
        atomic_write_json(data_root / "manifest.json", wire_dump(manifest))
        return manifest
    except Exception:
        if temporary_run.exists():
            shutil.rmtree(temporary_run)
        raise


def load_legacy_sidecar(path: str | Path | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_public_snapshot(public_dir: str | Path) -> ScanManifestV1:
    public_root = Path(public_dir).resolve()
    data_root = public_root / "data"
    manifest = ScanManifestV1.model_validate_json(
        (data_root / "manifest.json").read_text(encoding="utf-8")
    )
    expected_files: set[Path] = set()
    for name, descriptor in manifest.assets.items():
        target = data_root / descriptor.path
        if name == "details":
            files = sorted(target.glob("*.json"))
            if len(files) != descriptor.bucket_count:
                raise ValueError(
                    f"details bucket count mismatch: {len(files)} != {descriptor.bucket_count}"
                )
            written = [(path.name, path.read_bytes()) for path in files]
            actual = _details_descriptor(
                f"runs/{manifest.run_id}", written, descriptor.count
            )
            if actual.sha256 != descriptor.sha256 or actual.bytes != descriptor.bytes:
                raise ValueError("details descriptor hash/bytes mismatch")
            row_count = 0
            expected_files.update(files)
            for path in files:
                rows = json.loads(path.read_text(encoding="utf-8"))
                row_count += len(rows)
                assert_public_safe(rows)
            if row_count != descriptor.count:
                raise ValueError(f"details cardinality mismatch: {row_count} != {descriptor.count}")
            continue
        payload = target.read_bytes()
        if sha256_bytes(payload) != descriptor.sha256 or len(payload) != descriptor.bytes:
            raise ValueError(f"asset mismatch: {name}")
        assert_public_safe(json.loads(payload.decode("utf-8")))
        expected_files.add(target)

    run_root = data_root / "runs" / manifest.run_id
    actual_files = {path for path in run_root.rglob("*") if path.is_file()}
    if actual_files != expected_files:
        unexpected = sorted(str(path.relative_to(run_root)) for path in actual_files - expected_files)
        missing = sorted(str(path.relative_to(run_root)) for path in expected_files - actual_files)
        raise ValueError(f"run asset allowlist mismatch; unexpected={unexpected}, missing={missing}")

    core = json.loads((data_root / manifest.assets["core"].path).read_text(encoding="utf-8"))
    excluded = json.loads(
        (data_root / manifest.assets["excluded"].path).read_text(encoding="utf-8")
    )
    if len(core.get("universe") or []) != manifest.counts.candidates:
        raise ValueError("core cardinality does not match manifest")
    if len(excluded.get("rows") or []) != manifest.counts.excluded:
        raise ValueError("excluded cardinality does not match manifest")
    assert_public_safe(wire_dump(manifest))
    return manifest
