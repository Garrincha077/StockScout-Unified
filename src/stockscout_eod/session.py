"""NYSE-session guard used by both scheduled workflow slots."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

NYSE = mcal.get_calendar("NYSE")


@dataclass(frozen=True)
class GuardDecision:
    should_run: bool
    session_date: str | None
    reason: str


def _load_active_manifest(location: str | None) -> dict[str, Any] | None:
    if not location:
        return None
    try:
        if location.startswith(("https://", "http://")):
            request = Request(location, headers={"User-Agent": "StockScout-EOD/0.1"})
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        path = Path(location)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError, TimeoutError):
        # An unavailable current pointer must not suppress a valid recovery run.
        return None


def decide_session(
    *,
    now_utc: datetime | None = None,
    requested_date: date | None = None,
    active_manifest: str | None = None,
    force: bool = False,
) -> GuardDecision:
    now = now_utc or datetime.now(tz=UTC)
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)

    target = requested_date or now.astimezone(ZoneInfo("America/New_York")).date()
    # The exchange calendar is authoritative; a weekday is not necessarily a session.
    schedule = NYSE.schedule(start_date=target, end_date=target)
    if schedule.empty:
        return GuardDecision(False, target.isoformat(), "not_a_nyse_session")

    close = schedule.iloc[0]["market_close"].to_pydatetime().astimezone(UTC)
    if now < close:
        return GuardDecision(False, target.isoformat(), "market_not_closed")

    current = _load_active_manifest(active_manifest)
    if (
        not force
        and current
        and current.get("sessionDate") == target.isoformat()
        and current.get("status") == "healthy"
    ):
        return GuardDecision(False, target.isoformat(), "session_already_active")

    return GuardDecision(True, target.isoformat(), "completed_session_ready")


def write_github_outputs(decision: GuardDecision, path: str | Path) -> None:
    output = Path(path)
    with output.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"should_run={'true' if decision.should_run else 'false'}\n")
        handle.write(f"session_date={decision.session_date or ''}\n")
        handle.write(f"reason={decision.reason}\n")
