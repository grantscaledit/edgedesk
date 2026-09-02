"""Backtest harness.

Two things must both be true or the whole exercise is worthless: it must
FIND a signal that is really there, and it must NOT find one that is not.
So the tests plant a known signal in synthetic data and check both
directions, then check the lookahead guarantee directly.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from edgedesk.stats.backtest import (
    SIGNALS, summarise, verdict, walk_forward,
)

START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def synth(n_teams=20, n_matches=3000, strength=0.0, seed=1):
    """Teams have a hidden skill; `strength` sets how much it decides games.

    strength=0 makes every result a coin flip regardless of skill, so no
    signal exists to find. strength=1 makes the stronger team always win.
    """
    rng = random.Random(seed)
    skills = {t: rng.random() for t in range(1, n_teams + 1)}
    matches, maps = [], []
    for i in range(n_matches):
        a, b = rng.sample(range(1, n_teams + 1), 2)
        if rng.random() < strength:
            winner = a if skills[a] > skills[b] else b
        else:
            winner = rng.choice([a, b])
        matches.append({
            "id": i, "scheduled_at": START + timedelta(hours=6 * i),
            "status": "finished", "winner_team_id": winner,
            "decided_by_default": False, "team_a_id": a, "team_b_id": b})
        wr = 13
        lr = rng.randint(3, 11)
        maps.append({
            "match_id": i, "map_index": 1, "map_name": "Mirage",
            "team_a_rounds": wr if winner == a else lr,
            "team_b_rounds": lr if winner == a else wr,
            "winner_team_id": winner, "is_default": False,
            "team_a_id": a, "team_b_id": b,
            "scheduled_at": START + timedelta(hours=6 * i)})
    return matches, maps


def test_finds_a_signal_that_is_really_there():
    m, mp = synth(strength=0.85)
    out = walk_forward(m, mp)
    s = summarise(out["results"]["win_rate"])
    assert s["n"] > 500
    assert s["accuracy"] > 0.60, s
    assert verdict(s) == "clearly better than chance"


def test_finds_nothing_when_there_is_nothing():
    """Pure coin flips must not produce a signal. If this fails, every
    positive result the harness reports is suspect."""
    m, mp = synth(strength=0.0, seed=99)
    out = walk_forward(m, mp)
    s = summarise(out["results"]["win_rate"])
    assert abs(s["accuracy"] - 0.5) < 0.05, s
    assert verdict(s) in ("indistinguishable from chance",
                          "suggestive, not conclusive")


def test_the_random_control_lands_at_chance_on_real_signal_data():
    """The control exists to catch a broken harness. Even on data with a
    strong true signal, a random number must predict nothing."""
    m, mp = synth(strength=0.85)
    out = walk_forward(m, mp)
    s = summarise(out["results"]["random_control"])
    assert abs(s["accuracy"] - 0.5) < 0.05, s


def test_round_win_pct_also_detects_the_planted_signal():
    m, mp = synth(strength=0.85)
    out = walk_forward(m, mp)
    s = summarise(out["results"]["round_win_pct"])
    assert s["accuracy"] > 0.55, s


# ------------------------------------------------------------- lookahead


def test_a_match_cannot_see_its_own_result():
    """The load-bearing guarantee. Team A wins every match; scoring the
    very first eligible match must not already know that.

    Constructed so each team has exactly MIN_HISTORY prior matches, then
    the next match is scored. If history were extended before scoring, the
    signal would include the match being predicted.
    """
    matches, maps = [], []
    i = 0
    for _ in range(12):                       # build history for teams 1,2
        for a, b in ((1, 3), (2, 4)):
            matches.append({
                "id": i, "scheduled_at": START + timedelta(hours=i),
                "status": "finished", "winner_team_id": a,
                "decided_by_default": False, "team_a_id": a, "team_b_id": b})
            i += 1
    target_id = i
    matches.append({
        "id": target_id, "scheduled_at": START + timedelta(hours=i),
        "status": "finished", "winner_team_id": 2,     # the UNDERDOG wins
        "decided_by_default": False, "team_a_id": 1, "team_b_id": 2})

    out = walk_forward(matches, maps, min_history=10)
    rows = out["results"]["win_rate"]
    scored = [r for r in rows if r["scheduled_at"] == START + timedelta(hours=i)]
    # Both sides are 12-0 going in, so the gap is 0 and the match is skipped
    # entirely -- which is itself proof the outcome was not consulted.
    assert not scored or scored[0]["correct"] is False


def test_history_is_extended_after_scoring_not_before():
    """Directly: with min_history=1, the first scoreable match for a pair
    must use one prior match each, not two."""
    matches = []
    for i, (a, b, w) in enumerate([(1, 3, 1), (2, 4, 4), (1, 2, 2)]):
        matches.append({
            "id": i, "scheduled_at": START + timedelta(hours=i),
            "status": "finished", "winner_team_id": w,
            "decided_by_default": False, "team_a_id": a, "team_b_id": b})
    out = walk_forward(matches, [], min_history=1)
    rows = out["results"]["win_rate"]
    # Team 1 is 1-0, team 2 is 0-1 -> gap positive -> predicts team 1.
    # Team 2 actually won, so the prediction must be recorded as WRONG.
    assert len(rows) == 1
    assert rows[0]["correct"] is False


# ------------------------------------------------------------- reporting


def test_summarise_reports_uncertainty_not_just_a_percentage():
    """55% over 200 and 52% over 16,000 are opposite findings."""
    small = summarise([{"gap": 0.1, "correct": i < 110, "scheduled_at": START}
                       for i in range(200)])
    big = summarise([{"gap": 0.1, "correct": i < 8320, "scheduled_at": START}
                     for i in range(16000)])
    assert small["accuracy"] == 0.55 and big["accuracy"] == 0.52
    assert big["z"] > small["z"]          # smaller effect, far more certain


def test_small_samples_are_refused_not_reported():
    assert verdict(summarise([{"gap": 0.1, "correct": True, "match_id": 1,
                               "scheduled_at": START}] * 50)) == \
        "sample too small to say"


def test_buckets_show_whether_accuracy_rises_with_the_gap():
    rows = ([{"gap": 0.01, "correct": i % 2 == 0, "match_id": i,
              "scheduled_at": START} for i in range(100)]
            + [{"gap": 0.5, "correct": True, "match_id": 200 + i,
                "scheduled_at": START} for i in range(100)])
    b = summarise(rows)["buckets"]
    assert b[0]["accuracy"] == pytest.approx(0.5, abs=0.05)
    assert b[-1]["accuracy"] == 1.0


def test_every_signal_is_exercised():
    m, mp = synth(strength=0.7, n_matches=1200)
    out = walk_forward(m, mp)
    for name in SIGNALS:
        assert name in out["results"]


# --------------------------------------------------- percentile buckets


def test_buckets_are_percentiles_not_fixed_cutoffs():
    """Regression from the first real run: fixed probability-scaled edges
    put 91% of avg_round_diff — measured in rounds, not probabilities —
    into a single bucket, making its table unreadable."""
    rows = [{"gap": g, "correct": True, "scheduled_at": START}
            for g in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)]
    b = summarise(rows, buckets=5)["buckets"]
    assert len(b) == 5
    assert all(x["n"] == 2 for x in b), [x["n"] for x in b]
    assert b[0]["gap_hi"] < b[-1]["gap_lo"]


def test_buckets_work_on_probability_scale_too():
    rows = [{"gap": g / 100, "correct": True, "scheduled_at": START}
            for g in range(1, 101)]
    b = summarise(rows, buckets=5)["buckets"]
    assert len(b) == 5 and all(x["n"] == 20 for x in b)


# ------------------------------------------------------- incremental


def test_incremental_isolates_matches_where_the_base_is_undecided():
    from edgedesk.stats.backtest import incremental
    ts = [START + timedelta(hours=i) for i in range(10)]
    results = {
        "win_rate": [{"gap": 0.4 if i < 5 else 0.01, "correct": True,
                      "match_id": i, "scheduled_at": ts[i]}
                     for i in range(10)],
        "round_win_pct": [{"gap": 0.1, "correct": i >= 5, "match_id": i,
                           "scheduled_at": ts[i]} for i in range(10)],
    }
    out = incremental(results, base="win_rate", close_threshold=0.05)
    assert out["close_matches"] == 5
    # On exactly those five, round_win_pct was right every time.
    assert out["signals"]["round_win_pct"]["accuracy"] == 1.0
    assert out["signals"]["round_win_pct"]["n"] == 5


def test_incremental_handles_a_signal_with_no_overlap():
    from edgedesk.stats.backtest import incremental
    results = {"win_rate": [{"gap": 0.5, "correct": True, "match_id": 1,
                             "scheduled_at": START}],
               "other": [{"gap": 0.1, "correct": True, "match_id": 99,
                          "scheduled_at": START + timedelta(days=99)}]}
    out = incremental(results)
    assert out["close_matches"] == 0
    assert "other" not in out["signals"]


def test_results_carry_match_id_for_joining():
    """Regression: vs_market keyed its join on scheduled_at and matched
    NOTHING — the match's start comes from bo3, the Kalshi event's from
    expiration_time minus 48h, and the two clocks never agree to the
    second. Every signal reported '—' over 1,553 real events."""
    m, mp = synth(strength=0.7, n_matches=800)
    out = walk_forward(m, mp)
    rows = out["results"]["win_rate"]
    assert rows and all(r.get("match_id") is not None for r in rows)
