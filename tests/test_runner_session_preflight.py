from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from stockscout_eod.runner import _frame_last_date, preflight_session, validate_probe_dates


def test_probe_requires_benchmark_at_selected_session():
    with pytest.raises(ValueError, match="SPY=2026-08-27 expected=2026-08-28"):
        validate_probe_dates(
            {"SPY": date(2026, 8, 27), "AAPL": date(2026, 8, 27)},
            date(2026, 8, 28),
        )


def test_probe_rejects_low_sample_freshness_before_full_scan():
    with pytest.raises(ValueError, match="coverage too low"):
        validate_probe_dates(
            {
                "SPY": date(2026, 8, 28),
                "QQQ": date(2026, 8, 28),
                "AAPL": date(2026, 8, 27),
                "MSFT": None,
                "NVDA": date(2026, 8, 27),
            },
            date(2026, 8, 28),
            minimum_fresh_pct=80,
        )


def test_probe_accepts_coherent_sample_and_normalizes_timezone():
    frame = pd.DataFrame(
        {"close": [1.0]},
        index=pd.DatetimeIndex(["2026-08-28 20:00:00+00:00"]),
    )
    assert _frame_last_date(frame) == date(2026, 8, 28)
    validate_probe_dates(
        {"SPY": date(2026, 8, 28), "AAPL": date(2026, 8, 28)},
        date(2026, 8, 28),
    )


def test_preflight_retries_a_transient_provider_lag(monkeypatch):
    runner = SimpleNamespace(
        settings=SimpleNamespace(cache=SimpleNamespace(daily_history_years=5)),
    )
    calls = 0

    def fetch(_ticker, _start, _end):
        nonlocal calls
        calls += 1
        day = "2026-08-27" if calls <= 10 else "2026-08-28"
        return pd.DataFrame({"close": [1.0]}, index=pd.DatetimeIndex([day])), pd.DataFrame(), "yfinance", "primary"

    runner._fetch_with_fallback = fetch
    monkeypatch.setattr("stockscout_eod.runner.time.sleep", lambda _seconds: None)

    preflight_session(runner, date(2026, 8, 28), [f"T{i}" for i in range(8)])

    assert calls == 20
