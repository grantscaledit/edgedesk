"""Map statistics. The orientation logic is where a silent inversion lives."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edgedesk.stats.maps import (
    avg_round_diff, ct_t_split, map_pool, map_win_rate, round_win_pct,
    rounds_for,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
A, B = 1, 2


def m(days_ago, name, a_rounds, b_rounds, winner, default=False, a=A, b=B):
    return {"scheduled_at": NOW - timedelta(days=days_ago), "map_name": name,
            "team_a_rounds": a_rounds, "team_b_rounds": b_rounds,
            "winner_team_id": winner, "is_default": default,
            "team_a_id": a, "team_b_id": b}


def test_rounds_are_oriented_to_the_team_asked_about():
    row = m(1, "Mirage", 13, 7, A)
    assert rounds_for(row, A) == (13, 7)
    assert rounds_for(row, B) == (7, 13)


def test_rounds_for_a_team_not_in_the_match_is_none():
    assert rounds_for(m(1, "Mirage", 13, 7, A), 999) is None


def test_rounds_missing_scores_is_none():
    assert rounds_for(m(1, "Mirage", None, None, A), A) is None


def test_unresolved_maps_contribute_nothing():
    """A map whose winner we could not bind must not become evidence."""
    rows = [m(1, "Mirage", 13, 7, None), m(2, "Nuke", 13, 5, A)]
    assert map_win_rate(rows, A, shrunk=False, now=NOW).n == 1


def test_forfeited_maps_are_excluded():
    rows = [m(1, "Mirage", 13, 0, A, default=True), m(2, "Nuke", 13, 5, A)]
    assert map_win_rate(rows, A, shrunk=False, now=NOW).n == 1


def test_map_win_rate_filters_to_one_map():
    rows = [m(1, "Mirage", 13, 7, A), m(2, "Mirage", 5, 13, B),
            m(3, "Nuke", 13, 2, A)]
    assert map_win_rate(rows, A, "Mirage", shrunk=False, now=NOW).raw == "1-1"
    assert map_win_rate(rows, A, "Nuke", shrunk=False, now=NOW).raw == "1-0"


def test_map_win_rate_on_an_unplayed_map_is_unavailable():
    s = map_win_rate([m(1, "Mirage", 13, 7, A)], A, "Ancient", now=NOW)
    assert s.value is None and "Ancient" in s.note


def test_round_win_pct_uses_every_round():
    """The reason this beats map win rate at small n: one map carries ~25
    observations instead of one binary outcome."""
    rows = [m(1, "Mirage", 13, 7, A), m(2, "Nuke", 13, 11, A)]
    s = round_win_pct(rows, A, shrunk=False, now=NOW)
    assert s.value == pytest.approx(26 / 44, abs=1e-4)
    assert "26-18 rounds" in s.raw


def test_round_win_pct_is_oriented_correctly_for_the_other_side():
    rows = [m(1, "Mirage", 13, 7, A)]
    assert round_win_pct(rows, B, shrunk=False, now=NOW).value == pytest.approx(0.35, abs=1e-4)


def test_avg_round_diff_signs_correctly():
    rows = [m(1, "Mirage", 13, 7, A), m(2, "Nuke", 13, 3, A)]
    assert avg_round_diff(rows, A, now=NOW).value == pytest.approx(8.0)
    assert avg_round_diff(rows, B, now=NOW).value == pytest.approx(-8.0)


def test_avg_round_diff_is_never_shrunk():
    rows = [m(1, "Mirage", 13, 7, A)]
    assert avg_round_diff(rows, A, now=NOW).shrunk is False


def test_map_pool_hides_maps_below_the_threshold():
    rows = [m(1, "Mirage", 13, 7, A), m(2, "Mirage", 13, 9, A),
            m(3, "Nuke", 13, 2, A)]
    pool = map_pool(rows, A, min_maps=2, now=NOW)
    assert [p["map"] for p in pool] == ["Mirage"]
    assert [p["map"] for p in map_pool(rows, A, min_maps=1, now=NOW)] == \
        ["Mirage", "Nuke"]


def test_map_pool_is_ordered_by_games_played():
    rows = ([m(i, "Mirage", 13, 7, A) for i in range(3)]
            + [m(i, "Nuke", 13, 7, A) for i in range(5)])
    assert [p["map"] for p in map_pool(rows, A, now=NOW)] == ["Nuke", "Mirage"]


def test_map_pool_entries_carry_full_stats():
    rows = [m(1, "Mirage", 13, 7, A), m(2, "Mirage", 5, 13, B)]
    entry = map_pool(rows, A, now=NOW)[0]
    assert entry["played"] == 2
    assert entry["win_rate"].n == 2
    assert entry["round_win_pct"].value is not None
    assert entry["round_diff"].value is not None


def test_ct_t_split_is_a_named_permanent_gap():
    """Kept as an explicit gap so nobody re-derives the question later and
    quietly ships a fabricated answer."""
    s = ct_t_split([], A)
    assert s.value is None
    assert "no CT/T side data" in s.note
