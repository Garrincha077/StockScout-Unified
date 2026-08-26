from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_scout.data.base import (
    OHLCV_COLUMNS,
    DataIntegrityError,
    OHLCVFrame,
    normalize_ohlcv,
)
from stock_scout.utils.logging import get_logger
from stock_scout.utils.paths import cache_path_for, ensure_dir, safe_ticker_filename

log = get_logger(__name__)
# Read-side floor, kept in step with normalize_ohlcv. A filter (not a rejection)
# is right here because this defends against legacy contaminated parquets that
# already exist on disk; see base.py for why the cutoff is no longer 1990.
_MIN_PLAUSIBLE_DATE = pd.Timestamp("1900-01-01")
_EPOCH_DATE = pd.Timestamp("1970-01-01")


@dataclass
class CacheMetadata:
    ticker: str
    provider: str
    frequency: str          # "daily" | "weekly"
    last_updated: str       # ISO timestamp (UTC)
    start_date: str         # ISO date
    end_date: str           # ISO date
    rows: int
    adjusted: bool
    quality_status: str     # OK / WARNING / etc.
    # Earliest `start` we have ever requested a full fetch from. Lets the
    # orchestrator tell "the provider has no earlier data" (start_date stays put
    # because the stock IPO'd later) apart from "we never asked that far back",
    # so post-IPO names don't trigger a wasteful full refetch on every run.
    # Defaulted so legacy parquets (written before this field) still deserialize.
    requested_start: str = ""

    @classmethod
    def from_frame(
        cls,
        df: OHLCVFrame,
        ticker: str,
        provider: str,
        frequency: str,
        adjusted: bool,
        quality_status: str = "OK",
        requested_start: str = "",
    ) -> CacheMetadata:
        if df.empty:
            today = datetime.now(timezone.utc).date().isoformat()
            return cls(
                ticker=ticker,
                provider=provider,
                frequency=frequency,
                last_updated=datetime.now(timezone.utc).isoformat(),
                start_date=today,
                end_date=today,
                rows=0,
                adjusted=adjusted,
                quality_status="INSUFFICIENT_DATA",
                requested_start=requested_start,
            )
        return cls(
            ticker=ticker,
            provider=provider,
            frequency=frequency,
            last_updated=datetime.now(timezone.utc).isoformat(),
            start_date=str(df.index.min().date()),
            end_date=str(df.index.max().date()),
            rows=int(len(df)),
            adjusted=adjusted,
            quality_status=quality_status,
            requested_start=requested_start,
        )


class ParquetCache:
    """Per-ticker Parquet cache with embedded metadata.

    Layout:   {base_dir}/{provider}/{frequency}/{TICKER}.parquet
    Metadata: stored in the file's schema metadata (k-v bytes), so a single file
              is fully self-describing.
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        ensure_dir(self.base_dir)

    # ---- paths ----------------------------------------------------------------

    def path(self, provider: str, ticker: str, frequency: str = "daily") -> Path:
        return cache_path_for(self.base_dir, provider, ticker, frequency)

    # ---- read -----------------------------------------------------------------

    def read(self, provider: str, ticker: str, frequency: str = "daily") -> OHLCVFrame:
        p = self.path(provider, ticker, frequency)
        if not p.exists():
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        df = pd.read_parquet(p)
        # We wrote the index as a column on disk — historically named "date"
        # but yfinance frames came in with index.name="Date", so legacy parquet
        # files may have either spelling. Normalize both.
        for cand in ("date", "Date"):
            if cand in df.columns:
                # Same defensive logic as normalize_ohlcv: refuse to coerce
                # integer/float "date" columns (they were written from a
                # RangeIndex and silently became 1970+nanos).
                col_dtype = df[cand].dtype
                if pd.api.types.is_integer_dtype(col_dtype) or pd.api.types.is_float_dtype(col_dtype):
                    log.warning(
                        "cache.read.numeric_date_column",
                        provider=provider,
                        ticker=ticker,
                        dtype=str(col_dtype),
                    )
                    return pd.DataFrame(columns=OHLCV_COLUMNS)
                df[cand] = pd.to_datetime(df[cand], errors="coerce")
                df = df.set_index(cand)
                df.index.name = None
                break
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                df.index = pd.to_datetime(df.index, errors="raise")
            except Exception:
                log.warning("cache.read.bad_index", provider=provider, ticker=ticker)
                return pd.DataFrame(columns=OHLCV_COLUMNS)
        # Strip NaT, implausibly old, and epoch-corrupted rows defensively —
        # protects callers from legacy contaminated parquets still on disk.
        if df.index.hasnans:
            df = df[~df.index.isna()]
        bad_mask = df.index < _MIN_PLAUSIBLE_DATE
        # The 1970-epoch bug's fingerprint: bars on the epoch date itself (New
        # Year's Day, never an EOD bar) or nanosecond components.
        bad_mask |= (df.index.normalize() == _EPOCH_DATE) | (df.index.nanosecond != 0)
        if bad_mask.any():
            log.warning(
                "cache.read.dropped_implausible_rows",
                provider=provider,
                ticker=ticker,
                dropped=int(bad_mask.sum()),
            )
            df = df[~bad_mask]
        return df.sort_index()

    def read_metadata(self, provider: str, ticker: str, frequency: str = "daily") -> CacheMetadata | None:
        p = self.path(provider, ticker, frequency)
        if not p.exists():
            return None
        try:
            schema = pq.read_schema(p)
            meta = schema.metadata or {}
            raw = meta.get(b"scout_meta")
            if not raw:
                return None
            data = json.loads(raw.decode("utf-8"))
            return CacheMetadata(**data)
        except Exception:
            return None

    # ---- write ----------------------------------------------------------------

    def write(
        self,
        df: OHLCVFrame,
        provider: str,
        ticker: str,
        frequency: str = "daily",
        adjusted: bool = True,
        quality_status: str = "OK",
        requested_start: str | None = None,
    ) -> CacheMetadata:
        # Preserve any previously-recorded backfill floor unless the caller
        # explicitly supplies one (so incremental merges don't reset it).
        if requested_start is None:
            prior = self.read_metadata(provider, ticker, frequency)
            requested_start = prior.requested_start if prior is not None else ""
        try:
            df = normalize_ohlcv(df)
        except DataIntegrityError as e:
            log.error(
                "cache.write.rejected_integrity",
                provider=provider,
                ticker=ticker,
                error=str(e),
            )
            # Return metadata describing the rejection — do NOT contaminate disk.
            return CacheMetadata(
                ticker=ticker,
                provider=provider,
                frequency=frequency,
                last_updated=datetime.now(timezone.utc).isoformat(),
                start_date="",
                end_date="",
                rows=0,
                adjusted=adjusted,
                quality_status="INTEGRITY_REJECTED",
                requested_start=requested_start or "",
            )
        p = self.path(provider, ticker, frequency)
        meta = CacheMetadata.from_frame(
            df, ticker, provider, frequency, adjusted, quality_status, requested_start or ""
        )

        if df.empty:
            # Don't write empty frames — but still record metadata via a sidecar
            sidecar = p.with_suffix(".empty.json")
            sidecar.write_text(json.dumps(asdict(meta)), encoding="utf-8")
            return meta

        # Normalize the index column name to "date" before writing so consumers
        # don't have to guess between "Date" / "date" / "index".
        df_to_write = df.copy()
        df_to_write.index.name = "date"
        table = pa.Table.from_pandas(df_to_write.reset_index(), preserve_index=False)
        schema_meta = {b"scout_meta": json.dumps(asdict(meta)).encode("utf-8")}
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), **schema_meta})

        tmp = p.with_suffix(p.suffix + ".tmp")
        pq.write_table(table, tmp, compression="snappy")
        tmp.replace(p)
        return meta

    def merge_append(
        self,
        new_df: OHLCVFrame,
        provider: str,
        ticker: str,
        frequency: str = "daily",
        adjusted: bool = True,
    ) -> CacheMetadata:
        """Append new bars to existing cache, de-duplicating by date (keep last)."""
        existing = self.read(provider, ticker, frequency)
        try:
            new_df = normalize_ohlcv(new_df)
        except DataIntegrityError as e:
            log.warning(
                "cache.merge.rejected_new_df",
                provider=provider,
                ticker=ticker,
                error=str(e),
            )
            # Keep existing rather than contaminate with bad new data.
            return self.write(existing, provider, ticker, frequency, adjusted)

        if existing.empty:
            combined = new_df
        elif new_df.empty:
            # Nothing to merge — skip the rewrite entirely and keep the file +
            # its embedded metadata (including the backfill floor) untouched.
            meta = self.read_metadata(provider, ticker, frequency)
            if meta is not None:
                return meta
            combined = existing
        else:
            combined = pd.concat([existing, new_df])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
            # No-op guard: if the merge changed nothing (same index + values,
            # common when a same-day rerun re-fetches already-current bars),
            # don't rewrite the parquet — just return the existing metadata.
            if combined.equals(existing):
                meta = self.read_metadata(provider, ticker, frequency)
                if meta is not None:
                    return meta
        return self.write(combined, provider, ticker, frequency, adjusted)

    # ---- maintenance ----------------------------------------------------------

    def force_invalidate(self, provider: str, ticker: str, frequency: str | None = None) -> int:
        """Delete cache file(s) for a ticker. Returns number of files removed."""
        removed = 0
        frequencies = [frequency] if frequency else ["daily", "weekly"]
        for freq in frequencies:
            p = self.path(provider, ticker, freq)
            if p.exists():
                p.unlink()
                removed += 1
            sidecar = p.with_suffix(".empty.json")
            if sidecar.exists():
                sidecar.unlink()
        return removed

    def list_tickers(self, provider: str, frequency: str = "daily") -> list[str]:
        d = self.base_dir / provider / frequency
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.parquet"))

    def last_cached_date(
        self, provider: str, ticker: str, frequency: str = "daily"
    ) -> date | None:
        meta = self.read_metadata(provider, ticker, frequency)
        if meta is None or meta.rows == 0:
            return None
        try:
            return date.fromisoformat(meta.end_date)
        except Exception:
            return None

    @staticmethod
    def safe_filename(ticker: str) -> str:
        return safe_ticker_filename(ticker)
