from __future__ import annotations

import hashlib
from pathlib import Path

import stock_scout


def _canonical_source_bytes(path: Path) -> bytes:
    """Return source bytes with platform line endings normalized to LF."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def tree_fingerprint(relative: str = "") -> str:
    root = Path(stock_scout.__file__).resolve().parent / relative
    digest = hashlib.sha256()
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    base = root.parent if root.is_file() else root
    for path in files:
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_canonical_source_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest()


def engine_versions() -> dict[str, str | int]:
    from stock_scout.scoring.trade_plan import TRADE_PLAN_VERSION

    return {
        "engine": tree_fingerprint(),
        "ranking": tree_fingerprint("scoring/focus_blend.py"),
        "detectors": tree_fingerprint("setups"),
        "tradePlan": TRADE_PLAN_VERSION,
    }
