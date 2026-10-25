#!/usr/bin/env python3
"""Preserve exact expiring Unified Pages source artifacts without rewriting EOD history.

One-shot, fail-closed evidence capture. Assets go to a draft GitHub Release rather
than the growing Git repository or Vercel. No rescoring, EOD date insertion, or
source data invention is performed here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import zipfile

SOURCE_REPO = "Garrincha077/StockScout-Unified"
RELEASE_TAG = "tb-eod-source-evidence-2026-10-25"
SOURCES = (
    {
        "sessionDate": "2026-10-15",
        "workflowRunId": 38825335004,
        "headSha": "fc3950f70a9f04084e9e6de4e7bd7fade7abcc1e",
        "artifactId": 12016667723,
        "artifactName": "github-pages",
        "artifactSize": 162020086,
        "artifactDigest": "sha256:b8595b0e05da8d41cf8523b94b5d799a7e911efa697206cf84c97ed4e4f25e7a",
        "expiresAt": "2026-10-30T00:33:56Z",
    },
    {
        "sessionDate": "2026-10-19",
        "workflowRunId": 39347937228,
        "headSha": "512207547dfd8d6f15fda3ef70f92299e43313f8",
        "artifactId": 12235521722,
        "artifactName": "github-pages",
        "artifactSize": 159822532,
        "artifactDigest": "sha256:5e7041b976d902d12f6f5e84d84591d230ed9ee9e10df805e198cca41c9cf103",
        "expiresAt": "2026-11-03T01:56:55Z",
    },
)
REQUIRED_MANIFESTS = (
    "data/manifest.json",
    "data/modes/bottom-fishing/manifest.json",
    "data/modes/next/manifest.json",
    "data/modes/ryan-original/manifest.json",
)


def _gh(*args: str, binary: bool = False):
    output = subprocess.run(
        ["gh", *args], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout
    return output if binary else json.loads(output.decode("utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(part)
    return "sha256:" + digest.hexdigest()


def verify_pages_bundle(path: Path, source: dict) -> dict:
    """Validate ZIP CRC and tar-contained point-in-time source identity in place."""
    found: dict[str, dict] = {}
    with zipfile.ZipFile(path) as packed:
        if "artifact.tar" not in packed.namelist():
            raise ValueError("Pages artifact has no artifact.tar")
        bad = packed.testzip()
        if bad:
            raise ValueError("Invalid Pages ZIP CRC: " + bad)
        with packed.open("artifact.tar") as stream:
            with tarfile.open(fileobj=stream, mode="r|*") as tar:
                for member in tar:
                    name = member.name.removeprefix("./")
                    if not member.isfile() or name not in REQUIRED_MANIFESTS:
                        continue
                    handle = tar.extractfile(member)
                    if handle is None:
                        raise ValueError("Unreadable Pages manifest: " + name)
                    found[name] = json.load(handle)
                    if len(found) == len(REQUIRED_MANIFESTS):
                        break
    if set(found) != set(REQUIRED_MANIFESTS):
        raise ValueError("Pages bundle missing source manifests: " + str(sorted(set(REQUIRED_MANIFESTS) - set(found))))
    run_ids = set()
    for name in REQUIRED_MANIFESTS:
        manifest = found[name]
        if manifest.get("sessionDate") != source["sessionDate"]:
            raise ValueError("Pages session date mismatch: " + name)
        run_id = str(manifest.get("runId") or "")
        if not run_id.startswith(source["sessionDate"] + "-eod-" + str(source["workflowRunId"]) + "-"):
            raise ValueError("Pages workflow run identity mismatch: " + name)
        run_ids.add(run_id)
    if len(run_ids) != 1:
        raise ValueError("Disagreeing point-in-time run IDs within Pages bundle")
    return {"sessionDate": source["sessionDate"], "runId": run_ids.pop(),
            "verifiedManifestPaths": list(REQUIRED_MANIFESTS)}


def _source_api_check(source: dict) -> None:
    payload = _gh("api", f"repos/{SOURCE_REPO}/actions/runs/{source['workflowRunId']}/artifacts?per_page=100")
    matching = [a for a in payload.get("artifacts", []) if a.get("id") == source["artifactId"]]
    if len(matching) != 1:
        raise ValueError("Exact original artifact absent: " + str(source["artifactId"]))
    actual = matching[0]
    for key, expected in (
        ("name", source["artifactName"]),
        ("size_in_bytes", source["artifactSize"]),
        ("digest", source["artifactDigest"]),
        ("expires_at", source["expiresAt"]),
    ):
        if actual.get(key) != expected:
            raise ValueError("Original artifact metadata changed for " + str(source["artifactId"]) + ": " + key)
    if actual.get("expired"):
        raise ValueError("Original Pages artifact already expired")
    if (actual.get("workflow_run") or {}).get("head_sha") != source["headSha"]:
        raise ValueError("Original source commit mismatch")


def preserve(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    verified = []
    for source in SOURCES:
        _source_api_check(source)
        filename = "unified-eod-" + source["sessionDate"] + "-pages.zip"
        target = destination / filename
        if not target.exists():
            with target.open("wb") as output:
                subprocess.run([
                    "gh", "api", f"repos/{SOURCE_REPO}/actions/artifacts/{source['artifactId']}/zip"
                ], stdout=output, check=True, stderr=subprocess.PIPE)
        if target.stat().st_size != source["artifactSize"]:
            raise ValueError("Artifact ZIP size mismatch: " + filename)
        digest = _sha256(target)
        if digest != source["artifactDigest"]:
            raise ValueError("Artifact ZIP SHA256 mismatch: " + filename)
        identity = verify_pages_bundle(target, source)
        verified.append({**source, **identity, "archivedName": filename,
                         "verifiedSha256": digest, "verifiedSize": target.stat().st_size})
        print(json.dumps({"verified": source["sessionDate"], "runId": identity["runId"],
                          "sha256": digest, "bytes": target.stat().st_size}), flush=True)

    manifest = {
        "schemaVersion": "tb-original-unified-pages-evidence-v1",
        "sourceRepository": SOURCE_REPO,
        "preservedAtUtc": datetime.now(timezone.utc).isoformat(),
        "storage": "draft GitHub Release; not part of Git history or Vercel build",
        "releaseTag": RELEASE_TAG,
        "purpose": "Provenance preservation only: no historical stage or outcome mutation",
        "sources": verified,
    }
    (destination / "source-evidence-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_release(manifest: dict, repo: str, manifest_path: Path | None = None) -> None:
    releases = _gh("api", f"repos/{repo}/releases?per_page=100")
    matching = [item for item in releases if item.get("tag_name") == RELEASE_TAG]
    if len(matching) != 1:
        raise ValueError("Expected one exact draft evidence release for pinned tag")
    release = matching[0]
    if not release.get("draft"):
        raise ValueError("Evidence release must remain a private draft")
    assets = {a["name"]: a for a in release.get("assets", [])}
    expected = [(row["archivedName"], row["verifiedSize"], row["verifiedSha256"])
                for row in manifest["sources"]]
    expected.append(("source-evidence-manifest.json",
                     manifest_path.stat().st_size if manifest_path else None,
                     _sha256(manifest_path) if manifest_path else None))
    for name, size, digest in expected:
        asset = assets.get(name)
        if not asset:
            raise ValueError("Missing GitHub Release asset: " + name)
        if size is not None and asset.get("size") != size:
            raise ValueError("GitHub Release asset size mismatch: " + name)
        remote_digest = asset.get("digest")
        if digest is not None and remote_digest != digest:
            raise ValueError("GitHub Release asset digest mismatch: " + name)
    print(json.dumps({"status": "verified_draft_release", "tag": RELEASE_TAG,
                      "releaseUrl": release.get("html_url"), "assets": [n for n, _, _ in expected]}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--verify-release", action="store_true")
    args = parser.parse_args()
    if args.verify_release:
        manifest_path = args.output_dir / "source-evidence-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        verify_release(manifest, os.environ["GITHUB_REPOSITORY"], manifest_path)
    else:
        preserve(args.output_dir)


if __name__ == "__main__":
    main()
