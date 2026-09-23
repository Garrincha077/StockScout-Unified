"""Verify deployed snapshots and reserve Telegram delivery before network I/O.

The workflow must push the reservation before --send-reserved. Ambiguous delivery
is never retried automatically; Telegram has no idempotency key for sendMessage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from stock_scout.notifications.telegram import _escape_md_v2, sends_suppressed
from stockscout_unified.notifications import _telegram_config

DEFAULT_GRID_URL = "https://stockscout-trend-birth-review-lab.vercel.app"
DEFAULT_MARKER = ".state/trend-birth-gridview-last-sent.txt"
DEFAULT_LEDGER = ".state/trend-birth-gridview-deliveries.json"
DEFAULT_PUBLICATION_URL = (
    "https://raw.githubusercontent.com/Garrincha077/StockScout-Trend-Birth/"
    "feature/unified-review-grid-lab/lab/data/publication.json"
)
DEFAULT_UNIFIED_URL = "https://garrincha077.github.io/StockScout-Unified/data/manifest.json"
ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}--[a-f0-9]{64}"


def snapshot_summary(payload: dict[str, Any]) -> dict[str, Any]:
    session = payload["source"]["sessionDate"]
    dt.date.fromisoformat(session)
    candidates = payload["candidates"]
    if payload["candidateCount"] != len(candidates):
        raise ValueError("Candidate count mismatch")
    return {
        "sessionDate": session,
        "candidateCount": len(candidates),
        "kell3x": int((payload.get("kell") or {}).get("qualifiedCount") or 0),
        "gapUp": int((payload.get("kellGap") or {}).get("qualifiedCount") or 0),
        "multiHit": sum(len(set(item.get("sources") or [])) > 1 for item in candidates),
    }


def render_message(summary: dict[str, Any], *, grid_url: str) -> str:
    return "\n".join(
        [
            f"📊 *Trend Birth GridView*  `{_escape_md_v2(summary['sessionDate'])}`",
            f"Kandidati: *{int(summary['candidateCount'])}*",
            (
                f"Kell 3x RVOL: *{int(summary['kell3x'])}* \\| "
                f"Gap Up: *{int(summary['gapUp'])}* \\| Multi hit: *{int(summary['multiHit'])}*"
            ),
            f"[Open GridView]({_escape_md_v2(grid_url)})",
        ]
    )


def fetch_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=45, headers={"Cache-Control": "no-cache"})
    response.raise_for_status()
    return response.content


def verify_active_unified(manifest: dict[str, Any], content: bytes) -> dict[str, Any]:
    """Require the review publication to match the currently activated Unified scan."""
    active = json.loads(content)
    if active.get("status") != "healthy" or any(
        active.get(field) != manifest.get(field) for field in ("runId", "sessionDate")
    ):
        raise ValueError("Review is behind the active Unified scan")
    expected_sha = manifest.get("unifiedManifestSha256")
    if expected_sha and hashlib.sha256(content).hexdigest() != expected_sha:
        raise ValueError("Review is not bound to the active Unified activation")
    return active


def verify_publication(
    grid_url: str = DEFAULT_GRID_URL,
    expected_url: str = DEFAULT_PUBLICATION_URL,
    unified_url: str = DEFAULT_UNIFIED_URL,
) -> tuple[dict, dict]:
    """Verify the deployed pointer, archive, and snapshot-aware frontend."""
    base = grid_url.rstrip("/")
    manifest = json.loads(fetch_bytes(base + "/data/publication.json"))
    if manifest.get("schemaVersion") != "trend-birth-publication-v1":
        raise ValueError("Publication v1 is not deployed")
    if json.loads(fetch_bytes(expected_url)) != manifest:
        raise ValueError("The latest committed publication has not deployed yet")
    snapshot_id = manifest["snapshotId"]
    if not re.fullmatch(ID_PATTERN, snapshot_id):
        raise ValueError("Invalid snapshot ID")
    if manifest["snapshotPath"] != f"data/snapshots/{snapshot_id}.json":
        raise ValueError("Invalid archive path")
    content = fetch_bytes(base + "/" + manifest["snapshotPath"])
    digest = hashlib.sha256(content).hexdigest()
    if digest != manifest["sha256"] or snapshot_id != f"{manifest['runId']}--{digest}":
        raise ValueError("Deployed archive hash mismatch")
    snapshot = json.loads(content)
    for field in ("runId", "sessionDate"):
        if snapshot["source"][field] != manifest[field]:
            raise ValueError("Deployed archive identity mismatch")
    summary = snapshot_summary(snapshot)
    candidates = snapshot["candidates"]
    if len({item["ticker"] for item in candidates}) != len(candidates):
        raise ValueError("Duplicate ticker")
    charts = sum(bool(item.get("chartBars")) for item in candidates)
    if (
        charts != len(candidates)
        or snapshot["chartCount"] != charts
        or manifest["chartCount"] != charts
    ):
        raise ValueError("Incomplete chart coverage")
    if summary["candidateCount"] != manifest["candidateCount"]:
        raise ValueError("Publication count mismatch")
    if "snapshot-loader.js" not in fetch_bytes(base + "/").decode("utf-8"):
        raise ValueError("Snapshot-aware GridView is not deployed")
    loader = fetch_bytes(base + "/snapshot-loader.js").decode("utf-8")
    if "ReviewSnapshots" not in loader or "data/snapshots/" not in loader:
        raise ValueError("Snapshot loader is not deployed")
    if json.loads(fetch_bytes(base + "/data/publication.json")) != manifest:
        raise ValueError("Publication changed during verification")
    verify_active_unified(manifest, fetch_bytes(unified_url))
    return manifest, summary


def read_ledger(path: Path) -> dict:
    if not path.exists():
        return {"schemaVersion": 1, "sessions": {}}
    ledger = json.loads(path.read_text(encoding="utf-8"))
    if ledger.get("schemaVersion") != 1 or not isinstance(ledger.get("sessions"), dict):
        raise ValueError("Invalid delivery ledger")
    return ledger


def write_ledger(path: Path, ledger: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def reserve(
    manifest: dict,
    summary: dict,
    *,
    ledger_path: Path,
    marker_path: Path,
    reservation_id: str,
    grid_url: str = DEFAULT_GRID_URL,
) -> bool:
    session = manifest["sessionDate"]
    if not reservation_id:
        raise ValueError("A unique reservation ID is required")
    ledger = read_ledger(ledger_path)
    prior = ledger["sessions"].get(session)
    if prior:
        if prior["status"] != "sent":
            raise ValueError(
                f"Session {session} has unresolved delivery; reconcile before retrying"
            )
        return False
    if marker_path.exists():
        legacy = marker_path.read_text(encoding="utf-8").strip()
        if legacy and dt.date.fromisoformat(session) <= dt.date.fromisoformat(legacy):
            return False
    if ledger["sessions"] and session < max(ledger["sessions"]):
        raise ValueError("Refusing notification rollback")
    link = grid_url.rstrip("/") + "/?" + urlencode({"snapshot": manifest["snapshotId"]})
    ledger["sessions"][session] = {
        "status": "reserved",
        "reservationId": reservation_id,
        "snapshotId": manifest["snapshotId"],
        "runId": manifest["runId"],
        "gridUrl": link,
        "message": render_message(summary, grid_url=link),
    }
    write_ledger(ledger_path, ledger)
    return True


def send_reserved(ledger_path: Path, reservation_id: str) -> bool:
    if sends_suppressed():
        raise ValueError("Notifications are disabled; reservation was not sent")
    cfg = _telegram_config()
    ledger = read_ledger(ledger_path)
    records = [
        record
        for record in ledger["sessions"].values()
        if record["reservationId"] == reservation_id
    ]
    if len(records) != 1 or records[0]["status"] != "reserved":
        raise ValueError("No unique unsent reservation for this attempt")
    record = records[0]
    record["status"] = "uncertain"
    write_ledger(ledger_path, ledger)
    try:
        # Exactly one request; the generic Telegram helper retries ambiguous I/O.
        response = requests.post(
            f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage",
            data={
                "chat_id": cfg.chat_id,
                "text": record["message"],
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": "true",
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("ok") is not True:
            raise ValueError("Telegram did not confirm delivery")
        record["messageId"] = result["result"]["message_id"]
        record["status"] = "sent"
    except (requests.RequestException, ValueError, KeyError):
        # Exception URLs may contain the bot token; never log them.
        print("Telegram outcome unresolved. Automatic retry blocked; inspect delivery ledger.")
    write_ledger(ledger_path, ledger)
    return record["status"] == "sent"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid-url", default=DEFAULT_GRID_URL)
    parser.add_argument("--ledger-path", type=Path, default=Path(DEFAULT_LEDGER))
    parser.add_argument("--marker-path", type=Path, default=Path(DEFAULT_MARKER))
    parser.add_argument("--reservation-id", default="")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--send-reserved", action="store_true")
    args = parser.parse_args()
    if args.send_reserved:
        return 0 if send_reserved(args.ledger_path, args.reservation_id) else 1
    manifest, summary = verify_publication(args.grid_url)
    if args.prepare:
        if sends_suppressed():
            raise ValueError("Notifications are disabled")
        _telegram_config()
        prepared = reserve(
            manifest,
            summary,
            ledger_path=args.ledger_path,
            marker_path=args.marker_path,
            reservation_id=args.reservation_id,
            grid_url=args.grid_url,
        )
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"prepared={str(prepared).lower()}\n")
        print(f"Prepared: {prepared}; session: {manifest['sessionDate']}")
    else:
        print(json.dumps({"dryRun": True, "publication": manifest, "summary": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
