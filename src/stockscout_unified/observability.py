"""Compact scanner diagnostics for GitHub Actions job summaries.

These helpers are deliberately read-only: they summarize already-produced
Bottom/Next diagnostics and never decide whether an artifact is deployable.
Quality gates remain in the scanner/publisher paths.
"""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _duration(started_at: float | None) -> float | None:
    if started_at is None or started_at <= 0:
        return None
    return round(max(0.0, time.time() - started_at), 1)


def _top_items(value: Any, limit: int = 5) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    pairs: list[tuple[str, int]] = []
    for key, raw_count in value.items():
        count = _integer(raw_count, -1)
        if count >= 0:
            pairs.append((str(key), count))
    pairs.sort(key=lambda item: (-item[1], item[0]))
    return [{"name": name, "count": count} for name, count in pairs[:limit]]


def _matching_numeric_sum(mapping: Mapping[str, Any], needles: Sequence[str]) -> int | None:
    matched = False
    total = 0
    for key, raw_value in mapping.items():
        lowered = str(key).lower()
        if not any(needle in lowered for needle in needles):
            continue
        if isinstance(raw_value, bool):
            continue
        try:
            total += int(raw_value)
            matched = True
        except (TypeError, ValueError):
            continue
    return total if matched else None


def build_bottom_summary(path: str | Path, *, started_at: float | None = None) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Bottom scan is not a JSON object")
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    provenance = (
        payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
    )
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    excluded = payload.get("excluded") if isinstance(payload.get("excluded"), list) else []
    published = len(candidates) + len(excluded)
    universe = _integer(
        stats.get("universe_size")
        or provenance.get("requestedUniverseCount")
        or published
    )
    skipped = _integer(stats.get("skipped_count") or stats.get("skipped") or 0)
    explicit_failed = stats.get("tickers_failed_all_providers")
    failed = (
        _integer(explicit_failed)
        if explicit_failed is not None
        else max(0, universe - published - skipped)
    )
    coverage = stats.get("coverage_pct")
    if coverage is None:
        coverage = round(100.0 * published / max(1, universe), 2)

    errors = (
        stats.get("error_types")
        or stats.get("errors_by_type")
        or stats.get("failure_types")
        or {}
    )
    retries = _matching_numeric_sum(stats, ("retry", "retries"))
    rate_limits = _matching_numeric_sum(stats, ("rate_limit", "ratelimit", "429"))
    return {
        "schema": "stockscout-eod-summary/v1",
        "mode": "bottom",
        "status": str(stats.get("data_status") or "available"),
        "sessionDate": str(payload.get("sessionDate") or ""),
        "durationSeconds": _duration(started_at),
        "universeCount": universe,
        "successCount": published,
        "skippedCount": skipped,
        "failedCount": failed,
        "coveragePct": round(_number(coverage), 2),
        "freshMarketDataPct": stats.get("market_data_fresh_published_pct"),
        "missingMarketDataRows": stats.get("market_data_missing_published_rows"),
        "retryCount": retries,
        "rateLimitCount": rate_limits,
        "topErrorClasses": _top_items(errors),
    }


def build_next_summary(path: str | Path, *, started_at: float | None = None) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Next metrics are not a JSON object")
    summary = dict(payload)
    summary.setdefault("schema", "stockscout-eod-summary/v1")
    summary["mode"] = "next"
    summary["durationSeconds"] = _duration(started_at)
    return summary


def unavailable_summary(mode: str, reason: str, *, started_at: float | None = None) -> dict[str, Any]:
    return {
        "schema": "stockscout-eod-summary/v1",
        "mode": mode,
        "status": "unavailable",
        "reason": reason,
        "durationSeconds": _duration(started_at),
    }


def _fmt(value: Any, *, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def render_markdown(summary: Mapping[str, Any]) -> str:
    title = "Bottom" if summary.get("mode") == "bottom" else "Next"
    lines = [f"### {title} scanner summary", "", "| Metric | Value |", "| --- | ---: |"]
    metrics = (
        ("Status", summary.get("status", "available"), ""),
        ("Session", summary.get("sessionDate") or "n/a", ""),
        ("Duration", summary.get("durationSeconds"), " s"),
        ("Universe", summary.get("universeCount"), ""),
        ("Succeeded/analyzed", summary.get("successCount"), ""),
        ("Skipped/filtered", summary.get("skippedCount"), ""),
        ("Failed", summary.get("failedCount"), ""),
        ("Coverage", summary.get("coveragePct"), "%"),
        ("Retries", summary.get("retryCount"), ""),
        ("Rate-limit errors", summary.get("rateLimitCount"), ""),
    )
    for label, value, suffix in metrics:
        lines.append(f"| {label} | {_fmt(value, suffix=suffix)} |")
    if summary.get("freshMarketDataPct") is not None:
        lines.append(
            f"| Fresh market data | {_fmt(summary.get('freshMarketDataPct'), suffix='%')} |"
        )
    if summary.get("resumeCheckpointUsed") is not None:
        lines.append(
            f"| Resume checkpoint | {'used' if summary.get('resumeCheckpointUsed') else 'not used'} |"
        )
    top_errors = summary.get("topErrorClasses") or []
    if top_errors:
        details = ", ".join(
            f"{item.get('name')}={item.get('count')}" for item in top_errors if isinstance(item, dict)
        )
        lines.extend(["", f"Top error classes: {details or 'n/a'}"])
    if summary.get("reason"):
        lines.extend(["", f"Diagnostics unavailable: {summary['reason']}"])
    return "\n".join(lines) + "\n"


def write_summary(
    summary: Mapping[str, Any],
    *,
    output: str | Path,
    github_summary: str | Path | None = None,
) -> None:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if github_summary:
        summary_path = Path(github_summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("a", encoding="utf-8") as handle:
            handle.write(render_markdown(summary))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a compact EOD scanner summary")
    parser.add_argument("mode", choices=("bottom", "next"))
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-summary")
    parser.add_argument("--started-at", type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "bottom":
            summary = build_bottom_summary(args.input, started_at=args.started_at)
        else:
            summary = build_next_summary(args.input, started_at=args.started_at)
    except Exception as exc:  # diagnostics must never mask the scanner's real status
        summary = unavailable_summary(args.mode, str(exc), started_at=args.started_at)
    write_summary(summary, output=args.output, github_summary=args.github_summary)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
