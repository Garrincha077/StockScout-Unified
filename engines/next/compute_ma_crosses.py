#!/usr/bin/env python3
"""Hydrate sortable moving-average cross fields from the canonical 5Y scan cache.

No additional market-data request is made. Daily 10/20 EMA values are calculated
from adjusted daily closes. Weekly 10/20 SMA values are calculated from the last
available adjusted close in each market week, including the current partial week.
"""
from __future__ import annotations

import json
import math
import pickle
from pathlib import Path
from typing import Any

import pandas as pd

PRICE_CACHE = Path("data/batch_results/price_history_5y.pkl")
DATASET = Path("frontend/public/data/latest.json")
MODEL = "ma-cross-v1-daily-ema10-20-weekly-sma10-20"

DAILY_FIELDS = (
    "ema10d",
    "ema20d",
    "ema10d20dSpreadPct",
    "ema10d20dState",
    "ema10d20dCross",
    "ema10d20dCrossAge",
)
WEEKLY_FIELDS = (
    "sma10w",
    "sma20w",
    "sma10w20wSpreadPct",
    "sma10w20wState",
    "sma10w20wCross",
    "sma10w20wCrossAge",
)
ALL_FIELDS = DAILY_FIELDS + WEEKLY_FIELDS


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rounded(value: Any, digits: int = 4) -> float | None:
    number = _finite(value)
    return round(number, digits) if number is not None else None


def close_series(frame: pd.DataFrame | None) -> pd.Series:
    if frame is None or frame.empty or "Close" not in frame:
        return pd.Series(dtype=float)
    close = pd.to_numeric(frame["Close"], errors="coerce").dropna().astype(float)
    if close.empty:
        return close
    idx = pd.to_datetime(close.index, errors="coerce", utc=True)
    valid = ~idx.isna()
    close = close.loc[valid].copy()
    close.index = idx[valid].tz_convert(None)
    close = close[~close.index.duplicated(keep="last")].sort_index()
    return close


def weekly_closes(close: pd.Series) -> pd.Series:
    """Return one close per market week using the last completed session available."""
    if close.empty:
        return pd.Series(dtype=float)
    return close.resample("W-FRI").last().dropna()


def cross_summary(fast: pd.Series, slow: pd.Series) -> dict[str, Any]:
    aligned = pd.concat([fast.rename("fast"), slow.rename("slow")], axis=1).dropna()
    if aligned.empty:
        return {"state": None, "cross": None, "age": None, "spreadPct": None}

    diff = aligned["fast"] - aligned["slow"]
    raw_state = diff.map(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    state = raw_state.replace(0, pd.NA).ffill().bfill()
    if state.isna().all():
        return {
            "state": "FLAT",
            "cross": None,
            "age": None,
            "spreadPct": 0.0,
        }

    current = int(state.iloc[-1])
    changes = state.ne(state.shift())
    if len(changes):
        changes.iloc[0] = False
    positions = [i for i, changed in enumerate(changes.tolist()) if bool(changed)]
    last_cross_pos = positions[-1] if positions else None
    last_cross = None
    age = None
    if last_cross_pos is not None:
        last_cross = "BULL" if int(state.iloc[last_cross_pos]) > 0 else "BEAR"
        age = len(aligned) - 1 - last_cross_pos

    fast_last = _finite(aligned["fast"].iloc[-1])
    slow_last = _finite(aligned["slow"].iloc[-1])
    spread_pct = None
    if fast_last is not None and slow_last not in (None, 0.0):
        spread_pct = (fast_last / slow_last - 1.0) * 100.0

    return {
        "state": "BULL" if current > 0 else "BEAR",
        "cross": last_cross,
        "age": age,
        "spreadPct": _rounded(spread_pct),
    }


def compute_metrics(frame: pd.DataFrame | None) -> dict[str, Any]:
    metrics = {field: None for field in ALL_FIELDS}
    close = close_series(frame)
    if len(close) >= 20:
        ema10 = close.ewm(span=10, adjust=False, min_periods=10).mean()
        ema20 = close.ewm(span=20, adjust=False, min_periods=20).mean()
        daily = cross_summary(ema10, ema20)
        metrics.update(
            ema10d=_rounded(ema10.iloc[-1]),
            ema20d=_rounded(ema20.iloc[-1]),
            ema10d20dSpreadPct=daily["spreadPct"],
            ema10d20dState=daily["state"],
            ema10d20dCross=daily["cross"],
            ema10d20dCrossAge=daily["age"],
        )

    weekly = weekly_closes(close)
    if len(weekly) >= 20:
        sma10 = weekly.rolling(10, min_periods=10).mean()
        sma20 = weekly.rolling(20, min_periods=20).mean()
        wk = cross_summary(sma10, sma20)
        metrics.update(
            sma10w=_rounded(sma10.iloc[-1]),
            sma20w=_rounded(sma20.iloc[-1]),
            sma10w20wSpreadPct=wk["spreadPct"],
            sma10w20wState=wk["state"],
            sma10w20wCross=wk["cross"],
            sma10w20wCrossAge=wk["age"],
        )
    return metrics


def hydrate_dataset(
    dataset_path: Path = DATASET,
    price_cache_path: Path = PRICE_CACHE,
) -> dict[str, Any]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Missing canonical dataset: {dataset_path}")
    if not price_cache_path.exists():
        raise FileNotFoundError(f"Missing canonical 5Y price cache: {price_cache_path}")

    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    with price_cache_path.open("rb") as fh:
        history: dict[str, pd.DataFrame] = pickle.load(fh)

    daily_eligible = daily_complete = 0
    weekly_eligible = weekly_complete = 0
    missing_history: list[str] = []

    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        frame = history.get(ticker)
        close = close_series(frame)
        weekly = weekly_closes(close)
        if frame is None or close.empty:
            missing_history.append(ticker)

        if len(close) >= 20:
            daily_eligible += 1
        if len(weekly) >= 20:
            weekly_eligible += 1

        metrics = compute_metrics(frame)
        row.update(metrics)
        if metrics["ema10d20dSpreadPct"] is not None:
            daily_complete += 1
        if metrics["sma10w20wSpreadPct"] is not None:
            weekly_complete += 1

    market = payload.setdefault("market", {})
    market["maCrossCoverage"] = {
        "model": MODEL,
        "daily": {
            "eligible": daily_eligible,
            "complete": daily_complete,
            "coveragePct": round(100.0 * daily_complete / daily_eligible, 2) if daily_eligible else 0.0,
        },
        "weekly": {
            "eligible": weekly_eligible,
            "complete": weekly_complete,
            "coveragePct": round(100.0 * weekly_complete / weekly_eligible, 2) if weekly_eligible else 0.0,
        },
        "missingHistory": len(missing_history),
    }
    payload["maCrossModel"] = MODEL

    tmp = dataset_path.with_suffix(dataset_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(dataset_path)

    print(
        "MA cross hydration: "
        f"daily {daily_complete}/{daily_eligible} eligible, "
        f"weekly {weekly_complete}/{weekly_eligible} eligible, "
        f"missing history {len(missing_history)}"
    )
    return payload


def main() -> None:
    hydrate_dataset()


if __name__ == "__main__":
    main()
