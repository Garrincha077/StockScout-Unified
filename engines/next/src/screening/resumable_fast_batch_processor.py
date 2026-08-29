"""Identity-safe resume support for the fast Next full-market scanner.

The legacy optimized processor already checkpoints every batch, but its pickle did
not prove which NYSE session, universe, or source/config produced it.  This
wrapper keeps the scoring path unchanged while making resume fail-closed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .fast_batch_processor import FastOptimizedBatchProcessor

logger = logging.getLogger(__name__)

PROGRESS_SCHEMA = "stockscout-next-progress/v1"
SOURCE_HASH_ENV = "STOCKSCOUT_PROGRESS_SOURCE_HASH"
SESSION_ENV = "STOCKSCOUT_EXPECTED_SESSION"


class ResumableFastOptimizedBatchProcessor(FastOptimizedBatchProcessor):
    """Fast processor with strict checkpoint identity validation."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_identity: dict[str, Any] | None = None
        self._progress_universe: set[str] = set()
        self.resume_checkpoint_used = False
        self.resume_checkpoint_reason = "not-requested"

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
        normalized = sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()})
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
        if not isinstance(processed, list) or not set(processed).issubset(self._progress_universe):
            self.resume_checkpoint_reason = "invalid-processed-set"
            logger.warning("Ignoring Next resume checkpoint with invalid processed ticker set")
            return None
        if not isinstance(results, list):
            self.resume_checkpoint_reason = "invalid-results"
            logger.warning("Ignoring Next resume checkpoint with invalid result payload")
            return None
        for row in results:
            if not isinstance(row, dict) or str(row.get("ticker") or "").upper() not in self._progress_universe:
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
        if resume_requested and self.progress_identity["sessionDate"] and not self.progress_identity["sourceHash"]:
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
            result["progress_identity_sha256"] = self._identity_digest(self.progress_identity)
        return result
