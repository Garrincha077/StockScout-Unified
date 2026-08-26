"""Private GitHub-runner market cache backed by the owner-only Supabase bucket.

Raw provider bars never enter Git, Pages, or an Actions artifact/cache.  The
workflow restores and refreshes these deterministic gzip/tar shards directly
through the OIDC-authenticated publisher.
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import os
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from stockscout_eod.jsonio import canonical_json_bytes, sha256_bytes, write_json

CACHE_RUN_ID = "rolling-v1"
MAX_REMOTE_SHARD_BYTES = 8_000_000
TARGET_SHARD_BYTES = 6_500_000
RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
PUBLICATION_SLOTS = {"s0", "s1"}
OIDC_REFRESH_SKEW_SECONDS = 60

TokenSource = str | Callable[[], str]


@dataclass(frozen=True)
class CacheShard:
    name: str
    sha256: str
    bytes: int
    files: int


def _cache_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and (path.suffix == ".parquet" or path.name.endswith(".meta.json"))
    )


def _archive(root: Path, files: list[Path]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in files:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            data = path.read_bytes()
            info = tarfile.TarInfo(relative)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o600
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), compresslevel=9, mtime=0)


def _split_archive(root: Path, name: str, files: list[Path]) -> list[tuple[str, bytes, int]]:
    payload = _archive(root, files)
    if len(payload) <= TARGET_SHARD_BYTES:
        return [(name, payload, len(files))]
    if len(files) == 1:
        raise ValueError(f"market cache file cannot fit an 8 MB shard: {files[0].name}")
    midpoint = len(files) // 2
    return [
        *_split_archive(root, f"{name}a", files[:midpoint]),
        *_split_archive(root, f"{name}b", files[midpoint:]),
    ]


def build_market_cache_staging(
    cache_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    root = Path(cache_dir).resolve()
    output = Path(output_dir).resolve()
    if output == root or root in output.parents:
        raise ValueError("market cache staging must be outside the raw cache directory")
    groups: dict[str, list[Path]] = {}
    for path in _cache_files(root):
        relative = path.relative_to(root).as_posix()
        groups.setdefault(sha256_bytes(relative.encode("utf-8"))[:2], []).append(path)

    shard_dir = output / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    shards: list[CacheShard] = []
    for group, files in sorted(groups.items()):
        for name, payload, file_count in _split_archive(root, group, files):
            if not payload or len(payload) > MAX_REMOTE_SHARD_BYTES:
                raise ValueError(f"market cache shard {name} violates remote size limits")
            (shard_dir / f"{name}.bin.gz").write_bytes(payload)
            shards.append(
                CacheShard(
                    name=name,
                    sha256=sha256_bytes(payload),
                    bytes=len(payload),
                    files=file_count,
                )
            )
    manifest = {
        "schemaVersion": "stockscout-eod/market-cache-v1",
        "runId": CACHE_RUN_ID,
        "shards": [shard.__dict__ for shard in shards],
        "fileCount": sum(shard.files for shard in shards),
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def _oidc_token(audience: str = "stockscout-eod-publish") -> str:
    base_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not base_url or not request_token:
        raise RuntimeError("GitHub Actions OIDC environment is unavailable")
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["audience"] = audience
    request = Request(
        urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)),
        headers={"Authorization": f"Bearer {request_token}"},
    )
    with urlopen(request, timeout=30) as response:
        token = json.loads(response.read().decode("utf-8")).get("value")
    if not token:
        raise RuntimeError("GitHub OIDC response did not contain a token")
    return str(token)


def _jwt_expiration(token: str) -> float | None:
    """Read ``exp`` only to schedule refresh; Edge still verifies the JWT."""
    try:
        encoded_payload = token.split(".")[1]
        padding = "=" * (-len(encoded_payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded_payload + padding))
        expires_at = claims.get("exp") if isinstance(claims, dict) else None
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
            return None
        return float(expires_at)
    except (IndexError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None


def _bearer_provider(explicit_token: str | None) -> Callable[[], str]:
    if explicit_token is not None:
        if not explicit_token:
            raise ValueError("explicit OIDC token cannot be empty")
        return lambda: explicit_token

    cached_token: str | None = None
    expires_at = 0.0

    def bearer() -> str:
        nonlocal cached_token, expires_at
        now = time.time()
        if cached_token is None or now + OIDC_REFRESH_SKEW_SECONDS >= expires_at:
            refreshed = _oidc_token()
            refreshed_expiry = _jwt_expiration(refreshed)
            if refreshed_expiry is None:
                raise RuntimeError("GitHub OIDC token does not contain a valid exp claim")
            if refreshed_expiry <= now + OIDC_REFRESH_SKEW_SECONDS:
                raise RuntimeError("GitHub OIDC token expires too soon for a cache request")
            cached_token = refreshed
            expires_at = refreshed_expiry
        return cached_token

    return bearer


def _resolve_token(token: TokenSource) -> str:
    bearer = token() if callable(token) else token
    if not bearer:
        raise RuntimeError("market cache request has no bearer token")
    return bearer


def _post(
    endpoint: str,
    token: TokenSource,
    payload: dict[str, Any],
    *,
    max_attempts: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    request_body = canonical_json_bytes(payload)
    for attempt in range(max_attempts):
        request = Request(
            endpoint,
            data=request_body,
            headers={
                "Authorization": f"Bearer {_resolve_token(token)}",
                "Content-Type": "application/json",
                "User-Agent": "StockScout-EOD/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            retryable = exc.code in RETRYABLE_HTTP_STATUS
            if retryable and attempt + 1 < max_attempts:
                sleeper(_retry_delay(exc, attempt))
                continue
            try:
                error_body = json.loads(exc.read().decode("utf-8"))
                detail = error_body.get("error") if isinstance(error_body, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                detail = None
            raise RuntimeError(
                f"market cache publisher HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except (ConnectionError, TimeoutError, URLError) as exc:
            if attempt + 1 < max_attempts:
                sleeper(_retry_delay(exc, attempt))
                continue
            raise RuntimeError(f"market cache publisher network failure: {exc}") from exc
        if not isinstance(body, dict):
            raise RuntimeError("market cache publisher returned an invalid response")
        if not body.get("ok"):
            raise RuntimeError(f"market cache publisher rejected request: {body.get('error')}")
        return body.get("data") or {}
    raise RuntimeError("market cache publisher retry loop exhausted")


def _retry_delay(exc: BaseException, attempt: int) -> float:
    retry_after = (
        exc.headers.get("Retry-After")
        if isinstance(exc, HTTPError) and exc.headers is not None
        else None
    )
    try:
        return min(30.0, max(0.0, float(retry_after)))
    except (TypeError, ValueError):
        return min(8.0, float(2**attempt))


def _is_missing_cache_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "market cache lookup failed:" in message and "not found" in message
    ) or message.startswith("market cache download http 404:")


def _publication_object_name(slot: str, logical_name: str) -> str:
    if slot not in PUBLICATION_SLOTS:
        raise ValueError(f"unsupported market cache publication slot: {slot}")
    # Slot zero intentionally keeps the legacy object names. This reuses the
    # first-run partial upload instead of stranding hundreds of large objects.
    return logical_name if slot == "s0" else f"s1-{logical_name}"


def publish_market_cache_staging(
    staging_dir: str | Path,
    endpoint: str,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    root = Path(staging_dir).resolve()
    manifest = json.loads((root / "manifest.json").read_bytes())
    if manifest.get("runId") != CACHE_RUN_ID:
        raise ValueError("market cache manifest runId mismatch")
    bearer = _bearer_provider(token)
    current = _load_remote_manifest(endpoint, bearer)
    if current is None:
        target_slot = "s0"
    else:
        current_slot = current.get("slot", "s0")
        if current_slot not in PUBLICATION_SLOTS:
            raise ValueError("active market cache manifest has an invalid publication slot")
        target_slot = "s1" if current_slot == "s0" else "s0"

    published_shards: list[dict[str, Any]] = []
    for shard in manifest.get("shards") or []:
        content = (root / "shards" / f"{shard['name']}.bin.gz").read_bytes()
        if len(content) != shard["bytes"] or sha256_bytes(content) != shard["sha256"]:
            raise ValueError(f"market cache shard integrity mismatch: {shard['name']}")
        object_name = _publication_object_name(target_slot, str(shard["name"]))
        _post(
            endpoint,
            bearer,
            {
                "action": "put_blob",
                "kind": "market-cache",
                "runId": CACHE_RUN_ID,
                "shard": object_name,
                "contentHash": shard["sha256"],
                "contentBase64": base64.b64encode(content).decode("ascii"),
            },
        )
        published_shards.append({**shard, "objectName": object_name})

    # The inactive slot is complete before this single stable pointer changes.
    # Therefore an interrupted refresh leaves the previous manifest usable.
    published_manifest = {
        **manifest,
        "slot": target_slot,
        "shards": published_shards,
    }
    published_manifest_bytes = canonical_json_bytes(published_manifest)
    compressed_manifest = gzip.compress(published_manifest_bytes, compresslevel=9, mtime=0)
    _post(
        endpoint,
        bearer,
        {
            "action": "put_blob",
            "kind": "market-cache",
            "runId": CACHE_RUN_ID,
            "shard": "manifest",
            "contentHash": sha256_bytes(compressed_manifest),
            "contentBase64": base64.b64encode(compressed_manifest).decode("ascii"),
        },
    )
    return published_manifest


def _download_cache_blob(
    endpoint: str,
    token: TokenSource,
    shard: str,
    *,
    max_attempts: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
) -> bytes:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    data = _post(
        endpoint,
        token,
        {"action": "get_market_cache", "runId": CACHE_RUN_ID, "shard": shard},
    )
    signed_url = data.get("signedUrl")
    if not isinstance(signed_url, str) or not signed_url.startswith("https://"):
        raise RuntimeError("market cache publisher did not return a signed URL")
    for attempt in range(max_attempts):
        request = Request(signed_url, headers={"User-Agent": "StockScout-EOD/0.1"})
        try:
            with urlopen(request, timeout=120) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code in RETRYABLE_HTTP_STATUS and attempt + 1 < max_attempts:
                sleeper(_retry_delay(exc, attempt))
                continue
            raise RuntimeError(f"market cache download HTTP {exc.code}: {exc.reason}") from exc
        except (ConnectionError, TimeoutError, URLError) as exc:
            if attempt + 1 < max_attempts:
                sleeper(_retry_delay(exc, attempt))
                continue
            raise RuntimeError(f"market cache download network failure: {exc}") from exc
    raise RuntimeError("market cache download retry loop exhausted")


def _load_remote_manifest(endpoint: str, token: TokenSource) -> dict[str, Any] | None:
    try:
        payload = _download_cache_blob(endpoint, token, "manifest")
    except RuntimeError as exc:
        if _is_missing_cache_error(exc):
            return None
        raise
    try:
        manifest = json.loads(gzip.decompress(payload))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("market cache manifest is not valid gzip JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("market cache manifest must be an object")
    if manifest.get("schemaVersion") != "stockscout-eod/market-cache-v1":
        raise ValueError("unsupported market cache manifest")
    if manifest.get("runId") != CACHE_RUN_ID:
        raise ValueError("market cache manifest runId mismatch")
    return manifest


def _extract_archive(payload: bytes, destination: Path) -> int:
    tar_bytes = gzip.decompress(payload)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts:
                raise ValueError("unsafe market cache archive member")
            target = destination.joinpath(*path.parts).resolve()
            if destination.resolve() not in target.parents:
                raise ValueError("market cache archive escaped its destination")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("market cache archive member has no content")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            count += 1
    return count


def restore_market_cache(
    cache_dir: str | Path,
    endpoint: str,
    *,
    token: str | None = None,
) -> dict[str, Any] | None:
    bearer = _bearer_provider(token)
    manifest = _load_remote_manifest(endpoint, bearer)
    if manifest is None:  # cold bootstrap is an expected first-run state
        return None
    destination = Path(cache_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    restored = 0
    for shard in manifest.get("shards") or []:
        object_name = str(shard.get("objectName") or shard["name"])
        payload = _download_cache_blob(endpoint, bearer, object_name)
        if len(payload) != shard["bytes"] or sha256_bytes(payload) != shard["sha256"]:
            raise ValueError(f"market cache download integrity mismatch: {shard['name']}")
        restored += _extract_archive(payload, destination)
    if restored != manifest.get("fileCount"):
        raise ValueError("market cache restored file count mismatch")
    return manifest
