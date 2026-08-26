from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pandas_market_calendars as mcal

_NYSE = mcal.get_calendar("NYSE")
_NY_TZ = "America/New_York"


def today_ny() -> date:
    """Today's date in the NYSE timezone."""
    return datetime.now(tz=timezone.utc).astimezone(tz=_tz()).date()


def _tz():
    # Lazy import to avoid hard zoneinfo dependency surprises on Windows
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(_NY_TZ)
    except Exception:  # pragma: no cover
        import pytz

        return pytz.timezone(_NY_TZ)


def _completed_reference_date(now_utc: datetime | None = None) -> date:
    now_ny = (now_utc or datetime.now(tz=timezone.utc)).astimezone(tz=_tz())
    ref = now_ny.date()
    schedule = _NYSE.schedule(start_date=ref, end_date=ref)
    if schedule.empty:
        return ref
    close_ts = schedule.iloc[0]["market_close"].to_pydatetime()
    return ref if now_ny >= close_ts else ref - timedelta(days=1)


def last_trading_day(reference: date | None = None) -> date:
    """Return the most recent completed NYSE trading day on or before `reference`.

    When called without an explicit date during an active NYSE session, this
    intentionally returns the prior trading day because today's EOD bar does
    not exist yet.
    """
    ref = reference or _completed_reference_date()
    schedule = _NYSE.schedule(start_date=ref - timedelta(days=14), end_date=ref)
    if schedule.empty:
        return ref
    return schedule.index[-1].date()


def is_trading_day(d: date) -> bool:
    schedule = _NYSE.schedule(start_date=d, end_date=d)
    return not schedule.empty


def trading_days_between(start: date, end: date) -> int:
    schedule = _NYSE.schedule(start_date=start, end_date=end)
    return len(schedule)


def market_is_closed_for_today() -> bool:
    """True if NYSE has already closed for the current NY day (or today is not a trading day)."""
    now_ny = datetime.now(tz=timezone.utc).astimezone(tz=_tz())
    today = now_ny.date()
    schedule = _NYSE.schedule(start_date=today, end_date=today)
    if schedule.empty:
        return True
    close_ts = schedule.iloc[0]["market_close"]
    return now_ny >= close_ts.to_pydatetime()


def is_end_of_trading_week(d: date) -> bool:
    """True if `d` is a trading day and the next trading day falls in a later week.

    Detects the last NYSE trading day of a calendar week (typically Friday, but
    Thursday on weeks where Friday is a holiday).
    """
    if not is_trading_day(d):
        return False
    schedule = _NYSE.schedule(start_date=d + timedelta(days=1), end_date=d + timedelta(days=10))
    if schedule.empty:
        return True
    nxt = schedule.index[0].date()
    # isocalendar() -> (year, week, weekday); compare (year, week)
    d_iso = d.isocalendar()
    n_iso = nxt.isocalendar()
    return (d_iso[0], d_iso[1]) != (n_iso[0], n_iso[1])


def is_end_of_trading_month(d: date) -> bool:
    """True if `d` is a trading day and the next trading day falls in a later month."""
    if not is_trading_day(d):
        return False
    schedule = _NYSE.schedule(start_date=d + timedelta(days=1), end_date=d + timedelta(days=10))
    if schedule.empty:
        return True
    nxt = schedule.index[0].date()
    return (d.year, d.month) != (nxt.year, nxt.month)


def history_start(years: int, reference: date | None = None) -> date:
    """Return the start date for fetching `years` of history."""
    ref = reference or today_ny()
    return ref - timedelta(days=int(years * 365.25))


def to_pandas_ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d)
