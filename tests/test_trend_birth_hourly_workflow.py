from pathlib import Path

import yaml


def test_hourly_trend_birth_workflow_is_sparse_and_safe():
    path = Path(__file__).parents[1] / ".github/workflows/trend-birth-hourly.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    events = workflow.get("on", workflow.get(True))
    assert "push" not in events
    assert events["workflow_dispatch"]["inputs"]["deliver"]["default"] is False
    schedules = events["schedule"]
    assert schedules == [{"cron": "17 14-23 * * 1-5", "timezone": "Europe/Zagreb"}]
    assert workflow["permissions"]["contents"] == "read"
    assert workflow["permissions"]["id-token"] == "write"
    assert workflow["jobs"]["watch"]["environment"]["name"] == "production"
    steps = workflow["jobs"]["watch"]["steps"]
    live = next(step for step in steps if step.get("name") == "Run hourly watcher and deliver sparse alerts")
    assert "--update-state" in live["run"]
    assert "--send" in live["run"]
    assert "UNIFIED_DELIVERY_ENDPOINT" in live["run"]
    assert "TELEGRAM_BOT_TOKEN" in live["env"]
    assert "TELEGRAM_CHAT_ID" in live["env"]
    assert any("actions/cache/restore@" in step.get("uses", "") for step in steps)
    assert any("actions/cache/save@" in step.get("uses", "") for step in steps)
