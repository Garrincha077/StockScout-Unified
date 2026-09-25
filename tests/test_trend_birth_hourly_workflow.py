from pathlib import Path

import yaml


def test_hourly_trend_birth_workflow_is_sparse_and_safe():
    path = Path(__file__).parents[1] / ".github/workflows/trend-birth-hourly.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    events = workflow.get("on", workflow.get(True))
    assert events["push"]["branches"] == ["main"]
    assert ".github/workflows/trend-birth-hourly.yml" in events["push"]["paths"]
    assert events["workflow_dispatch"]["inputs"]["deliver"]["default"] is False
    schedules = events["schedule"]
    assert schedules == [{"cron": "47 9-15 * * 1-5", "timezone": "America/New_York"}]
    assert workflow["permissions"]["contents"] == "read"
    assert workflow["permissions"]["id-token"] == "write"
    assert workflow["jobs"]["watch"]["environment"]["name"] == "production"
    steps = workflow["jobs"]["watch"]["steps"]
    market = next(step for step in steps if step.get("name") == "Check NYSE regular session")
    assert "pandas_market_calendars" in market["run"]
    assert "NYSE" in market["run"]
    bootstrap = next(step for step in steps if step.get("name") == "Bootstrap hourly baseline on watcher change")
    assert "--update-state" in bootstrap["run"]
    assert "--send" not in bootstrap["run"]
    assert "UNIFIED_DELIVERY_ENDPOINT" in bootstrap["run"]
    live = next(step for step in steps if step.get("name") == "Run hourly watcher and deliver sparse alerts")
    assert "--update-state" in live["run"]
    assert "--send" in live["run"]
    assert "UNIFIED_DELIVERY_ENDPOINT" in live["run"]
    assert "TELEGRAM_BOT_TOKEN" in live["env"]
    assert "TELEGRAM_CHAT_ID" in live["env"]
    restore = next(step for step in steps if "actions/cache/restore@" in step.get("uses", ""))
    save = next(step for step in steps if "actions/cache/save@" in step.get("uses", ""))
    assert "trend-birth-hourly-v2-" in restore["with"]["key"]
    assert "trend-birth-hourly-v2-" in save["with"]["key"]
