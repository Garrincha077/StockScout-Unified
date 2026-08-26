"""Read/write derived research profiles without rewriting immutable reports."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SIDECAR_NAME = "ma_cluster_preferred_v1.json"


def load_preferred_sidecar(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / SIDECAR_NAME
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profiles, dict):
        return {}
    return {
        str(ticker).upper(): profile
        for ticker, profile in profiles.items()
        if isinstance(profile, dict)
    }


def merge_preferred_profiles(
    rows: list[dict[str, Any]], run_dir: Path
) -> list[dict[str, Any]]:
    profiles = load_preferred_sidecar(run_dir)
    if not profiles:
        return rows
    merged: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        profile = profiles.get(ticker)
        merged.append({**row, "ma_cluster_research": profile} if profile is not None else row)
    return merged


def write_preferred_sidecar(run_dir: Path, payload: dict[str, Any]) -> Path:
    """Atomically create/replace the derived sidecar only."""
    path = run_dir / SIDECAR_NAME
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path
