"""Compose identity-safe Next resume with resilient fundamentals fetching."""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Dict, List

from src.data.resilient_fundamentals_fetcher import ResilientGitStorageFetcher

from .resumable_fast_batch_processor import ResumableFastOptimizedBatchProcessor


class ResilientFundamentalsResumableFastOptimizedBatchProcessor(
    ResumableFastOptimizedBatchProcessor
):
    """Resumable fast processor that also preserves fundamentals provider counters."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.use_git_storage:
            fundamentals_dir = (
                str(self.git_fetcher.fundamentals_dir)
                if self.git_fetcher is not None
                else "./data/fundamentals_cache"
            )
            self.git_fetcher = ResilientGitStorageFetcher(
                fundamentals_dir=fundamentals_dir
            )

    def _fundamentals_stats(self) -> dict[str, object]:
        if isinstance(self.git_fetcher, ResilientGitStorageFetcher):
            return self.git_fetcher.provider_stats()
        return {}

    @staticmethod
    def _merge_top_errors(metrics: dict, provider: dict[str, object]) -> None:
        merged: dict[str, int] = {}
        for item in metrics.get("topErrorClasses") or []:
            if not isinstance(item, dict):
                continue
            try:
                merged[str(item.get("name"))] = int(item.get("count") or 0)
            except (TypeError, ValueError):
                continue
        for name, raw_count in dict(provider.get("errorClasses") or {}).items():
            try:
                key = f"fundamentals:{name}"
                merged[key] = merged.get(key, 0) + int(raw_count)
            except (TypeError, ValueError):
                continue
        metrics["topErrorClasses"] = [
            {"name": name, "count": count}
            for name, count in sorted(
                merged.items(), key=lambda item: (-item[1], item[0])
            )[:5]
        ]

    def _write_progress_metrics(
        self,
        results: List[Dict],
        *,
        status: str | None = None,
        extra: Dict | None = None,
    ) -> None:
        super()._write_progress_metrics(results, status=status, extra=extra)
        if not self.metrics_file.exists():
            return
        try:
            metrics = json.loads(self.metrics_file.read_text(encoding="utf-8"))
            provider = self._fundamentals_stats()
            metrics["fundamentalsAttemptCount"] = provider.get("attemptCount", 0)
            metrics["fundamentalsRetryCount"] = provider.get("retryCount", 0)
            metrics["fundamentalsRateLimitCount"] = provider.get("rateLimitCount", 0)
            metrics["fundamentalsTimeoutCount"] = provider.get("timeoutCount", 0)
            metrics["fundamentalsServerErrorCount"] = provider.get("serverErrorCount", 0)
            metrics["fundamentalsNetworkErrorCount"] = provider.get("networkErrorCount", 0)
            metrics["fundamentalsPermanentErrorCount"] = provider.get("permanentErrorCount", 0)
            metrics["fundamentalsProviderErrorCount"] = provider.get("providerErrorCount", 0)
            metrics["fundamentalsFailedTickerCount"] = provider.get("failedTickerCount", 0)
            metrics["fundamentalsEmptyDataCount"] = provider.get("emptyDataCount", 0)
            metrics["fundamentalsErrorClasses"] = provider.get("errorClasses", {})
            # Keep the existing top-level counters meaningful for the whole Next
            # scanner without changing the legacy analysis error rate.
            metrics["retryCount"] = int(metrics.get("retryCount") or 0) + int(
                provider.get("retryCount") or 0
            )
            metrics["rateLimitCount"] = int(metrics.get("rateLimitCount") or 0) + int(
                provider.get("rateLimitCount") or 0
            )
            metrics["providerTimeoutCount"] = int(
                metrics.get("providerTimeoutCount") or 0
            ) + int(provider.get("timeoutCount") or 0)
            self._merge_top_errors(metrics, provider)
            temporary = Path(f"{self.metrics_file}.tmp")
            temporary.write_text(
                json.dumps(metrics, sort_keys=True, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.metrics_file)
        except Exception:
            # Observability must never mask the scanner's original result.
            return

    def save_progress(self, tickers_list: List[str], results: List[Dict]):
        super().save_progress(tickers_list, results)
        if not self.progress_file.exists():
            return
        try:
            with self.progress_file.open("rb") as handle:
                progress = pickle.load(handle)
            if not isinstance(progress, dict):
                return
            provider_counters = progress.get("provider_counters")
            if not isinstance(provider_counters, dict):
                provider_counters = {}
            provider_counters["fundamentals"] = self._fundamentals_stats()
            progress["provider_counters"] = provider_counters
            temporary = Path(f"{self.progress_file}.tmp")
            with temporary.open("wb") as handle:
                pickle.dump(progress, handle, protocol=pickle.HIGHEST_PROTOCOL)
            temporary.replace(self.progress_file)
        except Exception:
            return

    def load_progress(self) -> Dict | None:
        progress = super().load_progress()
        if progress is None:
            return None
        provider_counters = progress.get("provider_counters") or {}
        if isinstance(self.git_fetcher, ResilientGitStorageFetcher):
            self.git_fetcher.restore_provider_stats(
                provider_counters.get("fundamentals")
                if isinstance(provider_counters, dict)
                else None
            )
        return progress
