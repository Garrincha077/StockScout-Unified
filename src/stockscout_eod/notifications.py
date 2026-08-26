"""Production Telegram delivery from one validated scan envelope."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from stock_scout.notifications.telegram import (
    TelegramConfig,
    _escape_md_v2,
    build_daily_digest_parts,
    send_message_parts,
    split_telegram_message,
)
from stock_scout.scoring.models import Candidate
from stockscout_eod.jsonio import canonical_json_bytes, sha256_bytes
from stockscout_eod.market_cache import _oidc_token, _post
from stockscout_eod.runner import load_raw_scan


@dataclass(frozen=True)
class DeliveryProgress:
    last_successful_part: int = 0
    completed: bool = False


class DeliveryLedger(Protocol):
    def get(self, *, digest_key: str, content_hash: str, total_parts: int) -> DeliveryProgress: ...

    def mark(
        self,
        *,
        digest_key: str,
        content_hash: str,
        total_parts: int,
        last_successful_part: int,
    ) -> None: ...


class EdgeDeliveryLedger:
    def __init__(self, endpoint: str, *, token: str | None = None) -> None:
        self.endpoint = endpoint
        self.token = token or _oidc_token()

    def get(self, *, digest_key: str, content_hash: str, total_parts: int) -> DeliveryProgress:
        digest_type, session_date = _digest_identity(digest_key)
        data = _post(
            self.endpoint,
            self.token,
            {
                "action": "delivery_get",
                "digestType": digest_type,
                "sessionDate": session_date,
            },
        )
        if data.get("contentHash") != content_hash or data.get("partCount") != total_parts:
            return DeliveryProgress()
        return DeliveryProgress(
            last_successful_part=int(data.get("lastPart") or 0),
            completed=bool(data.get("completed")),
        )

    def mark(
        self,
        *,
        digest_key: str,
        content_hash: str,
        total_parts: int,
        last_successful_part: int,
    ) -> None:
        digest_type, session_date = _digest_identity(digest_key)
        _post(
            self.endpoint,
            self.token,
            {
                "action": "delivery_progress",
                "digestType": digest_type,
                "sessionDate": session_date,
                "contentHash": content_hash,
                "partCount": total_parts,
                "lastPart": last_successful_part,
                "completed": last_successful_part == total_parts,
            },
        )


def _digest_identity(digest_key: str) -> tuple[str, str]:
    digest_type, separator, session_date = digest_key.partition(":")
    if (
        separator != ":"
        or digest_type not in {"daily", "operational_error"}
        or len(session_date) != 10
    ):
        raise ValueError("invalid delivery digest key")
    return digest_type, session_date


def telegram_config_from_environment() -> TelegramConfig:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    return TelegramConfig(bot_token=token, chat_id=chat_id)


def digest_content_hash(parts: list[str]) -> str:
    return sha256_bytes(canonical_json_bytes(parts))


def build_snapshot_digest_parts(
    scan_path: str | Path,
    *,
    cloud_sync_status: str,
    report_link: str,
) -> tuple[str, list[str]]:
    scan = load_raw_scan(scan_path)
    candidates = [Candidate.model_validate(row) for row in scan.candidates]
    metadata: dict[str, Any] = {
        **scan.stats,
        "regime": scan.market.get("regime") or {},
        "cloud_sync_status": cloud_sync_status,
    }
    parts = build_daily_digest_parts(
        as_of=scan.session_date,
        candidates=candidates,
        ranked=None,
        metadata=metadata,
        report_link=report_link,
    )
    return f"daily:{scan.session_date}", parts


def deliver_parts(
    cfg: TelegramConfig,
    *,
    digest_key: str,
    parts: list[str],
    ledger: DeliveryLedger,
) -> bool:
    if not parts or any(len(part) > 3900 for part in parts):
        raise ValueError("Telegram parts must be non-empty and at most 3900 characters")
    content_hash = digest_content_hash(parts)
    progress = ledger.get(
        digest_key=digest_key,
        content_hash=content_hash,
        total_parts=len(parts),
    )
    if progress.completed:
        return True
    start_part = min(max(progress.last_successful_part, 0), len(parts))

    def on_sent(sent: int, total: int) -> None:
        ledger.mark(
            digest_key=digest_key,
            content_hash=content_hash,
            total_parts=total,
            last_successful_part=sent,
        )

    return send_message_parts(
        cfg,
        parts,
        start_part=start_part,
        on_part_sent=on_sent,
    )


def deliver_snapshot_digest(
    scan_path: str | Path,
    *,
    endpoint: str,
    cloud_sync_status: str,
    report_link: str,
) -> bool:
    digest_key, parts = build_snapshot_digest_parts(
        scan_path,
        cloud_sync_status=cloud_sync_status,
        report_link=report_link,
    )
    return deliver_parts(
        telegram_config_from_environment(),
        digest_key=digest_key,
        parts=parts,
        ledger=EdgeDeliveryLedger(endpoint),
    )


def deliver_operational_error(*, endpoint: str, session_date: str, message: str) -> bool:
    text = _escape_md_v2(
        f"StockScout EOD {session_date}\nOperational error: {message[:1000]}"
    )
    parts = split_telegram_message(text)
    return deliver_parts(
        telegram_config_from_environment(),
        digest_key=f"operational_error:{session_date}",
        parts=parts,
        ledger=EdgeDeliveryLedger(endpoint),
    )
