"""Data quality checks for OHLCV frames and quotes.

Two concerns:
  1. Per-frame integrity: detect gaps, NaNs, stale data, suspicious one-day
     jumps that aren't backed by volume (split / corporate-action divergence
     suspect), insufficient history, duplicate dates.
  2. Cross-provider sanity: compare primary vs secondary close / volume /
     quote timestamp drift.

The checker emits `QualityIssue` records that the orchestrator maps to
`Flag` entries on the Candidate (severity drives whether the ticker survives
into the main candidate list vs goes to the Excluded section).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import numpy as np
import pandas as pd

QualitySeverity = Literal["info", "warning", "error"]


@dataclass
class QualityIssue:
    code: str
    severity: QualitySeverity
    detail: str = ""


@dataclass
class QualityCheckConfig:
    max_staleness_trading_days: int = 5
    min_bars: int = 250
    max_one_day_jump_pct: float = 25.0  # close-to-close
    max_one_day_jump_pct_with_low_volume: float = 12.0
    max_gap_trading_days: int = 5  # gap between consecutive index dates
    suspicious_volume_ratio: float = 0.05  # current vs trailing 50d avg


def _trading_day_diff(d1: date, d2: date) -> int:
    """Approximate count of US trading days between d1 and d2 (exclusive of d1).
    Uses 5-day work week as a cheap heuristic — good enough for staleness."""
    if d2 < d1:
        return 0
    days = pd.bdate_range(d1 + timedelta(days=1), d2)
    return len(days)


def check_frame(
    df: pd.DataFrame,
    *,
    ticker: str,
    today: date | None = None,
    cfg: QualityCheckConfig | None = None,
) -> list[QualityIssue]:
    """Return a list of integrity issues found in `df`. Empty list = clean."""
    cfg = cfg or QualityCheckConfig()
    today = today or datetime.now(timezone.utc).date()
    issues: list[QualityIssue] = []

    if df is None or df.empty:
        issues.append(QualityIssue("empty_frame", "error", "no rows"))
        return issues

    # Epoch / future-date corruption (the 1970-01-01 silent-coerce bug).
    if isinstance(df.index, pd.DatetimeIndex):
        bad_pre1990 = int((df.index < pd.Timestamp("1990-01-01")).sum())
        if bad_pre1990:
            issues.append(
                QualityIssue(
                    "epoch_corruption",
                    "error",
                    f"{bad_pre1990} rows have pre-1990 timestamps (RangeIndex→1970 bug)",
                )
            )
        bad_future = int((df.index > pd.Timestamp(today) + pd.Timedelta(days=7)).sum())
        if bad_future:
            issues.append(
                QualityIssue(
                    "future_dates",
                    "error",
                    f"{bad_future} rows have dates >7d in the future",
                )
            )

    n = len(df)
    if n < cfg.min_bars:
        issues.append(QualityIssue("insufficient_history", "warning", f"{n}<{cfg.min_bars}"))

    # NaN columns
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            nan_n = int(df[col].isna().sum())
            if nan_n > 0:
                issues.append(QualityIssue(f"nan_in_{col}", "warning", f"{nan_n} rows"))

    # Negative volume / non-positive close
    if "volume" in df.columns and (df["volume"] < 0).any():
        issues.append(QualityIssue("negative_volume", "error", ""))
    if "close" in df.columns and (df["close"] <= 0).any():
        issues.append(QualityIssue("non_positive_close", "error", ""))

    # OHLC relationship integrity: a valid bar satisfies
    #   high >= max(open, close)  and  low <= min(open, close)  and  high >= low.
    # Violations mean corrupt/misaligned data (e.g. a flattened MultiIndex put
    # the wrong column under 'high', or a provider returned unadjusted-vs-adjusted
    # mismatched fields). high < low is structurally impossible → error; open/close
    # poking outside [low, high] is treated as a warning unless it's widespread.
    if all(c in df.columns for c in ("open", "high", "low", "close")):
        o, h, lo, c = df["open"], df["high"], df["low"], df["close"]
        high_below_low = (h < lo).fillna(False)
        out_of_bounds = ((h < o) | (h < c) | (lo > o) | (lo > c)).fillna(False) & ~high_below_low
        hl_n = int(high_below_low.sum())
        oob_n = int(out_of_bounds.sum())
        if hl_n:
            issues.append(QualityIssue("ohlc_high_below_low", "error", f"{hl_n} bars high<low"))
        if oob_n:
            sev: QualitySeverity = "error" if oob_n > max(1, int(0.01 * n)) else "warning"
            issues.append(
                QualityIssue("ohlc_out_of_bounds", sev, f"{oob_n} bars open/close outside [low,high]")
            )

    # Duplicate index dates
    if df.index.duplicated().any():
        dup = int(df.index.duplicated().sum())
        issues.append(QualityIssue("duplicate_dates", "warning", f"{dup} duplicates"))

    # Staleness
    if isinstance(df.index, pd.DatetimeIndex) and len(df) > 0:
        last = df.index.max()
        try:
            last_d = last.date()
        except AttributeError:
            last_d = pd.Timestamp(last).date()
        td_gap = _trading_day_diff(last_d, today)
        if td_gap > cfg.max_staleness_trading_days:
            issues.append(
                QualityIssue(
                    "stale_data",
                    "warning",
                    f"last bar {last_d.isoformat()} is {td_gap} trading days behind",
                )
            )

    # Suspicious one-day jumps (split / data error)
    if "close" in df.columns and "volume" in df.columns and n >= 50:
        close = df["close"]
        vol = df["volume"]
        prev = close.shift(1)
        # Absolute % move (use clipped denom)
        pct_move = (close - prev).abs() / prev.replace(0, np.nan) * 100.0
        avg_vol = vol.rolling(50, min_periods=20).mean()
        vol_ratio = (vol / avg_vol).replace([np.inf, -np.inf], np.nan)
        # Major jump without backing volume
        major_low_vol = (pct_move >= cfg.max_one_day_jump_pct_with_low_volume) & (vol_ratio < 0.7)
        # Extreme jump regardless of volume
        extreme = pct_move >= cfg.max_one_day_jump_pct
        flagged_idx = list(close.index[major_low_vol.fillna(False) | extreme.fillna(False)])
        if flagged_idx:
            sample = ",".join(str(pd.Timestamp(i).date()) for i in flagged_idx[-3:])
            issues.append(
                QualityIssue(
                    "suspicious_jump",
                    "warning",
                    f"{len(flagged_idx)} bars; last: {sample}",
                )
            )

    # Trading-day gaps inside the series (delisted / halted / data missing)
    if isinstance(df.index, pd.DatetimeIndex) and n >= 2:
        dates = pd.Series(df.index)
        gaps_bd = []
        for a, b in zip(dates.iloc[:-1].dt.date, dates.iloc[1:].dt.date):
            td = _trading_day_diff(a, b)
            if td > cfg.max_gap_trading_days:
                gaps_bd.append((a, b, td))
        if gaps_bd:
            a, b, td = gaps_bd[-1]
            issues.append(
                QualityIssue(
                    "trading_day_gap",
                    "warning",
                    f"{len(gaps_bd)} gap(s); last {a}→{b} ({td} trading days)",
                )
            )

    return issues


@dataclass
class CrossProviderCheckConfig:
    close_warning_pct: float = 1.0
    close_error_pct: float = 5.0
    volume_warning_ratio: float = 0.5  # |1 - secondary/primary|
    timestamp_warning_minutes: int = 24 * 60  # 1 trading day


def check_cross_provider_quote(
    *,
    ticker: str,
    primary_close: float | None,
    primary_volume: int | None,
    primary_timestamp: datetime | None,
    secondary_close: float | None,
    secondary_volume: int | None,
    secondary_timestamp: datetime | None,
    cfg: CrossProviderCheckConfig | None = None,
) -> list[QualityIssue]:
    cfg = cfg or CrossProviderCheckConfig()
    issues: list[QualityIssue] = []

    if primary_close is None or secondary_close is None:
        issues.append(QualityIssue("no_secondary_quote", "warning", "missing close"))
        return issues

    close_diff = abs(primary_close - secondary_close) / max(1e-9, primary_close) * 100.0
    if close_diff >= cfg.close_error_pct:
        issues.append(
            QualityIssue("close_mismatch", "error", f"{close_diff:.2f}% (>{cfg.close_error_pct}%)")
        )
    elif close_diff >= cfg.close_warning_pct:
        issues.append(
            QualityIssue("close_warning", "warning", f"{close_diff:.2f}% (>{cfg.close_warning_pct}%)")
        )

    if primary_volume and secondary_volume:
        ratio_diff = abs(1.0 - secondary_volume / max(1.0, primary_volume))
        if ratio_diff >= cfg.volume_warning_ratio:
            issues.append(
                QualityIssue(
                    "volume_divergence",
                    "warning",
                    f"primary={primary_volume} secondary={secondary_volume}",
                )
            )

    if primary_timestamp and secondary_timestamp:
        # tz-normalize
        a = primary_timestamp.astimezone(timezone.utc) if primary_timestamp.tzinfo else primary_timestamp.replace(tzinfo=timezone.utc)
        b = secondary_timestamp.astimezone(timezone.utc) if secondary_timestamp.tzinfo else secondary_timestamp.replace(tzinfo=timezone.utc)
        delta_min = abs((a - b).total_seconds()) / 60.0
        if delta_min > cfg.timestamp_warning_minutes:
            issues.append(
                QualityIssue(
                    "timestamp_drift",
                    "info",
                    f"primary↔secondary differ by {delta_min:.0f} min",
                )
            )

    return issues


@dataclass
class IntegrityReport:
    ticker: str
    rows: int
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def has_warning(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)
