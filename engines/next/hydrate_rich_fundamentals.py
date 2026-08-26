#!/usr/bin/env python3
"""Hydrate fundamental cache for the entire analyzed post-market universe.

The original StockScout/LEGACY fundamental fetcher is deliberately frozen. This
post-market pass reuses that exact fetcher and augments its cache with additive
cash-flow/balance-sheet evidence from the same yfinance source via a separate
module. The additive layer never participates in LEGACY scoring.

Policy:
- inspect every ticker present in batch_progress.pkl analyses;
- reuse a cache file while it is <= MAX_AGE_DAYS old;
- bootstrap missing additive fields into a fresh cache without resetting the
  original cache ``fetched_at`` timestamp;
- when FORCE_FULL_REFRESH=true, bypass cache age for every analyzed ticker;
- on a stale/missing cache, run the frozen original fetch first, then additive
  enrichment and save the merged result once;
- never delete or overwrite a good old cache when the original refresh fails;
- rate-limit fresh requests conservatively.
"""
from __future__ import annotations

import json
import os
import pickle
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.data.extended_fundamentals_fetcher import (
    MODEL as EXTENDED_MODEL,
    fetch_extended_fundamentals,
)
from src.data.fundamentals_fetcher import fetch_quarterly_financials
from src.data.git_storage_fetcher import GitStorageFetcher

ROOT = Path(__file__).resolve().parent
PROGRESS = ROOT / "data" / "batch_results" / "batch_progress.pkl"
FUND_DIR = ROOT / "data" / "fundamentals_cache"
MAX_AGE_DAYS = max(1, int(os.getenv("RICH_FUNDAMENTALS_MAX_AGE_DAYS", "7")))
REQUEST_DELAY = max(0.0, float(os.getenv("RICH_FUNDAMENTALS_REQUEST_DELAY", "0.35")))
FORCE_FULL_REFRESH = os.getenv("FORCE_FULL_REFRESH", "false").strip().lower() in {"1", "true", "yes", "on"}


def read_cache_wrapper(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def cache_age_days(path: Path) -> int | None:
    wrapper = read_cache_wrapper(path)
    stamp = wrapper.get("fetched_at")
    if not stamp:
        return None
    try:
        return max(0, (datetime.now() - datetime.fromisoformat(stamp)).days)
    except Exception:
        return None


def cache_has_extended_model(path: Path) -> bool:
    wrapper = read_cache_wrapper(path)
    data = wrapper.get("data") if isinstance(wrapper.get("data"), dict) else {}
    return data.get("extended_fundamentals_model") == EXTENDED_MODEL


def save_cache(fetcher: GitStorageFetcher, ticker: str, data: dict[str, Any]) -> None:
    path = FUND_DIR / f"{ticker}_fundamentals.json"
    now = datetime.now().isoformat()
    payload = {
        "data": fetcher._clean_for_json(data),
        "fetched_at": now,
    }
    if data.get("extended_fundamentals_model") == EXTENDED_MODEL:
        payload["extended_fetched_at"] = now
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    fetcher._update_metadata(ticker)


def merge_extended_into_cache(
    fetcher: GitStorageFetcher,
    path: Path,
    extended: dict[str, Any],
) -> bool:
    """Merge additive evidence while preserving the legacy cache age/provenance."""
    if not extended or extended.get("extended_fundamentals_model") != EXTENDED_MODEL:
        return False
    wrapper = read_cache_wrapper(path)
    existing = wrapper.get("data") if isinstance(wrapper.get("data"), dict) else {}
    if not existing:
        return False

    merged = dict(existing)
    merged.update(extended)
    wrapper["data"] = fetcher._clean_for_json(merged)
    # Critical: do not change fetched_at; that timestamp describes the original
    # revenue/EPS fundamental refresh and is used by freshness confidence.
    wrapper["extended_fetched_at"] = datetime.now().isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(wrapper, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return True


def main() -> None:
    if not PROGRESS.exists():
        raise SystemExit(f"Missing completed scan progress: {PROGRESS}")

    with PROGRESS.open("rb") as fh:
        progress = pickle.load(fh)
    analyses = progress.get("results") or progress.get("analyses") or []
    tickers = sorted({str(a.get("ticker", "")).upper() for a in analyses if a.get("ticker")})
    if not tickers:
        raise SystemExit("No analyzed tickers found in batch_progress.pkl")

    FUND_DIR.mkdir(parents=True, exist_ok=True)
    fetcher = GitStorageFetcher(str(FUND_DIR))
    reused = refreshed = missing = failed = 0
    extended_bootstrap = extended_added = extended_failed = 0

    mode = "FORCED FULL REFRESH" if FORCE_FULL_REFRESH else f"max cache age={MAX_AGE_DAYS}d"
    print(
        f"Rich fundamentals hydration: {len(tickers):,} analyzed tickers; {mode}; "
        f"additive model={EXTENDED_MODEL}"
    )

    for idx, ticker in enumerate(tickers, 1):
        path = FUND_DIR / f"{ticker}_fundamentals.json"
        age = cache_age_days(path)
        cache_is_fresh = age is not None and age <= MAX_AGE_DAYS

        if cache_is_fresh and not FORCE_FULL_REFRESH:
            reused += 1
            if not cache_has_extended_model(path):
                # One-time migration for old but still fresh caches. Only the new
                # additive module is called; the legacy cache timestamp stays put.
                extended_bootstrap += 1
                try:
                    extended = fetch_extended_fundamentals(ticker)
                    if merge_extended_into_cache(fetcher, path, extended):
                        extended_added += 1
                    else:
                        extended_failed += 1
                        print(f"  {ticker}: additive fundamentals unavailable; legacy cache unchanged")
                except Exception as exc:
                    extended_failed += 1
                    print(f"  {ticker}: additive bootstrap failed ({type(exc).__name__}: {exc})")
                time.sleep(REQUEST_DELAY)
        else:
            if path.exists():
                refreshed += 1
            else:
                missing += 1
            try:
                # Frozen upstream/LEGACY source path. Do not modify its behavior.
                legacy_data = fetch_quarterly_financials(ticker)
                if legacy_data:
                    merged = dict(legacy_data)
                    try:
                        extended = fetch_extended_fundamentals(ticker)
                        if extended and extended.get("extended_fundamentals_model") == EXTENDED_MODEL:
                            merged.update(extended)
                            extended_added += 1
                        else:
                            extended_failed += 1
                    except Exception as exc:
                        extended_failed += 1
                        print(f"  {ticker}: additive refresh failed ({type(exc).__name__}: {exc}); saving legacy data")
                    save_cache(fetcher, ticker, merged)
                else:
                    failed += 1
                    print(f"  {ticker}: no legacy fundamental data returned; preserving old cache if present")
            except Exception as exc:
                failed += 1
                print(f"  {ticker}: refresh failed ({type(exc).__name__}: {exc}); preserving old cache")
            time.sleep(REQUEST_DELAY)

        if idx % 250 == 0 or idx == len(tickers):
            print(
                f"  {idx:,}/{len(tickers):,}: reused={reused:,}, "
                f"refresh_candidates={refreshed:,}, missing_candidates={missing:,}, failures={failed:,}, "
                f"extended_bootstrap={extended_bootstrap:,}, extended_added={extended_added:,}, "
                f"extended_failures={extended_failed:,}"
            )

    available = sum(1 for ticker in tickers if (FUND_DIR / f"{ticker}_fundamentals.json").exists())
    extended_available = sum(
        1 for ticker in tickers
        if cache_has_extended_model(FUND_DIR / f"{ticker}_fundamentals.json")
    )
    print(
        f"Rich fundamentals ready: {available:,}/{len(tickers):,} cached; "
        f"extended={extended_available:,}/{len(tickers):,}; reused={reused:,}; "
        f"refreshed candidates={refreshed:,}; new candidates={missing:,}; "
        f"refresh failures={failed:,}; extended failures={extended_failed:,}"
    )


if __name__ == "__main__":
    main()
