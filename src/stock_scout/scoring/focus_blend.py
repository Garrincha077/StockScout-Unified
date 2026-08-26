"""The one ranking in this project with held-out evidence behind it.

Every other ordering the app offers was chosen because it seemed sensible. This
one was tuned on a training block and then scored, unchanged, on dates it had
never seen: **+1.96 at 3m and +3.96 at 6m** against the same-day universe, on a
2016+ strict holdout. Nothing else survived that test - not the accumulation
threshold, not the book's four breakout conditions, not any stop rule.

It lived only in `backtest/calibration.py` and was never wired into the product,
so the nightly report kept leading with `actionable_now`, which measures **-0.28
in both blocks**. That bucket is a state, not an edge. This module exists so the
blend has a single definition that both the backtest and the report read, rather
than two that drift apart.

The weights are deliberately dull - a broad score, relative strength, how good
the setup is, and only a tenth of the weight on actionability. That last term is
why `actionable_now` still counts for something without being the headline.
"""
from __future__ import annotations

from typing import Any

# Points for a bucket name, so a categorical field can sit in a weighted sum.
ACTIONABILITY_POINTS: dict[str, float] = {
    "actionable_now": 100.0,
    "near_actionable": 75.0,
    "forming": 55.0,
    "watch": 35.0,
    "stage_only": 20.0,
}

# Held out on 2016+ dates the tuning never saw. Do not adjust these without
# re-running that holdout - a blend fitted on all the data is worth nothing.
RS_QUALITY_BLEND: dict[str, float] = {
    "score": 0.35,
    "rs_rating": 0.30,
    "setup_quality": 0.25,
    "actionability": 0.10,
}

# Substages measured on 2016+, positive in both time blocks, and the two that
# were consistently negative. Used for emphasis in the report, never to drop a
# name: the screener shows what it found and says what it thinks of it.
STAGE_FAVOURED = ("2B_healthy_advance", "2C_extended_advance")
STAGE_AVOID = ("1B_mature_base", "4A_fresh_breakdown")


def in_stage_2(candidate: Any) -> bool:
    """Weinstein's one rule: buy the advance, and nothing else.

    The headline ranking draws only from here, measured 2026-08-01 on
    `deep_2026` with the blend above left exactly as it is:

        blend, unrestricted      +2.78 held out   (3m +1.63, 6m +3.93)
        blend, stage 2 only      +3.22 held out   (3m +1.85, 6m +4.59)
        blend, 2B/2C only        +2.36 held out
        2,620-cell grid winner   +3.17 held out

    The last row is the point. A 5,720-cell search over weights, stage
    restrictions and portfolio sizes picked a cell scoring **+6.78 in sample**,
    and this rule - which scores +1.30 in sample and would never have been
    selected - beats it on the dates neither of them saw. That is what
    overfitting at the argmax looks like from the outside.

    Stage 2 whole, not 2B/2C. Narrowing to the two favoured substages is a
    fitted sub-selection and it *costs* 0.86 held out; the book says buy the
    advance, and the advance is the thing that measures.

    This restricts a ranking, never the list: every candidate stays in
    `candidates.json` and on the screen. It decides what goes at the top.
    """
    stage = getattr(candidate, "weinstein_stage", None)
    if stage is None and isinstance(candidate, dict):
        stage = candidate.get("weinstein_stage")
    try:
        return int(stage) == 2
    except (TypeError, ValueError):
        return False


def headline_ranking(candidates: list[Any], limit: int = 10) -> list[Any]:
    """The top `limit` names by measured edge: stage 2, ordered by the blend.

    One definition, so the report, the digest and anything else that wants to
    lead with "the best of tonight" cannot disagree about what that means.

    Falls back to the unrestricted blend when no candidate carries a stage - a
    run from before stage analysis existed should still get a ranking rather
    than an empty section.
    """
    pool = [c for c in candidates if in_stage_2(c)]
    if not pool:
        pool = list(candidates)
    return sorted(pool, key=candidate_blend_score, reverse=True)[:limit]


def _num(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def blend_from_parts(
    *,
    score: Any,
    rs_rating: Any,
    setup_quality: Any,
    actionability: Any,
    blend: dict[str, float] | None = None,
) -> float:
    """Weighted sum of the four inputs, with an unknown treated as zero.

    Zero is the right fold here and not a claim about the stock: every input is a
    0-100 score where absent means unranked, and the sum is only ever used to
    order names against each other on the same day.
    """
    values = {
        "score": _num(score),
        "rs_rating": _num(rs_rating),
        "setup_quality": _num(setup_quality),
        "actionability": ACTIONABILITY_POINTS.get(str(actionability or ""), 0.0),
    }
    weights = blend if blend is not None else RS_QUALITY_BLEND
    return sum(float(w) * values.get(name, 0.0) for name, w in weights.items())


def candidate_blend_score(candidate: Any, blend: dict[str, float] | None = None) -> float:
    """`blend_from_parts` for a scored `Candidate`.

    Reads `setup_quality` off the score breakdown rather than the candidate,
    which is where the scorer puts it.
    """
    breakdown = getattr(candidate, "score_breakdown", None)
    return blend_from_parts(
        score=getattr(candidate, "score", 0.0),
        rs_rating=getattr(candidate, "rs_rating", None),
        setup_quality=getattr(breakdown, "setup_quality", 0.0) if breakdown else 0.0,
        actionability=getattr(candidate, "actionability", ""),
        blend=blend,
    )


def stage_note(substage: Any) -> str:
    """A short mark for the report: what the measurements say about this bucket."""
    name = str(substage or "")
    if name in STAGE_FAVOURED:
        return "favoured"
    if name in STAGE_AVOID:
        return "avoid"
    return ""
