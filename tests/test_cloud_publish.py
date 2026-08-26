from __future__ import annotations

import copy
import io
from urllib.error import HTTPError, URLError

import pytest

from stockscout_eod.artifacts import build_public_snapshot
from stockscout_eod.cloud_publish import (
    CloudPublishError,
    CloudPublishPlan,
    build_cloud_publish_plan,
    evaluate_cloud_alerts,
    post_json_with_retry,
    publish_record_hash,
    records_hash,
    stable_hash,
    stable_json_text,
    verify_cloud_publish_plan,
)
from stockscout_eod.contracts import RawScanEnvelopeV1
from tests.test_artifacts import _candidate

UPLOAD_ID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(scope="module")
def large_plan(tmp_path_factory: pytest.TempPathFactory) -> CloudPublishPlan:
    root = tmp_path_factory.mktemp("cloud-publish")
    candidates = [_candidate(f"C{index:04d}") for index in range(2100)]
    excluded = [
        {**_candidate(f"X{index:04d}"), "actionability": "extended_too_late"}
        for index in range(100)
    ]
    scan = RawScanEnvelopeV1(
        runId="2026-08-21-eod-cloud-contract",
        sessionDate="2026-08-21",
        generatedAt="2026-08-21T20:30:00Z",
        priceMode="split_only",
        candidates=candidates,
        excluded=excluded,
        stats={
            "universe_size": 2200,
            "coverage_pct": 100.0,
            "data_status": "OK",
            "tickers_failed_all_providers": 0,
        },
        provenance={
            "mode": "fixture",
            "universeSource": "contract-test",
            "primaryProvider": "fixture",
        },
        versions={"ranking": "frozen-v1", "detectors": "frozen-v1"},
    )
    public = root / "public"
    build_public_snapshot(
        scan,
        public_dir=public,
        min_universe=2200,
        allow_fixture=True,
    )
    return build_cloud_publish_plan(public)


class MemoryEdge:
    def __init__(self) -> None:
        self.manifest: dict | None = None
        self.records: dict[str, dict] = {}
        self.chunk_calls: list[int] = []

    def __call__(self, endpoint: str, token: str, payload: dict) -> dict:
        assert endpoint == "https://example.supabase.co/functions/v1/stockscout-eod-publish"
        assert token == "oidc-fixture"
        action = payload["action"]
        if action == "begin":
            manifest = payload["manifest"]
            base = {key: value for key, value in manifest.items() if key != "manifestHash"}
            if stable_hash(base) != manifest["manifestHash"]:
                return {"ok": False, "error": "manifest hash mismatch"}
            self.manifest = manifest
            return {
                "ok": True,
                "data": {
                    "uploadId": UPLOAD_ID,
                    "runId": manifest["runId"],
                    "expectedCount": manifest["counts"]["total"],
                },
            }
        if action == "chunk":
            self.chunk_calls.append(payload["chunkIndex"])
            for row in payload["records"]:
                if publish_record_hash(row) != row["recordHash"]:
                    return {"ok": False, "error": "record hash mismatch"}
                self.records[row["ticker"]] = row
            return {
                "ok": True,
                "data": {"uploadId": UPLOAD_ID, "receivedCount": len(self.records)},
            }
        assert action == "finalize"
        assert self.manifest is not None
        received = list(self.records.values())
        digest = records_hash(received)
        if (
            len(received) != self.manifest["counts"]["total"]
            or digest != self.manifest["recordsHash"]
        ):
            return {"ok": False, "error": "incomplete or corrupt publish"}
        return {
            "ok": True,
            "data": {
                "scanId": 1,
                "runId": self.manifest["runId"],
                "recordCount": len(received),
                "recordsHash": digest,
                "idempotent": False,
            },
        }


def test_verified_snapshot_round_trips_all_2200_rows(large_plan: CloudPublishPlan) -> None:
    assert len(large_plan.records) == 2200
    assert large_plan.manifest["counts"] == {
        "candidates": 2100,
        "excluded": 100,
        "total": 2200,
    }
    assert len(large_plan.chunk_payloads(UPLOAD_ID)) == 22
    assert large_plan.manifest["recordsHash"] == records_hash(large_plan.records)
    fields = {row["field"] for row in large_plan.manifest["fieldCatalog"]}
    assert {
        "primary_setup",
        "risk_level",
        "trade_plan.status",
        "trade_plan.entry_risk_pct",
        "setups.rwb_squeeze_thrust.triggered",
    } <= fields
    first = large_plan.records[0]
    assert first["record"]["trade_plan"]["status"] == "entry_ready"
    assert first["summary"]["rankingScore"] == first["record"]["focus_blend"]


def test_wrong_record_hash_is_rejected_before_any_network_call(
    large_plan: CloudPublishPlan,
) -> None:
    rows = [copy.deepcopy(row) for row in large_plan.records]
    rows[0]["recordHash"] = "0" * 64
    corrupted = CloudPublishPlan(
        manifest=copy.deepcopy(large_plan.manifest),
        records=tuple(rows),
        chunk_size=large_plan.chunk_size,
    )

    with pytest.raises(CloudPublishError, match="record hash mismatch"):
        verify_cloud_publish_plan(corrupted)


def test_chunk_payloads_are_safe_when_delivered_out_of_order_and_twice(
    large_plan: CloudPublishPlan,
) -> None:
    edge = MemoryEdge()
    endpoint = "https://example.supabase.co/functions/v1/stockscout-eod-publish"
    edge(endpoint, "oidc-fixture", large_plan.begin_payload)
    chunks = list(large_plan.chunk_payloads(UPLOAD_ID))
    delivery = [*reversed(chunks), chunks[0], chunks[-1]]
    for payload in delivery:
        response = edge(endpoint, "oidc-fixture", payload)
        assert response["ok"] is True
    finalized = edge(endpoint, "oidc-fixture", large_plan.finalize_payload(UPLOAD_ID))

    assert finalized["ok"] is True
    assert finalized["data"]["recordCount"] == 2200
    assert len(edge.records) == 2200
    assert edge.chunk_calls[:3] == [21, 20, 19]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1"),
        (-0.0, "0"),
        (1e-7, "1e-7"),
        (1e-6, "0.000001"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
    ],
)
def test_stable_json_number_format_matches_edge_javascript(value: float, expected: str) -> None:
    assert stable_json_text(value) == expected


def test_retry_reuses_the_identical_idempotent_request_body(monkeypatch) -> None:
    requests: list[bytes] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b'{"ok":true,"data":{"receivedCount":1}}'

    def opener(request, timeout):
        assert timeout == 90
        requests.append(request.data)
        if len(requests) == 1:
            raise URLError("response lost after a potentially successful write")
        return Response()

    monkeypatch.setattr("stockscout_eod.cloud_publish.urlopen", opener)
    sleeps: list[float] = []
    result = post_json_with_retry(
        "https://example.supabase.co/functions/v1/publish",
        "token",
        {"action": "chunk", "chunkIndex": 4, "records": [{"ticker": "AAA"}]},
        sleeper=sleeps.append,
    )

    assert result["ok"] is True
    assert requests[0] == requests[1]
    assert sleeps == [1]


def test_python_record_wrapper_hash_is_canonical_and_stable() -> None:
    wrapper = {
        "source": "candidate",
        "ticker": "AAA",
        "scanOrder": 1.0,
        "record": {"nested": {"value": 2.0}, "labels": ["rwb", "ready"]},
    }
    reordered = {
        "record": {"labels": ["rwb", "ready"], "nested": {"value": 2}},
        "scanOrder": 1,
        "ticker": "AAA",
        "source": "candidate",
    }
    assert stable_json_text(wrapper) == stable_json_text(reordered)
    assert publish_record_hash(wrapper) == publish_record_hash(reordered)


def test_http_error_body_is_never_treated_as_success(monkeypatch) -> None:
    def opener(_request, timeout):
        assert timeout == 90
        raise HTTPError(
            "https://example.supabase.co",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"ok":false,"error":"record hash mismatch"}'),
        )

    monkeypatch.setattr("stockscout_eod.cloud_publish.urlopen", opener)
    with pytest.raises(CloudPublishError, match="record hash mismatch"):
        post_json_with_retry(
            "https://example.supabase.co/functions/v1/publish",
            "token",
            {"action": "finalize", "uploadId": UPLOAD_ID},
        )


def test_alert_evaluator_rejects_non_https_endpoint_before_requesting_oidc() -> None:
    with pytest.raises(CloudPublishError, match="must be HTTPS"):
        evaluate_cloud_alerts(endpoint="http://localhost/evaluate")
