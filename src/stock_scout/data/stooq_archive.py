"""Import the Stooq bulk .txt archive into the market store.

Stooq publishes daily archives as directory trees of per-symbol text files
(`d_us_txt`, `d_world_txt`, `d_macro_txt`). This module reads them with DuckDB's
native CSV reader rather than going through `BaseDataProvider`: the provider
contract is per-ticker request/response, and pushing 13k files through
`get_daily_ohlcv` -> `normalize_ohlcv` -> `upsert_ohlcv` would take hours where a
set-based read takes under a minute.

Two things about the format are load-bearing:

* `<TICKER>` uses a dash for class shares (`BRK-B.US`), while the registry uses a
  dot (`BRK.B`). The dash->dot rule is treated as a hint and resolved against
  `securities`, not trusted outright.
* The directory depth is uneven — `nasdaq stocks/{1,2,3}` is nested while
  `nasdaq etfs` and the `nysemkt` folders are flat — so the glob must recurse.
"""

from __future__ import annotations

import glob as _glob
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from stock_scout.data.etf_classify import EtfKind, classify_etf_name
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

# Explicit schema: DuckDB's sniffer costs ~8x more on this many small files.
_COLUMNS = (
    "{'tk':'VARCHAR','per':'VARCHAR','dt':'VARCHAR','tm':'VARCHAR',"
    "'o':'DOUBLE','h':'DOUBLE','l':'DOUBLE','c':'DOUBLE','v':'DOUBLE','oi':'DOUBLE'}"
)


@dataclass(frozen=True)
class ArchiveDomain:
    """One slice of the archive, with the date floor appropriate to its content."""

    name: str
    glob: str
    min_date: date
    kind: str  # "equity" -> ohlcv_daily, "reference" -> ohlcv_reference
    frequency: str = "daily"
    # A price cannot be <= 0, but a macro reading or a bond yield certainly can
    # (CPI year-over-year goes negative, as do European yields).
    require_positive_close: bool = True


# A single date floor would be wrong in both directions: it would delete a
# century of genuine macro history (US CPI starts 1801, the 10y Treasury yield
# 1871) while still admitting the junk 1970-01-02 rows in the equity files.
DOMAINS: tuple[ArchiveDomain, ...] = (
    # The `*/` before `**` is load-bearing: it requires an exchange directory
    # ("nasdaq stocks/1", "nyse etfs") and so skips files dropped straight into
    # us/. Stooq's dated bundles land there — 20260725_d.txt held 56 world
    # indices and 3,938 money-market rate symbols, and without this every one
    # of them would have been ingested as a US equity.
    ArchiveDomain("us", "d_us_txt/data/daily/us/*/**/*.txt", date(1962, 1, 2), "equity"),
    ArchiveDomain(
        "indices", "d_world_txt/data/daily/world/indices/**/*.txt", date(1900, 1, 1), "reference"
    ),
    ArchiveDomain(
        "bonds", "d_world_txt/data/daily/world/bonds/**/*.txt", date(1800, 1, 1), "reference",
        require_positive_close=False,
    ),
    ArchiveDomain(
        "currencies",
        "d_world_txt/data/daily/world/currencies/**/*.txt",
        date(1900, 1, 1),
        "reference",
    ),
    ArchiveDomain(
        "crypto",
        "d_world_txt/data/daily/world/cryptocurrencies/**/*.txt",
        date(1900, 1, 1),
        "reference",
    ),
    ArchiveDomain(
        "macro",
        "d_macro_txt/data/daily/macro/**/*.txt",
        date(1800, 1, 1),
        "reference",
        frequency="monthly",
        require_positive_close=False,
    ),
)

_EQUITY_DOMAINS = tuple(d for d in DOMAINS if d.kind == "equity")
_REFERENCE_DOMAINS = tuple(d for d in DOMAINS if d.kind == "reference")


@dataclass
class DomainResult:
    domain: str
    files: int = 0
    rows: int = 0
    symbols: int = 0
    min_date: date | None = None
    max_date: date | None = None


@dataclass
class ImportReport:
    """What an import did, or would do under --dry-run."""

    archive_dir: str
    dry_run: bool
    domains: list[DomainResult] = field(default_factory=list)
    # Reconciliation against securities.
    matched: int = 0
    stooq_only: int = 0
    registry_only: list[str] = field(default_factory=list)
    # ETF handling.
    etf_kinds: dict[str, int] = field(default_factory=dict)
    excluded_leveraged_symbols: int = 0
    excluded_leveraged_rows: int = 0
    equity_rows_written: int = 0
    reference_rows_written: int = 0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_dir": self.archive_dir,
            "dry_run": self.dry_run,
            "domains": [
                {
                    "domain": d.domain,
                    "files": d.files,
                    "rows": d.rows,
                    "symbols": d.symbols,
                    "min_date": d.min_date.isoformat() if d.min_date else None,
                    "max_date": d.max_date.isoformat() if d.max_date else None,
                }
                for d in self.domains
            ],
            "matched": self.matched,
            "stooq_only": self.stooq_only,
            "registry_only_count": len(self.registry_only),
            "registry_only_sample": self.registry_only[:25],
            "etf_kinds": self.etf_kinds,
            "excluded_leveraged_symbols": self.excluded_leveraged_symbols,
            "excluded_leveraged_rows": self.excluded_leveraged_rows,
            "equity_rows_written": self.equity_rows_written,
            "reference_rows_written": self.reference_rows_written,
            "duration_seconds": round(self.duration_seconds, 1),
        }


def _posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def resolve_domains(names: Sequence[str] | None) -> tuple[ArchiveDomain, ...]:
    """Map CLI domain names to definitions. "us"/"reference" select whole groups."""
    if not names:
        return DOMAINS
    wanted = {str(n).strip().lower() for n in names if str(n).strip()}
    if "all" in wanted:
        return DOMAINS
    out: list[ArchiveDomain] = []
    for domain in DOMAINS:
        if domain.name in wanted or (domain.kind in wanted):
            out.append(domain)
    unknown = wanted - {d.name for d in DOMAINS} - {d.kind for d in DOMAINS} - {"all"}
    if unknown:
        raise ValueError(f"Unknown archive domain(s): {sorted(unknown)}")
    return tuple(out)


def archive_state(archive_dir: Path | str) -> dict[str, Any]:
    """Cheap freshness probe: how many US files and how recent the newest bar is.

    Used by the `auto` refresh mode to decide whether re-importing would add
    anything, without parsing the whole archive.
    """
    root = Path(archive_dir)
    us_glob = _posix(root / _EQUITY_DOMAINS[0].glob)
    files = _glob.glob(us_glob, recursive=True)
    newest: date | None = None
    if files:
        # The newest bar lives at the end of a recently written file, but the
        # most recent one may be empty — the real archive ships 37 zero-byte
        # files — so sample a handful rather than trusting a single mtime.
        candidates = sorted(files, key=os.path.getmtime, reverse=True)[:25]
        for path in candidates:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
                if len(lines) < 2:
                    continue
                stamp = lines[-1].split(",")[2]
                parsed = datetime.strptime(stamp, "%Y%m%d").date()
            except Exception as e:  # noqa: BLE001 - a probe must never break a scan
                log.debug("stooq_archive.state_probe_failed", path=path, error=repr(e))
                continue
            if newest is None or parsed > newest:
                newest = parsed
    return {
        "archive_dir": str(root),
        "exists": root.exists(),
        "us_files": len(files),
        "archive_max_date": newest,
    }


def _duckdb():
    try:
        import duckdb
    except ImportError as e:  # pragma: no cover - dependency is installed in CI/dev
        raise RuntimeError("duckdb is required. Install project dependencies first.") from e
    return duckdb


def _read_domain_sql(root: Path, domain: ArchiveDomain) -> str:
    """SELECT that normalizes one domain's files into a common shape.

    `strptime` on the raw YYYYMMDD text, both ticker spellings, and the source
    path (used for exchange/ETF tagging) come out here; filtering and joining
    happen in the caller so each domain's floor applies to its own content.
    """
    pattern = _posix(root / domain.glob)
    # A price cannot be <= 0. Applied to every leg, not just the close: the
    # archive carries a handful of bars with a zero open or low, and they fail
    # the store's integrity checks on the way back in.
    if domain.require_positive_close:
        close_clause = "AND c IS NOT NULL AND c > 0 AND o > 0 AND h > 0 AND l > 0"
    else:
        close_clause = "AND c IS NOT NULL"
    # Belt to the glob's braces. Every US equity row carries the .US suffix —
    # verified across all 27.8M rows of the archive, zero exceptions — so a row
    # without it in this domain is a misplaced file, not a stock. The glob
    # tightening catches the bundle we know about; this catches the next one.
    symbol_clause = "AND upper(tk) LIKE '%.US'" if domain.kind == "equity" else ""
    # Third guard, for a hazard the other two miss: an intraday bundle with real
    # .US tickers under an exchange directory would yield many rows per calendar
    # date and silently collapse to whichever one the dedupe kept. 20260724_dh5
    # was exactly that shape, at PER=5. Every equity row in the archive is PER=D
    # — all 27.8M of them — so this rejects nothing real. Only equity: PER is
    # unreliable for reference series, where CPIYUS.M is monthly but stamped D.
    period_clause = "AND upper(per) = 'D'" if domain.kind == "equity" else ""
    # Clamp rather than drop where the wick does not enclose the body: the body
    # itself is credible, and dropping would punch a hole in the series. A
    # no-op on coherent bars, and correct for negative macro values too.
    return f"""
        SELECT
            upper(replace(tk, '.US', ''))                    AS symbol_raw,
            upper(replace(replace(tk, '.US', ''), '-', '.')) AS symbol_dot,
            strptime(dt, '%Y%m%d')::DATE                     AS date,
            o AS open,
            greatest(o, c, h) AS high,
            least(o, c, l)    AS low,
            c AS close, v AS volume,
            filename                                          AS source_path
        FROM read_csv('{pattern}', header=true, auto_detect=false, filename=true,
                      columns={_COLUMNS}, ignore_errors=true)
        WHERE dt IS NOT NULL
          AND try_strptime(dt, '%Y%m%d') IS NOT NULL
          AND strptime(dt, '%Y%m%d')::DATE >= DATE '{domain.min_date.isoformat()}'
          AND strptime(dt, '%Y%m%d')::DATE <= current_date + 7
          {close_clause}
          {symbol_clause}
          {period_clause}
    """


def _domain_has_files(root: Path, domain: ArchiveDomain) -> bool:
    """DuckDB raises on a glob that matches nothing, and a partial download (say,
    only d_us_txt) is a normal thing for a user to have."""
    return bool(_glob.glob(_posix(root / domain.glob), recursive=True))


def _stage_equities(con, root: Path, domains: Iterable[ArchiveDomain]) -> DomainResult | None:
    """Build `_stg_equity`, tagged with exchange and ETF flag from the file path."""
    parts = [_read_domain_sql(root, d) for d in domains if _domain_has_files(root, d)]
    if not parts:
        return None
    union = "\nUNION ALL\n".join(parts)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE _stg_equity AS
        SELECT symbol_raw, symbol_dot, date, open, high, low, close, volume,
               regexp_extract(path, 'daily/us/([a-z]+) (?:stocks|etfs)/', 1) AS exchange,
               regexp_extract(path, 'daily/us/[a-z]+ (stocks|etfs)/', 1) = 'etfs' AS is_etf,
               source_path
        FROM (SELECT *, replace(source_path, chr(92), '/') AS path FROM ({union}))
        """
    )
    row = con.execute(
        "SELECT count(*), count(DISTINCT symbol_raw), min(date), max(date),"
        " count(DISTINCT source_path) FROM _stg_equity"
    ).fetchone()
    return DomainResult(
        domain="us", rows=int(row[0]), symbols=int(row[1]), min_date=row[2], max_date=row[3],
        files=int(row[4]),
    )


def _stage_reference(con, root: Path, domains: Iterable[ArchiveDomain]) -> list[DomainResult]:
    """Build `_stg_reference`. Indices, FX, bonds, crypto and macro live apart
    from equities so they can never leak into the scan universe or the
    cross-sectional RS distribution."""
    results: list[DomainResult] = []
    con.execute(
        """
        CREATE OR REPLACE TABLE _stg_reference (
            symbol VARCHAR, date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
            close DOUBLE, volume DOUBLE, domain VARCHAR, frequency VARCHAR
        )
        """
    )
    for domain in domains:
        found = _glob.glob(_posix(root / domain.glob), recursive=True)
        if not found:
            log.info("stooq_archive.domain_absent", domain=domain.name)
            results.append(DomainResult(domain=domain.name))
            continue
        con.execute(
            f"""
            INSERT INTO _stg_reference
            SELECT symbol_raw, date, open, high, low, close, volume,
                   '{domain.name}', '{domain.frequency}'
            FROM ({_read_domain_sql(root, domain)})
            """
        )
        row = con.execute(
            "SELECT count(*), count(DISTINCT symbol), min(date), max(date)"
            " FROM _stg_reference WHERE domain = ?",
            [domain.name],
        ).fetchone()
        results.append(
            DomainResult(
                domain=domain.name, rows=int(row[0]), symbols=int(row[1]),
                min_date=row[2], max_date=row[3], files=len(found),
            )
        )
    return results


def _resolve_against_registry(con, securities: list[tuple[str, str | None, bool]]) -> None:
    """Attach registry identity to staged equities.

    The dash->dot transform is a hint; the registry decides. `_stg_equity_resolved`
    gains the canonical ticker, the security name, and the ETF kind.
    """
    con.execute(
        "CREATE OR REPLACE TABLE _securities_raw (ticker VARCHAR, name VARCHAR, etf BOOLEAN)"
    )
    if securities:
        con.executemany("INSERT INTO _securities_raw VALUES (?, ?, ?)", securities)
    # The registry genuinely collides: NAN is both Nano Labs (NASDAQ) and a
    # Nuveen municipal fund (NYSE). Joining twice against an undeduplicated
    # table fans every bar out 2x2, so collapse to one row per ticker first.
    con.execute(
        """
        CREATE OR REPLACE TABLE _securities AS
        SELECT ticker, any_value(name) AS name, bool_or(etf) AS etf
        FROM _securities_raw GROUP BY ticker
        """
    )
    con.execute("DROP TABLE IF EXISTS _securities_raw")
    con.execute(
        """
        CREATE OR REPLACE TABLE _stg_equity_resolved AS
        SELECT
            COALESCE(sd.ticker, sr.ticker, s.symbol_dot) AS ticker,
            COALESCE(sd.name, sr.name)                   AS name,
            (sd.ticker IS NOT NULL OR sr.ticker IS NOT NULL) AS in_registry,
            COALESCE(sd.etf, sr.etf, s.is_etf)           AS etf,
            s.date, s.open, s.high, s.low, s.close, s.volume, s.exchange
        FROM _stg_equity s
        LEFT JOIN _securities sd ON sd.ticker = s.symbol_dot
        LEFT JOIN _securities sr ON sr.ticker = s.symbol_raw
        """
    )


def _apply_etf_policy(con, report: ImportReport) -> None:
    """Drop leveraged and inverse ETFs, and record what each remaining one is.

    Classification runs in Python (the rules are name-based and non-trivial), so
    only the distinct symbol/name pairs cross the boundary, not the rows.
    """
    pairs = con.execute(
        "SELECT DISTINCT ticker, name, etf FROM _stg_equity_resolved WHERE etf"
    ).fetchall()
    kinds: dict[str, EtfKind] = {}
    counts: dict[str, int] = {}
    for ticker, name, _etf in pairs:
        kind = classify_etf_name(name)
        kinds[str(ticker)] = kind
        counts[kind] = counts.get(kind, 0) + 1
    report.etf_kinds = counts

    excluded = [t for t, k in kinds.items() if k in ("leveraged", "inverse")]
    con.execute("CREATE OR REPLACE TABLE _etf_kind (ticker VARCHAR, kind VARCHAR)")
    if kinds:
        con.executemany("INSERT INTO _etf_kind VALUES (?, ?)", list(kinds.items()))
    report.excluded_leveraged_symbols = len(excluded)
    if excluded:
        placeholders = ", ".join("?" for _ in excluded)
        report.excluded_leveraged_rows = int(
            con.execute(
                f"SELECT count(*) FROM _stg_equity_resolved WHERE ticker IN ({placeholders})",
                excluded,
            ).fetchone()[0]
        )
        con.execute(
            f"DELETE FROM _stg_equity_resolved WHERE ticker IN ({placeholders})", excluded
        )


def parse_archive(
    con,
    archive_dir: Path | str,
    domains: Sequence[ArchiveDomain],
    securities: list[tuple[str, str | None, bool]],
    report: ImportReport,
    scan_tickers: set[str] | None = None,
) -> None:
    """Stage every requested domain into `con` and reconcile against the registry.

    `scan_tickers` narrows the "missing from the archive" report to the tickers
    the scan actually uses, rather than every listed security.
    """
    root = Path(archive_dir)
    equity_domains = [d for d in domains if d.kind == "equity"]
    reference_domains = [d for d in domains if d.kind == "reference"]

    if equity_domains:
        staged = _stage_equities(con, root, equity_domains)
        if staged is not None:
            report.domains.append(staged)
        _resolve_against_registry(con, securities)
        _apply_etf_policy(con, report)
        row = con.execute(
            "SELECT count(*) FILTER (WHERE in_registry), count(*) FILTER (WHERE NOT in_registry)"
            " FROM (SELECT DISTINCT ticker, in_registry FROM _stg_equity_resolved)"
        ).fetchone()
        report.matched, report.stooq_only = int(row[0]), int(row[1])
        expected = scan_tickers if scan_tickers is not None else {t for t, _n, _e in securities}
        archive_tickers = {
            r[0] for r in con.execute("SELECT DISTINCT ticker FROM _stg_equity_resolved").fetchall()
        }
        report.registry_only = sorted(expected - archive_tickers)

    if reference_domains:
        report.domains.extend(_stage_reference(con, root, reference_domains))


REFERENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv_reference (
    symbol VARCHAR NOT NULL,
    date DATE NOT NULL,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE,
    domain VARCHAR NOT NULL,
    frequency VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    PRIMARY KEY (symbol, date)
)
"""


def rebuild_store_from_archive(
    staging_path: Path | str,
    *,
    old_store_path: Path | str,
    new_store_path: Path | str,
    memory_limit: str = "4GB",
) -> dict[str, Any]:
    """Build a fresh store whose prices are entirely on the archive's basis.

    Deliberately a rebuild rather than a merge. Appending deep history to the
    existing dividend-adjusted rows would splice two price bases inside every
    ticker; and since the old file carries ~328 bytes per row of accumulated
    tombstones with no VACUUM available in DuckDB, rewriting is also how the
    bloat gets reclaimed.

    Tickers absent from the archive are simply not carried over: an absent
    ticker is recoverable by the next network delta, whereas a ticker left on
    the old basis is a silent, permanent fake breakout.
    """
    duckdb = _duckdb()
    from stock_scout.data.market_store import MarketDataStore

    new_path = Path(new_store_path)
    if new_path.exists():
        raise FileExistsError(f"{new_path} already exists; move it aside first")

    # Create the schema through the store itself so it cannot drift.
    MarketDataStore(new_path).close()

    con = duckdb.connect(str(new_path))
    try:
        con.execute("SET enable_progress_bar=false")
        con.execute(f"SET memory_limit='{memory_limit}'")
        con.execute(f"ATTACH '{_posix(staging_path)}' AS stg (READ_ONLY)")
        con.execute(f"ATTACH '{_posix(old_store_path)}' AS old (READ_ONLY)")

        # Sorted by ticker so DuckDB's zone maps prune row groups on ticker
        # predicates; the table is intentionally left unindexed for write safety.
        con.execute(
            """
            INSERT INTO ohlcv_daily
            SELECT ticker, date, open, high, low, close, CAST(volume AS BIGINT),
                   NULL, FALSE, 'stooq_archive', current_localtimestamp(), 'split_only'
            FROM stg.stooq_equity ORDER BY ticker, date
            """
        )
        rows = int(con.execute("SELECT count(*) FROM ohlcv_daily").fetchone()[0])
        tickers = int(con.execute("SELECT count(DISTINCT ticker) FROM ohlcv_daily").fetchone()[0])

        con.execute("INSERT INTO securities SELECT * FROM old.securities")
        con.execute("INSERT INTO ipo_dates SELECT * FROM old.ipo_dates")

        # Record what this import saw so freshness checks stay a one-row query.
        archive_max = con.execute("SELECT max(date) FROM ohlcv_daily").fetchone()[0]
        con.execute(
            "INSERT INTO stooq_archive_state VALUES (?, current_localtimestamp(), ?, ?, ?)",
            [str(staging_path), archive_max, tickers, rows],
        )

        # Carry the ETF classification so the ETF section can filter on it.
        con.execute(
            "CREATE TABLE IF NOT EXISTS etf_kind (ticker TEXT NOT NULL, kind TEXT NOT NULL)"
        )
        con.execute("INSERT INTO etf_kind SELECT ticker, kind FROM stg.stooq_etf_kind")

        # Reference series live here too, but in their own table.
        con.execute(REFERENCE_SCHEMA)
        has_reference = bool(
            con.execute(
                "SELECT count(*) FROM duckdb_tables() WHERE database_name = 'stg'"
                " AND table_name = 'ohlcv_reference'"
            ).fetchone()[0]
        )
        reference_rows = 0
        if has_reference:
            con.execute("INSERT INTO ohlcv_reference SELECT * FROM stg.ohlcv_reference")
            reference_rows = int(
                con.execute("SELECT count(*) FROM ohlcv_reference").fetchone()[0]
            )

        coverage = con.execute(
            "SELECT min(mn), max(mx), min(n), median(n), max(n) FROM ("
            "  SELECT count(*) n, min(date) mn, max(date) mx FROM ohlcv_daily GROUP BY ticker)"
        ).fetchone()
        con.execute("DETACH stg")
        con.execute("DETACH old")
    finally:
        con.close()

    return {
        "new_store": str(new_path),
        "ohlcv_rows": rows,
        "ohlcv_tickers": tickers,
        "reference_rows": reference_rows,
        "min_date": coverage[0].isoformat() if coverage and coverage[0] else None,
        "max_date": coverage[1].isoformat() if coverage and coverage[1] else None,
        "bars_per_ticker_min_median_max": [
            int(coverage[2]), float(coverage[3]), int(coverage[4])
        ] if coverage else None,
        "size_bytes": new_path.stat().st_size,
    }


def rebuild_from_archive_and_swap(
    settings,
    *,
    archive_dir: Path | str | None = None,
    domains: Sequence[str] | None = None,
    memory_limit: str = "4GB",
    keep_backup: bool = True,
    progress: Any = None,
) -> dict[str, Any]:
    """Parse the archive, rebuild the store, verify it, then swap it in.

    The whole cycle in one call so the app can offer it as a button. Nothing
    touches the live store until a freshly built one has passed its integrity
    checks, and the old file is kept, so a failure anywhere leaves the running
    store exactly as it was.
    """
    from stock_scout.data.market_store import MarketDataStore, market_store_path

    def say(msg: str) -> None:
        log.info("stooq_rebuild.step", step=msg)
        if callable(progress):
            progress(msg)

    live = Path(market_store_path(settings))
    root = Path(archive_dir or settings.marketdata.stooq_archive_dir)
    if not root.is_absolute():
        root = settings.project_root / root
    if not root.exists():
        raise RuntimeError(f"Stooq archive not found at {root}")

    staging = live.parent / "stooq_staging.duckdb"
    candidate = live.parent / "market_new.duckdb"
    backup = live.parent / "market.duckdb.pre_stooq.bak"
    for path in (staging, candidate):
        if path.exists():
            path.unlink()

    out: dict[str, Any] = {"archive_dir": str(root)}
    try:
        say("Parsing the Stooq archive")
        report = import_stooq_archive(
            root, store_path=live, target=staging, domains=domains, memory_limit=memory_limit
        )
        out["import"] = report.to_dict()

        say("Rebuilding the store")
        out["rebuild"] = rebuild_store_from_archive(
            staging, old_store_path=live, new_store_path=candidate, memory_limit=memory_limit
        )

        say("Recomputing liquidity")
        out["liquidity"] = _refresh_liquidity(settings, candidate)

        say("Verifying the rebuilt store")
        store = MarketDataStore(candidate, read_only=True)
        try:
            integrity = store.integrity_report()
        finally:
            store.close()
        out["verify"] = integrity
        if not integrity["ok"]:
            # Leave the live store untouched; the candidate stays for inspection.
            raise RuntimeError(
                f"Rebuilt store failed integrity checks: {', '.join(integrity['problems'])}"
            )

        say("Swapping in the new store")
        if backup.exists():
            backup.unlink()
        if keep_backup:
            live.replace(backup)
        else:
            live.unlink()
        candidate.replace(live)
        out["swapped"] = True
        out["backup"] = str(backup) if keep_backup else None

        # Record what the archive held, so freshness checks stay a one-row query.
        state = archive_state(root)
        store = MarketDataStore(live)
        try:
            store.record_archive_import(
                str(root),
                archive_max_date=state.get("archive_max_date"),
                files=int(state.get("us_files") or 0),
                rows=int(out["rebuild"].get("ohlcv_rows") or 0),
            )
        finally:
            store.close()
        say("Done")
    finally:
        if staging.exists():
            try:
                staging.unlink()
            except OSError as e:  # noqa: PERF203 - best effort cleanup
                log.warning("stooq_rebuild.staging_cleanup_failed", error=repr(e))
    return out


def _refresh_liquidity(settings, store_path: Path) -> dict[str, int]:
    """Recompute eligibility against the rebuilt store before it goes live."""
    from stock_scout.data.market_store import MarketDataStore
    from stock_scout.utils.dates import last_trading_day

    pf = settings.prefilter
    store = MarketDataStore(store_path)
    try:
        tickers = store.scan_universe_tickers(eligible_only=False)
        metrics = store.liquidity_metrics(
            tickers, lookback_bars=max(60, pf.min_history_days)
        )
        rows: list[dict[str, Any]] = []
        eligible = 0
        for ticker in tickers:
            m = metrics.get(ticker)
            reasons: list[str] = []
            if m is None or not m["bars_available"]:
                reasons.append("missing_ohlcv")
                last_close = avg_vol = avg_dvol = 0.0
                bars = 0
            else:
                last_close = float(m["last_close"])
                avg_vol = float(m["avg_volume_50d"])
                avg_dvol = float(m["avg_dollar_volume_50d"])
                bars = int(m["bars_available"])
                if bars < pf.min_history_days:
                    reasons.append(f"bars<{pf.min_history_days}")
                if last_close < pf.min_price:
                    reasons.append(f"price<{pf.min_price}")
                if avg_vol < pf.min_avg_volume_50d:
                    reasons.append("avg_vol_50d_low")
                if avg_dvol < pf.min_avg_dollar_volume_50d:
                    reasons.append("avg_dollar_vol_50d_low")
            ok = not reasons
            eligible += ok
            rows.append(
                {
                    "ticker": ticker, "last_close": last_close,
                    "avg_volume_50d": avg_vol, "avg_dollar_volume_50d": avg_dvol,
                    "bars_available": bars, "eligible": ok, "reason": ",".join(reasons),
                }
            )
        store.write_liquidity_snapshot(rows, last_trading_day())
        return {"rows": len(rows), "eligible": eligible}
    finally:
        store.close()


def import_stooq_archive(
    archive_dir: Path | str,
    *,
    store_path: Path | str,
    target: Path | str | None = None,
    domains: Sequence[str] | None = None,
    dry_run: bool = False,
    memory_limit: str = "4GB",
) -> ImportReport:
    """Parse the archive and, unless `dry_run`, stage it into `target`.

    Reads `securities` from the live store read-only and never writes to it: the
    live store is only swapped for a rebuilt file in a later, explicit step.
    """
    import time

    started = time.monotonic()
    duckdb = _duckdb()
    selected = resolve_domains(domains)
    report = ImportReport(archive_dir=str(archive_dir), dry_run=dry_run)

    src = duckdb.connect(str(store_path), read_only=True)
    try:
        securities = [
            (str(r[0]), r[1], bool(r[2]))
            for r in src.execute("SELECT ticker, name, etf FROM securities").fetchall()
        ]
        scan_tickers = {
            str(r[0])
            for r in src.execute(
                "SELECT ticker FROM securities WHERE include_in_scan"
            ).fetchall()
        }
    finally:
        src.close()

    target_path = ":memory:" if (dry_run or target is None) else str(target)
    con = duckdb.connect(target_path)
    try:
        con.execute("SET enable_progress_bar=false")
        con.execute(f"SET memory_limit='{memory_limit}'")
        parse_archive(con, archive_dir, selected, securities, report, scan_tickers)

        if not dry_run and target is not None:
            con.execute(REFERENCE_SCHEMA)
            if any(d.kind == "equity" for d in selected):
                # Physically clustered by ticker: DuckDB's zone maps then prune
                # row groups on ticker predicates without any index, which is
                # what keeps point reads fast on a table we deliberately leave
                # unindexed for write reliability.
                con.execute(
                    """
                    CREATE OR REPLACE TABLE stooq_equity AS
                    SELECT ticker, date, open, high, low, close, volume, exchange, etf
                    FROM _stg_equity_resolved ORDER BY ticker, date
                    """
                )
                report.equity_rows_written = int(
                    con.execute("SELECT count(*) FROM stooq_equity").fetchone()[0]
                )
                con.execute(
                    "CREATE OR REPLACE TABLE stooq_etf_kind AS SELECT * FROM _etf_kind"
                )
            if any(d.kind == "reference" for d in selected):
                con.execute(
                    """
                    INSERT OR REPLACE INTO ohlcv_reference
                    SELECT symbol, date, open, high, low, close, volume, domain,
                           frequency, 'stooq_archive'
                    FROM _stg_reference ORDER BY symbol, date
                    """
                )
                report.reference_rows_written = int(
                    con.execute("SELECT count(*) FROM ohlcv_reference").fetchone()[0]
                )
        for name in ("_stg_equity", "_stg_equity_resolved", "_stg_reference", "_securities"):
            con.execute(f"DROP TABLE IF EXISTS {name}")
    finally:
        con.close()

    report.duration_seconds = time.monotonic() - started
    return report


# --- daily bundles ----------------------------------------------------------
#
# Stooq publishes a one-day, all-symbols file alongside the full archive.
# 20260723_d.txt held 11,729 US symbols in 698 KB: one row each, PER=D, one
# date, no bad prices. That is the same day's data the network delta spends
# ~0.55 s per ticker to assemble — about 20 minutes for the eligible universe —
# so a bundle import replaces the delta outright, on the same split-only basis
# and therefore without a seam.
#
# Stooq's world bundle has the identical filename shape (20260725_d.txt), and
# the intraday one differs only by an infix (20260724_dh5.txt). Nothing in the
# name distinguishes them, so bundles are identified by content: US equity rows
# carry the .US suffix and PER=D.

_BUNDLE_SELECT = """
    SELECT
        upper(replace(tk, '.US', ''))                    AS symbol_raw,
        upper(replace(replace(tk, '.US', ''), '-', '.')) AS symbol_dot,
        strptime(dt, '%Y%m%d')::DATE                     AS date,
        o AS open,
        greatest(o, c, h) AS high,
        least(o, c, l)    AS low,
        c AS close, v AS volume
    FROM read_csv({source}, header=true, auto_detect=false,
                  columns={columns}, ignore_errors=true)
    WHERE dt IS NOT NULL
      AND try_strptime(dt, '%Y%m%d') IS NOT NULL
      AND strptime(dt, '%Y%m%d')::DATE >= DATE '{floor}'
      AND strptime(dt, '%Y%m%d')::DATE <= current_date + 7
      AND upper(tk) LIKE '%.US'
      AND upper(per) = 'D'
      AND c IS NOT NULL AND c > 0 AND o > 0 AND h > 0 AND l > 0
"""


def find_daily_bundles(paths: Iterable[Path | str]) -> list[Path]:
    """Expand files and directories into a sorted list of candidate bundles.

    Shape only — whether a file actually holds US equity rows is decided by
    reading it, because the name cannot tell you.
    """
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(q for q in p.glob("*.txt") if q.is_file()))
        elif p.is_file():
            out.append(p)
    seen: set[str] = set()
    unique: list[Path] = []
    for p in out:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def import_daily_bundles(
    settings,
    paths: Iterable[Path | str],
    *,
    dry_run: bool = False,
    skip_imported: bool = False,
) -> dict[str, Any]:
    """Read Stooq daily bundles and upsert their US equity rows into the store.

    Tickers are resolved against the registry the same way the full import does
    — the dash-to-dot transform is a hint, `securities` decides — so BRK-B.US
    lands on BRK.B rather than creating a second series.

    A bundle carries one day against history the archive established, so a
    split between the archive download and the bundle would append a post-split
    price to a pre-split series. That exposure is not new: the yfinance delta it
    replaces has exactly the same shape, and the periodic full archive import
    rewrites every ticker's whole series, which is what corrects it.
    """
    import pandas as pd

    from stock_scout.data.market_store import MarketDataStore

    files = find_daily_bundles(paths)
    out: dict[str, Any] = {
        "files": [str(p) for p in files],
        "rows_read": 0,
        "rows_written": 0,
        "tickers": 0,
        "skipped_files": [],
        "excluded_leveraged_rows": 0,
        "min_date": None,
        "max_date": None,
        "dry_run": bool(dry_run),
    }
    if not files:
        return out

    store = MarketDataStore.from_settings(settings, read_only=dry_run)
    con = _duckdb().connect()
    try:
        con.execute("SET enable_progress_bar=false")
        securities = [
            (str(t), None, False) for t in store.scan_universe_tickers(eligible_only=False)
        ]
        seen = store.bundle_import_fingerprints() if skip_imported else set()
        frames = []
        consumed: list[tuple[Path, int, float, int, Any]] = []
        for path in files:
            stat = path.stat()
            fingerprint = (str(path.resolve()).lower(), int(stat.st_size), int(stat.st_mtime))
            if fingerprint in seen:
                out["skipped_files"].append({"path": str(path), "reason": "already imported"})
                continue
            sql = _BUNDLE_SELECT.format(
                source=f"'{_posix(path)}'", columns=_COLUMNS, floor="1962-01-02"
            )
            df = con.execute(sql).fetch_df()
            if df.empty:
                # A world or intraday bundle: correctly readable, nothing for us.
                out["skipped_files"].append({"path": str(path), "reason": "no US daily equity rows"})
                continue
            frames.append(df)
            consumed.append(
                (path, int(stat.st_size), float(stat.st_mtime), int(len(df)), df["date"].max())
            )
        if not frames:
            return out

        raw = pd.concat(frames, ignore_index=True)
        out["rows_read"] = int(len(raw))

        known = {str(t).upper() for t, _n, _e in securities}
        # Registry first, dash->dot hint second, raw spelling last.
        resolved = raw["symbol_dot"].where(raw["symbol_dot"].isin(known), raw["symbol_raw"])
        raw = raw.assign(ticker=resolved)
        rows = raw[["ticker", "date", "open", "high", "low", "close", "volume"]].copy()
        rows = rows.sort_values("date").drop_duplicates(["ticker", "date"], keep="last")

        # A bundle carries every US symbol, leveraged and inverse ETFs included.
        # The full archive import drops those; a bundle import that did not
        # would quietly put them back — 697 of them, the first time this ran.
        excluded = store.excluded_etf_tickers()
        if excluded:
            before = len(rows)
            rows = rows[~rows["ticker"].str.upper().isin(excluded)]
            out["excluded_leveraged_rows"] = int(before - len(rows))

        out["tickers"] = int(rows["ticker"].nunique())
        out["min_date"] = str(pd.to_datetime(rows["date"]).min().date())
        out["max_date"] = str(pd.to_datetime(rows["date"]).max().date())
        if not dry_run:
            out["rows_written"] = store.upsert_ohlcv_many(
                rows, provider="stooq_bundle", adjusted=False
            )
            # Recorded only after the write lands, so a crash mid-import leaves
            # the bundle looking unconsumed and the next run picks it up again.
            for path, size, mtime, nrows, max_date in consumed:
                store.record_bundle_import(
                    str(path.resolve()),
                    size=size,
                    mtime=mtime,
                    rows=nrows,
                    max_date=pd.to_datetime(max_date).date() if max_date is not None else None,
                )
        return out
    finally:
        con.close()
        store.close()
