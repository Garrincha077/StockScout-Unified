from __future__ import annotations

import pandas as pd

from stock_scout.config.schema import RWBSqueezeThrustSetupConfig
from stock_scout.data.corporate_actions import detect_m_and_a_from_price
from stock_scout.indicators.guppy import LONG_PERIODS, SHORT_PERIODS, guppy_state
from stock_scout.indicators.highs_lows import close_location_value
from stock_scout.indicators.moving_averages import ema, sma
from stock_scout.setups.base import SetupDetector, SetupResult
from stock_scout.setups.base_analysis import find_swings


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm_inverse(v: float | None, hi: float) -> float:
    if v is None or hi <= 0:
        return 0.0
    return _clamp((hi - float(v)) / hi * 100.0)


def _weekly_from_daily(df_daily: pd.DataFrame) -> pd.DataFrame:
    return (
        df_daily.resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def _safe_rel_volume(volume: pd.Series, window: int = 20, min_periods: int = 8) -> pd.Series:
    avg = volume.rolling(window, min_periods=min_periods).mean().shift(1)
    return volume / avg.mask(avg == 0)


def _ema_frame(close: pd.Series) -> pd.DataFrame:
    data = {f"ema{p}": ema(close, p) for p in (*SHORT_PERIODS, *LONG_PERIODS)}
    return pd.DataFrame(data, index=close.index)


def _pct_width(values: list[float], close: float) -> float | None:
    if not values or close <= 0:
        return None
    return (max(values) - min(values)) / close * 100.0


def _rs_turning_up(features: dict) -> bool:
    rs3 = features.get("rs_score_3m")
    rs6 = features.get("rs_score_6m")
    return (
        features.get("rs_line_at_52w_high") is True
        or features.get("rs_line_at_50d_high") is True
        or (rs3 is not None and rs6 is not None and float(rs3) > float(rs6) and float(rs3) > -5.0)
    )


def _trendline_breakout(work: pd.DataFrame, rel_vol: pd.Series, min_rel_volume: float) -> bool:
    """Best-effort falling/resistance trendline breakout from confirmed highs."""
    if len(work) < 30:
        return False
    lookback = min(80, len(work))
    window = work.iloc[-lookback:].copy()
    highs, _lows = find_swings(window["high"], left=2, right=2)
    prior_highs = [h for h in highs if h.idx < len(window) - 3]
    if len(prior_highs) < 2:
        return False
    h1, h2 = prior_highs[-2], prior_highs[-1]
    if h1.idx >= h2.idx:
        return False
    # Prefer descending or nearly flat resistance. Strongly rising lines are not
    # the squeeze-breakout shape this setup is trying to isolate.
    if h2.price > h1.price * 1.03:
        return False
    slope = (h2.price - h1.price) / max(1, h2.idx - h1.idx)
    projected = h2.price + slope * (len(window) - 1 - h2.idx)
    current = window.iloc[-1]
    current_rel_vol = rel_vol.iloc[-1]
    return (
        projected > 0
        and float(current["close"]) > projected * 1.01
        and float(current["close"]) > float(current["open"])
        and pd.notna(current_rel_vol)
        and float(current_rel_vol) >= min_rel_volume
    )


class RWBSqueezeThrustDetector(SetupDetector):
    """Weekly RWB/GMMA squeeze with high-volume price thrust.

    This catches the visual pattern where short and long Guppy groups coil into
    a tight band near a flat/rising 30-week SMA, then price expands through the
    band on unusually high volume. A tight squeeze without the thrust still
    triggers as a watch item so it can surface before the move.
    """

    name = "rwb_squeeze_thrust"

    def __init__(self, cfg: RWBSqueezeThrustSetupConfig):
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
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_daily_history"
            result.failed_conditions.append("insufficient_daily_history")
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
        min_weekly = max(70, max(LONG_PERIODS) + 10)
        if len(weekly) < min_weekly:
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_weekly_history"
            result.failed_conditions.append("insufficient_weekly_history")
            return result

        work = weekly.copy()
        close = work["close"]
        emas = _ema_frame(close)
        # Evaluate the squeeze band one completed week before the possible
        # thrust. A true thrust can widen the current EMAs, but the tradable
        # clue is that price launched out of a previously tight RWB band.
        context_close = close.iloc[:-1] if len(close) > min_weekly else close
        last = emas.iloc[-2] if len(emas) > min_weekly else emas.iloc[-1]
        if last.isna().any():
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_weekly_ema_history"
            result.failed_conditions.append("insufficient_weekly_ema_history")
            return result

        last_close = float(close.iloc[-1])
        band_context_close = float(context_close.iloc[-1])
        short_cols = [f"ema{p}" for p in SHORT_PERIODS]
        long_cols = [f"ema{p}" for p in LONG_PERIODS]
        short_vals = [float(last[c]) for c in short_cols]
        long_vals = [float(last[c]) for c in long_cols]
        all_vals = short_vals + long_vals
        band_top = max(all_vals)
        band_bottom = min(all_vals)
        band_width_pct = _pct_width(all_vals, band_context_close)
        short_width_pct = _pct_width(short_vals, band_context_close)
        long_width_pct = _pct_width(long_vals, band_context_close)
        avg_short = sum(short_vals) / len(short_vals)
        avg_long = sum(long_vals) / len(long_vals)
        spread_pct = (avg_short - avg_long) / avg_long * 100.0 if avg_long else 0.0
        weekly_state = guppy_state(context_close)
        state = str(weekly_state.get("state") or "unknown")

        tight_band = (
            band_width_pct is not None
            and short_width_pct is not None
            and long_width_pct is not None
            and band_width_pct <= self.cfg.tight_band_width_pct
            and short_width_pct <= self.cfg.max_short_group_width_pct
            and long_width_pct <= self.cfg.max_long_group_width_pct
            and abs(spread_pct) <= self.cfg.max_abs_rwb_spread_pct_for_squeeze
            and state in {"RWB", "compression"}
        )

        sma30 = sma(close, 30)
        weekly_30w_slope_pct: float | None = None
        above_30w = False
        extension_above_30w_pct: float | None = None
        if len(sma30) >= 35 and pd.notna(sma30.iloc[-1]) and float(sma30.iloc[-1]) > 0:
            last_sma = float(sma30.iloc[-1])
            prior_sma = sma30.iloc[-5]
            above_30w = last_close >= last_sma
            extension_above_30w_pct = (last_close - last_sma) / last_sma * 100.0
            if pd.notna(prior_sma) and float(prior_sma) > 0:
                weekly_30w_slope_pct = (last_sma - float(prior_sma)) / float(prior_sma) * 100.0
        sma30_ok = weekly_30w_slope_pct is not None and weekly_30w_slope_pct >= self.cfg.flat_30w_slope_min_pct

        rel_vol = _safe_rel_volume(work["volume"])
        clv = close_location_value(work, window=1).fillna(0.5)
        current = work.iloc[-1]
        thrust_rel_volume = float(rel_vol.iloc[-1]) if pd.notna(rel_vol.iloc[-1]) else None
        thrust_close_location = float(clv.iloc[-1]) if pd.notna(clv.iloc[-1]) else None
        price_above_rwb_band = last_close >= band_top * 1.005
        up_week = last_close > float(work["close"].iloc[-2])
        thrust = (
            up_week
            and price_above_rwb_band
            and thrust_rel_volume is not None
            and thrust_rel_volume >= self.cfg.min_thrust_rel_volume
            and thrust_close_location is not None
            and thrust_close_location >= self.cfg.min_close_location
        )

        # Count prior high-volume attempts to punch through the band. They are a
        # pressure/absorption clue, not a hard gate.
        band_top_series = emas[short_cols + long_cols].max(axis=1)
        base_attempt_mask = (
            (work["close"] > work["close"].shift(1))
            & (work["high"] >= band_top_series * 0.99)
            & (rel_vol >= self.cfg.min_thrust_rel_volume)
            & (clv >= self.cfg.min_close_location)
        )
        prior_attempt_window = base_attempt_mask.iloc[-(self.cfg.prior_attempt_lookback_weeks + 1) : -1]
        prior_attempts = int(prior_attempt_window.fillna(False).sum())

        trendline_breakout = _trendline_breakout(work, rel_vol, self.cfg.min_thrust_rel_volume)
        rs_up = _rs_turning_up(f)
        near_recent_high = last_close >= float(work["high"].shift(1).rolling(10, min_periods=4).max().iloc[-1]) * 0.98
        extension_above_band_pct = (last_close - band_top) / band_top * 100.0 if band_top > 0 else 0.0
        extended = (
            extension_above_band_pct > self.cfg.max_extension_above_band_pct
            or (
                extension_above_30w_pct is not None
                and extension_above_30w_pct > self.cfg.max_extension_above_band_pct
            )
        )

        squeeze_score = (
            0.45 * _norm_inverse(band_width_pct, self.cfg.tight_band_width_pct)
            + 0.25 * _norm_inverse(short_width_pct, self.cfg.max_short_group_width_pct)
            + 0.20 * _norm_inverse(long_width_pct, self.cfg.max_long_group_width_pct)
            + 0.10 * _norm_inverse(abs(spread_pct), self.cfg.max_abs_rwb_spread_pct_for_squeeze)
        )
        ma_score = 0.0
        if sma30_ok:
            ma_score += 65.0
        if above_30w:
            ma_score += 25.0
        if weekly_30w_slope_pct is not None and weekly_30w_slope_pct >= 0:
            ma_score += 10.0
        ma_score = _clamp(ma_score)
        thrust_score = 0.0
        if thrust_rel_volume is not None:
            thrust_score += _clamp((thrust_rel_volume - 1.0) / 1.5 * 45.0)
        if thrust_close_location is not None:
            thrust_score += _clamp((thrust_close_location - 0.45) / 0.45 * 25.0)
        if price_above_rwb_band:
            thrust_score += 20.0
        if up_week:
            thrust_score += 10.0
        if not thrust:
            thrust_score *= 0.35
        thrust_score = _clamp(thrust_score)
        prior_score = _clamp(prior_attempts / 3.0 * 100.0)
        bonus_score = (100.0 if trendline_breakout else 0.0) * 0.55 + (100.0 if rs_up else 0.0) * 0.45
        rwb_squeeze_score = (
            0.34 * squeeze_score
            + 0.18 * ma_score
            + 0.28 * thrust_score
            + 0.08 * prior_score
            + 0.12 * bonus_score
        )

        if not tight_band:
            result.actionability = "not_valid"
            result.actionability_reason = "rwb_band_not_tight"
            result.failed_conditions.append("rwb_band_not_tight")
        if not sma30_ok:
            result.failed_conditions.append("weekly_30w_not_flat_or_rising")
            result.actionability = "not_valid"
            result.actionability_reason = "weekly_30w_not_flat_or_rising"
        if result.failed_conditions:
            result.raw_features = {
                "rwb_squeeze_score": round(_clamp(rwb_squeeze_score), 1),
                "weekly_rwb_state": state,
                "weekly_rwb_band_width_pct": round(band_width_pct, 2) if band_width_pct is not None else None,
                "weekly_short_group_width_pct": round(short_width_pct, 2) if short_width_pct is not None else None,
                "weekly_long_group_width_pct": round(long_width_pct, 2) if long_width_pct is not None else None,
                "weekly_rwb_spread_pct": round(spread_pct, 2),
                "weekly_30w_slope_pct": round(weekly_30w_slope_pct, 2) if weekly_30w_slope_pct is not None else None,
            }
            return result

        if extended:
            phase = "extended"
        elif thrust and trendline_breakout:
            phase = "trendline_breakout"
        elif thrust and (rs_up or near_recent_high):
            phase = "confirmed"
        elif thrust:
            phase = "thrusting"
        else:
            phase = "watch_squeeze"

        if phase == "extended":
            bucket = "extended_too_late"
            reason = f"rwb_squeeze_extended_{extension_above_band_pct:.1f}%"
        elif phase in {"trendline_breakout", "confirmed"}:
            bucket = "actionable_now"
            reason = phase
        elif phase == "thrusting":
            bucket = (
                "actionable_now"
                if thrust_rel_volume is not None
                and thrust_rel_volume >= self.cfg.min_thrust_rel_volume * 1.35
                and thrust_close_location is not None
                and thrust_close_location >= 0.70
                and rs_up
                else "near_actionable"
            )
            reason = "rwb_squeeze_high_volume_thrust"
        else:
            bucket = "watch"
            reason = "rwb_squeeze_watch_no_thrust"

        result.triggered = phase != "extended" and rwb_squeeze_score >= 35.0
        result.sub_state = phase
        result.actionability = bucket if result.triggered else ("extended_too_late" if phase == "extended" else "not_valid")
        result.actionability_reason = reason if result.triggered or phase == "extended" else "rwb_squeeze_score_below_threshold"
        result.score = round(_clamp(rwb_squeeze_score), 1)
        result.trigger_level = round(max(float(current["high"]), band_top), 2)
        result.invalidation_level = round(max(0.01, min(band_bottom, float(sma30.iloc[-1]) if pd.notna(sma30.iloc[-1]) else band_bottom) * 0.97), 2)
        result.raw_features = {
            "rwb_squeeze_score": round(rwb_squeeze_score, 1),
            "rwb_squeeze_phase": phase,
            "weekly_rwb_state": state,
            "weekly_rwb_band_width_pct": round(band_width_pct, 2) if band_width_pct is not None else None,
            "weekly_short_group_width_pct": round(short_width_pct, 2) if short_width_pct is not None else None,
            "weekly_long_group_width_pct": round(long_width_pct, 2) if long_width_pct is not None else None,
            "weekly_rwb_spread_pct": round(spread_pct, 2),
            "weekly_30w_slope_pct": round(weekly_30w_slope_pct, 2) if weekly_30w_slope_pct is not None else None,
            "price_above_rwb_band": price_above_rwb_band,
            "rwb_thrust_rel_volume": round(thrust_rel_volume, 2) if thrust_rel_volume is not None else None,
            "rwb_thrust_close_location": round(thrust_close_location, 2) if thrust_close_location is not None else None,
            "prior_rwb_thrust_attempts": prior_attempts,
            "weekly_trendline_breakout": trendline_breakout,
            "rwb_extension_above_band_pct": round(extension_above_band_pct, 2),
            "extension_above_30w_pct": round(extension_above_30w_pct, 2) if extension_above_30w_pct is not None else None,
            "above_30w_sma": above_30w,
            "rs_turning_up": rs_up,
            "rwb_squeeze_thrust": thrust,
        }
        result.base_metrics = {
            "rwb_band_top": band_top,
            "rwb_band_bottom": band_bottom,
            "rwb_band_width_pct": band_width_pct,
            "prior_rwb_thrust_attempts": prior_attempts,
            "weekly_trendline_breakout": trendline_breakout,
        }
        result.reasons.extend(
            [
                f"squeeze={squeeze_score:.1f}",
                f"ma={ma_score:.1f}",
                f"thrust={thrust_score:.1f}",
                f"prior_attempts={prior_attempts}",
                f"phase={phase}",
            ]
        )
        if trendline_breakout:
            result.reasons.append("weekly_trendline_breakout")
        if rs_up:
            result.reasons.append("rs_turning_up")
        return result
