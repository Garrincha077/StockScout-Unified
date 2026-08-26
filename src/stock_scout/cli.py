"""Compatibility helpers used by the frozen multipart Telegram renderer tests.

The operator CLI and AI ranker from the private research app are deliberately
not part of this public package.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _load_telegram_marker(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - corrupt marker means safe retry
        return {}


def _telegram_marker_key(date: str, digest: str) -> str:
    return f"{digest}:{date}"


def _write_telegram_marker_entry(path: Path, key: str, entry: dict) -> None:
    data = _load_telegram_marker(path)
    data[key] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _mark_telegram_sent(
    path: Path,
    *,
    date: str,
    digest: str,
    content_hash: str | None = None,
    total_parts: int = 1,
) -> None:
    _write_telegram_marker_entry(
        path,
        _telegram_marker_key(date, digest),
        {
            "date": date,
            "digest": digest,
            "content_hash": content_hash,
            "total_parts": total_parts,
            "sent_parts": total_parts,
            "complete": True,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        },
    )
