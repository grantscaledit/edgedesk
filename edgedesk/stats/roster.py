"""Roster statistics. Pure functions.

bo3 gives today's lineup and no history, so everything here is built on
snapshots this project captures going forward. That means these figures
start out unavailable and get better with time, and they say so rather than
pretending — `Stat.unavailable` with a reason is the correct answer for a
team we have watched for three days.
"""
from __future__ import annotations

from .core import Stat, age_days

# The spec's coefficient for roster churn in no_show_risk.
CHURN_WEIGHT = 0.02


def lineup(rows) -> list[dict]:
    """Current five (or however many) from v_roster_latest rows."""
    return sorted(rows, key=lambda r: (r.get("nickname") or "").lower())


def roster_changes(snapshot_times, days: float = 30.0, now=None) -> int:
    """Distinct roster-change observations in the window.

    Each capture moment is one observed change, because sync_players writes
    a snapshot only when the lineup actually differs from the last stored
    one. The first-ever snapshot for a team is a change from nothing, which
    would overstate churn -- callers pass only subsequent captures.
    """
    count = 0
    for t in snapshot_times:
        age = age_days(t, now)
        if age is not None and 0 <= age <= days:
            count += 1
    return count


def continuity(map_rows, current_player_ids, appearances, now=None) -> Stat:
    """Share of recent maps played by the CURRENT lineup.

    `appearances` maps player_id -> number of maps in the sample. Requires
    per-map participation data, which bo3 does not publish, so this is
    unavailable until an HLTV layer supplies it. Named and returned as an
    explicit gap rather than silently omitted, so nobody later mistakes its
    absence for a value of zero.
    """
    if not appearances:
        return Stat.unavailable(
            "per-map player participation not collected — needs HLTV")
    total = sum(appearances.values())
    if not total:
        return Stat.unavailable("no map appearances recorded")
    by_current = sum(n for pid, n in appearances.items()
                     if pid in set(current_player_ids))
    return Stat(value=round(by_current / total, 4), n=len(appearances),
                n_eff=float(len(appearances)),
                raw=f"{by_current}/{total} maps by the current five")


def churn_term(snapshot_times, days: float = 30.0, now=None) -> float:
    """The 0.02 x roster_changes_30d contribution to no_show_risk."""
    return round(CHURN_WEIGHT * roster_changes(snapshot_times, days, now), 4)


def describe(rows, snapshot_times=None, now=None) -> dict:
    """Everything the dossier needs about one team's roster."""
    players = lineup(rows)
    captured = rows[0].get("captured_at") if rows else None
    return {
        "players": players,
        "size": len(players),
        "captured_at": captured,
        "staleness_days": age_days(captured, now) if captured else None,
        "changes_30d": roster_changes(snapshot_times or [], 30.0, now),
        "complete": len(players) == 5,
    }
