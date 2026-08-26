import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import refresh_gmli_context as gmli


def fixture_bundle():
    report = {
        "schema_version": "gmli-report-v1.6",
        "generated_at": "2026-08-25T11:19:47Z",
        "data_health": {"status": "HEALTHY"},
        "signal_role_taxonomy": {
            "version": "GMLI_SIGNAL_ROLE_TAXONOMY_V1",
            "scoring_effect": "NONE",
            "money_core": {"role": "LEADING", "interpretation": "money"},
            "funding_v2": {"role": "REACTIVE_CONFIRMATION", "interpretation": "funding"},
            "fiscal_v2": {"role": "MIXED", "interpretation": "fiscal"},
            "market_confirmation": {"role": "REACTIVE_CONFIRMATION", "interpretation": "market"},
        },
        "regime": {
            "engine_fact": {
                "money": {
                    "version": "GMLI_GLOBAL_MONEY_V2_PBOC_OFFICIAL",
                    "observation_month": "2026-06",
                    "available_date": "2026-07-31",
                    "freshness": "FRESH",
                    "usd_yoy_pct": 7.95,
                    "usd_score": 55.1,
                    "usd_regime": "NEUTRAL",
                    "fx_neutral_yoy_pct": 5.95,
                    "fx_neutral_score": 44.5,
                    "fx_neutral_regime": "NEUTRAL",
                    "agreement": "AGREE",
                }
            },
            "current_research_inference": {
                "label": "NEUTRAL",
                "tilt": "MILD_POSITIVE",
                "provisional": False,
                "fiscal": {"automatic_global_conviction_weight": 0},
            },
        },
    }
    extremes = {
        "schema_version": "gmli-money-extremes-v1",
        "version": "GMLI_MONEY_HISTORICAL_EXTREMES_V1",
        "evidence_tier": "RESEARCH_DIAGNOSTIC",
        "scoring_effect": "NONE",
        "construction": {"rolling_window_months": 120, "lookahead": False},
        "latest": {
            "month": "2026-06",
            "available_date": "2026-07-31",
            "usd_level": {"value_pct": 7.95, "z": 0.31, "percentile": 70.4, "band": "NORMAL"},
            "fx_neutral_level": {"value_pct": 5.95, "z": -0.33, "percentile": 46.3, "band": "NORMAL"},
            "usd_accel3": {"value_pp": -1.69, "z": -0.54, "percentile": 32.1, "band": "NORMAL"},
            "fx_neutral_accel3": {"value_pp": 0.0, "z": 0.03, "percentile": 52.9, "band": "NORMAL"},
        },
        "rows": [
            {"month": f"2025-{month:02d}", "usd_level_z": month / 10, "fx_neutral_level_z": -month / 10}
            for month in range(1, 13)
        ],
    }
    history = {
        "schema_version": "gmli-pages-context-history-v1",
        "scoring_effect": "NONE",
        "funding": {
            "version": "GMLI_FUNDING_V2_EFFECTIVE_CONDITIONS",
            "role": "REACTIVE_CONFIRMATION",
            "active_available_date": "2026-07-31",
            "rows": [
                {
                    "observation_month": "2026-06",
                    "available_date": "2026-07-31",
                    "score": 37.97,
                    "regime": "RESTRICTIVE",
                    "structural_support_score": 48.0,
                    "observed_conditions_score": 37.97,
                }
            ],
        },
        "fiscal": {
            "version": "GMLI_FISCAL_V2_DEFICIT_IMPULSE",
            "role": "MIXED",
            "active_available_date": "2026-07-31",
            "rows": [
                {
                    "observation_month": "2026-06",
                    "available_date": "2026-07-31",
                    "score": 45.91,
                    "regime": "NEUTRAL",
                    "deficit_pct_gdp": 5.56,
                    "fiscal_impulse_pp": -0.67,
                }
            ],
        },
        "market_confirmation": {
            "role": "REACTIVE_CONFIRMATION",
            "cutoff_month": "2026-07",
            "rows": [
                {
                    "month": "2026-07",
                    "positive": 3,
                    "total": 4,
                    "score_0_2": 2,
                    "assets_positive": {"SPY": True, "QQQ": True, "GLD": True, "DBC": False},
                }
            ],
        },
    }
    refresh = {
        "schema_version": "gmli-pages-refresh-v1",
        "policy": "FETCH_FIRST_WITH_PER_LAYER_LAST_GOOD_FALLBACK",
        "status": "PASS_FETCH_FIRST",
    }
    return report, extremes, history, refresh


class GmliContextTests(unittest.TestCase):
    def test_projects_canonical_outputs_without_scoring_effect(self):
        report, extremes, history, refresh = fixture_bundle()
        context = gmli.build_context(report, extremes, history, refresh, {"report.json": "abc"})
        self.assertEqual(context["schemaVersion"], 1)
        self.assertEqual(context["stockScoutImpact"], gmli.STOCKSCOUT_IMPACT)
        self.assertFalse(context["consumerContract"]["reconstructsGmli"])
        self.assertFalse(context["consumerContract"]["mutatesStockScoutScoring"])
        self.assertEqual(context["regime"]["label"], "NEUTRAL")
        self.assertEqual(context["regime"]["money"]["usdScore"], 55.1)
        self.assertEqual(context["regime"]["funding"]["score"], 37.97)
        self.assertEqual(context["regime"]["fiscal"]["automaticGlobalConvictionWeight"], 0)
        self.assertEqual(context["regime"]["market"]["positive"], 3)
        self.assertEqual(context["moneyExtremes"]["latest"]["usd_accel3"]["z"], -0.54)
        self.assertEqual(context["source"]["hashes"]["report.json"], "abc")

    def test_rejects_money_extremes_vintage_mismatch(self):
        report, extremes, history, refresh = fixture_bundle()
        extremes["latest"]["month"] = "2026-05"
        with self.assertRaises(gmli.GmliContextError):
            gmli.build_context(report, extremes, history, refresh)

    def test_rejects_non_publishable_upstream_refresh(self):
        report, extremes, history, refresh = fixture_bundle()
        refresh["status"] = "FAIL"
        with self.assertRaises(gmli.GmliContextError):
            gmli.build_context(report, extremes, history, refresh)

    def test_refresh_uses_valid_last_good_on_upstream_failure(self):
        report, extremes, history, refresh = fixture_bundle()
        context = gmli.build_context(report, extremes, history, refresh)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "gmli-context.json"
            output.write_text(json.dumps(context), encoding="utf-8")
            with patch.object(gmli, "fetch_source_bundle", side_effect=gmli.GmliContextError("network down")):
                result = gmli.refresh(output, timeout=0.1, allow_last_good=True)
            self.assertEqual(result["status"], "LAST_GOOD_FALLBACK")
            preserved = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(preserved["generatedAt"], context["generatedAt"])


if __name__ == "__main__":
    unittest.main()
