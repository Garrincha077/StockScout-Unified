from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["yfinance", "ibkr", "alpaca", "tiingo", "fmp", "csv", "stooq", "twelvedata"]


class ProvidersConfig(BaseModel):
    primary_data_provider: ProviderName = "yfinance"
    secondary_validation_provider: ProviderName | None = "alpaca"
    broker_provider: ProviderName | None = "ibkr"
    fallback_provider: ProviderName | None = "alpaca"
    tertiary_fallback_provider: ProviderName | None = "tiingo"
    sector_provider: ProviderName | None = "fmp"
    # Keyless deep-history backfill (Stooq). Appended as the LAST link in the
    # daily + weekly fallback chains so that when other providers cap history
    # or return empty, long EOD history is still available. None disables.
    deep_history_provider: ProviderName | None = "stooq"


class IBKRConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 17
    use_ib_gateway: bool = False
    market_data_type: Literal["live", "delayed", "frozen", "delayed_frozen"] = "delayed"
    max_requests_per_minute: int = 50


class AlpacaConfig(BaseModel):
    enabled: bool = False
    base_url: str = "https://paper-api.alpaca.markets"
    data_feed: Literal["iex", "sip"] = "iex"
    paper_trading: bool = True
    # Per-request wall-clock timeout (seconds). alpaca-py exposes no native
    # timeout, so the provider enforces this by running each bars call on a
    # worker thread and abandoning it past this budget. 0 disables the guard.
    request_timeout_seconds: float = 20.0


class TiingoConfig(BaseModel):
    enabled: bool = False


class TwelveDataConfig(BaseModel):
    """Twelve Data REST adapter (free tier: 800 calls/day, 8/min). Used as an
    extra cross-validation source for the median-of-providers consensus."""

    enabled: bool = False
    # Free tier is rate-limited (8 req/min); keep batches conservative.
    max_requests_per_minute: int = 8
    request_timeout_seconds: float = 20.0


class FMPConfig(BaseModel):
    enabled: bool = False
    # FMP HTTP calls had timeouts but no retry; a transient 5xx failed instantly.
    retry_attempts: int = 3
    retry_backoff_seconds: float = 2.0


class FetchConfig(BaseModel):
    """Per-run fetch robustness knobs (orchestrator)."""

    # Wall-clock budget per ticker. A pathological ticker that stacks provider
    # retries is counted as failed and skipped rather than blocking the scan.
    per_ticker_timeout_seconds: float = 90.0
    # Provider circuit-breaker: if the active primary's empty/error rate exceeds
    # `circuit_breaker_empty_pct` over at least `circuit_breaker_min_samples`
    # tickers, switch the active primary to the next healthy provider.
    circuit_breaker_empty_pct: float = 0.8
    circuit_breaker_min_samples: int = 50
    # Run a provider health pre-check before the per-ticker loop and promote a
    # healthy fallback to primary if the configured primary is down.
    health_precheck: bool = True
    # Sector/industry enrichment fallback. The FMP bulk sector endpoint requires
    # a paid plan (free tier → HTTP 402), so when the sector cache is missing
    # entries we backfill sector/industry per-ticker from yfinance company
    # profiles, persisted to data/sector_cache.parquet (sectors are ~static, so
    # each ticker is fetched at most once). Bounded per run so it never stalls a
    # scan — the cache fills in across runs. Set max_new to 0 to disable.
    sector_enrich_from_profiles: bool = True
    sector_enrich_max_new_per_run: int = 400


class YFinanceConfig(BaseModel):
    enabled: bool = True
    max_workers: int = 4
    batch_size: int = 50
    retry_attempts: int = 3
    retry_backoff_seconds: float = 2.0
    # Token-bucket: max sustained requests per second across all worker threads.
    # Yahoo's anti-bot rate-limits hard above ~3/s for unauthenticated traffic.
    requests_per_second: float = 2.0
    # When a "Too Many Requests" reply is detected, wait this many seconds
    # (plus uniform random jitter up to half this value) before retrying.
    rate_limit_cooldown_seconds: float = 60.0
    # If this many consecutive rate-limit replies happen, pause the whole pool.
    rate_limit_pool_pause_threshold: int = 10
    rate_limit_pool_pause_seconds: float = 300.0
    # Try to use curl_cffi.requests.Session(impersonate="chrome") to dodge
    # the bot fingerprint that Yahoo flags. Falls back silently if curl_cffi
    # is not installed.
    use_curl_cffi_session: bool = True
    # Hard per-request network timeout (seconds). Without it a stalled socket
    # blocks a ThreadPoolExecutor worker indefinitely, freezing the scan.
    request_timeout_seconds: float = 30.0


class UniverseConfig(BaseModel):
    include_nyse: bool = True
    include_nasdaq: bool = True
    include_amex: bool = True
    include_adr: bool = True
    exclude_etf: bool = True
    exclude_otc: bool = True
    exclude_warrants: bool = True
    exclude_units: bool = True
    exclude_preferred: bool = True
    exclude_rights: bool = True
    exclude_test_issues: bool = True
    manual_overrides_file: str = "config/universe_overrides.yaml"


class CacheConfig(BaseModel):
    base_dir: str = "data/cache"
    daily_history_years: int = 5
    # Weekly history drives Weinstein context (30w SMA slope/origin, Mansfield
    # RS over 52w) and multi-year GLB green lines. 10y of keyless Stooq weekly
    # is free; deeper history makes the Stage 1↔3 origin + long bases reliable.
    weekly_history_years: int = 10
    force_refresh_on_gap_pct: float = 30.0
    # If a parquet's last_updated metadata is older than this many days, do a
    # full refetch on next access (catches weekend gaps, delisted tickers,
    # provider outages that left us with stale data).
    max_staleness_days: int = 14


class MarketDataConfig(BaseModel):
    """Local DuckDB market-data store used by charts and scanner reads."""

    enabled: bool = True
    base_dir: str = "data/market"
    duckdb_file: str = "market.duckdb"
    scan_local_first: bool = True
    # Keep the provider fallback on by default so existing ad-hoc scans still
    # work before the local store is fully bootstrapped. The after-close
    # automation runs with --local-data-only once marketdata update has run.
    scan_provider_fallback: bool = True
    chart_provider_backfill: bool = False
    # Which price basis the store holds. The Stooq archive is split-adjusted
    # only; yfinance with auto_adjust=False matches it, auto_adjust=True does
    # not. Mixing the two inside a ticker fabricates a 10-36% gap for dividend
    # payers at the splice point, so every fetch path reads this one setting.
    price_basis: Literal["split_only", "split_div"] = "split_only"
    # How the nightly refresh gets new bars. "archive" re-imports the Stooq
    # dump, "network" pulls a delta from the providers, "auto" imports the dump
    # when it is newer than the store and then fills the remaining days from the
    # network. Both sources share the split-only basis, so auto never splices.
    refresh_mode: Literal["archive", "network", "auto"] = "auto"
    # Which tickers the network delta covers. "eligible" is the set the scan
    # actually reads (plus benchmarks and IPO seeds); "scan" is the full built
    # universe, most of which nothing downstream consumes. The archive import
    # still rewrites full history for everyone, so narrowing this costs no
    # coverage — it just stops the delta running out of budget before it
    # reaches the tickers that matter.
    refresh_scope: Literal["eligible", "scan", "common"] = "eligible"
    stooq_archive_dir: str = "DATA_Stooq"
    # Where Stooq's one-day, all-symbols bundles are dropped. Scanned
    # non-recursively; files are selected by content (.US rows at PER=D), never
    # by name, because the world and intraday bundles share the naming shape.
    # The first entry is where Stooq's own extract puts them.
    stooq_bundle_dirs: list[str] = Field(
        default_factory=lambda: [
            "DATA_Stooq/d_us_txt/data/daily/us",
            "DATA_Stooq/bundles",
        ]
    )
    # Beyond this the archive is treated as stale: still usable with --force,
    # but readiness says so rather than letting depth quietly stop growing.
    archive_max_age_days: int = 7
    benchmarks: list[str] = Field(
        default_factory=lambda: [
            "SPY",
            "QQQ",
            "IWM",
            "RSP",
            "XLK",
            "XLF",
            "XLE",
            "XLV",
            "XLI",
            "XLY",
            "XLP",
            "XLU",
            "XLB",
            "XLRE",
            "XLC",
        ]
    )


class PrefilterConfig(BaseModel):
    min_price: float = 5.0
    # Accumulation screens may include liquid near/sub-$5 names whose basing
    # footprints matter before conventional momentum filters wake up. This
    # relaxes only the hard price gate; liquidity/history still remain hard.
    accumulation_min_price: float = 3.0
    accumulation_min_avg_dollar_volume_50d: float = 15_000_000
    min_avg_volume_50d: int = 300_000
    min_avg_dollar_volume_50d: float = 5_000_000
    # 252, not 250: a full 52-week window. Below it the 52-week high/low come
    # back NaN, and several screens gate on distance_to_52w_high_pct.
    min_history_days: int = 252
    # Drop a ticker whose most recent daily bar lags the run's as-of date by
    # more than this many CALENDAR days (delisted / halted / cache-stale data
    # must not be scored as if it were current). Default tolerates a normal
    # week + a holiday gap while still catching weeks/months-old series. Set
    # to 0 (or negative) to disable the gate.
    max_data_lag_days: int = 10
    # Trend gate mode. "hard" = legacy behaviour: a stock below SMA50/150/200,
    # too far from its 52w high, or with weak RS is EXCLUDED before detectors
    # run. "soft" (default) = those trend conditions become non-fatal flags so
    # early setups in formation (Weinstein Stage 1->2, basing, GLB pre-breakout)
    # can surface and be RANKED rather than hidden. Liquidity/price/history
    # gates below always remain hard. The "require >=1 triggered setup" gate in
    # the orchestrator still keeps the candidate list from exploding.
    trend_gate: Literal["soft", "hard"] = "soft"
    require_close_above_sma50: bool = True
    require_close_above_sma150: bool = True
    require_close_above_sma200: bool = True
    max_distance_to_52w_high_pct: float = 30.0
    min_rs_vs_spy_3m: float = 0.0
    # Days an excluded ticker stays in the negative-cache before being rechecked.
    # 7 lets recovering microcaps re-enter quickly; 30 was too long (prices move).
    negative_cache_recheck_after_days: int = 7


class GLBSetupConfig(BaseModel):
    enabled: bool = True
    min_months_without_new_high: int = 3
    max_distance_below_glb_pct: float = 3.0
    min_volume_ratio_for_breakout: float = 1.5
    require_above_sma50: bool = True
    require_above_sma200: bool = True
    # Wish: a GLB is only legitimate if the level was contested. Count bars
    # whose high reached within `resistance_tolerance_pct` of the GLB and the
    # close stayed below — at least `min_resistance_touches` such bars are
    # required for the level to be considered tested.
    min_resistance_touches: int = 2
    resistance_tolerance_pct: float = 1.0
    # Require non-increasing contractions on the base. NOTE: this is a
    # Minervini-VCP rigor, NOT part of Eric Wish's canonical GLB (which only
    # needs a long-held horizontal high + sideways base + breakout on volume).
    # As a HARD gate it suppressed GLB almost entirely (measured: ~3/800 vs
    # ~141/800 with it off), so it defaults OFF. Base looseness still flows
    # into ranking via `is_wide_and_loose` (loose bases land in watch, not
    # actionable) — ranking, not exclusion.
    require_narrowing_contractions: bool = False


class MinerviniSetupConfig(BaseModel):
    enabled: bool = True
    require_150_over_200: bool = True
    sma200_rising_lookback_days: int = 20
    min_pct_above_52w_low: float = 30.0
    max_pct_below_52w_high: float = 25.0
    min_rs_vs_spy_6m: float = 0.0
    # Canonical Trend Template criterion #8: RS Rating (universe-relative
    # percentile, 1–99) must be >= 70 (Minervini: "ideally 90+"). Enforced in
    # the universe pass (setups/rs_gate.py) because the percentile isn't known
    # until the whole universe has been ranked. Sub-threshold names are demoted
    # to `watch` rather than dropped (ranking, not exclusion).
    min_rs_rating: float = 70.0
    # Minervini "successively tighter" VCP rule — each contraction <= 67% of
    # the previous one (i.e. at least 1/3 smaller). 1.0 = "non-increasing"
    # (older loose rule). 0.5 = strict knjiški (rarely matches real bases).
    vcp_contraction_ratio_threshold: float = 0.67


class TightBreakoutSetupConfig(BaseModel):
    enabled: bool = True
    tight_range_days_min: int = 5
    tight_range_days_max: int = 15
    # Minervini visual standard: recent volatility moderately compressed.
    # 1.0 means "no contraction required" (the old broken default).
    # Below 0.65 is too strict and filters out valid bases.
    atr_contraction_ratio: float = 0.85
    volume_dryup_ratio: float = 0.8
    max_distance_below_pivot_pct: float = 5.0


class WeinsteinSetupConfig(BaseModel):
    enabled: bool = True
    weekly_sma_period: int = 30
    sma_rising_lookback_weeks: int = 4
    # Industry standard for "extended Stage 2": >30% above the 30-week SMA.
    # Weinstein's own books don't quote a number; 20-25% would cut out many
    # mid-cycle Stage-2 stocks that are still tradeable on pullback.
    max_extension_from_30w_sma_pct: float = 30.0
    # Stage 1->2 transition window: max weeks since price reclaimed the 30w SMA
    # for the move to still count as an EARLY transition entry (not yet
    # established Stage 2). Weinstein's "buy as it crosses into Stage 2".
    transition_max_weeks: int = 6
    # Minimum weekly volume vs 10w avg to confirm demand on the transition.
    transition_min_vol_ratio: float = 1.3
    # `min_promote_slope_pct` was here from 17c3fd8 until 2026-08-01. Removed
    # rather than set to a disabling value, so nothing can quietly re-enable a
    # rule that lost: the promoted cohort scored +2.42 held out, and taking the
    # same number of weinstein's own signals per date by `rs_rating` instead
    # scored +7.62. See setups/weinstein.py for the full reason.


class HighRSSetupConfig(BaseModel):
    enabled: bool = True
    max_distance_to_52w_high_pct: float = 10.0
    min_rs_percentile: float = 70.0
    # Vertical run cap — above this % above SMA50, stock is too extended for
    # fresh entry (O'Neil / Minervini consensus).
    max_extension_above_sma50_pct: float = 25.0


class EMACrossSetupConfig(BaseModel):
    """10/20 EMA bullish cross — Stamatoudis-style stage-transition signal.

    Detects EMA10 crossing UP through EMA20 in the last `fresh_within_bars`
    days. Bonus state when the same cross also exists on the weekly bars.
    """

    enabled: bool = True
    # 5 trading days = 1 week. Stamatoudis/Bonde standard for "fresh" entry.
    # Below 3 is too tight (misses crosses you noticed Monday after Friday close);
    # above 7 starts including extended Stage 2 names that already ran.
    fresh_within_bars: int = 5
    weekly_fresh_within_bars: int = 2
    require_above_sma50: bool = True
    require_positive_rs_3m: bool = True
    # Qullamaggie standard: RS_3m >= 5 means clearly above-average momentum,
    # not just "above zero". Used only for the actionable_now bucket; lower
    # values still register as near_actionable.
    min_rs_3m_for_actionable: float = 5.0


class GuppySetupConfig(BaseModel):
    """Eric Wish GMMA/RWB setup: short EMAs crossing or tightening vs long EMAs."""

    enabled: bool = True
    fresh_rwb_cross_bars: int = 10
    tight_spread_pct: float = 2.5
    max_short_group_width_pct: float = 3.5
    max_long_group_width_pct: float = 4.0
    min_rlc: int = 4
    max_extension_above_long_group_pct: float = 18.0
    require_positive_rs_3m: bool = False


class RWBSqueezeThrustSetupConfig(BaseModel):
    """Weekly RWB/GMMA band squeeze followed by a high-volume price thrust."""

    enabled: bool = True
    tight_band_width_pct: float = 12.0
    max_short_group_width_pct: float = 6.0
    max_long_group_width_pct: float = 7.0
    max_abs_rwb_spread_pct_for_squeeze: float = 5.0
    min_thrust_rel_volume: float = 1.5
    min_close_location: float = 0.55
    flat_30w_slope_min_pct: float = -0.75
    max_extension_above_band_pct: float = 35.0
    prior_attempt_lookback_weeks: int = 52


class EMAStackLaunchSetupConfig(BaseModel):
    """Weekly EMA stack coil/ignition ranking for AEVA/CVNA-style launch moves."""

    enabled: bool = True
    recent_coil_lookback_weeks: int = 16
    max_stack_width_pct: float = 22.0
    max_short_group_width_pct: float = 8.0
    max_long_group_width_pct: float = 18.0
    ideal_long_group_width_pct: float = 7.0
    min_thrust_rel_volume: float = 1.5
    strong_thrust_rel_volume: float = 2.0
    min_close_location: float = 0.55
    flat_30w_slope_min_pct: float = -1.5
    max_extension_above_stack_pct: float = 65.0
    min_watch_score: float = 38.0
    min_thrust_score: float = 45.0
    # The cell that cleared the grid pre-registered in
    # docs/prereg/2026-08-01-four-detector-grids.md: +3.23 held out against this
    # detector's own unfiltered +0.03, and +2.52 ahead of taking the same
    # names-per-date by `rs_rating` from its own signals. Meeting all three
    # moves the signal up one rung. Quartiles of each feature's own
    # distribution, not round numbers, so they are not a lattice artefact.
    max_promote_stack_width_pct: float = 3.62
    min_promote_pressure_score: float = 53.3
    min_promote_30w_slope_pct: float = 2.92


class AccumulationSetupConfig(BaseModel):
    """Institutional accumulation base: demand footprints before breakout."""

    enabled: bool = True
    min_base_length_bars: int = 45
    max_base_depth_pct: float = 45.0
    min_support_volume_events: int = 2
    min_up_down_vol_ratio: float = 1.15
    min_volume_dryup_score: float = 35.0
    max_sma_compression_pct: float = 8.0
    max_distance_below_pivot_pct: float = 12.0
    breakout_volume_ratio: float = 1.4
    high_quality_score: float = 70.0


class LongBaseLaunchSetupConfig(BaseModel):
    """Long weekly/monthly base launch with demand spikes and quiet pullbacks."""

    enabled: bool = True
    min_weekly_base_bars: int = 20
    high_quality_weekly_base_bars: int = 40
    max_weekly_base_depth_pct: float = 60.0
    secular_monthly_min_bars: int = 12
    secular_max_base_depth_pct: float = 85.0
    demand_volume_mult: float = 1.5
    low_volume_pullback_mult: float = 0.85
    dryup_volume_mult: float = 0.75
    launch_volume_mult: float = 1.3
    min_demand_spikes: int = 2
    min_low_volume_pullbacks: int = 2
    min_demand_supply_ratio: float = 1.05
    max_distance_below_pivot_pct: float = 15.0
    max_extension_above_pivot_pct: float = 35.0
    max_extension_above_long_group_pct: float = 30.0
    high_quality_score: float = 70.0


class CrashBaseStage1SetupConfig(BaseModel):
    """Multi-year post-crash Stage 1 watch with RVOL and 5y trendline alerts."""

    enabled: bool = True
    lookback_weeks: int = 260
    min_weekly_history_weeks: int = 80
    min_drawdown_pct: float = 60.0
    high_quality_drawdown_pct: float = 75.0
    min_base_age_weeks: int = 52
    high_quality_base_weeks: int = 104
    very_long_base_weeks: int = 156
    ma_slope_lookback_weeks: int = 5
    min_30w_slope_pct: float = -1.5
    max_below_30w_reclaim_pct: float = 15.0
    recovery_lookback_weeks: int = 10
    min_recovery_price_slope_pct: float = 3.0
    min_range_position: float = 0.25
    weekly_rvol_window: int = 30
    daily_rvol_window: int = 50
    high_rvol_accumulation_mult: float = 1.5
    daily_headsup_rvol: float = 1.5
    min_close_location: float = 0.55
    low_pullback_rvol: float = 0.85
    low_pullback_max_weeks: int = 6
    max_distance_below_resistance_pct: float = 10.0
    resistance_tolerance_pct: float = 3.0
    resistance_break_buffer_pct: float = 1.0
    resistance_exclude_recent_weeks: int = 4
    min_attempt_gap_weeks: int = 4
    max_attempt_bonus_count: int = 5
    trendline_touch_tolerance_pct: float = 3.0
    trendline_break_buffer_pct: float = 1.0
    trendline_breakout_rvol: float = 1.5
    trendline_min_anchor_separation_weeks: int = 12
    trendline_min_anchor_drop_pct: float = 2.0
    min_watch_score: float = 45.0


class MAClusterVolumeBreakoutSetupConfig(BaseModel):
    """Daily EMA10/20 + SMA50/150/200 compression and volume ignition."""

    enabled: bool = True
    very_tight_cluster_width_pct: float = 6.0
    tight_cluster_width_pct: float = 8.0
    max_watch_cluster_width_pct: float = 12.0
    min_mas_crossed: int = 4
    breakout_buffer_pct: float = 0.25
    prior_close_tolerance_pct: float = 0.75
    min_breakout_rel_volume: float = 1.5
    strong_breakout_rel_volume: float = 2.0
    min_watch_rel_volume: float = 1.2
    min_close_location: float = 0.65
    min_watch_close_location: float = 0.50
    max_breakout_age_bars: int = 3
    near_cluster_top_pct: float = 2.0
    max_extension_above_cluster_pct: float = 7.0
    sma200_slope_lookback_days: int = 20
    min_sma200_slope_pct: float = -1.0
    weekly_30w_slope_lookback_weeks: int = 5
    min_weekly_30w_slope_pct: float = -0.75
    weekly_structure_lookback_weeks: int = 20
    weekly_base_lookback_weeks: int = 8
    weekly_pivot_left_weeks: int = 2
    weekly_pivot_right_weeks: int = 1
    weekly_min_support_gap_pct: float = 1.5
    weekly_stop_buffer_pct: float = 0.5
    weekly_stop_atr_fraction: float = 0.15
    max_actionable_stop_distance_pct: float = 8.0
    max_watch_stop_distance_pct: float = 12.0
    min_trigger_score: float = 35.0


class SetupsConfig(BaseModel):
    glb: GLBSetupConfig = Field(default_factory=GLBSetupConfig)
    minervini: MinerviniSetupConfig = Field(default_factory=MinerviniSetupConfig)
    tight_breakout: TightBreakoutSetupConfig = Field(default_factory=TightBreakoutSetupConfig)
    weinstein: WeinsteinSetupConfig = Field(default_factory=WeinsteinSetupConfig)
    high_rs: HighRSSetupConfig = Field(default_factory=HighRSSetupConfig)
    ema_cross: EMACrossSetupConfig = Field(default_factory=EMACrossSetupConfig)
    guppy: GuppySetupConfig = Field(default_factory=GuppySetupConfig)
    rwb_squeeze_thrust: RWBSqueezeThrustSetupConfig = Field(default_factory=RWBSqueezeThrustSetupConfig)
    ema_stack_launch: EMAStackLaunchSetupConfig = Field(default_factory=EMAStackLaunchSetupConfig)
    ma_cluster_volume_breakout: MAClusterVolumeBreakoutSetupConfig = Field(
        default_factory=MAClusterVolumeBreakoutSetupConfig
    )
    accumulation: AccumulationSetupConfig = Field(default_factory=AccumulationSetupConfig)
    long_base_launch: LongBaseLaunchSetupConfig = Field(default_factory=LongBaseLaunchSetupConfig)
    crash_base_stage1: CrashBaseStage1SetupConfig = Field(default_factory=CrashBaseStage1SetupConfig)


class ScoringWeights(BaseModel):
    liquidity: float = 0.10
    trend: float = 0.25
    relative_strength: float = 0.20
    setup_quality: float = 0.25
    tightness: float = 0.10
    volume_confirmation: float = 0.10

    @field_validator("liquidity", "trend", "relative_strength", "setup_quality", "tightness", "volume_confirmation")
    @classmethod
    def _check_range(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise ValueError("Weight must be between 0 and 1")
        return v

    @model_validator(mode="after")
    def _check_total(self) -> "ScoringWeights":
        total = (
            self.liquidity
            + self.trend
            + self.relative_strength
            + self.setup_quality
            + self.tightness
            + self.volume_confirmation
        )
        if total > 1.000001:
            raise ValueError("Scoring weights must sum to 1.0 or less")
        return self


class ScoringConfig(BaseModel):
    weights: ScoringWeights = Field(default_factory=ScoringWeights)
    top_n_for_report: int = 20
    top_n_for_ai: int = 100
    # Minimum number of universe RS samples required before the IBD-style
    # universe-relative RS Rating (percentile) is trusted. Below this, a single
    # outlier distorts the percentile (e.g. 1 stock in a 3-name debug run = the
    # 100th percentile), so rs_rating is left None (the scorer then falls back to
    # the absolute RS-vs-SPY mapping and the RS-rating gate is skipped).
    rs_rating_min_universe: int = 30


class AIProviderEntry(BaseModel):
    """One link in the AI ranker fallback chain."""

    provider: Literal["claude", "openai", "mistral", "groq", "ollama"]
    model: str


class AIRankerConfig(BaseModel):
    enabled: bool = True
    provider: Literal["claude", "openai", "mistral", "groq", "ollama", "none"] = "groq"
    model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    temperature: float = 0.2
    max_retries: int = 1
    max_output_tokens: int = 3500
    # Keep the daily free-AI workflow fast: the deterministic pipeline builds
    # the full list, then the LLM judges only the highest-focus names.
    max_candidates: int = 8
    compact_input: bool = True
    include_raw_features: bool = False
    groq_timeout_seconds: float = 45.0
    mistral_timeout_seconds: float = 60.0
    fallback_to_deterministic: bool = True
    # Verdict cache: memoize a ranking by a hash of (chain + ranker input), so
    # re-running the same date with unchanged candidates/features returns the
    # cached verdicts for free instead of re-billing the LLM. Keyed on content,
    # so any data/feature change misses the cache and re-ranks.
    cache_enabled: bool = True
    # Local Ollama server URL (only used when provider="ollama").
    ollama_base_url: str = "http://localhost:11434/v1"
    # Ordered fallback chain: if the primary provider errors / hits quota /
    # runs out of balance / returns unparseable output, the ranker advances to
    # the next entry automatically. The primary (provider/model above) is tried
    # first; these run after it.
    fallback_chain: list[AIProviderEntry] = Field(
        default_factory=lambda: [
            AIProviderEntry(provider="groq", model="llama-3.3-70b-versatile"),
            AIProviderEntry(provider="mistral", model="mistral-small-latest"),
        ]
    )


class ValidationConfig(BaseModel):
    enabled: bool = True
    top_n_to_validate: int = 30
    # Legacy single-threshold (kept for backwards compat). New 2-tier:
    # `close_tolerance_warning_pct` and `close_tolerance_error_pct`.
    close_tolerance_pct: float = 1.0
    # New: 2-tier tolerance because Alpaca IEX vs Tiingo composite normally
    # diverge 0.5-2.5% — 1% was too tight, generated 5-9 warnings per run.
    close_tolerance_warning_pct: float = 2.0
    close_tolerance_error_pct: float = 5.0
    volume_tolerance_pct: float = 10.0
    # Extra providers queried for a MEDIAN-of-providers consensus. With >=3
    # sources the primary is judged against the consensus median, so a single
    # divergent feed no longer false-flags a candidate. Keyless/free only here.
    extra_validation_providers: list[ProviderName] = Field(default_factory=list)


class TradingConfig(BaseModel):
    enable_paper_trading: bool = False
    enable_live_trading: bool = False
    require_manual_approval: bool = True
    max_position_size_pct: float = 5.0
    max_risk_per_trade_pct: float = 1.0
    max_daily_new_positions: int = 3
    max_total_exposure_pct: float = 60.0


class ReportsConfig(BaseModel):
    output_dir: str = "reports"
    output_markdown: bool = True
    output_csv: bool = True
    output_json: bool = True
    send_email: bool = False
    send_telegram: bool = False


class AutomationConfig(BaseModel):
    enabled: bool = False
    schedule_time: str = "22:30"
    weekdays_only: bool = True
    run_ai: bool = True
    ai_provider: str = "groq"
    ai_top_n: int = 8
    telegram_top_focus: int = 8
    telegram_top_launch: int = 4
    telegram_top_accumulation: int = 4
    power_action: Literal["Hibernate", "Sleep", "Shutdown", "None"] = "Hibernate"


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file: str = "logs/scout.log"


class StageAnalysisConfig(BaseModel):
    """Full-universe Weinstein stage classification (weekly, 30-week SMA)."""

    # Below this much weekly history the long-horizon reads (multi-year range
    # position, secular recovery) are reported as unavailable rather than
    # computed from a shorter window and presented as the real thing. The
    # default matches the 104-week floor the recovery detection already
    # requires; raise it to demand a fuller cycle before trusting the label.
    min_context_years_for_long_horizon: float = 2.0
    weekly_sma_period: int = 30
    # Slope of the 30w SMA measured as % change over this many weeks.
    slope_lookback_weeks: int = 5
    # |slope| at or below this %/window counts as a FLAT 30w SMA (Stage 1 / 3).
    flat_slope_threshold_pct: float = 1.0
    # Stage 1/3 require price NEAR the flat MA. Beyond this band, a flat-MA name
    # is just price extended above (→ Stage 2) or below (→ Stage 4) the MA.
    flat_near_band_pct: float = 10.0
    # A stage 3 top begins when the 30w MA "loses its upward slope and starts to
    # flatten out" (Weinstein pp.36-37), so a clearly rising MA is never a top.
    # This multiple of `flat_slope_threshold_pct` is where "still basing/topping
    # inside the box" ends and "stage 2 continuation" begins. It is deliberately
    # a multiple rather than its own number: the range-first design allows a
    # *slightly* rising MA inside a range, and that allowance has to move when
    # the flat band is retuned instead of silently drifting out of step.
    topping_max_slope_mult: float = 2.0
    # Mansfield RS = (rs_line / SMA(rs_line, N) - 1) * 100.
    mansfield_lookback_weeks: int = 52
    # --- Weinstein VOLUME rules (weekly) --------------------------------------
    # Weinstein: a real Stage-2 breakout comes on volume EXPANSION (he quotes
    # ~2x average; "the higher the better"). Stage 1 bases form on volume
    # DRY-UP. Volume is measured vs the 30w average weekly volume.
    volume_avg_weeks: int = 30
    breakout_volume_mult: float = 2.0     # >= this x 30w avg = genuine demand
    dryup_volume_mult: float = 0.85       # <= this x 30w avg = quiet base
    # Lookback (weeks) over which a Stage-2 breakout's volume is assessed.
    breakout_volume_lookback_weeks: int = 5
    # --- Weinstein RESISTANCE (Stage-2 breakout above the base ceiling) -------
    # A Stage-2 entry is a break ABOVE the prior trading-range top, not merely a
    # cross of the rising 30w SMA. The base ceiling = highest weekly close over
    # this lookback, excluding the most recent `resistance_exclude_recent_weeks`.
    resistance_lookback_weeks: int = 35
    resistance_exclude_recent_weeks: int = 5
    # --- Range-first Weinstein structure --------------------------------------
    # Stage 1/3 are trading-range regimes. A stock can remain Stage 1/3 even
    # when the 30w SMA is slightly rising/falling if price is still respecting
    # the support/resistance box.
    range_short_lookback_weeks: int = 26
    range_normal_lookback_weeks: int = 52
    range_long_lookback_weeks: int = 104
    range_secular_lookback_weeks: int = 156
    range_min_age_weeks: int = 20
    range_max_depth_pct: float = 80.0
    support_resistance_tolerance_pct: float = 4.0
    range_break_buffer_pct: float = 1.0
    min_support_touches: int = 2
    min_resistance_touches: int = 2
    min_whipsaw_count_for_range: int = 2
    # Stage 2 can still be valid but too stretched for a fresh-entry read.
    extended_up_from_30w_pct: float = 30.0
    extended_down_from_30w_pct: float = 30.0
    # Early Stage-1 recovery: after a decline, price can start basing while the
    # 30w SMA is still falling. These names should not remain pure Stage 4 if
    # the decline is decelerating and price has recovered into the upper part of
    # its base/reclaim zone.
    recovery_lookback_weeks: int = 10
    stage1_recovery_max_below_sma_pct: float = 15.0
    stage1_recovery_min_price_slope_pct: float = 3.0
    stage1_recovery_min_range_pos: float = 0.35

    @model_validator(mode="after")
    def _check_flat_bands(self) -> "StageAnalysisConfig":
        # The flat-MA branch needs the "flat slope" band to sit strictly inside
        # the "price near MA" band; otherwise the Stage 1/3 (near, flat) vs
        # Stage 2/4 (extended) routing collapses.
        if self.flat_slope_threshold_pct >= self.flat_near_band_pct:
            raise ValueError(
                "stage_analysis.flat_slope_threshold_pct must be < flat_near_band_pct"
            )
        return self


class AlertsConfig(BaseModel):
    """Post-scan alerts → Telegram (not real-time). 'screen' alerts fire when a
    candidate matches a saved screener filter; 'trendline' alerts fire when the
    latest bar touches/breaks a hand-drawn line. Credentials reuse
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from .env."""

    enabled: bool = False
    telegram: bool = True
    trendline_lookback_days: int = 60
    max_screen_tickers: int = 25
    # Automatic, report-to-report notification. It uses the trade-plan
    # contract rather than detector actionability and is safe for old reports
    # because levels are derived read-only from their primary setup.
    entry_ready_transitions: bool = True


class IPOConfig(BaseModel):
    """IPO-by-year watchlists. A seed list bootstraps the category; the app then
    resolves exact first-trade dates a few tickers per scan so the daily run is
    not slowed."""

    enabled: bool = True
    seed_file: str = "data/ipo_seed.json"
    resolve_per_scan: int = 25
    min_year: int = 2019


class MacroConfig(BaseModel):
    """Cross-asset Returns Leaderboard ("Macro" tab + weekly/monthly Telegram).

    A returns.json snapshot is written every scan (cheap; powers the tab). The
    Telegram snapshot is auto-sent only on the last trading day of the week
    (1W window) and the last trading day of the month (1M window). Credentials
    reuse TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from .env."""

    enabled: bool = True
    telegram_weekly: bool = True
    telegram_monthly: bool = True


class Settings(BaseModel):
    """Aggregated, validated runtime config (from config.yaml)."""

    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    ibkr: IBKRConfig = Field(default_factory=IBKRConfig)
    alpaca: AlpacaConfig = Field(default_factory=AlpacaConfig)
    tiingo: TiingoConfig = Field(default_factory=TiingoConfig)
    twelvedata: TwelveDataConfig = Field(default_factory=TwelveDataConfig)
    fmp: FMPConfig = Field(default_factory=FMPConfig)
    yfinance: YFinanceConfig = Field(default_factory=YFinanceConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    marketdata: MarketDataConfig = Field(default_factory=MarketDataConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    prefilter: PrefilterConfig = Field(default_factory=PrefilterConfig)
    setups: SetupsConfig = Field(default_factory=SetupsConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    stage_analysis: StageAnalysisConfig = Field(default_factory=StageAnalysisConfig)
    ai_ranker: AIRankerConfig = Field(default_factory=AIRankerConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    reports: ReportsConfig = Field(default_factory=ReportsConfig)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    ipo: IPOConfig = Field(default_factory=IPOConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # Resolved at load time, not from YAML
    project_root: Path = Field(default_factory=lambda: Path.cwd())


class Env(BaseSettings):
    """Secrets loaded from .env. Keys default to empty so missing keys don't crash startup."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    TIINGO_API_KEY: str = ""
    TWELVEDATA_API_KEY: str = ""
    FMP_API_KEY: str = ""
    FRED_API_KEY: str = ""
    ALPACA_API_KEY: str = ""
    ALPACA_SECRET_KEY: str = ""
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"
    ALPACA_DATA_FEED: str = "iex"
    IBKR_HOST: str = "127.0.0.1"
    IBKR_PORT: int = 7497
    IBKR_CLIENT_ID: int = 17
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    EMAIL_HOST: str = ""
    EMAIL_PORT: int = 587
    EMAIL_USER: str = ""
    EMAIL_PASSWORD: str = ""
