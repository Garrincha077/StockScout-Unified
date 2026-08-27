from __future__ import annotations

from pathlib import Path

from stockscout_eod.deployment import verify_pages_activation
from stockscout_eod.jsonio import write_json

DEPLOY_PAGES = "actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e"
REUSABLE_DEPLOY = "uses: ./.github/workflows/deploy-pages.yml"


def test_pages_activation_retries_old_pointer_then_matches_exact_hash(tmp_path) -> None:
    public = tmp_path / "public"
    current = {"runId": "new-run", "status": "healthy"}
    current_bytes = write_json(public / "data" / "manifest.json", current)
    responses = iter([b'{"runId":"old-run"}', current_bytes])
    sleeps: list[float] = []
    result = verify_pages_activation(
        public_dir=public,
        manifest_url="https://example.test/data/manifest.json",
        fetcher=lambda _url: next(responses),
        sleeper=sleeps.append,
    )
    assert result["runId"] == "new-run"
    assert result["attempt"] == 2
    assert sleeps == [2.0]


def test_unified_workflow_deploys_only_after_three_mode_activation() -> None:
    workflow = Path(".github/workflows/eod.yml").read_text(encoding="utf-8")
    assert "publish-adjusted --mode next" in workflow
    assert "publish-adjusted --mode ryan-original" in workflow
    assert "stockscout_unified.cli activate" in workflow
    assert "Upload Pages artifact only after all three modes pass" in workflow
    assert "path: frontend/dist" in workflow
    assert REUSABLE_DEPLOY in workflow
    reusable = Path(".github/workflows/deploy-pages.yml").read_text(encoding="utf-8")
    assert DEPLOY_PAGES in reusable
    assert "Verify exact remote run and every mode hash" in reusable
    assert workflow.index("stockscout_unified.cli activate") < workflow.index("actions/upload-pages-artifact")


def test_existing_snapshot_workflow_contains_no_scan_or_notification_delivery() -> None:
    workflow = Path(".github/workflows/publish-existing.yml").read_text(encoding="utf-8")
    assert "stockscout_eod.cli scan" not in workflow
    assert "run_fast_scan.py" not in workflow
    assert "--allow-notify" not in workflow
    assert "sha256sum --check --strict" in workflow
    assert "next_snapshot_commit" in workflow
    assert "Next engine source commit mismatch" in workflow
    assert "verify_next_chart_snapshot.py" in workflow
    assert "StockScreener-next/data/charts" in workflow
    assert "publish-bottom" in workflow
    assert "publish-adjusted" in workflow
    assert REUSABLE_DEPLOY in workflow
    assert "group: stockscout-unified-eod" not in workflow
    carrier = Path(".github/workflows/eod.yml").read_text(encoding="utf-8")
    assert "reuse_existing:" in carrier
    assert "uses: ./.github/workflows/publish-existing.yml" in carrier
    assert "!inputs.reuse_existing" in carrier
