"""Dossier rendering, against a fake database.

The important assertion here is the DISPLAY CONTRACT: no percentage may
reach the terminal without its sample size next to it. That rule is stated
in CLAUDE.md and enforced by the Stat type, but rendering is where it would
actually be broken -- one f-string formatting `stat.value` directly and the
guarantee is gone with nothing to notice it. So the test reads the rendered
output and fails on a bare percentage.
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)
A, B = 1, 2


def load():
    spec = importlib.util.spec_from_file_location(
        "dossier", ROOT / "scripts" / "dossier.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dossier"] = mod
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
    def __init__(self, responses):
        self.responses = responses

    def execute(self, sql, params=None):
        for frag, rows in self.responses.items():
            if frag in sql:
                if callable(rows):
                    return FakeResult(rows(params or {}))
                return FakeResult(rows)
        return FakeResult([])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def match_row(i, days_ago, a, b, winner, defwin=False):
    return {"id": i, "scheduled_at": NOW - timedelta(days=days_ago),
            "status": "finished", "winner_team_id": winner,
            "decided_by_default": defwin, "team_a_id": a, "team_b_id": b,
            "format_bo": 3, "tier": "b", "bo3_slug": f"m{i}"}


def map_row(i, days_ago, a, b, name, ar, br, winner):
    return {"match_id": i, "map_index": 1, "map_name": name,
            "team_a_rounds": ar, "team_b_rounds": br,
            "winner_team_id": winner, "is_default": False,
            "side_assignment": "exact",
            "scheduled_at": NOW - timedelta(days=days_ago),
            "team_a_id": a, "team_b_id": b, "tier": "b"}


@pytest.fixture
def conn():
    a_matches = [match_row(1, 3, A, B, A), match_row(2, 10, A, 3, A),
                 match_row(3, 20, A, 4, 4), match_row(4, 30, A, 5, A, defwin=True)]
    b_matches = [match_row(1, 3, A, B, A), match_row(5, 8, B, 3, 3),
                 match_row(6, 15, B, 4, B)]
    a_maps = [map_row(1, 3, A, B, "Mirage", 13, 7, A),
              map_row(1, 3, A, B, "Nuke", 13, 10, A),
              map_row(2, 10, A, 3, "Mirage", 13, 4, A)]
    b_maps = [map_row(1, 3, A, B, "Mirage", 13, 7, A),
              map_row(5, 8, B, 3, "Ancient", 8, 13, 3)]
    return FakeConn({
        "JOIN teams ta ON ta.id = m.team_a_id": [{
            "id": 99, "scheduled_at": NOW + timedelta(hours=6),
            "format_bo": 3, "tier": "b", "bo3_slug": "big-vs-small",
            "team_a_id": A, "team_a_name": "Alpha",
            "team_b_id": B, "team_b_name": "Beta"}],
        "FROM matches m\nWHERE (m.team_a_id": lambda p: (
            a_matches if p.get("team") == A else b_matches),
        "FROM match_maps mm": lambda p: (
            a_maps if p.get("team") == A else b_maps),
        "FROM teams t WHERE": lambda p: [{
            "id": p.get("team"), "canonical_name":
                "Alpha" if p.get("team") == A else "Beta",
            "acronym": "ALP", "bo3_rank": 40, "country": "SE",
            "aliases": "ALPHA"}],
        "AVG(CASE WHEN decided_by_default": [{"mean": 0.031}],
    })


def render(mod, conn, match_id=99, days=365) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.show_match(conn, match_id, days)
    return buf.getvalue()


def test_dossier_renders_both_teams_and_h2h(conn):
    out = render(load(), conn)
    assert "Alpha" in out and "Beta" in out
    assert "HEAD TO HEAD" in out
    assert "win rate" in out and "round win %" in out


def test_no_percentage_appears_without_its_sample_size(conn):
    """The display contract, enforced where it would actually break.

    Every rendered percentage must sit on a line that also carries n=, or
    be part of a map-pool table row (which shows played counts in its own
    column).
    """
    out = render(load(), conn)
    offenders = []
    for line in out.splitlines():
        # A NUMERIC percentage. 'round%' in a column header is a label, not
        # a claim about a team, so it is not what this rule is about.
        if not re.search(r"\d+(\.\d+)?%", line):
            continue
        if "n=" in line or "no data" in line:
            continue
        # map-pool rows: 'Mirage  3  2-1  87%  +6.0' -- the played count and
        # raw record are the sample size, shown in their own columns.
        if re.match(r"\s{4}\S[\w' .-]*\s+\d+\s+\d+-\d+\s", line):
            continue
        if "liquidity gate" in line or "spread<=" in line:
            continue
        offenders.append(line)
    assert not offenders, "bare percentages:\n" + "\n".join(offenders)


def test_forfeit_is_excluded_from_win_rate_in_the_output(conn):
    """Alpha has 4 matches, one a forfeit: the rendered record must be
    2-1, not 3-1."""
    out = render(load(), conn)
    assert "2-1" in out


def test_missing_data_is_named_not_omitted(conn):
    out = render(load(), conn)
    assert "DATA GAPS" in out
    assert "CT/T" in out
    assert "Phase 2" in out


def test_dossier_states_it_gives_no_recommendation(conn):
    """The tool assembles evidence. If it ever starts implying a bet, this
    is the test that should have to be deleted first."""
    out = render(load(), conn)
    assert "No fair value, no recommendation" in out


def test_unknown_match_says_so(conn):
    empty = FakeConn({})
    buf = io.StringIO()
    with redirect_stdout(buf):
        load().show_match(empty, 12345, 365)
    assert "No match with id 12345" in buf.getvalue()


def test_slate_handles_an_empty_board(conn):
    mod = load()
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.show_slate(FakeConn({}))
    out = buf.getvalue()
    assert "Nothing listed" in out and "healthcheck" in out


# ---------------------------------------------------------- identifiers


def test_slate_prints_an_id_you_can_actually_copy():
    """Regression: the slate told the user to run `--match <id>` and then
    printed no ids at all, so there was nothing to copy."""
    mod = load()
    conn = FakeConn({"FROM v_slate s": [{
        "event_ticker": "KXCS2GAME-26SEP031000HAVULAV",
        "scheduled_at": NOW, "teams": "HAVU vs Lavked", "match_id": 501,
        "best_spread": 3, "overround": 104, "top_depth": 52,
        "gate_pass": True, "volume": 900, "captured_at": NOW}]})
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.show_slate(conn)
    out = buf.getvalue()
    assert "501" in out
    assert "KXCS2GAME-26SEP031000HAVULAV" in out


def test_slate_marks_unbound_events():
    mod = load()
    conn = FakeConn({"FROM v_slate s": [{
        "event_ticker": "KXCS2GAME-X", "scheduled_at": NOW,
        "teams": "A vs B", "match_id": None, "best_spread": 9,
        "overround": 130, "top_depth": 1, "gate_pass": False,
        "volume": 3, "captured_at": NOW}]})
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.show_slate(conn)
    assert "unbound" in buf.getvalue()


@pytest.mark.parametrize("argv,expect_match,expect_event", [
    (["--match", "501"], 501, None),
    (["--match", "KXCS2GAME-26SEP031000HAVULAV"], None,
     "KXCS2GAME-26SEP031000HAVULAV"),
    (["--event", "KXCS2GAME-26SEP031000HAVULAV"], None,
     "KXCS2GAME-26SEP031000HAVULAV"),
    (["--event", "501"], 501, None),
])
def test_either_identifier_works_on_either_flag(argv, expect_match,
                                                expect_event, monkeypatch):
    """A ticker on --match used to die with 'invalid int value'. Making the
    user know which flag takes which identifier is a trap the tool can just
    absorb."""
    mod = load()
    captured = {}

    monkeypatch.setattr(sys, "argv", ["dossier.py"] + argv)
    monkeypatch.setattr(mod.db, "connect", lambda: FakeConn({}))
    monkeypatch.setattr(mod, "show_match",
                        lambda c, m, d: captured.update(match=m))
    monkeypatch.setattr(mod.queries, "event_sides",
                        lambda c, e: captured.update(event=e) or [])
    mod.main()

    assert captured.get("match") == expect_match
    assert captured.get("event") == expect_event
