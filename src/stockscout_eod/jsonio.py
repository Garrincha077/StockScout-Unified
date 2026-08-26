from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any


def json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): json_compatible(nested) for key, nested in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [json_compatible(nested) for nested in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        try:
            return json_compatible(value.item())
        except (TypeError, ValueError):
            pass
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        json_compatible(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: str | Path, value: Any) -> bytes:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value)
    destination.write_bytes(payload)
    return payload


def atomic_write_json(path: str | Path, value: Any) -> bytes:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    payload = canonical_json_bytes(value)
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return payload
