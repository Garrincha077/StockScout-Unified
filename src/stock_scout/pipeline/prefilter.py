from __future__ import annotations

from dataclasses import dataclass, field

from stock_scout.config.schema import PrefilterConfig


@dataclass
class PrefilterResult:
    passed: bool
    failed_conditions: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    # Trend conditions that didn't hold. In "soft" trend_gate these are NOT
    # fatal (they don't appear in failed_conditions) but are surfaced so the
    # UI / scorer can see a sub-trend / early-stage name was let through.
    trend_flags: list[str] = field(default_factory=list)


def prefilter(features: dict, cfg: PrefilterConfig) -> PrefilterResult:
    """Apply cheap deterministic gates against the latest indicator features.

    `features` comes from `enrich.latest_features(df)`.

    Liquidity / price / history are always HARD gates. Trend conditions
    (close vs SMA50/150/200, distance to 52w high, RS) are hard only when
    ``cfg.trend_gate == "hard"``; in the default "soft" mode they degrade to
    non-fatal ``trend_flags`` so early setups in formation can still surface.
    """
    fails: list[str] = []
    reasons: list[str] = []
    trend_flags: list[str] = []
    soft = cfg.trend_gate == "soft"

    def _trend(cond_failed: bool, fail_label: str, ok_label: str | None = None) -> None:
        """Record a trend condition: fatal in hard mode, a flag in soft mode."""
        if cond_failed:
            (trend_flags if soft else fails).append(fail_label)
        elif ok_label:
            reasons.append(ok_label)

    # --- Hard gates: tradeability + data sufficiency ---------------------
    bars = features.get("bars_available") or 0
    if bars < cfg.min_history_days:
        fails.append(f"insufficient_history({bars}<{cfg.min_history_days})")

    avg_dv = features.get("avg_dollar_volume_50d")
    close = features.get("close")
    if close is None:
        fails.append("missing_close")
    elif close < cfg.min_price:
        accumulation_price_ok = (
            close >= cfg.accumulation_min_price
            and avg_dv is not None
            and avg_dv >= cfg.accumulation_min_avg_dollar_volume_50d
        )
        if accumulation_price_ok:
            trend_flags.append(
                f"price_relaxed_for_accumulation({close:.2f}<${cfg.min_price:.2f})"
            )
        else:
            fails.append(f"price<{cfg.min_price}")

    avg_vol = features.get("avg_volume_50d")
    if avg_vol is None or avg_vol < cfg.min_avg_volume_50d:
        fails.append(f"avg_vol_50d<{cfg.min_avg_volume_50d:,}")

    if avg_dv is None or avg_dv < cfg.min_avg_dollar_volume_50d:
        fails.append(f"avg_$vol_50d<${cfg.min_avg_dollar_volume_50d:,.0f}")

    # --- Trend gates: hard or soft per cfg.trend_gate --------------------
    sma50 = features.get("sma50")
    sma150 = features.get("sma150")
    sma200 = features.get("sma200")

    if cfg.require_close_above_sma50:
        _trend(sma50 is None or close is None or close < sma50, "close<sma50", "close>=sma50")
    if cfg.require_close_above_sma150:
        _trend(sma150 is None or close is None or close < sma150, "close<sma150", "close>=sma150")
    if cfg.require_close_above_sma200:
        _trend(sma200 is None or close is None or close < sma200, "close<sma200", "close>=sma200")

    dist_high = features.get("distance_to_52w_high_pct")
    # dist_high is negative; -30 means 30% below 52w high.
    if dist_high is None:
        _trend(True, "missing_distance_to_52w_high")
    else:
        _trend(
            dist_high < -cfg.max_distance_to_52w_high_pct,
            f"too_far_from_52w_high({dist_high:.1f}%)",
            f"within_{cfg.max_distance_to_52w_high_pct:.0f}%_of_52w_high",
        )

    rs3m = features.get("rs_score_3m")
    if rs3m is not None:
        _trend(rs3m < cfg.min_rs_vs_spy_3m, f"weak_rs_3m({rs3m:.1f})", f"positive_rs_3m({rs3m:.1f})")

    return PrefilterResult(
        passed=not fails,
        failed_conditions=fails,
        reasons=reasons,
        trend_flags=trend_flags,
    )
