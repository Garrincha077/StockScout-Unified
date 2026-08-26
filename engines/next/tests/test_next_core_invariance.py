from copy import deepcopy

from audit_next_core_invariance import compare_payloads


def payload():
    return {
        "chartShards": {"AAA": "charts/A.json"},
        "universe": [
            {
                "ticker": "AAA",
                "opportunityScore": 82.0,
                "opportunityPotential": 80.0,
                "opportunityTiming": 84.0,
                "opportunityRank": 97,
                "opportunityTier": "READY",
                "emergingLeaderScore": 78.0,
                "maClusterScore": 73.0,
                "maClusterPhase": "READY",
                "maClusterTier": "A",
                "groupRank": 65.0,
                "groupConfidence": 72.0,
                "fundamentalEvidenceScore": 61.0,
                "stage": 2,
                "rsRank": 91.0,
                "leadershipScore": 82.0,
            }
        ],
    }


def test_append_only_confirmation_is_allowed():
    before = payload()
    after = deepcopy(before)
    after["universe"][0]["legacyConfirmationStatus"] = "CONFIRMED"
    after["universe"][0]["legacyConfirmationScore"] = 87
    assert compare_payloads(before, after) == []


def test_core_score_mutation_is_rejected():
    before = payload()
    after = deepcopy(before)
    after["universe"][0]["opportunityScore"] = 83.0
    errors = compare_payloads(before, after)
    assert errors
    assert "opportunityScore" in errors[0]


def test_chart_mapping_mutation_is_rejected():
    before = payload()
    after = deepcopy(before)
    after["chartShards"]["AAA"] = "charts/B.json"
    assert "chartShards mapping changed" in compare_payloads(before, after)
