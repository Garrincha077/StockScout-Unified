"""10/20 EMA bullish cross detector (daily + weekly).

Stage-transition signal popularised by momentum traders (Stamatoudis,
Pradeep Bonde): when EMA10 crosses up through EMA20 with price above SMA50
and positive relative strength, the stock is entering a fresh leg.

**Only fresh crosses count as triggered.** A cross that happened weeks ago is
not an entry signal — the stock is already extended into a confirmed Stage 2.
Use pullbacks-to-10/20-EMA for re-entries on those, not this detector.

Sub-states:
    daily_cross_fresh    : EMA10 crossed up within last 5 daily bars (1 trading
                           week) — triggered=True, actionable_now
    weekly_cross_fresh   : weekly EMA10 crossed up within last 2 weekly bars —
                           triggered=True, near_actionable (or actionable_now
                           when paired with fresh daily)
    both_cross_fresh     : daily AND weekly both fresh — strongest signal,
                           highest score
    daily_post_cross     : cross 6-10 daily bars ago — informational only,
                           triggered=False (not a fresh entry)
    stale_cross          : EMA10 still above EMA20 but cross >10 daily bars
                           ago — informational only, triggered=False
    not_crossed          : EMA10 not above EMA20 — not_valid
"""
from __future__ import annotations

import pandas as pd

from stock_scout.config.schema import EMACrossSetupConfig
from stock_scout.indicators.moving_averages import ema
from stock_scout.setups.base import SetupDetector, SetupResult


def _bars_since_up_cross(ema10: pd.Series, ema20: pd.Series) -> int | None:
    """Return how many bars ago EMA10 last crossed UP through EMA20 (inclusive).
    None if no up-cross is visible OR if EMA10 currently isn't above EMA20."""
    if len(ema10) < 3 or ema10.iloc[-1] <= ema20.iloc[-1]:
        return None
    above = (ema10 > ema20).astype(bool)
    prev_above = above.shift(1, fill_value=False).astype(bool)
    crossed_up = above & (~prev_above)
    idx_positions = list(crossed_up[crossed_up].index)
    if not idx_positions:
        return None
    last_cross = idx_positions[-1]
    # Bars since last cross (counting back from the last bar)
    try:
        loc_cross = ema10.index.get_loc(last_cross)
    except KeyError:
        return None
    return max(0, len(ema10) - 1 - int(loc_cross))


class EMACrossDetector(SetupDetector):
    name = "ema_cross"

    def __init__(self, cfg: EMACrossSetupConfig):
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
        if df_daily is None or df_daily.empty or len(df_daily) < 30:
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_history"
            return result

        f = features or {}
        close = f.get("close")
        sma50 = f.get("sma50")
        rs_3m = f.get("rs_score_3m")

        # Compute EMAs locally so this detector doesn't depend on enrich having
        # done so (defensive; orchestrator does enrich first but tests may not).
        ema10 = df_daily.get("ema10")
        ema20 = df_daily.get("ema20")
        if ema10 is None or ema20 is None or ema10.isna().all() or ema20.isna().all():
            ema10 = ema(df_daily["close"], 10)
            ema20 = ema(df_daily["close"], 20)

        # --- Daily cross detection -----------------------------------------
        daily_bars_since = _bars_since_up_cross(ema10, ema20)

        # --- Weekly cross detection (optional) -----------------------------
        weekly_bars_since: int | None = None
        if df_weekly is not None and not df_weekly.empty and len(df_weekly) >= 30:
            w_ema10 = df_weekly.get("ema10")
            w_ema20 = df_weekly.get("ema20")
            if w_ema10 is None or w_ema20 is None:
                w_ema10 = ema(df_weekly["close"], 10)
                w_ema20 = ema(df_weekly["close"], 20)
            weekly_bars_since = _bars_since_up_cross(w_ema10, w_ema20)

        result.raw_features = {
            "daily_bars_since_ema_cross_up": daily_bars_since,
            "weekly_bars_since_ema_cross_up": weekly_bars_since,
            "ema10": round(float(ema10.iloc[-1]), 2) if pd.notna(ema10.iloc[-1]) else None,
            "ema20": round(float(ema20.iloc[-1]), 2) if pd.notna(ema20.iloc[-1]) else None,
        }

        # --- Sub-state classification --------------------------------------
        if daily_bars_since is None:
            result.actionability = "not_valid"
            result.actionability_reason = "no_recent_ema10_above_ema20"
            result.sub_state = "not_crossed"
            return result

        # Filter requirements
        if self.cfg.require_above_sma50 and (close is None or sma50 is None or close <= sma50):
            result.failed_conditions.append("below_sma50")
            result.actionability = "not_valid"
            result.actionability_reason = "below_sma50"
            return result
        if self.cfg.require_positive_rs_3m and (rs_3m is None or rs_3m < 0):
            result.failed_conditions.append("negative_rs_3m")
            result.actionability = "not_valid"
            result.actionability_reason = "negative_rs_3m"
            return result

        # Sub-state from freshness
        daily_fresh = daily_bars_since <= self.cfg.fresh_within_bars
        weekly_fresh = (
            weekly_bars_since is not None
            and weekly_bars_since <= self.cfg.weekly_fresh_within_bars
        )

        # Qullamaggie momentum confirmation: RS_3m >= 5 means clearly
        # above-average momentum (not just any positive number). Lower values
        # demote actionable_now → near_actionable.
        rs_3m_val = float(rs_3m) if rs_3m is not None else 0.0
        strong_rs = rs_3m_val >= self.cfg.min_rs_3m_for_actionable

        # Only FRESH crosses are triggered entry signals. Older crosses are
        # informational state (the stock is in confirmed Stage 2, not a fresh
        # entry).
        if daily_fresh and weekly_fresh:
            sub_state = "both_cross_fresh"
            bucket = "actionable_now" if strong_rs else "near_actionable"
            score = 88.0 if strong_rs else 80.0
            triggered = True
        elif daily_fresh:
            sub_state = "daily_cross_fresh"
            bucket = "actionable_now" if strong_rs else "near_actionable"
            score = 78.0 if strong_rs else 70.0
            triggered = True
        elif weekly_fresh:
            sub_state = "weekly_cross_fresh"
            bucket = "near_actionable"
            score = 72.0
            triggered = True
        elif daily_bars_since <= 10:
            # Cross 6-10 bars ago: still constructive, but past the fresh
            # entry window. Mark as informational, do not trigger.
            sub_state = "daily_post_cross"
            bucket = "not_valid"
            score = 0.0
            triggered = False
            result.failed_conditions.append("cross_past_fresh_window")
        else:
            sub_state = "stale_cross"
            bucket = "not_valid"
            score = 0.0
            triggered = False
            result.failed_conditions.append("cross_stale")

        result.sub_state = sub_state
        result.actionability = bucket
        result.actionability_reason = (
            f"{sub_state}_d{daily_bars_since}"
            + (f"_w{weekly_bars_since}" if weekly_bars_since is not None else "")
        )
        result.triggered = triggered

        if not triggered:
            # Still surface the EMA10/EMA20 info via raw_features (already set
            # above) but don't propose trigger/invalidation levels.
            return result

        # Trigger / invalidation only for fresh crosses.
        trigger = float(ema20.iloc[-1])
        result.trigger_level = round(trigger, 2)
        # Stop sits below rising support. Prefer SMA50 (-3% buffer), but on a
        # fresh cross EMA20 can sit at/below SMA50, which would push the stop
        # ABOVE the trigger. Clamp to just under EMA20 so invalidation is
        # always below the entry trigger.
        inval = trigger * 0.97
        if sma50:
            inval = min(inval, float(sma50) * 0.97)
        result.invalidation_level = round(inval, 2)

        result.score = score
        result.reasons.append(f"ema10_up_through_ema20_{daily_bars_since}_bars_ago")
        if weekly_fresh:
            result.reasons.append(f"weekly_cross_also_fresh_{weekly_bars_since}_bars_ago")
        return result
