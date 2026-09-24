from stockscout_unified.trend_birth_alert_notification import build_series


def payload(**overrides):
    base = {
        "schemaVersion": "trend-birth-alerts-v1",
        "sessionDate": "2026-09-24",
        "baselineOnly": False,
        "readyCount": 1,
        "triggerCount": 1,
        "invalidatedCount": 0,
        "dashboardUrl": "https://example.test/?snapshot=abc--" + "a" * 64,
        "messages": [],
    }
    base.update(overrides)
    return base


def test_baseline_payload_never_builds_series():
    data = payload(
        baselineOnly=True,
        readyCount=0,
        triggerCount=0,
        messages=[],
    )
    assert build_series(data) == {}


def test_ready_message_keeps_clickable_dashboard_link():
    url = payload()["dashboardUrl"]
    data = payload(messages=[{
        "kind": "ready",
        "text": "🟠 KELL TREND BIRTH — 3/4 READY\n\n+2 additional candidates — View dashboard: " + url,
    }])
    series = build_series(data)
    assert list(series) == ["trend-birth-ready"]
    rendered = "\n".join(series["trend-birth-ready"])
    assert "[View dashboard](" in rendered
    assert url not in rendered  # URL is escaped for MarkdownV2.


def test_trigger_gets_ticker_specific_resumable_series():
    url = payload()["dashboardUrl"]
    data = payload(messages=[
        {
            "kind": "trigger",
            "ticker": "XYZ",
            "text": "🚀 KELL TREND BIRTH — 4/4 TRIGGER\nXYZ — 4/4\nView dashboard: " + url,
        },
        {
            "kind": "trigger",
            "ticker": "ABC",
            "text": "🚀 KELL TREND BIRTH — 4/4 TRIGGER\nABC — 4/4\nView dashboard: " + url,
        },
    ])
    series = build_series(data)
    assert set(series) == {"trend-birth-trigger-XYZ", "trend-birth-trigger-ABC"}


def test_invalidated_is_grouped_in_one_series():
    url = payload()["dashboardUrl"]
    data = payload(messages=[{
        "kind": "invalidated",
        "text": "⚪ KELL TREND BIRTH — INVALIDATED\n2 former READY/TRIGGER candidates\nView dashboard: " + url,
    }])
    assert list(build_series(data)) == ["trend-birth-invalidated"]
