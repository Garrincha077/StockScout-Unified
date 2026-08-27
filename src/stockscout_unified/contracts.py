"""Versioned contracts for the three-mode public activation pointer."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ModeId = Literal["bottom-fishing", "next", "ryan-original"]
PriceBasis = Literal["split_only", "split_div"]
UNIFIED_SCHEMA_VERSION = "stockscout-unified/v1"


class ModeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: ModeId
    label: str
    price_basis: PriceBasis
    source_commit: str
    ranking: str


MODE_SPECS: dict[ModeId, ModeSpec] = {
    "bottom-fishing": ModeSpec(
        id="bottom-fishing",
        label="Bottom Fishing",
        price_basis="split_only",
        source_commit="00a2d65a62256e5bc12c4d8e1118399a96ef8c57",
        ranking="stockscout-production-order",
    ),
    "next": ModeSpec(
        id="next",
        label="Next",
        price_basis="split_div",
        source_commit="528386109c5991ab8443ece446f85a48cc1e9c53",
        ranking="stockscreener-next-canonical-order",
    ),
    "ryan-original": ModeSpec(
        id="ryan-original",
        label="Ryan Original",
        price_basis="split_div",
        source_commit="2fce788b7c95e595bdbb012bd35d3a92fcc49e5a",
        ranking="original-buy-score-descending",
    ),
}


class ModePointerV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: ModeId
    label: str
    price_basis: PriceBasis = Field(alias="priceBasis")
    status: Literal["healthy"]
    manifest_path: str = Field(alias="manifestPath", pattern=r"^modes/[a-z-]+/manifest\.json$")
    manifest_sha256: str = Field(alias="manifestSha256", pattern=r"^[0-9a-f]{64}$")
    manifest_bytes: int = Field(alias="manifestBytes", ge=1)
    candidates: int = Field(ge=0)
    excluded: int = Field(ge=0)
    chart_coverage_pct: float = Field(alias="chartCoveragePct", ge=0, le=100)
    source_commit: str = Field(alias="sourceCommit", pattern=r"^[0-9a-f]{7,40}$")
    ranking: str


class UnifiedManifestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    manifest_version: Literal[1] = Field(1, alias="manifestVersion")
    schema_version: Literal["stockscout-unified/v1"] = Field(
        UNIFIED_SCHEMA_VERSION, alias="schemaVersion"
    )
    run_id: str = Field(alias="runId", min_length=1, max_length=128)
    session_date: str = Field(alias="sessionDate", pattern=r"^\d{4}-\d{2}-\d{2}$")
    generated_at: str = Field(alias="generatedAt")
    status: Literal["healthy"]
    default_mode: Literal["bottom-fishing"] = Field("bottom-fishing", alias="defaultMode")
    modes: dict[ModeId, ModePointerV1]

    @model_validator(mode="after")
    def all_modes_are_present(self) -> UnifiedManifestV1:
        expected = set(MODE_SPECS)
        if set(self.modes) != expected:
            raise ValueError(f"exactly these modes are required: {sorted(expected)}")
        for mode, pointer in self.modes.items():
            if pointer.mode != mode:
                raise ValueError(f"mode pointer key mismatch: {mode}")
        return self
