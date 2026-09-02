from __future__ import annotations

from pathlib import Path


def test_eod_persists_alert_evaluation_before_telegram_delivery() -> None:
    workflow = Path('.github/workflows/eod.yml').read_text(encoding='utf-8')

    evaluate = 'stockscout_unified.cli evaluate-alerts --public-dir .pages'
    artifact = 'name: unified-alerts-${{ github.run_id }}-${{ github.run_attempt }}'
    artifact_path = 'path: .notify/alerts.json'
    deliver = 'Deliver five resumable Telegram series'

    assert evaluate in workflow
    assert artifact in workflow
    assert artifact_path in workflow
    assert workflow.index(evaluate) < workflow.index(artifact) < workflow.index(deliver)


def test_notification_retry_reuses_exact_persisted_alert_snapshot() -> None:
    workflow = Path('.github/workflows/notification-retry.yml').read_text(encoding='utf-8')

    assert 'pattern: unified-alerts-${{ steps.request.outputs.source_run_id }}-*' in workflow
    assert 'alerts.json' in workflow
    assert "alerts.get('runId') != run_id" in workflow
    assert 'stockscout_unified.cli evaluate-alerts' not in workflow
    assert '--alerts .notify/alerts.json' in workflow


def test_retry_does_not_turn_delivery_recovery_into_alert_re_evaluation() -> None:
    workflow = Path('.github/workflows/notification-retry.yml').read_text(encoding='utf-8')

    assert 'Evaluate owner alerts against the exact live run' not in workflow
    assert 'Resume and deliver Telegram series only' in workflow
