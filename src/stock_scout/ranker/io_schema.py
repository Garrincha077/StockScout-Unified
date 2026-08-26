from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SetupType = Literal[
    "GLB",
    "Minervini",
    "Tight",
    "Weinstein",
    "HighRS",
    "Guppy",
    "RWBSqueeze",
    "EMAStackLaunch",
    "Accumulation",
    "LongBase",
    "CrashBase",
    "Mixed",
]
EntryStyle = Literal[
    "breakout", "anticipation", "wait_for_pullback", "avoid_for_now", "insufficient_data"
]
ConfidenceLevel = Literal["low", "medium", "high"]

Verdict = Literal[
    "actionable",
    "watch",
    "avoid",
    "extended",
    "insufficient_data",
    # Legacy names are still accepted so old ranked.json artifacts parse.
    "reject_false_positive",
    "reject_extended",
    "reject_insufficient_data",
]
PivotClarity = Literal["clear", "ambiguous", "none"]


class RankerCandidateInput(BaseModel):
    """Per-ticker input given to the LLM. Compact, JSON-friendly."""

    ticker: str
    as_of: str
    sector: str | None = None
    industry: str | None = None
    close: float | None = None
    avg_dollar_volume_50d: float | None = None
    distance_to_52w_high_pct: float | None = None
    sma50: float | None = None
    sma150: float | None = None
    sma200: float | None = None
    sma200_rising: bool | None = None
    rs_score_3m: float | None = None
    rs_score_6m: float | None = None
    atr20: float | None = None
    volume_ratio_50d: float | None = None
    rs_rating: float | None = None
    actionability: str | None = None
    actionability_reason: str | None = None
    focus_score: float | None = None
    pocket_pivot: bool | None = None
    up_down_vol_ratio_50d: float | None = None
    accumulation_score: float | None = None
    accumulation_phase: str | None = None
    institutional_footprint_score: float | None = None
    base_quality_score: float | None = None
    sma_compression_pct: float | None = None
    support_volume_events: int | None = None
    dryup_after_accumulation: bool | None = None
    base_length_bars: int | None = None
    base_depth_pct: float | None = None
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
    weinstein_stage: int | None = None
    weinstein_substage: str | None = None
    weinstein_stage_quality_score: float | None = None
    weinstein_stage_origin: str | None = None
    weinstein_ext_pct: float | None = None
    weinstein_resistance_zone_high: float | None = None
    weinstein_support_zone_low: float | None = None
    long_term_context: str | None = None
    secular_recovery_score: float | None = None
    near_secular_resistance: bool | None = None
    long_term_drawdown_pct: float | None = None
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
    launch_30w_slope_pct: float | None = None
    launch_30w_slope_state: str | None = None
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
    deterministic_score: float
    primary_setup: str | None = None
    trigger_level: float | None = None
    invalidation_level: float | None = None
    setups: dict[str, dict[str, Any]] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    data_status: str = "OK"
    m_and_a_confidence: str | None = None
    next_earnings_date: str | None = None
    insider_buying: bool | None = None
    float_shares: float | None = None
    short_pct_float: float | None = None
    alert_suggestions: list[str] = Field(default_factory=list)


class RankerInput(BaseModel):
    as_of_date: str
    primary_provider: str
    secondary_provider: str | None = None
    universe_size: int
    candidates_count: int
    candidates: list[RankerCandidateInput]


class FalsePositiveCheck(BaseModel):
    """Explicit Q&A so the model has to actively rule out common
    false-positive shapes before committing to a verdict."""

    m_and_a_clues: bool = False
    stale_data_clues: bool = False
    low_liquidity_tightness: bool = False
    no_clear_pivot: bool = False
    notes: str = ""


class RiskReward(BaseModel):
    stop_level: float | None = None
    first_target: float | None = None
    ratio: float | None = None      # (first_target - entry) / (entry - stop_level)


class RankedCandidate(BaseModel):
    ticker: str
    setup_type: SetupType
    # None for rejected candidates (the skeptical prompt allows that)
    overall_rank: int | None = Field(default=None, ge=1)
    score: float = Field(ge=0, le=100)
    bullish_factors: list[str] = Field(default_factory=list)
    warning_factors: list[str] = Field(default_factory=list)
    trigger_level: float | None = None
    invalidation_level: float | None = None
    ideal_entry_style: EntryStyle
    confidence_level: ConfidenceLevel
    short_comment: str = Field(max_length=500)

    # --- Skeptical ranker fields (added Faza 6) ---------------------------
    # All have defaults so legacy responses without these fields still parse.
    verdict: Verdict = "watch"
    false_positive_check: FalsePositiveCheck = Field(default_factory=FalsePositiveCheck)
    pivot_clarity: PivotClarity = "ambiguous"
    risk_reward: RiskReward = Field(default_factory=RiskReward)
    institutional_read: str | None = Field(default=None, max_length=400)
    confirmation_signal: str | None = Field(default=None, max_length=300)
    thesis_breaker: str | None = Field(default=None, max_length=300)


class RankerOutput(BaseModel):
    ranked: list[RankedCandidate]
