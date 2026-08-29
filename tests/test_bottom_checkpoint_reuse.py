import gzip
import json
from pathlib import Path

import pytest

from stockscout_eod.jsonio import sha256_bytes
from stockscout_unified.reuse import rebind_bottom_checkpoint


def _write_checkpoint(root: Path, *, run_id: str = "2026-08-28-eod-old") -> bytes:
    root.mkdir(parents=True, exist_ok=True)
    raw = {
        "schemaVersion": "stockscout-engine/v1",
        "runId": run_id,
        "sessionDate": "2026-08-28",
        "generatedAt": "2026-08-28T22:00:00+00:00",
        "priceMode": "split_only",
        "candidates": [],
        "excluded": [],
        "stats": {"universe_size": 2000, "coverage_pct": 100.0, "data_status": "OK"},
        "stageRows": [],
        "market": {},
        "provenance": {"engineSource": "test"},
        "versions": {"engine": "fixture"},
    }
    (root / "bottom.json").write_text(json.dumps(raw), encoding="utf-8")

    shard_bytes = gzip.compress(b"{}", compresslevel=9, mtime=0)
    chart_root = root / "bottom-charts" / run_id
    shard_root = chart_root / "shards"
    shard_root.mkdir(parents=True)
    (shard_root / "000.json.gz").write_bytes(shard_bytes)
    charts = {
        "schemaVersion": "stockscout-eod/charts-v1",
        "runId": run_id,
        "sessionDate": "2026-08-28",
        "generatedAt": "2026-08-28T22:00:00+00:00",
        "priceMode": "split_only",
        "requested": 1,
        "available": 1,
        "coveragePct": 100.0,
        "storageBaseUrl": f"https://example.test/runs/{run_id}/charts",
        "shards": [
            {
                "name": "000",
                "sha256": sha256_bytes(shard_bytes),
                "bytes": len(shard_bytes),
                "tickerCount": 0,
            }
        ],
        "shardsByTicker": {},
    }
    (chart_root / "manifest.json").write_text(json.dumps(charts), encoding="utf-8")
    return shard_bytes


def test_rebind_bottom_checkpoint_changes_only_run_scoped_identity(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    shard_bytes = _write_checkpoint(checkpoint)
    output = tmp_path / "output"
    new_run = "2026-08-28-eod-new"
    storage = f"https://garrincha077.github.io/StockScout-Unified/data/modes/bottom-fishing/runs/{new_run}/charts"

    raw_path, charts = rebind_bottom_checkpoint(
        checkpoint,
        output_dir=output,
        run_id=new_run,
        expected_session_date="2026-08-28",
        storage_base_url=storage,
    )

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw["runId"] == new_run
    assert raw["sessionDate"] == "2026-08-28"
    assert raw["provenance"]["checkpointReuse"] is True
    assert raw["provenance"]["checkpointSourceRunId"] == "2026-08-28-eod-old"
    assert charts.run_id == new_run
    assert charts.storage_base_url == storage
    assert (output / "bottom-charts" / new_run / "shards" / "000.json.gz").read_bytes() == shard_bytes


def test_rebind_bottom_checkpoint_rejects_wrong_session(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint"
    _write_checkpoint(checkpoint)

    with pytest.raises(ValueError, match="checkpoint session"):
        rebind_bottom_checkpoint(
            checkpoint,
            output_dir=tmp_path / "output",
            run_id="2026-08-27-eod-new",
            expected_session_date="2026-08-27",
            storage_base_url="https://example.test/runs/2026-08-27-eod-new/charts",
        )
