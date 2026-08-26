from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = next((ROOT / "supabase" / "migrations").glob("*_owner_state_only.sql"))
OPERATIONS = ROOT / "supabase" / "functions" / "unified-operations" / "index.ts"


def test_owner_state_is_mode_and_price_basis_scoped() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    for table in (
        "owner_allowlist",
        "unified_watchlist_items",
        "unified_saved_screens",
        "unified_drawings",
        "unified_alerts",
        "unified_alert_events",
        "unified_delivery_state",
    ):
        assert f"alter table stockscout_unified_api.{table} enable row level security" in sql
    assert sql.count("mode text not null") >= 5
    assert sql.count("price_basis text not null") >= 5
    assert "mode in ('bottom-fishing','next','ryan-original')" in sql
    assert "price_basis in ('split_only','split_div')" in sql


def test_browser_grants_are_explicit_and_anon_has_no_owner_access() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "revoke all on schema stockscout_unified_api from public, anon, authenticated" in sql
    assert "revoke all on all tables in schema stockscout_unified_api from public, anon" in sql
    assert "revoke all on all functions in schema stockscout_unified_api from public, anon, authenticated" in sql
    assert "grant usage on schema stockscout_unified_api to authenticated" in sql
    assert "grant select on stockscout_unified_api.unified_alert_events to authenticated" in sql
    assert "grant select on stockscout_unified_api.unified_delivery_state to authenticated" in sql
    assert "grant execute on function stockscout_unified_api.unified_set_watchlist_ticker" in sql
    assert "service_role" not in sql


def test_owner_policies_bind_rows_to_auth_uid_and_allowlist() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "user_metadata" not in sql
    assert sql.count("(select auth.uid()) = user_id") >= 7
    assert sql.count("from stockscout_unified_api.owner_allowlist") >= 6
    assert "security invoker" in sql
    assert "set search_path = ''" in sql


def test_operations_endpoint_is_github_oidc_scoped_and_has_no_static_publish_secret() -> None:
    source = OPERATIONS.read_text(encoding="utf-8")
    for claim in ("repository", "ref", "workflow_ref", "environment", "ref_protected"):
        assert claim in source
    assert "stockscout-unified-operations" in source
    assert "delivery_get" in source
    assert "delivery_mark" in source
    assert "evaluate_alerts" in source
    assert "SUPABASE_SERVICE_ROLE_KEY" in source
    assert "UNIFIED_PUBLISH_TOKEN" not in source
    assert "TELEGRAM_BOT_TOKEN" not in source
