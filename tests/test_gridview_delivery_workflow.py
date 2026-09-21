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
