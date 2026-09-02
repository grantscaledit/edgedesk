"""Web routes against a fake database.

The load-bearing test is test_no_bare_percentage_in_html. The display
contract is enforced by the Stat type in Python, but a template is free to
print `{{ s.value }}` and throw the provenance away -- HTML is exactly where
the guarantee would be lost silently. So the rendered markup is inspected.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from edgedesk.web.app import create_app

NOW = datetime.now(timezone.utc)
A, B = 1, 2


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
        self.writes = []

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("INSERT"):
            self.writes.append((sql, params))
            return FakeResult([{"id": 7}])
        for frag, rows in self.responses.items():
            if frag in sql:
                return FakeResult(rows(params or {}) if callable(rows) else rows)
        return FakeResult([])

    def commit(self):
        pass

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
            "team_a_rounds": ar, "team_b_rounds": br, "winner_team_id": winner,
            "is_default": False, "side_assignment": "exact",
            "scheduled_at": NOW - timedelta(days=days_ago),
            "team_a_id": a, "team_b_id": b, "tier": "b"}


@pytest.fixture
def conn():
    a_m = [match_row(1, 3, A, B, A), match_row(2, 9, A, 3, A),
           match_row(3, 20, A, 4, 4), match_row(4, 30, A, 5, A, defwin=True)]
    b_m = [match_row(1, 3, A, B, A), match_row(5, 8, B, 3, 3)]
    a_maps = [map_row(1, 3, A, B, "Mirage", 13, 7, A),
              map_row(1, 3, A, B, "Nuke", 13, 10, A),
              map_row(2, 9, A, 3, "Mirage", 13, 4, A)]
    return FakeConn({
        # Respects the requested id so a missing match really 404s.
        "JOIN teams ta ON ta.id = m.team_a_id": lambda p: ([{
            "id": 99, "scheduled_at": NOW + timedelta(hours=5),
            "format_bo": 3, "tier": "b", "tier_rank": 3, "bo3_slug": "x",
            "stage_title": "Playoffs", "team_a_id": A, "team_a_name": "Alpha",
            "team_b_id": B, "team_b_name": "Beta",
            "tournament": "ESEA Season 58", "tournament_tier": "b",
            "tournament_tier_rank": 3, "prize": 25000,
            "event_type": "online", "event_level": "regular"}]
            if p.get("match") == 99 else []),
        "FROM matches m\nWHERE (m.team_a_id": lambda p: (
            a_m if p.get("team") == A else b_m),
        "FROM match_maps mm": lambda p: (a_maps if p.get("team") == A else []),
        "FROM teams t WHERE": lambda p: [{
            "id": p.get("team"),
            "canonical_name": "Alpha" if p.get("team") == A else "Beta",
            "acronym": "ALP", "bo3_rank": 40, "country": "SE",
            "aliases": None}],
        "AVG(CASE WHEN decided_by_default": [{"mean": 0.031}],
        "FROM v_roster_latest": [],
        "FROM v_roster_changes": [],
        "FROM v_slate s": [{
            "event_ticker": "KXCS2GAME-X", "scheduled_at": NOW,
            "teams": "Alpha vs Beta", "match_id": 99, "best_spread": 3,
            "overround": 104, "top_depth": 52, "gate_pass": True,
            "volume": 900, "captured_at": NOW}],
        "FROM decisions d": [],
        "FROM decisions WHERE match_id": [],
        "FROM resolution_queue": [],
        "FROM collection_runs": [],
        "kalshi_price_snapshots": [],
        "pg_database_size": [{"b": 26 * 1024 * 1024}],
        "FROM kalshi_markets\nWHERE match_id": [],
    })


@pytest.fixture
def client(conn, monkeypatch):
    app = create_app()
    app.config.update(TESTING=True)
    monkeypatch.setattr("edgedesk.db.connect", lambda: conn)
    return app.test_client()


def test_slate_lists_events_and_links_to_the_dossier(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Alpha vs Beta" in body
    assert "/match/99" in body


def test_match_page_renders(client):
    body = client.get("/match/99").get_data(as_text=True)
    assert "Alpha" in body and "Beta" in body
    assert "head to head" in body
    assert "ESEA Season 58" in body


def test_no_bare_percentage_in_html(client):
    """Every rendered percentage must carry its sample size.

    A template printing {{ s.value }} directly would silently discard the
    provenance the Stat type exists to guarantee.
    """
    from html import unescape
    body = client.get("/match/99").get_data(as_text=True)
    body = re.sub(r"<(style|script)\b.*?</\1>", "", body,
                  flags=re.S | re.I)

    # Check per TABLE CELL, not per line: the macro emits the value and its
    # provenance as sibling spans, so a line-based check would flag correct
    # markup and, worse, would pass markup that dropped the provenance into
    # a different row entirely.
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", body, re.S | re.I)
    offenders = []
    for cell in cells:
        text = unescape(re.sub(r"<[^>]+>", " ", cell))
        if not re.search(r"\d+(\.\d+)?%", text):
            continue
        if "n=" in text or "no data" in text:
            continue
        # Map-pool rows carry played count and raw record in sibling cells.
        if re.fullmatch(r"\s*[+-]?\d+(\.\d+)?%\s*", text):
            continue
        offenders.append(" ".join(text.split()))
    assert not offenders, offenders


def test_missing_data_renders_as_a_named_gap(client):
    body = client.get("/match/99").get_data(as_text=True)
    assert "not captured" in body           # no roster ingested in fixtures
    assert "HLTV" in body


def test_decision_form_offers_no_bet_and_every_known_tag(client):
    body = client.get("/match/99").get_data(as_text=True)
    assert 'value="no_bet"' in body
    from edgedesk.decisions import TAGS
    for t in TAGS:
        assert f'value="{t}"' in body


def test_logging_a_decision_writes_and_redirects(client, conn):
    r = client.post("/match/99/decision", data={
        "prob_a": "0.62", "action": "no_bet", "tags": ["map_pool"]})
    assert r.status_code == 302
    assert any("INSERT INTO decisions" in s for s, _ in conn.writes)


def test_invalid_probability_is_rejected_without_writing(client, conn):
    r = client.post("/match/99/decision", data={
        "prob_a": "1.4", "action": "no_bet"})
    assert r.status_code == 302
    assert not conn.writes


def test_unknown_tag_is_rejected_without_writing(client, conn):
    """A typo'd tag silently becomes its own category and splits the
    evidence for the reason it was meant to record."""
    r = client.post("/match/99/decision", data={
        "prob_a": "0.5", "action": "no_bet", "tags": ["map_pol"]})
    assert r.status_code == 302
    assert not conn.writes


def test_bet_without_a_price_is_rejected(client, conn):
    client.post("/match/99/decision", data={"prob_a": "0.5", "action": "bet_a"})
    assert not conn.writes


def test_unknown_match_is_404(client):
    assert client.get("/match/12345").status_code == 404


def test_log_queue_and_health_render(client):
    for path in ("/log", "/queue", "/health"):
        assert client.get(path).status_code == 200


def test_footer_states_no_recommendation(client):
    body = client.get("/").get_data(as_text=True)
    assert "No fair value, no recommendation" in body
