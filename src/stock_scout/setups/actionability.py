"""Centralized actionability classification.

Each detector (GLB, Minervini-VCP, Weinstein, Tight, HighRS) produces a
sub-state ("breakout_day", "near_pivot", "extended", ...) and a set of base
metrics. `classify()` maps that to one of six buckets that drive whether a
candidate appears in the main list, the watch list, or the Excluded section.

The thresholds are deliberately conservative — see docs/METHODOLOGY.md §7 for
the rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Bucket = Literal[
    "actionable_now",
    "near_actionable",
    "forming",
    "watch",
    "extended_too_late",
    "not_valid",
    "excluded",
]


@dataclass
class ActionabilityConfig:
    """Per-setup thresholds for the % distance from pivot. Negative values
    mean below pivot."""

    # actionable: very near pivot or freshly breaking out
    actionable_pct_min: float = -2.0
    actionable_pct_max: float = +2.0
    # near actionable: pre-breakout window or early breakout
    near_pct_min: float = -5.0
    near_pct_max: float = +5.0
    # forming: setup taking shape, price still approaching the pivot from below
    # (between forming_pct_min and near_pct_min). Surfaces EARLY candidates
    # before the trigger fires.
    forming_pct_min: float = -8.0
    # extended: too late to chase
    extended_pct_threshold: float = 8.0
    # ATR-based extension (alternative ceiling for high-vol names)
    extended_atr_threshold: float = 5.0
    # Maximum bars since the pivot was crossed for breakout to remain "fresh"
    max_bars_since_breakout_for_actionable: int = 2
    max_bars_since_breakout_for_near: int = 5
    # --- Volume dry-up awareness (O'Neil/Deepvue: constructive bases go quiet) -
    # `volume_dryup_pct` = % of the last 20 base bars below 0.8x the pre-base
    # average volume (higher = quieter, more constructive base).
    # Strong dry-up promotes a passive "watch" to "forming" (an accumulation
    # footprint worth tracking before the trigger).
    dryup_strong_pct: float = 50.0
    # When enabled, a fresh breakout out of a base that showed essentially no
    # dry-up (a "wet" base) is downgraded actionable_now -> near_actionable.
    # Off by default so it never silently re-buckets without opt-in.
    require_dryup_for_actionable: bool = False
    min_dryup_for_actionable: float = 15.0


# Per-setup overrides where the defaults don't fit
SETUP_OVERRIDES: dict[str, ActionabilityConfig] = {
    "glb": ActionabilityConfig(
        actionable_pct_min=-2.0,
        actionable_pct_max=+3.0,
        near_pct_min=-5.0,
        near_pct_max=+5.0,
        extended_pct_threshold=5.0,  # GLB breakouts are sharper, extend faster
    ),
    "minervini": ActionabilityConfig(),  # defaults
    "tight_breakout": ActionabilityConfig(
        actionable_pct_min=-1.0,
        actionable_pct_max=+2.0,
        near_pct_min=-5.0,
        near_pct_max=+2.0,
        extended_pct_threshold=5.0,
    ),
    "weinstein": ActionabilityConfig(
        # Weinstein operates weekly; "extension from 30wSMA" is the relevant
        # measure here, not distance to a pivot. Caller passes extension
        # directly via the `extension_pct` arg.
        extended_pct_threshold=25.0,
    ),
    "high_rs": ActionabilityConfig(
        extended_pct_threshold=15.0,
    ),
}


@dataclass
class ClassificationInput:
    """Everything classify() needs. Setups should populate as many fields as
    they have; missing fields fall back to permissive defaults."""

    setup_name: str
    triggered: bool
    sub_state: str | None = None
    extension_pct: float | None = None       # close vs pivot (or vs 30wSMA for Weinstein)
    extension_atr_multiples: float | None = None
    bars_since_breakout: int | None = None   # 0 = today, 1 = yesterday, None = pre-breakout
    base_length_bars: int | None = None
    base_depth_pct: float | None = None
    n_contractions: int | None = None
    is_wide_and_loose: bool = False
    volume_dryup_pct: float | None = None
    has_clear_pivot: bool = True
    disqualifiers: list[str] | None = None   # M&A, stale, illiquid_tight, etc.
    pocket_pivot: bool = False               # early institutional-footprint up-volume day
    higher_lows: int = 0                     # trailing run of ascending swing lows (accumulation base)


def classify(inp: ClassificationInput, cfg: ActionabilityConfig | None = None) -> tuple[Bucket, str]:
    """Return (bucket, reason). `reason` is a short, machine-readable code.

    Order of precedence (first matching rule wins):
      1. disqualifiers -> "excluded"
      2. !triggered -> "not_valid"
      3. base is wide-and-loose, depth > 35, or no pivot -> "excluded"
      4. extension > extended_threshold -> "extended_too_late"
      5. extension within actionable window -> "actionable_now"
      6. extension within near window -> "near_actionable"
      7. else -> "watch"
    """
    if cfg is None:
        cfg = SETUP_OVERRIDES.get(inp.setup_name, ActionabilityConfig())

    # 1. Hard disqualifiers
    disq = inp.disqualifiers or []
    if disq:
        # Pick the most informative one for the reason text
        return "excluded", f"disqualifier:{disq[0]}"

    # 2. Not triggered at all
    if not inp.triggered:
        return "not_valid", "setup_not_triggered"

    # 3. Structural rejection — wide-and-loose base, no pivot, base too shallow/deep
    if inp.is_wide_and_loose:
        return "excluded", "wide_and_loose_base"
    if not inp.has_clear_pivot:
        return "excluded", "no_clear_pivot"
    if inp.base_depth_pct is not None and inp.base_depth_pct > 35.0:
        return "excluded", "base_depth_over_35pct"

    # 4. Extension check (the headline filter — "no chasing")
    ext = inp.extension_pct
    if ext is not None:
        if ext > cfg.extended_pct_threshold:
            return "extended_too_late", f"extension_{ext:.1f}%>{cfg.extended_pct_threshold}%"
        if (
            inp.extension_atr_multiples is not None
            and inp.extension_atr_multiples > cfg.extended_atr_threshold
        ):
            return (
                "extended_too_late",
                f"atr_extension_{inp.extension_atr_multiples:.1f}>{cfg.extended_atr_threshold}",
            )

        # 5/6/7. Window-based bucketing
        if cfg.actionable_pct_min <= ext <= cfg.actionable_pct_max:
            # Within actionable window, but if it's already +X% above pivot
            # and bars_since_breakout is too high, downgrade.
            if (
                ext > 0
                and inp.bars_since_breakout is not None
                and inp.bars_since_breakout > cfg.max_bars_since_breakout_for_actionable
            ):
                if inp.bars_since_breakout <= cfg.max_bars_since_breakout_for_near:
                    return "near_actionable", f"early_breakout_{inp.bars_since_breakout}_bars_ago"
                return "extended_too_late", f"breakout_{inp.bars_since_breakout}_bars_ago"
            # Optional volume gate: a fresh breakout out of a "wet" base (no
            # dry-up) is less reliable — downgrade to near_actionable when opted in.
            if (
                cfg.require_dryup_for_actionable
                and inp.volume_dryup_pct is not None
                and inp.volume_dryup_pct < cfg.min_dryup_for_actionable
            ):
                return "near_actionable", f"breakout_wet_base_dryup_{inp.volume_dryup_pct:.0f}pct"
            return "actionable_now", f"at_pivot_{ext:+.1f}%"

        if cfg.near_pct_min <= ext <= cfg.near_pct_max:
            return "near_actionable", f"near_pivot_{ext:+.1f}%"

        # Forming: structure present, price still approaching the pivot from
        # below. Early — surfaces the setup BEFORE the breakout.
        if cfg.forming_pct_min <= ext < cfg.near_pct_min:
            return "forming", f"approaching_pivot_{ext:+.1f}%"

        # A pocket pivot deep in the base is an early accumulation footprint —
        # promote what would otherwise be a passive "watch" to "forming".
        if inp.pocket_pivot:
            return "forming", f"pocket_pivot_in_base_{ext:+.1f}%"
        # A constructive base of ascending swing lows is the structural footprint
        # of accumulation — promote watch → forming so the early base surfaces.
        if inp.higher_lows >= 2:
            return "forming", f"higher_lows_x{inp.higher_lows}_{ext:+.1f}%"
        # Strong volume dry-up in the base is a constructive accumulation
        # footprint — surface it as forming rather than passive watch.
        if inp.volume_dryup_pct is not None and inp.volume_dryup_pct >= cfg.dryup_strong_pct:
            return "forming", f"volume_dryup_{inp.volume_dryup_pct:.0f}pct_{ext:+.1f}%"

        # Far below pivot but no other red flags: still a watch candidate
        return "watch", f"far_below_pivot_{ext:+.1f}%"

    # No extension info: a pocket pivot or ascending base still flags early accumulation.
    if inp.pocket_pivot:
        return "forming", "pocket_pivot_no_extension_data"
    if inp.higher_lows >= 2:
        return "forming", f"higher_lows_x{inp.higher_lows}_no_extension_data"
    return "watch", "no_extension_data"


def aggregate_actionability(buckets: list[Bucket]) -> Bucket:
    """If a candidate triggers multiple setups, pick the most-actionable one.

    Order: actionable_now > near_actionable > watch > extended_too_late >
    excluded > not_valid.
    """
    if not buckets:
        return "not_valid"
    order: list[Bucket] = [
        "actionable_now",
        "near_actionable",
        "forming",
        "watch",
        "extended_too_late",
        "excluded",
        "not_valid",
    ]
    for b in order:
        if b in buckets:
            return b
    return "not_valid"
