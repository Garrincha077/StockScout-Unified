from pathlib import Path

import yaml


def test_recovery_preserves_order_exact_identity_and_manual_no_send_default():
    workflow = Path(".github/workflows/morning-recovery.yml").read_text()
    parsed = yaml.safe_load(workflow)
    triggers = parsed.get("on", parsed.get(True))
    assert triggers["workflow_dispatch"]["inputs"]["notify"]["default"] is False
    assert workflow.index("dispatch_and_wait eod eod.yml") < workflow.index("request_gridview_refresh && wait_for_gridview_ready")
    assert '"inputs[notify]=$NOTIFY"' in workflow
    assert '"inputs[deliver]=$NOTIFY"' in workflow
    assert 'python scripts/verify_remote_activation.py --run-id "$live_run" --session-date "$SESSION_DATE"' in workflow
    assert "jq -e --slurp '.[0] == .[1]'" in workflow
    assert "+ 2700" in workflow
    assert 'result="ACTIVATION VERIFICATION FAILED"' in workflow
    assert "TREND_BIRTH_REFRESH_TOKEN" in workflow
    assert "actions/workflows/refresh-trend-birth-review-lab.yml/dispatches" in workflow
