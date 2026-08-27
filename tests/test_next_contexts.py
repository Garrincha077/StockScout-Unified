from __future__ import annotations

import json

import pytest

from scripts.build_next_contexts import annotate_freshness, validate_context


def _factor_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "method": {"stockScoutImpact": "none; read-only context"},
        "factors": [
            {"id": factor}
            for factor in ("MKT_RF", "SMB", "HML", "RMW", "CMA", "MOM")
        ],
    }


def _gmli_payload() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "status": "OK",
        "consumerContract": {
            "mode": "READ_ONLY_SIDECAR",
            "mutatesStockScoutScoring": False,
        },
    }


def test_context_contracts_remain_read_only_and_versioned() -> None:
    validate_context(_factor_payload(), "factorRegime")
    validate_context(_gmli_payload(), "gmliContext")
    broken = _gmli_payload()
    broken["consumerContract"]["mutatesStockScoutScoring"] = True  # type: ignore[index]
    with pytest.raises(ValueError, match="read-only"):
        validate_context(broken, "gmliContext")


def test_fallback_is_explicitly_marked_stale() -> None:
    payload = annotate_freshness(
        _factor_payload(),
        status="stale",
        fallback=True,
        provenance="https://example.test/last-good.json",
        checked_at="2026-08-26T12:00:00Z",
    )
    assert payload["freshness"] == {
        "status": "stale",
        "fallback": True,
        "checkedAt": "2026-08-26T12:00:00Z",
        "provenance": "https://example.test/last-good.json",
    }
    assert "score" not in json.loads(json.dumps(payload))
