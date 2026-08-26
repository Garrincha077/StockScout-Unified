"""Eric Wish GMMA / Guppy setup detector.

Detects the RWB transition that Wish teaches visually: the short EMA group
(3, 5, 8, 10, 12, 15) crossing above the long group (30, 35, 40, 45, 50, 60),
or both groups compressing tightly while the short group turns up.
"""
from __future__ import annotations

import pandas as pd

from stock_scout.config.schema import GuppySetupConfig
from stock_scout.indicators.guppy import LONG_PERIODS, SHORT_PERIODS, guppy_state
from stock_scout.indicators.moving_averages import ema
from stock_scout.setups.base import SetupDetector, SetupResult


def _ema_frame(close: pd.Series) -> pd.DataFrame:
    data = {f"ema{p}": ema(close, p) for p in (*SHORT_PERIODS, *LONG_PERIODS)}
    return pd.DataFrame(data, index=close.index)


def _bars_since_rwb_cross(emas: pd.DataFrame) -> int | None:
    short_cols = [f"ema{p}" for p in SHORT_PERIODS]
    long_cols = [f"ema{p}" for p in LONG_PERIODS]
    valid = emas[short_cols + long_cols].notna().all(axis=1)
    if valid.sum() < 2:
        return None
    short_min = emas[short_cols].min(axis=1)
    long_max = emas[long_cols].max(axis=1)
    rwb = (short_min > long_max) & valid
    if not bool(rwb.iloc[-1]):
        return None
    crossed = rwb & (~rwb.shift(1, fill_value=False))
    idx_positions = list(crossed[crossed].index)
    if not idx_positions:
        return None
    loc = emas.index.get_loc(idx_positions[-1])
    return max(0, len(emas) - 1 - int(loc))


class GuppyDetector(SetupDetector):
    name = "guppy"

    def __init__(self, cfg: GuppySetupConfig):
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
        if df_daily is None or df_daily.empty or len(df_daily) < max(LONG_PERIODS) + 10:
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_history"
            return result

        close = df_daily["close"]
        emas = _ema_frame(close)
        if emas.iloc[-1].isna().any():
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_ema_history"
            return result

        short_cols = [f"ema{p}" for p in SHORT_PERIODS]
        long_cols = [f"ema{p}" for p in LONG_PERIODS]
        last_short = [float(emas[c].iloc[-1]) for c in short_cols]
        last_long = [float(emas[c].iloc[-1]) for c in long_cols]
        last_close = float(close.iloc[-1])

        short_min = min(last_short)
        short_max = max(last_short)
        long_min = min(last_long)
        long_max = max(last_long)
        avg_short = sum(last_short) / len(last_short)
        avg_long = sum(last_long) / len(last_long)
        spread_pct = (avg_short - avg_long) / avg_long * 100.0 if avg_long else 0.0
        short_width_pct = (short_max - short_min) / last_close * 100.0 if last_close else 0.0
        long_width_pct = (long_max - long_min) / last_close * 100.0 if last_close else 0.0
        extension_above_long_pct = (last_close - long_max) / long_max * 100.0 if long_max else 0.0

        state = guppy_state(close)
        bars_since_cross = _bars_since_rwb_cross(emas)
        rwb_now = short_min > long_max
        tight = (
            abs(spread_pct) <= self.cfg.tight_spread_pct
            and short_width_pct <= self.cfg.max_short_group_width_pct
            and long_width_pct <= self.cfg.max_long_group_width_pct
        )
        rlc = int(state["rlc"] or 0)
        rs_3m = (features or {}).get("rs_score_3m")

        weekly_state = "unknown"
        if df_weekly is not None and not df_weekly.empty and len(df_weekly) >= max(LONG_PERIODS):
            weekly_state = str(guppy_state(df_weekly["close"]).get("state") or "unknown")

        result.raw_features = {
            "guppy_state": state["state"],
            "guppy_weekly_state": weekly_state,
            "guppy_rlc": rlc,
            "guppy_spread_pct": round(spread_pct, 2),
            "guppy_short_group_width_pct": round(short_width_pct, 2),
            "guppy_long_group_width_pct": round(long_width_pct, 2),
            "bars_since_guppy_rwb_cross": bars_since_cross,
            "guppy_tight": tight,
            "short_group_rising": state["short_rising"],
            "long_group_rising": state["long_rising"],
            "extension_above_long_group_pct": round(extension_above_long_pct, 2),
            "ema_short_min": round(short_min, 2),
            "ema_long_max": round(long_max, 2),
        }

        if self.cfg.require_positive_rs_3m and (rs_3m is None or float(rs_3m) < 0):
            result.failed_conditions.append("negative_rs_3m")
            result.actionability = "not_valid"
            result.actionability_reason = "negative_rs_3m"
            return result

        if extension_above_long_pct > self.cfg.max_extension_above_long_group_pct:
            result.failed_conditions.append("extended_above_guppy_long_group")
            result.actionability = "extended_too_late"
            result.actionability_reason = f"extended_{extension_above_long_pct:.1f}%_above_long_group"
            result.sub_state = "extended_rwb" if rwb_now else "extended"
            return result

        fresh_cross = bars_since_cross is not None and bars_since_cross <= self.cfg.fresh_rwb_cross_bars
        short_rising = bool(state["short_rising"])
        long_rising = bool(state["long_rising"])

        if fresh_cross and rlc >= self.cfg.min_rlc:
            result.triggered = True
            result.sub_state = "rwb_cross_fresh"
            result.actionability = "actionable_now" if (rs_3m is None or float(rs_3m) >= 5.0) else "near_actionable"
            result.score = 86.0 if weekly_state == "RWB" else 80.0
            result.reasons.append(f"guppy_rwb_cross_{bars_since_cross}_bars_ago")
        elif tight and short_rising and rlc >= self.cfg.min_rlc and state["state"] in ("RWB", "compression"):
            result.triggered = True
            result.sub_state = "guppy_tight_transition" if state["state"] == "compression" else "rwb_tight_pullback"
            result.actionability = "forming" if state["state"] == "compression" else "near_actionable"
            result.score = 74.0 if long_rising else 68.0
            result.reasons.append("guppy_groups_tight_short_group_turning_up")
        else:
            result.actionability = "not_valid"
            result.actionability_reason = f"{state['state']}_not_fresh_or_not_tight"
            if not fresh_cross:
                result.failed_conditions.append("no_fresh_rwb_cross")
            if not tight:
                result.failed_conditions.append("guppy_groups_not_tight")
            if rlc < self.cfg.min_rlc:
                result.failed_conditions.append(f"rlc_below_{self.cfg.min_rlc}")
            return result

        recent_high = float(df_daily["high"].tail(10).max()) if "high" in df_daily.columns else last_close
        result.trigger_level = round(max(recent_high, last_close), 2)
        result.invalidation_level = round(long_min * 0.97, 2)
        result.actionability_reason = f"{result.sub_state}_rlc{rlc}_spread{spread_pct:.1f}%"
        if weekly_state == "RWB":
            result.reasons.append("weekly_guppy_rwb")
        return result
