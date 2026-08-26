"""Fail-closed validation for values that are embedded in public clients."""
from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

PUBLISHABLE_KEY_RE = re.compile(r"^sb_publishable_[A-Za-z0-9._-]{16,}$")


class PublicConfigError(ValueError):
    """Raised before a credential-like value can enter a public bundle."""


def _jwt_payload(value: str) -> Mapping[str, object] | None:
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, Mapping) else None


def browser_key_kind(value: str) -> str:
    """Return the accepted public-key kind or fail before build/deploy."""

    key = value.strip()
    if not key:
        raise PublicConfigError("Supabase browser key is missing")
    if key.startswith("sb_secret_"):
        raise PublicConfigError("Supabase secret keys are forbidden in public clients")
    if PUBLISHABLE_KEY_RE.fullmatch(key):
        return "publishable"
    claims = _jwt_payload(key)
    if claims is not None and claims.get("role") == "anon":
        return "legacy_anon"
    if claims is not None and claims.get("role") == "service_role":
        raise PublicConfigError("Supabase service-role JWTs are forbidden in public clients")
    raise PublicConfigError("Supabase browser key must be publishable or a legacy anon JWT")


def _https_host(value: str, name: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise PublicConfigError(f"{name} must be an absolute credential-free HTTPS URL")
    return parsed.netloc


def validate_public_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Validate GitHub/Vite public configuration without returning key material."""

    values = environment or os.environ
    supabase_url = values.get("VITE_SUPABASE_URL", "")
    publish_url = values.get("STOCKSCOUT_EOD_PUBLISH_URL", "")
    key = values.get("VITE_SUPABASE_PUBLISHABLE_KEY", "")
    return {
        "supabaseHost": _https_host(supabase_url, "VITE_SUPABASE_URL"),
        "publishHost": _https_host(publish_url, "STOCKSCOUT_EOD_PUBLISH_URL"),
        "browserKeyKind": browser_key_kind(key),
    }
