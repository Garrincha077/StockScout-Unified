"""Telegram notification module.

Sends a daily / weekly digest of top breakout candidates to a Telegram chat
via the Bot API. No external SDK — uses `requests` directly.

Config:
  - `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`
  - `reports.send_telegram: true` in config.yaml to enable

Default schedule: weekly Friday (user runs `scout notify telegram` manually for
now; Windows Task Scheduler integration is out of scope for v1).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from stock_scout.ranker.io_schema import RankedCandidate
from stock_scout.scoring.focus_blend import candidate_blend_score, headline_ranking
from stock_scout.scoring.models import Candidate, TradePlan
from stock_scout.scoring.risk import is_hidden_by_default
from stock_scout.scoring.trade_plan import derive_trade_plan
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LEN = 3900  # Telegram hard cap is 4096; reserve room for part labels.


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str
    parse_mode: str = "MarkdownV2"   # rich formatting; we escape MD-special chars
    disable_web_page_preview: bool = True


LAST_SEND_ERROR: str | None = None

# Outward sends are the one thing an unattended run must never do by accident.
# A scan on the last trading day of the week fires the returns leaderboard, so
# "just run a scan to measure something" messages the user. The guard sits at
# the single choke point every send path goes through — digest, alerts and
# leaderboard all end at send_message — rather than at each call site, so a
# path added later is suppressed without anyone remembering to.
#
# It reads an environment variable as well as the module flag because the API
# runs the CLI as a subprocess: a module-level flag does not cross a process
# boundary, an inherited env var does.
NO_NOTIFY_ENV = "STOCK_SCOUT_NO_NOTIFY"
_suppressed = False


def suppress_sends(enabled: bool = True) -> None:
    """Turn every outward send into a no-op, for this process and its children."""
    global _suppressed
    _suppressed = enabled
    if enabled:
        os.environ[NO_NOTIFY_ENV] = "1"
    else:
        os.environ.pop(NO_NOTIFY_ENV, None)


def sends_suppressed() -> bool:
    if _suppressed:
        return True
    return os.environ.get(NO_NOTIFY_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _escape_md_v2(text: str) -> str:
    """Escape Telegram MarkdownV2 reserved chars."""
    if text is None:
        return ""
    specials = r"_*[]()~`>#+-=|{}.!\\"
    return "".join("\\" + c if c in specials else c for c in str(text))


def _split_oversized_block(block: str, limit: int) -> list[str]:
    """Split an exceptional oversized block without cutting Markdown escapes."""
    parts: list[str] = []
    remaining = block
    while len(remaining) > limit:
        cut = remaining.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        # An odd number of trailing backslashes would split a MarkdownV2 escape.
        candidate = remaining[:cut].rstrip()
        while cut > 1 and (len(candidate) - len(candidate.rstrip("\\"))) % 2 == 1:
            cut -= 1
            candidate = remaining[:cut].rstrip()
        parts.append(candidate)
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def split_telegram_message(text: str, *, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Pack a message into ordered Telegram-safe parts without dropping content."""
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]

    # Reserve enough room for ``\(123/123\)`` and the following newline.
    payload_limit = max(128, limit - 20)
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            block = "\n".join(current)
            blocks.extend(
                [block] if len(block) <= payload_limit else _split_oversized_block(block, payload_limit)
            )
            current.clear()

    # Candidate renderers start each atomic candidate with a numbered bold
    # line. Keep its continuation lines together and split only between names.
    for line in text.splitlines():
        candidate_start = bool(re.match(r"^\*\d+\\\.", line))
        section_start = bool(line.startswith("*") and not line.startswith("**"))
        if not line.strip():
            flush()
            continue
        if candidate_start or (section_start and current):
            flush()
        current.append(line)
    flush()

    packed: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= payload_limit:
            current = candidate
            continue
        if current:
            packed.append(current)
        current = block
    if current:
        packed.append(current)

    total = len(packed)
    return [f"\\({index}/{total}\\)\n{part}" for index, part in enumerate(packed, 1)]


def _unlabelled_telegram_parts(text: str) -> list[str]:
    """Reuse the safe packer, then let a combined digest number every part."""
    parts = split_telegram_message(text)
    return [re.sub(r"^\\\\\(\d+/\d+\\\\\)\n", "", part) for part in parts]


def _number_telegram_parts(parts: list[str]) -> list[str]:
    if len(parts) <= 1:
        return parts
    total = len(parts)
    return [f"\\({index}/{total}\\)\n{part}" for index, part in enumerate(parts, 1)]


def _format_candidate_line(
    rank: int,
    ticker: str,
    setup: str,
    price: float | None,
    trigger: float | None,
    structural_invalidation: float | None,
    trade_status: str | None = None,
    verdict: str | None = None,
    short: str | None = None,
) -> str:
    px = f"${price:.2f}" if price is not None else "-"
    trig = f"${trigger:.2f}" if trigger is not None else "-"
    structural = (
        f"${structural_invalidation:.2f}"
        if structural_invalidation is not None
        else "-"
    )
    head = f"*{rank}\\. {_escape_md_v2(ticker)}* `[{_escape_md_v2(setup)}]`"
    body = (
        f"  px {_escape_md_v2(px)} \\| trigger {_escape_md_v2(trig)}"
        f" \\| structural invalidation {_escape_md_v2(structural)}"
    )
    if trade_status:
        body += f" \\| {_escape_md_v2(_trade_status_label(trade_status))}"
    if verdict:
        body += f" \\| _{_escape_md_v2(verdict)}_"
    if short:
        body += "\n  " + _escape_md_v2(short[:200])
    return head + "\n" + body


def _fmt_num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${value:.2f}"


def _pretty_label(value: str | None) -> str:
    if not value:
        return "-"
    return str(value).replace("_", " ").title()


def _trade_status_label(status: str) -> str:
    return {
        "entry_ready": "ulaz spreman",
        "trigger_pending": "čeka trigger",
        "wait_for_retest": "čeka retest",
        "not_tradeable": "nije za ulaz",
        "insufficient_data": "nema dovoljno podataka",
    }.get(status, _pretty_label(status))


def _trade_plan_for(c: Candidate) -> TradePlan:
    """Use the persisted plan or derive it from a legacy detector snapshot."""
    return derive_trade_plan(c.model_dump())


def _regime_icon(state: str | None) -> str:
    if state == "confirmed_uptrend":
        return "🟢"
    if state == "under_pressure":
        return "🟡"
    if state == "correction":
        return "🔴"
    return "⚪"


def _candidate_icon(c: Candidate, ai: RankedCandidate | None = None) -> str:
    verdict = (ai.verdict if ai else "") or ""
    if verdict.startswith("reject") or verdict in {"avoid", "extended"} or c.actionability == "extended_too_late":
        return "🔴"
    if verdict == "actionable" or c.actionability == "actionable_now":
        return "🟢"
    if verdict == "watch" or c.actionability in {"near_actionable", "watch"}:
        return "🟡"
    return "🔵"


def _slope_icon(state: str | None) -> str:
    if state == "upward":
        return "↑"
    if state == "flat":
        return "→"
    if state == "downward":
        return "↓"
    return "-"


def _focus_score(c: Candidate) -> float:
    """Order the focus list by the blend that held out, not by the scorer's own.

    This used to return `focus_score`, which the backtest calls `current_focus`.
    Both were compared on a 2016+ strict holdout - tuned on a training block,
    then scored unchanged on dates they had never seen - and `rs_quality` won:
    +1.96 at 3m and +3.96 at 6m against the same-day universe, where the
    incumbent had no such support.

    `focus_score` is still what the scorer computes and still what gets shown;
    only the ordering moved.
    """
    return candidate_blend_score(c)


def _launch_score(c: Candidate) -> float:
    return max(c.ema_stack_launch_score or 0, c.rwb_squeeze_score or 0, c.long_base_score or 0)


def _accumulation_score(c: Candidate) -> float:
    return max(c.accumulation_score or 0, c.long_base_score or 0, c.institutional_footprint_score or 0, c.crash_base_score or 0)


def _crash_alert_rank(c: Candidate) -> tuple[int, float, int, int]:
    tier = {
        "tier1_trendline_breakout": 3,
        "tier2_daily_rvol_headsup": 2,
        "tier3_low_volume_pullback": 1,
    }.get(c.special_alert_level or "", 0)
    attempts = max(c.resistance_attempt_count or 0, c.trendline_attempt_count or 0)
    return tier, float(c.crash_base_score or 0.0), attempts, int(c.base_age_weeks or 0)


def _is_valid_candidate(c: Candidate) -> bool:
    return c.actionability not in {"excluded", "not_valid"} and c.data_status != "FAILED"


def _setup_phase(c: Candidate, preferred: str = "focus") -> tuple[str, str | None]:
    if preferred == "launch":
        if c.ema_stack_launch_score:
            return "EMA Stack", c.ema_stack_phase
        if c.rwb_squeeze_score:
            return "RWB", c.rwb_squeeze_phase
        if c.long_base_score:
            return "Long Base", c.long_base_phase
    if preferred == "accumulation":
        if c.crash_base_score:
            return "Crash Base", c.crash_base_phase
        if c.long_base_score:
            return "Long Base", c.long_base_phase
        if c.accumulation_score:
            return "Accumulation", c.accumulation_phase
        if c.institutional_footprint_score:
            return "Accumulation", c.accumulation_phase
    if preferred == "crash":
        return "Crash Base", c.crash_base_phase
    if c.ema_stack_launch_score:
        return "EMA Stack", c.ema_stack_phase
    if c.rwb_squeeze_score:
        return "RWB", c.rwb_squeeze_phase
    if c.long_base_score:
        return "Long Base", c.long_base_phase
    if c.crash_base_score:
        return "Crash Base", c.crash_base_phase
    if c.accumulation_score:
        return "Accumulation", c.accumulation_phase
    return c.primary_setup or "-", c.accumulation_phase


def _ranked_lookup(ranked: list[RankedCandidate] | None) -> dict[str, RankedCandidate]:
    return {r.ticker.upper(): r for r in ranked or []}


def _daily_candidate_line(
    rank: int,
    c: Candidate,
    *,
    ranked_by_ticker: dict[str, RankedCandidate],
    metric: str = "focus",
) -> str:
    setup, phase = _setup_phase(c, metric)
    ai = ranked_by_ticker.get(c.ticker.upper())
    score = _focus_score(c) if metric == "focus" else _launch_score(c) if metric == "launch" else (c.crash_base_score or 0) if metric == "crash" else _accumulation_score(c)
    phase_text = _pretty_label(phase)
    icon = _candidate_icon(c, ai)
    trade_plan = _trade_plan_for(c)
    head = (
        f"{rank}\\. {icon} *{_escape_md_v2(c.ticker)}*"
        f"  `{_escape_md_v2(setup)}`"
        f"  {_escape_md_v2(phase_text)}"
    )

    body = "\n".join(
        [
            (
                f"   Score *{_escape_md_v2(_fmt_num(score, 0))}*"
                f" \\| RS {_escape_md_v2(_fmt_num(c.rs_rating, 0))}"
                f" \\| Px {_escape_md_v2(_fmt_price(c.price))}"
            ),
            (
                f"   🎯 {_escape_md_v2(_fmt_price(trade_plan.trigger_reference_level))}"
                f" \\| 🧱 Strukturna invalidacija "
                f"{_escape_md_v2(_fmt_price(trade_plan.structural_invalidation_level))}"
                f" \\| {_escape_md_v2(_trade_status_label(trade_plan.status))}"
            ),
        ]
    )
    details: list[str] = []
    rel_vol = c.current_thrust_rel_volume or c.rwb_thrust_rel_volume
    if rel_vol is not None:
        details.append(f"Vol {_fmt_num(rel_vol, 2)}x")
    width = c.weekly_stack_width_pct or c.weekly_rwb_band_width_pct or c.sma_compression_pct
    if width is not None:
        details.append(f"Width {_fmt_pct(width)}")
    if c.launch_30w_slope_state:
        details.append(f"30W {_slope_icon(c.launch_30w_slope_state)}")
    if c.demand_spike_count is not None:
        details.append(f"Demand {c.demand_spike_count}")
    if metric == "crash":
        if c.special_alert_level:
            details.append(
                {
                    "tier1_trendline_breakout": "Tier 1 Trendline Breakout",
                    "tier2_daily_rvol_headsup": "Tier 2 Daily RVOL Heads-up",
                    "tier3_low_volume_pullback": "Tier 3 Low Pullback",
                }.get(c.special_alert_level, _pretty_label(c.special_alert_level))
            )
        if c.drawdown_5y_pct is not None:
            details.append(f"DD {_fmt_pct(c.drawdown_5y_pct)}")
        if c.base_age_weeks is not None:
            details.append(f"Base {c.base_age_weeks}w")
        attempts = max(c.resistance_attempt_count or 0, c.trendline_attempt_count or 0)
        if attempts:
            details.append(f"Attempts {attempts}")
        if c.weekly_breakout_rvol is not None:
            details.append(f"WVol {_fmt_num(c.weekly_breakout_rvol, 2)}x")
    if details:
        body += "\n   " + _escape_md_v2(" | ".join(details))
    if ai and ai.verdict:
        body += f"\n   🤖 *{_escape_md_v2(_pretty_label(ai.verdict))}*"
        if ai.confidence_level:
            body += f" \\| {_escape_md_v2(_pretty_label(ai.confidence_level))}"
    if ai and ai.short_comment:
        body += "\n   " + _escape_md_v2(ai.short_comment[:190])
    return head + "\n" + body


def _dedupe_pick(
    rows: list[Candidate],
    *,
    limit: int,
    used: set[str],
) -> list[Candidate]:
    out: list[Candidate] = []
    if limit <= 0:
        return out
    for c in rows:
        key = c.ticker.upper()
        if key in used:
            continue
        out.append(c)
        used.add(key)
        if len(out) >= limit:
            break
    return out


def _metadata_summary(metadata: dict[str, Any] | None) -> str:
    if not metadata:
        return ""
    regime = metadata.get("regime") if isinstance(metadata.get("regime"), dict) else {}
    regime_state = regime.get("state") if isinstance(regime, dict) else None
    ai_provider = metadata.get("ai_provider_used") or "-"
    ai_skip = metadata.get("ai_skip_reason")
    coverage = metadata.get("coverage_pct")
    parts = []
    if regime_state:
        parts.append(f"regime {regime_state}")
    if coverage is not None:
        parts.append(f"coverage {_fmt_num(float(coverage), 1)}%")
    parts.append(f"AI {ai_provider}")
    if ai_skip:
        parts.append(f"skip {str(ai_skip)[:80]}")
    return " | ".join(parts)


def _daily_header(as_of: str, candidates: list[Candidate], valid: list[Candidate], metadata: dict[str, Any] | None) -> list[str]:
    regime = metadata.get("regime") if isinstance(metadata, dict) and isinstance(metadata.get("regime"), dict) else {}
    regime_state = regime.get("state") if isinstance(regime, dict) else None
    ai_provider = metadata.get("ai_provider_used") if isinstance(metadata, dict) else None
    ai_skip = metadata.get("ai_skip_reason") if isinstance(metadata, dict) else None
    coverage = metadata.get("coverage_pct") if isinstance(metadata, dict) else None
    md_partial = bool(metadata.get("marketdata_partial_update")) if isinstance(metadata, dict) else False
    md_latest = metadata.get("marketdata_latest_bar") if isinstance(metadata, dict) else None
    md_expected = metadata.get("marketdata_latest_expected") if isinstance(metadata, dict) else None
    md_fresh = metadata.get("marketdata_fresh_eligible_pct") if isinstance(metadata, dict) else None
    md_stale = metadata.get("marketdata_stale_eligible_tickers") if isinstance(metadata, dict) else None
    cloud_sync = metadata.get("cloud_sync_status") if isinstance(metadata, dict) else None
    lines = [
        f"📈 *Stock Scout Daily*  `{_escape_md_v2(as_of)}`",
        "━━━━━━━━━━━━━━━━━━━━",
        (
            f"{_regime_icon(regime_state)} Regime: *{_escape_md_v2(_pretty_label(regime_state))}*"
            f"\n📋 Candidates: *{len(candidates)}* \\| Valid: *{len(valid)}*"
        ),
    ]
    if coverage is not None:
        lines.append(f"📡 Coverage: *{_escape_md_v2(_fmt_pct(float(coverage)))}*")
    if md_latest or md_fresh is not None or md_partial:
        bits = []
        if md_latest:
            bits.append(f"latest {md_latest}")
        if md_expected:
            bits.append(f"expected {md_expected}")
        if md_fresh is not None:
            bits.append(f"fresh {float(md_fresh):.1f}%")
        if md_stale is not None:
            bits.append(f"stale {md_stale}")
        if md_partial:
            reason = metadata.get("marketdata_update_stop_reason") or "partial"
            bits.append(f"partial {reason}")
        lines.append(f"Market data: {_escape_md_v2(' | '.join(str(b) for b in bits))}")
    if ai_provider:
        lines.append(f"🤖 AI: *{_escape_md_v2(str(ai_provider))}*")
    if ai_skip:
        lines.append(f"⚠️ AI skip: {_escape_md_v2(str(ai_skip)[:120])}")
    if cloud_sync == "failed":
        lines.append("WARNING: ChatGPT index was not refreshed; the previous scan remains active")
    elif cloud_sync == "synced":
        lines.append("ChatGPT index: new scan queued for synchronization")
    return lines


def render_daily_digest(
    *,
    as_of: str,
    candidates: list[Candidate],
    ranked: list[RankedCandidate] | None = None,
    metadata: dict[str, Any] | None = None,
    top_focus: int = 8,
    top_launch: int = 4,
    top_accumulation: int = 4,
    report_link: str | None = None,
) -> str:
    """Build a daily operator digest optimized for the private workflow."""
    ranked_by_ticker = _ranked_lookup(ranked)
    valid = [c for c in candidates if _is_valid_candidate(c)]
    # The digest is the surface with the least room to argue on: a handful of
    # tickers on a phone, read once, usually at night. On 2026-07-28 three of
    # the top four by score carried an M&A verdict, so the message led with
    # names that could not move. They come out - but the count stays, because
    # "here are your eight" hiding a ninth is how a filter turns into a lie.
    hidden = [c for c in valid if is_hidden_by_default(getattr(c, "risk_level", None))]
    if hidden:
        valid = [c for c in valid if not is_hidden_by_default(getattr(c, "risk_level", None))]
    # The focus list is the same question the report's headline asks - "the best
    # of tonight" - so it has to be the same answer. The report gained the
    # stage-2 restriction on 2026-08-01 (+3.22 held out against +2.78); without
    # this line the phone would keep recommending names the report no longer
    # leads with, which is the drift `headline_ranking` exists to prevent.
    #
    # If a market leaves fewer than `top_focus` names in stage 2, the list comes
    # back short. That is the message, not a bug: Weinstein's answer to "nothing
    # is advancing" is to buy nothing.
    stage_2_first = headline_ranking(valid, limit=len(valid))
    focus = stage_2_first
    launch = sorted(
        [
            c
            for c in valid
            if (c.ema_stack_launch_score or 0) > 0
            or (c.rwb_squeeze_score or 0) > 0
            or c.primary_setup in {"ema_stack_launch", "rwb_squeeze_thrust"}
        ],
        key=lambda c: (_launch_score(c), _focus_score(c), c.score),
        reverse=True,
    )
    accumulation = sorted(
        [
            c
            for c in valid
            if (c.accumulation_score or 0) > 0
            or (c.long_base_score or 0) > 0
            or (c.crash_base_score or 0) > 0
            or c.primary_setup in {"accumulation_base", "long_base_launch"}
        ],
        key=lambda c: (_accumulation_score(c), _focus_score(c), c.score),
        reverse=True,
    )
    crash = sorted(
        [
            c
            for c in valid
            if (c.crash_base_score or 0) > 0
            or c.primary_setup == "crash_base_stage1"
            or str(c.special_alert_level or "").startswith("tier")
        ],
        key=lambda c: (_crash_alert_rank(c), _focus_score(c), c.score),
        reverse=True,
    )

    used: set[str] = set()
    crash_picks = _dedupe_pick(crash, limit=top_accumulation, used=used)
    focus_picks = _dedupe_pick(focus, limit=top_focus, used=used)
    launch_picks = _dedupe_pick(launch, limit=top_launch, used=used)
    accumulation_picks = _dedupe_pick(accumulation, limit=top_accumulation, used=used)

    lines: list[str] = _daily_header(as_of, candidates, valid, metadata)
    lines.append("")

    if focus_picks:
        lines.append("🎯 *Focus List*")
        for i, c in enumerate(focus_picks, 1):
            lines.append(_daily_candidate_line(i, c, ranked_by_ticker=ranked_by_ticker, metric="focus"))
        lines.append("")

    if launch_picks:
        lines.append("🚀 *Launch / RWB / EMA Stack*")
        for i, c in enumerate(launch_picks, 1):
            lines.append(_daily_candidate_line(i, c, ranked_by_ticker=ranked_by_ticker, metric="launch"))
        lines.append("")

    if crash_picks:
        lines.append("*Crash Base Alerts*")
        for i, c in enumerate(crash_picks, 1):
            lines.append(_daily_candidate_line(i, c, ranked_by_ticker=ranked_by_ticker, metric="crash"))
        lines.append("")

    if accumulation_picks:
        lines.append("🏗️ *Accumulation / Long Base*")
        for i, c in enumerate(accumulation_picks, 1):
            lines.append(_daily_candidate_line(i, c, ranked_by_ticker=ranked_by_ticker, metric="accumulation"))
        lines.append("")

    if not (focus_picks or launch_picks or crash_picks or accumulation_picks):
        lines.append("_No valid candidates in this run_")
        lines.append("")

    if hidden:
        # Named, not just counted. A number alone invites the reader to assume
        # the filter took out something dull; the tickers let them disagree,
        # and they are all in the report and in candidates.json either way.
        shown = ", ".join(sorted(c.ticker for c in hidden)[:12])
        more = f" \\+{len(hidden) - 12} more" if len(hidden) > 12 else ""
        lines.append(
            f"⚠️ Held back \\({len(hidden)}\\): {_escape_md_v2(shown)}{more}"
        )
        lines.append("")

    if report_link:
        lines.append(f"[Open full report]({_escape_md_v2(report_link)})")

    return "\n".join(lines)


def _ma_cluster_thrust_pick(c: Candidate) -> dict[str, Any] | None:
    """Return the strongest daily-or-weekly non-ranking thrust annotation."""
    setup = c.setups.get("ma_cluster_volume_breakout")
    raw = setup.raw_features if setup is not None else {}
    options: list[dict[str, Any]] = []
    for key, timeframe in (
        ("ma_cluster_thrust_daily", "daily"),
        ("ma_cluster_thrust_weekly", "weekly"),
    ):
        assessment = raw.get(key)
        if not isinstance(assessment, dict) or not assessment.get("available"):
            continue
        row = dict(assessment)
        row["timeframe"] = timeframe
        options.append(row)
    if not options:
        return None
    return sorted(
        options,
        key=lambda item: (
            0 if item.get("tier") is not None else 1,
            int(item["tier"]) if item.get("tier") is not None else 99,
            -float(item.get("nearest_score") or 0.0),
        ),
    )[0]


def render_ma_cluster_thrust_digest(
    *,
    as_of: str,
    candidates: list[Candidate],
    limit: int = 10,
    report_link: str | None = None,
) -> str:
    """Render the dedicated daily/weekly tight-MA + relative-volume card.

    It is intentionally independent of headline ordering.  Tiers only label
    proximity to the visual setup; they do not claim a backtested edge.
    """
    rows: list[tuple[Candidate, dict[str, Any]]] = []
    for candidate in candidates:
        assessment = _ma_cluster_thrust_pick(candidate)
        if assessment is not None:
            rows.append((candidate, assessment))

    tiered = [(c, a) for c, a in rows if a.get("tier") is not None]
    selected = tiered if tiered else rows
    selected.sort(
        key=lambda item: (
            int(item[1]["tier"]) if item[1].get("tier") is not None else 99,
            -float(item[1].get("nearest_score") or 0.0),
            -_focus_score(item[0]),
            item[0].ticker,
        )
    )
    selected = selected[:limit]

    lines = [
        f"⚡ *MA Cluster RVOL Thrust*  `{_escape_md_v2(as_of)}`",
        "T1: snop ≤6% | RVOL ≥2x | ≥4/5 MA",
        "T2: snop ≤8% | RVOL ≥1\\.5x | ≥3/5 MA",
        "T3: snop ≤10% | RVOL ≥1\\.25x | ≥3/5 MA",
        "Daily + weekly; heuristički screen, nije potvrđeni backtest edge\\.",
        "",
    ]
    if not tiered and selected:
        lines.append("*Nema Tier 1-3 hitova — prikazani su najbliži kandidati\\.*")
    if not selected:
        lines.append("_Nema dovoljno MA/volumen podataka za ovaj scan\\._")
    for index, (candidate, assessment) in enumerate(selected, 1):
        tier = assessment.get("tier")
        label = f"Tier {tier}" if tier is not None else "Najbliži"
        width = _fmt_pct(assessment.get("ma_width_pct"))
        rvol = assessment.get("relative_volume")
        if rvol is None:
            rvol = candidate.volume_ratio_50d or candidate.rvol_today
        rvol_text = f"{float(rvol):.2f}x" if rvol is not None else "-"
        crossed = f"{int(assessment.get('mas_crossed') or 0)}/{int(assessment.get('mas_total') or 0)}"
        extension = _fmt_pct(assessment.get("extension_above_bundle_pct"))
        trade_plan = _trade_plan_for(candidate)
        lines.append(
            f"*{index}\\. {_escape_md_v2(candidate.ticker)}* "
            f"`[{_escape_md_v2(str(assessment['timeframe']))} · {_escape_md_v2(label)}]`"
        )
        lines.append(
            "  "
            f"snop {_escape_md_v2(width)} | RVOL {_escape_md_v2(rvol_text)} | "
            f"MA {_escape_md_v2(crossed)} | iznad snopa {_escape_md_v2(extension)} | "
            f"{_escape_md_v2(_trade_status_label(trade_plan.status))}"
        )
    if report_link:
        lines.extend(["", f"[Open full report]({_escape_md_v2(report_link)})"])
    return "\n".join(lines)


def render_preferred_ma_digest(
    *,
    as_of: str,
    candidates: list[Candidate],
    limit: int = 10,
    report_link: str | None = None,
) -> str:
    """Render the experimental preference-fit top ten as its own card."""
    rows = [
        candidate
        for candidate in candidates
        if candidate.ma_cluster_research is not None
        and candidate.ma_cluster_research.coverage == 6
        and candidate.ma_cluster_research.points is not None
        and candidate.ma_cluster_research.score is not None
    ]
    # Python's stable sort retains scan order for equal point totals.
    rows.sort(key=lambda candidate: -int(candidate.ma_cluster_research.points or 0))
    rows = rows[:limit]
    lines = [
        f"*{_escape_md_v2('Preferred Breakout Candidates - Research v1')}*  `{_escape_md_v2(as_of)}`",
        "Experimental preference fit; not a win probability, confirmed edge or sizing input\\.",
        "",
    ]
    if not rows:
        lines.append("_Research score unavailable for this scan\\._")
    for index, candidate in enumerate(rows, 1):
        profile = candidate.ma_cluster_research
        assert profile is not None
        metrics = profile.metrics
        assessment = _ma_cluster_thrust_pick(candidate) or {}
        tier = assessment.get("tier")
        tier_text = f"Tier {tier}" if tier is not None else "nearest"
        archetype = (profile.archetype or "balanced").replace("_", " / ").title()
        risk = _fmt_pct(metrics.get("pattern_risk_pct"))
        width = _fmt_pct(metrics.get("ma_width_pct"))
        high_distance = _fmt_pct(metrics.get("distance_to_prior_52w_high_pct"))
        range_atr = metrics.get("signal_range_atr20")
        range_text = f"{float(range_atr):.2f}" if range_atr is not None else "-"
        rvol = assessment.get("relative_volume")
        rvol_text = f"{float(rvol):.2f}x" if rvol is not None else "-"
        trade_plan = _trade_plan_for(candidate)
        lines.append(
            f"*{index}\\. {_escape_md_v2(candidate.ticker)}* "
            f"`[{_escape_md_v2(str(profile.timeframe))} | {_escape_md_v2(tier_text)}]` "
            f"*{profile.score:.1f}* `({_escape_md_v2(str(profile.points))}/6)`"
        )
        lines.append(
            "  "
            f"{_escape_md_v2(archetype)} | risk {_escape_md_v2(risk)} | "
            f"range/ATR {_escape_md_v2(range_text)} | snop {_escape_md_v2(width)}"
        )
        lines.append(
            "  "
            f"RVOL {_escape_md_v2(rvol_text)} | prior high {_escape_md_v2(high_distance)} | "
            f"{_escape_md_v2(_trade_status_label(trade_plan.status))}"
        )
        if trade_plan.status == "entry_ready" and trade_plan.tactical_stop_level is not None:
            lines.append(
                f"  Tactical stop `{_escape_md_v2(_fmt_price(trade_plan.tactical_stop_level))}`"
            )
        else:
            lines.append(f"  _{_escape_md_v2('Sizing disabled - tactical stop unavailable.')} _".replace(" _", "_"))
    if report_link:
        lines.extend(["", f"[Open full report]({_escape_md_v2(report_link)})"])
    return "\n".join(lines)


def render_digest(
    *,
    as_of: str,
    candidates: list[Candidate],
    ranked: list[RankedCandidate] | None = None,
    top_actionable: int = 5,
    top_watch: int = 5,
    report_link: str | None = None,
) -> str:
    """Build the Markdown message body.

    If `ranked` is provided, prefer it for ordering and verdicts.
    Otherwise rank by `Candidate.score` descending.
    """
    lines: list[str] = []
    title = f"📈 *Stock Scout digest* — {_escape_md_v2(as_of)}"
    lines.append(title)
    lines.append(f"_{_escape_md_v2(len(candidates))} candidates from screener_")
    lines.append("")

    if ranked:
        # Use ranker verdicts when available
        actionable = [r for r in ranked if r.verdict == "actionable"][:top_actionable]
        watch_set = [r for r in ranked if r.verdict in ("watch", "actionable")]
        # exclude already-shown actionable from the watch block
        actionable_ts = {r.ticker.upper() for r in actionable}
        watch = [r for r in watch_set if r.ticker.upper() not in actionable_ts][:top_watch]
        cand_lookup = {c.ticker.upper(): c for c in candidates}

        def _line_from_ranked(r: RankedCandidate, i: int) -> str:
            c = cand_lookup.get(r.ticker.upper())
            trade_plan = _trade_plan_for(c) if c else None
            return _format_candidate_line(
                rank=i,
                ticker=r.ticker,
                setup=r.setup_type,
                price=c.price if c else None,
                trigger=trade_plan.trigger_reference_level if trade_plan else None,
                structural_invalidation=(
                    trade_plan.structural_invalidation_level if trade_plan else None
                ),
                trade_status=trade_plan.status if trade_plan else None,
                verdict=r.verdict,
                short=r.short_comment,
            )

        if actionable:
            lines.append("*🚀 Actionable now*")
            for i, r in enumerate(actionable, 1):
                lines.append(_line_from_ranked(r, i))
            lines.append("")
        if watch:
            lines.append("*👀 Watch / near\\-actionable*")
            for i, r in enumerate(watch, 1):
                lines.append(_line_from_ranked(r, i))
            lines.append("")
        # Footer: rejects summary
        rejects = [r for r in ranked if r.verdict.startswith("reject_")]
        if rejects:
            lines.append(f"_{_escape_md_v2(len(rejects))} candidates rejected by AI judge_")
            lines.append("")
    else:
        # Deterministic-only: top by score
        top = sorted(candidates, key=lambda c: c.score, reverse=True)[: top_actionable + top_watch]
        lines.append("*🚀 Top deterministic candidates*")
        for i, c in enumerate(top, 1):
            setup = c.primary_setup or "-"
            trade_plan = _trade_plan_for(c)
            lines.append(
                _format_candidate_line(
                    rank=i,
                    ticker=c.ticker,
                    setup=setup,
                    price=c.price,
                    trigger=trade_plan.trigger_reference_level,
                    structural_invalidation=trade_plan.structural_invalidation_level,
                    trade_status=trade_plan.status,
                )
            )
        lines.append("")

    if report_link:
        lines.append(f"[Open full report]({_escape_md_v2(report_link)})")

    return "\n".join(lines)


def render_returns_leaderboard(as_of: str, window: str, rows: list[dict]) -> str:
    """Render a plain-text cross-asset returns snapshot (parse_mode="").

    Groups rows by asset class (in canonical order) and lists each asset with a
    signed percentage. `rows` are the leaderboard entries for one window.
    """
    from stock_scout.data.macro_universe import ASSET_CLASS_ORDER

    title_window = {
        "1W": "Week",
        "1M": "Month",
        "3M": "3 Months",
        "YTD": "YTD",
        "1Y": "Year",
    }.get(window, window)

    lines = [f"📊 Returns Leaderboard · {title_window} · {as_of}", ""]

    if not rows:
        lines.append("No data available.")
        return "\n".join(lines)

    by_class: dict[str, list[dict]] = {}
    for r in rows:
        by_class.setdefault(r.get("asset_class", "Other"), []).append(r)

    ordered_classes = [c for c in ASSET_CLASS_ORDER if c in by_class]
    ordered_classes += [c for c in by_class if c not in ASSET_CLASS_ORDER]

    for cls in ordered_classes:
        lines.append(f"— {cls} —")
        items = sorted(by_class[cls], key=lambda x: x.get("return_pct", 0.0), reverse=True)
        for r in items:
            pct = r.get("return_pct", 0.0)
            arrow = "🟢" if pct >= 0 else "🔴"
            sign = "+" if pct >= 0 else ""
            lines.append(f"{arrow} {r.get('label', r.get('ticker', '?'))}: {sign}{pct:.1f}%")
        lines.append("")

    return "\n".join(lines).rstrip()


def send_message_parts(
    cfg: TelegramConfig,
    parts: list[str],
    *,
    start_part: int = 0,
    on_part_sent: Any | None = None,
) -> bool:
    """Send ordered parts with bounded retry and optional durable progress."""
    global LAST_SEND_ERROR
    LAST_SEND_ERROR = None
    if sends_suppressed():
        # False, not True: nothing was sent, and the callers' dedupe ledgers
        # must not record it as delivered or the real run would skip it.
        LAST_SEND_ERROR = "Suppressed by --no-notify"
        log.info("telegram.send_suppressed", chars=sum(len(part) for part in parts))
        return False
    if not cfg.bot_token or not cfg.chat_id:
        LAST_SEND_ERROR = "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
        log.warning("telegram.missing_credentials")
        return False
    url = TELEGRAM_API.format(token=cfg.bot_token)
    for part_index, text in enumerate(parts[start_part:], start=start_part):
        if len(text) > 4096:
            LAST_SEND_ERROR = f"Telegram part {part_index + 1} exceeds 4096 characters"
            return False
        payload = {
            "chat_id": cfg.chat_id,
            "text": text,
            "parse_mode": cfg.parse_mode,
            "disable_web_page_preview": cfg.disable_web_page_preview,
        }
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, timeout=15)
            except Exception as exc:
                LAST_SEND_ERROR = str(exc)
                if attempt == 2:
                    log.warning("telegram.send_exception", error=str(exc), part=part_index + 1)
                    return False
                time.sleep(1 + attempt)
                continue
            if response.status_code == 200:
                if on_part_sent is not None:
                    on_part_sent(part_index + 1, len(parts))
                break
            LAST_SEND_ERROR = response.text[:1000]
            if response.status_code == 429 and attempt < 2:
                try:
                    retry_after = float(response.json().get("parameters", {}).get("retry_after", 1))
                except (TypeError, ValueError, requests.RequestException):
                    retry_after = 1.0
                time.sleep(max(1.0, min(retry_after, 30.0)))
                continue
            log.warning(
                "telegram.send_failed",
                status=response.status_code,
                body=response.text[:300],
                part=part_index + 1,
            )
            return False
        else:  # pragma: no cover - defensive; every branch breaks or returns
            return False
    return True


def send_message(cfg: TelegramConfig, text: str) -> bool:
    """POST all Telegram-safe parts. Returns True only after every part succeeds."""
    return send_message_parts(cfg, split_telegram_message(text))


def build_daily_digest_parts(
    *,
    as_of: str,
    candidates: list[Candidate],
    ranked: list[RankedCandidate] | None = None,
    metadata: dict[str, Any] | None = None,
    report_link: str | None = None,
    top_focus: int = 8,
    top_launch: int = 4,
    top_accumulation: int = 4,
) -> list[str]:
    """Render the complete daily delivery before any network side effect."""
    msg = render_daily_digest(
        as_of=as_of,
        candidates=candidates,
        ranked=ranked,
        metadata=metadata,
        report_link=report_link,
        top_focus=top_focus,
        top_launch=top_launch,
        top_accumulation=top_accumulation,
    )
    thrust_msg = render_ma_cluster_thrust_digest(
        as_of=as_of,
        candidates=candidates,
        report_link=report_link,
    )
    preferred_msg = render_preferred_ma_digest(
        as_of=as_of,
        candidates=candidates,
        report_link=report_link,
    )
    return _number_telegram_parts(
        _unlabelled_telegram_parts(msg)
        + _unlabelled_telegram_parts(thrust_msg)
        + _unlabelled_telegram_parts(preferred_msg)
    )


def send_digest(
    cfg: TelegramConfig,
    *,
    as_of: str,
    candidates: list[Candidate],
    ranked: list[RankedCandidate] | None = None,
    metadata: dict[str, Any] | None = None,
    digest: str = "legacy",
    report_link: str | None = None,
    top_actionable: int = 5,
    top_watch: int = 5,
    top_focus: int = 8,
    top_launch: int = 4,
    top_accumulation: int = 4,
) -> bool:
    if digest == "daily":
        parts = build_daily_digest_parts(
            as_of=as_of,
            candidates=candidates,
            ranked=ranked,
            metadata=metadata,
            report_link=report_link,
            top_focus=top_focus,
            top_launch=top_launch,
            top_accumulation=top_accumulation,
        )
    else:
        msg = render_digest(
            as_of=as_of,
            candidates=candidates,
            ranked=ranked,
            report_link=report_link,
            top_actionable=top_actionable,
            top_watch=top_watch,
        )
        parts = split_telegram_message(msg)
    return send_message_parts(cfg, parts)


def telegram_config_from_env(env) -> TelegramConfig | None:
    """Build a TelegramConfig from an `Env` settings object, or None if
    credentials are absent."""
    token = getattr(env, "TELEGRAM_BOT_TOKEN", "") or ""
    chat = getattr(env, "TELEGRAM_CHAT_ID", "") or ""
    if not token or not chat:
        return None
    return TelegramConfig(bot_token=token, chat_id=chat)


def load_report(report_dir: Path) -> tuple[str, list[Candidate], list[RankedCandidate] | None]:
    """Load a previously-generated report folder for re-sending.

    Returns (as_of_date, candidates, ranked_or_None).
    """
    import json

    from stock_scout.research.sidecar import merge_preferred_profiles
    from stock_scout.scoring.models import Candidate as _Candidate

    as_of = report_dir.name
    cand_path = report_dir / "candidates.json"
    rank_path = report_dir / "ranked.json"
    candidates_raw = json.loads(cand_path.read_text(encoding="utf-8")) if cand_path.exists() else []
    if isinstance(candidates_raw, list):
        candidates_raw = merge_preferred_profiles(candidates_raw, report_dir)
    candidates = [_Candidate.model_validate(d) for d in candidates_raw]
    ranked: list[RankedCandidate] | None = None
    if rank_path.exists():
        rank_raw = json.loads(rank_path.read_text(encoding="utf-8"))
        ranked = [RankedCandidate.model_validate(d) for d in rank_raw]
    return as_of, candidates, ranked


def load_report_metadata(report_dir: Path) -> dict[str, Any]:
    import json

    meta_path = report_dir / "run_metadata.json"
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))
