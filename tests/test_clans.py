"""Clan-tag resolution tests.

Getting this backwards silently inverts a map result: the row is well-formed
and says the wrong team won. So, as with fixture resolution, these tests care
more about refusing than about binding.
"""
from __future__ import annotations

from edgedesk.resolve.clans import CLAN_FLOOR, assign, rounds_for

A = (1, "Diamant Esports", "DIA")
B = (2, "UUST", "UUST")


def test_exact_tag_binds_immediately():
    r = assign("UUST", "Diamant Esports", A, B)
    assert r["winner_team_id"] == 2 and r["loser_team_id"] == 1
    assert r["confidence"] == "exact"


def test_shortened_tag_binds_fuzzy():
    """'Diamant' is the in-game tag for 'Diamant Esports'."""
    r = assign("Diamant", "UUST", A, B)
    assert r["winner_team_id"] == 1
    assert r["confidence"] in ("exact", "fuzzy")


def test_acronym_tag_binds():
    r = assign("DIA", "UUST", A, B)
    assert r["winner_team_id"] == 1


def test_loser_tag_rescues_an_ambiguous_winner_tag():
    """The winner set a junk tag; the loser tag is clean. Using both signals
    is what makes this resolvable at all."""
    r = assign("ggwp", "UUST", A, B)
    assert r["winner_team_id"] == 1
    assert r["confidence"] == "fuzzy"


def test_both_tags_junk_refuses_to_guess():
    r = assign("ggwp", "rekt", A, B)
    assert r["winner_team_id"] is None
    assert r["loser_team_id"] is None
    assert r["confidence"] == "unresolved"


def test_missing_tags_refuse_to_guess():
    assert assign(None, None, A, B)["confidence"] == "unresolved"
    assert assign("", "", A, B)["confidence"] == "unresolved"


def test_exact_match_on_one_side_only_still_binds():
    """BIG vs BIG Academy: the tag 'BIG' equals the parent club's registered
    name and does NOT equal 'BIG Academy'. Binding the parent is the best
    inference available, and refusing every parent/academy map would discard
    most of them. The tuple already established these are the two sides."""
    big = (10, "BIG", "BIG")
    acad = (11, "BIG Academy", None)
    r = assign("BIG", "BIG", big, acad)
    assert r["winner_team_id"] == 10
    assert r["confidence"] == "exact"


def test_tag_matching_both_sides_exactly_refuses():
    """The genuine ambiguity: an acronym collision means the tag matches one
    side by name and the other by acronym. Argument order must not decide
    this -- checking team A first and returning would silently invert half
    of these."""
    a = (10, "BIG", None)
    b = (11, "BIG Academy", "BIG")
    r = assign("BIG", "BIG", a, b)
    assert r["winner_team_id"] is None
    assert r["confidence"] == "unresolved"


def test_floor_is_actually_enforced():
    r = assign("Nuclear TigeRES", "MORROW", A, B)
    assert r["confidence"] == "unresolved"
    assert r["score"] < CLAN_FLOOR


# ---------------------------------------------------------------- rounds


def test_rounds_map_onto_team_a_and_b():
    assert rounds_for(13, 7, winner_team_id=1, team_a_id=1) == (13, 7)
    assert rounds_for(13, 7, winner_team_id=2, team_a_id=1) == (7, 13)


def test_unresolved_map_contributes_no_rounds():
    """An unresolved winner must not contribute round differential to either
    side -- a silently mis-assigned 13-7 is worse than a missing row."""
    assert rounds_for(13, 7, winner_team_id=None, team_a_id=1) == (None, None)


# --------------------------------------------------------- assign_side


def test_assign_side_picks_the_matching_team():
    """Filling kalshi_markets.team_id: one Kalshi name, two known teams."""
    from edgedesk.resolve.clans import assign_side
    r = assign_side("UUST", A, B)
    assert r["winner_team_id"] == 2 and r["loser_team_id"] == 1


def test_assign_side_matches_a_shortened_name():
    from edgedesk.resolve.clans import assign_side
    assert assign_side("Diamant", A, B)["winner_team_id"] == 1


def test_assign_side_refuses_an_ambiguous_name():
    """A market bound to the wrong side would invert every price it feeds,
    so an acronym collision must refuse rather than pick by argument order.
    """
    from edgedesk.resolve.clans import assign_side
    a = (10, "BIG", None)
    b = (11, "BIG Academy", "BIG")
    assert assign_side("BIG", a, b)["winner_team_id"] is None


def test_assign_side_refuses_an_unrelated_name():
    from edgedesk.resolve.clans import assign_side
    assert assign_side("Nuclear TigeRES", A, B)["winner_team_id"] is None


def test_assign_side_handles_a_missing_name():
    from edgedesk.resolve.clans import assign_side
    assert assign_side(None, A, B)["winner_team_id"] is None
