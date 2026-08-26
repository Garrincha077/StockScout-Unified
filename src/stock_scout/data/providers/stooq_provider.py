"""Stooq data provider — free, keyless deep daily/weekly history.

Stooq (stooq.com) serves long EOD history via a public CSV endpoint with no
API key and no signup:

    https://stooq.com/q/d/l/?s={symbol}.us&i=d&d1=YYYYMMDD&d2=YYYYMMDD

This makes it an ideal *deep-history fallback*: when the primary provider caps
history (e.g. Alpaca's free IEX feed) or rate-limits (yfinance), Stooq can
backfill the 5 years the screener wants. Prices are split-adjusted. Per the
provider contract, per-ticker misses return an empty frame (never raise);
systemic failures degrade to empty too (best-effort, never fatal).
"""

from __future__ import annotations

import io
import time
from datetime import date

import pandas as pd
import requests

from stock_scout.data.base import (
    BaseDataProvider,
    OHLCVFrame,
    ProviderHealth,
    Quote,
    normalize_ohlcv,
)
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

STOOQ_CSV = "https://stooq.com/q/d/l/"


def _to_stooq_symbol(ticker: str) -> str:
    """US equity → Stooq symbol. Class shares use a dash (BRK.B → brk-b.us)."""
    return ticker.strip().upper().replace(".", "-").lower() + ".us"


class StooqDataProvider(BaseDataProvider):
    name = "stooq"

    def __init__(self, timeout_seconds: float = 30.0, retry_attempts: int = 3,
                 retry_backoff_seconds: float = 2.0):
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_backoff_seconds = retry_backoff_seconds

    # ---- universe -------------------------------------------------------------

    def get_universe(self) -> list[str]:
        # No native universe endpoint; orchestrator supplies the universe.
        return []

    # ---- OHLCV ----------------------------------------------------------------

    def _download(self, ticker: str, start: date, end: date, interval: str) -> OHLCVFrame:
        params = {
            "s": _to_stooq_symbol(ticker),
            "i": interval,  # "d" daily, "w" weekly
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
        }
        last_err: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                r = requests.get(
                    STOOQ_CSV,
                    params=params,
                    timeout=self.timeout_seconds,
                    headers={"User-Agent": "Mozilla/5.0 (stock-scout)"},
                )
                r.raise_for_status()
                text = r.text or ""
                # Stooq returns a tiny body like "No data" / "Exceeded the daily
                # hits limit" when there's nothing to serve — the real payload
                # always starts with the "Date,Open,..." CSV header.
                if not text.lstrip().lower().startswith("date,"):
                    return pd.DataFrame()
                df = pd.read_csv(io.StringIO(text))
                if df.empty or "Date" not in df.columns or "Close" not in df.columns:
                    return pd.DataFrame()
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
                df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
                out = pd.DataFrame(
                    {
                        "open": df.get("Open"),
                        "high": df.get("High"),
                        "low": df.get("Low"),
                        "close": df.get("Close"),
                        "volume": df.get("Volume"),
                    }
                )
                return normalize_ohlcv(out)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self.retry_attempts:
                    time.sleep(self.retry_backoff_seconds * attempt)
                continue
        log.warning("stooq.download_failed", ticker=ticker, interval=interval, error=str(last_err)[:160])
        return pd.DataFrame()

    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date, adjusted: bool = True
    ) -> OHLCVFrame:
        return self._download(ticker, start, end, "d")

    def get_weekly_ohlcv(self, ticker: str, start: date, end: date) -> OHLCVFrame:
        wk = self._download(ticker, start, end, "w")
        if not wk.empty:
            return wk
        # Fall back to resampling daily if the weekly endpoint returned nothing.
        daily = self._download(ticker, start, end, "d")
        if daily.empty:
            return daily
        agg = daily.resample("W-FRI").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        return agg.dropna(how="all")

    def get_latest_quote(self, ticker: str) -> Quote | None:
        # Stooq's CSV is EOD only; no realtime quote.
        return None

    def validate_symbol(self, ticker: str) -> bool:
        # Cheap check: a recent short pull returns rows for a real symbol.
        from datetime import timedelta

        end = date.today()
        df = self._download(ticker, end - timedelta(days=14), end, "d")
        return not df.empty

    def health_check(self) -> ProviderHealth:
        try:
            from datetime import timedelta

            end = date.today()
            df = self._download("AAPL", end - timedelta(days=14), end, "d")
            ok = not df.empty
            return ProviderHealth(
                provider=self.name,
                healthy=ok,
                detail="ok" if ok else "no data for AAPL probe",
            )
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(provider=self.name, healthy=False, detail=str(e)[:160])
