"""Build the two read-only Next context assets with verified last-good fallback."""
from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
NEXT_ENGINE = ROOT / "engines" / "next"
DEFAULT_FACTOR_FALLBACK = (
    "https://garrincha077.github.io/StockScreener-next/data/factors/factor-regime.json"
)
DEFAULT_GMLI_FALLBACK = (
    "https://garrincha077.github.io/StockScreener-next/data/gmli/gmli-context.json"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Context is not a JSON object: {path}")
    return value


def validate_context(payload: Mapping[str, Any], kind: str) -> None:
    if payload.get("schemaVersion") != 1:
        raise ValueError(f"{kind} must use schemaVersion 1")
    if kind == "factorRegime":
        factors = payload.get("factors")
        method = payload.get("method")
        ids = {str(row.get("id")) for row in factors or [] if isinstance(row, Mapping)}
        if ids != {"MKT_RF", "SMB", "HML", "RMW", "CMA", "MOM"}:
            raise ValueError("factorRegime must contain exactly the six approved factors")
        if not isinstance(method, Mapping) or not str(method.get("stockScoutImpact") or "").startswith(
            "none;"
        ):
            raise ValueError("factorRegime is not declared read-only")
        return
    if kind == "gmliContext":
        contract = payload.get("consumerContract")
        if (
            payload.get("status") != "OK"
            or not isinstance(contract, Mapping)
            or contract.get("mode") != "READ_ONLY_SIDECAR"
            or contract.get("mutatesStockScoutScoring") is not False
        ):
            raise ValueError("gmliContext violates the read-only consumer contract")
        return
    raise ValueError(f"Unsupported context kind: {kind}")


def annotate_freshness(
    payload: dict[str, Any],
    *,
    status: str,
    fallback: bool,
    provenance: str,
    checked_at: str | None = None,
) -> dict[str, Any]:
    if status not in {"fresh", "stale"}:
        raise ValueError(f"Unsupported freshness status: {status}")
    payload["freshness"] = {
        "status": status,
        "fallback": fallback,
        "checkedAt": checked_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "provenance": provenance,
    }
    return payload


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _download_last_good(url: str, destination: Path, kind: str, timeout: float) -> bool:
    try:
        request = Request(url, headers={"Cache-Control": "no-cache", "User-Agent": "StockScout-Unified/1.0"})
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - explicit HTTPS fallback
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            return False
        validate_context(payload, kind)
        _write(destination, payload)
        return True
    except Exception:
        return False


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def build_contexts(
    output_dir: Path,
    *,
    timeout: float = 30.0,
    factor_fallback_url: str = DEFAULT_FACTOR_FALLBACK,
    gmli_fallback_url: str = DEFAULT_GMLI_FALLBACK,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    factor_path = output_dir / "factor-regime.json"
    gmli_path = output_dir / "gmli-context.json"
    factor_seeded = _download_last_good(
        factor_fallback_url, factor_path, "factorRegime", timeout
    )
    gmli_seeded = _download_last_good(gmli_fallback_url, gmli_path, "gmliContext", timeout)

    factor_result = _run(
        [
            sys.executable,
            str(NEXT_ENGINE / "build_factor_regime.py"),
            "--output",
            str(factor_path),
        ]
    )
    if factor_result.returncode == 0:
        factor = annotate_freshness(
            _load(factor_path), status="fresh", fallback=False, provenance="live-source-refresh"
        )
    elif factor_seeded:
        factor = annotate_freshness(
            _load(factor_path), status="stale", fallback=True, provenance=factor_fallback_url
        )
    else:
        raise RuntimeError(f"Factor refresh failed with no valid last-good asset: {factor_result.stderr[-500:]}")
    validate_context(factor, "factorRegime")
    _write(factor_path, factor)

    gmli_command = [
        sys.executable,
        str(NEXT_ENGINE / "refresh_gmli_context.py"),
        "--output",
        str(gmli_path),
        "--timeout",
        str(timeout),
    ]
    if gmli_seeded:
        gmli_command.append("--allow-last-good")
    gmli_result = _run(gmli_command)
    gmli_status = ""
    with contextlib.suppress(json.JSONDecodeError):
        gmli_status = str(json.loads(gmli_result.stdout or "{}").get("status") or "")
    if gmli_result.returncode == 0 and gmli_path.exists():
        fallback = gmli_status == "LAST_GOOD_FALLBACK"
        gmli = annotate_freshness(
            _load(gmli_path),
            status="stale" if fallback else "fresh",
            fallback=fallback,
            provenance=gmli_fallback_url if fallback else "live-source-refresh",
        )
    elif gmli_seeded:
        gmli = annotate_freshness(
            _load(gmli_path), status="stale", fallback=True, provenance=gmli_fallback_url
        )
    else:
        raise RuntimeError(f"GMLI refresh failed with no valid last-good asset: {gmli_result.stderr[-500:]}")
    validate_context(gmli, "gmliContext")
    _write(gmli_path, gmli)

    result = {
        "factorRegime": {"path": str(factor_path), "freshness": factor["freshness"]},
        "gmliContext": {"path": str(gmli_path), "freshness": gmli["freshness"]},
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--factor-fallback-url", default=DEFAULT_FACTOR_FALLBACK)
    parser.add_argument("--gmli-fallback-url", default=DEFAULT_GMLI_FALLBACK)
    args = parser.parse_args()
    build_contexts(
        args.output_dir,
        timeout=args.timeout,
        factor_fallback_url=args.factor_fallback_url,
        gmli_fallback_url=args.gmli_fallback_url,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
