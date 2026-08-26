from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import requests

from stock_scout.config.schema import Env, TiingoConfig
from stock_scout.data.base import (
    BaseDataProvider,
    OHLCVFrame,
    ProviderError,
    ProviderHealth,
    Quote,
    normalize_ohlcv,
)
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

TIINGO_BASE = "https://api.tiingo.com"


class TiingoDataProvider(BaseDataProvider):
    """Tiingo REST adapter. Low-cost EOD; good fallback to yfinance."""

    name = "tiingo"

    def __init__(self, cfg: TiingoConfig, env: Env):
        self.cfg = cfg
        self.env = env
        if not env.TIINGO_API_KEY:
            log.warning("tiingo.no_api_key")
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Token {env.TIINGO_API_KEY}",
        }

    def _check_key(self):
        if not self.env.TIINGO_API_KEY:
            raise ProviderError("TIINGO_API_KEY is not set in .env")

    def get_universe(self) -> list[str]:
        # Tiingo provides a supported-tickers CSV; orchestrator uses NASDAQ Trader instead.
        return []

    def _historical(
        self, ticker: str, start: date, end: date, resample: str | None = None
    ) -> OHLCVFrame:
        self._check_key()
        params = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "format": "json",
        }
        if resample:
            params["resampleFreq"] = resample
        url = f"{TIINGO_BASE}/tiingo/daily/{ticker.upper()}/prices"
        try:
            r = requests.get(url, params=params, headers=self._headers, timeout=30)
            if r.status_code == 404:
                return pd.DataFrame()
            r.raise_for_status()
            rows = r.json()
        except Exception as e:
            log.warning("tiingo.hist_failed", ticker=ticker, error=str(e))
            return pd.DataFrame()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.set_index("date")
        # Tiingo provides adjClose / adjOpen / adjHigh / adjLow / adjVolume — use adjusted.
        out = pd.DataFrame(
            {
                "open": df.get("adjOpen", df.get("open")),
                "high": df.get("adjHigh", df.get("high")),
                "low": df.get("adjLow", df.get("low")),
                "close": df.get("adjClose", df.get("close")),
                "volume": df.get("adjVolume", df.get("volume")),
            }
        )
        return normalize_ohlcv(out)

    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date, adjusted: bool = True
    ) -> OHLCVFrame:
        return self._historical(ticker, start, end, resample=None)

    def get_weekly_ohlcv(self, ticker: str, start: date, end: date) -> OHLCVFrame:
        return self._historical(ticker, start, end, resample="weekly")

    def get_latest_quote(self, ticker: str) -> Quote | None:
        self._check_key()
        url = f"{TIINGO_BASE}/iex/{ticker.upper()}"
        try:
            r = requests.get(url, headers=self._headers, timeout=15)
            if r.status_code != 200:
                return None
            data = r.json()
            if not data:
                return None
            row = data[0] if isinstance(data, list) else data
            price = row.get("last") or row.get("tngoLast") or row.get("prevClose")
            if price is None:
                return None
            return Quote(
                ticker=ticker,
                price=float(price),
                volume=int(row.get("volume")) if row.get("volume") else None,
                timestamp=datetime.now(timezone.utc),
                provider=self.name,
            )
        except Exception as e:
            log.debug("tiingo.quote_failed", ticker=ticker, error=str(e))
            return None

    def validate_symbol(self, ticker: str) -> bool:
        return self.get_latest_quote(ticker) is not None

    def health_check(self) -> ProviderHealth:
        try:
            self._check_key()
            r = requests.get(f"{TIINGO_BASE}/api/test", headers=self._headers, timeout=10)
            return ProviderHealth(
                provider=self.name,
                healthy=r.status_code == 200,
                detail=f"status={r.status_code}",
            )
        except Exception as e:
            return ProviderHealth(provider=self.name, healthy=False, detail=str(e))
