from __future__ import annotations

import math

import pandas as pd

from stock_scout.config.schema import AccumulationSetupConfig
from stock_scout.data.corporate_actions import detect_m_and_a_from_price
from stock_scout.indicators.highs_lows import close_location_value
from stock_scout.indicators.moving_averages import sma
from stock_scout.setups.base import SetupDetector, SetupResult
from stock_scout.setups.base_analysis import (
    extension_from_pivot,
    find_consolidation_base,
)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm(v: float | None, lo: float, hi: float) -> float:
    if v is None or hi <= lo:
        return 0.0
    return _clamp((v - lo) / (hi - lo) * 100.0)


def _sma_compression_pct(features: dict) -> float | None:
    close = features.get("close")
    vals = [features.get("sma10"), features.get("sma20"), features.get("sma50")]
    if close is None or close <= 0 or any(v is None or v <= 0 for v in vals):
        return None
    return (max(vals) - min(vals)) / close * 100.0


def _support_volume_events(df: pd.DataFrame, start_idx: int) -> int:
    """Count high-volume up-days that close in the upper half of the day.

    This is the practical "institutional footprint" used for a NOK-style base:
    repeated demand days during a sideways process, not one random spike.
    """
    if df.empty or "volume" not in df.columns or len(df) < 60:
        return 0
    work = df.copy()
    vol_avg = work["volume"].rolling(50, min_periods=20).mean()
    clv = close_location_value(work, window=1)
    change = work["close"].diff()
    base_mask = pd.Series(False, index=work.index)
    base_mask.iloc[max(0, start_idx) :] = True
    events = (
        base_mask
        & (change > 0)
        & (work["volume"] >= vol_avg * 1.25)
        & (clv >= 0.50)
    )
    return int(events.fillna(False).sum())


class AccumulationBaseDetector(SetupDetector):
    """NOK-style institutional accumulation inside a long base.

    This detector is intentionally separate from breakout detectors. It looks
    for demand footprints before the obvious pivot breaks: repeated up-volume
    support, U/D volume > 1, later dry-up, higher lows, SMA compression, and
    weekly 30w-SMA transition context.
    """

    name = "accumulation_base"

    def __init__(self, cfg: AccumulationSetupConfig):
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
        if df_daily.empty or len(df_daily) < max(120, self.cfg.min_base_length_bars + 20):
            result.failed_conditions.append("insufficient_history")
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_history"
            return result

        f = features or {}
        # Price alone no longer returns "high", so this exclusion is unreachable
        # from here and the warning below is what actually fires. That is
        # deliberate: a detector cannot see the news, and price on its own
        # cannot tell a locked deal from a stock that is merely dormant. The
        # orchestrator owns exclusion because it is the only place that has
        # both. The branch stays because a caller handing in a combined finding
        # should still be honoured.
        ma = detect_m_and_a_from_price(df_daily, ticker=str(f.get("ticker") or self.name))
        if ma.confidence == "high":
            result.disqualifiers.append("m_and_a_high")
            result.failed_conditions.append("m_and_a_high_confidence")
            result.actionability = "excluded"
            result.actionability_reason = "m_and_a_high_confidence"
            return result
        if ma.confidence == "medium":
            result.warning_flags.append("m_and_a_medium")

        base = find_consolidation_base(
            df_daily,
            min_length_bars=self.cfg.min_base_length_bars,
            max_depth_pct=self.cfg.max_base_depth_pct,
        )
        if base is None:
            result.failed_conditions.append("no_valid_long_base")
            result.actionability = "not_valid"
            result.actionability_reason = "no_valid_long_base"
            return result

        close = f.get("close")
        if close is None or close <= 0:
            result.failed_conditions.append("missing_close")
            result.actionability = "not_valid"
            result.actionability_reason = "missing_close"
            return result

        support_events = _support_volume_events(df_daily, base.start_idx)
        up_down = f.get("up_down_vol_ratio_50d")
        vol_dryup = float(f.get("volume_dryup_score") or 0.0)
        dryup_after_accumulation = (
            support_events >= self.cfg.min_support_volume_events
            and vol_dryup >= self.cfg.min_volume_dryup_score
        )
        higher_lows = int(f.get("higher_lows") or 0)
        pocket_recent = f.get("days_since_pocket_pivot")
        has_recent_pocket = pocket_recent is not None and pocket_recent <= 15
        compression = _sma_compression_pct(f)
        compression_score = (
            0.0
            if compression is None
            else _clamp((self.cfg.max_sma_compression_pct - compression) / self.cfg.max_sma_compression_pct * 100.0)
        )

        ext_pct, ext_atr = extension_from_pivot(
            float(close),
            base.pivot_price,
            f.get("atr20"),
        )
        too_far_below = ext_pct < -max(20.0, self.cfg.max_distance_below_pivot_pct * 1.5)
        if too_far_below:
            result.failed_conditions.append(f"too_far_below_pivot({ext_pct:.1f}%)")
            result.actionability = "not_valid"
            result.actionability_reason = "too_far_below_pivot"
            return result

        # Weekly transition context: flattening/rising 30w SMA and price near or
        # above it. This catches the "trend just about to start" behavior.
        weekly_slope_pct: float | None = None
        above_30w = False
        near_30w = False
        if df_weekly is not None and not df_weekly.empty and len(df_weekly) >= 35:
            wclose = df_weekly["close"]
            sma30 = sma(wclose, 30)
            last = sma30.iloc[-1]
            prior = sma30.iloc[-5] if len(sma30.dropna()) >= 5 else None
            if pd.notna(last) and prior is not None and pd.notna(prior) and float(prior) > 0:
                weekly_slope_pct = (float(last) - float(prior)) / float(prior) * 100.0
                above_30w = float(wclose.iloc[-1]) > float(last)
                near_30w = abs((float(wclose.iloc[-1]) - float(last)) / float(last) * 100.0) <= 5.0

        rs3 = f.get("rs_score_3m")
        rs6 = f.get("rs_score_6m")
        rs_turning_up = (
            f.get("rs_line_at_52w_high") is True
            or f.get("rs_line_at_50d_high") is True
            or (rs3 is not None and rs6 is not None and rs3 > rs6 and rs3 > -5.0)
        )
        ema_cross_recent = (
            f.get("bars_since_ema_cross_up") is not None
            and f.get("bars_since_ema_cross_up") <= 8
        )
        ema_aligned = f.get("ema10_above_ema20") is True

        base_length_score = _norm(base.length_bars, self.cfg.min_base_length_bars, 150.0)
        depth_score = _clamp(100.0 - max(0.0, base.depth_pct - 18.0) * 2.2)
        higher_lows_score = _clamp(higher_lows / 3.0 * 100.0)
        base_quality = (
            0.35 * base_length_score
            + 0.35 * depth_score
            + 0.20 * higher_lows_score
            + 0.10 * (100.0 if not base.is_wide_and_loose else 25.0)
        )

        up_down_score = _norm(up_down, 0.9, 1.6)
        support_score = _clamp(support_events / max(1, self.cfg.min_support_volume_events + 2) * 100.0)
        pocket_score = 100.0 if has_recent_pocket else 0.0
        dryup_score = _clamp(vol_dryup)
        footprint = (
            0.15 * up_down_score
            + 0.45 * support_score
            + 0.35 * dryup_score
            + 0.05 * pocket_score
        )

        transition_score = 0.0
        if above_30w:
            transition_score += 30.0
        elif near_30w:
            transition_score += 18.0
        if weekly_slope_pct is not None and weekly_slope_pct >= -0.5:
            transition_score += 20.0
        if ema_cross_recent:
            transition_score += 20.0
        elif ema_aligned:
            transition_score += 10.0
        if rs_turning_up:
            transition_score += 20.0
        if ext_pct >= -self.cfg.max_distance_below_pivot_pct:
            transition_score += 10.0
        transition_score = _clamp(transition_score)

        accumulation_score = (
            0.34 * footprint
            + 0.30 * base_quality
            + 0.20 * compression_score
            + 0.16 * transition_score
        )

        weak_footprint = (
            support_events < self.cfg.min_support_volume_events
            and (up_down is None or up_down < self.cfg.min_up_down_vol_ratio)
            and not has_recent_pocket
        )
        if weak_footprint:
            result.failed_conditions.append("weak_accumulation_footprint")
            result.actionability = "not_valid"
            result.actionability_reason = "weak_accumulation_footprint"
            result.raw_features = {
                "support_volume_events": support_events,
                "up_down_vol_ratio_50d": round(up_down, 2) if up_down is not None else None,
            }
            return result

        volume_breakout = (
            f.get("volume_ratio_50d") is not None
            and f.get("volume_ratio_50d") >= self.cfg.breakout_volume_ratio
            and ext_pct >= -1.0
        )
        if ext_pct > 8.0:
            phase = "extended"
        elif ext_pct >= -1.0 and (volume_breakout or accumulation_score >= self.cfg.high_quality_score):
            phase = "breakout_ready"
        elif (
            (above_30w or near_30w)
            and (ema_cross_recent or ema_aligned)
            and (rs_turning_up or accumulation_score >= self.cfg.high_quality_score)
        ):
            phase = "transitioning"
        elif (
            compression is not None
            and compression <= self.cfg.max_sma_compression_pct
            and dryup_after_accumulation
        ):
            phase = "tightening"
        elif footprint >= 55.0:
            phase = "accumulating"
        else:
            phase = "early_base"

        if phase == "extended":
            bucket = "extended_too_late"
            reason = f"accumulation_extended_{ext_pct:.1f}%"
        elif phase == "breakout_ready":
            bucket = "actionable_now" if volume_breakout and ext_pct >= 0 else "near_actionable"
            reason = f"breakout_ready_ext_{ext_pct:.1f}%"
        elif phase == "transitioning":
            bucket = "near_actionable"
            reason = "stage1_to_stage2_accumulation_transition"
        elif phase == "tightening":
            bucket = "forming"
            reason = "dryup_after_accumulation_tightening"
        elif phase == "accumulating":
            bucket = "forming" if accumulation_score >= self.cfg.high_quality_score else "watch"
            reason = "institutional_footprints_in_base"
        else:
            bucket = "watch"
            reason = "early_accumulation_base"

        result.triggered = accumulation_score >= 45.0 and phase != "extended"
        result.sub_state = phase
        result.actionability = bucket if result.triggered else "not_valid"
        result.actionability_reason = reason if result.triggered else "accumulation_score_below_threshold"
        result.score = round(_clamp(accumulation_score), 1)
        result.trigger_level = round(base.pivot_price, 2)
        result.invalidation_level = round(max(0.01, base.low_in_base * 0.98), 2)
        result.raw_features = {
            "accumulation_score": round(accumulation_score, 1),
            "accumulation_phase": phase,
            "institutional_footprint_score": round(footprint, 1),
            "base_quality_score": round(base_quality, 1),
            "sma_compression_pct": round(compression, 2) if compression is not None else None,
            "support_volume_events": support_events,
            "dryup_after_accumulation": dryup_after_accumulation,
            "base_length_bars": base.length_bars,
            "base_depth_pct": round(base.depth_pct, 1),
            "base_low": round(base.low_in_base, 2),
            "base_high": round(base.high_in_base, 2),
            "base_start_bars_ago": len(df_daily) - 1 - base.start_idx,
            "distance_to_pivot_pct": round(ext_pct, 2),
            "extension_atr_multiples": round(ext_atr, 2) if ext_atr is not None and math.isfinite(ext_atr) else None,
            "up_down_vol_ratio_50d": round(up_down, 2) if up_down is not None else None,
            "volume_dryup_score": round(vol_dryup, 1),
            "higher_lows": higher_lows,
            "weekly_30w_slope_pct": round(weekly_slope_pct, 2) if weekly_slope_pct is not None else None,
            "above_30w_sma": above_30w,
            "near_30w_sma": near_30w,
            "rs_turning_up": rs_turning_up,
            "ema_cross_recent": ema_cross_recent,
        }
        result.base_metrics = {
            "pivot_price": float(base.pivot_price),
            "base_low": float(base.low_in_base),
            "base_high": float(base.high_in_base),
            "base_length_bars": base.length_bars,
            "base_depth_pct": round(base.depth_pct, 1),
            "support_volume_events": support_events,
            "dryup_after_accumulation": dryup_after_accumulation,
        }
        result.reasons.extend(
            [
                f"footprint={footprint:.1f}",
                f"base_quality={base_quality:.1f}",
                f"sma_compression={compression:.2f}%" if compression is not None else "sma_compression=na",
                f"support_volume_events={support_events}",
                f"phase={phase}",
            ]
        )
        if dryup_after_accumulation:
            result.reasons.append("volume_dryup_after_prior_demand")
        if rs_turning_up:
            result.reasons.append("rs_turning_up")
        if ema_cross_recent:
            result.reasons.append("fresh_ema10_20_cross")
        return result
