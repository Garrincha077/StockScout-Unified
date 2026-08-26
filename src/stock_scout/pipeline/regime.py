"""Market regime gate (O'Neil's "M" — market direction).

Computes a coarse market-health snapshot from the major index ETFs (SPY +
QQQ): are they above their 50/200-day SMAs, is 50 above 200, and how many
distribution days have stacked up recently. The result is NOT a hard filter —
per the project's "ranking, not exclusion" philosophy it only:

  * produces a small score multiplier (gentle headwind in weak markets), and
  * fills a banner in the UI so the trader knows the backdrop.

A momentum breakout screen works best in a confirmed uptrend; in a correction
the same setups fail more often, so we lean the rankings — but we never hide
candidates the user might still want to see.
"""

from __future__ import annotations

import pandas as pd

from stock_scout.indicators.guppy import guppy_bottoming, guppy_state
from stock_scout.indicators.moving_averages import sma

BREADTH_MIN_SAMPLE = 50


def compute_market_breadth(samples: list[dict] | None, min_sample: int = BREADTH_MIN_SAMPLE) -> dict:
    """Aggregate universe participation from per-ticker breadth samples."""
    out = {
        "sample_size": 0,
        "pct_above_sma50": None,
        "pct_above_sma200": None,
        "advancers_pct": None,
        "state": "unknown",
        "low_confidence": True,
    }
    if not samples:
        return out

    valid = [
        s for s in samples
        if isinstance(s.get("above_sma50"), bool)
        and isinstance(s.get("above_sma200"), bool)
        and isinstance(s.get("advanced"), bool)
    ]
    n = len(valid)
    out["sample_size"] = n
    if n == 0:
        return out

    pct_above_sma50 = 100.0 * sum(1 for s in valid if s["above_sma50"]) / n
    pct_above_sma200 = 100.0 * sum(1 for s in valid if s["above_sma200"]) / n
    advancers_pct = 100.0 * sum(1 for s in valid if s["advanced"]) / n
    out.update(
        pct_above_sma50=round(pct_above_sma50, 1),
        pct_above_sma200=round(pct_above_sma200, 1),
        advancers_pct=round(advancers_pct, 1),
        low_confidence=n < max(1, min_sample),
    )
    if out["low_confidence"]:
        return out

    if pct_above_sma50 >= 50.0 and pct_above_sma200 >= 45.0:
        out["state"] = "healthy"
    elif pct_above_sma50 >= 35.0 or pct_above_sma200 >= 35.0:
        out["state"] = "mixed"
    else:
        out["state"] = "weak"
    return out


def apply_breadth_to_regime(regime: dict | None, breadth: dict | None) -> dict:
    """Attach market breadth and apply its small ranking headwind."""
    if not regime:
        return {}
    if not breadth:
        return regime

    out = dict(regime)
    out["breadth"] = breadth
    breadth_state = str(breadth.get("state") or "unknown")
    if breadth_state == "unknown":
        return out

    summary = str(out.get("summary") or "")
    if breadth_state == "weak" and out.get("state") == "confirmed_uptrend":
        try:
            current_mult = float(out.get("score_multiplier", 1.0))
        except (TypeError, ValueError):
            current_mult = 1.0
        out["score_multiplier"] = min(current_mult, 0.95)
        out["summary"] = f"{summary} Breadth is weak; leadership is narrow."
    elif breadth_state == "mixed":
        out["summary"] = f"{summary} Breadth is mixed; participation is uneven."
    return out


def _distribution_days(
    df: pd.DataFrame,
    lookback: int = 25,
    drop_pct: float = 0.2,
    rally_expire_pct: float = 5.0,
) -> int:
    """Count distribution days in the last `lookback` sessions: a down day
    (close off >= drop_pct%) on HIGHER volume than the prior session — the
    institutional-selling footprint O'Neil tracks.

    Distribution days are stale once the index rallies materially from that
    day's close. Without this expiry, a strong market near highs can remain
    incorrectly tagged as "under pressure" from old churn.
    """
    if df is None or df.empty or len(df) < 2 or "volume" not in df.columns:
        return 0
    tail = df.tail(lookback + 1)
    close = tail["close"]
    vol = tail["volume"]
    pct_change = close.pct_change() * 100.0
    higher_vol = vol > vol.shift(1)
    dist = (pct_change <= -drop_pct) & higher_vol
    count = 0
    for pos, is_dist in enumerate(dist.iloc[1:], start=1):
        if not bool(is_dist):
            continue
        dist_close = float(close.iloc[pos])
        later_high = float(close.iloc[pos:].max())
        if rally_expire_pct > 0 and later_high >= dist_close * (1.0 + rally_expire_pct / 100.0):
            continue
        count += 1
    return count


def _index_health(df: pd.DataFrame) -> dict:
    """Per-index trend booleans + distribution-day count."""
    out = {
        "above_sma50": None,
        "above_sma200": None,
        "sma50_above_sma200": None,
        "distance_above_sma50_pct": None,
        "distribution_days": 0,
        "available": False,
    }
    if df is None or df.empty or len(df) < 200:
        out["distribution_days"] = _distribution_days(df) if df is not None else 0
        return out
    close = df["close"]
    sma50 = sma(close, 50)
    sma200 = sma(close, 200)
    c = float(close.iloc[-1])
    s50 = float(sma50.iloc[-1])
    s200 = float(sma200.iloc[-1])
    out.update(
        above_sma50=c > s50,
        above_sma200=c > s200,
        sma50_above_sma200=s50 > s200,
        distance_above_sma50_pct=(c - s50) / s50 * 100.0 if s50 else None,
        distribution_days=_distribution_days(df),
        available=True,
    )
    return out


def _sanitize_index(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Drop NaN / non-positive OHLC bars before any regime math.

    A single bad row in the local market store (e.g. a NaN bar from a partial
    fetch) lands inside the rolling-200 window and makes the 200-day SMA NaN.
    `price > NaN` is False, so the index silently reads as "below the 200-day"
    and fakes a market correction even in a clear uptrend. The chart/API path
    already strips these (clean_ohlcv_for_serving); mirror that here so the
    regime can't be poisoned by one bar.
    """
    if df is None or df.empty:
        return df
    cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if not cols:
        return df
    out = df.copy()
    for col in cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out[(out[cols] > 0).all(axis=1)]


def compute_regime(
    spy_daily: pd.DataFrame | None,
    qqq_daily: pd.DataFrame | None = None,
    max_distribution_days: int = 5,
) -> dict:
    """Return a regime snapshot dict.

    state ∈ {confirmed_uptrend, under_pressure, correction, unknown}
    score_multiplier: gentle ranking lean (never excludes).
    """
    spy_daily = _sanitize_index(spy_daily)
    qqq_daily = _sanitize_index(qqq_daily)
    spy = _index_health(spy_daily)
    qqq = _index_health(qqq_daily) if qqq_daily is not None else None

    healths = [h for h in (spy, qqq) if h is not None and h["available"]]
    if not healths:
        return {
            "state": "unknown",
            "score_multiplier": 1.0,
            "spy": spy,
            "qqq": qqq,
            "summary": "Market regime unavailable (insufficient index history).",
        }

    below_200 = any(h["above_sma200"] is False for h in healths)
    death_cross = any(h["sma50_above_sma200"] is False for h in healths)
    below_50 = any(h["above_sma50"] is False for h in healths)
    max_dist = max(h["distribution_days"] for h in healths)
    dist_pressure_count = sum(1 for h in healths if h["distribution_days"] > max_distribution_days)
    near_50_with_distribution = any(
        h["distribution_days"] > max_distribution_days
        and h.get("distance_above_sma50_pct") is not None
        and float(h["distance_above_sma50_pct"]) < 2.0
        for h in healths
    )

    if below_200 or death_cross:
        state = "correction"
        mult = 0.85
    elif below_50 or dist_pressure_count >= 2 or near_50_with_distribution:
        state = "under_pressure"
        mult = 0.92
    else:
        state = "confirmed_uptrend"
        mult = 1.0

    summary = {
        "confirmed_uptrend": "Confirmed uptrend — indices above 50/200 SMA, distribution contained.",
        "under_pressure": "Uptrend under pressure — watch distribution / loss of 50-day.",
        "correction": "Market in correction — indices below 200-day; be selective.",
    }[state]
    if state == "confirmed_uptrend" and max_dist > max_distribution_days:
        summary += " Distribution is elevated but trend remains intact."

    # --- Eric Wish modified-Guppy (RWB/BWR) overlay on SPY ----------------
    # Daily GMMA "sizing up" of the market. In a downtrend, a turning-up short
    # group (bottoming) softens the penalty (ranking, not exclusion).
    guppy = {"state": "unknown", "rlc": 0, "bottoming_watch": False}
    if spy_daily is not None and not spy_daily.empty:
        gs = guppy_state(spy_daily["close"])
        gb = guppy_bottoming(spy_daily["close"])
        guppy = {
            "state": gs["state"],            # RWB | BWR | compression | unknown
            "rlc": gs["rlc"],
            "spread_pct": gs["spread_pct"],
            "bottoming_watch": bool(gb["bottoming"]),
            "bottoming_reason": gb["reason"],
        }

    if state in ("correction", "under_pressure") and guppy["bottoming_watch"]:
        # Don't bottom-fish blindly, but stop punishing as hard once the short
        # group turns up — early reversal odds improve.
        mult = max(mult, 0.90)
        summary += " · potential bottoming (Guppy short group turning up)."
    elif state == "confirmed_uptrend" and guppy["state"] == "RWB":
        summary += " · Guppy RWB confirms."

    return {
        "state": state,
        "score_multiplier": mult,
        "max_distribution_days": max_dist,
        "distribution_pressure_count": dist_pressure_count,
        "spy": spy,
        "qqq": qqq,
        "guppy_state": guppy["state"],
        "rlc": guppy["rlc"],
        "bottoming_watch": guppy["bottoming_watch"],
        "guppy": guppy,
        "summary": summary,
    }
