import math

import pandas as pd

from src.data import extended_fundamentals_fetcher as ef


def _frame(rows):
    periods = pd.to_datetime([
        "2025-03-31",
        "2025-06-30",
        "2025-09-30",
        "2025-12-31",
        "2026-03-31",
    ])
    return pd.DataFrame(rows, index=periods).T


class FakeTicker:
    def __init__(self):
        self.quarterly_financials = _frame({
            "Total Revenue": [100, 110, 120, 130, 150],
        })
        self.quarterly_balance_sheet = _frame({
            "Cash Cash Equivalents And Short Term Investments": [30, 32, 35, 38, 40],
            "Total Debt": [50, 49, 47, 45, 42],
            "Ordinary Shares Number": [100, 100, 101, 101, 102],
        })
        self.quarterly_cashflow = _frame({
            "Operating Cash Flow": [15, 17, 18, 20, 25],
            "Capital Expenditure": [-4, -4, -5, -5, -6],
        })


def test_extended_fetch_uses_unused_upstream_yfinance_statements(monkeypatch):
    monkeypatch.setattr(ef.yf, "Ticker", lambda ticker: FakeTicker())
    data = ef.fetch_extended_fundamentals("TEST")

    assert data["extended_fundamentals_model"] == ef.MODEL
    assert data["fundamental_data_source"] == "yfinance"
    assert data["operating_cash_flow_yoy_change"] == (25 - 15) / 15 * 100
    assert data["free_cash_flow_yoy_change"] == (19 - 11) / 11 * 100
    assert math.isclose(data["free_cash_flow_margin"], 19 / 150 * 100)
    assert data["total_debt_latest"] == 42
    assert data["cash_latest"] == 40
    assert data["net_debt_latest"] == 2
    assert data["total_debt_yoy_change"] == (42 - 50) / 50 * 100
    assert data["share_dilution_yoy_change"] == 2.0
    assert len(data["fundamental_periods"]) == 5


def test_missing_extended_rows_are_safe_and_remain_absent(monkeypatch):
    fake = FakeTicker()
    fake.quarterly_cashflow = pd.DataFrame()
    fake.quarterly_balance_sheet = pd.DataFrame()
    monkeypatch.setattr(ef.yf, "Ticker", lambda ticker: fake)

    data = ef.fetch_extended_fundamentals("TEST")

    assert data["extended_fundamentals_model"] == ef.MODEL
    assert data["fundamental_data_source"] == "yfinance"
    assert "free_cash_flow_yoy_change" not in data
    assert "total_debt_latest" not in data
    assert "share_dilution_yoy_change" not in data


def test_direct_free_cash_flow_is_preferred_over_derived(monkeypatch):
    fake = FakeTicker()
    fake.quarterly_cashflow.loc["Free Cash Flow"] = [12, 14, 15, 17, 22]
    monkeypatch.setattr(ef.yf, "Ticker", lambda ticker: fake)

    data = ef.fetch_extended_fundamentals("TEST")
    assert data["free_cash_flow_yoy_change"] == (22 - 12) / 12 * 100
    assert math.isclose(data["free_cash_flow_margin"], 22 / 150 * 100)


def test_provider_exception_fails_open(monkeypatch):
    def boom(_ticker):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(ef.yf, "Ticker", boom)
    assert ef.fetch_extended_fundamentals("TEST") == {}
