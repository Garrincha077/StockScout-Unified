import json
import pickle

from src.screening.fast_batch_processor import FastOptimizedBatchProcessor
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

    processor.save_progress(["AAA", "BBB"], [])

    metrics = json.loads(processor.metrics_file.read_text(encoding="utf-8"))
    assert metrics["sessionDate"] == "2026-08-28"
    assert metrics["universeCount"] == 2
    assert metrics["processedCount"] == 1
    assert metrics["coveragePct"] == 50.0
    assert metrics["rateLimitCount"] == 1
    assert metrics["topErrorClasses"] == [{"name": "HTTPError", "count": 1}]
    assert metrics["topFilterReasons"] == [{"name": "low_volume", "count": 1}]
