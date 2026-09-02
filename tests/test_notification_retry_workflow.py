from __future__ import annotations

from pathlib import Path


def test_notification_retry_is_notification_only_and_fail_closed() -> None:
    workflow = Path('.github/workflows/notification-retry.yml').read_text(encoding='utf-8')

    assert 'stockscout_eod.cli scan' not in workflow
    assert 'run_fast_scan.py' not in workflow
    assert 'actions/deploy-pages' not in workflow
    assert 'upload-pages-artifact' not in workflow

    assert 'actions: read' in workflow
    assert 'id-token: write' in workflow
    assert 'run-id: ${{ steps.request.outputs.source_run_id }}' in workflow
    assert 'pattern: unified-notify-${{ steps.request.outputs.source_run_id }}-*' in workflow
    assert "f'-eod-{source_run_id}-' not in run_id" in workflow
    assert "bottom.get('runId') != run_id" in workflow

    assert 'stockscout_unified.cli verify --public-dir .pages' in workflow
    assert 'verify_remote_activation.py --run-id "$RUN_ID"' in workflow
    assert 'stockscout_unified.cli evaluate-alerts' in workflow
    assert '--allow-notify' in workflow
    assert 'Scan invoked: **false**' in workflow
    assert 'Pages deployment invoked: **false**' in workflow
