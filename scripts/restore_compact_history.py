"""Best-effort restore of only the active compact histories from GitHub Pages."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

MODES = ("bottom-fishing", "next", "ryan-original")
BASE = "https://garrincha077.github.io/StockScout-Unified/data/"


def fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "StockScout-Unified/1.0"}), timeout=20) as response:
        return response.read()


def main(public_dir: str = "frontend/public") -> None:
    root = Path(public_dir).resolve() / "data"
    for mode in MODES:
        try:
            manifest_url = urljoin(BASE, f"modes/{mode}/manifest.json")
            manifest_bytes = fetch(manifest_url)
            manifest = json.loads(manifest_bytes)
            history_path = str(manifest["assets"]["history"]["path"])
            history_bytes = fetch(urljoin(BASE, f"modes/{mode}/{history_path}"))
            mode_root = root / "modes" / mode
            target = mode_root / history_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(history_bytes)
            (mode_root / "manifest.json").write_bytes(manifest_bytes)
            print(f"restored {mode} compact history")
        except Exception as exc:
            print(f"no previous {mode} history: {type(exc).__name__}")


if __name__ == "__main__":
    main()
