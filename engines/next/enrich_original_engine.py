#!/usr/bin/env python3
"""Attach the repository's ORIGINAL signal-engine outputs to latest.json.

This script does not invent new trading rules and does not download market data.
It reuses the completed scan analyses from batch_progress.pkl and calls the same
functions used by run_optimized_scan.py:

- score_buy_signal / score_sell_signal
- validate_minervini_trend_template
- detect_breakout
- the existing VCP detector output already stored on each analysis
- analyze_spy_trend / calculate_market_breadth / should_generate_signals

The custom StockScout Opportunity model remains a discovery layer. Fields written
by this script are explicitly prefixed ``original`` or nested under
``originalEngine`` so source methodology and custom discovery logic stay separate.
"""
from __future__ import annotations

import json
import math
import pickle
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.screening.benchmark import (
    analyze_spy_trend,
    calculate_market_breadth,
    should_generate_signals,
)
from src.screening.phase_indicators import (
    calculate_sma,
    detect_breakout,
    validate_minervini_trend_template,
)
from src.screening.signal_engine import score_buy_signal, score_sell_signal

ROOT = Path(__file__).resolve().parent
PROGRESS = ROOT / "data" / "batch_results" / "batch_progress.pkl"
PRICE_CACHE = ROOT / "data" / "batch_results" / "price_history_5y.pkl"
REPORT = ROOT / "data" / "daily_scans" / "latest_optimized_scan.txt"
OUT = ROOT / "frontend" / "public" / "data" / "latest.json"
MODEL = "original-signal-engine-v1"


def native(value: Any) -> Any:
    """Convert numpy/pandas values to compact JSON-safe Python values."""
    if value is None:
        return None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        v = float(value)
        return round(v, 6) if math.isfinite(v) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(v) for v in value]
    if isinstance(value, (str, int, bool)):
        return value
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def compact_contractions(vcp: dict) -> list[dict]:
    rows = []
    for item in (vcp.get("contractions") or [])[-6:]:
        rows.append(
            {
                "number": item.get("number"),
                "peakDate": native(item.get("peak_date")),
                "troughDate": native(item.get("trough_date")),
                "peakPrice": native(item.get("peak_price")),
                "troughPrice": native(item.get("trough_price")),
                "drawdownPct": native(item.get("drawdown_pct")),
                "volumeRatio": native(item.get("volume_ratio")),
                "durationDays": native(item.get("duration_days")),
            }
        )
    return rows


def _spy_from_report() -> dict[str, Any] | None:
    """Recover the SPY phase recorded by the completed original scan.

    Recovery artifacts intentionally do not carry the large 5Y price cache. The
    text report does carry the benchmark classification produced during that run,
    so using it is more faithful than fetching/reclassifying SPY later.
    """
    if not REPORT.exists():
        return None
    try:
        text = REPORT.read_text(encoding="utf-8", errors="ignore")
        match = re.search(
            r"SPY Trend Classification:\s*.*?Phase:\s*(\d+)\s*-\s*([^\n]+)",
            text,
            re.I | re.S,
        )
        if not match:
            return None
        phase = int(match.group(1))
        phase_name = match.group(2).strip()
        trend = {1: "Consolidating", 2: "Bullish", 3: "Topping", 4: "Bearish"}.get(phase, "Unknown")
        return {
            "ticker": "SPY",
            "phase": phase,
            "phase_name": phase_name,
            "trend": trend,
            "confidence": None,
            "source": "completed-scan-report",
        }
    except Exception:
        return None


def market_gate(analyses: list[dict]) -> dict:
    phase_results = [a.get("phase_info", {}) for a in analyses if a.get("phase_info")]
    breadth = calculate_market_breadth(phase_results)

    spy_analysis: dict[str, Any] = {
        "phase": 0,
        "phase_name": "Unknown",
        "trend": "Unknown",
        "confidence": 0,
        "source": "unavailable",
    }
    if PRICE_CACHE.exists():
        try:
            with PRICE_CACHE.open("rb") as fh:
                cache = pickle.load(fh)
            spy = cache.get("SPY") if isinstance(cache, dict) else None
            if isinstance(spy, pd.DataFrame) and not spy.empty and "Close" in spy:
                spy = spy.tail(252).copy()
                spy_analysis = analyze_spy_trend(spy, float(spy["Close"].iloc[-1]))
                spy_analysis["source"] = "scan-price-cache"
        except Exception as exc:
            print(f"Original engine: SPY cache unavailable for market gate ({exc})")

    if int(spy_analysis.get("phase", 0) or 0) == 0:
        report_spy = _spy_from_report()
        if report_spy:
            spy_analysis = report_spy

    gate = should_generate_signals(spy_analysis, breadth)
    return {
        "spy": native(spy_analysis),
        "breadth": native(breadth),
        "gate": native(gate),
    }


def enrich_row(analysis: dict, row: dict, should_buy_market: bool) -> None:
    ticker = str(analysis.get("ticker", ""))
    price_data = analysis.get("price_data")
    current_price = finite(analysis.get("current_price"))
    phase_info = analysis.get("phase_info", {}) or {}
    rs_series = analysis.get("rs_series")
    quarterly = analysis.get("quarterly_data", {}) or {}
    vcp = analysis.get("vcp_data", {}) or {}

    if not isinstance(price_data, pd.DataFrame) or price_data.empty:
        return
    if not isinstance(rs_series, pd.Series):
        rs_series = pd.Series(index=price_data.index, dtype=float)

    phase = int(phase_info.get("phase", 0) or 0)

    # Run exactly the same original signal functions used in run_optimized_scan.py.
    buy = score_buy_signal(
        ticker=ticker,
        price_data=price_data,
        current_price=current_price,
        phase_info=phase_info,
        rs_series=rs_series,
        fundamentals=quarterly,
        vcp_data=vcp,
    )
    sell = score_sell_signal(
        ticker=ticker,
        price_data=price_data,
        current_price=current_price,
        phase_info=phase_info,
        rs_series=rs_series,
        fundamentals=quarterly,
    )

    # Expose original Minervini checklist on every stock. score_buy_signal only
    # reaches it after the Phase-2 gate, but the source validator itself is useful
    # for explaining exactly which criteria a stock does/does not satisfy.
    sma_200 = calculate_sma(price_data["Close"], 200)
    minervini = validate_minervini_trend_template(current_price, phase_info, sma_200)

    # Same source breakout detector, exposed even when the strict buy engine exits
    # early because the Trend Template is not yet complete.
    breakout = detect_breakout(price_data, current_price, phase_info, vcp)

    buy_details = buy.get("details", {}) or {}
    sell_details = sell.get("details", {}) or {}
    stop = buy.get("stop_loss")
    risk_pct = ((current_price - finite(stop)) / current_price * 100) if stop and current_price > 0 else None
    avg_up = buy_details.get("avg_vol_up")
    avg_down = buy_details.get("avg_vol_down")
    ad_ratio = finite(avg_up) / finite(avg_down) if finite(avg_down) > 0 else None

    original = {
        "model": MODEL,
        "phase": phase,
        "phaseConfidence": native(phase_info.get("confidence")),
        "phaseReasons": native(phase_info.get("reasons", [])),
        "buy": {
            "score": native(buy.get("score")),
            "isBuy": bool(buy.get("is_buy", False)),
            "marketQualified": bool(should_buy_market and buy.get("is_buy", False)),
            "entryQuality": buy.get("entry_quality"),
            "stopLoss": native(stop),
            "riskPct": native(risk_pct),
            "riskReward": native(buy.get("risk_reward_ratio")),
            "rewardTarget": native(buy_details.get("reward_target")),
            "breakoutPrice": native(buy.get("breakout_price")),
            "reasons": native(buy.get("reasons", [])),
            "components": {
                "trend": native(buy_details.get("trend_score")),
                "fundamental": native(buy_details.get("fundamental_score")),
                "volume": native(buy_details.get("volume_score")),
                "relativeStrength": native(buy_details.get("rs_score")),
                "riskReward": native(buy_details.get("rr_score")),
                "entry": native(buy_details.get("entry_score")),
                "vcpBonus": native(buy_details.get("vcp_bonus")),
            },
            "avgVolumeUpDays": native(avg_up),
            "avgVolumeDownDays": native(avg_down),
            "adVolumeRatio": native(ad_ratio),
            "rsSlope": native(buy_details.get("rs_slope")),
        },
        "minervini": {
            "passes": bool(minervini.get("passes_template", False)),
            "score": native(minervini.get("template_score")),
            "passed": native(minervini.get("criteria_passed")),
            "total": native(minervini.get("criteria_total", 8)),
            "criteria": native(minervini.get("criteria_details", {})),
        },
        "breakout": native(breakout),
        "vcp": {
            "isVcp": bool(vcp.get("is_vcp", False)),
            "quality": native(vcp.get("vcp_quality")),
            "contractionCount": native(vcp.get("contraction_count")),
            "contractionQuality": native(vcp.get("contraction_quality")),
            "volumeQuality": native(vcp.get("volume_quality")),
            "baseLengthWeeks": native(vcp.get("base_length_weeks")),
            "breakoutVolumeRatio": native(vcp.get("breakout_volume_ratio")),
            "near52wHigh": native(vcp.get("near_52w_high")),
            "distance52wHighPct": native(vcp.get("distance_from_52w_high_pct")),
            "pattern": vcp.get("pattern_details", ""),
            "qualityFactors": native(vcp.get("quality_factors", [])),
            "contractions": compact_contractions(vcp),
        },
        "sell": {
            "score": native(sell.get("score")),
            "isSell": bool(sell.get("is_sell", False)),
            "severity": sell.get("severity", "none"),
            "breakdownLevel": native(sell.get("breakdown_level")),
            "reasons": native(sell.get("reasons", [])),
            "details": native(sell_details),
        },
    }
    row["originalEngine"] = original

    # Flat fields make source-methodology values sortable/filterable in the UI.
    row["originalBuyScore"] = native(buy.get("score"))
    row["originalBuy"] = bool(buy.get("is_buy", False))
    row["originalMarketQualifiedBuy"] = bool(should_buy_market and buy.get("is_buy", False))
    row["originalRR"] = native(buy.get("risk_reward_ratio"))
    row["originalStopLoss"] = native(stop)
    row["originalRiskPct"] = native(risk_pct)
    row["originalRewardTarget"] = native(buy_details.get("reward_target"))
    row["originalEntryQuality"] = buy.get("entry_quality")
    row["originalTTScore"] = native(minervini.get("template_score"))
    row["originalTTPasses"] = native(minervini.get("criteria_passed"))
    row["originalVcpQuality"] = native(vcp.get("vcp_quality"))
    row["originalAdVolumeRatio"] = native(ad_ratio)
    row["originalBreakoutType"] = breakout.get("breakout_type")
    row["originalBreakoutLevel"] = native(breakout.get("breakout_level"))
    row["originalBreakoutVolumeConfirmed"] = bool(breakout.get("volume_confirmed", False))
    row["originalSellScore"] = native(sell.get("score"))
    row["originalSell"] = bool(sell.get("is_sell", False))
    row["originalSellSeverity"] = sell.get("severity", "none")
    row["phaseConfidence"] = native(phase_info.get("confidence"))


def main() -> None:
    if not PROGRESS.exists():
        raise SystemExit(f"Missing scan progress: {PROGRESS}")
    if not OUT.exists():
        raise SystemExit(f"Missing frontend dataset: {OUT}")

    with PROGRESS.open("rb") as fh:
        progress = pickle.load(fh)
    analyses = progress.get("results") or progress.get("analyses") or []
    if not analyses:
        raise SystemExit("No scan analyses found in batch_progress.pkl")

    payload = json.loads(OUT.read_text(encoding="utf-8"))
    universe = payload.get("universe") or []
    by_ticker = {str(row.get("ticker", "")).upper(): row for row in universe if row.get("ticker")}

    market = market_gate(analyses)
    gate = market["gate"]
    should_buy_market = bool(gate.get("should_generate_buys", False))

    enriched = 0
    errors = 0
    for analysis in analyses:
        ticker = str(analysis.get("ticker", "")).upper()
        row = by_ticker.get(ticker)
        if not row:
            continue
        try:
            enrich_row(analysis, row, should_buy_market)
            enriched += 1
        except Exception as exc:
            errors += 1
            if errors <= 10:
                print(f"Original engine enrichment failed for {ticker}: {type(exc).__name__}: {exc}")

    m = payload.setdefault("market", {})
    m["originalEngineModel"] = MODEL
    m["originalSignalGate"] = market
    m["originalEngineCoverage"] = enriched
    m["originalEngineErrors"] = errors
    m["originalQualifiedBuys"] = sum(1 for r in universe if r.get("originalMarketQualifiedBuy"))
    m["originalSells"] = sum(1 for r in universe if r.get("originalSell"))
    payload["originalEngineModel"] = MODEL

    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(
        f"Original engine exported for {enriched:,}/{len(universe):,} stocks; "
        f"errors={errors}; market buys={'ON' if should_buy_market else 'OFF'}; "
        f"qualified buys={m['originalQualifiedBuys']}; sells={m['originalSells']}"
    )


if __name__ == "__main__":
    main()
