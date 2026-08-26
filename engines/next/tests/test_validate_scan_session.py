import os

from validate_scan_session import manual_backfill_allowed


def set_env(monkeypatch, *, event, workflow, workflow_ref=''):
    monkeypatch.setenv('GITHUB_EVENT_NAME', event)
    monkeypatch.setenv('GITHUB_WORKFLOW', workflow)
    monkeypatch.setenv('GITHUB_WORKFLOW_REF', workflow_ref)


def test_full_validation_pull_request_may_replay_prior_completed_session(monkeypatch):
    set_env(monkeypatch, event='pull_request', workflow='StockScout Full Validation')
    assert manual_backfill_allowed() is True


def test_full_validation_main_push_and_manual_dispatch_are_allowed(monkeypatch):
    set_env(monkeypatch, event='push', workflow='StockScout Full Validation')
    assert manual_backfill_allowed() is True
    set_env(monkeypatch, event='workflow_dispatch', workflow='StockScout Full Validation')
    assert manual_backfill_allowed() is True


def test_direct_daily_scan_dispatch_never_gets_full_validation_exception(monkeypatch):
    set_env(monkeypatch, event='workflow_dispatch', workflow='Daily Stock Screening (Post-Market)')
    assert manual_backfill_allowed() is False


def test_scheduled_nightly_never_gets_full_validation_exception(monkeypatch):
    set_env(monkeypatch, event='schedule', workflow='Daily Stock Screening (Post-Market)')
    assert manual_backfill_allowed() is False


def test_unrelated_pull_request_cannot_claim_backfill(monkeypatch):
    set_env(monkeypatch, event='pull_request', workflow='StockScout Validation')
    assert manual_backfill_allowed() is False


def test_workflow_ref_can_identify_full_validation_caller(monkeypatch):
    set_env(
        monkeypatch,
        event='pull_request',
        workflow='Reusable called workflow',
        workflow_ref='Garrincha077/StockScreener-next/.github/workflows/stockscout_full_validation.yml@refs/pull/18/merge',
    )
    assert manual_backfill_allowed() is True
