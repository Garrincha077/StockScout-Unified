#!/usr/bin/env python3
"""Attach StockScout Fundamental Evidence v1 to the canonical frontend dataset.

Runs after rich-layer enrichment. This script is intentionally non-invasive:
it never changes Opportunity, Confluence, setup tags, LEGACY outputs, or technical
scores. It only adds evidence fields derived from already-hydrated fundamentals.

Because this is the final enrichment step in the nightly pipeline, it also runs
the sortable-field data-quality gate before the dataset can be published.
"""
from __future__ import annotations

import json
from pathlib import Path

from audit_sortable_coverage import audit as audit_sortable_coverage
from fundamental_evidence import MODEL, score_fundamentals

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "frontend" / "public" / "data" / "latest.json"

EXTENDED_FLAT_FIELDS = {
    "operatingCashFlowYoY": "operating_cash_flow_yoy_change",
    "freeCashFlowYoY": "free_cash_flow_yoy_change",
    "freeCashFlowMargin": "free_cash_flow_margin",
    "totalDebtYoY": "total_debt_yoy_change",
    "netDebt": "net_debt_latest",
    "shareDilutionYoY": "share_dilution_yoy_change",
}


def main() -> None:
    if not OUT.exists():
        raise SystemExit(f"Missing canonical dataset: {OUT}")

    payload = json.loads(OUT.read_text(encoding="utf-8"))
    universe = payload.get("universe") or []
    if not universe:
        raise SystemExit("Canonical dataset has no universe rows")

    scored = high_conf = strong = supportive = mixed = weak = 0
    extended_counts = {field: 0 for field in EXTENDED_FLAT_FIELDS}
    source_counts: dict[str, int] = {}

    for row in universe:
        before_opp = row.get("opportunityScore")
        before_conf = row.get("confluence")

        rich = row.get("richData") if isinstance(row.get("richData"), dict) else {}
        fundamentals = rich.get("fundamentals") if isinstance(rich.get("fundamentals"), dict) else {}
        age = rich.get("fundamentalsAgeDays")
        try:
            age = int(age) if age is not None else None
        except Exception:
            age = None

        evidence = score_fundamentals(fundamentals, age)
        rich["fundamentalEvidence"] = evidence
        row["richData"] = rich

        stockscout = row.get("stockscout") if isinstance(row.get("stockscout"), dict) else {}
        stockscout["fundamentalEvidence"] = {
            "model": evidence["model"],
            "score": evidence["score"],
            "label": evidence["label"],
            "coveragePct": evidence["coveragePct"],
            "confidencePct": evidence["confidencePct"],
            "confidence": evidence["confidence"],
            "groupScores": evidence["groupScores"],
            "affectsOpportunity": False,
        }
        row["stockscout"] = stockscout

        groups = evidence.get("groupScores") or {}
        row["fundamentalEvidenceScore"] = evidence["score"]
        row["fundamentalEvidenceCoverage"] = evidence["coveragePct"]
        row["fundamentalEvidenceConfidence"] = evidence["confidencePct"]
        row["fundamentalEvidenceLabel"] = evidence["label"]
        row["fundamentalGrowthScore"] = groups.get("growth")
        row["fundamentalMarginScore"] = groups.get("margins")
        row["fundamentalInventoryScore"] = groups.get("inventory")

        # Additive-only projection of unused upstream yfinance statements. These
        # fields are sortable evidence but do not participate in Evidence v1 or
        # any existing StockScout/LEGACY score.
        for flat_name, raw_name in EXTENDED_FLAT_FIELDS.items():
            value = fundamentals.get(raw_name)
            row[flat_name] = value
            if value is not None:
                extended_counts[flat_name] += 1
        source = fundamentals.get("fundamental_data_source")
        row["fundamentalDataSource"] = source
        if source:
            source_counts[str(source)] = source_counts.get(str(source), 0) + 1

        if evidence["score"] is not None:
            scored += 1
            label = evidence["label"].lower()
            if label == "strong":
                strong += 1
            elif label == "supportive":
                supportive += 1
            elif label == "mixed":
                mixed += 1
            elif label == "weak":
                weak += 1
        if evidence["confidencePct"] >= 70:
            high_conf += 1

        if row.get("opportunityScore") != before_opp or row.get("confluence") != before_conf:
            raise RuntimeError(f"Evidence model mutated ranking fields for {row.get('ticker')}")

    payload["fundamentalEvidenceModel"] = MODEL
    market = payload.setdefault("market", {})
    market["fundamentalEvidence"] = {
        "model": MODEL,
        "rows": len(universe),
        "scored": scored,
        "coveragePct": round(scored / len(universe) * 100.0, 1) if universe else 0,
        "highConfidence": high_conf,
        "labels": {"strong": strong, "supportive": supportive, "mixed": mixed, "weak": weak},
        "affectsOpportunity": False,
    }
    market["extendedFundamentals"] = {
        "model": "upstream-yfinance-additive-v1",
        "affectsOpportunity": False,
        "affectsFundamentalEvidenceV1": False,
        "sources": source_counts,
        "coveragePct": {
            field: round(count / len(universe) * 100.0, 1) if universe else 0
            for field, count in extended_counts.items()
        },
    }

    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    print(
        f"Fundamental Evidence ready: scored={scored:,}/{len(universe):,}; "
        f"high-confidence={high_conf:,}; strong={strong:,}; supportive={supportive:,}; "
        f"mixed={mixed:,}; weak={weak:,}; Opportunity unchanged"
    )
    print("Extended upstream fundamentals coverage:", market["extendedFundamentals"]["coveragePct"])

    # Final publish-quality contract. This writes market.dataQuality and exits
    # non-zero when core sortable technical coverage materially regresses.
    audit_sortable_coverage(dataset_path=OUT, fail_on_gate=True)


if __name__ == "__main__":
    main()
