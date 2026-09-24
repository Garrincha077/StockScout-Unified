"""Hourly Trend Birth watcher over the quality-screened Kell universe.

The EOD Trend Birth project remains the source of candidate membership and
dashboard snapshots. This module only refreshes the daily bar intraday, applies
the same transparent 0/4 -> 4/4 rules, persists lightweight hysteresis state,
and hands sparse stage-change messages to Unified's existing Telegram ledger.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import pandas as pd
import yfinance as yf

from stock_scout.notifications.telegram import _escape_md_v2, split_telegram_message
from stockscout_unified.gridview_notification import (
    DEFAULT_GRID_URL,
    fetch_bytes,
    verify_publication,
)
from stockscout_unified.notifications import deliver_series

RULESET = "trend-birth-radar-v1"
DEFAULT_STATE = ".state/trend-birth-hourly.json"
MAX_READY = 5

CHECK_LABELS = (
    ("sma50Rising", "SMA50 rising"),
    ("sma30wRising", "SMA30W rising"),
    ("structureValid", "Structure valid"),
    ("notExtended", "Not extended"),
    ("recentPullbackCompression", "Recent pullback + EMA compression"),
    ("ema10Rising", "EMA10 rising"),
    ("ema20NonFalling", "EMA20 non-falling"),
    ("ema10AboveEma20", "EMA10 > EMA20"),
    ("closeAboveShortEmas", "Close > EMA10 & EMA20"),
    ("bullishReexpansion", "Bullish EMA re-expansion"),
)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_bars(rows: Iterable[Any]) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, dict):
            source = row
        elif isinstance(row, (list, tuple)) and len(row) >= 5:
            source = {
                "time": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5] if len(row) > 5 else 0,
            }
        else:
            continue
        open_ = _finite(source.get("open"))
        high = _finite(source.get("high"))
        low = _finite(source.get("low"))
        close = _finite(source.get("close"))
        if None in (open_, high, low, close):
            continue
        bars.append(
            {
                "time": str(source.get("time") or source.get("date") or ""),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": _finite(source.get("volume")) or 0.0,
            }
        )
    return bars


def _ema(values: list[float], length: int) -> list[float]:
    alpha = 2.0 / (length + 1.0)
    output: list[float] = []
    for value in values:
        output.append(value if not output else alpha * value + (1.0 - alpha) * output[-1])
    return output


def _sma(values: list[float], length: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= length:
            total -= values[index - length]
        if index >= length - 1:
            output[index] = total / length
    return output


def _wilder_atr(bars: list[dict[str, Any]], length: int = 14) -> list[float | None]:
    ranges: list[float] = []
    for index, bar in enumerate(bars):
        previous = bars[index - 1]["close"] if index else bar["close"]
        ranges.append(
            max(
                bar["high"] - bar["low"],
                abs(bar["high"] - previous),
                abs(bar["low"] - previous),
            )
        )
    output: list[float | None] = [None] * len(ranges)
    current: float | None = None
    for index, value in enumerate(ranges):
        current = value if current is None else ((length - 1) * current + value) / length
        if index >= length - 1:
            output[index] = current
    return output


def _parse_day(value: str):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _completed_weekly_closes(bars: list[dict[str, Any]]) -> list[float]:
    weeks: list[tuple[object, object, float]] = []
    current_key = None
    for bar in bars:
        day = _parse_day(bar["time"])
        if day is None:
            continue
        iso = day.isocalendar()
        key = (iso.year, iso.week)
        if key != current_key:
            weeks.append((key, day, bar["close"]))
            current_key = key
        else:
            weeks[-1] = (key, day, bar["close"])
    if weeks and weeks[-1][1].weekday() < 4:
        weeks = weeks[:-1]
    return [close for _, _, close in weeks]


def _slope(series: list[float], atr: float, lookback: int = 3) -> float | None:
    if len(series) <= lookback or atr <= 0:
        return None
    return (series[-1] - series[-1 - lookback]) / (lookback * atr)


def evaluate_stage(rows: Iterable[Any]) -> dict[str, Any]:
    bars = normalize_bars(rows)
    unavailable = {
        "available": False,
        "stage": 0,
        "barDate": "",
        "hardInvalidation": False,
        "checks": {key: False for key, _ in CHECK_LABELS},
        "missingFor4": [label for _, label in CHECK_LABELS],
    }
    if len(bars) < 170:
        return {**unavailable, "reason": "insufficient_daily_history"}

    closes = [bar["close"] for bar in bars]
    ema10 = _ema(closes, 10)
    ema20 = _ema(closes, 20)
    sma50 = _sma(closes, 50)
    atr14 = _wilder_atr(bars)
    weekly_close = _completed_weekly_closes(bars)
    sma30w = _sma(weekly_close, 30)
    if (
        len(sma30w) < 35
        or sma30w[-1] is None
        or sma30w[-5] is None
        or sma50[-1] is None
        or sma50[-6] is None
        or atr14[-1] is None
    ):
        return {**unavailable, "reason": "insufficient_indicator_history"}

    price = closes[-1]
    atr = float(atr14[-1])
    if atr <= 0:
        return {**unavailable, "reason": "invalid_atr"}

    ema10_slope = _slope(ema10, atr)
    ema20_slope = _slope(ema20, atr)
    if ema10_slope is None or ema20_slope is None:
        return {**unavailable, "reason": "insufficient_slope_history"}

    sma50_rising = float(sma50[-1]) > float(sma50[-6])
    sma30w_rising = float(sma30w[-1]) > float(sma30w[-5])
    structure_valid = price >= float(sma50[-1]) - 1.50 * atr
    not_extended = price <= ema20[-1] + 1.50 * atr

    near: list[bool] = []
    compressed: list[bool] = []
    sane: list[bool] = []
    for index, close in enumerate(closes):
        bar_atr = atr14[index]
        bar_sma50 = sma50[index]
        if bar_atr is None or bar_sma50 is None or float(bar_atr) <= 0:
            near.append(False)
            compressed.append(False)
            sane.append(False)
            continue
        bar_atr = float(bar_atr)
        near.append(
            float(bar_sma50) - 0.75 * bar_atr
            <= close
            <= float(bar_sma50) + 1.25 * bar_atr
        )
        compressed.append(abs(ema10[index] - ema20[index]) <= 0.35 * bar_atr)
        sane.append(close <= ema20[index] + 1.50 * bar_atr)

    recent_pullback_compression = any(
        a and b and c for a, b, c in zip(near[-10:], compressed[-10:], sane[-10:])
    )
    ema10_flat = ema10_slope >= -0.05
    ema20_flat = ema20_slope >= -0.03
    one_rising = ema10_slope > 0 or ema20_slope > 0
    ema10_rising = ema10_slope > 0
    ema20_non_falling = ema20_slope >= 0
    ema10_above = ema10[-1] > ema20[-1]
    close_above = price > ema10[-1] and price > ema20[-1]
    spread = [left - right for left, right in zip(ema10, ema20)]
    fresh_cross = ema10[-1] > ema20[-1] and ema10[-2] <= ema20[-2]
    widening = spread[-1] > 0 and spread[-1] > spread[-2]
    bullish_reexpansion = fresh_cross or widening

    trend_ok = sma50_rising and sma30w_rising and structure_valid and not_extended
    watch_closely = trend_ok and near[-1] and compressed[-1]
    ready = watch_closely and ema10_flat and ema20_flat and one_rising
    trigger = (
        trend_ok
        and recent_pullback_compression
        and ema10_rising
        and ema20_non_falling
        and ema10_above
        and close_above
        and bullish_reexpansion
    )
    stage = 4 if trigger else 3 if ready else 2 if watch_closely else 1 if trend_ok else 0

    checks = {
        "sma50Rising": sma50_rising,
        "sma30wRising": sma30w_rising,
        "structureValid": structure_valid,
        "notExtended": not_extended,
        "recentPullbackCompression": recent_pullback_compression,
        "ema10Rising": ema10_rising,
        "ema20NonFalling": ema20_non_falling,
        "ema10AboveEma20": ema10_above,
        "closeAboveShortEmas": close_above,
        "bullishReexpansion": bullish_reexpansion,
    }
    return {
        "available": True,
        "stage": stage,
        "barDate": bars[-1]["time"][:10],
        "hardInvalidation": not (sma50_rising and sma30w_rising and structure_valid),
        "checks": checks,
        "missingFor4": [label for key, label in CHECK_LABELS if not checks[key]],
        "metrics": {
            "price": price,
            "atr14": atr,
            "ema10": ema10[-1],
            "ema20": ema20[-1],
            "sma50": sma50[-1],
            "sma30w": sma30w[-1],
            "ema10SlopeAtrDay": ema10_slope,
            "ema20SlopeAtrDay": ema20_slope,
        },
    }


def apply_hysteresis(previous: dict[str, Any] | None, result: dict[str, Any]) -> dict[str, Any]:
    raw_stage = int(result.get("stage") or 0)
    bar_date = str(result.get("barDate") or "")
    if previous is None:
        return {
            "rawStage": raw_stage,
            "effectiveStage": raw_stage,
            "barDate": bar_date,
            "belowCount": 0,
            "lastBelowBar": None,
        }

    effective = int(previous.get("effectiveStage") or 0)
    below_count = int(previous.get("belowCount") or 0)
    last_below_bar = previous.get("lastBelowBar")

    if result.get("hardInvalidation"):
        effective = 0
        below_count = 0
        last_below_bar = None
    elif raw_stage >= effective:
        effective = raw_stage
        below_count = 0
        last_below_bar = None
    elif bar_date and bar_date != last_below_bar:
        below_count += 1
        last_below_bar = bar_date
        if below_count >= 2:
            effective = raw_stage
            below_count = 0
            last_below_bar = None

    return {
        "rawStage": raw_stage,
        "effectiveStage": effective,
        "barDate": bar_date,
        "belowCount": below_count,
        "lastBelowBar": last_below_bar,
    }


def _candidate_rank(item: dict[str, Any]) -> tuple[float, float, float, str]:
    return (
        float(item.get("kell_score") or 0.0),
        float(item.get("kell_readiness_score") or 0.0),
        float(item.get("kell_quality_score") or 0.0),
        str(item.get("ticker") or ""),
    )


def process_results(
    candidates: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
    prior_state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    initialized = bool(prior_state.get("initialized"))
    old = prior_state.get("tickers") if isinstance(prior_state.get("tickers"), dict) else {}
    next_tickers = dict(old)
    events = {"ready": [], "trigger": [], "invalidated": []}

    for item in candidates:
        ticker = str(item.get("ticker") or "").upper()
        if not ticker or ticker not in results or not results[ticker].get("available"):
            continue
        previous = old.get(ticker) if isinstance(old.get(ticker), dict) else None
        before = int(previous.get("effectiveStage") or 0) if previous else 0
        next_entry = apply_hysteresis(previous, results[ticker])
        after = int(next_entry["effectiveStage"])
        next_tickers[ticker] = next_entry

        if not initialized:
            continue
        enriched = {
            **item,
            "ticker": ticker,
            "trendBirthHourly": results[ticker],
            "previousEffectiveStage": before,
            "effectiveStage": after,
        }
        if after == 4 and before < 4:
            events["trigger"].append(enriched)
        elif after == 3 and before < 3:
            events["ready"].append(enriched)
        elif after == 0 and before >= 3:
            events["invalidated"].append(enriched)

    for group in events.values():
        group.sort(key=_candidate_rank, reverse=True)

    state = {
        "schemaVersion": 1,
        "ruleset": RULESET,
        "initialized": True,
        "tickers": next_tickers,
    }
    return state, events


def _dashboard_url(grid_url: str, snapshot_id: str) -> str:
    return grid_url.rstrip("/") + "/?" + urlencode({"snapshot": snapshot_id})


def _render_link(url: str) -> str:
    return f"[View dashboard]({_escape_md_v2(url)})"


def build_series(events: dict[str, list[dict[str, Any]]], dashboard_url: str) -> dict[str, list[str]]:
    series: dict[str, list[str]] = {}
    ready = events["ready"]
    if ready:
        shown = ready[:MAX_READY]
        lines = [
            "🟠 KELL TREND BIRTH — 3/4 READY",
            f"{len(ready)} new candidate{'s' if len(ready) != 1 else ''}",
            "",
        ]
        for item in shown:
            missing = (item.get("trendBirthHourly") or {}).get("missingFor4") or []
            lines.append(f"{item['ticker']} — 3/4")
            if missing:
                lines.append("❌ Still needed: " + "; ".join(missing))
            lines.append("")
        extra = len(ready) - len(shown)
        if extra:
            lines.append(f"+{extra} additional candidates — DASHBOARD")
        else:
            lines.append("DASHBOARD")
        text = _escape_md_v2("\n".join(lines).strip()).replace(
            _escape_md_v2("DASHBOARD"),
            _render_link(dashboard_url),
        )
        series["trend-birth-hourly-ready"] = split_telegram_message(text)

    for item in events["trigger"]:
        ticker = item["ticker"]
        checks = (item.get("trendBirthHourly") or {}).get("checks") or {}
        lines = [
            "🚀 KELL TREND BIRTH — 4/4 TRIGGER",
            f"{ticker} — intraday / provisional",
            "",
        ]
        lines.extend(
            f"{'✅' if checks.get(key) else '❌'} {label}" for key, label in CHECK_LABELS
        )
        lines.extend(["", "Final confirmation is the completed daily bar.", "DASHBOARD"])
        text = _escape_md_v2("\n".join(lines)).replace(
            _escape_md_v2("DASHBOARD"),
            _render_link(dashboard_url),
        )
        series[f"trend-birth-hourly-trigger-{ticker}"] = split_telegram_message(text)

    invalidated = events["invalidated"]
    if invalidated:
        lines = [
            "⚪ KELL TREND BIRTH — INVALIDATED",
            f"{len(invalidated)} former READY/TRIGGER candidate{'s' if len(invalidated) != 1 else ''}",
            "",
        ]
        lines.extend(
            f"{item['ticker']} — {item['previousEffectiveStage']}/4 → 0/4"
            for item in invalidated[:MAX_READY]
        )
        extra = len(invalidated) - min(len(invalidated), MAX_READY)
        lines.extend(["", f"+{extra} additional candidates — DASHBOARD" if extra else "DASHBOARD"])
        text = _escape_md_v2("\n".join(lines)).replace(
            _escape_md_v2("DASHBOARD"),
            _render_link(dashboard_url),
        )
        series["trend-birth-hourly-invalidated"] = split_telegram_message(text)

    return series


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schemaVersion": 1, "ruleset": RULESET, "initialized": False, "tickers": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1 or payload.get("ruleset") != RULESET:
        return {"schemaVersion": 1, "ruleset": RULESET, "initialized": False, "tickers": {}}
    return payload


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def fetch_verified_universe(grid_url: str = DEFAULT_GRID_URL) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, _summary = verify_publication(grid_url)
    snapshot = json.loads(fetch_bytes(grid_url.rstrip("/") + "/" + manifest["snapshotPath"]))
    candidates = snapshot.get("kellCandidates") or []
    if not isinstance(candidates, list):
        raise ValueError("Verified Trend Birth snapshot has no Kell candidate list")
    return manifest, [item for item in candidates if isinstance(item, dict) and item.get("ticker")]


def _frame_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stamp, row in frame.dropna(subset=["Open", "High", "Low", "Close"]).iterrows():
        rows.append(
            {
                "time": pd.Timestamp(stamp).date().isoformat(),
                "open": row["Open"],
                "high": row["High"],
                "low": row["Low"],
                "close": row["Close"],
                "volume": row.get("Volume", 0),
            }
        )
    return rows


def fetch_live_results(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tickers = sorted({str(item["ticker"]).upper() for item in candidates})
    if not tickers:
        return {}
    data = yf.download(
        tickers=tickers,
        period="2y",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        threads=True,
        progress=False,
    )
    results: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        try:
            if len(tickers) == 1 and not isinstance(data.columns, pd.MultiIndex):
                frame = data
            else:
                frame = data[ticker]
            results[ticker] = evaluate_stage(_frame_rows(frame))
        except (KeyError, TypeError, ValueError):
            results[ticker] = {
                "available": False,
                "stage": 0,
                "barDate": "",
                "hardInvalidation": False,
                "checks": {},
                "missingFor4": [],
                "reason": "live_data_unavailable",
            }
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid-url", default=DEFAULT_GRID_URL)
    parser.add_argument("--state-path", type=Path, default=Path(DEFAULT_STATE))
    parser.add_argument("--delivery-endpoint", default="")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--update-state", action="store_true")
    args = parser.parse_args()

    manifest, candidates = fetch_verified_universe(args.grid_url)
    live = fetch_live_results(candidates)
    prior = load_state(args.state_path)
    next_state, events = process_results(candidates, live, prior)
    dashboard = _dashboard_url(args.grid_url, str(manifest["snapshotId"]))
    series = build_series(events, dashboard)

    summary = {
        "ruleset": RULESET,
        "sessionDate": manifest["sessionDate"],
        "universe": len(candidates),
        "liveAvailable": sum(result.get("available") is True for result in live.values()),
        "ready": len(events["ready"]),
        "trigger": len(events["trigger"]),
        "invalidated": len(events["invalidated"]),
        "baselineOnly": not bool(prior.get("initialized")),
        "series": list(series),
    }

    if args.send and series:
        if not args.delivery_endpoint:
            raise ValueError("--delivery-endpoint is required for sending")
        if not deliver_series(series, endpoint=args.delivery_endpoint):
            raise RuntimeError("Trend Birth hourly Telegram delivery failed")

    if args.update_state:
        save_state(args.state_path, next_state)

    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
