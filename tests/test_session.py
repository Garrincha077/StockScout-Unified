from __future__ import annotations

import json
from datetime import UTC, date, datetime

from stockscout_eod.session import decide_session, write_github_outputs


def test_guard_waits_for_close_then_allows_completed_session() -> None:
    before = decide_session(now_utc=datetime(2026, 8, 21, 19, 30, tzinfo=UTC))
    after = decide_session(now_utc=datetime(2026, 8, 21, 20, 30, tzinfo=UTC))
    assert before.reason == "market_not_closed"
    assert before.should_run is False
    assert after == after.__class__(True, "2026-08-21", "completed_session_ready")


def test_guard_skips_holiday_and_already_active_session(tmp_path) -> None:
    holiday = decide_session(
        now_utc=datetime(2026, 12, 25, 23, 0, tzinfo=UTC),
        requested_date=date(2026, 12, 25),
    )
    assert holiday.reason == "not_a_nyse_session"

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"sessionDate": "2026-08-21", "status": "healthy"}),
        encoding="utf-8",
    )
    decision = decide_session(
        now_utc=datetime(2026, 8, 21, 20, 30, tzinfo=UTC),
        requested_date=date(2026, 8, 21),
        active_manifest=str(manifest),
    )
    assert decision.reason == "session_already_active"

    output = tmp_path / "github-output.txt"
    write_github_outputs(decision, output)
    assert "should_run=false" in output.read_text(encoding="utf-8")
