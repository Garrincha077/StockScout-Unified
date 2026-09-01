from __future__ import annotations

import json
import subprocess

import pytest

from scripts import build_next_contexts as contexts_module
from scripts.build_next_contexts import (
    annotate_freshness,
    ensure_local_next_handoff_manifest,
    validate_context,
)


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


def test_local_next_handoff_manifest_is_materialized_and_verified(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "latest.json").write_text(
        json.dumps({"market": {"scanDate": "2026-08-31"}, "universe": [{"ticker": "AAA"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(contexts_module, "LOCAL_NEXT_DATA", data_dir)
    monkeypatch.setattr(contexts_module, "NEXT_ENGINE", tmp_path / "engine")

    def fake_run(command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command[-1].endswith("prepare_frontend_payloads.py")
        (data_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "marketSession": {
                        "date": "2026-08-31",
                        "status": "closed",
                        "timezone": "America/New_York",
                    },
                    "assets": {"charts": {"coveragePct": 100.0}},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(contexts_module, "_run", fake_run)
    assert ensure_local_next_handoff_manifest() is True


def test_reuse_context_build_skips_local_handoff_when_no_local_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(contexts_module, "LOCAL_NEXT_DATA", tmp_path / "missing")
    assert ensure_local_next_handoff_manifest() is False
