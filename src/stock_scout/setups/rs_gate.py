"""Post-pass RS-rating gate for RS-dependent setups (Minervini, HighRS).

The IBD-style **RS Rating** is a *universe-relative percentile* (1–99): a stock's
6-month relative-strength score ranked against the whole fetched universe. That
distribution only exists AFTER every ticker has been processed, so individual
setup detectors (which run per-ticker, before the universe pass) cannot see it.

Two canonical criteria therefore could not be enforced inside the detectors:

  * **Minervini Trend Template criterion #8** — *RS Rating ≥ 70 (ideally 90+)*.
    The detector only had the raw 6-month RS score (``rs_score_6m``) and merely
    required it to be positive, which is far weaker than the canonical rule.
  * **HighRS ``min_rs_percentile``** — the config field existed (default 70) but
    was *never read*; the detector gated on "near 52-week high" + extension only.

This module re-evaluates those two setups once the universe RS rating is known
(orchestrator pass 2, just before scoring). To stay consistent with the
project's "ranking, not exclusion" stance, a sub-threshold name is **demoted to
``watch``** (kept visible, score capped so it can never rank as a leader) rather
than dropped — unless it was already weaker than watch.
"""
from __future__ import annotations

from stock_scout.config.schema import SetupsConfig
from stock_scout.setups.base import SetupResult

# Setups whose canonical definition hinges on the universe-relative RS rating.
_RS_GATED = {"minervini", "high_rs"}
# Buckets that represent an entry-grade signal — these get demoted to watch
# when the RS rating is too weak to call the stock a leader.
_LEADER_BUCKETS = {"actionable_now", "near_actionable", "forming"}
# Score ceiling applied to a demoted setup so its setup-quality contribution
# can't lift the candidate above genuine leaders.
_DEMOTED_SCORE_CAP = 50.0


def _threshold_for(setup_name: str, cfg: SetupsConfig) -> float | None:
    if setup_name == "minervini":
        return cfg.minervini.min_rs_rating
    if setup_name == "high_rs":
        return cfg.high_rs.min_rs_percentile
    return None


def apply_rs_rating_gate(
    results: list[SetupResult],
    rs_rating: float | None,
    cfg: SetupsConfig,
) -> list[SetupResult]:
    """Enforce the canonical RS-rating floor on RS-dependent setups in-place.

    Args:
        results:   the per-ticker SetupResult list (mutated in place).
        rs_rating: the candidate's universe-relative RS rating (1–99) or None.
        cfg:       the setups config (carries the per-setup thresholds).

    Behaviour:
        * ``rs_rating is None`` (insufficient universe RS data) → no gating,
          consistent with how missing data is treated elsewhere.
        * triggered RS-gated setup with ``rs_rating >= threshold`` → annotated
          PASS, left untouched.
        * triggered RS-gated setup with ``rs_rating < threshold`` → demoted:
          leader buckets fall to ``watch``, score is capped, a failed condition
          is recorded. Still ``triggered`` (watch is a valid triggered bucket)
          so the name stays visible and ranked.
    """
    if rs_rating is None:
        return results
    for r in results:
        if not r.triggered or r.setup_name not in _RS_GATED:
            continue
        thr = _threshold_for(r.setup_name, cfg)
        if thr is None:
            continue
        if rs_rating >= thr:
            r.reasons.append(f"PASS:rs_rating>={int(thr)}")
            continue
        # Sub-threshold: demote (ranking, not exclusion).
        r.failed_conditions.append(f"rs_rating_below_{int(thr)}")
        if r.actionability in _LEADER_BUCKETS:
            r.actionability = "watch"
        r.actionability_reason = f"rs_rating_{rs_rating:.0f}_below_{int(thr)}"
        r.score = round(min(r.score, _DEMOTED_SCORE_CAP), 1)
    return results
