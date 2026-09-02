"""Integration test for sync() against fake bo3 and fake database.

phase1_sync shipped for weeks with main() calling a sync() that did not
exist -- every run without --resolve-only raised NameError. Nothing caught
it because nothing exercised the script itself, only the pure modules it
imports. This test exists so that cannot recur.

The fakes are deliberately dumb: they record what SQL was issued and with
what arity, which is exactly the class of bug (wrong tuple width, wrong
column order, forgotten commit) that a real database would catch and a
static check would not.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "phase1_sync", ROOT / "scripts" / "phase1_sync.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase1_sync"] = mod
    spec.loader.exec_module(mod)
    return mod


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def executemany(self, sql, rows):
        self.log.append(("executemany", sql, list(rows)))


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Answers by matching a fragment of the SQL. Anything unrecognised
    returns no rows, which surfaces as a visible failure rather than a
    silent wrong answer."""

    def __init__(self, responses):
        self.responses = responses
        self.log = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.log.append(("execute", sql, params))
        for frag, rows in self.responses.items():
            if frag in sql:
                return FakeResult(rows)
        return FakeResult([])

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeBo3:
    def __init__(self, matches, teams):
        self._matches, self._teams = matches, teams

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_matches(self, status, **kw):
        for m in self._matches:
            if m["status"] == status:
                yield m

    def teams(self, ids):
        return [t for t in self._teams if t["id"] in set(ids)]


def make_match(mid, t1, t2, status="finished", slug=None, offset_h=-24):
    return {"id": mid, "slug": slug or f"match-{mid}", "team1_id": t1,
            "team2_id": t2, "winner_team_id": t1, "team1_score": 2,
            "team2_score": 1, "tournament_id": 9,
            "start_date": (NOW + timedelta(hours=offset_h)).isoformat(),
            "end_date": None, "bo_type": 3, "status": status,
            "tier": "a", "tier_rank": 2}


@pytest.fixture
def mod(monkeypatch):
    m = load_script()
    monkeypatch.setattr(m.bo3, "Bo3", lambda *a, **k: FakeBo3(
        [make_match(1, 100, 200), make_match(2, 100, 300)],
        [{"id": 100, "name": "Alpha", "slug": "alpha", "acronym": "ALP"},
         {"id": 200, "name": "Beta", "slug": "beta", "acronym": None},
         {"id": 300, "name": "Gamma", "slug": "gamma", "acronym": "GAM"}],
    ))
    return m


def test_sync_is_defined_and_callable(mod):
    """The regression that started this: main() referenced a function that
    was never written."""
    assert callable(getattr(mod, "sync", None))


def test_sync_inserts_new_matches_with_correct_arity(mod):
    conn = FakeConn({
        "FROM team_external_ids": [],
        "FROM match_external_ids": [],
        "FROM teams WHERE canonical_name": [
            {"id": 11, "canonical_name": "Alpha"},
            {"id": 12, "canonical_name": "Beta"},
            {"id": 13, "canonical_name": "Gamma"}],
        "SELECT id, bo3_slug FROM matches": [
            {"id": 501, "bo3_slug": "match-1"},
            {"id": 502, "bo3_slug": "match-2"}],
    })
    n = mod.sync(conn, days=30, dry_run=False)
    assert n == 2

    inserts = [c for c in conn.log
               if c[0] == "executemany" and "INSERT INTO matches" in c[1]]
    assert inserts, "no match insert was issued"
    rows = inserts[0][2]
    assert len(rows) == 2
    # 13 placeholders + now() == 14 columns. A mismatch here is the failure
    # a static check cannot see; this assertion has already caught one
    # widening (tournament and stage ids).
    assert all(len(r) == 13 for r in rows), [len(r) for r in rows]
    assert rows[0][10] == "match-1"          # slug at the index re-read uses


def test_sync_links_external_ids_after_insert(mod):
    conn = FakeConn({
        "FROM team_external_ids": [],
        "FROM match_external_ids": [],
        "FROM teams WHERE canonical_name": [
            {"id": 11, "canonical_name": "Alpha"},
            {"id": 12, "canonical_name": "Beta"},
            {"id": 13, "canonical_name": "Gamma"}],
        "SELECT id, bo3_slug FROM matches": [
            {"id": 501, "bo3_slug": "match-1"},
            {"id": 502, "bo3_slug": "match-2"}],
    })
    mod.sync(conn, days=30, dry_run=False)
    links = [c for c in conn.log
             if c[0] == "executemany" and "match_external_ids" in c[1]]
    assert links and len(links[0][2]) == 2
    assert {r[0] for r in links[0][2]} == {501, 502}


def test_sync_updates_known_matches_instead_of_reinserting(mod):
    conn = FakeConn({
        "FROM team_external_ids": [
            {"external_id": "100", "team_id": 11},
            {"external_id": "200", "team_id": 12},
            {"external_id": "300", "team_id": 13}],
        "FROM match_external_ids": [
            {"external_id": "1", "match_id": 501},
            {"external_id": "2", "match_id": 502}],
    })
    mod.sync(conn, days=30, dry_run=False)
    inserts = [c for c in conn.log
               if c[0] == "executemany" and "INSERT INTO matches" in c[1]]
    updates = [c for c in conn.log
               if c[0] == "executemany" and "UPDATE matches" in c[1]]
    assert not inserts
    assert updates and len(updates[0][2]) == 2


def test_dry_run_writes_nothing(mod):
    conn = FakeConn({"FROM team_external_ids": [], "FROM match_external_ids": []})
    mod.sync(conn, days=30, dry_run=True)
    assert not [c for c in conn.log if c[0] == "executemany"]
    assert conn.commits == 0


def test_sync_skips_matches_with_an_unresolvable_team(mod, monkeypatch):
    """A match referencing a team bo3 would not return must be skipped, not
    inserted with a NULL foreign key."""
    monkeypatch.setattr(mod.bo3, "Bo3", lambda *a, **k: FakeBo3(
        [make_match(1, 100, 999)],
        [{"id": 100, "name": "Alpha", "slug": "alpha", "acronym": "ALP"}],
    ))
    conn = FakeConn({
        "FROM team_external_ids": [],
        "FROM match_external_ids": [],
        "FROM teams WHERE canonical_name": [{"id": 11, "canonical_name": "Alpha"}],
        "SELECT id, bo3_slug FROM matches": [],
    })
    mod.sync(conn, days=30, dry_run=False)
    inserts = [c for c in conn.log
               if c[0] == "executemany" and "INSERT INTO matches" in c[1]]
    assert not inserts


def test_resolve_all_loads_candidates_once(mod):
    """The perf fix: one candidate query per RUN, not two per event."""
    events = [{"event_ticker": f"E{i}", "scheduled_at": NOW,
               "teams": "Alpha vs Beta", "rules_primary": None,
               "resolved": False} for i in range(25)]
    conn = FakeConn({
        "FROM v_unresolved": events,
        "FROM matches m": [
            {"match_id": 501, "team_a_name": "Alpha", "team_a_acronym": "ALP",
             "team_b_name": "Beta", "team_b_acronym": None,
             "team_a_aliases": None, "team_b_aliases": None,
             "scheduled_at": NOW, "format_bo": 3, "bo3_slug": "match-1",
             "event_name": None}],
    })
    bound, queued = mod.resolve_all(conn, dry_run=True)
    cand_queries = [c for c in conn.log
                    if c[0] == "execute" and "FROM matches m" in c[1]]
    assert len(cand_queries) == 1, f"{len(cand_queries)} candidate queries"
    assert bound == 25


def test_dense_narrow_window_still_escalates_to_the_wide_window(mod):
    """Regression from the 365-day backfill.

    The old escalation was `if not cands: widen`. While the match table held
    ~1k rows a +/-30min window was frequently empty and the fallback ran.
    After backfilling 16k matches the narrow window is never empty, the
    fallback became unreachable, and pairs that had previously resolved
    started queueing -- OLDBOYS-/RoundsGG went from 0.96 to 0.08.

    Here the narrow window holds only decoys and the true fixture sits 90
    minutes away, inside WIDE_WINDOW.
    """
    from edgedesk.resolve import fixtures as fx

    ev_at = NOW
    decoys = [{"match_id": 900 + i,
               "team_a_name": f"Decoy{i}", "team_a_acronym": None,
               "team_b_name": f"Other{i}", "team_b_acronym": None,
               "team_a_aliases": None, "team_b_aliases": None,
               "scheduled_at": ev_at + timedelta(minutes=m),
               "format_bo": 1, "bo3_slug": f"d{i}", "event_name": None}
              for i, m in enumerate((-20, -5, 5, 20))]
    truth = {"match_id": 777,
             "team_a_name": "OLDBOYS PL", "team_a_acronym": None,
             "team_b_name": "ROUNDS", "team_b_acronym": None,
             "team_a_aliases": None, "team_b_aliases": None,
             "scheduled_at": ev_at + timedelta(minutes=90),
             "format_bo": 1, "bo3_slug": "truth", "event_name": None}
    rows = sorted(decoys + [truth], key=lambda c: c["scheduled_at"])

    conn = FakeConn({
        "FROM v_unresolved": [{
            "event_ticker": "KXCS2GAME-26SEP021330RGGOLD",
            "scheduled_at": ev_at, "teams": "OLDBOYS- vs RoundsGG",
            "rules_primary": None, "resolved": False}],
        "FROM matches m": rows,
    })
    bound, queued = mod.resolve_all(conn, dry_run=True)
    assert (bound, queued) == (1, 0), "wide-window escalation did not fire"

    # And confirm the narrow window really was non-empty, so the test is
    # exercising the escalation rather than the empty-window path.
    starts = [c["scheduled_at"] for c in rows]
    assert fx.window_slice(rows, starts, ev_at, fx.WINDOW)


def test_escalation_does_not_lower_the_bar(mod):
    """Widening must find more candidates, never accept a worse one. If the
    wide window holds nothing plausible, the event still queues."""
    ev_at = NOW
    rows = [{"match_id": 900 + i,
             "team_a_name": f"Decoy{i}", "team_a_acronym": None,
             "team_b_name": f"Other{i}", "team_b_acronym": None,
             "team_a_aliases": None, "team_b_aliases": None,
             "scheduled_at": ev_at + timedelta(minutes=m),
             "format_bo": 1, "bo3_slug": f"d{i}", "event_name": None}
            for i, m in enumerate((-120, -20, 5, 100))]
    conn = FakeConn({
        "FROM v_unresolved": [{
            "event_ticker": "E1", "scheduled_at": ev_at,
            "teams": "Falcons Force vs Fraternity",
            "rules_primary": None, "resolved": False}],
        "FROM matches m": rows,
    })
    bound, queued = mod.resolve_all(conn, dry_run=True)
    assert (bound, queued) == (0, 1)


def test_history_flag_selects_the_settled_view(mod):
    """Regression: v_unresolved filters status IN ('active','open'), so
    every event that had ever settled was permanently unbindable — and
    settled events, carrying both a result and a closing price, ARE the
    backtest dataset. vs_market.py had six events to work with."""
    seen = []

    class Recording(FakeConn):
        def execute(self, sql, params=None):
            if "v_unresolved" in sql:
                seen.append(sql)
            return super().execute(sql, params)

    conn = Recording({"FROM v_unresolved": [], "FROM matches m": []})
    mod.resolve_all(conn, dry_run=True, history=True)
    assert any("v_unresolved_history" in s for s in seen), seen


def test_default_resolution_uses_the_live_view_only(mod):
    """A routine sync must keep looking at a handful of live events rather
    than re-scanning six thousand historical ones every four hours."""
    seen = []

    class Recording(FakeConn):
        def execute(self, sql, params=None):
            if "v_unresolved" in sql:
                seen.append(sql)
            return super().execute(sql, params)

    conn = Recording({"FROM v_unresolved": [], "FROM matches m": []})
    mod.resolve_all(conn, dry_run=True)
    assert seen and not any("v_unresolved_history" in s for s in seen)
