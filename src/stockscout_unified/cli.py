"""Command line entry point for isolated mode publication and activation."""
from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Sequence
from pathlib import Path

from stockscout_eod.artifacts import build_public_snapshot
from stockscout_eod.contracts import ScanManifestV1, wire_dump
from stockscout_eod.jsonio import sha256_bytes
from stockscout_eod.runner import load_raw_scan

from .contracts import UnifiedManifestV1
from .notifications import build_series, deliver_series, evaluate_owner_alerts
from .publisher import activate_unified, publish_adjusted_mode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stockscout-unified")
    commands = parser.add_subparsers(dest="command", required=True)

    bottom = commands.add_parser("publish-bottom", help="Publish one healthy Bottom Fishing snapshot")
    bottom.add_argument("--raw-scan", required=True)
    bottom.add_argument("--public-dir", required=True)
    bottom.add_argument("--previous-manifest")
    bottom.add_argument("--chart-manifest", required=True)
    bottom.add_argument("--chart-staging-dir", required=True)
    bottom.add_argument("--allow-fixture", action="store_true")
    bottom.add_argument("--min-universe", type=int, default=1000)

    adjusted = commands.add_parser("publish-adjusted", help="Publish isolated Next or Ryan assets")
    adjusted.add_argument("--mode", choices=("next", "ryan-original"), required=True)
    adjusted.add_argument("--canonical", required=True)
    adjusted.add_argument("--chart-dir", required=True)
    adjusted.add_argument("--public-dir", required=True)
    adjusted.add_argument("--run-id", required=True)
    adjusted.add_argument("--session-date", required=True)
    adjusted.add_argument("--min-chart-coverage", type=float, default=95.0)

    activate = commands.add_parser("activate", help="Atomically activate a healthy three-mode run")
    activate.add_argument("--public-dir", required=True)
    activate.add_argument("--run-id", required=True)
    activate.add_argument("--session-date", required=True)

    verify = commands.add_parser("verify", help="Verify the active unified and mode pointers")
    verify.add_argument("--public-dir", required=True)

    notify = commands.add_parser("notify", help="Render or deliver five mode-isolated Telegram series")
    notify.add_argument("--public-dir", required=True)
    notify.add_argument("--bottom-raw-scan", required=True)
    notify.add_argument("--alerts")
    notify.add_argument("--delivery-endpoint")
    notify.add_argument("--allow-notify", action="store_true")

    alerts = commands.add_parser("evaluate-alerts", help="Evaluate owner alerts for the exact active Pages run")
    alerts.add_argument("--public-dir", required=True)
    alerts.add_argument("--endpoint", required=True)
    alerts.add_argument("--output", required=True)
    return parser


def _copy_bottom_charts(*, public_dir: Path, manifest: ScanManifestV1, staging_dir: Path) -> None:
    source = staging_dir.resolve() / manifest.run_id / "shards"
    destination = (
        public_dir.resolve()
        / "data"
        / "modes"
        / "bottom-fishing"
        / "runs"
        / manifest.run_id
        / "charts"
        / "shards"
    )
    if not source.is_dir():
        raise FileNotFoundError(f"Bottom chart staging shards are missing: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.glob("*.json.gz"):
        shutil.copyfile(path, destination / path.name)


def verify(public_dir: str | Path) -> UnifiedManifestV1:
    data_root = Path(public_dir).resolve() / "data"
    unified = UnifiedManifestV1.model_validate_json((data_root / "manifest.json").read_bytes())
    for mode, pointer in unified.modes.items():
        path = data_root / pointer.manifest_path
        payload = path.read_bytes()
        if len(payload) != pointer.manifest_bytes or sha256_bytes(payload) != pointer.manifest_sha256:
            raise ValueError(f"{mode} manifest hash or byte count mismatch")
        manifest = ScanManifestV1.model_validate_json(payload)
        if manifest.run_id != unified.run_id or manifest.session_date != unified.session_date:
            raise ValueError(f"{mode} scan identity does not match active unified pointer")
        for name, descriptor in manifest.assets.items():
            target = path.parent / descriptor.path
            if name in {"details", "charts"}:
                if not target.is_dir():
                    raise FileNotFoundError(f"{mode} {name} directory is missing")
            elif not target.is_file():
                raise FileNotFoundError(f"{mode} asset is missing: {descriptor.path}")
    return unified


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "publish-bottom":
        scan = load_raw_scan(args.raw_scan)
        chart_index = json.loads(Path(args.chart_manifest).read_text(encoding="utf-8"))
        chart_coverage = float(chart_index.get("coveragePct") or 0.0)
        if chart_coverage < 95.0:
            raise ValueError(f"Bottom chart coverage is {chart_coverage:.2f}%, expected at least 95%")
        manifest = build_public_snapshot(
            scan,
            public_dir=args.public_dir,
            previous_manifest=args.previous_manifest,
            min_universe=args.min_universe,
            allow_fixture=args.allow_fixture,
            chart_status="ready" if chart_coverage == 100.0 else "stale",
            chart_manifest=args.chart_manifest,
            document_mode="bottom-fishing",
            data_subdir="data/modes/bottom-fishing",
            history_limit=20,
        )
        _copy_bottom_charts(
            public_dir=Path(args.public_dir),
            manifest=manifest,
            staging_dir=Path(args.chart_staging_dir),
        )
        print(json.dumps(wire_dump(manifest), sort_keys=True))
        return 0
    if args.command == "publish-adjusted":
        manifest = publish_adjusted_mode(
            mode=args.mode,
            canonical_path=args.canonical,
            chart_dir=args.chart_dir,
            public_dir=args.public_dir,
            run_id=args.run_id,
            session_date=args.session_date,
            min_chart_coverage_pct=args.min_chart_coverage,
        )
        print(json.dumps(wire_dump(manifest), sort_keys=True))
        return 0
    if args.command == "activate":
        manifest = activate_unified(
            public_dir=args.public_dir,
            run_id=args.run_id,
            session_date=args.session_date,
        )
        print(json.dumps(manifest.model_dump(mode="json", by_alias=True), sort_keys=True))
        return 0
    if args.command == "notify":
        series = build_series(public_dir=args.public_dir, bottom_raw_scan=args.bottom_raw_scan, alerts_path=args.alerts)
        if args.allow_notify:
            if not args.delivery_endpoint:
                raise ValueError("--delivery-endpoint is required with --allow-notify")
            if not deliver_series(series, endpoint=args.delivery_endpoint):
                return 1
        print(json.dumps({name: {"parts": len(parts), "characters": sum(map(len, parts))} for name, parts in series.items()}, sort_keys=True))
        return 0
    if args.command == "evaluate-alerts":
        result = evaluate_owner_alerts(
            public_dir=args.public_dir,
            endpoint=args.endpoint,
            output_path=args.output,
        )
        print(json.dumps({"runId": result["runId"], "events": len(result["events"])}, sort_keys=True))
        return 0
    manifest = verify(args.public_dir)
    print(json.dumps({"runId": manifest.run_id, "status": manifest.status}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
