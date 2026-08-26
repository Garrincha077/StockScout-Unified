"""Keep the two lines on the chart - the stop and the trigger - somewhere real.

The stop half bounds a distance, because any stop inside the bound is still a
decision a person could make. The trigger half does not bound anything: it
either passes the detector's level through or withholds it, because a trigger
moved to fit is a price at which nothing happens. See `clamp_trigger` below.

On the run of 2026-07-31 the stops were unusable and the charts showed it. Median
distance from price to the invalidation line, by setup:

    crash_base_stage1   230.8 %      long_base_launch  37.4 %
    ema_stack_launch     15.3 %      accumulation_base 14.1 %
    weinstein             9.5 %      guppy              8.3 %

The worst single case was DAVE at 372.69 with a stop at 4.33 - a 98.8 % risk,
which is not a stop but the low of the crash the base formed after. WULF, KOD,
PRCH and RLMD were all past 2,000 %. Nine of the ten worst were
`crash_base_stage1`, and the cause is structural rather than a bug in arithmetic:
that detector anchors invalidation on the five-year base low, which is the right
level while price still sits in the base and absurd once it has run away from it.

Two things follow. A stop that far away is not a decision anybody can act on, and
- the visible symptom - a chart drawing a line at 4.33 under a candle at 372 has
to scale its y-axis over two orders of magnitude, so the price action flattens
into a strip.

The fix is deliberately not "pick a better level per detector". Each of the
twelve has its own idea of structure and most of them are right about it; what
was missing was a single sanity bound applied after they have all had their say.
"""
from __future__ import annotations

# Weinstein sizes a position off the distance to support and refuses trades that
# need too wide a stop. 25% is generous for a weekly-chart method holding a
# quarter or two - it accommodates a volatile Stage 2 name pulling back to its
# 30-week line - while still rejecting the 230% that crash bases were producing.
MAX_RISK_PCT = 25.0
# Below this the "stop" is inside the noise of a single session and would be hit
# by a spread, not by a thesis breaking.
MIN_RISK_PCT = 1.0


def clamp_invalidation(
    price: float | None,
    invalidation: float | None,
    *,
    support: float | None = None,
    max_risk_pct: float = MAX_RISK_PCT,
    min_risk_pct: float = MIN_RISK_PCT,
) -> float | None:
    """The stop a detector proposed, pulled back to a tradeable distance.

    `support` is an optional nearer structural level - the 30-week MA is the
    natural one for this project - and is preferred over an arbitrary percentage
    whenever it sits between the price and the detector's own level. That keeps
    the stop on something the chart actually shows rather than on a round number.

    Returns None only when there is nothing to work with, and never returns a
    level at or above the price: a stop above the entry is not a stop.
    """
    if price is None or invalidation is None:
        return invalidation
    try:
        price_f = float(price)
        inval = float(invalidation)
    except (TypeError, ValueError):
        return invalidation
    if price_f <= 0:
        return invalidation

    floor = price_f * (1.0 - max_risk_pct / 100.0)
    ceiling = price_f * (1.0 - min_risk_pct / 100.0)

    # Above the price, or inside a single session's noise. Pull it to the
    # tightest usable level, *not* to the widest: this branch knows the stop is
    # wrong, not that the trade is risky, and widening it to the maximum would
    # quietly quadruple the position risk on names whose detector was merely
    # imprecise. A first version did exactly that and it showed up as guppy,
    # weinstein and glb median risk rising for no reason anyone could defend.
    if inval >= ceiling:
        inval = ceiling

    if inval < floor:
        # Too far. Prefer real structure between here and there.
        candidates = [floor]
        if support is not None:
            try:
                sup = float(support)
                if floor <= sup < ceiling:
                    candidates.append(sup)
            except (TypeError, ValueError):
                pass
        # The highest of them is the tightest stop that is still below price and
        # inside the risk bound.
        inval = max(candidates)

    return round(inval, 2)


# A trigger is not a risk parameter, it is a claim about structure: clear this
# and the setup is on. So it cannot be clamped the way a stop can - a stop
# pulled to 25% is still a decision somebody could make, while a trigger moved
# to a round number is a price at which nothing happens. What follows therefore
# withholds a level it cannot defend instead of inventing one.
#
# The bound is the chart's own reach rather than a percentage. Measured on the
# run of 2026-07-31, a flat band of [-15%, +50%] would have dropped 99 triggers
# that were plainly fine: AOSL wanted 48.04 against a 52-week high of 53.32,
# GENI wanted 13.50 against 13.55. Those levels are inside the year the chart
# draws, so they cost the axis nothing and the stock traded there recently. The
# levels that actually broke the chart were the ones *outside* that year -
# MLKN at 30.44 with a 52-week high of 23.60, NVTS at 34.17 against a price of
# 10.86, WPP's green line at 82.99 against 20.21.
MAX_TRIGGER_ABOVE_52W_HIGH_PCT = 10.0
# A genuine breakout to a new high sits just over the old one, never far over.
# Below this, when the year's range is tight, headroom off the price instead.
MIN_TRIGGER_HEADROOM_PCT = 25.0
# Under the price a level has already been crossed. Just under, it is the line
# the stock has only now taken out and is worth drawing as support; far under,
# it is stale and `price_crosses_X` can never fire again.
MAX_TRIGGER_BELOW_PCT = 15.0


def clamp_trigger(
    price: float | None,
    trigger: float | None,
    *,
    distance_to_52w_high_pct: float | None = None,
    max_above_52w_high_pct: float = MAX_TRIGGER_ABOVE_52W_HIGH_PCT,
    min_headroom_pct: float = MIN_TRIGGER_HEADROOM_PCT,
    max_below_pct: float = MAX_TRIGGER_BELOW_PCT,
) -> float | None:
    """The detector's trigger, or None when the chart could not draw it sanely.

    The level is returned untouched or not at all. `distance_to_52w_high_pct`
    is the candidate's own feature - price against the 252-day high, negative
    below it - and the 52-week high is reconstructed from it. Without it the
    headroom off the price is the only ceiling available.

    Withholding costs less than it looks: the per-setup breakdown still shows
    what each detector proposed, so the number is not lost, it just stops being
    presented as tonight's entry.
    """
    if price is None or trigger is None:
        return trigger
    try:
        price_f = float(price)
        trig = float(trigger)
    except (TypeError, ValueError):
        return trigger
    if price_f <= 0 or trig <= 0:
        return None

    if trig < price_f * (1.0 - max_below_pct / 100.0):
        return None

    ceiling = price_f * (1.0 + min_headroom_pct / 100.0)
    high_52w = _high_52w(price_f, distance_to_52w_high_pct)
    if high_52w is not None:
        ceiling = max(ceiling, high_52w * (1.0 + max_above_52w_high_pct / 100.0))
    if trig > ceiling:
        return None

    return round(trig, 2)


def _high_52w(price: float, distance_pct: float | None) -> float | None:
    if distance_pct is None:
        return None
    try:
        ratio = 1.0 + float(distance_pct) / 100.0
    except (TypeError, ValueError):
        return None
    if ratio <= 0:
        return None
    return price / ratio


def trigger_pct(price: float | None, trigger: float | None) -> float | None:
    """How far the price has to travel to reach the trigger, in percent."""
    if price is None or trigger is None:
        return None
    try:
        price_f = float(price)
        trig = float(trigger)
    except (TypeError, ValueError):
        return None
    if price_f <= 0:
        return None
    return round((trig - price_f) / price_f * 100.0, 2)


def risk_pct(price: float | None, invalidation: float | None) -> float | None:
    """How much of the position is at risk to the stop, in percent."""
    if price is None or invalidation is None:
        return None
    try:
        price_f = float(price)
        inval = float(invalidation)
    except (TypeError, ValueError):
        return None
    if price_f <= 0:
        return None
    return round((price_f - inval) / price_f * 100.0, 2)
