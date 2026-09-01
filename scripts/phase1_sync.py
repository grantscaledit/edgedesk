#!/usr/bin/env python3
"""Phase 1: sync bo3.gg fixtures and resolve them against Kalshi events.

    python scripts/phase1_sync.py                 # recent + upcoming, then resolve
    python scripts/phase1_sync.py --days 30       # widen the sync window
    python scripts/phase1_sync.py --resolve-only  # no fetching, just re-run matching
    python scripts/phase1_sync.py --dry-run       # report, write nothing

bo3 ignores date filters (see edgedesk/sources/bo3.py), so the window is
enforced client-side with early termination on a descending sort.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.sources import bo3                                 # noqa: E402
from edgedesk.resolve import fixtures as fx                      # noqa: E402

UPSERT_TEAM = """
INSERT INTO teams (canonical_name, country, acronym, bo3_rank, bo3_slug)
VALUES (%(name)s, NULL, %(acronym)s, %(bo3_rank)s, %(slug)s)
ON CONFLICT DO NOTHING
RETURNING id;
"""

LINK_TEAM = """
INSERT INTO team_external_ids (team_id, source, external_id, external_name)
VALUES (%s, 'bo3', %s, %s)
ON CONFLICT (source, external_id) DO UPDATE
  SET external_name = EXCLUDED.external_name
RETURNING team_id;
"""

FIND_TEAM_BY_EXT = """
SELECT team_id FROM team_external_ids WHERE source='bo3' AND external_id=%s;
"""

UPSERT_MATCH = """
INSERT INTO matches
  (team_a_id, team_b_id, scheduled_at, format_bo, status, bo3_status,
   winner_team_id, decided_by_default, tier, tier_rank, bo3_slug, updated_at)
VALUES (%(team_a_id)s, %(team_b_id)s, %(scheduled_at)s, %(bo_type)s,
        %(status)s, %(bo3_status)s, %(winner_team_id)s, %(decided_by_default)s,
        %(tier)s, %(tier_rank)s, %(slug)s, now())
RETURNING id;
"""

LINK_MATCH = """
INSERT INTO match_external_ids (match_id, source, external_id)
VALUES (%s, 'bo3', %s) ON CONFLICT (source, external_id) DO NOTHING;
"""

FIND_MATCH_BY_EXT = """
SELECT match_id FROM match_external_ids WHERE source='bo3' AND external_id=%s;
"""

UPDATE_MATCH = """
UPDATE matches SET status=%(status)s, bo3_status=%(bo3_status)s,
  winner_team_id=%(winner_team_id)s, decided_by_default=%(decided_by_default)s,
  scheduled_at=%(scheduled_at)s, format_bo=%(bo_type)s, updated_at=now()
WHERE id=%(id)s;
"""

CANDIDATES = """
SELECT m.id AS match_id,
       ta.canonical_name AS team_a_name, ta.acronym AS team_a_acronym,
       tb.canonical_name AS team_b_name, tb.acronym AS team_b_acronym,
       m.scheduled_at, m.format_bo, m.bo3_slug, NULL::text AS event_name
FROM matches m
JOIN teams ta ON ta.id = m.team_a_id
JOIN teams tb ON tb.id = m.team_b_id
WHERE m.scheduled_at BETWEEN %s AND %s;
"""

BIND = """
UPDATE kalshi_markets SET match_id=%s WHERE event_ticker=%s;
"""

QUEUE = """
INSERT INTO resolution_queue (kalshi_event_ticker, candidates, status)
VALUES (%s, %s, 'open')
ON CONFLICT (kalshi_event_ticker) DO UPDATE SET candidates=EXCLUDED.candidates;
"""


def load_team_map(conn) -> dict[str, int]:
    """One query for every known bo3 team id -> local id."""
    rows = conn.execute(
        "SELECT external_id, team_id FROM team_external_ids WHERE source='bo3'"
    ).fetchall()
    return {r["external_id"]: r["team_id"] for r in rows}


def load_match_map(conn) -> dict[str, int]:
    rows = conn.execute(
        "SELECT external_id, match_id FROM match_external_ids WHERE source='bo3'"
    ).fetchall()
    return {r["external_id"]: r["match_id"] for r in rows}


def insert_teams(conn, new_teams: list[dict]) -> dict[str, int]:
    """Batch-insert unknown teams and return bo3_id -> local id.

    Row-by-row inserts against a remote database cost one network round trip
    each; 300 teams is 300 round trips and several minutes of silence. These
    go in two statements.
    """
    if not new_teams:
        return {}
    values = [(t["name"], t.get("acronym"), t.get("bo3_rank"), t.get("slug"))
              for t in new_teams]
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO teams (canonical_name, acronym, bo3_rank, bo3_slug)
               VALUES (%s, %s, %s, %s)""",
            values,
        )
    # Re-read by name to get the ids back (executemany cannot RETURNING).
    names = [t["name"] for t in new_teams]
    rows = conn.execute(
        "SELECT id, canonical_name FROM teams WHERE canonical_name = ANY(%s)",
        (names,),
    ).fetchall()
    by_name: dict[str, int] = {}
    for r in rows:
        by_name.setdefault(r["canonical_name"], r["id"])

    links, out = [], {}
    for t in new_teams:
        tid = by_name.get(t["name"])
        if tid:
            links.append((tid, str(t["bo3_id"]), t["name"]))
            out[str(t["bo3_id"])] = tid
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO team_external_ids (team_id, source, external_id, external_name)
               VALUES (%s, 'bo3', %s, %s)
               ON CONFLICT (source, external_id) DO NOTHING""",
            links,
        )
    conn.commit()
    return out


def resolve_all(conn, dry_run: bool) -> tuple[int, int]:
    """Match unresolved Kalshi events to bo3 matches."""
    import json

    pending = conn.execute("SELECT * FROM v_unresolved ORDER BY scheduled_at").fetchall()
    print(f"\n  unresolved Kalshi events: {len(pending)}")
    bound = queued = 0

    for ev in pending:
        names = [n.strip() for n in (ev["teams"] or "").split(" vs ") if n.strip()]
        if len(names) != 2 or not ev["scheduled_at"]:
            continue
        a, b = names
        event_name = fx.extract_event_name(ev["rules_primary"])

        lo = ev["scheduled_at"] - fx.WINDOW
        hi = ev["scheduled_at"] + fx.WINDOW
        cands = [dict(c) for c in conn.execute(CANDIDATES, (lo, hi)).fetchall()]
        if not cands:
            lo, hi = ev["scheduled_at"] - fx.WIDE_WINDOW, ev["scheduled_at"] + fx.WIDE_WINDOW
            cands = [dict(c) for c in conn.execute(CANDIDATES, (lo, hi)).fetchall()]

        decision = fx.resolve(a, b, event_name, cands)
        best = decision["best"]

        if decision["verdict"] in ("accept", "fuzzy") and best:
            print(f"    BIND  {ev['event_ticker'][:44]:46} {a} vs {b}"
                  f"  score={best['score']:.2f} ({decision['reason']})")
            if not dry_run:
                conn.execute(BIND, (best["match_id"], ev["event_ticker"]))
            bound += 1
        else:
            print(f"    QUEUE {ev['event_ticker'][:44]:46} {a} vs {b}"
                  f"  ({decision['reason']})")
            if not dry_run:
                payload = json.dumps(
                    [{k: (str(v) if not isinstance(v, (int, float, str, type(None))) else v)
                      for k, v in c.items()} for c in decision["ranked"]])
                conn.execute(QUEUE, (ev["event_ticker"], payload))
            queued += 1

    if not dry_run:
        conn.commit()
    return bound, queued


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--resolve-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with db.connect() as conn:
        run_id = None if a.dry_run else db.start_run(conn, "bo3")
        try:
            n = 0 if a.resolve_only else sync(conn, a.days, a.dry_run)
            bound, queued = resolve_all(conn, a.dry_run)
            print(f"\nOK  {n} matches synced, {bound} bound, {queued} queued")
            if run_id is not None:
                db.finish_run(conn, run_id, "ok", n + bound)
        except Exception as exc:                             # noqa: BLE001
            if run_id is not None:
                db.finish_run(conn, run_id, "error", 0, str(exc))
            raise


if __name__ == "__main__":
    main()
