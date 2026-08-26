"""Sector / industry cache.

Rebuilt weekly from FMP `/stable/company-screener` (one bulk call, ~20k rows).
Daily runs read this parquet to enrich Candidates with sector/industry, so the
AI ranker can reason about peer groups without paying per-ticker API calls.

If the cache is missing or stale (>14 days), the pipeline still runs — sector
fields just stay None and a warning is logged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

STALE_AFTER_DAYS = 14


@dataclass
class SectorEntry:
    sector: str | None
    industry: str | None


def rebuild_sector_cache(fmp_provider, out_path: Path) -> pd.DataFrame:
    """Pull the FMP bulk sector map and write it to `out_path`.

    `fmp_provider` must be an FMPDataProvider instance (duck-typed: needs
    `get_bulk_sectors()`). Returns the written DataFrame (may be empty if FMP
    fails — caller should check)."""
    df = fmp_provider.get_bulk_sectors()
    if df.empty:
        log.warning("sector_cache.bulk_empty")
        return df
    df = df.copy()
    df["ticker"] = df["ticker"].astype(str).str.upper()
    df["evaluated_at"] = datetime.now(timezone.utc).isoformat()
    df["source"] = "fmp"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    log.info("sector_cache.written", path=str(out_path), rows=len(df))
    return df


def build_sector_cache_from_profiles(
    tickers: list[str],
    path: Path,
    profile_provider,
    max_new: int | None = None,
) -> dict[str, SectorEntry]:
    """Incrementally backfill sector/industry from per-ticker company profiles.

    Used when the FMP bulk sector map is unavailable (free tier returns HTTP 402).
    `profile_provider` is duck-typed: it needs `get_company_profile(ticker)`
    returning an object with `.sector` / `.industry` (the yfinance provider).

    Sectors are near-static, so every ticker already present in the parquet is
    reused and only *missing* ones are fetched (bounded by `max_new`). The full
    merged map is rewritten and returned, ignoring the staleness window that
    `load_sector_cache` applies to the weekly FMP cache.
    """
    rows: dict[str, dict] = {}
    if path.exists():
        try:
            for r in pd.read_parquet(path).to_dict("records"):
                t = str(r.get("ticker", "")).upper()
                if t:
                    rows[t] = r
        except Exception as e:  # noqa: BLE001
            log.warning("sector_cache.profile_read_failed", path=str(path), error=repr(e))

    wanted = list(dict.fromkeys(t.upper() for t in tickers if t))
    todo = [t for t in wanted if t not in rows]
    if max_new is not None:
        todo = todo[: max(0, max_new)]

    now = datetime.now(timezone.utc).isoformat()
    fetched = 0
    for t in todo:
        try:
            prof = profile_provider.get_company_profile(t)
        except Exception as e:  # noqa: BLE001
            log.debug("sector_cache.profile_failed", ticker=t, error=repr(e))
            prof = None
        rows[t] = {
            "ticker": t,
            "sector": getattr(prof, "sector", None),
            "industry": getattr(prof, "industry", None),
            "evaluated_at": now,
            "source": "profiles",
        }
        fetched += 1

    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            pd.DataFrame(list(rows.values())).to_parquet(path, index=False)
        except Exception as e:  # noqa: BLE001
            log.warning("sector_cache.profile_write_failed", path=str(path), error=repr(e))
    log.info("sector_cache.profile_built", total=len(rows), newly_fetched=fetched)

    out: dict[str, SectorEntry] = {}
    for t, r in rows.items():
        sector = r.get("sector")
        industry = r.get("industry")
        out[t] = SectorEntry(
            sector=str(sector) if sector not in (None, "") and pd.notna(sector) else None,
            industry=str(industry) if industry not in (None, "") and pd.notna(industry) else None,
        )
    return out


def load_sector_cache(path: Path) -> dict[str, SectorEntry]:
    """Load the sector parquet into a {TICKER: SectorEntry} dict.

    Returns empty dict if file missing, unreadable, or older than STALE_AFTER_DAYS
    (a stale cache is treated as missing — better to skip enrichment than feed
    obsolete sectors into the ranker)."""
    if not path.exists():
        log.info("sector_cache.missing", path=str(path))
        return {}
    try:
        df = pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        log.warning("sector_cache.load_failed", path=str(path), error=str(e))
        return {}
    if df.empty:
        return {}
    age_days = _cache_age_days(df)
    # Only the weekly FMP bulk cache expires (forces a re-pull). A profile-built
    # or mixed cache is self-maintaining and near-static, so it never goes stale
    # — otherwise the Groups view would empty out 14 days after the last FMP pull.
    fmp_only = (
        "source" in df.columns
        and df["source"].notna().all()
        and (df["source"].astype(str) == "fmp").all()
    )
    if fmp_only and age_days is not None and age_days > STALE_AFTER_DAYS:
        log.warning("sector_cache.stale", path=str(path), age_days=round(age_days, 1))
        return {}
    out: dict[str, SectorEntry] = {}
    for _, row in df.iterrows():
        t = str(row.get("ticker", "")).upper()
        if not t:
            continue
        sector = row.get("sector")
        industry = row.get("industry")
        out[t] = SectorEntry(
            sector=str(sector) if pd.notna(sector) else None,
            industry=str(industry) if pd.notna(industry) else None,
        )
    log.info("sector_cache.loaded", rows=len(out), age_days=round(age_days, 1) if age_days else None)
    return out


def _cache_age_days(df: pd.DataFrame) -> float | None:
    if "evaluated_at" not in df.columns or df.empty:
        return None
    try:
        # Newest stamp in the cache — incremental backfills keep this current.
        ts = max(
            datetime.fromisoformat(str(x))
            for x in df["evaluated_at"]
            if x is not None and str(x) not in ("", "nan", "NaT")
        )
    except (ValueError, TypeError):
        return None
    age = datetime.now(timezone.utc) - ts
    return age / timedelta(days=1)
