from pathlib import Path

WORKFLOW=Path(".github/workflows/frontend-refresh.yml").read_text()

def test_frontend_refresh_reuses_active_immutable_scan_and_never_scans():
    assert "Read currently active immutable Pages identity" in WORKFLOW
    assert "Recover the exact currently active Pages artifact" in WORKFLOW
    assert 'manifest.get("runId")!=os.environ["EXPECTED_RUN_ID"]' in WORKFLOW
    assert 'manifest.get("sessionDate")!=os.environ["EXPECTED_SESSION_DATE"]' in WORKFLOW
    assert "trend-birth-auth-bridge.html" in WORKFLOW
    assert "actions/upload-pages-artifact" in WORKFLOW
    assert "uses: ./.github/workflows/deploy-pages.yml" in WORKFLOW
    for forbidden in (
        "stockscout_eod.cli scan",
        "run_fast_scan.py",
        "run_next",
        "run_bottom",
    ):
        assert forbidden not in WORKFLOW
