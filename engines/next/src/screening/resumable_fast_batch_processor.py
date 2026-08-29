"""Identity-safe resume and provider hardening for the fast Next scanner.

The vendored fast processor remains untouched. This wrapper adds two production
boundaries around it:
- checkpoints are reused only when NYSE session, universe and source identity match;
- transient batched OHLCV failures are retried with bounded exponential backoff.

Scoring and signal rules are unchanged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yfinance as yf

from .fast_batch_processor import FastOptimizedBatchProcessor

logger = logging.getLogger(__name__)

PROGRESS_SCHEMA = "stockscout-next-progress/v1"
METRICS_SCHEMA = "stockscout-next-metrics/v1"
SOURCE_HASH_ENV = "STOCKSCOUT_PROGRESS_SOURCE_HASH"
SESSION_ENV = "STOCKSCOUT_EXPECTED_SESSION"
_PROVIDER_MAX_ATTEMPTS = 3
_PROVIDER_BACKOFF_BASE_SECONDS = 0.75
_PROVIDER_BACKOFF_JITTER_SECONDS = 0.25
_RETRYABLE_PROVIDER_CLASSES = {
    "timeout",
    "rate_limit",
    "server_5xx",
    "network",
    "empty_payload",
}


class ResumableFastOptimizedBatchProcessor(FastOptimizedBatchProcessor):
    """Fast processor with strict checkpoint identity and bounded provider retry."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_identity: dict[str, Any] | None = None
        self._progress_universe: set[str] = set()
        self.resume_checkpoint_used = False
        self.resume_checkpoint_reason = "not-requested"
        self.metrics_file = Path(self.results_dir).parent / "logs" / "next_scan_metrics.json"
        self.provider_retry_count = 0
        self.provider_rate_limit_count = 0
        self.provider_timeout_count = 0
        self.provider_error_types: dict[str, int] = {}

    @staticmethod
    def _identity_digest(identity: dict[str, Any]) -> str:
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_progress_identity(
        self,
        tickers: List[str],
        *,
        min_price: float,
        max_price: float,
        min_volume: int,
    ) -> dict[str, Any]:
        normalized = sorted(
            {str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()}
        )
        universe_hash = hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()
        return {
            "schema": PROGRESS_SCHEMA,
            "sessionDate": os.getenv(SESSION_ENV, "").strip(),
            "sourceHash": os.getenv(SOURCE_HASH_ENV, "").strip(),
            "universeSha256": universe_hash,
            "universeCount": len(normalized),
            "minPrice": float(min_price),
            "maxPrice": float(max_price),
            "minVolume": int(min_volume),
            "gitStorage": bool(self.use_git_storage),
            "forceFullRefresh": os.getenv("FORCE_FULL_REFRESH", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            "processor": "resumable-fast-v1",
        }

    @staticmethod
    def _classify_provider_error(exc: Exception) -> str:
        message = str(exc).lower()
        if isinstance(exc, TimeoutError) or any(
            token in message for token in ("timed out", "timeout", "read timeout")
        ):
            return "timeout"
        if any(token in message for token in ("429", "rate limit", "too many requests")):
            return "rate_limit"
        if any(
            token in message
            for token in ("500", "502", "503", "504", "server error", "bad gateway")
        ):
            return "server_5xx"
        if any(
            token in message
            for token in (
                "connection",
                "temporarily unavailable",
                "remote disconnected",
                "connection reset",
                "ssl error",
                "dns",
            )
        ):
            return "network"
        if "empty provider payload" in message:
            return "empty_payload"
        return "provider_error"

    def _record_provider_error(self, error_class: str) -> None:
        self.provider_error_types[error_class] = self.provider_error_types.get(error_class, 0) + 1
        if error_class == "rate_limit":
            self.provider_rate_limit_count += 1
        if error_class == "timeout":
            self.provider_timeout_count += 1

    def _download_chunk(self, chunk: List[str], threads: bool = True) -> Dict[str, Any]:
        """Fetch one OHLCV chunk with bounded retries around the existing 20s timeout.

        Parent-level missing/stale-symbol retries still run after this boundary.
        This method only retries a whole chunk when the provider call itself
        fails or returns no usable payload.
        """
        if not chunk:
            return {}

        for attempt in range(_PROVIDER_MAX_ATTEMPTS):
            try:
                raw = yf.download(
                    chunk,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=True,
                    progress=False,
                    threads=threads,
                    timeout=20,
                    **self._history_window(),
                )
                if raw is None or raw.empty:
                    raise RuntimeError("empty provider payload")

                out: Dict[str, Any] = {}
                for ticker in chunk:
                    frame = self._extract_ticker_frame(raw, ticker, len(chunk))
                    if not frame.empty:
                        out[ticker] = frame
                return out
            except Exception as exc:
                error_class = self._classify_provider_error(exc)
                self._record_provider_error(error_class)
                retryable = error_class in _RETRYABLE_PROVIDER_CLASSES
                final_attempt = attempt + 1 >= _PROVIDER_MAX_ATTEMPTS
                logger.warning(
                    "Next OHLCV provider failure: class=%s attempt=%d/%d symbols=%d error=%s",
                    error_class,
                    attempt + 1,
                    _PROVIDER_MAX_ATTEMPTS,
                    len(chunk),
                    exc,
                )
                if not retryable or final_attempt:
                    return {}
                self.provider_retry_count += 1
                delay = (
                    _PROVIDER_BACKOFF_BASE_SECONDS * (2**attempt)
                    + random.uniform(0.0, _PROVIDER_BACKOFF_JITTER_SECONDS)
                )
                logger.info(
                    "Retrying Next OHLCV provider chunk in %.2fs (class=%s)",
                    delay,
                    error_class,
                )
                time.sleep(delay)
        return {}

    def _rate_limit_count(self) -> int:
        total = self.provider_rate_limit_count
        for error_type, count in self.error_types.items():
            example = self.error_examples.get(error_type)
            message = ""
            if isinstance(example, (list, tuple)) and len(example) > 1:
                message = str(example[1]).lower()
            if any(token in message for token in ("429", "rate limit", "too many requests")):
                total += int(count)
        return total

    @staticmethod
    def _top_counts(values: Dict, limit: int = 5) -> list[dict[str, Any]]:
        pairs: list[tuple[str, int]] = []
        for name, raw_count in values.items():
            try:
                pairs.append((str(name), int(raw_count)))
            except (TypeError, ValueError):
                continue
        pairs.sort(key=lambda item: (-item[1], item[0]))
        return [{"name": name, "count": count} for name, count in pairs[:limit]]

    def _combined_error_types(self) -> dict[str, int]:
        combined = {str(name): int(count) for name, count in self.error_types.items()}
        for name, count in self.provider_error_types.items():
            key = f"provider:{name}"
            combined[key] = combined.get(key, 0) + int(count)
        return combined

    def _write_progress_metrics(
        self,
        results: List[Dict],
        *,
        status: str | None = None,
        extra: Dict | None = None,
    ) -> None:
        identity = self.progress_identity or {}
        universe_count = int(identity.get("universeCount") or len(self._progress_universe))
        processed_count = len(self.processed_tickers)
        metrics: dict[str, Any] = {
            "schema": METRICS_SCHEMA,
            "mode": "next",
            "status": status or ("complete" if processed_count >= universe_count else "partial"),
            "sessionDate": str(identity.get("sessionDate") or ""),
            "universeCount": universe_count,
            "processedCount": processed_count,
            "successCount": len(results),
            "skippedCount": int(self.filtered_count),
            "failedCount": int(self.error_count),
            "coveragePct": round(100.0 * processed_count / max(1, universe_count), 2),
            "analysisCoveragePct": round(100.0 * len(results) / max(1, universe_count), 2),
            "requestCount": int(self.total_requests),
            "errorRatePct": round(
                100.0 * self.error_count / max(1, self.total_requests), 2
            ),
            "retryCount": int(self.provider_retry_count),
            "rateLimitCount": self._rate_limit_count(),
            "providerTimeoutCount": int(self.provider_timeout_count),
            "resumeCheckpointUsed": bool(self.resume_checkpoint_used),
            "resumeCheckpointReason": self.resume_checkpoint_reason,
            "progressIdentitySha256": self._identity_digest(identity),
            "topErrorClasses": self._top_counts(self._combined_error_types()),
            "topFilterReasons": self._top_counts(self.filter_reasons),
            "generatedAt": datetime.now().isoformat(),
        }
        if extra:
            if extra.get("processing_time_seconds") is not None:
                metrics["processingTimeSeconds"] = round(
                    float(extra["processing_time_seconds"]), 1
                )
            if extra.get("price_prefetch_seconds") is not None:
                metrics["pricePrefetchSeconds"] = round(
                    float(extra["price_prefetch_seconds"]), 1
                )
            if extra.get("price_prefetch_missing") is not None:
                metrics["pricePrefetchMissing"] = int(extra["price_prefetch_missing"])

        self.metrics_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(f"{self.metrics_file}.tmp")
        try:
            temporary.write_text(
                json.dumps(metrics, sort_keys=True, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.metrics_file)
        except Exception as exc:
            logger.warning("Unable to persist Next scan metrics: %s", exc)
            temporary.unlink(missing_ok=True)

    def load_progress(self) -> Dict | None:
        """Load only a checkpoint that exactly matches the current scan identity."""
        if not self.progress_file.exists():
            self.resume_checkpoint_reason = "missing"
            return None

        try:
            with self.progress_file.open("rb") as handle:
                progress = pickle.load(handle)
        except Exception as exc:
            self.resume_checkpoint_reason = "unreadable"
            logger.warning("Ignoring unreadable Next resume checkpoint: %s", exc)
            return None

        expected = self.progress_identity
        actual = progress.get("identity") if isinstance(progress, dict) else None
        if expected is None or actual != expected:
            self.resume_checkpoint_reason = "identity-mismatch"
            expected_hash = self._identity_digest(expected or {})
            actual_hash = self._identity_digest(actual) if isinstance(actual, dict) else "missing"
            logger.warning(
                "Ignoring incompatible Next resume checkpoint (expected=%s actual=%s)",
                expected_hash[:12],
                actual_hash[:12],
            )
            return None

        processed = progress.get("processed") or []
        results = progress.get("results") or []
        if not isinstance(processed, list) or not set(processed).issubset(
            self._progress_universe
        ):
            self.resume_checkpoint_reason = "invalid-processed-set"
            logger.warning("Ignoring Next resume checkpoint with invalid processed ticker set")
            return None
        if not isinstance(results, list):
            self.resume_checkpoint_reason = "invalid-results"
            logger.warning("Ignoring Next resume checkpoint with invalid result payload")
            return None
        for row in results:
            if (
                not isinstance(row, dict)
                or str(row.get("ticker") or "").upper() not in self._progress_universe
            ):
                self.resume_checkpoint_reason = "invalid-results"
                logger.warning("Ignoring Next resume checkpoint with foreign result ticker")
                return None

        counters = progress.get("counters") or {}
        self.error_count = int(counters.get("error_count") or 0)
        self.filtered_count = int(counters.get("filtered_count") or 0)
        self.total_requests = int(counters.get("total_requests") or 0)
        self.error_types = dict(counters.get("error_types") or {})
        self.error_examples = dict(counters.get("error_examples") or {})
        self.filter_reasons = dict(counters.get("filter_reasons") or {})
        self.provider_retry_count = int(counters.get("provider_retry_count") or 0)
        self.provider_rate_limit_count = int(counters.get("provider_rate_limit_count") or 0)
        self.provider_timeout_count = int(counters.get("provider_timeout_count") or 0)
        self.provider_error_types = dict(counters.get("provider_error_types") or {})
        self.resume_checkpoint_used = True
        self.resume_checkpoint_reason = "accepted"
        logger.info(
            "Accepted identity-safe Next resume checkpoint: %d/%d tickers already processed",
            len(processed),
            len(self._progress_universe),
        )
        return progress

    def save_progress(self, tickers_list: List[str], results: List[Dict]):
        """Atomically persist scanner progress plus immutable resume identity."""
        if self.progress_identity is None:
            logger.warning("Skipping progress save because Next resume identity is not initialized")
            return

        progress = {
            "schema": PROGRESS_SCHEMA,
            "identity": self.progress_identity,
            "identity_sha256": self._identity_digest(self.progress_identity),
            "timestamp": datetime.now().isoformat(),
            "total_tickers": len(tickers_list),
            "processed": sorted(self.processed_tickers),
            "results": results,
            "batch_size": self.batch_size,
            "counters": {
                "error_count": self.error_count,
                "filtered_count": self.filtered_count,
                "total_requests": self.total_requests,
                "error_types": self.error_types,
                "error_examples": self.error_examples,
                "filter_reasons": self.filter_reasons,
                "provider_retry_count": self.provider_retry_count,
                "provider_rate_limit_count": self.provider_rate_limit_count,
                "provider_timeout_count": self.provider_timeout_count,
                "provider_error_types": self.provider_error_types,
            },
        }

        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(f"{self.progress_file}.tmp")
        try:
            with temporary.open("wb") as handle:
                pickle.dump(progress, handle, protocol=pickle.HIGHEST_PROTOCOL)
            temporary.replace(self.progress_file)
        except Exception as exc:
            logger.error("Error saving identity-safe Next progress: %s", exc)
            temporary.unlink(missing_ok=True)
        self._write_progress_metrics(results)

    def process_batch_parallel(self, tickers: List[str], *args, **kwargs) -> Dict:
        resume_requested = bool(kwargs.get("resume", True))
        min_price = float(kwargs.get("min_price", 5.0))
        max_price = float(kwargs.get("max_price", 10000.0))
        min_volume = int(kwargs.get("min_volume", 100000))
        self._progress_universe = {str(ticker).strip().upper() for ticker in tickers}
        self.progress_identity = self._build_progress_identity(
            tickers,
            min_price=min_price,
            max_price=max_price,
            min_volume=min_volume,
        )

        # Production EOD resume must prove the source/config hash. Interactive
        # scans without an immutable session may still use the historical local
        # behavior, but a pinned EOD session never accepts an unversioned pickle.
        if (
            resume_requested
            and self.progress_identity["sessionDate"]
            and not self.progress_identity["sourceHash"]
        ):
            logger.warning("Next resume disabled: %s is missing", SOURCE_HASH_ENV)
            kwargs["resume"] = False
            self.resume_checkpoint_reason = "missing-source-hash"

        result = super().process_batch_parallel(tickers, *args, **kwargs)
        if "error" not in result:
            # The legacy base processor rebuilds phase_results only for tickers
            # processed in the current attempt. Reconstruct it from the complete
            # resumed analysis set so breadth and signal gating are identical to
            # an uninterrupted run.
            phase_results: list[dict[str, Any]] = []
            for analysis in result.get("analyses", []):
                try:
                    phase = int((analysis.get("phase_info") or {}).get("phase"))
                    ticker = str(analysis.get("ticker") or "").upper()
                except (TypeError, ValueError):
                    continue
                if ticker and phase in {1, 2, 3, 4}:
                    phase_results.append({"ticker": ticker, "phase": phase})
            result["phase_results"] = phase_results
            result["resume_checkpoint_used"] = self.resume_checkpoint_used
            result["resume_checkpoint_reason"] = self.resume_checkpoint_reason
            result["progress_identity_sha256"] = self._identity_digest(
                self.progress_identity
            )
            result["provider_retry_count"] = self.provider_retry_count
            result["provider_rate_limit_count"] = self.provider_rate_limit_count
            result["provider_timeout_count"] = self.provider_timeout_count
            self._write_progress_metrics(
                result.get("analyses", []), status="complete", extra=result
            )
        else:
            self._write_progress_metrics(
                self.current_results, status="failed", extra=result
            )
        return result
