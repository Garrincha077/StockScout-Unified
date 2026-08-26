from export_frontend_data_fast import normalize_vcp_data


def test_vcp_quality_prefers_canonical_source_key():
    assert normalize_vcp_data({"vcp_quality": 72.5})["quality"] == 72.5


def test_vcp_quality_keeps_backward_compatibility():
    assert normalize_vcp_data({"quality": 61.0})["quality"] == 61.0


def test_vcp_quality_does_not_mutate_input():
    source = {"vcp_quality": 70.0}
    normalized = normalize_vcp_data(source)
    assert source == {"vcp_quality": 70.0}
    assert normalized["quality"] == 70.0
