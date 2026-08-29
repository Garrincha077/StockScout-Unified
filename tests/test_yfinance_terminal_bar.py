from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd

from stock_scout.data.providers.yfinance_provider import (
    YFinanceDataProvider,
    _has_complete_terminal_bar,
    _needs_terminal_bar_repair,
)


def _frame(close: list[float | None], *, start: str = "2026-08-27") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [768.5 + i for i in range(len(close))],
            "High": [772.0 + i for i in range(len(close))],
            "Low": [767.0 + i for i in range(len(close))],
            "Close": close,
            "Volume": [1000 + i for i in range(len(close))],
        },
        index=pd.date_range(start, periods=len(close), freq="D", tz="America/New_York"),
    )


def test_partial_terminal_bar_is_repairable() -> None:
    partial = _frame([771.1, None])
    complete = _frame([769.35], start="2026-08-28")

    assert _needs_terminal_bar_repair(partial, date(2026, 8, 28), "1d")
    assert not _has_complete_terminal_bar(partial, date(2026, 8, 28))
    assert _has_complete_terminal_bar(complete, date(2026, 8, 28))


def test_download_requeries_only_the_incomplete_terminal_session() -> None:
    provider = object.__new__(YFinanceDataProvider)
    provider.cfg = SimpleNamespace(request_timeout_seconds=5, retry_attempts=1)
    provider._session = None
    provider._bucket = SimpleNamespace(acquire=lambda: None)
    provider._pause = SimpleNamespace(
        wait_if_paused=lambda: None,
        record_success=lambda: None,
    )
    calls: list[tuple[date, date]] = []
    partial = _frame([771.1, None])
    repaired = _frame([769.35], start="2026-08-28")

    def download_once(_symbol: str, start: date, end: date, _interval: str, _adjusted: bool):
        calls.append((start, end))
        return partial if len(calls) == 1 else repaired

    provider._download_once = download_once
    result = provider._download("SPY", date(2026, 8, 27), date(2026, 8, 28), "1d", False)

    assert calls == [(date(2026, 8, 27), date(2026, 8, 28)), (date(2026, 8, 28), date(2026, 8, 28))]
    assert result.index[-1].date() == date(2026, 8, 28)
    assert result.iloc[-1]["close"] == 769.35


def test_bulk_download_repairs_all_partial_terminal_rows_in_one_request() -> None:
    provider = object.__new__(YFinanceDataProvider)
    provider._session = None
    provider._bucket = SimpleNamespace(acquire=lambda: None)
    provider._pause = SimpleNamespace(
        wait_if_paused=lambda: None,
        record_success=lambda: None,
    )
    calls: list[tuple[str, str]] = []
    initial = pd.concat(
        {
            "SPY": _frame([771.1, None]),
            "QQQ": _frame([721.1, None]),
        },
        axis=1,
    )
    repair = pd.concat(
        {
            "SPY": _frame([769.35], start="2026-08-28"),
            "QQQ": _frame([716.43], start="2026-08-28"),
        },
        axis=1,
    )

    class FakeYFinance:
        @staticmethod
        def download(*, start, end, **_kwargs):
            calls.append((start, end))
            return initial if start == "2026-08-27" else repair

    provider._yf = FakeYFinance()
    result = provider.get_bulk_daily_ohlcv(
        ["SPY", "QQQ"], date(2026, 8, 27), date(2026, 8, 28), adjusted=False
    )

    assert calls == [("2026-08-27", "2026-08-29"), ("2026-08-28", "2026-08-29")]
    assert result["SPY"].index[-1].date() == date(2026, 8, 28)
    assert result["SPY"].iloc[-1]["close"] == 769.35
    assert result["QQQ"].iloc[-1]["close"] == 716.43
