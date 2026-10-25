"""Fail-closed tests for one-shot preservation of original Unified EOD Pages evidence."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import preserve_unified_eod_evidence as evidence


def make_zip(root: Path, source: dict, *, corrupt_day: bool = False,
             corrupt_run: bool = False, omit_mode: bool = False) -> Path:
    tar_path = root / "artifact.tar"
    members = evidence.REQUIRED_MANIFESTS[:-1] if omit_mode else evidence.REQUIRED_MANIFESTS
    with tarfile.open(tar_path, "w") as tar:
        for index, member in enumerate(members):
            date = "2026-10-14" if corrupt_day and index == 1 else source["sessionDate"]
            run = 1 if corrupt_run and index == 2 else source["workflowRunId"]
            payload = json.dumps({
                "sessionDate": date,
                "runId": date + "-eod-" + str(run) + "-1",
            }).encode()
            info = tarfile.TarInfo("./" + member)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    path = root / "pages.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as packed:
        packed.write(tar_path, "artifact.tar")
    return path


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.source = evidence.SOURCES[0]

    def test_exact_source_identity_verified_for_all_four_manifests(self):
        with tempfile.TemporaryDirectory() as d:
            identity = evidence.verify_pages_bundle(make_zip(Path(d), self.source), self.source)
        self.assertEqual(identity["sessionDate"], "2026-10-15")
        self.assertEqual(identity["runId"], "2026-10-15-eod-38825335004-1")
        self.assertEqual(len(identity["verifiedManifestPaths"]), 4)

    def test_mismatched_session_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive = make_zip(Path(d), self.source, corrupt_day=True)
            with self.assertRaisesRegex(ValueError, "session date mismatch"):
                evidence.verify_pages_bundle(archive, self.source)

    def test_mismatched_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive = make_zip(Path(d), self.source, corrupt_run=True)
            with self.assertRaisesRegex(ValueError, "workflow run identity mismatch"):
                evidence.verify_pages_bundle(archive, self.source)

    def test_missing_mode_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive = make_zip(Path(d), self.source, omit_mode=True)
            with self.assertRaisesRegex(ValueError, "missing source manifests"):
                evidence.verify_pages_bundle(archive, self.source)

    def test_source_artifact_digest_is_pinned(self):
        row = self.source
        data = {"artifacts": [{
            "id": row["artifactId"], "name": row["artifactName"],
            "size_in_bytes": row["artifactSize"], "digest": row["artifactDigest"],
            "expires_at": row["expiresAt"], "expired": False,
            "workflow_run": {"head_sha": row["headSha"]},
        }]}
        with patch.object(evidence, "_gh", return_value=data):
            evidence._source_api_check(row)
        changed = json.loads(json.dumps(data))
        changed["artifacts"][0]["digest"] = "sha256:incorrect"
        with patch.object(evidence, "_gh", return_value=changed):
            with self.assertRaisesRegex(ValueError, "metadata changed"):
                evidence._source_api_check(row)

    def test_release_requires_draft_and_matching_original_assets(self):
        manifest = {
            "sources": [{
                "archivedName": "a.zip", "verifiedSize": 123,
                "verifiedSha256": "sha256:abc",
            }]
        }
        release = {"draft": True, "html_url": "https://github.com/x/y/releases/tag/t",
                   "assets": [
                       {"name": "a.zip", "size": 123, "digest": "sha256:abc"},
                       {"name": "source-evidence-manifest.json", "size": 10},
                   ]}
        with patch.object(evidence, "_gh", return_value=release):
            evidence.verify_release(manifest, "x/y")
        release["draft"] = False
        with patch.object(evidence, "_gh", return_value=release):
            with self.assertRaisesRegex(ValueError, "private draft"):
                evidence.verify_release(manifest, "x/y")
        release["draft"] = True
        release["assets"][0]["digest"] = "sha256:bad"
        with patch.object(evidence, "_gh", return_value=release):
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                evidence.verify_release(manifest, "x/y")


if __name__ == "__main__":
    unittest.main()
