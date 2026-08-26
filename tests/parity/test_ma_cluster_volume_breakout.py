from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from stock_scout.pipeline import orchestrator
from stock_scout.setups.ma_cluster_volume_breakout import (
    MAClusterVolumeBreakoutConfig,
    MAClusterVolumeBreakoutDetector,
)


def _base_frame(periods: int = 280) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-02", periods=periods)
    x = np.arange(periods, dtype=float)
    # Slow constructive trend plus enough natural variation to avoid looking
    # like a deal-locked/M&A flat-cap series.
    close = 18.8 + 0.006 * x + 0.22 * np.sin(x / 13.0)
    close[-45:] = 20.35 + 0.10 * np.sin(np.arange(45) / 3.5)
    open_ = close * 0.998
    high = np.maximum(open_, close) * 1.018
    low = np.minimum(open_, close) * 0.982
    volume = np.full(periods, 1_000_000.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _supportive_weekly(daily: pd.DataFrame, periods: int = 45) -> pd.DataFrame:
    """Completed weekly base whose thesis boundary is close enough to trade.

    The detector's actionable assertion is about a strong breakout with a
    defined weekly structure. Letting the detector resample the entire synthetic
    daily history creates a much older/deeper low and tests wide-risk demotion
    instead, which is a different behaviour covered by weekly-stop tests.
    """

    idx = pd.date_range(
        end=daily.index[-1] - pd.Timedelta(days=7), periods=periods, freq="W-FRI"
    )
    close = np.linspace(18.9, 20.45, periods)
    weekly = pd.DataFrame(
        {
            "open": close - 0.08,
            "high": close + 0.32,
            "low": close - 0.32,
            "close": close,
            "volume": np.full(periods, 5_000_000.0),
        },
        index=idx,
    )
    # A confirmed weekly pivot close enough to define <=8% trigger risk.
    weekly.iloc[-5, weekly.columns.get_loc("low")] = 20.30
    weekly.iloc[-4, weekly.columns.get_loc("low")] = 20.22
    weekly.iloc[-3, weekly.columns.get_loc("low")] = 20.10
    weekly.iloc[-2, weekly.columns.get_loc("low")] = 20.23
    weekly.iloc[-1, weekly.columns.get_loc("low")] = 20.28
    return weekly


def _features() -> dict:
    return {
        "ticker": "EXMP",
        "rs_score_weighted": 12.0,
        "rs_score_3m": 11.0,
        "rs_score_6m": 5.0,
        "rs_line_at_50d_high": True,
        "rs_line_at_52w_high": False,
    }


def _add_breakout(df: pd.DataFrame, pos: int = -1, volume: float = 2_500_000.0) -> pd.DataFrame:
    out = df.copy()
    # Put the context bar below the full compressed MA band, then launch
    # through all five averages with a strong close. These values intentionally
    # produce a real 5/5 cross rather than merely a close above the cluster.
    context_pos = pos - 1
    out.iloc[context_pos, out.columns.get_loc("close")] = 19.85
    out.iloc[context_pos, out.columns.get_loc("open")] = 19.95
    out.iloc[context_pos, out.columns.get_loc("high")] = 20.15
    out.iloc[context_pos, out.columns.get_loc("low")] = 19.75

    out.iloc[pos, out.columns.get_loc("open")] = 19.90
    out.iloc[pos, out.columns.get_loc("high")] = 21.55
    out.iloc[pos, out.columns.get_loc("low")] = 19.90
    out.iloc[pos, out.columns.get_loc("close")] = 21.38
    out.iloc[pos, out.columns.get_loc("volume")] = volume
    return out


def test_high_volume_thrust_through_tight_cluster_is_actionable():
    daily = _add_breakout(_base_frame())
    detector = MAClusterVolumeBreakoutDetector()

    result = detector.detect(
        daily,
        df_weekly=_supportive_weekly(daily),
        features=_features(),
    )

    assert result.triggered
    assert result.sub_state == "one_day_thrust"
    assert result.actionability == "actionable_now"
    assert result.raw_features["ma_cluster_width_pct"] <= 8.0
    assert result.raw_features["mas_crossed"] == 5
    assert result.raw_features["breakout_rel_volume_20d"] >= 2.0
    assert result.raw_features["breakout_close_location"] >= 0.65
    assert result.raw_features["weekly_structural_support_source"] == "weekly_pivot_low"
    assert result.raw_features["structural_stop_distance_pct"] <= 8.0
    assert result.trigger_level is not None
    assert result.invalidation_level is not None
    assert result.trigger_level > result.invalidation_level
    annotation = result.raw_features["ma_cluster_thrust_daily"]
    assert annotation["available"] is True
    assert annotation["timeframe"] == "daily"
    assert annotation["relative_volume"] >= 2.0


def test_thrust_annotation_keeps_weekly_data_separate_from_daily_setup():
    daily = _add_breakout(_base_frame(periods=320))
    weekly = _supportive_weekly(daily, periods=55)

    result = MAClusterVolumeBreakoutDetector().detect(daily, df_weekly=weekly, features=_features())

    daily_annotation = result.raw_features["ma_cluster_thrust_daily"]
    weekly_annotation = result.raw_features["ma_cluster_thrust_weekly"]
    assert daily_annotation["available"] is True
    assert weekly_annotation["available"] is True
    assert weekly_annotation["timeframe"] == "weekly"
    assert weekly_annotation["mas_total"] == 5
    # The annotation must not manufacture a second triggered setup or alter
    # the established detector's setup identity.
    assert result.setup_name == "ma_cluster_volume_breakout"


def test_recent_breakout_uses_original_ignition_volume_for_follow_through():
    daily = _add_breakout(_base_frame(), pos=-2)
    daily.iloc[-1, daily.columns.get_loc("open")] = 21.15
    daily.iloc[-1, daily.columns.get_loc("high")] = 21.50
    daily.iloc[-1, daily.columns.get_loc("low")] = 21.05
    daily.iloc[-1, daily.columns.get_loc("close")] = 21.42
    daily.iloc[-1, daily.columns.get_loc("volume")] = 1_100_000.0

    result = MAClusterVolumeBreakoutDetector().detect(daily, features=_features())

    assert result.triggered
    assert result.sub_state == "follow_through"
    assert result.raw_features["breakout_age_bars"] == 1
    assert result.raw_features["breakout_rel_volume_20d"] >= 2.0
    assert result.raw_features["held_above_cluster"] is True


def test_low_volume_breakout_is_not_promoted_to_actionable_now():
    daily = _add_breakout(_base_frame(), volume=1_250_000.0)

    result = MAClusterVolumeBreakoutDetector().detect(daily, features=_features())

    assert result.triggered
    assert result.actionability in {"near_actionable", "watch"}
    assert result.actionability != "actionable_now"
    assert "ma_cluster_volume_below_actionable" in result.warning_flags


def test_tight_cluster_without_thrust_surfaces_as_pre_breakout_watch():
    daily = _base_frame()
    daily.iloc[-1, daily.columns.get_loc("close")] = 20.40
    daily.iloc[-1, daily.columns.get_loc("open")] = 20.34
    daily.iloc[-1, daily.columns.get_loc("high")] = 20.52
    daily.iloc[-1, daily.columns.get_loc("low")] = 20.22

    result = MAClusterVolumeBreakoutDetector().detect(daily, features=_features())

    assert result.triggered
    assert result.sub_state == "pre_breakout"
    assert result.actionability in {"near_actionable", "watch"}
    assert result.raw_features["ma_cluster_width_pct"] <= 12.0
    assert result.trigger_level is not None


def test_breakout_more_than_max_extension_is_flagged_too_late():
    daily = _add_breakout(_base_frame(), pos=-2)
    daily.iloc[-1, daily.columns.get_loc("open")] = 22.20
    daily.iloc[-1, daily.columns.get_loc("high")] = 22.75
    daily.iloc[-1, daily.columns.get_loc("low")] = 22.05
    daily.iloc[-1, daily.columns.get_loc("close")] = 22.65
    daily.iloc[-1, daily.columns.get_loc("volume")] = 1_200_000.0

    result = MAClusterVolumeBreakoutDetector().detect(daily, features=_features())

    assert not result.triggered
    assert result.sub_state == "extended"
    assert result.actionability == "extended_too_late"
    assert result.raw_features["distance_from_cluster_top_pct"] > 7.0


def test_custom_threshold_object_is_coerced_without_schema_dependency():
    cfg = SimpleNamespace(
        enabled=True,
        tight_cluster_width_pct=5.0,
        min_breakout_rel_volume=3.0,
    )
    detector = MAClusterVolumeBreakoutDetector(cfg)

    assert isinstance(detector.cfg, MAClusterVolumeBreakoutConfig)
    assert detector.cfg.tight_cluster_width_pct == 5.0
    assert detector.cfg.min_breakout_rel_volume == 3.0
    assert detector.cfg.max_breakout_age_bars == MAClusterVolumeBreakoutConfig().max_breakout_age_bars


def test_pipeline_registration_includes_detector():
    import inspect

    source = inspect.getsource(orchestrator.PipelineRunner.__init__)
    assert "MAClusterVolumeBreakoutDetector" in source
    assert "settings.setups.ma_cluster_volume_breakout" in source


def test_backtest_registration_includes_detector() -> None:
    import pytest

    pytest.skip("backtest code remains in the private research lab by design")
    import inspect

    import scripts.backtest as backtest

    source = inspect.getsource(backtest._make_detectors)
    assert "MAClusterVolumeBreakoutDetector" in source
    assert "settings.setups.ma_cluster_volume_breakout" in source
    assert '"ma_cluster_volume_breakout"' in inspect.getsource(backtest.main)
