#!/usr/bin/env python3
"""Stamp and verify authoritative scan/publication identity in manifest.json.

The canonical ``latest.json`` remains immutable. This adapter enriches only the
frontend manifest with provenance already known to the source/publication
workflow: scan metadata, canonical SHA, source repo/ref and publication identity.
Re-publishing the same source keeps ``scanId`` stable even when publication run
metadata changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

SOURCE_REPOSITORY = "Garrincha077/stock-screener2"
SOURCE_REF = "main"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def deterministic_scan_id(generated_at: str, source_sha: str) -> str:
    """Stable identity for one exact canonical snapshot."""
    digest = hashlib.sha256(f"{generated_at}|{source_sha}".encode("utf-8")).hexdigest()
    return f"scan-{digest[:24]}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected object JSON in {path}")
    return payload


def verify_identity(
    manifest: Mapping[str, Any],
    canonical_bytes: bytes,
    canonical: Mapping[str, Any],
    source_meta: Mapping[str, Any],
    *,
    source_repository: str = SOURCE_REPOSITORY,
    source_ref: str = SOURCE_REF,
) -> None:
    generated_at = _clean(canonical.get("generatedAt"))
    source_sha = _sha256(canonical_bytes)
    meta_generated = _clean(source_meta.get("generated_at_utc"))
    workflow_run_id = _clean(source_meta.get("workflow_run_id"))
    expected_repository = _clean(source_repository)
    expected_ref = _clean(source_ref)
    if not generated_at:
        raise SystemExit("Canonical snapshot has no generatedAt")
    if not expected_repository or not expected_ref:
        raise SystemExit("Source repository/ref is required")
    if meta_generated != generated_at:
        raise SystemExit(
            f"Source metadata generated_at_utc mismatch: meta={meta_generated!r} canonical={generated_at!r}"
        )
    if not workflow_run_id:
        raise SystemExit("Source metadata has no workflow_run_id")
    if _clean(manifest.get("generatedAt")) != generated_at:
        raise SystemExit("Manifest generatedAt does not match canonical snapshot")

    provenance = manifest.get("provenance") or {}
    source = provenance.get("source") or {}
    publication = provenance.get("publication") or {}
    if _clean(source.get("sha256")) != source_sha:
        raise SystemExit("Manifest canonical source SHA does not match latest.json bytes")
    if _clean(publication.get("sourceSha256")) != source_sha:
        raise SystemExit("Manifest publication source SHA does not match canonical source SHA")

    expected_scan_id = deterministic_scan_id(generated_at, source_sha)
    if _clean(manifest.get("scanId")) != expected_scan_id:
        raise SystemExit("Manifest scanId is not deterministic for this canonical snapshot")
    if _clean(source.get("workflowRunId")) != workflow_run_id:
        raise SystemExit("Manifest source workflowRunId does not match scan metadata")
    if _clean(source.get("generatedAt")) != generated_at:
        raise SystemExit("Manifest source generatedAt does not match canonical snapshot")
    if _clean(source.get("repository")) != expected_repository or _clean(source.get("ref")) != expected_ref:
        raise SystemExit("Manifest source repository/ref is not authoritative")


def stamp_manifest(
    manifest_path: Path,
    canonical_path: Path,
    source_meta_path: Path,
    *,
    source_repository: str = SOURCE_REPOSITORY,
    source_ref: str = SOURCE_REF,
    publication_repository: str | None = None,
    publication_ref: str | None = None,
    publication_run_id: str | None = None,
    publication_commit_sha: str | None = None,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    canonical_bytes = canonical_path.read_bytes()
    canonical = json.loads(canonical_bytes)
    if not isinstance(canonical, dict):
        raise SystemExit("Canonical snapshot must be an object")
    source_meta = _read_json(source_meta_path)

    generated_at = _clean(canonical.get("generatedAt"))
    source_repository = _clean(source_repository)
    source_ref = _clean(source_ref)
    if not generated_at:
        raise SystemExit("Canonical snapshot has no generatedAt")
    if not source_repository or not source_ref:
        raise SystemExit("Source repository/ref is required")
    if _clean(source_meta.get("generated_at_utc")) != generated_at:
        raise SystemExit("Source scan metadata does not belong to canonical latest.json")

    source_sha = _sha256(canonical_bytes)
    provenance = manifest.setdefault("provenance", {})
    source = provenance.setdefault("source", {})
    publication = provenance.setdefault("publication", {})

    source.update(
        {
            "repository": source_repository,
            "ref": source_ref,
            "workflowRunId": _clean(source_meta.get("workflow_run_id")),
            "workflowRunAttempt": _clean(source_meta.get("workflow_run_attempt")),
            "sourceCommit": _clean(source_meta.get("source_commit")),
            "generatedAt": generated_at,
        }
    )
    manifest["scanId"] = deterministic_scan_id(generated_at, source_sha)

    publication_repository = _clean(publication_repository or os.getenv("GITHUB_REPOSITORY"))
    publication_ref = _clean(publication_ref or os.getenv("GITHUB_REF_NAME"))
    publication_run_id = _clean(publication_run_id or os.getenv("GITHUB_RUN_ID"))
    publication_commit_sha = _clean(publication_commit_sha or os.getenv("GITHUB_SHA"))
    publication.update(
        {
            "repository": publication_repository,
            "ref": publication_ref,
            "workflowRunId": publication_run_id,
            "commitSha": publication_commit_sha,
        }
    )
    if publication_repository and publication_run_id:
        publication["publicationId"] = f"{publication_repository}#{publication_run_id}"

    verify_identity(
        manifest,
        canonical_bytes,
        canonical,
        source_meta,
        source_repository=source_repository,
        source_ref=source_ref,
    )
    encoded = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    temp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temp.write_bytes(encoded)
    temp.replace(manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="frontend/public/data/manifest.json")
    parser.add_argument("--canonical", default="frontend/public/data/latest.json")
    parser.add_argument("--source-meta", default="/tmp/stable-scan-meta.json")
    parser.add_argument("--source-repository", default=SOURCE_REPOSITORY)
    parser.add_argument("--source-ref", default=SOURCE_REF)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    canonical_path = Path(args.canonical)
    source_meta_path = Path(args.source_meta)
    if args.verify_only:
        manifest = _read_json(manifest_path)
        canonical_bytes = canonical_path.read_bytes()
        canonical = json.loads(canonical_bytes)
        source_meta = _read_json(source_meta_path)
        verify_identity(
            manifest,
            canonical_bytes,
            canonical,
            source_meta,
            source_repository=args.source_repository,
            source_ref=args.source_ref,
        )
        print(f"Verified scan identity {manifest.get('scanId')} source run {(manifest.get('provenance') or {}).get('source', {}).get('workflowRunId')}")
        return

    manifest = stamp_manifest(
        manifest_path,
        canonical_path,
        source_meta_path,
        source_repository=args.source_repository,
        source_ref=args.source_ref,
    )
    print(
        "Stamped scan identity "
        f"{manifest.get('scanId')} source run "
        f"{(manifest.get('provenance') or {}).get('source', {}).get('workflowRunId')} publication run "
        f"{(manifest.get('provenance') or {}).get('publication', {}).get('workflowRunId')}"
    )


if __name__ == "__main__":
    main()
