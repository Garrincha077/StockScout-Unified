"""IPO / first-trade-date cache → IPO-by-year watchlists.

A curated seed list (``data/ipo_seed.json``, compiled from public IPO lists)
gives the IPO-year category content on day one. The authoritative source, though,
is the per-ticker first-trade date from the data provider (yfinance), resolved
incrementally — a few unknown tickers per scan so the daily run isn't slowed —
which *upgrades* seed guesses to the exact listing date over time. Because the
provider is authoritative, seed-year mistakes self-heal as tickers are resolved.

Cache schema (``data/ipo_cache.parquet``):
    ticker, ipo_date (ISO or None), ipo_year (int or None), source, resolved_at

``source`` is one of: ``seed`` (approx, from the seed list), ``yfinance``
(authoritative), ``unresolved`` (provider had no date — retried after a cooldown).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

_COLUMNS = ["ticker", "ipo_date", "ipo_year", "source", "resolved_at"]
_UNRESOLVED_RETRY_DAYS = 21


def _year_of(ipo_date: str | None) -> int | None:
    if not ipo_date or len(str(ipo_date)) < 4:
        return None
    try:
        return int(str(ipo_date)[:4])
    except ValueError:
        return None


def load_ipo_cache(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=_COLUMNS)
    try:
        return pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        log.warning("ipo_cache.read_failed", error=repr(e))
        return pd.DataFrame(columns=_COLUMNS)


def _rows_dict(df: pd.DataFrame) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in df.to_dict("records"):
        t = str(r.get("ticker", "")).upper()
        if t:
            out[t] = r
    return out


def _write(path: str | Path, rows: dict[str, dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(list(rows.values()), columns=_COLUMNS)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _is_stale(row: dict, days: int) -> bool:
    ts = row.get("resolved_at")
    if not ts:
        return True
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(ts))) > timedelta(days=days)
    except Exception:  # noqa: BLE001
        return True


def seed_ipo_cache(path: str | Path, seed_path: str | Path) -> int:
    """Populate the cache from the seed JSON for any ticker not already present.
    Seeded rows carry source='seed' and an approximate Jan-1 date; resolved later."""
    seed_path = Path(seed_path)
    if not seed_path.exists():
        return 0
    try:
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("ipo_seed.read_failed", error=repr(e))
        return 0
    rows = _rows_dict(load_ipo_cache(path))
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for year, tickers in (seed.items() if isinstance(seed, dict) else []):
        try:
            y = int(year)
        except (TypeError, ValueError):
            continue
        for t in tickers or []:
            tu = str(t).upper().strip()
            if not tu or tu in rows:
                continue
            rows[tu] = {
                "ticker": tu,
                "ipo_date": f"{y}-01-01",
                "ipo_year": y,
                "source": "seed",
                "resolved_at": now,
            }
            added += 1
    if added:
        _write(path, rows)
    log.info("ipo_cache.seeded", added=added)
    return added


def resolve_ipos(path: str | Path, tickers, profile_provider, max_new: int = 25) -> int:
    """Resolve up to ``max_new`` tickers that are unknown, seed-only, or stale-
    unresolved, using the provider's authoritative first-trade date. Bounded so a
    daily scan isn't slowed. Returns the number newly resolved to a real date."""
    rows = _rows_dict(load_ipo_cache(path))
    wanted = list(dict.fromkeys(str(t).upper() for t in tickers if t))
    todo = [
        t
        for t in wanted
        if t not in rows
        or rows[t].get("source") == "seed"
        or (rows[t].get("source") == "unresolved" and _is_stale(rows[t], _UNRESOLVED_RETRY_DAYS))
    ]
    todo = todo[: max(0, int(max_new))]
    if not todo:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    resolved = 0
    for t in todo:
        try:
            prof = profile_provider.get_company_profile(t)
        except Exception as e:  # noqa: BLE001
            log.debug("ipo_cache.profile_failed", ticker=t, error=repr(e))
            prof = None
        ipo_date = getattr(prof, "ipo_date", None)
        if not ipo_date:
            # Keep an existing seed/real row; only stamp a (retryable) marker for
            # genuinely unknown tickers so we don't refetch them every scan.
            if t not in rows or rows[t].get("source") == "unresolved":
                rows[t] = {"ticker": t, "ipo_date": None, "ipo_year": None, "source": "unresolved", "resolved_at": now}
            continue
        rows[t] = {
            "ticker": t,
            "ipo_date": ipo_date,
            "ipo_year": _year_of(ipo_date),
            "source": "yfinance",
            "resolved_at": now,
        }
        resolved += 1
    _write(path, rows)
    log.info("ipo_cache.resolved", attempted=len(todo), resolved=resolved)
    return resolved


def build_ipo_index(path: str | Path, universe: list[str] | None = None) -> dict[str, list[str]]:
    """Return ``{year: [tickers]}`` from the cache, optionally limited to a
    universe (intersection with the current run's symbols). Sorted by year."""
    df = load_ipo_cache(path)
    if df.empty:
        return {}
    uni = {str(t).upper() for t in universe} if universe else None
    out: dict[str, list[str]] = {}
    for r in df.to_dict("records"):
        y = r.get("ipo_year")
        t = str(r.get("ticker", "")).upper()
        if y is None or not t or (isinstance(y, float) and pd.isna(y)):
            continue
        if uni is not None and t not in uni:
            continue
        out.setdefault(str(int(y)), []).append(t)
    for y in out:
        out[y] = sorted(set(out[y]))
    return dict(sorted(out.items()))
