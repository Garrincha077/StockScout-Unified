import json
import pickle

import pandas as pd

from src.screening.fast_batch_processor import FastOptimizedBatchProcessor
from src.screening import resumable_fast_batch_processor as resumable_module
from src.screening.resumable_fast_batch_processor import ResumableFastOptimizedBatchProcessor


def _identity(processor, tickers):
    processor._progress_universe = set(tickers)
    processor.progress_identity = processor._build_progress_identity(
        tickers,
        min_price=5.0,
        max_price=10000.0,
        min_volume=100000,
    )


def test_resume_checkpoint_requires_exact_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-08-28")
    monkeypatch.setenv("STOCKSCOUT_PROGRESS_SOURCE_HASH", "source-a")
    processor = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))
    _identity(processor, ["AAA", "BBB"])
    processor.processed_tickers = {"AAA"}
    processor.total_requests = 1
    processor.provider_retry_count = 2
    processor.provider_timeout_count = 1
    processor.provider_error_types = {"timeout": 1}
    processor.save_progress(
        ["AAA", "BBB"],
        [{"ticker": "AAA", "phase_info": {"phase": 2}}],
    )

    same = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))
    _identity(same, ["AAA", "BBB"])
    progress = same.load_progress()
    assert progress is not None
    assert same.resume_checkpoint_used is True
    assert progress["processed"] == ["AAA"]
    assert same.provider_retry_count == 2
    assert same.provider_timeout_count == 1
    assert same.provider_error_types == {"timeout": 1}

    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-08-27")
    different_session = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))
    _identity(different_session, ["AAA", "BBB"])
    assert different_session.load_progress() is None
    assert different_session.resume_checkpoint_reason == "identity-mismatch"


def test_resume_checkpoint_rejects_foreign_processed_ticker(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-08-28")
    monkeypatch.setenv("STOCKSCOUT_PROGRESS_SOURCE_HASH", "source-a")
    processor = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))
    _identity(processor, ["AAA"])
    payload = {
        "identity": processor.progress_identity,
        "processed": ["ZZZ"],
        "results": [],
    }
    with processor.progress_file.open("wb") as handle:
        pickle.dump(payload, handle)

    assert processor.load_progress() is None
    assert processor.resume_checkpoint_reason == "invalid-processed-set"


def test_resumed_phase_results_are_rebuilt_from_complete_analysis_set(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-08-28")
    monkeypatch.setenv("STOCKSCOUT_PROGRESS_SOURCE_HASH", "source-a")

    def fake_process(self, tickers, *args, **kwargs):
        return {
            "analyses": [
                {"ticker": "AAA", "phase_info": {"phase": 1}},
                {"ticker": "BBB", "phase_info": {"phase": 4}},
            ],
            "phase_results": [{"ticker": "BBB", "phase": 4}],
        }

    monkeypatch.setattr(FastOptimizedBatchProcessor, "process_batch_parallel", fake_process)
    processor = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))
    result = processor.process_batch_parallel(
        ["AAA", "BBB"],
        resume=True,
        min_price=5.0,
        min_volume=100000,
    )

    assert result["phase_results"] == [
        {"ticker": "AAA", "phase": 1},
        {"ticker": "BBB", "phase": 4},
    ]
    assert result["progress_identity_sha256"]


def test_progress_save_emits_compact_json_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-08-28")
    monkeypatch.setenv("STOCKSCOUT_PROGRESS_SOURCE_HASH", "source-a")
    processor = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path / "batch_results"))
    _identity(processor, ["AAA", "BBB"])
    processor.processed_tickers = {"AAA"}
    processor.filtered_count = 1
    processor.total_requests = 1
    processor.error_count = 1
    processor.error_types = {"HTTPError": 1}
    processor.error_examples = {"HTTPError": ("AAA", "429 Too Many Requests")}
    processor.filter_reasons = {"low_volume": 1}
    processor.provider_retry_count = 2
    processor.provider_timeout_count = 1

    processor.save_progress(["AAA", "BBB"], [])

    metrics = json.loads(processor.metrics_file.read_text(encoding="utf-8"))
    assert metrics["sessionDate"] == "2026-08-28"
    assert metrics["universeCount"] == 2
    assert metrics["processedCount"] == 1
    assert metrics["coveragePct"] == 50.0
    assert metrics["retryCount"] == 2
    assert metrics["rateLimitCount"] == 1
    assert metrics["providerTimeoutCount"] == 1
    assert metrics["topErrorClasses"] == [{"name": "HTTPError", "count": 1}]
    assert metrics["topFilterReasons"] == [{"name": "low_volume", "count": 1}]


def test_ohlcv_chunk_retries_timeouts_with_bounded_backoff(tmp_path, monkeypatch):
    calls = 0

    def fake_download(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("provider timed out")
        index = pd.date_range("2026-08-27", periods=2, freq="D")
        return pd.DataFrame(
            {
                "Open": [10.0, 10.5],
                "High": [11.0, 11.5],
                "Low": [9.5, 10.0],
                "Close": [10.5, 11.0],
                "Volume": [1000, 1200],
            },
            index=index,
        )

    monkeypatch.setattr(resumable_module.yf, "download", fake_download)
    monkeypatch.setattr(resumable_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(resumable_module.random, "uniform", lambda *_: 0.0)
    processor = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))

    result = processor._download_chunk(["AAA"], threads=False)

    assert calls == 3
    assert "AAA" in result
    assert processor.provider_retry_count == 2
    assert processor.provider_timeout_count == 2
    assert processor.provider_error_types == {"timeout": 2}


def test_ohlcv_rate_limit_retries_stop_after_three_attempts(tmp_path, monkeypatch):
    calls = 0

    def always_rate_limited(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("429 Too Many Requests")

    monkeypatch.setattr(resumable_module.yf, "download", always_rate_limited)
    monkeypatch.setattr(resumable_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(resumable_module.random, "uniform", lambda *_: 0.0)
    processor = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))

    result = processor._download_chunk(["AAA"], threads=False)

    assert result == {}
    assert calls == 3
    assert processor.provider_retry_count == 2
    assert processor.provider_rate_limit_count == 3
    assert processor.provider_error_types == {"rate_limit": 3}


def test_partial_terminal_bar_is_repaired_without_losing_history(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-10-01")
    index = pd.date_range("2026-09-29", periods=3, freq="D")
    wide = pd.DataFrame({"Open": [10, 11, 12], "High": [12, 13, 14], "Low": [9, 10, 11], "Close": [11, 12, float("nan")], "Volume": [1000, 1100, 1200]}, index=index)
    exact = wide.tail(1).copy()
    exact["Close"] = 13.0
    calls = []

    def download(*args, **kwargs):
        calls.append(kwargs)
        return exact if kwargs.get("start") == "2026-10-01" else wide

    monkeypatch.setattr(resumable_module.yf, "download", download)
    processor = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))
    result = processor._download_chunk(["SPY"])
    assert len(calls) == 2
    assert calls[1]["end"] == "2026-10-02"
    pd.testing.assert_frame_equal(result["SPY"].iloc[:-1], processor._normalize_frame(wide).iloc[:2])
    assert len(result["SPY"]) == 3
    assert result["SPY"].iloc[-1]["Close"] == 13.0
    assert processor._has_expected_session(result["SPY"])


def test_wrong_day_exact_response_cannot_make_history_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-10-01")
    stale = pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11], "Volume": [1000]}, index=pd.DatetimeIndex(["2026-09-30"]))
    monkeypatch.setattr(resumable_module.yf, "download", lambda *args, **kwargs: stale)
    processor = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))
    result = processor._download_chunk(["SPY"])
    assert not processor._has_expected_session(result["SPY"])
    pd.testing.assert_frame_equal(result["SPY"], stale)


def test_interactive_scans_keep_existing_history_behavior(tmp_path, monkeypatch):
    monkeypatch.delenv("STOCKSCOUT_EXPECTED_SESSION", raising=False)
    frame = pd.DataFrame({"Close": [10.0]}, index=pd.DatetimeIndex(["2026-09-30"]))
    monkeypatch.setattr(resumable_module.yf, "download", lambda *args, **kwargs: frame)
    processor = ResumableFastOptimizedBatchProcessor(results_dir=str(tmp_path))
    result = processor._download_chunk(["AAA"])
    pd.testing.assert_frame_equal(result["AAA"], frame)
