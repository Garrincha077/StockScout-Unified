#!/usr/bin/env python3
"""Frontend export that reuses the fast scanner's canonical adjusted 5Y cache.

The fast scanner downloads adjusted OHLCV (``auto_adjust=True``). This exporter
keeps every chart path on that same convention, including rare Yahoo fallbacks,
and normalizes the upstream VCP key before the StockScout row scorer sees it.
"""
from __future__ import annotations

import json
import pickle
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

import export_frontend_data as base

PRICE_CACHE = Path("data/batch_results/price_history_5y.pkl")


def normalize_vcp_data(value: dict | None) -> dict:
    """Expose upstream ``vcp_quality`` under the scorer's historical alias.

    The source detector returns ``vcp_quality``. Older frontend code expected
    ``quality``. Preserve both without mutating the scan analysis object.
    """
    vcp = dict(value or {})
    if "quality" not in vcp and "vcp_quality" in vcp:
        vcp["quality"] = vcp.get("vcp_quality")
    return vcp


def score_row_with_canonical_vcp(analysis: dict) -> dict:
    fixed = dict(analysis)
    fixed["vcp_data"] = normalize_vcp_data(analysis.get("vcp_data"))
    return base._ORIGINAL_SCORE_ROW(fixed)


def download_adjusted_batch(chunk: list[str], spy_close: pd.Series, threads: bool = True) -> dict[str, list[list]]:
    """Download fallback charts using the same adjusted-price convention as the scan."""
    if not chunk:
        return {}
    try:
        download = yf.download(
            chunk,
            period="5y",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=threads,
        )
    except Exception as exc:
        print(f"  adjusted chart batch failed: {exc}")
        return {}

    result: dict[str, list[list]] = {}
    for ticker in chunk:
        frame = base.extract_ticker_frame(download, ticker, len(chunk))
        bars = base.compact_bars(frame, spy_close)
        if bars:
            result[ticker] = bars
    return result


def build_adjusted_five_year_chart_shards(tickers: list[str]) -> dict[str, str]:
    """Recovery path when the scanner cache is unavailable; never mix raw/adjusted prices."""
    base.CHART_DIR.mkdir(parents=True, exist_ok=True)
    for old in base.CHART_DIR.glob("*.json"):
        old.unlink()

    print(f"Fetching adjusted 5Y chart history for {len(tickers):,} analyzed tickers")
    spy_df = yf.download("SPY", period="5y", interval="1d", auto_adjust=True, progress=False, threads=False)
    if isinstance(spy_df.columns, pd.MultiIndex):
        if "SPY" in spy_df.columns.get_level_values(0):
            spy_df = spy_df["SPY"]
        elif "SPY" in spy_df.columns.get_level_values(1):
            spy_df = spy_df.xs("SPY", axis=1, level=1)
    spy_close = spy_df["Close"].astype(float).dropna() if "Close" in spy_df else pd.Series(dtype=float)
    if spy_close.empty:
        raise RuntimeError("Unable to fetch adjusted SPY history for chart recovery")

    shards: dict[str, dict[str, list[list]]] = {f"{i:03d}.json": {} for i in range(base.SHARD_COUNT)}
    mapping: dict[str, str] = {}
    missing: list[str] = []
    for start in range(0, len(tickers), 100):
        chunk = tickers[start:start + 100]
        batch = download_adjusted_batch(chunk, spy_close, threads=True)
        for ticker in chunk:
            bars = batch.get(ticker)
            if not bars:
                missing.append(ticker)
                continue
            shard = base.shard_for(ticker)
            shards[shard][ticker] = bars
            mapping[ticker] = shard

    if missing:
        retry_missing: list[str] = []
        for start in range(0, len(missing), 20):
            chunk = missing[start:start + 20]
            batch = download_adjusted_batch(chunk, spy_close, threads=False)
            for ticker in chunk:
                bars = batch.get(ticker)
                if not bars:
                    retry_missing.append(ticker)
                    continue
                shard = base.shard_for(ticker)
                shards[shard][ticker] = bars
                mapping[ticker] = shard
            time.sleep(0.20)
        if retry_missing:
            print(f"Adjusted charts still unavailable after retry: {len(retry_missing):,}")

    for name, payload in shards.items():
        if payload:
            (base.CHART_DIR / name).write_text(
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
            )
    return mapping


def build_from_scan_cache(tickers: list[str]) -> dict[str, str]:
    if not PRICE_CACHE.exists():
        print("Reusable 5Y price cache not found; using adjusted Yahoo chart recovery")
        return build_adjusted_five_year_chart_shards(tickers)

    try:
        with PRICE_CACHE.open("rb") as fh:
            price_history: dict[str, pd.DataFrame] = pickle.load(fh)
    except Exception as exc:
        print(f"Unable to load reusable 5Y price cache ({exc}); using adjusted Yahoo recovery")
        return build_adjusted_five_year_chart_shards(tickers)

    spy = price_history.get("SPY")
    if spy is None or spy.empty or "Close" not in spy:
        print("SPY missing from reusable cache; using adjusted Yahoo chart recovery")
        return build_adjusted_five_year_chart_shards(tickers)

    base.CHART_DIR.mkdir(parents=True, exist_ok=True)
    for old in base.CHART_DIR.glob("*.json"):
        old.unlink()

    spy_close = spy["Close"].astype(float).dropna()
    shards: dict[str, dict[str, list[list]]] = {f"{i:03d}.json": {} for i in range(base.SHARD_COUNT)}
    mapping: dict[str, str] = {}
    missing: list[str] = []

    for ticker in tickers:
        frame = price_history.get(ticker)
        if frame is None or frame.empty:
            missing.append(ticker)
            continue
        bars = base.compact_bars(frame, spy_close)
        if not bars:
            missing.append(ticker)
            continue
        shard = base.shard_for(ticker)
        shards[shard][ticker] = bars
        mapping[ticker] = shard

    # Rare fallback for Yahoo omissions. Keep the fast scan's adjusted convention.
    if missing:
        print(f"Fetching only {len(missing):,} missing adjusted chart histories from Yahoo")
        still_missing: list[str] = []
        for start in range(0, len(missing), 20):
            chunk = missing[start:start + 20]
            batch = download_adjusted_batch(chunk, spy_close, threads=True)
            for ticker in chunk:
                bars = batch.get(ticker)
                if not bars:
                    still_missing.append(ticker)
                    continue
                shard = base.shard_for(ticker)
                shards[shard][ticker] = bars
                mapping[ticker] = shard
            time.sleep(0.15)
        if still_missing:
            print(f"Charts unavailable after targeted adjusted fallback: {len(still_missing):,}")

    for name, payload in shards.items():
        if payload:
            (base.CHART_DIR / name).write_text(
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
            )

    size_mb = sum(p.stat().st_size for p in base.CHART_DIR.glob("*.json")) / 1024 / 1024
    print(
        f"Reused canonical adjusted scan cache for {len(mapping):,}/{len(tickers):,} frontend charts "
        f"({size_mb:.1f} MB across {len([p for p in base.CHART_DIR.glob('*.json')])} shards)"
    )
    return mapping


if not hasattr(base, "_ORIGINAL_SCORE_ROW"):
    base._ORIGINAL_SCORE_ROW = base.score_row
base.score_row = score_row_with_canonical_vcp
base.build_five_year_chart_shards = build_from_scan_cache

if __name__ == "__main__":
    base.main()
    from compute_ma_crosses import hydrate_dataset
    hydrate_dataset()
