"""Mode-isolated multipart Telegram rendering and resumable delivery."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from stock_scout.notifications.telegram import (
    TelegramConfig,
    _escape_md_v2,
    send_message_parts,
    split_telegram_message,
)
from stock_scout.scoring.models import Candidate
from stockscout_eod.github_oidc import github_oidc_token
from stockscout_eod.jsonio import canonical_json_bytes, sha256_bytes
from stockscout_eod.market_cache import _post
from stockscout_eod.notifications import build_snapshot_digest_parts


@dataclass(frozen=True)
class Progress:
    last_part: int = 0
    completed: bool = False


class Ledger(Protocol):
    def get(self, series: str, content_hash: str, total: int) -> Progress: ...
    def mark(self, series: str, content_hash: str, total: int, last_part: int) -> None: ...


class OidcLedger:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.token = github_oidc_token("stockscout-unified-operations")

    def get(self, series: str, content_hash: str, total: int) -> Progress:
        result = _post(self.endpoint, self.token, {"action": "delivery_get", "series": series})
        if result.get("contentHash") != content_hash or int(result.get("totalParts") or 0) != total:
            return Progress()
        return Progress(int(result.get("lastSuccessfulPart") or 0), bool(result.get("completed")))

    def mark(self, series: str, content_hash: str, total: int, last_part: int) -> None:
        _post(
            self.endpoint,
            self.token,
            {
                "action": "delivery_mark",
                "series": series,
                "contentHash": content_hash,
                "totalParts": total,
                "lastSuccessfulPart": last_part,
                "completed": last_part == total,
            },
        )


def _telegram_config() -> TelegramConfig:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    return TelegramConfig(bot_token=token, chat_id=chat_id)


def _mode_core(public_dir: str | Path, mode: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = Path(public_dir).resolve() / "data" / "modes" / mode
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    core_path = root / str(manifest["assets"]["core"]["path"])
    core = json.loads(core_path.read_text(encoding="utf-8"))
    return manifest, [row for row in core.get("universe") or [] if isinstance(row, dict)]


def _plain_candidate(index: int, row: dict[str, Any], *, score_field: str, label: str) -> str:
    ticker = str(row.get("ticker") or "-")
    setup = str(row.get("primarySetup") or row.get("setup") or row.get("stageName") or "watch")
    score = row.get(score_field) if row.get(score_field) is not None else row.get("score")
    price = row.get("price")
    details = [f"{label} {float(score):.1f}" if isinstance(score, (int, float)) else label]
    if isinstance(price, (int, float)):
        details.append(f"Px ${price:.2f}")
    if row.get("originalRR") is not None:
        details.append(f"R/R {float(row['originalRR']):.1f}:1")
    if row.get("tradeStatus"):
        details.append(str(row["tradeStatus"]).replace("_", " "))
    return f"*{index}\\. {_escape_md_v2(ticker)}* `{_escape_md_v2(setup)}`\n  {_escape_md_v2(' | '.join(details))}"


def _mode_parts(*, title: str, session_date: str, rows: list[dict[str, Any]], score_field: str, label: str) -> list[str]:
    header = f"*{_escape_md_v2(title)}* `{_escape_md_v2(session_date)}`\nPrices are EOD, not live\\. Canonical mode order; no cross\\-mode ranking\\."
    body = [header]
    body.extend(_plain_candidate(index, row, score_field=score_field, label=label) for index, row in enumerate(rows, 1))
    return split_telegram_message("\n\n".join(body))


def _drawing_alert_parts(session_date: str, rows: list[dict[str, Any]]) -> list[str]:
    body = [f"*Triggered drawing alerts* `{_escape_md_v2(session_date)}`", r"Daily EOD geometry \- one notification per armed episode\."]
    for index, row in enumerate(rows, 1):
        ticker = _escape_md_v2(str(row.get("ticker") or "-"))
        name = _escape_md_v2(str(row.get("name") or "Drawing alert"))
        drawing_type = _escape_md_v2(str(row.get("drawingType") or "drawing").replace("_", " "))
        condition = _escape_md_v2(str(row.get("condition") or "triggered").replace("_", " "))
        previous_price, current_price = row.get("previousPrice"), row.get("currentPrice")
        previous_level, current_level = row.get("previousLevel"), row.get("currentLevel")
        price_line = f"Close {float(previous_price):.2f} → {float(current_price):.2f}" if isinstance(previous_price, (int, float)) and isinstance(current_price, (int, float)) else f"Close {float(row.get('price') or 0):.2f}"
        level_line = f"Level {float(previous_level):.2f} → {float(current_level):.2f}" if isinstance(previous_level, (int, float)) and isinstance(current_level, (int, float)) else "Level unavailable"
        lines = [
            fr"*{index}\. {ticker}* \- {_escape_md_v2(str(row.get('sessionDate') or session_date))}",
            fr"{name} \- `{drawing_type} / {condition}`",
            _escape_md_v2(f"{price_line} | {level_line}"),
        ]
        deep_link = str(row.get("deepLink") or "").strip()
        if deep_link:
            safe_url = deep_link.replace("\\", "%5C").replace(")", "%29")
            lines.append(f"[Open and highlight drawing]({safe_url})")
        body.append("\n".join(lines))
    return split_telegram_message("\n\n".join(body))


def _ma_cluster_parts(scan_path: str | Path) -> list[str]:
    payload = json.loads(Path(scan_path).read_text(encoding="utf-8"))
    candidates = [Candidate.model_validate(row) for row in payload.get("candidates") or []]

    def relvol(candidate: Candidate) -> float:
        return float(candidate.current_thrust_rel_volume or candidate.rwb_thrust_rel_volume or candidate.rvol_today or 0.0)

    def width(candidate: Candidate) -> float:
        return float(candidate.ma_cluster_width_pct or candidate.sma_compression_pct or candidate.weekly_stack_width_pct or 999.0)

    triggered = [candidate for candidate in candidates if (candidate.ma_cluster_score or 0) > 0 and relvol(candidate) >= 2.0]
    selected = sorted(triggered, key=lambda candidate: (candidate.ma_cluster_score or 0, relvol(candidate), -width(candidate)), reverse=True)[:25]
    nearest = False
    if not selected:
        nearest = True
        selected = sorted(candidates, key=lambda candidate: (width(candidate), -relvol(candidate)))[:15]
    lines = [
        f"*Bottom MA cluster / RVOL* `{_escape_md_v2(str(payload.get('sessionDate') or ''))}`",
        "No exact hits; nearest candidates are shown\\." if nearest else "Exact daily or weekly cluster / volume candidates\\.",
    ]
    for index, candidate in enumerate(selected, 1):
        w, rv = width(candidate), relvol(candidate)
        tier = "Tier 1" if w <= 6 and rv >= 2 else "Tier 2" if w <= 8 else "Tier 3"
        lines.append(
            f"*{index}\\. {_escape_md_v2(candidate.ticker)}* `{_escape_md_v2(tier)}`\n"
            f"  {_escape_md_v2(f'cluster {w:.1f}% | RVOL {rv:.2f}x | score {float(candidate.ma_cluster_score or 0):.0f}')}"
        )
    return split_telegram_message("\n\n".join(lines))


def build_series(
    *,
    public_dir: str | Path,
    bottom_raw_scan: str | Path,
    alerts_path: str | Path | None = None,
) -> dict[str, list[str]]:
    bottom_key, bottom_parts = build_snapshot_digest_parts(
        bottom_raw_scan,
        cloud_sync_status="synced",
        report_link="https://garrincha077.github.io/StockScout-Unified/",
    )
    del bottom_key
    next_manifest, next_rows = _mode_core(public_dir, "next")
    ryan_manifest, ryan_rows = _mode_core(public_dir, "ryan-original")
    ryan_signals = [row for row in ryan_rows if row.get("originalRunBuySignal") is True]
    if not ryan_signals:
        ryan_signals = ryan_rows[:20]
    series = {
        "bottom-fishing": bottom_parts,
        "bottom-ma-cluster-rvol": _ma_cluster_parts(bottom_raw_scan),
        "next": _mode_parts(title="Next", session_date=str(next_manifest["sessionDate"]), rows=next_rows[:25], score_field="opportunityScore", label="Next"),
        "ryan-original": _mode_parts(title="Ryan Original", session_date=str(ryan_manifest["sessionDate"]), rows=ryan_signals[:25], score_field="originalBuyScore", label="Buy score"),
    }
    alert_rows: list[dict[str, Any]] = []
    if alerts_path and Path(alerts_path).exists():
        raw = json.loads(Path(alerts_path).read_text(encoding="utf-8"))
        alert_rows = [row for row in (raw.get("events") if isinstance(raw, dict) else raw) or [] if isinstance(row, dict)]
    alert_date = str(next_manifest["sessionDate"])
    if alert_rows:
        series["alerts"] = _drawing_alert_parts(alert_date, alert_rows)
    else:
        series["alerts"] = split_telegram_message(f"*Triggered alerts* `{_escape_md_v2(alert_date)}`\nNo owner alerts triggered\\.")
    if any(not parts or any(len(part) > 3900 for part in parts) for parts in series.values()):
        raise ValueError("Telegram series contains an empty or oversized part")
    return series


def evaluate_owner_alerts(
    *,
    public_dir: str | Path,
    endpoint: str,
    output_path: str | Path,
) -> dict[str, Any]:
    """Ask the OIDC-scoped state service to evaluate the exact active Pages run."""
    root = Path(public_dir).resolve() / "data" / "manifest.json"
    manifest = json.loads(root.read_text(encoding="utf-8"))
    run_id = str(manifest.get("runId") or "").strip()
    if not run_id or manifest.get("status") != "healthy":
        raise ValueError("A healthy unified activation is required for alert evaluation")
    token = github_oidc_token("stockscout-unified-operations")
    result = _post(endpoint, token, {"action": "evaluate_alerts", "runId": run_id})
    if result.get("runId") != run_id or not isinstance(result.get("events"), list):
        raise ValueError("Alert evaluator returned an invalid or mismatched response")
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(canonical_json_bytes(result))
    return result


_MARKDOWN_V2_RESERVED = r"_*[]()~`>#+-=|{}. !".replace(" ", "")
_MARKDOWN_V2_UNESCAPED = re.compile(r"(?<!\\)([" + re.escape(_MARKDOWN_V2_RESERVED) + r"])")


def _wire_safe_parts(parts: list[str], *, strict: bool = False) -> list[str]:
    """Make final Telegram wire text parse-safe without changing durable identity.

    The newer isolated renderers escape their own MarkdownV2 content, so their
    final boundary only needs the historic literal pipe repair.  The legacy
    Bottom digest is older and has accumulated raw MarkdownV2-reserved
    characters in prose (for example ``|`` and ``+``).  For that one series we
    escape every still-unescaped reserved character at the wire boundary.
    This intentionally trades rich formatting in the legacy digest for
    guaranteed delivery, while the ledger continues hashing canonical pre-wire
    parts so retries remain resumable and do not duplicate already sent parts.
    """
    pattern = _MARKDOWN_V2_UNESCAPED if strict else re.compile(r"(?<!\\)\|")
    return [pattern.sub(lambda match: "\\" + match.group(0), part) for part in parts]


def deliver_series(series: dict[str, list[str]], *, endpoint: str) -> bool:
    cfg, ledger = _telegram_config(), OidcLedger(endpoint)
    for name, parts in series.items():
        content_hash = sha256_bytes(canonical_json_bytes(parts))
        progress = ledger.get(name, content_hash, len(parts))
        if progress.completed:
            continue

        def on_sent(sent: int, total: int, *, series_name: str = name, digest: str = content_hash) -> None:
            ledger.mark(series_name, digest, total, sent)

        wire_parts = _wire_safe_parts(parts, strict=name == "bottom-fishing")
        if not send_message_parts(cfg, wire_parts, start_part=progress.last_part, on_part_sent=on_sent):
            return False
    return True
