from __future__ import annotations

import inspect
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from stock_scout.config.schema import Settings
from stock_scout.data.base import BaseDataProvider, ProviderError
from stock_scout.data.cache import ParquetCache
from stock_scout.data.factory import build_provider
from stock_scout.data.market_store import MarketDataStore
from stock_scout.data.sector_cache import (
    SectorEntry,
    build_sector_cache_from_profiles,
    load_sector_cache,
)
from stock_scout.indicators.momentum import percentile_of
from stock_scout.pipeline.enrich import compute_indicators, latest_features
from stock_scout.pipeline.market_stress import (
    compute_market_stress,
    fetch_hy_spread,
    fetch_vix,
    realized_vol_proxy,
)
from stock_scout.pipeline.regime import apply_breadth_to_regime, compute_market_breadth, compute_regime
from stock_scout.pipeline.stage_classifier import classify_stage
from stock_scout.pipeline.prefilter import PrefilterResult, prefilter
from stock_scout.scoring.models import Candidate, Reason
from stock_scout.scoring.scorer import CandidateScorer
from stock_scout.setups.accumulation_base import AccumulationBaseDetector
from stock_scout.setups.crash_base_stage1 import CrashBaseStage1Detector
from stock_scout.setups.ema_stack_launch import EMAStackLaunchDetector
from stock_scout.setups.ema_cross import EMACrossDetector
from stock_scout.setups.glb import GLBDetector
from stock_scout.setups.guppy import GuppyDetector
from stock_scout.setups.high_rs import HighRSDetector
from stock_scout.setups.long_base_launch import LongBaseLaunchDetector
from stock_scout.setups.ma_cluster_volume_breakout import MAClusterVolumeBreakoutDetector
from stock_scout.setups.minervini import MinerviniDetector
from stock_scout.setups.rwb_squeeze_thrust import RWBSqueezeThrustDetector
from stock_scout.setups.rs_gate import apply_rs_rating_gate
from stock_scout.setups.tight_breakout import TightBreakoutDetector
from stock_scout.setups.weinstein import WeinsteinDetector
from stock_scout.utils.dates import history_start, last_trading_day, today_ny
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

BENCHMARK_TICKER = "SPY"
REGIME_SECONDARY_TICKER = "QQQ"


@dataclass
class RunStats:
    universe_size: int = 0
    cache_updated: int = 0
    cache_failed: int = 0
    fallback_used: int = 0
    prefilter_passed: int = 0
    prefilter_failed: int = 0
    setup_triggered_counts: dict[str, int] = field(default_factory=dict)
    timings_seconds: dict[str, float] = field(default_factory=dict)
    primary_provider: str = ""
    fallback_provider: str | None = None
    tertiary_fallback_provider: str | None = None
    as_of_date: str = ""
    # --- Fetch coverage tracking (added 2026-05-18, Faza 1 P0) -----------
    tickers_fetched_primary: int = 0
    tickers_fetched_local: int = 0
    tickers_fetched_fallback: int = 0
    tickers_fetched_tertiary: int = 0
    tickers_failed_all_providers: int = 0
    tickers_fetched_deep_history: int = 0
    coverage_pct: float = 0.0
    data_status: str = "OK"  # OK | PARTIAL | FAILED
    provider_switched_to: str | None = None  # circuit-breaker promoted primary
    # --- Faza 5 bucket counts -------------------------------------------
    actionability_counts: dict[str, int] = field(default_factory=dict)
    m_and_a_excluded_count: int = 0
    # --- Faza G ----------------------------------------------------------
    rs_universe_count: int = 0                # size of cross-sectional RS distribution
    rs_rating_low_confidence: bool = False    # universe too small to trust RS-rating percentiles
    failed_tickers: list[str] = field(default_factory=list)  # dropped: error or no provider data
    regime: dict = field(default_factory=dict)  # market regime snapshot (SPY/QQQ)
    stage_counts: dict[str, int] = field(default_factory=dict)  # Weinstein stage distribution (full universe)
    # --- Faza O: universe-coverage audit (answers "when do the ~12k get
    # re-reviewed") + price-history completeness for Weinstein context --------
    universe_pre_negcache: int = 0            # common stocks before negative-cache subtraction
    negative_cache_excluded: int = 0          # illiquid names trimmed before fetch
    negative_cache_recheck_days: int = 0      # cadence: excluded names re-reviewed after N days
    tickers_insufficient_weekly_history: int = 0  # < bars needed for a Weinstein stage call
    tickers_missing_mansfield_rs: int = 0     # stage classified but no Mansfield RS (short history / no benchmark)


@dataclass
class RunResult:
    candidates: list[Candidate]          # actionable_now + near_actionable + watch
    excluded: list[Candidate] = field(default_factory=list)  # extended + excluded + not_valid (kept for audit)
    stats: RunStats = field(default_factory=RunStats)
    stage_rows: list[dict] = field(default_factory=list)  # full-universe Weinstein stage classification


@dataclass
class _PendingScore:
    """Carries everything needed to score a candidate, deferred until after the
    universe-wide RS distribution has been collected (so rs_rating can be
    injected before scoring). Built only for tickers that pass prefilter + have
    at least one triggered setup."""

    ticker: str
    features: dict
    results: list  # list[SetupResult]
    provider_used: str
    used_fallback: bool
    sector: str | None
    industry: str | None
    ma_conf: str
    # Whether the *absolute* pin test fired, as opposed to any of the looser
    # price signals. Tightness is scored off a range-contraction ratio that
    # saturates long before a deal-locked stock gets there, so a pinned name and
    # an ordinary consolidation both earn full marks and the score cannot tell
    # them apart. This flag is what lets it.
    ma_price_pin: bool = False


def _needs_backfill_refetch(cached_start: date, start: date, requested_start: str) -> bool:
    """Whether to force a full refetch purely to deepen cached history.

    True only when the cache's earliest bar is well after the requested start
    (>31 days) AND we have not already attempted a fetch from at least that far
    back. `requested_start` is the earliest start we ever asked the provider for;
    once it is <= `start`, a still-short cache means the provider simply has no
    earlier data (post-IPO name), so re-fetching every run is pointless.
    """
    if (cached_start - start).days <= 31:
        return False
    if requested_start:
        try:
            if date.fromisoformat(requested_start[:10]) <= start:
                return False
        except ValueError:
            pass
    return True


def _breadth_sample(features: dict, daily: pd.DataFrame) -> dict | None:
    close = features.get("close")
    sma50 = features.get("sma50")
    sma200 = features.get("sma200")
    if close is None or sma50 is None or sma200 is None or daily.empty or "close" not in daily.columns:
        return None
    closes = daily["close"].dropna()
    if len(closes) < 2:
        return None
    try:
        last_close = float(close)
        prev_close = float(closes.iloc[-2])
        last_sma50 = float(sma50)
        last_sma200 = float(sma200)
    except (TypeError, ValueError):
        return None
    return {
        "above_sma50": bool(last_close > last_sma50),
        "above_sma200": bool(last_close > last_sma200),
        "advanced": bool(last_close > prev_close),
    }


class PipelineRunner:
    """End-to-end daily-run orchestrator.

    Steps:
      1. Build / load universe (or accept caller-supplied list)
      2. Update cache (primary, then fallback for failures)
      3. Compute indicators (cross-references SPY benchmark)
      4. Prefilter
      5. Detect setups
      6. Score
      7. Return RunResult (reporting + AI ranking happen externally)
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        # One decision, read once: every fetch in this run writes the store's
        # basis. A ticker holding both split-only and dividend-adjusted bars
        # shows a 10-36% jump at the splice that reads as a breakout.
        self._adjusted = settings.marketdata.price_basis == "split_div"
        self.cache = ParquetCache(settings.project_root / settings.cache.base_dir)
        self.market_store = (
            MarketDataStore.from_settings(settings)
            if settings.marketdata.enabled and settings.marketdata.scan_local_first
            else None
        )
        self.scorer = CandidateScorer(settings.scoring)

        self.primary = build_provider(settings.providers.primary_data_provider, settings)
        self.fallback = (
            build_provider(settings.providers.fallback_provider, settings)
            if settings.providers.fallback_provider
            else None
        )
        self.tertiary = (
            build_provider(settings.providers.tertiary_fallback_provider, settings)
            if settings.providers.tertiary_fallback_provider
            else None
        )
        # Keyless deep-history backfill (Stooq by default). Built once; appended
        # as the final link in the daily + weekly fallback chains.
        self.deep_history = (
            build_provider(settings.providers.deep_history_provider, settings)
            if settings.providers.deep_history_provider
            else None
        )
        # Circuit-breaker state: the ACTIVE primary may be switched mid-run if
        # the configured one collapses (tracked over the first N tickers).
        self._active_primary = self.primary
        self._cb_samples = 0
        self._cb_empties = 0
        self._cb_tripped = False

        self.detectors = [
            GLBDetector(settings.setups.glb),
            MinerviniDetector(settings.setups.minervini),
            TightBreakoutDetector(settings.setups.tight_breakout),
            WeinsteinDetector(settings.setups.weinstein),
            HighRSDetector(settings.setups.high_rs),
            EMACrossDetector(settings.setups.ema_cross),
            GuppyDetector(settings.setups.guppy),
            RWBSqueezeThrustDetector(settings.setups.rwb_squeeze_thrust),
            EMAStackLaunchDetector(settings.setups.ema_stack_launch),
            AccumulationBaseDetector(settings.setups.accumulation),
            LongBaseLaunchDetector(settings.setups.long_base_launch),
            MAClusterVolumeBreakoutDetector(settings.setups.ma_cluster_volume_breakout),
            CrashBaseStage1Detector(settings.setups.crash_base_stage1),
        ]

        self.sector_path = settings.project_root / "data" / "sector_cache.parquet"
        self.sector_map: dict[str, SectorEntry] = load_sector_cache(self.sector_path)
        # yfinance profile provider for the sector/industry fallback (FMP bulk
        # sectors need a paid plan). Built lazily-safe; only used for enrichment.
        try:
            self._profile_provider = build_provider("yfinance", settings)
        except Exception:  # noqa: BLE001
            self._profile_provider = None

    # ---- bulk pre-warm (Alpaca-only optimisation) ----------------------------

    def _bulk_prewarm(
        self,
        tickers: list[str],
        start: date,
        end: date,
        freq: str = "daily",
        batch_size: int = 100,
    ) -> int:
        """Pre-warm cache by bulk-fetching daily/weekly bars in batches.

        Works only when the primary provider exposes `get_bulk_daily_ohlcv` /
        `get_bulk_weekly_ohlcv` (currently Alpaca). For other providers this
        is a no-op; the per-ticker loop will fetch normally.

        Returns the number of tickers successfully pre-warmed.
        """
        bulk_method = getattr(
            self.primary,
            f"get_bulk_{freq}_ohlcv",
            None,
        )
        if bulk_method is None:
            return 0

        # Only fetch tickers that need it: missing from cache OR cache is
        # incremental-stale. Reduces wasted API calls. We stash the metadata read
        # here and reuse it in the write loop below, so each parquet's metadata is
        # read once per prewarm pass instead of twice.
        to_fetch: list[str] = []
        meta_by_ticker: dict[str, object] = {}
        # One grouped query instead of a max(date) scan per ticker: against an
        # unindexed multi-GB ohlcv_daily the loop cost ~0.09s each, i.e. minutes
        # per prewarm pass, and this runs once for daily and once for weekly.
        latest_local_by_ticker: dict[str, date] = {}
        if self.market_store is not None:
            try:
                latest_local_by_ticker = self.market_store.latest_bar_dates(tickers)
            except Exception as e:  # noqa: BLE001
                log.warning("bulk_prewarm.latest_bar_dates_failed", error=str(e)[:200])
        for t in tickers:
            if self.market_store is not None:
                # keys come back normalized the way the store stores them
                latest_local = latest_local_by_ticker.get(t.strip().upper())
                if latest_local is not None and latest_local >= end:
                    continue
            meta = self.cache.read_metadata(self.primary.name, t, freq)
            if meta is None or meta.rows == 0:
                to_fetch.append(t)
                meta_by_ticker[t] = None
                continue
            last_cached = self.cache.last_cached_date(self.primary.name, t, freq)
            if last_cached and last_cached < end:
                to_fetch.append(t)
                meta_by_ticker[t] = meta
        if not to_fetch:
            log.info("bulk_prewarm.skipped", reason="cache_fresh", freq=freq)
            return 0

        log.info(
            "bulk_prewarm.start",
            count=len(to_fetch),
            batches=(len(to_fetch) + batch_size - 1) // batch_size,
            freq=freq,
        )
        successes = 0
        t0 = time.monotonic()
        for i in range(0, len(to_fetch), batch_size):
            batch = to_fetch[i : i + batch_size]
            try:
                # Not every provider's bulk fetch takes a basis; pass it only
                # where it exists rather than breaking the others.
                if "adjusted" in inspect.signature(bulk_method).parameters:
                    results = bulk_method(batch, start, end, adjusted=self._adjusted)
                else:
                    results = bulk_method(batch, start, end)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "bulk_prewarm.batch_failed",
                    batch_start=i,
                    error=str(e)[:200],
                )
                continue
            for ticker, df in results.items():
                if df is None or df.empty:
                    continue
                try:
                    # Use merge_append if cache exists, else write fresh. Reuse
                    # the metadata read during selection (the ticker is always a
                    # member of to_fetch here) rather than reading the parquet again.
                    meta = meta_by_ticker.get(ticker)
                    if meta is None or meta.rows == 0:
                        self.cache.write(df, self.primary.name, ticker, freq, adjusted=self._adjusted)
                    else:
                        self.cache.merge_append(
                            df, self.primary.name, ticker, freq, adjusted=self._adjusted
                        )
                    if self.market_store is not None and freq == "daily":
                        self.market_store.upsert_ohlcv(
                            ticker, df, self.primary.name, adjusted=self._adjusted
                        )
                    successes += 1
                except Exception as e:  # noqa: BLE001
                    log.debug(
                        "bulk_prewarm.write_failed",
                        ticker=ticker,
                        error=str(e)[:120],
                    )
        log.info(
            "bulk_prewarm.done",
            successes=successes,
            attempted=len(to_fetch),
            seconds=round(time.monotonic() - t0, 1),
            freq=freq,
        )
        return successes

    # ---- universe + cache -----------------------------------------------------

    @staticmethod
    def _weekly_from_daily(daily: pd.DataFrame) -> pd.DataFrame:
        if daily is None or daily.empty:
            return pd.DataFrame()
        return (
            daily.resample("W-FRI")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna(how="any")
        )

    def _local_history_for(self, ticker: str, freq: str, start: date, end: date) -> pd.DataFrame:
        if self.market_store is None:
            return pd.DataFrame()
        if freq == "daily":
            return self.market_store.read_ohlcv(ticker, start, end)
        daily = self.market_store.read_ohlcv(ticker, start, end)
        return self._weekly_from_daily(daily)

    def _local_daily_weekly(
        self, ticker: str, daily_start: date, weekly_start: date, end: date
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Both frequencies from a single store read.

        Weekly bars are resampled from the same daily rows, and the weekly
        window is the deeper of the two, so reading once at the deepest start
        and slicing gives identical frames for one scan instead of two.
        """
        if self.market_store is None:
            return pd.DataFrame(), pd.DataFrame()
        deepest = min(daily_start, weekly_start)
        full = self.market_store.read_ohlcv(ticker, deepest, end)
        if full.empty:
            return pd.DataFrame(), pd.DataFrame()
        daily = full[full.index >= pd.Timestamp(daily_start)]
        weekly_src = full[full.index >= pd.Timestamp(weekly_start)]
        return daily, self._weekly_from_daily(weekly_src)

    def _ensure_history_for(
        self,
        provider: BaseDataProvider,
        ticker: str,
        freq: str,
        start: date,
        end: date,
    ) -> tuple[pd.DataFrame, bool]:
        """Return (frame, fallback_used). Updates cache incrementally."""
        meta = self.cache.read_metadata(provider.name, ticker, freq)
        # Staleness check: a parquet older than max_staleness_days gets a full
        # refetch, not an incremental top-up. Protects against ticker that
        # was offline for a while or had a corrupted partial write.
        if meta is not None and meta.last_updated:
            try:
                from datetime import datetime as _dt
                from datetime import timezone as _tz

                last_upd = _dt.fromisoformat(meta.last_updated.replace("Z", "+00:00"))
                if last_upd.tzinfo is None:
                    last_upd = last_upd.replace(tzinfo=_tz.utc)
                age_days = (_dt.now(_tz.utc) - last_upd).days
                if age_days > self.settings.cache.max_staleness_days:
                    log.info(
                        "cache.stale_full_refetch",
                        ticker=ticker,
                        provider=provider.name,
                        age_days=age_days,
                    )
                    meta = None  # force the full-fetch path below
            except Exception as e:  # noqa: BLE001
                # Bad/parseable-but-odd last_updated stamp — don't trust it, fall
                # through to a normal incremental update. Log so a systematically
                # malformed cache surfaces instead of silently re-fetching forever.
                log.debug(
                    "cache.staleness_check_failed",
                    ticker=ticker,
                    provider=provider.name,
                    error=repr(e),
                )
        # Coverage check: if history depth was increased (config bumped), the
        # existing parquet may not reach back far enough. Force a full refetch
        # so the deeper base/stage context actually gets backfilled — BUT only if
        # we haven't already tried fetching from this far back. Post-IPO names
        # (e.g. RIVN) can never reach a 5y-ago start, so without this guard they
        # triggered a wasteful full refetch on every single run.
        if meta is not None and getattr(meta, "start_date", None):
            try:
                cached_start = date.fromisoformat(str(meta.start_date)[:10])
                if _needs_backfill_refetch(
                    cached_start, start, getattr(meta, "requested_start", "") or ""
                ):
                    log.info(
                        "cache.backfill_full_refetch",
                        ticker=ticker,
                        provider=provider.name,
                        cached_start=str(cached_start),
                        requested_start=str(start),
                    )
                    meta = None
            except Exception as e:  # noqa: BLE001
                log.debug(
                    "cache.backfill_check_failed",
                    ticker=ticker,
                    provider=provider.name,
                    error=repr(e),
                )
        if meta is None or meta.rows == 0:
            df = (
                provider.get_daily_ohlcv(ticker, start, end, adjusted=self._adjusted)
                if freq == "daily"
                else provider.get_weekly_ohlcv(ticker, start, end)
            )
            if df.empty:
                return pd.DataFrame(), False
            # Record the floor we asked for, so a post-IPO name whose data only
            # goes back to its listing date isn't re-fetched in full every run.
            self.cache.write(
                df, provider.name, ticker, freq, adjusted=self._adjusted, requested_start=str(start)
            )
            if self.market_store is not None and freq == "daily":
                self.market_store.upsert_ohlcv(ticker, df, provider.name, adjusted=self._adjusted)
            return self.cache.read(provider.name, ticker, freq), False

        last_cached = self.cache.last_cached_date(provider.name, ticker, freq)
        if last_cached and last_cached >= end:
            return self.cache.read(provider.name, ticker, freq), False

        fetch_start = last_cached or start
        new_df = (
            provider.get_daily_ohlcv(ticker, fetch_start, end, adjusted=self._adjusted)
            if freq == "daily"
            else provider.get_weekly_ohlcv(ticker, fetch_start, end)
        )
        if new_df.empty:
            return self.cache.read(provider.name, ticker, freq), False
        self.cache.merge_append(new_df, provider.name, ticker, freq, adjusted=self._adjusted)
        if self.market_store is not None and freq == "daily":
            self.market_store.upsert_ohlcv(ticker, new_df, provider.name, adjusted=self._adjusted)
        return self.cache.read(provider.name, ticker, freq), False

    def _ordered_providers(self) -> list[BaseDataProvider]:
        """Active primary first, then the configured fallbacks (de-duped)."""
        out: list[BaseDataProvider] = []
        for p in (self._active_primary, self.primary, self.fallback, self.tertiary, self.deep_history):
            if p is not None and all(p is not q for q in out):
                out.append(p)
        return out

    def _health_precheck(self, stats: "RunStats") -> None:
        """If the configured primary is unhealthy, promote the first healthy
        fallback to active primary for this run. Best-effort; never fatal."""
        if not self.settings.marketdata.scan_provider_fallback:
            log.info("health.precheck_skipped", reason="local_data_only")
            return
        if not self.settings.fetch.health_precheck:
            return
        try:
            h = self.primary.health_check()
        except Exception as e:  # noqa: BLE001
            h = None
            log.warning("health.precheck_error", provider=self.primary.name, error=str(e)[:160])
        if h is not None and h.healthy:
            return
        for alt in (self.fallback, self.tertiary, self.deep_history):
            if alt is None:
                continue
            try:
                ah = alt.health_check()
            except Exception:  # noqa: BLE001
                continue
            if ah.healthy:
                log.warning(
                    "health.primary_down_promoting_fallback",
                    primary=self.primary.name,
                    promoted=alt.name,
                )
                self._active_primary = alt
                stats.provider_switched_to = alt.name
                return

    def _maybe_trip_circuit_breaker(self, source: str, stats: "RunStats") -> None:
        """Switch the active primary to a healthy alternative if it keeps
        failing to serve data over the first N tickers of the run."""
        if self._cb_tripped:
            return
        self._cb_samples += 1
        if source != "primary":
            self._cb_empties += 1
        cfg = self.settings.fetch
        if self._cb_samples < cfg.circuit_breaker_min_samples:
            return
        if self._cb_empties / self._cb_samples < cfg.circuit_breaker_empty_pct:
            # Primary is healthy enough — stop evaluating (one-shot decision).
            self._cb_tripped = True
            return
        for alt in (self.fallback, self.tertiary, self.deep_history):
            if alt is None or alt is self._active_primary:
                continue
            try:
                if not alt.health_check().healthy:
                    continue
            except Exception:  # noqa: BLE001
                continue
            log.warning(
                "fetch.circuit_breaker_tripped",
                old_primary=self._active_primary.name,
                new_primary=alt.name,
                empty_rate=round(self._cb_empties / self._cb_samples, 2),
                samples=self._cb_samples,
            )
            self._active_primary = alt
            stats.provider_switched_to = alt.name
            self._cb_tripped = True
            return
        self._cb_tripped = True  # no healthy alternative; stop trying

    def _fetch_with_fallback(
        self, ticker: str, start: date, end: date
    ) -> tuple[pd.DataFrame, pd.DataFrame, str, str]:
        """Try local store first, then provider fallback when enabled."""
        weekly_start = history_start(self.settings.cache.weekly_history_years, end)
        local_daily, local_weekly = self._local_daily_weekly(ticker, start, weekly_start, end)
        if not local_daily.empty:
            local_latest = local_daily.index[-1].date()
            if local_latest >= end or not self.settings.marketdata.scan_provider_fallback:
                return local_daily, local_weekly, "local", "local"
        if not self.settings.marketdata.scan_provider_fallback:
            return pd.DataFrame(), pd.DataFrame(), "local", "none"
        # Daily fallback chain: active-primary → fallback → tertiary → deep-history.
        # `_active_primary` may differ from `self.primary` after a circuit-breaker
        # switch. The deep-history provider (Stooq) is the keyless last resort.
        daily_chain: list[tuple[str, BaseDataProvider | None]] = [
            ("primary", self._active_primary),
            ("fallback", self.fallback),
            ("tertiary", self.tertiary),
            ("deep_history", self.deep_history),
        ]
        # De-dupe (active primary might equal a configured fallback).
        seen: set[int] = set()
        daily = pd.DataFrame()
        source = "none"
        for label, prov in daily_chain:
            if prov is None or id(prov) in seen:
                continue
            seen.add(id(prov))
            try:
                df, _ = self._ensure_history_for(prov, ticker, "daily", start, end)
            except ProviderError as e:
                log.warning(f"{label}.error", ticker=ticker, provider=prov.name, error=str(e))
                continue
            if not df.empty:
                daily = df
                source = label
                break

        provider_used = self._active_primary.name
        for label, prov in daily_chain:
            if prov is not None and label == source:
                provider_used = prov.name
                break

        # The portable runner keeps at least as much split-only daily history
        # as weekly history. Resampling that authoritative frame matches the
        # private local-store path and avoids a mixed dividend-adjusted weekly
        # basis without changing any detector or ranking rule.
        if (
            not daily.empty
            and self.settings.cache.daily_history_years
            >= self.settings.cache.weekly_history_years
        ):
            weekly_source = daily[daily.index >= pd.Timestamp(weekly_start)]
            weekly = self._weekly_from_daily(weekly_source)
            if not weekly.empty:
                return daily, weekly, provider_used, source

        # Stage Analysis v2 is range-first, so it wants multi-year weekly
        # context rather than the first provider that clears the 30w-SMA
        # minimum.
        min_weekly = self.settings.stage_analysis.weekly_sma_period + self.settings.stage_analysis.slope_lookback_weeks
        target_weekly = max(min_weekly, int(self.settings.cache.weekly_history_years * 52 * 0.8))
        weekly = local_weekly  # already resampled from the single read above
        if not weekly.empty:
            # Local daily bars are the authority, so their resampling is the
            # weekly truth too. The provider loop below used to run regardless,
            # costing a round-trip per ticker; worse, get_weekly_ohlcv takes no
            # basis parameter and asks for adjusted bars, so it would hand the
            # stage classifier dividend-adjusted weeklies while the chart shows
            # split-only ones. Providers are now only a fallback for tickers the
            # store has nothing for.
            return daily, weekly, provider_used, source
        wk_seen: set[int] = set()
        for prov in (self._active_primary, self.fallback, self.tertiary, self.deep_history):
            if prov is None or id(prov) in wk_seen:
                continue
            wk_seen.add(id(prov))
            try:
                cand, _ = self._ensure_history_for(prov, ticker, "weekly", weekly_start, end)
            except ProviderError as e:
                log.debug("weekly.provider_error", ticker=ticker, provider=prov.name, error=str(e))
                continue
            if cand is None or cand.empty:
                continue
            if len(cand) > len(weekly):
                weekly = cand
            if len(weekly) >= target_weekly:
                break  # enough multi-year history for range-first stage context

        return daily, weekly, provider_used, source

    # ---- pre-fetch filter -----------------------------------------------------

    def _apply_pre_fetch_filter(self, tickers: list[str]) -> list[str]:
        """If a negative-cache file exists, subtract its tickers from the
        universe (decayed entries are ignored). Honors force_include from the
        universe_overrides.yaml if present."""
        from stock_scout.data.universe_filter import apply_negative_cache, load_negative_cache

        neg_path = self.settings.project_root / "data" / "universe" / "excluded_illiquid.parquet"
        neg = load_negative_cache(neg_path)
        if neg.empty:
            return tickers

        # Load force_include set from manual overrides (already merged into the
        # universe upstream, but we still want to protect those tickers from
        # the negative-cache subtraction).
        force = set()
        overrides_file = self.settings.universe.manual_overrides_file
        if overrides_file:
            ov_path = Path(overrides_file)
            if not ov_path.is_absolute():
                ov_path = self.settings.project_root / ov_path
            if ov_path.exists():
                try:
                    import yaml

                    data = yaml.safe_load(ov_path.read_text(encoding="utf-8")) or {}
                    force = {str(t).upper() for t in (data.get("force_include") or [])}
                except Exception as e:  # noqa: BLE001
                    log.warning("orchestrator.overrides_load_failed", error=str(e))

        res = apply_negative_cache(
            tickers,
            neg,
            force_include=force,
            recheck_after_days=self.settings.prefilter.negative_cache_recheck_after_days,
        )
        log.info(
            "orchestrator.pre_fetch_filter",
            input=res.summary["input"],
            kept=res.summary["kept"],
            excluded=res.summary["excluded"],
            decayed=res.summary["decayed"],
        )
        return res.keep

    # ---- main entry -----------------------------------------------------------

    def run(self, tickers: list[str], *, as_of: date | None = None) -> RunResult:
        """Run the frozen production engine for one completed market session.

        ``as_of`` is an orchestration input only.  It makes GitHub manual runs
        and parity fixtures reproducible; detector, score, ranking, and trade
        plan semantics are unchanged.
        """
        # Pre-fetch trim: subtract illiquid tickers from the negative-cache
        # (built from earlier runs). Decays after `recheck_after_days`.
        universe_pre = len(tickers)
        tickers = self._apply_pre_fetch_filter(tickers)

        stats = RunStats(
            universe_size=len(tickers),
            primary_provider=self.primary.name,
            fallback_provider=self.fallback.name if self.fallback else None,
            tertiary_fallback_provider=self.tertiary.name if self.tertiary else None,
        )
        # Universe-coverage audit: how many common stocks existed before the
        # negative-cache trim, how many were trimmed, and the re-review cadence.
        stats.universe_pre_negcache = universe_pre
        stats.negative_cache_excluded = universe_pre - len(tickers)
        stats.negative_cache_recheck_days = self.settings.prefilter.negative_cache_recheck_after_days
        as_of = as_of or last_trading_day()
        stats.as_of_date = as_of.isoformat()
        start = history_start(self.settings.cache.daily_history_years, as_of)

        # Console for human-readable phase markers + final summary. Structured
        # `log` lines still carry the machine-readable detail; this is the at-a-
        # glance "what is happening / how far along" view the operator watches.
        console = Console()
        console.rule(f"[bold]Scout scan[/] · {len(tickers)} symbols · as-of [cyan]{as_of.isoformat()}[/]")

        # 1) Fetch benchmark first — every ticker needs it for RS
        console.print("[cyan]›[/] [bold]Phase 1/3[/] · benchmark + market regime")
        t0 = time.monotonic()
        log.info("benchmark.fetching", ticker=BENCHMARK_TICKER)
        bench_daily, _, bench_provider, bench_source = self._fetch_with_fallback(BENCHMARK_TICKER, start, as_of)
        if bench_daily.empty:
            log.error("benchmark.missing", ticker=BENCHMARK_TICKER)
            raise ProviderError(f"Benchmark {BENCHMARK_TICKER} unavailable from any provider")
        benchmark_close = bench_daily["close"]
        # Weekly benchmark for Mansfield RS in full-universe stage classification.
        try:
            benchmark_weekly_close = (
                bench_daily["close"].resample("W-FRI").last().dropna()
            )
        except Exception:  # noqa: BLE001
            benchmark_weekly_close = None
        stats.timings_seconds["benchmark_fetch"] = round(time.monotonic() - t0, 2)

        # 1.1) Market regime snapshot (SPY + QQQ). Soft ranking lean only.
        try:
            qqq_daily, _, _, _ = self._fetch_with_fallback(REGIME_SECONDARY_TICKER, start, as_of)
        except Exception:  # noqa: BLE001
            qqq_daily = pd.DataFrame()
        stats.regime = compute_regime(bench_daily, qqq_daily if not qqq_daily.empty else None)
        log.info("run.regime", state=stats.regime.get("state"), mult=stats.regime.get("score_multiplier"))
        _regime_tone = {"confirmed_uptrend": "green", "under_pressure": "yellow", "correction": "red"}.get(
            str(stats.regime.get("state")), "white"
        )
        console.print(
            f"    regime [{_regime_tone}]{str(stats.regime.get('state')).replace('_', ' ')}[/]"
            f" · guppy {stats.regime.get('guppy_state', '?')}"
        )

        # 1.2) Weekly market-stress gauge (RSI + VIX + HY spread → level 0-3).
        # Best-effort external data; never fatal.
        try:
            vix_source = "none"
            if self.settings.marketdata.scan_provider_fallback:
                vix_val, vix_source = fetch_vix(self.settings)
                hy_val = fetch_hy_spread()
            else:
                # Local-only runs still get a volatility read, from SPY bars.
                vix_val = realized_vol_proxy(self.settings)
                vix_source = "realized_proxy" if vix_val is not None else "none"
                hy_val = None
            stress = compute_market_stress(benchmark_weekly_close, vix_val, hy_val)
            # Realized vol is not implied vol; label it so the tiers are not
            # read as if a real VIX print produced them.
            stress["vix_source"] = vix_source
            stats.regime["stress"] = stress
            log.info(
                "run.market_stress",
                level=stress["level"],
                rsi=stress["weekly_rsi"],
                vix=stress["vix"],
                vix_source=vix_source,
                hy=stress["hy_spread"],
            )
        except Exception as e:  # noqa: BLE001
            log.warning("market_stress.skipped", error=str(e))

        # Drop benchmark + regime indices from per-ticker work if present
        _skip = {BENCHMARK_TICKER, REGIME_SECONDARY_TICKER}
        work_list = [t for t in tickers if t.upper() not in _skip]

        # 1.5) Bulk pre-warm cache via primary provider's bulk endpoint (Alpaca).
        # This dramatically reduces wall-clock vs per-ticker fetch (200 req/min
        # limit becomes ~10 requests for 1000 tickers in batches of 100).
        # No-op if primary doesn't support bulk.
        weekly_start = history_start(self.settings.cache.weekly_history_years, as_of)
        console.print(f"[cyan]›[/] [bold]Phase 2/3[/] · bulk pre-warm cache ({len(work_list)} symbols)")
        t_bulk = time.monotonic()
        if self.settings.marketdata.scan_provider_fallback:
            bulk_daily_count = self._bulk_prewarm(work_list, start, as_of, "daily")
            bulk_weekly_count = (
                0
                if self.settings.cache.daily_history_years
                >= self.settings.cache.weekly_history_years
                else self._bulk_prewarm(work_list, weekly_start, as_of, "weekly")
            )
        else:
            bulk_daily_count = 0
            bulk_weekly_count = 0
            log.info("bulk_prewarm.skipped", reason="local_data_only")
        stats.timings_seconds["bulk_prewarm"] = round(time.monotonic() - t_bulk, 2)
        if bulk_daily_count or bulk_weekly_count:
            console.print(
                f"    pre-warmed [green]{bulk_daily_count}[/] daily · [green]{bulk_weekly_count}[/] weekly"
                f" in {stats.timings_seconds['bulk_prewarm']}s"
            )
        elif not self.settings.marketdata.scan_provider_fallback:
            console.print("    [dim]local-data-only - provider pre-warm skipped[/]")
        else:
            console.print("    [dim]no bulk endpoint — fetching per-ticker[/]")
            log.info(
                "orchestrator.bulk_prewarm_complete",
                daily=bulk_daily_count,
                weekly=bulk_weekly_count,
                seconds=stats.timings_seconds["bulk_prewarm"],
            )

        # 1.6) Provider health pre-check — if the configured primary is down,
        # promote a healthy fallback to active primary for this run.
        self._health_precheck(stats)

        # 2) Fetch + process per-ticker (threaded). Scoring is deferred so we
        #    can compute a universe-relative RS rating first.
        universe_rs: list[float] = []
        breadth_samples: list[dict] = []
        pendings: list[_PendingScore] = []
        stage_rows: list[dict] = []
        max_workers = self.settings.yfinance.max_workers
        # Watchdog: bound the whole scan so a pathological ticker can't hang the
        # run. Budget = per-ticker timeout × ceil(work/threads) + slack.
        overall_deadline = (
            self.settings.fetch.per_ticker_timeout_seconds
            * max(1, math.ceil(len(work_list) / max(1, max_workers)))
            + 120.0
        )
        console.print(f"[cyan]›[/] [bold]Phase 3/3[/] · scanning {len(work_list)} symbols ({max_workers} workers)")
        t1 = time.monotonic()

        def _scan_desc(last: str | None = None) -> str:
            """Live one-liner: candidates found, failures, and the last symbol seen."""
            d = (
                f"Scanning · [green]✓{stats.prefilter_passed}[/] cand"
                f" · [red]⚑{stats.tickers_failed_all_providers}[/] fail"
            )
            if last:
                d += f" · [dim]{last}[/]"
            return d

        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task(_scan_desc(), total=len(work_list))
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {
                    ex.submit(self._process_one, t, start, as_of, benchmark_close, benchmark_weekly_close): t
                    for t in work_list
                }
                try:
                    for fut in as_completed(futures, timeout=overall_deadline):
                        ticker = futures[fut]
                        try:
                            rs_score, pending, stage_row, breadth_sample, _prefilter_res, source = fut.result()
                        except Exception as e:
                            log.warning("ticker.error", ticker=ticker, error=str(e))
                            stats.cache_failed += 1
                            stats.tickers_failed_all_providers += 1
                            stats.failed_tickers.append(ticker)
                            progress.update(task, advance=1, description=_scan_desc(ticker))
                            continue

                        # Fetch coverage tallies
                        if source == "local":
                            stats.tickers_fetched_local += 1
                        elif source == "primary":
                            stats.tickers_fetched_primary += 1
                        elif source == "fallback":
                            stats.tickers_fetched_fallback += 1
                            stats.fallback_used += 1
                        elif source == "tertiary":
                            stats.tickers_fetched_tertiary += 1
                            stats.fallback_used += 1
                        elif source == "deep_history":
                            stats.tickers_fetched_deep_history += 1
                            stats.fallback_used += 1
                        else:  # "none" — every provider returned empty
                            stats.tickers_failed_all_providers += 1
                            stats.failed_tickers.append(ticker)

                        # Circuit-breaker: track how often the active primary
                        # failed to serve; switch to a healthy alt if it collapses.
                        if source != "local":
                            self._maybe_trip_circuit_breaker(source, stats)

                        # Universe RS distribution (every ticker with computable RS).
                        if rs_score is not None:
                            universe_rs.append(float(rs_score))

                        if breadth_sample is not None:
                            breadth_samples.append(breadth_sample)

                        # Full-universe Weinstein stage (every ticker w/ weekly data).
                        if stage_row is not None:
                            stage_rows.append(stage_row)

                        if pending is None:
                            stats.prefilter_failed += 1
                        else:
                            stats.prefilter_passed += 1
                            pendings.append(pending)
                        stats.cache_updated += 1
                        progress.update(task, advance=1, description=_scan_desc(ticker))
                except TimeoutError:
                    log.warning("scan.watchdog_timeout", deadline_s=round(overall_deadline, 1))
                # Watchdog sweep: any future still unfinished is counted as failed
                # so the run completes instead of blocking on a hung ticker.
                for fut, tkr in futures.items():
                    if not fut.done():
                        fut.cancel()
                        stats.tickers_failed_all_providers += 1
                        stats.failed_tickers.append(tkr)
                        progress.update(task, advance=1, description=_scan_desc(tkr))

        stats.timings_seconds["scan"] = round(time.monotonic() - t1, 2)
        console.print(
            f"    scan done in {stats.timings_seconds['scan']}s"
            f" · [green]{stats.prefilter_passed}[/] passed prefilter"
            f" · [red]{stats.tickers_failed_all_providers}[/] failed"
        )
        stats.rs_universe_count = len(universe_rs)
        breadth = compute_market_breadth(breadth_samples)
        stats.regime = apply_breadth_to_regime(stats.regime, breadth)
        log.info(
            "run.market_breadth",
            state=breadth.get("state"),
            sample_size=breadth.get("sample_size"),
            pct_above_sma50=breadth.get("pct_above_sma50"),
            pct_above_sma200=breadth.get("pct_above_sma200"),
            advancers_pct=breadth.get("advancers_pct"),
            low_confidence=breadth.get("low_confidence"),
        )
        # Guard: a tiny RS universe makes the percentile rank meaningless. Below
        # this floor we leave rs_rating None (scorer uses the absolute fallback,
        # the RS-rating gate is skipped) rather than emit misleading 1-99 ratings.
        rs_min_universe = self.settings.scoring.rs_rating_min_universe
        valid_rs_universe = sum(1 for v in universe_rs if v is not None and v == v)
        stats.rs_rating_low_confidence = valid_rs_universe < max(1, rs_min_universe)
        if stats.rs_rating_low_confidence:
            log.warning(
                "run.rs_rating_low_confidence",
                rs_universe_count=valid_rs_universe,
                min_required=rs_min_universe,
            )

        # Inject universe-relative RS rating into every stage row + tally the
        # full-universe stage distribution.
        for sr in stage_rows:
            sr["rs_rating"] = percentile_of(
                sr.pop("rs_score", None), universe_rs, min_population=rs_min_universe
            )
            key = f"stage_{sr.get('stage')}"
            stats.stage_counts[key] = stats.stage_counts.get(key, 0) + 1
            # History-context completeness: a stage row without Mansfield RS
            # means too-short weekly history or no benchmark overlap.
            if sr.get("mansfield_rs") is None:
                stats.tickers_missing_mansfield_rs += 1
        log.info("run.stages", counts=stats.stage_counts, classified=len(stage_rows))

        # 2.5) Cross-sectional RS Rating + deferred scoring -------------------
        # IBD-style: each candidate's rs_rating is its percentile within the
        # whole fetched universe's RS distribution. Inject into features, then
        # score (cheap, single-threaded over the surviving candidates).
        regime_mult = float(stats.regime.get("score_multiplier", 1.0)) if stats.regime else 1.0
        regime_state = stats.regime.get("state", "unknown") if stats.regime else "unknown"
        candidates: list[Candidate] = []
        for p in pendings:
            cand_rs = p.features.get("rs_score_6m")
            if cand_rs is None:
                cand_rs = p.features.get("rs_score_3m")
            p.features["rs_rating"] = percentile_of(
                cand_rs, universe_rs, min_population=rs_min_universe
            )
            # Canonical RS-rating floor for RS-dependent setups (Minervini
            # criterion #8, HighRS min_rs_percentile) — the universe-relative
            # percentile only exists now, so it's enforced post-pass.
            apply_rs_rating_gate(
                p.results, p.features["rs_rating"], self.settings.setups
            )
            cand = self.scorer.score(
                ticker=p.ticker,
                features=p.features,
                setups=p.results,
                provider_used=p.provider_used,
                fallback_triggered=p.used_fallback,
                sector=p.sector,
                industry=p.industry,
                m_and_a_confidence=p.ma_conf,
                m_and_a_price_pin=p.ma_price_pin,
            )
            # Soft market-regime lean (never excludes; just nudges ranking).
            if regime_mult != 1.0:
                cand.score = round(cand.score * regime_mult, 1)
                cand.reasons.append(
                    Reason(
                        text=f"market_regime_{regime_state}(x{regime_mult:.2f})",
                        weight=regime_mult,
                        category="regime",
                    )
                )
            candidates.append(cand)
            for setup_name, summary in cand.setups.items():
                if summary.triggered:
                    stats.setup_triggered_counts[setup_name] = (
                        stats.setup_triggered_counts.get(setup_name, 0) + 1
                    )
            bk = cand.actionability or "watch"
            stats.actionability_counts[bk] = stats.actionability_counts.get(bk, 0) + 1
            if cand.m_and_a_confidence == "high":
                stats.m_and_a_excluded_count += 1

        # --- Sector/industry enrichment (powers the GICS group RS leaderboard) -
        # FMP bulk sectors need a paid plan, so backfill from yfinance profiles
        # (cached, bounded per run). Then stamp sector/industry onto stage rows +
        # candidates so the groups view and AI ranker have peer-group context.
        self._enrich_sectors(stage_rows, candidates)

        # --- Compute coverage + data-status gate -------------------------------
        fetched_any = (
            stats.tickers_fetched_local
            + stats.tickers_fetched_primary
            + stats.tickers_fetched_fallback
            + stats.tickers_fetched_tertiary
            + stats.tickers_fetched_deep_history
        )
        denom = max(1, len(work_list))
        stats.coverage_pct = round(100.0 * fetched_any / denom, 2)
        # History-context completeness: fetched tickers that could NOT be given a
        # Weinstein stage (too-short weekly history for the 30w SMA + slope).
        stats.tickers_insufficient_weekly_history = max(0, fetched_any - len(stage_rows))
        # Threshold currently lives in `prefilter.min_history_days`-equivalent
        # default; expose as a top-level field once configurable.
        min_coverage = 90.0
        if stats.coverage_pct < 50.0:
            stats.data_status = "FAILED"
        elif stats.coverage_pct < min_coverage:
            stats.data_status = "PARTIAL"
        else:
            stats.data_status = "OK"
        log.info(
            "run.coverage",
            coverage_pct=stats.coverage_pct,
            data_status=stats.data_status,
            fetched_local=stats.tickers_fetched_local,
            fetched_primary=stats.tickers_fetched_primary,
            fetched_fallback=stats.tickers_fetched_fallback,
            fetched_tertiary=stats.tickers_fetched_tertiary,
            failed=stats.tickers_failed_all_providers,
        )
        _status_tone = {"OK": "green", "PARTIAL": "yellow", "FAILED": "red"}.get(stats.data_status, "white")
        console.rule(f"[bold]Scan complete[/] · data [{_status_tone}]{stats.data_status}[/]")
        console.print(
            f"  coverage [{_status_tone}]{stats.coverage_pct}%[/]"
            f" · fetched {fetched_any}/{len(work_list)}"
            f" (local {stats.tickers_fetched_local} · primary {stats.tickers_fetched_primary} · fallback {stats.tickers_fetched_fallback}"
            f" · tertiary {stats.tickers_fetched_tertiary} · deep {stats.tickers_fetched_deep_history})"
            f" · [red]{stats.tickers_failed_all_providers} failed[/]"
        )
        console.print(
            f"  candidates {len(candidates)}"
            f" · short weekly history {stats.tickers_insufficient_weekly_history}"
            f" · scan {stats.timings_seconds.get('scan', '?')}s"
        )

        # 3) Split candidates by actionability bucket
        actionable_set = {"actionable_now", "near_actionable", "forming", "watch"}
        actionable_list = [c for c in candidates if c.actionability in actionable_set]
        excluded_list = [c for c in candidates if c.actionability not in actionable_set]
        actionable_list.sort(key=lambda c: c.score, reverse=True)
        excluded_list.sort(key=lambda c: c.score, reverse=True)

        return RunResult(
            candidates=actionable_list,
            excluded=excluded_list,
            stats=stats,
            stage_rows=stage_rows,
        )

    # ---- sector / industry enrichment ----------------------------------------

    def _enrich_sectors(self, stage_rows: list[dict], candidates: list) -> None:
        """Backfill sector/industry (yfinance profiles, cached) and stamp it onto
        stage rows + candidates so the GICS group RS leaderboard has data."""
        cfg = self.settings.fetch
        # Tickers that feed the groups view: the full classified universe plus
        # any candidate not represented there.
        tickers = [str(sr.get("ticker") or "") for sr in stage_rows]
        tickers += [c.ticker for c in candidates]
        tickers = [t for t in tickers if t]

        if not self.settings.marketdata.scan_provider_fallback:
            log.info("sector_enrich.skipped", reason="local_data_only")
        elif cfg.sector_enrich_from_profiles and self._profile_provider is not None:
            missing = [t for t in tickers if not (self.sector_map.get(t.upper()))]
            if missing:
                try:
                    enriched = build_sector_cache_from_profiles(
                        tickers,
                        self.sector_path,
                        self._profile_provider,
                        max_new=cfg.sector_enrich_max_new_per_run,
                    )
                    self.sector_map.update(enriched)
                except Exception as e:  # noqa: BLE001
                    log.warning("sector_enrich.failed", error=repr(e))

        if not self.sector_map:
            return
        for sr in stage_rows:
            entry = self.sector_map.get(str(sr.get("ticker") or "").upper())
            if entry is not None:
                if not sr.get("sector"):
                    sr["sector"] = entry.sector
                if not sr.get("industry"):
                    sr["industry"] = entry.industry
        for c in candidates:
            entry = self.sector_map.get(c.ticker.upper())
            if entry is not None:
                if not c.sector:
                    c.sector = entry.sector
                if not c.industry:
                    c.industry = entry.industry

    # ---- per-ticker -----------------------------------------------------------

    def _stage_row(
        self,
        ticker: str,
        weekly: pd.DataFrame,
        benchmark_weekly: pd.Series | None,
    ) -> dict | None:
        """Weinstein stage classification for the WHOLE universe (any ticker
        with enough weekly history), independent of prefilter / setups."""
        if weekly is None or weekly.empty:
            return None
        try:
            sr = classify_stage(
                weekly["close"],
                weekly["volume"] if "volume" in weekly.columns else None,
                benchmark_weekly,
                self.settings.stage_analysis,
            )
        except Exception as e:  # noqa: BLE001
            # A single ticker failing stage classification must not abort the
            # universe pass, but swallowing it blind hid real bugs (e.g. a column
            # rename). Log at debug with the cause so it's diagnosable.
            log.debug("stage_row.error", ticker=ticker, error=repr(e))
            return None
        if sr is None:
            return None
        sr["ticker"] = ticker
        return sr

    def _process_one(
        self,
        ticker: str,
        start: date,
        end: date,
        benchmark_close: pd.Series,
        benchmark_weekly: pd.Series | None = None,
    ) -> tuple[float | None, "_PendingScore | None", dict | None, dict | None, PrefilterResult, str]:
        """Fetch + enrich + prefilter + detect setups. Scoring is DEFERRED to
        the caller (so a universe-relative RS rating can be injected first).

        Returns (rs_score, pending, stage_row, breadth_sample, prefilter_result, source):
          - rs_score: the ticker's RS-vs-SPY score (6m else 3m) for the
            cross-sectional distribution; None if features couldn't be computed.
          - pending: scoring inputs if the ticker passed prefilter + has a
            triggered setup; otherwise None.
          - stage_row: full-universe Weinstein stage dict (any ticker with
            enough weekly history), or None.
          - breadth_sample: one participation sample for the market-regime
            breadth aggregate, or None when daily features are unavailable.
        """
        daily, weekly, provider_used, source = self._fetch_with_fallback(ticker, start, end)
        used_fallback = source in ("fallback", "tertiary")

        # Stale-data gate: if the most recent daily bar lags the run's as-of
        # date by more than the allowed budget (delisted / halted / cache-stale),
        # the price action is not current — don't score OR stage-classify it.
        max_lag = self.settings.prefilter.max_data_lag_days
        if not daily.empty and max_lag and max_lag > 0:
            try:
                last_bar_date = pd.Timestamp(daily.index[-1]).date()
            except Exception:  # noqa: BLE001
                last_bar_date = None
            if last_bar_date is not None and (end - last_bar_date).days > max_lag:
                return (
                    None,
                    None,
                    None,
                    None,
                    PrefilterResult(passed=False, failed_conditions=["stale_data"]),
                    source,
                )

        # Stage classification needs only weekly bars — compute for EVERY ticker.
        stage_row = self._stage_row(ticker, weekly, benchmark_weekly)
        if daily.empty or len(daily) < self.settings.prefilter.min_history_days:
            return None, None, stage_row, None, PrefilterResult(passed=False, failed_conditions=["insufficient_history"]), source

        enriched = compute_indicators(daily, benchmark_close, self.settings.setups.minervini.sma200_rising_lookback_days)
        features = latest_features(enriched)
        breadth_sample = _breadth_sample(features, daily)
        if self.market_store is not None:
            try:
                self.market_store.upsert_feature_snapshot(ticker, end, features)
            except Exception as e:  # noqa: BLE001
                log.debug("feature_snapshot.write_failed", ticker=ticker, error=repr(e))
        # Make the real symbol available to detectors (e.g. tight_breakout's
        # M&A price check), which otherwise never receive the ticker.
        features["ticker"] = ticker
        # RS score for the universe distribution. Prefer the IBD-style weighted
        # multi-timeframe RS; fall back to 6m then 3m on short history.
        rs_score = features.get("rs_score_weighted")
        if rs_score is None:
            rs_score = features.get("rs_score_6m")
        if rs_score is None:
            rs_score = features.get("rs_score_3m")
        sector_entry = self.sector_map.get(ticker.upper())
        # Enrich stage row with daily-derived context for the Stages table.
        if stage_row is not None:
            stage_row["rs_score"] = rs_score
            stage_row["dist_52w_high"] = features.get("distance_to_52w_high_pct")
            stage_row["dollar_vol"] = features.get("avg_dollar_volume_50d")
            stage_row["sector"] = sector_entry.sector if sector_entry else None
            stage_row["industry"] = sector_entry.industry if sector_entry else None
            for key in (
                "stage", "substage", "stage_quality_score", "stage_trade_bias",
                "stage_range_state", "stage_origin", "short_quality_score", "ext_pct",
                "volume_confirms", "range_104w_pos", "range_156w_pos", "range_260w_pos",
                "range_520w_pos", "support_zone_low", "resistance_zone_high",
            ):
                features[f"weinstein_{key}"] = stage_row.get(key)
            for key in (
                "long_term_context", "secular_recovery_score", "near_secular_resistance",
                "long_term_drawdown_pct", "long_term_recovery_from_low_pct",
                "long_term_vs_prior_high_pct",
            ):
                features[key] = stage_row.get(key)
            # Mansfield RS (weekly, from the stage classifier) — surfaced on the
            # candidate so the screener can show the Weinstein leadership read.
            features["mansfield_rs"] = stage_row.get("mansfield_rs")

        pre = prefilter(features, self.settings.prefilter)
        if not pre.passed:
            return rs_score, None, stage_row, breadth_sample, pre, source

        # Run all setup detectors
        results = [d.detect(daily, weekly, features) for d in self.detectors]
        if stage_row is not None:
            crash_result = next((r for r in results if r.setup_name == "crash_base_stage1"), None)
            if crash_result is not None and crash_result.raw_features:
                raw = crash_result.raw_features
                for key in (
                    "crash_base_score",
                    "crash_base_phase",
                    "drawdown_5y_pct",
                    "base_age_weeks",
                    "resistance_attempt_count",
                    "trendline_attempt_count",
                    "trendline_breakout_5y",
                    "weekly_breakout_rvol",
                    "daily_rvol_headsup",
                    "special_alert_level",
                ):
                    stage_row[key] = raw.get(key)
        # Require at least ONE triggered setup to make the candidate list
        if not any(r.triggered for r in results):
            return rs_score, None, stage_row, breadth_sample, PrefilterResult(passed=False, failed_conditions=["no_setup_triggered"]), source

        # Centralised M&A pre-check: applies to ALL detectors, not just tight.
        # HIGH confidence → exclude (deal-locked price action is not tradeable).
        # MEDIUM → warning only (price coincidentally flat; user can still trade).
        # LOW / NONE → ignore.
        try:
            from stock_scout.data.corporate_actions import (
                combine_signals,
                detect_m_and_a_from_price,
                detect_target_keywords,
                fetch_news_items,
            )

            ma = detect_m_and_a_from_price(
                daily, ticker=ticker, spy_returns=benchmark_close.pct_change()
            )
            # News is what separates "being acquired" from "merely dormant", and
            # price cannot. A utility or a mortgage REIT trades in a 1% band all
            # year and looks identical to a locked deal - on a recent screen the
            # pin fired on 36 names, of which 27 were simply low-volatility and
            # belonged in the results.
            #
            # Only pinned names get a lookup, so this is a handful of reads a
            # night rather than one per candidate. `price_pin` is the gate on
            # purpose and `medium` is not: the looser price signals fire on any
            # stock that had a 10% day in the past year, and pairing one of
            # those with a stray keyword would exclude healthy candidates.
            if "price_pin" in ma.price_signals:
                company = (
                    self.market_store.security_name(ticker)
                    if self.market_store is not None
                    else None
                )
                keywords = detect_target_keywords(
                    fetch_news_items(ticker, company=company), company, ticker
                )
                ma = combine_signals(ma, keywords)
            ma_conf = ma.confidence
            ma_price_pin = "price_pin" in ma.price_signals
        except Exception as e:  # noqa: BLE001
            log.debug("m_and_a.detect_failed", ticker=ticker, error=repr(e))
            ma_conf = "none"
            ma_price_pin = False

        pending = _PendingScore(
            ticker=ticker,
            features=features,
            results=results,
            provider_used=provider_used,
            used_fallback=used_fallback,
            sector=sector_entry.sector if sector_entry else None,
            industry=sector_entry.industry if sector_entry else None,
            ma_conf=ma_conf,
            ma_price_pin=ma_price_pin,
        )
        return rs_score, pending, stage_row, breadth_sample, pre, source
