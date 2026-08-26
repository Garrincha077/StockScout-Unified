from __future__ import annotations

import math

from stock_scout.config.schema import ScoringConfig
from stock_scout.scoring.focus_blend import stage_note
from stock_scout.scoring.models import (
    Candidate,
    Flag,
    Reason,
    ScoreBreakdown,
    SetupSummary,
)
from stock_scout.scoring.risk import assess as assess_risk
from stock_scout.scoring.risk_levels import clamp_invalidation, clamp_trigger
from stock_scout.scoring.trade_plan import calculate_trade_plan
from stock_scout.setups.actionability import aggregate_actionability
from stock_scout.setups.base import SetupResult
from stock_scout.setups.base_analysis import sma_stack_conditions

# Per-setup warnings that the aggregate step below reports once, centrally. A
# detector may keep raising them - `SetupSummary.warning_flags` still records
# which ones did - but they must not be restated as top-level flags per setup.
_CENTRALLY_REPORTED_WARNINGS = frozenset({"m_and_a_medium", "m_and_a_high"})


def _normalize_to_100(value: float | None, low: float, high: float) -> float:
    """Linearly map `value` from [low, high] to [0, 100], clamped."""
    if value is None:
        return 0.0
    if high <= low:
        return 0.0
    return max(0.0, min(100.0, (value - low) / (high - low) * 100.0))


def _slope_state(value: float | None, threshold_pct: float = 0.75) -> str:
    if value is None:
        return "unknown"
    value = float(value)
    if value >= threshold_pct:
        return "upward"
    if value <= -threshold_pct:
        return "downward"
    return "flat"


def _launch_slope_bonus(state: str | None) -> float:
    if state == "upward":
        return 6.0
    if state == "flat":
        return 3.0
    if state == "downward":
        return -7.0
    return 0.0


def _extension_penalty(value: float | None) -> float:
    if value is None:
        return 0.0
    value = float(value)
    if value >= 50.0:
        return 25.0
    if value >= 35.0:
        return 16.0
    if value >= 25.0:
        return 8.0
    return 0.0


def _finite_float(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _has_edge_launch_phase(
    ema_stack_phase: str | None,
    rwb_squeeze_phase: str | None,
    long_base_phase: str | None,
) -> bool:
    return (
        ema_stack_phase in {"stack_thrust"}
        or rwb_squeeze_phase in {"trendline_breakout", "confirmed"}
        or long_base_phase in {"accumulating", "launching"}
    )


def _has_explosive_watch_phase(
    ema_stack_phase: str | None,
    rwb_squeeze_phase: str | None,
) -> bool:
    return ema_stack_phase == "early_ignition" or rwb_squeeze_phase == "thrusting"


class CandidateScorer:
    """Transparent weighted scoring with explicit reasons + flags.

    The overall score is a weighted combination of six normalized 0-100 component
    scores. Each contribution is recorded as a Reason for auditability.
    """

    def __init__(self, cfg: ScoringConfig):
        self.cfg = cfg

    def score(
        self,
        ticker: str,
        features: dict,
        setups: list[SetupResult],
        provider_used: str,
        fallback_triggered: bool = False,
        sector: str | None = None,
        industry: str | None = None,
        m_and_a_confidence: str = "none",
        m_and_a_price_pin: bool = False,
    ) -> Candidate:
        # --- Component scores --------------------------------------------------
        # Liquidity: $vol from $5M (0) to $50M+ (100)
        liquidity = _normalize_to_100(features.get("avg_dollar_volume_50d"), 5_000_000, 50_000_000)

        # Trend: how many of {>sma50, >sma150, >sma200, sma50>sma150>sma200, sma200_rising}.
        # Shared with the Minervini trend-template via sma_stack_conditions() so the
        # two never drift apart.
        trend_conditions = list(sma_stack_conditions(features).values())
        trend = (sum(trend_conditions) / len(trend_conditions)) * 100.0

        # Relative strength: prefer the universe-relative RS Rating (IBD-style
        # percentile, 0-100) when the cross-sectional pass has populated it —
        # this ranks true leaders vs the whole universe rather than vs a fixed
        # absolute outperformance band. Falls back to the absolute RS-vs-SPY
        # mapping when no universe distribution is available (e.g. single-ticker
        # debug runs).
        rs_rating = features.get("rs_rating")
        if rs_rating is not None:
            relative_strength = float(rs_rating)
        else:
            rs = features.get("rs_score_6m")
            if rs is None:
                rs = features.get("rs_score_3m")
            relative_strength = _normalize_to_100(rs, -10.0, 30.0)

        # Setup quality: max individual setup score (a stock can be a great GLB without being Minervini)
        triggered = [s for s in setups if s.triggered]
        setup_quality = max((s.score for s in triggered), default=0.0)

        # Tightness: range_contraction_10_50 ratio mapped (1.0 -> 0, 0.5 -> 100)
        ratio = features.get("range_contraction_10_50")
        if ratio is None:
            tightness = 0.0
        else:
            tightness = max(0.0, min(100.0, (1.0 - ratio) * 200.0))

        # A stock parked against a deal price is the tightest thing on the
        # screen, and this scale cannot say so: it saturates at a ratio of 0.5,
        # so an ordinary consolidation and a stock that has stopped trading both
        # come out at 100. That is how deal-locked names ride tightness to the
        # top of the ranking - which is the complaint this whole filter exists
        # to answer.
        #
        # Zeroing the contribution rather than rescaling the formula is
        # deliberate. Tightness earns its weight everywhere else and the problem
        # is narrow: 12 of 2,124 candidates on a recent run sat at exactly 100.
        # This also covers the case news does not confirm - the name stays on
        # screen, it just stops being carried there by a number that means
        # something different for it than for everything around it.
        tightness_voided = bool(m_and_a_price_pin and tightness > 0.0)
        if tightness_voided:
            tightness = 0.0

        # Volume confirmation: ratio of current vol to 50d avg. 1.0 -> 50, 2.0 -> 100.
        vr = features.get("volume_ratio_50d")
        volume_confirmation = _normalize_to_100(vr, 0.5, 2.0)

        breakdown = ScoreBreakdown(
            liquidity=round(liquidity, 1),
            trend=round(trend, 1),
            relative_strength=round(relative_strength, 1),
            setup_quality=round(setup_quality, 1),
            tightness=round(tightness, 1),
            volume_confirmation=round(volume_confirmation, 1),
        )

        # --- Weighted aggregate ------------------------------------------------
        w = self.cfg.weights
        total = (
            liquidity * w.liquidity
            + trend * w.trend
            + relative_strength * w.relative_strength
            + setup_quality * w.setup_quality
            + tightness * w.tightness
            + volume_confirmation * w.volume_confirmation
        )

        # --- Soft factor: ADR (volatility) penalty ----------------------------
        # ADR > 12% = highly volatile name. Not a disqualifier (some traders
        # specifically hunt high-ADR Qullamaggie names), but does dampen the
        # overall score to avoid ranking 25% ADR penny-class above 5% ADR
        # leader with the same setup. Soft factor.
        adr_pct = features.get("adr20_pct")
        adr_multiplier = 1.0
        if adr_pct is not None and adr_pct > 12.0:
            # 12% → 1.0 (no penalty), 18% → 0.9, 25% → 0.78
            adr_multiplier = max(0.7, 1.0 - (adr_pct - 12.0) * 0.02)
            total *= adr_multiplier

        # --- Reasons + flags ---------------------------------------------------
        reasons: list[Reason] = []
        flags: list[Flag] = []

        def add_reason(name: str, value: float, weight: float, category: str):
            reasons.append(
                Reason(
                    text=f"{name}={value:.1f} (weight {weight:.2f}, contrib {value * weight:.1f})",
                    weight=weight,
                    category=category,
                )
            )

        add_reason("liquidity_score", liquidity, w.liquidity, "liquidity")
        add_reason("trend_score", trend, w.trend, "trend")
        add_reason("relative_strength_score", relative_strength, w.relative_strength, "rs")
        add_reason("setup_quality_score", setup_quality, w.setup_quality, "setup")
        add_reason("tightness_score", tightness, w.tightness, "tightness")
        if tightness_voided:
            flags.append(Flag(code="tightness_void_price_pinned", severity="info"))
        add_reason("volume_confirmation_score", volume_confirmation, w.volume_confirmation, "volume")
        if adr_multiplier < 1.0:
            reasons.append(
                Reason(
                    text=f"adr_volatility_penalty (adr={adr_pct:.1f}%, mult={adr_multiplier:.2f})",
                    weight=adr_multiplier,
                    category="volatility",
                )
            )
            flags.append(Flag(code=f"high_adr:{adr_pct:.1f}%", severity="warning"))

        for s in setups:
            if s.warning_flags:
                for wf in s.warning_flags:
                    # M&A is decided centrally below and emitted once. Six
                    # detectors each ran their own `detect_m_and_a_from_price`
                    # and restated the answer here: 528 flags in one run, 16.5%
                    # of every flag emitted, so one cause looked like six.
                    #
                    # They are not merely duplicates, they are a *worse*
                    # verdict. The detectors pass no `spy_returns`, so their pin
                    # runs without the correlation condition that the central
                    # check applies - and 40 of that run's candidates carried a
                    # per-setup warning the central check had cleared. Which
                    # setups saw what is still recoverable from
                    # `SetupSummary.warning_flags`; it just stops being reported
                    # as if it were the finding.
                    if wf in _CENTRALLY_REPORTED_WARNINGS:
                        continue
                    flags.append(Flag(code=f"{s.setup_name}:{wf}", severity="warning"))
        if fallback_triggered:
            flags.append(Flag(code="data_fallback_used", severity="warning"))

        # --- Pick primary setup + trigger/invalidation -------------------------
        primary = max(triggered, key=lambda s: s.score, default=None)
        accumulation = next((s for s in setups if s.setup_name == "accumulation_base"), None)
        ar = accumulation.raw_features if accumulation is not None else {}
        accumulation_score = ar.get("accumulation_score")
        accumulation_phase = ar.get("accumulation_phase")
        institutional_footprint_score = ar.get("institutional_footprint_score")
        base_quality_score = ar.get("base_quality_score")
        sma_compression_pct = ar.get("sma_compression_pct")
        support_volume_events = ar.get("support_volume_events")
        dryup_after_accumulation = ar.get("dryup_after_accumulation")
        base_length_bars = ar.get("base_length_bars")
        base_depth_pct = ar.get("base_depth_pct")
        long_base = next((s for s in setups if s.setup_name == "long_base_launch"), None)
        lr = long_base.raw_features if long_base is not None else {}
        long_base_score = lr.get("long_base_score")
        long_base_phase = lr.get("long_base_phase")
        long_base_30w_slope_pct = lr.get("weekly_30w_slope_pct")
        demand_spike_count = lr.get("demand_spike_count")
        low_volume_pullback_count = lr.get("low_volume_pullback_count")
        demand_supply_ratio = lr.get("demand_supply_ratio")
        dryup_near_pivot = lr.get("dryup_near_pivot")
        weekly_base_length_bars = lr.get("weekly_base_length_bars")
        monthly_base_length_bars = lr.get("monthly_base_length_bars")
        support_hold_after_demand = lr.get("support_hold_after_demand")
        guppy_compression_state = lr.get("guppy_compression_state")
        rs_turning_up = lr.get("rs_turning_up")
        crash_base = next((s for s in setups if s.setup_name == "crash_base_stage1"), None)
        cr = crash_base.raw_features if crash_base is not None else {}
        crash_base_score = cr.get("crash_base_score")
        crash_base_phase = cr.get("crash_base_phase")
        drawdown_5y_pct = cr.get("drawdown_5y_pct")
        base_age_weeks = cr.get("base_age_weeks")
        resistance_attempt_count = cr.get("resistance_attempt_count")
        trendline_attempt_count = cr.get("trendline_attempt_count")
        trendline_breakout_5y = cr.get("trendline_breakout_5y")
        weekly_breakout_rvol = cr.get("weekly_breakout_rvol")
        daily_rvol_headsup = cr.get("daily_rvol_headsup")
        special_alert_level = cr.get("special_alert_level")
        rwb_squeeze = next((s for s in setups if s.setup_name == "rwb_squeeze_thrust"), None)
        rr = (
            rwb_squeeze.raw_features
            if rwb_squeeze is not None
            and (rwb_squeeze.triggered or rwb_squeeze.actionability == "extended_too_late")
            else {}
        )
        rwb_squeeze_score = rr.get("rwb_squeeze_score")
        rwb_squeeze_phase = rr.get("rwb_squeeze_phase")
        weekly_rwb_state = rr.get("weekly_rwb_state")
        weekly_rwb_band_width_pct = rr.get("weekly_rwb_band_width_pct")
        weekly_short_group_width_pct = rr.get("weekly_short_group_width_pct")
        weekly_long_group_width_pct = rr.get("weekly_long_group_width_pct")
        weekly_rwb_spread_pct = rr.get("weekly_rwb_spread_pct")
        weekly_30w_slope_pct = rr.get("weekly_30w_slope_pct")
        price_above_rwb_band = rr.get("price_above_rwb_band")
        rwb_thrust_rel_volume = rr.get("rwb_thrust_rel_volume")
        rwb_thrust_close_location = rr.get("rwb_thrust_close_location")
        prior_rwb_thrust_attempts = rr.get("prior_rwb_thrust_attempts")
        weekly_trendline_breakout = rr.get("weekly_trendline_breakout")
        rwb_extension_above_band_pct = rr.get("rwb_extension_above_band_pct")
        ema_stack = next((s for s in setups if s.setup_name == "ema_stack_launch"), None)
        er = ema_stack.raw_features if ema_stack is not None and ema_stack.triggered else {}
        ema_stack_launch_score = er.get("ema_stack_launch_score")
        ema_stack_phase = er.get("ema_stack_phase")
        recent_coil_date = er.get("recent_coil_date")
        recent_coil_bars_ago = er.get("recent_coil_bars_ago")
        recent_coil_score = er.get("recent_coil_score")
        long_ema_compression_score = er.get("long_ema_compression_score")
        short_ema_compression_score = er.get("short_ema_compression_score")
        ema_stack_relationship_score = er.get("ema_stack_relationship_score")
        ema_stack_thrust_score = er.get("ema_stack_thrust_score")
        prior_pressure_score = er.get("prior_pressure_score")
        weekly_stack_width_pct = er.get("weekly_stack_width_pct")
        weekly_short_ema_width_pct = er.get("weekly_short_ema_width_pct")
        weekly_long_ema_width_pct = er.get("weekly_long_ema_width_pct")
        current_thrust_rel_volume = er.get("current_thrust_rel_volume")
        current_thrust_close_location = er.get("current_thrust_close_location")
        price_above_stack_top = er.get("price_above_stack_top")
        prior_stack_thrust_attempts = er.get("prior_stack_thrust_attempts")
        extension_above_stack_pct = er.get("extension_above_stack_pct")
        ema_stack_30w_slope_pct = er.get("weekly_30w_slope_pct")

        ma_cluster = next((s for s in setups if s.setup_name == "ma_cluster_volume_breakout"), None)
        ma_cluster_research = (
            ma_cluster.raw_features.get("ma_cluster_research_profile")
            if ma_cluster is not None
            else None
        )
        mr = (
            ma_cluster.raw_features
            if ma_cluster is not None
            and (ma_cluster.triggered or ma_cluster.actionability == "extended_too_late")
            else {}
        )
        ma_cluster_score = ma_cluster.score if ma_cluster is not None else None
        ma_cluster_phase = mr.get("ma_cluster_phase")
        ma_cluster_width_pct = mr.get("ma_cluster_width_pct")
        ma_cluster_top = mr.get("ma_cluster_top")
        ma_cluster_bottom = mr.get("ma_cluster_bottom")
        ma_cluster_mas_crossed = mr.get("mas_crossed")
        ma_cluster_breakout_rel_volume_20d = mr.get("breakout_rel_volume_20d")
        ma_cluster_breakout_close_location = mr.get("breakout_close_location")
        ma_cluster_breakout_age_bars = mr.get("breakout_age_bars")
        ma_cluster_distance_from_top_pct = (
            mr.get("distance_from_cluster_top_pct")
            if mr.get("distance_from_cluster_top_pct") is not None
            else mr.get("distance_to_cluster_top_pct")
        )
        ma_cluster_structural_stop_distance_pct = mr.get("structural_stop_distance_pct")
        ma_cluster_sma200_slope_pct = mr.get("daily_sma200_slope_pct")
        ma_cluster_weekly_30w_slope_pct = mr.get("weekly_30w_slope_pct")
        ma_cluster_held_above = mr.get("held_above_cluster")

        launch_slope_sources = [
            (ma_cluster_score, ma_cluster_weekly_30w_slope_pct),
            (ema_stack_launch_score, ema_stack_30w_slope_pct),
            (rwb_squeeze_score, weekly_30w_slope_pct),
            (long_base_score, long_base_30w_slope_pct),
        ]
        launch_30w_slope_pct = None
        for _score, _slope in sorted(
            (
                (float(score), slope)
                for score, slope in launch_slope_sources
                if score is not None and slope is not None
            ),
            key=lambda x: x[0],
            reverse=True,
        ):
            launch_30w_slope_pct = float(_slope)
            break
        launch_30w_slope_state = _slope_state(launch_30w_slope_pct)

        # --- Aggregate actionability across setups -----------------------------
        all_buckets: list[str] = []
        # Start from centrally-detected M&A confidence (orchestrator-level,
        # applies to every detector). Individual detectors can still bump it.
        m_and_a_conf = m_and_a_confidence
        excluded_reason: str | None = None
        actionability_reason = ""
        for s in setups:
            bucket = getattr(s, "actionability", None)
            if bucket:
                all_buckets.append(bucket)
            # Carry m&a confidence if any setup detected it (escalation only).
            #
            # Only `disqualifiers` is read, and the `m_and_a_medium` branch
            # below is unreachable on purpose - six detectors raise that string
            # but every one of them puts it in `warning_flags`. Leaving it
            # unreachable is the correct behaviour, not an oversight to fix:
            # the detectors call `detect_m_and_a_from_price` **without
            # `spy_returns`**, and `_has_price_pin` silently drops its
            # correlation condition when the benchmark is missing. Their verdict
            # is therefore computed with strictly less information than the
            # orchestrator's, which does pass the benchmark.
            #
            # The cost is measurable. On the run of 2026-07-28, 40 candidates
            # carried a per-setup `m_and_a_medium` that the central check had
            # specifically cleared on correlation - AEE, AVA, CNP and DUK
            # (utilities), BCAT, CLM, CRF, DSL and ECAT (closed-end funds), CIM
            # and DX (mortgage REITs). Exactly the low-volatility population the
            # correlation condition exists to protect. Escalating from
            # `warning_flags` would hide all forty.
            for d in getattr(s, "disqualifiers", []) or []:
                if d == "m_and_a_high":
                    m_and_a_conf = "high"
                    break
                if d == "m_and_a_medium" and m_and_a_conf != "high":
                    m_and_a_conf = "medium"

        # Centralised M&A gating: HIGH excludes any candidate regardless of
        # which detector triggered. MEDIUM/LOW emits a warning flag but lets
        # the candidate through. (Per user spec: don't drop legit setups whose
        # price coincidentally went flat.)
        if m_and_a_conf == "high":
            all_buckets = ["excluded"]
            flags.append(Flag(code="m_and_a_high_confidence", severity="error"))
        elif m_and_a_conf == "medium":
            flags.append(Flag(code="m_and_a_medium_confidence", severity="warning"))

        actionability = aggregate_actionability(all_buckets) if all_buckets else "not_valid"
        # Reason for the report: pick the reason from the setup whose bucket
        # matches the aggregate (so the explanation lines up with the bucket).
        for s in setups:
            if getattr(s, "actionability", None) == actionability:
                actionability_reason = getattr(s, "actionability_reason", "") or ""
                break
        if actionability in ("excluded", "extended_too_late", "not_valid"):
            excluded_reason = actionability_reason or actionability
        if m_and_a_conf == "high":
            actionability_reason = "m_and_a_high_confidence"
            excluded_reason = "m_and_a_high_confidence"

        focus_score = total
        edge_launch_phase = _has_edge_launch_phase(
            str(ema_stack_phase) if ema_stack_phase is not None else None,
            str(rwb_squeeze_phase) if rwb_squeeze_phase is not None else None,
            str(long_base_phase) if long_base_phase is not None else None,
        )
        explosive_watch_phase = _has_explosive_watch_phase(
            str(ema_stack_phase) if ema_stack_phase is not None else None,
            str(rwb_squeeze_phase) if rwb_squeeze_phase is not None else None,
        )
        broad_primary = primary.setup_name if primary else None
        broad_primary_is_weak = broad_primary in {"glb", "guppy", "accumulation_base", "ema_stack_launch"}
        if accumulation_score is not None and accumulation_phase in {"breakout_ready"}:
            # Broad accumulation was not release-grade in calibration; only the
            # most mature breakout-ready phase earns a modest Focus lift.
            focus_score = max(
                float(focus_score),
                0.42 * float(accumulation_score) + 0.30 * setup_quality + 0.28 * relative_strength,
            )
        if long_base_score is not None:
            has_real_long_base_footprint = (
                int(demand_spike_count or 0) >= 2
                and int(low_volume_pullback_count or 0) >= 2
                and bool(support_hold_after_demand)
            )
            if long_base_phase in {"accumulating", "launching"} and has_real_long_base_footprint:
                focus_score = max(
                    float(focus_score),
                    0.48 * float(long_base_score) + 0.26 * setup_quality + 0.26 * relative_strength,
                )
                reasons.append(
                    Reason(
                        text=f"focus_long_base_{long_base_phase}_bonus",
                        weight=1.0,
                        category="focus",
                    )
                )
            elif long_base_phase == "extended":
                focus_score = min(float(focus_score), 64.0)
                flags.append(Flag(code="focus_wait_for_pullback:long_base_extended", severity="info"))
        if crash_base_score is not None:
            crash_focus = 0.50 * float(crash_base_score) + 0.25 * setup_quality + 0.25 * relative_strength
            if special_alert_level == "tier1_trendline_breakout":
                crash_focus += 10.0
                reasons.append(Reason(text="focus_crash_base_tier1_trendline_bonus", weight=1.0, category="focus"))
            elif special_alert_level == "tier2_daily_rvol_headsup":
                crash_focus += 6.0
                flags.append(Flag(code="provisional_daily_rvol_headsup", severity="info"))
                reasons.append(Reason(text="focus_crash_base_daily_rvol_bonus", weight=1.0, category="focus"))
            elif special_alert_level == "tier3_low_volume_pullback":
                crash_focus += 3.0
                reasons.append(Reason(text="focus_crash_base_low_pullback_bonus", weight=1.0, category="focus"))
            focus_score = max(float(focus_score), min(100.0, crash_focus))
        slope_bonus = _launch_slope_bonus(launch_30w_slope_state)
        if rwb_squeeze_score is not None:
            rwb_phase_bonus = {
                "watch_squeeze": -8.0,
                "thrusting": 1.0,
                "trendline_breakout": 5.0,
                "confirmed": 4.0,
                "extended": -18.0,
            }.get(str(rwb_squeeze_phase or ""), 0.0)
            rwb_focus = (
                0.42 * float(rwb_squeeze_score)
                + 0.20 * setup_quality
                + 0.26 * relative_strength
                + slope_bonus
                + rwb_phase_bonus
                - _extension_penalty(rwb_extension_above_band_pct)
            )
            if rwb_squeeze_phase == "watch_squeeze":
                rwb_focus = min(rwb_focus, 58.0)
            if rwb_squeeze_phase == "thrusting":
                rwb_focus = min(rwb_focus, 72.0)
            if rwb_squeeze_phase == "extended":
                rwb_focus = min(rwb_focus, 45.0)
            focus_score = max(float(focus_score), rwb_focus)
            if rwb_squeeze_phase in {"thrusting", "trendline_breakout", "confirmed"}:
                reasons.append(
                    Reason(
                        text=f"focus_rwb_{rwb_squeeze_phase}_bonus",
                        weight=1.0,
                        category="focus",
                    )
                )
        if ema_stack_launch_score is not None:
            ema_phase_bonus = {
                "coil_watch": -10.0,
                "early_ignition": 2.0,
                "stack_thrust": 10.0,
                "follow_through": -2.0,
                "failed_thrust": -8.0,
                "extended_leader": -18.0,
            }.get(str(ema_stack_phase or ""), 0.0)
            ema_focus = (
                0.48 * float(ema_stack_launch_score)
                + 0.18 * setup_quality
                + 0.24 * relative_strength
                + slope_bonus
                + ema_phase_bonus
                - _extension_penalty(extension_above_stack_pct)
            )
            if ema_stack_phase == "coil_watch":
                ema_focus = min(ema_focus, 60.0)
            if ema_stack_phase == "early_ignition":
                ema_focus = min(ema_focus, 78.0)
            if ema_stack_phase in {"failed_thrust", "extended_leader"}:
                ema_focus = min(ema_focus, 58.0)
            focus_score = max(float(focus_score), ema_focus)
            if ema_stack_phase in {"early_ignition", "stack_thrust"}:
                reasons.append(
                    Reason(
                        text=f"focus_ema_stack_{ema_stack_phase}_bonus",
                        weight=1.0,
                        category="focus",
                    )
                )
            if ema_stack_phase == "extended_leader":
                flags.append(Flag(code="focus_wait_for_pullback:ema_stack_extended_leader", severity="info"))
        if ma_cluster_score is not None:
            ma_cluster_focus = (
                0.50 * float(ma_cluster_score)
                + 0.22 * setup_quality
                + 0.20 * relative_strength
                + _launch_slope_bonus(_slope_state(ma_cluster_weekly_30w_slope_pct))
                - _extension_penalty(ma_cluster_distance_from_top_pct)
            )
            if ma_cluster_phase == "pre_breakout":
                ma_cluster_focus = min(ma_cluster_focus, 68.0)
            elif ma_cluster_phase == "weak_breakout":
                ma_cluster_focus = min(ma_cluster_focus, 72.0)
            elif ma_cluster_phase == "extended":
                ma_cluster_focus = min(ma_cluster_focus, 45.0)
            focus_score = max(float(focus_score), ma_cluster_focus)
            if ma_cluster_phase in {"one_day_thrust", "follow_through"}:
                reasons.append(
                    Reason(
                        text=f"focus_ma_cluster_{ma_cluster_phase}_bonus",
                        weight=1.0,
                        category="focus",
                    )
                )
        if broad_primary_is_weak and not edge_launch_phase and not explosive_watch_phase:
            focus_score = min(float(focus_score), 62.0)
            flags.append(Flag(code=f"focus_downweight:broad_{broad_primary}", severity="info"))
        if explosive_watch_phase and not edge_launch_phase:
            focus_score = min(float(focus_score), 78.0)
            flags.append(Flag(code="focus_watch_only:explosive_low_sample", severity="info"))

        stage = features.get("weinstein_stage")
        stage_substage = features.get("weinstein_substage")
        stage_quality = features.get("weinstein_stage_quality_score")
        stage_trade_bias = features.get("weinstein_stage_trade_bias")
        stage_ext_pct = features.get("weinstein_ext_pct")
        stage_volume_confirms = features.get("weinstein_volume_confirms")
        stage_origin = features.get("weinstein_stage_origin")
        long_term_context = features.get("long_term_context")
        secular_recovery_score = features.get("secular_recovery_score")
        stage_resistance_zone_high = features.get("weinstein_resistance_zone_high")
        stage_support_zone_low = features.get("weinstein_support_zone_low")
        is_secular_recovery = (
            stage == 1
            and stage_substage == "1C_pre_breakout"
            and stage_origin == "from_decline"
            and long_term_context == "secular_recovery"
        )
        stage2_fresh = stage_substage in {"2A_fresh_breakout", "2A_confirmed_breakout"}
        stage2_unconfirmed = (
            stage_substage == "2A_unconfirmed_breakout"
            or (stage_substage == "2A_fresh_breakout" and stage_volume_confirms is False)
        )
        if stage2_fresh and not stage2_unconfirmed:
            if stage_ext_pct is not None and float(stage_ext_pct) <= 25.0:
                focus_score = min(100.0, float(focus_score) + 8.0)
                reasons.append(Reason(text="focus_stage2_confirmed_breakout_fresh_bonus", weight=1.0, category="focus"))
            else:
                focus_score = min(100.0, float(focus_score) + 3.0)
                flags.append(Flag(code="stage2_confirmed_but_extended", severity="info"))
                reasons.append(Reason(text="focus_stage2_confirmed_breakout_bonus", weight=1.0, category="focus"))
        elif stage2_unconfirmed:
            focus_score = min(float(focus_score), 70.0)
            flags.append(Flag(code="stage2_breakout_unconfirmed_volume", severity="info"))
        # `focus_high_quality_stage1_base_bonus` used to add +4 here for stage 1
        # with `stage_quality_score >= 65` and any accumulation / long-base /
        # EMA-stack phase. Measured 2026-08-01 under the rules in
        # docs/prereg/2026-08-01-stage-quality.md, that gate fired on 68% of
        # stage 1 in sample and 71% held out - entirely 1B_mature_base and
        # 1C_pre_breakout - and the names it selected scored **worse** than the
        # ones it passed over: -0.83 in sample and -0.68 held out. Every
        # threshold from 50 to 80 is negative in both blocks and it gets worse
        # as the threshold rises (-1.72 held out at 80), so it was never a
        # mis-set number; the quantity points the wrong way. Within 1B and 1C
        # the rank correlation between quality and outcome is -0.41 and -0.66
        # held out, which is the same fact seen from the other side.
        #
        # Deleted rather than inverted. Inverting would fit a rule to the dates
        # that just judged the old one. Whether the phase condition alone
        # deserves a bonus is a separate registered question, in the backlog.
        if (
            stage == 1
            and stage_substage in {"1A_early_base", "1B_mature_base", "1C_pre_breakout", "1-recovery"}
            and actionability in {"watch", "forming", "near_actionable", "actionable_now"}
        ):
            focus_score = min(100.0, float(focus_score) + 3.0)
            reasons.append(Reason(text="focus_stage1_recovery_context_bonus", weight=1.0, category="focus"))
        if is_secular_recovery and secular_recovery_score is not None:
            focus_score = max(
                float(focus_score),
                0.46 * float(secular_recovery_score) + 0.24 * setup_quality + 0.20 * relative_strength + 0.10 * volume_confirmation,
            )
            reasons.append(Reason(text="focus_secular_recovery_watch_bonus", weight=1.0, category="focus"))
        if stage == 3:
            flags.append(Flag(code="stage3_distribution_context", severity="info"))
        if stage_trade_bias == "short" and stage == 4:
            focus_score = min(float(focus_score), 58.0)
            flags.append(Flag(code="stage4_short_context_not_standalone", severity="warning"))

        # The two headline levels, decided once and then used everywhere: the
        # alert wording, the chart line and the row all have to agree, and an
        # alert at a level the chart refuses to draw is the worst of the three.
        headline_trigger = clamp_trigger(
            features.get("close"),
            primary.trigger_level if primary else None,
            distance_to_52w_high_pct=features.get("distance_to_52w_high_pct"),
        )
        headline_invalidation = clamp_invalidation(
            features.get("close"),
            primary.invalidation_level if primary else None,
            support=stage_support_zone_low,
        )
        trade_plan = calculate_trade_plan(
            price=features.get("close"),
            atr20=features.get("atr20"),
            trigger_reference_level=primary.trigger_level if primary else None,
            structural_invalidation_level=(
                primary.invalidation_level if primary else None
            ),
        )

        alert_suggestions: list[str] = []

        def suggest(code: str) -> None:
            if code not in alert_suggestions:
                alert_suggestions.append(code)

        # A price-cross alert is only actionable while the raw detector trigger
        # is still overhead. Once crossed, the trade plan owns the fresh/retest
        # state and a legacy headline crossing alert would wait for a second,
        # unrelated cross.
        if (
            trade_plan.status == "trigger_pending"
            and trade_plan.trigger_reference_level is not None
        ):
            suggest(f"price_crosses_{trade_plan.trigger_reference_level:.2f}")
        # Keep the stable alert identifier, but anchor it on the detector's
        # actual thesis boundary. The bounded legacy headline value is only a
        # presentation aid and must never masquerade as structural invalidation.
        structural_alert_level = trade_plan.structural_invalidation_level
        alert_price = _finite_float(features.get("close"))
        if (
            structural_alert_level is not None
            and alert_price is not None
            and alert_price > 0
            and structural_alert_level < alert_price
        ):
            suggest(f"close_below_{structural_alert_level:.2f}")
        if accumulation_phase in ("tightening", "transitioning", "breakout_ready"):
            suggest("accumulation_phase_improves")
        if accumulation_phase in ("transitioning", "breakout_ready"):
            suggest("stage1_to_stage2_confirmation")
        if long_base_phase in ("compression", "launching"):
            suggest("long_base_launch_transition")
        if special_alert_level == "tier1_trendline_breakout":
            suggest("crash_base_weekly_trendline_breakout")
        elif special_alert_level == "tier2_daily_rvol_headsup":
            suggest("crash_base_daily_rvol_headsup")
        elif special_alert_level == "tier3_low_volume_pullback":
            suggest("crash_base_low_volume_pullback")
        if rwb_squeeze_phase == "watch_squeeze":
            suggest("rwb_squeeze_volume_thrust")
        if rwb_squeeze_phase in ("thrusting", "trendline_breakout", "confirmed"):
            suggest("rwb_squeeze_thrust_confirmation")
        if ema_stack_phase == "coil_watch":
            suggest("ema_stack_ignition_thrust")
        if ema_stack_phase in ("early_ignition", "stack_thrust", "follow_through"):
            suggest("ema_stack_launch_follow_through")
        if ma_cluster_phase == "pre_breakout":
            suggest("ma_cluster_volume_breakout_trigger")
        if ma_cluster_phase in ("one_day_thrust", "follow_through"):
            suggest("ma_cluster_follow_through")
        if dryup_near_pivot:
            suggest("volume_expansion_after_dryup")
        if features.get("bars_since_ema_cross_up") is not None:
            suggest("fresh_ema10_20_cross")
        if features.get("rs_line_at_52w_high") is True:
            suggest("rs_line_52w_high")
        if is_secular_recovery:
            breakout_level = _finite_float(stage_resistance_zone_high)
            if breakout_level is None:
                breakout_level = _finite_float(headline_trigger)
            if breakout_level is not None:
                suggest(f"stage2_breakout_above_{breakout_level:.2f}")
            support_level = _finite_float(stage_support_zone_low)
            if support_level is not None:
                suggest(f"range_low_touch_{support_level:.2f}")
            suggest("secular_recovery_stage2_confirmation")
            suggest("secular_recovery_rvol_thrust")

        # Derived last, from the finished flag list, so there is exactly one
        # definition of "risky" for the UI, the report and Telegram to read.
        risk_level, risk_reasons = assess_risk(flags)

        return Candidate(
            ticker=ticker,
            as_of=str(features.get("as_of") or ""),
            price=features.get("close"),
            sector=sector,
            industry=industry,
            sma50=features.get("sma50"),
            sma150=features.get("sma150"),
            sma200=features.get("sma200"),
            distance_to_52w_high_pct=features.get("distance_to_52w_high_pct"),
            rs_score_3m=features.get("rs_score_3m"),
            rs_score_6m=features.get("rs_score_6m"),
            rs_score_weighted=features.get("rs_score_weighted"),
            rs_rating=features.get("rs_rating"),
            rs_line_at_52w_high=features.get("rs_line_at_52w_high"),
            rs_line_52w_distance_pct=features.get("rs_line_52w_distance_pct"),
            # "Blue dot": RS line is at a new 52w high while price is NOT yet at its
            # own 52w high — the strongest O'Neil/Weinstein Stage-2 leadership tell.
            rs_new_high_before_price=bool(
                features.get("rs_line_at_52w_high") is True
                and (features.get("distance_to_52w_high_pct") is not None)
                and float(features.get("distance_to_52w_high_pct")) < -1.0
            ),
            mansfield_rs=features.get("mansfield_rs"),
            avg_dollar_volume_50d=features.get("avg_dollar_volume_50d"),
            atr20=features.get("atr20"),
            volume_ratio_50d=features.get("volume_ratio_50d"),
            adr_pct=features.get("adr20_pct"),
            ret_1d_pct=features.get("ret_1d_pct"),
            ret_1m_pct=features.get("ret_1m_pct"),
            ret_3m_pct=features.get("ret_3m_pct"),
            ret_6m_pct=features.get("ret_6m_pct"),
            rvol_today=features.get("rvol_today"),
            pocket_pivot=features.get("pocket_pivot"),
            up_down_vol_ratio_50d=features.get("up_down_vol_ratio_50d"),
            accumulation_score=accumulation_score,
            accumulation_phase=str(accumulation_phase) if accumulation_phase is not None else None,
            institutional_footprint_score=institutional_footprint_score,
            base_quality_score=base_quality_score,
            sma_compression_pct=sma_compression_pct,
            support_volume_events=int(support_volume_events) if support_volume_events is not None else None,
            dryup_after_accumulation=bool(dryup_after_accumulation) if dryup_after_accumulation is not None else None,
            base_length_bars=int(base_length_bars) if base_length_bars is not None else None,
            base_depth_pct=base_depth_pct,
            long_base_score=long_base_score,
            long_base_phase=str(long_base_phase) if long_base_phase is not None else None,
            demand_spike_count=int(demand_spike_count) if demand_spike_count is not None else None,
            low_volume_pullback_count=int(low_volume_pullback_count) if low_volume_pullback_count is not None else None,
            demand_supply_ratio=demand_supply_ratio,
            dryup_near_pivot=bool(dryup_near_pivot) if dryup_near_pivot is not None else None,
            weekly_base_length_bars=int(weekly_base_length_bars) if weekly_base_length_bars is not None else None,
            monthly_base_length_bars=int(monthly_base_length_bars) if monthly_base_length_bars is not None else None,
            support_hold_after_demand=bool(support_hold_after_demand) if support_hold_after_demand is not None else None,
            guppy_compression_state=str(guppy_compression_state) if guppy_compression_state is not None else None,
            rs_turning_up=bool(rs_turning_up) if rs_turning_up is not None else None,
            crash_base_score=crash_base_score,
            crash_base_phase=str(crash_base_phase) if crash_base_phase is not None else None,
            drawdown_5y_pct=drawdown_5y_pct,
            base_age_weeks=int(base_age_weeks) if base_age_weeks is not None else None,
            resistance_attempt_count=int(resistance_attempt_count) if resistance_attempt_count is not None else None,
            trendline_attempt_count=int(trendline_attempt_count) if trendline_attempt_count is not None else None,
            trendline_breakout_5y=bool(trendline_breakout_5y) if trendline_breakout_5y is not None else None,
            weekly_breakout_rvol=weekly_breakout_rvol,
            daily_rvol_headsup=bool(daily_rvol_headsup) if daily_rvol_headsup is not None else None,
            special_alert_level=str(special_alert_level) if special_alert_level is not None else None,
            weinstein_stage=int(stage) if stage is not None else None,
            weinstein_substage=str(stage_substage) if stage_substage is not None else None,
            stage_note=stage_note(stage_substage) or None,
            weinstein_stage_quality_score=stage_quality,
            weinstein_stage_trade_bias=str(stage_trade_bias) if stage_trade_bias is not None else None,
            weinstein_stage_range_state=str(features.get("weinstein_stage_range_state")) if features.get("weinstein_stage_range_state") is not None else None,
            weinstein_stage_origin=str(stage_origin) if stage_origin is not None else None,
            weinstein_ext_pct=stage_ext_pct,
            weinstein_range_104w_pos=features.get("weinstein_range_104w_pos"),
            weinstein_range_156w_pos=features.get("weinstein_range_156w_pos"),
            weinstein_range_260w_pos=features.get("weinstein_range_260w_pos"),
            weinstein_range_520w_pos=features.get("weinstein_range_520w_pos"),
            weinstein_support_zone_low=stage_support_zone_low,
            weinstein_resistance_zone_high=stage_resistance_zone_high,
            long_term_context=str(long_term_context) if long_term_context is not None else None,
            secular_recovery_score=secular_recovery_score,
            near_secular_resistance=bool(features.get("near_secular_resistance")) if features.get("near_secular_resistance") is not None else None,
            long_term_drawdown_pct=features.get("long_term_drawdown_pct"),
            long_term_recovery_from_low_pct=features.get("long_term_recovery_from_low_pct"),
            long_term_vs_prior_high_pct=features.get("long_term_vs_prior_high_pct"),
            rwb_squeeze_score=rwb_squeeze_score,
            rwb_squeeze_phase=str(rwb_squeeze_phase) if rwb_squeeze_phase is not None else None,
            weekly_rwb_state=str(weekly_rwb_state) if weekly_rwb_state is not None else None,
            weekly_rwb_band_width_pct=weekly_rwb_band_width_pct,
            weekly_short_group_width_pct=weekly_short_group_width_pct,
            weekly_long_group_width_pct=weekly_long_group_width_pct,
            weekly_rwb_spread_pct=weekly_rwb_spread_pct,
            weekly_30w_slope_pct=weekly_30w_slope_pct,
            price_above_rwb_band=bool(price_above_rwb_band) if price_above_rwb_band is not None else None,
            rwb_thrust_rel_volume=rwb_thrust_rel_volume,
            rwb_thrust_close_location=rwb_thrust_close_location,
            prior_rwb_thrust_attempts=int(prior_rwb_thrust_attempts) if prior_rwb_thrust_attempts is not None else None,
            weekly_trendline_breakout=bool(weekly_trendline_breakout) if weekly_trendline_breakout is not None else None,
            rwb_extension_above_band_pct=rwb_extension_above_band_pct,
            launch_30w_slope_pct=launch_30w_slope_pct,
            launch_30w_slope_state=launch_30w_slope_state,
            ema_stack_launch_score=ema_stack_launch_score,
            ema_stack_phase=str(ema_stack_phase) if ema_stack_phase is not None else None,
            recent_coil_date=str(recent_coil_date) if recent_coil_date is not None else None,
            recent_coil_bars_ago=int(recent_coil_bars_ago) if recent_coil_bars_ago is not None else None,
            recent_coil_score=recent_coil_score,
            long_ema_compression_score=long_ema_compression_score,
            short_ema_compression_score=short_ema_compression_score,
            ema_stack_relationship_score=ema_stack_relationship_score,
            ema_stack_thrust_score=ema_stack_thrust_score,
            prior_pressure_score=prior_pressure_score,
            weekly_stack_width_pct=weekly_stack_width_pct,
            weekly_short_ema_width_pct=weekly_short_ema_width_pct,
            weekly_long_ema_width_pct=weekly_long_ema_width_pct,
            current_thrust_rel_volume=current_thrust_rel_volume,
            current_thrust_close_location=current_thrust_close_location,
            price_above_stack_top=bool(price_above_stack_top) if price_above_stack_top is not None else None,
            prior_stack_thrust_attempts=int(prior_stack_thrust_attempts) if prior_stack_thrust_attempts is not None else None,
            extension_above_stack_pct=extension_above_stack_pct,
            ma_cluster_score=ma_cluster_score,
            ma_cluster_phase=str(ma_cluster_phase) if ma_cluster_phase is not None else None,
            ma_cluster_width_pct=ma_cluster_width_pct,
            ma_cluster_top=ma_cluster_top,
            ma_cluster_bottom=ma_cluster_bottom,
            ma_cluster_mas_crossed=int(ma_cluster_mas_crossed) if ma_cluster_mas_crossed is not None else None,
            ma_cluster_breakout_rel_volume_20d=ma_cluster_breakout_rel_volume_20d,
            ma_cluster_breakout_close_location=ma_cluster_breakout_close_location,
            ma_cluster_breakout_age_bars=int(ma_cluster_breakout_age_bars) if ma_cluster_breakout_age_bars is not None else None,
            ma_cluster_distance_from_top_pct=ma_cluster_distance_from_top_pct,
            ma_cluster_structural_stop_distance_pct=ma_cluster_structural_stop_distance_pct,
            ma_cluster_sma200_slope_pct=ma_cluster_sma200_slope_pct,
            ma_cluster_weekly_30w_slope_pct=ma_cluster_weekly_30w_slope_pct,
            ma_cluster_held_above=bool(ma_cluster_held_above) if ma_cluster_held_above is not None else None,
            ma_cluster_research=ma_cluster_research,
            focus_score=round(max(0.0, min(100.0, float(focus_score))), 1),
            alert_suggestions=alert_suggestions,
            setups={
                s.setup_name: SetupSummary(
                    setup_name=s.setup_name,
                    triggered=s.triggered,
                    sub_state=s.sub_state,
                    score=s.score,
                    trigger_level=s.trigger_level,
                    invalidation_level=s.invalidation_level,
                    reasons=s.reasons,
                    failed_conditions=s.failed_conditions,
                    warning_flags=s.warning_flags,
                    raw_features=s.raw_features,
                )
                for s in setups
            },
            score=round(total, 1),
            score_breakdown=breakdown,
            reasons=reasons,
            flags=flags,
            primary_setup=primary.setup_name if primary else None,
            trade_plan=trade_plan,
            # Both bounded above, after every detector has had its say: each has
            # its own idea of structure and most are right about it, but
            # `crash_base_stage1` anchored its stop on the five-year base low,
            # which is correct while price sits in the base and absurd once it
            # has run - on 2026-07-31 its median stop was 230.8% away and DAVE's
            # was at 4.33 under a price of 372.69. Unusable as a decision, and
            # the reason the charts looked wrong: a line two orders of magnitude
            # below the candles forces the y-axis to span both. The per-setup
            # breakdown below still carries what each detector proposed.
            trigger_level=headline_trigger,
            invalidation_level=headline_invalidation,
            provider_used=provider_used,
            fallback_triggered=fallback_triggered,
            data_status="OK",
            actionability=actionability,
            actionability_reason=actionability_reason,
            excluded_reason=excluded_reason,
            m_and_a_confidence=m_and_a_conf,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
        )
