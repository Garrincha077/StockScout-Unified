#!/usr/bin/env python3
"""Build a read-only Fama-French factor drought / regime dataset.

The engine downloads Kenneth R. French monthly factor files, aligns the six
factor premiums used in the Tactical Asset Allocation chart, and publishes a
compact JSON artifact for the StockScout Factor Regime UI.

This module is deliberately isolated from StockScout scoring and canonical
scan data. It has no dependency on the scanner, LEGACY adapter, or ranking
pipeline.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import statistics
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

FF5_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_5_Factors_2x3_CSV.zip"
)
MOM_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Momentum_Factor_CSV.zip"
)
DEFAULT_OUTPUT = Path("frontend/public/data/factors/factor-regime.json")
WINDOW_MONTHS = 120

FACTOR_SPECS = (
    ("MKT_RF", "Mkt-RF", "Market over cash"),
    ("SMB", "SMB", "Small caps over large"),
    ("HML", "HML", "Value over growth"),
    ("RMW", "RMW", "Profitable over unprofitable"),
    ("CMA", "CMA", "Conservative over aggressive"),
    ("MOM", "Mom", "Winners over losers"),
)


@dataclass(frozen=True)
class Drought:
    start_index: int
    end_index: int
    months: int
    ongoing: bool


def _download(url: str, timeout: int = 45) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "StockScout-Factor-Regime/1.0 (+https://github.com/Garrincha077/StockScreener-next)"
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed HTTPS sources
        return response.read()


def _archive_text(payload: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith((".csv", ".txt"))]
        if not members:
            raise ValueError("Factor archive contains no CSV/TXT member")
        raw = archive.read(members[0])
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unable to decode factor archive")


def fetch_factor_text(url: str) -> str:
    return _archive_text(_download(url))


def parse_monthly_rows(text: str, columns: Sequence[str]) -> dict[str, dict[str, float]]:
    """Parse only YYYYMM monthly rows from a French CSV/TXT export.

    French files include explanatory headers plus annual tables after the
    monthly table. Restricting to six-digit dates makes the parser resilient to
    those sections and to minor header wording changes.
    """

    rows: dict[str, dict[str, float]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not re.match(r"^\d{6}\s*[,\s]", line):
            continue

        if "," in line:
            parts = [part.strip() for part in line.split(",")]
        else:
            parts = re.split(r"\s+", line)

        if len(parts) < len(columns) + 1 or not re.fullmatch(r"\d{6}", parts[0]):
            continue

        year = int(parts[0][:4])
        month = int(parts[0][4:])
        if year < 1900 or not 1 <= month <= 12:
            continue

        try:
            values = [float(parts[index + 1]) for index in range(len(columns))]
        except (ValueError, IndexError):
            continue

        # Kenneth French uses large negative sentinels in some portfolio files.
        # They should never occur in these factor series, so reject them if they
        # appear rather than silently contaminating a decade window.
        if any(not math.isfinite(value) or value <= -90 for value in values):
            continue

        key = f"{year:04d}-{month:02d}"
        rows[key] = dict(zip(columns, values))

    if not rows:
        raise ValueError("No monthly rows were parsed from factor source")
    return rows


def align_factors(
    ff5_rows: Mapping[str, Mapping[str, float]],
    mom_rows: Mapping[str, Mapping[str, float]],
) -> list[dict[str, float | str]]:
    months = sorted(set(ff5_rows).intersection(mom_rows))
    aligned: list[dict[str, float | str]] = []
    for month in months:
        ff = ff5_rows[month]
        mom = mom_rows[month]
        aligned.append(
            {
                "month": month,
                "MKT_RF": float(ff["Mkt-RF"]),
                "SMB": float(ff["SMB"]),
                "HML": float(ff["HML"]),
                "RMW": float(ff["RMW"]),
                "CMA": float(ff["CMA"]),
                "MOM": float(mom["Mom"]),
            }
        )
    if len(aligned) < WINDOW_MONTHS:
        raise ValueError(f"Need at least {WINDOW_MONTHS} aligned months; got {len(aligned)}")
    return aligned


def rolling_annualized_premium(values: Sequence[float], window: int = WINDOW_MONTHS) -> list[float]:
    """Trailing geometrically annualised factor premium, in percent per year.

    Each monthly factor return is compounded inside the trailing window and
    then annualised. This is the convention that reproduces the drought lengths
    shown in the reference chart much more closely than arithmetic mean × 12.
    """

    if window <= 0:
        raise ValueError("window must be positive")
    if len(values) < window:
        return []

    log_growth = []
    for value in values:
        growth = 1.0 + value / 100.0
        if growth <= 0 or not math.isfinite(growth):
            raise ValueError(f"Invalid monthly factor return for compounding: {value}")
        log_growth.append(math.log(growth))

    prefix = [0.0]
    for value in log_growth:
        prefix.append(prefix[-1] + value)

    result = []
    for index in range(window - 1, len(values)):
        window_log_growth = prefix[index + 1] - prefix[index + 1 - window]
        annual_log_growth = window_log_growth * (12.0 / window)
        result.append((math.exp(annual_log_growth) - 1.0) * 100.0)
    return result


def linear_slope(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean_x = (len(values) - 1) / 2.0
    mean_y = statistics.fmean(values)
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    denominator = sum((index - mean_x) ** 2 for index in range(len(values)))
    return numerator / denominator if denominator else None


def droughts(series: Sequence[float]) -> list[Drought]:
    runs: list[Drought] = []
    start: int | None = None
    for index, value in enumerate(series):
        if value < 0 and start is None:
            start = index
        elif value >= 0 and start is not None:
            end = index - 1
            runs.append(Drought(start, end, end - start + 1, False))
            start = None
    if start is not None:
        end = len(series) - 1
        runs.append(Drought(start, end, end - start + 1, True))
    return runs


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _delta(series: Sequence[float], offset: int) -> float | None:
    if len(series) <= offset:
        return None
    return series[-1] - series[-1 - offset]


def _years_months(months: int) -> str:
    years, remainder = divmod(max(0, months), 12)
    if years and remainder:
        return f"{years}y {remainder}m"
    if years:
        return f"{years}y"
    return f"{remainder}m"


def _drought_payload(run: Drought | None, months: Sequence[str]) -> dict[str, object]:
    if run is None:
        return {
            "active": False,
            "startMonth": None,
            "endMonth": None,
            "months": 0,
            "duration": "0m",
            "ongoing": False,
        }
    return {
        "active": True,
        "startMonth": months[run.start_index],
        "endMonth": months[run.end_index],
        "months": run.months,
        "duration": _years_months(run.months),
        "ongoing": run.ongoing,
    }


def _regime(latest: float, delta12: float | None) -> str:
    improving = delta12 is not None and delta12 >= 0
    if latest >= 0:
        return "STRONG" if improving else "DETERIORATING"
    return "RECOVERY" if improving else "DEEPENING_DROUGHT"


def build_payload(
    aligned: Sequence[Mapping[str, float | str]],
    *,
    generated_at: str | None = None,
) -> dict[str, object]:
    months = [str(row["month"]) for row in aligned]
    rolling_months = months[WINDOW_MONTHS - 1 :]
    factors: list[dict[str, object]] = []
    all_rolling_values: list[float] = []

    for factor_id, source_code, label in FACTOR_SPECS:
        monthly_values = [float(row[factor_id]) for row in aligned]
        rolling = rolling_annualized_premium(monthly_values)
        all_rolling_values.extend(rolling)
        runs = droughts(rolling)
        current_run = runs[-1] if runs and runs[-1].ongoing else None
        longest_run = max(runs, key=lambda run: run.months) if runs else None
        latest = rolling[-1]
        delta1 = _delta(rolling, 1)
        delta6 = _delta(rolling, 6)
        delta12 = _delta(rolling, 12)
        slope12 = linear_slope(rolling[-12:])
        recent12 = rolling_annualized_premium(monthly_values[-12:], window=12)[-1] if len(monthly_values) >= 12 else None
        percentile = 100.0 * sum(value <= latest for value in rolling) / len(rolling)

        factors.append(
            {
                "id": factor_id,
                "sourceCode": source_code,
                "label": label,
                "latest": {
                    "month": rolling_months[-1],
                    "premiumPct": _round(latest),
                    "delta1mPp": _round(delta1),
                    "delta6mPp": _round(delta6),
                    "delta12mPp": _round(delta12),
                    "slope12mPpPerMonth": _round(slope12, 4),
                    "recent12mPremiumPct": _round(recent12),
                    "historicalPercentile": _round(percentile, 1),
                    "regime": _regime(latest, delta12),
                },
                "currentDrought": _drought_payload(current_run, rolling_months),
                "longestDrought": _drought_payload(longest_run, rolling_months),
                "series": [
                    {"month": month, "premiumPct": _round(value)}
                    for month, value in zip(rolling_months, rolling)
                ],
            }
        )

    generated = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    common_min = min(all_rolling_values)
    common_max = max(all_rolling_values)
    span = max(1.0, common_max - common_min)
    pad = span * 0.06

    improving = sorted(
        factors,
        key=lambda factor: float((factor["latest"] or {}).get("delta12mPp") or -9999),  # type: ignore[union-attr]
        reverse=True,
    )

    return {
        "schemaVersion": 1,
        "generatedAt": generated,
        "source": {
            "provider": "Kenneth R. French Data Library",
            "ff5Url": FF5_URL,
            "momentumUrl": MOM_URL,
        },
        "method": {
            "windowMonths": WINDOW_MONTHS,
            "annualization": "geometric annualisation of compounded trailing monthly factor returns",
            "droughtDefinition": "trailing 10-year annualised premium < 0%",
            "deltaDefinition": "change in the trailing 10-year annualised premium, percentage points",
            "stockScoutImpact": "none; read-only independent macro/factor module",
        },
        "range": {
            "firstMonth": months[0],
            "lastMonth": months[-1],
            "rollingFirstMonth": rolling_months[0],
            "alignedMonths": len(months),
        },
        "commonScale": {
            "minPct": _round(common_min - pad),
            "maxPct": _round(common_max + pad),
        },
        "summary": {
            "mostImproving12m": [factor["id"] for factor in improving[:3]],
            "activeDroughts": sum(bool(factor["currentDrought"]["active"]) for factor in factors),  # type: ignore[index]
        },
        "factors": factors,
    }


def load_sources(ff5_text: str | None = None, mom_text: str | None = None) -> list[dict[str, float | str]]:
    ff5 = ff5_text if ff5_text is not None else fetch_factor_text(FF5_URL)
    mom = mom_text if mom_text is not None else fetch_factor_text(MOM_URL)
    ff5_rows = parse_monthly_rows(ff5, ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"))
    mom_rows = parse_monthly_rows(mom, ("Mom",))
    return align_factors(ff5_rows, mom_rows)


def write_payload(payload: Mapping[str, object], output: Path = DEFAULT_OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(output)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ff5-file", type=Path, default=None, help="Optional local French FF5 CSV/TXT")
    parser.add_argument("--mom-file", type=Path, default=None, help="Optional local French momentum CSV/TXT")
    args = parser.parse_args(list(argv) if argv is not None else None)

    ff5_text = args.ff5_file.read_text(encoding="utf-8-sig") if args.ff5_file else None
    mom_text = args.mom_file.read_text(encoding="utf-8-sig") if args.mom_file else None
    aligned = load_sources(ff5_text, mom_text)
    payload = build_payload(aligned)
    write_payload(payload, args.output)

    print(
        f"Factor regime: {payload['range']['firstMonth']} -> {payload['range']['lastMonth']} | "  # type: ignore[index]
        f"{len(payload['factors'])} factors | output={args.output}"  # type: ignore[arg-type]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
