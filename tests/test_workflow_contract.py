from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "eod.yml"
RECOVERY_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-existing.yml"


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


def test_recovery_skips_telegram_rendering_when_notifications_are_disabled() -> None:
    workflow = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    assert "- name: Render Telegram dry run only when requested" in workflow
    assert "        if: inputs.notify" in workflow
    assert "python -m stockscout_unified.cli notify" in workflow
    eod = WORKFLOW.read_text(encoding="utf-8")
    assert "      notify: ${{ inputs.notify }}" in eod
