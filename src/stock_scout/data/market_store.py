from __future__ import annotations

import json
import threading
import time
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd
import pandas_market_calendars as mcal

from stock_scout.config.schema import Settings
from stock_scout.data.base import OHLCV_COLUMNS, DataIntegrityError, normalize_ohlcv
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class MissingRange:
    start: date
    end: date


@dataclass(frozen=True)
class RepairTarget:
    ticker: str
    start: date
    end: date
    reason: str


def market_store_path(settings: Settings) -> Path:
    return settings.project_root / settings.marketdata.base_dir / settings.marketdata.duckdb_file


def _duckdb():
    try:
        import duckdb
    except ImportError as e:  # pragma: no cover - covered by dependency install in CI/dev
        raise RuntimeError("duckdb is required. Install project dependencies first.") from e
    return duckdb


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ticker(value: str) -> str:
    return str(value or "").strip().upper()


@lru_cache(maxsize=256)
def _trading_dates_cached(start: date, end: date) -> tuple[date, ...]:
    cal = mcal.get_calendar("NYSE")
    schedule = cal.schedule(start_date=start, end_date=end)
    return tuple(ts.date() for ts in schedule.index)


def _trading_dates(start: date, end: date) -> list[date]:
    if end < start:
        return []
    # missing_ranges() calls this once per ticker over a handful of distinct
    # (start, end) windows, and rebuilding the NYSE calendar each time cost
    # more than the gap detection it feeds. Cached tuple, copied per call so
    # callers can't mutate the shared value.
    return list(_trading_dates_cached(start, end))


def _json_safe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out: list[dict[str, Any]] = []
    for row in df.to_dict("records"):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if pd.isna(value):
                clean[str(key)] = None
            elif isinstance(value, (date, datetime, pd.Timestamp)):
                clean[str(key)] = pd.Timestamp(value).date().isoformat()
            else:
                clean[str(key)] = value
        out.append(clean)
    return out


class MarketDataStore:
    """Local DuckDB-backed market-data store.

    DuckDB is used as the authoritative local read path for daily OHLCV and the
    audit tables that decide which tickers are eligible for fast daily scans.
    Existing per-ticker parquet files can be imported and remain a fallback
    while the store is being bootstrapped.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        read_only: bool = False,
        lock_timeout_seconds: float = 60.0,
    ):
        self.path = Path(path)
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._duckdb = _duckdb()
        self.read_only = bool(read_only)
        self._conn = self._connect_with_retry(lock_timeout_seconds=lock_timeout_seconds)
        if not self.read_only:
            self.init_schema()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        read_only: bool = False,
        lock_timeout_seconds: float = 60.0,
    ) -> "MarketDataStore":
        return cls(market_store_path(settings), read_only=read_only, lock_timeout_seconds=lock_timeout_seconds)

    # Open failures that mean "someone else is still holding this", as opposed
    # to "this store is broken". Only the first kind is worth waiting out.
    #
    # The last two were added after 2026-07-30, when a refresh killed at its
    # timeout left a WAL and the very next command failed with
    #   IO Error: Failure while replaying WAL file "...market.duckdb.wal":
    #   Could not write file "...market.duckdb" (error in WriteFile): Access is
    #   denied.
    # That matched neither of the original two substrings, so `locked` came out
    # False and the open raised on its first attempt - with 30 seconds of budget
    # left unspent, which the traceback shows plainly (deadline 1419.45,
    # locked False). The kill was 2 seconds earlier and the dying process had
    # not let go of the file yet. Waiting was the whole remedy and the code
    # declined to wait, so the night died and, because record-night reads the
    # store too, died silently.
    _TRANSIENT_OPEN_ERRORS = (
        "being used by another process",
        "cannot open file",
        "access is denied",
        "failure while replaying wal",
    )

    def _connect_with_retry(self, *, lock_timeout_seconds: float) -> Any:
        deadline = time.monotonic() + max(0.0, float(lock_timeout_seconds or 0.0))
        last_error: Exception | None = None
        while True:
            try:
                return self._duckdb.connect(str(self.path), read_only=self.read_only)
            except Exception as e:  # noqa: BLE001
                last_error = e
                message = str(e).lower()
                locked = any(s in message for s in self._TRANSIENT_OPEN_ERRORS)
                if not locked or time.monotonic() >= deadline:
                    mode = "read-only" if self.read_only else "read-write"
                    raise RuntimeError(
                        f"Could not open market-data store in {mode} mode after "
                        f"{max(0.0, float(lock_timeout_seconds or 0.0)):.0f}s: {e}"
                    ) from e
                time.sleep(1.0)
        raise RuntimeError(f"Could not open market-data store: {last_error}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def checkpoint(self) -> None:
        """Fold any write-ahead log back into the database file.

        Called after a refresh has been killed mid-write. The WAL it leaves is
        replayed by the next writer anyway, but until then every reader has to
        replay it too, and a reader that arrives while the dead process still
        holds the file is what turned a timeout into a lost night on
        2026-07-30. Doing it once, deliberately, on the way out of a kill means
        the store is quiet again before `verify` looks at it.
        """
        if self.read_only:
            raise RuntimeError("checkpoint needs a read-write store")
        with self._lock:
            self._conn.execute("CHECKPOINT")

    def init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS securities (
                ticker TEXT NOT NULL,
                name TEXT,
                exchange TEXT,
                etf BOOLEAN,
                test_issue BOOLEAN,
                is_common_stock BOOLEAN,
                include_in_scan BOOLEAN,
                exclude_reason TEXT,
                raw_source TEXT,
                updated_at TIMESTAMP
            )
            """
            )
            self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ohlcv_daily (
                ticker TEXT NOT NULL,
                date DATE NOT NULL,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                adj_close DOUBLE,
                adjusted BOOLEAN,
                provider TEXT,
                fetched_at TIMESTAMP,
                -- 'split_only' (Stooq basis, and yfinance auto_adjust=False) or
                -- 'split_div'. Mixing the two within one ticker fabricates a
                -- 10-36% gap for dividend payers at the splice point, which
                -- reads as a breakout; basis_conflicts() exists to catch that.
                basis TEXT
            )
            """
            )
            # Older stores predate the column; add it rather than forcing a rebuild.
            existing_cols = {
                row[0]
                for row in self._conn.execute(
                    "SELECT column_name FROM duckdb_columns()"
                    " WHERE table_name = 'ohlcv_daily'"
                ).fetchall()
            }
            if "basis" not in existing_cols:
                self._conn.execute("ALTER TABLE ohlcv_daily ADD COLUMN basis TEXT")
            self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stooq_archive_state (
                archive_dir TEXT NOT NULL,
                imported_at TIMESTAMP,
                archive_max_date DATE,
                files INTEGER,
                rows BIGINT
            )
            """
            )
            # One row per daily bundle consumed. Size and mtime rather than the
            # path alone: Stooq reuses filenames, and a re-downloaded bundle
            # covering a corrected day has to be picked up again.
            self._conn.execute(
                """
            CREATE TABLE IF NOT EXISTS stooq_bundle_state (
                path TEXT NOT NULL,
                size BIGINT,
                mtime DOUBLE,
                imported_at TIMESTAMP,
                rows BIGINT,
                max_date DATE
            )
            """
            )
            self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ipo_dates (
                ticker TEXT NOT NULL,
                ipo_date DATE,
                source TEXT,
                resolved_at TIMESTAMP
            )
            """
            )
            self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS liquidity_snapshots (
                ticker TEXT NOT NULL,
                as_of DATE NOT NULL,
                last_close DOUBLE,
                avg_volume_50d DOUBLE,
                avg_dollar_volume_50d DOUBLE,
                bars_available INTEGER,
                eligible BOOLEAN,
                reason TEXT,
                evaluated_at TIMESTAMP
            )
            """
            )
            self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_sync_state (
                provider TEXT NOT NULL,
                ticker TEXT NOT NULL,
                frequency TEXT NOT NULL,
                start_date DATE,
                end_date DATE,
                status TEXT,
                last_success TIMESTAMP,
                last_error TEXT,
                updated_at TIMESTAMP
            )
            """
            )
            self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feature_daily (
                ticker TEXT NOT NULL,
                date DATE NOT NULL,
                features_json TEXT,
                factor_breakdown_json TEXT,
                computed_at TIMESTAMP
            )
            """
            )
            # DuckDB can fail fatally while deleting through a secondary index
            # after an interrupted write. Keep the write-heavy tables unindexed:
            # scan reliability matters more than the small lookup speedup, and
            # feature_daily is rewritten once per ticker on every scan.
            for name in (
                "provider_sync_idx",
                "ohlcv_daily_ticker_date_idx",
                "feature_daily_ticker_date_idx",
            ):
                try:
                    self._conn.execute(f"DROP INDEX IF EXISTS {name}")
                except Exception as e:  # noqa: BLE001
                    log.debug("market_store.index_drop_skipped", index=name, error=repr(e))
            for name, table, cols in (
                ("securities_ticker_idx", "securities", "ticker"),
                ("ipo_dates_ticker_idx", "ipo_dates", "ticker"),
                ("liquidity_ticker_asof_idx", "liquidity_snapshots", "ticker, as_of"),
            ):
                try:
                    self._conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})")
                except Exception as e:  # noqa: BLE001 - indexes are performance-only
                    log.debug("market_store.index_skipped", index=name, error=repr(e))

    # ---- securities ---------------------------------------------------------

    def upsert_security_registry(self, rows: Iterable[Mapping[str, Any]]) -> int:
        data: list[dict[str, Any]] = []
        now = _utc_now()
        for row in rows:
            ticker = _ticker(str(row.get("ticker") or ""))
            if not ticker:
                continue
            data.append(
                {
                    "ticker": ticker,
                    "name": row.get("name"),
                    "exchange": row.get("exchange"),
                    "etf": bool(row.get("etf")) if row.get("etf") is not None else None,
                    "test_issue": bool(row.get("test_issue")) if row.get("test_issue") is not None else None,
                    "is_common_stock": bool(row.get("is_common_stock")),
                    "include_in_scan": bool(row.get("include_in_scan")),
                    "exclude_reason": row.get("exclude_reason") or "",
                    "raw_source": row.get("raw_source") or "nasdaq_trader",
                    "updated_at": now,
                }
            )
        if not data:
            return 0
        df = pd.DataFrame(data)
        with self._lock:
            self._conn.register("_security_rows", df)
            try:
                # DuckDB can occasionally fail a large indexed DELETE after an
                # interrupted registry refresh. Rebuild this small index around
                # the replace so the OHLCV tables stay untouched.
                self._conn.execute("DROP INDEX IF EXISTS securities_ticker_idx")
                self._conn.execute("DELETE FROM securities USING _security_rows WHERE securities.ticker = _security_rows.ticker")
                self._conn.execute(
                    """
                    INSERT INTO securities
                    SELECT ticker, name, exchange, etf, test_issue, is_common_stock,
                           include_in_scan, exclude_reason, raw_source, updated_at
                    FROM _security_rows
                    """
                )
                self._conn.execute("CREATE INDEX IF NOT EXISTS securities_ticker_idx ON securities (ticker)")
            finally:
                self._conn.unregister("_security_rows")
        return len(data)

    def scan_universe_tickers(self, eligible_only: bool = False) -> list[str]:
        with self._lock:
            if not eligible_only:
                rows = self._conn.execute(
                    "SELECT ticker FROM securities WHERE include_in_scan = true ORDER BY ticker"
                ).fetchall()
                return [str(r[0]) for r in rows]
            rows = self._conn.execute(
                """
                WITH latest AS (SELECT max(as_of) AS as_of FROM liquidity_snapshots)
                SELECT s.ticker
                FROM securities s
                JOIN liquidity_snapshots l ON l.ticker = s.ticker
                JOIN latest ON latest.as_of = l.as_of
                WHERE s.include_in_scan = true AND l.eligible = true
                ORDER BY s.ticker
                """
            ).fetchall()
            return [str(r[0]) for r in rows]

    # ---- OHLCV --------------------------------------------------------------

    def upsert_ohlcv(
        self,
        ticker: str,
        df: pd.DataFrame,
        provider: str,
        *,
        adjusted: bool = True,
    ) -> int:
        ticker_up = _ticker(ticker)
        if not ticker_up or df is None or df.empty:
            return 0
        try:
            norm = normalize_ohlcv(df)
        except DataIntegrityError as e:
            self.mark_sync_state(provider, ticker_up, "daily", status="failed", error=str(e))
            return 0
        if norm.empty:
            return 0
        rows = norm.reset_index().rename(columns={norm.index.name or "index": "date"})
        if "date" not in rows.columns:
            rows = rows.rename(columns={rows.columns[0]: "date"})
        rows["date"] = pd.to_datetime(rows["date"]).dt.date
        rows["ticker"] = ticker_up
        rows["provider"] = str(provider or "").lower()
        rows["fetched_at"] = _utc_now()
        rows["adjusted"] = bool(adjusted)
        rows["adj_close"] = rows["close"] if adjusted else pd.NA
        # adjusted=True means dividends are baked in; False is the split-only
        # basis shared by Stooq and yfinance auto_adjust=False.
        rows["basis"] = "split_div" if adjusted else "split_only"
        rows = rows[
            [
                "ticker",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "adj_close",
                "adjusted",
                "provider",
                "fetched_at",
                "basis",
            ]
        ].copy()
        rows = rows.sort_values("date").drop_duplicates(["ticker", "date"], keep="last")
        rows["volume"] = pd.to_numeric(rows["volume"], errors="coerce").astype("Int64")
        with self._lock:
            self._conn.register("_ohlcv_rows", rows)
            try:
                self._conn.execute("BEGIN TRANSACTION")
                self._conn.execute(
                    """
                    DELETE FROM ohlcv_daily
                    USING _ohlcv_rows
                    WHERE ohlcv_daily.ticker = _ohlcv_rows.ticker
                      AND ohlcv_daily.date = _ohlcv_rows.date
                    """
                )
                self._conn.execute(
                    """
                    INSERT INTO ohlcv_daily
                    SELECT ticker, date, open, high, low, close, volume, adj_close,
                           adjusted, provider, fetched_at, basis
                    FROM _ohlcv_rows
                    """
                )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001 - connection may already be invalidated
                    pass
                raise
            finally:
                self._conn.unregister("_ohlcv_rows")
        return int(len(rows))

    def write_etf_kinds(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Record ETF classifications (ticker, kind).

        The rebuild populates this table wholesale; this is the supported way
        for anything else to, now that a write path outside the rebuild
        (the daily-bundle import) depends on reading it.
        """
        records = [
            (str(r.get("ticker", "")).strip().upper(), str(r.get("kind", "")).strip().lower())
            for r in rows
        ]
        records = [(t, k) for t, k in records if t and k]
        if not records:
            return 0
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS etf_kind (ticker TEXT NOT NULL, kind TEXT NOT NULL)"
            )
            self._conn.executemany(
                "DELETE FROM etf_kind WHERE ticker = ?", [(t,) for t, _ in records]
            )
            self._conn.executemany("INSERT INTO etf_kind VALUES (?, ?)", records)
        return len(records)

    def excluded_etf_tickers(self) -> set[str]:
        """Tickers the full archive import refuses to carry.

        The user does not trade leveraged or inverse ETFs, and 26M rows of
        something never displayed is pure cost. Any write path that bypasses
        the archive importer has to consult this or it reintroduces them —
        which a daily-bundle import did, 697 of them, caught by `verify`.
        """
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT ticker FROM etf_kind WHERE kind IN ('leveraged', 'inverse')"
                ).fetchall()
            except Exception:  # noqa: BLE001 - stores predating the table
                return set()
        return {str(r[0]).strip().upper() for r in rows if r and r[0]}

    def upsert_ohlcv_many(
        self,
        rows: pd.DataFrame,
        provider: str,
        *,
        adjusted: bool = True,
    ) -> int:
        """Upsert a long-format frame covering many tickers in one transaction.

        `upsert_ohlcv` is per-ticker, which is right for a provider fetch but
        wrong for a Stooq daily bundle: 11,729 symbols would mean 11,729
        transactions against a 27M-row table. Expects columns ticker, date,
        open, high, low, close, volume — already cleaned, since there is no
        per-ticker frame to hand to normalize_ohlcv.
        """
        if rows is None or rows.empty:
            return 0
        out = rows.copy()
        out["ticker"] = out["ticker"].astype(str).str.strip().str.upper()
        out["date"] = pd.to_datetime(out["date"]).dt.date
        out["provider"] = str(provider or "").lower()
        out["fetched_at"] = _utc_now()
        out["adjusted"] = bool(adjusted)
        out["adj_close"] = out["close"] if adjusted else pd.NA
        out["basis"] = "split_div" if adjusted else "split_only"
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce").astype("Int64")
        out = out[
            [
                "ticker", "date", "open", "high", "low", "close", "volume",
                "adj_close", "adjusted", "provider", "fetched_at", "basis",
            ]
        ]
        out = out[out["ticker"].astype(bool) & out["close"].notna()]
        out = out.drop_duplicates(["ticker", "date"], keep="last")
        if out.empty:
            return 0
        with self._lock:
            self._conn.register("_ohlcv_bulk_rows", out)
            try:
                self._conn.execute("BEGIN TRANSACTION")
                self._conn.execute(
                    """
                    DELETE FROM ohlcv_daily
                    USING _ohlcv_bulk_rows b
                    WHERE ohlcv_daily.ticker = b.ticker AND ohlcv_daily.date = b.date
                    """
                )
                self._conn.execute(
                    """
                    INSERT INTO ohlcv_daily
                    SELECT ticker, date, open, high, low, close, volume, adj_close,
                           adjusted, provider, fetched_at, basis
                    FROM _ohlcv_bulk_rows
                    """
                )
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001 - connection may already be invalidated
                    pass
                raise
            finally:
                self._conn.unregister("_ohlcv_bulk_rows")
        return int(len(out))

    def security_name(self, ticker: str) -> str | None:
        """Registered company name for `ticker`, or None if it is not on file.

        Used to tell whether a headline is about this company or merely sitting
        in its feed. A symbol alone is not enough for that: matching the bare
        word "TWO" against prose hits every other sentence, while "Two Harbors"
        does not.
        """
        ticker_up = _ticker(ticker)
        if not ticker_up:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT name FROM securities WHERE ticker = ?", [ticker_up]
            ).fetchone()
        if not row or row[0] is None:
            return None
        name = str(row[0]).strip()
        return name or None

    def read_ohlcv(
        self,
        ticker: str,
        start: date | None = None,
        end: date | None = None,
        *,
        limit: int | None = None,
    ) -> pd.DataFrame:
        ticker_up = _ticker(ticker)
        if not ticker_up:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        clauses = ["ticker = ?"]
        params: list[Any] = [ticker_up]
        if start is not None:
            clauses.append("date >= ?")
            params.append(start)
        if end is not None:
            clauses.append("date <= ?")
            params.append(end)
        order = "ASC"
        limit_sql = ""
        if limit is not None and limit > 0:
            order = "DESC"
            limit_sql = " LIMIT ?"
            params.append(int(limit))
        sql = (
            "SELECT date, open, high, low, close, volume FROM ohlcv_daily "
            f"WHERE {' AND '.join(clauses)} ORDER BY date {order}{limit_sql}"
        )
        with self._lock:
            df = self._conn.execute(sql, params).fetchdf()
        if df.empty:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        df.index.name = None
        return df[OHLCV_COLUMNS]

    def read_ohlcv_resampled(
        self,
        ticker: str,
        interval: str = "d",
        *,
        start: date | None = None,
        end: date | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Read bars aggregated to daily, weekly or monthly.

        Aggregating in DuckDB rather than shipping 20 years of daily bars to the
        chart keeps the payload sane now that tickers carry 5,000-16,000 rows.
        Each bucket is stamped with its LAST trading day, matching the weekly
        candle convention the client already uses (and unlike date_trunc, which
        would label a week by its Monday).
        """
        key = str(interval or "d").lower()
        if key in ("d", "day", "daily"):
            return self.read_ohlcv(ticker, start, end, limit=limit)
        unit = {"w": "week", "week": "week", "weekly": "week",
                "m": "month", "month": "month", "monthly": "month"}.get(key)
        if unit is None:
            raise ValueError(f"Unsupported interval: {interval!r}")

        ticker_up = _ticker(ticker)
        if not ticker_up:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        clauses = ["ticker = ?"]
        params: list[Any] = [ticker_up]
        if start is not None:
            clauses.append("date >= ?")
            params.append(start)
        if end is not None:
            clauses.append("date <= ?")
            params.append(end)
        # The limit applies to aggregated bars, not the raw daily rows feeding
        # them, so it is applied after grouping.
        limit_sql = ""
        if limit is not None and limit > 0:
            limit_sql = " LIMIT ?"
        sql = f"""
            SELECT bucket_end AS date, open, high, low, close, volume FROM (
                SELECT max(date) AS bucket_end,
                       arg_min(open, date) AS open,
                       max(high) AS high,
                       min(low) AS low,
                       arg_max(close, date) AS close,
                       sum(volume) AS volume
                FROM ohlcv_daily
                WHERE {' AND '.join(clauses)}
                GROUP BY date_trunc('{unit}', date)
                ORDER BY bucket_end DESC{limit_sql}
            ) ORDER BY date
        """
        if limit_sql:
            params.append(int(limit))
        with self._lock:
            df = self._conn.execute(sql, params).fetchdf()
        if df.empty:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        df.index.name = None
        return df[OHLCV_COLUMNS]

    def read_reference(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Read a non-equity series (index, FX, bond yield, crypto, macro).

        These live apart from ohlcv_daily so they can never reach the scan
        universe or the cross-sectional RS distribution, and so they can carry
        their own date floors — the 10-year Treasury yield starts in 1871.
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        clauses = ["symbol = ?"]
        params: list[Any] = [sym]
        if start is not None:
            clauses.append("date >= ?")
            params.append(start)
        if end is not None:
            clauses.append("date <= ?")
            params.append(end)
        order = "ASC"
        limit_sql = ""
        if limit is not None and limit > 0:
            order = "DESC"
            limit_sql = " LIMIT ?"
            params.append(int(limit))
        sql = (
            "SELECT date, open, high, low, close, volume FROM ohlcv_reference "
            f"WHERE {' AND '.join(clauses)} ORDER BY date {order}{limit_sql}"
        )
        with self._lock:
            try:
                df = self._conn.execute(sql, params).fetchdf()
            except Exception:  # noqa: BLE001 - stores predating the table
                return pd.DataFrame(columns=OHLCV_COLUMNS)
        if df.empty:
            return pd.DataFrame(columns=OHLCV_COLUMNS)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        df.index.name = None
        return df[OHLCV_COLUMNS]

    def reference_symbols(self, domain: str | None = None) -> list[dict[str, Any]]:
        """Available reference series, with their coverage."""
        clause = " WHERE domain = ?" if domain else ""
        params = [str(domain)] if domain else []
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT symbol, domain, frequency, count(*), min(date), max(date)"
                    f" FROM ohlcv_reference{clause} GROUP BY symbol, domain, frequency"
                    " ORDER BY symbol",
                    params,
                ).fetchall()
            except Exception:  # noqa: BLE001 - stores predating the table
                return []
        return [
            {
                "symbol": str(r[0]), "domain": str(r[1]), "frequency": str(r[2]),
                "bars": int(r[3]), "first": r[4].isoformat() if r[4] else None,
                "last": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]

    def ticker_coverage(self, ticker: str) -> tuple[date | None, date | None, int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT min(date), max(date), count(*) FROM ohlcv_daily WHERE ticker = ?",
                [_ticker(ticker)],
            ).fetchone()
        if row is None or not row[2]:
            return None, None, 0
        return row[0], row[1], int(row[2])

    def latest_bar_date(self, ticker: str) -> date | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT max(date) FROM ohlcv_daily WHERE ticker = ?",
                [_ticker(ticker)],
            ).fetchone()
        return row[0] if row and row[0] is not None else None

    def latest_bar_date_overall(self) -> date | None:
        """Newest bar in the store, regardless of ticker."""
        with self._lock:
            row = self._conn.execute("SELECT max(date) FROM ohlcv_daily").fetchone()
        return row[0] if row and row[0] is not None else None

    def latest_bar_dates(self, tickers: Iterable[str] | None = None) -> dict[str, date]:
        """Latest bar date for many tickers in one pass.

        ohlcv_daily is deliberately unindexed (see init_schema), so a per-ticker
        `max(date)` costs a scan each — ~0.09s against a multi-GB store, which
        turned the universe-wide loops in the scan prewarm and the nightly
        update into tens of minutes. One GROUP BY answers all of them in well
        under a second. Tickers with no bars are absent from the result, the
        same signal `latest_bar_date` gives by returning None.
        """
        wanted: list[str] | None = None
        if tickers is not None:
            wanted = sorted({t for t in (_ticker(x) for x in tickers) if t})
            if not wanted:
                return {}
        sql = "SELECT ticker, max(date) FROM ohlcv_daily"
        params: list[Any] = []
        if wanted is not None:
            placeholders = ", ".join("?" for _ in wanted)
            sql += f" WHERE ticker IN ({placeholders})"
            params.extend(wanted)
        sql += " GROUP BY ticker"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return {str(r[0]): r[1] for r in rows if r[1] is not None}

    def read_ohlcv_bulk(
        self,
        tickers: Iterable[str],
        start: date | None = None,
        end: date | None = None,
        *,
        chunk_size: int = 500,
    ) -> dict[str, pd.DataFrame]:
        """Read many tickers' bars in chunked scans instead of one query each.

        The per-ticker `read_ohlcv` pattern gets steadily worse as history
        deepens; this one barely notices. Chunked so a full-universe read of
        20+ years does not materialise the whole table at once. Tickers with no
        rows are omitted — callers should treat a missing key as "no data",
        matching the empty frame `read_ohlcv` returns.
        """
        wanted = sorted({t for t in (_ticker(x) for x in tickers) if t})
        if not wanted:
            return {}
        date_clauses = ""
        date_params: list[Any] = []
        if start is not None:
            date_clauses += " AND date >= ?"
            date_params.append(start)
        if end is not None:
            date_clauses += " AND date <= ?"
            date_params.append(end)

        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(wanted), max(1, int(chunk_size))):
            chunk = wanted[i : i + max(1, int(chunk_size))]
            placeholders = ", ".join("?" for _ in chunk)
            sql = (
                "SELECT ticker, date, open, high, low, close, volume FROM ohlcv_daily "
                f"WHERE ticker IN ({placeholders}){date_clauses} ORDER BY ticker, date"
            )
            with self._lock:
                frame = self._conn.execute(sql, [*chunk, *date_params]).fetchdf()
            if frame.empty:
                continue
            frame["date"] = pd.to_datetime(frame["date"])
            for ticker_value, group in frame.groupby("ticker", sort=False):
                part = group.drop(columns=["ticker"]).set_index("date")
                part.index.name = None
                out[str(ticker_value)] = part[OHLCV_COLUMNS]
        return out

    def record_archive_import(
        self,
        archive_dir: str,
        *,
        archive_max_date: date | None,
        files: int,
        rows: int,
    ) -> None:
        """Remember what the last archive import saw, so freshness checks are a
        one-row query rather than a filesystem walk."""
        with self._lock:
            self._conn.execute("DELETE FROM stooq_archive_state")
            self._conn.execute(
                "INSERT INTO stooq_archive_state VALUES (?, ?, ?, ?, ?)",
                [str(archive_dir), _utc_now(), archive_max_date, int(files), int(rows)],
            )

    def bundle_import_fingerprints(self) -> set[tuple[str, int, int]]:
        """(path, size, mtime-as-int) for every bundle already consumed."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT path, size, mtime FROM stooq_bundle_state"
                ).fetchall()
            except Exception:  # noqa: BLE001 - stores predating the table
                return set()
        return {(str(r[0]).lower(), int(r[1] or 0), int(r[2] or 0)) for r in rows}

    def record_bundle_import(
        self,
        path: str,
        *,
        size: int,
        mtime: float,
        rows: int,
        max_date: date | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM stooq_bundle_state WHERE lower(path) = ?", [str(path).lower()]
            )
            self._conn.execute(
                "INSERT INTO stooq_bundle_state VALUES (?, ?, ?, ?, ?, ?)",
                [str(path), int(size), float(mtime), _utc_now(), int(rows), max_date],
            )

    def archive_import_state(self) -> dict[str, Any] | None:
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT archive_dir, imported_at, archive_max_date, files, rows"
                    " FROM stooq_archive_state LIMIT 1"
                ).fetchone()
            except Exception:  # noqa: BLE001 - stores predating the table
                return None
        if not row:
            return None
        return {
            "archive_dir": row[0],
            "imported_at": row[1].isoformat() if row[1] else None,
            "archive_max_date": row[2].isoformat() if row[2] else None,
            "files": int(row[3] or 0),
            "rows": int(row[4] or 0),
        }

    def _has_basis_column(self) -> bool:
        """A store opened read-only cannot be migrated, and old backups predate
        the column entirely — both must degrade rather than raise."""
        with self._lock:
            row = self._conn.execute(
                "SELECT count(*) FROM duckdb_columns()"
                " WHERE table_name = 'ohlcv_daily' AND column_name = 'basis'"
            ).fetchone()
        return bool(row and row[0])

    def basis_conflicts(self, limit: int = 25) -> list[tuple[str, int]]:
        """Tickers whose bars mix price bases — the fake-breakout failure mode.

        Stooq is split-adjusted only; yfinance with auto_adjust=True is also
        dividend-adjusted. Splicing them inside one ticker fabricates a jump of
        10-36% for dividend payers (T ~1.36x, PFE ~1.23x, XOM ~1.12x) exactly at
        the seam. Any hit here means the delta path regressed and is writing the
        wrong basis, so this is wired into readiness as a hard failure.
        """
        if not self._has_basis_column():
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT ticker, count(DISTINCT basis) AS bases FROM ohlcv_daily "
                "WHERE basis IS NOT NULL GROUP BY ticker HAVING count(DISTINCT basis) > 1 "
                "ORDER BY ticker LIMIT ?",
                [int(limit)],
            ).fetchall()
        return [(str(r[0]), int(r[1])) for r in rows]

    def basis_summary(self) -> dict[str, int]:
        """Row counts per price basis, including untagged legacy rows."""
        if not self._has_basis_column():
            with self._lock:
                total = self._conn.execute("SELECT count(*) FROM ohlcv_daily").fetchone()
            return {"untagged": int((total or [0])[0] or 0)}
        with self._lock:
            rows = self._conn.execute(
                "SELECT COALESCE(basis, 'untagged'), count(*) FROM ohlcv_daily GROUP BY 1"
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def liquidity_metrics(
        self,
        tickers: Iterable[str] | None = None,
        *,
        lookback_bars: int,
        average_bars: int = 50,
    ) -> dict[str, dict[str, float | int]]:
        """Last close, average volume/dollar-volume and bar count, for everyone at once.

        Replaces a per-ticker `read_ohlcv` loop that cost minutes across the
        universe. `bars_available` stays capped at `lookback_bars`, matching the
        previous behaviour of counting rows in a limited frame.
        """
        limit = max(1, int(lookback_bars))
        window = max(1, int(average_bars))
        wanted: list[str] | None = None
        if tickers is not None:
            wanted = sorted({t for t in (_ticker(x) for x in tickers) if t})
            if not wanted:
                return {}
        where = ""
        params: list[Any] = []
        if wanted is not None:
            placeholders = ", ".join("?" for _ in wanted)
            where = f"WHERE ticker IN ({placeholders})"
            params.extend(wanted)
        sql = f"""
            WITH ranked AS (
                SELECT ticker, close, volume,
                       row_number() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                FROM ohlcv_daily
                {where}
            )
            SELECT ticker,
                   count(*) AS bars_available,
                   max(close) FILTER (WHERE rn = 1) AS last_close,
                   avg(volume) FILTER (WHERE rn <= {window}) AS avg_volume,
                   avg(close * volume) FILTER (WHERE rn <= {window}) AS avg_dollar_volume
            FROM ranked
            WHERE rn <= {limit}
            GROUP BY ticker
        """
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out: dict[str, dict[str, float | int]] = {}
        for ticker, bars, last_close, avg_volume, avg_dollar_volume in rows:
            out[str(ticker)] = {
                "bars_available": int(bars or 0),
                "last_close": float(last_close or 0.0),
                "avg_volume_50d": float(avg_volume or 0.0),
                "avg_dollar_volume_50d": float(avg_dollar_volume or 0.0),
            }
        return out

    def missing_ranges(self, ticker: str, start: date, end: date) -> list[MissingRange]:
        ticker_up = _ticker(ticker)
        ipo = self.ipo_date(ticker_up)
        effective_start = max(start, ipo) if ipo else start
        expected = _trading_dates(effective_start, end)
        if not expected:
            return []
        with self._lock:
            present_rows = self._conn.execute(
                "SELECT date FROM ohlcv_daily WHERE ticker = ? AND date BETWEEN ? AND ?",
                [ticker_up, effective_start, end],
            ).fetchall()
        present = {r[0] for r in present_rows}
        missing = [d for d in expected if d not in present]
        if not missing:
            return []
        expected_pos = {d: i for i, d in enumerate(expected)}
        ranges: list[MissingRange] = []
        start_d = prev = missing[0]
        prev_pos = expected_pos[prev]
        for d in missing[1:]:
            pos = expected_pos[d]
            if pos == prev_pos + 1:
                prev = d
                prev_pos = pos
                continue
            ranges.append(MissingRange(start_d, prev))
            start_d = prev = d
            prev_pos = pos
        ranges.append(MissingRange(start_d, prev))
        return ranges

    # ---- IPO / liquidity / sync --------------------------------------------

    def upsert_ipo_dates(self, rows: Iterable[Mapping[str, Any]]) -> int:
        out: list[dict[str, Any]] = []
        for row in rows:
            ticker = _ticker(str(row.get("ticker") or ""))
            if not ticker:
                continue
            ipo_value = row.get("ipo_date")
            ipo_date = None
            if ipo_value:
                try:
                    ipo_date = pd.Timestamp(ipo_value).date()
                except Exception:  # noqa: BLE001
                    ipo_date = None
            resolved = row.get("resolved_at") or _utc_now()
            out.append(
                {
                    "ticker": ticker,
                    "ipo_date": ipo_date,
                    "source": row.get("source") or "unknown",
                    "resolved_at": pd.Timestamp(resolved).to_pydatetime().replace(tzinfo=None),
                }
            )
        if not out:
            return 0
        df = pd.DataFrame(out)
        with self._lock:
            self._conn.register("_ipo_rows", df)
            try:
                self._conn.execute("DELETE FROM ipo_dates USING _ipo_rows WHERE ipo_dates.ticker = _ipo_rows.ticker")
                self._conn.execute("INSERT INTO ipo_dates SELECT ticker, ipo_date, source, resolved_at FROM _ipo_rows")
            finally:
                self._conn.unregister("_ipo_rows")
        return len(out)

    def ipo_date(self, ticker: str) -> date | None:
        with self._lock:
            row = self._conn.execute("SELECT ipo_date FROM ipo_dates WHERE ticker = ?", [_ticker(ticker)]).fetchone()
        return row[0] if row and row[0] is not None else None

    def write_liquidity_snapshot(self, rows: Iterable[Mapping[str, Any]], as_of: date) -> int:
        data: list[dict[str, Any]] = []
        now = _utc_now()
        for row in rows:
            ticker = _ticker(str(row.get("ticker") or ""))
            if not ticker:
                continue
            data.append(
                {
                    "ticker": ticker,
                    "as_of": as_of,
                    "last_close": row.get("last_close"),
                    "avg_volume_50d": row.get("avg_volume_50d"),
                    "avg_dollar_volume_50d": row.get("avg_dollar_volume_50d"),
                    "bars_available": int(row.get("bars_available") or 0),
                    "eligible": bool(row.get("eligible")),
                    "reason": row.get("reason") or "",
                    "evaluated_at": now,
                }
            )
        if not data:
            return 0
        df = pd.DataFrame(data)
        with self._lock:
            self._conn.register("_liquidity_rows", df)
            try:
                self._conn.execute("DELETE FROM liquidity_snapshots WHERE as_of = ?", [as_of])
                self._conn.execute(
                    """
                    INSERT INTO liquidity_snapshots
                    SELECT ticker, as_of, last_close, avg_volume_50d,
                           avg_dollar_volume_50d, bars_available, eligible, reason, evaluated_at
                    FROM _liquidity_rows
                    """
                )
            finally:
                self._conn.unregister("_liquidity_rows")
        return len(data)

    def latest_liquidity_snapshot(self, *, eligible: bool | None = None) -> pd.DataFrame:
        clauses = [
            "as_of = (SELECT max(as_of) FROM liquidity_snapshots)",
        ]
        params: list[Any] = []
        if eligible is not None:
            clauses.append("eligible = ?")
            params.append(bool(eligible))
        with self._lock:
            return self._conn.execute(
                f"SELECT * FROM liquidity_snapshots WHERE {' AND '.join(clauses)} ORDER BY ticker",
                params,
            ).fetchdf()

    def mark_sync_state(
        self,
        provider: str,
        ticker: str,
        frequency: str,
        *,
        status: str,
        start: date | None = None,
        end: date | None = None,
        error: str | None = None,
    ) -> None:
        provider_key = str(provider or "").lower()
        ticker_key = _ticker(ticker)
        now = _utc_now()
        last_success = now if status == "ok" else None
        last_error = (error or "")[:1000]
        with self._lock:
            self._conn.execute(
                """
                DELETE FROM provider_sync_state
                WHERE provider = ? AND ticker = ? AND frequency = ?
                """,
                [provider_key, ticker_key, frequency],
            )
            self._conn.execute(
                """
                INSERT INTO provider_sync_state
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [provider_key, ticker_key, frequency, start, end, status, last_success, last_error, now],
            )

    def upsert_feature_snapshot(
        self,
        ticker: str,
        as_of: date,
        features: Mapping[str, Any],
        factor_breakdown: Mapping[str, Any] | None = None,
    ) -> None:
        row = pd.DataFrame(
            [
                {
                    "ticker": _ticker(ticker),
                    "date": as_of,
                    "features_json": json.dumps(dict(features), sort_keys=True, default=str),
                    "factor_breakdown_json": json.dumps(dict(factor_breakdown or {}), sort_keys=True, default=str),
                    "computed_at": _utc_now(),
                }
            ]
        )
        with self._lock:
            self._conn.register("_feature_rows", row)
            try:
                self._conn.execute(
                    """
                    DELETE FROM feature_daily
                    USING _feature_rows
                    WHERE feature_daily.ticker = _feature_rows.ticker
                      AND feature_daily.date = _feature_rows.date
                    """
                )
                self._conn.execute(
                    "INSERT INTO feature_daily SELECT ticker, date, features_json, factor_breakdown_json, computed_at FROM _feature_rows"
                )
            finally:
                self._conn.unregister("_feature_rows")

    # ---- maintenance / reporting -------------------------------------------

    def import_legacy_parquet_cache(
        self,
        cache_dir: str | Path,
        *,
        provider: str | None = None,
        limit: int | None = None,
    ) -> dict[str, int]:
        base = Path(cache_dir)
        pattern = f"{provider}/daily/*.parquet" if provider else "*/daily/*.parquet"
        files = sorted(base.glob(pattern))
        if limit and limit > 0:
            files = files[:limit]
        imported = failed = bars = 0
        for fp in files:
            provider_name = fp.parts[-3]
            ticker = fp.stem
            try:
                df = pd.read_parquet(fp)
                n = self.upsert_ohlcv(ticker, df, provider_name, adjusted=True)
                if n:
                    imported += 1
                    bars += n
                    _, end, _ = self.ticker_coverage(ticker)
                    self.mark_sync_state(provider_name, ticker, "daily", status="ok", end=end)
            except Exception as e:  # noqa: BLE001
                failed += 1
                self.mark_sync_state(provider_name, ticker, "daily", status="failed", error=repr(e))
        return {"files": len(files), "imported": imported, "failed": failed, "bars": bars}

    def _coverage_start(self, *, coverage_years: int, latest_expected: date) -> date:
        return latest_expected - timedelta(days=int(coverage_years * 365.25))

    def _scoped_tickers(self, scope: str) -> list[str]:
        if scope == "scan":
            return self.scan_universe_tickers(eligible_only=False)
        if scope == "eligible":
            return self.scan_universe_tickers(eligible_only=True)
        raise ValueError("--scope must be scan or eligible")

    def _repair_start_for(self, ticker: str, base_start: date) -> date:
        ipo = self.ipo_date(ticker)
        return max(base_start, ipo) if ipo else base_start

    def repair_targets(
        self,
        *,
        mode: str = "all",
        scope: str = "scan",
        coverage_years: int = 10,
        latest_expected: date | None = None,
        limit: int = 0,
    ) -> list[RepairTarget]:
        """Return deterministic repair work without fetching provider data."""
        latest_expected = latest_expected or date.today()
        valid_modes = {"all", "no-ohlcv", "missing-latest", "eligible-gaps"}
        if mode not in valid_modes:
            raise ValueError("--mode must be all, no-ohlcv, missing-latest, or eligible-gaps")
        base_start = self._coverage_start(coverage_years=coverage_years, latest_expected=latest_expected)
        tickers = self._scoped_tickers(scope)
        with self._lock:
            latest_by_ticker = {
                str(row[0]): row[1]
                for row in self._conn.execute(
                    "SELECT ticker, max(date) AS latest_date FROM ohlcv_daily GROUP BY ticker"
                ).fetchall()
            }
        out: list[RepairTarget] = []
        seen: set[str] = set()

        def append(target: RepairTarget) -> None:
            if limit > 0 and len(out) >= limit:
                return
            key = target.ticker if mode == "all" else f"{target.ticker}:{target.reason}"
            if key in seen:
                return
            seen.add(key)
            out.append(target)

        if mode in {"all", "no-ohlcv"}:
            for ticker in tickers:
                if limit > 0 and len(out) >= limit:
                    break
                if latest_by_ticker.get(ticker) is not None:
                    continue
                start = self._repair_start_for(ticker, base_start)
                if start <= latest_expected:
                    append(RepairTarget(ticker=ticker, start=start, end=latest_expected, reason="no_ohlcv"))

        if mode in {"all", "eligible-gaps"}:
            start_rows = [
                {"ticker": ticker, "expected_start": self._repair_start_for(ticker, base_start)}
                for ticker in tickers
            ]
            expected_dates = _trading_dates(base_start, latest_expected)
            expected_count_by_start = {
                row["expected_start"]: len(expected_dates) - bisect_left(expected_dates, row["expected_start"])
                for row in start_rows
            }
            coverage_rows: list[tuple[str, date, int]] = []
            if start_rows:
                starts_df = pd.DataFrame(start_rows)
                with self._lock:
                    self._conn.register("_repair_starts", starts_df)
                    try:
                        coverage_rows = self._conn.execute(
                            """
                            SELECT
                              _repair_starts.ticker,
                              _repair_starts.expected_start,
                              count(ohlcv_daily.date) AS present_count
                            FROM _repair_starts
                            LEFT JOIN ohlcv_daily
                              ON ohlcv_daily.ticker = _repair_starts.ticker
                             AND ohlcv_daily.date BETWEEN _repair_starts.expected_start AND ?
                            GROUP BY _repair_starts.ticker, _repair_starts.expected_start
                            ORDER BY _repair_starts.ticker
                            """,
                            [latest_expected],
                        ).fetchall()
                    finally:
                        self._conn.unregister("_repair_starts")
            for ticker, start, present_count in coverage_rows:
                if limit > 0 and len(out) >= limit:
                    break
                if int(present_count or 0) >= expected_count_by_start.get(start, 0):
                    continue
                gaps = self.missing_ranges(str(ticker), start, latest_expected)
                if not gaps:
                    continue
                first_gap = gaps[0]
                append(
                    RepairTarget(
                        ticker=str(ticker),
                        start=first_gap.start,
                        end=latest_expected,
                        reason="eligible_gaps",
                    )
                )

        if mode in {"all", "missing-latest"}:
            for ticker in tickers:
                if limit > 0 and len(out) >= limit:
                    break
                latest = latest_by_ticker.get(ticker)
                if latest is None or latest >= latest_expected:
                    continue
                start = latest + timedelta(days=1)
                if start <= latest_expected:
                    append(RepairTarget(ticker=ticker, start=start, end=latest_expected, reason="missing_latest"))
        return out

    def health_summary(
        self,
        *,
        coverage_years: int = 10,
        latest_expected: date | None = None,
        details: bool = False,
        details_limit: int = 50,
    ) -> dict[str, Any]:
        latest_expected = latest_expected or date.today()
        with self._lock:
            latest_bar = self._conn.execute("SELECT max(date) FROM ohlcv_daily").fetchone()[0]
        coverage_anchor = min(latest_expected, latest_bar) if latest_bar else latest_expected
        coverage_start = self._coverage_start(coverage_years=coverage_years, latest_expected=coverage_anchor)
        coverage_start_cutoff = coverage_start + timedelta(days=7)
        with self._lock:
            ohlcv = self._conn.execute("SELECT count(DISTINCT ticker), count(*) FROM ohlcv_daily").fetchone()
            covered = self._conn.execute(
                """
                SELECT count(*)
                FROM (
                    SELECT ticker, min(date) AS start_date, max(date) AS end_date
                    FROM ohlcv_daily
                    GROUP BY ticker
                )
                WHERE start_date <= ? AND end_date >= ?
                """,
                [coverage_start_cutoff, coverage_anchor],
            ).fetchone()[0]
            expected_covered = self._conn.execute(
                """
                WITH coverage AS (
                    SELECT ticker, min(date) AS start_date, max(date) AS end_date
                    FROM ohlcv_daily
                    GROUP BY ticker
                ),
                expected AS (
                    SELECT
                      coverage.ticker,
                      coverage.start_date,
                      coverage.end_date,
                      CASE
                        WHEN ipo_dates.ipo_date IS NOT NULL AND ipo_dates.ipo_date > ?
                          THEN ipo_dates.ipo_date
                        ELSE ?
                      END AS expected_start
                    FROM coverage
                    LEFT JOIN ipo_dates ON ipo_dates.ticker = coverage.ticker
                )
                SELECT count(*)
                FROM expected
                WHERE start_date <= expected_start + INTERVAL 7 DAY
                  AND end_date >= ?
                """,
                [coverage_start_cutoff, coverage_start_cutoff, coverage_anchor],
            ).fetchone()[0]
            missing_latest = self._conn.execute(
                """
                SELECT count(*)
                FROM (
                    SELECT ticker, max(date) AS end_date
                    FROM ohlcv_daily
                    GROUP BY ticker
                )
                WHERE end_date < ?
                """,
                [latest_expected],
            ).fetchone()[0]
            eligible_row = self._conn.execute(
                """
                WITH latest AS (SELECT max(as_of) AS as_of FROM liquidity_snapshots)
                SELECT
                  count(*) FILTER (WHERE eligible = true),
                  count(*)
                FROM liquidity_snapshots, latest
                WHERE liquidity_snapshots.as_of = latest.as_of
                """
            ).fetchone()
            failed_provider_events = self._conn.execute(
                "SELECT count(*) FROM provider_sync_state WHERE status = 'failed'"
            ).fetchone()[0]
            failed_provider_tickers = self._conn.execute(
                "SELECT count(DISTINCT ticker) FROM provider_sync_state WHERE status = 'failed'"
            ).fetchone()[0]
            failed_sync = self._conn.execute(
                """
                WITH coverage AS (
                    SELECT ticker, max(date) AS end_date
                    FROM ohlcv_daily
                    GROUP BY ticker
                )
                SELECT count(DISTINCT provider_sync_state.ticker)
                FROM provider_sync_state
                LEFT JOIN coverage ON coverage.ticker = provider_sync_state.ticker
                WHERE provider_sync_state.status = 'failed'
                  AND (coverage.end_date IS NULL OR coverage.end_date < ?)
                """,
                [coverage_anchor],
            ).fetchone()[0]
            scan_without_ohlcv = self._conn.execute(
                """
                SELECT count(*)
                FROM securities
                LEFT JOIN (SELECT DISTINCT ticker FROM ohlcv_daily) coverage USING (ticker)
                WHERE securities.include_in_scan = true
                  AND coverage.ticker IS NULL
                """
            ).fetchone()[0]
            eligible_without_expected_coverage = self._conn.execute(
                """
                WITH latest_liquidity AS (SELECT max(as_of) AS as_of FROM liquidity_snapshots),
                coverage AS (
                    SELECT ticker, min(date) AS start_date, max(date) AS end_date
                    FROM ohlcv_daily
                    GROUP BY ticker
                ),
                expected AS (
                    SELECT
                      coverage.ticker,
                      coverage.start_date,
                      coverage.end_date,
                      CASE
                        WHEN ipo_dates.ipo_date IS NOT NULL AND ipo_dates.ipo_date > ?
                          THEN ipo_dates.ipo_date
                        ELSE ?
                      END AS expected_start
                    FROM coverage
                    LEFT JOIN ipo_dates ON ipo_dates.ticker = coverage.ticker
                )
                SELECT count(*)
                FROM liquidity_snapshots
                JOIN latest_liquidity ON latest_liquidity.as_of = liquidity_snapshots.as_of
                LEFT JOIN expected ON expected.ticker = liquidity_snapshots.ticker
                WHERE liquidity_snapshots.eligible = true
                  AND (
                    expected.ticker IS NULL
                    OR expected.start_date > expected.expected_start + INTERVAL 7 DAY
                    OR expected.end_date < ?
                  )
                """,
                [coverage_start_cutoff, coverage_start_cutoff, coverage_anchor],
            ).fetchone()[0]
        db_size = self.path.stat().st_size if self.path.exists() else 0
        summary: dict[str, Any] = {
            "path": str(self.path),
            "size_mb": round(db_size / 1_048_576, 2),
            "ohlcv_tickers": int(ohlcv[0] or 0),
            "ohlcv_bars": int(ohlcv[1] or 0),
            "coverage_years": coverage_years,
            "tickers_with_full_coverage": int(covered or 0),
            "tickers_with_expected_coverage": int(expected_covered or 0),
            "latest_expected": latest_expected.isoformat(),
            "latest_bar": latest_bar.isoformat() if latest_bar else None,
            "missing_latest_tickers": int(missing_latest or 0),
            "stale_latest_tickers": int(missing_latest or 0),
            "scan_without_ohlcv_tickers": int(scan_without_ohlcv or 0),
            "eligible_tickers": int((eligible_row or [0, 0])[0] or 0),
            "liquidity_snapshot_tickers": int((eligible_row or [0, 0])[1] or 0),
            "eligible_without_expected_coverage": int(eligible_without_expected_coverage or 0),
            "failed_provider_events": int(failed_provider_events or 0),
            "failed_provider_tickers": int(failed_provider_tickers or 0),
            "failed_sync": int(failed_sync or 0),
        }
        if not details:
            return summary

        limit = max(1, int(details_limit or 50))
        with self._lock:
            scan_without = self._conn.execute(
                """
                SELECT securities.ticker, securities.name, securities.exchange
                FROM securities
                LEFT JOIN (SELECT DISTINCT ticker FROM ohlcv_daily) coverage USING (ticker)
                WHERE securities.include_in_scan = true
                  AND coverage.ticker IS NULL
                ORDER BY securities.ticker
                LIMIT ?
                """,
                [limit],
            ).fetchdf()
            stale_latest = self._conn.execute(
                """
                SELECT ticker, min(date) AS first_date, max(date) AS latest_date, count(*) AS bars
                FROM ohlcv_daily
                GROUP BY ticker
                HAVING max(date) < ?
                ORDER BY latest_date, ticker
                LIMIT ?
                """,
                [latest_expected, limit],
            ).fetchdf()
            failed_no_ohlcv = self._conn.execute(
                """
                SELECT
                  provider_sync_state.ticker,
                  string_agg(DISTINCT provider_sync_state.provider, ',') AS providers,
                  max(provider_sync_state.last_error) AS last_error
                FROM provider_sync_state
                LEFT JOIN (SELECT DISTINCT ticker FROM ohlcv_daily) coverage USING (ticker)
                WHERE provider_sync_state.status = 'failed'
                  AND coverage.ticker IS NULL
                GROUP BY provider_sync_state.ticker
                ORDER BY provider_sync_state.ticker
                LIMIT ?
                """,
                [limit],
            ).fetchdf()
            eligible_gaps = self._conn.execute(
                """
                WITH latest_liquidity AS (SELECT max(as_of) AS as_of FROM liquidity_snapshots),
                coverage AS (
                    SELECT ticker, min(date) AS first_date, max(date) AS latest_date, count(*) AS bars
                    FROM ohlcv_daily
                    GROUP BY ticker
                ),
                expected AS (
                    SELECT
                      coverage.ticker,
                      coverage.first_date,
                      coverage.latest_date,
                      coverage.bars,
                      ipo_dates.ipo_date,
                      CASE
                        WHEN ipo_dates.ipo_date IS NOT NULL AND ipo_dates.ipo_date > ?
                          THEN ipo_dates.ipo_date
                        ELSE ?
                      END AS expected_start
                    FROM coverage
                    LEFT JOIN ipo_dates ON ipo_dates.ticker = coverage.ticker
                )
                SELECT ticker, first_date, latest_date, bars, ipo_date, expected_start
                FROM liquidity_snapshots
                JOIN latest_liquidity ON latest_liquidity.as_of = liquidity_snapshots.as_of
                LEFT JOIN expected USING (ticker)
                WHERE liquidity_snapshots.eligible = true
                  AND (
                    expected.ticker IS NULL
                    OR expected.first_date > expected.expected_start + INTERVAL 7 DAY
                    OR expected.latest_date < ?
                  )
                ORDER BY ticker
                LIMIT ?
                """,
                [coverage_start_cutoff, coverage_start_cutoff, coverage_anchor, limit],
            ).fetchdf()
        summary["details"] = {
            "scan_without_ohlcv": _json_safe_records(scan_without),
            "stale_latest": _json_safe_records(stale_latest),
            "failed_no_ohlcv": _json_safe_records(failed_no_ohlcv),
            "eligible_coverage_gaps": _json_safe_records(eligible_gaps),
        }
        return summary

    def integrity_report(self, *, sample_limit: int = 10) -> dict[str, Any]:
        """Read-only structural checks over the store.

        Meant to run before the nightly readiness gate: these are the invariants
        the archive import and the split-only migration rely on, and each one
        has a failure mode that is invisible in the output rather than loud.
        """
        checks: dict[str, Any] = {}
        with self._lock:
            def scalar(sql: str) -> int:
                try:
                    row = self._conn.execute(sql).fetchone()
                    return int((row or [0])[0] or 0)
                except Exception:  # noqa: BLE001 - older stores lack some tables
                    return 0

            def sample(sql: str) -> list[str]:
                try:
                    return [str(r[0]) for r in self._conn.execute(sql).fetchall()]
                except Exception:  # noqa: BLE001
                    return []

            checks["duplicate_ticker_date"] = scalar(
                "SELECT count(*) FROM (SELECT ticker, date FROM ohlcv_daily"
                " GROUP BY 1, 2 HAVING count(*) > 1)"
            )
            checks["non_positive_prices"] = scalar(
                "SELECT count(*) FROM ohlcv_daily WHERE close <= 0 OR open <= 0"
                " OR high <= 0 OR low <= 0"
            )
            # Separate from the above, because `close <= 0` is false for NULL:
            # a partial bulk download writes bars with a real open/high/low and
            # no close, and that slipped past every check here.
            checks["null_prices"] = scalar(
                "SELECT count(*) FROM ohlcv_daily WHERE close IS NULL OR open IS NULL"
                " OR high IS NULL OR low IS NULL"
            )
            # An impossible bar: the high must enclose the body.
            checks["incoherent_high_low"] = scalar(
                "SELECT count(*) FROM ohlcv_daily WHERE high < greatest(open, close)"
                " OR low > least(open, close)"
            )
            checks["bars_before_1900"] = scalar(
                "SELECT count(*) FROM ohlcv_daily WHERE date < DATE '1900-01-01'"
            )
            checks["bars_on_epoch_date"] = scalar(
                "SELECT count(*) FROM ohlcv_daily WHERE date = DATE '1970-01-01'"
            )
            checks["future_bars"] = scalar(
                "SELECT count(*) FROM ohlcv_daily WHERE date > current_date + 7"
            )
            checks["untagged_basis_rows"] = (
                scalar("SELECT count(*) FROM ohlcv_daily WHERE basis IS NULL")
                if self._has_basis_column()
                else scalar("SELECT count(*) FROM ohlcv_daily")
            )
            # Leveraged/inverse ETFs are dropped at import; any here means a
            # later write let them back in.
            checks["leveraged_etfs_present"] = scalar(
                "SELECT count(DISTINCT o.ticker) FROM ohlcv_daily o JOIN etf_kind k"
                " ON k.ticker = o.ticker WHERE k.kind IN ('leveraged', 'inverse')"
            )
            # Coverage, not integrity: tickers the archive lacks are expected
            # and the network delta fills them, so this is reported but never
            # fails the gate.
            coverage = {
                "scan_tickers_without_bars": scalar(
                    "SELECT count(*) FROM securities s WHERE s.include_in_scan AND NOT EXISTS"
                    " (SELECT 1 FROM ohlcv_daily o WHERE o.ticker = s.ticker)"
                )
            }
            samples = {
                "duplicate_ticker_date": sample(
                    "SELECT ticker FROM ohlcv_daily GROUP BY ticker, date HAVING count(*) > 1"
                    f" LIMIT {int(sample_limit)}"
                ),
                "leveraged_etfs_present": sample(
                    "SELECT DISTINCT o.ticker FROM ohlcv_daily o JOIN etf_kind k"
                    " ON k.ticker = o.ticker WHERE k.kind IN ('leveraged', 'inverse')"
                    f" LIMIT {int(sample_limit)}"
                ),
                # Ticker and date both, because a bad bar is diagnosed by
                # refetching that one day from the provider that wrote it.
                "non_positive_prices": sample(
                    "SELECT ticker || ' ' || date FROM ohlcv_daily"
                    " WHERE close <= 0 OR open <= 0 OR high <= 0 OR low <= 0"
                    f" ORDER BY date DESC LIMIT {int(sample_limit)}"
                ),
                "null_prices": sample(
                    "SELECT ticker || ' ' || date FROM ohlcv_daily"
                    " WHERE close IS NULL OR open IS NULL OR high IS NULL OR low IS NULL"
                    f" ORDER BY date DESC LIMIT {int(sample_limit)}"
                ),
                "incoherent_high_low": sample(
                    "SELECT ticker || ' ' || date FROM ohlcv_daily"
                    " WHERE high < greatest(open, close) OR low > least(open, close)"
                    f" ORDER BY date DESC LIMIT {int(sample_limit)}"
                ),
            }

        conflicts = self.basis_conflicts()
        checks["mixed_price_basis"] = len(conflicts)
        problems = [name for name, count in checks.items() if count]
        return {
            "ok": not problems,
            "problems": problems,
            "checks": checks,
            "coverage": coverage,
            "basis_conflict_sample": [t for t, _ in conflicts[:sample_limit]],
            "samples": {k: v for k, v in samples.items() if v},
            "price_basis": self.basis_summary(),
        }

    def readiness_summary(
        self,
        *,
        latest_expected: date,
        max_lag_days: int,
        min_eligible_tickers: int = 500,
        fresh_warning_pct: float = 80.0,
        benchmark_tickers: Iterable[str] = ("SPY",),
        archive_stale_warning_days: int = 7,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        hard_failures: list[str] = []
        with self._lock:
            latest_bar = self._conn.execute("SELECT max(date) FROM ohlcv_daily").fetchone()[0]
            eligible_row = self._conn.execute(
                """
                WITH latest AS (SELECT max(as_of) AS as_of FROM liquidity_snapshots)
                SELECT
                  count(*) FILTER (WHERE eligible = true),
                  count(*)
                FROM liquidity_snapshots, latest
                WHERE liquidity_snapshots.as_of = latest.as_of
                """
            ).fetchone()
            fresh_row = self._conn.execute(
                """
                WITH latest_liquidity AS (SELECT max(as_of) AS as_of FROM liquidity_snapshots),
                latest_ohlcv AS (
                    SELECT ticker, max(date) AS latest_date
                    FROM ohlcv_daily
                    GROUP BY ticker
                )
                SELECT
                  count(*) FILTER (WHERE liquidity_snapshots.eligible = true AND latest_ohlcv.latest_date >= ?),
                  count(*) FILTER (WHERE liquidity_snapshots.eligible = true AND (latest_ohlcv.latest_date IS NULL OR latest_ohlcv.latest_date < ?))
                FROM liquidity_snapshots
                JOIN latest_liquidity ON latest_liquidity.as_of = liquidity_snapshots.as_of
                LEFT JOIN latest_ohlcv ON latest_ohlcv.ticker = liquidity_snapshots.ticker
                """,
                [latest_expected, latest_expected],
            ).fetchone()
            benchmark_rows = self._conn.execute(
                """
                SELECT ticker, max(date) AS latest_date
                FROM ohlcv_daily
                WHERE ticker IN (
                    SELECT * FROM unnest(?)
                )
                GROUP BY ticker
                """,
                [[_ticker(t) for t in benchmark_tickers if _ticker(t)]],
            ).fetchall()

        eligible_tickers = int((eligible_row or [0, 0])[0] or 0)
        liquidity_snapshot_tickers = int((eligible_row or [0, 0])[1] or 0)
        fresh_eligible = int((fresh_row or [0, 0])[0] or 0)
        stale_eligible = int((fresh_row or [0, 0])[1] or 0)
        fresh_pct = (fresh_eligible / eligible_tickers * 100.0) if eligible_tickers else 0.0
        latest_bar_iso = latest_bar.isoformat() if latest_bar else None

        if latest_bar is None:
            hard_failures.append("no_ohlcv_bars")
        else:
            lag_days = (latest_expected - latest_bar).days
            if lag_days > max(0, int(max_lag_days)):
                hard_failures.append(f"latest_bar_lag_days>{max_lag_days}")
        if liquidity_snapshot_tickers <= 0:
            hard_failures.append("missing_liquidity_snapshot")
        if eligible_tickers < min_eligible_tickers:
            hard_failures.append(f"eligible_tickers<{min_eligible_tickers}")

        benchmark_latest = {str(t): d for t, d in benchmark_rows}
        missing_benchmarks: list[str] = []
        stale_benchmarks: list[str] = []
        for ticker in [_ticker(t) for t in benchmark_tickers if _ticker(t)]:
            bench_latest = benchmark_latest.get(ticker)
            if bench_latest is None:
                missing_benchmarks.append(ticker)
            elif latest_bar is not None and (latest_expected - bench_latest).days > max(0, int(max_lag_days)):
                stale_benchmarks.append(ticker)
        if missing_benchmarks:
            hard_failures.append(f"missing_benchmarks:{','.join(missing_benchmarks)}")
        if stale_benchmarks:
            hard_failures.append(f"stale_benchmarks:{','.join(stale_benchmarks)}")

        if eligible_tickers and fresh_pct < float(fresh_warning_pct):
            warnings.append(f"fresh_eligible_pct<{fresh_warning_pct:g}")
        if stale_eligible:
            warnings.append(f"stale_eligible_tickers={stale_eligible}")

        # A mixed price basis silently fabricates breakouts, so it stops the
        # nightly rather than merely warning.
        conflicts = self.basis_conflicts()
        if conflicts:
            hard_failures.append(f"mixed_price_basis:{len(conflicts)}")

        # A forgotten archive re-download costs nothing but depth, so it warns
        # rather than blocking — but it must be visible, not silent.
        archive = self.archive_import_state()
        archive_age_days: int | None = None
        if archive and archive.get("archive_max_date"):
            archive_max = date.fromisoformat(str(archive["archive_max_date"]))
            archive_age_days = (latest_expected - archive_max).days
            if archive_age_days > int(archive_stale_warning_days):
                warnings.append(f"stooq_archive_stale_days={archive_age_days}")

        return {
            "ready": not hard_failures,
            "warnings": warnings,
            "hard_failures": hard_failures,
            "price_basis": self.basis_summary(),
            "basis_conflict_sample": [t for t, _ in conflicts[:10]],
            "stooq_archive": archive,
            "stooq_archive_age_days": archive_age_days,
            "latest_expected": latest_expected.isoformat(),
            "latest_bar": latest_bar_iso,
            "eligible_tickers": eligible_tickers,
            "liquidity_snapshot_tickers": liquidity_snapshot_tickers,
            "fresh_eligible_tickers": fresh_eligible,
            "fresh_eligible_pct": round(fresh_pct, 2),
            "stale_eligible_tickers": stale_eligible,
            "benchmark_latest": {
                ticker: value.isoformat() if value else None for ticker, value in benchmark_latest.items()
            },
        }
