from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Reason(BaseModel):
    text: str
    weight: float = 0.0
    category: str | None = None


class Flag(BaseModel):
    code: str
    severity: str = "warning"     # info | warning | error
    detail: str | None = None


class SetupSummary(BaseModel):
    setup_name: str
    triggered: bool
    sub_state: str | None = None
    score: float = 0.0
    trigger_level: float | None = None
    invalidation_level: float | None = None
    reasons: list[str] = Field(default_factory=list)
    failed_conditions: list[str] = Field(default_factory=list)
    warning_flags: list[str] = Field(default_factory=list)
    raw_features: dict[str, Any] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    liquidity: float = 0.0
    trend: float = 0.0
    relative_strength: float = 0.0
    setup_quality: float = 0.0
    tightness: float = 0.0
    volume_confirmation: float = 0.0


class TradePlan(BaseModel):
    """Implementation-readiness derived from a detector's original levels.

    The legacy headline levels remain on :class:`Candidate` for compatibility,
    but they are presentation values and must not be used for position sizing.
    This object only trusts the primary detector's unmodified trigger and
    structural invalidation.
    """

    status: Literal[
        "entry_ready",
        "trigger_pending",
        "wait_for_retest",
        "not_tradeable",
        "insufficient_data",
    ]
    reason_codes: list[str] = Field(default_factory=list)
    trigger_state: Literal["pending", "fresh", "extended", "unavailable"]
    trigger_reference_level: float | None = None
    entry_reference_level: float | None = None
    structural_invalidation_level: float | None = None
    entry_risk_pct: float | None = None
    extension_atr: float | None = None
    tactical_stop_level: float | None = None
    tactical_risk_pct: float | None = None
    source: str
    version: int


class MAClusterResearchProfile(BaseModel):
    """Experimental preference fit; never a production rank or sizing input."""

    version: int
    timeframe: Literal["daily", "weekly"]
    points: int | None = None
    score: float | None = None
    coverage: int = 0
    components: dict[str, bool | None] = Field(default_factory=dict)
    metrics: dict[str, float | None] = Field(default_factory=dict)
    archetype: Literal[
        "recovery_reclaim", "tight_efficient", "fresh_momentum", "balanced"
    ] | None = None
    archetype_confidence: float | None = None
    archetype_scores: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    search_text: str = "preferred breakout research fit"
    source: str


class Candidate(BaseModel):
    ticker: str
    as_of: str
    price: float | None = None
    sector: str | None = None
    industry: str | None = None

    sma50: float | None = None
    sma150: float | None = None
    sma200: float | None = None
    distance_to_52w_high_pct: float | None = None
    rs_score_3m: float | None = None
    rs_score_6m: float | None = None
    rs_score_weighted: float | None = None     # IBD-style weighted multi-timeframe RS (raw input to rs_rating)
    rs_rating: float | None = None             # IBD-style universe-relative percentile (0-100)
    # RS-line leadership signals (Weinstein / O'Neil): RS line at its own 52w high,
    # signed distance from that high, and whether it leads price ("blue dot").
    rs_line_at_52w_high: bool | None = None
    rs_line_52w_distance_pct: float | None = None
    rs_new_high_before_price: bool | None = None
    mansfield_rs: float | None = None          # weekly Mansfield RS from the stage classifier
    avg_dollar_volume_50d: float | None = None
    atr20: float | None = None
    volume_ratio_50d: float | None = None
    # Momentum & volatility (Qullamaggie/Oliver Kell-style momentum screens)
    adr_pct: float | None = None               # 20-day average daily range %
    ret_1d_pct: float | None = None            # 1-bar price return %
    ret_1m_pct: float | None = None            # ~21-bar price return %
    ret_3m_pct: float | None = None            # ~63-bar price return %
    ret_6m_pct: float | None = None            # ~126-bar price return %
    rvol_today: float | None = None            # latest volume / 50d avg volume
    # Faza G early-signal volume features (surfaced in the list view)
    pocket_pivot: bool | None = None
    up_down_vol_ratio_50d: float | None = None
    # Accumulation Footprint module (NOK-style basing / demand footprints)
    accumulation_score: float | None = None
    accumulation_phase: str | None = None
    institutional_footprint_score: float | None = None
    base_quality_score: float | None = None
    sma_compression_pct: float | None = None
    support_volume_events: int | None = None
    dryup_after_accumulation: bool | None = None
    base_length_bars: int | None = None
    base_depth_pct: float | None = None
    # Long Base Launch module (weekly/monthly base, demand spikes, quiet pullbacks)
    long_base_score: float | None = None
    long_base_phase: str | None = None
    demand_spike_count: int | None = None
    low_volume_pullback_count: int | None = None
    demand_supply_ratio: float | None = None
    dryup_near_pivot: bool | None = None
    weekly_base_length_bars: int | None = None
    monthly_base_length_bars: int | None = None
    support_hold_after_demand: bool | None = None
    guppy_compression_state: str | None = None
    rs_turning_up: bool | None = None
    # Crash Base Stage 1 module (multi-year post-crash bases and alert tiers)
    crash_base_score: float | None = None
    crash_base_phase: str | None = None
    drawdown_5y_pct: float | None = None
    base_age_weeks: int | None = None
    resistance_attempt_count: int | None = None
    trendline_attempt_count: int | None = None
    trendline_breakout_5y: bool | None = None
    weekly_breakout_rvol: float | None = None
    daily_rvol_headsup: bool | None = None
    special_alert_level: str | None = None
    # Weinstein stage context surfaced for secular Stage 1 -> 2 recovery screens.
    weinstein_stage: int | None = None
    weinstein_substage: str | None = None
    # "favoured" / "avoid" / "" - what the 2016+ holdout says about this bucket,
    # derived once from `focus_blend.stage_note` so the screen, the report and
    # the digest read one dictionary rather than three copies of a tuple. The
    # substage alone cannot carry it: `1B_mature_base` is the app's strongest
    # negative (-1.03 in sample, -2.35 held out, n=19,343) and looks like any
    # other label on screen.
    stage_note: str | None = None
    weinstein_stage_quality_score: float | None = None
    weinstein_stage_trade_bias: str | None = None
    weinstein_stage_range_state: str | None = None
    weinstein_stage_origin: str | None = None
    weinstein_ext_pct: float | None = None
    weinstein_range_104w_pos: float | None = None
    weinstein_range_156w_pos: float | None = None
    weinstein_range_260w_pos: float | None = None
    weinstein_range_520w_pos: float | None = None
    weinstein_support_zone_low: float | None = None
    weinstein_resistance_zone_high: float | None = None
    long_term_context: str | None = None
    secular_recovery_score: float | None = None
    near_secular_resistance: bool | None = None
    long_term_drawdown_pct: float | None = None
    long_term_recovery_from_low_pct: float | None = None
    long_term_vs_prior_high_pct: float | None = None
    # RWB Squeeze Thrust module (weekly GMMA/RWB squeeze + volume thrust)
    rwb_squeeze_score: float | None = None
    rwb_squeeze_phase: str | None = None
    weekly_rwb_state: str | None = None
    weekly_rwb_band_width_pct: float | None = None
    weekly_short_group_width_pct: float | None = None
    weekly_long_group_width_pct: float | None = None
    weekly_rwb_spread_pct: float | None = None
    weekly_30w_slope_pct: float | None = None
    price_above_rwb_band: bool | None = None
    rwb_thrust_rel_volume: float | None = None
    rwb_thrust_close_location: float | None = None
    prior_rwb_thrust_attempts: int | None = None
    weekly_trendline_breakout: bool | None = None
    rwb_extension_above_band_pct: float | None = None
    # Unified Launch Desk slope filter (best available launch setup context)
    launch_30w_slope_pct: float | None = None
    launch_30w_slope_state: str | None = None
    # EMA Stack Launch module (broader AEVA/CVNA-style coil + ignition ranking)
    ema_stack_launch_score: float | None = None
    ema_stack_phase: str | None = None
    recent_coil_date: str | None = None
    recent_coil_bars_ago: int | None = None
    recent_coil_score: float | None = None
    long_ema_compression_score: float | None = None
    short_ema_compression_score: float | None = None
    ema_stack_relationship_score: float | None = None
    ema_stack_thrust_score: float | None = None
    prior_pressure_score: float | None = None
    weekly_stack_width_pct: float | None = None
    weekly_short_ema_width_pct: float | None = None
    weekly_long_ema_width_pct: float | None = None
    current_thrust_rel_volume: float | None = None
    current_thrust_close_location: float | None = None
    price_above_stack_top: bool | None = None
    prior_stack_thrust_attempts: int | None = None
    extension_above_stack_pct: float | None = None
    # Daily MA cluster + volume ignition module.
    ma_cluster_score: float | None = None
    ma_cluster_phase: str | None = None
    ma_cluster_width_pct: float | None = None
    ma_cluster_top: float | None = None
    ma_cluster_bottom: float | None = None
    ma_cluster_mas_crossed: int | None = None
    ma_cluster_breakout_rel_volume_20d: float | None = None
    ma_cluster_breakout_close_location: float | None = None
    ma_cluster_breakout_age_bars: int | None = None
    ma_cluster_distance_from_top_pct: float | None = None
    ma_cluster_structural_stop_distance_pct: float | None = None
    ma_cluster_sma200_slope_pct: float | None = None
    ma_cluster_weekly_30w_slope_pct: float | None = None
    ma_cluster_held_above: bool | None = None
    ma_cluster_research: MAClusterResearchProfile | None = None
    focus_score: float | None = None
    alert_suggestions: list[str] = Field(default_factory=list)

    setups: dict[str, SetupSummary] = Field(default_factory=dict)

    score: float = 0.0
    score_breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    reasons: list[Reason] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)

    primary_setup: str | None = None
    trade_plan: TradePlan | None = None
    # Legacy presentation fields. They remain on the wire for compatibility,
    # but `trade_plan` is the only source permitted for sizing/readiness.
    trigger_level: float | None = None
    invalidation_level: float | None = None

    provider_used: str = ""
    fallback_triggered: bool = False
    data_status: str = "OK"

    # --- Faza 5: actionability + exclusion routing -----------------------
    actionability: str = "watch"               # actionable_now | near_actionable | watch | extended_too_late | not_valid | excluded
    actionability_reason: str = ""
    excluded_reason: str | None = None         # set when actionability indicates exclusion
    data_quality_issues: list[Flag] = Field(default_factory=list)
    m_and_a_confidence: str = "none"           # none | low | medium | high
    # --- Risk, derived once so every surface agrees -----------------------
    # `none | elevated | excluded`, from scoring/risk.py. This is not
    # `Flag.severity`: 1,245 of a run's flags are `warning` and almost all are
    # ordinary commentary, so filtering the screen on severity would hide half
    # of it. Presentation reads this; `candidates.json` still carries every row.
    risk_level: str = "none"
    risk_reasons: list[dict[str, str]] = Field(default_factory=list)
    next_earnings_date: str | None = None      # ISO date if known (annotated post-scan)
    # --- Faza G: conviction / risk context (top-N, annotated post-scan) ----
    insider_buying: bool | None = None         # net open-market insider buying recently
    float_shares: float | None = None          # tradable float (low float = sharper moves)
    short_pct_float: float | None = None        # short interest as % of float (squeeze/risk)
