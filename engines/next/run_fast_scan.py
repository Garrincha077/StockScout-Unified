#!/usr/bin/env python3
"""Run the existing full-market scanner with the batched-price processor.

The reporting/signal logic stays exactly in run_optimized_scan.py. Only the
processor class is swapped, which makes rollback trivial. When the workflow's
FORCE_FULL_REFRESH flag is true, the existing Git fundamentals fetcher is made to
refresh every Stage 1/2 fundamental input before scoring.
"""
from __future__ import annotations

import os

from src.data.git_storage_fetcher import GitStorageFetcher
from src.screening.resumable_fast_batch_processor import ResumableFastOptimizedBatchProcessor
import run_optimized_scan


def force_refresh_requested() -> bool:
    return os.getenv("FORCE_FULL_REFRESH", "false").strip().lower() in {"1", "true", "yes", "on"}


if force_refresh_requested():
    def _always_refresh(self, ticker, file_path):
        return True

    GitStorageFetcher._should_refresh_fundamental = _always_refresh
    print("FORCE_FULL_REFRESH=true: bypassing fundamentals cache freshness for scan scoring")

run_optimized_scan.OptimizedBatchProcessor = ResumableFastOptimizedBatchProcessor

if __name__ == "__main__":
    run_optimized_scan.main()
