from __future__ import annotations

import base64
import json

import pytest

from stockscout_eod.public_config import (
    PublicConfigError,
    browser_key_kind,
    validate_owner_environment,
    validate_public_environment,
)


def _jwt(role: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_only_publishable_or_legacy_anon_keys_are_browser_safe() -> None:
    assert browser_key_kind("sb_publishable_abcdefghijklmnop") == "publishable"
    assert browser_key_kind(_jwt("anon")) == "legacy_anon"
    with pytest.raises(PublicConfigError, match="secret"):
        browser_key_kind("sb_secret_abcdefghijklmnop")
    with pytest.raises(PublicConfigError, match="service-role"):
        browser_key_kind(_jwt("service_role"))
    with pytest.raises(PublicConfigError, match="publishable"):
        browser_key_kind("not-a-key")


def test_public_environment_requires_https_and_never_returns_the_key() -> None:
    result = validate_public_environment(
        {
            "VITE_SUPABASE_URL": "https://project.supabase.co",
            "VITE_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_abcdefghijklmnop",
            "STOCKSCOUT_EOD_PUBLISH_URL": (
                "https://project.supabase.co/functions/v1/stockscout-eod-publish"
            ),
        }
    )
    assert result == {
        "supabaseHost": "project.supabase.co",
        "publishHost": "project.supabase.co",
        "browserKeyKind": "publishable",
    }
    assert "abcdefghijklmnop" not in json.dumps(result)
    with pytest.raises(PublicConfigError, match="HTTPS"):
        validate_public_environment(
            {
                "VITE_SUPABASE_URL": "http://project.supabase.co",
                "VITE_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_abcdefghijklmnop",
                "STOCKSCOUT_EOD_PUBLISH_URL": "https://project.supabase.co/functions/v1/x",
            }
        )


def test_owner_environment_is_all_or_none_and_same_project() -> None:
    assert validate_owner_environment({}) == {"configured": "false"}
    with pytest.raises(PublicConfigError, match="required"):
        validate_owner_environment({}, required=True)
    with pytest.raises(PublicConfigError, match="partial"):
        validate_owner_environment({"VITE_SUPABASE_URL": "https://project.supabase.co"})
    result = validate_owner_environment(
        {
            "VITE_SUPABASE_URL": "https://project.supabase.co",
            "VITE_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_abcdefghijklmnop",
            "UNIFIED_DELIVERY_ENDPOINT": (
                "https://project.supabase.co/functions/v1/unified-operations"
            ),
        },
        required=True,
    )
    assert result == {
        "configured": "true",
        "supabaseHost": "project.supabase.co",
        "browserKeyKind": "publishable",
        "deliveryFunction": "unified-operations",
    }
    with pytest.raises(PublicConfigError, match="Supabase host"):
        validate_owner_environment(
            {
                "VITE_SUPABASE_URL": "https://project.supabase.co",
                "VITE_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_abcdefghijklmnop",
                "UNIFIED_DELIVERY_ENDPOINT": (
                    "https://other.supabase.co/functions/v1/unified-operations"
                ),
            }
        )
