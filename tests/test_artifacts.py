from __future__ import annotations

import json

import pytest

from stock_scout.scoring.focus_blend import candidate_blend_score
from stock_scout.scoring.models import Candidate
from stockscout_eod.artifacts import build_public_snapshot, verify_public_snapshot
from stockscout_eod.contracts import ChartManifestV1, ChartShardV1, RawScanEnvelopeV1, wire_dump
from stockscout_eod.jsonio import write_json


def _candidate(ticker: str, status: str = "entry_ready") -> dict:
    ready = status == "entry_ready"
    return {
        "ticker": ticker,
        "as_of": "2026-08-21",
        "price": 100.0,
        "score": 80.0 if ticker == "AAA" else 70.0,
        "focus_score": 82.0,
        "rs_rating": 91.0,
        "volume_ratio_50d": 2.25,
        "weinstein_stage": 2,
        "weinstein_substage": "2A_fresh_breakout",
        "actionability": "actionable_now" if ready else "watch",
        "primary_setup": "rwb_squeeze_thrust",
        "risk_level": "none",
        "score_breakdown": {"trend": 77.0, "setup_quality": 88.0},
        "setups": {
            "rwb_squeeze_thrust": {
                "setup_name": "rwb_squeeze_thrust",
                "triggered": True,
                "sub_state": "fresh" if ready else "forming",
                "score": 90.0,
                "trigger_level": 99.0,
                "invalidation_level": 94.0,
            }
        },
        "trade_plan": {
            "status": status,
            "reason_codes": [status],
            "trigger_state": "fresh" if ready else "pending",
            "trigger_reference_level": 99.0,
            "entry_reference_level": 100.0 if ready else 99.0,
            "structural_invalidation_level": 94.0,
            "entry_risk_pct": 6.0,
            "extension_atr": 0.2 if ready else None,
            "tactical_stop_level": 94.0 if ready else None,
            "tactical_risk_pct": 6.0 if ready else None,
            "source": "primary_detector",
            "version": 1,
        },
        "trigger_level": 99.0,
        "invalidation_level": 94.0,
    }


def _scan(run_id: str = "2026-08-21-eod-test") -> RawScanEnvelopeV1:
    return RawScanEnvelopeV1(
        runId=run_id,
        sessionDate="2026-08-21",
        generatedAt="2026-08-21T20:30:00Z",
        priceMode="split_only",
        candidates=[_candidate("AAA"), _candidate("BBB", "trigger_pending")],
        excluded=[{**_candidate("CCC"), "actionability": "extended_too_late"}],
        stats={
            "universe_size": 3,
            "coverage_pct": 100.0,
            "data_status": "OK",
            "tickers_failed_all_providers": 0,
        },
        market={"regime": {"state": "confirmed_uptrend"}},
        provenance={
            "mode": "fixture",
            "universeSource": "test",
            "primaryProvider": "fixture",
        },
        versions={"engine": "fixture", "ranking": "fixture", "detectors": "fixture", "tradePlan": 1},
    )


def test_public_snapshot_is_immutable_hashed_and_contains_no_charts(tmp_path) -> None:
    public = tmp_path / "public"
    manifest = build_public_snapshot(
        _scan(), public_dir=public, min_universe=3, allow_fixture=True
    )
    verified = verify_public_snapshot(public)
    assert verified.run_id == manifest.run_id
    assert manifest.manifest_version == 1
    assert manifest.counts.candidates == 2
    assert set(manifest.assets) == {
        "core",
        "excluded",
        "history",
        "details",
        "legacyConfirmation",
    }
    assert not list((public / "data").rglob("*chart*"))

    core = json.loads((public / "data" / manifest.assets["core"].path).read_text())
    first = core["universe"][0]
    assert first["id"] == "scan:2026-08-21-eod-test:candidate:AAA"
    assert first["tradeStatus"] == "entry_ready"
    assert first["tacticalStopLevel"] == 94.0
    assert first["opportunityScore"] == 80.0
    assert first["opportunityRank"] == 1
    assert first["stage"] == 2
    assert first["rsRank"] == 91.0
    assert first["volumeRatio"] == 2.25
    assert first["setupTags"] == ["rwb_squeeze_thrust"]
    assert core["universe"][1].get("tacticalStopLevel") is None
    for row in core["universe"]:
        ticker = row["ticker"]
        bucket = core["detailShards"][ticker]
        shard = public / "data" / manifest.assets["details"].path / f"{bucket}.json"
        shard_rows = json.loads(shard.read_text(encoding="utf-8"))
        assert ticker in shard_rows
        detail = shard_rows[ticker]
        assert "candidate" not in detail
        assert detail["price"] == row["price"]
        assert detail["stage"] == row["stage"]
        assert detail["rsRank"] == row["rsRank"]
        assert detail["tradePlan"] == row["tradePlan"]
        assert detail["setupHits"]["rwb_squeeze_thrust"]["triggered"] is True


def test_public_snapshot_exposes_a_hashed_chart_index_without_embedding_bars(tmp_path) -> None:
    scan = _scan()
    chart_manifest = ChartManifestV1(
        runId=scan.run_id,
        sessionDate=scan.session_date,
        generatedAt=scan.generated_at,
        priceMode=scan.price_mode,
        requested=3,
        available=3,
        coveragePct=100,
        storageBaseUrl=(
            "https://fixture.supabase.co/storage/v1/object/public/"
            f"stockscout-eod-charts/{scan.run_id}"
        ),
        shards=[
            ChartShardV1(name="001", sha256="a" * 64, bytes=123, tickerCount=3)
        ],
        shardsByTicker={"AAA": "001", "BBB": "001", "CCC": "001"},
    )
    source = tmp_path / "charts.json"
    write_json(source, wire_dump(chart_manifest))
    public = tmp_path / "public"
    manifest = build_public_snapshot(
        scan,
        public_dir=public,
        min_universe=3,
        allow_fixture=True,
        chart_status="ready",
        chart_manifest=source,
    )

    verified = verify_public_snapshot(public)
    descriptor = verified.assets["charts"]
    chart_index = json.loads((public / "data" / descriptor.path).read_text())
    assert manifest.chart_status == "ready"
    assert descriptor.count == 3
    assert descriptor.coverage_pct == 100
    assert chart_index["storageBaseUrl"].endswith(scan.run_id)
    assert chart_index["shardsByTicker"]["AAA"] == "001"
    assert "daily" not in json.dumps(chart_index)


def test_ready_chart_status_requires_the_matching_complete_manifest(tmp_path) -> None:
    with pytest.raises(ValueError, match="requires a chart manifest"):
        build_public_snapshot(
            _scan(),
            public_dir=tmp_path / "public",
            min_universe=3,
            allow_fixture=True,
            chart_status="ready",
        )


def test_serialized_blend_and_headline_rank_match_frozen_candidate_model(tmp_path) -> None:
    scan = _scan().model_copy(deep=True)
    scan.candidates[0]["score"] = 50.0
    scan.candidates[0]["rs_rating"] = 40.0
    scan.candidates[0]["score_breakdown"]["setup_quality"] = 30.0
    scan.candidates[1]["score"] = 80.0
    scan.candidates[1]["rs_rating"] = 95.0
    scan.candidates[1]["score_breakdown"]["setup_quality"] = 90.0

    public = tmp_path / "public"
    manifest = build_public_snapshot(
        scan, public_dir=public, min_universe=3, allow_fixture=True
    )
    core = json.loads((public / "data" / manifest.assets["core"].path).read_text())
    rows = {row["ticker"]: row for row in core["universe"]}

    for raw in scan.candidates:
        model = Candidate.model_validate(raw)
        assert rows[model.ticker]["focusBlend"] == round(candidate_blend_score(model), 4)
    assert rows["BBB"]["headlineRank"] == 1
    assert rows["AAA"]["headlineRank"] == 2
    assert [row["ticker"] for row in core["universe"]] == ["AAA", "BBB"]
    assert rows["AAA"]["canonicalUrl"] == (
        "https://garrincha077.github.io/StockScout-Unified/ticker/AAA?run=2026-08-21-eod-test"
    )


def test_public_canonical_url_rejects_non_https_base(tmp_path) -> None:
    with pytest.raises(ValueError, match="absolute HTTPS"):
        build_public_snapshot(
            _scan(),
            public_dir=tmp_path / "public",
            min_universe=3,
            allow_fixture=True,
            public_base_url="/StockScout-EOD",
        )


def test_today_and_new_flags_are_derived_from_the_verified_previous_core(tmp_path) -> None:
    prior_dir = tmp_path / "prior"
    build_public_snapshot(
        _scan(), public_dir=prior_dir, min_universe=3, allow_fixture=True
    )
    current_scan = _scan("2026-08-22-eod-test")
    current_scan.candidates[0]["trade_plan"]["status"] = "wait_for_retest"
    current_scan.candidates[0]["trade_plan"]["trigger_state"] = "extended"
    current_scan.candidates[1] = _candidate("DDD")
    current_dir = tmp_path / "current"
    manifest = build_public_snapshot(
        current_scan,
        public_dir=current_dir,
        previous_manifest=str(prior_dir / "data" / "manifest.json"),
        min_universe=3,
        allow_fixture=True,
    )
    core = json.loads((current_dir / "data" / manifest.assets["core"].path).read_text())
    rows = {row["ticker"]: row for row in core["universe"]}
    assert rows["AAA"]["changedToday"] is True
    assert "Trade status changed" in rows["AAA"]["changeLabels"]
    assert rows["DDD"]["newUniverseMember"] is True
    assert rows["DDD"]["changeImpact"] == 100.0


def test_legacy_confirmation_is_observational_and_cannot_change_ranking(tmp_path) -> None:
    scan = _scan()
    neutral_public = tmp_path / "neutral"
    risk_public = tmp_path / "risk"
    neutral = build_public_snapshot(
        scan,
        public_dir=neutral_public,
        legacy_sidecar={"candidates": {"AAA": {"status": "NEUTRAL"}}},
        min_universe=3,
        allow_fixture=True,
    )
    risk = build_public_snapshot(
        scan.model_copy(update={"run_id": "legacy-risk-run"}),
        public_dir=risk_public,
        legacy_sidecar={"candidates": {"AAA": {"status": "RISK"}}},
        min_universe=3,
        allow_fixture=True,
    )
    neutral_core = json.loads(
        (neutral_public / "data" / neutral.assets["core"].path).read_text()
    )
    risk_core = json.loads((risk_public / "data" / risk.assets["core"].path).read_text())
    def projection(row: dict) -> tuple:
        return row["ticker"], row["scanOrder"], row["focusBlend"], row.get("headlineRank")

    assert list(map(projection, neutral_core["universe"])) == list(
        map(projection, risk_core["universe"])
    )


def test_fixture_scan_fails_closed_without_explicit_test_flag(tmp_path) -> None:
    with pytest.raises(ValueError, match="production_provenance"):
        build_public_snapshot(_scan(), public_dir=tmp_path / "public", min_universe=3)


def test_previous_history_is_carried_forward_and_capped_by_session(tmp_path) -> None:
    public = tmp_path / "public"
    first = build_public_snapshot(
        _scan("run-one"), public_dir=public, min_universe=3, allow_fixture=True
    )
    pointer = public / "data" / "manifest.json"
    second_scan = _scan("run-two")
    second_scan.session_date = "2026-08-22"
    second_scan.generated_at = "2026-08-22T20:30:00Z"
    for row in [*second_scan.candidates, *second_scan.excluded]:
        row["as_of"] = "2026-08-22"
    second = build_public_snapshot(
        second_scan,
        public_dir=public,
        previous_manifest=str(pointer),
        min_universe=3,
        allow_fixture=True,
    )
    history = json.loads((public / "data" / second.assets["history"].path).read_text())
    assert [row["sessionDate"] for row in history["sessions"]] == ["2026-08-22", "2026-08-21"]
    assert (public / "data" / first.assets["core"].path).exists()


def test_secret_like_fields_are_rejected_before_activation(tmp_path) -> None:
    scan = _scan().model_copy(deep=True)
    scan.candidates[0]["telegram_token"] = "never-public"
    with pytest.raises(ValueError, match="public_payload_safety"):
        build_public_snapshot(
            scan, public_dir=tmp_path / "public", min_universe=3, allow_fixture=True
        )
    assert not (tmp_path / "public" / "data" / "manifest.json").exists()


def test_private_chart_payload_is_explicitly_rejected_from_public_assets(tmp_path) -> None:
    scan = _scan().model_copy(deep=True)
    scan.candidates[0]["private_charts"] = {"daily": [[1, 2, 3, 4, 5, 6]]}
    with pytest.raises(ValueError, match="public_payload_safety"):
        build_public_snapshot(
            scan, public_dir=tmp_path / "public", min_universe=3, allow_fixture=True
        )
