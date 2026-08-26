from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

import pandas as pd

# Canonical OHLCV columns expected from every provider.
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
OHLCVFrame = pd.DataFrame  # type alias for clarity


class DataQuality(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    MISMATCH = "MISMATCH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Quote:
    ticker: str
    price: float
    volume: int | None
    timestamp: datetime
    provider: str


@dataclass(frozen=True)
class CompanyProfile:
    ticker: str
    name: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    # First-trade / IPO date as an ISO "YYYY-MM-DD" string when known. Used to
    # bucket tickers into IPO-year watchlists.
    ipo_date: str | None = None


@dataclass(frozen=True)
class SectorInfo:
    ticker: str
    sector: str | None
    industry: str | None


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    healthy: bool
    detail: str
    extra: dict[str, Any] | None = None


class ProviderError(Exception):
    """Raised when a provider cannot fulfil a request (and caller should consider fallback)."""


class DataIntegrityError(Exception):
    """Raised when a frame fails plausibility checks (e.g. non-date index converted
    silently to epoch nanoseconds, NaT bars, dates pre-1990 or in the far future).

    Callers SHOULD catch this and treat the frame as empty (do not write to cache).
    Caused the 1970-01-01 corruption that hit 100 smoke tickers in 2026-05-18."""


# Absolute sanity floor for any bar we'd store. This used to be 1990 as a proxy
# for detecting the RangeIndex→pd.to_datetime bug, but that also rejected real
# deep history (Stooq carries AAPL from 1984 and index series from the 1930s).
# The corruption is now caught directly by its own signature below, so this is
# just a floor for obviously nonsensical dates.
_MIN_PLAUSIBLE_DATE = pd.Timestamp("1900-01-01")

# pd.to_datetime(RangeIndex(0..N)) silently yields 1970-01-01 plus N nanoseconds.
# Real EOD feeds are at worst second-resolution and never land here en masse.
_EPOCH_DATE = pd.Timestamp("1970-01-01")


def _max_plausible_date() -> pd.Timestamp:
    # 7 days into the future tolerates timezone slop and Friday→Monday EOD pulls.
    return pd.Timestamp.utcnow().tz_localize(None) + pd.Timedelta(days=7)


class BaseDataProvider(ABC):
    """Common interface every data provider must implement.

    Adapters should not raise on missing per-ticker data — return empty frames /
    None instead. They should raise ProviderError for systemic issues (auth,
    network down, rate limit exhausted) so the orchestrator can fall back.
    """

    name: str = "base"

    @abstractmethod
    def get_universe(self) -> list[str]:
        """Return the list of tickers this provider can offer. Empty list = use external universe."""

    @abstractmethod
    def get_daily_ohlcv(
        self,
        ticker: str,
        start: date,
        end: date,
        adjusted: bool = True,
    ) -> OHLCVFrame:
        """Return a DataFrame indexed by date with columns: open, high, low, close, volume.

        Empty DataFrame on missing data (not an exception).
        """

    @abstractmethod
    def get_weekly_ohlcv(self, ticker: str, start: date, end: date) -> OHLCVFrame:
        """Return weekly bars. Implementations may compute from daily if no native weekly endpoint."""

    @abstractmethod
    def get_latest_quote(self, ticker: str) -> Quote | None:
        """Return the latest trade/quote, or None if not available."""

    def get_company_profile(self, ticker: str) -> CompanyProfile | None:
        """Optional. Default: not supported."""
        return None

    def get_sector_industry(self, ticker: str) -> SectorInfo | None:
        """Optional. Default: not supported."""
        return None

    @abstractmethod
    def validate_symbol(self, ticker: str) -> bool:
        """Return True if the provider considers this symbol tradeable/known."""

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True, detail="default health check")


def normalize_ohlcv(df: OHLCVFrame) -> OHLCVFrame:
    """Coerce a provider DataFrame into the canonical shape and column names."""
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    out = df.copy()
    # yfinance (and occasionally other providers) can return MultiIndex columns
    # when concurrent fetches collide or upstream changes shape. Flatten to the
    # outermost level (the OHLCV name) — this is the defensive fix for the
    # "Duplicate column names found" bug that hit ~163 mega-caps in 2026-05-17.
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out.columns = [str(c).lower().strip() for c in out.columns]
    # After lowercasing, MultiIndex flatten can produce duplicates (multiple
    # 'open' cols if upstream returned per-ticker stacked frames). Keep first.
    if out.columns.duplicated().any():
        out = out.loc[:, ~out.columns.duplicated(keep="first")]
    rename = {
        "adj close": "close",
        "adj_close": "close",
        "adjclose": "close",
        "vol": "volume",
    }
    out = out.rename(columns=rename)
    # Renaming can itself create duplicates (both 'close' and 'adj close' map
    # to 'close'). Resolve again.
    if out.columns.duplicated().any():
        out = out.loc[:, ~out.columns.duplicated(keep="first")]

    missing = [c for c in OHLCV_COLUMNS if c not in out.columns]
    if missing:
        # Pad with NaN — but caller can detect via .isna()
        for c in missing:
            out[c] = pd.NA

    out = out[OHLCV_COLUMNS].copy()

    # Index integrity: this is the hard guard against the 1970-01-01 corruption.
    # `pd.to_datetime(RangeIndex(0..N))` silently produces nanosecond epoch
    # stamps (1970-01-01 + N ns) without raising — that bug contaminated 100
    # smoke tickers. We now validate explicitly and reject the frame if the
    # resulting index has any pre-1990 or far-future dates.
    if not isinstance(out.index, pd.DatetimeIndex):
        # Only attempt conversion if the index actually looks date-like
        # (object/string dtype). Integer indexes are NEVER valid dates here.
        idx_dtype = out.index.dtype
        if pd.api.types.is_integer_dtype(idx_dtype) or pd.api.types.is_float_dtype(idx_dtype):
            raise DataIntegrityError(
                f"Refusing to coerce numeric index ({idx_dtype}) to datetime — "
                f"would produce 1970-epoch garbage. Provider must return DatetimeIndex."
            )
        try:
            out.index = pd.to_datetime(out.index, errors="raise")
        except Exception as e:
            raise DataIntegrityError(f"Could not parse index as datetime: {e}") from e

    # Strip tz so plausibility comparisons (against tz-naive bounds) work and
    # so the cache parquet roundtrips cleanly. Alpaca returns tz-aware UTC.
    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        out.index = out.index.tz_convert("UTC").tz_localize(None)

    # Post-conversion plausibility: even DatetimeIndex can carry NaT or
    # nonsense dates. Fail loud so callers know to discard the frame.
    if out.index.hasnans:
        raise DataIntegrityError("Index contains NaT values")
    # Epoch corruption, caught by its signature rather than by a year cutoff.
    # Any bar on 1970-01-01 is the tell: it is New Year's Day, never an EOD bar,
    # and pd.to_datetime(RangeIndex) lands every value there for any realistic
    # bar count. Note microseconds are NOT checked — pd.Timestamp.utcnow() has
    # them legitimately.
    epoch_bars = int((out.index.normalize() == _EPOCH_DATE).sum())
    if epoch_bars:
        raise DataIntegrityError(
            f"{epoch_bars} bars land on 1970-01-01 — epoch-nanosecond corruption"
        )
    if (out.index.nanosecond != 0).any():
        raise DataIntegrityError(
            "Index has nanosecond components — a numeric index was coerced to datetime"
        )
    bad_low = (out.index < _MIN_PLAUSIBLE_DATE).sum()
    if bad_low:
        raise DataIntegrityError(
            f"{int(bad_low)} bars have dates before "
            f"{_MIN_PLAUSIBLE_DATE.date()} — implausible for an EOD bar"
        )
    bad_high = (out.index > _max_plausible_date()).sum()
    if bad_high:
        raise DataIntegrityError(f"{int(bad_high)} bars have dates >7d in the future")

    # A bar without a close is not a bar. yfinance's bulk download only drops
    # rows that are entirely NaN (`dropna(how="all")`), so a partially-returned
    # batch — which is what a run full of curl timeouts produces — yields rows
    # with a real open/high/low and a NaN close. Those reached the store: 2166
    # of the 2852 bars written for 2026-07-24 had no close at all, and no
    # integrity check looked for it because `close <= 0` is false for NULL.
    prices = out[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    # A non-positive price is not a price — a zero low is a vendor artefact, not
    # a trade at zero (SPRC 2021-09-29 carried low=0.0 against a ~24570 body).
    # Treat those as missing so the clamp below rebuilds them from the body.
    prices = prices.mask(prices <= 0)
    has_close = prices["close"].notna()
    out = out[has_close].copy()
    prices = prices[has_close]
    prices["open"] = prices["open"].fillna(prices["close"])

    # Vendor feeds occasionally emit a bar whose high does not enclose the body,
    # or whose low does not undercut it. Open and close are the reliable fields,
    # so clamp the wick to bracket them rather than reject the frame — one bad
    # bar must not cost a whole ticker its history. This mirrors the rule the
    # Stooq archive import applies; two policies for one defect would be worse
    # than either. A missing high/low lands here too: NaN fails both
    # comparisons, so it is replaced by the body edge.
    body_high = prices[["open", "close"]].max(axis=1)
    body_low = prices[["open", "close"]].min(axis=1)
    out["open"] = prices["open"]
    out["close"] = prices["close"]
    out["high"] = prices["high"].where(prices["high"] >= body_high, body_high)
    out["low"] = prices["low"].where(prices["low"] <= body_low, body_low)

    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out
