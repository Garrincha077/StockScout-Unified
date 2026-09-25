from datetime import date, timedelta

from stockscout_unified.trend_birth_hourly import (
    CHECK_LABELS,
    apply_hysteresis,
    build_series,
    evaluate_stage,
    merge_owner_watchlist,
    process_results,
)


def _business_days(count: int, start: date = date(2025, 1, 2)) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _rows(closes: list[float]) -> list[dict]:
    return [
        {
            "time": day.isoformat(),
            "open": close - 0.2,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000,
        }
        for day, close in zip(_business_days(len(closes)), closes, strict=True)
    ]


def _staged(end_slope: float) -> list[float]:
    first = [50.0 + index * 0.15 for index in range(180)]
    peak = first[-1]
    pullback = [peak - (index + 1) * 0.10 for index in range(8)]
    start = pullback[-1]
    ending = [start + (index + 1) * end_slope for index in range(15)]
    return first + pullback + ending


def test_hourly_stage_rules_match_ready_and_trigger_shape():
    ready = evaluate_stage(_rows(_staged(0.0)))
    trigger = evaluate_stage(_rows(_staged(0.04)))
    assert ready["stage"] == 3
    assert trigger["stage"] == 4
    assert trigger["checks"]["ema10AboveEma20"] is True
    assert trigger["checks"]["bullishReexpansion"] is True


def test_hourly_hysteresis_does_not_downgrade_twice_on_same_daily_bar():
    previous = {
        "rawStage": 3,
        "effectiveStage": 3,
        "barDate": "2026-09-24",
        "belowCount": 0,
        "lastBelowBar": None,
    }
    lower = {
        "stage": 2,
        "barDate": "2026-09-24",
        "hardInvalidation": False,
    }
    once = apply_hysteresis(previous, lower)
    twice = apply_hysteresis(once, lower)
    assert once["effectiveStage"] == 3
    assert once["belowCount"] == 1
    assert twice["effectiveStage"] == 3
    assert twice["belowCount"] == 1


def test_hourly_hard_invalidation_is_immediate():
    previous = {
        "rawStage": 4,
        "effectiveStage": 4,
        "barDate": "2026-09-23",
        "belowCount": 0,
        "lastBelowBar": None,
    }
    result = {
        "stage": 0,
        "barDate": "2026-09-24",
        "hardInvalidation": True,
    }
    updated = apply_hysteresis(previous, result)
    assert updated["effectiveStage"] == 0


def test_first_ever_hourly_run_is_baseline_only():
    candidates = [{"ticker": "ABC", "kell_score": 90}]
    results = {
        "ABC": {
            "available": True,
            "stage": 4,
            "barDate": "2026-09-24",
            "hardInvalidation": False,
        }
    }
    state, events = process_results(
        candidates,
        results,
        {"initialized": False, "tickers": {}},
    )
    assert state["initialized"] is True
    assert all(not rows for rows in events.values())


def test_owner_watchlist_union_adds_tracked_only_and_marks_existing():
    merged = merge_owner_watchlist(
        [{"ticker": "AAA", "kell_score": 80}],
        ["aaa", "CLOV", "CLOV"],
    )
    by_ticker = {item["ticker"]: item for item in merged}
    assert set(by_ticker) == {"AAA", "CLOV"}
    assert by_ticker["AAA"]["trackedWatchlist"] is True
    assert by_ticker["AAA"]["trackedOnly"] is False
    assert by_ticker["CLOV"]["trackedWatchlist"] is True
    assert by_ticker["CLOV"]["trackedOnly"] is True


def test_new_tracked_ticker_establishes_hourly_baseline_without_alert():
    candidates = [{"ticker": "CLOV", "trackedWatchlist": True, "trackedOnly": True}]
    prior = {"initialized": True, "tickers": {}}
    results = {
        "CLOV": {
            "available": True,
            "stage": 4,
            "barDate": "2026-09-24",
            "hardInvalidation": False,
            "missingFor4": [],
            "checks": {},
        }
    }
    state, events = process_results(candidates, results, prior)
    assert state["tickers"]["CLOV"]["effectiveStage"] == 4
    assert all(not rows for rows in events.values())


def test_new_ready_and_trigger_events_are_sparse():
    candidates = [
        {"ticker": "AAA", "kell_score": 80},
        {"ticker": "BBB", "kell_score": 70},
    ]
    prior = {
        "initialized": True,
        "tickers": {
            "AAA": {"effectiveStage": 2, "belowCount": 0, "lastBelowBar": None},
            "BBB": {"effectiveStage": 3, "belowCount": 0, "lastBelowBar": None},
        },
    }
    results = {
        "AAA": {
            "available": True,
            "stage": 3,
            "barDate": "2026-09-24",
            "hardInvalidation": False,
            "missingFor4": ["EMA10 > EMA20"],
            "checks": {},
        },
        "BBB": {
            "available": True,
            "stage": 4,
            "barDate": "2026-09-24",
            "hardInvalidation": False,
            "missingFor4": [],
            "checks": {},
        },
    }
    _state, events = process_results(candidates, results, prior)
    assert [item["ticker"] for item in events["ready"]] == ["AAA"]
    assert [item["ticker"] for item in events["trigger"]] == ["BBB"]


def test_additional_candidates_message_keeps_dashboard_link():
    ready = []
    for index in range(7):
        ready.append(
            {
                "ticker": f"T{index}",
                "kell_score": 100 - index,
                "trendBirthHourly": {
                    "missingFor4": ["EMA10 > EMA20"],
                    "checks": {},
                },
            }
        )
    url = "https://example.test/?snapshot=abc--" + "a" * 64
    series = build_series(
        {"ready": ready, "trigger": [], "invalidated": []},
        url,
    )
    text = "\n".join(series["trend-birth-hourly-ready"])
    assert "+2 additional candidates" in text
    assert "[View dashboard](" in text


def test_multiple_triggers_are_grouped_to_avoid_telegram_spam():
    triggers = []
    for index in range(8):
        triggers.append(
            {
                "ticker": f"X{index}",
                "kell_score": 100 - index,
                "trendBirthHourly": {
                    "missingFor4": [],
                    "checks": {key: True for key, _ in CHECK_LABELS},
                },
            }
        )
    url = "https://example.test/?snapshot=abc--" + "b" * 64
    series = build_series(
        {"ready": [], "trigger": triggers, "invalidated": []},
        url,
    )
    assert list(series) == ["trend-birth-hourly-trigger"]
    text = "\n".join(series["trend-birth-hourly-trigger"])
    assert "8 new candidates" in text
    assert "+3 additional candidates" in text
    assert "[View dashboard](" in text
