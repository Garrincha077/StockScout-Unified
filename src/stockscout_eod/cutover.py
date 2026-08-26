"""Derived evidence ledger for the five-session production cutover.

The comparator is intentionally read-only with respect to its three sanitized
scan inputs.  It joins records only by ``(ticker, session_date)`` and persists
an allowlisted audit projection; candidate payloads and input paths are never
copied into the ledger.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

import pandas_market_calendars as mcal
from pydantic import Field

from stockscout_eod.contracts import WireModel, wire_dump
from stockscout_eod.jsonio import atomic_write_json, canonical_json_bytes, sha256_bytes

CUTOVER_SCHEMA_VERSION = "stockscout-eod/cutover-ledger-v1"
REQUIRED_GREEN_SESSIONS = 5
SOURCE_NAMES = ("new", "local", "stable")
PAIR_NAMES = (("new", "local"), ("new", "stable"), ("local", "stable"))
NYSE = mcal.get_calendar("NYSE")

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.^/-]{0,31}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


class CutoverError(ValueError):
    """Raised when an input cannot provide trustworthy cutover evidence."""


class ProviderEvidenceV1(WireModel):
    provider: str | None = None
    data_date: str | None = Field(None, alias="dataDate")
    price_mode: str | None = Field(None, alias="priceMode")
    data_status: str | None = Field(None, alias="dataStatus")
    primary_provider: str | None = Field(None, alias="primaryProvider")
    fallback_provider: str | None = Field(None, alias="fallbackProvider")
    universe_source: str | None = Field(None, alias="universeSource")


class SourceEvidenceV1(WireModel):
    run_id: str | None = Field(None, alias="runId")
    input_sha256: str = Field(alias="inputSha256", pattern=r"^[0-9a-f]{64}$")
    candidates: int = Field(ge=0)
    excluded: int = Field(ge=0)
    total: int = Field(ge=1)
    provenance: ProviderEvidenceV1


class RecordDifferenceV1(WireModel):
    ticker: str
    date: str
    categories: list[str]
    left_presence: Literal["candidate", "excluded", "missing"] = Field(
        alias="leftPresence"
    )
    right_presence: Literal["candidate", "excluded", "missing"] = Field(
        alias="rightPresence"
    )
    left_setup_hits: list[str] = Field(default_factory=list, alias="leftSetupHits")
    right_setup_hits: list[str] = Field(default_factory=list, alias="rightSetupHits")
    trade_plan_changed_fields: list[str] = Field(
        default_factory=list, alias="tradePlanChangedFields"
    )
    left_trade_plan_hash: str | None = Field(None, alias="leftTradePlanHash")
    right_trade_plan_hash: str | None = Field(None, alias="rightTradePlanHash")


class ProviderDivergenceV1(RecordDifferenceV1):
    left_provenance: ProviderEvidenceV1 = Field(alias="leftProvenance")
    right_provenance: ProviderEvidenceV1 = Field(alias="rightProvenance")


class RankingDifferenceV1(WireModel):
    ticker: str
    date: str
    left_position: int | None = Field(None, alias="leftPosition")
    right_position: int | None = Field(None, alias="rightPosition")


class RankingEvidenceV1(WireModel):
    compared_count: int = Field(alias="comparedCount", ge=0)
    exact: bool
    left_order_hash: str = Field(alias="leftOrderHash", pattern=r"^[0-9a-f]{64}$")
    right_order_hash: str = Field(alias="rightOrderHash", pattern=r"^[0-9a-f]{64}$")
    differences: list[RankingDifferenceV1] = Field(default_factory=list)


class PairComparisonV1(WireModel):
    left_source: Literal["new", "local", "stable"] = Field(alias="leftSource")
    right_source: Literal["new", "local", "stable"] = Field(alias="rightSource")
    common_keys: int = Field(alias="commonKeys", ge=0)
    comparable_keys: int = Field(alias="comparableKeys", ge=0)
    blocking_mismatches: list[RecordDifferenceV1] = Field(
        default_factory=list, alias="blockingMismatches"
    )
    provider_divergences: list[ProviderDivergenceV1] = Field(
        default_factory=list, alias="providerDivergences"
    )
    ranking: RankingEvidenceV1
    green: bool


class SessionEvidenceV1(WireModel):
    session_date: str = Field(alias="sessionDate")
    evaluated_at: str = Field(alias="evaluatedAt")
    status: Literal["green", "red"]
    sources: dict[str, SourceEvidenceV1]
    comparisons: list[PairComparisonV1]
    blocking_issue_count: int = Field(alias="blockingIssueCount", ge=0)
    provider_divergence_count: int = Field(alias="providerDivergenceCount", ge=0)


class CutoverReadinessV1(WireModel):
    ready: bool
    required_green_sessions: Literal[5] = Field(
        REQUIRED_GREEN_SESSIONS, alias="requiredGreenSessions"
    )
    consecutive_green_sessions: int = Field(alias="consecutiveGreenSessions", ge=0)
    latest_session_date: str | None = Field(None, alias="latestSessionDate")
    streak_dates: list[str] = Field(default_factory=list, alias="streakDates")
    reason: str


class CutoverLedgerV1(WireModel):
    schema_version: Literal["stockscout-eod/cutover-ledger-v1"] = Field(
        CUTOVER_SCHEMA_VERSION, alias="schemaVersion"
    )
    generated_at: str = Field(alias="generatedAt")
    sessions: list[SessionEvidenceV1]
    readiness: CutoverReadinessV1


@dataclass(frozen=True)
class _Record:
    ticker: str
    session_date: str
    excluded: bool
    scan_order: int | None
    setup_hits: tuple[str, ...]
    trade_plan: dict[str, Any] | None
    provenance: ProviderEvidenceV1

    @property
    def key(self) -> tuple[str, str]:
        return self.ticker, self.session_date

    @property
    def presence(self) -> Literal["candidate", "excluded"]:
        return "excluded" if self.excluded else "candidate"

    @property
    def provider_signature(self) -> bytes:
        # Only fields that describe the actual row's market-data basis are used
        # for attribution.  Operational labels such as local/new are excluded.
        return canonical_json_bytes(
            {
                "provider": self.provenance.provider,
                "dataDate": self.provenance.data_date,
                "priceMode": self.provenance.price_mode,
                "dataStatus": self.provenance.data_status,
            }
        )


@dataclass(frozen=True)
class _NormalizedScan:
    source: str
    session_date: str
    records: dict[tuple[str, str], _Record]
    evidence: SourceEvidenceV1

    @property
    def candidates(self) -> list[_Record]:
        return sorted(
            (row for row in self.records.values() if not row.excluded),
            key=lambda row: (row.scan_order if row.scan_order is not None else 10**9, row.ticker),
        )


def _safe_scalar(value: Any) -> str | None:
    if value is None or (
        isinstance(value, (Mapping, Sequence)) and not isinstance(value, str)
    ):
        return None
    text = str(value).strip().replace("\r", " ").replace("\n", " ").replace("|", "/")
    if not text:
        return None
    return text[:128]


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _provider_evidence(
    payload: Mapping[str, Any], row: Mapping[str, Any] | None = None
) -> ProviderEvidenceV1:
    top_provenance = _mapping(payload.get("provenance"))
    row = row or {}
    row_provenance = _mapping(row.get("provenance"))

    def pick(*keys: str) -> Any:
        return (
            _first(row, *keys)
            or _first(row_provenance, *keys)
            or _first(top_provenance, *keys)
            or _first(payload, *keys)
        )

    primary = pick("primaryProvider", "primary_provider")
    return ProviderEvidenceV1(
        provider=_safe_scalar(
            pick("providerUsed", "provider_used", "dataProvider", "data_provider", "provider")
            or primary
        ),
        dataDate=_safe_scalar(
            pick(
                "dataLastDate",
                "data_last_date",
                "marketDataDate",
                "market_data_date",
            )
        ),
        priceMode=_safe_scalar(pick("priceMode", "price_mode")),
        dataStatus=_safe_scalar(pick("dataStatus", "data_status")),
        primaryProvider=_safe_scalar(primary),
        fallbackProvider=_safe_scalar(pick("fallbackProvider", "fallback_provider")),
        universeSource=_safe_scalar(pick("universeSource", "universe_source")),
    )


def _date_text(value: Any, *, label: str) -> str:
    try:
        result = date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise CutoverError(f"{label} must be YYYY-MM-DD") from exc
    return result.isoformat()


def _root_session_date(payload: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> str:
    raw = _first(
        payload,
        "sessionDate",
        "session_date",
        "scanDate",
        "scan_date",
        "asOf",
        "as_of",
        "date",
    )
    if raw is not None:
        return _date_text(raw, label="session date")
    observed = {
        _date_text(value, label="candidate date")
        for row in rows
        if (value := _first(row, "asOf", "as_of", "sessionDate", "session_date", "date"))
        is not None
    }
    if len(observed) != 1:
        raise CutoverError("scan must expose one unambiguous session date")
    return observed.pop()


def _rows(value: Any, *, label: str) -> tuple[list[Mapping[str, Any]], bool]:
    if value is None:
        return [], False
    if isinstance(value, Mapping):
        rows: list[Mapping[str, Any]] = []
        for ticker, candidate in value.items():
            if not isinstance(candidate, Mapping):
                raise CutoverError(f"{label} must contain JSON objects")
            row = dict(candidate)
            row.setdefault("ticker", ticker)
            rows.append(row)
        return rows, True
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not all(isinstance(row, Mapping) for row in value):
            raise CutoverError(f"{label} must contain JSON objects")
        return list(value), False
    raise CutoverError(f"{label} must be an array or ticker-keyed object")


def _extract_rows(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], bool]:
    candidates_value = _first(payload, "candidates", "universe", "rows", "records")
    candidates, candidate_mapping = _rows(candidates_value, label="candidates")
    excluded_value = payload.get("excluded")
    excluded, excluded_mapping = _rows(
        excluded_value if isinstance(excluded_value, (Mapping, Sequence)) else None,
        label="excluded",
    )
    if not candidates and not excluded:
        raise CutoverError("scan has no candidate or excluded rows")
    return candidates, excluded, candidate_mapping or excluded_mapping


def _ticker(value: Any) -> str:
    ticker = str(value or "").strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise CutoverError(f"invalid or unsanitized ticker: {ticker!r}")
    return ticker


def _setup_name(value: Any) -> str:
    text = str(value or "").strip()
    if not _SAFE_NAME_RE.fullmatch(text):
        raise CutoverError(f"invalid or unsanitized setup name: {text!r}")
    return text


def _setup_hits(row: Mapping[str, Any]) -> tuple[str, ...]:
    explicit_present = any(key in row for key in ("setupNames", "setup_names", "setupTags"))
    if explicit_present:
        explicit = _first(row, "setupNames", "setup_names", "setupTags") or []
        if not isinstance(explicit, Sequence) or isinstance(explicit, (str, bytes, bytearray)):
            raise CutoverError("setup names must be an array")
        return tuple(sorted({_setup_name(value) for value in explicit}))

    setups = _first(row, "setupHits", "setup_hits", "setups")
    if isinstance(setups, Mapping):
        hits = {
            _setup_name(name)
            for name, value in setups.items()
            if value is True
            or (isinstance(value, Mapping) and value.get("triggered") is True)
        }
        return tuple(sorted(hits))

    fallback = _first(row, "primarySetup", "primary_setup", "setup")
    return (_setup_name(fallback),) if fallback else ()


_TRADE_PLAN_FIELDS = {
    "status": ("status",),
    "reasonCodes": ("reasonCodes", "reason_codes"),
    "triggerState": ("triggerState", "trigger_state"),
    "triggerReferenceLevel": ("triggerReferenceLevel", "trigger_reference_level"),
    "entryReferenceLevel": ("entryReferenceLevel", "entry_reference_level"),
    "structuralInvalidationLevel": (
        "structuralInvalidationLevel",
        "structural_invalidation_level",
    ),
    "entryRiskPct": ("entryRiskPct", "entry_risk_pct"),
    "extensionAtr": ("extensionAtr", "extension_atr"),
    "tacticalStopLevel": ("tacticalStopLevel", "tactical_stop_level"),
    "tacticalRiskPct": ("tacticalRiskPct", "tactical_risk_pct"),
    "source": ("source",),
    "version": ("version",),
}
_NUMERIC_TRADE_FIELDS = {
    "triggerReferenceLevel",
    "entryReferenceLevel",
    "structuralInvalidationLevel",
    "entryRiskPct",
    "extensionAtr",
    "tacticalStopLevel",
    "tacticalRiskPct",
}


def _normalized_number(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise CutoverError("trade-plan numeric field cannot be boolean")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CutoverError("trade-plan numeric field is not finite numeric data") from exc
    if not decimal.is_finite():
        raise CutoverError("trade-plan numeric field is not finite numeric data")
    if decimal == 0:
        return "0"
    return format(decimal.normalize(), "f")


def _trade_plan(row: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = _first(row, "tradePlan", "trade_plan")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise CutoverError("tradePlan must be an object or null")
    normalized: dict[str, Any] = {}
    for canonical, aliases in _TRADE_PLAN_FIELDS.items():
        value = _first(raw, *aliases)
        if canonical == "reasonCodes":
            values = value or []
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
                raise CutoverError("trade-plan reasonCodes must be an array")
            normalized[canonical] = sorted({_setup_name(item) for item in values})
        elif canonical in _NUMERIC_TRADE_FIELDS:
            normalized[canonical] = _normalized_number(value)
        elif canonical == "version":
            normalized[canonical] = str(value) if value is not None else None
        else:
            normalized[canonical] = _safe_scalar(value)
    return normalized


def _scan_order(row: Mapping[str, Any], index: int, *, mapping_input: bool) -> int:
    raw = _first(row, "scanOrder", "scan_order")
    if raw is None:
        if mapping_input:
            raise CutoverError("ticker-keyed candidates require explicit scanOrder")
        return index
    if isinstance(raw, bool):
        raise CutoverError("scanOrder must be a non-negative integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise CutoverError("scanOrder must be a non-negative integer") from exc
    if value < 0 or str(value) != str(raw).strip():
        raise CutoverError("scanOrder must be a non-negative integer")
    return value


def _normalize_scan(path: str | Path, source: str) -> _NormalizedScan:
    location = Path(path)
    try:
        raw_bytes = location.read_bytes()
        payload = json.loads(raw_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise CutoverError(f"cannot read {source} sanitized scan") from exc
    if not isinstance(payload, Mapping):
        raise CutoverError(f"{source} scan root must be an object")

    candidates, excluded, mapping_input = _extract_rows(payload)
    session_date = _root_session_date(payload, [*candidates, *excluded])
    records: dict[tuple[str, str], _Record] = {}
    candidate_orders: set[int] = set()

    def add(row: Mapping[str, Any], *, collection_excluded: bool, index: int) -> None:
        ticker = _ticker(row.get("ticker"))
        row_date_raw = _first(row, "asOf", "as_of", "sessionDate", "session_date", "date")
        row_date = _date_text(row_date_raw, label=f"{ticker} date") if row_date_raw else session_date
        if row_date != session_date:
            raise CutoverError(
                f"{source} row {ticker} has date {row_date}; expected {session_date}"
            )
        excluded_flag = collection_excluded or row.get("excluded") is True
        order = None if excluded_flag else _scan_order(row, index, mapping_input=mapping_input)
        if order is not None and order in candidate_orders:
            raise CutoverError(f"{source} has duplicate scanOrder {order}")
        if order is not None:
            candidate_orders.add(order)
        record = _Record(
            ticker=ticker,
            session_date=row_date,
            excluded=excluded_flag,
            scan_order=order,
            setup_hits=_setup_hits(row),
            trade_plan=_trade_plan(row),
            provenance=_provider_evidence(payload, row),
        )
        if record.key in records:
            raise CutoverError(
                f"{source} has duplicate (ticker,date) key {ticker}/{session_date}"
            )
        records[record.key] = record

    for index, row in enumerate(candidates):
        add(row, collection_excluded=False, index=index)
    for index, row in enumerate(excluded):
        add(row, collection_excluded=True, index=index)

    run_id_raw = _first(payload, "runId", "run_id")
    run_id = _safe_scalar(run_id_raw)
    if run_id is not None and not _SAFE_NAME_RE.fullmatch(run_id):
        run_id = None
    evidence = SourceEvidenceV1(
        runId=run_id,
        inputSha256=sha256_bytes(canonical_json_bytes(payload)),
        candidates=sum(not row.excluded for row in records.values()),
        excluded=sum(row.excluded for row in records.values()),
        total=len(records),
        provenance=_provider_evidence(payload),
    )
    return _NormalizedScan(source, session_date, records, evidence)


def _plan_hash(plan: Mapping[str, Any] | None) -> str | None:
    return sha256_bytes(canonical_json_bytes(plan)) if plan is not None else None


def _plan_changed_fields(
    left: Mapping[str, Any] | None, right: Mapping[str, Any] | None
) -> list[str]:
    if left is None and right is None:
        return []
    if left is None or right is None:
        return ["availability"]
    return sorted(key for key in _TRADE_PLAN_FIELDS if left.get(key) != right.get(key))


def _presence(record: _Record | None) -> Literal["candidate", "excluded", "missing"]:
    return record.presence if record else "missing"


def _global_signature(scan: _NormalizedScan) -> bytes:
    provenance = scan.evidence.provenance
    return canonical_json_bytes(
        {
            "provider": provenance.provider or provenance.primary_provider,
            "dataDate": provenance.data_date,
            "priceMode": provenance.price_mode,
            "dataStatus": provenance.data_status,
        }
    )


def _provider_changed(
    left: _Record | None,
    right: _Record | None,
    left_scan: _NormalizedScan,
    right_scan: _NormalizedScan,
) -> bool:
    if left is not None and right is not None:
        return left.provider_signature != right.provider_signature
    return _global_signature(left_scan) != _global_signature(right_scan)


def _record_difference(
    key: tuple[str, str],
    left: _Record | None,
    right: _Record | None,
    categories: list[str],
) -> RecordDifferenceV1:
    return RecordDifferenceV1(
        ticker=key[0],
        date=key[1],
        categories=categories,
        leftPresence=_presence(left),
        rightPresence=_presence(right),
        leftSetupHits=list(left.setup_hits if left else ()),
        rightSetupHits=list(right.setup_hits if right else ()),
        tradePlanChangedFields=_plan_changed_fields(
            left.trade_plan if left else None, right.trade_plan if right else None
        ),
        leftTradePlanHash=_plan_hash(left.trade_plan if left else None),
        rightTradePlanHash=_plan_hash(right.trade_plan if right else None),
    )


def _provider_divergence(
    difference: RecordDifferenceV1,
    left: _Record | None,
    right: _Record | None,
    left_scan: _NormalizedScan,
    right_scan: _NormalizedScan,
) -> ProviderDivergenceV1:
    left_provenance = left.provenance if left else left_scan.evidence.provenance
    right_provenance = right.provenance if right else right_scan.evidence.provenance
    payload = wire_dump(difference)
    return ProviderDivergenceV1(
        **payload,
        leftProvenance=wire_dump(left_provenance),
        rightProvenance=wire_dump(right_provenance),
    )


def _ranking_evidence(
    left: _NormalizedScan, right: _NormalizedScan
) -> RankingEvidenceV1:
    comparable = {
        key
        for key in left.records.keys() & right.records.keys()
        if not left.records[key].excluded
        and not right.records[key].excluded
        and left.records[key].provider_signature == right.records[key].provider_signature
    }
    left_order = [row.key for row in left.candidates if row.key in comparable]
    right_order = [row.key for row in right.candidates if row.key in comparable]
    exact = left_order == right_order
    left_positions = {key: index for index, key in enumerate(left_order)}
    right_positions = {key: index for index, key in enumerate(right_order)}
    differences = [
        RankingDifferenceV1(
            ticker=key[0],
            date=key[1],
            leftPosition=left_positions.get(key),
            rightPosition=right_positions.get(key),
        )
        for key in sorted(comparable)
        if left_positions.get(key) != right_positions.get(key)
    ]
    return RankingEvidenceV1(
        comparedCount=len(comparable),
        exact=exact,
        leftOrderHash=sha256_bytes(canonical_json_bytes(left_order)),
        rightOrderHash=sha256_bytes(canonical_json_bytes(right_order)),
        differences=differences,
    )


def _compare(left: _NormalizedScan, right: _NormalizedScan) -> PairComparisonV1:
    blocking: list[RecordDifferenceV1] = []
    provider_divergences: list[ProviderDivergenceV1] = []
    all_keys = sorted(left.records.keys() | right.records.keys())
    common_keys = left.records.keys() & right.records.keys()
    comparable_keys = 0

    for key in all_keys:
        left_record = left.records.get(key)
        right_record = right.records.get(key)
        provider_changed = _provider_changed(left_record, right_record, left, right)
        if left_record is not None and right_record is not None and not provider_changed:
            comparable_keys += 1

        categories: list[str] = []
        if _presence(left_record) != _presence(right_record):
            categories.append("membership")
        if (left_record.setup_hits if left_record else ()) != (
            right_record.setup_hits if right_record else ()
        ):
            categories.append("setup_hits")
        if (left_record.trade_plan if left_record else None) != (
            right_record.trade_plan if right_record else None
        ):
            categories.append("trade_plan")
        if provider_changed:
            categories.append("provenance")

        if not categories:
            continue
        difference = _record_difference(key, left_record, right_record, categories)
        if provider_changed:
            provider_divergences.append(
                _provider_divergence(difference, left_record, right_record, left, right)
            )
        else:
            blocking.append(difference)

    ranking = _ranking_evidence(left, right)
    green = not blocking and ranking.exact and ranking.compared_count > 0
    return PairComparisonV1(
        leftSource=left.source,
        rightSource=right.source,
        commonKeys=len(common_keys),
        comparableKeys=comparable_keys,
        blockingMismatches=blocking,
        providerDivergences=provider_divergences,
        ranking=ranking,
        green=green,
    )


def _is_nyse_session(session_date: str) -> bool:
    schedule = NYSE.schedule(start_date=session_date, end_date=session_date)
    return not schedule.empty


def build_session_evidence(
    new_scan: str | Path,
    local_scan: str | Path,
    stable_scan: str | Path,
    *,
    evaluated_at: datetime | None = None,
) -> SessionEvidenceV1:
    inputs = {"new": new_scan, "local": local_scan, "stable": stable_scan}
    scans = {name: _normalize_scan(path, name) for name, path in inputs.items()}
    dates = {scan.session_date for scan in scans.values()}
    if len(dates) != 1:
        detail = ", ".join(f"{name}={scan.session_date}" for name, scan in scans.items())
        raise CutoverError(f"all scans must have the same session date ({detail})")
    session_date = dates.pop()
    if not _is_nyse_session(session_date):
        raise CutoverError(f"{session_date} is not an NYSE session")

    comparisons = [_compare(scans[left], scans[right]) for left, right in PAIR_NAMES]
    blocking_count = sum(
        len(comparison.blocking_mismatches) + len(comparison.ranking.differences)
        + (comparison.ranking.compared_count == 0)
        for comparison in comparisons
    )
    divergence_count = sum(len(item.provider_divergences) for item in comparisons)
    evaluated = (evaluated_at or datetime.now(tz=UTC)).astimezone(UTC)
    return SessionEvidenceV1(
        sessionDate=session_date,
        evaluatedAt=evaluated.isoformat().replace("+00:00", "Z"),
        status="green" if all(item.green for item in comparisons) else "red",
        sources={name: scan.evidence for name, scan in scans.items()},
        comparisons=comparisons,
        blockingIssueCount=int(blocking_count),
        providerDivergenceCount=divergence_count,
    )


def _read_existing(path: Path) -> list[SessionEvidenceV1]:
    if not path.exists():
        return []
    try:
        ledger = CutoverLedgerV1.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CutoverError("existing cutover ledger is invalid") from exc
    session_dates = [item.session_date for item in ledger.sessions]
    if len(session_dates) != len(set(session_dates)):
        raise CutoverError("existing cutover ledger contains duplicate session dates")
    if any(set(item.sources) != set(SOURCE_NAMES) for item in ledger.sessions):
        raise CutoverError("existing cutover ledger has an invalid source set")
    return ledger.sessions


def _readiness(sessions: list[SessionEvidenceV1]) -> CutoverReadinessV1:
    if not sessions:
        return CutoverReadinessV1(
            ready=False,
            consecutiveGreenSessions=0,
            latestSessionDate=None,
            streakDates=[],
            reason="no_session_evidence",
        )

    by_date = {item.session_date: item for item in sessions}
    start = min(by_date)
    end = max(by_date)
    calendar_dates = [
        timestamp.date().isoformat()
        for timestamp in NYSE.valid_days(start_date=start, end_date=end)
    ]
    streak: list[str] = []
    for session_date in calendar_dates:
        evidence = by_date.get(session_date)
        if evidence is not None and evidence.status == "green":
            streak.append(session_date)
        else:
            streak = []

    latest = end
    ready = len(streak) >= REQUIRED_GREEN_SESSIONS and streak[-1] == latest
    if ready:
        reason = "five_consecutive_nyse_sessions_green"
    elif by_date[latest].status == "red":
        reason = "latest_session_red"
    elif len(streak) < REQUIRED_GREEN_SESSIONS:
        reason = "fewer_than_five_consecutive_green_nyse_sessions"
    else:
        reason = "latest_session_not_in_green_streak"
    return CutoverReadinessV1(
        ready=ready,
        consecutiveGreenSessions=len(streak),
        latestSessionDate=latest,
        streakDates=streak,
        reason=reason,
    )


def _markdown(ledger: CutoverLedgerV1) -> str:
    readiness = ledger.readiness
    lines = [
        "# StockScout-EOD cutover evidence",
        "",
        f"- Ready: **{'YES' if readiness.ready else 'NO'}**",
        (
            f"- Consecutive green NYSE sessions: "
            f"**{readiness.consecutive_green_sessions}/{readiness.required_green_sessions}**"
        ),
        f"- Reason: `{readiness.reason}`",
        f"- Latest session: `{readiness.latest_session_date or 'none'}`",
        "",
        "| Session | Status | Blocking | Provider divergences |",
        "|---|---:|---:|---:|",
    ]
    for session in ledger.sessions:
        lines.append(
            f"| {session.session_date} | {session.status.upper()} | "
            f"{session.blocking_issue_count} | {session.provider_divergence_count} |"
        )

    for session in ledger.sessions:
        lines.extend(["", f"## {session.session_date}", ""])
        for comparison in session.comparisons:
            lines.append(
                f"### {comparison.left_source} vs {comparison.right_source} — "
                f"{'GREEN' if comparison.green else 'RED'}"
            )
            lines.extend(
                [
                    "",
                    f"Comparable `(ticker,date)` keys: {comparison.comparable_keys}; "
                    f"ranking rows: {comparison.ranking.compared_count}.",
                    "",
                ]
            )
            if comparison.blocking_mismatches:
                lines.extend(
                    [
                        "| Blocking ticker/date | Categories |",
                        "|---|---|",
                        *[
                            f"| {item.ticker} / {item.date} | {', '.join(item.categories)} |"
                            for item in comparison.blocking_mismatches
                        ],
                        "",
                    ]
                )
            if comparison.ranking.differences:
                lines.extend(
                    [
                        "| Ranking ticker/date | Left | Right |",
                        "|---|---:|---:|",
                        *[
                            f"| {item.ticker} / {item.date} | {item.left_position} | "
                            f"{item.right_position} |"
                            for item in comparison.ranking.differences
                        ],
                        "",
                    ]
                )
            if comparison.provider_divergences:
                lines.extend(
                    [
                        "| Provider divergence ticker/date | Categories | Left provider/date | "
                        "Right provider/date |",
                        "|---|---|---|---|",
                        *[
                            (
                                f"| {item.ticker} / {item.date} | {', '.join(item.categories)} | "
                                f"{item.left_provenance.provider or 'unknown'} / "
                                f"{item.left_provenance.data_date or 'unknown'} | "
                                f"{item.right_provenance.provider or 'unknown'} / "
                                f"{item.right_provenance.data_date or 'unknown'} |"
                            )
                            for item in comparison.provider_divergences
                        ],
                        "",
                    ]
                )
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def update_cutover_ledger(
    *,
    new_scan: str | Path,
    local_scan: str | Path,
    stable_scan: str | Path,
    ledger_json: str | Path,
    ledger_markdown: str | Path,
    evaluated_at: datetime | None = None,
) -> CutoverLedgerV1:
    input_paths = {Path(value).resolve() for value in (new_scan, local_scan, stable_scan)}
    json_path = Path(ledger_json).resolve()
    markdown_path = Path(ledger_markdown).resolve()
    if json_path == markdown_path:
        raise CutoverError("JSON and Markdown outputs must be different explicit paths")
    if json_path in input_paths or markdown_path in input_paths:
        raise CutoverError("cutover outputs cannot overwrite scan inputs")

    session = build_session_evidence(
        new_scan,
        local_scan,
        stable_scan,
        evaluated_at=evaluated_at,
    )
    existing = _read_existing(json_path)
    by_date = {item.session_date: item for item in existing}
    by_date[session.session_date] = session
    sessions = [by_date[key] for key in sorted(by_date)]
    generated = (evaluated_at or datetime.now(tz=UTC)).astimezone(UTC)
    ledger = CutoverLedgerV1(
        generatedAt=generated.isoformat().replace("+00:00", "Z"),
        sessions=sessions,
        readiness=_readiness(sessions),
    )
    atomic_write_json(json_path, wire_dump(ledger))
    _atomic_write_text(markdown_path, _markdown(ledger))
    return ledger
