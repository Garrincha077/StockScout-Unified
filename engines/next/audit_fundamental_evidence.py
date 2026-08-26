#!/usr/bin/env python3
"""Report Fundamental Evidence coverage/distribution and simple relationships.

This is descriptive only. It does not tune thresholds or alter any ranking.
Spearman correlation is computed as Pearson correlation of average ranks so the
audit stays dependency-light and does not require SciPy.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "frontend" / "public" / "data" / "latest.json"


def spearman_rank_corr(frame: pd.DataFrame, left: str, right: str) -> float | None:
    """Return Spearman rank correlation without SciPy.

    Spearman's rho is Pearson correlation applied to the variables' ranks. Pandas'
    default rank method="average" correctly handles ties. Returning ``None`` for
    insufficient/constant samples keeps this audit descriptive rather than brittle.
    """
    pair = frame[[left, right]].apply(pd.to_numeric, errors="coerce").dropna()
    if len(pair) < 10:
        return None
    left_rank = pair[left].rank(method="average")
    right_rank = pair[right].rank(method="average")
    if left_rank.nunique() < 2 or right_rank.nunique() < 2:
        return None
    value = left_rank.corr(right_rank)
    try:
        value = float(value)
    except Exception:
        return None
    return value if math.isfinite(value) else None


def main() -> None:
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    rows = payload.get("universe") or []
    records = []
    for row in rows:
        score = row.get("fundamentalEvidenceScore")
        if score is None:
            continue
        records.append({
            "ticker": row.get("ticker"),
            "fund": score,
            "confidence": row.get("fundamentalEvidenceConfidence"),
            "coverage": row.get("fundamentalEvidenceCoverage"),
            "opportunity": row.get("opportunityScore"),
            "rsRank": row.get("rsRank"),
            "stage": row.get("stage"),
            "label": row.get("fundamentalEvidenceLabel"),
        })

    df = pd.DataFrame(records)
    print("=== Fundamental Evidence audit ===")
    print(f"Scored: {len(df):,}/{len(rows):,} ({(len(df)/len(rows)*100 if rows else 0):.1f}%)")
    if df.empty:
        return

    for col in ("fund", "confidence", "coverage", "opportunity", "rsRank"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    print("Score quantiles:", df["fund"].dropna().quantile([0, .1, .25, .5, .75, .9, 1]).round(1).to_dict())
    print("Confidence quantiles:", df["confidence"].dropna().quantile([0, .25, .5, .75, 1]).round(1).to_dict())
    print("Labels:", df["label"].value_counts(dropna=False).to_dict())
    for other in ("opportunity", "rsRank"):
        corr = spearman_rank_corr(df, "fund", other)
        print(f"Spearman fund vs {other}: {corr:.3f}" if corr is not None else f"Spearman fund vs {other}: n/a")

    print("Top evidence (score, confidence):")
    top = df.sort_values(["fund", "confidence"], ascending=False, na_position="last").head(20)
    for _, row in top.iterrows():
        fund = row.get("fund")
        confidence = row.get("confidence")
        fund_text = f"{fund:.1f}" if pd.notna(fund) else "n/a"
        confidence_text = f"{confidence:.1f}%" if pd.notna(confidence) else "n/a"
        print(
            f"  {row.get('ticker')}: {fund_text} / conf {confidence_text} / "
            f"stage {row.get('stage')} / opp {row.get('opportunity')}"
        )


if __name__ == "__main__":
    main()
