#!/usr/bin/env python3
"""Add confidence-weighted behavioral group leadership to StockScout.

Stocks are mapped to liquid ETF proxies by correlation of SPY-relative daily
returns over roughly six months. These are behavioural proxies, not official
GICS classifications. Proxy ranks combine 1M/3M/6M performance relative to SPY.

v2 keeps proxy assignment observational but adds explicit confidence based on
correlation strength, persistence across two half-windows and usable history.
Weak/noisy proxy relationships are pulled toward neutral before they can affect
the separate leadership-adjusted ranking. Opportunity/Confluence are untouched.
"""
from __future__ import annotations

import json
import math
import pickle
from pathlib import Path
from statistics import median
from typing import Dict

import pandas as pd

from src.screening.group_proxies import SECTOR_PROXIES, INDUSTRY_PROXIES

DATA = Path("frontend/public/data/latest.json")
PRICE_CACHE = Path("data/batch_results/price_history_5y.pkl")
MODEL = "behavioral-proxy-v2-confidence"
SECTOR_MIN_CORR = 0.10
INDUSTRY_MIN_CORR = 0.12
STRONG_CORR = 0.55
MAX_LEADERSHIP_ADJUSTMENT_WEIGHT = 0.10


def finite(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def close_series(frame: pd.DataFrame | None) -> pd.Series:
    if frame is None or frame.empty or "Close" not in frame.columns:
        return pd.Series(dtype=float)
    out = frame["Close"].astype(float).dropna().copy()
    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    return out[~out.index.duplicated(keep="last")].sort_index()


def relative_perf(close: pd.Series, spy: pd.Series, periods: int) -> float:
    joined = pd.concat([close.rename("asset"), spy.rename("spy")], axis=1).dropna()
    if len(joined) < 2:
        return 0.0
    p = min(periods, len(joined) - 1)
    asset0, asset1 = float(joined.asset.iloc[-p - 1]), float(joined.asset.iloc[-1])
    spy0, spy1 = float(joined.spy.iloc[-p - 1]), float(joined.spy.iloc[-1])
    if not asset0 or not spy0:
        return 0.0
    return ((asset1 / asset0 - 1.0) - (spy1 / spy0 - 1.0)) * 100.0


def residual_returns(close: pd.Series, spy: pd.Series, periods: int = 126) -> pd.Series:
    joined = pd.concat([close.rename("asset"), spy.rename("spy")], axis=1).dropna().tail(periods + 2)
    if len(joined) < 20:
        return pd.Series(dtype=float)
    ret = joined.pct_change().dropna()
    return (ret.asset - ret.spy).rename("residual")


def proxy_stats(price_history: Dict[str, pd.DataFrame], spy: pd.Series, mapping: Dict[str, str]):
    stats = {}
    for ticker, name in mapping.items():
        close = close_series(price_history.get(ticker))
        if close.empty:
            continue
        r1 = relative_perf(close, spy, 20)
        r3 = relative_perf(close, spy, 63)
        r6 = relative_perf(close, spy, 126)
        stats[ticker] = {
            "ticker": ticker,
            "name": name,
            "rel1m": round(r1, 2),
            "rel3m": round(r3, 2),
            "rel6m": round(r6, 2),
            "composite": r1 * 0.25 + r3 * 0.35 + r6 * 0.40,
            "residual": residual_returns(close, spy),
        }
    order = sorted(stats, key=lambda t: stats[t]["composite"])
    den = max(1, len(order) - 1)
    for i, ticker in enumerate(order):
        stats[ticker]["rank"] = int(round(1 + 98 * i / den))
    return stats


def proxy_matrix(stats: dict) -> pd.DataFrame:
    series = {ticker: item["residual"] for ticker, item in stats.items() if not item["residual"].empty}
    return pd.DataFrame(series).sort_index() if series else pd.DataFrame()


def correlation_confidence(
    full_corr: float,
    recent_corr: float,
    prior_corr: float,
    observations: int,
    min_corr: float,
) -> tuple[float, float]:
    """Return (confidence 0-100, stability 0-100) for one proxy relationship.

    Confidence is deliberately conservative:
    - no credit at the minimum assignment threshold;
    - full strength around 0.55 correlation;
    - persistence is reduced if either half-window loses the relationship;
    - short histories are proportionally discounted.
    """
    full_corr = finite(full_corr)
    recent_corr = finite(recent_corr)
    prior_corr = finite(prior_corr)
    observations = max(0, int(observations or 0))
    strength = clamp((full_corr - min_corr) / max(1e-9, STRONG_CORR - min_corr), 0.0, 1.0)
    stability = clamp(1.0 - abs(recent_corr - prior_corr) / 0.50, 0.0, 1.0)
    if recent_corr >= min_corr and prior_corr >= min_corr:
        persistence = 1.0
    elif recent_corr >= min_corr or prior_corr >= min_corr:
        persistence = 0.50
    else:
        persistence = 0.0
    coverage = clamp(observations / 120.0, 0.0, 1.0)
    confidence = 100.0 * strength * (0.60 + 0.40 * stability) * persistence * coverage
    return round(clamp(confidence, 0.0, 100.0), 1), round(stability * 100.0, 1)


def neutralize_rank(rank: float, confidence: float) -> int:
    """Pull a 1-99 proxy rank toward neutral 50 as confidence falls."""
    adjusted = 50.0 + (finite(rank, 50.0) - 50.0) * clamp(finite(confidence) / 100.0, 0.0, 1.0)
    return int(round(clamp(adjusted, 1.0, 99.0)))


def leadership_score(individual: float, group_rank: float) -> int:
    """Bound group impact around the existing individual score.

    A neutral group rank (50) changes nothing. Even a fully confident extreme
    group can move the separate leadership score by only about +/-5 points.
    """
    individual = finite(individual)
    adjustment = (finite(group_rank, 50.0) - 50.0) * MAX_LEADERSHIP_ADJUSTMENT_WEIGHT
    return int(round(clamp(individual + adjustment, 0.0, 100.0)))


def best_proxy(stock_close: pd.Series, spy: pd.Series, matrix: pd.DataFrame, min_corr: float):
    stock = residual_returns(stock_close, spy)
    if stock.empty or matrix.empty:
        return None
    joined = matrix.join(stock.rename("__stock"), how="inner")
    if len(joined) < 60:
        return None
    candidates = []
    for col in matrix.columns:
        pair = joined[[col, "__stock"]].dropna()
        if len(pair) < 60:
            continue
        full_corr = finite(pair[col].corr(pair["__stock"]))
        recent = pair.tail(63)
        prior = pair.iloc[-126:-63] if len(pair) >= 126 else pair.iloc[: max(0, len(pair) - 63)]
        recent_corr = finite(recent[col].corr(recent["__stock"])) if len(recent) >= 20 else 0.0
        prior_corr = finite(prior[col].corr(prior["__stock"])) if len(prior) >= 20 else recent_corr
        confidence, stability = correlation_confidence(full_corr, recent_corr, prior_corr, len(pair), min_corr)
        candidates.append((str(col), full_corr, recent_corr, prior_corr, confidence, stability, len(pair)))
    if not candidates:
        return None
    ticker, corr, recent_corr, prior_corr, confidence, stability, observations = max(candidates, key=lambda x: x[1])
    if corr < min_corr:
        return {
            "ticker": None,
            "corr": corr,
            "recentCorr": recent_corr,
            "priorCorr": prior_corr,
            "confidence": 0.0,
            "stability": stability,
            "observations": observations,
        }
    return {
        "ticker": ticker,
        "corr": corr,
        "recentCorr": recent_corr,
        "priorCorr": prior_corr,
        "confidence": confidence,
        "stability": stability,
        "observations": observations,
    }


def stock_early(row: dict) -> bool:
    stage = int(finite(row.get("stage")))
    return (
        stage in (1, 2)
        and finite(row.get("rsRank")) >= 70
        and finite(row.get("rsAcceleration")) > 0
        and -8 <= finite(row.get("distance10w")) <= 10
        and not bool(row.get("extended"))
        and (stage == 1 or finite(row.get("stage2AgeWeeks")) <= 12)
    )


def summarize(mapping: Dict[str, str], stats: dict, rows: list[dict], ticker_field: str, confidence_field: str):
    buckets = {ticker: [] for ticker in mapping}
    for row in rows:
        ticker = row.get(ticker_field)
        if ticker in buckets:
            buckets[ticker].append(row)
    out = []
    for ticker, name in mapping.items():
        item = stats.get(ticker)
        if not item:
            continue
        members = buckets[ticker]
        opportunities = [finite(r.get("opportunityScore", r.get("score", 0))) for r in members]
        confidences = [finite(r.get(confidence_field)) for r in members]
        top = sorted(members, key=lambda r: (finite(r.get("leadershipScore")), finite(r.get("opportunityScore")), finite(r.get("rsRank"))), reverse=True)[:8]
        out.append({
            "ticker": ticker,
            "name": name,
            "rank": item["rank"],
            "rel1m": item["rel1m"],
            "rel3m": item["rel3m"],
            "rel6m": item["rel6m"],
            "stocks": len(members),
            "stage2Pct": round(sum(int(finite(r.get("stage"))) == 2 for r in members) / len(members) * 100.0, 1) if members else 0.0,
            "earlyLeaders": sum(stock_early(r) for r in members),
            "medianOpportunity": round(median(opportunities), 1) if opportunities else 0.0,
            "avgConfidence": round(sum(confidences) / len(confidences), 1) if confidences else 0.0,
            "topTickers": [r.get("ticker") for r in top if r.get("ticker")],
        })
    return sorted(out, key=lambda x: (x["rank"], x["avgConfidence"], x["earlyLeaders"], x["medianOpportunity"]), reverse=True)


def apply_fit(row: dict, prefix: str, fit: dict | None, stats: dict) -> tuple[int, float, float]:
    """Persist proxy diagnostics and return (raw rank, confidence, composite RS)."""
    if not fit or not fit.get("ticker"):
        row.update({
            f"{prefix}ProxyTicker": None,
            f"{prefix}Proxy": "Broad / Unclassified",
            f"{prefix}Correlation": round(finite((fit or {}).get("corr")), 3),
            f"{prefix}CorrelationRecent": round(finite((fit or {}).get("recentCorr")), 3),
            f"{prefix}CorrelationPrior": round(finite((fit or {}).get("priorCorr")), 3),
            f"{prefix}CorrelationStability": round(finite((fit or {}).get("stability")), 1),
            f"{prefix}ProxyConfidence": 0.0,
            f"{prefix}Rank": 50,
        })
        return 50, 0.0, 0.0
    ticker = fit["ticker"]
    item = stats[ticker]
    confidence = finite(fit.get("confidence"))
    row.update({
        f"{prefix}ProxyTicker": ticker,
        f"{prefix}Proxy": item["name"],
        f"{prefix}Correlation": round(finite(fit.get("corr")), 3),
        f"{prefix}CorrelationRecent": round(finite(fit.get("recentCorr")), 3),
        f"{prefix}CorrelationPrior": round(finite(fit.get("priorCorr")), 3),
        f"{prefix}CorrelationStability": round(finite(fit.get("stability")), 1),
        f"{prefix}ProxyConfidence": round(confidence, 1),
        f"{prefix}Rank": item["rank"],
    })
    return item["rank"], confidence, finite(item.get("composite"))


def main():
    if not DATA.exists() or not PRICE_CACHE.exists():
        print("Group leadership skipped: dataset or price cache missing")
        return
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    with PRICE_CACHE.open("rb") as fh:
        price_history: Dict[str, pd.DataFrame] = pickle.load(fh)
    spy = close_series(price_history.get("SPY"))
    if spy.empty:
        print("Group leadership skipped: SPY missing")
        return

    sector_stats = proxy_stats(price_history, spy, SECTOR_PROXIES)
    industry_stats = proxy_stats(price_history, spy, INDUSTRY_PROXIES)
    sectors_matrix = proxy_matrix(sector_stats)
    industries_matrix = proxy_matrix(industry_stats)

    sector_covered = industry_covered = 0
    confidences = []
    for row in rows:
        close = close_series(price_history.get(str(row.get("ticker", "")).upper()))
        sector_fit = None if close.empty else best_proxy(close, spy, sectors_matrix, SECTOR_MIN_CORR)
        industry_fit = None if close.empty else best_proxy(close, spy, industries_matrix, INDUSTRY_MIN_CORR)

        sector_rank, sector_conf, sector_rs = apply_fit(row, "sector", sector_fit, sector_stats)
        industry_rank, industry_conf, industry_rs = apply_fit(row, "industry", industry_fit, industry_stats)
        sector_covered += int(bool(sector_fit and sector_fit.get("ticker")))
        industry_covered += int(bool(industry_fit and industry_fit.get("ticker")))

        sector_weighted_rank = neutralize_rank(sector_rank, sector_conf)
        industry_weighted_rank = neutralize_rank(industry_rank, industry_conf)
        group_rank = int(round(sector_weighted_rank * 0.45 + industry_weighted_rank * 0.55))
        group_confidence = round(sector_conf * 0.45 + industry_conf * 0.55, 1)
        group_rs = round(sector_rs * (sector_conf / 100.0) * 0.45 + industry_rs * (industry_conf / 100.0) * 0.55, 2)
        row["sectorLeadershipRank"] = sector_weighted_rank
        row["industryLeadershipRank"] = industry_weighted_rank
        row["groupRank"] = group_rank
        row["groupRS"] = group_rs
        row["groupConfidence"] = group_confidence
        row["groupLeadership"] = group_rank
        individual = finite(row.get("opportunityScore", row.get("score", 0)))
        row["leadershipScore"] = leadership_score(individual, group_rank)
        confidences.append(group_confidence)

    sectors = summarize(SECTOR_PROXIES, sector_stats, rows, "sectorProxyTicker", "sectorProxyConfidence")
    industries = summarize(INDUSTRY_PROXIES, industry_stats, rows, "industryProxyTicker", "industryProxyConfidence")
    avg_confidence = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    payload["groups"] = {
        "method": MODEL,
        "description": "Behavioral ETF proxies from 6M SPY-relative correlation; rank/RS influence is pulled toward neutral as correlation confidence falls.",
        "confidenceMethod": "strength + recent/prior persistence + half-window stability + usable-history coverage",
        "sectorCoverage": sector_covered,
        "industryCoverage": industry_covered,
        "averageConfidence": avg_confidence,
        "maxLeadershipAdjustmentPoints": 5.0,
        "sectors": sectors,
        "industries": industries,
    }
    market = payload.setdefault("market", {})
    market.update(
        groupModel=MODEL,
        sectorCoverage=sector_covered,
        industryCoverage=industry_covered,
        groupAverageConfidence=avg_confidence,
        groupLeadershipMaxAdjustment=5.0,
    )
    if sectors:
        market.update(topSector=sectors[0]["name"], topSectorRank=sectors[0]["rank"])
    if industries:
        market.update(topIndustry=industries[0]["name"], topIndustryRank=industries[0]["rank"])
    payload["version"] = max(6, int(payload.get("version", 1) or 1))
    DATA.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(
        f"Group leadership v2: sectors {sector_covered:,}/{len(rows):,}, "
        f"industries {industry_covered:,}/{len(rows):,}, avg confidence={avg_confidence:.1f}%; "
        f"top sector={market.get('topSector','—')}, top industry={market.get('topIndustry','—')}"
    )


if __name__ == "__main__":
    main()
