"""GICS sector / industry relative-strength leaderboard.

Deepvue-style "groups" view: rank each sector and industry by the relative
strength of its members, so leadership rotation is visible at a glance and the
top names within a strong group can be drilled into.

Input is the full-universe stage rows (each carries `ticker`, `rs_rating`,
`sector`, `industry`) so the ranking reflects the entire scanned universe, not
just the few names that triggered a setup. RS rating is the universe-relative
IBD-style percentile already computed in the orchestrator.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median, pstdev


def compute_group_rs(
    rows: list[dict],
    level_key: str,
    *,
    min_members: int = 3,
    top_n: int = 10,
) -> list[dict]:
    """Aggregate `rows` by `level_key` (``"sector"`` or ``"industry"``).

    Returns one dict per group with the member count, median/mean RS rating and
    the top `top_n` members by RS, sorted by median RS descending and ranked
    1..N. Groups with fewer than `min_members` valid members are dropped (too
    small to be a meaningful "group RS").
    """
    # Each member is (ticker, rs, sector) — sector lets us tag each *industry*
    # group with its parent sector so the UI can show a few industries per sector.
    buckets: dict[str, list[tuple[str, float, str | None]]] = defaultdict(list)
    for r in rows:
        group = r.get(level_key)
        rs = r.get("rs_rating")
        if not group or rs is None:
            continue
        sector = r.get("sector")
        try:
            buckets[str(group)].append(
                (str(r.get("ticker") or ""), float(rs), str(sector) if sector else None)
            )
        except (TypeError, ValueError):
            continue

    out: list[dict] = []
    for group, members in buckets.items():
        if len(members) < min_members:
            continue
        values = [v for _, v, _ in members]
        members_sorted = sorted(members, key=lambda m: m[1], reverse=True)
        entry = {
            "group": group,
            "level": level_key,
            "count": len(members),
            "median_rs": round(median(values), 1),
            "mean_rs": round(sum(values) / len(values), 1),
            "max_rs": round(max(values), 1),
            # Dispersion of member RS — a tight, high-median group is a
            # cleaner leadership signal than one dragged up by a few names.
            "stdev_rs": round(pstdev(values), 1) if len(values) > 1 else 0.0,
            "top": [
                {"ticker": t, "rs_rating": round(v, 1)}
                for t, v, _ in members_sorted[:top_n]
            ],
        }
        if level_key == "industry":
            # Parent sector = most common sector among the industry's members.
            sectors = [s for _, _, s in members if s]
            entry["sector"] = Counter(sectors).most_common(1)[0][0] if sectors else None
        out.append(entry)

    out.sort(key=lambda g: (g["median_rs"], g["mean_rs"]), reverse=True)
    for rank, group in enumerate(out, start=1):
        group["rank"] = rank
    return out


def build_group_leaderboard(rows: list[dict]) -> dict:
    """Sector + industry leaderboards from full-universe rows."""
    return {
        "sectors": compute_group_rs(rows, "sector", min_members=3),
        "industries": compute_group_rs(rows, "industry", min_members=3),
    }
