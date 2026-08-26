from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

import pandas as pd

from stock_scout.data.corporate_actions import detect_m_and_a_from_price
from stock_scout.indicators.moving_averages import ema, sma
from stock_scout.research.ma_cluster_preferred import (
    build_ma_cluster_research_profile,
    choose_ma_cluster_research_profile,
)
from stock_scout.setups.base import SetupDetector, SetupResult
from stock_scout.setups.weekly_structure import (
    WeeklyStructuralStop,
    derive_weekly_structural_stop,
)

MA_COLUMNS = ("ema10", "ema20", "sma50", "sma150", "sma200")
WEEKLY_THRUST_MA_COLUMNS = ("ema10", "ema20", "sma30", "sma40", "sma50")

# This is a scan annotation, not a scored setup.  The threshold ladder is
# deliberately broad: it tells the operator how close a name is to the visual
# "tight bundle + demand thrust" pattern without pretending the cut-offs are a
# measured entry edge.
THRUST_TIERS = (
    (1, 6.0, 2.0, 4, 0.65),
    (2, 8.0, 1.5, 3, 0.55),
    (3, 10.0, 1.25, 3, 0.50),
)


@dataclass(frozen=True)
class MAClusterVolumeBreakoutConfig:
    """Defaults for the daily MA-cluster volume-breakout detector.

    The constructor also accepts any object exposing fields with these names.
    That keeps the detector ready for a future Pydantic settings block without
    coupling the first implementation to a config-schema migration.
    """

    enabled: bool = True
    very_tight_cluster_width_pct: float = 6.0
    tight_cluster_width_pct: float = 8.0
    max_watch_cluster_width_pct: float = 12.0
    min_mas_crossed: int = 4
    breakout_buffer_pct: float = 0.25
    prior_close_tolerance_pct: float = 0.75
    min_breakout_rel_volume: float = 1.5
    strong_breakout_rel_volume: float = 2.0
    min_watch_rel_volume: float = 1.2
    min_close_location: float = 0.65
    min_watch_close_location: float = 0.50
    max_breakout_age_bars: int = 3
    near_cluster_top_pct: float = 2.0
    max_extension_above_cluster_pct: float = 7.0
    sma200_slope_lookback_days: int = 20
    min_sma200_slope_pct: float = -1.0
    weekly_30w_slope_lookback_weeks: int = 5
    min_weekly_30w_slope_pct: float = -0.75
    weekly_structure_lookback_weeks: int = 20
    weekly_base_lookback_weeks: int = 8
    weekly_pivot_left_weeks: int = 2
    weekly_pivot_right_weeks: int = 1
    weekly_min_support_gap_pct: float = 1.5
    weekly_stop_buffer_pct: float = 0.5
    weekly_stop_atr_fraction: float = 0.15
    max_actionable_stop_distance_pct: float = 8.0
    max_watch_stop_distance_pct: float = 12.0
    min_trigger_score: float = 35.0


def _coerce_config(cfg: Any | None) -> MAClusterVolumeBreakoutConfig:
    default = MAClusterVolumeBreakoutConfig()
    if cfg is None:
        return default
    values = {
        item.name: getattr(cfg, item.name, getattr(default, item.name))
        for item in fields(MAClusterVolumeBreakoutConfig)
    }
    return MAClusterVolumeBreakoutConfig(**values)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _weekly_from_daily(df_daily: pd.DataFrame) -> pd.DataFrame:
    return (
        df_daily.resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["close"])
    )


def _ma_frame(close: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ema10": ema(close, 10),
            "ema20": ema(close, 20),
            "sma50": sma(close, 50),
            "sma150": sma(close, 150),
            "sma200": sma(close, 200),
        },
        index=close.index,
    )


def _weekly_thrust_ma_frame(close: pd.Series) -> pd.DataFrame:
    """Use one compact, one-year weekly bundle for the weekly annotation.

    Daily SMA150/200 have no useful weekly analogue for a fresh trend; the
    10/20 EMA and 30/40/50 SMA bundle keeps the same short-to-intermediate
    intent while still being available for stocks with a year of history.
    """
    return pd.DataFrame(
        {
            "ema10": ema(close, 10),
            "ema20": ema(close, 20),
            "sma30": sma(close, 30),
            "sma40": sma(close, 40),
            "sma50": sma(close, 50),
        },
        index=close.index,
    )


def _cluster(values: list[float]) -> tuple[float, float, float]:
    top = max(values)
    bottom = min(values)
    midpoint = sum(values) / len(values)
    width_pct = (top - bottom) / midpoint * 100.0 if midpoint > 0 else 999.0
    return top, bottom, width_pct


def _close_location(row: pd.Series) -> float:
    span = float(row["high"]) - float(row["low"])
    if span <= 0:
        return 0.5
    return _clamp((float(row["close"]) - float(row["low"])) / span, 0.0, 1.0)


def _relative_volume(volume: pd.Series, pos: int, window: int = 20) -> tuple[float | None, float | None]:
    start = max(0, pos - window)
    prior = volume.iloc[start:pos].dropna()
    if len(prior) < 10:
        return None, None
    avg = float(prior.mean())
    if avg <= 0:
        return None, None
    return float(volume.iloc[pos]) / avg, avg


def _series_slope_pct(series: pd.Series, pos: int, lookback: int) -> float | None:
    prior_pos = pos - lookback
    if prior_pos < 0 or pos >= len(series):
        return None
    current = series.iloc[pos]
    prior = series.iloc[prior_pos]
    if pd.isna(current) or pd.isna(prior) or float(prior) <= 0:
        return None
    return (float(current) - float(prior)) / float(prior) * 100.0


def _ma_cluster_thrust_assessment(
    frame: pd.DataFrame,
    *,
    timeframe: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe today's daily or weekly MA-volume thrust without scoring it.

    A result is retained even when it misses every tier so Telegram can show
    the nearest names rather than silently returning an empty section.
    """
    volume_window = 20 if timeframe == "daily" else 10
    min_bars = max(51, volume_window + 1)
    if frame is None or len(frame) < min_bars:
        return {"timeframe": timeframe, "available": False, "reason": "insufficient_history"}

    clean = frame.dropna(subset=["open", "high", "low", "close", "volume"]).copy()
    if len(clean) < min_bars:
        return {"timeframe": timeframe, "available": False, "reason": "insufficient_clean_history"}

    close = clean["close"].astype(float)
    ma = _ma_frame(close) if timeframe == "daily" else _weekly_thrust_ma_frame(close)
    current = ma.iloc[-1]
    prior = ma.iloc[-2]
    columns = MA_COLUMNS if timeframe == "daily" else WEEKLY_THRUST_MA_COLUMNS
    if current[list(columns)].isna().any() or prior[list(columns)].isna().any():
        return {"timeframe": timeframe, "available": False, "reason": "insufficient_ma_history"}

    values = [float(current[column]) for column in columns]
    top, _bottom, width_pct = _cluster(values)
    current_close = float(close.iloc[-1])
    prior_close = float(close.iloc[-2])
    crossed = sum(
        1
        for column in columns
        if prior_close <= float(prior[column]) * 1.0075
        and current_close > float(current[column]) * 1.0025
    )
    rel_volume, _ = _relative_volume(clean["volume"].astype(float), len(clean) - 1, volume_window)
    close_location = _close_location(clean.iloc[-1])
    extension_pct = (current_close - top) / top * 100.0 if top > 0 else None
    above_bundle = bool(current_close > top * 1.0025)
    # A very extended bar is not the tight-bundle entry the operator asked for.
    within_extension = extension_pct is not None and extension_pct <= 7.0

    tier: int | None = None
    for candidate_tier, max_width, min_rvol, min_crossed, min_clv in THRUST_TIERS:
        if (
            width_pct <= max_width
            and rel_volume is not None
            and rel_volume >= min_rvol
            and crossed >= min_crossed
            and close_location >= min_clv
            and above_bundle
            and within_extension
        ):
            tier = candidate_tier
            break

    # This is only a transparent ordering aid when no tier fires.  It is not
    # persisted as a setup score and never feeds ranking or position sizing.
    nearest_score = 100.0 * (
        0.35 * max(0.0, 1.0 - width_pct / 10.0)
        + 0.25 * min(1.0, float(rel_volume or 0.0) / 2.0)
        + 0.20 * min(1.0, crossed / len(columns))
        + 0.10 * close_location
        + 0.10 * float(above_bundle and within_extension)
    )
    assessment = {
        "timeframe": timeframe,
        "available": True,
        "tier": tier,
        "ma_width_pct": round(width_pct, 2),
        "bundle_top": round(top, 4),
        "bundle_bottom": round(_bottom, 4),
        # Research-only static exit: one percent below the bundle's low.  It
        # is persisted so the point-in-time replay can price stop-outs, not so
        # the application can use it as a production tactical stop.
        "research_stop_level": round(_bottom * 0.99, 4),
        "relative_volume": round(rel_volume, 2) if rel_volume is not None else None,
        "mas_crossed": crossed,
        "mas_total": len(columns),
        "close_location": round(close_location, 2),
        "extension_above_bundle_pct": round(extension_pct, 2) if extension_pct is not None else None,
        "above_bundle": above_bundle,
        "within_entry_extension": within_extension,
        "nearest_score": round(nearest_score, 1),
    }
    assessment["research_profile"] = build_ma_cluster_research_profile(
        clean,
        timeframe=timeframe,
        assessment=assessment,
        context=context,
    )
    return assessment


def _rs_quality(features: dict) -> tuple[float, bool]:
    weighted = features.get("rs_score_weighted")
    rs3 = features.get("rs_score_3m")
    rs6 = features.get("rs_score_6m")
    raw = weighted if weighted is not None else (rs3 if rs3 is not None else rs6)
    score = 50.0 if raw is None else _clamp((float(raw) + 10.0) / 30.0 * 100.0)
    improving = bool(
        features.get("rs_line_at_52w_high") is True
        or features.get("rs_line_at_50d_high") is True
        or (
            rs3 is not None
            and rs6 is not None
            and float(rs3) > float(rs6)
            and float(rs3) > 0
        )
    )
    return score, improving


class MAClusterVolumeBreakoutDetector(SetupDetector):
    """Daily EMA10/20 + SMA50/150/200 compression and volume ignition.

    A valid breakout must launch through a MA cluster that was tight on the bar
    immediately before the thrust. Relative volume deliberately excludes the
    breakout bar from its 20-day denominator. Tight clusters without a breakout
    remain visible as pre-breakout watch/near-actionable candidates.
    """

    name = "ma_cluster_volume_breakout"

    def __init__(self, cfg: Any | None = None):
        self.cfg = _coerce_config(cfg)

    def detect(
        self,
        df_daily: pd.DataFrame,
        df_weekly: pd.DataFrame | None = None,
        features: dict | None = None,
    ) -> SetupResult:
        result = SetupResult(setup_name=self.name, triggered=False)
        if not self.cfg.enabled:
            result.actionability_reason = "setup_disabled"
            return result
        if df_daily is None or df_daily.empty or len(df_daily) < 220:
            result.failed_conditions.append("insufficient_daily_history")
            result.actionability_reason = "insufficient_daily_history"
            return result

        work = df_daily.dropna(subset=["open", "high", "low", "close", "volume"]).copy()
        if len(work) < 220:
            result.failed_conditions.append("insufficient_clean_daily_history")
            result.actionability_reason = "insufficient_clean_daily_history"
            return result

        f = features or {}
        weekly = df_weekly.copy() if df_weekly is not None and not df_weekly.empty else _weekly_from_daily(work)
        weekly = weekly.dropna(subset=["close"])
        weekly_sma30 = sma(weekly["close"].astype(float), 30) if len(weekly) >= 35 else pd.Series(dtype=float)
        weekly_30w_slope_pct = (
            _series_slope_pct(
                weekly_sma30,
                len(weekly_sma30) - 1,
                self.cfg.weekly_30w_slope_lookback_weeks,
            )
            if not weekly_sma30.empty
            else None
        )
        research_context = dict(f)
        research_context["weekly_30w_slope_pct"] = weekly_30w_slope_pct
        thrust_assessments = {
            "daily": _ma_cluster_thrust_assessment(
                work, timeframe="daily", context=research_context
            ),
            "weekly": _ma_cluster_thrust_assessment(
                weekly, timeframe="weekly", context=research_context
            ),
        }

        ma_event = detect_m_and_a_from_price(work, ticker=str(f.get("ticker") or self.name))
        if ma_event.confidence == "high":
            result.disqualifiers.append("m_and_a_high")
            result.failed_conditions.append("m_and_a_high_confidence")
            result.actionability = "excluded"
            result.actionability_reason = "m_and_a_high_confidence"
            self._attach_thrust_assessments(result, thrust_assessments)
            return result
        if ma_event.confidence == "medium":
            result.warning_flags.append("m_and_a_medium")

        close = work["close"].astype(float)
        ma = _ma_frame(close)
        rs_score, rs_improving = _rs_quality(f)

        breakout: dict[str, Any] | None = None
        max_age = min(self.cfg.max_breakout_age_bars, len(work) - 202)
        for age in range(max_age + 1):
            bar_pos = len(work) - 1 - age
            context_pos = bar_pos - 1
            context_mas = ma.iloc[context_pos]
            if context_mas[list(MA_COLUMNS)].isna().any():
                continue

            values = [float(context_mas[col]) for col in MA_COLUMNS]
            cluster_top, cluster_bottom, cluster_width_pct = _cluster(values)
            if cluster_width_pct > self.cfg.max_watch_cluster_width_pct:
                continue

            prior_close = float(close.iloc[context_pos])
            row = work.iloc[bar_pos]
            breakout_close = float(row["close"])
            breakout_open = float(row["open"])
            breakout_low = float(row["low"])
            breakout_high = float(row["high"])
            buffer = 1.0 + self.cfg.breakout_buffer_pct / 100.0
            prior_tolerance = 1.0 + self.cfg.prior_close_tolerance_pct / 100.0
            crossed = sum(
                1
                for value in values
                if prior_close <= value * prior_tolerance and breakout_close > value * buffer
            )
            crossed_cluster = (
                crossed >= self.cfg.min_mas_crossed
                and prior_close <= cluster_top * prior_tolerance
                and min(breakout_open, breakout_low) <= cluster_top * prior_tolerance
                and breakout_close > cluster_top * buffer
            )
            if not crossed_cluster:
                continue

            rel_volume, avg_volume = _relative_volume(work["volume"].astype(float), bar_pos)
            clv = _close_location(row)
            held_above = bool(
                age == 0
                or (close.iloc[bar_pos + 1 :] >= cluster_top * 0.995).all()
            )
            breakout = {
                "age": age,
                "bar_pos": bar_pos,
                "context_pos": context_pos,
                "row": row,
                "cluster_top": cluster_top,
                "cluster_bottom": cluster_bottom,
                "cluster_width_pct": cluster_width_pct,
                "ma_values": dict(zip(MA_COLUMNS, values, strict=False)),
                "crossed": crossed,
                "rel_volume": rel_volume,
                "avg_volume": avg_volume,
                "clv": clv,
                "held_above": held_above,
                "up_day": breakout_close > breakout_open,
                "breakout_low": breakout_low,
                "breakout_high": breakout_high,
                "breakout_close": breakout_close,
            }
            break

        if breakout is None:
            return self._pre_breakout_result(
                result=result,
                work=work,
                ma=ma,
                weekly=weekly,
                weekly_30w_slope_pct=weekly_30w_slope_pct,
                rs_score=rs_score,
                rs_improving=rs_improving,
                thrust_assessments=thrust_assessments,
            )

        context_pos = int(breakout["context_pos"])
        sma200_slope_pct = _series_slope_pct(
            ma["sma200"], context_pos, self.cfg.sma200_slope_lookback_days
        )
        daily_trend_ok = (
            sma200_slope_pct is not None
            and sma200_slope_pct >= self.cfg.min_sma200_slope_pct
        )
        weekly_trend_ok = (
            weekly_30w_slope_pct is None
            or weekly_30w_slope_pct >= self.cfg.min_weekly_30w_slope_pct
        )
        trend_ok = daily_trend_ok and weekly_trend_ok

        current_close = float(close.iloc[-1])
        cluster_top = float(breakout["cluster_top"])
        cluster_bottom = float(breakout["cluster_bottom"])
        extension_pct = (current_close - cluster_top) / cluster_top * 100.0
        entry_trigger = float(breakout["breakout_high"]) * 1.001
        daily_tactical_stop = max(
            0.01, min(cluster_bottom, float(breakout["breakout_low"])) * 0.99
        )
        weekly_stop = derive_weekly_structural_stop(
            weekly,
            reference_price=entry_trigger,
            as_of=work.index[-1],
            cutoff_date=breakout["row"].name,
            structure_lookback_weeks=self.cfg.weekly_structure_lookback_weeks,
            base_lookback_weeks=self.cfg.weekly_base_lookback_weeks,
            pivot_left_weeks=self.cfg.weekly_pivot_left_weeks,
            pivot_right_weeks=self.cfg.weekly_pivot_right_weeks,
            min_support_gap_pct=self.cfg.weekly_min_support_gap_pct,
            stop_buffer_pct=self.cfg.weekly_stop_buffer_pct,
            stop_atr_fraction=self.cfg.weekly_stop_atr_fraction,
        )
        invalidation = (
            weekly_stop.stop_level if weekly_stop is not None else daily_tactical_stop
        )
        stop_distance_pct = (entry_trigger - invalidation) / entry_trigger * 100.0
        rel_volume = breakout["rel_volume"]
        clv = float(breakout["clv"])
        age = int(breakout["age"])

        tightness_score = self._tightness_score(float(breakout["cluster_width_pct"]))
        volume_score = (
            _clamp(
                (float(rel_volume) - 1.0)
                / max(0.01, self.cfg.strong_breakout_rel_volume - 1.0)
                * 100.0
            )
            if rel_volume is not None
            else 0.0
        )
        candle_score = _clamp(
            45.0 * int(breakout["crossed"]) / len(MA_COLUMNS)
            + 40.0 * clv
            + (15.0 if breakout["up_day"] else 0.0)
        )
        trend_score = 0.0
        if daily_trend_ok:
            trend_score += 60.0
        if weekly_trend_ok:
            trend_score += 30.0
        if sma200_slope_pct is not None and sma200_slope_pct >= 0:
            trend_score += 10.0
        trend_score = _clamp(trend_score)
        risk_score = self._risk_score(stop_distance_pct)
        score = (
            0.25 * tightness_score
            + 0.25 * volume_score
            + 0.20 * candle_score
            + 0.15 * trend_score
            + 0.10 * rs_score
            + 0.05 * risk_score
        )

        extended = extension_pct > self.cfg.max_extension_above_cluster_pct
        strong_breakout = (
            float(breakout["cluster_width_pct"]) <= self.cfg.tight_cluster_width_pct
            and rel_volume is not None
            and float(rel_volume) >= self.cfg.min_breakout_rel_volume
            and clv >= self.cfg.min_close_location
            and int(breakout["crossed"]) >= self.cfg.min_mas_crossed
            and bool(breakout["held_above"])
            and trend_ok
            and stop_distance_pct <= self.cfg.max_actionable_stop_distance_pct
        )
        acceptable_breakout = (
            rel_volume is not None
            and float(rel_volume) >= self.cfg.min_watch_rel_volume
            and clv >= self.cfg.min_watch_close_location
            and bool(breakout["held_above"])
            and trend_ok
            and stop_distance_pct <= self.cfg.max_watch_stop_distance_pct
        )

        if extended:
            phase = "extended"
            bucket = "extended_too_late"
            reason = f"ma_cluster_extended_{extension_pct:.1f}%"
            triggered = False
        elif strong_breakout:
            phase = "one_day_thrust" if age == 0 else "follow_through"
            bucket = "actionable_now"
            reason = "tight_ma_cluster_high_volume_breakout"
            triggered = score >= self.cfg.min_trigger_score
        elif acceptable_breakout:
            phase = "one_day_thrust" if age == 0 else "follow_through"
            bucket = "near_actionable"
            reason = "ma_cluster_breakout_needs_stronger_confirmation"
            triggered = score >= self.cfg.min_trigger_score
        else:
            phase = "weak_breakout"
            bucket = "watch"
            reason = "ma_cluster_breakout_weak_volume_or_structure"
            triggered = score >= self.cfg.min_trigger_score

        result.triggered = triggered
        result.sub_state = phase
        result.actionability = bucket if triggered or extended else "not_valid"
        result.actionability_reason = reason if triggered or extended else "ma_cluster_score_below_threshold"
        result.score = round(_clamp(score), 1)
        result.trigger_level = round(entry_trigger, 2)
        result.invalidation_level = round(invalidation, 2)
        result.raw_features = self._raw_features(
            breakout=breakout,
            current_close=current_close,
            extension_pct=extension_pct,
            stop_distance_pct=stop_distance_pct,
            sma200_slope_pct=sma200_slope_pct,
            weekly_30w_slope_pct=weekly_30w_slope_pct,
            rs_improving=rs_improving,
            phase=phase,
            weekly_stop=weekly_stop,
            daily_tactical_stop=daily_tactical_stop,
        )
        self._attach_thrust_assessments(result, thrust_assessments)
        result.base_metrics = {
            "ma_cluster_top": cluster_top,
            "ma_cluster_bottom": cluster_bottom,
            "ma_cluster_width_pct": float(breakout["cluster_width_pct"]),
            "breakout_age_bars": age,
            "stop_distance_pct": stop_distance_pct,
        }
        result.reasons.extend(
            [
                f"ma_width={float(breakout['cluster_width_pct']):.2f}%",
                f"mas_crossed={int(breakout['crossed'])}/{len(MA_COLUMNS)}",
                f"rvol20={float(rel_volume):.2f}x" if rel_volume is not None else "rvol20=n/a",
                f"clv={clv:.2f}",
                f"phase={phase}",
            ]
        )
        if not trend_ok:
            result.warning_flags.append("ma_cluster_long_term_trend_weak")
        if rel_volume is None or float(rel_volume) < self.cfg.min_breakout_rel_volume:
            result.warning_flags.append("ma_cluster_volume_below_actionable")
        if clv < self.cfg.min_close_location:
            result.warning_flags.append("ma_cluster_close_not_high_in_range")
        if stop_distance_pct > self.cfg.max_actionable_stop_distance_pct:
            result.warning_flags.append("ma_cluster_structural_stop_wide")
        if weekly_stop is None:
            result.warning_flags.append("ma_cluster_weekly_structure_unavailable")
        if not breakout["held_above"]:
            result.warning_flags.append("ma_cluster_failed_follow_through")
        return result

    def _pre_breakout_result(
        self,
        result: SetupResult,
        work: pd.DataFrame,
        ma: pd.DataFrame,
        weekly: pd.DataFrame,
        weekly_30w_slope_pct: float | None,
        rs_score: float,
        rs_improving: bool,
        thrust_assessments: dict[str, dict[str, Any]],
    ) -> SetupResult:
        current = ma.iloc[-1]
        if current[list(MA_COLUMNS)].isna().any():
            result.failed_conditions.append("insufficient_ma_history")
            result.actionability_reason = "insufficient_ma_history"
            self._attach_thrust_assessments(result, thrust_assessments)
            return result

        values = [float(current[col]) for col in MA_COLUMNS]
        cluster_top, cluster_bottom, cluster_width_pct = _cluster(values)
        current_close = float(work["close"].iloc[-1])
        distance_to_top_pct = (cluster_top - current_close) / cluster_top * 100.0
        inside_or_near = (
            current_close >= cluster_bottom * 0.98
            and current_close <= cluster_top * (1.0 + self.cfg.near_cluster_top_pct / 100.0)
        )
        if cluster_width_pct > self.cfg.max_watch_cluster_width_pct or not inside_or_near:
            result.failed_conditions.append("no_tight_ma_cluster_or_recent_breakout")
            result.actionability_reason = "no_tight_ma_cluster_or_recent_breakout"
            result.raw_features = {
                "ma_cluster_width_pct": round(cluster_width_pct, 2),
                "distance_to_cluster_top_pct": round(distance_to_top_pct, 2),
            }
            self._attach_thrust_assessments(result, thrust_assessments)
            return result

        sma200_slope_pct = _series_slope_pct(
            ma["sma200"], len(ma) - 1, self.cfg.sma200_slope_lookback_days
        )
        trend_ok = (
            sma200_slope_pct is not None
            and sma200_slope_pct >= self.cfg.min_sma200_slope_pct
            and (
                weekly_30w_slope_pct is None
                or weekly_30w_slope_pct >= self.cfg.min_weekly_30w_slope_pct
            )
        )
        proximity_score = _clamp(
            (self.cfg.near_cluster_top_pct - abs(distance_to_top_pct))
            / max(0.01, self.cfg.near_cluster_top_pct)
            * 100.0
        )
        score = (
            0.55 * self._tightness_score(cluster_width_pct)
            + 0.20 * proximity_score
            + 0.15 * (100.0 if trend_ok else 25.0)
            + 0.10 * rs_score
        )
        near = (
            cluster_width_pct <= self.cfg.tight_cluster_width_pct
            and abs(distance_to_top_pct) <= self.cfg.near_cluster_top_pct
            and trend_ok
        )
        entry_trigger = cluster_top * (1.0 + self.cfg.breakout_buffer_pct / 100.0)
        daily_tactical_stop = max(0.01, cluster_bottom * 0.98)
        weekly_stop = derive_weekly_structural_stop(
            weekly,
            reference_price=entry_trigger,
            as_of=work.index[-1],
            structure_lookback_weeks=self.cfg.weekly_structure_lookback_weeks,
            base_lookback_weeks=self.cfg.weekly_base_lookback_weeks,
            pivot_left_weeks=self.cfg.weekly_pivot_left_weeks,
            pivot_right_weeks=self.cfg.weekly_pivot_right_weeks,
            min_support_gap_pct=self.cfg.weekly_min_support_gap_pct,
            stop_buffer_pct=self.cfg.weekly_stop_buffer_pct,
            stop_atr_fraction=self.cfg.weekly_stop_atr_fraction,
        )
        invalidation = (
            weekly_stop.stop_level if weekly_stop is not None else daily_tactical_stop
        )
        stop_distance_pct = (entry_trigger - invalidation) / entry_trigger * 100.0
        result.triggered = score >= self.cfg.min_trigger_score
        result.sub_state = "pre_breakout"
        result.actionability = "near_actionable" if near and result.triggered else ("watch" if result.triggered else "not_valid")
        result.actionability_reason = (
            "tight_ma_cluster_near_breakout"
            if near
            else "ma_cluster_watch_waiting_for_volume_thrust"
        )
        result.score = round(_clamp(score), 1)
        result.trigger_level = round(cluster_top * (1.0 + self.cfg.breakout_buffer_pct / 100.0), 2)
        result.invalidation_level = round(invalidation, 2)
        result.raw_features = {
            "ma_cluster_phase": "pre_breakout",
            "ma_cluster_width_pct": round(cluster_width_pct, 2),
            "ma_cluster_top": round(cluster_top, 4),
            "ma_cluster_bottom": round(cluster_bottom, 4),
            "distance_to_cluster_top_pct": round(distance_to_top_pct, 2),
            "daily_sma200_slope_pct": round(sma200_slope_pct, 2) if sma200_slope_pct is not None else None,
            "weekly_30w_slope_pct": round(weekly_30w_slope_pct, 2) if weekly_30w_slope_pct is not None else None,
            "daily_tactical_stop_level": round(daily_tactical_stop, 4),
            "weekly_structural_stop_level": round(weekly_stop.stop_level, 4) if weekly_stop is not None else None,
            "weekly_structural_support_level": round(weekly_stop.support_level, 4) if weekly_stop is not None else None,
            "weekly_structural_support_source": weekly_stop.support_source if weekly_stop is not None else None,
            "weekly_structural_support_week": weekly_stop.support_week if weekly_stop is not None else None,
            "weekly_structural_stop_distance_pct": round(stop_distance_pct, 2),
            "weekly_atr14": round(weekly_stop.weekly_atr14, 4) if weekly_stop is not None and weekly_stop.weekly_atr14 is not None else None,
            "weekly_ema10": round(weekly_stop.weekly_ema10, 4) if weekly_stop is not None and weekly_stop.weekly_ema10 is not None else None,
            "weekly_sma30": round(weekly_stop.weekly_sma30, 4) if weekly_stop is not None and weekly_stop.weekly_sma30 is not None else None,
            "rs_improving": rs_improving,
            "ema10": round(values[0], 4),
            "ema20": round(values[1], 4),
            "sma50": round(values[2], 4),
            "sma150": round(values[3], 4),
            "sma200": round(values[4], 4),
        }
        self._attach_thrust_assessments(result, thrust_assessments)
        result.base_metrics = {
            "ma_cluster_top": cluster_top,
            "ma_cluster_bottom": cluster_bottom,
            "ma_cluster_width_pct": cluster_width_pct,
            "stop_distance_pct": stop_distance_pct,
            "weekly_structural_support_level": weekly_stop.support_level if weekly_stop is not None else None,
        }
        result.reasons.extend(
            [
                f"ma_width={cluster_width_pct:.2f}%",
                f"distance_to_top={distance_to_top_pct:.2f}%",
                "waiting_for_volume_thrust",
            ]
        )
        if not trend_ok:
            result.warning_flags.append("ma_cluster_long_term_trend_weak")
        if weekly_stop is None:
            result.warning_flags.append("ma_cluster_weekly_structure_unavailable")
        return result

    @staticmethod
    def _attach_thrust_assessments(
        result: SetupResult,
        assessments: dict[str, dict[str, Any]],
    ) -> None:
        """Persist the daily/weekly annotation without affecting setup scoring."""
        profiles = [
            assessment.get("research_profile")
            for assessment in assessments.values()
            if isinstance(assessment, dict)
        ]
        result.raw_features.update(
            {
                "ma_cluster_thrust_daily": assessments["daily"],
                "ma_cluster_thrust_weekly": assessments["weekly"],
                "ma_cluster_research_profile": choose_ma_cluster_research_profile(profiles),
            }
        )

    def _tightness_score(self, width_pct: float) -> float:
        if width_pct <= self.cfg.very_tight_cluster_width_pct:
            return 100.0
        span = self.cfg.max_watch_cluster_width_pct - self.cfg.very_tight_cluster_width_pct
        if span <= 0:
            return 0.0
        return _clamp(
            (self.cfg.max_watch_cluster_width_pct - width_pct) / span * 100.0
        )

    def _risk_score(self, stop_distance_pct: float) -> float:
        if stop_distance_pct <= 5.0:
            return 100.0
        span = self.cfg.max_watch_stop_distance_pct - 5.0
        if span <= 0:
            return 0.0
        return _clamp(
            (self.cfg.max_watch_stop_distance_pct - stop_distance_pct) / span * 100.0
        )

    @staticmethod
    def _raw_features(
        breakout: dict[str, Any],
        current_close: float,
        extension_pct: float,
        stop_distance_pct: float,
        sma200_slope_pct: float | None,
        weekly_30w_slope_pct: float | None,
        rs_improving: bool,
        phase: str,
        weekly_stop: WeeklyStructuralStop | None,
        daily_tactical_stop: float,
    ) -> dict[str, float | str | bool | None]:
        row = breakout["row"]
        values = breakout["ma_values"]
        return {
            "ma_cluster_phase": phase,
            "ma_cluster_width_pct": round(float(breakout["cluster_width_pct"]), 2),
            "ma_cluster_top": round(float(breakout["cluster_top"]), 4),
            "ma_cluster_bottom": round(float(breakout["cluster_bottom"]), 4),
            "mas_crossed": int(breakout["crossed"]),
            "mas_total": len(MA_COLUMNS),
            "breakout_date": pd.Timestamp(row.name).date().isoformat(),
            "breakout_type": "one_day_thrust" if int(breakout["age"]) == 0 else "follow_through",
            "breakout_age_bars": int(breakout["age"]),
            "breakout_rel_volume_20d": round(float(breakout["rel_volume"]), 2) if breakout["rel_volume"] is not None else None,
            "breakout_volume": round(float(row["volume"]), 0),
            "avg_volume_20d_prior": round(float(breakout["avg_volume"]), 0) if breakout["avg_volume"] is not None else None,
            "breakout_close_location": round(float(breakout["clv"]), 2),
            "breakout_open": round(float(row["open"]), 4),
            "breakout_high": round(float(row["high"]), 4),
            "breakout_low": round(float(row["low"]), 4),
            "breakout_close": round(float(row["close"]), 4),
            "current_close": round(current_close, 4),
            "distance_from_cluster_top_pct": round(extension_pct, 2),
            "structural_stop_distance_pct": round(stop_distance_pct, 2),
            "daily_tactical_stop_level": round(daily_tactical_stop, 4),
            "weekly_structural_stop_level": round(weekly_stop.stop_level, 4) if weekly_stop is not None else None,
            "weekly_structural_support_level": round(weekly_stop.support_level, 4) if weekly_stop is not None else None,
            "weekly_structural_support_source": weekly_stop.support_source if weekly_stop is not None else None,
            "weekly_structural_support_week": weekly_stop.support_week if weekly_stop is not None else None,
            "weekly_atr14": round(weekly_stop.weekly_atr14, 4) if weekly_stop is not None and weekly_stop.weekly_atr14 is not None else None,
            "weekly_ema10": round(weekly_stop.weekly_ema10, 4) if weekly_stop is not None and weekly_stop.weekly_ema10 is not None else None,
            "weekly_sma30": round(weekly_stop.weekly_sma30, 4) if weekly_stop is not None and weekly_stop.weekly_sma30 is not None else None,
            "daily_sma200_slope_pct": round(sma200_slope_pct, 2) if sma200_slope_pct is not None else None,
            "weekly_30w_slope_pct": round(weekly_30w_slope_pct, 2) if weekly_30w_slope_pct is not None else None,
            "held_above_cluster": bool(breakout["held_above"]),
            "rs_improving": rs_improving,
            "ema10": round(float(values["ema10"]), 4),
            "ema20": round(float(values["ema20"]), 4),
            "sma50": round(float(values["sma50"]), 4),
            "sma150": round(float(values["sma150"]), 4),
            "sma200": round(float(values["sma200"]), 4),
        }
