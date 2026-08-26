"""Verified Pages snapshot -> atomic Supabase begin/chunk/finalize publisher."""
from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from stockscout_eod.artifacts import verify_public_snapshot
from stockscout_eod.contracts import (
    CloudFieldCatalogEntryV1,
    CloudPublishManifestV1,
    ScanManifestV1,
    wire_dump,
)
from stockscout_eod.github_oidc import github_oidc_token
from stockscout_eod.jsonio import canonical_json_bytes, json_compatible, sha256_bytes

AUDIENCE = "stockscout-eod-publish"
MAX_CHUNK_SIZE = 100
MAX_CONTROL_BODY_BYTES = 12_000_000
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
TICKER_RE = re.compile(r"^[A-Z0-9._-]{1,20}$")
FIELD_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
SAFE_JS_INTEGER = 9_007_199_254_740_991
_MISSING = object()

JsonObject = dict[str, Any]
PostJson = Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]


class CloudPublishError(RuntimeError):
    """A local contract or remote publish failure."""


def _js_number(value: int | float) -> str:
    """Render a JSON number the way JavaScript ``JSON.stringify`` does.

    The Edge Function hashes its parsed body with a recursive stable stringify.
    Python's encoder writes ``1.0`` and zero-padded exponents, while JavaScript
    writes ``1`` and ``1e-7``; hashing Python's representation would therefore
    reject otherwise valid records.
    """

    if isinstance(value, int):
        if abs(value) > SAFE_JS_INTEGER:
            raise CloudPublishError("integer exceeds JavaScript's exact JSON range")
        return str(value)
    if not math.isfinite(value):
        raise CloudPublishError("non-finite JSON number cannot be published")
    if value == 0:
        return "0"
    decimal = Decimal(repr(value))
    absolute = abs(decimal)
    if Decimal("1e-6") <= absolute < Decimal("1e21"):
        rendered = format(decimal, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered

    sign = "-" if decimal < 0 else ""
    positive = abs(decimal)
    exponent = positive.adjusted()
    mantissa = format(positive.scaleb(-exponent), "f").rstrip("0").rstrip(".")
    exponent_text = f"+{exponent}" if exponent >= 0 else str(exponent)
    return f"{sign}{mantissa}e{exponent_text}"


def stable_json_text(value: Any) -> str:
    """Match the Edge Function's ``stableStringify`` for JSON-compatible data."""

    normalized = json_compatible(value)
    if normalized is None:
        return "null"
    if normalized is True:
        return "true"
    if normalized is False:
        return "false"
    if isinstance(normalized, (int, float)):
        return _js_number(normalized)
    if isinstance(normalized, str):
        return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if isinstance(normalized, Mapping):
        if not all(isinstance(key, str) for key in normalized):
            raise CloudPublishError("cloud JSON object keys must be strings")
        pairs = (
            f"{stable_json_text(key)}:{stable_json_text(normalized[key])}"
            for key in sorted(normalized)
        )
        return "{" + ",".join(pairs) + "}"
    if isinstance(normalized, Sequence) and not isinstance(
        normalized, (str, bytes, bytearray)
    ):
        return "[" + ",".join(stable_json_text(item) for item in normalized) + "]"
    raise CloudPublishError(f"unsupported cloud JSON value: {type(normalized).__name__}")


def stable_hash(value: Any) -> str:
    return sha256_bytes(stable_json_text(value).encode("utf-8"))


_CAMEL_WORD = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def _snake_key(value: str) -> str:
    first = _CAMEL_WORD.sub(r"\1_\2", value)
    return _CAMEL_BOUNDARY.sub(r"\1_\2", first).replace("-", "_").lower()


def _snake_record(value: Any) -> Any:
    if isinstance(value, Mapping):
        output: JsonObject = {}
        for key, nested in value.items():
            normalized = _snake_key(str(key))
            converted = _snake_record(nested)
            if normalized in output and output[normalized] != converted:
                raise CloudPublishError(f"camelCase field collision at {normalized}")
            output[normalized] = converted
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_snake_record(item) for item in value]
    return json_compatible(value)


def _scalar_type(value: Any) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def build_field_catalog(records: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    """Catalog allowlistable scalar JSON paths; arrays are not scalar fields."""

    stats: dict[str, dict[str, Any]] = {}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key in sorted(value):
                name = str(key)
                child = f"{path}.{name}" if path else name
                if FIELD_RE.fullmatch(child):
                    visit(value[key], child)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return
        kind = _scalar_type(value)
        if kind is None or not path:
            return
        item = stats.setdefault(
            path,
            {"types": set(), "count": 0, "example": _MISSING},
        )
        item["types"].add(kind)
        if value is not None:
            item["count"] += 1
            if item["example"] is _MISSING:
                item["example"] = value

    for record in records:
        visit(record, "")

    catalog: list[JsonObject] = []
    for field in sorted(stats):
        item = stats[field]
        payload: JsonObject = {
            "field": field,
            "types": sorted(item["types"]),
            "count": item["count"],
        }
        if item["example"] is not _MISSING:
            payload["example"] = item["example"]
        catalog.append(wire_dump(CloudFieldCatalogEntryV1.model_validate(payload)))
    return catalog


def _first_present(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _compact_summary(row: Mapping[str, Any]) -> JsonObject:
    allowed = (
        "score",
        "focusBlend",
        "headlineRank",
        "actionability",
        "primarySetup",
        "tradeStatus",
        "entryRiskPct",
        "riskLevel",
        "price",
        "stage",
        "rsRank",
        "setupTags",
    )
    summary = {key: row[key] for key in allowed if row.get(key) is not None}
    ranking_score = _first_present(row, "focusBlend", "score", "opportunityScore")
    if ranking_score is not None:
        summary["rankingScore"] = ranking_score
    return json_compatible(summary)


def publish_record_hash(row: Mapping[str, Any]) -> str:
    """Hash the exact wrapper committed by Edge ``publishRecordHash``."""

    wrapper = {
        "ticker": row.get("ticker"),
        "source": row.get("source"),
        "scanOrder": row.get("scanOrder"),
        "record": row.get("record"),
        "summary": row.get("summary") or {},
    }
    return stable_hash(wrapper)


def records_hash(records: Sequence[Mapping[str, Any]]) -> str:
    """Match SQL ``string_agg(record_hash order by source,ticker)`` exactly."""

    lines = [
        str(row["recordHash"])
        for row in sorted(records, key=lambda item: (str(item["source"]), str(item["ticker"])))
    ]
    return sha256_bytes("\n".join(lines).encode("utf-8"))


@dataclass(frozen=True)
class CloudPublishPlan:
    manifest: JsonObject
    records: tuple[JsonObject, ...]
    chunk_size: int = MAX_CHUNK_SIZE

    @property
    def begin_payload(self) -> JsonObject:
        return {"action": "begin", "manifest": self.manifest}

    def chunk_payloads(self, upload_id: str) -> tuple[JsonObject, ...]:
        if not UUID_RE.fullmatch(upload_id):
            raise CloudPublishError("begin response did not contain a valid uploadId")
        return tuple(
            {
                "action": "chunk",
                "uploadId": upload_id,
                "chunkIndex": index // self.chunk_size,
                "records": list(self.records[index : index + self.chunk_size]),
            }
            for index in range(0, len(self.records), self.chunk_size)
        )

    @staticmethod
    def finalize_payload(upload_id: str) -> JsonObject:
        if not UUID_RE.fullmatch(upload_id):
            raise CloudPublishError("invalid uploadId")
        return {"action": "finalize", "uploadId": upload_id}


def _read_json(path: Path) -> JsonObject:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CloudPublishError(f"expected JSON object: {path.name}")
    return value


def _load_snapshot_records(
    public_dir: str | Path,
    manifest: ScanManifestV1,
) -> tuple[list[JsonObject], list[JsonObject]]:
    data_root = Path(public_dir).resolve() / "data"
    core = _read_json(data_root / manifest.assets["core"].path)
    candidate_summaries = core.get("universe")
    if not isinstance(candidate_summaries, list):
        raise CloudPublishError("core universe must be an array")
    summaries_by_ticker = {
        str(row.get("ticker") or "").upper(): row
        for row in candidate_summaries
        if isinstance(row, Mapping)
    }

    detail_root = data_root / manifest.assets["details"].path
    details: dict[str, JsonObject] = {}
    for path in sorted(detail_root.glob("*.json")):
        shard = _read_json(path)
        for ticker, record in shard.items():
            symbol = str(ticker).upper()
            if symbol in details or not isinstance(record, dict):
                raise CloudPublishError(f"invalid or duplicate detail record: {symbol}")
            details[symbol] = record
    if set(details) != set(summaries_by_ticker):
        raise CloudPublishError("core/detail ticker sets do not match")

    excluded_payload = _read_json(data_root / manifest.assets["excluded"].path)
    excluded = excluded_payload.get("rows")
    if not isinstance(excluded, list) or not all(isinstance(row, dict) for row in excluded):
        raise CloudPublishError("excluded rows must be an array of objects")

    records: list[JsonObject] = []
    for ticker, summary in summaries_by_ticker.items():
        records.append(_publish_record(details[ticker], summary, "candidate"))
    for row in excluded:
        records.append(_publish_record(row, row, "excluded"))
    return records, candidate_summaries


def _publish_record(
    public_record: Mapping[str, Any],
    public_summary: Mapping[str, Any],
    source: str,
) -> JsonObject:
    ticker = str(public_record.get("ticker") or "").strip().upper()
    if not TICKER_RE.fullmatch(ticker):
        raise CloudPublishError(f"invalid ticker in public snapshot: {ticker!r}")
    scan_order = public_record.get("scanOrder")
    if not isinstance(scan_order, int) or isinstance(scan_order, bool) or scan_order < 0:
        raise CloudPublishError(f"invalid scanOrder for {ticker}")
    record = _snake_record(public_record)
    record["ticker"] = ticker
    record["scan_order"] = scan_order
    wrapper = {
        "ticker": ticker,
        "source": source,
        "scanOrder": scan_order,
        "record": record,
        "summary": _compact_summary(public_summary),
    }
    return {**wrapper, "recordHash": publish_record_hash(wrapper)}


def build_cloud_publish_plan(
    public_dir: str | Path,
    *,
    chunk_size: int = MAX_CHUNK_SIZE,
) -> CloudPublishPlan:
    if chunk_size < 1 or chunk_size > MAX_CHUNK_SIZE:
        raise CloudPublishError("chunk_size must be between 1 and 100")
    manifest = verify_public_snapshot(public_dir)
    records, _ = _load_snapshot_records(public_dir, manifest)
    records.sort(key=lambda item: (item["source"], item["ticker"]))
    pointer = Path(public_dir).resolve() / "data" / "manifest.json"
    asset_hashes = {
        "pagesManifest": sha256_bytes(pointer.read_bytes()),
        **{name: descriptor.sha256 for name, descriptor in sorted(manifest.assets.items())},
    }
    record_payloads = [row["record"] for row in records]
    versions = manifest.versions
    ranking_version = versions.get("ranking")
    detector_version = versions.get("detectors")
    base_manifest: JsonObject = {
        "schemaVersion": manifest.schema_version,
        "runId": manifest.run_id,
        "scanDate": manifest.session_date,
        "marketDataDate": manifest.market_data_date,
        "generatedAt": manifest.generated_at,
        "priceMode": manifest.price_mode,
        "health": wire_dump(manifest.health),
        "counts": {
            "candidates": manifest.counts.candidates,
            "excluded": manifest.counts.excluded,
            "total": len(records),
        },
        "recordsHash": records_hash(records),
        "rankingVersion": str(ranking_version) if ranking_version is not None else None,
        "detectorVersion": str(detector_version) if detector_version is not None else None,
        "provenance": {
            **manifest.provenance,
            "pagesManifestSha256": asset_hashes["pagesManifest"],
        },
        "assetHashes": asset_hashes,
        "fieldCatalog": build_field_catalog(record_payloads),
    }
    base_manifest = {key: value for key, value in base_manifest.items() if value is not None}
    cloud_manifest = {
        **base_manifest,
        "manifestHash": stable_hash(base_manifest),
    }
    validated = wire_dump(CloudPublishManifestV1.model_validate(cloud_manifest))
    plan = CloudPublishPlan(
        manifest=validated,
        records=tuple(records),
        chunk_size=chunk_size,
    )
    verify_cloud_publish_plan(plan)
    return plan


def verify_cloud_publish_plan(plan: CloudPublishPlan) -> None:
    manifest = wire_dump(CloudPublishManifestV1.model_validate(plan.manifest))
    if plan.chunk_size < 1 or plan.chunk_size > MAX_CHUNK_SIZE:
        raise CloudPublishError("publish chunk size is outside the Edge contract")
    without_hash = {key: value for key, value in manifest.items() if key != "manifestHash"}
    if stable_hash(without_hash) != manifest["manifestHash"]:
        raise CloudPublishError("cloud manifest hash mismatch")

    seen: set[str] = set()
    candidate_count = 0
    excluded_count = 0
    for row in plan.records:
        ticker = row.get("ticker")
        source = row.get("source")
        scan_order = row.get("scanOrder")
        record = row.get("record")
        if not isinstance(ticker, str) or not TICKER_RE.fullmatch(ticker):
            raise CloudPublishError("invalid cloud publish ticker")
        if ticker in seen:
            raise CloudPublishError(f"duplicate cloud publish ticker: {ticker}")
        seen.add(ticker)
        if source not in {"candidate", "excluded"}:
            raise CloudPublishError(f"invalid cloud publish source for {ticker}")
        if not isinstance(scan_order, int) or isinstance(scan_order, bool) or scan_order < 0:
            raise CloudPublishError(f"invalid cloud publish scanOrder for {ticker}")
        if not isinstance(record, Mapping):
            raise CloudPublishError(f"cloud publish record is not an object: {ticker}")
        if publish_record_hash(row) != row.get("recordHash"):
            raise CloudPublishError(f"cloud publish record hash mismatch: {ticker}")
        candidate_count += source == "candidate"
        excluded_count += source == "excluded"

    counts = manifest["counts"]
    if (
        counts["candidates"] != candidate_count
        or counts["excluded"] != excluded_count
        or counts["total"] != len(plan.records)
    ):
        raise CloudPublishError("cloud publish counts do not reconcile")
    if records_hash(plan.records) != manifest["recordsHash"]:
        raise CloudPublishError("cloud records hash mismatch")
    expected_catalog = build_field_catalog([row["record"] for row in plan.records])
    if expected_catalog != manifest["fieldCatalog"]:
        raise CloudPublishError("cloud field catalog mismatch")


def _retry_after_seconds(error: HTTPError, attempt: int) -> float:
    raw = error.headers.get("Retry-After") if error.headers else None
    try:
        return min(30.0, max(0.0, float(raw))) if raw is not None else min(8.0, 2**attempt)
    except ValueError:
        return min(8.0, 2**attempt)


def post_json_with_retry(
    endpoint: str,
    token: str,
    payload: Mapping[str, Any],
    *,
    max_attempts: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
) -> Mapping[str, Any]:
    body = canonical_json_bytes(payload)
    if len(body) > MAX_CONTROL_BODY_BYTES:
        raise CloudPublishError("cloud publish request exceeds Edge body limit")
    for attempt in range(max_attempts):
        request = Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "StockScout-EOD/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, Mapping):
                    raise CloudPublishError("cloud publisher returned a non-object response")
                return result
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt + 1 >= max_attempts:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise CloudPublishError(
                    f"cloud publish HTTP {exc.code}: {detail or exc.reason}"
                ) from exc
            sleeper(_retry_after_seconds(exc, attempt))
        except (TimeoutError, URLError) as exc:
            if attempt + 1 >= max_attempts:
                raise CloudPublishError(f"cloud publish network failure: {exc}") from exc
            sleeper(min(8.0, 2**attempt))
    raise CloudPublishError("cloud publish retry loop exhausted")


def _data(response: Mapping[str, Any], action: str) -> Mapping[str, Any]:
    data = response.get("data")
    if response.get("ok") is not True or not isinstance(data, Mapping):
        raise CloudPublishError(f"cloud {action} failed: {response.get('error') or 'invalid response'}")
    return data


def publish_cloud_plan(
    plan: CloudPublishPlan,
    *,
    endpoint: str,
    token: str,
    post: PostJson | None = None,
) -> Mapping[str, Any]:
    verify_cloud_publish_plan(plan)
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.netloc:
        raise CloudPublishError("cloud publish endpoint must be HTTPS")
    sender = post or post_json_with_retry
    begin = _data(sender(endpoint, token, plan.begin_payload), "begin")
    upload_id = str(begin.get("uploadId") or "")
    if not UUID_RE.fullmatch(upload_id):
        raise CloudPublishError("cloud begin response has an invalid uploadId")
    for payload in plan.chunk_payloads(upload_id):
        chunk = _data(sender(endpoint, token, payload), "chunk")
        if str(chunk.get("uploadId") or "") != upload_id:
            raise CloudPublishError("cloud chunk response uploadId mismatch")
    finalized = _data(
        sender(endpoint, token, plan.finalize_payload(upload_id)),
        "finalize",
    )
    if (
        finalized.get("runId") != plan.manifest["runId"]
        or finalized.get("recordCount") != len(plan.records)
        or finalized.get("recordsHash") != plan.manifest["recordsHash"]
    ):
        raise CloudPublishError("cloud finalize response does not match the publish plan")
    return finalized


def publish_cloud_snapshot(
    *,
    public_dir: str | Path,
    endpoint: str,
    chunk_size: int = MAX_CHUNK_SIZE,
    audience: str = AUDIENCE,
) -> Mapping[str, Any]:
    plan = build_cloud_publish_plan(public_dir, chunk_size=chunk_size)
    token = github_oidc_token(audience)
    return publish_cloud_plan(plan, endpoint=endpoint, token=token)


def maintain_cloud_snapshot(
    *, endpoint: str, protected_run_id: str, audience: str = AUDIENCE
) -> Mapping[str, Any]:
    """Remove abandoned uploads and chart runs not used by cloud or Pages."""
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.netloc:
        raise CloudPublishError("cloud publish endpoint must be HTTPS")
    if not RUN_ID_RE.fullmatch(protected_run_id):
        raise CloudPublishError("protected chart run ID is invalid")
    token = github_oidc_token(audience)
    return _data(
        post_json_with_retry(
            endpoint,
            token,
            {"action": "cleanup", "protectedRunId": protected_run_id},
        ),
        "cleanup",
    )


def evaluate_cloud_alerts(
    *,
    endpoint: str,
    audience: str = AUDIENCE,
) -> dict[str, Any]:
    """Evaluate owner EOD alerts only after the new snapshot is active."""
    parts = urlsplit(endpoint)
    if parts.scheme != "https" or not parts.netloc:
        raise CloudPublishError("cloud publish endpoint must be HTTPS")
    token = github_oidc_token(audience)
    return _data(
        post_json_with_retry(endpoint, token, {"action": "evaluate_alerts"}),
        "evaluate_alerts",
    )
