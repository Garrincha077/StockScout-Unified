from __future__ import annotations

import time
from datetime import date, datetime, timezone

import pandas as pd
import requests

from stock_scout.config.schema import Env, FMPConfig
from stock_scout.data.base import (
    BaseDataProvider,
    CompanyProfile,
    OHLCVFrame,
    ProviderError,
    ProviderHealth,
    Quote,
    SectorInfo,
    normalize_ohlcv,
)
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

FMP_BASE = "https://financialmodelingprep.com/stable"


class FMPDataProvider(BaseDataProvider):
    """FinancialModelingPrep adapter. Free tier (250 req/day) is enough for
    occasional EOD validation + the bulk profile/sector endpoint for the
    full universe in a single call."""

    name = "fmp"

    def __init__(self, cfg: FMPConfig, env: Env):
        self.cfg = cfg
        self.env = env
        if not env.FMP_API_KEY:
            log.warning("fmp.no_api_key")

    @property
    def _key(self) -> str:
        if not self.env.FMP_API_KEY:
            raise ProviderError("FMP_API_KEY is not set in .env")
        return self.env.FMP_API_KEY

    def _get_with_retry(self, url: str, params: dict, timeout: int):
        """GET with bounded exponential backoff. 404 returns immediately (no
        retry); 5xx / network errors retry up to `retry_attempts`."""
        attempts = max(1, int(getattr(self.cfg, "retry_attempts", 3)))
        backoff = float(getattr(self.cfg, "retry_backoff_seconds", 2.0))
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                r = requests.get(url, params=params, timeout=timeout)
                if r.status_code == 404:
                    return r
                if r.status_code >= 500:
                    raise requests.HTTPError(f"{r.status_code} server error")
                r.raise_for_status()
                return r
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < attempts:
                    time.sleep(backoff * attempt)
                continue
        raise last_err if last_err else RuntimeError("fmp request failed")

    def get_universe(self) -> list[str]:
        try:
            r = requests.get(
                f"{FMP_BASE}/stock-list",
                params={"apikey": self._key},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            return sorted({d["symbol"] for d in data if d.get("symbol")})
        except Exception as e:
            log.warning("fmp.universe_failed", error=str(e))
            return []

    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date, adjusted: bool = True
    ) -> OHLCVFrame:
        try:
            # Stable API: /historical-price-eod/full returns the adjusted series.
            url = f"{FMP_BASE}/historical-price-eod/full"
            r = self._get_with_retry(
                url,
                params={
                    "symbol": ticker.upper(),
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "apikey": self._key,
                },
                timeout=30,
            )
            if r.status_code == 404:
                return pd.DataFrame()
            payload = r.json()
            # Stable endpoint returns either a list or {historical: [...]}; handle both.
            hist = payload if isinstance(payload, list) else (payload.get("historical") or [])
            if not hist:
                return pd.DataFrame()
            df = pd.DataFrame(hist)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            out = pd.DataFrame(
                {
                    "open": df.get("adjOpen", df.get("open")),
                    "high": df.get("adjHigh", df.get("high")),
                    "low": df.get("adjLow", df.get("low")),
                    "close": df.get("adjClose", df.get("close")),
                    "volume": df.get("volume"),
                }
            )
            return normalize_ohlcv(out)
        except Exception as e:
            log.warning("fmp.hist_failed", ticker=ticker, error=str(e))
            return pd.DataFrame()

    def get_weekly_ohlcv(self, ticker: str, start: date, end: date) -> OHLCVFrame:
        daily = self.get_daily_ohlcv(ticker, start, end)
        if daily.empty:
            return daily
        agg = daily.resample("W-FRI").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        return agg.dropna(how="all")

    def get_latest_quote(self, ticker: str) -> Quote | None:
        try:
            r = requests.get(
                f"{FMP_BASE}/quote-short",
                params={"symbol": ticker.upper(), "apikey": self._key},
                timeout=15,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if not data:
                return None
            row = data[0] if isinstance(data, list) else data
            price = row.get("price")
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
            log.debug("fmp.quote_failed", ticker=ticker, error=str(e))
            return None

    def get_company_profile(self, ticker: str) -> CompanyProfile | None:
        try:
            r = requests.get(
                f"{FMP_BASE}/profile",
                params={"symbol": ticker.upper(), "apikey": self._key},
                timeout=15,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if not data:
                return None
            row = data[0] if isinstance(data, list) else data
            return CompanyProfile(
                ticker=ticker,
                name=row.get("companyName"),
                exchange=row.get("exchangeShortName"),
                sector=row.get("sector"),
                industry=row.get("industry"),
                country=row.get("country"),
            )
        except Exception:
            return None

    def get_sector_industry(self, ticker: str) -> SectorInfo | None:
        prof = self.get_company_profile(ticker)
        if prof is None:
            return None
        return SectorInfo(ticker=ticker, sector=prof.sector, industry=prof.industry)

    def get_bulk_sectors(self) -> pd.DataFrame:
        """Bulk endpoint: one call returns the full sector map. Use this instead
        of per-ticker get_sector_industry() when populating the universe."""
        try:
            r = requests.get(
                f"{FMP_BASE}/company-screener",
                params={"limit": 20000, "apikey": self._key},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            if not data:
                return pd.DataFrame()
            df = pd.DataFrame(data)
            keep = [c for c in ["symbol", "companyName", "sector", "industry", "exchange", "country"] if c in df.columns]
            return df[keep].rename(columns={"symbol": "ticker", "companyName": "name"})
        except Exception as e:
            log.warning("fmp.bulk_sectors_failed", error=str(e))
            return pd.DataFrame()

    def get_insider_buys(self, ticker: str, window_days: int = 90) -> dict | None:
        """Recent insider transactions for a ticker. Returns a small summary
        dict {buy_count, sell_count, net_transactions, window_days, insider_buying}
        over the trailing `window_days`. Best-effort — None on failure.

        Open-market PURCHASES (transactionType starting 'P') are a strong
        conviction signal; sales are noisier (often tax/diversification).
        """
        try:
            r = requests.get(
                f"{FMP_BASE}/insider-trading/search",
                params={"symbol": ticker.upper(), "page": 0, "limit": 100, "apikey": self._key},
                timeout=20,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if not isinstance(data, list) or not data:
                return {"buy_count": 0, "sell_count": 0, "net_transactions": 0,
                        "window_days": window_days, "insider_buying": False}
            cutoff = (datetime.now(timezone.utc).date() - pd.Timedelta(days=window_days).to_pytimedelta())
            buys = sells = 0
            for row in data:
                dt_raw = row.get("transactionDate") or row.get("filingDate")
                try:
                    tdate = pd.Timestamp(dt_raw).date()
                except Exception:  # noqa: BLE001
                    continue
                if tdate < cutoff:
                    continue
                ttype = str(row.get("transactionType", "")).upper()
                acq = str(row.get("acquisitionOrDisposition", "")).upper()
                is_buy = ttype.startswith("P") or acq == "A"
                is_sell = ttype.startswith("S") or acq == "D"
                if is_buy:
                    buys += 1
                elif is_sell:
                    sells += 1
            return {
                "buy_count": buys,
                "sell_count": sells,
                "net_transactions": buys - sells,
                "window_days": window_days,
                "insider_buying": buys > sells and buys >= 2,
            }
        except Exception as e:  # noqa: BLE001
            log.debug("fmp.insider_failed", ticker=ticker, error=str(e))
            return None

    def get_share_float(self, ticker: str) -> dict | None:
        """Float + outstanding shares via FMP /shares-float. None on failure."""
        try:
            r = requests.get(
                f"{FMP_BASE}/shares-float",
                params={"symbol": ticker.upper(), "apikey": self._key},
                timeout=20,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if not data:
                return None
            row = data[0] if isinstance(data, list) else data

            def _num(key: str):
                v = row.get(key)
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            return {
                "float_shares": _num("floatShares"),
                "shares_outstanding": _num("outstandingShares"),
            }
        except Exception as e:  # noqa: BLE001
            log.debug("fmp.float_failed", ticker=ticker, error=str(e))
            return None

    def validate_symbol(self, ticker: str) -> bool:
        return self.get_latest_quote(ticker) is not None

    def health_check(self) -> ProviderHealth:
        try:
            r = requests.get(
                f"{FMP_BASE}/quote-short",
                params={"symbol": "SPY", "apikey": self._key},
                timeout=10,
            )
            return ProviderHealth(
                provider=self.name,
                healthy=r.status_code == 200,
                detail=f"status={r.status_code}",
            )
        except Exception as e:
            return ProviderHealth(provider=self.name, healthy=False, detail=str(e))
