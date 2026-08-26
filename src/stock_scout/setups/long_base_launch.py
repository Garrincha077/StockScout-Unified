from __future__ import annotations

import math

import pandas as pd

from stock_scout.config.schema import LongBaseLaunchSetupConfig
from stock_scout.data.corporate_actions import detect_m_and_a_from_price
from stock_scout.indicators.guppy import guppy_state
from stock_scout.indicators.highs_lows import close_location_value
from stock_scout.indicators.moving_averages import sma
from stock_scout.setups.base import SetupDetector, SetupResult
from stock_scout.setups.base_analysis import (
    extension_from_pivot,
    find_consolidation_base,
)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm(v: float | int | None, lo: float, hi: float) -> float:
    if v is None or hi <= lo:
        return 0.0
    return _clamp((float(v) - lo) / (hi - lo) * 100.0)


def _weekly_from_daily(df_daily: pd.DataFrame) -> pd.DataFrame:
    return (
        df_daily.resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def _monthly_from_frame(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.resample("ME")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def _safe_rel_volume(volume: pd.Series, window: int = 20, min_periods: int = 8) -> pd.Series:
    avg = volume.rolling(window, min_periods=min_periods).mean().shift(1)
    return volume / avg.mask(avg == 0)


def _guppy_group_widths(close: pd.Series) -> tuple[float | None, float | None]:
    from stock_scout.indicators.guppy import LONG_PERIODS, SHORT_PERIODS
    from stock_scout.indicators.moving_averages import ema

    if close.empty:
        return None, None
    last_close = float(close.iloc[-1])
    if last_close <= 0:
        return None, None
    short = [float(ema(close, p).iloc[-1]) for p in SHORT_PERIODS if pd.notna(ema(close, p).iloc[-1])]
    long = [float(ema(close, p).iloc[-1]) for p in LONG_PERIODS if pd.notna(ema(close, p).iloc[-1])]
    if len(short) != len(SHORT_PERIODS) or len(long) != len(LONG_PERIODS):
        return None, None
    return (max(short) - min(short)) / last_close * 100.0, (max(long) - min(long)) / last_close * 100.0


def _bucket_ratio(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio < 1.0:
        return "lt_1"
    if ratio < 1.5:
        return "1_1p5"
    if ratio < 2.0:
        return "1p5_2"
    return "gte_2"


class LongBaseLaunchDetector(SetupDetector):
    """Weekly long-base launch detector.

    This is separate from the daily accumulation detector. It is tuned for the
    VFF/CVNA/NOK visual pattern: a long weekly base, constructive high-volume
    demand weeks, quiet pullbacks, absorption, Guppy/EMA compression, and a
    launch or near-launch from a multi-month or secular zone.
    """

    name = "long_base_launch"

    def __init__(self, cfg: LongBaseLaunchSetupConfig):
        self.cfg = cfg

    def detect(
        self,
        df_daily: pd.DataFrame,
        df_weekly: pd.DataFrame | None = None,
        features: dict | None = None,
    ) -> SetupResult:
        result = SetupResult(setup_name=self.name, triggered=False)
        if not self.cfg.enabled:
            result.actionability = "not_valid"
            result.actionability_reason = "setup_disabled"
            return result

        if df_daily is None or df_daily.empty or len(df_daily) < 120:
            result.failed_conditions.append("insufficient_daily_history")
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_daily_history"
            return result

        f = features or {}
        ma = detect_m_and_a_from_price(df_daily, ticker=str(f.get("ticker") or self.name))
        if ma.confidence == "high":
            result.disqualifiers.append("m_and_a_high")
            result.failed_conditions.append("m_and_a_high_confidence")
            result.actionability = "excluded"
            result.actionability_reason = "m_and_a_high_confidence"
            return result
        if ma.confidence == "medium":
            result.warning_flags.append("m_and_a_medium")

        weekly = df_weekly.copy() if df_weekly is not None and not df_weekly.empty else _weekly_from_daily(df_daily)
        weekly = weekly.dropna(subset=["close"])
        if len(weekly) < max(65, self.cfg.min_weekly_base_bars + 10):
            result.failed_conditions.append("insufficient_weekly_history")
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_weekly_history"
            return result

        monthly = _monthly_from_frame(weekly)
        monthly_base = (
            find_consolidation_base(
                monthly,
                min_length_bars=self.cfg.secular_monthly_min_bars,
                max_depth_pct=self.cfg.secular_max_base_depth_pct,
                swing_left=1,
                swing_right=1,
            )
            if len(monthly) >= self.cfg.secular_monthly_min_bars + 4
            else None
        )
        monthly_base_length = int(monthly_base.length_bars) if monthly_base is not None else 0
        secular_base = monthly_base_length >= self.cfg.secular_monthly_min_bars

        weekly_base = find_consolidation_base(
            weekly,
            min_length_bars=self.cfg.min_weekly_base_bars,
            max_depth_pct=(
                self.cfg.secular_max_base_depth_pct
                if secular_base
                else self.cfg.max_weekly_base_depth_pct
            ),
            swing_left=2,
            swing_right=2,
        )
        if weekly_base is None:
            result.failed_conditions.append("no_valid_weekly_long_base")
            result.actionability = "not_valid"
            result.actionability_reason = "no_valid_weekly_long_base"
            return result

        close = float(weekly["close"].iloc[-1])
        if close <= 0:
            result.failed_conditions.append("missing_close")
            result.actionability = "not_valid"
            result.actionability_reason = "missing_close"
            return result

        work = weekly.copy()
        rel_vol = _safe_rel_volume(work["volume"])
        clv = close_location_value(work, window=1).fillna(0.5)
        up_week = work["close"] > work["close"].shift(1)
        down_week = work["close"] < work["close"].shift(1)
        inside_week = (work["high"] <= work["high"].shift(1)) & (work["low"] >= work["low"].shift(1))
        prior_8w_high = work["high"].shift(1).rolling(8, min_periods=4).max()

        base_mask = pd.Series(False, index=work.index)
        base_mask.iloc[max(0, weekly_base.start_idx) :] = True
        demand_mask = (
            base_mask
            & up_week
            & (clv >= 0.55)
            & (rel_vol >= self.cfg.demand_volume_mult)
        )
        demand_breakout_mask = demand_mask & (work["close"] >= prior_8w_high * 0.995)
        supply_mask = (
            base_mask
            & down_week
            & (clv <= 0.55)
            & (rel_vol >= 1.0)
        )
        low_volume_pullback_mask = (
            base_mask
            & (down_week | inside_week)
            & (rel_vol <= self.cfg.low_volume_pullback_mult)
            & (work["low"] >= weekly_base.low_in_base * 0.98)
        )

        demand_spike_count = int(demand_mask.fillna(False).sum())
        demand_breakout_count = int(demand_breakout_mask.fillna(False).sum())
        low_volume_pullback_count = int(low_volume_pullback_mask.fillna(False).sum())
        demand_volume = float(work.loc[demand_mask.fillna(False), "volume"].sum())
        supply_volume = float(work.loc[supply_mask.fillna(False), "volume"].sum())
        avg_base_volume = float(work.loc[base_mask, "volume"].mean() or 0.0)
        if supply_volume <= 0:
            supply_volume = avg_base_volume if avg_base_volume > 0 else 1.0
        demand_supply_ratio = demand_volume / max(1.0, supply_volume)

        hold_count = 0
        demand_positions = [int(work.index.get_loc(idx)) for idx in work.index[demand_mask.fillna(False)]]
        for pos in demand_positions:
            event_low = float(work["low"].iloc[pos])
            future_low = float(work["low"].iloc[pos : min(len(work), pos + 5)].min())
            if future_low >= min(event_low * 0.97, weekly_base.low_in_base * 1.03):
                hold_count += 1
        support_hold_after_demand = demand_spike_count > 0 and hold_count >= max(1, math.ceil(demand_spike_count / 2))

        ext_pct, ext_atr = extension_from_pivot(close, weekly_base.pivot_price, f.get("atr20"))
        distance_to_pivot_ok = ext_pct >= -self.cfg.max_distance_below_pivot_pct
        launch_volume_expansion = bool(rel_vol.iloc[-1] >= self.cfg.launch_volume_mult) if pd.notna(rel_vol.iloc[-1]) else False
        close_above_pivot = close >= weekly_base.pivot_price * 0.995
        extended = ext_pct > self.cfg.max_extension_above_pivot_pct

        dryup_slice = rel_vol.iloc[-7:-1] if close_above_pivot and len(rel_vol) >= 8 else rel_vol.iloc[-6:]
        dryup_rel_volume = float(dryup_slice.dropna().mean()) if not dryup_slice.dropna().empty else None
        dryup_near_pivot = (
            dryup_rel_volume is not None
            and dryup_rel_volume <= self.cfg.dryup_volume_mult
            and distance_to_pivot_ok
        )

        weekly_state = guppy_state(work["close"])
        short_width, long_width = _guppy_group_widths(work["close"])
        state = str(weekly_state.get("state") or "unknown")
        rlc = int(weekly_state.get("rlc") or 0)
        spread = weekly_state.get("spread_pct")
        short_rising = bool(weekly_state.get("short_rising"))
        long_rising = bool(weekly_state.get("long_rising"))
        tight_groups = (
            spread is not None
            and abs(float(spread)) <= 3.5
            and (short_width is None or short_width <= 4.0)
            and (long_width is None or long_width <= 5.0)
        )
        if state == "RWB" and tight_groups:
            guppy_compression_state = "rwb_resolving"
        elif state == "RWB":
            guppy_compression_state = "rwb"
        elif state == "compression" and short_rising:
            guppy_compression_state = "compression_short_rising"
        elif state == "compression":
            guppy_compression_state = "compression"
        elif state == "BWR" and short_rising and rlc >= 3:
            guppy_compression_state = "bottoming"
        else:
            guppy_compression_state = state.lower()

        rs3 = f.get("rs_score_3m")
        rs6 = f.get("rs_score_6m")
        rs_turning_up = (
            f.get("rs_line_at_52w_high") is True
            or f.get("rs_line_at_50d_high") is True
            or (rs3 is not None and rs6 is not None and float(rs3) > float(rs6) and float(rs3) > -5.0)
        )

        weekly_slope_pct: float | None = None
        above_30w = False
        near_30w = False
        extension_above_30w_pct: float | None = None
        if len(work) >= 35:
            sma30 = sma(work["close"], 30)
            last_sma = sma30.iloc[-1]
            prior_sma = sma30.iloc[-5] if len(sma30) >= 5 else None
            if pd.notna(last_sma) and float(last_sma) > 0:
                above_30w = close > float(last_sma)
                extension_above_30w_pct = (close - float(last_sma)) / float(last_sma) * 100.0
                near_30w = abs(extension_above_30w_pct) <= 7.0
                if prior_sma is not None and pd.notna(prior_sma) and float(prior_sma) > 0:
                    weekly_slope_pct = (float(last_sma) - float(prior_sma)) / float(prior_sma) * 100.0
        if extension_above_30w_pct is not None and extension_above_30w_pct > self.cfg.max_extension_above_long_group_pct:
            extended = True

        base_length_score = _norm(weekly_base.length_bars, self.cfg.min_weekly_base_bars, 90.0)
        if weekly_base.length_bars >= self.cfg.high_quality_weekly_base_bars:
            base_length_score = max(base_length_score, 72.0)
        depth_score = _clamp(100.0 - max(0.0, weekly_base.depth_pct - 35.0) * 2.0)
        if weekly_base.depth_pct > self.cfg.max_weekly_base_depth_pct and not secular_base:
            depth_score *= 0.65
        base_quality_score = (
            0.42 * base_length_score
            + 0.25 * depth_score
            + 0.18 * (100.0 if secular_base else 45.0)
            + 0.15 * (45.0 if weekly_base.is_wide_and_loose and not secular_base else 100.0)
        )

        demand_score = (
            0.35 * _norm(demand_spike_count, self.cfg.min_demand_spikes - 1, 5)
            + 0.22 * _norm(demand_breakout_count, 0, 3)
            + 0.25 * _norm(demand_supply_ratio, 0.9, 2.2)
            + 0.18 * (100.0 if support_hold_after_demand else 0.0)
        )
        quiet_score = (
            0.45 * _norm(low_volume_pullback_count, self.cfg.min_low_volume_pullbacks - 1, 6)
            + 0.35 * (100.0 if dryup_near_pivot else 0.0)
            + 0.20 * _norm(1.0 - (dryup_rel_volume or 1.0), 0.0, 0.35)
        )
        compression_score = 0.0
        if guppy_compression_state in {"compression_short_rising", "rwb_resolving"}:
            compression_score += 45.0
        elif guppy_compression_state in {"compression", "bottoming", "rwb"}:
            compression_score += 28.0
        if tight_groups:
            compression_score += 20.0
        if above_30w:
            compression_score += 15.0
        elif near_30w:
            compression_score += 10.0
        if weekly_slope_pct is not None and weekly_slope_pct >= -0.6:
            compression_score += 10.0
        if rs_turning_up:
            compression_score += 10.0
        compression_score = _clamp(compression_score)

        launching_signal = (
            close_above_pivot
            and (launch_volume_expansion or demand_breakout_count > 0)
            and (guppy_compression_state in {"rwb", "rwb_resolving", "compression_short_rising"} or above_30w)
        )
        launch_score = (
            35.0 * (1.0 if close_above_pivot else _clamp((ext_pct + self.cfg.max_distance_below_pivot_pct) / self.cfg.max_distance_below_pivot_pct, 0.0, 1.0))
            + 25.0 * (1.0 if launch_volume_expansion else 0.0)
            + 20.0 * (1.0 if rs_turning_up else 0.0)
            + 20.0 * (1.0 if launching_signal else 0.0)
        )

        long_base_score = (
            0.28 * base_quality_score
            + 0.30 * demand_score
            + 0.22 * quiet_score
            + 0.12 * compression_score
            + 0.08 * launch_score
        )

        weak_evidence = (
            demand_spike_count < self.cfg.min_demand_spikes
            or low_volume_pullback_count < self.cfg.min_low_volume_pullbacks
            or demand_supply_ratio < self.cfg.min_demand_supply_ratio
            or not support_hold_after_demand
        )
        one_off_spike = demand_spike_count <= 1 and low_volume_pullback_count < 2
        if one_off_spike:
            result.failed_conditions.append("one_off_volume_spike")
        if weak_evidence:
            if demand_spike_count < self.cfg.min_demand_spikes:
                result.failed_conditions.append("insufficient_demand_spikes")
            if low_volume_pullback_count < self.cfg.min_low_volume_pullbacks:
                result.failed_conditions.append("insufficient_low_volume_pullbacks")
            if demand_supply_ratio < self.cfg.min_demand_supply_ratio:
                result.failed_conditions.append("weak_demand_supply_ratio")
            if not support_hold_after_demand:
                result.failed_conditions.append("support_not_held_after_demand")

        if extended:
            phase = "extended"
        elif launching_signal:
            phase = "launching"
        elif (
            dryup_near_pivot
            and guppy_compression_state in {"compression", "compression_short_rising", "rwb_resolving", "bottoming"}
            and distance_to_pivot_ok
        ):
            phase = "compression"
        elif dryup_near_pivot:
            phase = "drying_up"
        elif demand_score >= 55.0:
            phase = "accumulating"
        else:
            phase = "dead_base"

        if phase == "extended":
            bucket = "extended_too_late"
            reason = f"long_base_extended_{ext_pct:.1f}%"
        elif phase == "launching":
            bucket = "actionable_now" if launch_volume_expansion and rs_turning_up else "near_actionable"
            reason = "long_base_launch_confirming"
        elif phase == "compression":
            bucket = "forming"
            reason = "long_base_compression_after_demand"
        elif phase == "drying_up":
            bucket = "forming"
            reason = "long_base_supply_dryup_near_pivot"
        elif phase == "accumulating":
            bucket = "watch"
            reason = "long_base_demand_footprints"
        else:
            bucket = "watch"
            reason = "long_base_dead_base"

        result.triggered = (long_base_score >= 45.0 and not weak_evidence and phase != "extended")
        result.sub_state = phase
        result.actionability = bucket if result.triggered else ("extended_too_late" if phase == "extended" else "not_valid")
        result.actionability_reason = reason if result.triggered or phase == "extended" else "long_base_evidence_below_threshold"
        result.score = round(_clamp(long_base_score), 1)
        result.trigger_level = round(float(weekly_base.pivot_price), 2)
        result.invalidation_level = round(max(0.01, float(weekly_base.low_in_base) * 0.97), 2)
        result.raw_features = {
            "long_base_score": round(long_base_score, 1),
            "long_base_phase": phase,
            "demand_spike_count": demand_spike_count,
            "demand_breakout_count": demand_breakout_count,
            "low_volume_pullback_count": low_volume_pullback_count,
            "demand_supply_ratio": round(min(demand_supply_ratio, 9.99), 2),
            "demand_supply_ratio_bucket": _bucket_ratio(demand_supply_ratio),
            "dryup_near_pivot": dryup_near_pivot,
            "dryup_rel_volume_near_pivot": round(dryup_rel_volume, 2) if dryup_rel_volume is not None else None,
            "weekly_base_length_bars": int(weekly_base.length_bars),
            "monthly_base_length_bars": int(monthly_base_length),
            "monthly_secular_base": secular_base,
            "support_hold_after_demand": support_hold_after_demand,
            "guppy_compression_state": guppy_compression_state,
            "weekly_guppy_state": state,
            "weekly_guppy_rlc": rlc,
            "weekly_guppy_spread_pct": round(float(spread), 2) if spread is not None else None,
            "weekly_guppy_short_width_pct": round(short_width, 2) if short_width is not None else None,
            "weekly_guppy_long_width_pct": round(long_width, 2) if long_width is not None else None,
            "rs_turning_up": rs_turning_up,
            "base_quality_score": round(base_quality_score, 1),
            "base_depth_pct": round(float(weekly_base.depth_pct), 1),
            "base_high": round(float(weekly_base.high_in_base), 2),
            "base_low": round(float(weekly_base.low_in_base), 2),
            "base_start_bars_ago": len(work) - 1 - int(weekly_base.start_idx),
            "distance_to_pivot_pct": round(ext_pct, 2),
            "extension_atr_multiples": round(ext_atr, 2) if ext_atr is not None and math.isfinite(ext_atr) else None,
            "launch_volume_expansion": launch_volume_expansion,
            "weekly_30w_slope_pct": round(weekly_slope_pct, 2) if weekly_slope_pct is not None else None,
            "above_30w_sma": above_30w,
            "near_30w_sma": near_30w,
            "extension_above_30w_pct": round(extension_above_30w_pct, 2) if extension_above_30w_pct is not None else None,
        }
        result.base_metrics = {
            "pivot_price": float(weekly_base.pivot_price),
            "base_low": float(weekly_base.low_in_base),
            "base_high": float(weekly_base.high_in_base),
            "weekly_base_length_bars": int(weekly_base.length_bars),
            "monthly_base_length_bars": int(monthly_base_length),
            "base_depth_pct": round(float(weekly_base.depth_pct), 1),
            "demand_spike_count": demand_spike_count,
            "low_volume_pullback_count": low_volume_pullback_count,
            "dryup_near_pivot": dryup_near_pivot,
        }
        result.reasons.extend(
            [
                f"base_quality={base_quality_score:.1f}",
                f"demand_spikes={demand_spike_count}",
                f"low_volume_pullbacks={low_volume_pullback_count}",
                f"demand_supply_ratio={demand_supply_ratio:.2f}",
                f"guppy={guppy_compression_state}",
                f"phase={phase}",
            ]
        )
        if dryup_near_pivot:
            result.reasons.append("dryup_near_pivot")
        if support_hold_after_demand:
            result.reasons.append("support_held_after_demand")
        if secular_base:
            result.reasons.append("monthly_secular_base")
        if rs_turning_up:
            result.reasons.append("rs_turning_up")
        return result
