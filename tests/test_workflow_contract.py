import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "eod.yml"
RECOVERY_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-existing.yml"


def test_expensive_bottom_result_is_checkpointed_before_next() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    checkpoint = workflow.index("Preserve resumable Bottom checkpoint before Next")
    next_scan = workflow.index("Run Next scanner (adjusted OHLCV)")
    assert checkpoint < next_scan
    assert "name: unified-bottom-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert workflow.count("include-hidden-files: true") >= 3


def test_next_is_split_into_scan_enrich_and_validation_phases() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    scan = workflow.index("Run Next scanner (adjusted OHLCV)")
    enrich = workflow.index("Enrich Next and exact Ryan Original capture")
    validate = workflow.index("Validate Next dataset before publish")
    diagnostics = workflow.index("Preserve Next diagnostics after every attempt")
    contexts = workflow.index("Build verified read-only Next contexts")
    assert scan < enrich < validate < diagnostics < contexts
    assert "name: unified-next-diagnostics-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "if: ${{ always() && steps.guard.outputs.should_run == 'true' }}" in workflow
    assert "engines/next/frontend/public/data/latest.json" in workflow


def test_next_validates_the_exact_orchestrator_session() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "STOCKSCOUT_EXPECTED_SESSION: ${{ steps.guard.outputs.session_date }}" in workflow


def test_bottom_scan_has_an_early_market_session_preflight() -> None:
    # The preflight is deliberately inside the production scan entrypoint so
    # every workflow caller (scheduled, manual, and recovery) gets the same
    # bounded provider check before the expensive full-universe loop.
    runner = Path("src/stockscout_eod/runner.py").read_text(encoding="utf-8")
    assert "preflight_session(pipeline, session_date, universe)" in runner
    assert "Market-session preflight passed" in runner


def test_recovery_skips_telegram_rendering_when_notifications_are_disabled() -> None:
    workflow = RECOVERY_WORKFLOW.read_text(encoding="utf-8")
    assert "- name: Render Telegram dry run only when requested" in workflow
    assert "        if: inputs.notify" in workflow
    assert "python -m stockscout_unified.cli notify" in workflow
    eod = WORKFLOW.read_text(encoding="utf-8")
    assert "      notify: ${{ inputs.notify }}" in eod


def test_next_contexts_and_groups_fail_closed_before_publish() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    audit = workflow.index("python audit_group_leadership.py")
    contexts = workflow.index("python scripts/build_next_contexts.py")
    publish = workflow.index("publish-adjusted --mode next")
    assert audit < publish
    assert contexts < publish
    assert "--factor-regime .staging/next-context/factor-regime.json" in workflow
    assert "--gmli-context .staging/next-context/gmli-context.json" in workflow
    assert "python scripts/check_owner_config.py" in workflow
    assert "REQUIRE_OWNER: ${{ github.event_name == 'schedule' || inputs.notify }}" in workflow


def test_required_ci_covers_every_public_contract() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    for command in (
        "python -m pytest -q",
        "npm run check --prefix frontend",
        "npm test --prefix services/mcp",
        "deno test supabase/functions/unified-operations/index_test.ts",
        "supabase@2.116.0 test db",
        "npm run test:e2e --prefix frontend",
    ):
        assert command in workflow


def test_all_third_party_actions_are_pinned_to_full_commit_shas() -> None:
    for path in Path(".github/workflows").glob("*.yml"):
        workflow = path.read_text(encoding="utf-8")
        for reference in re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, flags=re.MULTILINE):
            if reference.startswith("./"):
                continue
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference), (path, reference)
