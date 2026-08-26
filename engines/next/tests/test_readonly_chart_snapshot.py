import pandas as pd

from hydrate_frontend_charts_readonly import compact_bars, snapshot_window


def test_snapshot_window_is_anchored_to_generated_at():
    cutoff, start, end = snapshot_window("2026-08-18T22:07:29+00:00")
    assert cutoff == pd.Timestamp("2026-08-18")
    assert start == "2021-08-08"
    assert end == "2026-08-19"


def test_compact_bars_excludes_prices_after_scan_cutoff():
    index = pd.to_datetime(["2026-08-17", "2026-08-18", "2026-08-19"])
    frame = pd.DataFrame(
        {
            "Open": [10.0, 11.0, 12.0],
            "High": [11.0, 12.0, 13.0],
            "Low": [9.0, 10.0, 11.0],
            "Close": [10.5, 11.5, 12.5],
            "Volume": [100, 200, 300],
        },
        index=index,
    )
    spy = pd.Series([100.0, 101.0, 102.0], index=index)

    bars = compact_bars(frame, spy, pd.Timestamp("2026-08-18"))

    assert [row[0] for row in bars] == ["2026-08-17", "2026-08-18"]
    assert bars[-1][4] == 11.5
