"""Fast full-market processor.

This is a drop-in replacement for OptimizedBatchProcessor that removes the
one-Yahoo-request-per-ticker bottleneck. It batch-downloads fresh 5Y OHLCV once,
reuses those frames for technical analysis, and persists the analyzed subset so
the frontend exporter can reuse the exact same market data without downloading it
again.

The original OptimizedBatchProcessor remains untouched as a fallback.
"""
from __future__ import annotations

import logging
import os
import pickle
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from .optimized_batch_processor import OptimizedBatchProcessor
from .phase_indicators import classify_phase, calculate_relative_strength, detect_vcp_pattern
from .group_proxies import ALL_PROXY_TICKERS
from ..data.fundamentals_fetcher import fetch_quarterly_financials, analyze_fundamentals_for_signal

logger = logging.getLogger(__name__)

EXPECTED_SESSION_ENV = "STOCKSCOUT_EXPECTED_SESSION"


def expected_market_session() -> date | None:
    """Return the EOD orchestrator's immutable market-session cutoff, if set.

    The normal interactive scanner deliberately follows Yahoo's latest bar. The
    Unified EOD workflow is different: its Bottom, Next and Ryan artifacts must
    all describe one already-selected NYSE session. Keeping this boundary here,
    at the only batched OHLCV ingress, prevents a long scan crossing midnight or
    a delayed Yahoo batch from silently mixing sessions.
    """
    value = os.getenv(EXPECTED_SESSION_ENV, "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {EXPECTED_SESSION_ENV}: {value!r}") from exc


class FastOptimizedBatchProcessor(OptimizedBatchProcessor):
    """OptimizedBatchProcessor using one batched 5Y market-data pass."""

    def __init__(self, *args, price_chunk_size: int = 100, **kwargs):
        super().__init__(*args, **kwargs)
        self.price_chunk_size = price_chunk_size
        self.price_history: Dict[str, pd.DataFrame] = {}
        self.price_history_file = Path(self.results_dir) / "price_history_5y.pkl"
        self.price_prefetch_seconds = 0.0
        self.price_prefetch_missing: List[str] = []
        self.expected_session = expected_market_session()
        self.price_session_stale: List[str] = []

    def _history_window(self) -> dict[str, str]:
        """Build a five-year Yahoo range capped at the selected EOD session.

        Yahoo treats ``end`` as exclusive, hence the one-day offset.  A dated
        range is intentionally used instead of ``period=5y`` when Unified has
        selected a session; the latter can advance while a scan is running.
        """
        if self.expected_session is None:
            return {"period": "5y"}
        return {
            "start": (self.expected_session - timedelta(days=5 * 366)).isoformat(),
            "end": (self.expected_session + timedelta(days=1)).isoformat(),
        }

    def _has_expected_session(self, frame: pd.DataFrame | None) -> bool:
        if self.expected_session is None or frame is None or frame.empty:
            return frame is not None and not frame.empty
        try:
            return pd.Timestamp(frame.index.max()).date() == self.expected_session
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty or "Close" not in frame.columns:
            return pd.DataFrame()
        frame = frame.copy().dropna(subset=["Close"])
        if frame.empty:
            return pd.DataFrame()
        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in frame.columns]
        frame = frame[keep]
        if not isinstance(frame.index, pd.DatetimeIndex):
            try:
                frame.index = pd.DatetimeIndex(frame.index)
            except Exception:
                return pd.DataFrame()
        if frame.index.tz is not None:
            frame.index = frame.index.tz_localize(None)
        return frame.sort_index()

    @staticmethod
    def _sanitize_quarterly_data(data: Dict) -> Dict:
        """Remove null optional metrics before legacy fundamental scoring.

        The fetch/cache layer intentionally represents unavailable metrics as
        ``None``. The legacy fundamental scorer uses ``dict.get(key, 0)`` and
        then performs numeric comparisons; when a key exists with a ``None``
        value the default is not used and Python raises ``TypeError``. Removing
        only null optional numeric keys preserves the semantic distinction for
        report rendering (a missing key still renders as N/A) while allowing the
        legacy scorer to use its established neutral defaults.
        """
        if not isinstance(data, dict) or not data:
            return {}

        cleaned = dict(data)
        optional_numeric = (
            "revenue_yoy_change",
            "revenue_qoq_change",
            "eps_yoy_change",
            "eps_qoq_change",
            "inventory_qoq_change",
            "inventory_to_sales_ratio",
            "gross_margin",
            "margin_change",
            "operating_margin",
        )
        for key in optional_numeric:
            if key not in cleaned:
                continue
            value = cleaned.get(key)
            is_missing = value is None
            if not is_missing:
                try:
                    is_missing = bool(pd.isna(value))
                except (TypeError, ValueError):
                    is_missing = False
            if is_missing:
                cleaned.pop(key, None)
        return cleaned

    @classmethod
    def _extract_ticker_frame(cls, download: pd.DataFrame, ticker: str, chunk_size: int) -> pd.DataFrame:
        if download is None or download.empty:
            return pd.DataFrame()
        frame = pd.DataFrame()
        if chunk_size == 1 and not isinstance(download.columns, pd.MultiIndex):
            frame = download
        elif isinstance(download.columns, pd.MultiIndex):
            level0 = download.columns.get_level_values(0)
            level1 = download.columns.get_level_values(1)
            if ticker in level0:
                frame = download[ticker]
            elif ticker in level1:
                frame = download.xs(ticker, axis=1, level=1)
        return cls._normalize_frame(frame)

    def _download_chunk(self, chunk: List[str], threads: bool = True) -> Dict[str, pd.DataFrame]:
        if not chunk:
            return {}
        try:
            # auto_adjust=True matches Ticker.history's adjusted-price behaviour and
            # avoids split artifacts in long-term technical indicators.
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
        except Exception as exc:
            logger.warning("Batch price download failed for %d symbols: %s", len(chunk), exc)
            return {}

        out: Dict[str, pd.DataFrame] = {}
        for ticker in chunk:
            frame = self._extract_ticker_frame(raw, ticker, len(chunk))
            if not frame.empty:
                out[ticker] = frame
        return out

    def prefetch_price_history(self, tickers: List[str]) -> None:
        start = time.time()
        unique = list(dict.fromkeys(["SPY", *ALL_PROXY_TICKERS, *[str(t).upper() for t in tickers]]))
        logger.info("=" * 60)
        logger.info("FAST PRICE PREFETCH: %s symbols, 5Y daily history", f"{len(unique):,}")
        logger.info("=" * 60)

        missing: List[str] = []
        for start_idx in range(0, len(unique), self.price_chunk_size):
            chunk = unique[start_idx:start_idx + self.price_chunk_size]
            batch = self._download_chunk(chunk, threads=True)
            self.price_history.update(batch)
            missing.extend(t for t in chunk if t not in batch)
            done = min(start_idx + len(chunk), len(unique))
            if done % 500 < self.price_chunk_size or done == len(unique):
                logger.info("Price prefetch: %s/%s", f"{done:,}", f"{len(unique):,}")

        # Yahoo occasionally omits a few symbols from large batches. Retry only
        # those symbols in smaller batches. Anything still missing falls back to
        # the original per-ticker fetch path.
        if missing:
            logger.info("Retrying %d missing price histories in small batches", len(missing))
            retry_missing: List[str] = []
            for start_idx in range(0, len(missing), 20):
                chunk = missing[start_idx:start_idx + 20]
                batch = self._download_chunk(chunk, threads=True)
                self.price_history.update(batch)
                retry_missing.extend(t for t in chunk if t not in batch)
                time.sleep(0.15)
            missing = retry_missing

        # A large Yahoo batch can be one session behind for only part of the
        # universe. Retry that small stale subset serially, then exclude it from
        # scoring rather than falling back to an unbounded per-ticker request.
        # This preserves the invariant that every persisted scan frame belongs
        # to the EOD session selected by the orchestrator.
        if self.expected_session is not None:
            stale = [
                ticker for ticker in unique
                if ticker in self.price_history and not self._has_expected_session(self.price_history[ticker])
            ]
            if stale:
                logger.info(
                    "Retrying %d price histories missing selected session %s",
                    len(stale), self.expected_session,
                )
                for start_idx in range(0, len(stale), 20):
                    chunk = stale[start_idx:start_idx + 20]
                    self.price_history.update(self._download_chunk(chunk, threads=False))
                    time.sleep(0.15)
            self.price_session_stale = [
                ticker for ticker in unique
                if ticker not in self.price_history or not self._has_expected_session(self.price_history.get(ticker))
            ]
            for ticker in self.price_session_stale:
                self.price_history.pop(ticker, None)
            if self.price_session_stale:
                logger.warning(
                    "Excluded %d histories that could not reach selected session %s",
                    len(self.price_session_stale), self.expected_session,
                )

        self.price_prefetch_missing = missing
        self.price_prefetch_seconds = time.time() - start
        logger.info(
            "FAST PRICE PREFETCH COMPLETE: %s/%s loaded in %.1f min; %d fallback symbols",
            f"{len(self.price_history):,}", f"{len(unique):,}", self.price_prefetch_seconds / 60, len(missing),
        )

    def fetch_spy_data(self) -> bool:
        if "SPY" in self.price_session_stale:
            logger.error("SPY did not reach selected session %s", self.expected_session)
            return False
        frame = self.price_history.get("SPY")
        if frame is not None and not frame.empty and len(frame) >= 200:
            self.spy_data = frame.tail(252).copy()
            self.spy_price = float(self.spy_data["Close"].iloc[-1])
            logger.info("SPY ready from batch cache: %d days, $%.2f", len(self.spy_data), self.spy_price)
            return True
        logger.warning("SPY missing from batch cache; using legacy fetch fallback")
        return super().fetch_spy_data()

    def analyze_single_stock(
        self,
        ticker: str,
        min_price: float,
        max_price: float,
        min_volume: int,
    ) -> Optional[Dict]:
        if ticker in self.price_session_stale:
            self.filtered_count += 1
            self.filter_reasons["session_mismatch"] = self.filter_reasons.get("session_mismatch", 0) + 1
            return None

        long_hist = self.price_history.get(ticker)
        if long_hist is None or long_hist.empty:
            # Rare Yahoo omission: retain the proven legacy fallback rather than
            # silently dropping the stock.
            return super().analyze_single_stock(ticker, min_price, max_price, min_volume)

        try:
            self.total_requests += 1
            price_data = long_hist.tail(252) if len(long_hist) > 252 else long_hist
            if price_data.empty or len(price_data) < 200:
                self.filtered_count += 1
                self.filter_reasons["insufficient_data"] = self.filter_reasons.get("insufficient_data", 0) + 1
                return None

            current_price = float(price_data["Close"].iloc[-1])

            if len(long_hist) >= 252:
                closes = long_hist["Close"].astype(float)
                running_max = closes.expanding().max()
                max_drawdown = ((closes - running_max) / running_max).min()
                if pd.notna(max_drawdown) and max_drawdown < -0.90:
                    self.filtered_count += 1
                    self.filter_reasons["severe_drawdown_90pct"] = self.filter_reasons.get("severe_drawdown_90pct", 0) + 1
                    return None

            if current_price < min_price or current_price > max_price:
                self.filtered_count += 1
                self.filter_reasons["price_range"] = self.filter_reasons.get("price_range", 0) + 1
                return None

            if "Volume" in price_data.columns:
                avg_volume = float(price_data["Volume"].iloc[-20:].mean())
                if avg_volume < min_volume:
                    self.filtered_count += 1
                    self.filter_reasons["low_volume"] = self.filter_reasons.get("low_volume", 0) + 1
                    return None
            else:
                avg_volume = 0.0

            phase_info = classify_phase(price_data, current_price)
            phase = int(phase_info.get("phase", 0) or 0)
            if phase not in [1, 2, 3, 4]:
                self.filtered_count += 1
                self.filter_reasons["invalid_phase"] = self.filter_reasons.get("invalid_phase", 0) + 1
                return None

            rs_series = calculate_relative_strength(price_data["Close"], self.spy_data["Close"], period=63)

            vcp_data = {}
            if phase in [1, 2]:
                vcp_data = detect_vcp_pattern(price_data, current_price, phase_info)

            quarterly_data = {}
            fundamental_analysis = {}
            if phase in [1, 2]:
                if self.use_git_storage and self.git_fetcher:
                    quarterly_data = self.git_fetcher.fetch_fundamentals_smart(ticker)
                else:
                    quarterly_data = fetch_quarterly_financials(ticker)
                quarterly_data = self._sanitize_quarterly_data(quarterly_data)
                fundamental_analysis = analyze_fundamentals_for_signal(quarterly_data)

            return {
                "ticker": ticker,
                "price_data": price_data,
                "current_price": current_price,
                "avg_volume": avg_volume,
                "phase_info": phase_info,
                "rs_series": rs_series,
                "vcp_data": vcp_data,
                "quarterly_data": quarterly_data,
                "fundamental_analysis": fundamental_analysis,
            }

        except Exception as exc:
            self.error_count += 1
            error_type = type(exc).__name__
            self.error_types[error_type] = self.error_types.get(error_type, 0) + 1
            self.error_examples.setdefault(error_type, (ticker, str(exc)))
            logger.exception("Fast analysis failed for %s: %s", ticker, exc)
            return None

    def _persist_analyzed_price_history(self, analyses: List[Dict]) -> None:
        wanted = {str(a.get("ticker", "")).upper() for a in analyses if a.get("ticker")}
        payload: Dict[str, pd.DataFrame] = {}
        for ticker in ("SPY", *ALL_PROXY_TICKERS):
            frame = self.price_history.get(ticker)
            if frame is not None and not frame.empty:
                payload[ticker] = frame
        for ticker in wanted:
            frame = self.price_history.get(ticker)
            if frame is not None and not frame.empty:
                payload[ticker] = frame
        try:
            with self.price_history_file.open("wb") as fh:
                pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
            logger.info(
                "Persisted reusable 5Y price cache: %s tickers, %.1f MB",
                f"{len(payload):,}", self.price_history_file.stat().st_size / 1024 / 1024,
            )
        except Exception as exc:
            logger.warning("Unable to persist 5Y price cache: %s", exc)

    def process_batch_parallel(self, tickers: List[str], *args, **kwargs) -> Dict:
        self.prefetch_price_history(tickers)
        result = super().process_batch_parallel(tickers, *args, **kwargs)
        if "error" not in result:
            self._persist_analyzed_price_history(result.get("analyses", []))
            result["price_prefetch_seconds"] = self.price_prefetch_seconds
            result["price_prefetch_missing"] = len(self.price_prefetch_missing)
        return result
