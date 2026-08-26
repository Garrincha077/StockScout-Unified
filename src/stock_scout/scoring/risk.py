"""One definition of "this name carries a risk the screen should not hide".

The screener already knew which candidates were dangerous and had no way to say
so. Measured on the run of 2026-07-28, 2,113 candidates:

* 12 reached `m_and_a_high_confidence` and were dropped outright. That works —
  none of them leak into `candidates.json`.
* **48 reached `m_and_a_medium_confidence` and stayed**, by an earlier decision
  to flag rather than drop.

`_has_short_pin` has since taken 8 of those 48 to `high` on held-out evidence,
including ACA — an agreed $8.5bn all-cash takeover that was sitting third by
score. **That fixes the worst of it and does not remove the need for this
module: 40 names are still flagged and in the screen, and the highest-scoring
candidate of the whole run (TMHC, 83.2) is one of them.** A price test can only
reach the names whose price has already stopped moving; everything else needs
saying out loud rather than dropping.

**Why this is not `Flag.severity`.** That field exists and looks like the
natural filter, but it cannot carry the meaning: 1,245 of the run's flags are
`warning`, and almost all are ordinary commentary — `glb:extended_*` 291,
`stage4_short_context_not_standalone` 333, and 528 duplicated per-detector M&A
notices. Filtering the screen on `severity == "warning"` would hide half of it.
`severity` defaults to `"warning"` in the model, so it marks "not silent", not
"risky".

**Why a derived field rather than matching codes in the UI.** Three components
already pattern-match stage strings, each slightly differently
([DashboardView.tsx:108](web/src/components/DashboardView.tsx:108),
[StagesView.tsx:31](web/src/components/StagesView.tsx:31),
[EtfsView.tsx:61](web/src/components/EtfsView.tsx:61)). A risk rule copied into
each list view drifts the first time one of them is edited, and the failure is
silent — a dangerous name simply reappears. One definition here, read by the UI,
the markdown report and Telegram alike, the same way `focus_blend` is the single
source for the ranking.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

NONE = "none"
ELEVATED = "elevated"
EXCLUDED = "excluded"

# Exact flag codes that make a candidate elevated-risk, with the reason a human
# should see. Exact codes, not prefixes: `high_adr` arrives as `high_adr:12.4%`
# and is handled below, but everything else must match outright so that a new
# flag cannot silently join this set by being named similarly.
ELEVATED_CODES: dict[str, str] = {
    "m_and_a_medium_confidence": "price pinned like a deal, news unconfirmed",
    "tightness_void_price_pinned": "tightness discarded: price is pinned",
}

EXCLUDED_CODES: dict[str, str] = {
    "m_and_a_high_confidence": "takeover confirmed by price and news",
}

# Prefix rules, for codes that carry a measured value after a colon.
ELEVATED_PREFIXES: dict[str, str] = {
    "high_adr": "daily range far above normal",
}


def _codes(flags: Iterable[Any]) -> list[str]:
    out = []
    for f in flags or []:
        code = f.get("code") if isinstance(f, dict) else getattr(f, "code", None)
        if code:
            out.append(str(code))
    return out


def assess(flags: Iterable[Any]) -> tuple[str, list[dict[str, str]]]:
    """Return `(level, reasons)` for one candidate.

    Reasons are returned even when the level is `excluded`, because the point of
    keeping excluded rows reachable is being able to see *why* something was
    dropped rather than trusting that it deserved it.

    An earlier draft also took `data_quality_issues`. That field is declared on
    `Candidate` and **nothing has ever written to it**, so the branch had never
    run on a real input — and it was wrong anyway, stringifying whole `Flag`
    models instead of reading their codes. Better to leave it out than to ship a
    path whose first real input would be its first test.
    """
    reasons: list[dict[str, str]] = []
    level = NONE
    for code in _codes(flags):
        if code in EXCLUDED_CODES:
            level = EXCLUDED
            reasons.append({"code": code, "label": EXCLUDED_CODES[code]})
            continue
        if code in ELEVATED_CODES:
            if level != EXCLUDED:
                level = ELEVATED
            reasons.append({"code": code, "label": ELEVATED_CODES[code]})
            continue
        prefix = code.split(":", 1)[0]
        if prefix in ELEVATED_PREFIXES:
            if level != EXCLUDED:
                level = ELEVATED
            reasons.append({"code": code, "label": ELEVATED_PREFIXES[prefix]})

    # Two detectors can raise the same concern about one name; the level is
    # unchanged by that and a repeated line only makes the card noisier.
    seen: set[str] = set()
    deduped = [r for r in reasons if not (r["code"] in seen or seen.add(r["code"]))]
    return level, deduped


def is_hidden_by_default(level: str | None) -> bool:
    """Whether a presentation surface should leave this out unless asked.

    Presentation only. `candidates.json` keeps every row whatever this says, so
    a decision made tonight stays auditable tomorrow.
    """
    return str(level or NONE) != NONE
