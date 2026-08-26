"""Post-scan alert evaluation.

Two alert kinds, both evaluated AFTER a scan completes (not real-time):

* ``screen``    — a saved screener filter (the same nested ANY/ALL ``FilterGroup``
                  the web builder produces). Fires for candidates that match.
* ``trendline`` — a hand-drawn line on a ticker chart (two ``{time, price}``
                  points). Fires when the latest bar touches/breaks the line
                  projected to that bar's date.

The filter evaluator mirrors ``web/src/lib/screener/filters.ts`` exactly so the
UI preview and the server-side firing agree. Pure functions + a tiny dedupe
ledger keep this testable and replay-safe.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

from stock_scout.scoring.trade_plan import derive_trade_readiness, project_trade_readiness
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# Filter evaluator (port of web/src/lib/screener/filters.ts)
# --------------------------------------------------------------------------- #
def _as_number(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(float(v)) else None
    if isinstance(v, str):
        try:
            n = float(v.replace("$", "").replace("%", "").replace(",", ""))
        except ValueError:
            return None
        return n if math.isfinite(n) else None
    return None


def _is_missing(v) -> bool:
    if v is None or v == "":
        return True
    if isinstance(v, float) and not math.isfinite(v):
        return True
    return False


def match_condition(row: dict, c: dict) -> bool:
    raw = row.get(c.get("field"))
    op = c.get("op")
    if op == "is_set":
        return not _is_missing(raw)
    if op == "is_unset":
        return _is_missing(raw)
    if op == "is_true":
        return raw is True
    if op == "is_false":
        return raw is False
    if op in ("in", "not_in"):
        values = [str(s).lower() for s in (c.get("values") or [])]
        hit = (not _is_missing(raw)) and str(raw).lower() in values
        return hit if op == "in" else not hit

    n = _as_number(raw)
    if n is None:
        return False
    val = c.get("value")
    if op == "between":
        lo, hi = c.get("min"), c.get("max")
        if lo is not None and n < float(lo):
            return False
        if hi is not None and n > float(hi):
            return False
        return True
    if val is None and op in ("gte", "lte", "gt", "lt", "eq", "neq"):
        return False
    if op == "gte":
        return n >= float(val)
    if op == "lte":
        return n <= float(val)
    if op == "gt":
        return n > float(val)
    if op == "lt":
        return n < float(val)
    if op == "eq":
        return n == float(val)
    if op == "neq":
        return n != float(val)
    return False


def match_group(row: dict, g: dict) -> bool:
    results = [match_condition(row, c) for c in (g.get("conditions") or [])]
    results += [match_group(row, sub) for sub in (g.get("groups") or [])]
    if not results:
        return True
    return all(results) if g.get("logic") == "ALL" else any(results)


def match_row(row: dict, root: dict) -> bool:
    return match_group(row, root)


# --------------------------------------------------------------------------- #
# Trendline projection
# --------------------------------------------------------------------------- #
def _to_ordinal(t) -> float | None:
    """Day-ordinal for an ISO 'YYYY-MM-DD' (or datetime/seconds) anchor."""
    if t is None:
        return None
    if isinstance(t, (int, float)):
        # epoch seconds → fractional days
        return float(t) / 86400.0
    s = str(t)[:10]
    try:
        return float(date.fromisoformat(s).toordinal())
    except ValueError:
        return None


def project_line_price(points: list[dict], at_time) -> float | None:
    """Price of the trendline at ``at_time`` (linear through the two anchors,
    extrapolated). ``points`` = ``[{time, price}, {time, price}]``."""
    if not points or len(points) < 2:
        return None
    t1, t2 = _to_ordinal(points[0].get("time")), _to_ordinal(points[1].get("time"))
    p1, p2 = points[0].get("price"), points[1].get("price")
    at = _to_ordinal(at_time)
    if None in (t1, t2, p1, p2, at):
        return None
    if t2 == t1:
        return float(p2)
    slope = (float(p2) - float(p1)) / (t2 - t1)
    return float(p1) + slope * (at - t1)


def evaluate_trendline(alert: dict, bars: list[dict]) -> dict | None:
    """Return fire info if the latest bar meets the alert's ``mode`` against the
    projected line, else None. ``mode`` ∈ break_up | break_down | touch."""
    if not bars:
        return None
    last = bars[-1]
    line = project_line_price(alert.get("points") or [], last.get("time"))
    if line is None:
        return None
    mode = alert.get("mode", "touch")
    high = _as_number(last.get("high"))
    low = _as_number(last.get("low"))
    close = _as_number(last.get("close"))
    if close is None:
        return None
    fired = False
    if mode == "break_up":
        fired = close > line
    elif mode == "break_down":
        fired = close < line
    else:  # touch — intrabar contact with the line
        if high is not None and low is not None:
            fired = low <= line <= high
        else:
            fired = abs(close - line) / max(1e-9, line) <= 0.01
    if not fired:
        return None
    return {
        "ticker": alert.get("ticker"),
        "mode": mode,
        "line_price": round(line, 2),
        "close": round(close, 2),
        "date": str(last.get("time"))[:10],
    }


def _crossed(prev_close: float, close: float, level: float) -> bool:
    return (prev_close <= level <= close) or (prev_close >= level >= close)


def evaluate_price_alert(alert: dict, bars: list[dict]) -> dict | None:
    """Evaluate a horizontal price-level alert against the latest close.

    Operators mirror the TradingView-like UI vocabulary used by the web app:
    crossing, crossing_up, crossing_down, greater_than, less_than, touch.
    """
    if not bars:
        return None
    last = bars[-1]
    close = _as_number(last.get("close"))
    level = _as_number(alert.get("price") if alert.get("price") is not None else alert.get("level"))
    if close is None or level is None:
        return None
    prev_close = _as_number(bars[-2].get("close")) if len(bars) >= 2 else close
    op = alert.get("operator", "crossing_up")
    high = _as_number(last.get("high"))
    low = _as_number(last.get("low"))
    fired = False
    if op == "crossing":
        fired = prev_close is not None and _crossed(prev_close, close, level)
    elif op == "crossing_up":
        fired = prev_close is not None and prev_close <= level <= close
    elif op == "crossing_down":
        fired = prev_close is not None and prev_close >= level >= close
    elif op == "greater_than":
        fired = close > level
    elif op == "less_than":
        fired = close < level
    elif op == "touch":
        fired = (high is not None and low is not None and low <= level <= high) or abs(close - level) / max(1e-9, level) <= 0.01
    if not fired:
        return None
    return {
        "kind": "price",
        "ticker": str(alert.get("ticker") or "").upper(),
        "operator": op,
        "level": round(level, 2),
        "close": round(close, 2),
        "date": str(last.get("time"))[:10],
    }


def _drawing_by_id(drawings: list[dict], drawing_id: str | None) -> dict | None:
    if not drawing_id:
        return None
    return next((d for d in drawings if d.get("id") == drawing_id), None)


def _drawing_levels(alert: dict, drawing: dict | None, at_time) -> list[tuple[str, float]]:
    """Return named alertable levels for line/fib drawings at ``at_time``."""
    src = drawing or alert
    points = src.get("points") or []
    dtype = src.get("type") or ("horizontal" if len(points) >= 2 and points[0].get("price") == points[1].get("price") else "trendline")
    if dtype == "fib" and len(points) >= 2:
        p1 = _as_number(points[0].get("price"))
        p2 = _as_number(points[1].get("price"))
        if p1 is None or p2 is None:
            return []
        levels = src.get("fibLevels") or [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
        return [(f"fib {float(lv):.3g}", p1 + (p2 - p1) * float(lv)) for lv in levels]
    line = project_line_price(points, at_time)
    return [("line", line)] if line is not None else []


def _evaluate_level(operator: str, bars: list[dict], level: float) -> bool:
    last = bars[-1]
    close = _as_number(last.get("close"))
    if close is None:
        return False
    prev_close = _as_number(bars[-2].get("close")) if len(bars) >= 2 else close
    high = _as_number(last.get("high"))
    low = _as_number(last.get("low"))
    if operator == "crossing":
        return prev_close is not None and _crossed(prev_close, close, level)
    if operator == "crossing_up":
        return prev_close is not None and prev_close <= level <= close
    if operator == "crossing_down":
        return prev_close is not None and prev_close >= level >= close
    if operator == "greater_than":
        return close > level
    if operator == "less_than":
        return close < level
    if operator == "touch":
        return (high is not None and low is not None and low <= level <= high) or abs(close - level) / max(1e-9, level) <= 0.01
    return False


def _box_bounds(points: list[dict]) -> tuple[float, float, str, str] | None:
    if len(points) < 2:
        return None
    prices = [_as_number(p.get("price")) for p in points[:2]]
    times = [str(p.get("time"))[:10] for p in points[:2] if p.get("time")]
    if prices[0] is None or prices[1] is None or len(times) < 2:
        return None
    return min(prices), max(prices), min(times), max(times)


def evaluate_drawing_alert(alert: dict, bars: list[dict], drawings: list[dict] | None = None) -> dict | None:
    """Evaluate an alert linked to a saved chart drawing.

    Line-like drawings use price/line operators. Rectangle/channel support the
    coarse entering/exiting/inside/outside operators based on price containment.
    Fib drawings evaluate crossings/touches against every enabled fib level.
    """
    if not bars:
        return None
    drawings = drawings or []
    drawing = _drawing_by_id(drawings, alert.get("drawing_id"))
    src = drawing or alert
    points = src.get("points") or []
    if len(points) < 2:
        return None
    last = bars[-1]
    prev = bars[-2] if len(bars) >= 2 else last
    close = _as_number(last.get("close"))
    prev_close = _as_number(prev.get("close"))
    if close is None or prev_close is None:
        return None
    operator = alert.get("operator") or alert.get("mode") or "touch"
    operator = {"break_up": "crossing_up", "break_down": "crossing_down"}.get(operator, operator)
    dtype = src.get("type") or _line_type_for_alert(points)

    if dtype in {"rectangle", "channel"} and operator in {"entering", "exiting", "inside", "outside"}:
        bounds = _box_bounds(points)
        if bounds is None:
            return None
        low, high, start, end = bounds
        in_time = start <= str(last.get("time"))[:10] <= end
        inside = in_time and low <= close <= high
        prev_inside = in_time and low <= prev_close <= high
        fired = (
            (operator == "inside" and inside)
            or (operator == "outside" and not inside)
            or (operator == "entering" and inside and not prev_inside)
            or (operator == "exiting" and prev_inside and not inside)
        )
        if not fired:
            return None
        return {
            "kind": "drawing",
            "ticker": str(alert.get("ticker") or src.get("ticker") or "").upper(),
            "operator": operator,
            "drawing_id": alert.get("drawing_id") or src.get("id"),
            "drawing_type": dtype,
            "close": round(close, 2),
            "date": str(last.get("time"))[:10],
        }

    for label, level in _drawing_levels(alert, drawing, last.get("time")):
        if alert.get("level") is not None and abs(float(alert["level"]) - level) > 1e-9:
            continue
        if _evaluate_level(operator, bars, level):
            return {
                "kind": "drawing",
                "ticker": str(alert.get("ticker") or src.get("ticker") or "").upper(),
                "operator": operator,
                "drawing_id": alert.get("drawing_id") or src.get("id"),
                "drawing_type": dtype,
                "level_name": label,
                "line_price": round(level, 2),
                "close": round(close, 2),
                "date": str(last.get("time"))[:10],
            }
    return None


def _line_type_for_alert(points: list[dict]) -> str:
    if len(points) >= 2 and points[0].get("price") == points[1].get("price"):
        return "horizontal"
    return "trendline"


def evaluate_screen(candidates: list[dict], alert: dict, cap: int = 25) -> list[str]:
    """Tickers (uppercased) matching a screen alert's filter, capped.

    Readiness fields are projected in memory from ``trade_plan`` for both new
    and legacy reports.  That keeps saved `trade_status` / risk filters aligned
    with the dashboard without modifying the scan artifact being evaluated.
    """
    flt = alert.get("filter") or {}
    out: list[str] = []
    for candidate in candidates:
        c = project_trade_readiness(candidate)
        t = str(c.get("ticker") or "").upper()
        if not t:
            continue
        if match_row(c, flt):
            out.append(t)
        if len(out) >= cap:
            break
    return out


def evaluate_watchlist(candidates: list[dict], alert: dict, watchlists_store: dict, cap: int = 25) -> list[str]:
    """Tickers from a saved watchlist that also match an optional filter."""
    watchlist_id = alert.get("watchlist_id")
    lists = watchlists_store.get("lists", []) if isinstance(watchlists_store, dict) else []
    watchlist = next((w for w in lists if w.get("id") == watchlist_id), None)
    if watchlist is None and lists:
        watchlist = lists[0]
    tickers = {
        str(item.get("ticker") or "").upper()
        for item in (watchlist or {}).get("items", [])
        if item.get("ticker")
    }
    if not tickers:
        tickers = {str(t).upper() for t in (watchlist or {}).get("tickers", [])}
    flt = alert.get("filter") or {}
    out: list[str] = []
    for candidate in candidates:
        c = project_trade_readiness(candidate)
        t = str(c.get("ticker") or "").upper()
        if not t or t not in tickers:
            continue
        if match_row(c, flt):
            out.append(t)
        if len(out) >= cap:
            break
    return out


def entry_ready_transitions(
    current_candidates: list[dict],
    previous_candidates: list[dict],
) -> list[dict]:
    """Names whose authoritative readiness newly became ``entry_ready``.

    The comparison is intentionally between finished reports instead of an
    in-memory process flag: it naturally re-arms after a setup stops being
    ready, survives restarts, and works for legacy reports through the shared
    read-only derivation.
    """
    previous_by_ticker = {
        str(candidate.get("ticker") or "").upper(): candidate
        for candidate in previous_candidates
        if str(candidate.get("ticker") or "").strip()
    }
    transitions: list[dict] = []
    for candidate in current_candidates:
        ticker = str(candidate.get("ticker") or "").upper()
        if not ticker:
            continue
        plan, fields = derive_trade_readiness(candidate)
        if plan.status != "entry_ready":
            continue
        previous = previous_by_ticker.get(ticker)
        if previous is None:
            continue
        previous_plan, _ = derive_trade_readiness(previous)
        if previous_plan.status == "entry_ready":
            continue
        transitions.append(
            {
                "kind": "entry_ready",
                "ticker": ticker,
                "previous_trade_status": previous_plan.status,
                "trade_status": plan.status,
                "price": candidate.get("price"),
                "trigger_reference_level": plan.trigger_reference_level,
                "structural_invalidation_level": plan.structural_invalidation_level,
                "tactical_stop_level": plan.tactical_stop_level,
                "entry_risk_pct": fields["entry_risk_pct"],
            }
        )
    return transitions


# --------------------------------------------------------------------------- #
# Dedupe ledger (so a same-day re-run doesn't re-send)
# --------------------------------------------------------------------------- #
def load_ledger(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def already_sent(ledger: dict, as_of: str, key: str) -> bool:
    return key in set(ledger.get(str(as_of), []))


def mark_sent(ledger: dict, as_of: str, keys: list[str]) -> dict:
    day = ledger.setdefault(str(as_of), [])
    for k in keys:
        if k not in day:
            day.append(k)
    return ledger


def state_key(kind: str, alert_id: str | None, suffix: str = "") -> str:
    return ":".join(x for x in [kind, str(alert_id or "missing"), suffix] if x)


def should_fire_state(state: dict, key: str, condition_true: bool) -> bool:
    """Return true once per continuous true-condition streak.

    When a condition goes false, the state re-arms so a later true can trigger
    again. This is separate from the legacy same-day send ledger.
    """
    if not condition_true:
        state[key] = False
        return False
    if state.get(key):
        return False
    state[key] = True
    return True


def save_ledger(path: str | Path, ledger: dict) -> None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ledger), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:  # noqa: BLE001
        log.warning("alerts.ledger_write_failed", error=repr(e))


def append_events(path: str | Path, events: list[dict]) -> None:
    if not events:
        return
    path = Path(path)
    existing = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            existing = data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            existing = []
    for e in events:
        e.setdefault("fired_at", now_iso())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps((existing + events)[-1000:], indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as e:  # noqa: BLE001
        log.warning("alerts.events_write_failed", error=repr(e))


def _legacy_to_drawing_alert(t: dict) -> dict | None:
    if not t.get("enabled", True):
        return None
    ticker = str(t.get("ticker") or "").upper()
    if not ticker:
        return None
    mode = t.get("mode", "touch")
    operator = {"break_up": "crossing_up", "break_down": "crossing_down", "touch": "touch"}.get(mode, "touch")
    return {
        "id": t.get("id"),
        "name": f"{ticker} {operator.replace('_', ' ')}",
        "ticker": ticker,
        "drawing_id": t.get("drawing_id") or t.get("id"),
        "operator": operator,
        "enabled": True,
        "points": t.get("points") or [],
        "legacy": True,
    }


def load_alerts_store(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"version": 2, "screen": [], "price": [], "drawing": [], "watchlist": [], "trendline": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 2, "screen": [], "price": [], "drawing": [], "watchlist": [], "trendline": []}
    store = {
        "version": 2,
        "screen": data.get("screen", []),
        "price": data.get("price", []),
        "drawing": data.get("drawing", []),
        "watchlist": data.get("watchlist", []),
        "trendline": data.get("trendline", []),
    }
    seen = {a.get("id") for a in store["drawing"]}
    for legacy in data.get("trendline", []) or []:
        converted = _legacy_to_drawing_alert(legacy)
        if converted and converted.get("id") not in seen:
            store["drawing"].append(converted)
    return store


def load_drawings_store(path: str | Path, alerts_path: str | Path | None = None) -> dict:
    path = Path(path)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    else:
        data = {}
    drawings = data.get("drawings", []) if isinstance(data, dict) else []
    seen = {d.get("id") for d in drawings}
    if alerts_path:
        for legacy in load_alerts_store(alerts_path).get("trendline", []):
            if legacy.get("id") in seen or not legacy.get("points"):
                continue
            drawings.append(
                {
                    "id": legacy.get("id"),
                    "ticker": str(legacy.get("ticker") or "").upper(),
                    "type": _line_type_for_alert(legacy.get("points") or []),
                    "points": legacy.get("points") or [],
                    "legacy": True,
                }
            )
    return {"version": 1, "drawings": drawings}


def load_watchlists_store(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"version": 1, "lists": [], "active_id": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"version": 1, "lists": [], "active_id": None}
    return {
        "version": 1,
        "lists": data.get("lists", []),
        "active_id": data.get("active_id"),
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_screen_alert(name: str, tickers: list[str]) -> str:
    head = f"📋 Screen alert: {name}"
    return head + "\n" + ", ".join(tickers)


def render_entry_ready_alert(transitions: list[dict]) -> str:
    """Render the automatic readiness transition without calling a stop a stop.

    ``structural_invalidation`` stays explicit because it is the detector's
    thesis boundary.  A tactical stop appears only when the central plan has
    actually made the candidate entry-ready.
    """
    lines = ["🚀 Entry ready: trade plan changed to entry_ready"]
    for transition in transitions:
        ticker = transition.get("ticker") or "?"
        trigger = transition.get("trigger_reference_level")
        tactical = transition.get("tactical_stop_level")
        structural = transition.get("structural_invalidation_level")
        risk = transition.get("entry_risk_pct")
        parts = [str(ticker)]
        if trigger is not None:
            parts.append(f"trigger {float(trigger):.2f}")
        if tactical is not None:
            parts.append(f"tactical stop {float(tactical):.2f}")
        if structural is not None:
            parts.append(f"structural invalidation {float(structural):.2f}")
        if risk is not None:
            parts.append(f"risk {float(risk):.1f}%")
        lines.append(" · ".join(parts))
    return "\n".join(lines)


def render_trendline_alert(fire: dict) -> str:
    arrow = {"break_up": "📈 broke above", "break_down": "📉 broke below", "touch": "🎯 touched"}.get(
        fire.get("mode"), "touched"
    )
    return (
        f"✏️ Trendline alert: {fire.get('ticker')} {arrow} line "
        f"~{fire.get('line_price')} (close {fire.get('close')}, {fire.get('date')})"
    )


def render_price_alert(fire: dict) -> str:
    return (
        f"Price alert: {fire.get('ticker')} {str(fire.get('operator')).replace('_', ' ')} "
        f"{fire.get('level')} (close {fire.get('close')}, {fire.get('date')})"
    )


def render_drawing_alert(fire: dict) -> str:
    level = fire.get("line_price") if fire.get("line_price") is not None else fire.get("level")
    suffix = f" {level}" if level is not None else ""
    return (
        f"Drawing alert: {fire.get('ticker')} {str(fire.get('operator')).replace('_', ' ')} "
        f"{fire.get('drawing_type', 'drawing')}{suffix} "
        f"(close {fire.get('close')}, {fire.get('date')})"
    )


def render_watchlist_alert(name: str, tickers: list[str]) -> str:
    head = f"Watchlist alert: {name}"
    return head + "\n" + ", ".join(tickers)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
