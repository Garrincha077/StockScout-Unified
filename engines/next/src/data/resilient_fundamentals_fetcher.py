"""Resilient quarterly-fundamentals provider boundary for Unified EOD.

The vendored fundamentals helper intentionally converts every provider exception
into an empty dictionary, which makes transient Yahoo failures indistinguishable
from legitimate no-data responses. Unified EOD needs that distinction for
bounded retries and diagnostics, so this module mirrors the established metric
extraction while allowing provider exceptions to propagate to a small retry
wrapper. The legacy helper remains untouched.
"""
from __future__ import annotations

import logging
import math
import random
import re
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict

import pandas as pd
import yfinance as yf

from .git_storage_fetcher import GitStorageFetcher

logger = logging.getLogger(__name__)

_TRANSIENT_CATEGORIES = {"timeout", "rate_limit", "server", "network"}


def fetch_quarterly_financials_strict(ticker: str) -> Dict[str, object]:
    """Return the same quarterly metrics as the legacy helper, but do not hide errors."""
    stock = yf.Ticker(ticker)
    quarterly_income = stock.quarterly_financials
    quarterly_balance = stock.quarterly_balance_sheet
    # Preserve the legacy provider touch even though cash flow is not currently
    # used by the score. A failure here must be classified consistently.
    _ = stock.quarterly_cashflow

    if quarterly_income.empty:
        logger.warning("No quarterly income data for %s", ticker)
        return {}

    result: Dict[str, object] = {
        "ticker": ticker,
        "fetch_date": datetime.now().isoformat(),
    }

    if "Total Revenue" in quarterly_income.index:
        revenues = quarterly_income.loc["Total Revenue"].sort_index()
        result["quarterly_revenue"] = revenues.to_dict()
        if len(revenues) >= 2:
            latest_rev = revenues.iloc[-1]
            prev_rev = revenues.iloc[-2]
            if (
                not math.isnan(latest_rev)
                and not math.isnan(prev_rev)
                and prev_rev != 0
                and latest_rev != 0
            ):
                result["revenue_qoq_change"] = (latest_rev - prev_rev) / prev_rev * 100
            else:
                result["revenue_qoq_change"] = None
        if len(revenues) >= 5:
            latest_rev = revenues.iloc[-1]
            yoy_rev = revenues.iloc[-5]
            if (
                not math.isnan(latest_rev)
                and not math.isnan(yoy_rev)
                and yoy_rev != 0
                and latest_rev != 0
            ):
                result["revenue_yoy_change"] = (latest_rev - yoy_rev) / yoy_rev * 100
            else:
                result["revenue_yoy_change"] = None

    eps_key = None
    if "Diluted EPS" in quarterly_income.index:
        eps_key = "Diluted EPS"
    elif "Basic EPS" in quarterly_income.index:
        eps_key = "Basic EPS"
    if eps_key:
        eps_values = quarterly_income.loc[eps_key].sort_index()
        result["quarterly_eps"] = eps_values.to_dict()
        if len(eps_values) >= 2:
            latest_eps = eps_values.iloc[-1]
            prev_eps = eps_values.iloc[-2]
            if (
                not math.isnan(latest_eps)
                and not math.isnan(prev_eps)
                and prev_eps != 0
                and latest_eps != 0
            ):
                result["eps_qoq_change"] = (latest_eps - prev_eps) / abs(prev_eps) * 100
            else:
                result["eps_qoq_change"] = None
        if len(eps_values) >= 5:
            latest_eps = eps_values.iloc[-1]
            yoy_eps = eps_values.iloc[-5]
            if not math.isnan(latest_eps) and not math.isnan(yoy_eps) and yoy_eps != 0:
                result["eps_yoy_change"] = (latest_eps - yoy_eps) / abs(yoy_eps) * 100
            else:
                result["eps_yoy_change"] = None

    if "Gross Profit" in quarterly_income.index and "Total Revenue" in quarterly_income.index:
        gross_profit = quarterly_income.loc["Gross Profit"].sort_index()
        revenue = quarterly_income.loc["Total Revenue"].sort_index()
        if len(gross_profit) > 0 and len(revenue) > 0:
            latest_margin = (
                gross_profit.iloc[-1] / revenue.iloc[-1] * 100 if revenue.iloc[-1] != 0 else 0
            )
            result["gross_margin"] = round(latest_margin, 2)
            if len(gross_profit) >= 2:
                prev_margin = (
                    gross_profit.iloc[-2] / revenue.iloc[-2] * 100
                    if revenue.iloc[-2] != 0
                    else 0
                )
                result["margin_change"] = round(latest_margin - prev_margin, 2)

    if "Operating Income" in quarterly_income.index and "Total Revenue" in quarterly_income.index:
        operating_income = quarterly_income.loc["Operating Income"].sort_index()
        revenue = quarterly_income.loc["Total Revenue"].sort_index()
        if len(operating_income) > 0 and len(revenue) > 0:
            latest_op_margin = (
                operating_income.iloc[-1] / revenue.iloc[-1] * 100
                if revenue.iloc[-1] != 0
                else 0
            )
            result["operating_margin"] = round(latest_op_margin, 2)

    if not quarterly_balance.empty and "Inventory" in quarterly_balance.index:
        inventory = quarterly_balance.loc["Inventory"].sort_index()
        result["quarterly_inventory"] = inventory.to_dict()
        if len(inventory) >= 2:
            latest_inv = inventory.iloc[-1]
            prev_inv = inventory.iloc[-2]
            if (
                not math.isnan(latest_inv)
                and not math.isnan(prev_inv)
                and prev_inv != 0
                and latest_inv != 0
            ):
                result["inventory_qoq_change"] = round(
                    (latest_inv - prev_inv) / prev_inv * 100,
                    2,
                )
            else:
                result["inventory_qoq_change"] = None
        if "Total Revenue" in quarterly_income.index:
            revenues = quarterly_income.loc["Total Revenue"].sort_index()
            if len(revenues) > 0:
                latest_inv = inventory.iloc[-1]
                latest_rev = revenues.iloc[-1]
                inv_to_sales = latest_inv / latest_rev if latest_rev != 0 else 0
                result["inventory_to_sales_ratio"] = round(inv_to_sales, 3)

    result["inventory_breakdown_available"] = False
    return result


class ResilientGitStorageFetcher(GitStorageFetcher):
    """GitStorageFetcher with bounded, classified retries for fresh fundamentals."""

    def __init__(
        self,
        *args,
        max_provider_attempts: int = 3,
        retry_base_seconds: float = 1.0,
        retry_jitter_seconds: float = 0.25,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.max_provider_attempts = max(1, int(max_provider_attempts))
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.retry_jitter_seconds = max(0.0, float(retry_jitter_seconds))
        self._sleep_fn = sleep_fn
        self._random_fn = random_fn
        self._provider_lock = threading.Lock()
        self._provider_counters: Counter[str] = Counter()
        self._provider_error_classes: Counter[str] = Counter()

    @staticmethod
    def _classify_provider_error(exc: Exception) -> str:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        combined = f"{name} {message}"
        if isinstance(exc, TimeoutError) or "timeout" in combined or "timed out" in combined:
            return "timeout"
        if "429" in combined or "rate limit" in combined or "too many requests" in combined:
            return "rate_limit"
        if re.search(r"(?:^|\D)5\d\d(?:\D|$)", combined) or "server error" in combined:
            return "server"
        network_tokens = (
            "connectionerror",
            "connection error",
            "connection reset",
            "remote disconnected",
            "network",
            "dns",
            "name resolution",
            "could not resolve",
            "curl error 5",
            "curl error 6",
            "curl error 7",
            "curl error 18",
            "curl error 28",
            "curl error 35",
            "curl error 52",
            "curl error 56",
        )
        if any(token in combined for token in network_tokens):
            return "network"
        return "permanent"

    def _increment(self, key: str, amount: int = 1) -> None:
        with self._provider_lock:
            self._provider_counters[key] += amount

    def _record_error(self, category: str, exc: Exception) -> None:
        with self._provider_lock:
            self._provider_counters["providerErrorCount"] += 1
            self._provider_counters[f"{category}Count"] += 1
            self._provider_error_classes[type(exc).__name__] += 1

    def provider_stats(self) -> dict[str, object]:
        with self._provider_lock:
            counters = dict(self._provider_counters)
            errors = dict(self._provider_error_classes)
        return {
            "attemptCount": int(counters.get("attemptCount", 0)),
            "retryCount": int(counters.get("retryCount", 0)),
            "rateLimitCount": int(counters.get("rate_limitCount", 0)),
            "timeoutCount": int(counters.get("timeoutCount", 0)),
            "serverErrorCount": int(counters.get("serverCount", 0)),
            "networkErrorCount": int(counters.get("networkCount", 0)),
            "permanentErrorCount": int(counters.get("permanentCount", 0)),
            "providerErrorCount": int(counters.get("providerErrorCount", 0)),
            "failedTickerCount": int(counters.get("failedTickerCount", 0)),
            "emptyDataCount": int(counters.get("emptyDataCount", 0)),
            "errorClasses": errors,
        }

    def restore_provider_stats(self, stats: dict[str, object] | None) -> None:
        if not isinstance(stats, dict):
            return
        mapping = {
            "attemptCount": "attemptCount",
            "retryCount": "retryCount",
            "rateLimitCount": "rate_limitCount",
            "timeoutCount": "timeoutCount",
            "serverErrorCount": "serverCount",
            "networkErrorCount": "networkCount",
            "permanentErrorCount": "permanentCount",
            "providerErrorCount": "providerErrorCount",
            "failedTickerCount": "failedTickerCount",
            "emptyDataCount": "emptyDataCount",
        }
        with self._provider_lock:
            for public_name, internal_name in mapping.items():
                try:
                    self._provider_counters[internal_name] = int(stats.get(public_name) or 0)
                except (TypeError, ValueError):
                    self._provider_counters[internal_name] = 0
            errors = stats.get("errorClasses") or {}
            self._provider_error_classes = Counter(
                {str(name): int(count) for name, count in dict(errors).items()}
            )

    def _fetch_fresh_with_retry(self, ticker: str) -> Dict[str, object]:
        for attempt in range(1, self.max_provider_attempts + 1):
            self._increment("attemptCount")
            try:
                data = fetch_quarterly_financials_strict(ticker)
                if not data:
                    # Empty financial statements are a legitimate data-quality
                    # outcome, not evidence of a transient transport failure.
                    self._increment("emptyDataCount")
                return data
            except Exception as exc:
                category = self._classify_provider_error(exc)
                self._record_error(category, exc)
                transient = category in _TRANSIENT_CATEGORIES
                if not transient or attempt >= self.max_provider_attempts:
                    self._increment("failedTickerCount")
                    logger.error(
                        "%s: fundamentals provider failed (%s) after %d attempt(s): %s",
                        ticker,
                        category,
                        attempt,
                        exc,
                    )
                    return {}
                self._increment("retryCount")
                delay = self.retry_base_seconds * (2 ** (attempt - 1))
                delay += self.retry_jitter_seconds * self._random_fn()
                logger.warning(
                    "%s: transient fundamentals provider error (%s), retry %d/%d in %.2fs: %s",
                    ticker,
                    category,
                    attempt + 1,
                    self.max_provider_attempts,
                    delay,
                    exc,
                )
                self._sleep_fn(delay)
        return {}

    def fetch_fundamentals_smart(self, ticker: str) -> Dict:
        fundamental_file = self.fundamentals_dir / f"{ticker}_fundamentals.json"
        should_refresh = self._should_refresh_fundamental(ticker, fundamental_file)

        if not should_refresh and fundamental_file.exists():
            try:
                with fundamental_file.open("r", encoding="utf-8") as handle:
                    cached = __import__("json").load(handle)
                logger.debug("%s: Using cached fundamentals", ticker)
                return cached.get("data", {})
            except Exception as exc:
                logger.warning("%s: Cache load failed: %s, will refresh", ticker, exc)

        logger.info("%s: Fetching fresh fundamentals", ticker)
        data = self._fetch_fresh_with_retry(ticker)
        if not data:
            return {}

        cleaned_data = self._clean_for_json(data)
        cache_data = {
            "data": cleaned_data,
            "fetched_at": datetime.now().isoformat(),
        }
        import json

        with fundamental_file.open("w", encoding="utf-8") as handle:
            json.dump(cache_data, handle, indent=2, default=str)
        self._update_metadata(ticker)
        logger.info("%s: Fundamentals cached to Git", ticker)
        return data
