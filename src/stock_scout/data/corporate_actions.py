"""M&A / corporate-action false-positive filter.

Detects stocks whose price-action is consistent with being in a buyout /
merger deal — typically a one-day announcement gap followed by 20+ days of
flat trading near the deal price. Such tickers technically pass tight /
VCP / GLB filters but are not real breakout setups.

Two detection layers:
  1. **Price-based** (zero-cost, always works) — gap-then-flat, range
     collapse, flat-cap patterns from the cached OHLCV.
  2. **News-based** (optional) — keyword scan against FMP / Alpaca / yfinance
     news endpoints. Cached for 7 days.

See docs/METHODOLOGY.md §6 for the methodology.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from stock_scout.indicators.volatility import atr
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)

Confidence = Literal["none", "low", "medium", "high"]


_KEYWORDS = (
    # "acquir" covers acquire/acquired/acquirer/acquiring but NOT "acquisition",
    # which is spelled acquis- and is the single most common word in deal
    # headlines. A $55bn take-private was surviving the filter on that alone.
    "acquir",
    "acquisition",
    "take-private",
    "take private",
    "taken private",
    "bid for",
    "merger",
    "merge ",
    "buyout",
    "tender offer",
    "definitive agreement",
    "going private",
    "going-private",
    "offer price",
    "deal expected to close",
    "deal close",
    "takeover",
    "to be acquired",
    "agreed to acquire",
    "all-cash transaction",
    "all cash transaction",
    "per-share offer",
)


@dataclass
class MAFinding:
    ticker: str
    confidence: Confidence = "none"
    price_signals: list[str] = field(default_factory=list)
    news_keywords: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "confidence": self.confidence,
            "price_signals": ",".join(self.price_signals),
            "news_keywords": ",".join(self.news_keywords),
            "detail": self.detail,
        }


@dataclass
class MADetectorConfig:
    # Gap detection
    gap_pct_threshold: float = 10.0          # one-day move >= this counts as a gap
    # How far back to look for the announcement gap. Was 60, which combined with
    # `flat_lookback_bars` searched only bars 80..20 back - a window a pending
    # deal outgrows in about four months, after which `gap_then_flat` can never
    # be true again no matter how obviously pinned the stock is. Deals routinely
    # take 6-12 months to close, so the search now covers a year.
    gap_lookback_bars: int = 250
    # Post-gap flat: avg daily range over last N bars
    flat_lookback_bars: int = 20
    flat_max_daily_range_pct: float = 1.5
    # Range collapse: atr20_now vs atr20_60d_ago
    range_collapse_ratio: float = 0.4
    # Flat-cap: close within +/- X% of a constant level
    flat_cap_band_pct: float = 2.0
    flat_cap_volume_lookback_bars: int = 60   # baseline period, kept for reporting
    # Minimum history required to evaluate
    min_bars: int = 80

    # --- Absolute pin test -------------------------------------------------
    # The three tests above are all relative: to a gap that scrolls out of view,
    # or to how the stock traded 60 bars ago. A name pinned for months looks
    # unremarkable to every one of them, because its own recent past is pinned
    # too. These thresholds are absolute, and were read off the measurements in
    # the plan rather than guessed:
    #
    #   pinned   CPRX 0.99% range / 88% in band / 0.03 corr
    #            TBPH 1.69%       / 85%         / -0.15
    #            TWO  0.77%       / 98%         /  0.04
    #   not      CRNX 3.75%       / 28%         / -0.08   (+118% in 60 days)
    #            ACA  2.38%       / 28%         /  0.36
    #   controls 6.9-12.6%        / 0-15%       /  0.12-0.57
    #
    # The nearest control sits at 6.9% daily range against a 2.0% threshold, so
    # there is room. Correlation alone would convict CRNX, which is why all
    # three conditions must hold together.
    pin_lookback_bars: int = 60
    pin_band_pct: float = 3.0
    pin_min_in_band: float = 0.80
    pin_max_daily_range_pct: float = 2.0
    pin_max_spy_corr: float = 0.20
    pin_min_corr_overlap: int = 30            # bars needed before corr is trusted

    # --- 20-bar pin --------------------------------------------------------
    # The 60-bar pin above cannot see a deal until the announcement has scrolled
    # out of its own window. ACA, an agreed $8.5bn all-cash takeover, sits at
    # in-band 20.0% and ADR-60 2.23 because those sixty bars still contain the
    # pre-announcement price, while its last twenty bars have already collapsed
    # to 0.43% from 3.03%. The two tests catch different *phases* of one event,
    # which is why this is a separate condition rather than a tightening.
    #
    # Both halves are needed and neither works alone. The ratio on its own fires
    # on 0.91% of all liquid ticker-dates -- 5.60% of them in 2020, where it is
    # reading a volatility crush -- at a forward dispersion of 23.41 against the
    # universe's 24.0, i.e. ordinary stocks. The absolute floor on its own is
    # what utilities defeat: SO 1.79, O 1.58 and ED 1.87 all sit under a 2.0%
    # bar while their ratios are 1.11, 1.03 and 1.01, no collapse at all.
    #
    # Chosen on in-sample dates only and applied unchanged to 2023-2025:
    # 19 firings at sd 6.34 (universe 22.9) in sample, 32 at sd 8.15 (universe
    # 25.8) held out. See docs/MEASUREMENTS.md, "The ADR collapse, held out".
    short_pin_lookback_bars: int = 20
    short_pin_prior_start_bars: int = 150
    short_pin_prior_end_bars: int = 60
    short_pin_max_daily_range_pct: float = 1.0
    short_pin_max_ratio: float = 0.25

    # --- News confirmation -------------------------------------------------
    # A deal announced two years ago is not a reason to drop a name today.
    news_max_age_days: int = 400


# ---- Price-based detection -------------------------------------------------


def _avg_daily_range_pct(df: pd.DataFrame, last_n: int) -> float:
    if df.empty or last_n < 1:
        return 0.0
    tail = df.tail(last_n)
    rng = (tail["high"] - tail["low"]) / tail["close"].replace(0, np.nan) * 100.0
    return float(rng.mean(skipna=True))


def _has_gap_then_flat(df: pd.DataFrame, cfg: MADetectorConfig) -> tuple[bool, dict]:
    """True if there is a >= gap_pct_threshold one-day move within the lookback
    window AND the last `flat_lookback_bars` are flat."""
    detail: dict = {}
    if len(df) < cfg.flat_lookback_bars + 5:
        return False, detail

    close = df["close"]
    prev = close.shift(1)
    pct_move = (close - prev) / prev.replace(0, np.nan) * 100.0
    window = pct_move.iloc[-cfg.gap_lookback_bars - cfg.flat_lookback_bars : -cfg.flat_lookback_bars]
    if window.empty:
        return False, detail
    max_gap = float(window.abs().max(skipna=True))
    detail["max_gap_pct"] = round(max_gap, 2)
    if max_gap < cfg.gap_pct_threshold:
        return False, detail

    avg_rng = _avg_daily_range_pct(df, cfg.flat_lookback_bars)
    detail["recent_avg_daily_range_pct"] = round(avg_rng, 2)
    if avg_rng > cfg.flat_max_daily_range_pct:
        return False, detail

    return True, detail


def _has_range_collapse(df: pd.DataFrame, cfg: MADetectorConfig) -> tuple[bool, dict]:
    detail: dict = {}
    if len(df) < 90:
        return False, detail
    a = atr(df, 20)
    if a.empty:
        return False, detail
    now = float(a.iloc[-1])
    then = float(a.iloc[-60]) if not pd.isna(a.iloc[-60]) else 0.0
    if then <= 0:
        return False, detail
    ratio = now / then
    detail["atr20_now_over_60d_ago"] = round(ratio, 3)
    return ratio < cfg.range_collapse_ratio, detail


def _has_flat_cap(df: pd.DataFrame, cfg: MADetectorConfig) -> tuple[bool, dict]:
    """Price parked inside a narrow band around a constant level.

    This used to also require volume to have dried up to under half its
    pre-event baseline, which describes a quiet base and not a locked deal.
    Merger arbitrage does the opposite - measured on the three clearest pins in
    the screen, volume ran at 0.98x, 1.66x and 2.21x the baseline, so the single
    most diagnostic signal available (price inside a 2% band on every one of the
    last 20 days) was being vetoed by the crowd that shows up *because* of the
    deal. The ratio is still reported, because it is worth seeing; it no longer
    decides.
    """
    detail: dict = {}
    if len(df) < cfg.flat_lookback_bars:
        return False, detail
    tail = df.tail(cfg.flat_lookback_bars)
    level = float(tail["close"].median())
    if level <= 0:
        return False, detail
    band = (tail["close"] - level).abs() / level * 100.0
    within = (band <= cfg.flat_cap_band_pct).mean()
    detail["within_band_pct"] = round(float(within) * 100.0, 1)

    needed = cfg.flat_cap_volume_lookback_bars + cfg.flat_lookback_bars
    if len(df) >= needed:
        baseline = df["volume"].iloc[-needed : -cfg.flat_lookback_bars]
        if not baseline.empty and float(baseline.mean()) > 0:
            detail["vol_ratio_vs_baseline"] = round(
                float(tail["volume"].mean()) / float(baseline.mean()), 2
            )
    return bool(within >= 0.9), detail


def _has_price_pin(
    df: pd.DataFrame,
    cfg: MADetectorConfig,
    spy_returns: pd.Series | None = None,
) -> tuple[bool, dict]:
    """Is this stock parked, in absolute terms, and has it stopped tracking the market?

    Every other detector here is relative - to an announcement gap that scrolls
    out of the search window, or to how the stock traded 60 bars ago. A name
    that has been pinned for months defeats all of them, because its own recent
    past is pinned as well. That is exactly when its tightness score is highest,
    so the screener was most confident precisely where it was most wrong.

    Three absolute conditions, all of which must hold. None is sufficient alone:
    correlation on its own would convict a biotech that is uncorrelated because
    it is up 118% in sixty days, and a narrow band on its own would convict any
    quiet stock. Together they separated the pinned names from both the movers
    and the controls without a single crossover.

    `spy_returns` is optional. A missing benchmark drops the correlation
    condition rather than failing it - an absent reading is not evidence.
    """
    detail: dict = {}
    n = cfg.pin_lookback_bars
    if len(df) < n:
        return False, detail
    tail = df.tail(n)

    level = float(tail["close"].median())
    if level <= 0:
        return False, detail
    in_band = float((((tail["close"] - level).abs() / level * 100.0) <= cfg.pin_band_pct).mean())
    detail["pin_in_band_pct"] = round(in_band * 100.0, 1)

    close = tail["close"].replace(0, np.nan)
    daily_range = float((((tail["high"] - tail["low"]) / close) * 100.0).mean(skipna=True))
    detail["pin_daily_range_pct"] = round(daily_range, 2)

    if in_band < cfg.pin_min_in_band or not np.isfinite(daily_range):
        return False, detail
    if daily_range >= cfg.pin_max_daily_range_pct:
        return False, detail

    if spy_returns is not None and not spy_returns.empty:
        joined = pd.concat(
            [df["close"].pct_change(), spy_returns], axis=1, join="inner"
        ).dropna().tail(n)
        if len(joined) >= cfg.pin_min_corr_overlap:
            corr = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
            detail["pin_spy_corr"] = round(corr, 2) if np.isfinite(corr) else None
            if np.isfinite(corr) and abs(corr) >= cfg.pin_max_spy_corr:
                return False, detail

    return True, detail


def _has_short_pin(df: pd.DataFrame, cfg: MADetectorConfig) -> tuple[bool, dict]:
    """Has the daily range collapsed against this stock's *own* earlier range?

    Measured over 120 month-end dates and 198,086 liquid ticker-dates, the two
    conditions together fire 43 times across **18 distinct tickers** in ten
    years, and essentially every one is a named deal or take-private. Held out
    on dates the thresholds never saw, those names carry a forward 3-month
    standard deviation of 8.15 against the universe's 25.8.

    Dispersion is the test here, not return. A real target converges on a
    contracted price, so its outcome is small **and** low-variance, because it is
    written in an agreement rather than argued in a market. That is also the
    limit of what this licenses: the sign of the excess return is not stable
    (+2.21 in sample, -4.83 out, because the early block is full of deals that
    broke and popped). It says the outcome is contractual, not that the stock
    will fall.

    The comparison window stops 60 bars back rather than running to the present.
    A name pinned for months has a pinned recent past too, which is exactly how
    it defeats `_has_range_collapse`; and a deal older than ~150 bars defeats
    this one in turn, for the same reason.
    """
    detail: dict = {}
    if len(df) < cfg.short_pin_prior_start_bars + 1:
        return False, detail

    recent = _avg_daily_range_pct(df, cfg.short_pin_lookback_bars)
    prior_slice = df.iloc[-cfg.short_pin_prior_start_bars : -cfg.short_pin_prior_end_bars]
    prior = _avg_daily_range_pct(prior_slice, len(prior_slice))
    detail["short_pin_adr_pct"] = round(recent, 3)
    detail["short_pin_adr_prior_pct"] = round(prior, 3)
    if not (np.isfinite(recent) and np.isfinite(prior)) or prior <= 0:
        return False, detail
    detail["short_pin_ratio"] = round(recent / prior, 3)

    # A halted or badly-sourced name is not a locked deal. INHD reported a
    # 20-bar range of 0.00 against 17.18 prior - the cleanest possible ratio and
    # no evidence whatsoever - so a zero range, or any zero-volume day inside the
    # window, disqualifies before the thresholds are consulted.
    if recent <= 0:
        return False, detail
    vol = df["volume"].tail(cfg.short_pin_lookback_bars)
    if vol.empty or float(vol.min()) <= 0:
        return False, detail

    if recent >= cfg.short_pin_max_daily_range_pct:
        return False, detail
    return (recent / prior) < cfg.short_pin_max_ratio, detail


def detect_m_and_a_from_price(
    df: pd.DataFrame,
    *,
    ticker: str,
    cfg: MADetectorConfig | None = None,
    spy_returns: pd.Series | None = None,
) -> MAFinding:
    """Run the price-based detectors and aggregate to a confidence level."""
    cfg = cfg or MADetectorConfig()
    finding = MAFinding(ticker=ticker)
    if df is None or df.empty or len(df) < cfg.min_bars:
        return finding

    detail_parts: list[str] = []
    gap_flat, gd = _has_gap_then_flat(df, cfg)
    rcollapse, rd = _has_range_collapse(df, cfg)
    flatcap, fd = _has_flat_cap(df, cfg)
    pin, pd_ = _has_price_pin(df, cfg, spy_returns)
    short_pin, sd_ = _has_short_pin(df, cfg)

    if gap_flat:
        finding.price_signals.append("gap_then_flat")
        detail_parts.append(f"gap={gd.get('max_gap_pct')}%/range={gd.get('recent_avg_daily_range_pct')}%")
    if rcollapse:
        finding.price_signals.append("range_collapse")
        detail_parts.append(f"atr_ratio={rd.get('atr20_now_over_60d_ago')}")
    if flatcap:
        finding.price_signals.append("flat_cap")
        detail_parts.append(f"flat={fd.get('within_band_pct')}%/vol={fd.get('vol_ratio_vs_baseline')}")
    if pin:
        finding.price_signals.append("price_pin")
        detail_parts.append(
            f"pin={pd_.get('pin_in_band_pct')}%/rng={pd_.get('pin_daily_range_pct')}%"
            f"/corr={pd_.get('pin_spy_corr')}"
        )
    if short_pin:
        finding.price_signals.append("short_pin")
        detail_parts.append(
            f"adr20={sd_.get('short_pin_adr_pct')}%/ratio={sd_.get('short_pin_ratio')}"
        )

    # Confidence. **`short_pin` is the one price test that reaches high on its
    # own**, and it earned that on a holdout rather than on a reading of one
    # day: chosen on in-sample dates, then applied unchanged to 2023-2025, where
    # its 32 firings carry a third of the universe's forward dispersion (8.15
    # against 25.8) across 14 tickers that are all real deals. The objection
    # below - that price cannot tell "being acquired" from "dormant" - is
    # answered by the *ratio*, not by the level: a dormant REIT is quiet against
    # its own past too, so its ratio sits near 1.0, while these sit under 0.25.
    # A first attempt at this used the ratio alone at 0.50 and was retracted; it
    # fired on 0.91% of everything and 5.60% of the universe in 2020, reading a
    # volatility crush. Both halves are load-bearing.
    #
    # The ceiling still stands for every other price test:
    #
    # It used to. `gap_then_flat and (range_collapse or flat_cap)` returned
    # high, which was survivable only because the gap search was so narrow that
    # the rule almost never fired - 0 of 2,124 candidates. Widening that window
    # to catch older deals immediately pushed a mortgage REIT and an industrial
    # to high on price alone, both of which belong in the screen. The old rule
    # was not correct, it was inert, and widening the window exposed it.
    #
    # Price genuinely cannot separate "being acquired" from "dormant" - a REIT
    # parked in a 1% band all year has the same signature as a locked deal.
    # So the strongest verdict price may return is medium, and `combine_signals`
    # promotes to high only when a recent headline about *this* company says so.
    if short_pin:
        finding.confidence = "high"
    elif pin or gap_flat or (rcollapse and flatcap):
        finding.confidence = "medium"
    elif rcollapse or flatcap:
        finding.confidence = "low"
    else:
        finding.confidence = "none"

    finding.detail = "; ".join(detail_parts)
    return finding


# ---- News-based detection (optional) ---------------------------------------


# yfinance summaries carry zero-width joiners mid-word - "set <U+200C>to secure",
# "public investment <U+200C>fund" - which silently break substring matching with
# no error to show for it. Stripping them is cheap insurance; it was not the
# cause of the one miss found while building this, which was a missing keyword.
_INVISIBLE = re.compile(r"[​-‏⁠﻿]")


def _normalise(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", _INVISIBLE.sub("", str(text))).lower()


def _matched_keywords(text: str | None) -> list[str]:
    t = _normalise(text)
    if not t:
        return []
    return [k for k in _KEYWORDS if k in t]


def _adapt_news_item(item: dict) -> dict:
    """Flatten one provider item to `{title, summary, pubdate, tickers}`.

    yfinance 0.2.66 nests everything under `content`, so the flat
    `item["title"]` this module was written against is absent and the keyword
    scan silently returns nothing. Measured on three names whose deals are in
    the headlines: flat shape found zero keywords, adapted shape found
    `merger`, `buyout` and `acquir`. Older providers hand back the flat shape
    directly, so both are accepted.
    """
    content = item.get("content") if isinstance(item.get("content"), dict) else None
    src = content or item
    finance = src.get("finance") if isinstance(src.get("finance"), dict) else {}
    tickers = []
    for key in ("stockTickers", "tickers", "relatedTickers"):
        raw = finance.get(key) or src.get(key) or []
        if isinstance(raw, list):
            for entry in raw:
                sym = entry.get("symbol") if isinstance(entry, dict) else entry
                if sym:
                    tickers.append(str(sym).upper())
    return {
        "title": src.get("title") or src.get("headline"),
        "summary": src.get("description") or src.get("summary"),
        "pubdate": str(src.get("displayTime") or src.get("providerPublishTime") or "")[:10],
        "tickers": tickers,
    }


_NAME_NOISE = {
    "inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "company",
    "plc", "ltd", "ltd.", "limited", "holdings", "holding", "group",
    "common", "stock", "class", "shares", "the", "trust", "&",
}


def _company_phrases(company: str | None) -> list[str]:
    """Distinctive lowercase phrases identifying a company in prose.

    "Two Harbors Investment Corp" has to become "two harbors" and not "two",
    because a ticker like TWO, ON or ALL matches ordinary English constantly and
    would let any headline in the feed convict the stock. So a one-word match is
    only allowed when the word is long enough to be a name rather than a word.
    """
    if not company:
        return []
    head = re.split(r"[,\-|]", str(company))[0]
    tokens = [t.strip(".,'\"").lower() for t in head.split()]
    tokens = [t for t in tokens if t and t not in _NAME_NOISE]
    if not tokens:
        return []
    phrases = []
    if len(tokens) >= 2:
        phrases.append(f"{tokens[0]} {tokens[1]}")
    if len(tokens[0]) >= 4:
        phrases.append(tokens[0])
    return phrases


def _is_about(item: dict, ticker: str, company: str | None) -> bool:
    """Is this article about the company, or merely sitting in its feed?

    yfinance mixes unrelated market stories into a ticker's feed - CPRX's feed
    genuinely carried a Solaris Energy headline. Without this check, somebody
    else's merger in the same feed convicts this stock, and the exclusion is
    silent.
    """
    if ticker.upper() in {t.upper() for t in item.get("tickers") or []}:
        return True
    text = _normalise(f"{item.get('title') or ''} {item.get('summary') or ''}")
    if not text.strip():
        return False
    # The financial press writes the symbol parenthesised. A bare symbol match
    # is deliberately not accepted - see `_company_phrases`.
    if f"({ticker.lower()})" in text:
        return True
    return any(p in text for p in _company_phrases(company))


def _within_age(item: dict, max_age_days: int) -> bool:
    raw = str(item.get("pubdate") or "")
    if not raw:
        return True  # undated: let the keyword and relevance checks decide
    try:
        published = datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except ValueError:
        return True
    return datetime.now(UTC) - published <= timedelta(days=max_age_days)


def fetch_news_items(
    ticker: str,
    *,
    company: str | None = None,
    cfg: MADetectorConfig | None = None,
) -> list[dict]:
    """Recent headlines about `ticker`, adapted and filtered. Never raises.

    Called only for names the price gate already flagged - on a recent scan that
    was a handful out of 2,124 - so this is a few network reads a night, not a
    per-candidate cost. A failure here returns nothing, which downgrades the
    verdict to the price-only reading rather than dropping the scan.
    """
    cfg = cfg or MADetectorConfig()
    try:
        import yfinance as yf

        raw = yf.Ticker(str(ticker).upper()).news or []
    except Exception as e:
        log.debug("m_and_a.news_fetch_failed", ticker=ticker, error=repr(e))
        return []

    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        adapted = _adapt_news_item(item)
        if not _within_age(adapted, cfg.news_max_age_days):
            continue
        if not _is_about(adapted, str(ticker), company):
            continue
        out.append(adapted)
    return out


# A company that is buying something is not a company that is being bought, and
# only the second one is dead money. Measured on the pinned names in a real
# screen, roughly half the keyword hits were the company doing the acquiring:
# "DigitalBridge to Acquire ArcLight", "Global Net Lease Buys Modiv", "Starwood
# Acquires Industrial Portfolio". Excluding those would have silently deleted
# healthy candidates.
_ACQUIRER_VERBS = (
    "to acquire", "acquires", "acquired", "agreed to acquire", "agrees to acquire",
    "to buy", "buys", "buying", "to purchase", "purchases", "snaps up", "lands",
)
# Distance in characters between the company name and the deal language before
# the pairing stops meaning anything. A round-up piece that lists twenty tickers
# and mentions a merger elsewhere in the body is not news about this company.
_KEYWORD_PROXIMITY = 120


def _is_acquirer(text: str, phrases: list[str]) -> bool:
    """Does the company read as the buyer in this sentence rather than the target?"""
    for phrase in phrases:
        start = text.find(phrase)
        while start != -1:
            after = text[start + len(phrase) : start + len(phrase) + 40]
            if any(v in after for v in _ACQUIRER_VERBS):
                return True
            start = text.find(phrase, start + 1)
    return False


def _keyword_near_company(text: str, phrases: list[str]) -> list[str]:
    """Keywords that sit close enough to the company name to be about it."""
    found: list[str] = []
    positions = []
    for phrase in phrases:
        idx = text.find(phrase)
        while idx != -1:
            positions.append((idx, idx + len(phrase)))
            idx = text.find(phrase, idx + 1)
    if not positions:
        return []
    for kw in _KEYWORDS:
        k = text.find(kw)
        while k != -1:
            if any(
                min(abs(k - end), abs(start - (k + len(kw)))) <= _KEYWORD_PROXIMITY
                for start, end in positions
            ):
                found.append(kw)
                break
            k = text.find(kw, k + 1)
    return found


def detect_target_keywords(
    news_items: list[dict] | None,
    company: str | None,
    ticker: str = "",
) -> list[str]:
    """Deal keywords that describe *this* company being bought.

    Three things have to line up before a headline counts, and each one was
    added because the previous version convicted something it should not have:

      * the article is about this company (`fetch_news_items` filters that);
      * the deal language sits near the company's name, not merely somewhere in
        a market round-up that happens to list it;
      * the company is not the one doing the buying - a single "X to acquire Y"
        anywhere in the feed vetoes the whole ticker, because a serial acquirer
        will always have deal words around it.
    """
    if not news_items:
        return []
    phrases = _company_phrases(company)
    if ticker:
        phrases = [*phrases, f"({ticker.lower()})"]
    if not phrases:
        return []

    hits: set[str] = set()
    for item in news_items:
        for field_name in ("title", "summary"):
            text = _normalise(item.get(field_name))
            if not text:
                continue
            if _is_acquirer(text, phrases):
                return []  # serial acquirer: this ticker is not a target
            hits.update(_keyword_near_company(text, phrases))
    return sorted(hits)


def detect_m_and_a_from_news(news_items: list[dict] | None) -> list[str]:
    """Return the matched keywords found in `news_items`.

    Each item is expected to have at least `title` and optionally `summary` or
    `description` fields. Provider-agnostic shape.
    """
    if not news_items:
        return []
    out: set[str] = set()
    for item in news_items:
        for field_name in ("title", "headline", "summary", "description"):
            text = item.get(field_name) if isinstance(item, dict) else None
            for kw in _matched_keywords(text):
                out.add(kw)
    return sorted(out)


# ---- Combined ---------------------------------------------------------------


def combine_signals(
    price_finding: MAFinding,
    news_keywords: list[str],
) -> MAFinding:
    """Promote to `high` only where a pinned price and a deal headline agree.

    `high` is the level that excludes a candidate outright, so both halves of
    the evidence have to be the strong kind.

    One case never reaches here needing help: `short_pin` already returns `high`
    from the price layer, on held-out evidence, and this function only ever
    raises a reading. So a name whose 20-bar range has collapsed against its own
    past is excluded with or without news, which is the point - ACA was an
    agreed $8.5bn takeover sitting third in the screen while its headline was
    six months old and out of the news window.

    On the price side that means `price_pin` specifically, not merely `medium`.
    The other detectors are far looser - `gap_then_flat` fires on any stock that
    had a 10% day in the past year and then twenty quiet ones, which on a recent
    screen included a biotech up 118% in sixty days. Pairing a loose price
    signal with a loose keyword would have excluded it.

    On the news side, a keyword only counts once the article has been confirmed
    to be about this company; `fetch_news_items` does that filtering, and this
    function trusts it.

    Without a pin, news still raises the reading to `medium`, which flags and
    de-weights but never drops. Takeover *speculation* is common and cheap; a
    stock that is genuinely locked to a deal price stops trading like a stock,
    and that is what the pin measures.
    """
    finding = MAFinding(
        ticker=price_finding.ticker,
        confidence=price_finding.confidence,
        price_signals=list(price_finding.price_signals),
        news_keywords=news_keywords,
        detail=price_finding.detail,
    )
    if not news_keywords:
        return finding

    if "price_pin" in finding.price_signals:
        finding.confidence = "high"
    elif finding.confidence in ("none", "low"):
        finding.confidence = "medium"
    return finding


# ---- Cache -----------------------------------------------------------------

DEFAULT_CACHE_PATH = Path("data") / "corporate_actions_cache.parquet"
DEFAULT_TTL_DAYS = 7


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_cache(path: Path = DEFAULT_CACHE_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(
            columns=["ticker", "confidence", "price_signals", "news_keywords", "detail", "evaluated_at"]
        )
    try:
        return pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        log.warning("corporate_actions.cache_load_failed", path=str(path), error=str(e))
        return pd.DataFrame()


def save_cache(df: pd.DataFrame, path: Path = DEFAULT_CACHE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def is_cache_fresh(evaluated_at_iso: str, ttl_days: int = DEFAULT_TTL_DAYS) -> bool:
    try:
        ts = datetime.fromisoformat(evaluated_at_iso)
    except Exception:  # noqa: BLE001
        return False
    return datetime.now(timezone.utc) - ts < timedelta(days=ttl_days)


def upsert_cache(cache: pd.DataFrame, finding: MAFinding) -> pd.DataFrame:
    row = finding.as_dict()
    row["evaluated_at"] = _now_iso()
    if cache.empty:
        return pd.DataFrame([row])
    cache = cache[cache["ticker"] != finding.ticker]
    return pd.concat([cache, pd.DataFrame([row])], ignore_index=True)


def cached_finding(cache: pd.DataFrame, ticker: str, ttl_days: int = DEFAULT_TTL_DAYS) -> MAFinding | None:
    if cache.empty:
        return None
    rows = cache[cache["ticker"] == ticker]
    if rows.empty:
        return None
    row = rows.iloc[-1]
    if not is_cache_fresh(str(row.get("evaluated_at", "")), ttl_days):
        return None
    return MAFinding(
        ticker=str(row["ticker"]),
        confidence=str(row.get("confidence", "none")),  # type: ignore[arg-type]
        price_signals=str(row.get("price_signals", "")).split(",") if row.get("price_signals") else [],
        news_keywords=str(row.get("news_keywords", "")).split(",") if row.get("news_keywords") else [],
        detail=str(row.get("detail", "")),
    )
