"""Tight-breakout detector with separately replayable extraction and rules."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from stock_scout.config.schema import TightBreakoutSetupConfig
from stock_scout.data.corporate_actions import detect_m_and_a_from_price
from stock_scout.indicators.patterns import find_pivot_high
from stock_scout.setups.actionability import ClassificationInput, classify
from stock_scout.setups.base import SetupDetector, SetupResult
from stock_scout.setups.base_analysis import atr20_at_last_bar, extension_from_pivot


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def extract_tight_breakout_features(
    df_daily: pd.DataFrame,
    features: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract all point-in-time inputs needed by tight-breakout rules.

    This is intentionally config-free. The resulting plain dictionary is the
    replay contract stored in an opportunity cache: changing a threshold later
    must not require indicators, pivots, or an M&A inference to be recomputed.
    """
    source = features or {}
    out: dict[str, Any] = {
        "history_bars": len(df_daily) if df_daily is not None else 0,
        "close": source.get("close"),
        "atr10": source.get("atr10"),
        "atr50": source.get("atr50"),
        "ema10": source.get("ema10"),
        "ema20": source.get("ema20"),
        "sma50": source.get("sma50"),
        "volume_dryup_score": _number(source.get("volume_dryup_score")) or 0.0,
        "clv5": source.get("clv5"),
        "avg_dollar_volume_50d": _number(source.get("avg_dollar_volume_50d")) or 0.0,
        "pocket_pivot": bool(source.get("pocket_pivot")),
        "higher_lows": int(source.get("higher_lows") or 0),
        "m_and_a_confidence": None,
        "m_and_a_signals": [],
        "pivot_level": None,
        "pivot_price": None,
        "pivot_bars_ago": None,
        "distance_to_pivot_pct": None,
        "distance_to_pivot_pct_exact": None,
        "atr10_over_atr50": None,
        "above_key_mas": None,
        "atr20": None,
        "extension_pct": None,
        "extension_pct_exact": None,
        "extension_atr_multiples": None,
        "extension_atr_multiples_exact": None,
        "bars_since_cross": None,
    }
    if df_daily is None or df_daily.empty or len(df_daily) < 80:
        return out

    ma = detect_m_and_a_from_price(df_daily, ticker=str(source.get("ticker") or "tight_breakout"))
    out["m_and_a_confidence"] = ma.confidence
    out["m_and_a_signals"] = list(ma.price_signals)

    pivot = find_pivot_high(df_daily["close"], lookback=40, left_bars=5, right_bars=5)
    if pivot is None:
        return out
    out["pivot_level"] = round(float(pivot.price), 2)
    out["pivot_price"] = float(pivot.price)
    out["pivot_bars_ago"] = pivot.bars_ago

    close = _number(out["close"])
    if close is None:
        return out
    pivot_price = float(pivot.price)
    distance_pct = (close - pivot_price) / pivot_price * 100.0
    out["distance_to_pivot_pct"] = round(distance_pct, 2)
    out["distance_to_pivot_pct_exact"] = distance_pct

    atr10, atr50 = _number(out["atr10"]), _number(out["atr50"])
    if atr10 is not None and atr50 not in (None, 0.0):
        out["atr10_over_atr50"] = round(atr10 / atr50, 3)

    ema10, ema20, sma50 = (_number(out["ema10"]), _number(out["ema20"]), _number(out["sma50"]))
    if ema10 is not None and ema20 is not None and sma50 is not None:
        out["above_key_mas"] = bool(close > ema10 and close > ema20 and close > sma50)

    atr20 = atr20_at_last_bar(df_daily)
    out["atr20"] = atr20
    extension_pct, extension_atr = extension_from_pivot(close, pivot_price, atr20)
    out["extension_pct"] = round(extension_pct, 2)
    out["extension_pct_exact"] = extension_pct
    out["extension_atr_multiples"] = round(extension_atr, 2) if extension_atr is not None else None
    out["extension_atr_multiples_exact"] = extension_atr
    if extension_pct > 0:
        above = df_daily["close"] > pivot.price
        bars_since_cross = 0
        for value in above.iloc[::-1]:
            if not value:
                break
            bars_since_cross += 1
        out["bars_since_cross"] = bars_since_cross
    return out


def tight_breakout_levels(extracted: Mapping[str, Any]) -> tuple[float, float] | None:
    """Return trigger and invalidation levels from cached structural inputs."""
    pivot = _number(extracted.get("pivot_price"))
    if pivot is None:
        pivot = _number(extracted.get("pivot_level"))
    atr10 = _number(extracted.get("atr10"))
    sma50 = _number(extracted.get("sma50"))
    if pivot is None or atr10 is None or sma50 is None:
        return None
    return round(pivot, 2), round(max(pivot - 1.5 * atr10, sma50 * 0.97), 2)


def classify_tight_breakout_features(
    extracted: Mapping[str, Any],
    cfg: TightBreakoutSetupConfig,
) -> SetupResult:
    """Apply current tight-breakout rules to previously extracted features."""
    raw = dict(extracted)
    result = SetupResult(setup_name=TightBreakoutDetector.name, triggered=False, raw_features=raw)
    if not cfg.enabled:
        result.actionability = "not_valid"
        result.actionability_reason = "setup_disabled"
        return result
    if int(raw.get("history_bars") or 0) < 80:
        result.failed_conditions.append("insufficient_history")
        result.actionability = "not_valid"
        result.actionability_reason = "insufficient_history"
        return result

    if raw.get("m_and_a_confidence") == "high":
        result.disqualifiers.append("m_and_a_high")
        result.failed_conditions.append("m_and_a_high_confidence")
        result.actionability = "excluded"
        result.actionability_reason = "m_and_a_high_confidence"
        return result
    if raw.get("m_and_a_confidence") == "medium":
        result.warning_flags.append("m_and_a_medium")

    pivot = _number(raw.get("pivot_price"))
    if pivot is None:
        pivot = _number(raw.get("pivot_level"))
    if pivot is None:
        result.failed_conditions.append("no_recent_pivot")
        result.actionability = "excluded"
        result.actionability_reason = "no_clear_pivot"
        return result
    close = _number(raw.get("close"))
    if close is None:
        result.actionability = "not_valid"
        result.actionability_reason = "missing_close"
        return result

    distance_pct = _number(raw.get("distance_to_pivot_pct_exact"))
    if distance_pct is None:
        distance_pct = (close - pivot) / pivot * 100.0
    if distance_pct < -cfg.max_distance_below_pivot_pct:
        result.failed_conditions.append(f"too_far_below_pivot({distance_pct:.1f}%)")
        result.actionability = "not_valid"
        result.actionability_reason = "too_far_below_pivot"
        return result

    atr10, atr50 = _number(raw.get("atr10")), _number(raw.get("atr50"))
    if atr10 is None or atr50 in (None, 0.0):
        result.failed_conditions.append("missing_atr")
        result.actionability = "not_valid"
        result.actionability_reason = "missing_atr"
        return result
    atr_ratio = atr10 / atr50
    if atr_ratio >= cfg.atr_contraction_ratio:
        result.failed_conditions.append(f"no_atr_contraction({atr_ratio:.2f})")
        result.actionability = "not_valid"
        result.actionability_reason = "no_atr_contraction"
        return result

    avg_dvol = _number(raw.get("avg_dollar_volume_50d")) or 0.0
    if avg_dvol < 5_000_000 and atr_ratio < 0.4:
        result.disqualifiers.append("illiquid_tight")
        result.failed_conditions.append("illiquid_tight_suspect")
        result.actionability = "excluded"
        result.actionability_reason = "illiquid_tight"
        return result

    ema10, ema20, sma50 = (_number(raw.get("ema10")), _number(raw.get("ema20")), _number(raw.get("sma50")))
    if ema10 is None or ema20 is None or sma50 is None:
        result.failed_conditions.append("missing_ma")
        result.actionability = "not_valid"
        result.actionability_reason = "missing_ma"
        return result
    if not (close > ema10 and close > ema20 and close > sma50):
        result.failed_conditions.append("not_above_key_mas")
        result.actionability = "not_valid"
        result.actionability_reason = "not_above_key_mas"
        return result

    vol_dryup = _number(raw.get("volume_dryup_score")) or 0.0
    if vol_dryup < 40.0:
        result.failed_conditions.append(f"no_volume_dryup(score={vol_dryup:.0f})")
        result.actionability = "not_valid"
        result.actionability_reason = "no_volume_dryup"
        return result

    clv5 = _number(raw.get("clv5"))
    clv_below_threshold = clv5 is not None and clv5 < 0.5
    if clv_below_threshold:
        result.warning_flags.append(f"lower_half_of_base(clv5={clv5:.2f})")

    extension_pct = _number(raw.get("extension_pct_exact"))
    if extension_pct is None:
        extension_pct = (close - pivot) / pivot * 100.0
    extension_atr = _number(raw.get("extension_atr_multiples_exact"))
    if extension_atr is None:
        _ignored_extension, extension_atr = extension_from_pivot(
            close, pivot, _number(raw.get("atr20"))
        )
    bars_since_cross = raw.get("bars_since_cross")
    if bars_since_cross is not None:
        bars_since_cross = int(bars_since_cross)
    bucket, reason = classify(ClassificationInput(
        setup_name=TightBreakoutDetector.name,
        triggered=True,
        extension_pct=extension_pct,
        extension_atr_multiples=extension_atr,
        bars_since_breakout=bars_since_cross,
        has_clear_pivot=True,
        volume_dryup_pct=vol_dryup,
        disqualifiers=[],
        pocket_pivot=bool(raw.get("pocket_pivot")),
        higher_lows=int(raw.get("higher_lows") or 0),
    ))
    if clv_below_threshold and bucket == "actionable_now":
        bucket = "watch"
        reason = f"clv5_below_0.5({clv5:.2f})"
    result.actionability = bucket
    result.actionability_reason = reason
    result.triggered = bucket in ("actionable_now", "near_actionable", "forming", "watch")

    if extension_pct > 5.0:
        result.sub_state = "extended"
    elif extension_pct > 2.0:
        result.sub_state = "early_breakout"
    elif extension_pct > 0.5:
        result.sub_state = "breakout_day"
    elif extension_pct > -1.0:
        result.sub_state = "at_pivot"
    else:
        result.sub_state = "anticipation"

    levels = tight_breakout_levels(raw)
    assert levels is not None  # pivot, ATR10 and SMA50 passed the gates above
    result.trigger_level, result.invalidation_level = levels
    tightness = (1.0 - atr_ratio) * 100.0
    anticipation = max(0.0, 100.0 - abs(extension_pct) * 10.0)
    if bucket == "actionable_now":
        score = 0.4 * tightness + 0.4 * vol_dryup + 0.2 * anticipation
    elif bucket == "near_actionable":
        score = 0.35 * tightness + 0.35 * vol_dryup + 0.15 * anticipation + 10.0
    elif bucket == "forming":
        score = 0.3 * tightness + 0.3 * vol_dryup + 0.1 * anticipation + 5.0
    elif bucket == "watch":
        score = 0.25 * tightness + 0.25 * vol_dryup
    elif bucket == "extended_too_late":
        score = min(25.0, 0.2 * tightness)
    else:
        score = 25.0
    result.score = round(min(100.0, max(0.0, score)), 1)
    result.raw_features["tightness_score"] = round(tightness, 1)
    result.raw_features["anticipation_score"] = round(anticipation, 1)
    result.base_metrics = {
        "pivot_price": pivot,
        "extension_pct": round(extension_pct, 2),
        "extension_atr_multiples": round(extension_atr, 2) if extension_atr is not None else None,
        "bars_since_cross": bars_since_cross,
        "atr_ratio": round(atr_ratio, 3),
        "volume_dryup_score": round(vol_dryup, 1),
    }
    result.reasons.extend((
        f"contraction(ATR10/50={atr_ratio:.2f})",
        f"volume_dryup({vol_dryup:.0f})",
        f"{extension_pct:+.1f}%_to_pivot",
    ))
    return result


class TightBreakoutDetector(SetupDetector):
    """Tight / anticipation breakout detector using replayable inputs."""

    name = "tight_breakout"

    def __init__(self, cfg: TightBreakoutSetupConfig):
        self.cfg = cfg

    def extract_features(
        self,
        df_daily: pd.DataFrame,
        features: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return extract_tight_breakout_features(df_daily, features)

    def classify_features(self, extracted: Mapping[str, Any]) -> SetupResult:
        return classify_tight_breakout_features(extracted, self.cfg)

    def detect(
        self,
        df_daily: pd.DataFrame,
        df_weekly: pd.DataFrame | None = None,
        features: dict | None = None,
    ) -> SetupResult:
        del df_weekly
        return self.classify_features(self.extract_features(df_daily, features))
