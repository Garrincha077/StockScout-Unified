"""Base-quality analysis utilities used by all setup detectors.

These functions identify a *consolidation base* after a prior uptrend, count
the contractions inside it, measure volume dry-up, detect "wide-and-loose"
configurations, and compute extension from a pivot.

Implementations follow the methodology spec in docs/METHODOLOGY.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from stock_scout.indicators.volatility import atr


def sma_stack_conditions(features: dict) -> dict[str, bool]:
    """None-safe daily SMA-stack trend-template flags.

    The canonical O'Neil/Minervini moving-average stack
    (`close > sma50/150/200`, `sma50 > sma150 > sma200`, plus a rising SMA200)
    was computed independently in both the Minervini detector and the scorer's
    trend component. This single source keeps them in lock-step. A flag is False
    whenever either operand is missing (no zero-default surprises).

    Returns the seven flags in a stable order; callers may select a subset.
    """

    def gt(a: float | None, b: float | None) -> bool:
        return bool(a is not None and b is not None and a > b)

    close = features.get("close")
    sma50 = features.get("sma50")
    sma150 = features.get("sma150")
    sma200 = features.get("sma200")
    return {
        "close>sma50": gt(close, sma50),
        "close>sma150": gt(close, sma150),
        "close>sma200": gt(close, sma200),
        "sma50>sma150": gt(sma50, sma150),
        "sma150>sma200": gt(sma150, sma200),
        "sma50>sma200": gt(sma50, sma200),
        "sma200_rising": bool(features.get("sma200_rising")),
    }


@dataclass
class SwingPoint:
    idx: int           # positional index into the source frame
    date: date
    price: float
    kind: str          # "high" or "low"


@dataclass
class Contraction:
    start_idx: int
    end_idx: int
    high: float
    low: float

    @property
    def width_pct(self) -> float:
        if self.high <= 0:
            return 0.0
        return (self.high - self.low) / self.high * 100.0

    @property
    def length_bars(self) -> int:
        return self.end_idx - self.start_idx


@dataclass
class BaseDescriptor:
    start_idx: int                   # inclusive
    end_idx: int                     # inclusive (= last bar of the frame)
    high_at_start: float             # the peak that kicked off the base
    low_in_base: float
    high_in_base: float
    pivot_price: float               # last swing high in the base
    pivot_idx: int
    length_bars: int
    depth_pct: float
    contractions: list[Contraction] = field(default_factory=list)
    swing_highs: list[SwingPoint] = field(default_factory=list)
    swing_lows: list[SwingPoint] = field(default_factory=list)
    volume_dryup_pct: float = 0.0    # % of last 20 bars below 0.8x pre-base avg
    is_wide_and_loose: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def n_contractions(self) -> int:
        return len(self.contractions)

    @property
    def contractions_non_increasing(self) -> bool:
        """True if each contraction's width is at most 1.1x the previous one.

        Loose rule used by GLB (Wish-style structural test). For strict
        Minervini-VCP "successively tighter" check use
        :meth:`contractions_tightening_at`.
        """
        if len(self.contractions) < 2:
            return False
        for prev, nxt in zip(self.contractions[:-1], self.contractions[1:]):
            if nxt.width_pct > prev.width_pct * 1.1:
                return False
        return True

    def contractions_tightening_at(self, ratio: float = 0.67) -> bool:
        """True if each successive contraction is <= ``ratio`` × the previous
        one (i.e. visibly tighter). Default 0.67 ≈ Minervini "1/3 smaller".

        Use 1.0 for "non-increasing" semantics; 0.5 for strict knjiški-VCP.
        """
        if len(self.contractions) < 2:
            return False
        if ratio <= 0:
            return False
        for prev, nxt in zip(self.contractions[:-1], self.contractions[1:]):
            if prev.width_pct <= 0:
                continue
            if nxt.width_pct > prev.width_pct * ratio:
                return False
        return True


# ---- Swing detection -------------------------------------------------------


def find_swings(close: pd.Series, left: int = 3, right: int = 3) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """Identify confirmed swing highs and lows in `close`.

    A bar at position i is a swing high if close[i] >= close[i-left:i] and
    close[i] >= close[i+1:i+right+1]. Last `right` bars cannot be confirmed.
    """
    highs: list[SwingPoint] = []
    lows: list[SwingPoint] = []
    n = len(close)
    if n < left + right + 1:
        return highs, lows

    values = close.values
    idx = close.index

    for i in range(left, n - right):
        window_left = values[i - left : i]
        window_right = values[i + 1 : i + right + 1]
        v = values[i]
        if v >= window_left.max() and v >= window_right.max():
            highs.append(SwingPoint(idx=i, date=pd.Timestamp(idx[i]).date(), price=float(v), kind="high"))
        # Use strict-less for lows so a single bar can't be both high and low.
        if v <= window_left.min() and v <= window_right.min() and not (v >= window_left.max() and v >= window_right.max()):
            lows.append(SwingPoint(idx=i, date=pd.Timestamp(idx[i]).date(), price=float(v), kind="low"))

    return highs, lows


def higher_lows_count(close: pd.Series, left: int = 3, right: int = 3, max_lookback: int = 60) -> int:
    """Count the trailing run of ASCENDING confirmed swing lows.

    A constructive Stage-1 / accumulation base prints successively higher lows
    (demand stepping in earlier each pullback). Returns how many of the most
    recent swing lows form a strictly-ascending sequence (0 if <2 swing lows or
    the last two aren't ascending). Reuses :func:`find_swings`.
    """
    if close is None or len(close) < left + right + 2:
        return 0
    window = close.iloc[-max_lookback:] if max_lookback and len(close) > max_lookback else close
    _, lows = find_swings(window, left=left, right=right)
    if len(lows) < 2:
        return 0
    # `lows` is chronological (oldest first). Walk newest→oldest, counting the
    # trailing run where each newer swing low sits above the previous one.
    run = 1
    for i in range(len(lows) - 1, 0, -1):
        if lows[i].price > lows[i - 1].price:
            run += 1
        else:
            break
    return run if run >= 2 else 0


# ---- Base detection --------------------------------------------------------


def find_consolidation_base(
    df: pd.DataFrame,
    *,
    min_length_bars: int = 21,
    max_depth_pct: float = 35.0,
    swing_left: int = 3,
    swing_right: int = 3,
) -> BaseDescriptor | None:
    """Return the most recent consolidation base, or None if no valid base.

    A base starts at the most recent significant swing high before today, such
    that the maximum drawdown since that high is <= `max_depth_pct` and the
    base length is >= `min_length_bars`.
    """
    if df is None or df.empty or len(df) < min_length_bars + swing_left + swing_right:
        return None

    close = df["close"]
    high_series = df["high"] if "high" in df.columns else close
    low_series = df["low"] if "low" in df.columns else close

    highs, lows = find_swings(close, left=swing_left, right=swing_right)
    if not highs:
        return None

    last_idx = len(df) - 1
    # Walk swing highs from newest to oldest; the first that anchors a valid
    # base wins.
    for sh in reversed(highs):
        if last_idx - sh.idx < min_length_bars:
            continue
        # Drawdown from the swing high to the deepest low after it
        window_low = low_series.iloc[sh.idx : last_idx + 1].min()
        window_high = high_series.iloc[sh.idx : last_idx + 1].max()
        depth_pct = (window_high - window_low) / max(1e-9, window_high) * 100.0
        if depth_pct > max_depth_pct:
            # Try the previous (older) swing high instead — maybe this is just
            # a noisy local high inside a bigger base.
            continue

        # Find the pivot = last swing high in [sh.idx, last_idx]
        in_base_highs = [h for h in highs if sh.idx <= h.idx <= last_idx]
        pivot = in_base_highs[-1]

        # Volume dry-up over last 20 bars vs pre-base 50-bar avg
        vol_dryup = 0.0
        if "volume" in df.columns:
            pre_start = max(0, sh.idx - 50)
            pre_vol = df["volume"].iloc[pre_start : sh.idx]
            if len(pre_vol) >= 10:
                pre_avg = float(pre_vol.mean())
                if pre_avg > 0:
                    recent = df["volume"].iloc[max(0, last_idx - 19) : last_idx + 1]
                    below = (recent < 0.8 * pre_avg).sum()
                    vol_dryup = float(below) / max(1, len(recent)) * 100.0

        in_base_lows = [lo for lo in lows if sh.idx <= lo.idx <= last_idx]

        # Build contractions: rolling windows between successive swing highs.
        contractions: list[Contraction] = []
        if len(in_base_highs) >= 2:
            for a, b in zip(in_base_highs[:-1], in_base_highs[1:]):
                segment_high = float(high_series.iloc[a.idx : b.idx + 1].max())
                segment_low = float(low_series.iloc[a.idx : b.idx + 1].min())
                contractions.append(
                    Contraction(start_idx=a.idx, end_idx=b.idx, high=segment_high, low=segment_low)
                )

        wide_loose = depth_pct > 35.0
        if len(contractions) >= 2:
            # No successive narrowing -> also wide-and-loose
            any_narrowing = any(
                nxt.width_pct < prev.width_pct
                for prev, nxt in zip(contractions[:-1], contractions[1:])
            )
            if not any_narrowing:
                wide_loose = True

        return BaseDescriptor(
            start_idx=sh.idx,
            end_idx=last_idx,
            high_at_start=float(window_high),
            low_in_base=float(window_low),
            high_in_base=float(window_high),
            pivot_price=float(pivot.price),
            pivot_idx=pivot.idx,
            length_bars=last_idx - sh.idx,
            depth_pct=float(depth_pct),
            contractions=contractions,
            swing_highs=in_base_highs,
            swing_lows=in_base_lows,
            volume_dryup_pct=float(vol_dryup),
            is_wide_and_loose=wide_loose,
        )

    return None


# ---- Extension -------------------------------------------------------------


def extension_from_pivot(
    close: float,
    pivot_price: float,
    atr20_value: float | None = None,
) -> tuple[float, float | None]:
    """Return (pct_extension, atr_extension_or_None).

    pct_extension: (close - pivot) / pivot * 100  (negative = below pivot)
    atr_extension: (close - pivot) / atr20  (None if atr20 missing/zero)
    """
    if pivot_price <= 0:
        return 0.0, None
    pct = (close - pivot_price) / pivot_price * 100.0
    atr_mult: float | None = None
    if atr20_value and atr20_value > 0:
        atr_mult = (close - pivot_price) / atr20_value
    return float(pct), atr_mult


# ---- Convenience: prior uptrend --------------------------------------------


def prior_uptrend_pct(close: pd.Series, base_start_idx: int, lookback: int = 120) -> float:
    """Approximate % gain in the `lookback` bars BEFORE the base started.

    Used to require a real uptrend before declaring a VCP base.
    """
    if base_start_idx <= 0:
        return 0.0
    start = max(0, base_start_idx - lookback)
    if start >= base_start_idx:
        return 0.0
    pre = close.iloc[start:base_start_idx]
    if pre.empty:
        return 0.0
    low_pre = float(pre.min())
    high_pre = float(pre.max())
    if low_pre <= 0:
        return 0.0
    return (high_pre - low_pre) / low_pre * 100.0


def atr20_at_last_bar(df: pd.DataFrame) -> float | None:
    """Convenience helper: return the most recent ATR(20) value or None."""
    if df is None or df.empty or len(df) < 21:
        return None
    s = atr(df, 20)
    if s.empty:
        return None
    last = s.iloc[-1]
    if pd.isna(last):
        return None
    return float(last)
