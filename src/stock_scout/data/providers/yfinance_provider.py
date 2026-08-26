from __future__ import annotations

import random
import threading
import time
from datetime import date, datetime, timezone

import pandas as pd

from stock_scout.config.schema import YFinanceConfig
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


def _to_yf_symbol(ticker: str) -> str:
    """yfinance uses '-' for class shares (BRK-B not BRK.B)."""
    return ticker.replace(".", "-").upper()


def _ipo_date_from_info(info: dict) -> str | None:
    """Best-effort first-trade / IPO date as ISO 'YYYY-MM-DD' from a yfinance
    info dict. Tries the epoch fields (seconds, then milliseconds) and finally a
    literal 'ipoDate' string. Returns None when nothing usable is present."""
    epoch = info.get("firstTradeDateEpochUtc")
    if epoch is not None:
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    ms = info.get("firstTradeDateMilliseconds")
    if ms is not None:
        try:
            return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    raw = info.get("ipoDate") or info.get("ipoExpectedDate")
    if isinstance(raw, str) and len(raw) >= 10:
        return raw[:10]
    return None


def _looks_like_rate_limit(err: BaseException) -> bool:
    msg = str(err).lower()
    return (
        "too many requests" in msg
        or "rate limit" in msg
        or "rate-limit" in msg
        or "429" in msg
    )


class _TokenBucket:
    """Simple thread-safe token bucket: refills `rate` tokens per second up
    to a small burst capacity. acquire() blocks until a token is available."""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = max(0.01, float(rate))
        self.capacity = float(capacity if capacity is not None else max(1.0, rate))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait = deficit / self.rate
            time.sleep(wait)


class _PoolPause:
    """Tracks consecutive rate-limit hits across all workers and lets the
    pool sleep collectively when a threshold is breached. Resets on success."""

    def __init__(self, threshold: int, pause_seconds: float) -> None:
        self.threshold = max(1, int(threshold))
        self.pause_seconds = max(0.0, float(pause_seconds))
        self._consecutive = 0
        self._sleeping_until = 0.0
        self._lock = threading.Lock()

    def wait_if_paused(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._sleeping_until:
                    return
                sleep_for = self._sleeping_until - now
            time.sleep(min(sleep_for, 5.0))

    def record_rate_limit(self) -> bool:
        """Returns True if this hit caused a pool-wide pause to start."""
        with self._lock:
            self._consecutive += 1
            if self._consecutive >= self.threshold:
                self._sleeping_until = time.monotonic() + self.pause_seconds
                self._consecutive = 0
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._consecutive = 0


class YFinanceDataProvider(BaseDataProvider):
    """yfinance adapter. Primary cheap-EOD source. Unofficial; pin versions.

    Throttling layers (applied in order to fight Yahoo's anti-bot rate limit):

      1. `_TokenBucket` enforces a global RPS ceiling shared across all worker
         threads. Default 2 req/s.
      2. `_PoolPause` tracks consecutive rate-limit replies; after N in a row,
         every worker sleeps for the pause window.
      3. Per-request retry: on rate-limit, sleep `cooldown ± jitter` (~60s)
         before retrying. Generic errors get exponential backoff.
      4. Optional `curl_cffi` session for browser-impersonating TLS handshake
         (yfinance >=0.2.66 supports this). Falls back to requests if missing.
    """

    name = "yfinance"

    def __init__(self, cfg: YFinanceConfig):
        self.cfg = cfg
        # Lazy import — yfinance is heavy
        import yfinance as yf

        self._yf = yf
        self._bucket = _TokenBucket(rate=cfg.requests_per_second)
        self._pause = _PoolPause(
            threshold=cfg.rate_limit_pool_pause_threshold,
            pause_seconds=cfg.rate_limit_pool_pause_seconds,
        )
        self._session = self._build_session() if cfg.use_curl_cffi_session else None
        if self._session is not None:
            log.info("yfinance.session", impersonate="chrome", lib="curl_cffi")
        elif cfg.use_curl_cffi_session:
            # curl_cffi requested but unavailable — without browser-impersonating
            # TLS, Yahoo rate-limits aggressively (429s). Surface this loudly so a
            # machine missing the dependency is diagnosable (it would otherwise
            # silently fall back to plain requests).
            log.warning(
                "yfinance.curl_cffi_missing",
                detail="curl_cffi unavailable; falling back to plain requests "
                "(aggressive Yahoo rate-limiting). Run: pip install 'curl_cffi>=0.7'",
            )
        # yfinance < 0.2.66 does not reliably accept a curl_cffi `session=`, so the
        # impersonation is silently dropped there. Warn so the two-machine setup
        # surfaces an outdated install instead of mysterious 429 storms.
        _ver = getattr(self._yf, "__version__", "0")
        try:
            if tuple(int(p) for p in _ver.split(".")[:3]) < (0, 2, 66):
                log.warning(
                    "yfinance.version_outdated",
                    version=_ver,
                    detail="Upgrade to yfinance>=0.2.66 for curl_cffi session support "
                    "(pip install -U 'yfinance>=0.2.66').",
                )
        except (ValueError, TypeError):
            pass

    @staticmethod
    def _build_session():
        try:
            from curl_cffi import requests as cffi_requests

            return cffi_requests.Session(impersonate="chrome")
        except Exception as e:  # noqa: BLE001
            log.debug("yfinance.curl_cffi_unavailable", error=str(e))
            return None

    # ---- universe -------------------------------------------------------------

    def get_universe(self) -> list[str]:
        # yfinance has no native universe endpoint; orchestrator uses NASDAQ Trader files.
        return []

    # ---- OHLCV ----------------------------------------------------------------

    def _ticker(self, yf_symbol: str):
        if self._session is not None:
            try:
                return self._yf.Ticker(yf_symbol, session=self._session)
            except TypeError:
                # Older yfinance versions don't accept `session=` on Ticker.
                pass
        return self._yf.Ticker(yf_symbol)

    def _download_once(
        self,
        yf_symbol: str,
        start: date,
        end: date,
        interval: str,
        adjusted: bool,
    ) -> pd.DataFrame:
        # yfinance treats `end` as exclusive; add 1 day so we include `end` itself.
        end_exclusive = date.fromordinal(end.toordinal() + 1)
        return self._ticker(yf_symbol).history(
            start=start.isoformat(),
            end=end_exclusive.isoformat(),
            interval=interval,
            auto_adjust=adjusted,
            actions=False,
            raise_errors=False,
            # Hard network timeout so a stalled socket can't freeze a worker.
            timeout=self.cfg.request_timeout_seconds,
        )

    def _download(
        self,
        ticker: str,
        start: date,
        end: date,
        interval: str,
        adjusted: bool,
    ) -> OHLCVFrame:
        yf_symbol = _to_yf_symbol(ticker)
        attempts = max(1, int(self.cfg.retry_attempts))
        last_err: Exception | None = None

        for attempt in range(1, attempts + 1):
            self._pause.wait_if_paused()
            self._bucket.acquire()
            try:
                df = self._download_once(yf_symbol, start, end, interval, adjusted)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if _looks_like_rate_limit(e):
                    paused = self._pause.record_rate_limit()
                    if paused:
                        log.warning(
                            "yfinance.pool_paused",
                            seconds=self.cfg.rate_limit_pool_pause_seconds,
                            ticker=ticker,
                        )
                        continue
                    delay = self.cfg.rate_limit_cooldown_seconds + random.uniform(
                        0.0, self.cfg.rate_limit_cooldown_seconds * 0.5
                    )
                    log.warning(
                        "yfinance.rate_limited",
                        ticker=ticker,
                        attempt=attempt,
                        sleep_seconds=round(delay, 1),
                    )
                    time.sleep(delay)
                    continue
                # Generic transient error: exponential backoff.
                backoff = min(30.0, self.cfg.retry_backoff_seconds * (2 ** (attempt - 1)))
                log.debug(
                    "yfinance.transient_error",
                    ticker=ticker,
                    attempt=attempt,
                    error=str(e),
                    sleep_seconds=round(backoff, 1),
                )
                time.sleep(backoff)
                continue

            if df is None or df.empty:
                self._pause.record_success()
                return pd.DataFrame()

            self._pause.record_success()
            # Strip any tz-info so cache parquet roundtrips cleanly.
            if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            return normalize_ohlcv(df)

        # All attempts exhausted.
        if last_err is not None and _looks_like_rate_limit(last_err):
            log.warning("yfinance.download_failed_rate_limit", ticker=ticker, error=str(last_err))
        else:
            log.warning("yfinance.download_failed", ticker=ticker, error=str(last_err) if last_err else "unknown")
        return pd.DataFrame()

    def get_daily_ohlcv(
        self, ticker: str, start: date, end: date, adjusted: bool = True
    ) -> OHLCVFrame:
        return self._download(ticker, start, end, interval="1d", adjusted=adjusted)

    def get_weekly_ohlcv(self, ticker: str, start: date, end: date) -> OHLCVFrame:
        return self._download(ticker, start, end, interval="1wk", adjusted=True)

    # ---- bulk multi-ticker fetch (yf.download) --------------------------------
    # Enables yfinance to serve the full universe at speed (so we get the CORRECT
    # consolidated volume + official close), mirroring Alpaca's bulk interface so
    # the orchestrator's bulk_prewarm picks it up automatically.

    def _download_bulk(
        self, tickers: list[str], start: date, end: date, interval: str, adjusted: bool = True
    ) -> dict[str, OHLCVFrame]:
        if not tickers:
            return {}
        sym_map = {_to_yf_symbol(t): t.upper() for t in tickers}
        out: dict[str, OHLCVFrame] = {t.upper(): pd.DataFrame() for t in tickers}
        end_exclusive = date.fromordinal(end.toordinal() + 1)
        self._pause.wait_if_paused()
        kwargs = dict(
            tickers=list(sym_map.keys()),
            start=start.isoformat(),
            end=end_exclusive.isoformat(),
            interval=interval,
            # auto_adjust=False is the split-only basis the Stooq archive uses;
            # True additionally folds in dividends.
            auto_adjust=adjusted,
            actions=False,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        try:
            try:
                data = self._yf.download(session=self._session, **kwargs)
            except TypeError:
                # Older yfinance: download() doesn't accept session=.
                data = self._yf.download(**kwargs)
        except Exception as e:  # noqa: BLE001
            if _looks_like_rate_limit(e):
                self._pause.record_rate_limit()
            log.warning("yfinance.bulk_failed", count=len(tickers), error=str(e)[:200])
            return out
        if data is None or data.empty:
            return out
        self._pause.record_success()
        multi = isinstance(data.columns, pd.MultiIndex)
        for yfsym, orig in sym_map.items():
            try:
                if multi:
                    if yfsym not in data.columns.get_level_values(0):
                        continue
                    sub = data[yfsym]
                else:
                    sub = data  # single-ticker fallback shape
                sub = sub.dropna(how="all")
                if sub.empty:
                    continue
                df = pd.DataFrame(
                    {
                        "open": sub["Open"], "high": sub["High"], "low": sub["Low"],
                        "close": sub["Close"], "volume": sub["Volume"],
                    }
                )
                if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                out[orig] = normalize_ohlcv(df)
            except Exception as e:  # noqa: BLE001
                # Per-symbol slice of a bulk download failed (missing column,
                # integrity reject, …). Skip just this ticker but log the cause so
                # a systematic bulk-shape regression doesn't vanish silently.
                log.debug("yfinance.bulk_symbol_skipped", ticker=orig, error=repr(e))
                continue
        return out

    def get_bulk_daily_ohlcv(
        self, tickers: list[str], start: date, end: date, adjusted: bool = True
    ) -> dict[str, OHLCVFrame]:
        return self._download_bulk(tickers, start, end, interval="1d", adjusted=adjusted)

    def get_bulk_weekly_ohlcv(
        self, tickers: list[str], start: date, end: date
    ) -> dict[str, OHLCVFrame]:
        return self._download_bulk(tickers, start, end, interval="1wk")

    # ---- quote ----------------------------------------------------------------

    def get_latest_quote(self, ticker: str) -> Quote | None:
        try:
            self._pause.wait_if_paused()
            self._bucket.acquire()
            t = self._ticker(_to_yf_symbol(ticker))
            fi = t.fast_info
            price = float(fi.last_price) if fi.last_price is not None else None
            if price is None:
                return None
            return Quote(
                ticker=ticker,
                price=price,
                volume=int(fi.last_volume) if fi.last_volume is not None else None,
                timestamp=datetime.now(timezone.utc),
                provider=self.name,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("yfinance.quote_failed", ticker=ticker, error=str(e))
            return None

    # ---- profile --------------------------------------------------------------

    def get_company_profile(self, ticker: str) -> CompanyProfile | None:
        """Slow + flaky for bulk use. Caller should restrict to shortlist."""
        try:
            self._bucket.acquire()
            t = self._ticker(_to_yf_symbol(ticker))
            info = t.info or {}
            return CompanyProfile(
                ticker=ticker,
                name=info.get("longName") or info.get("shortName"),
                exchange=info.get("exchange"),
                sector=info.get("sector"),
                industry=info.get("industry"),
                country=info.get("country"),
                ipo_date=_ipo_date_from_info(info),
            )
        except Exception as e:  # noqa: BLE001
            log.debug("yfinance.company_profile_failed", ticker=ticker, error=repr(e))
            return None

    def get_sector_industry(self, ticker: str) -> SectorInfo | None:
        prof = self.get_company_profile(ticker)
        if prof is None:
            return None
        return SectorInfo(ticker=ticker, sector=prof.sector, industry=prof.industry)

    # ---- earnings calendar ---------------------------------------------------

    def get_next_earnings_date(self, ticker: str) -> date | None:
        """Best-effort earnings date via `Ticker.calendar`. Returns None if
        the field is missing or parsing fails. yfinance throttles harder on
        this endpoint — keep callers under ~250 tickers/run."""
        try:
            self._bucket.acquire()
            t = self._ticker(_to_yf_symbol(ticker))
            cal = t.calendar
            if cal is None:
                return None
            # Calendar can be dict-like or DataFrame depending on yfinance version.
            ed = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date") or cal.get("earningsDate")
                if isinstance(ed, list) and ed:
                    ed = ed[0]
            else:
                # DataFrame: first column, "Earnings Date" row.
                try:
                    ed = cal.loc["Earnings Date"][0]
                except Exception:  # noqa: BLE001
                    ed = None
            if ed is None:
                return None
            ts = pd.Timestamp(ed)
            return ts.date()
        except Exception as e:  # noqa: BLE001
            log.debug("yfinance.earnings_date_failed", ticker=ticker, error=str(e))
            return None

    def get_share_stats(self, ticker: str) -> dict | None:
        """Best-effort float + short-interest snapshot via `Ticker.info`.

        Returns a dict with float_shares, shares_outstanding, short_pct_float,
        short_ratio (any may be None). yfinance throttles `.info` hard — keep
        callers to the top-N candidates and cache the result.
        """
        try:
            self._bucket.acquire()
            t = self._ticker(_to_yf_symbol(ticker))
            try:
                info = t.get_info()
            except Exception:  # noqa: BLE001
                info = getattr(t, "info", None)
            if not info:
                return None

            def _num(key: str):
                v = info.get(key)
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            spf = _num("shortPercentOfFloat")
            if spf is not None and spf <= 1.0:
                spf *= 100.0  # yfinance returns a fraction; normalise to %.
            return {
                "float_shares": _num("floatShares"),
                "shares_outstanding": _num("sharesOutstanding"),
                "short_pct_float": spf,
                "short_ratio": _num("shortRatio"),
            }
        except Exception as e:  # noqa: BLE001
            log.debug("yfinance.share_stats_failed", ticker=ticker, error=str(e))
            return None

    # ---- misc -----------------------------------------------------------------

    def validate_symbol(self, ticker: str) -> bool:
        q = self.get_latest_quote(ticker)
        return q is not None and q.price > 0

    def health_check(self) -> ProviderHealth:
        try:
            df = self._download(
                "SPY",
                date.today().replace(year=date.today().year - 1),
                date.today(),
                "1d",
                True,
            )
            ok = not df.empty
            return ProviderHealth(provider=self.name, healthy=ok, detail=f"SPY 1y rows={len(df)}")
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(provider=self.name, healthy=False, detail=str(e))
