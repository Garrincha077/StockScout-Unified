from pathlib import Path


def test_unified_next_wrapper_composes_resume_and_fundamentals_hardening() -> None:
    wrapper = Path("engines/next/run_resumable_fast_scan.py").read_text(encoding="utf-8")
    assert "ResilientFundamentalsResumableFastOptimizedBatchProcessor" in wrapper
    assert "run_fast_scan.run_optimized_scan.OptimizedBatchProcessor" in wrapper


def test_fundamentals_hardening_does_not_replace_pinned_vendored_entrypoint() -> None:
    pinned = Path("engines/next/run_fast_scan.py").read_text(encoding="utf-8")
    assert "FastOptimizedBatchProcessor" in pinned
    assert "ResilientGitStorageFetcher" not in pinned
