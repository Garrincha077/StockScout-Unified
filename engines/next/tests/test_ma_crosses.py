from __future__ import annotations

import json
import pickle
from pathlib import Path

import pandas as pd

from compute_ma_crosses import MODEL, compute_metrics, cross_summary, hydrate_dataset, weekly_closes


def frame_from_prices(prices: list[float], start: str = "2025-01-02") -> pd.DataFrame:
    idx = pd.bdate_range(start, periods=len(prices))
    return pd.DataFrame({"Close": prices}, index=idx)


def test_cross_summary_reports_last_direction_and_age():
    idx = pd.bdate_range("2026-01-02", periods=6)
    fast = pd.Series([9, 9, 11, 12, 13, 14], index=idx, dtype=float)
    slow = pd.Series([10, 10, 10, 10, 10, 10], index=idx, dtype=float)
    result = cross_summary(fast, slow)
    assert result["state"] == "BULL"
    assert result["cross"] == "BULL"
    assert result["age"] == 3
    assert result["spreadPct"] == 40.0


def test_compute_metrics_has_daily_and_weekly_values():
    down = [120 - i * 0.25 for i in range(90)]
    up = [97.75 + i * 0.45 for i in range(110)]
    metrics = compute_metrics(frame_from_prices(down + up))

    assert metrics["ema10d"] is not None
    assert metrics["ema20d"] is not None
    assert metrics["ema10d20dState"] == "BULL"
    assert metrics["ema10d20dCross"] == "BULL"
    assert isinstance(metrics["ema10d20dCrossAge"], int)
    assert metrics["ema10d20dSpreadPct"] > 0

    assert metrics["sma10w"] is not None
    assert metrics["sma20w"] is not None
    assert metrics["sma10w20wState"] == "BULL"
    assert metrics["sma10w20wCross"] == "BULL"
    assert isinstance(metrics["sma10w20wCrossAge"], int)
    assert metrics["sma10w20wSpreadPct"] > 0


def test_weekly_closes_uses_last_available_session_of_week():
    idx = pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-17", "2026-08-18"])
    close = pd.Series([10.0, 11.0, 12.0, 20.0, 21.0], index=idx)
    weekly = weekly_closes(close)
    assert weekly.tolist() == [12.0, 21.0]


def test_short_history_stays_missing_instead_of_zero():
    metrics = compute_metrics(frame_from_prices([100.0 + i for i in range(15)]))
    assert all(value is None for value in metrics.values())


def test_hydrate_dataset_writes_flat_sortable_fields_and_coverage(tmp_path: Path):
    prices = [100 - i * 0.1 for i in range(70)] + [93 + i * 0.25 for i in range(130)]
    cache_path = tmp_path / "prices.pkl"
    dataset_path = tmp_path / "latest.json"
    with cache_path.open("wb") as fh:
        pickle.dump({"ABC": frame_from_prices(prices)}, fh)
    dataset_path.write_text(
        json.dumps({"generatedAt": "2026-08-19T00:00:00Z", "market": {}, "universe": [{"ticker": "ABC"}, {"ticker": "MISS"}]}),
        encoding="utf-8",
    )

    payload = hydrate_dataset(dataset_path, cache_path)
    abc = payload["universe"][0]
    miss = payload["universe"][1]

    assert payload["maCrossModel"] == MODEL
    assert abc["ema10d20dSpreadPct"] is not None
    assert abc["sma10w20wSpreadPct"] is not None
    assert miss["ema10d20dSpreadPct"] is None
    assert miss["sma10w20wSpreadPct"] is None
    assert payload["market"]["maCrossCoverage"]["daily"]["coveragePct"] == 100.0
    assert payload["market"]["maCrossCoverage"]["weekly"]["coveragePct"] == 100.0
    assert payload["market"]["maCrossCoverage"]["missingHistory"] == 1
