from __future__ import annotations

import pandas as pd

from stock_scout.config.schema import MinerviniSetupConfig
from stock_scout.setups.actionability import ClassificationInput, classify
from stock_scout.setups.base import SetupDetector, SetupResult
from stock_scout.setups.base_analysis import (
    atr20_at_last_bar,
    extension_from_pivot,
    find_consolidation_base,
    prior_uptrend_pct,
    sma_stack_conditions,
)


class MinerviniDetector(SetupDetector):
    """Mark Minervini Trend Template + VCP.

    Faza 3 rewrite: the Trend Template is now an ELIGIBILITY filter (gates
    whether we look further), while VCP base + pivot is the actual setup
    trigger. Sub-states distinguish:

        trend_template_only  TT passes but no usable base / contractions
        vcp_forming          1-2 contractions, distance > 5% from pivot
        near_pivot           VCP OK, -5% .. -1% from pivot
        at_pivot             -1% .. +0.5% from pivot
        breakout_day         +0.5% .. +2%, vol >= 1.5x, <=2 bars since cross
        early_breakout       2% .. 5%, <=5 bars since cross
        extended_too_late    > 8% from pivot
    """

    name = "minervini"

    def __init__(self, cfg: MinerviniSetupConfig):
        self.cfg = cfg

    # ---- trend template -------------------------------------------------------

    def _trend_template(self, features: dict) -> tuple[bool, dict[str, bool], list[str]]:
        dist_high = features.get("distance_to_52w_high_pct")
        dist_low = features.get("distance_to_52w_low_pct")
        rs_6m = features.get("rs_score_6m")

        # Shared SMA-stack flags (kept in sync with the scorer's trend component).
        stack = sma_stack_conditions(features)
        conds: dict[str, bool] = {
            "close>sma50": stack["close>sma50"],
            "close>sma150": stack["close>sma150"],
            "close>sma200": stack["close>sma200"],
            "sma50>sma150": stack["sma50>sma150"],
            "sma50>sma200": stack["sma50>sma200"],
            "sma200_rising": stack["sma200_rising"],
            "above_52w_low_by_30pct": bool(
                dist_low is not None and dist_low >= self.cfg.min_pct_above_52w_low
            ),
            "within_25pct_of_52w_high": bool(
                dist_high is not None and dist_high >= -self.cfg.max_pct_below_52w_high
            ),
            "positive_rs_6m": bool(rs_6m is not None and rs_6m >= self.cfg.min_rs_vs_spy_6m),
        }
        if self.cfg.require_150_over_200:
            conds["sma150>sma200"] = stack["sma150>sma200"]
        failing = [k for k, v in conds.items() if not v]
        return (len(failing) == 0), conds, failing

    # ---- main detect ----------------------------------------------------------

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
        f = features or {}
        if df_daily.empty or len(df_daily) < 250:
            result.failed_conditions.append("insufficient_history")
            result.actionability = "not_valid"
            result.actionability_reason = "insufficient_history"
            return result

        tt_pass, conds, failing = self._trend_template(f)
        result.raw_features = {
            "close": f.get("close"),
            "sma50": f.get("sma50"),
            "sma150": f.get("sma150"),
            "sma200": f.get("sma200"),
            "distance_to_52w_high_pct": f.get("distance_to_52w_high_pct"),
            "distance_to_52w_low_pct": f.get("distance_to_52w_low_pct"),
            "rs_score_6m": f.get("rs_score_6m"),
            "trend_template_pass": tt_pass,
        }
        result.reasons = [f"PASS:{k}" for k, v in conds.items() if v]
        result.failed_conditions = failing

        if not tt_pass:
            result.actionability = "not_valid"
            result.actionability_reason = f"trend_template_failed_{len(failing)}_conditions"
            result.sub_state = f"failed_{len(failing)}_conditions"
            result.score = round(40.0 * (len(conds) - len(failing)) / max(1, len(conds)), 1)
            return result

        # --- VCP base analysis ------------------------------------------------
        base = find_consolidation_base(
            df_daily, min_length_bars=21, max_depth_pct=35.0
        )
        prior_pct = (
            prior_uptrend_pct(df_daily["close"], base.start_idx) if base is not None else 0.0
        )
        close = float(f.get("close") or df_daily["close"].iloc[-1])
        atr20 = atr20_at_last_bar(df_daily)

        # Default fields when no usable base
        sub_state = "trend_template_only"
        pivot_price: float | None = None
        ext_pct: float | None = None
        ext_atr: float | None = None
        bars_since_cross: int | None = None
        has_clear_pivot = False
        is_wide_loose = False
        n_contractions = 0
        base_length: int | None = None
        base_depth: float | None = None
        vol_dryup: float | None = None

        if base is not None:
            base_length = base.length_bars
            base_depth = base.depth_pct
            n_contractions = base.n_contractions
            vol_dryup = base.volume_dryup_pct
            is_wide_loose = bool(base.is_wide_and_loose)
            pivot_price = float(base.pivot_price)
            has_clear_pivot = pivot_price > 0 and n_contractions >= 1
            # Minervini VCP strictness: each contraction must be visibly
            # tighter than the previous one (default ratio 0.67 = 1/3 smaller).
            vcp_tightening = base.contractions_tightening_at(
                self.cfg.vcp_contraction_ratio_threshold
            )
            result.raw_features["vcp_tightening"] = vcp_tightening

            ext_pct, ext_atr = extension_from_pivot(close, pivot_price, atr20)

            # bars since cross (extension > 0)
            if ext_pct is not None and ext_pct > 0:
                above = df_daily["close"] > pivot_price
                tail = above.iloc[::-1]
                count = 0
                for v in tail:
                    if v:
                        count += 1
                    else:
                        break
                bars_since_cross = count

            # Sub-state derivation
            if not has_clear_pivot or n_contractions < 1:
                sub_state = "trend_template_only"
            elif prior_pct < 30.0 and n_contractions < 2:
                # Not enough prior uptrend to call it a real VCP base
                sub_state = "trend_template_only"
            elif n_contractions < 2 and (ext_pct is None or ext_pct < -5.0):
                sub_state = "vcp_forming"
            elif ext_pct is not None and ext_pct > 8.0:
                sub_state = "extended_too_late"
            elif ext_pct is not None and ext_pct > 2.0:
                if bars_since_cross is not None and bars_since_cross <= 5:
                    sub_state = "early_breakout"
                else:
                    sub_state = "extended_too_late"
            elif ext_pct is not None and ext_pct > 0.5:
                sub_state = "breakout_day"
            elif ext_pct is not None and ext_pct >= -1.0:
                sub_state = "at_pivot"
            elif ext_pct is not None and ext_pct >= -5.0:
                sub_state = "near_pivot"
            else:
                sub_state = "vcp_forming"

        result.sub_state = sub_state
        result.base_metrics = {
            "base_length_bars": base_length,
            "base_depth_pct": round(base_depth, 2) if base_depth is not None else None,
            "n_contractions": n_contractions,
            "pivot_price": pivot_price,
            "volume_dryup_pct": round(vol_dryup, 1) if vol_dryup is not None else None,
            "is_wide_and_loose": is_wide_loose,
            "prior_uptrend_pct": round(prior_pct, 1),
            "extension_pct": round(ext_pct, 2) if ext_pct is not None else None,
            "extension_atr_multiples": round(ext_atr, 2) if ext_atr is not None else None,
            "bars_since_cross": bars_since_cross,
        }
        if pivot_price:
            result.raw_features["pivot_price"] = pivot_price
        if ext_pct is not None:
            result.raw_features["extension_pct"] = round(ext_pct, 2)

        # --- Actionability via centralized classifier ------------------------
        cls_inp = ClassificationInput(
            setup_name=self.name,
            triggered=True,
            extension_pct=ext_pct,
            extension_atr_multiples=ext_atr,
            bars_since_breakout=bars_since_cross,
            base_length_bars=base_length,
            base_depth_pct=base_depth,
            n_contractions=n_contractions,
            is_wide_and_loose=is_wide_loose,
            volume_dryup_pct=vol_dryup,
            has_clear_pivot=has_clear_pivot,
            disqualifiers=[],
            pocket_pivot=bool(f.get("pocket_pivot")),
            higher_lows=int(f.get("higher_lows") or 0),
        )
        bucket, reason = classify(cls_inp)
        # Override: if sub-state says trend_template_only, demote to "watch"
        if sub_state == "trend_template_only" and bucket not in ("excluded", "not_valid"):
            bucket = "watch"
            reason = "trend_template_only_no_base"
        result.actionability = bucket
        result.actionability_reason = reason
        # Minervini explicitly forbids chasing extended breakouts. Only fresh
        # entries / pullbacks-to-pivot / forming bases count as triggered.
        result.triggered = bucket in ("actionable_now", "near_actionable", "forming", "watch")

        # Trigger / invalidation
        if pivot_price:
            result.trigger_level = round(pivot_price, 2)
        sma50 = f.get("sma50")
        if sma50:
            result.invalidation_level = round(float(sma50) * 0.97, 2)

        # Score derivation
        if bucket == "actionable_now":
            score = 90.0 if sub_state in ("breakout_day", "at_pivot") else 80.0
        elif bucket == "near_actionable":
            score = 72.0
        elif bucket == "forming":
            # Setup taking shape pre-trigger. Informative-early, ranks below a
            # fresh actionable entry but above passive watch.
            score = 60.0
        elif bucket == "watch":
            score = 55.0 if sub_state == "vcp_forming" else 45.0
        elif bucket == "extended_too_late":
            # Minervini: never chase. Cap at 30 so extended never ranks above
            # a constructive base.
            score = 25.0
        else:
            score = 30.0
        # Bonuses for high RS, near-high, n_contractions — only apply to
        # entry-grade buckets, never inflate extended/excluded.
        if bucket in ("actionable_now", "near_actionable", "forming", "watch"):
            rs_6m = f.get("rs_score_6m")
            if rs_6m is not None and rs_6m > 10:
                score += min(8.0, rs_6m * 0.2)
            if n_contractions >= 3:
                score += 5.0
            # Minervini VCP gold-standard: successively tighter contractions.
            if base is not None and base.contractions_tightening_at(
                self.cfg.vcp_contraction_ratio_threshold
            ):
                score += 6.0
                result.reasons.append("vcp_successively_tighter")
        # Hard cap extended scores.
        if bucket == "extended_too_late":
            score = min(score, 30.0)
        result.score = round(min(100.0, max(0.0, score)), 1)

        return result
