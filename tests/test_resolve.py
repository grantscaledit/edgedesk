"""Resolution tests.

This is the module where a wrong answer is most expensive: a bad link
produces a confident dossier about the wrong match. So the tests care more
about NOT binding wrongly than about binding often.
"""
from __future__ import annotations

import pytest

from edgedesk.resolve.fixtures import (
    extract_event_name, normalise, pair_score, resolve, similarity, tokens,
)


def cand(bo3_id, a, b, event="ESEA", a_acr=None, b_acr=None):
    return {"bo3_id": bo3_id, "match_id": bo3_id, "team_a_name": a,
            "team_b_name": b, "event_name": event,
            "team_a_acronym": a_acr, "team_b_acronym": b_acr}


# ---------------------------------------------------------------- normalise


def test_normalise_strips_accents_and_punctuation():
    assert normalise("ex-RUBY") == "ex ruby"
    assert normalise("OLDBOYS-") == "oldboys"
    assert normalise("Grêmio Esports") == "gremio esports"
    assert normalise(None) == ""


def test_tokens_drop_noise_but_never_everything():
    assert tokens("BESTIA Academy") == {"bestia"}
    assert tokens("Wave Esports") == {"wave"}
    # a name that is entirely noise must still yield something
    assert tokens("Team Gaming") == {"team", "gaming"}


# ---------------------------------------------------------------- similarity


def test_identical_names_score_one():
    assert similarity("BESTIA Academy", "BESTIA Academy") == 1.0


def test_suffix_variants_score_high():
    assert similarity("Diamant Esports", "Diamant") >= 0.85
    assert similarity("BESTIA Academy", "BESTIA") >= 0.85
    assert similarity("ex-RUBY", "RUBY") >= 0.80


def test_unrelated_names_score_zero():
    assert similarity("A Great Chaos", "MTX") < 0.15
    assert similarity("Wave Esports", "OLDBOYS-") < 0.15


def test_typo_level_drift_is_recovered():
    """usst/UUST is the case that broke name-only matching. It must score
    well above unrelated names, even if below the auto-accept bar."""
    s = similarity("usst esports", "UUST")
    assert s > 0.40
    assert s > similarity("usst esports", "Fire Flux")


def test_char_ratio_ignores_noise_words():
    """Regression: comparing 'usstesports' to 'uust' scored 0.215. The
    comparison must use meaningful tokens only."""
    assert similarity("usst esports", "UUST") > 0.40


def test_perfect_containment_is_not_diluted_by_char_ratio():
    """Regression (real slate, KXCS2GAME-26SEP021330LFOUKBG).

    'LFO UKRAINE' vs 'LFO' has perfect token containment but a poor
    character ratio (0.364) because one string is three times longer. The
    old blend let that weak signal veto a certain match, scoring 0.774 and
    queueing a correct pair. Containment of 1.0 is decisive on its own.
    """
    assert similarity("LFO UKRAINE", "LFO") >= 0.90
    assert similarity("OLDBOYS-", "OLDBOYS PL") >= 0.90


def test_suffixed_org_names_match_by_substring():
    """Regression (real slate, KXCS2GAME-26SEP021330RGGOLD).

    'RoundsGG' vs 'ROUNDS' shares no whole token, so token overlap is 0.
    Orgs routinely append GG / PL / a country tag. Scored 0.583 before.
    """
    assert similarity("RoundsGG", "ROUNDS") >= 0.90


def test_substring_rule_is_length_guarded():
    """The substring rule must not let a short fragment match inside an
    unrelated longer name, and must not fire below the length ratio."""
    assert similarity("NIP", "Sniper") < 0.55
    assert similarity("HAVU", "Noir Verse") < 0.15
    assert similarity("Color", "Nuclear TigeRES") < 0.15


def test_real_slate_pairs_that_were_wrongly_queued_now_accept():
    """End-to-end on the two events that sat in resolution_queue."""
    r = resolve("OLDBOYS-", "RoundsGG", None,
                [cand(1, "OLDBOYS PL", "ROUNDS")])
    assert r["verdict"] == "accept", r["reason"]

    r = resolve("LFO UKRAINE", "Banger Gang", None,
                [cand(2, "Banger Gang", "LFO")])
    assert r["verdict"] == "accept", r["reason"]
    assert r["best"]["swapped"] is True


def test_academy_teams_still_score_high_against_parent():
    """'Academy' is a noise token by design, so BIG/BIG Academy scores 1.0.
    That is intended — the fixture tuple (opponent + start time) is what
    separates a parent team from its academy side, not the name."""
    assert similarity("BIG", "BIG Academy") >= 0.90
    assert similarity("paiN", "paiN Academy") >= 0.90


def test_pair_score_tries_both_orientations():
    direct, swapped = pair_score("A", "B", "A", "B")
    assert direct == 1.0 and swapped is False
    direct, swapped = pair_score("A", "B", "B", "A")
    assert direct == 1.0 and swapped is True


# ---------------------------------------------------------------- resolve


def test_exact_pair_auto_accepts():
    r = resolve("BESTIA Academy", "underw0rld", "Gamers Club Liga Serie A 2026",
                [cand(1, "BESTIA Academy", "underw0rld", "Gamers Club Liga Serie A 2026")])
    assert r["verdict"] == "accept"
    assert r["best"]["score"] >= 0.85


def test_reversed_order_still_accepts():
    r = resolve("underw0rld", "BESTIA Academy", None,
                [cand(1, "BESTIA Academy", "underw0rld")])
    assert r["verdict"] == "accept"
    assert r["best"]["swapped"] is True


def test_sole_plausible_candidate_accepts():
    """usst/UUST: the tight time window has already filtered heavily."""
    r = resolve("Diamant Esports", "usst esports", "ESEA Advanced Europe",
                [cand(1, "UUST", "Diamant", "ESEA Season 58")])
    assert r["verdict"] == "accept"
    assert r["reason"] == "sole candidate in window"


def test_sole_but_unrelated_candidate_must_queue():
    """The floor is what stops an unrelated match in the same window from
    being adopted just because it was alone."""
    r = resolve("BESTIA Academy", "underw0rld", "Gamers Club",
                [cand(1, "Fire Flux", "NoTime")])
    assert r["verdict"] == "queue"
    assert r["best"]["team_score"] < 0.55


def test_ambiguous_multiple_candidates_refuse_to_guess():
    r = resolve("Diamant Esports", "usst esports", "ESEA",
                [cand(1, "UUST", "Diamant"), cand(2, "Fire Flux", "NoTime")])
    assert r["verdict"] == "queue"


def test_no_candidates_queues_with_reason():
    r = resolve("A", "B", None, [])
    assert r["verdict"] == "queue"
    assert r["best"] is None
    assert "no candidates" in r["reason"]


def test_acronym_lifts_a_name_only_mismatch():
    """'Natus Vincere' vs 'NAVI' is unreachable on names; the acronym field
    is what makes it scoreable."""
    with_acr = resolve("NAVI", "TheMongolz", None,
                       [cand(1, "Natus Vincere", "TheMongolz",
                             a_acr="NAVI", b_acr="MGLZ")])
    without = resolve("NAVI", "TheMongolz", None,
                      [cand(1, "Natus Vincere", "TheMongolz")])
    assert with_acr["best"]["team_score"] > without["best"]["team_score"]


def test_ranked_output_is_sorted_and_capped():
    cands = [cand(i, f"Team{i}", f"Other{i}") for i in range(10)]
    cands.append(cand(99, "BESTIA Academy", "underw0rld"))
    r = resolve("BESTIA Academy", "underw0rld", None, cands)
    assert r["ranked"][0]["bo3_id"] == 99
    assert len(r["ranked"]) <= 5
    scores = [c["score"] for c in r["ranked"]]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------- event name


def test_extract_event_name_from_kalshi_rules():
    rules = ("If BESTIA Academy wins the Gamers Club Liga Serie A 2026: BESTIA "
             "Academy vs. underw0rld CS2 match originally scheduled for Aug 25, "
             "2026 at 5:00 PM EDT, then the market resolves to Yes.")
    assert extract_event_name(rules) == "Gamers Club Liga Serie A 2026"


def test_extract_event_name_handles_missing():
    assert extract_event_name(None) is None
    assert extract_event_name("no colon here") is None


@pytest.mark.parametrize("rules,expected", [
    ("If X wins the ESL Challenger League Europe Cup #5 2026: X vs. Y CS2 match",
     "ESL Challenger League Europe Cup #5 2026"),
    ("If A wins the CCT 2026 South America Series 5: A vs. B CS2 match",
     "CCT 2026 South America Series 5"),
])
def test_extract_event_name_across_real_formats(rules, expected):
    assert extract_event_name(rules) == expected


# ---------------------------------------------------------------- aliases


def test_harvested_aliases_rescue_a_team_with_no_acronym():
    """bo3 gives no acronym for 168 of 323 teams. An observed clan tag is
    then the only second alias there is, and is what makes the pair
    reachable at all -- 'MTZ' scores 0.0 against 'Metizport' on names."""
    plain = cand(1, "Metizport", "Sangal Esports")
    without = resolve("MTZ", "Sangal", None, [plain])
    assert without["verdict"] == "queue"

    c = dict(plain, team_a_aliases=["MTZ"])
    with_alias = resolve("MTZ", "Sangal", None, [c])
    assert with_alias["best"]["team_score"] > without["best"]["team_score"]
    assert with_alias["verdict"] == "accept"


def test_alias_column_accepts_a_comma_joined_string():
    """string_agg in SQL hands back one string, not a list."""
    c = cand(1, "Metizport", "Sangal Esports")
    c["team_a_aliases"] = "MTZ,Metiz"
    r = resolve("MTZ", "Sangal", None, [c])
    assert r["verdict"] == "accept"


def test_missing_alias_key_changes_nothing():
    """Backwards compatibility: callers that have not joined the alias table
    must score exactly as before."""
    a = resolve("BESTIA Academy", "underw0rld", None,
                [cand(1, "BESTIA Academy", "underw0rld")])
    c = cand(2, "BESTIA Academy", "underw0rld")
    c["team_a_aliases"] = None
    b = resolve("BESTIA Academy", "underw0rld", None, [c])
    assert a["best"]["score"] == b["best"]["score"]


def test_aliases_never_lower_a_score():
    """Adding a junk alias must not make a good match worse -- best-alias
    semantics, not average."""
    plain = cand(1, "BESTIA Academy", "underw0rld")
    noisy = dict(plain, team_a_aliases=["zzzz", "qqqq"])
    a = resolve("BESTIA Academy", "underw0rld", None, [plain])
    b = resolve("BESTIA Academy", "underw0rld", None, [noisy])
    assert b["best"]["team_score"] >= a["best"]["team_score"]


# ---------------------------------------------------------------- windowing


def _series(n=10, step_minutes=60):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    cands = [dict(cand(i, f"T{i}", f"O{i}"),
                  scheduled_at=base + timedelta(minutes=step_minutes * i))
             for i in range(n)]
    return cands, [c["scheduled_at"] for c in cands]


def test_window_slice_picks_only_the_matching_span():
    from datetime import timedelta
    from edgedesk.resolve.fixtures import window_slice
    cands, starts = _series()
    got = window_slice(cands, starts, starts[3], timedelta(minutes=30))
    assert [c["bo3_id"] for c in got] == [3]


def test_window_slice_is_inclusive_at_the_boundary():
    from datetime import timedelta
    from edgedesk.resolve.fixtures import window_slice
    cands, starts = _series()
    got = window_slice(cands, starts, starts[3], timedelta(minutes=60))
    assert [c["bo3_id"] for c in got] == [2, 3, 4]


def test_window_slice_handles_empty_and_none():
    from datetime import timedelta
    from edgedesk.resolve.fixtures import window_slice
    cands, starts = _series()
    assert window_slice(cands, starts, None, timedelta(minutes=30)) == []
    assert window_slice([], [], starts[0], timedelta(minutes=30)) == []


def test_window_slice_matches_a_linear_scan():
    """Equivalence check against the obvious implementation -- bisect is an
    optimisation and must not change which candidates are considered."""
    from datetime import timedelta
    from edgedesk.resolve.fixtures import window_slice
    cands, starts = _series(40, step_minutes=17)
    span = timedelta(minutes=45)
    for at in starts:
        fast = {c["bo3_id"] for c in window_slice(cands, starts, at, span)}
        slow = {c["bo3_id"] for c in cands
                if abs(c["scheduled_at"] - at) <= span}
        assert fast == slow


def test_time_proximity_breaks_a_score_tie():
    """A wide window can return two identically-named fixtures. The one
    starting nearest the Kalshi close time is the better bet -- but time is
    a tiebreak only, never folded into the score, because the 0.85 accept
    threshold is tuned against name similarity alone."""
    from datetime import datetime, timedelta, timezone
    from edgedesk.resolve.fixtures import resolve
    at = datetime(2026, 9, 2, 13, 30, tzinfo=timezone.utc)
    near = dict(cand(1, "OLDBOYS PL", "ROUNDS"),
                scheduled_at=at + timedelta(minutes=10))
    far = dict(cand(2, "OLDBOYS PL", "ROUNDS"),
               scheduled_at=at + timedelta(minutes=150))
    r = resolve("OLDBOYS-", "RoundsGG", None, [far, near], kalshi_start=at)
    assert r["best"]["match_id"] == 1
    assert r["best"]["minutes_apart"] == 10


def test_time_tiebreak_never_outranks_a_better_score():
    from datetime import datetime, timedelta, timezone
    from edgedesk.resolve.fixtures import resolve
    at = datetime(2026, 9, 2, 13, 30, tzinfo=timezone.utc)
    wrong_but_close = dict(cand(1, "Fire Flux", "NoTime"),
                           scheduled_at=at)
    right_but_far = dict(cand(2, "OLDBOYS PL", "ROUNDS"),
                         scheduled_at=at + timedelta(minutes=170))
    r = resolve("OLDBOYS-", "RoundsGG", None,
                [wrong_but_close, right_but_far], kalshi_start=at)
    assert r["best"]["match_id"] == 2


def test_resolve_works_without_a_start_time():
    """kalshi_start is optional; callers that omit it must behave as before."""
    r = resolve("OLDBOYS-", "RoundsGG", None, [cand(1, "OLDBOYS PL", "ROUNDS")])
    assert r["verdict"] == "accept"
