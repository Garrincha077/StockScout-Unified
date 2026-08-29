from datetime import date

import pandas as pd

from run_optimized_scan import report_market_session
from export_frontend_data_fast import chart_history_window
from src.screening.fast_batch_processor import FastOptimizedBatchProcessor, expected_market_session


def test_expected_market_session_and_window_are_pinned(monkeypatch):
    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-08-28")
    processor = FastOptimizedBatchProcessor(max_workers=1, use_git_storage=False)

    assert expected_market_session() == date(2026, 8, 28)
    assert processor._history_window() == {"start": "2021-08-24", "end": "2026-08-29"}
    assert processor._has_expected_session(
        pd.DataFrame({"Close": [1.0]}, index=pd.DatetimeIndex(["2026-08-28"]))
    )
    assert not processor._has_expected_session(
        pd.DataFrame({"Close": [1.0]}, index=pd.DatetimeIndex(["2026-08-27"]))
    )


def test_canonical_report_uses_workflow_session_not_wall_clock(monkeypatch):
    monkeypatch.setenv("STOCKSCOUT_EXPECTED_SESSION", "2026-08-28")
    assert report_market_session() == "2026-08-28"
    assert chart_history_window() == {"start": "2021-08-24", "end": "2026-08-29"}
