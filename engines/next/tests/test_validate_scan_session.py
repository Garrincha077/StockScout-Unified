import os
import pickle
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from validate_scan_session import (
    expected_session_from_env,
    manual_backfill_allowed,
    validate_session,
)


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


def test_unified_orchestrator_session_is_parsed_exactly(monkeypatch):
    monkeypatch.setenv('STOCKSCOUT_EXPECTED_SESSION', '2026-08-25')
    assert expected_session_from_env() == date(2026, 8, 25)


def test_unified_orchestrator_session_fails_closed_when_malformed(monkeypatch):
    monkeypatch.setenv('STOCKSCOUT_EXPECTED_SESSION', 'yesterday')
    with pytest.raises(SystemExit, match='Invalid STOCKSCOUT_EXPECTED_SESSION'):
        expected_session_from_env()


def test_unified_prior_session_replay_keeps_exact_cache_guards(tmp_path, monkeypatch):
    session = pd.DataFrame(
        {'Close': [100.0]},
        index=pd.DatetimeIndex(['2026-08-25']),
    )
    cache = tmp_path / 'price_history_5y.pkl'
    cache.write_bytes(pickle.dumps({'SPY': session, 'AAPL': session, 'MSFT': session}))
    monkeypatch.setenv('STOCKSCOUT_EXPECTED_SESSION', '2026-08-25')

    validate_session(
        now_utc=datetime(2026, 8, 26, 15, 30, tzinfo=timezone.utc),
        price_cache=cache,
    )

    monkeypatch.setenv('STOCKSCOUT_EXPECTED_SESSION', '2026-08-24')
    with pytest.raises(SystemExit, match='does not match orchestrator session'):
        validate_session(
            now_utc=datetime(2026, 8, 26, 15, 30, tzinfo=timezone.utc),
            price_cache=cache,
        )
