"""Verify and deliver Trend Birth stage-change alerts through Unified's Telegram ledger."""
from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.parse import urlencode

from stock_scout.notifications.telegram import _escape_md_v2, split_telegram_message
from stockscout_unified.gridview_notification import (
    DEFAULT_GRID_URL,
    fetch_bytes,
    verify_publication,
)
from stockscout_unified.notifications import deliver_series

ALERT_PATH = "/data/trend-birth-alerts.json"
ALERT_SCHEMA = "trend-birth-alerts-v1"
ALLOWED_KINDS = {"ready", "trigger", "invalidated"}


def _expected_dashboard_url(grid_url: str, snapshot_id: str) -> str:
    return grid_url.rstrip("/") + "/?" + urlencode({"snapshot": snapshot_id})


def verify_alert_payload(grid_url: str = DEFAULT_GRID_URL) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify the deployed alert payload belongs to the exact verified GridView snapshot."""
    manifest, _summary = verify_publication(grid_url)
    payload = json.loads(fetch_bytes(grid_url.rstrip("/") + ALERT_PATH))
    if payload.get("schemaVersion") != ALERT_SCHEMA:
        raise ValueError("Trend Birth alert payload schema mismatch")
    if payload.get("sessionDate") != manifest.get("sessionDate"):
        raise ValueError("Trend Birth alert payload session mismatch")
    expected_dashboard = _expected_dashboard_url(grid_url, str(manifest["snapshotId"]))
    if payload.get("dashboardUrl") != expected_dashboard:
        raise ValueError("Trend Birth alert dashboard link does not match verified snapshot")

    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Trend Birth alert messages must be a list")
    if payload.get("baselineOnly") is True and messages:
        raise ValueError("Baseline-only Trend Birth payload must not contain messages")

    trigger_tickers: set[str] = set()
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Trend Birth alert message must be an object")
        kind = str(message.get("kind") or "")
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"Unsupported Trend Birth alert kind: {kind}")
        text = str(message.get("text") or "")
        if not text or expected_dashboard not in text:
            raise ValueError("Every Trend Birth alert must contain the verified dashboard link")
        if kind == "trigger":
            ticker = str(message.get("ticker") or "").strip().upper()
            if not ticker or ticker in trigger_tickers:
                raise ValueError("Trigger alerts require unique tickers")
            trigger_tickers.add(ticker)

    return manifest, payload


def _render_markdown(text: str, dashboard_url: str) -> str:
    """Escape untrusted prose while preserving one clickable verified dashboard link."""
    marker = "TREND_BIRTH_DASHBOARD_LINK"
    needle = "View dashboard: " + dashboard_url
    if needle not in text:
        raise ValueError("Trend Birth alert is missing its dashboard link")
    replaced = text.replace(needle, marker)
    escaped = _escape_md_v2(replaced)
    link = f"[View dashboard]({_escape_md_v2(dashboard_url)})"
    return escaped.replace(_escape_md_v2(marker), link)


def build_series(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Convert the no-send Trend Birth payload to resumable Unified Telegram series."""
    if payload.get("baselineOnly") is True:
        return {}
    dashboard_url = str(payload.get("dashboardUrl") or "")
    series: dict[str, list[str]] = {}
    for message in payload.get("messages") or []:
        kind = str(message["kind"])
        if kind == "trigger":
            ticker = str(message["ticker"]).strip().upper()
            key = f"trend-birth-trigger-{ticker}"
        else:
            key = f"trend-birth-{kind}"
        if key in series:
            raise ValueError(f"Duplicate Trend Birth Telegram series: {key}")
        rendered = _render_markdown(str(message["text"]), dashboard_url)
        parts = split_telegram_message(rendered)
        if not parts or any(not part or len(part) > 3900 for part in parts):
            raise ValueError("Trend Birth Telegram series contains an empty or oversized part")
        series[key] = parts
    return series


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid-url", default=DEFAULT_GRID_URL)
    parser.add_argument("--delivery-endpoint", default="")
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()

    manifest, payload = verify_alert_payload(args.grid_url)
    series = build_series(payload)
    summary = {
        "sessionDate": manifest["sessionDate"],
        "baselineOnly": bool(payload.get("baselineOnly")),
        "readyCount": int(payload.get("readyCount") or 0),
        "triggerCount": int(payload.get("triggerCount") or 0),
        "invalidatedCount": int(payload.get("invalidatedCount") or 0),
        "series": list(series),
        "send": bool(args.send),
    }
    if not args.send:
        print(json.dumps({"dryRun": True, **summary}))
        return 0
    if not series:
        print(json.dumps({"delivered": False, "reason": "no-new-trend-birth-alerts", **summary}))
        return 0
    if not args.delivery_endpoint:
        raise ValueError("--delivery-endpoint is required for sending")
    delivered = deliver_series(series, endpoint=args.delivery_endpoint)
    print(json.dumps({"delivered": delivered, **summary}))
    return 0 if delivered else 1


if __name__ == "__main__":
    raise SystemExit(main())
