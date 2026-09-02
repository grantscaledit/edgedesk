"""Head-to-head between two teams.

H2H is the statistic most likely to be over-read. Two teams at this level
meet three or four times a year, so a 3-1 record is both the most
emotionally compelling number on a dossier and one of the weakest pieces of
evidence on it. Everything here is built to keep that visible: the raw
record leads, n_eff shows how much of it is stale, and the roster caveat is
attached to the value rather than left to the reader.
"""
from __future__ import annotations

from .core import Stat, n_eff, rate, staleness
from .maps import _usable, rounds_for


def meetings(match_rows, team_a: int, team_b: int) -> list[dict]:
    """Completed matches between exactly these two teams."""
    pair = {team_a, team_b}
    return [r for r in match_rows
            if r.get("status") == "finished"
            and {r.get("team_a_id"), r.get("team_b_id")} == pair]


def record(match_rows, team_a: int, team_b: int, now=None) -> Stat:
    """team_a's record against team_b. Forfeits excluded.

    Never shrunk. Shrinking H2H toward 0.5 would erase the entire signal at
    the sample sizes it actually occurs at, leaving a number that says
    nothing while looking like it says something.
    """
    played = [r for r in meetings(match_rows, team_a, team_b)
              if not r.get("decided_by_default")]
    if not played:
        return Stat.unavailable("these teams have not met in the window")
    w = sum(1 for r in played if r.get("winner_team_id") == team_a)
    stat = rate(w, len(played) - w, played, None, now=now)
    return Stat(
        value=stat.value, n=stat.n, n_eff=stat.n_eff,
        staleness_days=stat.staleness_days, raw=stat.raw, shrunk=False,
        note=("rosters at the time of these meetings are unknown — "
              "treat as weak evidence"),
    )


def map_record(map_rows, team_a: int, team_b: int, now=None) -> list[dict]:
    """Per-map head-to-head, most-played first."""
    pair = {team_a, team_b}
    rows = [r for r in _usable(map_rows)
            if {r.get("team_a_id"), r.get("team_b_id")} == pair]
    by_map: dict[str, list[dict]] = {}
    for r in rows:
        name = r.get("map_name")
        if name:
            by_map.setdefault(name, []).append(r)

    out = []
    for name, maps in by_map.items():
        wins = sum(1 for r in maps if r.get("winner_team_id") == team_a)
        diffs = []
        for r in maps:
            rl = rounds_for(r, team_a)
            if rl:
                diffs.append(rl[0] - rl[1])
        out.append({
            "map": name,
            "played": len(maps),
            "record": f"{wins}-{len(maps) - wins}",
            "avg_round_diff": round(sum(diffs) / len(diffs), 2) if diffs else None,
            "n_eff": n_eff(maps, now=now),
            "staleness_days": staleness(maps, now=now),
        })
    return sorted(out, key=lambda m: (-m["played"], m["map"]))


def common_opponents(match_rows, team_a: int, team_b: int,
                     now=None) -> dict:
    """Records against teams both sides have played.

    Weak evidence, and labelled as such — transitivity does not hold in
    esports, and a shared opponent may have fielded different players
    against each side. It earns its place only because two teams that have
    never met leave nothing else to compare, and an explicit weak signal
    beats an implicit guess.
    """
    def opponents(team):
        out = {}
        for r in match_rows:
            if r.get("status") != "finished" or r.get("decided_by_default"):
                continue
            a, b = r.get("team_a_id"), r.get("team_b_id")
            if a == team:
                opp = b
            elif b == team:
                opp = a
            else:
                continue
            if opp is None:
                continue
            out.setdefault(opp, []).append(r)
        return out

    oa, ob = opponents(team_a), opponents(team_b)
    shared = sorted(set(oa) & set(ob))
    rows = []
    for opp in shared:
        def rec(team, matches):
            w = sum(1 for r in matches if r.get("winner_team_id") == team)
            return f"{w}-{len(matches) - w}"
        rows.append({
            "opponent_id": opp,
            "team_a_record": rec(team_a, oa[opp]),
            "team_b_record": rec(team_b, ob[opp]),
            "n": len(oa[opp]) + len(ob[opp]),
        })
    return {
        "count": len(shared),
        "opponents": sorted(rows, key=lambda r: -r["n"]),
        "note": "weak evidence — transitivity does not hold, and rosters "
                "may differ between the two meetings",
    }
