from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stock_scout.config.schema import CrashBaseStage1SetupConfig
from stock_scout.data.corporate_actions import detect_m_and_a_from_price
from stock_scout.indicators.highs_lows import close_location_value
from stock_scout.indicators.moving_averages import sma
from stock_scout.setups.base import SetupDetector, SetupResult
from stock_scout.setups.base_analysis import find_swings


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm(v: float | int | None, lo: float, hi: float) -> float:
    if v is None or hi <= lo:
        return 0.0
    return _clamp((float(v) - lo) / (hi - lo) * 100.0)


def _weekly_from_daily(df_daily: pd.DataFrame) -> pd.DataFrame:
    return (
        df_daily.resample("W-FRI")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def _safe_rel_volume(volume: pd.Series, window: int, min_periods: int = 8) -> pd.Series:
    avg = volume.rolling(window, min_periods=min_periods).mean().shift(1)
    return volume / avg.mask(avg == 0)


def _slope_pct(series: pd.Series, lookback: int) -> float | None:
    clean = series.dropna()
    if len(clean) <= lookback:
        return None
    start = clean.iloc[-1 - lookback]
    end = clean.iloc[-1]
    if pd.isna(start) or pd.isna(end) or float(start) == 0:
        return None
    return (float(end) - float(start)) / abs(float(start)) * 100.0


def _atr_abs(df: pd.DataFrame, window: int = 14) -> float | None:
    if df is None or len(df) < window + 1:
        return None
    high = df["high"].astype("float64")
    low = df["low"].astype("float64")
    close = df["close"].astype("float64")
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = tr.rolling(window, min_periods=max(5, window // 2)).mean().iloc[-1]
    return float(value) if pd.notna(value) else None


def _spaced_cluster_count(indices: list[int], min_gap: int) -> int:
    count = 0
    last = -10_000
    for idx in sorted(indices):
        if idx - last >= min_gap:
            count += 1
            last = idx
    return count


@dataclass
class _TrendlineInfo:
    line_value: float | None
    p1_date: str | None
    p1_price: float | None
    p2_date: str | None
    p2_price: float | None
    attempt_count: int
    breakout: bool
    breakout_buffer_pct: float | None


def _trendline_info(
    work: pd.DataFrame,
    rel_vol: pd.Series,
    cfg: CrashBaseStage1SetupConfig,
) -> _TrendlineInfo:
    if len(work) < cfg.min_base_age_weeks:
        return _TrendlineInfo(None, None, None, None, None, 0, False, None)

    highs, _ = find_swings(work["high"], left=2, right=2)
    prior_highs = [h for h in highs if h.idx < len(work) - 1]
    if len(prior_highs) < 2:
        return _TrendlineInfo(None, None, None, None, None, 0, False, None)

    current_close = float(work["close"].iloc[-1])
    current_idx = len(work) - 1
    atr = _atr_abs(work)
    best: tuple[float, object, object, float, int] | None = None
    # Hoisted out of the pair loop below. The touch scan used to read
    # work["high"].iloc[pos] and work["close"].iloc[pos] one bar at a time
    # inside a triple-nested loop — with ~30 swing highs over 520 weekly bars
    # that is on the order of 200k scalar pandas lookups per ticker, and the
    # profiler attributed 42% of the whole per-ticker scan cost to this
    # function. Same arithmetic, same comparisons, done on numpy arrays.
    high_arr = work["high"].to_numpy(dtype=float)
    close_arr = work["close"].to_numpy(dtype=float)
    touch_factor = 1.0 - cfg.trendline_touch_tolerance_pct / 100.0
    break_factor = 1.0 + cfg.trendline_break_buffer_pct / 100.0

    for i, h1 in enumerate(prior_highs[:-1]):
        for h2 in prior_highs[i + 1 :]:
            if h2.idx - h1.idx < cfg.trendline_min_anchor_separation_weeks:
                continue
            if h2.price > h1.price * (1.0 - cfg.trendline_min_anchor_drop_pct / 100.0):
                continue
            slope = (h2.price - h1.price) / max(1, h2.idx - h1.idx)
            projected = h1.price + slope * (current_idx - h1.idx)
            if projected <= 0:
                continue
            # Very old steep lines often project far below zero/current price;
            # ignore them because they no longer describe the present base.
            if projected < current_close * 0.45 or projected > current_close * 3.0:
                continue

            positions = np.arange(h2.idx + 1, current_idx)
            if positions.size:
                line = h1.price + slope * (positions - h1.idx)
                touched = (
                    (line > 0)
                    & (high_arr[positions] >= line * touch_factor)
                    & (close_arr[positions] <= line * break_factor)
                )
                attempts = positions[touched].tolist()
            else:
                attempts = []

            attempt_count = _spaced_cluster_count(attempts, cfg.min_attempt_gap_weeks)
            proximity_penalty = min(30.0, abs(projected / current_close - 1.0) * 30.0)
            span_score = min(20.0, (h2.idx - h1.idx) / max(1, cfg.lookback_weeks) * 25.0)
            recency_score = min(18.0, h2.idx / max(1, current_idx) * 18.0)
            score = attempt_count * 18.0 + span_score + recency_score - proximity_penalty
            candidate = (score, h1, h2, projected, attempt_count)
            if best is None or candidate[0] > best[0]:
                best = candidate

    if best is None:
        return _TrendlineInfo(None, None, None, None, None, 0, False, None)

    _score, h1, h2, projected, attempt_count = best
    atr_buffer_pct = ((0.5 * atr) / projected * 100.0) if atr is not None and projected > 0 else 0.0
    buffer_pct = max(cfg.trendline_break_buffer_pct, atr_buffer_pct)
    current_rel_vol = rel_vol.iloc[-1] if len(rel_vol) else None
    breakout = (
        current_close > projected * (1.0 + buffer_pct / 100.0)
        and current_rel_vol is not None
        and pd.notna(current_rel_vol)
        and float(current_rel_vol) >= cfg.trendline_breakout_rvol
    )
    return _TrendlineInfo(
        line_value=projected,
        p1_date=h1.date.isoformat(),
        p1_price=round(float(h1.price), 2),
        p2_date=h2.date.isoformat(),
        p2_price=round(float(h2.price), 2),
        attempt_count=attempt_count,
        breakout=bool(breakout),
        breakout_buffer_pct=round(buffer_pct, 2),
    )


def _resistance_attempts(
    base: pd.DataFrame,
    cfg: CrashBaseStage1SetupConfig,
) -> tuple[float | None, int, bool]:
    if len(base) < max(8, cfg.min_attempt_gap_weeks + cfg.resistance_exclude_recent_weeks):
        return None, 0, False
    prior = base.iloc[: -cfg.resistance_exclude_recent_weeks] if cfg.resistance_exclude_recent_weeks > 0 else base
    if prior.empty:
        return None, 0, False
    resistance = float(prior["close"].max())
    tol = cfg.resistance_tolerance_pct / 100.0
    break_buf = cfg.resistance_break_buffer_pct / 100.0
    # Same scalar-lookup pattern as the trendline scan above, same fix.
    n = max(0, len(base) - 1)
    if n:
        highs = base["high"].to_numpy(dtype=float)[:n]
        closes = base["close"].to_numpy(dtype=float)[:n]
        hit = (highs >= resistance * (1.0 - tol)) & (closes <= resistance * (1.0 + break_buf))
        indices = np.flatnonzero(hit).tolist()
    else:
        indices = []
    attempts = _spaced_cluster_count(indices, cfg.min_attempt_gap_weeks)
    current_close = float(base["close"].iloc[-1])
    breakout = current_close > resistance * (1.0 + break_buf)
    return resistance, attempts, breakout


def _daily_rvol_headsup(df_daily: pd.DataFrame, cfg: CrashBaseStage1SetupConfig) -> tuple[bool, float | None, float | None]:
    if df_daily is None or len(df_daily) < cfg.daily_rvol_window + 1:
        return False, None, None
    work = df_daily.dropna(subset=["open", "high", "low", "close", "volume"])
    if len(work) < cfg.daily_rvol_window + 1:
        return False, None, None
    avg = float(work["volume"].iloc[-cfg.daily_rvol_window - 1 : -1].mean())
    if avg <= 0:
        return False, None, None
    latest = work.iloc[-1]
    rv = float(latest["volume"]) / avg
    clv_denom = float(latest["high"] - latest["low"])
    clv = 0.5 if clv_denom <= 0 else (float(latest["close"]) - float(latest["low"])) / clv_denom
    prev_close = float(work["close"].iloc[-2])
    up_day = float(latest["close"]) > prev_close or float(latest["close"]) > float(latest["open"])
    return bool(up_day and rv >= cfg.daily_headsup_rvol and clv >= cfg.min_close_location), round(rv, 2), round(clv, 2)


class CrashBaseStage1Detector(SetupDetector):
    """Multi-year Stage 1 crash-base watch detector.

    This intentionally tolerates wide, post-crash bases that conventional
    launch detectors may reject. It surfaces the watchlist family described by
    RNG/ZM/ROKU/APPS/KC: a major 5-year drawdown, long basing process, repeated
    resistance pressure, high-RVOL demand, quiet pullbacks, and finally a
    weekly descending-trendline breakout on volume.
    """

    name = "crash_base_stage1"

    def __init__(self, cfg: CrashBaseStage1SetupConfig):
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
        weekly = weekly.dropna(subset=["open", "high", "low", "close", "volume"])
        if len(weekly) < self.cfg.min_weekly_history_weeks:
            result.failed_conditions.append("insufficient_weekly_history")
            result.actionability_reason = "insufficient_weekly_history"
            return result

        work = weekly.iloc[-min(len(weekly), self.cfg.lookback_weeks) :].copy()
        current = work.iloc[-1]
        current_close = float(current["close"])
        if current_close <= 0:
            result.failed_conditions.append("missing_close")
            result.actionability_reason = "missing_close"
            return result

        peak_pos = int(work["high"].values.argmax())
        peak_high = float(work["high"].iloc[peak_pos])
        after_peak = work.iloc[peak_pos:]
        low_pos_abs = int(after_peak["low"].values.argmin()) + peak_pos
        low_after_peak = float(work["low"].iloc[low_pos_abs])
        drawdown_pct = (peak_high - low_after_peak) / peak_high * 100.0 if peak_high > 0 else 0.0
        base_age_weeks = len(work) - 1 - low_pos_abs
        base = work.iloc[low_pos_abs:].copy()
        base_low = float(base["low"].min())
        base_high = float(base["high"].max())
        base_depth_pct = (base_high - base_low) / max(1e-9, base_low) * 100.0
        range_position = (
            (current_close - base_low) / (base_high - base_low)
            if base_high > base_low
            else 0.5
        )
        range_position = max(0.0, min(1.0, float(range_position)))

        rel_vol = _safe_rel_volume(work["volume"], self.cfg.weekly_rvol_window)
        clv = close_location_value(work, window=1).fillna(0.5)
        up_week = work["close"] > work["close"].shift(1)
        current_weekly_rvol = float(rel_vol.iloc[-1]) if pd.notna(rel_vol.iloc[-1]) else None
        current_clv = float(clv.iloc[-1]) if pd.notna(clv.iloc[-1]) else None
        base_mask = pd.Series(False, index=work.index)
        base_mask.iloc[low_pos_abs:] = True
        demand_mask = (
            base_mask
            & up_week
            & (rel_vol >= self.cfg.high_rvol_accumulation_mult)
            & (clv >= self.cfg.min_close_location)
        )
        demand_positions = [int(work.index.get_loc(idx)) for idx in work.index[demand_mask.fillna(False)]]
        demand_spike_count = len(demand_positions)
        high_rvol_accumulation = bool(demand_mask.iloc[-1]) if len(demand_mask) else False

        low_pullback_after_demand = False
        last_demand_pos = next((p for p in reversed(demand_positions) if p < len(work) - 1), None)
        if last_demand_pos is not None:
            weeks_since = len(work) - 1 - last_demand_pos
            if 1 <= weeks_since <= self.cfg.low_pullback_max_weeks:
                pullback = work.iloc[last_demand_pos + 1 :]
                pullback_rv = rel_vol.iloc[last_demand_pos + 1 :]
                down_or_inside_all = (
                    (work["close"] < work["close"].shift(1))
                    | ((work["high"] <= work["high"].shift(1)) & (work["low"] >= work["low"].shift(1)))
                )
                down_or_inside = down_or_inside_all.iloc[last_demand_pos + 1 :]
                avg_pullback_rv = float(pullback_rv.dropna().mean()) if not pullback_rv.dropna().empty else None
                demand_low = float(work["low"].iloc[last_demand_pos])
                low_pullback_after_demand = (
                    avg_pullback_rv is not None
                    and avg_pullback_rv <= self.cfg.low_pullback_rvol
                    and bool(down_or_inside.fillna(False).any())
                    and float(pullback["close"].iloc[-1]) >= demand_low * 0.97
                )

        resistance, resistance_attempt_count, resistance_breakout = _resistance_attempts(base, self.cfg)
        tl = _trendline_info(work, rel_vol, self.cfg)
        daily_headsup, daily_rvol, daily_clv = _daily_rvol_headsup(df_daily, self.cfg)

        sma30 = sma(work["close"], 30)
        weekly_30w_slope_pct: float | None = None
        extension_above_30w_pct: float | None = None
        above_30w = False
        near_reclaim_zone = False
        if len(sma30) >= 35 and pd.notna(sma30.iloc[-1]) and float(sma30.iloc[-1]) > 0:
            last_sma = float(sma30.iloc[-1])
            above_30w = current_close >= last_sma
            extension_above_30w_pct = (current_close - last_sma) / last_sma * 100.0
            prior_sma = sma30.iloc[-self.cfg.ma_slope_lookback_weeks - 1]
            if pd.notna(prior_sma) and float(prior_sma) > 0:
                weekly_30w_slope_pct = (last_sma - float(prior_sma)) / float(prior_sma) * 100.0
            near_reclaim_zone = extension_above_30w_pct >= -self.cfg.max_below_30w_reclaim_pct

        price_slope_recent = _slope_pct(work["close"], self.cfg.recovery_lookback_weeks)
        ma_context_ok = (
            weekly_30w_slope_pct is not None
            and weekly_30w_slope_pct >= self.cfg.min_30w_slope_pct
            and near_reclaim_zone
        ) or (
            above_30w
            and price_slope_recent is not None
            and price_slope_recent >= self.cfg.min_recovery_price_slope_pct
        )
        recovery_not_falling_knife = (
            range_position >= self.cfg.min_range_position
            or (price_slope_recent is not None and price_slope_recent >= self.cfg.min_recovery_price_slope_pct)
            or above_30w
        )

        dryup_near_resistance = False
        if resistance is not None and resistance > 0 and len(rel_vol.dropna()) >= 6:
            recent_rv = float(rel_vol.iloc[-6:].dropna().mean()) if not rel_vol.iloc[-6:].dropna().empty else None
            distance_to_resistance_pct = (current_close - resistance) / resistance * 100.0
            dryup_near_resistance = (
                recent_rv is not None
                and recent_rv <= self.cfg.low_pullback_rvol
                and -self.cfg.max_distance_below_resistance_pct <= distance_to_resistance_pct <= self.cfg.resistance_break_buffer_pct
            )

        valid_shape = (
            drawdown_pct >= self.cfg.min_drawdown_pct
            and base_age_weeks >= self.cfg.min_base_age_weeks
            and ma_context_ok
            and recovery_not_falling_knife
        )
        if drawdown_pct < self.cfg.min_drawdown_pct:
            result.failed_conditions.append(f"drawdown<{self.cfg.min_drawdown_pct:.0f}%")
        if base_age_weeks < self.cfg.min_base_age_weeks:
            result.failed_conditions.append(f"base_age<{self.cfg.min_base_age_weeks}w")
        if not ma_context_ok:
            result.failed_conditions.append("30w_context_not_ready")
        if not recovery_not_falling_knife:
            result.failed_conditions.append("falling_knife_context")

        alert_level = "watch"
        if tl.breakout:
            alert_level = "tier1_trendline_breakout"
        elif daily_headsup:
            alert_level = "tier2_daily_rvol_headsup"
        elif low_pullback_after_demand or dryup_near_resistance:
            alert_level = "tier3_low_volume_pullback"

        drawdown_score = _norm(drawdown_pct, self.cfg.min_drawdown_pct, self.cfg.high_quality_drawdown_pct)
        age_score = _norm(base_age_weeks, self.cfg.min_base_age_weeks, self.cfg.very_long_base_weeks)
        resistance_score = _clamp(resistance_attempt_count / self.cfg.max_attempt_bonus_count * 100.0)
        trendline_score = _clamp(tl.attempt_count / self.cfg.max_attempt_bonus_count * 100.0)
        ma_score = 0.0
        if above_30w:
            ma_score += 38.0
        if near_reclaim_zone:
            ma_score += 22.0
        if weekly_30w_slope_pct is not None:
            ma_score += _norm(weekly_30w_slope_pct, self.cfg.min_30w_slope_pct, 3.0) * 0.28
        if price_slope_recent is not None and price_slope_recent >= self.cfg.min_recovery_price_slope_pct:
            ma_score += 12.0
        ma_score = _clamp(ma_score)
        volume_score = (
            25.0 * _clamp(demand_spike_count / 4.0, 0.0, 1.0)
            + (18.0 if low_pullback_after_demand else 0.0)
            + (18.0 if dryup_near_resistance else 0.0)
            + (20.0 if high_rvol_accumulation else 0.0)
            + (28.0 if daily_headsup else 0.0)
            + (40.0 if tl.breakout else 0.0)
        )
        volume_score = _clamp(volume_score)
        crash_base_score = (
            0.20 * drawdown_score
            + 0.20 * age_score
            + 0.18 * max(resistance_score, trendline_score)
            + 0.18 * ma_score
            + 0.24 * volume_score
        )
        if drawdown_pct >= self.cfg.high_quality_drawdown_pct and base_age_weeks >= self.cfg.high_quality_base_weeks:
            crash_base_score += 5.0
        crash_base_score = _clamp(crash_base_score)

        if tl.breakout:
            phase = "trendline_breakout"
            bucket = "actionable_now"
            reason = "crash_base_5y_trendline_breakout"
        elif daily_headsup:
            phase = "daily_rvol_headsup"
            bucket = "near_actionable"
            reason = "provisional_daily_rvol_accumulation"
        elif low_pullback_after_demand:
            phase = "low_volume_pullback"
            bucket = "watch"
            reason = "quiet_pullback_after_demand"
        elif dryup_near_resistance:
            phase = "drying_up"
            bucket = "watch"
            reason = "dryup_near_resistance"
        elif high_rvol_accumulation:
            phase = "weekly_rvol_accumulation"
            bucket = "near_actionable"
            reason = "weekly_high_rvol_accumulation"
        elif crash_base_score >= self.cfg.min_watch_score:
            phase = "forming"
            bucket = "watch"
            reason = "long_stage1_crash_base"
        else:
            phase = "early_base"
            bucket = "not_valid"
            reason = "crash_base_score_below_threshold"

        result.triggered = valid_shape and (
            crash_base_score >= self.cfg.min_watch_score
            or alert_level.startswith("tier")
        )
        result.sub_state = phase
        result.actionability = bucket if result.triggered else "not_valid"
        result.actionability_reason = reason if result.triggered else (result.failed_conditions[0] if result.failed_conditions else reason)
        result.score = round(crash_base_score, 1)
        # The trigger is the nearest level still standing *above* the price.
        #
        # This used to be `tl.line_value or resistance or base_high`, and the
        # `or` chain never fell through: on 2026-07-31 the trendline supplied
        # the trigger in 146 of 146 crash bases and sat below the price in 131
        # of them, median 25.4% below. A trendline drawn off the crash high
        # keeps descending, so on a base 139 weeks old it has usually fallen
        # through the base itself - at which point it is a line the stock
        # cleared long ago, not the line it has to clear. ATAI printed a
        # trigger of 3.89 against a price of 7.18 while its own base high stood
        # at 7.22, and its own `resistance_breakout` feature already said the
        # level was taken out.
        overhead = [
            float(level)
            for level in (tl.line_value, resistance, base_high)
            if level is not None and float(level) > current_close
        ]
        # Nothing overhead means the base is fully cleared; the base high is
        # then the honest reference for what was broken.
        result.trigger_level = round(min(overhead) if overhead else base_high, 2)
        result.invalidation_level = round(max(0.01, base_low * 0.97), 2)
        result.raw_features = {
            "crash_base_score": round(crash_base_score, 1),
            "crash_base_phase": phase,
            "drawdown_5y_pct": round(drawdown_pct, 1),
            "current_below_5y_peak_pct": round((current_close / peak_high - 1.0) * 100.0, 1) if peak_high > 0 else None,
            "base_age_weeks": int(base_age_weeks),
            "base_depth_pct": round(base_depth_pct, 1),
            "base_low": round(base_low, 2),
            "base_high": round(base_high, 2),
            "range_position": round(range_position, 2),
            "resistance_level": round(resistance, 2) if resistance is not None else None,
            "resistance_attempt_count": int(resistance_attempt_count),
            "resistance_breakout": resistance_breakout,
            "trendline_attempt_count": int(tl.attempt_count),
            "trendline_value": round(tl.line_value, 2) if tl.line_value is not None else None,
            "trendline_anchor_1_date": tl.p1_date,
            "trendline_anchor_1_price": tl.p1_price,
            "trendline_anchor_2_date": tl.p2_date,
            "trendline_anchor_2_price": tl.p2_price,
            "trendline_breakout_5y": tl.breakout,
            "trendline_breakout_buffer_pct": tl.breakout_buffer_pct,
            "weekly_breakout_rvol": round(current_weekly_rvol, 2) if current_weekly_rvol is not None else None,
            "weekly_close_location": round(current_clv, 2) if current_clv is not None else None,
            "high_rvol_accumulation": high_rvol_accumulation,
            "demand_spike_count": int(demand_spike_count),
            "low_volume_pullback": low_pullback_after_demand,
            "dryup_near_resistance": dryup_near_resistance,
            "daily_rvol_headsup": daily_headsup,
            "daily_rvol": daily_rvol,
            "daily_close_location": daily_clv,
            "special_alert_level": alert_level,
            "weekly_30w_slope_pct": round(weekly_30w_slope_pct, 2) if weekly_30w_slope_pct is not None else None,
            "above_30w_sma": above_30w,
            "extension_above_30w_pct": round(extension_above_30w_pct, 2) if extension_above_30w_pct is not None else None,
            "price_slope_recent_pct": round(price_slope_recent, 2) if price_slope_recent is not None else None,
        }
        result.base_metrics = {
            "base_low": base_low,
            "base_high": base_high,
            "resistance_level": resistance,
            "trendline_value": tl.line_value,
            "drawdown_5y_pct": drawdown_pct,
            "base_age_weeks": base_age_weeks,
        }
        result.reasons.extend(
            [
                f"drawdown={drawdown_pct:.1f}%",
                f"base_age={base_age_weeks}w",
                f"resistance_attempts={resistance_attempt_count}",
                f"trendline_attempts={tl.attempt_count}",
                f"volume_score={volume_score:.1f}",
                f"phase={phase}",
            ]
        )
        if daily_headsup:
            result.reasons.append("daily_rvol_headsup")
        if tl.breakout:
            result.reasons.append("trendline_breakout_5y")
        if low_pullback_after_demand:
            result.reasons.append("low_volume_pullback_after_demand")
        return result
