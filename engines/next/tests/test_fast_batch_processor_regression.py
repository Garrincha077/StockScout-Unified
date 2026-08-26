from src.data.fundamentals_fetcher import analyze_fundamentals_for_signal
from src.screening.fast_batch_processor import FastOptimizedBatchProcessor


def test_null_optional_fundamentals_do_not_break_fast_scan():
    raw = {
        "ticker": "AMZN",
        "revenue_yoy_change": None,
        "revenue_qoq_change": None,
        "eps_yoy_change": None,
        "eps_qoq_change": None,
        "inventory_qoq_change": None,
        "margin_change": None,
        "quarterly_revenue": {"2026-Q1": 100.0, "2026-Q2": 110.0},
    }

    cleaned = FastOptimizedBatchProcessor._sanitize_quarterly_data(raw)

    assert cleaned["ticker"] == "AMZN"
    assert "revenue_yoy_change" not in cleaned
    assert "eps_yoy_change" not in cleaned
    assert "inventory_qoq_change" not in cleaned

    result = analyze_fundamentals_for_signal(cleaned)
    assert result["revenue_trend"] == "flat"
    assert result["eps_trend"] == "flat"
    assert result["inventory_signal"] == "neutral"
    assert result["supports_breakout"] is False


def test_real_numeric_fundamentals_are_preserved():
    raw = {
        "revenue_yoy_change": 18.0,
        "revenue_qoq_change": 4.0,
        "eps_yoy_change": 25.0,
        "inventory_qoq_change": 3.0,
        "margin_change": 1.2,
    }

    cleaned = FastOptimizedBatchProcessor._sanitize_quarterly_data(raw)

    assert cleaned == raw
    result = analyze_fundamentals_for_signal(cleaned)
    assert result["revenue_trend"] == "accelerating"
    assert result["eps_trend"] == "accelerating"
    assert result["inventory_signal"] == "neutral"
    assert result["supports_breakout"] is True
