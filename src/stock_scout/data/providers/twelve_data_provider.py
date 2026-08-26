from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import requests

from stock_scout.config.schema import Env, TwelveDataConfig
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

TWELVEDATA_BASE = "https://api.twelvedata.com"


class TwelveDataDataProvider(BaseDataProvider):
    """Twelve Data REST adapter (free tier: 800 calls/day, 8 req/min).

    Free-tier rate limits make this unsuitable as a primary full-universe
    fetcher; it is intended as an EXTRA cross-validation source for the
    median-of-providers price consensus (run only over the top-N candidates).
    """

    name = "twelvedata"

    def __init__(self, cfg: TwelveDataConfig, env: Env):
        self.cfg = cfg
        self.env = env
        if not env.TWELVEDATA_API_KEY:
            log.warning("twelvedata.no_api_key")

    def _check_key(self):
        if not self.env.TWELVEDATA_API_KEY:
            raise ProviderError("TWELVEDATA_API_KEY is not set in .env")

    def get_universe(self) -> list[str]:
        return []

    def _historical(self, ticker: str, start: date, end: date, interval: str) -> OHLCVFrame:
        self._check_key()
        params = {
            "symbol": ticker.upper(),
            "interval": interval,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "outputsize": 5000,
            "order": "ASC",
            "apikey": self.env.TWELVEDATA_API_KEY,
            "format": "JSON",
        }
        try:
            r = requests.get(
                f"{TWELVEDATA_BASE}/time_series",
                params=params,
                timeout=self.cfg.request_timeout_seconds,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("twelvedata.hist_failed", ticker=ticker, error=str(e)[:200])
            return pd.DataFrame()
        # Twelve Data signals errors in the JSON body (status="error"), e.g. an
        # unknown symbol or a 429 rate-limit — never raises HTTP for those.
        if isinstance(data, dict) and data.get("status") == "error":
            log.warning("twelvedata.api_error", ticker=ticker, msg=str(data.get("message"))[:160])
            return pd.DataFrame()
        values = data.get("values") if isinstance(data, dict) else None
        if not values:
            return pd.DataFrame()
        df = pd.DataFrame(values)
        df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)
        df = df.set_index("datetime").sort_index()
        out = pd.DataFrame(
            {
                "open": pd.to_numeric(df.get("open"), errors="coerce"),
                "high": pd.to_numeric(df.get("high"), errors="coerce"),
                "low": pd.to_numeric(df.get("low"), errors="coerce"),
                "close": pd.to_numeric(df.get("close"), errors="coerce"),
                "volume": pd.to_numeric(df.get("volume"), errors="coerce"),
            }
        )
        return normalize_ohlcv(out)

    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date, adjusted: bool = True
    ) -> OHLCVFrame:
        return self._historical(ticker, start, end, interval="1day")

    def get_weekly_ohlcv(self, ticker: str, start: date, end: date) -> OHLCVFrame:
        return self._historical(ticker, start, end, interval="1week")

    def get_latest_quote(self, ticker: str) -> Quote | None:
        self._check_key()
        try:
            r = requests.get(
                f"{TWELVEDATA_BASE}/quote",
                params={"symbol": ticker.upper(), "apikey": self.env.TWELVEDATA_API_KEY},
                timeout=self.cfg.request_timeout_seconds,
            )
            if r.status_code != 200:
                return None
            row = r.json()
            if not isinstance(row, dict) or row.get("status") == "error":
                return None
            price = row.get("close") or row.get("previous_close")
            if price is None:
                return None
            vol = row.get("volume")
            return Quote(
                ticker=ticker,
                price=float(price),
                volume=int(float(vol)) if vol not in (None, "") else None,
                timestamp=datetime.now(timezone.utc),
                provider=self.name,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("twelvedata.quote_failed", ticker=ticker, error=str(e)[:160])
            return None

    def validate_symbol(self, ticker: str) -> bool:
        return self.get_latest_quote(ticker) is not None

    def health_check(self) -> ProviderHealth:
        try:
            self._check_key()
            q = self.get_latest_quote("AAPL")
            return ProviderHealth(
                provider=self.name,
                healthy=q is not None,
                detail="quote_ok" if q is not None else "no_quote",
            )
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(provider=self.name, healthy=False, detail=str(e)[:160])
