#!/usr/bin/env python3
"""Transparent StockScout fundamental evidence model.

This model is intentionally separate from Opportunity/Confluence scoring. It turns
already-hydrated quarterly fundamentals into a comparable 0-100 evidence score,
while reporting coverage and freshness confidence separately.

Design goals:
- YoY growth carries most of the weight; sequential QoQ is deliberately light
  because seasonality can distort it.
- Missing metrics are not treated as zero. The score is normalized across the
  evidence that exists and coverage/confidence tell the user how much to trust it.
- Inventory is scored relative to revenue growth, not by absolute inventory/sales,
  because inventory intensity is industry-dependent.
- Data age affects confidence, never the company's fundamental-quality score.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

MODEL = "stockscout-fundamental-evidence-v1"
MIN_WEIGHT_FOR_SCORE = 25.0


def finite(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def interpolate(value: float, points: Iterable[tuple[float, float]]) -> float:
    pts = list(points)
    if value <= pts[0][0]:
        return pts[0][1]
    if value >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            t = (value - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return pts[-1][1]


def freshness_factor(age_days: int | None) -> float:
    if age_days is None:
        return 0.50
    if age_days <= 3:
        return 1.00
    if age_days <= 7:
        return 0.90
    if age_days <= 14:
        return 0.75
    if age_days <= 30:
        return 0.50
    return 0.25


def score_label(score: float | None) -> str:
    if score is None:
        return "INSUFFICIENT"
    if score >= 75:
        return "STRONG"
    if score >= 60:
        return "SUPPORTIVE"
    if score >= 40:
        return "MIXED"
    return "WEAK"


def confidence_label(confidence: float) -> str:
    if confidence >= 70:
        return "HIGH"
    if confidence >= 45:
        return "MEDIUM"
    return "LOW"


def score_fundamentals(data: dict[str, Any] | None, age_days: int | None = None) -> dict[str, Any]:
    data = data or {}
    components: list[dict[str, Any]] = []

    def add(metric: str, raw: Any, weight: float, curve: list[tuple[float, float]], note: str) -> None:
        value = finite(raw)
        if value is None:
            return
        metric_score = max(0.0, min(100.0, interpolate(value, curve)))
        components.append({
            "metric": metric,
            "value": round(value, 2),
            "score": round(metric_score, 1),
            "weight": weight,
            "weightedPoints": round(metric_score * weight / 100.0, 2),
            "note": note,
        })

    # Growth: 60% of the full evidence model. YoY dominates to limit seasonality noise.
    add("revenueYoY", data.get("revenue_yoy_change"), 25.0,
        [(-30, 0), (-10, 15), (0, 35), (10, 55), (20, 75), (40, 95), (60, 100)],
        "YoY revenue growth")
    add("epsYoY", data.get("eps_yoy_change"), 25.0,
        [(-60, 0), (-20, 15), (0, 35), (15, 60), (30, 80), (60, 100)],
        "YoY EPS growth")
    add("revenueQoQ", data.get("revenue_qoq_change"), 5.0,
        [(-25, 0), (-10, 20), (0, 45), (10, 70), (20, 90), (35, 100)],
        "Sequential revenue; intentionally low weight")
    add("epsQoQ", data.get("eps_qoq_change"), 5.0,
        [(-60, 0), (-20, 20), (0, 45), (20, 70), (50, 90), (100, 100)],
        "Sequential EPS; intentionally low weight")

    # Margin/profitability: absolute operating margin is useful but industry-dependent,
    # so margin direction receives more weight than margin level.
    add("marginChange", data.get("margin_change"), 15.0,
        [(-6, 0), (-3, 15), (-1, 35), (0, 50), (1, 65), (2, 80), (4, 100)],
        "Gross-margin change in percentage points QoQ")
    add("operatingMargin", data.get("operating_margin"), 10.0,
        [(-15, 0), (0, 30), (5, 45), (10, 60), (20, 80), (30, 95), (40, 100)],
        "Current operating profitability")

    # Inventory discipline: compare inventory growth with revenue growth. This is much
    # less sector-biased than punishing a high absolute inventory/sales ratio.
    inv = finite(data.get("inventory_qoq_change"))
    rev_qoq = finite(data.get("revenue_qoq_change"))
    if inv is not None and rev_qoq is not None:
        spread = inv - rev_qoq
        inv_score = max(0.0, min(100.0, interpolate(spread, [
            (-20, 100), (-10, 92), (-5, 82), (0, 70), (5, 50), (10, 30), (20, 10), (30, 0),
        ])))
        components.append({
            "metric": "inventoryDiscipline",
            "value": round(spread, 2),
            "score": round(inv_score, 1),
            "weight": 15.0,
            "weightedPoints": round(inv_score * 0.15, 2),
            "note": "Inventory QoQ minus revenue QoQ; lower is generally healthier",
            "inventoryQoQ": round(inv, 2),
            "revenueQoQ": round(rev_qoq, 2),
            "inventoryToSales": finite(data.get("inventory_to_sales_ratio")),
        })

    available_weight = sum(float(c["weight"]) for c in components)
    weighted_points = sum(float(c["weightedPoints"]) for c in components)
    score = None if available_weight < MIN_WEIGHT_FOR_SCORE else round(weighted_points / available_weight * 100.0, 1)
    coverage = round(min(100.0, available_weight), 1)
    confidence = round(coverage * freshness_factor(age_days), 1)

    groups = {
        "growth": ["revenueYoY", "epsYoY", "revenueQoQ", "epsQoQ"],
        "margins": ["marginChange", "operatingMargin"],
        "inventory": ["inventoryDiscipline"],
    }
    group_scores: dict[str, float | None] = {}
    for name, metrics in groups.items():
        subset = [c for c in components if c["metric"] in metrics]
        weight = sum(float(c["weight"]) for c in subset)
        points = sum(float(c["weightedPoints"]) for c in subset)
        group_scores[name] = round(points / weight * 100.0, 1) if weight else None

    positives = sorted(components, key=lambda c: float(c["score"]), reverse=True)[:2]
    risks = sorted(components, key=lambda c: float(c["score"]))[:2]

    return {
        "model": MODEL,
        "score": score,
        "label": score_label(score),
        "coveragePct": coverage,
        "confidencePct": confidence,
        "confidence": confidence_label(confidence),
        "ageDays": age_days,
        "freshnessFactor": freshness_factor(age_days),
        "availableWeight": round(available_weight, 1),
        "components": components,
        "groupScores": group_scores,
        "positives": [{"metric": c["metric"], "score": c["score"], "value": c["value"]} for c in positives],
        "risks": [{"metric": c["metric"], "score": c["score"], "value": c["value"]} for c in risks],
        "affectsOpportunity": False,
        "inventoryToSales": finite(data.get("inventory_to_sales_ratio")),
    }
