from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI. No lookahead."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def roc(series: pd.Series, window: int) -> pd.Series:
    """Rate of change in percent."""
    return (series / series.shift(window) - 1.0) * 100.0


def relative_strength_line(ticker_close: pd.Series, benchmark_close: pd.Series) -> pd.Series:
    """RS line = ticker / benchmark, aligned on dates."""
    aligned = pd.concat([ticker_close, benchmark_close], axis=1, join="inner").dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    return aligned.iloc[:, 0] / aligned.iloc[:, 1].replace(0, np.nan)


def rs_score_vs_benchmark(
    ticker_close: pd.Series, benchmark_close: pd.Series, window: int = 63
) -> pd.Series:
    """RS as ROC differential vs benchmark over `window` days. Default 63 ≈ 3 months."""
    a = roc(ticker_close, window)
    b = roc(benchmark_close, window)
    return a - b


def weighted_rs_score(
    ticker_close: pd.Series,
    benchmark_close: pd.Series,
    windows: tuple[int, ...] = (63, 126, 189, 252),
    weights: tuple[float, ...] = (0.40, 0.20, 0.20, 0.20),
) -> pd.Series:
    """IBD-style weighted multi-timeframe relative strength vs the benchmark.

    Blends the RS-vs-benchmark ROC differential over ~3/6/9/12-month windows,
    over-weighting the most recent quarter (40/20/20/20), so a leader that has
    been strong across all horizons ranks above one that only popped recently.
    This is the canonical input to the universe-relative RS Rating percentile.

    Windows whose lookback exceeds the available history are skipped and the
    remaining weights are renormalised, so shorter series still yield a value
    (degrading gracefully toward the available timeframes).
    """
    if len(weights) != len(windows):
        raise ValueError("weights and windows must be the same length")
    acc: pd.Series | None = None
    total_w = 0.0
    for win, wt in zip(windows, weights):
        if len(ticker_close) <= win:
            continue
        contrib = rs_score_vs_benchmark(ticker_close, benchmark_close, win) * wt
        acc = contrib if acc is None else acc.add(contrib, fill_value=0.0)
        total_w += wt
    if acc is None or total_w == 0.0:
        return pd.Series(dtype=float)
    return acc / total_w


def rs_line_at_52w_high(
    ticker_close: pd.Series,
    benchmark_close: pd.Series,
    lookback_days: int = 252,
    tolerance_pct: float = 2.0,
) -> tuple[bool, float]:
    """Is the RS line (ticker / benchmark) at or near its trailing 252-day high?

    Stage-2 / O'Neil canonical bullish signal: RS line breaks its own 52w high
    BEFORE price does — the relative-strength leadership is confirmed by the
    very act of staying strong while broad market chops.

    Returns (is_at_high, distance_pct_from_high):
      - is_at_high = True if RS-line current value within `tolerance_pct` of its
        252-day max.
      - distance_pct is signed: 0.0 at exact high, negative if below (e.g. -3.5
        means RS-line is 3.5% below its 52w high).
    """
    rs = relative_strength_line(ticker_close, benchmark_close).dropna()
    if len(rs) < min(lookback_days, 60):
        return False, float("nan")
    window = rs.tail(lookback_days)
    high = float(window.max())
    if high <= 0:
        return False, float("nan")
    cur = float(window.iloc[-1])
    distance_pct = (cur - high) / high * 100.0
    return distance_pct >= -tolerance_pct, distance_pct


def percentile_of(
    value: float | None,
    population: list[float] | np.ndarray,
    min_population: int = 1,
) -> float | None:
    """Cross-sectional percentile rank (0-100) of `value` within `population`.

    This is the building block for an IBD-style universe-relative RS Rating:
    given every fetched ticker's RS score on a given day, each ticker's rating
    is the % of the universe it outperforms. Returns None if the population is
    empty or `value` is None.

    `min_population` guards against tiny universes: when fewer than `min_population`
    valid (non-NaN) samples are available, the percentile is statistically
    meaningless (a single outlier in a 3-name run lands at the 100th percentile),
    so we return None instead of a misleading rank. Callers treat None as
    "RS rating unavailable" (scorer falls back to the absolute mapping; the
    RS-rating gate is skipped).

    Uses the midpoint convention (counts < plus half of ==) so identical scores
    map to the same rank without biasing to 0 or 100.
    """
    if value is None or population is None:
        return None
    arr = np.asarray([p for p in population if p is not None], dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0 or arr.size < max(1, min_population):
        return None
    below = float(np.sum(arr < value))
    equal = float(np.sum(arr == value))
    rank = (below + 0.5 * equal) / arr.size * 100.0
    return round(max(0.0, min(100.0, rank)), 1)
