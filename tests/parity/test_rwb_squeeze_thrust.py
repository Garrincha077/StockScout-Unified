from __future__ import annotations

import numpy as np
import pandas as pd

from stock_scout.config.schema import RWBSqueezeThrustSetupConfig
from stock_scout.setups.rwb_squeeze_thrust import RWBSqueezeThrustDetector


def _weekly_frame(close_vals: list[float], volume_vals: list[int] | None = None) -> pd.DataFrame:
    idx = pd.date_range("2024-01-05", periods=len(close_vals), freq="W-FRI")
    close = np.array(close_vals, dtype=float)
    if volume_vals is None:
        volume_vals = [1_000_000] * len(close)
    return pd.DataFrame(
        {
            "open": close * 0.985,
            "high": close * 1.015,
            "low": close * 0.965,
            "close": close,
            "volume": volume_vals,
        },
        index=idx,
    )


def _daily_from_weekly(weekly: pd.DataFrame) -> pd.DataFrame:
    rows = []
    dates = []
    prev_close = float(weekly["close"].iloc[0])
    for week_end, row in weekly.iterrows():
        days = pd.bdate_range(pd.Timestamp(week_end) - pd.offsets.BDay(4), periods=5)
        closes = np.linspace(prev_close, float(row["close"]), len(days))
        for d, c in zip(days, closes):
            dates.append(d)
            rows.append(
                {
                    "open": c * 0.995,
                    "high": c * 1.015,
                    "low": c * 0.985,
                    "close": c,
                    "volume": float(row["volume"]) / 5.0,
                }
            )
        prev_close = float(row["close"])
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(dates))
    return df[~df.index.duplicated(keep="last")].sort_index()


def _features(ticker: str = "BABA", rs_up: bool = True) -> dict:
    return {
        "ticker": ticker,
        "rs_score_3m": 8.0 if rs_up else -2.0,
        "rs_score_6m": 1.0,
        "rs_line_at_50d_high": rs_up,
        "rs_line_at_52w_high": False,
    }


def _squeeze_series() -> tuple[list[float], list[int]]:
    x = np.arange(76)
    close = list(10.0 + 0.10 * np.sin(x / 4.0) + x * 0.006)
    volume = [1_000_000] * len(close)
    for i in (48, 63):
        close[i] = close[i - 1] * 1.045
        volume[i] = 2_000_000
    return close, volume


def test_rwb_squeeze_thrust_triggers_on_tight_band_prior_attempts_and_current_thrust():
    close, volume = _squeeze_series()
    close[-1] = max(close[-12:]) * 1.08
    volume[-1] = 2_600_000
    weekly = _weekly_frame(close, volume)
    daily = _daily_from_weekly(weekly)
    detector = RWBSqueezeThrustDetector(RWBSqueezeThrustSetupConfig())

    result = detector.detect(daily, weekly, _features())

    assert result.triggered
    assert result.sub_state in {"thrusting", "confirmed", "trendline_breakout"}
    assert result.actionability in {"near_actionable", "actionable_now"}
    assert result.raw_features["rwb_squeeze_thrust"] is True
    assert result.raw_features["rwb_thrust_rel_volume"] >= 1.5
    assert result.raw_features["prior_rwb_thrust_attempts"] >= 1


def test_rwb_squeeze_without_volume_thrust_is_watch_only():
    close, volume = _squeeze_series()
    weekly = _weekly_frame(close, volume)
    daily = _daily_from_weekly(weekly)
    detector = RWBSqueezeThrustDetector(RWBSqueezeThrustSetupConfig())

    result = detector.detect(daily, weekly, _features("FSLY", rs_up=False))

    assert result.triggered
    assert result.sub_state == "watch_squeeze"
    assert result.actionability == "watch"
    assert result.raw_features["rwb_squeeze_thrust"] is False


def test_rwb_squeeze_rejects_falling_or_wide_weekly_context():
    close = list(np.linspace(24.0, 8.0, 80))
    volume = [1_000_000] * len(close)
    volume[-1] = 3_000_000
    weekly = _weekly_frame(close, volume)
    daily = _daily_from_weekly(weekly)
    detector = RWBSqueezeThrustDetector(RWBSqueezeThrustSetupConfig())

    result = detector.detect(daily, weekly, _features("BAD"))

    assert not result.triggered
    assert "rwb_band_not_tight" in result.failed_conditions or "weekly_30w_not_flat_or_rising" in result.failed_conditions


def test_rwb_squeeze_flags_vertical_move_as_extended():
    close, volume = _squeeze_series()
    close[-1] = max(close[-20:]) * 1.55
    volume[-1] = 3_200_000
    weekly = _weekly_frame(close, volume)
    daily = _daily_from_weekly(weekly)
    cfg = RWBSqueezeThrustSetupConfig(max_extension_above_band_pct=25.0)
    detector = RWBSqueezeThrustDetector(cfg)

    result = detector.detect(daily, weekly, _features("AEVA"))

    assert not result.triggered
    assert result.sub_state == "extended"
    assert result.actionability == "extended_too_late"
