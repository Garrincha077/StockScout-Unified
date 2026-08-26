from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest


def _canonical_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_allowlisted_sources_still_match_private_workspace() -> None:
    private_root_value = os.environ.get("STOCKSCOUT_PRIVATE_ROOT")
    if not private_root_value:
        pytest.skip("STOCKSCOUT_PRIVATE_ROOT is intentionally unavailable in public CI")
    private_root = Path(private_root_value).resolve()
    if not (private_root / "src" / "stock_scout").is_dir():
        pytest.fail("STOCKSCOUT_PRIVATE_ROOT does not contain src/stock_scout")

    root = Path(__file__).resolve().parents[1]
    snapshot = json.loads((root / "config" / "engine_snapshot.json").read_text(encoding="utf-8"))
    public_engine = root / "src" / "stock_scout"
    private_engine = private_root / "src" / "stock_scout"
    mismatches: list[str] = []
    for relative, record in snapshot["frozenFiles"].items():
        expected = record["sha256"]
        public_hash = _canonical_sha256(public_engine / relative)
        private_hash = _canonical_sha256(private_engine / relative)
        if public_hash != expected or private_hash != expected:
            mismatches.append(
                f"{relative} ({record['source']}): public={public_hash} private={private_hash} expected={expected}"
            )
    assert not mismatches, "allowlist parity failed:\n" + "\n".join(mismatches)
