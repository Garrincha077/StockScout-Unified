#!/usr/bin/env python3
"""Hydrate 5Y chart shards from an already-produced frontend scan dataset.

This never re-runs the stock screening engine. It reads frontend/public/data/latest.json,
downloads only price history for tickers already present in that dataset, enriches a few
chart-derived fields, and writes lazy-loaded static chart shards for GitHub Pages.
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


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def pct_change(series: pd.Series, periods: int) -> float:
    s = series.dropna()
    if len(s) <= periods:
        return 0.0
    base = finite(s.iloc[-periods - 1])
    last = finite(s.iloc[-1])
    return (last / base - 1.0) * 100.0 if base else 0.0


def shard_for(ticker: str) -> str:
    value = sum((idx + 1) * ord(ch) for idx, ch in enumerate(ticker.upper())) % SHARD_COUNT
    return f"{value:03d}.json"


def extract_ticker_frame(download: pd.DataFrame, ticker: str, chunk_size: int) -> pd.DataFrame:
    if download.empty:
        return pd.DataFrame()
    if chunk_size == 1 and not isinstance(download.columns, pd.MultiIndex):
        return download
    if isinstance(download.columns, pd.MultiIndex):
        level0 = download.columns.get_level_values(0)
        level1 = download.columns.get_level_values(1)
        if ticker in level0:
            return download[ticker]
        if ticker in level1:
            return download.xs(ticker, axis=1, level=1)
    return pd.DataFrame()


def normalize_index(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    frame.index = idx
    return frame


def compact_bars(frame: pd.DataFrame, spy_close: pd.Series) -> tuple[list[list], pd.Series]:
    if frame.empty or "Close" not in frame:
        return [], pd.Series(dtype=float)
    frame = normalize_index(frame.dropna(subset=["Close"]))
    if frame.empty:
        return [], pd.Series(dtype=float)
    spy = spy_close.copy()
    if isinstance(spy.index, pd.DatetimeIndex) and spy.index.tz is not None:
        spy.index = spy.index.tz_localize(None)
    spy_aligned = spy.reindex(frame.index, method="ffill")
    close = frame["Close"].astype(float)
    rs = close / spy_aligned.replace(0, float("nan")) * 100.0
    rows = []
    for ts, row in frame.tail(1265).iterrows():
        c = finite(row.get("Close"))
        spy_value = finite(spy_aligned.get(ts))
        rs_value = c / spy_value * 100.0 if spy_value else 0.0
        rows.append([
            ts.strftime("%Y-%m-%d"),
            round(finite(row.get("Open")), 4),
            round(finite(row.get("High")), 4),
            round(finite(row.get("Low")), 4),
            round(c, 4),
            int(finite(row.get("Volume"))),
            round(finite(rs_value), 6),
        ])
    return rows, rs.dropna()


def assign_rs_ranks(universe: list[dict]) -> None:
    ranked = [(finite(row.get("rsComposite")), idx) for idx, row in enumerate(universe)]
    ranked.sort(key=lambda x: x[0])
    n = len(ranked)
    if not n:
        return
    for pos, (_, idx) in enumerate(ranked):
        rank = 99 if n == 1 else 1 + round(pos / (n - 1) * 98)
        universe[idx]["rsRank"] = int(max(1, min(99, rank)))


def main() -> None:
    if not DATA.exists():
        print("No latest.json yet; chart hydration skipped")
        return

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    universe = payload.get("universe") or []
    tickers = [str(row.get("ticker", "")).upper() for row in universe if row.get("ticker")]
    if not tickers:
        raise SystemExit("latest.json contains no tickers")

    row_by_ticker = {str(row["ticker"]).upper(): row for row in universe}
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    for old in CHART_DIR.glob("*.json"):
        old.unlink()

    print(f"Hydrating 5Y charts for {len(tickers):,} scan tickers")
    spy = yf.download("SPY", period="5y", interval="1d", auto_adjust=False, progress=False, threads=False)
    if isinstance(spy.columns, pd.MultiIndex):
        if "SPY" in spy.columns.get_level_values(0):
            spy = spy["SPY"]
        elif "SPY" in spy.columns.get_level_values(1):
            spy = spy.xs("SPY", axis=1, level=1)
    if "Close" not in spy:
        raise SystemExit("Unable to download SPY benchmark history")
    spy_close = spy["Close"].dropna()

    shards: dict[str, dict[str, list[list]]] = {f"{i:03d}.json": {} for i in range(SHARD_COUNT)}
    mapping: dict[str, str] = {}
    chunk_size = 100

    for start in range(0, len(tickers), chunk_size):
        chunk = tickers[start:start + chunk_size]
        print(f"  {start + 1:,}-{min(start + len(chunk), len(tickers)):,}/{len(tickers):,}")
        try:
            download = yf.download(
                chunk,
                period="5y",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            print(f"  batch failed: {exc}")
            time.sleep(2)
            continue

        for ticker in chunk:
            frame = extract_ticker_frame(download, ticker, len(chunk))
            bars, rs = compact_bars(frame, spy_close)
            if not bars:
                continue
            shard = shard_for(ticker)
            shards[shard][ticker] = bars
            mapping[ticker] = shard

            row = row_by_ticker[ticker]
            frame = normalize_index(frame.dropna(subset=["Close"]))
            close = frame["Close"].astype(float)
            volume = frame["Volume"].astype(float) if "Volume" in frame else pd.Series(dtype=float)
            row["return1y"] = round(pct_change(close, min(252, max(1, len(close) - 2))), 2)
            row["rs3m"] = round(pct_change(rs, min(63, max(1, len(rs) - 2))), 2) if len(rs) > 2 else 0.0
            row["rs6m"] = round(pct_change(rs, min(126, max(1, len(rs) - 2))), 2) if len(rs) > 2 else 0.0
            row["rs12m"] = round(pct_change(rs, min(252, max(1, len(rs) - 2))), 2) if len(rs) > 2 else 0.0
            row["rsComposite"] = round(row["rs3m"] * 0.2 + row["rs6m"] * 0.3 + row["rs12m"] * 0.5, 3)
            if not volume.empty:
                avg_vol = finite(volume.tail(20).mean())
                avg_price = finite(close.tail(20).mean())
                row["avgVolume20"] = int(avg_vol)
                row["avgDollarVolume20"] = round(avg_vol * avg_price, 2)

    for name, data in shards.items():
        if data:
            (CHART_DIR / name).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    assign_rs_ranks(universe)
    market = payload.setdefault("market", {})
    market["fiveYearCharts"] = len(mapping)
    market["rs90Plus"] = sum(1 for row in universe if int(row.get("rsRank", 0) or 0) >= 90)
    payload["chartShards"] = mapping
    payload["chartShardCount"] = len([p for p in CHART_DIR.glob("*.json")])
    payload["version"] = max(3, int(payload.get("version", 1) or 1))
    payload.pop("charts", None)
    DATA.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    size_mb = sum(p.stat().st_size for p in CHART_DIR.glob("*.json")) / 1024 / 1024
    print(f"Hydrated {len(mapping):,}/{len(tickers):,} tickers into {payload['chartShardCount']} shards ({size_mb:.1f} MB)")
    print(f"Updated dataset: {DATA.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
