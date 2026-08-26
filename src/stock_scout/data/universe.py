from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests
import yaml

from stock_scout.config.schema import UniverseConfig
from stock_scout.utils.logging import get_logger

log = get_logger(__name__)


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


@dataclass
class UniverseSymbol:
    ticker: str
    name: str
    exchange: str
    etf: bool
    test_issue: bool


def _fetch_text(url: str, timeout: int = 30) -> str:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "stock-scout/0.1"})
    resp.raise_for_status()
    return resp.text


def _latest_universe_path(project_root: Path | None) -> Path | None:
    if project_root is None:
        return None
    return project_root / "data" / "universe" / "latest.txt"


def _load_latest_universe(project_root: Path | None) -> list[str]:
    path = _latest_universe_path(project_root)
    if path is None or not path.exists():
        return []
    tickers = [
        line.strip().upper()
        for line in path.read_text(encoding="utf-8").replace(",", "\n").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return sorted(set(tickers))


def _write_latest_universe(project_root: Path | None, tickers: list[str]) -> None:
    path = _latest_universe_path(project_root)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(sorted(set(tickers))) + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("universe.latest_write_failed", path=str(path), error=str(e))


def _parse_nasdaq_listed(text: str) -> list[UniverseSymbol]:
    df = pd.read_csv(io.StringIO(text), sep="|")
    if df.empty:
        return []
    # Drop trailing "File Creation Time" footer row
    df = df[~df.iloc[:, 0].astype(str).str.startswith("File Creation Time")]
    out = []
    for _, row in df.iterrows():
        ticker = str(row.get("Symbol", "")).strip()
        if not ticker:
            continue
        out.append(
            UniverseSymbol(
                ticker=ticker,
                name=str(row.get("Security Name", "")).strip(),
                exchange="NASDAQ",
                etf=str(row.get("ETF", "N")).strip().upper() == "Y",
                test_issue=str(row.get("Test Issue", "N")).strip().upper() == "Y",
            )
        )
    return out


def _parse_other_listed(text: str) -> list[UniverseSymbol]:
    df = pd.read_csv(io.StringIO(text), sep="|")
    if df.empty:
        return []
    df = df[~df.iloc[:, 0].astype(str).str.startswith("File Creation Time")]
    out = []
    exch_map = {"A": "AMEX", "N": "NYSE", "P": "NYSEARCA", "Z": "BATS"}
    for _, row in df.iterrows():
        ticker = str(row.get("ACT Symbol", row.get("CQS Symbol", ""))).strip()
        if not ticker:
            continue
        exch_code = str(row.get("Exchange", "")).strip().upper()
        out.append(
            UniverseSymbol(
                ticker=ticker,
                name=str(row.get("Security Name", "")).strip(),
                exchange=exch_map.get(exch_code, exch_code or "OTHER"),
                etf=str(row.get("ETF", "N")).strip().upper() == "Y",
                test_issue=str(row.get("Test Issue", "N")).strip().upper() == "Y",
            )
        )
    return out


# Word-boundary patterns on the Security Name. Substring matching ("UNIT" in
# name) is unsafe — it drops legit common stock such as UNITED-HEALTH ("UNIT" ⊂
# "UNITED") or names like WRIGHT/BRIGHT ("RIGHT"). The NASDAQ Trader Security
# Name reliably spells out the instrument type, so a word-boundary match on the
# name is the primary, low-false-positive signal.
_WARRANT_NAME_RE = re.compile(r"\bWARRANTS?\b")
_UNIT_NAME_RE = re.compile(r"\bUNITS?\b")
_RIGHT_NAME_RE = re.compile(r"\bRIGHTS?\b")
_PREFERRED_NAME_RE = re.compile(r"\b(PREFERRED|PREFERENCE|PFD)\b")
_PREFERRED_DEPOSITARY_RE = re.compile(
    r"(\bPREFERRED\b.*\bDEPOSITARY\b|\bDEPOSITARY\b.*\bPREFERRED\b)"
)
_ADR_NAME_RE = re.compile(
    r"\b(AMERICAN DEPOSITARY|SPONSORED AMERICAN DEPOSITARY|ADS\b|ADR\b)"
)
_DEBT_NAME_RE = re.compile(
    r"(\b\d+(?:\.\d+)?%\b.*\bNOTES?\b|\bNOTES?\s+DUE\b|\bSENIOR\s+NOTES?\b|\bSUBORDINATED\s+NOTES?\b|\bDEBENTURES?\b)"
)
_WHEN_ISSUED_NAME_RE = re.compile(r"\bWHEN[- ]ISSUED\b|\bWHEN ISSUED\b|\bEX[- ]DISTRIBUTION\b")


def _looks_like_warrant_or_unit(name: str, ticker: str) -> tuple[bool, bool, bool, bool]:
    """Heuristic flags: (warrant, unit, right, preferred).

    Primary signal is the Security Name (word-boundary match). The secondary
    signal is ONLY unambiguous, *separated* ticker suffixes (".WS", ".U", ".R",
    "$", "-P"). The previous bare single-letter rules (endswith "W"/"U"/"R"/"PR")
    were dropping legitimate common stock — e.g. SNOW, EW, ARW (end in W) and
    EXPR (ends in PR) — so they are intentionally gone. Genuine 5-letter NASDAQ
    warrants/units/rights ("ABCDW") still carry "Warrant"/"Unit"/"Right" in the
    name and are caught there. A handful of name-less leaks fall to the liquidity
    prefilter — preferable to silently deleting real common stock.
    """
    n = name.upper()
    t = ticker.upper()
    warrant = bool(_WARRANT_NAME_RE.search(n)) or t.endswith((".WS", "-WS", "/WS"))
    unit = bool(_UNIT_NAME_RE.search(n)) or t.endswith((".U", "-U", "/U")) or "=" in t
    right = bool(_RIGHT_NAME_RE.search(n)) or t.endswith((".R", "-R", "/R"))
    # Preferred-share quoted symbols use "$" (AHL$D), "-P"/".PR" (class series),
    # which providers can't fetch — drop them at build time.
    is_ordinary_adr = bool(_ADR_NAME_RE.search(n)) and not bool(_PREFERRED_NAME_RE.search(n))
    preferred = (
        not is_ordinary_adr
        and (
            bool(_PREFERRED_NAME_RE.search(n))
            or bool(_PREFERRED_DEPOSITARY_RE.search(n))
            or "$" in t
            or "-P" in t
            or t.endswith(".PR")
        )
    )
    return warrant, unit, right, preferred


def _looks_like_non_common_security(name: str) -> bool:
    n = name.upper()
    return bool(_DEBT_NAME_RE.search(n) or _WHEN_ISSUED_NAME_RE.search(n))


@dataclass
class UniverseStats:
    initial: int
    after_filter: int
    dropped_etf: int
    dropped_warrant: int
    dropped_unit: int
    dropped_right: int
    dropped_preferred: int
    dropped_test_issue: int
    dropped_other: int
    force_included: int
    force_excluded: int


@dataclass
class UniverseRegistry:
    records: list[dict]
    tickers: list[str]
    stats: UniverseStats


def build_universe_registry(
    cfg: UniverseConfig,
    project_root: Path | None = None,
) -> UniverseRegistry:
    """Fetch + filter the US universe and keep raw include/exclude metadata."""
    log.info("universe.fetch_start")
    try:
        nasdaq = _parse_nasdaq_listed(_fetch_text(NASDAQ_LISTED_URL))
        other = _parse_other_listed(_fetch_text(OTHER_LISTED_URL))
    except Exception as e:
        log.error("universe.fetch_failed", error=str(e))
        cached = _load_latest_universe(project_root)
        if cached:
            log.warning("universe.using_cached_latest", count=len(cached))
            stats = UniverseStats(
                initial=len(cached),
                after_filter=len(cached),
                dropped_etf=0,
                dropped_warrant=0,
                dropped_unit=0,
                dropped_right=0,
                dropped_preferred=0,
                dropped_test_issue=0,
                dropped_other=0,
                force_included=0,
                force_excluded=0,
            )
            records = [
                {
                    "ticker": t,
                    "name": None,
                    "exchange": "cached",
                    "etf": False,
                    "test_issue": False,
                    "is_common_stock": True,
                    "include_in_scan": True,
                    "exclude_reason": "",
                    "raw_source": "cached_latest",
                }
                for t in cached
            ]
            return UniverseRegistry(records=records, tickers=cached, stats=stats)
        raise

    all_syms = nasdaq + other
    initial = len(all_syms)
    log.info("universe.fetched", count=initial)

    # Exchange include/exclude
    keep_exchanges: set[str] = set()
    if cfg.include_nasdaq:
        keep_exchanges.add("NASDAQ")
    if cfg.include_nyse:
        keep_exchanges.add("NYSE")
    if cfg.include_amex:
        keep_exchanges.add("AMEX")

    dropped_etf = dropped_warrant = dropped_unit = dropped_right = 0
    dropped_preferred = dropped_test_issue = dropped_other = 0
    keep: list[str] = []
    records: list[dict] = []

    for s in all_syms:
        exclude_reason = ""
        if s.exchange not in keep_exchanges:
            dropped_other += 1
            exclude_reason = "exchange"
        elif cfg.exclude_test_issues and s.test_issue:
            dropped_test_issue += 1
            exclude_reason = "test_issue"
        elif cfg.exclude_etf and s.etf:
            dropped_etf += 1
            exclude_reason = "etf"
        else:
            warrant, unit, right, preferred = _looks_like_warrant_or_unit(s.name, s.ticker)
            if cfg.exclude_warrants and warrant:
                dropped_warrant += 1
                exclude_reason = "warrant"
            elif cfg.exclude_units and unit:
                dropped_unit += 1
                exclude_reason = "unit"
            elif cfg.exclude_rights and right:
                dropped_right += 1
                exclude_reason = "right"
            elif cfg.exclude_preferred and preferred:
                dropped_preferred += 1
                exclude_reason = "preferred"
            elif _looks_like_non_common_security(s.name):
                dropped_other += 1
                exclude_reason = "non_common"
            elif not cfg.include_adr and _ADR_NAME_RE.search(s.name.upper()):
                dropped_other += 1
                exclude_reason = "adr"

        include = not exclude_reason
        if include:
            keep.append(s.ticker)
        records.append(
            {
                "ticker": s.ticker.upper(),
                "name": s.name,
                "exchange": s.exchange,
                "etf": s.etf,
                "test_issue": s.test_issue,
                "is_common_stock": include,
                "include_in_scan": include,
                "exclude_reason": exclude_reason,
                "raw_source": "nasdaq_trader",
            }
        )

    record_map = {r["ticker"]: r for r in records}

    # Manual overrides
    force_included = 0
    force_excluded = 0
    if cfg.manual_overrides_file:
        overrides_path = Path(cfg.manual_overrides_file)
        if project_root and not overrides_path.is_absolute():
            overrides_path = project_root / overrides_path
        if overrides_path.exists():
            with overrides_path.open("r", encoding="utf-8") as fh:
                ov = yaml.safe_load(fh) or {}
            forced_in = [t.upper() for t in (ov.get("force_include") or []) if t]
            forced_out = {t.upper() for t in (ov.get("force_exclude") or []) if t}
            keep_set = {t.upper() for t in keep}
            for t in forced_in:
                if t not in keep_set:
                    keep.append(t)
                    keep_set.add(t)
                    force_included += 1
                rec = record_map.get(t)
                if rec is None:
                    rec = {
                        "ticker": t,
                        "name": None,
                        "exchange": "manual",
                        "etf": False,
                        "test_issue": False,
                        "raw_source": "manual_override",
                    }
                    records.append(rec)
                    record_map[t] = rec
                rec["is_common_stock"] = True
                rec["include_in_scan"] = True
                rec["exclude_reason"] = ""
            before = len(keep)
            keep = [t for t in keep if t.upper() not in forced_out]
            force_excluded = before - len(keep)
            for t in forced_out:
                rec = record_map.get(t)
                if rec is not None:
                    rec["include_in_scan"] = False
                    rec["exclude_reason"] = "manual_exclude"
                    rec["is_common_stock"] = False

    keep = sorted(set(keep))
    stats = UniverseStats(
        initial=initial,
        after_filter=len(keep),
        dropped_etf=dropped_etf,
        dropped_warrant=dropped_warrant,
        dropped_unit=dropped_unit,
        dropped_right=dropped_right,
        dropped_preferred=dropped_preferred,
        dropped_test_issue=dropped_test_issue,
        dropped_other=dropped_other,
        force_included=force_included,
        force_excluded=force_excluded,
    )
    log.info("universe.built", **stats.__dict__)
    _write_latest_universe(project_root, keep)
    return UniverseRegistry(records=records, tickers=keep, stats=stats)


def build_universe(
    cfg: UniverseConfig,
    project_root: Path | None = None,
) -> tuple[list[str], UniverseStats]:
    """Fetch + filter the US common-stock universe from NASDAQ Trader files."""
    registry = build_universe_registry(cfg, project_root)
    return registry.tickers, registry.stats


def load_smoke_universe(path: str | Path) -> list[str]:
    """Load a hand-picked ticker list from JSON (config/smoke_universe.json)."""
    import json

    with Path(path).open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    tickers = data.get("tickers", [])
    return sorted({str(t).upper() for t in tickers if t})
