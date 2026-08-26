from __future__ import annotations

import pandas as pd

from stock_scout.indicators.highs_lows import (
    close_location_value,
    distance_to_52w_high_pct,
    distance_to_52w_low_pct,
    rolling_high,
    rolling_low,
)
from stock_scout.indicators.momentum import rs_score_vs_benchmark, rsi, weighted_rs_score
from stock_scout.indicators.moving_averages import ema, is_rising, sma
from stock_scout.indicators.patterns import range_contraction, vcp_score
from stock_scout.indicators.volatility import adr_pct, atr
from stock_scout.indicators.volume import (
    avg_dollar_volume,
    avg_volume,
    pocket_pivot,
    up_down_volume_ratio,
    volume_dryup_score,
    volume_expansion_ratio,
    volume_ratio,
)


def compute_indicators(
    df: pd.DataFrame,
    benchmark_close: pd.Series | None = None,
    sma200_rising_lookback: int = 20,
) -> pd.DataFrame:
    """Attach the full indicator panel to a per-ticker OHLCV frame.

    `benchmark_close` should be SPY's adjusted close, aligned by date. If None,
    RS columns will be NaN.
    """
    if df.empty:
        return df

    out = df.copy()
    close = out["close"]
    volume = out["volume"]

    out["sma10"] = sma(close, 10)
    out["sma20"] = sma(close, 20)
    out["sma50"] = sma(close, 50)
    out["sma150"] = sma(close, 150)
    out["sma200"] = sma(close, 200)
    out["ema10"] = ema(close, 10)
    out["ema20"] = ema(close, 20)

    out["sma200_rising"] = is_rising(out["sma200"], sma200_rising_lookback)

    out["atr10"] = atr(out, 10)
    out["atr14"] = atr(out, 14)
    out["atr20"] = atr(out, 20)
    out["atr50"] = atr(out, 50)
    out["adr20_pct"] = adr_pct(out, 20)

    out["rsi14"] = rsi(close, 14)

    # min_periods=252: a genuine 52-week high, not the max of however many bars
    # a young listing happens to have. Every screen that gates on
    # distance_to_52w_high_pct was reading the latter. Safe now that prefilter
    # requires 252 bars, so anything scored has a full window.
    out["high_52w"] = rolling_high(close, 252, min_periods=252)
    out["low_52w"] = rolling_low(close, 252, min_periods=252)
    out["distance_to_52w_high_pct"] = distance_to_52w_high_pct(close, 252, min_periods=252)
    out["distance_to_52w_low_pct"] = distance_to_52w_low_pct(close, 252, min_periods=252)

    out["avg_volume_20d"] = avg_volume(volume, 20)
    out["avg_volume_50d"] = avg_volume(volume, 50)
    out["avg_dollar_volume_50d"] = avg_dollar_volume(close, volume, 50)
    out["volume_ratio_50d"] = volume_ratio(volume, 50)
    out["volume_dryup_score"] = volume_dryup_score(volume, 10, 50)

    # Raw price returns + simple relative volume (Qullamaggie/Oliver Kell momentum
    # screens). Returns are in percent; rvol is the latest bar's volume vs its 50d avg.
    out["ret_1d_pct"] = close.pct_change(1) * 100.0
    out["ret_1m_pct"] = close.pct_change(21) * 100.0
    out["ret_3m_pct"] = close.pct_change(63) * 100.0
    out["ret_6m_pct"] = close.pct_change(126) * 100.0
    out["rvol_today"] = volume / out["avg_volume_50d"].replace(0, pd.NA)

    # Accumulation / early-footprint volume features (Faza G).
    out["up_down_vol_ratio_50d"] = up_down_volume_ratio(close, volume, 50)
    out["volume_expansion_5_50"] = volume_expansion_ratio(volume, 5, 50)
    pp = pocket_pivot(close, volume, lookback=10)
    out["pocket_pivot"] = pp
    # Bars since the most recent pocket pivot (cumcount that resets on each True).
    pp_group = pp.cumsum()
    out["days_since_pocket_pivot"] = pp_group.groupby(pp_group).cumcount()
    out.loc[pp_group == 0, "days_since_pocket_pivot"] = pd.NA

    out["range_contraction_10_50"] = range_contraction(out, 10, 50)
    out["vcp_score"] = vcp_score(out, lookback=50)

    out["clv1"] = close_location_value(out, window=1)
    out["clv5"] = close_location_value(out, window=5)

    # Higher-lows / accumulation-base footprint (constant over the last bar;
    # detectors read it via latest_features). Computed once on the full close.
    from stock_scout.setups.base_analysis import higher_lows_count

    out["higher_lows"] = higher_lows_count(close)

    if benchmark_close is not None:
        # Align benchmark to ticker's date index.
        aligned_bench = benchmark_close.reindex(out.index).ffill()
        out["rs_score_3m"] = rs_score_vs_benchmark(close, aligned_bench, window=63)
        out["rs_score_6m"] = rs_score_vs_benchmark(close, aligned_bench, window=126)
        # IBD-style weighted multi-timeframe RS (3/6/9/12m, 40/20/20/20) — the
        # canonical input to the universe-relative RS Rating percentile.
        out["rs_score_weighted"] = weighted_rs_score(close, aligned_bench)
        out["rs_line"] = close / aligned_bench.replace(0, pd.NA)
        # RS line trend = is RS line at a 50-day high?
        rs_line = out["rs_line"]
        rs_max_50 = rs_line.rolling(window=50, min_periods=50).max()
        out["rs_line_at_50d_high"] = rs_line >= (rs_max_50 - 1e-9)
        # 52-week RS line high — the canonical Weinstein / O'Neil leading signal.
        # "RS line punches its 52w high before price does" = strongest Stage-2 tell.
        rs_max_252 = rs_line.rolling(window=252, min_periods=126).max()
        out["rs_line_52w_high"] = rs_max_252
        out["rs_line_52w_distance_pct"] = (rs_line - rs_max_252) / rs_max_252 * 100.0
        out["rs_line_at_52w_high"] = out["rs_line_52w_distance_pct"] >= -2.0
    else:
        for col in [
            "rs_score_3m",
            "rs_score_6m",
            "rs_score_weighted",
            "rs_line",
            "rs_line_at_50d_high",
            "rs_line_52w_high",
            "rs_line_52w_distance_pct",
            "rs_line_at_52w_high",
        ]:
            out[col] = pd.NA

    # 10/20 EMA cross features (used by new EMACrossDetector and as bonus signal
    # for Minervini/Weinstein).
    out["ema10_above_ema20"] = out["ema10"] > out["ema20"]
    # Track how many bars since the most recent up-cross of EMA10 over EMA20.
    crossed_up = (out["ema10"] > out["ema20"]) & (
        out["ema10"].shift(1) <= out["ema20"].shift(1)
    )
    # Index-since-last-True helper: cumulative count that resets on every True.
    group = crossed_up.cumsum()
    out["bars_since_ema_cross_up"] = group.groupby(group).cumcount()
    # Mask: only meaningful when at least one cross has happened.
    out.loc[group == 0, "bars_since_ema_cross_up"] = pd.NA

    return out


def latest_features(df_enriched: pd.DataFrame) -> dict[str, float | bool | None]:
    """Extract the last bar's features as a flat dict for setup detectors / scoring."""
    if df_enriched.empty:
        return {}
    row = df_enriched.iloc[-1]

    def _f(name: str):
        v = row.get(name)
        if pd.isna(v):
            return None
        if isinstance(v, (bool,)):
            return bool(v)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    keys = [
        "open", "high", "low", "close", "volume",
        "sma10", "sma20", "sma50", "sma150", "sma200",
        "ema10", "ema20",
        "atr10", "atr14", "atr20", "atr50",
        "adr20_pct",
        "rsi14",
        "high_52w", "low_52w",
        "distance_to_52w_high_pct", "distance_to_52w_low_pct",
        "avg_volume_20d", "avg_volume_50d", "avg_dollar_volume_50d",
        "volume_ratio_50d", "volume_dryup_score",
        "ret_1d_pct", "ret_1m_pct", "ret_3m_pct", "ret_6m_pct", "rvol_today",
        "up_down_vol_ratio_50d", "volume_expansion_5_50", "days_since_pocket_pivot",
        "range_contraction_10_50", "vcp_score",
        "clv1", "clv5", "higher_lows",
        "rs_score_3m", "rs_score_6m", "rs_score_weighted", "rs_line",
        "rs_line_52w_distance_pct",
        "bars_since_ema_cross_up",
    ]
    flat = {k: _f(k) for k in keys}
    flat["sma200_rising"] = bool(row.get("sma200_rising")) if not pd.isna(row.get("sma200_rising")) else None
    flat["rs_line_at_50d_high"] = (
        bool(row.get("rs_line_at_50d_high")) if not pd.isna(row.get("rs_line_at_50d_high")) else None
    )
    flat["rs_line_at_52w_high"] = (
        bool(row.get("rs_line_at_52w_high")) if not pd.isna(row.get("rs_line_at_52w_high")) else None
    )
    flat["ema10_above_ema20"] = (
        bool(row.get("ema10_above_ema20")) if not pd.isna(row.get("ema10_above_ema20")) else None
    )
    flat["pocket_pivot"] = (
        bool(row.get("pocket_pivot")) if not pd.isna(row.get("pocket_pivot")) else None
    )
    flat["as_of"] = str(df_enriched.index[-1].date())
    flat["bars_available"] = len(df_enriched)
    return flat
