#!/usr/bin/env python3
"""Refresh the read-only GMLI context sidecar consumed by StockScout Next.

This module never reconstructs GMLI methodology. It only validates and projects
published GMLI outputs from Garrincha077/NUEVO into a compact frontend sidecar.
A failed upstream refresh may fall back to the last checked-in valid sidecar.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

SOURCE_REPOSITORY = "Garrincha077/NUEVO"
SOURCE_REF = "gh-pages"
SOURCE_BASE = f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{SOURCE_REF}/api"
SOURCE_FILES = {
    "report": "report.json",
    "money_extremes": "money-extremes.json",
    "context_history": "context-history.json",
    "refresh_status": "refresh-status.json",
}
OUTPUT_SCHEMA = 1
STOCKSCOUT_IMPACT = "none; read-only independent macro context"
MAX_HISTORY_MONTHS = 120


class GmliContextError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GmliContextError(message)


def _last(rows: Any, label: str) -> dict[str, Any]:
    _require(isinstance(rows, list) and rows, f"{label} history is empty")
    row = rows[-1]
    _require(isinstance(row, dict), f"{label} latest row is invalid")
    return row


def _find_dict_with_key(root: Any, key: str) -> dict[str, Any] | None:
    if isinstance(root, dict):
        if key in root:
            return root
        for value in root.values():
            found = _find_dict_with_key(value, key)
            if found is not None:
                return found
    elif isinstance(root, list):
        for value in root:
            found = _find_dict_with_key(value, key)
            if found is not None:
                return found
    return None


def _fetch_json(url: str, timeout: float) -> tuple[dict[str, Any], str]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "StockScout-next-gmli-context/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        status = getattr(response, "status", 200)
        _require(status == 200, f"HTTP {status} for {url}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive network boundary
        raise GmliContextError(f"Invalid JSON from {url}: {exc}") from exc
    _require(isinstance(payload, dict), f"Expected JSON object from {url}")
    return payload, hashlib.sha256(raw).hexdigest()


def fetch_source_bundle(timeout: float = 25.0) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    payloads: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for key, filename in SOURCE_FILES.items():
        payload, digest = _fetch_json(f"{SOURCE_BASE}/{filename}", timeout)
        payloads[key] = payload
        hashes[filename] = digest
    return payloads, hashes


def _trim(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return rows[-MAX_HISTORY_MONTHS:]


def build_context(
    report: dict[str, Any],
    money_extremes: dict[str, Any],
    context_history: dict[str, Any],
    refresh_status: dict[str, Any],
    source_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    report_schema = str(report.get("schema_version") or "")
    _require(report_schema.startswith("gmli-report-v1."), f"Unsupported GMLI report schema: {report_schema!r}")
    _require(report.get("generated_at"), "GMLI report has no generated_at")

    engine_fact = ((report.get("regime") or {}).get("engine_fact") or {})
    money = engine_fact.get("money") or {}
    _require(money.get("version"), "GMLI active Money Core is missing")
    _require(money.get("observation_month"), "GMLI Money observation month is missing")
    _require(money.get("available_date"), "GMLI Money available date is missing")

    inference = ((report.get("regime") or {}).get("current_research_inference") or {})
    _require(inference.get("label"), "GMLI inference label is missing")

    taxonomy = report.get("signal_role_taxonomy") or {}
    _require(taxonomy.get("money_core"), "GMLI signal-role taxonomy is missing Money Core")
    _require(taxonomy.get("funding_v2"), "GMLI signal-role taxonomy is missing Funding V2")
    _require(taxonomy.get("fiscal_v2"), "GMLI signal-role taxonomy is missing Fiscal V2")
    _require(taxonomy.get("market_confirmation"), "GMLI signal-role taxonomy is missing market confirmation")

    _require(money_extremes.get("schema_version") == "gmli-money-extremes-v1", "Unsupported Money Extremes schema")
    _require(money_extremes.get("scoring_effect") == "NONE", "Money Extremes unexpectedly affects GMLI scoring")
    extremes_latest = money_extremes.get("latest") or {}
    _require(extremes_latest.get("month") == money.get("observation_month"), "Money Extremes vintage differs from active Money Core")

    _require(context_history.get("schema_version") == "gmli-pages-context-history-v1", "Unsupported GMLI context-history schema")
    _require(context_history.get("scoring_effect") == "NONE", "Context history unexpectedly affects GMLI scoring")
    funding_history = context_history.get("funding") or {}
    fiscal_history = context_history.get("fiscal") or {}
    market_history = context_history.get("market_confirmation") or {}
    funding_rows = funding_history.get("rows") or []
    fiscal_rows = fiscal_history.get("rows") or []
    market_rows = market_history.get("rows") or []
    funding_latest = _last(funding_rows, "Funding")
    fiscal_latest = _last(fiscal_rows, "Fiscal")
    market_latest = _last(market_rows, "Market confirmation")
    _require(
        funding_latest.get("available_date") == funding_history.get("active_available_date"),
        "Funding history latest row does not match active available date",
    )
    _require(
        fiscal_latest.get("available_date") == fiscal_history.get("active_available_date"),
        "Fiscal history latest row does not match active available date",
    )
    _require(
        market_latest.get("month") == market_history.get("cutoff_month"),
        "Market confirmation history latest row does not match cutoff month",
    )

    refresh_schema = str(refresh_status.get("schema_version") or "")
    _require(refresh_schema == "gmli-pages-refresh-v1", f"Unsupported GMLI refresh-status schema: {refresh_schema!r}")
    upstream_refresh_status = str(refresh_status.get("status") or "UNKNOWN")
    _require(
        upstream_refresh_status in {"PASS_FETCH_FIRST", "PASS_WITH_LAST_GOOD_FALLBACK"},
        f"GMLI upstream refresh is not publishable: {upstream_refresh_status}",
    )

    fiscal_weight_holder = _find_dict_with_key(report, "automatic_global_conviction_weight") or {}
    fiscal_weight = fiscal_weight_holder.get("automatic_global_conviction_weight")

    role_projection = {
        "version": taxonomy.get("version"),
        "scoringEffect": taxonomy.get("scoring_effect"),
        "money": taxonomy.get("money_core"),
        "funding": taxonomy.get("funding_v2"),
        "fiscal": taxonomy.get("fiscal_v2"),
        "market": taxonomy.get("market_confirmation"),
    }

    context = {
        "schemaVersion": OUTPUT_SCHEMA,
        "status": "OK",
        "generatedAt": report.get("generated_at"),
        "source": {
            "repository": SOURCE_REPOSITORY,
            "ref": SOURCE_REF,
            "baseUrl": SOURCE_BASE,
            "reportSchema": report_schema,
            "upstreamRefreshStatus": upstream_refresh_status,
            "upstreamRefreshPolicy": refresh_status.get("policy"),
            "hashes": dict(sorted((source_hashes or {}).items())),
        },
        "stockScoutImpact": STOCKSCOUT_IMPACT,
        "consumerContract": {
            "mode": "READ_ONLY_SIDECAR",
            "reconstructsGmli": False,
            "mutatesStockScoutScoring": False,
            "lastGoodFallbackAllowed": True,
            "canonicalMethodologyRepository": SOURCE_REPOSITORY,
        },
        "dataHealth": report.get("data_health") or {},
        "regime": {
            "label": inference.get("label"),
            "tilt": inference.get("tilt"),
            "provisional": inference.get("provisional"),
            "money": {
                "version": money.get("version"),
                "observationMonth": money.get("observation_month"),
                "availableDate": money.get("available_date"),
                "freshness": money.get("freshness"),
                "usdYoYPct": money.get("usd_yoy_pct"),
                "usdScore": money.get("usd_score"),
                "usdRegime": money.get("usd_regime"),
                "fxNeutralYoYPct": money.get("fx_neutral_yoy_pct"),
                "fxNeutralScore": money.get("fx_neutral_score"),
                "fxNeutralRegime": money.get("fx_neutral_regime"),
                "agreement": money.get("agreement"),
            },
            "funding": {
                "version": funding_history.get("version"),
                "role": funding_history.get("role"),
                "observationMonth": funding_latest.get("observation_month"),
                "availableDate": funding_latest.get("available_date"),
                "score": funding_latest.get("score"),
                "regime": funding_latest.get("regime"),
                "structuralSupportScore": funding_latest.get("structural_support_score"),
                "observedConditionsScore": funding_latest.get("observed_conditions_score"),
            },
            "fiscal": {
                "version": fiscal_history.get("version"),
                "role": fiscal_history.get("role"),
                "observationMonth": fiscal_latest.get("observation_month"),
                "availableDate": fiscal_latest.get("available_date"),
                "score": fiscal_latest.get("score"),
                "regime": fiscal_latest.get("regime"),
                "deficitPctGdp": fiscal_latest.get("deficit_pct_gdp"),
                "fiscalImpulsePp": fiscal_latest.get("fiscal_impulse_pp"),
                "automaticGlobalConvictionWeight": fiscal_weight,
            },
            "market": {
                "role": market_history.get("role"),
                "month": market_latest.get("month"),
                "positive": market_latest.get("positive"),
                "total": market_latest.get("total"),
                "score0To2": market_latest.get("score_0_2"),
                "assetsPositive": market_latest.get("assets_positive") or {},
            },
        },
        "signalRoles": role_projection,
        "moneyExtremes": {
            "version": money_extremes.get("version"),
            "evidenceTier": money_extremes.get("evidence_tier"),
            "scoringEffect": money_extremes.get("scoring_effect"),
            "construction": money_extremes.get("construction") or {},
            "latest": extremes_latest,
            "rows": _trim(money_extremes.get("rows") or []),
        },
        "history": {
            "windowMonths": MAX_HISTORY_MONTHS,
            "funding": _trim(funding_rows),
            "fiscal": _trim(fiscal_rows),
            "market": _trim(market_rows),
        },
    }
    validate_context(context)
    return context


def validate_context(context: dict[str, Any]) -> None:
    _require(context.get("schemaVersion") == OUTPUT_SCHEMA, "Invalid StockScout GMLI sidecar schema")
    _require(context.get("stockScoutImpact") == STOCKSCOUT_IMPACT, "GMLI sidecar may not affect StockScout scoring")
    contract = context.get("consumerContract") or {}
    _require(contract.get("mode") == "READ_ONLY_SIDECAR", "GMLI consumer must remain read-only")
    _require(contract.get("reconstructsGmli") is False, "StockScout may not reconstruct GMLI")
    _require(contract.get("mutatesStockScoutScoring") is False, "GMLI sidecar may not mutate StockScout scoring")
    _require((context.get("regime") or {}).get("money"), "GMLI sidecar has no Money context")
    _require((context.get("regime") or {}).get("funding"), "GMLI sidecar has no Funding context")
    _require((context.get("regime") or {}).get("fiscal"), "GMLI sidecar has no Fiscal context")
    _require((context.get("regime") or {}).get("market"), "GMLI sidecar has no Market context")


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def refresh(output: Path, timeout: float, allow_last_good: bool) -> dict[str, Any]:
    try:
        payloads, hashes = fetch_source_bundle(timeout=timeout)
        context = build_context(
            payloads["report"],
            payloads["money_extremes"],
            payloads["context_history"],
            payloads["refresh_status"],
            source_hashes=hashes,
        )
        write_atomic(output, context)
        return {
            "status": "REFRESH_OK",
            "output": str(output),
            "sourceGeneratedAt": context.get("generatedAt"),
            "moneyAvailableDate": ((context.get("regime") or {}).get("money") or {}).get("availableDate"),
            "fundingAvailableDate": ((context.get("regime") or {}).get("funding") or {}).get("availableDate"),
            "fiscalAvailableDate": ((context.get("regime") or {}).get("fiscal") or {}).get("availableDate"),
            "stockScoutImpact": context.get("stockScoutImpact"),
        }
    except Exception as exc:
        if allow_last_good and output.exists():
            try:
                last_good = json.loads(output.read_text(encoding="utf-8"))
                validate_context(last_good)
                return {
                    "status": "LAST_GOOD_FALLBACK",
                    "output": str(output),
                    "error": str(exc),
                    "sourceGeneratedAt": last_good.get("generatedAt"),
                    "stockScoutImpact": last_good.get("stockScoutImpact"),
                }
            except Exception as fallback_exc:
                raise GmliContextError(f"Upstream refresh failed ({exc}); last-good is invalid ({fallback_exc})") from fallback_exc
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="frontend/public/data/gmli/gmli-context.json")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--allow-last-good", action="store_true")
    parser.add_argument("--validate-existing", action="store_true")
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        if args.validate_existing:
            payload = json.loads(output.read_text(encoding="utf-8"))
            validate_context(payload)
            result = {"status": "PASS_EXISTING_GMLI_CONTEXT", "output": str(output)}
        else:
            result = refresh(output, args.timeout, args.allow_last_good)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAIL_GMLI_CONTEXT", "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
