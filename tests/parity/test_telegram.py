from __future__ import annotations

from stock_scout.notifications.telegram import (
    TelegramConfig,
    _escape_md_v2,
    render_daily_digest,
    render_digest,
    render_ma_cluster_thrust_digest,
    render_preferred_ma_digest,
    send_digest,
    send_message_parts,
    split_telegram_message,
    suppress_sends,
    telegram_config_from_env,
)
from stock_scout.ranker.io_schema import RankedCandidate
from stock_scout.scoring.models import Candidate


def _cand(ticker: str, score: float = 80.0, price: float = 100.0, **kwargs) -> Candidate:
    data = {
        "ticker": ticker,
        "as_of": "2026-05-18",
        "price": price,
        "score": score,
        "primary_setup": "minervini",
        "trigger_level": price + 1,
        "invalidation_level": price - 5,
        **kwargs,
    }
    data.setdefault("atr20", 2.0)
    data.setdefault(
        "setups",
        {
            data["primary_setup"]: {
                "setup_name": data["primary_setup"],
                "triggered": True,
                "trigger_level": price + 1,
                "invalidation_level": price - 5,
            }
        },
    )
    return Candidate(**data)


def _ranked(ticker: str, rank: int, verdict: str, score: float = 80.0) -> RankedCandidate:
    return RankedCandidate(
        ticker=ticker,
        setup_type="Minervini",
        overall_rank=rank,
        score=score,
        ideal_entry_style="breakout",
        confidence_level="high",
        short_comment="strong vcp",
        verdict=verdict,  # type: ignore[arg-type]
        trigger_level=101.0,
        invalidation_level=95.0,
    )


def test_escape_md_v2_quotes_specials():
    assert _escape_md_v2("hello.world") == "hello\\.world"
    assert _escape_md_v2("price=$100") == "price\\=$100"
    assert _escape_md_v2("(test)") == "\\(test\\)"


def test_render_digest_with_ranker_uses_verdicts():
    cands = [_cand("AAPL"), _cand("NVDA"), _cand("MSFT"), _cand("BADCO")]
    ranked = [
        _ranked("AAPL", 1, "actionable"),
        _ranked("NVDA", 2, "actionable"),
        _ranked("MSFT", 3, "watch"),
        _ranked("BADCO", 99, "reject_false_positive"),
    ]
    msg = render_digest(as_of="2026-05-18", candidates=cands, ranked=ranked)
    assert "Actionable now" in msg
    assert "Watch" in msg
    assert "AAPL" in msg
    assert "MSFT" in msg
    # Rejects are summarized in footer, not listed as picks
    assert "BADCO" not in msg or "rejected" in msg


def test_render_digest_without_ranker_uses_score():
    cands = [_cand("LOW", 50), _cand("HIGH", 95), _cand("MID", 70)]
    msg = render_digest(as_of="2026-05-18", candidates=cands)
    # HIGH (95) should appear before MID (70) and LOW (50)
    assert msg.find("HIGH") < msg.find("MID") < msg.find("LOW")


def test_render_digest_preserves_and_splits_long_message():
    cands = [_cand(f"TICK{i:03}", 80) for i in range(200)]
    ranked = [_ranked(f"TICK{i:03}", i + 1, "actionable") for i in range(200)]
    msg = render_digest(
        as_of="2026-05-18",
        candidates=cands,
        ranked=ranked,
        top_actionable=200,
        top_watch=0,
    )
    parts = split_telegram_message(msg)
    assert len(msg) > 4000
    assert len(parts) > 1
    assert all(len(part) <= 3900 for part in parts)
    assert "TICK199" in "\n".join(parts)
    assert "truncated" not in msg.lower()


def test_render_daily_digest_groups_focus_launch_and_accumulation():
    cands = [
        _cand("FOCUS", 95, focus_score=98, rs_rating=91),
        _cand(
            "AEVA",
            82,
            focus_score=88,
            primary_setup="ema_stack_launch",
            ema_stack_launch_score=92,
            ema_stack_phase="stack_thrust",
            current_thrust_rel_volume=2.4,
            weekly_stack_width_pct=4.2,
            launch_30w_slope_state="flat",
            rs_rating=76,
        ),
        _cand(
            "NOK",
            78,
            focus_score=84,
            primary_setup="long_base_launch",
            accumulation_score=81,
            long_base_score=87,
            long_base_phase="launching",
            demand_spike_count=4,
            rs_rating=72,
        ),
    ]
    ranked = [_ranked("AEVA", 1, "actionable")]
    msg = render_daily_digest(
        as_of="2026-05-18",
        candidates=cands,
        ranked=ranked,
        metadata={
            "regime": {"state": "confirmed_uptrend"},
            "ai_provider_used": "groq",
            "coverage_pct": 98.5,
        },
        top_focus=1,
        top_launch=1,
        top_accumulation=1,
    )
    assert "Stock Scout Daily" in msg
    assert "🎯 *Focus List*" in msg
    assert "🚀 *Launch / RWB / EMA Stack*" in msg
    assert "🏗️ *Accumulation / Long Base*" in msg
    assert "FOCUS" in msg
    assert "AEVA" in msg
    assert "NOK" in msg
    assert "Confirmed Uptrend" in msg
    assert "groq" in msg
    assert "🎯" in msg
    assert "🧱 Strukturna invalidacija" in msg
    assert "🛑" not in msg


def test_digest_never_labels_a_structural_level_as_a_trade_stop():
    msg = render_digest(as_of="2026-05-18", candidates=[_cand("STRUCT")])

    assert "structural invalidation" in msg
    assert "stop" not in msg.lower()
    assert "čeka trigger" in msg


def test_render_daily_digest_includes_crash_base_alerts():
    cands = [
        _cand(
            "RNG",
            72,
            focus_score=74,
            primary_setup="crash_base_stage1",
            crash_base_score=78,
            crash_base_phase="trendline_breakout",
            drawdown_5y_pct=94.0,
            base_age_weeks=118,
            resistance_attempt_count=4,
            trendline_attempt_count=3,
            trendline_breakout_5y=True,
            weekly_breakout_rvol=2.1,
            special_alert_level="tier1_trendline_breakout",
        )
    ]
    msg = render_daily_digest(
        as_of="2026-05-18",
        candidates=cands,
        top_focus=0,
        top_launch=0,
        top_accumulation=3,
    )
    assert "Crash Base Alerts" in msg
    assert "RNG" in msg
    assert "Tier 1 Trendline Breakout" in msg


def test_ma_cluster_thrust_digest_prefers_a_weekly_tier_and_marks_fallback():
    weekly_tier = {
        "available": True,
        "tier": 2,
        "ma_width_pct": 7.2,
        "relative_volume": 1.7,
        "mas_crossed": 3,
        "mas_total": 5,
        "close_location": 0.6,
        "extension_above_bundle_pct": 1.3,
        "nearest_score": 81.0,
    }
    nearest = {**weekly_tier, "tier": None, "ma_width_pct": 11.4, "nearest_score": 72.0}
    cands = [
        _cand(
            "WEEK",
            setups={"ma_cluster_volume_breakout": {"setup_name": "ma_cluster_volume_breakout", "triggered": False, "raw_features": {"ma_cluster_thrust_weekly": weekly_tier}}},
        ),
        _cand(
            "NEAR",
            setups={"ma_cluster_volume_breakout": {"setup_name": "ma_cluster_volume_breakout", "triggered": False, "raw_features": {"ma_cluster_thrust_daily": nearest}}},
        ),
    ]

    msg = render_ma_cluster_thrust_digest(as_of="2026-05-18", candidates=cands)

    assert "WEEK" in msg
    assert "weekly" in msg
    assert "Tier 2" in msg
    assert "NEAR" not in msg

    fallback = render_ma_cluster_thrust_digest(as_of="2026-05-18", candidates=[cands[1]])
    assert "najbliži kandidati" in fallback
    assert "NEAR" in fallback


def test_daily_send_appends_ma_cluster_card_as_a_numbered_part(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        "stock_scout.notifications.telegram.send_message_parts",
        lambda _cfg, parts: sent.extend(parts) or True,
    )
    candidate = _cand(
        "THRUST",
        setups={"ma_cluster_volume_breakout": {"setup_name": "ma_cluster_volume_breakout", "triggered": False, "raw_features": {"ma_cluster_thrust_daily": {"available": True, "tier": 1, "ma_width_pct": 4.0, "relative_volume": 2.4, "mas_crossed": 5, "mas_total": 5, "close_location": 0.8, "extension_above_bundle_pct": 2.0, "nearest_score": 95.0}}}},
    )

    assert send_digest(TelegramConfig("token", "chat"), as_of="2026-05-18", candidates=[candidate], digest="daily")
    assert len(sent) == 3
    assert all(part.startswith(f"\\({index}/3\\)") for index, part in enumerate(sent, 1))
    assert "MA Cluster RVOL Thrust" in sent[-2]
    assert "Preferred Breakout Candidates" in sent[-1]


def test_preferred_ma_digest_is_pure_top_ten_with_stable_ties_and_sizing_gate():
    def profile(points: int) -> dict:
        return {
            "version": 1,
            "timeframe": "daily",
            "points": points,
            "score": round(points / 6 * 100, 1),
            "coverage": 6,
            "components": {},
            "metrics": {
                "pattern_risk_pct": 7.5,
                "ma_width_pct": 5.0,
                "distance_to_prior_52w_high_pct": -12.0,
                "signal_range_atr20": 1.2,
            },
            "archetype": "tight_efficient",
            "archetype_confidence": 75.0,
            "archetype_scores": {"tight_efficient": 75.0},
            "warnings": [],
            "source": "ma_cluster_preferred_research_v1",
        }

    candidates = [_cand("FIRST", ma_cluster_research=profile(5)), _cand("SECOND", ma_cluster_research=profile(5))]
    msg = render_preferred_ma_digest(as_of="2026-05-18", candidates=candidates)
    assert msg.index("FIRST") < msg.index("SECOND")
    assert "5/6" in msg
    assert "Sizing disabled" in msg


def test_render_daily_digest_preserves_and_splits_long_message():
    cands = [
        _cand(
            f"TICK{i:03}",
            90,
            focus_score=90,
            primary_setup="ema_stack_launch",
            ema_stack_launch_score=85,
            ema_stack_phase="stack_thrust",
            current_thrust_rel_volume=2.0,
        )
        for i in range(300)
    ]
    msg = render_daily_digest(
        as_of="2026-05-18",
        candidates=cands,
        top_focus=200,
        top_launch=50,
        top_accumulation=50,
    )
    parts = split_telegram_message(msg)
    assert len(msg) > 4000
    assert len(parts) > 1
    assert all(len(part) <= 3900 for part in parts)
    assert "TICK199" in "\n".join(parts)
    assert "truncated" not in msg.lower()


def test_telegram_config_from_env_returns_none_if_no_creds():
    class _Env:
        TELEGRAM_BOT_TOKEN = ""
        TELEGRAM_CHAT_ID = ""

    assert telegram_config_from_env(_Env()) is None


def test_telegram_config_from_env_builds_when_present():
    class _Env:
        TELEGRAM_BOT_TOKEN = "123:abc"
        TELEGRAM_CHAT_ID = "456"

    cfg = telegram_config_from_env(_Env())
    assert isinstance(cfg, TelegramConfig)
    assert cfg.bot_token == "123:abc"
    assert cfg.chat_id == "456"


def test_telegram_marker_records_sent_digest(tmp_path):
    from stock_scout.cli import (
        _load_telegram_marker,
        _mark_telegram_sent,
        _telegram_marker_key,
    )

    marker = tmp_path / ".telegram_sent.json"
    _mark_telegram_sent(marker, date="2026-05-18", digest="daily")
    data = _load_telegram_marker(marker)

    key = _telegram_marker_key("2026-05-18", "daily")
    assert key in data
    assert data[key]["date"] == "2026-05-18"
    assert data[key]["digest"] == "daily"


def test_multipart_send_honors_retry_after_and_records_each_part(monkeypatch):
    class Response:
        def __init__(self, status_code: int, retry_after: int = 0) -> None:
            self.status_code = status_code
            self.text = "rate limited" if status_code == 429 else "ok"
            self._retry_after = retry_after

        def json(self):
            return {"parameters": {"retry_after": self._retry_after}}

    responses = iter([Response(429, 2), Response(200), Response(200)])
    sleeps: list[float] = []
    progress: list[tuple[int, int]] = []
    monkeypatch.setattr("stock_scout.notifications.telegram.requests.post", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr("stock_scout.notifications.telegram.time.sleep", sleeps.append)
    suppress_sends(False)

    ok = send_message_parts(
        TelegramConfig("token", "chat"),
        ["part 1", "part 2"],
        on_part_sent=lambda sent, total: progress.append((sent, total)),
    )

    assert ok is True
    assert sleeps == [2.0]
    assert progress == [(1, 2), (2, 2)]


def test_partial_multipart_send_does_not_report_completion(monkeypatch):
    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.text = "failed"

    responses = iter([Response(200), Response(500)])
    progress: list[tuple[int, int]] = []
    monkeypatch.setattr("stock_scout.notifications.telegram.requests.post", lambda *args, **kwargs: next(responses))
    suppress_sends(False)

    ok = send_message_parts(
        TelegramConfig("token", "chat"),
        ["part 1", "part 2"],
        on_part_sent=lambda sent, total: progress.append((sent, total)),
    )

    assert ok is False
    assert progress == [(1, 2)]


def test_splitter_does_not_cut_a_markdown_escape():
    text = "word\\. " * 1000

    parts = split_telegram_message(text, limit=300)

    for part in parts:
        trailing_backslashes = len(part) - len(part.rstrip("\\"))
        assert trailing_backslashes % 2 == 0
