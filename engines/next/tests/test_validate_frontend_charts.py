import json

import pytest

from validate_frontend_charts import shard_for, validate


def write_site(tmp_path, tickers, covered):
    data = tmp_path / "data"
    charts = data / "charts"
    charts.mkdir(parents=True)
    payload = {
        "universe": [{"ticker": ticker} for ticker in tickers],
        "chartShards": {ticker: shard_for(ticker) for ticker in tickers},
    }
    (data / "core.json").write_text(json.dumps(payload), encoding="utf-8")
    by_shard = {}
    for ticker in covered:
        by_shard.setdefault(shard_for(ticker), {})[ticker] = [{"date": "2026-08-21", "close": 10}]
    for shard, rows in by_shard.items():
        (charts / shard).write_text(json.dumps(rows), encoding="utf-8")


def test_strict_chart_gate_accepts_coverage_at_threshold(tmp_path):
    tickers = [f"T{i:03d}" for i in range(20)]
    write_site(tmp_path, tickers, tickers[:19])
    assert validate(tmp_path, strict=True, minimum_coverage=0.95) == pytest.approx(0.95)


def test_strict_chart_gate_rejects_coverage_below_threshold(tmp_path):
    tickers = [f"T{i:03d}" for i in range(20)]
    write_site(tmp_path, tickers, tickers[:18])
    with pytest.raises(SystemExit, match="Chart publication gate failed"):
        validate(tmp_path, strict=True, minimum_coverage=0.95)


def test_diagnostic_mode_remains_non_blocking(tmp_path):
    tickers = ["AAA", "BBB"]
    write_site(tmp_path, tickers, ["AAA"])
    assert validate(tmp_path, strict=False, minimum_coverage=0.95) == pytest.approx(0.5)
