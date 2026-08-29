#!/usr/bin/env python3
"""Run the pinned fast scanner through Unified's hardened wrapper.

`run_fast_scan.py` remains byte-for-byte pinned to the vendored Next source.
This orchestration-only entrypoint swaps only the processor subclass used by
`run_optimized_scan.main()`, adding identity-safe resume and classified,
bounded fundamentals-provider retries.
"""
from __future__ import annotations

import run_fast_scan
from src.screening.resilient_fundamentals_batch_processor import (
    ResilientFundamentalsResumableFastOptimizedBatchProcessor,
)

run_fast_scan.run_optimized_scan.OptimizedBatchProcessor = (
    ResilientFundamentalsResumableFastOptimizedBatchProcessor
)


if __name__ == "__main__":
    run_fast_scan.run_optimized_scan.main()
