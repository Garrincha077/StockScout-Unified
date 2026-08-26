#!/usr/bin/env python3
"""Build the canonical two-layer dataset and attach rich post-market evidence.

LEGACY is the frozen upstream source methodology under ``row.originalEngine``.
STOCKSCOUT is the custom discovery methodology under ``row.stockscout``.
``row.richData`` contains shared evidence derived from the completed scan's 5Y
price cache plus the hydrated fundamental cache. No market-data redownload occurs
in this step and no LEGACY scoring rule is changed.
"""
from __future__ import annotations

import json
import math
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "frontend" / "public" / "data" / "latest.json"
PRICE_CACHE = ROOT / "data" / "batch_results" / "price_history_5y.pkl"
FUND_DIR = ROOT / "data" / "fundamentals_cache"
LEGACY_MANIFEST = ROOT / "config" / "legacy_baseline.json"
MODEL = "rich-scan-layers-v1"


def finite(value: Any, default: float | None = None) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def rounded(value: Any, digits: int = 2) -> float | None:
    value = finite(value)
    return round(value, digits) if value is not None else None


def clean_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        value = float(value)
        return round(value, 6) if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, (str, int, bool)):
        return value
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def pct_change(series: pd.Series, periods: int) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) <= periods:
        return None
    old, now = finite(s.iloc[-periods - 1]), finite(s.iloc[-1])
    if old in (None, 0) or now is None:
        return None
    return (now / old - 1.0) * 100.0


def long_horizon_change(series: pd.Series, target_periods: int, min_periods: int) -> float | None:
    """Return long-horizon change without losing 5Y data to holiday-count variance."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) <= min_periods:
        return None
    periods = min(target_periods, len(s) - 1)
    old, now = finite(s.iloc[-periods - 1]), finite(s.iloc[-1])
    if old in (None, 0) or now is None:
        return None
    return (now / old - 1.0) * 100.0


def distance(value: float | None, anchor: float | None) -> float | None:
    if value is None or anchor in (None, 0):
        return None
    return (value / anchor - 1.0) * 100.0


def annualized_volatility(close: pd.Series, periods: int) -> float | None:
    returns = pd.to_numeric(close, errors="coerce").pct_change().dropna().tail(periods)
    if len(returns) < min(10, periods // 2):
        return None
    return float(returns.std(ddof=0) * math.sqrt(252) * 100.0)


def slope_pct(series: pd.Series, periods: int) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna().tail(periods)
    if len(s) < 3:
        return None
    avg = finite(s.mean())
    if avg in (None, 0):
        return None
    x = np.arange(len(s), dtype=float)
    return float(np.polyfit(x, s.to_numpy(dtype=float), 1)[0] / avg * 100.0)


def atr(frame: pd.DataFrame, periods: int) -> float | None:
    if not {"High", "Low", "Close"}.issubset(frame.columns):
        return None
    high = pd.to_numeric(frame["High"], errors="coerce")
    low = pd.to_numeric(frame["Low"], errors="coerce")
    close = pd.to_numeric(frame["Close"], errors="coerce")
    prev = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return finite(tr.dropna().tail(periods).mean())


def max_drawdown(close: pd.Series, periods: int) -> float | None:
    s = pd.to_numeric(close, errors="coerce").dropna().tail(periods)
    if len(s) < 2:
        return None
    drawdown = s / s.cummax() - 1.0
    return float(drawdown.min() * 100.0)


def up_down_volume_ratio(frame: pd.DataFrame, periods: int) -> float | None:
    if not {"Close", "Volume"}.issubset(frame.columns):
        return None
    close = pd.to_numeric(frame["Close"], errors="coerce").tail(periods)
    volume = pd.to_numeric(frame["Volume"], errors="coerce").tail(periods)
    delta = close.diff()
    up = finite(volume[delta > 0].mean())
    down = finite(volume[delta < 0].mean())
    if up is None or down in (None, 0):
        return None
    return up / down


def technical_snapshot(frame: pd.DataFrame, spy: pd.DataFrame | None) -> dict[str, Any]:
    frame = frame.copy()
    if frame.empty or "Close" not in frame.columns:
        return {}
    frame = frame.dropna(subset=["Close"])
    if frame.empty:
        return {}

    close = pd.to_numeric(frame["Close"], errors="coerce")
    high = pd.to_numeric(frame.get("High", close), errors="coerce")
    low = pd.to_numeric(frame.get("Low", close), errors="coerce")
    volume = pd.to_numeric(frame.get("Volume", pd.Series(index=frame.index, dtype=float)), errors="coerce")
    price = finite(close.iloc[-1])
    if price is None:
        return {}

    mas: dict[str, float | None] = {}
    ma_distances: dict[str, float | None] = {}
    for n in (10, 20, 50, 100, 150, 200):
        ma = finite(close.tail(n).mean()) if len(close) >= n else None
        mas[f"sma{n}"] = rounded(ma)
        ma_distances[f"distanceSma{n}"] = rounded(distance(price, ma))

    ema21 = finite(close.ewm(span=21, adjust=False).mean().iloc[-1]) if len(close) >= 21 else None
    a14, a20 = atr(frame, 14), atr(frame, 20)
    avg_vol5 = finite(volume.tail(5).mean())
    avg_vol20 = finite(volume.tail(20).mean())
    avg_vol50 = finite(volume.tail(50).mean())
    today_vol = finite(volume.iloc[-1]) if len(volume) else None
    avg_dollar20 = avg_vol20 * price if avg_vol20 is not None else None
    avg_dollar50 = avg_vol50 * price if avg_vol50 is not None else None

    high20, high63 = finite(high.tail(20).max()), finite(high.tail(63).max())
    high126, high252, high504 = finite(high.tail(126).max()), finite(high.tail(252).max()), finite(high.tail(504).max())
    low20, low63 = finite(low.tail(20).min()), finite(low.tail(63).min())
    low126, low252 = finite(low.tail(126).min()), finite(low.tail(252).min())

    relative: dict[str, Any] = {}
    if isinstance(spy, pd.DataFrame) and not spy.empty and "Close" in spy.columns:
        joined = pd.concat([
            close.rename("stock"),
            pd.to_numeric(spy["Close"], errors="coerce").rename("spy"),
        ], axis=1).dropna()
        if len(joined) > 20:
            rs = joined["stock"] / joined["spy"] * 100.0
            relative = {
                "rs1m": rounded(pct_change(rs, 20)),
                "rs3m": rounded(pct_change(rs, 63)),
                "rs6m": rounded(pct_change(rs, 126)),
                "rs12m": rounded(pct_change(rs, 252)),
                "rsSlope20": rounded(slope_pct(rs, 20), 4),
                "rsSlope60": rounded(slope_pct(rs, 60), 4),
                "rsFrom52wHigh": rounded(distance(finite(rs.iloc[-1]), finite(rs.tail(252).max()))),
            }

    last_date = frame.index[-1]
    last_date = last_date.date().isoformat() if isinstance(last_date, pd.Timestamp) else str(last_date)[:10]

    return clean_json({
        "price": rounded(price),
        "historyBars": int(len(frame)),
        "lastBarDate": last_date,
        "returns": {
            "1w": rounded(pct_change(close, 5)),
            "1m": rounded(pct_change(close, 20)),
            "3m": rounded(pct_change(close, 63)),
            "6m": rounded(pct_change(close, 126)),
            "1y": rounded(pct_change(close, 252)),
            "2y": rounded(long_horizon_change(close, 504, 450)),
            "3y": rounded(long_horizon_change(close, 756, 700)),
            "5y": rounded(long_horizon_change(close, 1260, 1100)),
        },
        "movingAverages": {
            **mas,
            "ema21": rounded(ema21),
            **ma_distances,
            "distanceEma21": rounded(distance(price, ema21)),
        },
        "trend": {
            "closeSlope20": rounded(slope_pct(close, 20), 4),
            "closeSlope60": rounded(slope_pct(close, 60), 4),
            "sma50Slope20": rounded(slope_pct(close.rolling(50).mean(), 20), 4),
            "sma150Slope20": rounded(slope_pct(close.rolling(150).mean(), 20), 4),
            "sma200Slope20": rounded(slope_pct(close.rolling(200).mean(), 20), 4),
        },
        "range": {
            "from20dHigh": rounded(distance(price, high20)),
            "from3mHigh": rounded(distance(price, high63)),
            "from6mHigh": rounded(distance(price, high126)),
            "from52wHigh": rounded(distance(price, high252)),
            "from2yHigh": rounded(distance(price, high504)),
            "from20dLow": rounded(distance(price, low20)),
            "from3mLow": rounded(distance(price, low63)),
            "from6mLow": rounded(distance(price, low126)),
            "from52wLow": rounded(distance(price, low252)),
            "range20Pct": rounded(distance(high20, low20)) if low20 else None,
            "range63Pct": rounded(distance(high63, low63)) if low63 else None,
            "range126Pct": rounded(distance(high126, low126)) if low126 else None,
            "maxDrawdown1y": rounded(max_drawdown(close, 252)),
            "maxDrawdown2y": rounded(max_drawdown(close, 504)),
        },
        "volatility": {
            "atr14": rounded(a14),
            "atr14Pct": rounded(a14 / price * 100.0) if a14 is not None else None,
            "atr20": rounded(a20),
            "atr20Pct": rounded(a20 / price * 100.0) if a20 is not None else None,
            "realized20Pct": rounded(annualized_volatility(close, 20)),
            "realized60Pct": rounded(annualized_volatility(close, 60)),
        },
        "volume": {
            "today": int(today_vol) if today_vol is not None else None,
            "avg20": int(avg_vol20) if avg_vol20 is not None else None,
            "avg50": int(avg_vol50) if avg_vol50 is not None else None,
            "todayVs20": rounded(today_vol / avg_vol20) if today_vol is not None and avg_vol20 else None,
            "avg5Vs50": rounded(avg_vol5 / avg_vol50) if avg_vol5 is not None and avg_vol50 else None,
            "avgDollar20": int(avg_dollar20) if avg_dollar20 is not None else None,
            "avgDollar50": int(avg_dollar50) if avg_dollar50 is not None else None,
            "upDown20": rounded(up_down_volume_ratio(frame, 20)),
            "upDown50": rounded(up_down_volume_ratio(frame, 50)),
        },
        "relativeStrength": relative,
    })


def read_fundamentals(ticker: str) -> tuple[dict[str, Any], str | None]:
    path = FUND_DIR / f"{ticker}_fundamentals.json"
    if not path.exists():
        return {}, None
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        data = wrapper.get("data") if isinstance(wrapper, dict) else {}
        fetched = wrapper.get("fetched_at") if isinstance(wrapper, dict) else None
        return clean_json(data or {}), fetched
    except Exception:
        return {}, None


def age_days(value: str | None) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() // 86400))
    except Exception:
        return None


def stockscout_projection(row: dict[str, Any]) -> dict[str, Any]:
    return clean_json({
        "model": "stockscout-discovery-v1",
        "primarySetup": row.get("primarySetup"),
        "setupTags": row.get("setupTags", []),
        "opportunityScore": row.get("opportunityScore"),
        "confluence": row.get("confluence"),
        "scores": {
            "structure": row.get("structureScore"),
            "relativeStrength": row.get("rsScore"),
            "base": row.get("baseScore"),
            "trigger": row.get("triggerScore"),
            "freshness": row.get("freshnessScore"),
            "neglected": row.get("neglectedScore"),
        },
        "stage": row.get("stage"),
        "stageName": row.get("stageName"),
        "stage2AgeWeeks": row.get("stage2AgeWeeks"),
        "rsRank": row.get("rsRank"),
        "rsAcceleration": row.get("rsAcceleration"),
        "trendTemplatePasses": row.get("trendTemplatePasses"),
        "extended": row.get("extended"),
        "group": {
            "sector": row.get("sectorProxy") or row.get("sector"),
            "industry": row.get("industryProxy") or row.get("industry"),
            "sectorRank": row.get("sectorRank"),
            "industryRank": row.get("industryRank"),
            "groupLeadership": row.get("groupLeadership"),
            "leadershipScore": row.get("leadershipScore"),
        },
        "change": {
            "changedToday": row.get("changedToday"),
            "impact": row.get("changeImpact"),
            "labels": row.get("changeLabels", []),
            "newSetupTags": row.get("newSetupTags", []),
            "lostSetupTags": row.get("lostSetupTags", []),
        },
    })


def flatten_rich(row: dict[str, Any], technical: dict[str, Any], fundamentals: dict[str, Any], fund_age: int | None) -> None:
    ret = technical.get("returns", {})
    ma = technical.get("movingAverages", {})
    vola = technical.get("volatility", {})
    vol = technical.get("volume", {})
    rs = technical.get("relativeStrength", {})
    rng = technical.get("range", {})
    flat = {
        "return1w": ret.get("1w"),
        "return1m": ret.get("1m"),
        "return2y": ret.get("2y"),
        "return3y": ret.get("3y"),
        "return5y": ret.get("5y"),
        "distance20": ma.get("distanceSma20"),
        "distance100": ma.get("distanceSma100"),
        "distance150": ma.get("distanceSma150"),
        "atr14Pct": vola.get("atr14Pct"),
        "realizedVol20": vola.get("realized20Pct"),
        "realizedVol60": vola.get("realized60Pct"),
        "avgDollarVolume50": vol.get("avgDollar50"),
        "upDownVolume20": vol.get("upDown20"),
        "upDownVolume50": vol.get("upDown50"),
        "rs1m": rs.get("rs1m"),
        "richRs3m": rs.get("rs3m"),
        "richRs6m": rs.get("rs6m"),
        "richRs12m": rs.get("rs12m"),
        "distance2yHigh": rng.get("from2yHigh"),
        "maxDrawdown1y": rng.get("maxDrawdown1y"),
        "revenueQoQ": fundamentals.get("revenue_qoq_change"),
        "epsQoQ": fundamentals.get("eps_qoq_change"),
        "operatingMargin": fundamentals.get("operating_margin"),
        "inventoryQoQ": fundamentals.get("inventory_qoq_change"),
        "inventoryToSales": fundamentals.get("inventory_to_sales_ratio"),
        "fundamentalsAgeDays": fund_age,
    }
    for key, value in flat.items():
        row[key] = clean_json(value)


def main() -> None:
    if not OUT.exists():
        raise SystemExit(f"Missing canonical dataset: {OUT}")
    if not PRICE_CACHE.exists():
        raise SystemExit(f"Missing reusable 5Y price cache: {PRICE_CACHE}")

    payload = json.loads(OUT.read_text(encoding="utf-8"))
    with PRICE_CACHE.open("rb") as fh:
        cache = pickle.load(fh)
    if not isinstance(cache, dict):
        raise SystemExit("5Y price cache is not a ticker->DataFrame mapping")

    legacy = json.loads(LEGACY_MANIFEST.read_text(encoding="utf-8"))
    spy = cache.get("SPY")
    universe = payload.get("universe") or []
    technical_coverage = fundamental_coverage = legacy_coverage = 0

    for row in universe:
        ticker = str(row.get("ticker", "")).upper()
        frame = cache.get(ticker)
        technical: dict[str, Any] = {}
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            technical = technical_snapshot(frame, spy if isinstance(spy, pd.DataFrame) else None)
            if technical:
                technical_coverage += 1

        fundamentals, fetched_at = read_fundamentals(ticker)
        fund_age = age_days(fetched_at)
        if fundamentals:
            fundamental_coverage += 1

        row["richData"] = {
            "model": MODEL,
            "technical": technical,
            "fundamentals": fundamentals,
            "fundamentalsFetchedAt": fetched_at,
            "fundamentalsAgeDays": fund_age,
        }
        row["stockscout"] = stockscout_projection(row)
        if row.get("originalEngine"):
            legacy_coverage += 1
        flatten_rich(row, technical, fundamentals, fund_age)

    payload["layers"] = {
        "legacy": {
            "label": "LEGACY",
            "rowPath": "originalEngine",
            "methodology": "frozen-upstream-source",
            "upstreamRepository": legacy["upstream_repository"],
            "upstreamCommit": legacy["upstream_commit"],
            "protectedFiles": legacy["files"],
            "rule": "No StockScout score, tag, calibration or ranking may alter this layer.",
        },
        "stockscout": {
            "label": "STOCKSCOUT",
            "rowPath": "stockscout",
            "methodology": "custom-discovery",
            "rule": "Discovery layer may evolve while LEGACY remains frozen.",
        },
        "sharedEvidencePath": "richData",
    }
    payload["dataModel"] = MODEL
    market = payload.setdefault("market", {})
    market["dataModel"] = MODEL
    market["richTechnicalCoverage"] = technical_coverage
    market["richFundamentalCoverage"] = fundamental_coverage
    market["legacyLayerCoverage"] = legacy_coverage
    market["stockscoutLayerCoverage"] = len(universe)

    OUT.write_text(json.dumps(clean_json(payload), separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(
        f"Layered rich dataset: technical={technical_coverage:,}/{len(universe):,}, "
        f"fundamentals={fundamental_coverage:,}/{len(universe):,}, "
        f"legacy={legacy_coverage:,}/{len(universe):,}"
    )


if __name__ == "__main__":
    main()
