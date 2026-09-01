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
