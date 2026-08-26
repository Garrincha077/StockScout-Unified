import json

from prepare_frontend_payloads import MAX_CORE_BYTES, build_core_payload, publish, shard_number


def sample_payload():
    return {
        "version": 8,
        "generatedAt": "2026-08-19T00:00:00+00:00",
        "market": {
            "regime": "UP",
            "originalSignalGate": {
                "gate": {"should_generate_buys": True, "should_generate_sells": True},
                "spy": {"phase": 2, "trend": "Bullish"},
            },
        },
        "layers": {"legacy": {"label": "LEGACY"}, "sharedEvidencePath": "richData"},
        "chartShards": {"AAA": "001.json", "BBB": "002.json"},
        "originalEngineModel": "original-signal-engine-v1",
        "legacyCompleteSourceCaptureModel": "legacy-complete-source-capture-v1",
        "universe": [
            {
                "ticker": "AAA",
                "opportunityScore": 81,
                "fundamentalGrowthScore": 77,
                "fundamentalMarginScore": 66,
                "fundamentalInventoryScore": 55,
                "originalBuyScore": 92,
                "originalRunBuySignal": True,
                "originalBreakoutVolumeConfirmed": True,
                "originalRunSellSignal": False,
                "originalEngine": {
                    "model": "original-signal-engine-v1",
                    "completeSourceCaptureModel": "legacy-complete-source-capture-v1",
                    "buy": {"score": 92, "emittedByOriginalRun": True},
                    "sell": {"emittedByOriginalRun": False},
                    "minervini": {"passes": True},
                    "vcp": {"isVcp": False},
                    "breakout": {"is_breakout": False},
                    "blob": "x" * 200,
                },
                "richData": {"fundamentals": {"blob": "y" * 200}},
                "stockscout": {"blob": "z" * 100},
            },
            {
                "ticker": "BBB",
                "opportunityScore": 70,
                "originalEngine": {
                    "model": "original-signal-engine-v1",
                    "completeSourceCaptureModel": "legacy-complete-source-capture-v1",
                    "buy": {"emittedByOriginalRun": False},
                    "sell": {"emittedByOriginalRun": False},
                    "minervini": {"passes": False},
                    "vcp": {"isVcp": False},
                    "breakout": {"is_breakout": False},
                    "blob": "x" * 200,
                },
                "richData": {"blob": "y" * 200},
                "stockscout": {"blob": "z" * 100},
            },
        ],
    }


def test_core_projection_strips_only_heavy_nested_row_payloads():
    payload = sample_payload()
    confirmations = {
        "AAA": {"status": "CONFIRMED", "reasons": ["ORIGINAL_RUN_BUY"]},
        "BBB": {"status": "NEUTRAL", "reasons": ["NO_FROZEN_CONFIRMATION_TRIGGER"]},
    }
    core = build_core_payload(payload, confirmations)

    assert [row["ticker"] for row in core["universe"]] == ["AAA", "BBB"]
    assert core["universe"][0]["opportunityScore"] == 81
    assert core["universe"][0]["fundamentalDims"] == [77, 66, 55]
    assert "fundamentalGrowthScore" not in core["universe"][0]
    assert "fundamentalMarginScore" not in core["universe"][0]
    assert "fundamentalInventoryScore" not in core["universe"][0]
    assert core["universe"][0]["originalBuyScore"] == 92
    assert core["universe"][0]["originalBreakoutVolumeConfirmed"] is True
    assert core["universe"][0]["originalRunSellSignal"] is False
    assert core["universe"][0]["legacyConfirmationStatus"] == "CONFIRMED"
    assert core["universe"][0]["legacyConfirmationReasons"] == ["ORIGINAL_RUN_BUY"]
    assert core["universe"][1]["legacyConfirmationStatus"] == "NEUTRAL"
    assert "originalEngine" not in core["universe"][0]
    assert "richData" not in core["universe"][0]
    assert "stockscout" not in core["universe"][0]
    assert core["chartShards"] == payload["chartShards"]
    assert "lazyFile" not in core["layers"]["legacy"]
    assert core["layers"]["legacy"]["indexFile"] == "legacy/index.json"
    assert core["layers"]["legacy"]["detailsPath"] == "legacy/details"
    assert core["layers"]["legacy"]["confirmationFile"] == "shadow/legacy-confirmation.json"
    assert core["legacyConfirmationFile"] == "shadow/legacy-confirmation.json"


def test_publish_preserves_canonical_snapshot_and_writes_versioned_client_artifacts(tmp_path):
    latest = tmp_path / "latest.json"
    core_path = tmp_path / "core.json"
    manifest = tmp_path / "manifest.json"
    sidecar_path = tmp_path / "shadow" / "legacy-confirmation.json"
    payload = sample_payload()
    original = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    latest.write_bytes(original)

    meta = publish(latest, core_path, manifest, sidecar_path)
    core = json.loads(core_path.read_text())
    sidecar = json.loads(sidecar_path.read_text())
    manifest_payload = json.loads(manifest.read_text())

    assert latest.read_bytes() == original
    assert meta["manifestVersion"] == 2
    assert meta["universe"] == 2
    assert meta["assets"]["core"]["bytes"] <= MAX_CORE_BYTES
    assert meta["assets"]["core"]["bytes"] < meta["provenance"]["source"]["bytes"]
    assert meta["assets"]["legacyConfirmation"]["bytes"] > 0
    assert meta["legacyConfirmationCounts"] == {"CONFIRMED": 1, "NEUTRAL": 1}
    assert "fullDataFile" not in core
    assert core["legacyConfirmationFile"] == "shadow/legacy-confirmation.json"
    assert len(core["universe"]) == len(payload["universe"])
    assert core["universe"][0]["legacyConfirmationStatus"] == "CONFIRMED"
    assert core["universe"][1]["legacyConfirmationStatus"] == "NEUTRAL"
    assert sidecar["source"]["generatedAt"] == payload["generatedAt"]
    assert sidecar["byTicker"]["AAA"]["status"] == "CONFIRMED"
    assert sidecar["byTicker"]["BBB"]["status"] == "NEUTRAL"
    assert manifest_payload["provenance"]["source"]["path"] == "latest.json"
    assert manifest_payload["assets"]["legacyConfirmation"]["path"] == "shadow/legacy-confirmation.json"
    assert manifest_payload["assets"]["legacyIndex"]["coveragePct"] == 100
    assert manifest_payload["assets"]["legacyDetails"]["shardCount"] == 128

    index = json.loads((tmp_path / "legacy" / "index.json").read_text())
    detail_rows = {}
    for detail_path in sorted((tmp_path / "legacy" / "details").glob("*.json")):
        detail_rows.update(json.loads(detail_path.read_text()))
    canonical_tickers = [row["ticker"] for row in payload["universe"]]
    assert [row["ticker"] for row in core["universe"]] == canonical_tickers
    assert [row["ticker"] for row in index["universe"]] == canonical_tickers
    assert sorted(detail_rows) == sorted(canonical_tickers)
    assert len(list((tmp_path / "legacy" / "details").glob("*.json"))) == 128
    assert detail_rows["AAA"]["originalEngine"] == payload["universe"][0]["originalEngine"]
    assert (tmp_path / "legacy" / "details" / f"{shard_number('AAA'):03d}.json").exists()
