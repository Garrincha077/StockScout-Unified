#!/usr/bin/env python3
"""Compute the user's weekly 10W/30W MA-cluster timing signal.

Definition:
- WATCH: 10W/30W are starting to compress and stop falling;
- READY: a tight cluster sits close to price before the trigger;
- ENTRY: price crosses above the upper MA on confirming weekly-volume pace,
  without already being materially extended.

Timing quality is exposed as Tier A/B/C. This is intentionally separate from the
Emerging Leader, RS, group and fundamental layers: those decide *what* to watch;
this module decides *when* the user's concrete weekly entry is forming/firing.

The calculation uses the existing adjusted-OHLCV 5Y chart shards. LEGACY remains
untouched.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "frontend" / "public" / "data" / "latest.json"
CHART_DIR = ROOT / "frontend" / "public" / "data" / "charts"
MODEL = "weekly-ma-cluster-breakout-v2-tiers"


def finite(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else float(default)
    except Exception:
        return float(default)


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def pct(now, old):
    now, old = finite(now), finite(old)
    return (now / old - 1.0) * 100.0 if old else 0.0


def rolling_sma(values, n):
    out = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i + 1 >= n else None)
    return out


def aggregate_weekly(bars):
    """Aggregate daily [date,o,h,l,c,volume,rs] bars into Monday-keyed weeks."""
    weeks = []
    current = None
    for raw in bars:
        if len(raw) < 6:
            continue
        try:
            d = datetime.fromisoformat(str(raw[0])[:10])
        except Exception:
            continue
        monday = (d - timedelta(days=d.weekday())).date().isoformat()
        o, h, l, c, v = map(finite, raw[1:6])
        if current is None or current["week"] != monday:
            current = {
                "week": monday,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
                "days": 1,
            }
            weeks.append(current)
        else:
            current["high"] = max(current["high"], h)
            current["low"] = min(current["low"], l)
            current["close"] = c
            current["volume"] += v
            current["days"] += 1
    return weeks


def empty_result(reason):
    return {
        "maClusterPhase": "NONE",
        "maClusterTier": None,
        "maClusterTierRank": 0,
        "maClusterTierLabel": "—",
        "maClusterWatch": False,
        "maClusterReady": False,
        "maClusterEntrySignal": False,
        "maClusterScore": 0.0,
        "maClusterReasons": [reason],
    }


def score_bars(bars):
    weeks = aggregate_weekly(bars)
    if len(weeks) < 35:
        return empty_result("Insufficient weekly history")

    closes = [finite(w["close"]) for w in weeks]
    ma10s = rolling_sma(closes, 10)
    ma30s = rolling_sma(closes, 30)
    i = len(weeks) - 1
    if ma10s[i] is None or ma30s[i] is None:
        return empty_result("Insufficient weekly MA history")

    ma10 = finite(ma10s[i])
    ma30 = finite(ma30s[i])
    cluster_mid = (ma10 + ma30) / 2.0 if ma10 and ma30 else 0.0
    cluster_top = max(ma10, ma30)
    spread_pct = abs(ma10 - ma30) / cluster_mid * 100.0 if cluster_mid else 100.0

    def ma_slope(series, lookback=4):
        now = series[i]
        old = series[max(0, i - lookback)]
        if now is None or old is None:
            return 0.0
        return pct(now, old)

    slope10_4w = ma_slope(ma10s, 4)
    slope30_4w = ma_slope(ma30s, 4)
    turn_count = int(slope10_4w >= 0) + int(slope30_4w >= 0)

    price = closes[-1]
    price_vs_top = pct(price, cluster_top)

    prev_i = i - 1
    prev_close = closes[prev_i]
    prev_ma10 = finite(ma10s[prev_i]) if ma10s[prev_i] is not None else ma10
    prev_ma30 = finite(ma30s[prev_i]) if ma30s[prev_i] is not None else ma30
    prev_top = max(prev_ma10, prev_ma30)

    # Normalize the current partial week to a daily pace versus the prior 10 weeks.
    # This keeps an intrawweek scan comparable with completed historical weeks.
    current_week = weeks[-1]
    daily_pace = current_week["volume"] / max(1, current_week["days"])
    prior_weeks = weeks[-11:-1]
    prior_daily = [w["volume"] / max(1, w["days"]) for w in prior_weeks]
    avg_prior_daily = sum(prior_daily) / len(prior_daily) if prior_daily else 0.0
    volume_pace = daily_pace / avg_prior_daily if avg_prior_daily else 1.0

    very_tight = spread_pct <= 2.0
    tight = spread_pct <= 3.5
    developing = spread_pct <= 5.0
    flattening = slope10_4w >= -1.0 and slope30_4w >= -1.0 and turn_count >= 1
    developing_turn = slope10_4w >= -1.5 and slope30_4w >= -1.5
    above_cluster = price > cluster_top
    crossed_now = above_cluster and prev_close <= prev_top * 1.02
    not_chased = -1.0 <= price_vs_top <= 8.0
    volume_confirmed = volume_pace >= 1.40

    watch = bool(
        developing
        and developing_turn
        and -8.0 <= price_vs_top <= 4.0
    )
    ready = bool(
        tight
        and flattening
        and -6.0 <= price_vs_top <= 2.5
    )
    entry = bool(
        tight
        and flattening
        and crossed_now
        and not_chased
        and volume_confirmed
    )

    tightness_score = clamp((4.5 - spread_pct) / 4.0 * 100.0)
    turn_score = clamp(
        40.0
        + max(-1.0, min(2.0, slope10_4w)) * 15.0
        + max(-1.0, min(2.0, slope30_4w)) * 15.0
        + turn_count * 10.0
    )
    trigger_score = 100.0 if entry else clamp((8.0 - abs(price_vs_top)) / 8.0 * 100.0)
    volume_score = clamp((volume_pace - 0.8) / 1.2 * 100.0)
    score = clamp(0.35 * tightness_score + 0.20 * turn_score + 0.25 * trigger_score + 0.20 * volume_score)

    # Timing tiers are deliberately rule-based and readable.
    # A-entry requires both MAs actually turning up and clearly above-normal volume.
    if entry:
        phase = "ENTRY"
        if (
            very_tight
            and turn_count == 2
            and slope10_4w >= 0
            and slope30_4w >= 0
            and volume_pace >= 1.80
            and 0.0 <= price_vs_top <= 5.0
        ):
            tier = "A"
        else:
            tier = "B"
    elif ready:
        phase = "READY"
        if (
            very_tight
            and turn_count == 2
            and slope10_4w >= -0.25
            and slope30_4w >= -0.25
            and -3.5 <= price_vs_top <= 1.5
        ):
            tier = "A"
        else:
            tier = "B"
    elif watch:
        phase = "WATCH"
        tier = "C"
    else:
        phase = "NONE"
        tier = None

    tier_rank = {None: 0, "C": 1, "B": 2, "A": 3}[tier]
    tier_label = f"{tier} · {phase}" if tier else "—"

    reasons = [
        f"10W/30W spread {spread_pct:.1f}%",
        f"10W slope4w {slope10_4w:+.1f}%",
        f"30W slope4w {slope30_4w:+.1f}%",
        f"Price vs cluster {price_vs_top:+.1f}%",
        f"Volume pace {volume_pace:.2f}x",
    ]
    if entry:
        reasons.insert(0, f"{tier_label}: broke above 10W/30W cluster on volume")
    elif ready:
        reasons.insert(0, f"{tier_label}: compressed cluster near price")
    elif watch:
        reasons.insert(0, f"{tier_label}: cluster is forming")

    return {
        "ma10w": round(ma10, 4),
        "ma30w": round(ma30, 4),
        "maClusterSpreadPct": round(spread_pct, 2),
        "ma10wSlope4w": round(slope10_4w, 3),
        "ma30wSlope4w": round(slope30_4w, 3),
        "maClusterTurnCount": int(turn_count),
        "maClusterTop": round(cluster_top, 4),
        "maClusterPricePct": round(price_vs_top, 2),
        "maClusterVolumePace": round(volume_pace, 2),
        "maClusterTight": bool(tight),
        "maClusterVeryTight": bool(very_tight),
        "maClusterWatch": watch,
        "maClusterReady": ready,
        "maClusterEntrySignal": entry,
        "maClusterPhase": phase,
        "maClusterTier": tier,
        "maClusterTierRank": tier_rank,
        "maClusterTierLabel": tier_label,
        "maClusterScore": round(score, 1),
        "maClusterReasons": reasons[:6],
    }


def apply_to_payload(payload):
    rows = payload.get("universe") or []
    row_map = {str(r.get("ticker") or "").upper(): r for r in rows}
    shards = payload.get("chartShards") or {}
    cache = {}
    covered = 0

    for ticker, row in row_map.items():
        shard_name = shards.get(ticker)
        if not shard_name:
            row.update(score_bars([]))
            continue
        path = CHART_DIR / shard_name
        try:
            if shard_name not in cache:
                cache[shard_name] = json.loads(path.read_text(encoding="utf-8"))
            bars = cache[shard_name].get(ticker) or []
            result = score_bars(bars)
            if result.get("ma10w") is not None:
                covered += 1
            row.update(result)
        except Exception as exc:
            row.update(score_bars([]))
            row["maClusterReasons"] = [f"Cluster data error: {type(exc).__name__}"]

    market = payload.setdefault("market", {})
    market["maClusterModel"] = MODEL
    market["maClusterCoverage"] = covered
    market["maClusterWatchCount"] = sum(r.get("maClusterPhase") == "WATCH" for r in rows)
    market["maClusterReadyCount"] = sum(r.get("maClusterPhase") == "READY" for r in rows)
    market["maClusterEntryCount"] = sum(r.get("maClusterPhase") == "ENTRY" for r in rows)
    market["maClusterTierCounts"] = {
        tier: sum(r.get("maClusterTier") == tier for r in rows)
        for tier in ("A", "B", "C")
    }
    market["maClusterPhaseTierCounts"] = {
        f"{tier}-{phase}": sum(
            r.get("maClusterTier") == tier and r.get("maClusterPhase") == phase
            for r in rows
        )
        for tier in ("A", "B", "C")
        for phase in ("ENTRY", "READY", "WATCH")
    }
    market["maClusterTop"] = [
        r.get("ticker")
        for r in sorted(
            rows,
            key=lambda r: (
                r.get("maClusterPhase") == "ENTRY",
                r.get("maClusterPhase") == "READY",
                finite(r.get("maClusterTierRank")),
                finite(r.get("maClusterScore")),
                finite(r.get("rsRank")),
            ),
            reverse=True,
        )[:20]
    ]
    payload["maClusterModel"] = MODEL
    return payload


def main():
    if not DATA.exists():
        print("MA cluster computation skipped: latest.json missing")
        return
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    apply_to_payload(payload)
    DATA.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    market = payload.get("market") or {}
    print(
        f"MA Cluster: coverage={market.get('maClusterCoverage',0)} "
        f"watch={market.get('maClusterWatchCount',0)} "
        f"ready={market.get('maClusterReadyCount',0)} "
        f"entry={market.get('maClusterEntryCount',0)} "
        f"tiers={market.get('maClusterTierCounts',{})}"
    )


if __name__ == "__main__":
    main()
