"""Weekly market-stress indicator (level 0-3).

Combines three weekly macro-fear gauges into a single hierarchical level:

  * RSI(14, weekly) of the market index — oversold confirmation
  * VIX — expected volatility / fear
  * High-Yield OAS spread — credit stress (risky corporate vs treasuries)

The level is the HIGHEST tier whose conditions are met (strict → mild):

  Level 3  Systemic panic     ALL of: RSI<=30, VIX>=40, HY>=8
  Level 2  Cyclical bear      >=2 of: RSI<=33, VIX>=30, HY>=6
  Level 1  Bull-market reset  >=2 of: RSI<=40, VIX>=25, HY>=4.5
  Level 0  Calm               none of the above

Missing inputs (VIX / HY unavailable) are treated as "condition not met" so the
gauge degrades gracefully to whatever data is present.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from stock_scout.indicators.momentum import rsi

LEVEL_LABELS = {
    0: "Calm",
    1: "Bull-market reset",
    2: "Cyclical bear",
    3: "Systemic panic",
}

# Per-level thresholds: (rsi_max, vix_min, hy_min, required_count).
# Level 3 requires ALL three (count == 3); 2 and 1 require any two.
_TIERS = [
    (3, 30.0, 40.0, 8.0, 3),
    (2, 33.0, 30.0, 6.0, 2),
    (1, 40.0, 25.0, 4.5, 2),
]


def weekly_rsi(weekly_close: pd.Series, window: int = 14) -> float | None:
    """Latest weekly RSI value, or None if insufficient history."""
    if weekly_close is None or len(weekly_close.dropna()) < window + 1:
        return None
    val = rsi(weekly_close.dropna(), window).iloc[-1]
    return None if pd.isna(val) else float(val)


def compute_market_stress(
    weekly_close: pd.Series | None,
    vix: float | None = None,
    hy_spread: float | None = None,
    rsi_window: int = 14,
) -> dict:
    """Return the market-stress snapshot dict.

    level ∈ {0,1,2,3}; label; the three component readings; and which
    conditions each tier counted as met.
    """
    rsi_val = weekly_rsi(weekly_close, rsi_window)

    level = 0
    met_at_level: list[str] = []
    for lvl, rsi_max, vix_min, hy_min, required in _TIERS:
        conds = []
        if rsi_val is not None and rsi_val <= rsi_max:
            conds.append(f"rsi<={rsi_max:g}")
        if vix is not None and vix >= vix_min:
            conds.append(f"vix>={vix_min:g}")
        if hy_spread is not None and hy_spread >= hy_min:
            conds.append(f"hy>={hy_min:g}")
        if len(conds) >= required:
            level = lvl
            met_at_level = conds
            break  # tiers are ordered strictest-first → first match wins

    return {
        "level": level,
        "label": LEVEL_LABELS[level],
        "weekly_rsi": round(rsi_val, 1) if rsi_val is not None else None,
        "vix": round(vix, 1) if vix is not None else None,
        "hy_spread": round(hy_spread, 2) if hy_spread is not None else None,
        "conditions_met": met_at_level,
    }


# ---------------------------------------------------------------------------
# Best-effort external data fetchers (never fatal)
# ---------------------------------------------------------------------------


def realized_vol_proxy(settings, window: int = 21) -> float | None:
    """Annualized realized volatility of SPY, as a stand-in for the VIX.

    The Stooq archive carries no VIX series, and the leveraged VIX-futures ETFs
    that do exist are roll-decayed and not comparable to the 25/30/40 tiers
    above. Realized vol is a different quantity from implied vol — it typically
    runs below the VIX outside stress — but it moves with the same regime and
    keeps the stress read alive when the network call fails. Flagged in the
    output so it is never mistaken for the real thing.
    """
    try:
        from stock_scout.data.market_store import MarketDataStore

        store = MarketDataStore.from_settings(settings, read_only=True, lock_timeout_seconds=10.0)
        try:
            df = store.read_ohlcv("SPY", limit=window + 5)
        finally:
            store.close()
        if df is None or len(df) < window + 1:
            return None
        returns = np.log(df["close"]).diff().dropna().tail(window)
        if len(returns) < window:
            return None
        return float(returns.std(ddof=1) * math.sqrt(252) * 100.0)
    except Exception:  # noqa: BLE001 - the stress read must never break a scan
        return None


def fetch_vix(settings) -> tuple[float | None, str]:
    """Latest VIX close via yfinance (^VIX), falling back to realized vol.

    Returns (value, source) where source is "vix", "realized_proxy", or "none".
    """
    try:
        from stock_scout.data.factory import build_provider
        from stock_scout.utils.dates import history_start, last_trading_day

        prov = build_provider("yfinance", settings)
        end = last_trading_day()
        start = history_start(1, end)
        # yfinance accepts the caret index symbol directly.
        df = prov.get_daily_ohlcv("^VIX", start, end)
        if df is not None and not df.empty:
            return float(df["close"].iloc[-1]), "vix"
    except Exception:  # noqa: BLE001
        pass
    proxy = realized_vol_proxy(settings)
    return (proxy, "realized_proxy") if proxy is not None else (None, "none")


_HY_SERIES = "BAMLH0A0HYM2"  # BofA US High Yield OAS (percent)


def fetch_hy_spread(api_key: str | None = None) -> float | None:
    """Latest BofA US High Yield OAS (FRED series BAMLH0A0HYM2), in percent.

    If `api_key` (FRED_API_KEY) is provided, uses the official FRED API JSON
    endpoint (most reliable). Otherwise falls back to FRED's public, keyless
    CSV endpoint. If `api_key` is None it is read from the environment
    (FRED_API_KEY). Returns None on any failure.
    """
    import urllib.request

    if api_key is None:
        try:
            from stock_scout.config.loader import load_env

            api_key = (load_env().FRED_API_KEY or "").strip() or None
        except Exception:  # noqa: BLE001
            api_key = None

    # 1) Official FRED API (requires a free key).
    if api_key:
        try:
            import json as _json

            url = (
                "https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={_HY_SERIES}&api_key={api_key}&file_type=json"
                "&sort_order=desc&limit=1"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "stock-scout"})
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                data = _json.loads(resp.read().decode("utf-8", errors="replace"))
            obs = data.get("observations") or []
            for o in obs:
                raw = (o.get("value") or "").strip()
                if raw and raw != ".":
                    return float(raw)
        except Exception:  # noqa: BLE001
            pass  # fall through to keyless CSV

    # 2) Keyless public CSV fallback.
    try:
        import csv
        import io

        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={_HY_SERIES}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            text = resp.read().decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        last_val: float | None = None
        for row in reader:
            raw = (row.get(_HY_SERIES) or "").strip()
            if raw and raw != ".":
                try:
                    last_val = float(raw)
                except ValueError:
                    continue
        return last_val
    except Exception:  # noqa: BLE001
        return None
