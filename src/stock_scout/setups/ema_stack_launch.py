from __future__ import annotations

import pandas as pd

from stock_scout.config.schema import EMAStackLaunchSetupConfig
from stock_scout.data.corporate_actions import detect_m_and_a_from_price
from stock_scout.indicators.guppy import LONG_PERIODS, SHORT_PERIODS, guppy_state_at
from stock_scout.indicators.highs_lows import close_location_value
from stock_scout.indicators.moving_averages import ema, sma
from stock_scout.setups.base import SetupDetector, SetupResult

# One rung up, and no further. `actionable_now` is deliberately absent as a key:
# a name already at the top has nowhere to go, and `not_valid` is a statement
# that the shape is not there at all, which no threshold on that shape can undo.
_PROMOTION_RUNGS = {"watch": "near_actionable", "near_actionable": "actionable_now"}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm_inverse(v: float | None, hi: float) -> float:
    if v is None or hi <= 0:
        return 0.0
    return _clamp((hi - float(v)) / hi * 100.0)


def _norm(v: float | None, lo: float, hi: float) -> float:
    if v is None or hi <= lo:
        return 0.0
    return _clamp((float(v) - lo) / (hi - lo) * 100.0)


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


class EMAStackLaunchDetector(SetupDetector):
    """Weekly EMA stack coil/ignition ranker.

    This is intentionally broader than strict RWB Squeeze Thrust. It ranks the
    AEVA/CVNA shape where the short EMA group coils first, long EMAs may still
    be imperfect, and a high-volume weekly ignition starts through the stack.
    """

    name = "ema_stack_launch"

    def __init__(self, cfg: EMAStackLaunchSetupConfig):
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
        min_weekly = max(70, max(LONG_PERIODS) + 10)
        if len(weekly) < min_weekly:
            result.failed_conditions.append("insufficient_weekly_history")
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_weekly_history"
            return result

        work = weekly.copy()
        close = work["close"]
        emas = _ema_frame(close)
        if emas.iloc[-1].isna().any():
            result.failed_conditions.append("insufficient_weekly_ema_history")
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_weekly_ema_history"
            return result

        short_cols = [f"ema{p}" for p in SHORT_PERIODS]
        long_cols = [f"ema{p}" for p in LONG_PERIODS]
        sma30 = sma(close, 30)
        rel_vol = _safe_rel_volume(work["volume"])
        clv = close_location_value(work, window=1).fillna(0.5)

        def measure(idx: int) -> dict | None:
            row = emas.iloc[idx]
            if row[short_cols + long_cols].isna().any():
                return None
            px = float(close.iloc[idx])
            if px <= 0:
                return None
            short_vals = [float(row[c]) for c in short_cols]
            long_vals = [float(row[c]) for c in long_cols]
            all_vals = short_vals + long_vals
            avg_short = sum(short_vals) / len(short_vals)
            avg_long = sum(long_vals) / len(long_vals)
            spread_pct = (avg_short - avg_long) / avg_long * 100.0 if avg_long else 0.0
            # `emas` above is exactly the GMMA stack over the same closes, so
            # reading it at `idx` gives what recomputing over close[:idx+1]
            # gave — and `measure` is called for every candidate bar, which is
            # how one ticker ended up recomputing 455 EMAs.
            state_raw = guppy_state_at(emas, close, idx)
            state = str(state_raw.get("state") or "unknown")
            rlc = int(state_raw.get("rlc") or 0)
            short_rising = bool(state_raw.get("short_rising"))
            long_rising = bool(state_raw.get("long_rising"))
            slope_pct = None
            if idx >= 34 and pd.notna(sma30.iloc[idx]) and pd.notna(sma30.iloc[idx - 4]) and float(sma30.iloc[idx - 4]) > 0:
                slope_pct = (float(sma30.iloc[idx]) - float(sma30.iloc[idx - 4])) / float(sma30.iloc[idx - 4]) * 100.0
            relation_score = 35.0
            if state == "compression":
                relation_score = 78.0
            elif state == "RWB":
                relation_score = 72.0 + (10.0 if long_rising else 0.0)
            elif state == "BWR" and (short_rising or rlc >= 3):
                relation_score = 55.0
            if abs(spread_pct) <= 4.0:
                relation_score += 10.0
            if rlc >= 5:
                relation_score += 7.0
            return {
                "idx": idx,
                "date": work.index[idx].date().isoformat(),
                "state": state,
                "rlc": rlc,
                "short_rising": short_rising,
                "long_rising": long_rising,
                "stack_top": max(all_vals),
                "stack_bottom": min(all_vals),
                "short_top": max(short_vals),
                "long_median": sorted(long_vals)[len(long_vals) // 2],
                "long_top": max(long_vals),
                "stack_width_pct": _pct_width(all_vals, px),
                "short_width_pct": _pct_width(short_vals, px),
                "long_width_pct": _pct_width(long_vals, px),
                "spread_pct": spread_pct,
                "slope_pct": slope_pct,
                "relation_score": _clamp(relation_score),
            }

        latest = measure(len(work) - 1)
        if latest is None:
            result.failed_conditions.append("missing_latest_ema_measure")
            result.actionability = "not_valid"
            result.actionability_reason = "missing_latest_ema_measure"
            return result

        start_idx = max(min_weekly - 1, len(work) - 1 - self.cfg.recent_coil_lookback_weeks)
        best: dict | None = None
        for idx in range(start_idx, len(work) - 1):
            m = measure(idx)
            if m is None:
                continue
            slope_ok = m["slope_pct"] is not None and float(m["slope_pct"]) >= self.cfg.flat_30w_slope_min_pct
            valid = (
                m["stack_width_pct"] is not None
                and m["short_width_pct"] is not None
                and m["long_width_pct"] is not None
                and float(m["stack_width_pct"]) <= self.cfg.max_stack_width_pct
                and float(m["short_width_pct"]) <= self.cfg.max_short_group_width_pct
                and float(m["long_width_pct"]) <= self.cfg.max_long_group_width_pct
                and slope_ok
            )
            bars_ago = len(work) - 1 - idx
            recency = _clamp((self.cfg.recent_coil_lookback_weeks - bars_ago + 1) / self.cfg.recent_coil_lookback_weeks * 100.0)
            long_score = max(
                _norm_inverse(m["long_width_pct"], self.cfg.max_long_group_width_pct),
                _norm_inverse(m["long_width_pct"], self.cfg.ideal_long_group_width_pct) * 0.85,
            )
            short_score = _norm_inverse(m["short_width_pct"], self.cfg.max_short_group_width_pct)
            stack_score = _norm_inverse(m["stack_width_pct"], self.cfg.max_stack_width_pct)
            slope_score = 70.0 if slope_ok else 0.0
            if m["slope_pct"] is not None and float(m["slope_pct"]) >= 0:
                slope_score = 100.0
            coil_score = (
                0.24 * stack_score
                + 0.22 * short_score
                + 0.18 * long_score
                + 0.18 * float(m["relation_score"])
                + 0.10 * slope_score
                + 0.08 * recency
            )
            m.update(
                {
                    "valid_coil": valid,
                    "bars_ago": bars_ago,
                    "recency_score": recency,
                    "long_ema_compression_score": long_score,
                    "short_ema_compression_score": short_score,
                    "stack_width_score": stack_score,
                    "coil_score": coil_score if valid else coil_score * 0.65,
                }
            )
            if best is None or float(m["coil_score"]) > float(best["coil_score"]):
                best = m

        if best is None:
            result.failed_conditions.append("no_recent_ema_coil")
            result.actionability = "not_valid"
            result.actionability_reason = "no_recent_ema_coil"
            return result

        current = work.iloc[-1]
        current_close = float(current["close"])
        current_rel_vol = float(rel_vol.iloc[-1]) if pd.notna(rel_vol.iloc[-1]) else None
        current_clv = float(clv.iloc[-1]) if pd.notna(clv.iloc[-1]) else None
        up_week = current_close > float(work["close"].iloc[-2])
        prior_8w_high = float(work["high"].shift(1).rolling(8, min_periods=4).max().iloc[-1])
        close_above_short = current_close >= float(latest["short_top"])
        close_above_long_median = current_close >= float(latest["long_median"])
        close_above_stack = current_close >= float(latest["stack_top"]) or current_close >= float(best["stack_top"]) * 1.01
        close_above_recent_high = current_close >= prior_8w_high * 0.995 if prior_8w_high > 0 else False
        high_volume = current_rel_vol is not None and current_rel_vol >= self.cfg.min_thrust_rel_volume
        strong_volume = current_rel_vol is not None and current_rel_vol >= self.cfg.strong_thrust_rel_volume
        close_location_ok = current_clv is not None and current_clv >= self.cfg.min_close_location
        early_ignition = up_week and high_volume and close_location_ok and close_above_short and close_above_long_median
        stack_thrust = early_ignition and close_above_stack

        prior_attempt_mask = (
            (work["close"] > work["close"].shift(1))
            & (rel_vol >= self.cfg.min_thrust_rel_volume)
            & (clv >= self.cfg.min_close_location)
            & (work["high"] >= emas[short_cols].max(axis=1) * 0.98)
        )
        prior_attempts = int(prior_attempt_mask.iloc[-21:-1].fillna(False).sum())
        low_volume_pullbacks = int(
            (
                (work["close"] < work["close"].shift(1))
                & (rel_vol <= 0.85)
                & (work["low"] >= float(best["stack_bottom"]) * 0.90)
            )
            .iloc[-12:-1]
            .fillna(False)
            .sum()
        )
        pressure_score = _clamp(prior_attempts / 3.0 * 70.0 + low_volume_pullbacks / 3.0 * 30.0)
        thrust_score = 0.0
        if current_rel_vol is not None:
            thrust_score += _clamp((current_rel_vol - 1.0) / 1.5 * 35.0)
        if current_clv is not None:
            thrust_score += _clamp((current_clv - 0.45) / 0.45 * 20.0)
        if close_above_short:
            thrust_score += 12.0
        if close_above_long_median:
            thrust_score += 13.0
        if close_above_stack:
            thrust_score += 12.0
        if close_above_recent_high:
            thrust_score += 8.0
        thrust_score = _clamp(thrust_score)

        rs_up = _rs_turning_up(f)
        context_score = (55.0 if latest["slope_pct"] is not None and float(latest["slope_pct"]) >= self.cfg.flat_30w_slope_min_pct else 0.0)
        if latest["slope_pct"] is not None and float(latest["slope_pct"]) >= 0:
            context_score += 25.0
        if rs_up:
            context_score += 20.0
        context_score = _clamp(context_score)

        extension_above_stack_pct = (current_close - float(best["stack_top"])) / float(best["stack_top"]) * 100.0 if float(best["stack_top"]) > 0 else 0.0
        extension_penalty = (
            min(35.0, _clamp((extension_above_stack_pct - self.cfg.max_extension_above_stack_pct) * 0.7))
            if extension_above_stack_pct > self.cfg.max_extension_above_stack_pct
            else 0.0
        )
        ema_stack_score = (
            0.18 * float(best["long_ema_compression_score"])
            + 0.15 * float(best["short_ema_compression_score"])
            + 0.15 * float(best["relation_score"])
            + 0.16 * float(best["coil_score"])
            + 0.22 * thrust_score
            + 0.09 * pressure_score
            + 0.05 * context_score
            - extension_penalty
        )
        ema_stack_score = _clamp(ema_stack_score)

        if extension_above_stack_pct > self.cfg.max_extension_above_stack_pct and (stack_thrust or current_close > float(best["stack_top"])):
            phase = "extended_leader"
            bucket = "watch"
            reason = "ema_stack_extended_leader_rank_only"
        elif stack_thrust:
            phase = "stack_thrust"
            bucket = "actionable_now" if strong_volume and current_clv is not None and current_clv >= 0.65 else "near_actionable"
            reason = "ema_stack_high_volume_stack_thrust"
        elif early_ignition:
            phase = "early_ignition"
            bucket = "near_actionable"
            reason = "ema_stack_early_ignition"
        elif (
            current_close > float(best["stack_top"]) * 1.02
            and (close_above_stack or close_above_recent_high)
            and ema_stack_score >= self.cfg.min_watch_score
        ):
            phase = "follow_through"
            bucket = "near_actionable" if (up_week and (current_rel_vol or 0) >= 1.0) else "watch"
            reason = "ema_stack_follow_through_after_coil"
        elif bool(best.get("valid_coil")):
            phase = "coil_watch"
            bucket = "watch"
            reason = "ema_stack_recent_coil_watch"
        else:
            phase = "failed_thrust" if high_volume and up_week else "not_valid"
            bucket = "watch" if phase == "failed_thrust" else "not_valid"
            reason = "ema_stack_incomplete_coil_or_thrust"

        # A tight stack with pressure behind it and a decisively rising 30-week
        # average moves up one rung. The three thresholds are the cell that came
        # out of the grid pre-registered in
        # docs/prereg/2026-08-01-four-detector-grids.md: held out it scores
        # +3.23 against this detector's own unfiltered +0.03, with all seven
        # eligible in-sample cells positive out of sample (+0.98 .. +3.23).
        #
        # It also survived the control this project needed most. `ema_stack_launch`
        # has been caught being a relative-strength proxy before - "the rescue was
        # an RS effect, not the detector" is on the refutations list - and the
        # leading knob here is a momentum knob. Taking the same number of this
        # detector's own signals per date by `rs_rating` instead scores +0.71,
        # so the cell is +2.52 ahead of the RS-matched selection, not behind it.
        # `weinstein`'s cell failed exactly that control and was not written in.
        #
        # `extended_leader` is excluded, and not because of the numbers. That
        # phase exists to say "already too far from the stack to act on", and a
        # rule that promotes it contradicts the thing it is for. Out of sample
        # the cell's extended members scored +4.27, *above* the cell's own
        # +3.23, so leaving them out costs something rather than flattering the
        # result - which is the only reason a restriction chosen after the fact
        # is worth trusting.
        promote = (
            phase != "extended_leader"
            and best["stack_width_pct"] is not None
            and float(best["stack_width_pct"]) <= self.cfg.max_promote_stack_width_pct
            and float(pressure_score) >= self.cfg.min_promote_pressure_score
            and latest["slope_pct"] is not None
            and float(latest["slope_pct"]) >= self.cfg.min_promote_30w_slope_pct
        )
        if promote and bucket in _PROMOTION_RUNGS:
            bucket = _PROMOTION_RUNGS[bucket]
            reason = f"{reason}_promoted_tight_stack_rising_30w"

        min_score = self.cfg.min_thrust_score if phase in {"early_ignition", "stack_thrust"} else self.cfg.min_watch_score
        result.triggered = phase != "not_valid" and ema_stack_score >= min_score
        result.sub_state = phase if result.triggered or phase == "extended_leader" else None
        result.actionability = bucket if result.triggered else "not_valid"
        result.actionability_reason = reason if result.triggered else "ema_stack_score_below_threshold"
        result.score = round(ema_stack_score, 1)
        result.trigger_level = round(max(float(best["stack_top"]), float(latest["stack_top"]), prior_8w_high), 2)
        result.invalidation_level = round(max(0.01, min(float(best["stack_bottom"]), float(latest["stack_bottom"])) * 0.92), 2)
        result.raw_features = {
            "ema_stack_launch_score": round(ema_stack_score, 1),
            "ema_stack_phase": phase,
            "recent_coil_date": best["date"],
            "recent_coil_bars_ago": int(best["bars_ago"]),
            "recent_coil_score": round(float(best["coil_score"]), 1),
            "long_ema_compression_score": round(float(best["long_ema_compression_score"]), 1),
            "short_ema_compression_score": round(float(best["short_ema_compression_score"]), 1),
            "ema_stack_relationship_score": round(float(best["relation_score"]), 1),
            "ema_stack_thrust_score": round(thrust_score, 1),
            "prior_pressure_score": round(pressure_score, 1),
            "weekly_stack_width_pct": round(float(best["stack_width_pct"]), 2),
            "weekly_short_ema_width_pct": round(float(best["short_width_pct"]), 2),
            "weekly_long_ema_width_pct": round(float(best["long_width_pct"]), 2),
            "weekly_stack_spread_pct": round(float(best["spread_pct"]), 2),
            "weekly_30w_slope_pct": round(float(latest["slope_pct"]), 2) if latest["slope_pct"] is not None else None,
            "current_thrust_rel_volume": round(current_rel_vol, 2) if current_rel_vol is not None else None,
            "current_thrust_close_location": round(current_clv, 2) if current_clv is not None else None,
            "price_above_short_stack": close_above_short,
            "price_above_long_median": close_above_long_median,
            "price_above_stack_top": close_above_stack,
            "close_above_recent_high": close_above_recent_high,
            "prior_stack_thrust_attempts": prior_attempts,
            "low_volume_pullbacks_after_coil": low_volume_pullbacks,
            "extension_above_stack_pct": round(extension_above_stack_pct, 2),
            "rs_turning_up": rs_up,
            "ema_stack_ignition": early_ignition,
        }
        result.base_metrics = {
            "stack_top": float(best["stack_top"]),
            "stack_bottom": float(best["stack_bottom"]),
            "recent_coil_date": best["date"],
            "recent_coil_score": float(best["coil_score"]),
        }
        result.reasons.extend(
            [
                f"coil={best['coil_score']:.1f}",
                f"long_ema={best['long_ema_compression_score']:.1f}",
                f"short_ema={best['short_ema_compression_score']:.1f}",
                f"thrust={thrust_score:.1f}",
                f"phase={phase}",
            ]
        )
        if early_ignition:
            result.reasons.append("high_volume_ema_stack_ignition")
        if close_above_recent_high:
            result.reasons.append("close_above_recent_high")
        return result
