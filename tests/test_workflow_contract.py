from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "eod.yml"


def test_expensive_bottom_result_is_checkpointed_before_next() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    checkpoint = workflow.index("Preserve resumable Bottom checkpoint before Next")
    next_scan = workflow.index("Run Next and exact Ryan Original capture")
    assert checkpoint < next_scan
    assert "name: unified-bottom-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert workflow.count("include-hidden-files: true") >= 3


def test_next_validates_the_exact_orchestrator_session() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "STOCKSCOUT_EXPECTED_SESSION: ${{ steps.guard.outputs.session_date }}" in workflow
