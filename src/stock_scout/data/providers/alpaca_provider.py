from __future__ import annotations

import concurrent.futures
from datetime import date, datetime, timezone

import pandas as pd

from stock_scout.config.schema import AlpacaConfig, Env
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


def _alpaca_symbol(ticker: str) -> str:
    """Alpaca uses '.' for class shares (BRK.B)."""
    return ticker.replace("-", ".").upper()


class AlpacaDataProvider(BaseDataProvider):
    """Alpaca adapter (alpaca-py). Free tier: IEX feed, 15-min delayed.

    Good for validation cross-check and future paper trading.
    """

    name = "alpaca"

    def __init__(self, cfg: AlpacaConfig, env: Env):
        self.cfg = cfg
        self.env = env
        self._hist_client = None
        self._trading_client = None
        self._trade_client = None

    # ---- clients --------------------------------------------------------------

    def _hist(self):
        if self._hist_client is not None:
            return self._hist_client
        try:
            from alpaca.data.historical import StockHistoricalDataClient
        except ImportError as e:  # pragma: no cover
            raise ProviderError("alpaca-py is not installed. Run: pip install alpaca-py") from e
        if not self.env.ALPACA_API_KEY or not self.env.ALPACA_SECRET_KEY:
            raise ProviderError("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set in .env")
        # NOTE: alpaca-py's StockHistoricalDataClient does not expose a
        # per-request network timeout. We enforce one ourselves by running each
        # bars call on a worker thread and abandoning it after
        # cfg.request_timeout_seconds (see _with_timeout), so a hung socket can
        # never block the whole scan.
        self._hist_client = StockHistoricalDataClient(
            api_key=self.env.ALPACA_API_KEY,
            secret_key=self.env.ALPACA_SECRET_KEY,
        )
        return self._hist_client

    def _with_timeout(self, fn, *args, **kwargs):
        """Run `fn` with a per-request wall-clock timeout from config.

        alpaca-py has no native timeout knob, so we submit the blocking call to
        a one-shot worker thread and give up after `request_timeout_seconds`.
        On timeout the executor is shut down without waiting (the orphaned
        request unwinds when its own socket times out) and the error propagates
        to the caller's broad except, which returns an empty frame + warns.
        """
        timeout = float(getattr(self.cfg, "request_timeout_seconds", 0) or 0)
        if timeout <= 0:
            return fn(*args, **kwargs)
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=timeout)
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    def _trading(self):
        if self._trading_client is not None:
            return self._trading_client
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as e:  # pragma: no cover
            raise ProviderError("alpaca-py is not installed") from e
        self._trading_client = TradingClient(
            api_key=self.env.ALPACA_API_KEY,
            secret_key=self.env.ALPACA_SECRET_KEY,
            paper=self.cfg.paper_trading,
        )
        return self._trading_client

    # ---- universe -------------------------------------------------------------

    def get_universe(self) -> list[str]:
        try:
            from alpaca.trading.requests import GetAssetsRequest
            from alpaca.trading.enums import AssetClass, AssetStatus

            req = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
            assets = self._trading().get_all_assets(req)
            return sorted({a.symbol for a in assets if a.tradable})
        except Exception as e:
            log.warning("alpaca.universe_failed", error=str(e))
            return []

    # ---- OHLCV ----------------------------------------------------------------

    def _bars(self, ticker: str, start: date, end: date, timeframe) -> OHLCVFrame:
        try:
            from alpaca.data.requests import StockBarsRequest

            req = StockBarsRequest(
                symbol_or_symbols=_alpaca_symbol(ticker),
                timeframe=timeframe,
                start=datetime.combine(start, datetime.min.time()),
                end=datetime.combine(end, datetime.min.time()),
                feed=self.cfg.data_feed,
            )
            bars = self._with_timeout(self._hist().get_stock_bars, req)
            df = bars.df
            if df is None or df.empty:
                return pd.DataFrame()
            if isinstance(df.index, pd.MultiIndex):
                df = df.reset_index(level=0, drop=True)
            return normalize_ohlcv(df.rename(columns=str.lower))
        except Exception as e:
            log.warning("alpaca.bars_failed", ticker=ticker, error=str(e))
            return pd.DataFrame()

    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date, adjusted: bool = True
    ) -> OHLCVFrame:
        from alpaca.data.timeframe import TimeFrame

        return self._bars(ticker, start, end, TimeFrame.Day)

    def get_weekly_ohlcv(self, ticker: str, start: date, end: date) -> OHLCVFrame:
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        return self._bars(ticker, start, end, TimeFrame(1, TimeFrameUnit.Week))

    # ---- bulk fetch (Alpaca-specific optimisation) ---------------------------

    def get_bulk_daily_ohlcv(
        self, tickers: list[str], start: date, end: date
    ) -> dict[str, OHLCVFrame]:
        """Fetch daily bars for many tickers in a single API call.

        Alpaca's StockBarsRequest accepts a list of symbols. This is dramatically
        faster than per-ticker calls (one request for 100 tickers vs 100 requests).
        Returns a {ticker -> OHLCVFrame} dict. Missing tickers get an empty frame.
        """
        return self._bulk_bars(tickers, start, end, freq="daily")

    def get_bulk_weekly_ohlcv(
        self, tickers: list[str], start: date, end: date
    ) -> dict[str, OHLCVFrame]:
        """Bulk weekly counterpart."""
        return self._bulk_bars(tickers, start, end, freq="weekly")

    def _bulk_bars(
        self, tickers: list[str], start: date, end: date, freq: str
    ) -> dict[str, OHLCVFrame]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        if not tickers:
            return {}
        out: dict[str, OHLCVFrame] = {t.upper(): pd.DataFrame() for t in tickers}
        symbols = [_alpaca_symbol(t) for t in tickers]
        tf = TimeFrame.Day if freq == "daily" else TimeFrame(1, TimeFrameUnit.Week)
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=tf,
                start=datetime.combine(start, datetime.min.time()),
                end=datetime.combine(end, datetime.min.time()),
                feed=self.cfg.data_feed,
            )
            bars = self._with_timeout(self._hist().get_stock_bars, req)
            df = bars.df
            if df is None or df.empty:
                return out
            # df has MultiIndex (symbol, timestamp). Split by first level.
            if isinstance(df.index, pd.MultiIndex):
                for sym, sub in df.groupby(level=0):
                    sub = sub.reset_index(level=0, drop=True)
                    try:
                        normalised = normalize_ohlcv(sub.rename(columns=str.lower))
                    except Exception as e:  # noqa: BLE001
                        log.debug(
                            "alpaca.bulk_normalize_failed",
                            symbol=sym,
                            error=str(e),
                        )
                        continue
                    # Map Alpaca symbol back to upstream ticker
                    upstream = sym.replace(".", "-").upper()
                    out[upstream] = normalised
            else:
                # Single-symbol result — should not happen here but be defensive
                try:
                    normalised = normalize_ohlcv(df.rename(columns=str.lower))
                    out[symbols[0].replace(".", "-").upper()] = normalised
                except Exception as e:  # noqa: BLE001
                    log.debug("alpaca.bulk_single_normalize_failed", symbol=symbols[0], error=repr(e))
        except Exception as e:  # noqa: BLE001
            log.warning(
                "alpaca.bulk_bars_failed",
                ticker_count=len(tickers),
                error=str(e)[:200],
            )
        return out

    # ---- quote / validation ---------------------------------------------------

    def get_latest_quote(self, ticker: str) -> Quote | None:
        try:
            from alpaca.data.requests import StockLatestTradeRequest

            req = StockLatestTradeRequest(
                symbol_or_symbols=_alpaca_symbol(ticker), feed=self.cfg.data_feed
            )
            trade_map = self._with_timeout(self._hist().get_stock_latest_trade, req)
            trade = trade_map.get(_alpaca_symbol(ticker))
            if trade is None:
                return None
            return Quote(
                ticker=ticker,
                price=float(trade.price),
                volume=int(trade.size) if trade.size else None,
                timestamp=trade.timestamp.astimezone(timezone.utc) if trade.timestamp else datetime.now(timezone.utc),
                provider=self.name,
            )
        except Exception as e:
            log.debug("alpaca.quote_failed", ticker=ticker, error=str(e))
            return None

    def validate_symbol(self, ticker: str) -> bool:
        try:
            asset = self._trading().get_asset(_alpaca_symbol(ticker))
            return bool(asset and asset.tradable)
        except Exception:
            return False

    def health_check(self) -> ProviderHealth:
        try:
            self._hist()
            account = self._trading().get_account()
            return ProviderHealth(
                provider=self.name,
                healthy=True,
                detail=f"account_status={account.status} paper={self.cfg.paper_trading}",
            )
        except Exception as e:
            return ProviderHealth(provider=self.name, healthy=False, detail=str(e))
