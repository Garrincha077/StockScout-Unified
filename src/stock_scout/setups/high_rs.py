from __future__ import annotations

import pandas as pd

from stock_scout.config.schema import HighRSSetupConfig
from stock_scout.setups.actionability import ClassificationInput, classify
from stock_scout.setups.base import SetupDetector, SetupResult


class HighRSDetector(SetupDetector):
    """52w-high + Relative Strength setup (Faza 3 rewrite).

    This is a *combining* filter — it does NOT do its own base analysis. It
    just verifies the eligibility conditions (near 52w high, strong RS,
    constructive ext from SMA50) and routes to actionability buckets via the
    centralized classifier. Use it as a sanity boost on top of GLB /
    Minervini / Tight rather than as a standalone setup.

    Sub-states:
        actionable          near 52w high, low extension, strong RS
        watch_extended      close to 52w high but already 15-25% above SMA50
        excluded            > 25% above SMA50 (too vertical)
    """

    name = "high_rs"

    def __init__(self, cfg: HighRSSetupConfig):
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
        f = features or {}
        dist_high = f.get("distance_to_52w_high_pct")
        rs_3m = f.get("rs_score_3m")
        rs_6m = f.get("rs_score_6m")
        close = f.get("close")
        sma50 = f.get("sma50")
        vcp = float(f.get("vcp_score") or 0.0)

        if dist_high is None or close is None:
            result.failed_conditions.append("missing_features")
            result.actionability = "not_valid"
            result.actionability_reason = "missing_features"
            return result

        if dist_high < -self.cfg.max_distance_to_52w_high_pct:
            result.failed_conditions.append(f"too_far_from_high({dist_high:.1f}%)")
            result.actionability = "not_valid"
            result.actionability_reason = "too_far_from_52w_high"
            return result

        rs = rs_6m if rs_6m is not None else rs_3m
        if rs is None:
            result.failed_conditions.append("missing_rs")
            result.actionability = "not_valid"
            result.actionability_reason = "missing_rs"
            return result

        result.raw_features["distance_to_52w_high_pct"] = round(dist_high, 2)
        result.raw_features["rs_score_3m"] = rs_3m
        result.raw_features["rs_score_6m"] = rs_6m
        result.raw_features["vcp_score"] = round(vcp, 1)

        # Extension above SMA50 (vertical run risk)
        ext_above_sma50: float | None = None
        if sma50 is not None and sma50 > 0:
            ext_above_sma50 = (close - sma50) / sma50 * 100.0
            result.raw_features["extension_above_sma50_pct"] = round(ext_above_sma50, 2)

        # Sub-state by extension
        if ext_above_sma50 is not None and ext_above_sma50 > self.cfg.max_extension_above_sma50_pct:
            result.actionability = "excluded"
            result.actionability_reason = f"extension_above_sma50_{ext_above_sma50:.0f}pct"
            result.failed_conditions.append("extension_too_high")
            result.sub_state = "vertical_run"
            result.warning_flags.append(f"extended_{ext_above_sma50:.1f}%_above_sma50")
            result.disqualifiers.append("vertical_extension")
            return result

        cls_inp = ClassificationInput(
            setup_name=self.name,
            triggered=True,
            extension_pct=ext_above_sma50,
            has_clear_pivot=True,
            volume_dryup_pct=None,
            disqualifiers=[],
            pocket_pivot=bool(f.get("pocket_pivot")),
        )
        bucket, reason = classify(cls_inp)
        result.actionability = bucket
        result.actionability_reason = reason
        # Same trader logic: only entry-worthy buckets count as triggered.
        result.triggered = bucket in ("actionable_now", "near_actionable", "forming", "watch")

        if ext_above_sma50 is not None:
            if ext_above_sma50 > 15.0:
                result.sub_state = "watch_extended"
            elif ext_above_sma50 > 5.0:
                result.sub_state = "in_trend"
            else:
                result.sub_state = "near_pivot"
        else:
            result.sub_state = "in_trend"

        # Score
        near_high_score = max(0.0, 100.0 - abs(dist_high) * 8.0)
        rs_score = max(0.0, min(100.0, 50.0 + rs * 1.5))
        consolidation_score = vcp
        score = 0.4 * near_high_score + 0.4 * rs_score + 0.2 * consolidation_score
        if ext_above_sma50 is not None and ext_above_sma50 > 15.0:
            score *= 0.85
        if bucket == "watch":
            score *= 0.9
        # Canonical leading-RS signal: RS line at its own 52w high boosts score.
        # +15 reflects this is THE Stage-2 confirmation signal (O'Neil + Weinstein).
        rs_line_at_52w = f.get("rs_line_at_52w_high") is True
        if rs_line_at_52w:
            score += 15.0
            result.reasons.append("RS_line_at_52w_high")
            result.raw_features["rs_line_at_52w_high"] = True
        result.score = round(min(100.0, max(0.0, score)), 1)

        # Trigger / invalidation
        high_52w = f.get("high_52w")
        if high_52w:
            result.trigger_level = round(float(high_52w), 2)
        if sma50:
            result.invalidation_level = round(float(sma50) * 0.97, 2)
        result.reasons.append(f"near_52w_high({dist_high:.1f}%)")
        result.reasons.append(f"rs_strength({rs:.1f})")
        if vcp > 50:
            result.reasons.append(f"contraction(vcp={vcp:.0f})")
        return result
