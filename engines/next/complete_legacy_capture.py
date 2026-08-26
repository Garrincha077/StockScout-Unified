#!/usr/bin/env python3
"""Complete the LEGACY layer with the exact outputs of the original scan path.

Scope is intentionally the upstream production/full-market execution path used by
run_optimized_scan.py at the frozen fork baseline:
- phase_info produced by phase_indicators.py
- score_buy_signal / score_sell_signal
- validate_minervini_trend_template / detect_breakout
- raw VCP output already produced by the original processor
- market gating already attached by enrich_original_engine.py
- the standard fundamental snapshot emitted by the original default run

No StockScout rules participate here. Large OHLCV frames are not duplicated into
latest.json because the canonical chart/rich-data store already preserves them.
"""
from __future__ import annotations

import json
import math
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.data.enhanced_fundamentals import EnhancedFundamentalsFetcher
from src.screening.phase_indicators import calculate_sma, detect_breakout, validate_minervini_trend_template
from src.screening.signal_engine import score_buy_signal, score_sell_signal

ROOT = Path(__file__).resolve().parent
PROGRESS = ROOT / "data" / "batch_results" / "batch_progress.pkl"
OUT = ROOT / "frontend" / "public" / "data" / "latest.json"
MODEL = "legacy-complete-source-capture-v1"


def native(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        v = float(value)
        return round(v, 6) if math.isfinite(v) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Series):
        return {str(k): native(v) for k, v in value.items()}
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


def main() -> None:
    if not PROGRESS.exists() or not OUT.exists():
        raise SystemExit("Missing scan progress or canonical frontend dataset")

    with PROGRESS.open("rb") as fh:
        progress = pickle.load(fh)
    analyses = progress.get("results") or progress.get("analyses") or []
    if not analyses:
        raise SystemExit("No analyses in batch_progress.pkl")

    payload = json.loads(OUT.read_text(encoding="utf-8"))
    rows = {str(r.get("ticker", "")).upper(): r for r in payload.get("universe", []) if r.get("ticker")}
    gate = ((payload.get("market") or {}).get("originalSignalGate") or {}).get("gate") or {}
    buy_gate = bool(gate.get("should_generate_buys", False))
    sell_gate = bool(gate.get("should_generate_sells", False))
    fundamentals_fetcher = EnhancedFundamentalsFetcher()

    captured = 0
    errors = 0
    for analysis in analyses:
        ticker = str(analysis.get("ticker", "")).upper()
        row = rows.get(ticker)
        price_data = analysis.get("price_data")
        if row is None or not isinstance(price_data, pd.DataFrame) or price_data.empty:
            continue
        try:
            phase_info = analysis.get("phase_info", {}) or {}
            phase = int(phase_info.get("phase", 0) or 0)
            current_price = finite(analysis.get("current_price"))
            rs_series = analysis.get("rs_series")
            if not isinstance(rs_series, pd.Series):
                rs_series = pd.Series(index=price_data.index, dtype=float)
            quarterly = analysis.get("quarterly_data", {}) or {}
            fundamental_analysis = analysis.get("fundamental_analysis", {}) or {}
            vcp = analysis.get("vcp_data", {}) or {}

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
            sma_200 = calculate_sma(price_data["Close"], 200)
            minervini = validate_minervini_trend_template(current_price, phase_info, sma_200)
            breakout = detect_breakout(price_data, current_price, phase_info, vcp)

            emitted_buy = bool(buy_gate and phase in (1, 2) and buy.get("is_buy", False))
            emitted_sell = bool(sell_gate and phase in (3, 4) and sell.get("is_sell", False))
            fundamental_snapshot = None
            if emitted_buy or emitted_sell:
                # The original default run calls create_snapshot with use_fmp=False
                # unless --use-fmp is explicitly supplied. This reproduces that
                # default source behavior without adding any StockScout enrichment.
                fundamental_snapshot = fundamentals_fetcher.create_snapshot(
                    ticker,
                    quarterly_data=quarterly,
                    use_fmp=False,
                )

            engine = row.setdefault("originalEngine", {})
            engine["completeSourceCaptureModel"] = MODEL
            engine["phaseInfo"] = native(phase_info)
            engine["sourceInputs"] = {
                "quarterlyData": native(quarterly),
                "fundamentalAnalysis": native(fundamental_analysis),
                "vcpData": native(vcp),
            }
            engine["sourceOutputs"] = {
                "buy": native(buy),
                "sell": native(sell),
                "minervini": native(minervini),
                "breakout": native(breakout),
                "vcp": native(vcp),
                "fundamentalSnapshot": fundamental_snapshot,
            }
            engine.setdefault("buy", {})["sourceReason"] = native(buy.get("reason"))
            engine["buy"]["allDetails"] = native(buy.get("details", {}))
            engine["buy"]["emittedByOriginalRun"] = emitted_buy
            if emitted_buy:
                engine["buy"]["fundamentalSnapshot"] = fundamental_snapshot
            engine.setdefault("sell", {})["sourceReason"] = native(sell.get("reason"))
            engine["sell"]["allDetails"] = native(sell.get("details", {}))
            engine["sell"]["emittedByOriginalRun"] = emitted_sell
            if emitted_sell:
                engine["sell"]["fundamentalSnapshot"] = fundamental_snapshot

            row["originalRunBuySignal"] = emitted_buy
            row["originalRunSellSignal"] = emitted_sell
            row["originalMarketQualifiedSell"] = emitted_sell
            captured += 1
        except Exception as exc:
            errors += 1
            if errors <= 10:
                print(f"LEGACY complete capture failed for {ticker}: {type(exc).__name__}: {exc}")

    market = payload.setdefault("market", {})
    market["legacyCompleteSourceCaptureModel"] = MODEL
    market["legacyCompleteSourceCoverage"] = captured
    market["legacyCompleteSourceErrors"] = errors
    market["originalRunBuySignals"] = sum(1 for r in rows.values() if r.get("originalRunBuySignal"))
    market["originalRunSellSignals"] = sum(1 for r in rows.values() if r.get("originalRunSellSignal"))
    payload["legacyCompleteSourceCaptureModel"] = MODEL

    OUT.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(
        f"Complete LEGACY source capture: {captured:,}/{len(rows):,}; errors={errors}; "
        f"original-run BUY={market['originalRunBuySignals']}; SELL={market['originalRunSellSignals']}"
    )


if __name__ == "__main__":
    main()
