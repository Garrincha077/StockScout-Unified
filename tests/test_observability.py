import json
from pathlib import Path

from stockscout_unified.observability import (
    build_bottom_summary,
    build_next_summary,
    render_markdown,
    unavailable_summary,
    write_summary,
)


def test_bottom_summary_uses_scan_health_counts(tmp_path: Path) -> None:
    raw = tmp_path / "bottom.json"
    raw.write_text(
        json.dumps(
            {
                "sessionDate": "2026-08-28",
                "candidates": [{"ticker": "AAA"}],
                "excluded": [{"ticker": "BBB"}],
                "stats": {
                    "universe_size": 3,
                    "coverage_pct": 66.67,
                    "data_status": "DEGRADED",
                    "tickers_failed_all_providers": 1,
                    "market_data_fresh_published_pct": 100.0,
                    "market_data_missing_published_rows": 0,
                    "provider_retry_count": 2,
                    "rate_limit_count": 1,
                    "error_types": {"TimeoutError": 1},
                },
                "provenance": {"requestedUniverseCount": 3},
            }
        ),
        encoding="utf-8",
    )

    summary = build_bottom_summary(raw)

    assert summary["status"] == "DEGRADED"
    assert summary["universeCount"] == 3
    assert summary["successCount"] == 2
    assert summary["failedCount"] == 1
    assert summary["coveragePct"] == 66.67
    assert summary["retryCount"] == 2
    assert summary["rateLimitCount"] == 1
    assert summary["topErrorClasses"] == [{"name": "TimeoutError", "count": 1}]


def test_next_summary_and_markdown_preserve_resume_metrics(tmp_path: Path) -> None:
    metrics = tmp_path / "next.json"
    metrics.write_text(
        json.dumps(
            {
                "schema": "stockscout-next-metrics/v1",
                "status": "complete",
                "sessionDate": "2026-08-28",
                "universeCount": 2000,
                "successCount": 1500,
                "skippedCount": 490,
                "failedCount": 10,
                "coveragePct": 100.0,
                "retryCount": None,
                "rateLimitCount": 2,
                "resumeCheckpointUsed": True,
                "topErrorClasses": [{"name": "TimeoutError", "count": 10}],
            }
        ),
        encoding="utf-8",
    )

    summary = build_next_summary(metrics)
    markdown = render_markdown(summary)

    assert summary["mode"] == "next"
    assert "| Universe | 2000 |" in markdown
    assert "| Resume checkpoint | used |" in markdown
    assert "Top error classes: TimeoutError=10" in markdown


def test_write_summary_appends_to_github_step_summary(tmp_path: Path) -> None:
    output = tmp_path / "summary.json"
    github = tmp_path / "github-summary.md"
    summary = unavailable_summary("next", "metrics missing")

    write_summary(summary, output=output, github_summary=github)

    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "unavailable"
    assert "Diagnostics unavailable: metrics missing" in github.read_text(encoding="utf-8")
