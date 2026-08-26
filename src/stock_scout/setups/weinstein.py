from __future__ import annotations

import pandas as pd

from stock_scout.config.schema import WeinsteinSetupConfig
from stock_scout.indicators.moving_averages import is_rising, sma
from stock_scout.setups.actionability import ClassificationInput, classify
from stock_scout.setups.base import SetupDetector, SetupResult


class WeinsteinDetector(SetupDetector):
    """Stan Weinstein Stage 2 (Faza 3 rewrite).

    Operates on WEEKLY bars. The legacy version only checked
    `weekly close > 30wSMA + rising slope`, which flagged any uptrend as
    Stage 2 regardless of whether a real Stage 1 base preceded it. The
    rewrite distinguishes:

        stage_1_late      close near 30wSMA, SMA flattening or just turning up,
                           preceded by >=10w base; not yet breaking out
        stage_2_breakout  weekly close > 30wSMA strictly rising, close at 26w
                           high, weekly volume >= 1.4x 10w avg
        stage_2_early     in Stage 2 < 8 weeks, extension < 15%
        stage_2_extended  > 25% above 30wSMA or > 26 weeks since breakout
        not_valid         missing pre-Stage-1 base, hard rules failed
    """

    name = "weinstein"

    def __init__(self, cfg: WeinsteinSetupConfig):
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
        if (
            df_weekly is None
            or df_weekly.empty
            or len(df_weekly) < self.cfg.weekly_sma_period + 12
        ):
            result.failed_conditions.append("insufficient_weekly_history")
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_weekly_history"
            return result

        wclose = df_weekly["close"]
        wvol = df_weekly["volume"] if "volume" in df_weekly.columns else None
        sma_w = sma(wclose, self.cfg.weekly_sma_period)
        rising_w = is_rising(sma_w, self.cfg.sma_rising_lookback_weeks)

        last_close = float(wclose.iloc[-1])
        last_sma = sma_w.iloc[-1]
        if pd.isna(last_sma):
            result.failed_conditions.append("missing_30w_sma")
            result.actionability = "not_valid"
            result.actionability_reason = "missing_30w_sma"
            return result

        last_sma_f = float(last_sma)
        extension_pct = (last_close - last_sma_f) / last_sma_f * 100.0
        sma_rising = bool(rising_w.iloc[-1])
        # Looser "flattening to rising" check: slope over the last `lookback`
        # weeks not strongly negative.
        try:
            slope_pct = (
                (last_sma_f - float(sma_w.iloc[-self.cfg.sma_rising_lookback_weeks - 1]))
                / max(1e-9, float(sma_w.iloc[-self.cfg.sma_rising_lookback_weeks - 1]))
                * 100.0
            )
        except Exception:  # noqa: BLE001
            slope_pct = 0.0
        sma_flattening_or_rising = slope_pct >= -0.5

        # SMA slope INFLECTION: was the 30w SMA flat/declining one window ago and
        # now turning up? This is the heart of a Stage 1->2 transition — the
        # moment the long base stops falling and demand takes over.
        lb = self.cfg.sma_rising_lookback_weeks
        try:
            prev_anchor = float(sma_w.iloc[-2 * lb - 1])
            mid_anchor = float(sma_w.iloc[-lb - 1])
            prior_slope_pct = (mid_anchor - prev_anchor) / max(1e-9, prev_anchor) * 100.0
        except Exception:  # noqa: BLE001
            prior_slope_pct = slope_pct
        sma_inflection = slope_pct > 0.0 and prior_slope_pct <= 0.5

        # 26-week high
        recent_high_26w = (
            float(wclose.rolling(26, min_periods=26).max().iloc[-1])
            if len(wclose) >= 26
            else float("nan")
        )
        at_26w_high = pd.notna(recent_high_26w) and last_close >= recent_high_26w - 1e-9

        # Weeks since breakout: first week where close > 30wSMA after a prior <= bar.
        weeks_since_breakout: int | None = None
        prior_below: bool | None = None
        if len(sma_w) >= 2:
            crossed = (wclose > sma_w).astype(int)
            # Walk back from the last week until we find a week where close <= sma.
            count = 0
            for c in crossed.iloc[::-1]:
                if c == 1:
                    count += 1
                else:
                    prior_below = True
                    break
            else:
                prior_below = False
            weeks_since_breakout = count

        # Pre-Stage-1 base check: at least 10 weeks of flat-to-down weekly
        # closes before the most recent breakout.
        pre_base_ok = True
        if weeks_since_breakout is not None and weeks_since_breakout >= 1:
            base_end_idx = len(wclose) - weeks_since_breakout
            base_start_idx = max(0, base_end_idx - 12)
            base_slice = wclose.iloc[base_start_idx:base_end_idx]
            if len(base_slice) < 10:
                pre_base_ok = False
            else:
                base_depth = (base_slice.max() - base_slice.min()) / max(1e-9, base_slice.max()) * 100.0
                # A real Stage 1 base is contained; a vertical run-up isn't
                if base_depth > 35.0:
                    pre_base_ok = False
                # And shouldn't be a strong uptrend already
                rise = (base_slice.iloc[-1] - base_slice.iloc[0]) / max(1e-9, base_slice.iloc[0]) * 100.0
                if rise > 20.0:
                    pre_base_ok = False

        # Weekly volume confirmation on the breakout week
        weekly_vol_ratio: float | None = None
        if wvol is not None and len(wvol) >= 11:
            avg_10w_vol = float(wvol.rolling(10, min_periods=10).mean().iloc[-1])
            if avg_10w_vol > 0:
                # use volume at breakout week if known, otherwise this week
                idx = (
                    len(wvol) - weeks_since_breakout
                    if weeks_since_breakout and weeks_since_breakout >= 1 and weeks_since_breakout <= len(wvol)
                    else -1
                )
                try:
                    bo_vol = float(wvol.iloc[idx])
                    weekly_vol_ratio = bo_vol / avg_10w_vol
                except Exception:  # noqa: BLE001
                    pass

        # RS trend annotation. 52w high on RS line beats 50d high — it's the
        # canonical Stage-2 leading signal (Weinstein/O'Neil).
        rs_trend = "unknown"
        f = features or {}
        rs_line_at_52w = f.get("rs_line_at_52w_high") is True
        if rs_line_at_52w:
            rs_trend = "leading_at_52w_high"
        elif f.get("rs_line_at_50d_high") is True:
            rs_trend = "rising_strongly"
        elif (f.get("rs_score_3m") or 0) > 0:
            rs_trend = "positive"
        else:
            rs_trend = "weak"

        # --- Stage 1 -> 2 transition flag ------------------------------------
        # Early entry: price has JUST reclaimed the 30w SMA (within
        # transition_max_weeks), the SMA is inflecting up, and volume is
        # expanding off the base. Catches the move as it ENTERS Stage 2 rather
        # than once it is already established / extended.
        recently_reclaimed = (
            weeks_since_breakout is not None
            and 1 <= weeks_since_breakout <= self.cfg.transition_max_weeks
        )
        vol_expanding = (
            weekly_vol_ratio is not None and weekly_vol_ratio >= self.cfg.transition_min_vol_ratio
        )
        if not vol_expanding:
            ve = (features or {}).get("volume_expansion_5_50")
            vol_expanding = ve is not None and ve >= self.cfg.transition_min_vol_ratio
        is_transition = bool(
            last_close > last_sma_f
            and recently_reclaimed
            and sma_inflection
            and vol_expanding
            and pre_base_ok
            and extension_pct <= self.cfg.max_extension_from_30w_sma_pct
        )

        result.raw_features = {
            "weekly_close": round(last_close, 2),
            "sma_30w": round(last_sma_f, 2),
            "distance_to_30w_sma_pct": round(extension_pct, 2),
            "sma_30w_rising": sma_rising,
            "sma_30w_slope_pct_over_lookback": round(slope_pct, 3),
            "sma_30w_prior_slope_pct": round(prior_slope_pct, 3),
            "sma_30w_inflecting_up": sma_inflection,
            "at_26w_high": at_26w_high,
            "weeks_since_breakout": weeks_since_breakout,
            "weekly_vol_ratio": round(weekly_vol_ratio, 2) if weekly_vol_ratio else None,
            "stage_1_to_2_transition": is_transition,
            "pre_base_ok": pre_base_ok,
            "rs_trend": rs_trend,
        }

        # --- Stage sub-state derivation --------------------------------------
        sub_state: str
        if last_close <= last_sma_f:
            if -3.0 <= extension_pct <= 1.0 and sma_flattening_or_rising and pre_base_ok:
                sub_state = "stage_1_late"
            else:
                result.actionability = "not_valid"
                result.actionability_reason = "close_below_30wSMA_and_no_stage1_setup"
                result.sub_state = "not_in_stage_2"
                result.score = 25.0
                return result
        else:
            # Above SMA
            if not sma_rising and not sma_flattening_or_rising:
                result.actionability = "not_valid"
                result.actionability_reason = "30wSMA_falling"
                result.sub_state = "not_in_stage_2"
                result.score = 30.0
                return result
            if extension_pct > self.cfg.max_extension_from_30w_sma_pct or (
                weeks_since_breakout is not None and weeks_since_breakout > 26
            ):
                sub_state = "stage_2_extended"
            elif is_transition:
                # Earliest high-quality entry — price entering Stage 2 now.
                sub_state = "stage_1_to_2_transition"
            elif (
                at_26w_high
                and sma_rising
                and (weekly_vol_ratio is None or weekly_vol_ratio >= 1.4)
                and (weeks_since_breakout is None or weeks_since_breakout <= 4)
                and pre_base_ok
            ):
                sub_state = "stage_2_breakout"
            elif extension_pct < 15.0 and (weeks_since_breakout or 0) < 8 and pre_base_ok:
                sub_state = "stage_2_early"
            elif not pre_base_ok:
                sub_state = "stage_2_no_base"
            else:
                sub_state = "stage_2_mid"

        # Disqualifiers
        disq: list[str] = []
        if not pre_base_ok:
            disq.append("no_stage1_base")

        # Classify via the centralized helper using extension vs 30wSMA
        cls_inp = ClassificationInput(
            setup_name=self.name,
            triggered=True,
            extension_pct=extension_pct,
            bars_since_breakout=weeks_since_breakout,
            base_length_bars=None,
            base_depth_pct=None,
            n_contractions=None,
            is_wide_and_loose=False,
            has_clear_pivot=True,
            disqualifiers=disq,
        )
        bucket, reason = classify(cls_inp)
        # Override mapping for Weinstein-specific sub-states
        if sub_state == "stage_1_to_2_transition":
            # Fresh transition with confirming volume = prime Weinstein buy.
            bucket = "actionable_now" if (at_26w_high or (weeks_since_breakout or 99) <= 3) else "near_actionable"
            reason = f"stage_1_to_2_transition_ext_{extension_pct:.1f}%"
        elif sub_state == "stage_2_breakout":
            bucket = "actionable_now"
            reason = f"stage_2_breakout_ext_{extension_pct:.1f}%"
        elif sub_state == "stage_2_early":
            # A promotion lived here from 17c3fd8 until 2026-08-01: a slope over
            # `min_promote_slope_pct` moved the signal to `actionable_now`. It
            # was reverted, and the reason is worth keeping so it is not
            # reinvented.
            #
            # Everything it was measured on held up - the cohort really does
            # score +2.21 in sample and +2.42 held out, and folding it in really
            # did move the bucket in both blocks. What was never asked is
            # whether it beat the free alternative. Taking the same number of
            # weinstein's *own* signals per date by `rs_rating` descending
            # scores +2.09 in sample and **+7.62 held out** against the
            # promotion's +2.42 - so the rule is 5.20 *behind* sorting the very
            # same signals by a column the app already has.
            #
            # The condition was pure momentum: slope alone, with nothing
            # offsetting how far the stock had already run. That is what an RS
            # proxy looks like from the inside, and this project had already
            # recorded the same failure for `ema_stack_launch`.
            #
            # What survives is a genuine interaction, not this rule: the top ~13
            # weinstein signals by RS score +7.62 held out while the top 13 of
            # the *whole universe* by RS scores -0.71. The detector earns its
            # place as a filter under an RS ranking. That is a different rule and
            # gets its own registered test before any of it is written down.
            bucket = "near_actionable"
            reason = f"stage_2_early_ext_{extension_pct:.1f}%"
        elif sub_state == "stage_1_late":
            bucket = "watch"
            reason = "stage_1_late"
        elif sub_state == "stage_2_extended":
            bucket = "extended_too_late"
            reason = f"stage_2_extended_ext_{extension_pct:.1f}%"
        elif sub_state == "stage_2_mid":
            bucket = "watch"
            reason = "stage_2_mid"
        elif sub_state == "stage_2_no_base":
            bucket = "excluded"
            reason = "no_stage1_base"

        result.actionability = bucket
        result.actionability_reason = reason
        result.sub_state = sub_state
        result.disqualifiers = disq
        # stage_2_extended is "stock is in confirmed Stage 2, but >25% above
        # 30wSMA = too far for fresh entry". Weinstein himself: wait for
        # pullback to 30wSMA. Not a triggered entry signal.
        result.triggered = bucket in ("actionable_now", "near_actionable", "forming", "watch")

        # Trigger / invalidation
        if pd.notna(recent_high_26w):
            result.trigger_level = round(float(recent_high_26w), 2)
        result.invalidation_level = round(last_sma_f * 0.97, 2)

        # Score
        if bucket == "actionable_now":
            score = 88.0
        elif bucket == "near_actionable":
            score = 72.0
        elif bucket == "forming":
            score = 60.0
        elif bucket == "watch":
            score = 55.0
        elif bucket == "extended_too_late":
            # Extended Stage 2 — informational, not an entry signal. Cap at 30.
            score = max(15.0, 50.0 - max(0.0, extension_pct - 20.0))
        else:
            score = 35.0
        if sub_state == "stage_1_to_2_transition":
            score += 4.0  # confirmed early transition with volume
            result.reasons.append("stage_1_to_2_transition(sma_inflecting_up)")
        if rs_trend == "leading_at_52w_high":
            score += 15.0  # canonical Stage-2 leading signal (Weinstein + O'Neil)
            result.reasons.append("RS_line_at_52w_high")
        elif rs_trend == "rising_strongly":
            score += 8.0
        elif rs_trend == "positive":
            score += 3.0
        if weekly_vol_ratio and weekly_vol_ratio >= 1.4:
            score += 5.0
        result.score = round(min(100.0, max(0.0, score)), 1)

        result.reasons.append(f"weekly_close_above_30wSMA(ext={extension_pct:.1f}%)")
        if sma_rising:
            result.reasons.append("30wSMA_rising")
        result.reasons.append(f"rs_trend={rs_trend}")
        if weekly_vol_ratio:
            result.reasons.append(f"weekly_vol_ratio_{weekly_vol_ratio:.2f}")
        return result
