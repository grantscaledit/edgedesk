"""Team-level statistics from bo3 match rows.

Pure functions. Every row is a dict; every return is a `Stat`.

Expected row shape (from the matches table, one row per match):
    scheduled_at, status, winner_team_id, decided_by_default,
    team_a_id, team_b_id
"""
from __future__ import annotations

from .core import Stat, age_days, n_eff, rate, staleness

# Pool mean for shrinkage. 0.5 because a match has exactly two sides, so
# across all teams the win rate is 0.5 by construction -- no estimation
# needed, and no risk of the prior drifting with the sample.
POOL_WIN_RATE = 0.5

# Forfeits are rare, so the prior is low rather than 0.5. Measured across
# the current board this sits near 3%; revisit if that moves materially.
POOL_FORFEIT_RATE = 0.03


def _played(rows) -> list[dict]:
    return [r for r in rows if r.get("status") == "finished"]


def won(row, team_id) -> bool | None:
    w = row.get("winner_team_id")
    return None if w is None else w == team_id


def win_rate(rows, team_id, shrunk: bool = True, now=None) -> Stat:
    """Match win rate. Forfeits are EXCLUDED from the denominator.

    A walkover says nothing about how a team plays, and Kalshi resolves a
    pre-game forfeit to Fair Market Price rather than to a loss, so counting
    it as a defeat would misstate both the skill estimate and the thing the
    market is actually pricing. Forfeit propensity is its own statistic.
    """
    played = [r for r in _played(rows) if not r.get("decided_by_default")]
    w = sum(1 for r in played if won(r, team_id) is True)
    l = sum(1 for r in played if won(r, team_id) is False)
    return rate(w, l, played, POOL_WIN_RATE if shrunk else None, now=now)


def forfeit_rate(rows, team_id, shrunk: bool = True, now=None) -> Stat:
    """Share of completed matches decided by default.

    The single highest-value risk signal available, and bo3 is the only
    source exposing it cleanly. Note what it measures: under Kalshi's rules
    a pre-game forfeit resolves to Fair Market Price, so this is
    position-unwind risk, not loss risk.
    """
    played = _played(rows)
    if not played:
        return Stat.unavailable("no completed matches in window")
    ff = sum(1 for r in played if r.get("decided_by_default"))
    return rate(ff, len(played) - ff, played,
                POOL_FORFEIT_RATE if shrunk else None, now=now)


def form(rows, team_id, last: int = 5, now=None) -> Stat:
    """Win rate over the most recent `last` completed matches.

    Never shrunk -- form is deliberately a small-sample figure, and pulling
    it toward the pool mean would erase the only thing it measures. It
    carries n so the reader can see it is thin by design.
    """
    played = [r for r in _played(rows) if not r.get("decided_by_default")]
    played = sorted(played, key=lambda r: r.get("scheduled_at") or 0,
                    reverse=True)[:last]
    if not played:
        return Stat.unavailable("no completed matches in window")
    w = sum(1 for r in played if won(r, team_id) is True)
    return rate(w, len(played) - w, played, None, now=now)


def form_string(rows, team_id, last: int = 5) -> str:
    """'WWLWL', most recent first. '-' for a forfeit."""
    played = sorted(_played(rows), key=lambda r: r.get("scheduled_at") or 0,
                    reverse=True)[:last]
    out = []
    for r in played:
        if r.get("decided_by_default"):
            out.append("-")
        else:
            w = won(r, team_id)
            out.append("?" if w is None else ("W" if w else "L"))
    return "".join(out)


def matches_in_window(rows, days: float, now=None, future: bool = False) -> int:
    """Count of matches within `days` behind (or ahead of) now."""
    count = 0
    for r in rows:
        age = age_days(r.get("scheduled_at"), now)
        if age is None:
            continue
        if future and -days <= age <= 0:
            count += 1
        elif not future and 0 <= age <= days:
            count += 1
    return count


def fatigue(rows, now=None) -> Stat:
    """Matches played in the previous 48 hours.

    Reported as a count, not a rate -- there is no denominator that means
    anything here, and inventing one would imply a precision this does not
    have.
    """
    played = _played(rows)
    recent = matches_in_window(played, 2.0, now)
    return Stat(value=float(recent), n=len(played),
                n_eff=n_eff(played, now=now),
                staleness_days=staleness(played, now=now),
                raw=f"{recent} in 48h")


def no_show_risk(rows, team_id, concurrent_tournaments: int = 1,
                 roster_changes_30d: int | None = None, now=None) -> Stat:
    """clamp(forfeit_rate + 0.02·churn + 0.01·(concurrent−1) + 0.01·next_48h,
    0, 0.40)

    `roster_changes_30d` is None when roster history has not been captured
    for this team yet -- bo3 publishes today's lineup and no past, so the
    history only accrues from the day sync_players starts running. When it
    is None the term is omitted AND the note says so, because a risk score
    that hides which of its inputs are missing is worse than no score.
    """
    ff = forfeit_rate(rows, team_id, shrunk=True, now=now)
    if ff.value is None:
        return Stat.unavailable("no completed matches to estimate forfeit rate")
    upcoming = matches_in_window(rows, 2.0, now, future=True)
    churn = 0.0 if roster_changes_30d is None else 0.02 * roster_changes_30d
    score = (ff.value + churn
             + 0.01 * max(0, concurrent_tournaments - 1)
             + 0.01 * upcoming)
    raw = f"ff {ff.raw}, {upcoming} upcoming in 48h"
    note = None
    if roster_changes_30d is None:
        note = "excludes roster churn — no roster history captured yet"
    else:
        raw += f", {roster_changes_30d} roster change(s) in 30d"
    return Stat(
        value=round(min(max(score, 0.0), 0.40), 4),
        n=ff.n, n_eff=ff.n_eff, staleness_days=ff.staleness_days,
        raw=raw, note=note,
    )
