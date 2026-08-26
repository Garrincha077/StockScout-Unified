import json
from datetime import datetime

import hydrate_rich_fundamentals as hrf


class DummyFetcher:
    def _clean_for_json(self, data):
        return data

    def _update_metadata(self, ticker):
        self.updated = ticker


def test_extended_bootstrap_preserves_legacy_fetched_at(tmp_path):
    path = tmp_path / "TEST_fundamentals.json"
    original_stamp = "2026-08-18T10:00:00"
    path.write_text(json.dumps({
        "data": {"ticker": "TEST", "revenue_yoy_change": 12.5},
        "fetched_at": original_stamp,
    }), encoding="utf-8")

    ok = hrf.merge_extended_into_cache(
        DummyFetcher(),
        path,
        {
            "extended_fundamentals_model": hrf.EXTENDED_MODEL,
            "fundamental_data_source": "yfinance",
            "free_cash_flow_yoy_change": 20.0,
        },
    )

    assert ok is True
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    assert wrapper["fetched_at"] == original_stamp
    assert wrapper["data"]["revenue_yoy_change"] == 12.5
    assert wrapper["data"]["free_cash_flow_yoy_change"] == 20.0
    assert wrapper["data"]["extended_fundamentals_model"] == hrf.EXTENDED_MODEL
    assert wrapper.get("extended_fetched_at")


def test_invalid_extended_payload_does_not_touch_cache(tmp_path):
    path = tmp_path / "TEST_fundamentals.json"
    original = {
        "data": {"ticker": "TEST", "revenue_yoy_change": 12.5},
        "fetched_at": "2026-08-18T10:00:00",
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    assert hrf.merge_extended_into_cache(DummyFetcher(), path, {}) is False
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_cache_marker_detects_only_current_extended_model(tmp_path):
    path = tmp_path / "TEST_fundamentals.json"
    path.write_text(json.dumps({
        "data": {"extended_fundamentals_model": hrf.EXTENDED_MODEL},
        "fetched_at": datetime.now().isoformat(),
    }), encoding="utf-8")
    assert hrf.cache_has_extended_model(path) is True

    path.write_text(json.dumps({
        "data": {"extended_fundamentals_model": "old-model"},
        "fetched_at": datetime.now().isoformat(),
    }), encoding="utf-8")
    assert hrf.cache_has_extended_model(path) is False
