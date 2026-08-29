import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "eod.yml"
RECOVERY_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-existing.yml"


def test_bottom_and_next_are_independent_scanner_jobs() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    prepare = workflow.index("  prepare:")
    bottom = workflow.index("  bottom:")
    next_job = workflow.index("  next:")
    assemble = workflow.index("  assemble:")
    deploy = workflow.index("  deploy-pages:")
    assert prepare < bottom < next_job < assemble < deploy
    assert workflow.count("needs: prepare") >= 2
    assert "needs: [prepare, bottom, next]" in workflow
    assert "needs.bottom.result == 'success'" in workflow
    assert "needs.next.result == 'success'" in workflow


def test_expensive_bottom_result_is_reusable_and_handed_off_before_assembly() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    restore = workflow.index("Restore same-session Bottom checkpoint")
    scan = workflow.index("Run Bottom Fishing scan and charts on cache miss")
    rebind = workflow.index("Rebind cached Bottom checkpoint to scanner run")
    validate = workflow.index("Validate Bottom handoff")
    save = workflow.index("Save reusable Bottom checkpoint before Next")
    handoff = workflow.index("Preserve Bottom scanner handoff")
    assemble = workflow.index("  assemble:")
    assert restore < scan < rebind < validate < save < handoff < assemble
    assert "actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830" in workflow
    assert "actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830" in workflow
    assert "steps.bottom_checkpoint.outputs.cache-hit != 'true'" in workflow
    assert "steps.bottom_checkpoint.outputs.cache-hit == 'true'" in workflow
    assert "rebind-bottom-checkpoint" in workflow
    assert "unified-bottom-v1-${{ runner.os }}-${{ needs.prepare.outputs.session_date }}" in workflow
    assert "name: unified-bottom-${{ github.run_id }}-${{ github.run_attempt }}" in workflow


def test_next_is_split_into_scan_enrich_validation_and_handoff_phases() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    scan = workflow.index("Run Next scanner (adjusted OHLCV)")
    resume = workflow.index("Preserve Next resume checkpoint after every scanner attempt")
    enrich = workflow.index("Enrich Next and exact Ryan Original capture")
    validate = workflow.index("Validate Next dataset before handoff")
    contexts = workflow.index("Build verified read-only Next contexts")
    stage = workflow.index("Stage validated Next handoff")
    handoff = workflow.index("Preserve Next scanner handoff")
    diagnostics = workflow.index("Preserve Next diagnostics after every attempt")
    assert scan < resume < enrich < validate < contexts < stage < handoff < diagnostics
    assert "name: unified-next-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "name: unified-next-diagnostics-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "if: ${{ always() }}" in workflow


def test_next_resume_checkpoint_is_attempt_scoped_and_identity_pinned() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    download = workflow.index("Download prior-attempt Next resume checkpoints")
    restore = workflow.index("Restore latest Next resume checkpoint")
    scan = workflow.index("Run Next scanner (adjusted OHLCV)")
    preserve = workflow.index("Preserve Next resume checkpoint after every scanner attempt")
    assert download < restore < scan < preserve
    assert "if: github.run_attempt > 1" in workflow
    assert "pattern: unified-next-progress-${{ github.run_id }}-*" in workflow
    assert "name: unified-next-progress-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "engines/next/data/batch_results/batch_progress.pkl" in workflow
    assert "STOCKSCOUT_PROGRESS_SOURCE_HASH:" in workflow
    assert "engines/next/src/**/*.py" in workflow
    assert "python run_fast_scan.py --conservative --git-storage --resume" in workflow


def test_assembly_downloads_scanner_handoffs_across_run_attempts() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "pattern: unified-bottom-${{ github.run_id }}-*" in workflow
    assert "pattern: unified-next-${{ github.run_id }}-*" in workflow
    assert "sort -V | tail -n 1" in workflow
    assert "Rebind and validate Bottom handoff for assembly" in workflow
    assert "Materialize and verify Next handoff" in workflow
    assert "python scripts/verify_next_chart_snapshot.py" in workflow


def test_next_validates_the_exact_orchestrator_session() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "STOCKSCOUT_EXPECTED_SESSION: ${{ needs.prepare.outputs.session_date }}" in workflow


def test_bottom_scan_has_an_early_market_session_preflight() -> None:
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
    assert audit < contexts < publish
    assert "--factor-regime .staging/next-context/factor-regime.json" in workflow
    assert "--gmli-context .staging/next-context/gmli-context.json" in workflow
    assert "python scripts/check_owner_config.py" in workflow
    assert "REQUIRE_OWNER: ${{ github.event_name == 'schedule' || inputs.notify }}" in workflow


def test_pages_deploy_waits_for_assembled_verified_artifact() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assemble = workflow.index("  assemble:")
    pages = workflow.index("Upload Pages artifact only after all three modes pass")
    deploy = workflow.index("  deploy-pages:")
    assert assemble < pages < deploy
    assert "needs: assemble" in workflow
    assert "run_id: ${{ needs.assemble.outputs.run_id }}" in workflow


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
