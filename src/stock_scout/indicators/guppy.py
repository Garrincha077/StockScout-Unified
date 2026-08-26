"""Guppy Multiple Moving Averages (GMMA) — Eric Wish "RWB / BWR" interpretation.

Daryl Guppy's GMMA plots two fanned groups of EMAs; Eric Wish (Wishing Wealth)
colours them RED (short-term traders) and BLUE (long-term investors) with a
WHITE gap between, and reads the picture to "size up" a market or stock:

  * short group: EMA 3, 5, 8, 10, 12, 15
  * long  group: EMA 30, 35, 40, 45, 50, 60

  RWB  — short (red) entirely ABOVE long (blue) with white space → uptrend
  BWR  — short entirely BELOW long → downtrend
  compression — the two groups overlap / white space gone → transition / warning

RLC ("Red Line Count", 0-6) = how many of the six short EMAs the close is above.
A reversal from a downtrend (bottoming) shows as the short group turning up and
compressing into / crossing the long group while RLC climbs 0 → 6.

All functions operate on a close-price Series and have no lookahead.
"""

from __future__ import annotations

import pandas as pd

from stock_scout.indicators.moving_averages import ema

SHORT_PERIODS = (3, 5, 8, 10, 12, 15)
LONG_PERIODS = (30, 35, 40, 45, 50, 60)


def guppy_groups(close: pd.Series) -> tuple[list[float], list[float]]:
    """Return (short_emas, long_emas) evaluated at the LAST bar. Values that
    can't be computed (insufficient history) are dropped from the lists."""
    short = []
    long = []
    for p in SHORT_PERIODS:
        v = ema(close, p).iloc[-1]
        if pd.notna(v):
            short.append(float(v))
    for p in LONG_PERIODS:
        v = ema(close, p).iloc[-1]
        if pd.notna(v):
            long.append(float(v))
    return short, long


def guppy_emas(close: pd.Series) -> pd.DataFrame:
    """All twelve GMMA EMAs, one column per period, computed once."""
    return pd.DataFrame(
        {f"ema{p}": ema(close, p) for p in (*SHORT_PERIODS, *LONG_PERIODS)}, index=close.index
    )


def guppy_state_at(
    emas: pd.DataFrame, close: pd.Series, idx: int = -1, slope_lookback: int = 5
) -> dict:
    """`guppy_state` from precomputed EMA columns, evaluated at bar `idx`.

    Reading a full-series EMA at position `idx` is *identical* to computing the
    EMA over the prefix `close[:idx+1]` and taking its last value — `ema` uses
    `ewm(adjust=False)`, which is causal. That equality is what makes this an
    exact refactor rather than an approximation, and it is worth the note
    because the saving is large: callers were recomputing twelve full-series
    EMAs per candidate bar, and the profiler found 455 `ema()` calls per ticker.
    """
    out = {
        "state": "unknown",
        "rlc": 0,
        "short_rising": None,
        "long_rising": None,
        "spread_pct": None,
    }
    if close is None or close.empty or emas is None or emas.empty:
        return out
    n = len(close)
    pos = idx if idx >= 0 else n + idx
    if pos < 0 or pos >= n:
        return out

    row = emas.iloc[pos]
    short = [float(row[f"ema{p}"]) for p in SHORT_PERIODS if pd.notna(row[f"ema{p}"])]
    long = [float(row[f"ema{p}"]) for p in LONG_PERIODS if pd.notna(row[f"ema{p}"])]
    if len(short) < len(SHORT_PERIODS) or len(long) < len(LONG_PERIODS):
        return out

    last_close = float(close.iloc[pos])
    out["rlc"] = sum(1 for p in SHORT_PERIODS if last_close > float(row[f"ema{p}"]))

    short_min, short_max = min(short), max(short)
    long_min, long_max = min(long), max(long)
    avg_short = sum(short) / len(short)
    avg_long = sum(long) / len(long)
    out["spread_pct"] = round((avg_short - avg_long) / avg_long * 100.0, 2) if avg_long else None

    if short_min > long_max:
        out["state"] = "RWB"
    elif short_max < long_min:
        out["state"] = "BWR"
    else:
        out["state"] = "compression"

    # Group slope: a representative EMA from each group (shortest short, a mid long).
    fast = emas[f"ema{SHORT_PERIODS[0]}"]   # EMA3
    slow = emas[f"ema{LONG_PERIODS[2]}"]    # EMA40 (group anchor)
    prev = pos - slope_lookback
    if prev >= 0:
        if pd.notna(fast.iloc[pos]) and pd.notna(fast.iloc[prev]):
            out["short_rising"] = bool(fast.iloc[pos] > fast.iloc[prev])
        if pd.notna(slow.iloc[pos]) and pd.notna(slow.iloc[prev]):
            out["long_rising"] = bool(slow.iloc[pos] > slow.iloc[prev])
    return out


def guppy_state(close: pd.Series, slope_lookback: int = 5) -> dict:
    """Classify the GMMA picture at the last bar.

    Returns a dict:
      state          'RWB' | 'BWR' | 'compression' | 'unknown'
      rlc            int 0-6 — how many short EMAs the close sits above
      short_rising   bool — short group's anchor EMA is higher than `slope_lookback` bars ago
      long_rising    bool — long group's anchor EMA is rising
      spread_pct     (avg_short - avg_long) / avg_long * 100  (signed separation)
    """
    if close is None or close.empty:
        return {
            "state": "unknown",
            "rlc": 0,
            "short_rising": None,
            "long_rising": None,
            "spread_pct": None,
        }
    return guppy_state_at(guppy_emas(close), close, -1, slope_lookback)


def guppy_bottoming(close: pd.Series, slope_lookback: int = 5) -> dict:
    """Detect a potential bottoming / downtrend-to-uptrend transition.

    A bottoming setup (Eric Wish): the market/stock is in a BWR downtrend or
    compression, BUT the short (red) group has turned UP and is climbing into /
    through the long (blue) group while RLC rises toward 6. This is the earliest
    "the tide may be turning" tell — informative, not a confirmed uptrend.

    Returns {bottoming: bool, reason: str, rlc: int}.
    """
    st = guppy_state(close, slope_lookback)
    rlc = st["rlc"]
    if st["state"] in ("BWR", "compression") and st["short_rising"] and rlc >= 3:
        return {
            "bottoming": True,
            "reason": f"{st['state']}_short_group_turning_up_rlc{rlc}",
            "rlc": rlc,
        }
    return {"bottoming": False, "reason": st["state"], "rlc": rlc}
