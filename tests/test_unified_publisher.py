from __future__ import annotations

import json
from pathlib import Path

from stockscout_eod.contracts import (
    AssetDescriptorV1,
    HealthCheckV1,
    HealthV1,
    ScanCountsV1,
    ScanManifestV1,
    wire_dump,
)
from stockscout_eod.jsonio import canonical_json_bytes, sha256_bytes
from stockscout_unified.publisher import activate_unified, publish_adjusted_mode

RUN_ID = "2026-08-24-eod-test"
SESSION = "2026-08-24"


def descriptor(path: str, count: int = 1, coverage: float | None = None) -> AssetDescriptorV1:
    return AssetDescriptorV1(
        path=path,
        sha256="a" * 64,
        bytes=2,
        count=count,
        coveragePct=coverage,
    )


def write_bottom(public: Path) -> None:
    root = public / "data" / "modes" / "bottom-fishing"
    run = root / "runs" / RUN_ID
    (run / "details").mkdir(parents=True)
    (run / "charts").mkdir(parents=True)
    for name, value in {
        "core.json": {"universe": [{"ticker": "BOT"}]},
        "excluded.json": {"rows": []},
        "history.json": {"sessions": []},
    }.items():
        (run / name).write_bytes(canonical_json_bytes(value))
    (run / "details" / "000.json").write_bytes(canonical_json_bytes({"BOT": {"ticker": "BOT"}}))
    health = HealthV1(
        status="healthy",
        coveragePct=100,
        checks=[HealthCheckV1(code="fixture", passed=True, detail="healthy")],
    )
    manifest = ScanManifestV1(
        mode="bottom-fishing",
        runId=RUN_ID,
        sessionDate=SESSION,
        marketDataDate=SESSION,
        generatedAt=f"{SESSION}T22:00:00Z",
        status="healthy",
        priceMode="split_only",
        chartStatus="ready",
        counts=ScanCountsV1(universe=1, candidates=1, excluded=0, failed=0),
        health=health,
        provenance={"fixture": True},
        versions={"ranking": "frozen", "detectors": "frozen", "tradePlan": "v1"},
        assets={
            "core": descriptor(f"runs/{RUN_ID}/core.json"),
            "excluded": descriptor(f"runs/{RUN_ID}/excluded.json", 0),
            "history": descriptor(f"runs/{RUN_ID}/history.json", 0),
            "details": descriptor(f"runs/{RUN_ID}/details"),
            "charts": descriptor(f"runs/{RUN_ID}/charts", coverage=100),
        },
    )
    (root / "manifest.json").write_bytes(canonical_json_bytes(wire_dump(manifest)))


def test_adjusted_modes_remain_isolated_and_activate_atomically(tmp_path: Path) -> None:
    public = tmp_path / "public"
    chart_dir = tmp_path / "charts"
    chart_dir.mkdir()
    (chart_dir / "000.json").write_bytes(canonical_json_bytes({"AAA": [], "BBB": []}))
    canonical = {
        "generatedAt": f"{SESSION}T22:00:00Z",
        "market": {"scanDate": SESSION},
        "chartShardCount": 1,
        "chartShards": {"AAA": "000.json", "BBB": "000.json"},
        "universe": [
            {"ticker": "AAA", "price": 10, "score": 90, "opportunityScore": 90, "originalBuyScore": 61, "originalRunBuySignal": False, "originalEngine": {"buy": {"sourceReason": "watch"}}},
            {"ticker": "BBB", "price": 20, "score": 80, "opportunityScore": 80, "originalBuyScore": 95, "originalRunBuySignal": True, "originalEngine": {"buy": {"sourceReason": "buy"}}},
        ],
    }
    canonical_path = tmp_path / "latest.json"
    canonical_path.write_bytes(canonical_json_bytes(canonical))

    next_manifest = publish_adjusted_mode(mode="next", canonical_path=canonical_path, chart_dir=chart_dir, public_dir=public, run_id=RUN_ID, session_date=SESSION)
    ryan_manifest = publish_adjusted_mode(mode="ryan-original", canonical_path=canonical_path, chart_dir=chart_dir, public_dir=public, run_id=RUN_ID, session_date=SESSION)
    write_bottom(public)
    active = activate_unified(public_dir=public, run_id=RUN_ID, session_date=SESSION)

    assert set(active.modes) == {"bottom-fishing", "next", "ryan-original"}
    assert next_manifest.price_mode == ryan_manifest.price_mode == "split_div"
    next_core = json.loads((public / "data" / "modes" / "next" / next_manifest.assets["core"].path).read_text(encoding="utf-8"))
    ryan_core = json.loads((public / "data" / "modes" / "ryan-original" / ryan_manifest.assets["core"].path).read_text(encoding="utf-8"))
    assert [row["ticker"] for row in next_core["universe"]] == ["AAA", "BBB"]
    assert [row["ticker"] for row in ryan_core["universe"]] == ["BBB", "AAA"]
    assert next_core["universe"][0]["id"] == f"scan:{RUN_ID}:mode:next:candidate:AAA"
    assert ryan_core["universe"][0]["id"] == f"scan:{RUN_ID}:mode:ryan-original:candidate:BBB"
    root_bytes = (public / "data" / "modes" / "next" / "manifest.json").read_bytes()
    assert active.modes["next"].manifest_sha256 == sha256_bytes(root_bytes)


def test_activation_rejects_cross_mode_price_basis(tmp_path: Path) -> None:
    public = tmp_path / "public"
    write_bottom(public)
    root = public / "data" / "modes" / "bottom-fishing" / "manifest.json"
    payload = json.loads(root.read_text(encoding="utf-8"))
    payload["priceMode"] = "split_div"
    root.write_bytes(canonical_json_bytes(payload))
    try:
        activate_unified(public_dir=public, run_id=RUN_ID, session_date=SESSION)
    except (FileNotFoundError, ValueError) as exc:
        assert "bottom-fishing" in str(exc) or "next" in str(exc)
    else:
        raise AssertionError("activation accepted an invalid Bottom Fishing price basis")
