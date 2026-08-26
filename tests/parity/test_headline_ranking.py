"""The headline ranking draws from stage 2, and why that beat the grid.

Measured 2026-08-01 on `deep_2026`, held out on 2023-2025:

    blend, unrestricted      +2.78   (3m +1.63, 6m +3.93)
    blend, stage 2 only      +3.22   (3m +1.85, 6m +4.59)
    blend, 2B/2C only        +2.36
    5,720-cell grid winner   +3.17   (and +6.78 in sample)

The last two lines are the reason this is a rule and not a weight vector. A
search over weights, stage restrictions and portfolio sizes found a cell five
times better in sample that is worse out of it, while the plainest reading of
Weinstein - buy the advance - wins on dates neither had seen. Narrowing further
to the two favoured substages costs 0.86, so "stage 2" means stage 2.
"""
from __future__ import annotations

import pytest

from stock_scout.scoring.focus_blend import headline_ranking, in_stage_2
from stock_scout.scoring.models import Candidate


def _cand(ticker: str, score: float, stage: int | None, **kw) -> Candidate:
    return Candidate(
        ticker=ticker,
        as_of="2026-07-31",
        score=score,
        price=100.0,
        rs_rating=kw.pop("rs_rating", 80.0),
        actionability=kw.pop("actionability", "actionable_now"),
        primary_setup="minervini",
        weinstein_stage=stage,
        **kw,
    )


def test_a_stage_4_name_never_leads_however_high_it_scores():
    # The failure this prevents: a high relative-strength reading on a stock in
    # decline is a falling knife, and the previous grid winner - pure RS with no
    # stage restriction - would have bought it.
    rows = [
        _cand("FALLING", 99.0, 4),
        _cand("ADVANCE", 50.0, 2),
    ]

    assert [c.ticker for c in headline_ranking(rows)] == ["ADVANCE"]


def test_stage_1_and_3_are_out_too():
    rows = [_cand("BASE", 95.0, 1), _cand("TOP", 90.0, 3), _cand("GO", 40.0, 2)]

    assert [c.ticker for c in headline_ranking(rows)] == ["GO"]


def test_within_stage_2_the_blend_still_orders():
    rows = [
        _cand("LOW", 40.0, 2),
        _cand("HIGH", 90.0, 2),
        _cand("MID", 65.0, 2),
    ]

    assert [c.ticker for c in headline_ranking(rows)] == ["HIGH", "MID", "LOW"]


def test_every_stage_2_substage_qualifies_not_just_the_favoured_two():
    # Restricting to 2B/2C measured +2.36 against stage 2's +3.22. The fitted
    # sub-selection is worse than the book's plain rule, so 2A and 2D belong.
    rows = [
        _cand("A", 60.0, 2, weinstein_substage="2A_fresh_breakout"),
        _cand("D", 61.0, 2, weinstein_substage="2D_advance_consolidation"),
        _cand("B", 62.0, 2, weinstein_substage="2B_healthy_advance"),
    ]

    assert len(headline_ranking(rows)) == 3


def test_the_limit_is_honoured():
    rows = [_cand(f"T{i}", float(i), 2) for i in range(20)]

    assert len(headline_ranking(rows, limit=10)) == 10


def test_a_run_with_no_stage_data_still_gets_a_ranking():
    # Runs from before stage analysis existed must not produce an empty
    # headline section - an absent stage is not a stage 4.
    rows = [_cand("A", 90.0, None), _cand("B", 40.0, None)]

    assert [c.ticker for c in headline_ranking(rows)] == ["A", "B"]


@pytest.mark.parametrize(
    "stage,expected", [(2, True), (1, False), (3, False), (4, False), (None, False)]
)
def test_in_stage_2_reads_the_stage(stage, expected):
    assert in_stage_2(_cand("X", 50.0, stage)) is expected


def test_the_digest_and_the_report_lead_with_the_same_names():
    # These answer the same question - "the best of tonight" - so they have to
    # give the same answer. Before 2026-08-01 the report gained the stage-2
    # restriction and the digest kept its own ordering, which would have had the
    # phone recommending names the report no longer led with.
    import re

    from stock_scout.notifications.telegram import render_daily_digest
    from stock_scout.reporting.markdown import render_markdown_report

    rows = [
        _cand("LEADER", 90.0, 2),
        _cand("SECOND", 80.0, 2),
        _cand("FALLING", 99.0, 4),
        _cand("BASE", 95.0, 1),
    ]

    md = render_markdown_report(rows, [], {}, "2026-07-31")
    top = re.findall(r"\| \*\*([A-Z0-9.]+)\*\*", md.split("## Top 10 by measured edge")[1])
    digest = render_daily_digest(as_of="2026-07-31", candidates=rows)

    assert top[:2] == ["LEADER", "SECOND"]
    assert "FALLING" not in digest.split("Held back")[0]
    assert "BASE" not in digest.split("Held back")[0]


def test_in_stage_2_accepts_a_plain_dict():
    # The backtest hands around dicts, the product hands around Candidates, and
    # one definition has to serve both or they drift.
    assert in_stage_2({"weinstein_stage": 2}) is True
    assert in_stage_2({"weinstein_stage": 4}) is False
    assert in_stage_2({}) is False
