"""Compose identity-safe Next resume with resilient fundamentals fetching.

Production retries also reconcile each saved ticker against the freshly prefetched
adjusted OHLCV snapshot before a checkpoint is accepted.  This prevents a
same-session provider correction, split, dividend adjustment, or restatement from
mixing old analysis rows with newly generated charts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import pickle
from pathlib import Path
from typing import Dict, List

from src.data.resilient_fundamentals_fetcher import ResilientGitStorageFetcher

from .resumable_fast_batch_processor import ResumableFastOptimizedBatchProcessor

logger = logging.getLogger(__name__)

MARKET_DATA_FINGERPRINT_SCHEMA = "stockscout-next-market-data/v1"
MARKET_DATA_FINGERPRINT_BARS = 252


class ResilientFundamentalsResumableFastOptimizedBatchProcessor(
    ResumableFastOptimizedBatchProcessor
):
    """Resumable fast processor with provider and market-data retry hardening."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resume_market_invalidated_count = 0
        self.resume_market_invalidated_examples: list[str] = []
        self._market_fingerprint_cache: dict[str, str | None] = {}
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

    @staticmethod
    def _market_data_fingerprint(frame) -> str | None:
        """Hash the exact adjusted OHLCV window that drives technical scoring."""
        if frame is None or frame.empty or "Close" not in frame.columns:
            return None
        columns = [
            column
            for column in ("Open", "High", "Low", "Close", "Volume")
            if column in frame.columns
        ]
        window = frame.loc[:, columns].tail(MARKET_DATA_FINGERPRINT_BARS)
        if window.empty:
            return None
        encoded = window.to_csv(
            index=True,
            date_format="%Y-%m-%dT%H:%M:%S",
            float_format="%.10g",
            na_rep="",
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _fingerprint_for_ticker(self, ticker: str) -> str | None:
        normalized = str(ticker).strip().upper()
        if normalized not in self._market_fingerprint_cache:
            self._market_fingerprint_cache[normalized] = self._market_data_fingerprint(
                self.price_history.get(normalized)
            )
        return self._market_fingerprint_cache[normalized]

    def _market_fingerprints(self, tickers) -> dict[str, str | None]:
        return {
            ticker: self._fingerprint_for_ticker(ticker)
            for ticker in sorted({str(value).strip().upper() for value in tickers})
            if ticker
        }

    def _reset_loaded_analysis_counters(self) -> None:
        """Discard counters restored from a checkpoint that is rejected."""
        self.error_count = 0
        self.filtered_count = 0
        self.total_requests = 0
        self.error_types = {}
        self.error_examples = {}
        self.filter_reasons = {}

    def _reconcile_market_data(self, progress: Dict) -> Dict | None:
        """Invalidate only resumed tickers whose current adjusted OHLCV changed.

        FastOptimizedBatchProcessor always performs the fresh batch prefetch before
        the base class calls load_progress(), so production EOD retries reach this
        method with current-session price_history already populated.  Direct unit
        calls without a prefetch retain the historical checkpoint-only behaviour.
        """
        if not self.price_history:
            return progress

        schema = progress.get("market_data_fingerprint_schema")
        saved = progress.get("market_data_fingerprints")
        if schema != MARKET_DATA_FINGERPRINT_SCHEMA or not isinstance(saved, dict):
            self.resume_checkpoint_used = False
            self.resume_checkpoint_reason = "market-fingerprint-missing"
            self._reset_loaded_analysis_counters()
            logger.warning(
                "Ignoring Next resume checkpoint without current market-data fingerprints"
            )
            return None

        processed = [str(ticker).strip().upper() for ticker in progress.get("processed") or []]
        current = self._market_fingerprints(processed)
        invalidated = sorted(
            ticker
            for ticker in processed
            if not isinstance(saved.get(ticker), str)
            or not isinstance(current.get(ticker), str)
            or saved.get(ticker) != current.get(ticker)
        )
        if not invalidated:
            return progress

        invalidated_set = set(invalidated)
        reconciled = dict(progress)
        reconciled["processed"] = [
            ticker for ticker in processed if ticker not in invalidated_set
        ]
        reconciled["results"] = [
            row
            for row in progress.get("results") or []
            if str((row or {}).get("ticker") or "").strip().upper()
            not in invalidated_set
        ]
        self.resume_market_invalidated_count = len(invalidated)
        self.resume_market_invalidated_examples = invalidated[:10]
        self.resume_checkpoint_reason = "accepted-with-market-refresh"
        logger.warning(
            "Invalidated %d resumed tickers after adjusted OHLCV changed: %s",
            len(invalidated),
            ", ".join(invalidated[:10]),
        )
        return reconciled

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
            metrics["resumeMarketInvalidatedCount"] = int(
                self.resume_market_invalidated_count
            )
            metrics["resumeMarketInvalidatedExamples"] = list(
                self.resume_market_invalidated_examples
            )
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
            # In production the fast prefetch is complete before any progress
            # save. Persist one deterministic fingerprint for every processed
            # ticker so a later attempt can prove its analysis still belongs to
            # the exact adjusted OHLCV snapshot used by its charts.
            if self.price_history:
                progress["market_data_fingerprint_schema"] = (
                    MARKET_DATA_FINGERPRINT_SCHEMA
                )
                progress["market_data_fingerprints"] = self._market_fingerprints(
                    self.processed_tickers
                )
            temporary = Path(f"{self.progress_file}.tmp")
            with temporary.open("wb") as handle:
                pickle.dump(progress, handle, protocol=pickle.HIGHEST_PROTOCOL)
            temporary.replace(self.progress_file)
        except Exception:
            return

    def load_progress(self) -> Dict | None:
        # Preserve provider errors from the fresh prefetch; the base resume layer
        # restores prior-attempt counters and would otherwise overwrite them.
        current_retry_count = int(self.provider_retry_count)
        current_rate_limit_count = int(self.provider_rate_limit_count)
        current_timeout_count = int(self.provider_timeout_count)
        current_provider_errors = dict(self.provider_error_types)

        progress = super().load_progress()
        if progress is None:
            return None

        progress = self._reconcile_market_data(progress)
        if progress is None:
            self.provider_retry_count = current_retry_count
            self.provider_rate_limit_count = current_rate_limit_count
            self.provider_timeout_count = current_timeout_count
            self.provider_error_types = current_provider_errors
            return None

        self.provider_retry_count += current_retry_count
        self.provider_rate_limit_count += current_rate_limit_count
        self.provider_timeout_count += current_timeout_count
        for name, count in current_provider_errors.items():
            self.provider_error_types[name] = (
                self.provider_error_types.get(name, 0) + int(count)
            )

        provider_counters = progress.get("provider_counters") or {}
        if isinstance(self.git_fetcher, ResilientGitStorageFetcher):
            self.git_fetcher.restore_provider_stats(
                provider_counters.get("fundamentals")
                if isinstance(provider_counters, dict)
                else None
            )
        return progress
