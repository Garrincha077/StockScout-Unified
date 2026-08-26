from __future__ import annotations

import gzip
import json
from io import BytesIO
from urllib.error import HTTPError

import pandas as pd
import pytest

from stock_scout.data.cache import ParquetCache
from stockscout_eod import charts
from stockscout_eod.charts import build_chart_staging
from tests.test_artifacts import _scan


def _bars(periods: int, frequency: str) -> pd.DataFrame:
    index = pd.date_range(end="2026-08-21", periods=periods, freq=frequency)
    values = range(periods)
    return pd.DataFrame(
        {
            "open": [10.0 + value / 10 for value in values],
            "high": [10.5 + value / 10 for value in values],
            "low": [9.5 + value / 10 for value in values],
            "close": [10.2 + value / 10 for value in values],
            "volume": [100_000 + value for value in values],
        },
        index=index,
    )


def test_chart_shards_are_compact_gzipped_and_staged_outside_pages(tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    cache = ParquetCache(cache_dir)
    cache.write(_bars(300, "B"), "fixture", "AAA", "daily")
    cache.write(_bars(80, "W-FRI"), "fixture", "AAA", "weekly")
    config = tmp_path / "chart-config.yaml"
    config.write_text(
        f"cache:\n  base_dir: '{cache_dir.as_posix()}'\nmarketdata:\n  enabled: false\n",
        encoding="utf-8",
    )
    scan = _scan()
    for row in [*scan.candidates, *scan.excluded]:
        row["provider_used"] = "fixture"

    destination = tmp_path / "chart-staging"
    manifest = build_chart_staging(
        scan,
        config_path=config,
        output_dir=destination,
        storage_base_url=(
            "https://fixture.supabase.co/storage/v1/object/public/"
            f"stockscout-eod-charts/{scan.run_id}"
        ),
    )
    assert manifest.requested == 3
    assert manifest.available == 1
    assert manifest.coverage_pct == 33.33
    assert set(manifest.shards_by_ticker) == {"AAA"}
    assert manifest.storage_base_url.endswith(scan.run_id)
    assert all(shard.bytes < 5 * 1024 * 1024 for shard in manifest.shards)

    populated = next(shard for shard in manifest.shards if shard.ticker_count)
    compressed = destination / scan.run_id / "shards" / f"{populated.name}.json.gz"
    rows = json.loads(gzip.decompress(compressed.read_bytes()))
    assert manifest.shards_by_ticker["AAA"] == populated.name
    assert rows["AAA"]["columns"] == ["t", "o", "h", "l", "c", "v"]
    assert len(rows["AAA"]["daily"]) == 300
    assert 50 <= len(rows["AAA"]["weekly"]) <= 65
    assert not (tmp_path / "public").exists()


def test_chart_builder_rejects_pages_public_destination(tmp_path) -> None:
    scan = _scan()
    with pytest.raises(ValueError, match="outside the Pages public"):
        build_chart_staging(
            scan,
            config_path="config/eod.yaml",
            output_dir=tmp_path / "frontend" / "public" / "charts",
            storage_base_url=(
                "https://fixture.supabase.co/storage/v1/object/public/"
                f"stockscout-eod-charts/{scan.run_id}"
            ),
        )


def test_chart_publisher_surfaces_bounded_json_error(monkeypatch) -> None:
    def reject(*_args, **_kwargs):
        raise HTTPError(
            "https://example.invalid/publish",
            400,
            "Bad Request",
            {},
            BytesIO(b'{"error":"legacy chart manifest mismatch"}'),
        )

    monkeypatch.setattr(charts, "urlopen", reject)

    with pytest.raises(
        RuntimeError,
        match=r"HTTP 400.*legacy chart manifest mismatch",
    ):
        charts._post_json(
            "https://example.invalid/publish",
            "secret-token",
            {"action": "promote_chart_run"},
        )
