from __future__ import annotations

import base64
import gzip
import io
import json
import tarfile
from urllib.error import HTTPError

import pytest

import stockscout_eod.market_cache as market_cache
from stockscout_eod.market_cache import (
    _download_cache_blob,
    _extract_archive,
    _post,
    build_market_cache_staging,
    publish_market_cache_staging,
    restore_market_cache,
)


def test_market_cache_round_trip_is_private_sharded_and_deterministic(tmp_path) -> None:
    cache = tmp_path / "cache"
    (cache / "yfinance" / "daily").mkdir(parents=True)
    (cache / "yfinance" / "daily" / "AAA.parquet").write_bytes(b"derived-test-bars")
    (cache / "yfinance" / "daily" / "AAA.meta.json").write_text("{}")
    first = tmp_path / "first"
    second = tmp_path / "second"
    manifest = build_market_cache_staging(cache, first)
    repeated = build_market_cache_staging(cache, second)
    assert manifest == repeated
    assert manifest["fileCount"] == 2
    assert not any(path.suffix == ".parquet" for path in first.rglob("*"))
    restored = tmp_path / "restored"
    count = 0
    for shard in manifest["shards"]:
        count += _extract_archive(
            (first / "shards" / f"{shard['name']}.bin.gz").read_bytes(), restored
        )
    assert count == 2
    assert (restored / "yfinance" / "daily" / "AAA.parquet").read_bytes() == b"derived-test-bars"


def test_market_cache_archive_rejects_path_traversal(tmp_path) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo("../secret")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="unsafe"):
        _extract_archive(gzip.compress(raw.getvalue()), tmp_path / "restore")


def test_market_cache_restore_allows_only_a_real_cold_bootstrap(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*_args: object, **_kwargs: object) -> bytes:
        raise RuntimeError("market cache lookup failed: Object not found")

    monkeypatch.setattr(market_cache, "_download_cache_blob", missing)
    assert restore_market_cache(tmp_path / "cache", "https://publisher.invalid", token="oidc") is None


def test_market_cache_restore_does_not_hide_authorization_or_service_failures(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(*_args: object, **_kwargs: object) -> bytes:
        raise RuntimeError("market cache lookup failed: permission denied")

    monkeypatch.setattr(market_cache, "_download_cache_blob", denied)
    with pytest.raises(RuntimeError, match="permission denied"):
        restore_market_cache(tmp_path / "cache", "https://publisher.invalid", token="oidc")


def test_market_cache_restore_does_not_treat_an_endpoint_404_as_cold_bootstrap(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def wrong_endpoint(*_args: object, **_kwargs: object) -> bytes:
        raise RuntimeError("market cache publisher HTTP 404: route not found")

    monkeypatch.setattr(market_cache, "_download_cache_blob", wrong_endpoint)
    with pytest.raises(RuntimeError, match="route not found"):
        restore_market_cache(tmp_path / "cache", "https://publisher.invalid", token="oidc")


def test_market_cache_post_retries_a_transient_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    delays: list[float] = []

    def opener(_request: object, timeout: int) -> io.BytesIO:
        nonlocal calls
        calls += 1
        assert timeout == 120
        if calls == 1:
            raise TimeoutError("read operation timed out")
        return io.BytesIO(b'{"ok":true,"data":{"path":"rolling-v1/00.bin.gz"}}')

    monkeypatch.setattr(market_cache, "urlopen", opener)
    response = _post(
        "https://publisher.invalid",
        "oidc",
        {"action": "put_blob"},
        sleeper=delays.append,
    )

    assert response == {"path": "rolling-v1/00.bin.gz"}
    assert calls == 2
    assert delays == [1]


def test_market_cache_post_does_not_retry_an_authorization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    delays: list[float] = []

    def opener(_request: object, timeout: int) -> io.BytesIO:
        nonlocal calls
        calls += 1
        assert timeout == 120
        raise HTTPError(
            "https://publisher.invalid",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":"OIDC claims rejected"}'),
        )

    monkeypatch.setattr(market_cache, "urlopen", opener)
    with pytest.raises(RuntimeError, match=r"HTTP 401: OIDC claims rejected"):
        _post(
            "https://publisher.invalid",
            "oidc",
            {"action": "put_blob"},
            sleeper=delays.append,
        )

    assert calls == 1
    assert delays == []


def test_market_cache_post_honors_retry_after_for_a_transient_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    delays: list[float] = []

    def opener(_request: object, timeout: int) -> io.BytesIO:
        nonlocal calls
        calls += 1
        assert timeout == 120
        if calls == 1:
            raise HTTPError(
                "https://publisher.invalid",
                503,
                "Unavailable",
                {"Retry-After": "4"},
                io.BytesIO(b'{"error":"temporary"}'),
            )
        return io.BytesIO(b'{"ok":true,"data":{}}')

    monkeypatch.setattr(market_cache, "urlopen", opener)
    assert _post(
        "https://publisher.invalid",
        "oidc",
        {"action": "put_blob"},
        sleeper=delays.append,
    ) == {}
    assert calls == 2
    assert delays == [4]


def test_market_cache_post_reports_transient_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    delays: list[float] = []

    def opener(_request: object, timeout: int) -> io.BytesIO:
        nonlocal calls
        calls += 1
        assert timeout == 120
        raise TimeoutError("read operation timed out")

    monkeypatch.setattr(market_cache, "urlopen", opener)
    with pytest.raises(RuntimeError, match="network failure"):
        _post(
            "https://publisher.invalid",
            "oidc",
            {"action": "put_blob"},
            sleeper=delays.append,
        )
    assert calls == 3
    assert delays == [1, 2]


def test_market_cache_signed_download_retries_a_transient_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    delays: list[float] = []
    monkeypatch.setattr(
        market_cache,
        "_post",
        lambda *_args, **_kwargs: {"signedUrl": "https://storage.invalid/signed"},
    )

    def opener(request: object, timeout: int) -> io.BytesIO:
        nonlocal calls
        calls += 1
        assert request.full_url == "https://storage.invalid/signed"
        assert timeout == 120
        if calls == 1:
            raise TimeoutError("read operation timed out")
        return io.BytesIO(b"cache-data")

    monkeypatch.setattr(market_cache, "urlopen", opener)
    assert _download_cache_blob(
        "https://publisher.invalid",
        "oidc",
        "00",
        sleeper=delays.append,
    ) == b"cache-data"
    assert calls == 2
    assert delays == [1]


def test_market_cache_publish_refreshes_the_short_lived_actions_oidc_token(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "AAA.parquet").write_bytes(b"bars")
    staging = tmp_path / "staging"
    build_market_cache_staging(cache, staging)
    monkeypatch.setattr(market_cache, "_load_remote_manifest", lambda *_args: None)

    def token(expires_at: int, marker: str) -> str:
        payload = base64.urlsafe_b64encode(
            json.dumps({"exp": expires_at}).encode("utf-8")
        ).decode("ascii").rstrip("=")
        return f"header.{payload}.{marker}"

    first_token = token(400, "first")
    second_token = token(700, "second")
    issued = iter((first_token, second_token))
    issued_count = 0
    now = 100.0

    def oidc_token() -> str:
        nonlocal issued_count
        issued_count += 1
        return next(issued)

    authorizations: list[str | None] = []

    def opener(request: object, timeout: int) -> io.BytesIO:
        nonlocal now
        assert timeout == 120
        authorizations.append(request.get_header("Authorization"))
        now = 350.0
        return io.BytesIO(b'{"ok":true,"data":{}}')

    monkeypatch.setattr(market_cache, "_oidc_token", oidc_token)
    monkeypatch.setattr(market_cache.time, "time", lambda: now)
    monkeypatch.setattr(market_cache, "urlopen", opener)

    publish_market_cache_staging(staging, "https://publisher.invalid")

    assert authorizations == [f"Bearer {first_token}", f"Bearer {second_token}"]
    assert issued_count == 2


def test_market_cache_publish_replays_identical_bytes_and_commits_manifest_last(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "AAA.parquet").write_bytes(b"bars")
    staging = tmp_path / "staging"
    build_market_cache_staging(cache, staging)
    monkeypatch.setattr(market_cache, "_load_remote_manifest", lambda *_args: None)

    requests: list[dict[str, object]] = []
    calls = 0

    def opener(request: object, timeout: int) -> io.BytesIO:
        nonlocal calls
        calls += 1
        assert timeout == 120
        requests.append(json.loads(request.data))
        if calls == 1:
            raise TimeoutError("response lost after commit")
        return io.BytesIO(b'{"ok":true,"data":{}}')

    monkeypatch.setattr(market_cache, "urlopen", opener)
    post = market_cache._post
    monkeypatch.setattr(
        market_cache,
        "_post",
        lambda endpoint, token, payload: post(
            endpoint,
            token,
            payload,
            sleeper=lambda _delay: None,
        ),
    )
    published = publish_market_cache_staging(
        staging,
        "https://publisher.invalid",
        token="oidc",
    )

    assert requests[0] == requests[1]
    assert requests[0]["shard"] == published["shards"][0]["name"]
    assert requests[-1]["shard"] == "manifest"
    assert published["slot"] == "s0"
    assert published["shards"][0]["objectName"] == published["shards"][0]["name"]


def test_market_cache_publish_writes_the_inactive_slot(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "AAA.parquet").write_bytes(b"bars")
    staging = tmp_path / "staging"
    build_market_cache_staging(cache, staging)
    monkeypatch.setattr(
        market_cache,
        "_load_remote_manifest",
        lambda *_args: {
            "schemaVersion": "stockscout-eod/market-cache-v1",
            "runId": "rolling-v1",
            "slot": "s0",
        },
    )
    requests: list[dict[str, object]] = []

    def post(_endpoint: str, _token: str, payload: dict[str, object]) -> dict[str, object]:
        requests.append(payload)
        return {}

    monkeypatch.setattr(market_cache, "_post", post)
    published = publish_market_cache_staging(
        staging,
        "https://publisher.invalid",
        token="oidc",
    )

    logical_name = published["shards"][0]["name"]
    assert published["slot"] == "s1"
    assert published["shards"][0]["objectName"] == f"s1-{logical_name}"
    assert requests[0]["shard"] == f"s1-{logical_name}"
    assert requests[-1]["shard"] == "manifest"
    committed = json.loads(
        gzip.decompress(base64.b64decode(requests[-1]["contentBase64"]))
    )
    assert committed == published


@pytest.mark.parametrize("use_object_name", [False, True])
def test_market_cache_restore_supports_legacy_and_slotted_manifests(
    tmp_path, monkeypatch: pytest.MonkeyPatch, use_object_name: bool
) -> None:
    cache = tmp_path / "source"
    cache.mkdir()
    (cache / "AAA.parquet").write_bytes(b"bars")
    staging = tmp_path / "staging"
    manifest = build_market_cache_staging(cache, staging)
    shard = manifest["shards"][0]
    expected_object_name = "s1-" + shard["name"] if use_object_name else shard["name"]
    if use_object_name:
        shard["objectName"] = expected_object_name
    monkeypatch.setattr(market_cache, "_load_remote_manifest", lambda *_args: manifest)
    downloads: list[str] = []

    def download(_endpoint: str, _token: str, object_name: str) -> bytes:
        downloads.append(object_name)
        return (staging / "shards" / f"{shard['name']}.bin.gz").read_bytes()

    monkeypatch.setattr(market_cache, "_download_cache_blob", download)
    restored = tmp_path / "restored"
    assert restore_market_cache(restored, "https://publisher.invalid", token="oidc") == manifest
    assert downloads == [expected_object_name]
    assert (restored / "AAA.parquet").read_bytes() == b"bars"
