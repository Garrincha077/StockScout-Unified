from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from datetime import date, datetime, timezone

import pandas as pd

from stock_scout.config.schema import IBKRConfig
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


def _ibkr_symbol(ticker: str) -> str:
    """IBKR uses spaces for class shares (BRK B not BRK.B)."""
    return ticker.replace(".", " ").replace("-", " ").upper()


_MARKET_DATA_TYPE_MAP = {"live": 1, "frozen": 2, "delayed": 3, "delayed_frozen": 4}


class IBKRDataProvider(BaseDataProvider):
    """IBKR adapter using ib_async. Best for execution + per-ticker validation.

    Not recommended as primary for bulk daily EOD on 5k+ tickers due to
    pacing limits. Recommended role: `secondary_validation_provider` or
    `broker_provider`.

    Connection is opened lazily on first use. Caller should `close()` when done.
    """

    name = "ibkr"

    def __init__(self, cfg: IBKRConfig):
        self.cfg = cfg
        self._ib = None
        self._lock = threading.Lock()
        self._request_times: deque[float] = deque(maxlen=cfg.max_requests_per_minute)

    # ---- connection -----------------------------------------------------------

    def _connect(self):
        if self._ib is not None and self._ib.isConnected():
            return self._ib

        # Python 3.10+ stopped auto-creating an event loop in non-async contexts,
        # and 3.14 enforces this strictly. ib_async imports `nest_asyncio` which
        # calls asyncio.get_event_loop() at IMPORT time. Pre-create a loop here
        # before importing ib_async, otherwise the import itself crashes.
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            if loop.is_closed():
                raise RuntimeError("loop closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            from ib_async import IB
        except ImportError as e:  # pragma: no cover
            raise ProviderError(
                "ib_async is not installed. Run: pip install ib_async"
            ) from e

        ib = IB()
        try:
            ib.connect(
                host=self.cfg.host,
                port=self.cfg.port,
                clientId=self.cfg.client_id,
                timeout=10,
                readonly=True,
            )
        except Exception as e:
            raise ProviderError(f"IBKR connection failed ({self.cfg.host}:{self.cfg.port}): {e}") from e

        md_type = _MARKET_DATA_TYPE_MAP.get(self.cfg.market_data_type, 3)
        try:
            ib.reqMarketDataType(md_type)
        except Exception as e:
            log.warning("ibkr.set_market_data_type_failed", error=str(e))

        self._ib = ib
        log.info("ibkr.connected", host=self.cfg.host, port=self.cfg.port, client_id=self.cfg.client_id)
        return ib

    def close(self):
        if self._ib is not None and self._ib.isConnected():
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None

    # ---- pacing ---------------------------------------------------------------

    def _pace(self):
        with self._lock:
            now = time.monotonic()
            window = 60.0
            while self._request_times and now - self._request_times[0] > window:
                self._request_times.popleft()
            if len(self._request_times) >= self.cfg.max_requests_per_minute:
                sleep_for = window - (now - self._request_times[0]) + 0.1
                if sleep_for > 0:
                    log.debug("ibkr.pacing_sleep", seconds=sleep_for)
                    time.sleep(sleep_for)
            self._request_times.append(time.monotonic())

    # ---- universe / OHLCV -----------------------------------------------------

    def get_universe(self) -> list[str]:
        return []  # IBKR doesn't expose a free bulk symbol list

    def _make_contract(self, ticker: str):
        from ib_async import Stock

        return Stock(_ibkr_symbol(ticker), "SMART", "USD")

    def _request_bars(
        self,
        ticker: str,
        end: date,
        duration: str,
        bar_size: str,
    ) -> OHLCVFrame:
        ib = self._connect()
        self._pace()
        contract = self._make_contract(ticker)
        end_dt = datetime.combine(end, datetime.min.time()).strftime("%Y%m%d-23:59:59")
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_dt,
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
        except Exception as e:
            log.warning("ibkr.hist_failed", ticker=ticker, error=str(e))
            return pd.DataFrame()

        if not bars:
            return pd.DataFrame()

        df = pd.DataFrame(
            [
                {
                    "date": pd.Timestamp(b.date),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
            ]
        )
        df = df.set_index("date")
        return normalize_ohlcv(df)

    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date, adjusted: bool = True
    ) -> OHLCVFrame:
        days = (end - start).days
        if days <= 0:
            return pd.DataFrame()
        years = max(1, days // 365 + 1)
        duration = f"{years} Y"
        df = self._request_bars(ticker, end, duration=duration, bar_size="1 day")
        if df.empty:
            return df
        return df.loc[str(start) : str(end)]

    def get_weekly_ohlcv(self, ticker: str, start: date, end: date) -> OHLCVFrame:
        days = (end - start).days
        years = max(1, days // 365 + 1)
        df = self._request_bars(ticker, end, duration=f"{years} Y", bar_size="1 week")
        if df.empty:
            return df
        return df.loc[str(start) : str(end)]

    # ---- quote / validation ---------------------------------------------------

    def get_latest_quote(self, ticker: str) -> Quote | None:
        try:
            ib = self._connect()
        except ProviderError:
            return None
        self._pace()
        contract = self._make_contract(ticker)
        try:
            ticker_obj = ib.reqMktData(contract, "", False, False)
            ib.sleep(2.0)
            price = ticker_obj.last or ticker_obj.close
            if price is None or price != price:  # NaN check
                return None
            return Quote(
                ticker=ticker,
                price=float(price),
                volume=int(ticker_obj.volume) if ticker_obj.volume else None,
                timestamp=datetime.now(timezone.utc),
                provider=self.name,
            )
        except Exception as e:
            log.debug("ibkr.quote_failed", ticker=ticker, error=str(e))
            return None
        finally:
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass

    def validate_symbol(self, ticker: str) -> bool:
        try:
            ib = self._connect()
        except ProviderError:
            return False
        self._pace()
        try:
            details = ib.reqContractDetails(self._make_contract(ticker))
            return bool(details)
        except Exception:
            return False

    def health_check(self) -> ProviderHealth:
        try:
            ib = self._connect()
            healthy = ib.isConnected()
            return ProviderHealth(
                provider=self.name,
                healthy=healthy,
                detail=f"connected={healthy} server_version={ib.client.serverVersion() if healthy else 'n/a'}",
            )
        except ProviderError as e:
            return ProviderHealth(provider=self.name, healthy=False, detail=str(e))
