from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from stock_scout.pipeline.orchestrator import PipelineRunner


def test_portable_runner_resamples_split_only_daily_without_weekly_provider_call() -> None:
    runner = object.__new__(PipelineRunner)
    provider = SimpleNamespace(name="yfinance")
    runner.settings = SimpleNamespace(
        cache=SimpleNamespace(daily_history_years=10, weekly_history_years=10),
        marketdata=SimpleNamespace(scan_provider_fallback=True),
        stage_analysis=SimpleNamespace(weekly_sma_period=30, slope_lookback_weeks=5),
    )
    runner._active_primary = provider
    runner.fallback = None
    runner.tertiary = None
    runner.deep_history = None
    runner._local_daily_weekly = lambda *_args: (pd.DataFrame(), pd.DataFrame())
    calls: list[str] = []
    index = pd.date_range("2016-08-22", "2026-08-21", freq="B")
    daily = pd.DataFrame(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100.0},
        index=index,
    )

    def ensure(_provider, _ticker, frequency, _start, _end):
        calls.append(frequency)
        return daily, False

    runner._ensure_history_for = ensure
    fetched_daily, weekly, provider_used, source = runner._fetch_with_fallback(
        "AAA", date(2016, 8, 22), date(2026, 8, 21)
    )
    assert fetched_daily is daily
    assert not weekly.empty
    assert calls == ["daily"]
    assert provider_used == "yfinance"
    assert source == "primary"
