from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ENGINE = Path(__file__).resolve().parents[1] / "engines" / "next"
SESSION = date(2026, 10, 1)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules(monkeypatch):
    repair = load_module("market_session_repair", ENGINE / "market_session_repair.py")
    monkeypatch.setitem(sys.modules, "market_session_repair", repair)
    gate = load_module("next_market_readiness", ENGINE / "check_market_readiness.py")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return gate, repair


def history(*, partial=False, day="2026-10-01", field="Close"):
    frame = pd.DataFrame(
        {"Open": [10.0, 11.0], "High": [12.0, 13.0], "Low": [9.0, 10.0],
         "Close": [11.0, 12.0], "Volume": [1000.0, 1200.0]},
        index=pd.DatetimeIndex(["2026-09-29", day]),
    )
    if partial:
        frame.loc[frame.index[-1], field] = float("nan")
    return frame


def sample(gate, **kwargs):
    return pd.concat({ticker: history(**kwargs) for ticker in gate.SAMPLE}, axis=1)


def run_gate(gate, monkeypatch, tmp_path):
    output = tmp_path / "readiness.json"
    monkeypatch.setattr(sys, "argv", ["gate", "--session-date", SESSION.isoformat(), "--output", str(output)])
    result = gate.main()
    return result, json.loads(output.read_text())


def test_complete_sample_starts_without_repair(modules, monkeypatch, tmp_path):
    gate, _ = modules
    calls = []

    def download(*args, **kwargs):
        calls.append(kwargs)
        return sample(gate)

    monkeypatch.setattr(gate.yf, "download", download)
    code, diagnostic = run_gate(gate, monkeypatch, tmp_path)
    assert code == 0
    assert len(calls) == 1
    assert diagnostic["status"] == "ready"
    assert diagnostic["retryable"] is False


@pytest.mark.parametrize("field", ["Close", "Volume"])
def test_partial_terminal_sample_recovers_by_exact_day(modules, monkeypatch, tmp_path, field):
    gate, _ = modules
    calls = []

    def download(*args, **kwargs):
        calls.append(kwargs)
        return sample(gate) if kwargs["start"] == SESSION.isoformat() else sample(gate, partial=True, field=field)

    monkeypatch.setattr(gate.yf, "download", download)
    code, diagnostic = run_gate(gate, monkeypatch, tmp_path)
    assert code == 0
    assert len(calls) == 2
    assert calls[1]["end"] == "2026-10-02"
    assert calls[1]["auto_adjust"] is True
    assert calls[1]["threads"] is False
    assert diagnostic["attempts"][0]["exactSessionRecovered"] == sorted(gate.SAMPLE)
    assert set(diagnostic["attempts"][0]["dates"].values()) == {"2026-10-01"}


def test_actual_provider_lag_stops_with_retryable_diagnostics(modules, monkeypatch, tmp_path, capsys):
    gate, _ = modules
    calls = []
    delays = []

    def download(*args, **kwargs):
        calls.append(kwargs)
        return sample(gate, day="2026-09-30")

    monkeypatch.setattr(gate.yf, "download", download)
    monkeypatch.setattr(gate.time, "sleep", delays.append)
    code, diagnostic = run_gate(gate, monkeypatch, tmp_path)
    assert code == 75
    assert diagnostic["status"] == "provider_not_ready"
    assert diagnostic["retryable"] is True
    assert len(diagnostic["attempts"]) == 4
    assert delays == [30, 60, 120]
    assert len(calls) == 8
    assert "NEXT_PROVIDER_NOT_READY session=2026-10-01" in capsys.readouterr().out


def test_sample_requires_complete_spy_and_eighty_percent(modules):
    gate, _ = modules
    frames = {ticker: history() for ticker in gate.SAMPLE}
    frames["SPY"] = history(partial=True)
    assert not gate.readiness(pd.concat(frames, axis=1), SESSION)[0]
    frames["SPY"] = history()
    for ticker in gate.SAMPLE[-3:]:
        frames[ticker] = history(day="2026-09-30")
    assert not gate.readiness(pd.concat(frames, axis=1), SESSION)[0]
    frames[gate.SAMPLE[-1]] = history()
    assert gate.readiness(pd.concat(frames, axis=1), SESSION)[0]


def test_flat_multiticker_response_cannot_stand_in_for_ten_symbols(modules, monkeypatch):
    gate, repair = modules
    monkeypatch.setattr(repair.yf, "download", lambda *args, **kwargs: history())
    assert gate.readiness(history(), SESSION)[0] is False
    assert repair.download_session_bars(list(gate.SAMPLE), SESSION) == {}


def test_exact_bar_rejects_wrong_day_and_nonfinite_fields(modules):
    _, repair = modules
    assert repair.session_bar(history(day="2026-09-30"), SESSION).empty
    frame = history()
    frame.iloc[-1, frame.columns.get_loc("Close")] = float("inf")
    assert repair.session_bar(frame, SESSION).empty


def test_repair_preserves_prior_history_and_normalizes_exchange_date(modules):
    _, repair = modules
    old = history(partial=True)
    bar = history().tail(1)
    bar.index = pd.DatetimeIndex(["2026-10-01 04:00:00+00:00"])
    result = repair.replace_session_bar(old, bar, SESSION)
    pd.testing.assert_frame_equal(result.iloc[:-1], old.iloc[:-1])
    assert result.index[-1].date() == SESSION
    assert result.iloc[-1]["Close"] == 12.0
    assert len(result) == len(old)


def test_both_yahoo_column_orientations_are_supported(modules):
    _, repair = modules
    raw = pd.concat({"SPY": history(), "QQQ": history(partial=True)}, axis=1)
    for frame in (raw, raw.swaplevel(axis=1)):
        assert not repair.session_bar(repair.ticker_frame(frame, "SPY"), SESSION).empty
        assert repair.session_bar(repair.ticker_frame(frame, "QQQ"), SESSION).empty
