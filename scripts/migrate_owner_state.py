#!/usr/bin/env python3
"""Dry-run-first import of local owner state into StockScout-EOD Supabase tables."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

TICKER = __import__("re").compile(r"^[A-Z0-9._-]{1,20}$")


def _load(path: Path | None) -> Any:
    if path is None:
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _items(value: Any, key: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return value[key]
    raise ValueError(f"expected a JSON list or an object containing '{key}'")


def _ticker(value: Any) -> str:
    ticker = str(value or "").strip().upper()
    if not TICKER.fullmatch(ticker):
        raise ValueError("invalid ticker in owner-state input")
    return ticker


def _stable_id(kind: str, user_id: str, item: dict[str, Any]) -> str:
    identity = item.get("id")
    if identity is None:
        identity = hashlib.sha256(
            json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"stockscout-eod:{kind}:{user_id}:{identity}"))


def normalize_watchlists(value: Any, user_id: str) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("lists"), list):
        flattened: list[dict[str, Any]] = []
        for watchlist in value["lists"]:
            if not isinstance(watchlist, dict):
                raise ValueError("named watchlists must be objects")
            name = str(watchlist.get("name") or "Default")[:80]
            seen: set[str] = set()
            for item in watchlist.get("items") or []:
                if not isinstance(item, dict):
                    raise ValueError("watchlist items must be objects")
                ticker = _ticker(item.get("ticker"))
                if ticker in seen:
                    continue
                seen.add(ticker)
                flattened.append(
                    {
                        "name": name,
                        "ticker": ticker,
                        "notes": item.get("note"),
                    }
                )
            for raw_ticker in watchlist.get("tickers") or []:
                ticker = _ticker(raw_ticker)
                if ticker not in seen:
                    seen.add(ticker)
                    flattened.append({"name": name, "ticker": ticker})
        value = flattened
    rows: list[dict[str, Any]] = []
    for item in _items(value, "watchlists"):
        if isinstance(item, str):
            item = {"ticker": item}
        if not isinstance(item, dict):
            raise ValueError("watchlist entries must be tickers or objects")
        row = {
            "user_id": user_id,
            "name": str(item.get("name") or "Default")[:80],
            "ticker": _ticker(item.get("ticker")),
        }
        if item.get("notes") is not None:
            row["notes"] = str(item["notes"])[:1000]
        rows.append(row)
    return rows


def normalize_drawings(value: Any, user_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _items(value, "drawings"):
        if not isinstance(item, dict):
            raise ValueError("drawing entries must be objects")
        payload = item.get("payload", item)
        if not isinstance(payload, dict):
            raise ValueError("drawing payload must be an object")
        rows.append(
            {
                "id": _stable_id("drawing", user_id, item),
                "user_id": user_id,
                "ticker": _ticker(item.get("ticker")),
                "interval": str(item.get("interval") or "daily")[:20],
                "payload": payload,
            }
        )
    return rows


def normalize_alerts(value: Any, user_id: str) -> list[dict[str, Any]]:
    if isinstance(value, dict) and "alerts" not in value:
        flattened: list[dict[str, Any]] = []
        for kind in ("screen", "price", "drawing", "watchlist"):
            entries = value.get(kind, [])
            if not isinstance(entries, list):
                raise ValueError(f"alert group '{kind}' must be a list")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("alert entries must be objects")
                flattened.append({**entry, "_imported_kind": kind})
        value = flattened
    rows: list[dict[str, Any]] = []
    for item in _items(value, "alerts"):
        if not isinstance(item, dict):
            raise ValueError("alert entries must be objects")
        payload = item.get("payload", item)
        if not isinstance(payload, dict):
            raise ValueError("alert payload must be an object")
        ticker = item.get("ticker")
        row: dict[str, Any] = {
            "id": _stable_id("alert", user_id, item),
            "user_id": user_id,
            "name": str(
                item.get("name")
                or item.get("type")
                or item.get("_imported_kind")
                or "Imported alert"
            )[:120],
            "payload": payload,
            "enabled": bool(item.get("enabled", True)),
        }
        if ticker not in (None, ""):
            row["ticker"] = _ticker(ticker)
        rows.append(row)
    return rows


def _jwt_subject(token: str) -> str:
    try:
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        return str(uuid.UUID(str(payload["sub"])))
    except (IndexError, KeyError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("SUPABASE_USER_TOKEN has no valid UUID subject") from error


def _chunks(rows: list[dict[str, Any]], size: int = 100) -> Iterable[list[dict[str, Any]]]:
    for offset in range(0, len(rows), size):
        yield rows[offset : offset + size]


def _post_rows(
    base_url: str,
    publishable_key: str,
    user_token: str,
    table: str,
    conflict: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    url = (
        f"{base_url.rstrip('/')}/rest/v1/{table}?"
        + urllib.parse.urlencode({"on_conflict": conflict})
    )
    for batch in _chunks(rows):
        request = urllib.request.Request(
            url,
            data=json.dumps(batch, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "apikey": publishable_key,
                "authorization": f"Bearer {user_token}",
                "content-type": "application/json",
                "prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status not in (200, 201, 204):
                    raise RuntimeError(f"unexpected HTTP status {response.status}")
        except urllib.error.HTTPError as error:
            # Do not print response bodies: they can echo input or auth details.
            raise RuntimeError(f"{table} import failed with HTTP {error.code}") from error


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--watchlists", type=Path)
    result.add_argument("--drawings", type=Path)
    result.add_argument("--alerts", type=Path)
    result.add_argument(
        "--user-id",
        help="Owner UUID for dry-run. Apply mode derives and verifies it from SUPABASE_USER_TOKEN.",
    )
    result.add_argument(
        "--apply",
        action="store_true",
        help="Write rows. Without this flag the command only validates and counts.",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not any((args.watchlists, args.drawings, args.alerts)):
        raise ValueError("provide at least one explicit input path")

    token = os.getenv("SUPABASE_USER_TOKEN", "").strip()
    token_user = _jwt_subject(token) if args.apply else None
    supplied_user = str(uuid.UUID(args.user_id)) if args.user_id else None
    if args.apply and supplied_user and supplied_user != token_user:
        raise ValueError("--user-id does not match SUPABASE_USER_TOKEN subject")
    user_id = token_user or supplied_user
    if not user_id:
        raise ValueError("dry-run requires --user-id; apply requires SUPABASE_USER_TOKEN")

    watchlists = normalize_watchlists(_load(args.watchlists), user_id)
    drawings = normalize_drawings(_load(args.drawings), user_id)
    alerts = normalize_alerts(_load(args.alerts), user_id)
    print(
        f"validated owner-state rows: watchlists={len(watchlists)} "
        f"drawings={len(drawings)} alerts={len(alerts)}"
    )
    if not args.apply:
        print("dry-run only; no network request or file mutation performed")
        return 0

    base_url = os.getenv("SUPABASE_URL", "").strip()
    key = (
        os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
        or os.getenv("SUPABASE_ANON_KEY", "").strip()
    )
    if not base_url or not key or not token:
        raise ValueError(
            "apply requires SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, and SUPABASE_USER_TOKEN"
        )
    _post_rows(base_url, key, token, "eod_watchlists", "user_id,name,ticker", watchlists)
    _post_rows(base_url, key, token, "eod_drawings", "id", drawings)
    _post_rows(base_url, key, token, "eod_alerts", "id", alerts)
    print(
        f"applied owner-state rows: watchlists={len(watchlists)} "
        f"drawings={len(drawings)} alerts={len(alerts)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"owner-state migration failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
