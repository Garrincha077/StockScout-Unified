from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from stockscout_eod.artifacts import (
    build_public_snapshot,
    load_legacy_sidecar,
    verify_public_snapshot,
)
from stockscout_eod.charts import build_chart_staging, promote_chart_run, publish_chart_staging
from stockscout_eod.cloud_publish import (
    evaluate_cloud_alerts,
    maintain_cloud_snapshot,
    publish_cloud_snapshot,
)
from stockscout_eod.contracts import wire_dump
from stockscout_eod.cutover import update_cutover_ledger
from stockscout_eod.deployment import verify_pages_activation
from stockscout_eod.health import evaluate_scan_health, require_healthy
from stockscout_eod.legacy_runner import run_legacy_shadow
from stockscout_eod.market_cache import (
    build_market_cache_staging,
    publish_market_cache_staging,
    restore_market_cache,
)
from stockscout_eod.notifications import (
    build_snapshot_digest_parts,
    deliver_operational_error,
    deliver_snapshot_digest,
)
from stockscout_eod.public_config import validate_public_environment
from stockscout_eod.runner import load_raw_scan, run_scan
from stockscout_eod.runtime_export import (
    write_runtime_diagnostic,
    write_runtime_scan_export,
)
from stockscout_eod.session import decide_session, write_github_outputs


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected ISO-8601 timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stockscout-eod")
    commands = parser.add_subparsers(dest="command", required=True)

    guard = commands.add_parser("guard", help="decide whether a completed NYSE session should run")
    guard.add_argument("--requested-date", type=_date)
    guard.add_argument("--active-manifest")
    guard.add_argument("--force", action="store_true")
    guard.add_argument("--now", type=_datetime, help=argparse.SUPPRESS)
    guard.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))

    scan = commands.add_parser("scan", help="run the frozen deterministic StockScout engine")
    scan.add_argument("--config", default="config/eod.yaml")
    scan.add_argument("--output", required=True)
    scan.add_argument("--session-date", required=True, type=_date)
    scan.add_argument("--run-id", required=True)
    scan.add_argument("--tickers", help="fixture-only comma-separated universe")
    scan.add_argument("--smoke-universe", help="fixture-only JSON universe")
    scan.add_argument(
        "--no-notify",
        action="store_true",
        help="explicitly document that the scan itself has no outward side effects",
    )

    health = commands.add_parser("health", help="validate raw scan activation gates")
    health.add_argument("--input", required=True)
    health.add_argument("--min-coverage", type=float, default=90.0)
    health.add_argument("--min-universe", type=int, default=1000)
    health.add_argument("--allow-fixture", action="store_true", help=argparse.SUPPRESS)

    runtime_export = commands.add_parser(
        "export-runtime-scan",
        help="write the allowlisted scan envelope used by post-deploy jobs",
    )
    runtime_export.add_argument("--input", required=True)
    runtime_export.add_argument("--output", required=True)

    diagnostic = commands.add_parser(
        "write-runtime-diagnostic",
        help="write fixed health codes and counters without copying raw scan data",
    )
    diagnostic.add_argument("--input", required=True)
    diagnostic.add_argument("--output", required=True)
    diagnostic.add_argument("--min-coverage", type=float, default=90.0)
    diagnostic.add_argument("--min-universe", type=int, default=1000)
    diagnostic.add_argument("--allow-fixture", action="store_true", help=argparse.SUPPRESS)

    build = commands.add_parser("build", help="build and activate immutable public assets")
    build.add_argument("--input", required=True)
    build.add_argument("--public-dir", default="frontend/public")
    build.add_argument("--legacy-sidecar")
    build.add_argument("--previous-manifest")
    build.add_argument(
        "--public-base-url", default="https://garrincha077.github.io/StockScout-EOD"
    )
    build.add_argument("--min-coverage", type=float, default=90.0)
    build.add_argument("--min-universe", type=int, default=1000)
    build.add_argument("--allow-fixture", action="store_true", help=argparse.SUPPRESS)
    build.add_argument("--chart-manifest")
    build.add_argument(
        "--chart-status", choices=("ready", "stale", "missing"), default="missing"
    )

    charts = commands.add_parser("charts", help="build compact gzip chart shards")
    charts.add_argument("--input", required=True)
    charts.add_argument("--config", default="config/eod.yaml")
    charts.add_argument("--output", default=".staging/charts")
    charts.add_argument("--storage-base-url", required=True)
    charts.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))

    legacy = commands.add_parser(
        "legacy-shadow", help="run Ryan phase/Minervini confirmation without ranking impact"
    )
    legacy.add_argument("--input", required=True)
    legacy.add_argument("--config", default="config/eod.yaml")
    legacy.add_argument("--output", required=True)

    publish_charts = commands.add_parser(
        "publish-charts", help="upload chart shards directly with GitHub OIDC"
    )
    publish_charts.add_argument("--staging-dir", default=".staging/charts")
    publish_charts.add_argument("--run-id", required=True)
    publish_charts.add_argument("--endpoint", required=True)

    promote_charts = commands.add_parser(
        "promote-charts", help="promote an active legacy chart run to its public path"
    )
    promote_charts.add_argument("--run-id", required=True)
    promote_charts.add_argument("--endpoint", required=True)

    publish_cloud = commands.add_parser(
        "publish-cloud", help="atomically publish a verified Pages snapshot with GitHub OIDC"
    )
    publish_cloud.add_argument("--public-dir", default="frontend/public")
    publish_cloud.add_argument("--endpoint", required=True)
    publish_cloud.add_argument("--chunk-size", type=int, default=100)

    cleanup_cloud = commands.add_parser(
        "cloud-cleanup", help="clean abandoned uploads and unprotected chart runs"
    )
    cleanup_cloud.add_argument("--endpoint", required=True)
    cleanup_cloud.add_argument("--protected-run-id", required=True)

    evaluate_alerts = commands.add_parser(
        "evaluate-alerts", help="evaluate owner alerts against the active healthy EOD snapshot"
    )
    evaluate_alerts.add_argument("--endpoint", required=True)

    verify_pages = commands.add_parser(
        "verify-pages", help="confirm the deployed Pages pointer matches the local artifact"
    )
    verify_pages.add_argument("--public-dir", default="frontend/public")
    verify_pages.add_argument("--manifest-url", required=True)

    restore_cache = commands.add_parser(
        "market-cache-restore", help="restore the private raw cache through GitHub OIDC"
    )
    restore_cache.add_argument("--cache-dir", default="data/cache")
    restore_cache.add_argument("--endpoint", required=True)

    build_cache = commands.add_parser(
        "market-cache-build", help="build deterministic private market-cache shards"
    )
    build_cache.add_argument("--cache-dir", default="data/cache")
    build_cache.add_argument("--output", default=".staging/market-cache")

    publish_cache = commands.add_parser(
        "market-cache-publish", help="publish private market-cache shards through GitHub OIDC"
    )
    publish_cache.add_argument("--staging-dir", default=".staging/market-cache")
    publish_cache.add_argument("--endpoint", required=True)

    notify = commands.add_parser(
        "notify-telegram", help="send or dry-run the resumable multipart daily digest"
    )
    notify.add_argument("--input", required=True)
    notify.add_argument("--endpoint", required=True)
    notify.add_argument("--cloud-sync-status", choices=("synced", "failed"), required=True)
    notify.add_argument(
        "--report-link", default="https://garrincha077.github.io/StockScout-EOD/"
    )
    notify.add_argument("--allow-notify", action="store_true")

    notify_error = commands.add_parser(
        "notify-operational-error", help="send or dry-run one bounded operational error"
    )
    notify_error.add_argument("--endpoint", required=True)
    notify_error.add_argument("--session-date", required=True, type=_date)
    notify_error.add_argument("--message", required=True)
    notify_error.add_argument("--allow-notify", action="store_true")

    verify = commands.add_parser("verify", help="verify hashes and public asset allowlist")
    verify.add_argument("--public-dir", default="frontend/public")

    commands.add_parser(
        "validate-browser-config",
        help="reject secret/service-role keys before building a public client",
    )

    cutover = commands.add_parser(
        "cutover-evidence",
        help="append read-only new/local/stable parity evidence to the cutover ledger",
    )
    cutover.add_argument("--new", required=True, help="sanitized new-app scan JSON")
    cutover.add_argument("--local", required=True, help="sanitized local StockScout scan JSON")
    cutover.add_argument("--stable", required=True, help="sanitized Stable scan JSON")
    cutover.add_argument("--ledger-json", required=True)
    cutover.add_argument("--ledger-markdown", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "guard":
        decision = decide_session(
            now_utc=args.now,
            requested_date=args.requested_date,
            active_manifest=args.active_manifest,
            force=args.force,
        )
        if args.github_output:
            write_github_outputs(decision, args.github_output)
        print(json.dumps(decision.__dict__, sort_keys=True))
        return 0

    if args.command == "scan":
        tickers = args.tickers.split(",") if args.tickers else None
        envelope = run_scan(
            config_path=args.config,
            output_path=args.output,
            session_date=args.session_date,
            run_id=args.run_id,
            tickers=tickers,
            smoke_universe=args.smoke_universe,
        )
        print(json.dumps({"runId": envelope.run_id, "candidates": len(envelope.candidates)}))
        return 0

    if args.command == "health":
        health = evaluate_scan_health(
            load_raw_scan(args.input),
            min_coverage_pct=args.min_coverage,
            min_universe=args.min_universe,
            allow_fixture=args.allow_fixture,
        )
        print(json.dumps(wire_dump(health), sort_keys=True))
        require_healthy(health)
        return 0
    if args.command == "export-runtime-scan":
        payload = write_runtime_scan_export(load_raw_scan(args.input), args.output)
        print(
            json.dumps(
                {
                    "status": "written",
                    "candidates": len(payload["candidates"]),
                    "excluded": len(payload["excluded"]),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "write-runtime-diagnostic":
        diagnostic = write_runtime_diagnostic(
            args.input,
            args.output,
            min_coverage_pct=args.min_coverage,
            min_universe=args.min_universe,
            allow_fixture=args.allow_fixture,
        )
        print(json.dumps(diagnostic, sort_keys=True))
        return 0
    if args.command == "charts":
        manifest = build_chart_staging(
            load_raw_scan(args.input),
            config_path=args.config,
            output_dir=args.output,
            storage_base_url=args.storage_base_url,
        )
        coverage_status = "ready" if manifest.coverage_pct == 100.0 else "stale"
        if args.github_output:
            output = Path(args.github_output)
            with output.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"coverage_status={coverage_status}\n")
                handle.write(f"coverage_pct={manifest.coverage_pct}\n")
                manifest_path = Path(args.output).resolve() / manifest.run_id / "manifest.json"
                handle.write(f"manifest={manifest_path}\n")
        print(json.dumps(wire_dump(manifest), sort_keys=True))
        return 0
    if args.command == "legacy-shadow":
        result = run_legacy_shadow(
            load_raw_scan(args.input),
            config_path=args.config,
            output_path=args.output,
        )
        available = sum(
            bool(row.get("available")) for row in result["candidates"].values()
        )
        print(
            json.dumps(
                {
                    "runId": result["runId"],
                    "candidates": len(result["candidates"]),
                    "available": available,
                    "affectsRanking": False,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "publish-charts":
        manifest = publish_chart_staging(
            staging_dir=args.staging_dir,
            run_id=args.run_id,
            endpoint=args.endpoint,
        )
        print(json.dumps({"runId": manifest.run_id, "coveragePct": manifest.coverage_pct}))
        return 0
    if args.command == "promote-charts":
        print(
            json.dumps(
                promote_chart_run(endpoint=args.endpoint, run_id=args.run_id), sort_keys=True
            )
        )
        return 0
    if args.command == "publish-cloud":
        result = publish_cloud_snapshot(
            public_dir=args.public_dir,
            endpoint=args.endpoint,
            chunk_size=args.chunk_size,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "cloud-cleanup":
        print(
            json.dumps(
                maintain_cloud_snapshot(
                    endpoint=args.endpoint, protected_run_id=args.protected_run_id
                ),
                sort_keys=True,
            )
        )
        return 0
    if args.command == "evaluate-alerts":
        print(json.dumps(evaluate_cloud_alerts(endpoint=args.endpoint), sort_keys=True))
        return 0
    if args.command == "verify-pages":
        print(
            json.dumps(
                verify_pages_activation(
                    public_dir=args.public_dir, manifest_url=args.manifest_url
                ),
                sort_keys=True,
            )
        )
        return 0
    if args.command == "market-cache-restore":
        manifest = restore_market_cache(args.cache_dir, args.endpoint)
        print(
            json.dumps(
                manifest or {"status": "cold_bootstrap", "restored": False},
                sort_keys=True,
            )
        )
        return 0
    if args.command == "market-cache-build":
        manifest = build_market_cache_staging(args.cache_dir, args.output)
        print(json.dumps(manifest, sort_keys=True))
        return 0
    if args.command == "market-cache-publish":
        manifest = publish_market_cache_staging(args.staging_dir, args.endpoint)
        print(json.dumps(manifest, sort_keys=True))
        return 0
    if args.command == "notify-telegram":
        if not args.allow_notify:
            digest_key, parts = build_snapshot_digest_parts(
                args.input,
                cloud_sync_status=args.cloud_sync_status,
                report_link=args.report_link,
            )
            print(
                json.dumps(
                    {
                        "status": "suppressed",
                        "digestKey": digest_key,
                        "parts": len(parts),
                        "characters": sum(map(len, parts)),
                    },
                    sort_keys=True,
                )
            )
            return 0
        sent = deliver_snapshot_digest(
            args.input,
            endpoint=args.endpoint,
            cloud_sync_status=args.cloud_sync_status,
            report_link=args.report_link,
        )
        if not sent:
            raise RuntimeError("Telegram digest did not complete")
        print(json.dumps({"status": "sent"}, sort_keys=True))
        return 0
    if args.command == "notify-operational-error":
        if not args.allow_notify:
            print(json.dumps({"status": "suppressed"}, sort_keys=True))
            return 0
        sent = deliver_operational_error(
            endpoint=args.endpoint,
            session_date=args.session_date.isoformat(),
            message=args.message,
        )
        if not sent:
            raise RuntimeError("Telegram operational error did not complete")
        print(json.dumps({"status": "sent"}, sort_keys=True))
        return 0

    if args.command == "build":
        manifest = build_public_snapshot(
            load_raw_scan(args.input),
            public_dir=args.public_dir,
            legacy_sidecar=load_legacy_sidecar(args.legacy_sidecar),
            previous_manifest=args.previous_manifest,
            min_coverage_pct=args.min_coverage,
            min_universe=args.min_universe,
            allow_fixture=args.allow_fixture,
            chart_status=args.chart_status,
            chart_manifest=args.chart_manifest,
            public_base_url=args.public_base_url,
        )
        print(json.dumps(wire_dump(manifest), sort_keys=True))
        return 0

    if args.command == "verify":
        manifest = verify_public_snapshot(args.public_dir)
        print(json.dumps({"runId": manifest.run_id, "status": manifest.status}))
        return 0
    if args.command == "validate-browser-config":
        print(json.dumps(validate_public_environment(), sort_keys=True))
        return 0
    if args.command == "cutover-evidence":
        ledger = update_cutover_ledger(
            new_scan=args.new,
            local_scan=args.local,
            stable_scan=args.stable,
            ledger_json=args.ledger_json,
            ledger_markdown=args.ledger_markdown,
        )
        print(json.dumps(wire_dump(ledger.readiness), sort_keys=True))
        return 0 if ledger.readiness.ready else 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
