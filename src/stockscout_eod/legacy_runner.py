"""Run Ryan/LEGACY analysis as a deterministic shadow sidecar."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from stock_scout.config.loader import load_config
from stock_scout.data.cache import ParquetCache
from stockscout_eod.contracts import RawScanEnvelopeV1
from stockscout_eod.jsonio import atomic_write_json
from stockscout_eod.vendor.ryan_phase import (
    calculate_sma,
    classify_phase,
    validate_minervini_trend_template,
)

RYAN_SOURCE_COMMIT = "c2737ffa2e22409f40de955f519c40079826ecaf"


def _provider_order(row: dict[str, Any], settings: Any) -> list[str]:
    values = [
        row.get("provider_used"),
        settings.providers.primary_data_provider,
        settings.providers.fallback_provider,
        settings.providers.tertiary_fallback_provider,
        settings.providers.deep_history_provider,
    ]
    return list(dict.fromkeys(str(value) for value in values if value))


def _daily_bars(
    cache: ParquetCache,
    providers: list[str],
    ticker: str,
    session_date: date,
) -> tuple[pd.DataFrame, str | None]:
    for provider in providers:
        frame = cache.read(provider, ticker, "daily")
        if frame.empty:
            continue
        normalized = frame.rename(
            columns={
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        ).sort_index()
        required = {"Open", "High", "Low", "Close", "Volume"}
        if not required.issubset(normalized.columns):
            continue
        normalized = normalized.loc[normalized.index.date <= session_date, sorted(required)]
        if not normalized.empty:
            return normalized, provider
    return pd.DataFrame(), None


def evaluate_ryan_confirmation(
    ticker: str,
    bars: pd.DataFrame,
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """Map frozen Ryan phase/template evidence to a read-only confirmation."""

    if bars.empty or len(bars) < 200:
        return {
            "ticker": ticker,
            "status": "UNAVAILABLE",
            "available": False,
            "sourceModel": "ryan-phase-minervini-shadow-v1",
            "affectsRanking": False,
            "reasons": ["INSUFFICIENT_DAILY_BARS"],
            "evidence": {"barCount": len(bars), "provider": provider},
        }
    current_price = float(bars["Close"].iloc[-1])
    phase = classify_phase(bars, current_price)
    template = validate_minervini_trend_template(
        current_price,
        phase,
        calculate_sma(bars["Close"], 200),
    )
    phase_number = int(phase.get("phase") or 0)
    criteria = int(template.get("criteria_passed") or 0)
    if phase_number == 2 and template["passes_template"]:
        status = "CONFIRMED"
    elif phase_number in {3, 4}:
        status = "RISK"
    elif phase_number == 2:
        status = "CONFLICT"
    elif phase_number == 1 and criteria >= 6:
        status = "EARLY"
    else:
        status = "NEUTRAL"
    return {
        "ticker": ticker,
        "status": status,
        "available": True,
        "sourceModel": "ryan-phase-minervini-shadow-v1",
        "affectsRanking": False,
        "reasons": [f"RYAN_PHASE_{phase_number}", f"MINERVINI_{criteria}_OF_8"],
        "evidence": {
            "provider": provider,
            "barCount": len(bars),
            "phase": phase_number,
            "phaseName": phase.get("phase_name"),
            "phaseConfidence": phase.get("confidence"),
            "templatePasses": template.get("passes_template"),
            "templateScore": template.get("template_score"),
            "criteriaPassed": criteria,
            "criteriaTotal": 8,
            "criteria": template.get("criteria_details"),
        },
    }


def run_legacy_shadow(
    scan: RawScanEnvelopeV1,
    *,
    config_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    settings = load_config(config_path)
    cache = ParquetCache(settings.project_root / settings.cache.base_dir)
    session = date.fromisoformat(scan.session_date)
    rows: dict[str, Any] = {}
    for source in [*scan.candidates, *scan.excluded]:
        ticker = str(source.get("ticker") or "").strip().upper()
        bars, provider = _daily_bars(
            cache,
            _provider_order(source, settings),
            ticker,
            session,
        )
        rows[ticker] = evaluate_ryan_confirmation(ticker, bars, provider=provider)
    result = {
        "schemaVersion": "stockscout-eod/legacy-shadow-v1",
        "runId": scan.run_id,
        "sessionDate": scan.session_date,
        "generatedAt": scan.generated_at,
        "affectsRanking": False,
        "source": {
            "repository": "RyanJHamby/stock-screener",
            "commit": RYAN_SOURCE_COMMIT,
            "license": "MIT",
            "adapter": "ryan-phase-minervini-shadow-v1",
        },
        "candidates": rows,
    }
    atomic_write_json(Path(output_path), result)
    return result
