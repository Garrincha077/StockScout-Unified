"""Fail when the public provenance contract drifts from the vendored sources."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from stockscout_unified.contracts import MODE_SPECS

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    pins = json.loads((ROOT / "config" / "source_pins.json").read_text(encoding="utf-8"))
    baseline = json.loads(
        (ROOT / "engines" / "next" / "config" / "legacy_baseline.json").read_text(
            encoding="utf-8"
        )
    )
    if set(pins) != set(MODE_SPECS):
        raise SystemExit("source pin modes do not match the unified contract")
    for mode, spec in MODE_SPECS.items():
        pin = pins[mode]
        if pin.get("commit") != spec.source_commit or pin.get("priceBasis") != spec.price_basis:
            raise SystemExit(f"source pin mismatch: {mode}")
    if baseline.get("upstream_commit") != pins["ryan-original"]["commit"]:
        raise SystemExit("Ryan protected baseline disagrees with the unified source pin")
    for relative, expected in pins["next"].get("criticalFiles", {}).items():
        path = (ROOT / relative).resolve()
        if not path.is_file() or ROOT not in path.parents:
            raise SystemExit(f"pinned Next source is missing or outside the repository: {relative}")
        actual = sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"vendored Next source drifted from {pins['next']['commit']}: {relative}")
    print("Source pins agree: Bottom Fishing, Next, and frozen Ryan Original")


if __name__ == "__main__":
    main()
