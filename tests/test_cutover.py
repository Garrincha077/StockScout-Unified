from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stockscout_eod.cli import main
from stockscout_eod.cutover import CutoverError, build_session_evidence, update_cutover_ledger


def _candidate(
    ticker: str,
    session_date: str,
    order: int,
    *,
    provider: str = "yahoo",
    setup: str = "rwb_squeeze_thrust",
    status: str = "entry_ready",
) -> dict:
    return {
        "ticker": ticker,
        "asOf": session_date,
        "scanOrder": order,
        "setupNames": [setup],
        "providerUsed": provider,
        "dataLastDate": session_date,
        "dataStatus": "OK",
        "tradePlan": {
            "status": status,
            "reasonCodes": [status],
            "triggerState": "fresh" if status == "entry_ready" else "pending",
            "triggerReferenceLevel": 99,
            "entryReferenceLevel": 100.0,
            "structuralInvalidationLevel": 94.0,
            "entryRiskPct": 6,
            "extensionAtr": 0.2,
            "tacticalStopLevel": 94 if status == "entry_ready" else None,
            "tacticalRiskPct": 6.0 if status == "entry_ready" else None,
            "source": "primary_detector",
            "version": 1,
        },
    }


def _payload(session_date: str, *, provider: str = "yahoo") -> dict:
    return {
        "schemaVersion": "stockscout-eod/v1",
        "runId": f"{session_date}-eod",
        "sessionDate": session_date,
        "priceMode": "split_only",
        "provenance": {
            "primaryProvider": provider,
            "fallbackProvider": "stooq",
            "marketDataDate": session_date,
            "universeSource": "nasdaq_trader",
        },
        "candidates": [
            _candidate("AAA", session_date, 0, provider=provider),
            _candidate("BBB", session_date, 1, provider=provider),
        ],
        "excluded": [
            {
                **_candidate("CCC", session_date, 2, provider=provider),
                "excluded": True,
            }
        ],
    }


def _write_triplet(
    root: Path, session_date: str, payloads: tuple[dict, dict, dict] | None = None
) -> tuple[Path, Path, Path]:
    values = payloads or (_payload(session_date),) * 3
    paths: list[Path] = []
    for name, payload in zip(("new", "local", "stable"), values, strict=True):
        path = root / f"{session_date}-{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)
    return paths[0], paths[1], paths[2]


def _append(root: Path, session_date: str, payloads: tuple[dict, dict, dict] | None = None):
    new, local, stable = _write_triplet(root, session_date, payloads)
    return update_cutover_ledger(
        new_scan=new,
        local_scan=local,
        stable_scan=stable,
        ledger_json=root / "cutover.json",
        ledger_markdown=root / "cutover.md",
        evaluated_at=datetime(2026, 8, 22, 12, tzinfo=UTC),
    )


def test_readiness_requires_five_consecutive_green_nyse_sessions(tmp_path: Path) -> None:
    dates = ["2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"]
    for index, session_date in enumerate(dates, start=1):
        ledger = _append(tmp_path, session_date)
        assert ledger.readiness.consecutive_green_sessions == index
        assert ledger.readiness.ready is (index == 5)

    assert ledger.readiness.streak_dates == dates
    assert ledger.readiness.reason == "five_consecutive_nyse_sessions_green"
    assert [item.session_date for item in ledger.sessions] == dates
    markdown = (tmp_path / "cutover.md").read_text(encoding="utf-8")
    assert "Ready: **YES**" in markdown
    assert "**5/5**" in markdown


def test_missing_nyse_session_breaks_green_streak(tmp_path: Path) -> None:
    for session_date in (
        "2026-08-17",
        "2026-08-18",
        "2026-08-20",
        "2026-08-21",
        "2026-08-24",
    ):
        ledger = _append(tmp_path, session_date)

    assert ledger.readiness.ready is False
    assert ledger.readiness.consecutive_green_sessions == 3
    assert ledger.readiness.streak_dates == ["2026-08-20", "2026-08-21", "2026-08-24"]


def test_provider_differences_are_evidenced_without_copying_raw_data(tmp_path: Path) -> None:
    session_date = "2026-08-21"
    new_payload = _payload(session_date)
    new_payload["candidates"][1] = _candidate(
        "BBB",
        session_date,
        0,
        provider="stooq",
        setup="ma_cluster_rvol",
        status="trigger_pending",
    )
    new_payload["candidates"][0]["scanOrder"] = 1
    new_payload["candidates"][1]["rawBars"] = [1, 2, 3]
    new_payload["candidates"][1]["operatorPath"] = r"C:\Users\Someone\secret"
    new_payload["candidates"][1]["token"] = "do-not-copy-this"

    ledger = _append(
        tmp_path,
        session_date,
        (new_payload, _payload(session_date), _payload(session_date)),
    )

    assert ledger.sessions[0].status == "green"
    assert ledger.sessions[0].provider_divergence_count == 2
    new_local = ledger.sessions[0].comparisons[0]
    assert new_local.green is True
    divergence = new_local.provider_divergences[0]
    assert (divergence.ticker, divergence.date) == ("BBB", session_date)
    assert {"setup_hits", "trade_plan", "provenance"} <= set(divergence.categories)
    assert divergence.left_provenance.provider == "stooq"
    assert divergence.right_provenance.provider == "yahoo"
    assert new_local.ranking.exact is True
    assert new_local.ranking.compared_count == 1

    persisted = (tmp_path / "cutover.json").read_text(encoding="utf-8")
    assert "do-not-copy-this" not in persisted
    assert "operatorPath" not in persisted
    assert "rawBars" not in persisted
    assert r"C:\Users" not in persisted


def test_camel_and_snake_case_scan_contracts_compare_semantically(tmp_path: Path) -> None:
    session_date = "2026-08-21"
    snake = _payload(session_date)
    snake["session_date"] = snake.pop("sessionDate")
    snake["price_mode"] = snake.pop("priceMode")
    for row in [*snake["candidates"], *snake["excluded"]]:
        row["as_of"] = row.pop("asOf")
        if "scanOrder" in row:
            row["scan_order"] = row.pop("scanOrder")
        row["provider_used"] = row.pop("providerUsed")
        row["data_last_date"] = row.pop("dataLastDate")
        row["data_status"] = row.pop("dataStatus")
        row["setup_names"] = row.pop("setupNames")
        plan = row.pop("tradePlan")
        row["trade_plan"] = {
            "status": plan["status"],
            "reason_codes": plan["reasonCodes"],
            "trigger_state": plan["triggerState"],
            "trigger_reference_level": float(plan["triggerReferenceLevel"]),
            "entry_reference_level": plan["entryReferenceLevel"],
            "structural_invalidation_level": plan["structuralInvalidationLevel"],
            "entry_risk_pct": float(plan["entryRiskPct"]),
            "extension_atr": plan["extensionAtr"],
            "tactical_stop_level": plan["tacticalStopLevel"],
            "tactical_risk_pct": plan["tacticalRiskPct"],
            "source": plan["source"],
            "version": str(plan["version"]),
        }

    new, local, stable = _write_triplet(
        tmp_path,
        session_date,
        (_payload(session_date), snake, _payload(session_date)),
    )
    evidence = build_session_evidence(new, local, stable)

    assert evidence.status == "green"
    assert all(comparison.green for comparison in evidence.comparisons)
    assert all(not comparison.provider_divergences for comparison in evidence.comparisons)


def test_same_provider_setup_or_trade_plan_difference_is_blocking(tmp_path: Path) -> None:
    session_date = "2026-08-21"
    changed = _payload(session_date)
    changed["candidates"][0] = _candidate(
        "AAA",
        session_date,
        0,
        setup="ma_cluster_rvol",
        status="trigger_pending",
    )
    ledger = _append(
        tmp_path,
        session_date,
        (changed, _payload(session_date), _payload(session_date)),
    )

    session = ledger.sessions[0]
    assert session.status == "red"
    assert session.blocking_issue_count == 2
    assert ledger.readiness.ready is False
    assert ledger.readiness.reason == "latest_session_red"
    mismatch = session.comparisons[0].blocking_mismatches[0]
    assert mismatch.ticker == "AAA"
    assert mismatch.categories == ["setup_hits", "trade_plan"]
    assert "status" in mismatch.trade_plan_changed_fields


def test_ranking_order_difference_on_same_provider_is_blocking(tmp_path: Path) -> None:
    session_date = "2026-08-21"
    changed = _payload(session_date)
    changed["candidates"][0]["scanOrder"] = 1
    changed["candidates"][1]["scanOrder"] = 0
    new, local, stable = _write_triplet(
        tmp_path,
        session_date,
        (changed, _payload(session_date), _payload(session_date)),
    )
    evidence = build_session_evidence(new, local, stable)

    assert evidence.status == "red"
    comparison = evidence.comparisons[0]
    assert comparison.ranking.exact is False
    assert {item.ticker for item in comparison.ranking.differences} == {"AAA", "BBB"}


def test_join_rejects_mixed_dates_duplicates_and_non_session(tmp_path: Path) -> None:
    mixed = _payload("2026-08-21")
    mixed["candidates"][0]["asOf"] = "2026-08-20"
    new, local, stable = _write_triplet(
        tmp_path,
        "2026-08-21",
        (mixed, _payload("2026-08-21"), _payload("2026-08-21")),
    )
    with pytest.raises(CutoverError, match="expected 2026-08-21"):
        build_session_evidence(new, local, stable)

    duplicate = _payload("2026-08-21")
    duplicate["candidates"].append({**duplicate["candidates"][0], "scanOrder": 3})
    new, local, stable = _write_triplet(
        tmp_path,
        "duplicate",
        (duplicate, _payload("2026-08-21"), _payload("2026-08-21")),
    )
    with pytest.raises(CutoverError, match=r"duplicate \(ticker,date\)"):
        build_session_evidence(new, local, stable)

    weekend = _payload("2026-08-22")
    new, local, stable = _write_triplet(
        tmp_path,
        "weekend",
        (weekend, weekend, weekend),
    )
    with pytest.raises(CutoverError, match="not an NYSE session"):
        build_session_evidence(new, local, stable)


def test_outputs_are_explicit_idempotent_and_cannot_overwrite_inputs(tmp_path: Path) -> None:
    paths = _write_triplet(tmp_path, "2026-08-21")
    kwargs = {
        "new_scan": paths[0],
        "local_scan": paths[1],
        "stable_scan": paths[2],
        "ledger_json": tmp_path / "ledger.json",
        "ledger_markdown": tmp_path / "ledger.md",
        "evaluated_at": datetime(2026, 8, 22, 12, tzinfo=UTC),
    }
    first = update_cutover_ledger(**kwargs)
    second = update_cutover_ledger(**kwargs)
    assert len(first.sessions) == len(second.sessions) == 1
    assert (tmp_path / "ledger.json").exists()
    assert (tmp_path / "ledger.md").exists()

    with pytest.raises(CutoverError, match="cannot overwrite"):
        update_cutover_ledger(**{**kwargs, "ledger_json": paths[0]})


def test_cli_writes_evidence_but_returns_not_ready_until_cutover_gate(tmp_path: Path) -> None:
    new, local, stable = _write_triplet(tmp_path, "2026-08-21")
    result = main(
        [
            "cutover-evidence",
            "--new",
            str(new),
            "--local",
            str(local),
            "--stable",
            str(stable),
            "--ledger-json",
            str(tmp_path / "ledger.json"),
            "--ledger-markdown",
            str(tmp_path / "ledger.md"),
        ]
    )

    assert result == 3
    readiness = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))[
        "readiness"
    ]
    assert readiness["ready"] is False
    assert readiness["consecutiveGreenSessions"] == 1
