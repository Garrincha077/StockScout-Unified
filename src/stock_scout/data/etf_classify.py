"""Classify ETFs by name so leveraged and inverse products stay out of the scan.

The Stooq archive carries ~3.6k ETF files with no metadata beyond the ticker, so
classification runs off `securities.name` (the NASDAQ Trader security name).

The hard part is that "short" and "ultra" are overloaded. "ProShares UltraShort
Nasdaq Biotechnology" is -2x leveraged; "CrossingBridge Ultra-Short Duration ETF"
is an ordinary bond fund. Rather than guess from those words, leverage is
recognised by the things that actually mark it: an explicit multiplier, a known
leveraged product line, or an explicit inverse/bull/bear label.
"""

from __future__ import annotations

import re
from typing import Literal

EtfKind = Literal["plain", "leveraged", "inverse", "volatility", "unknown"]

# "2X", "3x", "1.5X", "-3x". The dash form only appears on inverse products.
_MULTIPLIER = re.compile(r"(?<![A-Za-z0-9.])-?\d(?:\.\d)?\s*[Xx](?![A-Za-z0-9])")

# "Ultra" means leverage only when ProShares says it. Everyone else uses it for
# bond maturity: Vanguard Ultra-Short Bond, Schwab Ultra-Short Income, Angel Oak
# UltraShort Income. A bond-context word cannot be used to tell them apart —
# ProShares' own TBT is "UltraShort Lehman 20 Year Treasury", leveraged despite
# the word Treasury. The issuer is the discriminator.
# UltraPro is exempt: it is a ProShares-only 3x brand and appears unprefixed
# (SDOW is just "UltraPro Short Dow30").
_ULTRA_LEVERED = re.compile(r"\bultrapro\b|\bproshares\s+ultra", re.I)

# Direxion-style directional labels, and explicit inverse wording.
_INVERSE_WORDS = re.compile(r"\binverse\b|\bbear\b|\bultrashort\b", re.I)
_BULL = re.compile(r"\bbull\b", re.I)
_LEVERAGED_WORD = re.compile(r"\bleveraged?\b", re.I)

# Volatility products: unleveraged but roll-decaying, so not ordinary holdings.
_VOLATILITY = re.compile(r"\bvix\b|\bvolatility\b", re.I)


def classify_etf_name(name: str | None) -> EtfKind:
    """Return the ETF's kind, or "unknown" when there is no name to judge.

    "unknown" is deliberately distinct from "plain": a ticker present only in the
    archive (delisted, or missing from the registry feed) must not be silently
    treated as an ordinary fund.
    """
    if name is None:
        return "unknown"
    text = str(name).strip()
    if not text:
        return "unknown"

    ultra_levered = bool(_ULTRA_LEVERED.search(text))
    daily_reset = bool(re.search(r"\bdaily\b", text, re.I))
    levered = (
        bool(_MULTIPLIER.search(text))
        or ultra_levered
        or bool(_LEVERAGED_WORD.search(text))
        # "Bull"/"Bear" only mean leverage on a daily-reset product; on their own
        # they turn up in ordinary fund names too.
        or (daily_reset and bool(_BULL.search(text)))
        or (daily_reset and bool(_INVERSE_WORDS.search(text)))
    )
    if levered:
        # Inverse is the more specific label; both are excluded from the scan.
        # `_INVERSE_WORDS` includes "ultrashort", which is only consulted here —
        # i.e. after something already established leverage — so a non-ProShares
        # ultra-short bond fund never reaches this branch.
        if _INVERSE_WORDS.search(text) or re.search(r"(?<![A-Za-z0-9.])-\d", text):
            return "inverse"
        return "leveraged"

    if _VOLATILITY.search(text):
        return "volatility"
    return "plain"


def is_scannable_etf_kind(kind: EtfKind) -> bool:
    """Only ordinary funds belong in the ETF section's default view."""
    return kind == "plain"
