from copy import deepcopy

from build_legacy_confirmation_sidecar import build_sidecar
from phase6_empirical_cohorts import (
    COHORT_CONFIRMED,
    COHORT_EARLY,
    COHORT_LEGACY_BUY_LOW,
    COHORT_RISK,
    build_snapshot_observations,
)


def row(ticker: str):
    return {
        "ticker": ticker,
        "originalRunBuySignal": False,
        "originalRunSellSignal": False,
        "originalBuy": False,
        "originalMarketQualifiedBuy": False,
        "originalSell": False,
        "originalEngine": {
            "model": "original-signal-engine-v1",
            "completeSourceCaptureModel": "legacy-complete-source-capture-v1",
            "buy": {"isBuy": False, "marketQualified": False, "emittedByOriginalRun": False},
            "sell": {"isSell": False, "emittedByOriginalRun": False, "reasons": []},
            "minervini": {"passes": False},
            "vcp": {"isVcp": False},
            "breakout": {"is_breakout": False},
        },
    }


def market():
    return {
        "originalSignalGate": {
            "spy": {"phase": 2, "trend": "Bullish"},
            "gate": {"should_generate_buys": True, "should_generate_sells": False},
        }
    }


def test_sidecar_is_compact_and_does_not_mutate_canonical():
    buy = row("BUY")
    buy["originalRunBuySignal"] = True
    buy["originalEngine"]["buy"]["emittedByOriginalRun"] = True
    early = row("EARLY")
    early["originalEngine"]["vcp"]["isVcp"] = True
    canonical = {
        "generatedAt": "2026-08-20T00:00:00Z",
        "market": market(),
        "universe": [buy, early],
    }
    before = deepcopy(canonical)
    sidecar = build_sidecar(canonical)
    assert canonical == before
    assert sidecar["affectsStockScout"] is False
    assert sidecar["counts"] == {"CONFIRMED": 1, "EARLY": 1}
    assert sidecar["byTicker"]["BUY"] == {
        "status": "CONFIRMED",
        "available": True,
        "reasons": ["ORIGINAL_RUN_BUY"],
    }
    assert "evidence" not in sidecar["byTicker"]["EARLY"]


def test_sidecar_rejects_duplicate_tickers():
    candidate = row("AAA")
    try:
        build_sidecar({"market": market(), "universe": [candidate, deepcopy(candidate)]})
    except AssertionError as exc:
        assert "Duplicate ticker" in str(exc)
    else:
        raise AssertionError("duplicate ticker should fail")


def test_phase6_observer_reuses_canonical_sidecar_without_mutation():
    def stock(ticker: str, *, score=84.0, rank=94, tier="READY"):
        candidate = row(ticker)
        candidate.update(
            {
                "price": 42.5,
                "opportunityScore": score,
                "opportunityRank": rank,
                "opportunityTier": tier,
                "extended": False,
                "stage": 2,
                "rsRank": 91,
                "breakoutPct": -1.0,
            }
        )
        return candidate

    confirmed = stock("CONF")
    confirmed["originalRunBuySignal"] = True
    confirmed["originalEngine"]["buy"]["emittedByOriginalRun"] = True

    early = stock("EARLY")
    early["originalEngine"]["vcp"]["isVcp"] = True

    risk = stock("RISK")
    risk["originalRunSellSignal"] = True
    risk["originalEngine"]["sell"]["emittedByOriginalRun"] = True

    low = stock("LOWBUY", score=49.0, rank=20, tier="PASS")
    low["originalRunBuySignal"] = True
    low["originalEngine"]["buy"]["emittedByOriginalRun"] = True

    canonical = {
        "generatedAt": "2026-08-21T00:00:00Z",
        "market": market(),
        "universe": [risk, confirmed, early, low],
    }
    before = deepcopy(canonical)
    projection = build_snapshot_observations(canonical)

    assert canonical == before
    assert projection["cohortCounts"] == {
        COHORT_CONFIRMED: 1,
        COHORT_EARLY: 1,
        COHORT_LEGACY_BUY_LOW: 1,
        COHORT_RISK: 1,
    }
    assert projection["definitions"]["legacyConfirmation"]["affectsStockScout"] is False
