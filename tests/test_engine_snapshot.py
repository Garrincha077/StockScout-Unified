from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _canonical_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_frozen_engine_and_promoted_product_sources_match_snapshot() -> None:
    root = Path(__file__).resolve().parents[1]
    snapshot = json.loads((root / "config" / "engine_snapshot.json").read_text(encoding="utf-8"))
    engine = root / "src" / "stock_scout"
    mismatches = []
    for group in ("frozenFiles", "promotedFiles"):
        for relative, record in snapshot.get(group, {}).items():
            expected = record["sha256"]
            actual = _canonical_sha256(engine / relative)
            if actual != expected:
                mismatches.append(f"{relative}: {actual} != {expected}")
    legacy = snapshot["legacyShadowSource"]
    adapter = root / legacy["adapterFile"]
    adapter_hash = _canonical_sha256(adapter)
    if adapter_hash != legacy["adapterSha256"]:
        mismatches.append(
            f"{legacy['adapterFile']}: {adapter_hash} != {legacy['adapterSha256']}"
        )
    assert legacy["affectsRanking"] is False
    assert not mismatches, "frozen production source drifted:\n" + "\n".join(mismatches)
