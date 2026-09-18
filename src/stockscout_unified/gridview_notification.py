"""Send the isolated Trend Birth GridView daily summary through Unified Telegram infrastructure."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from typing import Any

import requests

from stock_scout.notifications.telegram import _escape_md_v2, send_message_parts
from stockscout_eod.jsonio import canonical_json_bytes, sha256_bytes
from stockscout_unified.notifications import OidcLedger, _telegram_config

DEFAULT_SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/Garrincha077/StockScout-Trend-Birth/"
    "feature/unified-review-grid-lab/lab/data/latest.json"
)
DEFAULT_GRID_URL = "https://stockscout-trend-birth-review-lab.vercel.app"
SERIES = "trend-birth-gridview"


def snapshot_summary(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    session_date = str(source.get("sessionDate") or "").strip()
    dt.date.fromisoformat(session_date)

    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    candidate_count = int(payload.get("candidateCount") or len(candidates))
    kell = payload.get("kell") if isinstance(payload.get("kell"), dict) else {}
    gap = payload.get("kellGap") if isinstance(payload.get("kellGap"), dict) else {}
    multi_hit = sum(
        1
        for item in candidates
        if isinstance(item, dict) and len(set(item.get("sources") or [])) > 1
    )
    return {
        "sessionDate": session_date,
        "candidateCount": candidate_count,
        "kell3x": int(kell.get("qualifiedCount") or 0),
        "gapUp": int(gap.get("qualifiedCount") or 0),
        "multiHit": multi_hit,
    }


def render_message(summary: dict[str, Any], *, grid_url: str = DEFAULT_GRID_URL) -> str:
    return "\n".join(
        [
            f"📊 *Trend Birth GridView*  `{_escape_md_v2(summary['sessionDate'])}`",
            f"Kandidati: *{int(summary['candidateCount'])}*",
            (
                f"Kell 3x RVOL: *{int(summary['kell3x'])}* \\| "
                f"Gap Up: *{int(summary['gapUp'])}* \\| "
                f"Multi hit: *{int(summary['multiHit'])}*"
            ),
            f"[Open GridView]({_escape_md_v2(grid_url)})",
        ]
    )


def fetch_snapshot(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("GridView snapshot must be a JSON object")
    return payload


def deliver_once_per_session(
    payload: dict[str, Any],
    *,
    endpoint: str,
    min_session_date: str | None = None,
    grid_url: str = DEFAULT_GRID_URL,
) -> bool:
    summary = snapshot_summary(payload)
    session_date = summary["sessionDate"]
    if min_session_date and dt.date.fromisoformat(session_date) < dt.date.fromisoformat(min_session_date):
        print(f"GridView session {session_date} is below bootstrap floor {min_session_date}; skipping.")
        return True

    identity_hash = sha256_bytes(canonical_json_bytes({"sessionDate": session_date}))
    ledger = OidcLedger(endpoint)
    progress = ledger.get(SERIES, identity_hash, 1)
    if progress.completed:
        print(f"GridView Telegram summary already delivered for {session_date}.")
        return True

    cfg = _telegram_config()
    message = render_message(summary, grid_url=grid_url)

    def on_sent(sent: int, total: int) -> None:
        ledger.mark(SERIES, identity_hash, total, sent)

    ok = send_message_parts(cfg, [message], start_part=progress.last_part, on_part_sent=on_sent)
    if ok:
        print(
            "GridView Telegram summary delivered: "
            f"{session_date} candidates={summary['candidateCount']} "
            f"kell3x={summary['kell3x']} gap={summary['gapUp']} multi={summary['multiHit']}"
        )
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-url", default=DEFAULT_SNAPSHOT_URL)
    parser.add_argument("--grid-url", default=DEFAULT_GRID_URL)
    parser.add_argument("--delivery-endpoint", required=True)
    parser.add_argument("--min-session-date", default=None)
    args = parser.parse_args()

    payload = fetch_snapshot(args.snapshot_url)
    return 0 if deliver_once_per_session(
        payload,
        endpoint=args.delivery_endpoint,
        min_session_date=args.min_session_date,
        grid_url=args.grid_url,
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
