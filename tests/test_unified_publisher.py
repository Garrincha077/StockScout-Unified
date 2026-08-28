from __future__ import annotations

import json
from pathlib import Path

from stockscout_eod.contracts import (
    AssetDescriptorV1,
    ChartManifestV1,
    ChartShardV1,
    HealthCheckV1,
    HealthV1,
    ScanCountsV1,
    ScanManifestV1,
    wire_dump,
)
from stockscout_eod.jsonio import canonical_json_bytes, sha256_bytes
from stockscout_unified.cli import verify
from stockscout_unified.publisher import (
    activate_unified,
    attach_bottom_screener_asset,
    publish_adjusted_mode,
)

RUN_ID = "2026-08-24-eod-test"
SESSION = "2026-08-24"


def groups(count: int) -> dict:
    row = {
        "ticker": "XLK",
        "name": "Technology",
        "rank": 92,
        "rel1m": 1.2,
        "rel3m": 4.1,
        "rel6m": 8.5,
        "stocks": count,
        "stage2Pct": 50.0,
        "earlyLeaders": 1,
        "medianOpportunity": 80.0,
        "avgConfidence": 75.0,
        "topTickers": ["AAA"],
    }
    return {
        "method": "behavioral-proxy-v2-confidence",
        "description": "fixture",
        "sectorCoverage": count,
        "industryCoverage": count,
        "averageConfidence": 75.0,
        "maxLeadershipAdjustmentPoints": 5.0,
        "sectors": [row],
        "industries": [{**row, "ticker": "IGV", "name": "Software"}],
    }


def descriptor(path: str, count: int = 1, coverage: float | None = None) -> AssetDescriptorV1:
    return AssetDescriptorV1(
        path=path,
        sha256="a" * 64,
        bytes=2,
        count=count,
        coveragePct=coverage,
    )


def write_bottom(public: Path, *, manifest_backed_charts: bool = False) -> None:
    root = public / "data" / "modes" / "bottom-fishing"
    run = root / "runs" / RUN_ID
    (run / "details").mkdir(parents=True)
    (run / "charts").mkdir(parents=True)
    for name, value in {
        "core.json": {"universe": [{"ticker": "BOT"}]},
        "excluded.json": {"rows": []},
        "history.json": {"sessions": []},
    }.items():
        (run / name).write_bytes(canonical_json_bytes(value))
    (run / "details" / "000.json").write_bytes(canonical_json_bytes({"BOT": {"ticker": "BOT"}}))
    charts = descriptor(f"runs/{RUN_ID}/charts", coverage=100)
    if manifest_backed_charts:
        shard_bytes = b"compact chart fixture"
        shard = ChartShardV1(
            name="000",
            sha256=sha256_bytes(shard_bytes),
            bytes=len(shard_bytes),
            tickerCount=1,
        )
        chart_index = ChartManifestV1(
            runId=RUN_ID,
            sessionDate=SESSION,
            generatedAt=f"{SESSION}T22:00:00Z",
            priceMode="split_only",
            requested=1,
            available=1,
            coveragePct=100,
            storageBaseUrl=f"https://fixture.invalid/runs/{RUN_ID}/charts",
            shards=[shard],
            shardsByTicker={"BOT": shard.name},
        )
        chart_index_bytes = canonical_json_bytes(wire_dump(chart_index))
        (run / "charts" / "manifest.json").write_bytes(chart_index_bytes)
        shard_path = run / "charts" / "shards" / f"{shard.name}.json.gz"
        shard_path.parent.mkdir()
        shard_path.write_bytes(shard_bytes)
        charts = AssetDescriptorV1(
            path=f"runs/{RUN_ID}/charts/manifest.json",
            sha256=sha256_bytes(chart_index_bytes),
            bytes=len(chart_index_bytes),
            count=1,
            coveragePct=100,
            pattern="shards/{bucket}.json.gz",
            bucketCount=1,
        )
    health = HealthV1(
        status="healthy",
        coveragePct=100,
        checks=[HealthCheckV1(code="fixture", passed=True, detail="healthy")],
    )
    manifest = ScanManifestV1(
        mode="bottom-fishing",
        runId=RUN_ID,
        sessionDate=SESSION,
        marketDataDate=SESSION,
        generatedAt=f"{SESSION}T22:00:00Z",
        status="healthy",
        priceMode="split_only",
        chartStatus="ready",
        counts=ScanCountsV1(universe=1, candidates=1, excluded=0, failed=0),
        health=health,
        provenance={"fixture": True},
        versions={"ranking": "frozen", "detectors": "frozen", "tradePlan": "v1"},
        assets={
            "core": descriptor(f"runs/{RUN_ID}/core.json"),
            "excluded": descriptor(f"runs/{RUN_ID}/excluded.json", 0),
            "history": descriptor(f"runs/{RUN_ID}/history.json", 0),
            "details": descriptor(f"runs/{RUN_ID}/details"),
            "charts": charts,
        },
    )
    (root / "manifest.json").write_bytes(canonical_json_bytes(wire_dump(manifest)))


def test_adjusted_modes_remain_isolated_and_activate_atomically(tmp_path: Path) -> None:
    public = tmp_path / "public"
    chart_dir = tmp_path / "charts"
    chart_dir.mkdir()
    (chart_dir / "000.json").write_bytes(canonical_json_bytes({"AAA": [], "BBB": []}))
    canonical = {
        "generatedAt": f"{SESSION}T22:00:00Z",
        "market": {"scanDate": SESSION},
        "chartShardCount": 1,
        "chartShards": {"AAA": "000.json", "BBB": "000.json"},
        "groups": groups(2),
        "universe": [
            {"ticker": "AAA", "price": 10, "score": 90, "opportunityScore": 90, "originalBuyScore": 61, "originalRunBuySignal": False, "originalEngine": {"buy": {"sourceReason": "watch"}}},
            {"ticker": "BBB", "price": 20, "score": 80, "opportunityScore": 80, "originalBuyScore": 95, "originalRunBuySignal": True, "originalEngine": {"buy": {"sourceReason": "buy"}}},
        ],
    }
    canonical_path = tmp_path / "latest.json"
    canonical_path.write_bytes(canonical_json_bytes(canonical))

    next_manifest = publish_adjusted_mode(mode="next", canonical_path=canonical_path, chart_dir=chart_dir, public_dir=public, run_id=RUN_ID, session_date=SESSION)
    ryan_manifest = publish_adjusted_mode(mode="ryan-original", canonical_path=canonical_path, chart_dir=chart_dir, public_dir=public, run_id=RUN_ID, session_date=SESSION)
    write_bottom(public)
    active = activate_unified(public_dir=public, run_id=RUN_ID, session_date=SESSION)

    assert set(active.modes) == {"bottom-fishing", "next", "ryan-original"}
    assert next_manifest.price_mode == ryan_manifest.price_mode == "split_div"
    next_core = json.loads((public / "data" / "modes" / "next" / next_manifest.assets["core"].path).read_text(encoding="utf-8"))
    ryan_core = json.loads((public / "data" / "modes" / "ryan-original" / ryan_manifest.assets["core"].path).read_text(encoding="utf-8"))
    assert [row["ticker"] for row in next_core["universe"]] == ["AAA", "BBB"]
    assert next_core["groups"]["sectorCoverage"] == 2
    assert next_core["groups"]["sectors"][0]["ticker"] == "XLK"
    assert "groups" not in ryan_core
    assert [row["ticker"] for row in ryan_core["universe"]] == ["BBB", "AAA"]
    assert next_core["universe"][0]["id"] == f"scan:{RUN_ID}:mode:next:candidate:AAA"
    assert ryan_core["universe"][0]["id"] == f"scan:{RUN_ID}:mode:ryan-original:candidate:BBB"
    root_bytes = (public / "data" / "modes" / "next" / "manifest.json").read_bytes()
    assert active.modes["next"].manifest_sha256 == sha256_bytes(root_bytes)


def test_activation_rejects_cross_mode_price_basis(tmp_path: Path) -> None:
    public = tmp_path / "public"
    write_bottom(public)
    root = public / "data" / "modes" / "bottom-fishing" / "manifest.json"
    payload = json.loads(root.read_text(encoding="utf-8"))
    payload["priceMode"] = "split_div"
    root.write_bytes(canonical_json_bytes(payload))
    try:
        activate_unified(public_dir=public, run_id=RUN_ID, session_date=SESSION)
    except (FileNotFoundError, ValueError) as exc:
        assert "bottom-fishing" in str(exc) or "next" in str(exc)
    else:
        raise AssertionError("activation accepted an invalid Bottom Fishing price basis")


def test_bottom_screener_sidecar_keeps_rich_fields_without_changing_ranking(tmp_path: Path) -> None:
    public = tmp_path / "public"
    write_bottom(public)
    manifest_path = public / "data" / "modes" / "bottom-fishing" / "manifest.json"
    manifest = ScanManifestV1.model_validate_json(manifest_path.read_bytes())
    candidate = {
        "ticker": "BOT",
        "score": 88.0,
        "price": 99.0,
        "atr20": 2.0,
        "trade_plan": {
            "status": "trigger_pending",
            "trigger_state": "pending",
            "trigger_reference_level": 100.0,
            "entry_risk_pct": 6.0,
            "extension_atr": None,
        },
        **{f"sourceField{index}": index for index in range(65)},
    }

    updated = attach_bottom_screener_asset(
        manifest=manifest,
        public_dir=public,
        raw_scan={"candidates": [candidate]},
    )

    assert "bottomScreener" in updated.assets
    assert updated.versions["ranking"] == manifest.versions["ranking"]
    asset = updated.assets["bottomScreener"]
    payload = json.loads((manifest_path.parent / asset.path).read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "stockscout-unified/bottom-screener-v1"
    assert payload["priceBasis"] == "split_only"
    assert len(payload["fields"]) >= 60
    assert payload["rows"][0]["score"] == 88.0
    assert payload["rows"][0]["trade_status"] == "trigger_pending"
    assert payload["rows"][0]["distance_to_trigger_pct"] == -1.0
    assert payload["rows"][0]["distance_to_trigger_atr"] == -0.5


def test_verify_accepts_bottom_manifest_backed_gzip_chart_shards(tmp_path: Path) -> None:
    public = tmp_path / "public"
    chart_dir = tmp_path / "charts"
    chart_dir.mkdir()
    (chart_dir / "000.json").write_bytes(canonical_json_bytes({"AAA": []}))
    canonical_path = tmp_path / "latest.json"
    canonical_path.write_bytes(
        canonical_json_bytes(
            {
                "generatedAt": f"{SESSION}T22:00:00Z",
                "market": {"scanDate": SESSION},
                "chartShardCount": 1,
                "chartShards": {"AAA": "000.json"},
                "groups": groups(1),
                "universe": [{"ticker": "AAA", "price": 10, "score": 90, "opportunityScore": 90}],
            }
        )
    )
    publish_adjusted_mode(
        mode="next",
        canonical_path=canonical_path,
        chart_dir=chart_dir,
        public_dir=public,
        run_id=RUN_ID,
        session_date=SESSION,
    )
    publish_adjusted_mode(
        mode="ryan-original",
        canonical_path=canonical_path,
        chart_dir=chart_dir,
        public_dir=public,
        run_id=RUN_ID,
        session_date=SESSION,
    )
    write_bottom(public, manifest_backed_charts=True)
    activate_unified(public_dir=public, run_id=RUN_ID, session_date=SESSION)

    assert verify(public).run_id == RUN_ID


def test_next_rejects_missing_or_invalid_group_aggregate(tmp_path: Path) -> None:
    chart_dir = tmp_path / "charts"
    chart_dir.mkdir()
    (chart_dir / "000.json").write_bytes(canonical_json_bytes({"AAA": []}))
    canonical = {
        "generatedAt": f"{SESSION}T22:00:00Z",
        "market": {"scanDate": SESSION},
        "chartShards": {"AAA": "000.json"},
        "universe": [{"ticker": "AAA", "price": 10, "opportunityScore": 91}],
    }
    source = tmp_path / "latest.json"
    source.write_bytes(canonical_json_bytes(canonical))
    for invalid in (None, {**groups(1), "sectorCoverage": 0}, {**groups(1), "sectors": []}):
        canonical["groups"] = invalid
        source.write_bytes(canonical_json_bytes(canonical))
        try:
            publish_adjusted_mode(
                mode="next",
                canonical_path=source,
                chart_dir=chart_dir,
                public_dir=tmp_path / f"public-{len(str(invalid))}",
                run_id=RUN_ID,
                session_date=SESSION,
            )
        except ValueError as exc:
            assert "group" in str(exc).lower()
        else:
            raise AssertionError("Next accepted an invalid group aggregate")


def test_next_publishes_hash_bound_read_only_contexts(tmp_path: Path) -> None:
    public = tmp_path / "public"
    chart_dir = tmp_path / "charts"
    chart_dir.mkdir()
    (chart_dir / "000.json").write_bytes(canonical_json_bytes({"AAA": []}))
    canonical = tmp_path / "latest.json"
    canonical.write_bytes(canonical_json_bytes({
        "generatedAt": f"{SESSION}T22:00:00Z",
        "market": {"scanDate": SESSION},
        "chartShards": {"AAA": "000.json"},
        "groups": groups(1),
        "universe": [{"ticker": "AAA", "price": 10, "opportunityScore": 91}],
    }))
    factor = tmp_path / "factor.json"
    factor.write_bytes(canonical_json_bytes({
        "schemaVersion": 1,
        "generatedAt": f"{SESSION}T12:00:00Z",
        "method": {"stockScoutImpact": "none; read-only independent macro/factor module"},
        "factors": [{"sourceCode": code} for code in ("MKT_RF", "SMB", "HML", "RMW", "CMA", "MOM")],
    }))
    gmli = tmp_path / "gmli.json"
    gmli.write_bytes(canonical_json_bytes({
        "schemaVersion": 1,
        "status": "OK",
        "generatedAt": f"{SESSION}T12:00:00Z",
        "consumerContract": {"mode": "READ_ONLY_SIDECAR", "mutatesStockScoutScoring": False},
    }))
    manifest = publish_adjusted_mode(
        mode="next",
        canonical_path=canonical,
        chart_dir=chart_dir,
        public_dir=public,
        run_id=RUN_ID,
        session_date=SESSION,
        factor_regime_path=factor,
        gmli_context_path=gmli,
    )
    assert manifest.assets["factorRegime"].count == 6
    assert manifest.assets["gmliContext"].count == 1
    for name in ("factorRegime", "gmliContext"):
        asset = manifest.assets[name]
        payload = (public / "data" / "modes" / "next" / asset.path).read_bytes()
        assert len(payload) == asset.bytes
        assert sha256_bytes(payload) == asset.sha256
    core_asset = manifest.assets["core"]
    core = json.loads((public / "data" / "modes" / "next" / core_asset.path).read_bytes())
    assert core["universe"][0]["opportunityScore"] == 91
    assert "factors" not in core["universe"][0]

    ryan = publish_adjusted_mode(
        mode="ryan-original",
        canonical_path=canonical,
        chart_dir=chart_dir,
        public_dir=tmp_path / "ryan-public",
        run_id=RUN_ID,
        session_date=SESSION,
        factor_regime_path=factor,
        gmli_context_path=gmli,
    )
    assert "factorRegime" not in ryan.assets
    assert "gmliContext" not in ryan.assets


def test_next_recovery_can_preserve_its_historical_source_commit(tmp_path: Path) -> None:
    chart_dir = tmp_path / "charts"
    chart_dir.mkdir()
    (chart_dir / "000.json").write_bytes(canonical_json_bytes({"AAA": []}))
    canonical = tmp_path / "latest.json"
    canonical.write_bytes(canonical_json_bytes({
        "generatedAt": f"{SESSION}T22:00:00Z",
        "market": {"scanDate": SESSION},
        "chartShards": {"AAA": "000.json"},
        "groups": groups(1),
        "universe": [{"ticker": "AAA", "price": 10}],
    }))
    historical = "a878b671e93617f3331604a8ea4eea592fddc6e4"
    manifest = publish_adjusted_mode(
        mode="next",
        canonical_path=canonical,
        chart_dir=chart_dir,
        public_dir=tmp_path / "public",
        run_id=RUN_ID,
        session_date=SESSION,
        source_commit=historical,
    )
    assert manifest.provenance["sourceCommit"] == historical
    assert manifest.versions["detectors"] == historical
