"""Small GitHub Actions OIDC token client shared by cloud publishers."""
from __future__ import annotations

import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


def github_oidc_token(audience: str) -> str:
    base_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not base_url or not request_token:
        raise RuntimeError("GitHub Actions OIDC environment is unavailable")
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["audience"] = audience
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    request = Request(url, headers={"Authorization": f"Bearer {request_token}"})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("value")
    if not token:
        raise RuntimeError("GitHub OIDC response did not contain a token")
    return str(token)
