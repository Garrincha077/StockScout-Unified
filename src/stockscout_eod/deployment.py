"""Post-Pages activation check used before cloud publication."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from stockscout_eod.jsonio import sha256_bytes


def _fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "StockScout-EOD/0.1"})
    with urlopen(request, timeout=30) as response:
        return response.read()


def verify_pages_activation(
    *,
    public_dir: str | Path,
    manifest_url: str,
    attempts: int = 8,
    fetcher: Callable[[str], bytes] = _fetch,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    parts = urlsplit(manifest_url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("Pages manifest URL must be absolute HTTPS")
    local_bytes = (Path(public_dir).resolve() / "data" / "manifest.json").read_bytes()
    local = json.loads(local_bytes)
    expected_hash = sha256_bytes(local_bytes)
    last_error = "not fetched"
    for attempt in range(max(1, attempts)):
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["activation"] = expected_hash[:16]
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        try:
            remote_bytes = fetcher(url)
            remote = json.loads(remote_bytes)
            if remote.get("runId") != local.get("runId"):
                last_error = f"runId={remote.get('runId')}"
            elif sha256_bytes(remote_bytes) != expected_hash:
                last_error = "manifest hash mismatch"
            else:
                return {
                    "runId": local["runId"],
                    "manifestHash": expected_hash,
                    "attempt": attempt + 1,
                }
        except Exception as exc:  # CDN propagation/network errors are retried
            last_error = str(exc)
        if attempt + 1 < attempts:
            sleeper(min(15.0, 2.0 + attempt * 2.0))
    raise RuntimeError(f"Pages activation was not observable: {last_error}")
