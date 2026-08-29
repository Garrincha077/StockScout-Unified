from pathlib import Path


SMOKE = Path(".github/workflows/eod-smoke.yml")


def test_smoke_workflow_is_manual_and_never_deploys() -> None:
    workflow = SMOKE.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "scanner:" in workflow
    assert "- both" in workflow
    assert "- bottom" in workflow
    assert "- next" in workflow
    for forbidden in (
        "deploy-pages",
        "upload-pages-artifact",
        "publish-adjusted",
        "activate --public-dir",
        "notify --public-dir",
        "TELEGRAM_BOT_TOKEN",
    ):
        assert forbidden not in workflow


def test_smoke_workflow_exercises_both_scanner_boundaries() -> None:
    workflow = SMOKE.read_text(encoding="utf-8")
    assert "--tickers \"AAPL,MSFT,NVDA,AMZN,META,GOOGL,JPM,XOM,CAT,COST,UNH,NKE\"" in workflow
    assert "--allow-fixture" in workflow
    assert "python run_resumable_fast_scan.py" in workflow
    assert "--test-mode" in workflow
    assert "python validate_scan_session.py" in workflow
    assert "next_scan_metrics.json" in workflow
    assert "bottom-smoke-summary.json" in workflow


def test_smoke_uses_completed_session_guard_and_bounded_jobs() -> None:
    workflow = SMOKE.read_text(encoding="utf-8")
    assert "guard --force" in workflow
    assert "timeout-minutes: 30" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "cancel-in-progress: true" in workflow
