import numpy as np
import pandas as pd
import pytest

import src.screening.fast_batch_processor as fast_module
import src.screening.optimized_batch_processor as legacy_module
from src.screening.benchmark import analyze_spy_trend, calculate_market_breadth, should_generate_signals
from src.screening.fast_batch_processor import FastOptimizedBatchProcessor
from src.screening.optimized_batch_processor import OptimizedBatchProcessor
from src.screening.phase_indicators import calculate_sma, detect_breakout, validate_minervini_trend_template
from src.screening.signal_engine import score_buy_signal, score_sell_signal

TEST_DELAY = 0.001


def make_ohlcv(kind: str, periods: int = 360, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-03-03", periods=periods)
    x = np.arange(periods, dtype=float)
    if kind == "up":
        close = 18 + x * 0.075 + np.sin(x / 9) * 0.45
    elif kind == "down":
        close = 55 - x * 0.09 + np.sin(x / 10) * 0.35
    elif kind == "flat":
        close = 25 + np.sin(x / 8) * 0.65 + np.sin(x / 31) * 0.25
    else:
        raise ValueError(kind)
    close = np.maximum(close, 3.0)
    noise = rng.normal(0, 0.0025, periods)
    open_ = close * (1 + noise)
    high = np.maximum(open_, close) * (1.012 + rng.random(periods) * 0.004)
    low = np.minimum(open_, close) * (0.988 - rng.random(periods) * 0.004)
    volume = np.full(periods, 900_000.0)
    volume[-20:] = np.linspace(750_000, 1_350_000, 20)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )


def make_fundamentals():
    return {
        "revenue_yoy_change": 18.0,
        "revenue_qoq_change": 4.0,
        "eps_yoy_change": 22.0,
        "eps_qoq_change": 6.0,
        "inventory_qoq_change": 2.0,
        "inventory_to_sales_ratio": 0.12,
        "gross_margin": 48.0,
        "margin_change": 1.5,
        "operating_margin": 16.0,
    }


def source_outputs(analysis: dict):
    price_data = analysis["price_data"]
    current_price = analysis["current_price"]
    phase_info = analysis["phase_info"]
    rs_series = analysis["rs_series"]
    fundamentals = analysis.get("quarterly_data", {})
    vcp = analysis.get("vcp_data", {})
    buy = score_buy_signal(
        ticker=analysis["ticker"],
        price_data=price_data,
        current_price=current_price,
        phase_info=phase_info,
        rs_series=rs_series,
        fundamentals=fundamentals,
        vcp_data=vcp,
    )
    sell = score_sell_signal(
        ticker=analysis["ticker"],
        price_data=price_data,
        current_price=current_price,
        phase_info=phase_info,
        rs_series=rs_series,
        fundamentals=fundamentals,
    )
    sma_200 = calculate_sma(price_data["Close"], 200)
    minervini = validate_minervini_trend_template(current_price, phase_info, sma_200)
    breakout = detect_breakout(price_data, current_price, phase_info, vcp)
    return buy, sell, minervini, breakout


@pytest.mark.parametrize("kind", ["up", "flat", "down"])
def test_fast_path_matches_frozen_processor_on_identical_inputs(monkeypatch, kind):
    ticker = "FIX"
    long_hist = make_ohlcv(kind, seed={"up": 7, "flat": 11, "down": 13}[kind])
    spy_long = make_ohlcv("up", seed=99)
    spy = spy_long.tail(252).copy()
    fundamentals = make_fundamentals()

    legacy = OptimizedBatchProcessor(max_workers=1, rate_limit_delay=TEST_DELAY, use_git_storage=False)
    legacy.spy_data = spy
    legacy.spy_price = float(spy["Close"].iloc[-1])
    monkeypatch.setattr(legacy.fetcher, "fetch_price_history", lambda symbol, period="5y": long_hist.copy())
    monkeypatch.setattr(legacy_module, "fetch_quarterly_financials", lambda symbol: dict(fundamentals))

    fast = FastOptimizedBatchProcessor(max_workers=1, rate_limit_delay=TEST_DELAY, use_git_storage=False)
    fast.spy_data = spy
    fast.spy_price = float(spy["Close"].iloc[-1])
    fast.price_history[ticker] = long_hist.copy()
    monkeypatch.setattr(fast_module, "fetch_quarterly_financials", lambda symbol: dict(fundamentals))

    original_analysis = legacy.analyze_single_stock(ticker, 5.0, 10_000.0, 100_000)
    fast_analysis = fast.analyze_single_stock(ticker, 5.0, 10_000.0, 100_000)

    assert original_analysis is not None
    assert fast_analysis is not None
    assert fast_analysis["ticker"] == original_analysis["ticker"]
    assert fast_analysis["current_price"] == pytest.approx(original_analysis["current_price"], rel=0, abs=1e-12)
    assert fast_analysis["avg_volume"] == pytest.approx(original_analysis["avg_volume"], rel=0, abs=1e-9)
    assert fast_analysis["phase_info"] == original_analysis["phase_info"]
    assert fast_analysis["vcp_data"] == original_analysis["vcp_data"]
    assert fast_analysis["quarterly_data"] == original_analysis["quarterly_data"]
    assert fast_analysis["fundamental_analysis"] == original_analysis["fundamental_analysis"]
    pd.testing.assert_series_equal(fast_analysis["rs_series"], original_analysis["rs_series"])

    fast_outputs = source_outputs(fast_analysis)
    original_outputs = source_outputs(original_analysis)
    assert fast_outputs == original_outputs


def test_market_gate_is_stable_for_fixture_phase_set(monkeypatch):
    kinds = ["up", "up", "flat", "down"]
    spy = make_ohlcv("up").tail(252)
    phases = []
    for index, kind in enumerate(kinds):
        ticker = f"F{index}"
        hist = make_ohlcv(kind, seed=20 + index)
        processor = FastOptimizedBatchProcessor(max_workers=1, rate_limit_delay=TEST_DELAY, use_git_storage=False)
        processor.spy_data = spy
        processor.spy_price = float(spy["Close"].iloc[-1])
        processor.price_history[ticker] = hist
        monkeypatch.setattr(fast_module, "fetch_quarterly_financials", lambda symbol: make_fundamentals())
        analysis = processor.analyze_single_stock(ticker, 5.0, 10_000.0, 100_000)
        assert analysis is not None
        phases.append(analysis["phase_info"])

    spy_analysis = analyze_spy_trend(spy, float(spy["Close"].iloc[-1]))
    breadth = calculate_market_breadth(phases)
    gate_a = should_generate_signals(spy_analysis, breadth)
    gate_b = should_generate_signals(dict(spy_analysis), dict(breadth))
    assert gate_a == gate_b
