"""Full-universe Weinstein stage classification (Stage 1/2/3/4).

Range-first interpretation:

  Stage 1  Basing / accumulation  - price is still inside a valid support /
                                     resistance range after a decline.
  Stage 2  Advancing / markup     - price has broken above range resistance,
                                     ideally on volume, or is in a clear trend
                                     above a rising 30-week SMA.
  Stage 3  Topping / distribution - price is still inside a valid range after
                                     an advance.
  Stage 4  Declining / markdown   - price has broken below support, or is in a
                                     clear markdown below a falling 30-week SMA.

The 30-week SMA remains the anchor, but while price is respecting a range, the
range dominates the classification. This avoids forcing Stage 2/4 solely because
the 30-week SMA is slightly rising/falling inside the box.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from stock_scout.config.schema import StageAnalysisConfig
from stock_scout.indicators.moving_averages import sma

STAGE_NAMES = {1: "Basing", 2: "Advancing", 3: "Topping", 4: "Declining"}

_LONG_TERM_ORIGIN_LOOKBACK_WEEKS = 520
_LONG_TERM_ORIGIN_MIN_WEEKS = 104
_LONG_TERM_CRASH_DRAWDOWN_PCT = 50.0
_LONG_TERM_RECOVERY_FROM_LOW_PCT = 25.0
_LONG_TERM_STILL_BELOW_HIGH_PCT = -25.0


@dataclass
class _RangeInfo:
    support: float
    resistance: float
    support_touches: int
    resistance_touches: int
    age_weeks: int
    depth_pct: float
    range_position: float
    state: str
    whipsaw_count: int
    origin: str
    quality: float


@dataclass
class _LongTermRecoveryContext:
    context_years: float
    prior_high: float
    low_after_high: float
    drawdown_pct: float
    recovery_from_low_pct: float
    end_vs_prior_high_pct: float


@dataclass
class _StageContext:
    cfg: StageAnalysisConfig
    last_close: float
    last_sma: float
    ext_pct: float
    above: bool
    ma_direction: str
    slope_recent: float
    slope_prior: float | None
    price_slope_recent: float | None
    range_info: _RangeInfo | None
    stage_range_state: str
    support: float | None
    resistance: float | None
    range_pos: float | None
    range_104w_pos: float | None
    range_156w_pos: float | None
    range_260w_pos: float | None
    range_520w_pos: float | None
    range_position: float | None
    stage_origin: str
    mansfield_sign: int | None
    mansfield_rising: bool | None
    broke_resistance: bool | None
    breakout_volume_confirmed: bool | None
    current_volume_ratio: float | None
    weeks_since_breakout: int
    stage1_recovery_context: bool
    long_term_recovery_context: bool


def _slope_pct(series: pd.Series, lookback: int, offset: int = 0) -> float | None:
    """% change of `series` over `lookback` bars, ending `offset` bars before the last bar."""
    idx_end = -1 - offset
    idx_start = idx_end - lookback
    if len(series) < lookback + offset + 1:
        return None
    a = series.iloc[idx_start]
    b = series.iloc[idx_end]
    if pd.isna(a) or pd.isna(b) or a == 0:
        return None
    return (b - a) / abs(a) * 100.0


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _cluster_count(mask: pd.Series) -> int:
    """Count separated touch clusters instead of every consecutive week."""
    count = 0
    in_cluster = False
    for value in mask.fillna(False).astype(bool):
        if value and not in_cluster:
            count += 1
            in_cluster = True
        elif not value:
            in_cluster = False
    return count


def _cross_count(close: pd.Series, ma: pd.Series) -> int:
    aligned = pd.concat([close, ma], axis=1).dropna()
    if len(aligned) < 2:
        return 0
    above = aligned.iloc[:, 0] > aligned.iloc[:, 1]
    return int((above.astype(int).diff().abs() == 1).sum())


def _range_position(price: float, support: float, resistance: float) -> float | None:
    if resistance <= support:
        return None
    return max(0.0, min(1.0, (price - support) / (resistance - support)))


def _long_term_recovery_context(wclose: pd.Series, start_idx: int) -> _LongTermRecoveryContext | None:
    """Detect crash-base context that a short range-origin window can miss."""
    context = wclose.iloc[max(0, start_idx - _LONG_TERM_ORIGIN_LOOKBACK_WEEKS) : start_idx + 1].dropna()
    if len(context) < _LONG_TERM_ORIGIN_MIN_WEEKS:
        return None

    prior_high = float(context.max())
    high_at = context.idxmax()
    after_high = context.loc[high_at:].dropna()
    if after_high.empty:
        return None

    low_after_high = float(after_high.min())
    end = float(context.iloc[-1])
    if prior_high <= 0 or low_after_high <= 0 or end <= 0:
        return None

    drawdown_pct = (prior_high - low_after_high) / prior_high * 100.0
    end_vs_high_pct = (end - prior_high) / prior_high * 100.0
    recovery_from_low_pct = (end - low_after_high) / low_after_high * 100.0
    if not (
        drawdown_pct >= _LONG_TERM_CRASH_DRAWDOWN_PCT
        and end_vs_high_pct <= _LONG_TERM_STILL_BELOW_HIGH_PCT
        and recovery_from_low_pct >= _LONG_TERM_RECOVERY_FROM_LOW_PCT
    ):
        return None
    return _LongTermRecoveryContext(
        context_years=len(context) / 52.0,
        prior_high=prior_high,
        low_after_high=low_after_high,
        drawdown_pct=drawdown_pct,
        recovery_from_low_pct=recovery_from_low_pct,
        end_vs_prior_high_pct=end_vs_high_pct,
    )


def _looks_like_long_term_recovery(wclose: pd.Series, start_idx: int) -> bool:
    return _long_term_recovery_context(wclose, start_idx) is not None


def _origin_from_prior_move(wclose: pd.Series, start_idx: int) -> str:
    """Best-effort origin of the range: did it follow a decline or an advance?"""
    if start_idx <= 4:
        return "ambiguous"
    prior = wclose.iloc[max(0, start_idx - 52) : start_idx + 1].dropna()
    if len(prior) < 8:
        return "ambiguous"
    start = float(prior.iloc[0])
    end = float(prior.iloc[-1])
    if start <= 0:
        return "ambiguous"
    change_pct = (end - start) / abs(start) * 100.0
    drawdown_from_prior_high = (end - float(prior.max())) / max(1e-9, float(prior.max())) * 100.0
    advance_from_prior_low = (end - float(prior.min())) / max(1e-9, float(prior.min())) * 100.0
    if change_pct <= -8.0 or drawdown_from_prior_high <= -18.0:
        return "from_decline"
    if _looks_like_long_term_recovery(wclose, start_idx):
        return "from_decline"
    if change_pct >= 12.0 or advance_from_prior_low >= 25.0:
        return "from_advance"
    return "ambiguous"


def _detect_trading_range(
    wclose: pd.Series,
    sma30: pd.Series,
    cfg: StageAnalysisConfig,
) -> _RangeInfo | None:
    """Detect a support/resistance box that price is still respecting or just broke."""
    if len(wclose) < cfg.range_min_age_weeks + 1:
        return None

    last_close = float(wclose.iloc[-1])
    tol = cfg.support_resistance_tolerance_pct / 100.0
    break_buf = max(cfg.range_break_buffer_pct / 100.0, tol)
    lookbacks = [
        cfg.range_secular_lookback_weeks,
        cfg.range_long_lookback_weeks,
        cfg.range_normal_lookback_weeks,
        cfg.range_short_lookback_weeks,
    ]
    best: _RangeInfo | None = None

    for lookback in lookbacks:
        if lookback < cfg.range_min_age_weeks or len(wclose) < lookback + 1:
            continue
        base = wclose.iloc[-(lookback + 1) : -1].dropna()
        if len(base) < cfg.range_min_age_weeks:
            continue
        support = float(base.min())
        resistance = float(base.max())
        if support <= 0 or resistance <= support:
            continue
        depth_pct = (resistance - support) / support * 100.0
        if depth_pct > cfg.range_max_depth_pct:
            continue

        support_touches = _cluster_count(base <= support * (1.0 + tol))
        resistance_touches = _cluster_count(base >= resistance * (1.0 - tol))
        if support_touches < cfg.min_support_touches or resistance_touches < cfg.min_resistance_touches:
            continue

        breakout = last_close > resistance * (1.0 + break_buf)
        breakdown = last_close < support * (1.0 - break_buf)
        inside = not breakout and not breakdown
        state = "breaking_out" if breakout else "breaking_down" if breakdown else "inside_range"
        pos = _range_position(last_close, support, resistance)
        if pos is None:
            continue

        start_idx = len(wclose) - lookback - 1
        ma_slice = sma30.iloc[start_idx:]
        close_slice = wclose.iloc[start_idx:]
        whipsaws = _cross_count(close_slice, ma_slice)
        origin = _origin_from_prior_move(wclose, start_idx)

        touch_score = min(28.0, (support_touches + resistance_touches) * 4.0)
        age_score = min(22.0, lookback / cfg.range_long_lookback_weeks * 22.0)
        whipsaw_score = min(16.0, whipsaws * 4.0)
        depth_score = 16.0 if 10.0 <= depth_pct <= 45.0 else 10.0 if depth_pct <= 65.0 else 4.0
        origin_score = 10.0 if origin != "ambiguous" else 3.0
        state_score = 8.0 if state in {"inside_range", "breaking_out", "breaking_down"} else 0.0
        quality = max(0.0, min(100.0, touch_score + age_score + whipsaw_score + depth_score + origin_score + state_score))

        info = _RangeInfo(
            support=support,
            resistance=resistance,
            support_touches=support_touches,
            resistance_touches=resistance_touches,
            age_weeks=int(lookback),
            depth_pct=depth_pct,
            range_position=pos,
            state=state,
            whipsaw_count=whipsaws,
            origin=origin,
            quality=quality,
        )
        if best is None or info.quality > best.quality:
            best = info

    return best


def _upper_long_term_range(*positions: float | None) -> float | None:
    """Highest known long-horizon range position, or None if none are known.

    Folding None to 0.0 would assert "sitting at the bottom of its multi-year
    range", which is a claim, not an absence. Callers that need a number for
    scoring can still coalesce; callers that gate on a threshold must not.
    """
    known = [p for p in positions if p is not None]
    return max(known) if known else None


def _is_rolling_over(ctx: _StageContext) -> bool:
    """Has the 30-week MA lost its upward slope? Weinstein pp.36-37.

    A top begins when the MA flattens and price starts tiptoeing through it, so
    a *clearly* rising MA is never a top. Two conditions, both already in the
    context and neither costing a new computation:

    * the slope is at or under `topping_max_slope_mult` x the flat band, and
    * it is no longer steepening.

    The deceleration half is the weaker of the two and is deliberately allowed
    to pass when `slope_prior` is unavailable: an absent reading is not
    evidence, here as everywhere else in this project. `slope_prior_pct` is now
    written into the stage record so the next pass can measure what that half
    is worth on its own - it could not be measured before, because it was never
    recorded.
    """
    ceiling = ctx.cfg.flat_slope_threshold_pct * ctx.cfg.topping_max_slope_mult
    if ctx.slope_recent > ceiling:
        return False
    if ctx.slope_prior is not None and ctx.slope_recent > ctx.slope_prior:
        return ctx.slope_recent <= ctx.cfg.flat_slope_threshold_pct
    return True


def _detect_main_stage(ctx: _StageContext) -> tuple[int, str]:
    """Route into the primary Weinstein regime before deriving any substage."""
    stage_range_state = ctx.stage_range_state
    upper_long_term_range = _upper_long_term_range(
        ctx.range_104w_pos, ctx.range_156w_pos, ctx.range_260w_pos
    )
    long_term_recovery_transition = (
        ctx.long_term_recovery_context
        and ctx.above
        and ctx.ma_direction != "falling"
        and upper_long_term_range is not None
        and upper_long_term_range >= 0.85
        and ctx.ext_pct <= ctx.cfg.extended_up_from_30w_pct * 1.5
        and (ctx.mansfield_sign is None or ctx.mansfield_sign >= 0)
    )
    if ctx.range_info is not None and stage_range_state == "inside_range":
        if ctx.stage_origin == "from_advance":
            # This branch used to return stage 3 on origin alone, testing the MA
            # slope not at all - and it is the largest single routing defect the
            # conformance audit found. Measured on the 2016+ monthly cache it
            # held 39,718 classifications, 28.6% of them on an MA climbing at
            # more than twice the flat band, i.e. names in an advance filed as
            # tops. Held out, that group ran +1.13 to +4.26 at 3m while the
            # genuinely flat remainder ran -1.36; blending the two is what made
            # stage 3 useless as an avoid signal.
            if _is_rolling_over(ctx):
                return 3, stage_range_state
            return 2, stage_range_state
        if ctx.stage_origin == "from_decline":
            return 1, stage_range_state
        # Ambiguous ranges break the 1-vs-3 tie on position in the range, which
        # is structural: low in the multi-year range is a base, high in it is a
        # top. Relative strength used to vote here too and no longer does -
        # RS sets quality, it never selects the stage.
        if (ctx.range_position or 0.0) >= 0.65:
            return 3, stage_range_state
        return 1, stage_range_state
    if ctx.range_info is not None and stage_range_state == "breaking_out":
        if long_term_recovery_transition and ctx.breakout_volume_confirmed is not True:
            return 1, "inside_range"
        return 2, stage_range_state
    if ctx.range_info is not None and stage_range_state == "breaking_down":
        return 4, stage_range_state
    if ctx.broke_resistance and ctx.above:
        if long_term_recovery_transition and ctx.breakout_volume_confirmed is not True:
            return 1, stage_range_state
        return 2, "breaking_out"
    if ctx.support is not None:
        break_buf = max(ctx.cfg.range_break_buffer_pct, ctx.cfg.support_resistance_tolerance_pct) / 100.0
        if ctx.last_close < ctx.support * (1.0 - break_buf):
            return 4, "breaking_down"
    if long_term_recovery_transition:
        return 1, stage_range_state
    if ctx.ma_direction == "rising":
        # Price above a rising 30-week MA, with no range found to place it in.
        # Whether RS may veto stage 2 here was measured on 2016+ rather than
        # argued. Dropping the veto outright moved 5,008 pairs and they split in
        # two by `stage_origin`, which on this path is exactly
        # `long_term_recovery_context` - see the assignment near the bottom of
        # `classify_stage`, where a missing range falls back to it.
        #
        #   recovering from a long decline  1,946 pairs   +1.39 / +5.02 at 3m/6m
        #   no recovery context             3,062 pairs   -0.65 / -1.10
        #
        # A stock breaking out of a multi-year base lags the index by
        # construction - it has just spent years going nowhere - so vetoing it
        # on RS is what filed turnarounds as tops. Without that context, a
        # rising MA plus negative RS is a laggard drifting up in a strong
        # market, and the veto earns its place: those names beat neither stage 2
        # nor stage 3.
        if ctx.above and (
            ctx.stage_origin != "ambiguous"
            or ctx.mansfield_sign is None
            or ctx.mansfield_sign >= 0
        ):
            return 2, stage_range_state
        return 3, stage_range_state
    if ctx.ma_direction == "falling":
        return (1, stage_range_state) if ctx.stage1_recovery_context else (4, stage_range_state)
    if ctx.ext_pct > ctx.cfg.flat_near_band_pct:
        return 2, stage_range_state
    if ctx.ext_pct < -ctx.cfg.flat_near_band_pct:
        return (1, stage_range_state) if ctx.stage1_recovery_context else (4, stage_range_state)
    if ctx.stage_origin == "from_advance":
        return 3, stage_range_state
    if ctx.stage_origin == "from_decline":
        return 1, stage_range_state
    if ctx.range_pos is not None:
        return (3 if ctx.range_pos >= 0.5 else 1), stage_range_state
    return (3 if ctx.above and (ctx.mansfield_sign is None or ctx.mansfield_sign < 0) else 1), stage_range_state


def _is_stage2_extended(ctx: _StageContext) -> bool:
    return ctx.ext_pct > ctx.cfg.extended_up_from_30w_pct or ctx.weeks_since_breakout > 26


def _detect_substage(stage: int, ctx: _StageContext, stage_range_state: str) -> tuple[str, bool]:
    """Derive substage inside an already-selected primary stage."""
    transition = False
    if stage == 2:
        is_fresh_breakout = stage_range_state == "breaking_out" or bool(ctx.broke_resistance)
        # A stage 2 name still inside its box: only the rolling-over test above
        # produces this, since every other route to stage 2 leaves the state as
        # `breaking_out` or `trend` - verified on the 2016+ cache, where
        # stage 2 + inside_range was 0 of 196,859 records before this change.
        #
        # It gets its own label rather than joining 2C on purpose. Most of these
        # are extended enough to satisfy `_is_stage2_extended`, so folding them
        # in would move 11,357 records into the one substage the report
        # promotes, and 2C's measured edge would then be a different cohort's
        # under the same name. Held out this group runs +1.58/+2.33 - real, but
        # under 2C's +2.35/+5.25, and flat in sample (-0.24/+0.07). Which is
        # also why it is NOT in STAGE_FAVOURED: that list means positive in both
        # blocks, and this is positive in one.
        if stage_range_state == "inside_range":
            return "2D_advance_consolidation", transition
        if _is_stage2_extended(ctx):
            return "2C_extended_advance", transition
        if is_fresh_breakout:
            # The book wants volume on the breakout - roughly twice normal. The
            # classifier has always computed `breakout_volume_confirmed` and
            # never let it decide anything, so the difference was invisible.
            # Splitting the *label* rather than the stage keeps every name on
            # screen and makes the difference countable: 1,227 of 7,318 fresh
            # breakouts clear the 2x floor, 6,091 do not.
            #
            # Deliberately not sold as an edge. Requiring volume tuned to +1.36
            # in sample and fell to -0.71 held out, so this is a label faithful
            # to the book that can now be measured, not a filter that earned its
            # place. `stage_volume_confirms is False` already capped these in
            # the scorer, so scores are unchanged by the rename.
            #
            # Unknown volume keeps the confirmed name: an absent reading is not
            # a failed one, the same rule `_upper_long_term_range` follows. It
            # is 0% of fresh breakouts today, and this is what happens if that
            # ever stops being true.
            #
            # `transition` stays, though the substage now says the same thing.
            # The plan had it removed as redundant, which was written without
            # checking who reads it: StagesView filters on it and
            # AccumulationView has a "Stage 1 -> 2" preset built on it. Dropping
            # it would have emptied a working screen to tidy a field.
            transition = bool(ctx.breakout_volume_confirmed)
            if ctx.breakout_volume_confirmed is False:
                return "2A_unconfirmed_breakout", transition
            return "2A_fresh_breakout", transition
        return "2B_healthy_advance", transition

    if stage == 1:
        upper_long_term_range = _upper_long_term_range(
            ctx.range_104w_pos, ctx.range_156w_pos, ctx.range_260w_pos
        )
        long_term_pre_breakout = (
            ctx.long_term_recovery_context
            and ctx.above
            and ctx.ma_direction != "falling"
            and upper_long_term_range is not None
            and upper_long_term_range >= 0.85
            and ctx.ext_pct <= ctx.cfg.extended_up_from_30w_pct * 1.5
            and (ctx.mansfield_sign is None or ctx.mansfield_sign >= 0)
        )
        pre_breakout = (
            (
                ctx.range_info is not None
                and (ctx.range_position or 0.0) >= 0.72
                and ctx.ext_pct >= -ctx.cfg.flat_near_band_pct
                and (ctx.ma_direction != "falling" or ctx.stage1_recovery_context or ctx.mansfield_rising is True)
            )
            or long_term_pre_breakout
        )
        if pre_breakout:
            return "1C_pre_breakout", transition
        if ctx.range_info is not None:
            return "1B_mature_base", transition
        return "1A_early_base", transition

    if stage == 3:
        rs_weakening = (
            (ctx.mansfield_sign is not None and ctx.mansfield_sign < 0)
            or ctx.mansfield_rising is False
        )
        confirmed = (
            (ctx.ma_direction in {"flat", "falling"} and rs_weakening)
            or (ctx.range_info is not None and (ctx.range_position or 1.0) < 0.45 and rs_weakening)
            or (ctx.range_info is None and ctx.ext_pct <= 5.0)
        )
        return ("3B_confirmed_distribution" if confirmed else "3A_early_distribution"), transition

    if stage == 4:
        if stage_range_state == "breaking_down":
            return "4A_fresh_breakdown", transition
        if (
            ctx.ma_direction in {"falling", "flat"}
            and -8.0 < ctx.ext_pct <= ctx.cfg.flat_near_band_pct
            and stage_range_state == "trend"
        ):
            return "4C_weak_rally", transition
        return "4B_decline", transition

    return "", transition


def classify_stage(
    weekly_close: pd.Series,
    weekly_volume: pd.Series | None,
    weekly_benchmark_close: pd.Series | None,
    cfg: StageAnalysisConfig | None = None,
) -> dict | None:
    """Classify a ticker's current Weinstein stage from weekly closes."""
    if cfg is None:
        cfg = StageAnalysisConfig()
    if weekly_close is None or weekly_close.empty:
        return None
    wclose = weekly_close.dropna()
    need = cfg.weekly_sma_period + cfg.slope_lookback_weeks + 1
    if len(wclose) < need:
        return None

    sma30 = sma(wclose, cfg.weekly_sma_period)
    sma30v = sma30.dropna()
    last_close = float(wclose.iloc[-1])
    last_sma = sma30.iloc[-1]
    if pd.isna(last_sma) or last_sma == 0:
        return None
    last_sma = float(last_sma)
    ext_pct = (last_close - last_sma) / last_sma * 100.0
    stage_context_years = len(wclose) / 52.0

    range_window = wclose.tail(cfg.mansfield_lookback_weeks)
    r_hi = float(range_window.max())
    r_lo = float(range_window.min())
    range_pos = _range_position(last_close, r_lo, r_hi) if r_hi > r_lo else None
    range_52w_pos = range_pos
    def _window_position(weeks: int) -> float | None:
        """Range position over exactly `weeks` of history, or None.

        Truncating to whatever exists (tail(min(weeks, len))) used to fabricate
        a value: a 60-week ticker reported identical 104/156/260/520-week
        positions, all really its 60-week one, with nothing marking them as
        approximations. Those feed the long-horizon routing, so an honest None
        is the only safe answer when the history is not there.
        """
        if len(wclose) < weeks:
            return None
        window = wclose.tail(weeks)
        return _range_position(last_close, float(window.min()), float(window.max()))

    range_104w_pos = _window_position(104)
    range_156w_pos = _window_position(156)
    range_260w_pos = _window_position(260)
    range_520w_pos = _window_position(520)
    # Which window actually backed the long-horizon view, so downstream can tell
    # "not extended" from "we could not know".
    upper_long_term_window_weeks = next(
        (w for w, p in ((520, range_520w_pos), (260, range_260w_pos),
                        (156, range_156w_pos), (104, range_104w_pos)) if p is not None),
        None,
    )

    slope_recent = _slope_pct(sma30v, cfg.slope_lookback_weeks, offset=0)
    slope_prior = _slope_pct(sma30v, cfg.slope_lookback_weeks, offset=cfg.slope_lookback_weeks)
    price_slope_recent = _slope_pct(wclose, cfg.recovery_lookback_weeks, offset=0)
    flat = cfg.flat_slope_threshold_pct
    if slope_recent is None:
        return None
    if slope_recent > flat:
        ma_direction = "rising"
    elif slope_recent < -flat:
        ma_direction = "falling"
    else:
        ma_direction = "flat"
    above = last_close > last_sma

    # Mansfield RS.
    mansfield_rs: float | None = None
    mansfield_rising: bool | None = None
    if weekly_benchmark_close is not None and not weekly_benchmark_close.empty:
        bench = weekly_benchmark_close.reindex(wclose.index, method="ffill")
        rs_line = (wclose / bench.replace(0, pd.NA)).dropna()
        if len(rs_line) >= cfg.mansfield_lookback_weeks + 1:
            rs_ma = sma(rs_line, cfg.mansfield_lookback_weeks)
            mansfield_series = ((rs_line / rs_ma - 1.0) * 100.0).dropna()
            if not mansfield_series.empty:
                mansfield_rs = float(mansfield_series.iloc[-1])
                k = cfg.slope_lookback_weeks
                if len(mansfield_series) > k:
                    mansfield_rising = bool(mansfield_series.iloc[-1] > mansfield_series.iloc[-1 - k])
    mansfield_sign = None if mansfield_rs is None else (1 if mansfield_rs >= 0 else -1)

    # Volume: current week and recent peak vs 30w average.
    breakout_volume_confirmed: bool | None = None
    volume_dryup: bool | None = None
    volume_ratio: float | None = None
    current_volume_ratio: float | None = None
    if weekly_volume is not None and not weekly_volume.empty:
        wvol = weekly_volume.reindex(wclose.index).astype("float64")
        if len(wvol.dropna()) >= cfg.volume_avg_weeks + 1:
            vol_avg = float(wvol.rolling(cfg.volume_avg_weeks).mean().iloc[-1])
            if vol_avg and vol_avg > 0:
                recent_peak = float(wvol.tail(cfg.breakout_volume_lookback_weeks).max())
                cur_vol = float(wvol.iloc[-1])
                volume_ratio = round(recent_peak / vol_avg, 2)
                current_volume_ratio = round(cur_vol / vol_avg, 2)
                breakout_volume_confirmed = recent_peak >= cfg.breakout_volume_mult * vol_avg
                volume_dryup = cur_vol <= cfg.dryup_volume_mult * vol_avg

    # Legacy resistance ceiling remains as a fallback when no full range is detected.
    base_ceiling: float | None = None
    broke_resistance: bool | None = None
    excl = cfg.resistance_exclude_recent_weeks
    window = cfg.resistance_lookback_weeks
    if len(wclose) >= window + excl:
        base_slice = wclose.iloc[-(window + excl) : -excl] if excl > 0 else wclose.iloc[-window:]
        if not base_slice.empty:
            base_ceiling = float(base_slice.max())
            broke_resistance = last_close > base_ceiling * (1.0 + max(cfg.range_break_buffer_pct, cfg.support_resistance_tolerance_pct) / 100.0)

    crossed = (wclose > sma30).astype("float")
    weeks_since_breakout = 0
    for value in crossed.iloc[::-1]:
        if value == 1.0:
            weeks_since_breakout += 1
        else:
            break

    range_info = _detect_trading_range(wclose, sma30, cfg)
    stage_range_state = range_info.state if range_info is not None else "trend"
    support = range_info.support if range_info is not None else None
    resistance = range_info.resistance if range_info is not None else base_ceiling
    long_term_recovery = _long_term_recovery_context(wclose, len(wclose) - 1)
    long_term_recovery_context = long_term_recovery is not None
    stage_origin = range_info.origin if range_info is not None else "from_decline" if long_term_recovery_context else "ambiguous"
    range_position = range_info.range_position if range_info is not None else range_pos
    ma_whipsaw_count = range_info.whipsaw_count if range_info is not None else _cross_count(wclose.tail(52), sma30.tail(52))

    slope_decelerating = slope_prior is not None and slope_recent > slope_prior
    price_recovering = price_slope_recent is not None and price_slope_recent >= cfg.stage1_recovery_min_price_slope_pct
    near_reclaim_zone = -cfg.stage1_recovery_max_below_sma_pct <= ext_pct <= cfg.flat_near_band_pct
    stage1_recovery_context = (
        range_info is not None
        and near_reclaim_zone
        and slope_decelerating
        and (
            above
            or price_recovering
            or (range_pos is not None and range_pos >= cfg.stage1_recovery_min_range_pos)
        )
    )

    ctx = _StageContext(
        cfg=cfg,
        last_close=last_close,
        last_sma=last_sma,
        ext_pct=ext_pct,
        above=above,
        ma_direction=ma_direction,
        slope_recent=slope_recent,
        slope_prior=slope_prior,
        price_slope_recent=price_slope_recent,
        range_info=range_info,
        stage_range_state=stage_range_state,
        support=support,
        resistance=resistance,
        range_pos=range_pos,
        range_104w_pos=range_104w_pos,
        range_156w_pos=range_156w_pos,
        range_260w_pos=range_260w_pos,
        range_520w_pos=range_520w_pos,
        range_position=range_position,
        stage_origin=stage_origin,
        mansfield_sign=mansfield_sign,
        mansfield_rising=mansfield_rising,
        broke_resistance=broke_resistance,
        breakout_volume_confirmed=breakout_volume_confirmed,
        current_volume_ratio=current_volume_ratio,
        weeks_since_breakout=weeks_since_breakout,
        stage1_recovery_context=stage1_recovery_context,
        long_term_recovery_context=long_term_recovery_context,
    )
    stage, stage_range_state = _detect_main_stage(ctx)
    substage, transition = _detect_substage(stage, ctx, stage_range_state)

    volume_confirms: bool | None = None
    if stage == 2 and breakout_volume_confirmed is not None:
        volume_confirms = breakout_volume_confirmed
    elif stage == 1 and volume_dryup is not None:
        volume_confirms = volume_dryup

    range_quality = range_info.quality if range_info is not None else 0.0
    quality = 45.0
    if stage in {1, 3}:
        quality = range_quality
        if volume_confirms is True and stage == 1:
            quality += 6.0
        if stage == 1 and long_term_recovery_context and substage == "1C_pre_breakout":
            upper_long_term_range = _upper_long_term_range(
                range_104w_pos, range_156w_pos, range_260w_pos
            )
            quality = max(quality, 54.0)
            if upper_long_term_range is not None and upper_long_term_range >= 0.85:
                quality += 8.0
            if mansfield_sign is not None and mansfield_sign > 0:
                quality += 7.0 if mansfield_rising is False else 9.0
            if ma_direction == "rising":
                quality += 5.0
            if ext_pct > cfg.extended_up_from_30w_pct:
                quality -= 5.0
        if stage == 3 and mansfield_sign is not None and mansfield_sign < 0:
            quality += 6.0
    elif stage == 2:
        quality = 48.0
        if stage_range_state == "breaking_out":
            quality += 18.0
        if breakout_volume_confirmed:
            quality += 14.0
        if mansfield_sign is not None and mansfield_sign > 0:
            quality += 8.0 if mansfield_rising else 5.0
        if ma_direction == "rising":
            quality += 7.0
        if substage == "2C_extended_advance":
            quality -= 12.0
    elif stage == 4:
        quality = 44.0
        if stage_range_state == "breaking_down":
            quality += 18.0
        if ma_direction == "falling":
            quality += 10.0
        if mansfield_sign is not None and mansfield_sign < 0:
            quality += 8.0
        if ext_pct <= -cfg.extended_down_from_30w_pct:
            quality -= 18.0
    stage_quality_score = round(max(0.0, min(100.0, quality)), 1)

    short_quality = 0.0
    if stage == 4:
        short_quality = 35.0
        if substage == "4A_fresh_breakdown":
            short_quality += 22.0
        if substage == "4C_weak_rally":
            short_quality += 20.0
        if ma_direction == "falling":
            short_quality += 12.0
        if mansfield_sign is not None and mansfield_sign < 0:
            short_quality += 10.0
        if price_slope_recent is not None and price_slope_recent < 0:
            short_quality += 6.0
        if current_volume_ratio is not None and current_volume_ratio >= 1.5:
            short_quality += 5.0
        if ext_pct > cfg.extended_up_from_30w_pct:
            short_quality -= 35.0
        elif ext_pct > cfg.flat_near_band_pct:
            short_quality -= 20.0
        if ext_pct <= -45.0:
            short_quality -= 35.0
        elif ext_pct <= -cfg.extended_down_from_30w_pct:
            short_quality -= 20.0
    elif stage == 3:
        short_quality = 20.0 + (range_quality * 0.35)
        if mansfield_sign is not None and mansfield_sign < 0:
            short_quality += 10.0
        if mansfield_rising is False:
            short_quality += 5.0
    short_quality_score = round(max(0.0, min(100.0, short_quality)), 1)

    if stage == 2:
        stage_trade_bias = "long"
    elif stage == 4:
        stage_trade_bias = "avoid" if short_quality_score < 45 else "short"
    elif stage == 1:
        stage_trade_bias = "long" if stage_quality_score >= 60.0 and (range_position or 0.0) >= 0.5 else "neutral"
    else:
        stage_trade_bias = "neutral"

    confidence = stage_quality_score / 100.0
    if stage == 2 and mansfield_sign is not None and mansfield_sign > 0 and ma_direction == "rising":
        confidence = max(confidence, 0.65)
    if mansfield_sign is None:
        confidence = min(confidence, 0.6)
    if stage == 2 and substage == "2A_fresh_breakout" and breakout_volume_confirmed is False:
        confidence = min(confidence, 0.7)

    upper_long_term_range = _upper_long_term_range(
        range_104w_pos, range_156w_pos, range_260w_pos
    )
    near_secular_resistance = bool(
        (range_info is not None and (range_position or 0.0) >= 0.80)
        or (resistance is not None and last_close >= resistance * 0.90)
        or (upper_long_term_range is not None and upper_long_term_range >= 0.85)
    )
    # A ticker too young for multi-year context is a distinct state from one we
    # looked at and could not characterise. Collapsing both into "ambiguous"
    # hid which was which.
    # Both conditions: enough weeks overall, and at least one multi-year window
    # actually computed rather than skipped for want of data.
    has_long_horizon = (
        upper_long_term_window_weeks is not None
        and stage_context_years >= cfg.min_context_years_for_long_horizon
    )
    long_term_context = (
        "insufficient_history"
        if not has_long_horizon
        else "secular_recovery"
        if long_term_recovery_context
        else "mature_advance"
        if stage_origin == "from_advance" and stage in {2, 3}
        else "ambiguous"
    )
    secular_recovery_score: float | None = None
    if long_term_recovery is not None and has_long_horizon:
        score = 34.0
        score += min(18.0, long_term_recovery.drawdown_pct / 4.0)
        score += min(14.0, long_term_recovery.recovery_from_low_pct / 10.0)
        # No known long-term position earns no bonus, rather than the bottom-of-
        # range reading that `or 0.0` used to assert.
        score += (upper_long_term_range or 0.0) * 16.0
        if stage == 1 and substage == "1C_pre_breakout":
            score += 10.0
        if ma_direction == "rising":
            score += 5.0
        elif ma_direction == "flat":
            score += 3.0
        if mansfield_sign is not None and mansfield_sign > 0:
            score += 5.0 if mansfield_rising is False else 7.0
        if range_info is not None:
            score += min(6.0, range_info.age_weeks / 52.0)
        if ext_pct > cfg.extended_up_from_30w_pct * 1.5:
            score -= 18.0
        elif ext_pct > cfg.extended_up_from_30w_pct:
            score -= 7.0
        secular_recovery_score = round(max(0.0, min(100.0, score)), 1)

    tol = cfg.support_resistance_tolerance_pct / 100.0
    structure_notes: list[str] = []
    if range_info is not None:
        structure_notes.append(f"{stage_range_state}:{range_info.age_weeks}w_range")
        structure_notes.append(f"touches_s{range_info.support_touches}_r{range_info.resistance_touches}")
    if substage:
        structure_notes.append(substage)
    if long_term_recovery_context:
        structure_notes.append("long_term_recovery_context")
    if long_term_context == "secular_recovery":
        structure_notes.append("secular_recovery")
    if breakout_volume_confirmed:
        structure_notes.append("volume_confirms_breakout")
    if stage == 4 and current_volume_ratio is not None and current_volume_ratio >= 1.5:
        structure_notes.append("heavy_breakdown_volume")

    return {
        "stage": stage,
        "stage_name": STAGE_NAMES[stage],
        "substage": substage,
        "transition": transition,
        "price": round(last_close, 2),
        "sma_30w": round(last_sma, 2),
        "ext_pct": round(ext_pct, 2),
        "ma_direction": ma_direction,
        "slope_pct": round(slope_recent, 3),
        # The prior window's slope, so "is the MA decelerating" becomes a
        # measurable question instead of an assumed one. `_is_rolling_over`
        # already uses it; nothing had ever recorded it.
        "slope_prior_pct": _round(slope_prior, 3),
        "price_slope_pct": _round(price_slope_recent, 3),
        "range_pos": _round(range_pos, 3),
        "range_52w_pos": _round(range_52w_pos, 3),
        "range_104w_pos": _round(range_104w_pos, 3),
        "range_156w_pos": _round(range_156w_pos, 3),
        "range_260w_pos": _round(range_260w_pos, 3),
        "range_520w_pos": _round(range_520w_pos, 3),
        "mansfield_rs": _round(mansfield_rs, 2),
        "mansfield_sign": mansfield_sign,
        "mansfield_rising": mansfield_rising,
        "weeks_since_breakout": weeks_since_breakout,
        "volume_ratio": volume_ratio,
        "volume_confirms": volume_confirms,
        "broke_resistance": bool(stage_range_state == "breaking_out" or broke_resistance),
        # Which evidence stands behind that boolean, because it covers two very
        # different things. One is price clearing the top of a range that was
        # tested repeatedly - the event the method is actually built on. The
        # other is price exceeding a 35-week rolling high when no range was
        # found at all, which is what `broke_resistance` falls back to.
        #
        # Measured 2026-07-27: of 19,479 records carrying
        # stage_range_state == "breaking_out", 18,177 (93.3%) had no tested base
        # of any kind, against 0% of the 1,218 breakdowns - breakdowns only ever
        # come from the real range branch. Returns say leave the behaviour
        # alone; the rolling-high group is larger and steadier than the
        # structured one. This field exists so nothing downstream tells a reader
        # a stock broke out of a base when no base was ever found.
        "breakout_basis": (
            "tested_range"
            if stage_range_state == "breaking_out" and range_info is not None
            else "rolling_high"
            if broke_resistance
            else None
        ),
        "base_ceiling": _round(base_ceiling, 2),
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "stage_quality_score": stage_quality_score,
        "stage_trade_bias": stage_trade_bias,
        "stage_range_state": stage_range_state,
        "support_zone_low": _round(support * (1.0 - tol), 2) if support is not None else None,
        "support_zone_high": _round(support * (1.0 + tol), 2) if support is not None else None,
        "resistance_zone_low": _round(resistance * (1.0 - tol), 2) if resistance is not None else None,
        "resistance_zone_high": _round(resistance * (1.0 + tol), 2) if resistance is not None else None,
        "support_touches": range_info.support_touches if range_info is not None else 0,
        "resistance_touches": range_info.resistance_touches if range_info is not None else 0,
        "range_age_weeks": range_info.age_weeks if range_info is not None else 0,
        "range_depth_pct": _round(range_info.depth_pct, 2) if range_info is not None else None,
        "range_position": _round(range_position, 3),
        "ma_whipsaw_count": ma_whipsaw_count,
        "stage_origin": stage_origin,
        "stage_context_years": round(stage_context_years, 2),
        # Which window actually backed the long-horizon read, so a consumer can
        # distinguish "not extended" from "we could not know".
        "upper_long_term_window_weeks": upper_long_term_window_weeks,
        "long_term_context": long_term_context,
        "secular_recovery_score": secular_recovery_score,
        "near_secular_resistance": near_secular_resistance,
        "long_term_prior_high": _round(long_term_recovery.prior_high, 2) if long_term_recovery is not None else None,
        "long_term_low_after_high": _round(long_term_recovery.low_after_high, 2) if long_term_recovery is not None else None,
        "long_term_drawdown_pct": _round(long_term_recovery.drawdown_pct, 2) if long_term_recovery is not None else None,
        "long_term_recovery_from_low_pct": _round(long_term_recovery.recovery_from_low_pct, 2) if long_term_recovery is not None else None,
        "long_term_vs_prior_high_pct": _round(long_term_recovery.end_vs_prior_high_pct, 2) if long_term_recovery is not None else None,
        "breakout_volume_ratio": volume_ratio if stage_range_state == "breaking_out" else None,
        "breakdown_volume_ratio": current_volume_ratio if stage_range_state == "breaking_down" else None,
        "short_quality_score": short_quality_score,
        "structure_notes": structure_notes,
    }
