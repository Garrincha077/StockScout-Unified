#!/usr/bin/env python3
"""Build frontend chart shards without mutating the canonical scan dataset.

Frontend-only deployments must be presentation-only. They may download adjusted
OHLCV to rebuild static chart shards that are not committed to Git, but they must
never rewrite ``latest.json`` or recalculate scan results. Chart history is
anchored to the canonical payload's ``generatedAt`` timestamp, so a later code
redeploy cannot silently add newer price bars to an older scan snapshot.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "frontend" / "public" / "data" / "latest.json"
CHART_DIR = ROOT / "frontend" / "public" / "data" / "charts"
SHARD_COUNT = 128
MIN_COVERAGE = 0.95


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def snapshot_window(generated_at: str) -> tuple[pd.Timestamp, str, str]:
    if not generated_at:
        raise SystemExit("Canonical dataset has no generatedAt timestamp")
    try:
        stamp = pd.Timestamp(generated_at)
    except Exception as exc:
        raise SystemExit(f"Invalid canonical generatedAt timestamp: {generated_at!r} ({exc})") from exc
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    cutoff = stamp.normalize()
    start = (cutoff - pd.DateOffset(years=5) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    # yfinance treats end as exclusive, so include the completed cutoff session.
    end = (cutoff + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return cutoff, start, end


def shard_for(ticker: str) -> str:
    value = sum((idx + 1) * ord(ch) for idx, ch in enumerate(ticker.upper())) % SHARD_COUNT
    return f"{value:03d}.json"


def extract_ticker_frame(download: pd.DataFrame, ticker: str, chunk_size: int) -> pd.DataFrame:
    if download is None or download.empty:
        return pd.DataFrame()
    if chunk_size == 1 and not isinstance(download.columns, pd.MultiIndex):
        frame = download
    elif isinstance(download.columns, pd.MultiIndex):
        level0 = download.columns.get_level_values(0)
        level1 = download.columns.get_level_values(1)
        if ticker in level0:
            frame = download[ticker]
        elif ticker in level1:
            frame = download.xs(ticker, axis=1, level=1)
        else:
            return pd.DataFrame()
    else:
        return pd.DataFrame()
    frame = frame.dropna(subset=["Close"]).copy() if "Close" in frame else pd.DataFrame()
    if frame.empty:
        return frame
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    frame.index = idx
    return frame.sort_index()


def compact_bars(frame: pd.DataFrame, spy_close: pd.Series, cutoff: pd.Timestamp) -> list[list]:
    if frame.empty or "Close" not in frame:
        return []
    frame = frame.loc[pd.DatetimeIndex(frame.index) <= cutoff].copy()
    if frame.empty:
        return []
    spy = spy_close.copy()
    if isinstance(spy.index, pd.DatetimeIndex) and spy.index.tz is not None:
        spy.index = spy.index.tz_localize(None)
    spy = spy.loc[pd.DatetimeIndex(spy.index) <= cutoff]
    spy_aligned = spy.reindex(frame.index, method="ffill")
    rows: list[list] = []
    for ts, row in frame.tail(1265).iterrows():
        close = finite(row.get("Close"))
        spy_value = finite(spy_aligned.get(ts))
        rs = close / spy_value * 100.0 if spy_value > 0 else 0.0
        rows.append([
            ts.strftime("%Y-%m-%d"),
            round(finite(row.get("Open")), 3),
            round(finite(row.get("High")), 3),
            round(finite(row.get("Low")), 3),
            round(close, 3),
            int(finite(row.get("Volume"))),
            round(rs, 4),
        ])
    return rows


def download_chunk(
    chunk: list[str],
    spy_close: pd.Series,
    cutoff: pd.Timestamp,
    start_date: str,
    end_date: str,
    threads: bool,
) -> dict[str, list[list]]:
    if not chunk:
        return {}
    try:
        raw = yf.download(
            chunk,
            start=start_date,
            end=end_date,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=threads,
            timeout=30,
        )
    except Exception as exc:
        print(f"Chart batch failed ({len(chunk)} symbols): {exc}", flush=True)
        return {}
    out: dict[str, list[list]] = {}
    for ticker in chunk:
        frame = extract_ticker_frame(raw, ticker, len(chunk))
        bars = compact_bars(frame, spy_close, cutoff)
        if bars:
            out[ticker] = bars
    return out


def main() -> None:
    if not DATA.exists():
        raise SystemExit(f"Canonical dataset missing: {DATA}")
    before = DATA.read_bytes()
    payload = json.loads(before)
    tickers = [str(row.get("ticker", "")).upper() for row in payload.get("universe", []) if row.get("ticker")]
    if not tickers:
        raise SystemExit("Canonical dataset has no universe")
    cutoff, start_date, end_date = snapshot_window(str(payload.get("generatedAt") or ""))

    existing_mapping = payload.get("chartShards") or {}
    for ticker, shard in existing_mapping.items():
        expected = shard_for(str(ticker))
        if shard != expected:
            raise SystemExit(f"Canonical chart mapping mismatch for {ticker}: {shard} != {expected}")

    print(f"Chart hydration snapshot {cutoff.date()}: {len(tickers):,} symbols", flush=True)
    print("Chart hydration benchmark: SPY", flush=True)
    spy = yf.download(
        "SPY",
        start=start_date,
        end=end_date,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=30,
    )
    if isinstance(spy.columns, pd.MultiIndex):
        if "SPY" in spy.columns.get_level_values(0):
            spy = spy["SPY"]
        elif "SPY" in spy.columns.get_level_values(1):
            spy = spy.xs("SPY", axis=1, level=1)
    if spy.empty or "Close" not in spy:
        raise SystemExit("Unable to download adjusted SPY history")
    spy.index = pd.DatetimeIndex(spy.index).tz_localize(None) if pd.DatetimeIndex(spy.index).tz is not None else pd.DatetimeIndex(spy.index)
    spy = spy.loc[pd.DatetimeIndex(spy.index) <= cutoff]
    spy_close = spy["Close"].astype(float).dropna()

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    for old in CHART_DIR.glob("*.json"):
        old.unlink()
    shards: dict[str, dict[str, list[list]]] = {f"{i:03d}.json": {} for i in range(SHARD_COUNT)}
    missing: list[str] = []

    batch_size = 100
    total_batches = math.ceil(len(tickers) / batch_size)
    for batch_index, start in enumerate(range(0, len(tickers), batch_size), start=1):
        chunk = tickers[start:start + batch_size]
        print(f"Chart hydration batch {batch_index}/{total_batches}: {len(chunk)} symbols", flush=True)
        batch = download_chunk(chunk, spy_close, cutoff, start_date, end_date, threads=True)
        for ticker in chunk:
            bars = batch.get(ticker)
            if bars:
                shards[shard_for(ticker)][ticker] = bars
            else:
                missing.append(ticker)
        print(f"Chart hydration batch {batch_index}/{total_batches} complete: {len(batch)}/{len(chunk)} covered", flush=True)

    if missing:
        still_missing: list[str] = []
        retry_size = 20
        retry_batches = math.ceil(len(missing) / retry_size)
        print(f"Chart hydration retry lane: {len(missing)} symbols in {retry_batches} batches", flush=True)
        for batch_index, start in enumerate(range(0, len(missing), retry_size), start=1):
            chunk = missing[start:start + retry_size]
            print(f"Chart hydration retry {batch_index}/{retry_batches}: {len(chunk)} symbols", flush=True)
            batch = download_chunk(chunk, spy_close, cutoff, start_date, end_date, threads=False)
            for ticker in chunk:
                bars = batch.get(ticker)
                if bars:
                    shards[shard_for(ticker)][ticker] = bars
                else:
                    still_missing.append(ticker)
            print(f"Chart hydration retry {batch_index}/{retry_batches} complete: {len(batch)}/{len(chunk)} covered", flush=True)
            time.sleep(0.20)
        missing = still_missing

    written = 0
    covered = 0
    for name, data in shards.items():
        if not data:
            continue
        covered += len(data)
        (CHART_DIR / name).write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        written += 1

    coverage = covered / len(tickers)
    if coverage < MIN_COVERAGE:
        raise SystemExit(f"Chart coverage too low: {covered}/{len(tickers)} ({coverage:.1%})")
    if DATA.read_bytes() != before:
        raise SystemExit("Invariant violation: read-only chart hydration modified latest.json")

    size_mb = sum(p.stat().st_size for p in CHART_DIR.glob("*.json")) / 1024 / 1024
    print(
        f"Read-only adjusted chart hydration at {cutoff.date()}: "
        f"{covered:,}/{len(tickers):,}, {written} shards, {size_mb:.1f} MB",
        flush=True,
    )
    if missing:
        print(f"Charts unavailable after retry: {len(missing):,}", flush=True)


if __name__ == "__main__":
    main()
