from copy import deepcopy

from audit_next_core_invariance import compare_payloads
from compute_legacy_confirmation import (
    attach_legacy_confirmation,
    compute_legacy_confirmation,
    enrich_payload_with_legacy_confirmation,
    project_captured_legacy_confirmation,
)


def legacy_row(**overrides):
    row = {
        "ticker": "AAA",
        "opportunityScore": 82.0,
        "emergingLeaderScore": 78.0,
        "maClusterScore": 73.0,
        "groupRank": 65.0,
        "fundamentalEvidenceScore": 61.0,
        "stage": 2,
        "rsRank": 91.0,
        "leadershipScore": 82.0,
        "originalRunBuySignal": False,
        "originalRunSellSignal": False,
        "originalBuy": False,
        "originalMarketQualifiedBuy": False,
        "originalSell": False,
        "originalEngine": {
            "model": "original-signal-engine-v1",
            "completeSourceCaptureModel": "legacy-complete-source-capture-v1",
            "buy": {
                "isBuy": False,
                "marketQualified": False,
                "emittedByOriginalRun": False,
                "score": 12,
                "riskReward": 2.2,
                "riskPct": 4.5,
                "adVolumeRatio": 1.3,
                "avgVolumeUpDays": 200,
                "avgVolumeDownDays": 100,
            },
            "sell": {
                "isSell": False,
                "emittedByOriginalRun": False,
                "score": 0,
                "severity": "none",
                "reasons": [],
            },
            "minervini": {
                "passes": False,
                "score": 62,
                "passed": 5,
                "total": 8,
                "criteria": {"c1": True},
            },
            "vcp": {
                "isVcp": False,
                "quality": 40,
                "contractionCount": 2,
                "contractionQuality": 60,
                "volumeQuality": 70,
                "contractions": [{"number": 1}],
            },
            "breakout": {
                "is_breakout": False,
                "breakout_type": None,
                "breakout_level": None,
                "volume_confirmed": False,
                "volume_ratio": 0.9,
            },
        },
    }
    row.update(overrides)
    return row


def market():
    return {
        "originalSignalGate": {
            "spy": {"phase": 2, "trend": "Bullish"},
            "breadth": {"phase2_pct": 44},
            "gate": {"should_generate_buys": True, "should_generate_sells": False},
        }
    }


def test_missing_legacy_is_explicitly_unavailable():
    result = compute_legacy_confirmation(None)
    assert result["status"] == "UNAVAILABLE"
    assert result["available"] is False
    assert result["provenance"] == "unavailable"
    assert result["source"] is None


def test_observed_legacy_fields_are_normalized_without_reconstruction():
    result = compute_legacy_confirmation(
        {
            "confirmationStatus": "confirmed",
            "score": 87,
            "positionPct": 4.2,
            "daysFromLow": 18,
            "mature": False,
            "pathReason": "observed legacy path",
        }
    )
    assert result["status"] == "CONFIRMED"
    assert result["available"] is True
    assert result["score"] == 87
    assert result["positionPct"] == 4.2
    assert result["pathReason"] == "observed legacy path"


def test_unknown_observed_status_is_not_invented():
    result = compute_legacy_confirmation({"status": "BUY", "score": 91})
    assert result["status"] == "UNAVAILABLE"
    assert result["available"] is False
    assert result["provenance"] == "observed"
    assert result["score"] == 91


def test_attach_is_append_only_and_does_not_mutate_candidate():
    candidate = {"ticker": "AAA", "nested": {"keep": [1, 2, 3]}}
    before = deepcopy(candidate)
    enriched = attach_legacy_confirmation(candidate, {"status": "EARLY"})
    assert candidate == before
    assert {k: v for k, v in enriched.items() if k != "legacyConfirmation"} == before
    assert enriched["legacyConfirmation"]["status"] == "EARLY"


def test_missing_captured_engine_is_unavailable():
    projected = project_captured_legacy_confirmation({"ticker": "AAA"}, market=market())
    assert projected["status"] == "UNAVAILABLE"
    assert projected["available"] is False
    assert projected["affectsStockScout"] is False


def test_emitted_original_buy_is_confirmed():
    row = legacy_row(originalRunBuySignal=True)
    row["originalEngine"]["buy"]["emittedByOriginalRun"] = True
    projected = project_captured_legacy_confirmation(row, market=market())
    assert projected["status"] == "CONFIRMED"
    assert projected["reasons"] == ["ORIGINAL_RUN_BUY"]


def test_emitted_original_sell_is_risk_and_has_precedence():
    row = legacy_row(originalRunBuySignal=True, originalRunSellSignal=True)
    row["originalEngine"]["buy"]["emittedByOriginalRun"] = True
    row["originalEngine"]["sell"].update(
        {
            "emittedByOriginalRun": True,
            "isSell": True,
            "reasons": ["Failed breakout below pivot"],
        }
    )
    projected = project_captured_legacy_confirmation(row)
    assert projected["status"] == "RISK"
    assert "ORIGINAL_RUN_BUY_ALSO_PRESENT" in projected["reasons"]
    assert projected["evidence"]["sell"]["failedBreakoutMentioned"] is True


def test_raw_buy_blocked_by_market_is_conflict():
    row = legacy_row(originalBuy=True)
    row["originalEngine"]["buy"]["isBuy"] = True
    projected = project_captured_legacy_confirmation(row)
    assert projected["status"] == "CONFLICT"
    assert projected["reasons"] == ["RAW_BUY_MARKET_BLOCKED"]


def test_raw_sell_not_emitted_is_conflict():
    row = legacy_row(originalSell=True)
    row["originalEngine"]["sell"]["isSell"] = True
    projected = project_captured_legacy_confirmation(row)
    assert projected["status"] == "CONFLICT"
    assert "RAW_SELL_NOT_EMITTED" in projected["reasons"]


def test_trend_template_pass_without_emission_is_early():
    row = legacy_row()
    row["originalEngine"]["minervini"]["passes"] = True
    assert project_captured_legacy_confirmation(row)["status"] == "EARLY"


def test_vcp_without_emission_is_early():
    row = legacy_row()
    row["originalEngine"]["vcp"]["isVcp"] = True
    assert project_captured_legacy_confirmation(row)["status"] == "EARLY"


def test_breakout_without_emission_is_early():
    row = legacy_row()
    row["originalEngine"]["breakout"]["is_breakout"] = True
    assert project_captured_legacy_confirmation(row)["status"] == "EARLY"


def test_captured_row_without_boolean_trigger_is_neutral():
    assert project_captured_legacy_confirmation(legacy_row())["status"] == "NEUTRAL"


def test_projection_maps_frozen_evidence_without_confirmation_score():
    row = legacy_row(
        originalRR=6.28,
        originalAdVolumeRatio=1.74,
        originalBreakoutVolumeConfirmed=True,
    )
    row["originalEngine"]["vcp"]["isVcp"] = True
    row["originalEngine"]["breakout"].update(
        {
            "is_breakout": True,
            "breakout_type": "50 SMA Breakout",
            "breakout_level": 68.37,
            "volume_confirmed": True,
        }
    )
    projected = project_captured_legacy_confirmation(row, market=market())
    assert "score" not in projected
    assert projected["classificationBasis"] == "captured-frozen-boolean-outputs-only"
    assert projected["evidence"]["buy"]["riskReward"] == 6.28
    assert projected["evidence"]["adVolume"]["ratio"] == 1.74
    assert projected["evidence"]["breakout"]["volumeConfirmed"] is True
    assert projected["evidence"]["market"]["buyEnabled"] is True
    assert projected["evidence"]["market"]["ref"] == "market.originalSignalGate"
    assert projected["evidence"]["vcp"]["contractions"] == [{"number": 1}]


def test_payload_enrichment_is_append_only_and_core_invariant():
    before = {
        "chartShards": {"AAA": "charts/A.json"},
        "market": market(),
        "universe": [legacy_row()],
    }
    snapshot = deepcopy(before)
    after = enrich_payload_with_legacy_confirmation(before)
    assert before == snapshot
    assert compare_payloads(before, after) == []
    assert after["chartShards"] == before["chartShards"]
    assert after["legacyConfirmationSummary"]["total"] == 1
    assert after["legacyConfirmationSummary"]["counts"]["NEUTRAL"] == 1
