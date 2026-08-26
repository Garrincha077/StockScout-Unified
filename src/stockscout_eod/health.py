"""Activation gates and public-payload safety checks."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from stockscout_eod.contracts import HealthCheckV1, HealthV1, RawScanEnvelopeV1
from stockscout_eod.runtime_export import build_runtime_scan_export

SECRET_KEY_RE = re.compile(
    r"(^|_)(api_?key|secret|password|passwd|token|authorization|cookie|chat_?id)($|_)",
    re.IGNORECASE,
)
ABSOLUTE_PATH_RE = re.compile(r"(?:^[A-Za-z]:\\|^/home/|^/Users/|file://)", re.IGNORECASE)
FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "bars",
        "ohlcv",
        "chart_data",
        "chartdata",
        "chart_payload",
        "chartpayload",
        "private_charts",
        "privatecharts",
        "daily_bars",
        "weekly_bars",
        "provider_cache",
        "duckdb_path",
    }
)


class PublicPayloadError(ValueError):
    """Raised when a value is unsafe for a public Pages artifact."""


def assert_public_safe(value: Any, *, path: str = "$") -> None:
    """Reject credentials, local paths, embedded provider bars, and invalid JSON."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            name = str(key)
            normalized = name.lower().replace("-", "_")
            child = f"{path}.{name}"
            if SECRET_KEY_RE.search(normalized):
                raise PublicPayloadError(f"secret-like key is not public-safe: {child}")
            if normalized in FORBIDDEN_PUBLIC_KEYS:
                raise PublicPayloadError(f"raw/private field is not public-safe: {child}")
            assert_public_safe(nested, path=child)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            assert_public_safe(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and ABSOLUTE_PATH_RE.search(value.strip()):
        raise PublicPayloadError(f"local path is not public-safe: {path}")
    if isinstance(value, float) and not math.isfinite(value):
        raise PublicPayloadError(f"non-finite number is not valid public JSON: {path}")


def _check(code: str, passed: bool, detail: str) -> HealthCheckV1:
    return HealthCheckV1(code=code, passed=passed, detail=detail)


def evaluate_scan_health(
    scan: RawScanEnvelopeV1,
    *,
    min_coverage_pct: float = 90.0,
    min_universe: int = 1000,
    allow_fixture: bool = False,
) -> HealthV1:
    stats = scan.stats
    universe = int(stats.get("universe_size") or stats.get("universe_pre_negcache") or 0)
    coverage = float(stats.get("coverage_pct") or 0.0)
    data_status = str(stats.get("data_status") or "FAILED").upper()
    market_data_date = str(stats.get("market_data_latest_bar") or "")
    fresh_published_pct = float(stats.get("market_data_fresh_published_pct") or 0.0)
    failed = int(stats.get("tickers_failed_all_providers") or 0)
    total_rows = len(scan.candidates) + len(scan.excluded)
    tickers = [str(row.get("ticker") or "").strip().upper() for row in scan.candidates]
    excluded_tickers = [str(row.get("ticker") or "").strip().upper() for row in scan.excluded]
    all_tickers = tickers + excluded_tickers
    as_of_dates = {
        str(row.get("as_of") or row.get("asOf") or "")
        for row in [*scan.candidates, *scan.excluded]
    }

    checks = [
        _check(
            "production_provenance",
            allow_fixture or scan.provenance.get("mode") == "production",
            f"mode={scan.provenance.get('mode') or 'missing'}",
        ),
        _check(
            "provider_provenance",
            bool(scan.provenance.get("primaryProvider")),
            f"primaryProvider={scan.provenance.get('primaryProvider') or 'missing'}",
        ),
        _check(
            "session_date_alignment",
            bool(as_of_dates) and as_of_dates == {scan.session_date},
            f"candidate dates={sorted(as_of_dates)} session={scan.session_date}",
        ),
        _check(
            "market_data_freshness",
            allow_fixture
            or (
                market_data_date == scan.session_date
                and fresh_published_pct >= min_coverage_pct
            ),
            (
                f"latest={market_data_date or 'missing'} session={scan.session_date} "
                f"fresh_published={fresh_published_pct:.2f}%"
            ),
        ),
        _check(
            "engine_data_status",
            data_status == "OK",
            f"engine data_status={data_status}",
        ),
        _check(
            "coverage",
            coverage >= min_coverage_pct,
            f"coverage={coverage:.2f}% required>={min_coverage_pct:.2f}%",
        ),
        _check(
            "universe",
            universe >= min_universe,
            f"universe={universe} required>={min_universe}",
        ),
        _check("rows_present", total_rows > 0, f"candidate/excluded rows={total_rows}"),
        _check(
            "unique_tickers",
            bool(all_tickers)
            and all(bool(ticker) for ticker in all_tickers)
            and len(all_tickers) == len(set(all_tickers)),
            f"unique={len(set(all_tickers))} total={len(all_tickers)}",
        ),
        _check(
            "failed_ticker_budget",
            universe == 0 or failed / universe <= max(0.0, 1.0 - min_coverage_pct / 100.0),
            f"failed={failed} universe={universe}",
        ),
    ]

    try:
        build_runtime_scan_export(scan)
    except PublicPayloadError as exc:
        checks.append(_check("public_payload_safety", False, str(exc)))
    else:
        checks.append(_check("public_payload_safety", True, "no secrets, local paths, or OHLCV"))

    status = "healthy" if all(item.passed for item in checks) else "failed"
    return HealthV1(status=status, coveragePct=coverage, checks=checks)


def require_healthy(health: HealthV1) -> None:
    if health.status == "healthy":
        return
    failures = "; ".join(
        f"{check.code}: {check.detail}" for check in health.checks if not check.passed
    )
    raise ValueError(f"scan failed activation health gate: {failures}")
