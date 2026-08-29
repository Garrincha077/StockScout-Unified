#!/usr/bin/env python3
"""Run the pinned fast scanner through the identity-safe resume wrapper.

`run_fast_scan.py` remains byte-for-byte pinned to the vendored Next source.
This orchestration-only entrypoint imports that wrapper, then swaps only the
processor subclass used by `run_optimized_scan.main()`.
"""
from __future__ import annotations

import run_fast_scan
from src.screening.resumable_fast_batch_processor import ResumableFastOptimizedBatchProcessor

run_fast_scan.run_optimized_scan.OptimizedBatchProcessor = ResumableFastOptimizedBatchProcessor


if __name__ == "__main__":
    run_fast_scan.run_optimized_scan.main()
