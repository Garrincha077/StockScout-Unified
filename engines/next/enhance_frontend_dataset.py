#!/usr/bin/env python3
"""Enrich an already-produced StockScout frontend dataset from its 5Y chart shards.

No stock screening is repeated here. The script turns raw OHLCV + RS history into
sortable decision fields and transparent setup recipes. It is intentionally heuristic:
the raw fields remain visible so the UI never hides the evidence behind one score.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "frontend" / "public" / "data" / "latest.json"
CHART_DIR = ROOT / "frontend" / "public" / "data" / "charts"


def finite(v, default=0.0):
    try:
        v = float(v)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


def pct(now, old):
    now, old = finite(now), finite(old)
    return (now / old - 1.0) * 100.0 if old else 0.0


def value_ago(values, periods):
    if not values:
        return 0.0
    idx = max(0, len(values) - 1 - periods)
    return finite(values[idx])


def avg(values):
    vals = [finite(v) for v in values if math.isfinite(finite(v))]
    return mean(vals) if vals else 0.0


def sma(values, n):
    return avg(values[-n:]) if len(values) >= n else avg(values)


def slope_pct(values, n=20):
    vals = [finite(v) for v in values[-n:]]
    if len(vals) < 3:
        return 0.0
    xbar = (len(vals) - 1) / 2.0
    ybar = avg(vals)
    den = sum((i - xbar) ** 2 for i in range(len(vals)))
    if not den or not ybar:
        return 0.0
    num = sum((i - xbar) * (v - ybar) for i, v in enumerate(vals))
    return num / den / ybar * 100.0


def rolling_sma(values, n):
    out = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i + 1 >= n else None)
    return out


def trend_template(close, high, low, stage):
    if len(close) < 200:
        return 0
    s50, s150, s200 = sma(close, 50), sma(close, 150), sma(close, 200)
    s200_series = rolling_sma(close, 200)
    s200_now = s200_series[-1] or s200
    old = s200_series[-21] if len(s200_series) >= 21 else None
    high52 = max(high[-252:]) if high else 0
    low52 = min(low[-252:]) if low else 0
    p = close[-1]
    checks = [
        p > s150 and p > s200,
        s150 > s200,
        old is not None and s200_now > old,
        s50 > s150,
        p > s50,
        low52 > 0 and pct(p, low52) >= 30,
        high52 > 0 and pct(p, high52) >= -25,
        int(stage or 0) == 2,
    ]
    return sum(bool(x) for x in checks)


def true_ranges(rows):
    out = []
    prev = None
    for r in rows:
        h, l, c = finite(r[2]), finite(r[3]), finite(r[4])
        tr = h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev))
        out.append(max(0.0, tr))
        prev = c
    return out


def consecutive_stage2_days(close):
    if len(close) < 205:
        return 0
    s50 = rolling_sma(close, 50)
    s200 = rolling_sma(close, 200)
    count = 0
    for i in range(len(close) - 1, 200, -1):
        if s50[i] is None or s200[i] is None:
            break
        recent50 = [v for v in s50[max(0, i - 19):i + 1] if v is not None]
        slope = slope_pct(recent50, min(20, len(recent50))) if recent50 else 0
        if close[i] > s50[i] > s200[i] and slope > 0:
            count += 1
        else:
            break
    return count


def base_weeks(close, high, low):
    """Approximate current-base age: consecutive weeks price stayed within 35% of 26W high."""
    if len(close) < 30:
        return 0
    look = min(325, len(close))
    start = len(close) - look
    recent_high = max(high[-min(126, len(high)):]) if high else close[-1]
    floor = recent_high * 0.65
    idx = len(close) - 1
    while idx > start and low[idx] >= floor:
        idx -= 1
    return max(1, round((len(close) - 1 - idx) / 5))


def score_and_recipes(row, bars):
    rows = bars[-1265:]
    close = [finite(r[4]) for r in rows]
    high = [finite(r[2]) for r in rows]
    low = [finite(r[3]) for r in rows]
    vol = [finite(r[5]) for r in rows]
    rs = [finite(r[6]) for r in rows]
    if len(close) < 60:
        return

    price = close[-1]
    ret3 = pct(price, value_ago(close, 63))
    ret6 = pct(price, value_ago(close, 126))
    ret12 = pct(price, value_ago(close, 252))
    prior9 = pct(value_ago(close, 63), value_ago(close, 252)) if len(close) > 252 else 0.0
    high52 = max(high[-252:]) if high else 0.0
    low52 = min(low[-252:]) if low else 0.0
    from_high = pct(price, high52) if high52 else 0.0
    from_low = pct(price, low52) if low52 else 0.0

    ma50, ma150, ma200 = sma(close, 50), sma(close, 150), sma(close, 200)
    ma50_series = [v for v in rolling_sma(close, 50) if v is not None]
    ma150_series = [v for v in rolling_sma(close, 150) if v is not None]
    ma200_series = [v for v in rolling_sma(close, 200) if v is not None]
    slope50 = slope_pct(ma50_series, 20)
    slope150 = slope_pct(ma150_series, 20)
    slope200 = slope_pct(ma200_series, 20)
    dist10w = pct(price, ma50)
    dist30w = pct(price, ma150)

    prev60 = max(high[-61:-1]) if len(high) > 61 else max(high[:-1])
    breakout_pct = pct(price, prev60) if prev60 else 0.0
    range20 = pct(max(high[-20:]), min(low[-20:])) if min(low[-20:]) > 0 else 0.0
    range60 = pct(max(high[-60:]), min(low[-60:])) if min(low[-60:]) > 0 else 0.0
    depth126 = pct(max(high[-126:]), min(low[-126:])) if min(low[-126:]) > 0 else 0.0

    tr = true_ranges(rows)
    atr20 = avg(tr[-20:])
    prior_tr = avg(tr[-80:-20]) if len(tr) >= 80 else avg(tr[:-20])
    atr_pct = atr20 / price * 100.0 if price else 0.0
    atr_compression = clamp((1 - atr20 / prior_tr) * 100.0) if prior_tr else 0.0
    recent_vol = avg(vol[-10:])
    prior_vol = avg(vol[-50:-10]) if len(vol) >= 50 else avg(vol[:-10])
    volume_dry = recent_vol / prior_vol if prior_vol else 1.0

    rs_now = rs[-1] if rs else 0.0
    rs_high = max(rs[-252:]) if rs else 0.0
    rs_from_high = pct(rs_now, rs_high) if rs_high else 0.0
    rs_slope20 = slope_pct(rs, 20)
    rs_prev = slope_pct(rs[-40:-20], 20) if len(rs) >= 40 else 0.0
    chart_rs_accel = rs_slope20 - rs_prev

    stage = int(row.get("stage", 0) or 0)
    stage2_age = round(consecutive_stage2_days(close) / 5.0, 1)
    tt = trend_template(close, high, low, stage)
    bweeks = base_weeks(close, high, low)
    vol_ratio = finite(row.get("volumeRatio"), 1.0)
    rs_rank = int(row.get("rsRank", 0) or 0)
    rs_accel = finite(row.get("rsAcceleration"), chart_rs_accel)
    vcp = finite(row.get("vcpScore"))
    contraction = finite(row.get("contraction"))

    extension_abs = abs(dist10w)
    extended = dist10w > 15 or dist30w > 25

    # Independent transparent dimensions.  These are intentionally not one monolithic model.
    structure = clamp(tt / 8 * 65 + max(0, slope150) * 80 + (10 if stage == 2 else 4 if stage == 1 else 0))
    rs_score = clamp((rs_rank - 50) * 1.4 + max(0, rs_accel) * 35 + max(0, rs_slope20) * 20)
    base_score = clamp(max(vcp, contraction) * .65 + atr_compression * .25 + max(0, 18 - range20) * 1.5)
    trigger = clamp(max(0, 5 + breakout_pct * 8) + max(0, vol_ratio - .8) * 28 + max(0, ret3) * .7)
    freshness = clamp((30 if stage == 1 else max(0, 40 - stage2_age * 2.5)) + max(0, rs_accel) * 30 + max(0, 12 - extension_abs) * 2.5)
    neglected_score = clamp((20 - min(20, max(0, prior9))) * 3 + max(0, ret3) * 1.5 + max(0, rs_accel) * 25)

    tags = []
    if prior9 <= 15 and ret3 >= 5 and rs_rank >= 70 and rs_accel > 0 and -8 <= dist10w <= 12:
        tags.append("Neglected → Leader")
    if (stage == 1 or (stage == 2 and stage2_age <= 8)) and tt >= 5 and rs_accel > 0 and dist30w <= 15:
        tags.append("S1→S2 Transition")
    if stage == 2 and stage2_age <= 12 and tt >= 6 and not extended:
        tags.append("Fresh Stage 2")
    if breakout_pct >= -1.5 and vol_ratio >= 1.5 and rs_accel > 0:
        tags.append("Fresh Breakout")
    if rs_rank >= 80 and rs_from_high >= -3 and from_high <= -5:
        tags.append("RS Before Price")
    if (vcp >= 60 or contraction >= 50 or atr_compression >= 25) and range20 <= 12 and volume_dry <= 1.05:
        tags.append("Tight / VCP")
    if vol_ratio >= 1.8 and rs_accel > 0 and ret3 > 0:
        tags.append("Volume Wake-Up")
    if stage == 2 and -3 <= dist10w <= 5 and rs_rank >= 70 and slope150 > 0:
        tags.append("10W Pullback")
    if bweeks >= 20 and breakout_pct >= -2 and vol_ratio >= 1.3 and rs_accel > 0:
        tags.append("Long Base Breakout")
    if extended:
        tags.append("⚠ Extended")
    if not tags:
        if stage == 2 and rs_rank >= 80:
            tags.append("Trend Leader")
        elif stage == 1:
            tags.append("Base Building")
        else:
            tags.append(str(row.get("stageName") or "Other"))

    positive_tags = [t for t in tags if not t.startswith("⚠")]
    confluence_checks = [
        stage in (1, 2), tt >= 6, rs_rank >= 75, rs_accel > 0, vol_ratio >= 1.2,
        max(vcp, contraction, atr_compression) >= 40, -8 <= dist10w <= 12,
        breakout_pct >= -5, rs_from_high >= -5,
    ]
    if row.get("fundamentalSupport") is True:
        confluence_checks.append(True)
    confluence = sum(bool(x) for x in confluence_checks)

    opportunity = round(clamp(structure * .20 + rs_score * .23 + base_score * .17 + trigger * .20 + freshness * .20))
    if extended:
        opportunity = max(0, opportunity - 12)

    primary_order = [
        "Neglected → Leader", "S1→S2 Transition", "Long Base Breakout", "Fresh Breakout",
        "RS Before Price", "Tight / VCP", "10W Pullback", "Fresh Stage 2", "Volume Wake-Up",
        "Trend Leader", "Base Building",
    ]
    primary = next((t for t in primary_order if t in positive_tags), positive_tags[0] if positive_tags else tags[0])

    row.update({
        "primarySetup": primary,
        "setupTags": tags,
        "setupMatchCount": len(positive_tags),
        "opportunityScore": int(opportunity),
        "confluence": int(confluence),
        "structureScore": round(structure, 1),
        "rsScore": round(rs_score, 1),
        "baseScore": round(base_score, 1),
        "triggerScore": round(trigger, 1),
        "freshnessScore": round(freshness, 1),
        "neglectedScore": round(neglected_score, 1),
        "return3m": round(ret3, 2),
        "return6m": round(ret6, 2),
        "return1y": round(ret12, 2),
        "prior9mReturn": round(prior9, 2),
        "from52wHigh": round(from_high, 2),
        "from52wLow": round(from_low, 2),
        "distance10w": round(dist10w, 2),
        "distance30w": round(dist30w, 2),
        "extensionAbs": round(extension_abs, 2),
        "slope50": round(slope50, 4),
        "slope150": round(slope150, 4),
        "slope200": round(slope200, 4),
        "trendTemplatePasses": int(tt),
        "stage2AgeWeeks": stage2_age,
        "baseWeeks": int(bweeks),
        "baseDepthPct": round(depth126, 2),
        "tightRange20": round(range20, 2),
        "tightRange60": round(range60, 2),
        "atrPct": round(atr_pct, 2),
        "atrCompression": round(atr_compression, 1),
        "volumeDryUp": round(volume_dry, 2),
        "breakoutPct": round(breakout_pct, 2),
        "breakout60": bool(breakout_pct >= -1.5),
        "rsFromHigh": round(rs_from_high, 2),
        "rsNewHigh": bool(rs_from_high >= -1.0),
        "chartRsSlope": round(rs_slope20, 4),
        "chartRsAcceleration": round(chart_rs_accel, 4),
        "extended": bool(extended),
    })


def main():
    if not DATA.exists():
        print("No latest.json; feature enhancement skipped")
        return
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    universe = payload.get("universe") or []
    by_ticker = {str(r.get("ticker", "")).upper(): r for r in universe if r.get("ticker")}
    seen = 0
    for shard in CHART_DIR.glob("*.json"):
        try:
            content = json.loads(shard.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"skip {shard.name}: {exc}")
            continue
        for ticker, bars in content.items():
            row = by_ticker.get(str(ticker).upper())
            if row is None or not bars:
                continue
            score_and_recipes(row, bars)
            seen += 1

    market = payload.setdefault("market", {})
    market["featureCoverage"] = seen
    market["neglectedLeaders"] = sum(1 for r in universe if "Neglected → Leader" in (r.get("setupTags") or []))
    market["transitions"] = sum(1 for r in universe if "S1→S2 Transition" in (r.get("setupTags") or []))
    market["freshBreakouts"] = sum(1 for r in universe if "Fresh Breakout" in (r.get("setupTags") or []))
    market["highConfluence"] = sum(1 for r in universe if int(r.get("confluence", 0) or 0) >= 7)
    payload["version"] = max(4, int(payload.get("version", 1) or 1))
    payload["featureModel"] = "data-first-v1"
    DATA.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"Enhanced {seen:,}/{len(universe):,} rows with data-first features and setup recipes")


if __name__ == "__main__":
    main()
