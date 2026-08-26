"""Versioned wire contracts shared by the scanner, Pages PWA, and MCP index.

The public contract is intentionally smaller than the internal ``Candidate``
model. Full derived candidate evidence remains available in the detail shard;
the bounded chart contract is separate from candidate assets, while provider
caches and operator state remain forbidden.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

SCHEMA_VERSION = "stockscout-eod/v1"
MANIFEST_VERSION = 1

TradeStatus = Literal[
    "entry_ready",
    "trigger_pending",
    "wait_for_retest",
    "not_tradeable",
    "insufficient_data",
]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TradePlanV1(WireModel):
    status: TradeStatus
    reason_codes: list[str] = Field(default_factory=list, alias="reasonCodes")
    trigger_state: Literal["pending", "fresh", "extended", "unavailable"] = Field(
        alias="triggerState"
    )
    trigger_reference_level: float | None = Field(None, alias="triggerReferenceLevel")
    entry_reference_level: float | None = Field(None, alias="entryReferenceLevel")
    structural_invalidation_level: float | None = Field(
        None, alias="structuralInvalidationLevel"
    )
    entry_risk_pct: float | None = Field(None, alias="entryRiskPct")
    extension_atr: float | None = Field(None, alias="extensionAtr")
    tactical_stop_level: float | None = Field(None, alias="tacticalStopLevel")
    tactical_risk_pct: float | None = Field(None, alias="tacticalRiskPct")
    source: str
    version: int


class CandidateSummaryV1(WireModel):
    id: str
    mode: Literal["bottom-fishing", "next", "ryan-original"] | None = None
    price_basis: Literal["split_only", "split_div"] | None = Field(None, alias="priceBasis")
    canonical_url: str = Field(alias="canonicalUrl")
    ticker: str
    as_of: str = Field(alias="asOf")
    scan_order: int = Field(alias="scanOrder", ge=0)
    score: float
    focus_score: float | None = Field(None, alias="focusScore")
    focus_blend: float = Field(alias="focusBlend")
    headline_rank: int | None = Field(None, alias="headlineRank")
    price: float | None = None
    sector: str | None = None
    industry: str | None = None
    actionability: str
    primary_setup: str | None = Field(None, alias="primarySetup")
    setup_names: list[str] = Field(default_factory=list, alias="setupNames")
    setup_tags: list[str] = Field(default_factory=list, alias="setupTags")
    setup_states: dict[str, str | None] = Field(default_factory=dict, alias="setupStates")
    trade_plan: TradePlanV1 | None = Field(None, alias="tradePlan")
    trade_status: TradeStatus | None = Field(None, alias="tradeStatus")
    entry_risk_pct: float | None = Field(None, alias="entryRiskPct")
    tactical_stop_level: float | None = Field(None, alias="tacticalStopLevel")
    risk_level: str = Field("none", alias="riskLevel")
    risk_reasons: list[dict[str, str]] = Field(default_factory=list, alias="riskReasons")
    excluded: bool = False
    excluded_reason: str | None = Field(None, alias="excludedReason")
    data_status: str = Field("OK", alias="dataStatus")
    legacy_confirmation_status: str = Field("UNAVAILABLE", alias="legacyConfirmationStatus")
    setup: str | None = None
    setup_match_count: int = Field(0, alias="setupMatchCount")
    opportunity_score: float = Field(alias="opportunityScore")
    opportunity_rank: int = Field(alias="opportunityRank")
    opportunity_tier: str = Field(alias="opportunityTier")
    stage: int | None = None
    stage_name: str | None = Field(None, alias="stageName")
    rs_rank: float | None = Field(None, alias="rsRank")
    rs_score: float | None = Field(None, alias="rsScore")
    rs_3m: float | None = Field(None, alias="rs3m")
    rs_6m: float | None = Field(None, alias="rs6m")
    rs_new_high: bool | None = Field(None, alias="rsNewHigh")
    change_20d: float | None = Field(None, alias="change20d")
    return_3m: float | None = Field(None, alias="return3m")
    return_6m: float | None = Field(None, alias="return6m")
    volume_ratio: float | None = Field(None, alias="volumeRatio")
    avg_dollar_volume_50: float | None = Field(None, alias="avgDollarVolume50")
    atr_pct: float | None = Field(None, alias="atrPct")
    distance_50: float | None = Field(None, alias="distance50")
    distance_200: float | None = Field(None, alias="distance200")
    distance_30w: float | None = Field(None, alias="distance30w")
    from_52w_high: float | None = Field(None, alias="from52wHigh")
    trend_template_passes: int | None = Field(None, alias="trendTemplatePasses")
    base_weeks: int | None = Field(None, alias="baseWeeks")
    base_depth_pct: float | None = Field(None, alias="baseDepthPct")
    breakout_pct: float | None = Field(None, alias="breakoutPct")
    extended: bool = False
    sma50: float | None = None
    sma150: float | None = None
    sma200: float | None = None
    structure_score: float | None = Field(None, alias="structureScore")
    base_score: float | None = Field(None, alias="baseScore")
    trigger_score: float | None = Field(None, alias="triggerScore")
    accumulation_score: float | None = Field(None, alias="accumulationScore")
    long_base_score: float | None = Field(None, alias="longBaseScore")
    crash_base_score: float | None = Field(None, alias="crashBaseScore")
    rwb_squeeze_score: float | None = Field(None, alias="rwbSqueezeScore")
    ema_stack_launch_score: float | None = Field(None, alias="emaStackLaunchScore")
    ma_cluster_score: float | None = Field(None, alias="maClusterScore")
    sma_compression_pct: float | None = Field(None, alias="smaCompressionPct")
    ma_cluster_width_pct: float | None = Field(None, alias="maClusterWidthPct")
    fundamental_evidence_score: float | None = Field(None, alias="fundamentalEvidenceScore")
    fundamental_evidence_confidence: float | None = Field(
        None, alias="fundamentalEvidenceConfidence"
    )
    changed_today: bool | None = Field(None, alias="changedToday")
    new_universe_member: bool | None = Field(None, alias="newUniverseMember")
    change_impact: float | None = Field(None, alias="changeImpact")
    change_labels: list[str] = Field(default_factory=list, alias="changeLabels")


class CandidateDetailV1(CandidateSummaryV1):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    legacy_confirmation: dict[str, Any] = Field(
        default_factory=dict, alias="legacyConfirmation"
    )


class AssetDescriptorV1(WireModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    count: int = Field(ge=0)
    content_type: str = Field("application/json", alias="contentType")
    pattern: str | None = None
    bucket_count: int | None = Field(None, alias="bucketCount", ge=1)
    coverage_pct: float | None = Field(None, alias="coveragePct", ge=0, le=100)


class ScanCountsV1(WireModel):
    universe: int = Field(ge=0)
    candidates: int = Field(ge=0)
    excluded: int = Field(ge=0)
    failed: int = Field(ge=0)


class HealthCheckV1(WireModel):
    code: str
    passed: bool
    detail: str


class HealthV1(WireModel):
    status: Literal["healthy", "degraded", "failed"]
    coverage_pct: float = Field(alias="coveragePct", ge=0, le=100)
    checks: list[HealthCheckV1]


class ScanManifestV1(WireModel):
    manifest_version: Literal[1] = Field(MANIFEST_VERSION, alias="manifestVersion")
    schema_version: Literal["stockscout-eod/v1"] = Field(SCHEMA_VERSION, alias="schemaVersion")
    mode: Literal["bottom-fishing", "next", "ryan-original"] | None = None
    run_id: str = Field(alias="runId")
    session_date: str = Field(alias="sessionDate")
    market_data_date: str = Field(alias="marketDataDate")
    generated_at: str = Field(alias="generatedAt")
    status: Literal["healthy", "degraded"]
    price_mode: Literal["split_only", "split_div"] = Field(alias="priceMode")
    chart_status: Literal["ready", "stale", "missing"] = Field(
        "missing",
        alias="chartStatus",
        validation_alias=AliasChoices("chartStatus", "ownerChartStatus"),
    )
    counts: ScanCountsV1
    health: HealthV1
    provenance: dict[str, Any]
    versions: dict[str, str | int]
    assets: dict[str, AssetDescriptorV1]


class CloudFieldCatalogEntryV1(WireModel):
    field: str = Field(pattern=r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
    types: list[Literal["boolean", "null", "number", "string"]]
    count: int = Field(ge=0)
    example: Any = None


class CloudPublishCountsV1(WireModel):
    candidates: int = Field(ge=0)
    excluded: int = Field(ge=0)
    total: int = Field(ge=1)


class CloudPublishManifestV1(WireModel):
    """Control manifest accepted by the OIDC publish Edge Function.

    This is intentionally separate from :class:`ScanManifestV1`: the Pages
    pointer describes immutable assets, while this wrapper commits the exact
    candidate record set that Supabase will activate atomically.
    """

    schema_version: Literal["stockscout-eod/v1"] = Field(alias="schemaVersion")
    run_id: str = Field(alias="runId", pattern=r"^[A-Za-z0-9._:-]{1,100}$")
    scan_date: str = Field(alias="scanDate", pattern=r"^\d{4}-\d{2}-\d{2}$")
    market_data_date: str = Field(
        alias="marketDataDate", pattern=r"^\d{4}-\d{2}-\d{2}$"
    )
    generated_at: str = Field(alias="generatedAt")
    price_mode: Literal["split_only", "split_div"] = Field(alias="priceMode")
    health: HealthV1
    counts: CloudPublishCountsV1
    records_hash: str = Field(alias="recordsHash", pattern=r"^[0-9a-f]{64}$")
    manifest_hash: str = Field(alias="manifestHash", pattern=r"^[0-9a-f]{64}$")
    ranking_version: str | None = Field(None, alias="rankingVersion")
    detector_version: str | None = Field(None, alias="detectorVersion")
    provenance: dict[str, Any]
    asset_hashes: dict[str, str] = Field(alias="assetHashes")
    field_catalog: list[CloudFieldCatalogEntryV1] = Field(alias="fieldCatalog")


class ChartPayloadV1(WireModel):
    schema_version: Literal["stockscout-eod/chart-v1"] = Field(
        "stockscout-eod/chart-v1", alias="schemaVersion"
    )
    ticker: str
    as_of: str = Field(alias="asOf")
    price_mode: Literal["split_only", "split_div"] = Field(alias="priceMode")
    columns: list[str] = Field(default_factory=lambda: ["t", "o", "h", "l", "c", "v"])
    daily: list[list[int | float]] = Field(default_factory=list)
    weekly: list[list[int | float]] = Field(default_factory=list)


class ChartShardV1(WireModel):
    name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    ticker_count: int = Field(alias="tickerCount", ge=0)


class ChartManifestV1(WireModel):
    schema_version: Literal["stockscout-eod/charts-v1"] = Field(
        "stockscout-eod/charts-v1", alias="schemaVersion"
    )
    run_id: str = Field(alias="runId")
    session_date: str = Field(alias="sessionDate")
    generated_at: str = Field(alias="generatedAt")
    price_mode: Literal["split_only", "split_div"] = Field(alias="priceMode")
    requested: int = Field(ge=0)
    available: int = Field(ge=0)
    coverage_pct: float = Field(alias="coveragePct", ge=0, le=100)
    storage_base_url: str = Field(alias="storageBaseUrl")
    shards: list[ChartShardV1]
    shards_by_ticker: dict[str, str] = Field(default_factory=dict, alias="shardsByTicker")


class RawScanEnvelopeV1(WireModel):
    schema_version: Literal["stockscout-engine/v1"] = Field(
        "stockscout-engine/v1", alias="schemaVersion"
    )
    run_id: str = Field(alias="runId")
    session_date: str = Field(alias="sessionDate")
    generated_at: str = Field(alias="generatedAt")
    price_mode: Literal["split_only", "split_div"] = Field(alias="priceMode")
    candidates: list[dict[str, Any]]
    excluded: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any]
    stage_rows: list[dict[str, Any]] = Field(default_factory=list, alias="stageRows")
    market: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    versions: dict[str, str | int] = Field(default_factory=dict)


def wire_dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)
