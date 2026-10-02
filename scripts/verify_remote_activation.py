"""Verify the exact active Pages run and every mode-manifest hash."""
from __future__ import annotations

import argparse
import hashlib
import json
from urllib.parse import urljoin
from urllib.request import Request, urlopen

BASE = "https://garrincha077.github.io/StockScout-Unified/data/"


def fetch(path: str) -> bytes:
    request = Request(urljoin(BASE, path), headers={"Cache-Control": "no-cache", "User-Agent": "StockScout-Unified/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--session-date", default=None)
    args = parser.parse_args()
    unified = json.loads(fetch(f"manifest.json?run={args.run_id}"))
    if unified.get("runId") != args.run_id or unified.get("status") != "healthy":
        raise SystemExit(f"Pages activation mismatch: expected {args.run_id}, got {unified.get('runId')}")
    if args.session_date and unified.get("sessionDate") != args.session_date:
        raise SystemExit(f"Pages session mismatch: expected {args.session_date}, got {unified.get('sessionDate')}")
    for mode in ("bottom-fishing", "next", "ryan-original"):
        pointer = unified["modes"][mode]
        payload = fetch(f"{pointer['manifestPath']}?run={args.run_id}")
        if hashlib.sha256(payload).hexdigest() != pointer["manifestSha256"]:
            raise SystemExit(f"remote manifest hash mismatch: {mode}")
        manifest = json.loads(payload)
        if manifest.get("runId") != args.run_id or manifest.get("mode") != mode:
            raise SystemExit(f"remote manifest identity mismatch: {mode}")
        if args.session_date and manifest.get("sessionDate") != args.session_date:
            raise SystemExit(f"remote manifest session mismatch: {mode}")
    print(f"Verified active Pages run {args.run_id} across all three modes")


if __name__ == "__main__":
    main()
