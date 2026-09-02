from __future__ import annotations

from pathlib import Path


def test_owner_oidc_allowlist_is_exact_and_includes_notification_retry() -> None:
    source = Path("supabase/functions/unified-operations/index.ts").read_text(encoding="utf-8")

    assert "const EXPECTED_WORKFLOWS = new Set([" in source
    assert "`${EXPECTED_REPOSITORY}/.github/workflows/eod.yml@${EXPECTED_REF}`" in source
    assert "`${EXPECTED_REPOSITORY}/.github/workflows/notification-retry.yml@${EXPECTED_REF}`" in source
    assert "!EXPECTED_WORKFLOWS.has(String(claims.workflow_ref))" in source
    assert 'claims.repository !== EXPECTED_REPOSITORY' in source
    assert 'claims.ref !== EXPECTED_REF' in source
    assert 'claims.environment !== "production"' in source
    assert 'String(claims.ref_protected) !== "true"' in source
    assert "workflow_ref?.includes" not in source
    assert "workflow_ref?.endsWith" not in source
