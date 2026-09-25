from __future__ import annotations

from pathlib import Path


def test_owner_oidc_allowlist_is_exact_and_scopes_trend_birth_read_only() -> None:
    source = Path("supabase/functions/unified-operations/index.ts").read_text(encoding="utf-8")

    assert "const UNIFIED_PRODUCTION_WORKFLOWS = new Set([" in source
    assert "`${UNIFIED_REPOSITORY}/.github/workflows/eod.yml@${UNIFIED_REF}`" in source
    assert "`${UNIFIED_REPOSITORY}/.github/workflows/notification-retry.yml@${UNIFIED_REF}`" in source
    assert "`${UNIFIED_REPOSITORY}/.github/workflows/trend-birth-hourly.yml@${UNIFIED_REF}`" in source
    assert "`${UNIFIED_REPOSITORY}/.github/workflows/trend-birth-gridview-telegram.yml@${UNIFIED_REF}`" in source
    assert 'const TREND_BIRTH_REPOSITORY = "Garrincha077/StockScout-Trend-Birth"' in source
    assert 'const TREND_BIRTH_REF = "refs/heads/feature/unified-review-grid-lab"' in source
    assert 'workflowRef === TREND_BIRTH_WATCHLIST_WORKFLOW' in source
    assert 'claims.environment === "production"' in source
    assert 'String(claims.ref_protected) === "true"' in source
    assert 'caller.scope === "trend-birth-watchlist" && action !== "watchlist_tickers"' in source
    assert "workflow_ref?.includes" not in source
    assert "workflow_ref?.endsWith" not in source
