import json

import pandas as pd

from src.data import fundamentals_fetcher as legacy_fundamentals
from src.data import resilient_fundamentals_fetcher as resilient_module
from src.data.resilient_fundamentals_fetcher import ResilientGitStorageFetcher
from src.screening.resilient_fundamentals_batch_processor import (
    ResilientFundamentalsResumableFastOptimizedBatchProcessor,
)


class _FakeTicker:
    def __init__(self):
        columns = pd.to_datetime(
            ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"]
        )
        self.quarterly_financials = pd.DataFrame(
            [
                [100.0, 110.0, 120.0, 130.0, 150.0],
                [1.0, 1.1, 1.2, 1.3, 1.6],
                [40.0, 45.0, 50.0, 55.0, 66.0],
                [20.0, 22.0, 24.0, 26.0, 31.0],
            ],
            index=["Total Revenue", "Diluted EPS", "Gross Profit", "Operating Income"],
            columns=columns,
        )
        self.quarterly_balance_sheet = pd.DataFrame(
            [[20.0, 21.0, 22.0, 23.0, 24.0]],
            index=["Inventory"],
            columns=columns,
        )
        self.quarterly_cashflow = pd.DataFrame(
            [[10.0, 11.0, 12.0, 13.0, 14.0]],
            index=["Operating Cash Flow"],
            columns=columns,
        )


def test_strict_provider_keeps_legacy_metric_semantics(monkeypatch):
    monkeypatch.setattr(legacy_fundamentals.yf, "Ticker", lambda ticker: _FakeTicker())
    legacy = legacy_fundamentals.fetch_quarterly_financials("TEST")
    strict = resilient_module.fetch_quarterly_financials_strict("TEST")
    legacy.pop("fetch_date")
    strict.pop("fetch_date")
    assert strict == legacy


def test_transient_timeout_retries_then_succeeds(tmp_path, monkeypatch):
    outcomes = [TimeoutError("provider timed out"), {"ticker": "AAA", "revenue_yoy_change": 10.0}]
    calls = []

    def fake_fetch(ticker):
        calls.append(ticker)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(resilient_module, "fetch_quarterly_financials_strict", fake_fetch)
    sleeps = []
    fetcher = ResilientGitStorageFetcher(
        fundamentals_dir=str(tmp_path),
        retry_base_seconds=0.1,
        retry_jitter_seconds=0.0,
        sleep_fn=sleeps.append,
        random_fn=lambda: 0.0,
    )

    result = fetcher.fetch_fundamentals_smart("AAA")

    assert result["ticker"] == "AAA"
    assert calls == ["AAA", "AAA"]
    assert sleeps == [0.1]
    stats = fetcher.provider_stats()
    assert stats["attemptCount"] == 2
    assert stats["retryCount"] == 1
    assert stats["timeoutCount"] == 1
    assert stats["failedTickerCount"] == 0


def test_rate_limit_exhaustion_is_bounded_and_counted(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(ticker):
        calls.append(ticker)
        raise RuntimeError("HTTP 429 Too Many Requests")

    monkeypatch.setattr(resilient_module, "fetch_quarterly_financials_strict", fake_fetch)
    sleeps = []
    fetcher = ResilientGitStorageFetcher(
        fundamentals_dir=str(tmp_path),
        max_provider_attempts=3,
        retry_base_seconds=0.0,
        retry_jitter_seconds=0.0,
        sleep_fn=sleeps.append,
        random_fn=lambda: 0.0,
    )

    assert fetcher.fetch_fundamentals_smart("RATE") == {}
    assert calls == ["RATE", "RATE", "RATE"]
    assert len(sleeps) == 2
    stats = fetcher.provider_stats()
    assert stats["retryCount"] == 2
    assert stats["rateLimitCount"] == 3
    assert stats["failedTickerCount"] == 1
    assert stats["errorClasses"] == {"RuntimeError": 3}


def test_permanent_provider_error_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(ticker):
        calls.append(ticker)
        raise ValueError("invalid provider payload shape")

    monkeypatch.setattr(resilient_module, "fetch_quarterly_financials_strict", fake_fetch)
    fetcher = ResilientGitStorageFetcher(
        fundamentals_dir=str(tmp_path),
        retry_base_seconds=0.0,
        retry_jitter_seconds=0.0,
    )

    assert fetcher.fetch_fundamentals_smart("BAD") == {}
    assert calls == ["BAD"]
    stats = fetcher.provider_stats()
    assert stats["retryCount"] == 0
    assert stats["permanentErrorCount"] == 1
    assert stats["failedTickerCount"] == 1


def test_legitimate_empty_financials_are_not_retried(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(ticker):
        calls.append(ticker)
        return {}

    monkeypatch.setattr(resilient_module, "fetch_quarterly_financials_strict", fake_fetch)
    fetcher = ResilientGitStorageFetcher(fundamentals_dir=str(tmp_path))

    assert fetcher.fetch_fundamentals_smart("EMPTY") == {}
    assert calls == ["EMPTY"]
    stats = fetcher.provider_stats()
    assert stats["emptyDataCount"] == 1
    assert stats["retryCount"] == 0
    assert stats["providerErrorCount"] == 0


def _set_identity(processor):
    processor._progress_universe = {"AAA"}
    processor.progress_identity = processor._build_progress_identity(
        ["AAA"], min_price=5.0, max_price=10000.0, min_volume=100000
    )


def test_fundamentals_counters_survive_resume_and_enter_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-08-28")
    monkeypatch.setenv("STOCKSCOUT_PROGRESS_SOURCE_HASH", "source-a")
    results_dir = tmp_path / "batch_results"
    processor = ResilientFundamentalsResumableFastOptimizedBatchProcessor(
        results_dir=str(results_dir), use_git_storage=True
    )
    _set_identity(processor)
    processor.processed_tickers = {"AAA"}
    processor.git_fetcher.restore_provider_stats(
        {
            "attemptCount": 3,
            "retryCount": 2,
            "rateLimitCount": 1,
            "timeoutCount": 1,
            "failedTickerCount": 0,
            "errorClasses": {"TimeoutError": 1, "RuntimeError": 1},
        }
    )
    processor.save_progress(["AAA"], [{"ticker": "AAA", "phase_info": {"phase": 2}}])

    metrics = json.loads(processor.metrics_file.read_text(encoding="utf-8"))
    assert metrics["fundamentalsRetryCount"] == 2
    assert metrics["fundamentalsRateLimitCount"] == 1
    assert metrics["fundamentalsTimeoutCount"] == 1
    assert metrics["retryCount"] == 2

    resumed = ResilientFundamentalsResumableFastOptimizedBatchProcessor(
        results_dir=str(results_dir), use_git_storage=True
    )
    _set_identity(resumed)
    progress = resumed.load_progress()
    assert progress is not None
    restored = resumed.git_fetcher.provider_stats()
    assert restored["retryCount"] == 2
    assert restored["rateLimitCount"] == 1
    assert restored["timeoutCount"] == 1
