#!/usr/bin/env python3
"""Fail if StockScout accidentally mutates the frozen upstream LEGACY engine.

The expected hashes are Git blob SHAs from RyanJHamby/stock-screener at the fork
baseline recorded in config/legacy_baseline.json.  Discovery/enrichment code may
call these source functions, but must not silently change them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "config" / "legacy_baseline.json"


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures: list[str] = []
    for rel, expected in manifest["files"].items():
        path = ROOT / rel
        if not path.exists():
            failures.append(f"missing {rel}")
            continue
        actual = git_blob_sha(path)
        if actual != expected:
            failures.append(f"{rel}: expected {expected}, got {actual}")

    if failures:
        print("LEGACY BASELINE VIOLATION")
        for item in failures:
            print(f" - {item}")
        raise SystemExit(1)

    print(
        "LEGACY baseline verified: "
        f"{manifest['upstream_repository']}@{manifest['upstream_commit']} "
        f"({len(manifest['files'])} protected source files)"
    )


if __name__ == "__main__":
    main()
