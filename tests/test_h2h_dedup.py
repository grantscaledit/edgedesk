"""Regression: head-to-head maps were counted twice."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from edgedesk.stats.h2h import map_record

NOW = datetime.now(timezone.utc)
A, B = 1, 2


def mp(match_id, idx, name, ar, br, winner):
    return {"match_id": match_id, "map_index": idx, "map_name": name,
            "team_a_rounds": ar, "team_b_rounds": br,
            "winner_team_id": winner, "is_default": False,
            "scheduled_at": NOW - timedelta(days=3),
            "team_a_id": A, "team_b_id": B}


def test_map_record_counts_each_map_once():
    """A Bo3 played between these two teams must show at most 3 maps.

    The bug: a map from a match BETWEEN the pair lives in both teams' map
    lists, and the dossier concatenated them. Real output showed 38 maps
    over 11 meetings -- 3.45 per match, above the Bo3 ceiling of 3, which
    is what made it visible.
    """
    rows = [mp(1, 1, "Mirage", 13, 7, A), mp(1, 2, "Nuke", 11, 13, B)]
    out = map_record(rows, A, B)
    assert sum(e["played"] for e in out) == 2


def test_identical_rows_are_not_silently_merged_by_map_record():
    """map_record itself does NOT dedupe -- that is queries' job. This
    pins the boundary so the fix cannot drift to the wrong layer without
    someone noticing."""
    row = mp(1, 1, "Mirage", 13, 7, A)
    assert map_record([row, dict(row)], A, B)[0]["played"] == 2
