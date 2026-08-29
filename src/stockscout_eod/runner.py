"""Portable GitHub runner around the frozen StockScout production engine."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

from stock_scout.config.loader import load_config
from stock_scout.data.cache import ParquetCache
from stock_scout.data.universe import build_universe, load_smoke_universe
from stock_scout.pipeline.orchestrator import PipelineRunner
from stock_scout.utils.dates import history_start
from stockscout_eod.contracts import RawScanEnvelopeV1, wire_dump
from stockscout_eod.fingerprints import engine_versions
from stockscout_eod.jsonio import atomic_write_json, json_compatible

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _frame_last_date(frame) -> date | None:
    """Read a provider frame's last calendar bar without trusting its shape."""
    if frame is None or frame.empty:
        return None
    try:
        index = frame.index
        if getattr(index, "tz", None) is not None:
            index = index.tz_convert("America/New_York").tz_localize(None)
        return index.max().date()
    except (AttributeError, TypeError, ValueError):
        return None


def validate_probe_dates(
    probed: Mapping[str, date | None],
    session_date: date,
    *,
    minimum_fresh_pct: float = 80.0,
) -> None:
    """Fail before the expensive scan when the provider cannot serve one session.

    The full health gate remains authoritative for every published row. This
    bounded probe is an early operator safeguard: it catches a delayed/broken
    feed (including SPY) before 5,000+ per-ticker requests are spent on a run
    that can never activate.
    """
    if not probed:
        raise ValueError("market-session preflight returned no symbols")
    benchmark = probed.get("SPY")
    if benchmark != session_date:
        raise ValueError(
            f"market-session preflight failed: SPY={benchmark or 'missing'} "
            f"expected={session_date}"
        )
    fresh = sum(value == session_date for value in probed.values())
    fresh_pct = 100.0 * fresh / len(probed)
    if fresh_pct < minimum_fresh_pct:
        details = ", ".join(f"{ticker}={value or 'missing'}" for ticker, value in probed.items())
        raise ValueError(
            f"market-session preflight coverage too low: {fresh}/{len(probed)} "
            f"({fresh_pct:.1f}%) expected={session_date}; {details}"
        )


def preflight_session(
    runner: PipelineRunner,
    session_date: date,
    universe: list[str],
    *,
    sample_size: int = 8,
) -> None:
    """Probe the benchmark and a deterministic universe sample before scanning."""
    start = history_start(runner.settings.cache.daily_history_years, session_date)
    tickers = ["SPY", "QQQ", *universe[: max(0, int(sample_size))]]
    probed: dict[str, date | None] = {}
    for ticker in dict.fromkeys(tickers):
        daily, _, _, _ = runner._fetch_with_fallback(ticker, start, session_date)
        probed[ticker] = _frame_last_date(daily)
    validate_probe_dates(probed, session_date)


def _normalized_tickers(values: Iterable[str]) -> list[str]:
    return sorted({str(value).strip().upper() for value in values if str(value).strip()})


def run_scan(
    *,
    config_path: str | Path,
    output_path: str | Path,
    session_date: date,
    run_id: str,
    tickers: Iterable[str] | None = None,
    smoke_universe: str | Path | None = None,
    generated_at: datetime | None = None,
) -> RawScanEnvelopeV1:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id must contain only letters, numbers, dot, underscore, and dash")
    settings = load_config(config_path)
    settings.ai_ranker.enabled = False
    settings.validation.enabled = False
    settings.reports.send_telegram = False
    settings.reports.send_email = False
    settings.automation.enabled = False

    if tickers is not None:
        universe = _normalized_tickers(tickers)
        universe_provenance = "explicit"
        run_mode = "fixture"
    elif smoke_universe is not None:
        universe = load_smoke_universe(smoke_universe)
        universe_provenance = "smoke_fixture"
        run_mode = "fixture"
    else:
        universe, universe_stats = build_universe(settings.universe, settings.project_root)
        universe_provenance = "nasdaq_trader"
        run_mode = "production"
        if not universe:
            raise ValueError(f"public universe is empty: {universe_stats}")

    if not universe:
        raise ValueError("scan universe is empty")

    pipeline = PipelineRunner(settings)
    preflight_session(pipeline, session_date, universe)
    print(
        f"Market-session preflight passed: session={session_date.isoformat()} "
        f"sample={min(len(universe), 8) + 2} symbols"
    )
    result = pipeline.run(universe, as_of=session_date)
    generated = (generated_at or datetime.now(tz=UTC)).astimezone(UTC)
    versions = engine_versions()
    stats = asdict(result.stats)
    candidates = json_compatible(
        [candidate.model_dump(mode="json") for candidate in result.candidates]
    )
    excluded = json_compatible(
        [candidate.model_dump(mode="json") for candidate in result.excluded]
    )
    cache = ParquetCache(settings.project_root / settings.cache.base_dir)
    data_dates: list[date] = []
    fresh_rows = 0
    for row in [*candidates, *excluded]:
        provider = str(row.get("provider_used") or result.stats.primary_provider or "")
        ticker = str(row.get("ticker") or "").strip().upper()
        last_date = cache.last_cached_date(provider, ticker, "daily") if provider and ticker else None
        row["data_last_date"] = last_date.isoformat() if last_date else None
        if last_date is not None:
            data_dates.append(last_date)
            fresh_rows += last_date >= session_date
    published_rows = len(candidates) + len(excluded)
    stats["market_data_latest_bar"] = max(data_dates).isoformat() if data_dates else None
    stats["market_data_oldest_published_bar"] = min(data_dates).isoformat() if data_dates else None
    stats["market_data_fresh_published_pct"] = round(
        100.0 * fresh_rows / max(1, published_rows), 2
    )
    stats["market_data_missing_published_rows"] = published_rows - len(data_dates)

    envelope = RawScanEnvelopeV1(
        runId=run_id,
        sessionDate=session_date.isoformat(),
        generatedAt=generated.isoformat().replace("+00:00", "Z"),
        priceMode=settings.marketdata.price_basis,
        candidates=candidates,
        excluded=excluded,
        stats=json_compatible(stats),
        stageRows=json_compatible(result.stage_rows),
        market={"regime": stats.get("regime") or {}},
        provenance={
            "engineSource": "allowlisted-stockscout-production-snapshot",
            "mode": run_mode,
            "universeSource": universe_provenance,
            "requestedUniverseCount": len(universe),
            "primaryProvider": result.stats.primary_provider,
            "fallbackProvider": result.stats.fallback_provider,
            "legacyConfirmation": "shadow-only",
            "aiRanking": False,
            "marketDataDate": stats["market_data_latest_bar"],
            "weeklyBars": "resampled_split_only_daily",
        },
        versions=versions,
    )
    atomic_write_json(Path(output_path), wire_dump(envelope))
    return envelope


def load_raw_scan(path: str | Path) -> RawScanEnvelopeV1:
    return RawScanEnvelopeV1.model_validate_json(Path(path).read_text(encoding="utf-8"))
