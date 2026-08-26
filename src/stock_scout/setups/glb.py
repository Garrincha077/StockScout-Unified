from __future__ import annotations

import pandas as pd

from stock_scout.config.schema import GLBSetupConfig
from stock_scout.indicators.patterns import find_glb_level
from stock_scout.setups.actionability import ClassificationInput, classify
from stock_scout.setups.base import SetupDetector, SetupResult
from stock_scout.setups.base_analysis import (
    atr20_at_last_bar,
    extension_from_pivot,
    find_consolidation_base,
)


class GLBDetector(SetupDetector):
    """Eric Wish Green Line Breakout.

    Rewrite (Faza 3): the detector now produces an actionability bucket via
    :func:`setups.actionability.classify`, plus base-quality metrics so the
    pipeline can split candidates into Actionable / Near / Watch / Excluded.

    A candidate qualifies as GLB when:
      - A long-term horizontal high is held for >= min_months_without_new_high
      - After that high, price has carved a constructive base
        (length >= 60 bars, depth <= 35%)
      - Close is currently above rising 50/200 SMAs

    Sub-states:
      pre_breakout            within (-5%, -2%) of the GLB level
      at_pivot                within [-2%, +0.5%]
      breakout_day            +0.5% to +3%, vol >= 1.5x, <=2 bars since cross
      early_breakout          +0.5% to +5%, 2-5 bars since cross
      extended                > +5% or > 5 trading days since the cross
    """

    name = "glb"

    def __init__(self, cfg: GLBSetupConfig):
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
        if df_daily.empty or len(df_daily) < 100:
            result.failed_conditions.append("insufficient_history")
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_history"
            return result

        f = features or {}
        min_days = self.cfg.min_months_without_new_high * 21
        glb = find_glb_level(df_daily["close"], min_days_without_new_high=min_days)
        if glb is None:
            result.failed_conditions.append("no_valid_glb_level")
            result.actionability = "not_valid"
            result.actionability_reason = "no_valid_glb_level"
            return result

        close = float(df_daily["close"].iloc[-1])
        result.raw_features["glb_level"] = glb.level
        result.raw_features["glb_set_at"] = str(glb.set_at.date())
        result.raw_features["glb_days_held"] = glb.days_held

        # SMA gating (eligibility)
        sma50 = f.get("sma50")
        sma200 = f.get("sma200")
        if self.cfg.require_above_sma50 and (sma50 is None or close < sma50):
            result.failed_conditions.append("close<sma50")
            result.actionability = "not_valid"
            result.actionability_reason = "close<sma50"
            return result
        if self.cfg.require_above_sma200 and (sma200 is None or close < sma200):
            result.failed_conditions.append("close<sma200")
            result.actionability = "not_valid"
            result.actionability_reason = "close<sma200"
            return result

        # --- Lateral-resistance test count (Wish rule) ----------------------
        # Count how many times the GLB level has been tested (high within
        # tolerance, close below level) before the most recent bar. A real GLB
        # must be contested at least `min_resistance_touches` times.
        from stock_scout.indicators.highs_lows import count_resistance_touches

        if glb.set_at in df_daily.index:
            base_window = df_daily.iloc[df_daily.index.get_indexer([glb.set_at])[0] :]
        else:
            base_window = df_daily
        touches = count_resistance_touches(
            base_window["high"],
            base_window["close"],
            level=glb.level,
            tolerance_pct=self.cfg.resistance_tolerance_pct,
        )
        result.raw_features["resistance_touches"] = touches
        if touches < self.cfg.min_resistance_touches:
            result.failed_conditions.append(
                f"glb_only_{touches}_touches(<{self.cfg.min_resistance_touches})"
            )
            result.actionability = "not_valid"
            result.actionability_reason = "glb_untested_level"
            return result

        # Consolidation base since the GLB high
        base = find_consolidation_base(
            df_daily.iloc[max(0, df_daily.index.get_indexer([glb.set_at])[0]) :]
            if glb.set_at in df_daily.index
            else df_daily,
            min_length_bars=60,
            max_depth_pct=35.0,
        )
        if base is not None:
            result.base_metrics = {
                "base_length_bars": base.length_bars,
                "base_depth_pct": round(base.depth_pct, 2),
                "n_contractions": base.n_contractions,
                "pivot_price": base.pivot_price,
                "volume_dryup_pct": round(base.volume_dryup_pct, 1),
                "is_wide_and_loose": base.is_wide_and_loose,
                "contractions_non_increasing": getattr(
                    base, "contractions_non_increasing", None
                ),
            }
            # Strict narrowing check (Wish/Minervini VCP rigor): once we have
            # ≥2 contractions, each subsequent one should be ≤ the prior
            # (tolerated within 10%). When only 1 contraction is detected we
            # can't measure narrowing — skip rather than penalise.
            if (
                self.cfg.require_narrowing_contractions
                and base.n_contractions >= 2
                and base.contractions_non_increasing is False
            ):
                result.failed_conditions.append("contractions_not_narrowing")
                result.actionability = "not_valid"
                result.actionability_reason = "wide_and_loose_base"
                return result

        # Distance to GLB
        distance_pct = (close - glb.level) / glb.level * 100.0
        result.raw_features["distance_to_glb_pct"] = round(distance_pct, 2)
        volume_ratio = float(f.get("volume_ratio_50d") or 0.0)
        result.raw_features["volume_ratio_50d"] = round(volume_ratio, 2)

        # Sub-state classification
        bars_since_cross: int | None = None
        if distance_pct > 0:
            # Walk back to find where close first crossed the GLB
            close_series = df_daily["close"]
            above = close_series > glb.level
            # bars_since_cross = number of consecutive True at the tail
            tail = above.iloc[::-1]
            count = 0
            for v in tail:
                if v:
                    count += 1
                else:
                    break
            bars_since_cross = count
            result.raw_features["bars_since_cross"] = bars_since_cross

        # Classify
        cls_inp = ClassificationInput(
            setup_name=self.name,
            triggered=True,
            extension_pct=distance_pct,
            extension_atr_multiples=None,
            bars_since_breakout=bars_since_cross,
            base_length_bars=base.length_bars if base else None,
            base_depth_pct=base.depth_pct if base else None,
            n_contractions=base.n_contractions if base else None,
            is_wide_and_loose=base.is_wide_and_loose if base else False,
            volume_dryup_pct=base.volume_dryup_pct if base else None,
            has_clear_pivot=base is not None and base.n_contractions >= 1,
            disqualifiers=[],
            pocket_pivot=bool(f.get("pocket_pivot")),
            higher_lows=int(f.get("higher_lows") or 0),
        )
        bucket, reason = classify(cls_inp)
        result.actionability = bucket
        result.actionability_reason = reason

        # Derive sub-state string for human reports
        if distance_pct < -5.0:
            sub_state = "far_from_glb"
        elif -5.0 <= distance_pct < -2.0:
            sub_state = "pre_breakout"
            result.reasons.append(f"close_within_{abs(distance_pct):.1f}%_below_GLB")
        elif -2.0 <= distance_pct <= 0.5:
            sub_state = "at_pivot"
        elif distance_pct <= 3.0 and volume_ratio >= self.cfg.min_volume_ratio_for_breakout and (
            bars_since_cross is None or bars_since_cross <= 2
        ):
            sub_state = "breakout_day"
            result.reasons.append(f"breakout_with_volume_x{volume_ratio:.2f}")
        elif distance_pct <= 5.0 and (bars_since_cross is None or bars_since_cross <= 5):
            sub_state = "early_breakout"
        else:
            sub_state = "extended"
            result.warning_flags.append(f"extended_{distance_pct:.1f}%_above_GLB")
        result.sub_state = sub_state

        # Triggered = real entry signal. "extended_too_late" is NOT triggered:
        # Wish-style breakouts are not chased after 5%. Trader waits for next
        # base. UI showing extended as "active signal" misleads.
        result.triggered = bucket in ("actionable_now", "near_actionable", "forming", "watch")

        result.trigger_level = glb.level
        invalidation = max(glb.level * 0.93, (sma50 or 0) * 0.97)
        result.invalidation_level = round(invalidation, 2)

        # ATR-extended check
        atr20 = atr20_at_last_bar(df_daily)
        if atr20:
            _, atr_mult = extension_from_pivot(close, glb.level, atr20)
            if atr_mult is not None:
                result.raw_features["atr_extension_multiples"] = round(atr_mult, 2)

        # Score derivation
        if bucket == "actionable_now":
            score = 90.0 if sub_state == "breakout_day" else 80.0
        elif bucket == "near_actionable":
            score = 70.0
        elif bucket == "forming":
            score = 58.0
        elif bucket == "watch":
            score = 55.0
        elif bucket == "extended_too_late":
            # Wish: extended GLB → wait for next base, don't chase. Cap at 30.
            score = max(15.0, 45.0 - distance_pct * 2)
        else:
            score = 40.0
        # Hard cap extended scores so they never compete with fresh entries.
        if bucket == "extended_too_late":
            score = min(score, 30.0)
        result.score = round(min(100.0, max(0.0, score)), 1)

        d52 = f.get("distance_to_52w_high_pct")
        if d52 is not None and d52 > -5.0:
            result.reasons.append(f"near_52w_high({d52:.1f}%)")

        return result
