"""Map-level statistics from match_maps rows.

Expected row shape (match_maps joined to matches for the date):
    match_id, map_name, team_a_rounds, team_b_rounds, winner_team_id,
    is_default, side_assignment, scheduled_at, team_a_id, team_b_id

Rows whose winner we could not bind carry winner_team_id IS NULL and
contribute nothing. That is deliberate: a silently mis-assigned 13-7 is
worse than a missing row, because it looks like evidence.
"""
from __future__ import annotations

from .core import Stat, n_eff, rate, staleness

POOL_MAP_WIN_RATE = 0.5
POOL_ROUND_WIN_PCT = 0.5


def _usable(rows) -> list[dict]:
    """Rows with a bound winner and no forfeit."""
    return [r for r in rows
            if r.get("winner_team_id") is not None
            and not r.get("is_default")]


def rounds_for(row, team_id) -> tuple[int, int] | None:
    """(rounds_won, rounds_lost) for team_id on this map."""
    a, b = row.get("team_a_rounds"), row.get("team_b_rounds")
    if a is None or b is None:
        return None
    if row.get("team_a_id") == team_id:
        return a, b
    if row.get("team_b_id") == team_id:
        return b, a
    return None


def map_win_rate(rows, team_id, map_name: str | None = None,
                 shrunk: bool = True, now=None) -> Stat:
    """Win rate across maps, optionally filtered to one map."""
    usable = _usable(rows)
    if map_name is not None:
        usable = [r for r in usable if r.get("map_name") == map_name]
    if not usable:
        return Stat.unavailable(
            f"no resolved maps{f' on {map_name}' if map_name else ''}")
    w = sum(1 for r in usable if r.get("winner_team_id") == team_id)
    return rate(w, len(usable) - w, usable,
                POOL_MAP_WIN_RATE if shrunk else None, now=now)


def round_win_pct(rows, team_id, map_name: str | None = None,
                  shrunk: bool = True, now=None) -> Stat:
    """Σ rounds won / Σ rounds played.

    A better skill estimate than map win rate at small n, because every map
    contributes ~25 rounds instead of one binary outcome -- the same number
    of matches carries far more signal.
    """
    usable = _usable(rows)
    if map_name is not None:
        usable = [r for r in usable if r.get("map_name") == map_name]

    won = lost = 0
    counted = []
    for r in usable:
        rl = rounds_for(r, team_id)
        if rl is None:
            continue
        won += rl[0]
        lost += rl[1]
        counted.append(r)

    if not counted:
        return Stat.unavailable("no maps with round scores")

    total = won + lost
    value = (won / total if not shrunk
             else (won + 10 * POOL_ROUND_WIN_PCT * 25) / (total + 10 * 25))
    return Stat(
        value=round(value, 4),
        n=len(counted),
        n_eff=n_eff(counted, now=now),
        staleness_days=staleness(counted, now=now),
        raw=f"{won}-{lost} rounds over {len(counted)} maps",
        shrunk=shrunk,
    )


def avg_round_diff(rows, team_id, map_name: str | None = None,
                   now=None) -> Stat:
    """Mean (rounds won − rounds lost) per map.

    Never shrunk: it is already an average over many rounds rather than a
    proportion, so the small-sample pathology shrinkage exists to fix does
    not apply in the same way. Positive means winning maps comfortably.
    """
    usable = _usable(rows)
    if map_name is not None:
        usable = [r for r in usable if r.get("map_name") == map_name]

    diffs, counted = [], []
    for r in usable:
        rl = rounds_for(r, team_id)
        if rl is None:
            continue
        diffs.append(rl[0] - rl[1])
        counted.append(r)

    if not diffs:
        return Stat.unavailable("no maps with round scores")
    mean = sum(diffs) / len(diffs)
    return Stat(
        value=round(mean, 2),
        n=len(diffs),
        n_eff=n_eff(counted, now=now),
        staleness_days=staleness(counted, now=now),
        raw=f"{mean:+.1f} per map over {len(diffs)}",
    )


def map_pool(rows, team_id, min_maps: int = 2, now=None) -> list[dict]:
    """Per-map record, most-played first.

    `min_maps` hides maps with too little evidence from the headline view.
    They are not deleted -- the caller can lower the threshold -- but a
    100% record on one map should not sit beside a 60% record on twenty as
    if they were comparable claims.
    """
    by_map: dict[str, list[dict]] = {}
    for r in _usable(rows):
        name = r.get("map_name")
        if name:
            by_map.setdefault(name, []).append(r)

    out = []
    for name, maps in by_map.items():
        if len(maps) < min_maps:
            continue
        out.append({
            "map": name,
            "played": len(maps),
            "win_rate": map_win_rate(maps, team_id, now=now),
            "round_win_pct": round_win_pct(maps, team_id, now=now),
            "round_diff": avg_round_diff(maps, team_id, now=now),
        })
    return sorted(out, key=lambda m: (-m["played"], m["map"]))


def ct_t_split(rows, team_id) -> Stat:
    """Always unavailable. bo3 exposes no half-time or side data anywhere.

    Kept as an explicit, named gap rather than omitted, so that nobody
    re-derives the question later and quietly ships a fabricated answer.
    """
    return Stat.unavailable("bo3 exposes no CT/T side data — not computable")
