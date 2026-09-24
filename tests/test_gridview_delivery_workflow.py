from pathlib import Path

import yaml


def test_gridview_workflow_reserves_before_send_and_manual_defaults_to_dry_run():
    path = Path(__file__).parents[1] / ".github/workflows/trend-birth-gridview-telegram.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    events = workflow.get("on", workflow.get(True))
    assert "push" not in events
    assert events["workflow_dispatch"]["inputs"]["deliver"]["default"] is False
    assert workflow["concurrency"]["cancel-in-progress"] is False
    steps = workflow["jobs"]["send"]["steps"]
    prepare = next(i for i, step in enumerate(steps) if "--prepare" in step.get("run", ""))
    send = next(i for i, step in enumerate(steps) if "--send-reserved" in step.get("run", ""))
    assert any(
        "git push origin HEAD:main" in step.get("run", "") for step in steps[prepare + 1 : send]
    )
    assert "success()" in steps[send]["if"]
    assert any("always()" in step.get("if", "") for step in steps[send + 1 :])


def test_gridview_workflow_delivers_trend_birth_alerts_through_unified_ledger():
    path = Path(__file__).parents[1] / ".github/workflows/trend-birth-gridview-telegram.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert workflow["permissions"]["id-token"] == "write"
    steps = workflow["jobs"]["send"]["steps"]
    dry = next(step for step in steps if step.get("name") == "Verify only (manual default)")
    assert "stockscout_unified.trend_birth_alert_notification" in dry.get("run", "")
    deliver = next(step for step in steps if step.get("name") == "Deliver Trend Birth stage-change alerts")
    assert "--send" in deliver.get("run", "")
    assert "UNIFIED_DELIVERY_ENDPOINT" in deliver.get("run", "")
    assert "TELEGRAM_BOT_TOKEN" in deliver.get("env", {})
    assert "TELEGRAM_CHAT_ID" in deliver.get("env", {})
