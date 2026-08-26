from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from stock_scout.ranker.io_schema import RankedCandidate
from stock_scout.scoring.focus_blend import candidate_blend_score, headline_ranking, stage_note
from stock_scout.scoring.models import Candidate, TradePlan
from stock_scout.scoring.risk import is_hidden_by_default
from stock_scout.scoring.trade_plan import derive_trade_plan

DISCLAIMER = (
    "**Disclaimer**: This is research/watchlist tooling for technical analysis. "
    "Nothing here is financial advice. Do your own due diligence. Trade at your own risk."
)


def _fmt_pct(x: float | None) -> str:
    return f"{x:+.1f}%" if x is not None else "n/a"


def _fmt_price(x: float | None) -> str:
    return f"${x:,.2f}" if x is not None else "n/a"


def _fmt_dv(x: float | None) -> str:
    if x is None:
        return "n/a"
    if x >= 1_000_000_000:
        return f"${x/1_000_000_000:.1f}B"
    if x >= 1_000_000:
        return f"${x/1_000_000:.1f}M"
    return f"${x:,.0f}"


def _trade_plan(c: Candidate) -> TradePlan:
    return derive_trade_plan(c.model_dump())


def _setup_breakdown(c: Candidate) -> str:
    parts = []
    for name, s in sorted(c.setups.items()):
        if s.triggered:
            parts.append(f"**{name}** ({s.sub_state or 'triggered'}, {s.score:.0f})")
    return ", ".join(parts) or "—"


def _ranked_lookup(ranked: list[RankedCandidate]) -> dict[str, RankedCandidate]:
    return {r.ticker: r for r in ranked}


def render_markdown_report(
    candidates: list[Candidate],
    ranked: list[RankedCandidate],
    stats: dict,
    as_of: str,
    top_n: int = 20,
    excluded: list[Candidate] | None = None,
) -> str:
    """Render the daily watchlist report as Markdown text."""
    lines: list[str] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines.append(f"# US Stock Scout — Daily Watchlist · {as_of}")
    lines.append("")
    lines.append(f"_Generated {now_iso} UTC_")
    lines.append("")
    lines.append(DISCLAIMER)
    lines.append("")
    lines.append("## Run summary")
    lines.append("")
    lines.append(f"- Primary data provider: `{stats.get('primary_provider')}`")
    if stats.get("secondary_provider"):
        lines.append(f"- Validation provider: `{stats.get('secondary_provider')}`")
    if stats.get("fallback_provider"):
        lines.append(f"- Fallback provider: `{stats.get('fallback_provider')}`")
    lines.append(f"- Universe size: {stats.get('universe_size')}")
    lines.append(f"- Passed prefilter & ≥1 setup triggered: {stats.get('prefilter_passed')}")
    lines.append(f"- Failed prefilter: {stats.get('prefilter_failed')}")
    if stats.get("fallback_used"):
        lines.append(f"- Tickers needing fallback: {stats.get('fallback_used')}")
    if stats.get("marketdata_latest_bar") or stats.get("marketdata_fresh_eligible_pct") is not None:
        md_bits = []
        if stats.get("marketdata_latest_bar"):
            md_bits.append(f"latest `{stats.get('marketdata_latest_bar')}`")
        if stats.get("marketdata_latest_expected"):
            md_bits.append(f"expected `{stats.get('marketdata_latest_expected')}`")
        if stats.get("marketdata_fresh_eligible_pct") is not None:
            md_bits.append(f"fresh eligible {stats.get('marketdata_fresh_eligible_pct')}%")
        if stats.get("marketdata_stale_eligible_tickers") is not None:
            md_bits.append(f"stale eligible {stats.get('marketdata_stale_eligible_tickers')}")
        if stats.get("marketdata_partial_update"):
            md_bits.append(f"partial update `{stats.get('marketdata_update_stop_reason') or 'yes'}`")
        lines.append("- Market data: " + ", ".join(md_bits))
    lines.append("- Setups triggered:")
    for setup_name, count in (stats.get("setup_triggered_counts") or {}).items():
        lines.append(f"  - `{setup_name}`: {count}")
    if stats.get("timings_seconds"):
        timings = stats["timings_seconds"]
        lines.append("- Timings (s): " + ", ".join(f"`{k}`={v}" for k, v in timings.items()))
    if stats.get("ai_model"):
        ai_bits = [str(stats.get("ai_model"))]
        if stats.get("ai_provider_used"):
            ai_bits.insert(0, str(stats.get("ai_provider_used")))
        if stats.get("ai_elapsed_sec") is not None:
            ai_bits.append(f"{stats.get('ai_elapsed_sec')}s")
        if stats.get("ai_candidates_reviewed"):
            ai_bits.append(f"{stats.get('ai_candidates_reviewed')} reviewed")
        cost = stats.get("ai_cost_usd")
        if cost:
            ai_bits.append(f"~${cost:.4f}")
        lines.append("- AI ranker: " + " / ".join(ai_bits))
    if stats.get("ai_skip_reason"):
        lines.append(f"- AI skip reason: `{stats.get('ai_skip_reason')}`")
    lines.append("")

    # AI-ranked top picks — only show entries the model actually ranked (non-null)
    ranked_only = [r for r in ranked if r.overall_rank is not None]
    ranked_only.sort(key=lambda x: x.overall_rank or 0)
    if ranked_only:
        lines.append(f"## Top {min(top_n, len(ranked_only))} AI-ranked picks")
        lines.append("")
        lines.append(
            "| Rank | Ticker | Setup | Verdict | Score | Entry | Confidence | Trigger | AI invalidation (not sizing) | Comment |"
        )
        lines.append("|---:|:---|:---|:---|---:|:---|:---|---:|---:|:---|")
        for r in ranked_only[:top_n]:
            escaped_comment = r.short_comment.replace("|", "\\|")
            lines.append(
                f"| {r.overall_rank} | **{r.ticker}** | {r.setup_type} | {r.verdict} | {r.score:.0f} | "
                f"{r.ideal_entry_style} | {r.confidence_level} | "
                f"{_fmt_price(r.trigger_level)} | {_fmt_price(r.invalidation_level)} | "
                f"{escaped_comment} |"
            )
        lines.append("")
        # Show the rejects so the user sees what the model culled
        rejected = [
            r
            for r in ranked
            if r.verdict.startswith("reject_")
            or r.verdict in {"avoid", "extended", "insufficient_data"}
        ]
        if rejected:
            lines.append(f"### AI rejected ({len(rejected)})")
            lines.append("")
            lines.append("| Ticker | Setup | Verdict | Comment |")
            lines.append("|:---|:---|:---|:---|")
            for r in rejected:
                escaped = r.short_comment.replace("|", "\\|")
                lines.append(f"| {r.ticker} | {r.setup_type} | {r.verdict} | {escaped} |")
            lines.append("")

    # Names carrying an M&A or data-quality verdict come out of every ranking
    # and bucket below, and get their own section at the end instead. On
    # 2026-07-28 three of the top four by score were agreed takeovers, so the
    # report opened with dead money. `candidates.json` still has every row -
    # this is presentation, and tonight's decision has to stay auditable
    # tomorrow. The definition lives in `scoring/risk.py` so this cannot drift
    # from what the screen and the digest do.
    held_back = [c for c in candidates if is_hidden_by_default(getattr(c, "risk_level", None))]
    shown = (
        [c for c in candidates if not is_hidden_by_default(getattr(c, "risk_level", None))]
        if held_back
        else candidates
    )

    # --- Bucket sections (Faza 5) -----------------------------------------
    actionable_now = [c for c in shown if c.actionability == "actionable_now"]
    near = [c for c in shown if c.actionability == "near_actionable"]
    watch = [c for c in shown if c.actionability == "watch"]

    def _bucket_table(rows: list[Candidate], header: str, cap: int) -> None:
        if not rows:
            return
        lines.append(header)
        lines.append("")
        lines.append(
            "| # | Ticker | Setup | Sub-state | Score | Close | Trigger | Structural invalidation | d52w | RS 6m | $Vol(50d) | Reason |"
        )
        lines.append("|---:|:---|:---|:---|---:|---:|---:|---:|---:|---:|---:|:---|")
        for i, c in enumerate(rows[:cap], start=1):
            primary = c.setups.get(c.primary_setup) if c.primary_setup else None
            trade_plan = _trade_plan(c)
            sub = primary.sub_state if primary else "—"
            reason = c.actionability_reason or "—"
            lines.append(
                f"| {i} | **{c.ticker}** | {c.primary_setup or '—'} | {sub} | {c.score:.1f} | "
                f"{_fmt_price(c.price)} | {_fmt_price(trade_plan.trigger_reference_level)} | "
                f"{_fmt_price(trade_plan.structural_invalidation_level)} | {_fmt_pct(c.distance_to_52w_high_pct)} | "
                f"{_fmt_pct(c.rs_score_6m)} | {_fmt_dv(c.avg_dollar_volume_50d)} | "
                f"{reason} |"
            )
        lines.append("")

    # The headline used to be `actionable_now`. It measures -0.28 excess against
    # the same-day universe in both time blocks, so it was leading with a state
    # rather than an edge. The `rs_quality` blend is the one ranking in this
    # project that was tuned on a training block and then held up, unchanged, on
    # dates it had never seen - +1.96 at 3m and +3.96 at 6m on a 2016+ strict
    # holdout. It leads now. The buckets keep their sections, one rung down.
    ranked = headline_ranking(shown, limit=10)
    if ranked:
        lines.append("## Top 10 by measured edge (`rs_quality`, stage 2)")
        lines.append("")
        lines.append(
            "_Held out on 2016+ dates the weights never saw: **+1.85 at 3m, +4.59 at 6m** "
            "against the same-day universe, with the stage-2 restriction added 2026-08-01 "
            "(+3.22 against +2.78 unrestricted). A 5,720-cell grid picked a cell scoring "
            "+6.78 in sample and only +3.17 out; this rule scores +1.30 in sample and "
            "beats it. The buckets below are grouping, not ranking._"
        )
        lines.append("")
        lines.append(
            "| # | Ticker | Setup | Stage | Blend | Score | RS | Close | Trigger | Structural invalidation | Note |"
        )
        lines.append("|---:|:---|:---|:---|---:|---:|---:|---:|---:|---:|:---|")
        for i, c in enumerate(ranked, start=1):
            note = stage_note(c.weinstein_substage)
            trade_plan = _trade_plan(c)
            marker = {"favoured": "**favoured**", "avoid": "avoid"}.get(note, "—")
            lines.append(
                f"| {i} | **{c.ticker}** | {c.primary_setup or '—'} | "
                f"{c.weinstein_substage or '—'} | {candidate_blend_score(c):.1f} | "
                f"{c.score:.1f} | {_fmt_pct(c.rs_score_6m)} | {_fmt_price(c.price)} | "
                f"{_fmt_price(trade_plan.trigger_reference_level)} | "
                f"{_fmt_price(trade_plan.structural_invalidation_level)} | "
                f"{marker} |"
            )
        lines.append("")

    _bucket_table(actionable_now, f"## Actionable now ({len(actionable_now)})", top_n)
    _bucket_table(near, f"## Near actionable ({len(near)})", top_n)
    _bucket_table(watch, f"## Watch for breakout ({len(watch)})", top_n)

    # Per-setup breakdowns
    by_setup: dict[str, list[Candidate]] = {}
    for c in candidates:
        for name, s in c.setups.items():
            if s.triggered:
                by_setup.setdefault(name, []).append(c)
    for setup_name in ["glb", "minervini", "tight_breakout", "weinstein", "high_rs", "ema_cross", "guppy", "rwb_squeeze_thrust", "ema_stack_launch", "accumulation_base", "long_base_launch"]:
        bucket = sorted(
            (c for c in by_setup.get(setup_name, [])),
            key=lambda c: c.setups[setup_name].score,
            reverse=True,
        )[:10]
        if not bucket:
            continue
        lines.append(f"### Top by `{setup_name}`")
        lines.append("")
        lines.append("| Ticker | Sub-state | Setup score | Overall | Trigger | Structural invalidation |")
        lines.append("|:---|:---|---:|---:|---:|---:|")
        for c in bucket:
            s = c.setups[setup_name]
            lines.append(
                f"| **{c.ticker}** | {s.sub_state or '—'} | {s.score:.1f} | "
                f"{c.score:.1f} | {_fmt_price(s.trigger_level)} | "
                f"{_fmt_price(s.invalidation_level)} |"
            )
        lines.append("")

    # --- Excluded section (Faza 5) ----------------------------------------
    if excluded:
        lines.append(f"## Excluded — false positives & extended ({len(excluded)})")
        lines.append("")
        lines.append("Candidates filtered out from the main list. Includes M&A flat patterns, ")
        lines.append("late-stage runners, illiquid-tight setups, and wide-and-loose bases.")
        lines.append("")
        # Group by *category* (first token of reason) for readability. Keeps
        # M&A, illiquid_tight, wide_loose, stage_2_extended etc. as buckets
        # instead of one-row groups per extension percentage.
        def _category(c: Candidate) -> str:
            r = (c.excluded_reason or c.actionability or "other").lower()
            # strip "_ext_NN.N%" suffix so all stage_2_extended_ext_72.2 / _29.6 collapse
            for sep in ("_ext_", " ext "):
                if sep in r:
                    r = r.split(sep)[0]
                    break
            return r

        by_reason: dict[str, list[Candidate]] = {}
        for c in excluded:
            by_reason.setdefault(_category(c), []).append(c)
        for reason, rows in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"### {reason} ({len(rows)})")
            lines.append("")
            lines.append("| Ticker | Setup | Score | Close | d52w | Reason detail |")
            lines.append("|:---|:---|---:|---:|---:|:---|")
            for c in rows[:15]:
                lines.append(
                    f"| {c.ticker} | {c.primary_setup or '—'} | {c.score:.1f} | "
                    f"{_fmt_price(c.price)} | {_fmt_pct(c.distance_to_52w_high_pct)} | "
                    f"{c.actionability_reason or '—'} |"
                )
            if len(rows) > 15:
                lines.append(f"| _... +{len(rows)-15} more_ | | | | | |")
            lines.append("")

    if held_back:
        lines.append(f"## Held back ({len(held_back)})")
        lines.append("")
        lines.append(
            "_Kept out of the rankings and buckets above, not out of the run. Every "
            "one of these is in `candidates.json` with its verdict, so this page can "
            "be checked rather than trusted._"
        )
        lines.append("")
        lines.append("| Ticker | Setup | Score | Close | Level | Why |")
        lines.append("|:---|:---|---:|---:|:---|:---|")
        for c in sorted(held_back, key=lambda x: -x.score):
            reasons = getattr(c, "risk_reasons", None) or []
            why = "; ".join(str(r.get("label") or r.get("code")) for r in reasons) or "—"
            lines.append(
                f"| **{c.ticker}** | {c.primary_setup or '—'} | {c.score:.1f} | "
                f"{_fmt_price(c.price)} | {getattr(c, 'risk_level', '—')} | {why} |"
            )
        lines.append("")

    # Data quality section
    mismatch = [c for c in candidates if c.data_status == "MISMATCH"]
    warning = [c for c in candidates if c.data_status == "WARNING"]
    insufficient = [c for c in candidates if c.data_status == "INSUFFICIENT_DATA"]
    if mismatch or warning or insufficient:
        lines.append("## Data quality warnings")
        lines.append("")
        for c in mismatch:
            lines.append(f"- **MISMATCH** `{c.ticker}`: " + "; ".join(f.code for f in c.flags))
        for c in warning:
            lines.append(f"- WARNING `{c.ticker}`: " + "; ".join(f.code for f in c.flags))
        for c in insufficient:
            lines.append(f"- INSUFFICIENT_DATA `{c.ticker}`")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(DISCLAIMER)
    lines.append("")
    return "\n".join(lines)


def write_markdown_report(path: str | Path, content: str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p
