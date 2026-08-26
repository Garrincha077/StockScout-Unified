from compute_opportunity_v2 import apply
from finalize_opportunity_v2 import finalize


def row(ticker, score_seed=80, group_rank=75, sector="XLK", industry="IGV"):
    return {
        "ticker": ticker,
        "stage": 2,
        "stage2AgeWeeks": 6,
        "emergingArchetype": "Neglected Emerging",
        "emergingLeaderScore": score_seed,
        "resetScore": 88,
        "neglectHistoryScore": 85,
        "stageFreshnessScore": 100,
        "reawakeningStructureScore": 70,
        "dryResetScore": 75,
        "recoveryScore": 70,
        "distance10w": 2,
        "distance30w": 4,
        "breakoutPct": 1,
        "rsRank": 88 if ticker == "A" else 72,
        "rsAcceleration": 0.65 if ticker == "A" else 0.20,
        "maClusterScore": 85 if ticker == "A" else 65,
        "maClusterPhase": "READY",
        "maClusterVolumePace": 1.5,
        "maClusterPricePct": 1,
        "volumeRatio": 1.8 if ticker == "A" else 1.1,
        "groupRank": group_rank,
        "groupConfidence": 80,
        "fundamentalEvidenceScore": 75,
        "fundamentalEvidenceConfidence": 80,
        "extended": False,
        "sectorProxyTicker": sector,
        "industryProxyTicker": industry,
        "leadershipScore": 1,
    }


def test_finalizer_prevents_group_double_count_and_refreshes_group_views():
    rows = [row("A"), row("B", score_seed=70, group_rank=65)]
    payload = {
        "universe": rows,
        "market": {"groupModel": "behavioral-proxy-v2-confidence"},
        "groups": {
            "method": "behavioral-proxy-v2-confidence",
            "sectors": [{"ticker": "XLK", "medianOpportunity": 1, "topTickers": ["B"]}],
            "industries": [{"ticker": "IGV", "medianOpportunity": 1, "topTickers": ["B"]}],
        },
    }
    apply(payload)
    assert rows[0]["opportunityScore"] != rows[0]["emergingLeaderScore"]

    finalize(payload)

    assert all(r["leadershipScore"] == r["opportunityScore"] for r in rows)
    assert payload["groups"]["leadershipScoreMode"] == "opportunity-v2-alias"
    for collection in ("sectors", "industries"):
        group = payload["groups"][collection][0]
        assert group["topTickers"][0] == "A"
        assert group["medianOpportunity"] > 1
        assert group["opportunityModel"] == "stockscout-opportunity-v2-potential-timing"


def test_emerging_score_remains_independent_discovery_evidence():
    rows = [row("A")]
    payload = {"universe": rows, "market": {}}
    apply(payload)
    emerging = rows[0]["emergingLeaderScore"]
    opportunity = rows[0]["opportunityScore"]
    finalize(payload)
    assert rows[0]["emergingLeaderScore"] == emerging
    assert rows[0]["opportunityScore"] == opportunity
    assert rows[0]["leadershipScore"] == opportunity
