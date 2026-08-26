"""Additive yfinance fundamentals used only by the StockScout enrichment layer.

The upstream/LEGACY fundamentals fetcher is intentionally frozen. This module
uses the same yfinance statement source to expose cash-flow and balance-sheet
fields that upstream already makes available but does not use in its scoring.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import yfinance as yf

MODEL = "upstream-yfinance-additive-v1"
logger = logging.getLogger(__name__)


def _series(frame: pd.DataFrame, *names: str) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    for name in names:
        if name in frame.index:
            return pd.to_numeric(frame.loc[name], errors="coerce").sort_index()
    return None


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
        return value if pd.notna(value) else None
    except Exception:
        return None


def _change(values: pd.Series | None, periods: int) -> float | None:
    if values is None:
        return None
    clean = values.dropna()
    if len(clean) <= periods:
        return None
    latest = _finite(clean.iloc[-1])
    previous = _finite(clean.iloc[-periods - 1])
    if latest is None or previous in (None, 0):
        return None
    return ((latest - previous) / abs(previous)) * 100.0


def _latest(values: pd.Series | None) -> float | None:
    if values is None:
        return None
    clean = values.dropna()
    return _finite(clean.iloc[-1]) if len(clean) else None


def fetch_extended_fundamentals(ticker: str) -> dict[str, Any]:
    """Fetch additive evidence without touching any LEGACY calculation."""
    try:
        stock = yf.Ticker(ticker)
        income = stock.quarterly_financials
        balance = stock.quarterly_balance_sheet
        cashflow = stock.quarterly_cashflow

        result: dict[str, Any] = {
            "extended_fundamentals_model": MODEL,
            "fundamental_data_source": "yfinance",
        }

        revenue = _series(income, "Total Revenue")
        ocf = _series(cashflow, "Operating Cash Flow", "Total Cash From Operating Activities")
        capex = _series(cashflow, "Capital Expenditure", "Capital Expenditures")
        fcf = _series(cashflow, "Free Cash Flow")

        if ocf is not None:
            result["quarterly_operating_cash_flow"] = ocf.to_dict()
            result["operating_cash_flow_qoq_change"] = _change(ocf, 1)
            result["operating_cash_flow_yoy_change"] = _change(ocf, 4)
        if capex is not None:
            result["quarterly_capex"] = capex.to_dict()
        if fcf is None and ocf is not None and capex is not None:
            aligned = pd.concat([ocf.rename("ocf"), capex.rename("capex")], axis=1).dropna()
            if not aligned.empty:
                # Yahoo normally reports capex as a negative cash-flow item.
                fcf = aligned["ocf"] + aligned["capex"]
        if fcf is not None:
            result["quarterly_free_cash_flow"] = fcf.to_dict()
            result["free_cash_flow_qoq_change"] = _change(fcf, 1)
            result["free_cash_flow_yoy_change"] = _change(fcf, 4)
            latest_fcf = _latest(fcf)
            latest_revenue = _latest(revenue)
            result["free_cash_flow_margin"] = (
                latest_fcf / latest_revenue * 100.0
                if latest_fcf is not None and latest_revenue not in (None, 0)
                else None
            )

        cash = _series(
            balance,
            "Cash Cash Equivalents And Short Term Investments",
            "Cash And Cash Equivalents",
            "Cash",
        )
        debt = _series(
            balance,
            "Total Debt",
            "Long Term Debt And Capital Lease Obligation",
            "Long Term Debt",
        )
        shares = _series(balance, "Ordinary Shares Number", "Share Issued")

        latest_cash = _latest(cash)
        latest_debt = _latest(debt)
        if cash is not None:
            result["quarterly_cash"] = cash.to_dict()
            result["cash_latest"] = latest_cash
        if debt is not None:
            result["quarterly_total_debt"] = debt.to_dict()
            result["total_debt_latest"] = latest_debt
            result["total_debt_qoq_change"] = _change(debt, 1)
            result["total_debt_yoy_change"] = _change(debt, 4)
        if latest_cash is not None or latest_debt is not None:
            result["net_debt_latest"] = (latest_debt or 0.0) - (latest_cash or 0.0)
        if shares is not None:
            result["quarterly_shares_outstanding"] = shares.to_dict()
            result["shares_outstanding_latest"] = _latest(shares)
            result["share_dilution_yoy_change"] = _change(shares, 4)

        periods = set()
        for frame in (income, balance, cashflow):
            if frame is not None and not frame.empty:
                for value in frame.columns:
                    try:
                        periods.add(pd.Timestamp(value).date().isoformat())
                    except Exception:
                        periods.add(str(value))
        result["fundamental_periods"] = sorted(periods)
        return result
    except Exception as exc:
        logger.warning("%s: extended fundamentals unavailable: %s", ticker, exc)
        return {}
