from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class SetupResult:
    setup_name: str
    triggered: bool
    sub_state: str | None = None              # e.g. "breakout-day", "pre-breakout"
    score: float = 0.0                         # 0..100, setup-local quality score
    raw_features: dict[str, float | str | bool | None] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    failed_conditions: list[str] = field(default_factory=list)
    warning_flags: list[str] = field(default_factory=list)
    trigger_level: float | None = None
    invalidation_level: float | None = None
    # --- Faza 3 actionability fields (defaults keep this backward-compatible
    # with detectors that don't populate them yet) -------------------------
    actionability: str = "not_valid"          # bucket from actionability.classify
    actionability_reason: str = ""
    disqualifiers: list[str] = field(default_factory=list)
    base_metrics: dict[str, Any] = field(default_factory=dict)


class SetupDetector(ABC):
    """Each setup detector takes an enriched daily frame (and optionally a
    weekly frame) and returns a SetupResult for the last bar."""

    name: str = "base"

    @abstractmethod
    def detect(
        self,
        df_daily: pd.DataFrame,
        df_weekly: pd.DataFrame | None = None,
        features: dict | None = None,
    ) -> SetupResult: ...
