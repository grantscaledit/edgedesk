"""Head-to-head. The tests here mostly guard against over-reading."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edgedesk.stats.h2h import common_opponents, map_record, meetings, record

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
A, B, C, D = 1, 2, 3, 4


def match(days_ago, a, b, winner, defwin=False, status="finished"):
    return {"scheduled_at": NOW - timedelta(days=days_ago), "status": status,
            "winner_team_id": winner, "decided_by_default": defwin,
            "team_a_id": a, "team_b_id": b}


def mp(days_ago, a, b, name, ar, br, winner):
    return {"scheduled_at": NOW - timedelta(days=days_ago), "map_name": name,
            "team_a_rounds": ar, "team_b_rounds": br,
            "winner_team_id": winner, "is_default": False,
            "team_a_id": a, "team_b_id": b}


def test_meetings_finds_the_pair_in_either_orientation():
    rows = [match(1, A, B, A), match(2, B, A, A), match(3, A, C, A)]
    assert len(meetings(rows, A, B)) == 2


def test_record_counts_from_the_first_team_perspective():
    rows = [match(1, A, B, A), match(2, B, A, A), match(3, A, B, B)]
    assert record(rows, A, B, now=NOW).raw == "2-1"
    assert record(rows, B, A, now=NOW).raw == "1-2"


def test_record_excludes_forfeits():
    rows = [match(1, A, B, A), match(2, A, B, A, defwin=True)]
    assert record(rows, A, B, now=NOW).n == 1


def test_record_is_never_shrunk():
    """At three or four meetings a year, shrinking toward 0.5 would erase
    the entire signal while still looking like a number."""
    rows = [match(1, A, B, A), match(2, A, B, A), match(3, A, B, A)]
    s = record(rows, A, B, now=NOW)
    assert s.shrunk is False
    assert s.value == 1.0


def test_record_attaches_the_roster_caveat_to_the_value():
    """The caveat travels with the number rather than being left to the
    reader, because this is the figure most likely to be over-read."""
    rows = [match(1, A, B, A)]
    assert "rosters" in record(rows, A, B, now=NOW).note


def test_record_with_no_meetings_is_unavailable():
    s = record([match(1, A, C, A)], A, B, now=NOW)
    assert s.value is None
    assert "have not met" in s.note


def test_map_record_reports_per_map_and_orients_diff():
    rows = [mp(1, A, B, "Mirage", 13, 7, A), mp(2, B, A, "Mirage", 7, 13, A)]
    entry = map_record(rows, A, B, now=NOW)[0]
    assert entry["map"] == "Mirage" and entry["played"] == 2
    assert entry["record"] == "2-0"
    assert entry["avg_round_diff"] == pytest.approx(6.0)


def test_common_opponents_finds_shared_teams_only():
    rows = [match(1, A, C, A), match(2, B, C, C), match(3, A, D, A)]
    out = common_opponents(rows, A, B, now=NOW)
    assert out["count"] == 1
    assert out["opponents"][0]["opponent_id"] == C
    assert out["opponents"][0]["team_a_record"] == "1-0"
    assert out["opponents"][0]["team_b_record"] == "0-1"


def test_common_opponents_is_labelled_weak_evidence():
    """Transitivity does not hold in esports. The label is the point."""
    rows = [match(1, A, C, A), match(2, B, C, C)]
    assert "transitivity" in common_opponents(rows, A, B, now=NOW)["note"]


def test_common_opponents_with_none_shared():
    rows = [match(1, A, C, A), match(2, B, D, B)]
    assert common_opponents(rows, A, B, now=NOW)["count"] == 0
