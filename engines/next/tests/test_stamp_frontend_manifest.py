import hashlib
import json

import pytest

from stamp_frontend_manifest import deterministic_scan_id, stamp_manifest, verify_identity


def fixture_files(tmp_path):
    canonical = {
        "generatedAt": "2026-08-21T22:21:16.292856+00:00",
        "market": {"scanDate": "2026-08-21"},
        "universe": [{"ticker": "AAA"}],
    }
    canonical_bytes = json.dumps(canonical, separators=(",", ":")).encode()
    source_sha = hashlib.sha256(canonical_bytes).hexdigest()
    manifest = {
        "manifestVersion": 2,
        "model": "stockscout-client-core-v2",
        "generatedAt": canonical["generatedAt"],
        "marketSession": {"date": "2026-08-21", "status": "closed", "timezone": "America/New_York"},
        "universe": 1,
        "provenance": {
            "source": {"kind": "canonical-audit", "path": "latest.json", "sha256": source_sha, "bytes": len(canonical_bytes)},
            "publication": {"kind": "frontend-projection", "model": "stockscout-client-core-v2", "sourceSha256": source_sha},
        },
        "assets": {},
    }
    meta = {
        "generated_at_utc": canonical["generatedAt"],
        "workflow_run_id": "32530930150",
        "workflow_run_attempt": "1",
        "source_commit": "8c7d3cefc2029b448ce4e6ec49c735090832dff6",
    }
    canonical_path = tmp_path / "latest.json"
    manifest_path = tmp_path / "manifest.json"
    meta_path = tmp_path / "latest_scan_meta.json"
    canonical_path.write_bytes(canonical_bytes)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return canonical, canonical_bytes, manifest_path, canonical_path, meta_path


def test_stamp_embeds_authoritative_source_and_publication_identity(tmp_path):
    canonical, canonical_bytes, manifest_path, canonical_path, meta_path = fixture_files(tmp_path)
    stamped = stamp_manifest(
        manifest_path,
        canonical_path,
        meta_path,
        publication_repository="Garrincha077/StockScreener-next",
        publication_ref="main",
        publication_run_id="999",
        publication_commit_sha="abc123",
    )

    source_sha = hashlib.sha256(canonical_bytes).hexdigest()
    assert stamped["scanId"] == deterministic_scan_id(canonical["generatedAt"], source_sha)
    assert stamped["provenance"]["source"] == {
        "kind": "canonical-audit",
        "path": "latest.json",
        "sha256": source_sha,
        "bytes": len(canonical_bytes),
        "repository": "Garrincha077/stock-screener2",
        "ref": "main",
        "workflowRunId": "32530930150",
        "workflowRunAttempt": "1",
        "sourceCommit": "8c7d3cefc2029b448ce4e6ec49c735090832dff6",
        "generatedAt": canonical["generatedAt"],
    }
    publication = stamped["provenance"]["publication"]
    assert publication["repository"] == "Garrincha077/StockScreener-next"
    assert publication["ref"] == "main"
    assert publication["workflowRunId"] == "999"
    assert publication["commitSha"] == "abc123"
    assert publication["publicationId"] == "Garrincha077/StockScreener-next#999"

    verify_identity(stamped, canonical_bytes, canonical, json.loads(meta_path.read_text()))


def test_scan_id_is_stable_across_publication_runs(tmp_path):
    _, _, manifest_path, canonical_path, meta_path = fixture_files(tmp_path)
    first = stamp_manifest(manifest_path, canonical_path, meta_path, publication_run_id="100")
    first_id = first["scanId"]

    source_sha = hashlib.sha256(canonical_path.read_bytes()).hexdigest()
    regenerated = {
        "manifestVersion": 2,
        "model": "stockscout-client-core-v2",
        "generatedAt": json.loads(canonical_path.read_text())["generatedAt"],
        "universe": 1,
        "provenance": {
            "source": {"kind": "canonical-audit", "path": "latest.json", "sha256": source_sha, "bytes": len(canonical_path.read_bytes())},
            "publication": {"kind": "frontend-projection", "model": "stockscout-client-core-v2", "sourceSha256": source_sha},
        },
        "assets": {},
    }
    manifest_path.write_text(json.dumps(regenerated), encoding="utf-8")
    second = stamp_manifest(manifest_path, canonical_path, meta_path, publication_run_id="101")
    assert second["scanId"] == first_id
    assert second["provenance"]["publication"]["workflowRunId"] == "101"


def test_stamp_supports_next_as_the_authoritative_scan_source(tmp_path):
    canonical, canonical_bytes, manifest_path, canonical_path, meta_path = fixture_files(tmp_path)
    stamped = stamp_manifest(
        manifest_path,
        canonical_path,
        meta_path,
        source_repository="Garrincha077/StockScreener-next",
        source_ref="main",
        publication_repository="Garrincha077/StockScreener-next",
        publication_ref="main",
        publication_run_id="32530930150",
        publication_commit_sha="abc123",
    )
    source_sha = hashlib.sha256(canonical_bytes).hexdigest()
    assert stamped["scanId"] == deterministic_scan_id(canonical["generatedAt"], source_sha)
    assert stamped["provenance"]["source"]["repository"] == "Garrincha077/StockScreener-next"
    assert stamped["provenance"]["source"]["ref"] == "main"
    assert stamped["provenance"]["source"]["workflowRunId"] == "32530930150"
    verify_identity(
        stamped,
        canonical_bytes,
        canonical,
        json.loads(meta_path.read_text()),
        source_repository="Garrincha077/StockScreener-next",
        source_ref="main",
    )


def test_stamp_fails_closed_when_source_meta_does_not_match_snapshot(tmp_path):
    _, _, manifest_path, canonical_path, meta_path = fixture_files(tmp_path)
    meta = json.loads(meta_path.read_text())
    meta["generated_at_utc"] = "2026-08-20T22:21:16.292856+00:00"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(SystemExit, match="does not belong"):
        stamp_manifest(manifest_path, canonical_path, meta_path)
