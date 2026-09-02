"""Scoring the log against fake settled matches.

The forfeit rule is the substance: a pre-game forfeit resolves at Fair
Market Price, so it must not be scored as a win or a loss.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "review", ROOT / "scripts" / "review.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["review"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    def __init__(self, unscored):
        self.unscored = unscored
        self.applied = []
        self.commits = 0

    def execute(self, sql, params=None):
        if "scored_at IS NULL" in sql and "JOIN matches" in sql:
            return FakeResult(self.unscored)
        if "UPDATE decisions SET result" in sql:
            self.applied.append(params)
            return FakeResult([])
        return FakeResult([])

    def commit(self):
        self.commits += 1


def dec(i, prob, winner, team_a=1, team_b=2, defwin=False, market=None):
    return {"id": i, "match_id": 100 + i, "prob_team_a": prob,
            "market_prob_a": market, "team_a_id": team_a,
            "team_b_id": team_b, "status": "finished",
            "winner_team_id": winner, "decided_by_default": defwin}


def test_scores_a_correct_call_well():
    conn = FakeConn([dec(1, 0.8, winner=1, market=0.5)])
    load().do_score(conn)
    a = conn.applied[0]
    assert a["result"] == "team_a"
    assert a["brier"] == pytest.approx(0.04)
    assert a["market_brier"] == pytest.approx(0.25)


def test_scores_the_other_side_correctly():
    conn = FakeConn([dec(1, 0.8, winner=2, market=0.5)])
    load().do_score(conn)
    a = conn.applied[0]
    assert a["result"] == "team_b"
    assert a["brier"] == pytest.approx(0.64)


def test_forfeit_is_recorded_as_fmp_and_left_unscored():
    """Kalshi resolves a pre-game forfeit to Fair Market Price. Scoring it
    as a win or a loss would score an event that never happened."""
    conn = FakeConn([dec(1, 0.8, winner=1, defwin=True, market=0.5)])
    load().do_score(conn)
    a = conn.applied[0]
    assert a["result"] == "fmp"
    assert a["brier"] is None and a["market_brier"] is None


def test_missing_winner_is_void_not_a_loss():
    conn = FakeConn([dec(1, 0.8, winner=None)])
    load().do_score(conn)
    assert conn.applied[0]["result"] == "void"
    assert conn.applied[0]["brier"] is None


def test_a_decision_without_a_market_price_still_scores():
    conn = FakeConn([dec(1, 0.8, winner=1, market=None)])
    load().do_score(conn)
    a = conn.applied[0]
    assert a["brier"] is not None
    assert a["market_brier"] is None


def test_scoring_commits_once():
    conn = FakeConn([dec(i, 0.6, winner=1) for i in range(1, 4)])
    assert load().do_score(conn) == 3
    assert conn.commits == 1


def test_nothing_settled_scores_nothing():
    conn = FakeConn([])
    assert load().do_score(conn) == 0


# ------------------------------------------------------- pending reasons


@pytest.mark.parametrize("row,expected", [
    ({"match_id": 1, "match_status": "live"}, "playing"),
    ({"match_id": 1, "match_status": "scheduled"}, "not started"),
    ({"match_id": 1, "match_status": "finished", "match_winner": 5},
     "run --score"),
    ({"match_id": 1, "match_status": "finished", "match_winner": None},
     "no winner"),
    ({"match_id": None}, "no match"),
])
def test_pending_reason_distinguishes_the_cases(row, expected):
    """'open' looked identical whether a match was being played right now
    or the local database was simply stale. Those need different actions."""
    assert load().pending_reason(row) == expected
