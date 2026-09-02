"""Resolve a bo3 in-game clan tag to one of a match's two teams.

bo3 reports map scores as winner/loser with **clan names** — the tag players
set in-game — not registered team names. "Diamant" vs "Diamant Esports",
"NAVI" vs "Natus Vincere", and sometimes something unrelated entirely because
a stand-in set the tag.

This is an easier problem than fixture resolution: the candidate pool is
exactly two teams, both already known. It is still worth doing carefully,
because getting it backwards silently inverts a map result — the row looks
perfectly well-formed and says the wrong team won.

Two signals, not one. The winner tag and the loser tag are scored together
across both orientations, so a clear loser tag rescues an ambiguous winner
tag. Below the floor we store NULL and mark the row `unresolved` rather than
guessing; the raw strings are always kept.

Pure functions — no I/O.
"""
from __future__ import annotations

from .fixtures import best_similarity, normalise

# Minimum combined orientation score to bind a winner.
CLAN_FLOOR = 0.55
# Minimum gap between the two orientations. Without this, a match between two
# similarly-named sides ("BIG" vs "BIG Academy") would bind on noise.
CLAN_MARGIN = 0.10


def assign(winner_clan: str | None, loser_clan: str | None,
           team_a: tuple, team_b: tuple) -> dict:
    """Decide which side won a map.

    team_a / team_b are (id, name, acronym) tuples.

    Returns {"winner_team_id", "loser_team_id", "confidence", "score"} where
    confidence is 'exact' | 'fuzzy' | 'unresolved'. On 'unresolved' both ids
    are None -- callers must store NULL, never a fallback guess.
    """
    a_id, *a_alias = team_a
    b_id, *b_alias = team_b

    # Exact normalised equality on the winner tag is decisive -- but ONLY if
    # it matches one side and not the other. Checking team A first and
    # returning would bind "BIG" to the parent club in a BIG vs BIG Academy
    # game purely because of argument order.
    nw = normalise(winner_clan)
    if nw:
        hit_a = any(alias and normalise(alias) == nw for alias in a_alias)
        hit_b = any(alias and normalise(alias) == nw for alias in b_alias)
        if hit_a and not hit_b:
            return _out(a_id, b_id, "exact", 1.0)
        if hit_b and not hit_a:
            return _out(b_id, a_id, "exact", 1.0)

    # Score both orientations using both tags.
    wa = best_similarity(winner_clan or "", *a_alias)
    wb = best_similarity(winner_clan or "", *b_alias)
    la = best_similarity(loser_clan or "", *a_alias)
    lb = best_similarity(loser_clan or "", *b_alias)

    # MAX, not mean. Either tag alone identifies the orientation, so a clean
    # loser tag beside a junk winner tag is a solved map -- and averaging
    # caps that case at 0.5, below any floor worth having. Averaging made two
    # signals strictly worse than one; the margin rule below is what guards
    # against noise instead.
    a_won = max(wa, lb)        # winner tag is team A, loser tag is team B
    b_won = max(wb, la)

    best, other = max(a_won, b_won), min(a_won, b_won)
    if best < CLAN_FLOOR or (best - other) < CLAN_MARGIN:
        return _out(None, None, "unresolved", round(best, 4))

    if a_won >= b_won:
        return _out(a_id, b_id, "fuzzy", round(a_won, 4))
    return _out(b_id, a_id, "fuzzy", round(b_won, 4))


def _out(winner, loser, confidence, score) -> dict:
    return {"winner_team_id": winner, "loser_team_id": loser,
            "confidence": confidence, "score": score}


def rounds_for(winner_score, loser_score, winner_team_id,
               team_a_id) -> tuple:
    """Map winner/loser scores onto (team_a_rounds, team_b_rounds).

    Returns (None, None) when the winner is unresolved -- an unresolved map
    must not contribute round differential to either side.
    """
    if winner_team_id is None:
        return (None, None)
    if winner_team_id == team_a_id:
        return (winner_score, loser_score)
    return (loser_score, winner_score)
