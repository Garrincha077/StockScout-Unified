from __future__ import annotations

from pathlib import Path

from stockscout_eod.contracts import wire_dump
from stockscout_eod.jsonio import write_json
from stockscout_unified.notifications import Progress, build_series, deliver_series
from tests.test_artifacts import _scan


def _write_mode(public: Path, mode: str, rows: list[dict]) -> None:
    root = public / "data" / "modes" / mode
    run = root / "runs" / "fixture"
    write_json(run / "core.json", {"universe": rows})
    write_json(
        root / "manifest.json",
        {
            "sessionDate": "2026-08-21",
            "assets": {"core": {"path": "runs/fixture/core.json"}},
        },
    )


def test_five_telegram_series_keep_every_selected_candidate_without_truncation(tmp_path: Path) -> None:
    public = tmp_path / "public"
    next_rows = [
        {"ticker": f"N{index:02d}", "opportunityScore": 100 - index, "price": 20 + index, "primarySetup": "next"}
        for index in range(30)
    ]
    ryan_rows = [
        {"ticker": f"R{index:02d}", "originalBuyScore": 100 - index, "price": 30 + index, "primarySetup": "ryan", "originalRunBuySignal": True}
        for index in range(30)
    ]
    _write_mode(public, "next", next_rows)
    _write_mode(public, "ryan-original", ryan_rows)
    bottom = tmp_path / "bottom.json"
    write_json(bottom, wire_dump(_scan()))
    alerts = tmp_path / "alerts.json"
    write_json(alerts, {"events": [{"ticker": "ALERT", "price": 10, "name": "Price crossed"}]})

    series = build_series(public_dir=public, bottom_raw_scan=bottom, alerts_path=alerts)

    assert list(series) == ["bottom-fishing", "bottom-ma-cluster-rvol", "next", "ryan-original", "alerts"]
    assert all(parts and all(0 < len(part) <= 3900 for part in parts) for parts in series.values())
    joined = "\n".join(part for parts in series.values() for part in parts)
    assert "truncated" not in joined.lower()
    assert all(f"N{index:02d}" in "\n".join(series["next"]) for index in range(25))
    assert all(f"R{index:02d}" in "\n".join(series["ryan-original"]) for index in range(25))
    assert "ALERT" in "\n".join(series["alerts"])


def test_delivery_resumes_each_series_and_deduplicates_completed_content(monkeypatch) -> None:
    progress = {
        "bottom-fishing": Progress(last_part=1),
        "next": Progress(completed=True),
    }
    marks: list[tuple[str, int]] = []
    sends: list[tuple[list[str], int]] = []

    class Ledger:
        def __init__(self, _endpoint: str) -> None:
            pass

        def get(self, series: str, _content_hash: str, _total: int) -> Progress:
            return progress.get(series, Progress())

        def mark(self, series: str, _content_hash: str, _total: int, last_part: int) -> None:
            marks.append((series, last_part))

    def send(_config, parts, *, start_part, on_part_sent):
        sends.append((parts, start_part))
        for part in range(start_part + 1, len(parts) + 1):
            on_part_sent(part, len(parts))
        return True

    monkeypatch.setattr("stockscout_unified.notifications.OidcLedger", Ledger)
    monkeypatch.setattr("stockscout_unified.notifications._telegram_config", lambda: object())
    monkeypatch.setattr("stockscout_unified.notifications.send_message_parts", send)

    assert deliver_series(
        {"bottom-fishing": ["one", "two"], "next": ["done"], "alerts": ["none"]},
        endpoint="https://fixture.invalid/operations",
    )
    assert sends == [(["one", "two"], 1), (["none"], 0)]
    assert marks == [("bottom-fishing", 2), ("alerts", 1)]
