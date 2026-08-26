from __future__ import annotations

import hashlib
import json

import pytest

from scripts.verify_next_chart_snapshot import verify


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _write_fixture(tmp_path):
    canonical = {
        "market": {"scanDate": "2026-08-25"},
        "universe": [{"ticker": "AAA"}, {"ticker": "BBB"}],
        "chartShards": {"AAA": "000.json", "BBB": "001.json"},
    }
    canonical_path = tmp_path / "latest.json"
    canonical_bytes = _encoded(canonical)
    canonical_path.write_bytes(canonical_bytes)
    chart_dir = tmp_path / "charts"
    chart_dir.mkdir()
    files = [
        ("charts/000.json", _encoded({"AAA": [[1, 2, 3, 1, 2, 100]]})),
        ("charts/001.json", _encoded({"BBB": [[1, 2, 3, 1, 2, 100]]})),
    ]
    for name, data in files:
        (chart_dir / name.split("/")[-1]).write_bytes(data)
    digest_rows = [
        {"path": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in files
    ]
    manifest = {
        "marketSession": {"date": "2026-08-25"},
        "provenance": {"source": {"sha256": hashlib.sha256(canonical_bytes).hexdigest()}},
        "assets": {
            "charts": {
                "shardCount": 2,
                "sha256": hashlib.sha256(_encoded(digest_rows)).hexdigest(),
                "bytes": sum(len(data) for _, data in files),
            }
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(_encoded(manifest))
    return canonical_path, manifest_path, chart_dir


def test_verifies_hash_bytes_and_ticker_coverage(tmp_path) -> None:
    canonical, manifest, charts = _write_fixture(tmp_path)

    result = verify(canonical, manifest, charts)

    assert result["sessionDate"] == "2026-08-25"
    assert result["tickers"] == 2
    assert result["shards"] == 2


def test_rejects_tampered_chart_shard(tmp_path) -> None:
    canonical, manifest, charts = _write_fixture(tmp_path)
    (charts / "000.json").write_bytes(_encoded({"AAA": [[999]]}))

    with pytest.raises(ValueError, match="aggregate hash mismatch"):
        verify(canonical, manifest, charts)
