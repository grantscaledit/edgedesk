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
  scheduled_at=%(scheduled_at)s, format_bo=%(bo_type)s,
  bo3_tournament_id=COALESCE(%(tournament_id)s, bo3_tournament_id),
  bo3_stage_id=COALESCE(%(stage_id)s, bo3_stage_id), updated_at=now()
WHERE id=%(id)s;
"""

# Loaded ONCE for the whole run, then windowed in Python. Running this per
# event was fine for 47 events and is 6,000 round trips to Neon for the
# historical backlog.
CANDIDATES = """
SELECT m.id AS match_id,
       ta.canonical_name AS team_a_name, ta.acronym AS team_a_acronym,
       tb.canonical_name AS team_b_name, tb.acronym AS team_b_acronym,
       aa.aliases AS team_a_aliases,
       ba.aliases AS team_b_aliases,
       m.scheduled_at, m.format_bo, m.bo3_slug,
       -- Both names, comma joined; scoring takes the better match. Kalshi's
       -- rules text sometimes names the tournament and sometimes the stage.
       concat_ws(',', t.name, m.stage_title) AS event_name
FROM matches m
JOIN teams ta ON ta.id = m.team_a_id
JOIN teams tb ON tb.id = m.team_b_id
LEFT JOIN LATERAL (
  SELECT string_agg(alias_name, ',') AS aliases
  FROM v_team_aliases WHERE team_id = ta.id
) aa ON true
LEFT JOIN LATERAL (
  SELECT string_agg(alias_name, ',') AS aliases
  FROM v_team_aliases WHERE team_id = tb.id
) ba ON true
LEFT JOIN tournaments t ON t.bo3_id = m.bo3_tournament_id
WHERE m.scheduled_at IS NOT NULL
ORDER BY m.scheduled_at;
"""

INSERT_MATCHES = """
INSERT INTO matches
  (team_a_id, team_b_id, scheduled_at, format_bo, status, bo3_status,
   winner_team_id, decided_by_default, tier, tier_rank, bo3_slug,
   bo3_tournament_id, bo3_stage_id, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now());
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


def sync(conn, days: int, dry_run: bool) -> int:
    """Fetch bo3 matches and upsert them. Returns the number touched.

    THIS FUNCTION WAS MISSING. main() called it, so any invocation without
    --resolve-only raised NameError -- which is also why the scheduled
    sync.yml would have failed on its first run.

    bo3 silently ignores date filters, so the window is enforced client-side
    with early termination on a descending sort. Finished matches terminate
    at the cutoff; upcoming ones are unbounded in the past direction and are
    simply taken whole, since there are never many.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    print(f"  window: {cutoff:%Y-%m-%d} -> now  ({days}d)")

    raw: dict[str, dict] = {}
    with bo3.Bo3() as client:
        for status, bounded in ((bo3.STATUS_FINISHED, True),
                                (bo3.STATUS_DEFWIN, True),
                                (bo3.STATUS_CURRENT, False),
                                (bo3.STATUS_UPCOMING, False)):
            print(f"    fetching {status}...", flush=True)
            n_before = len(raw)
            for item in client.iter_matches(
                status,
                since=cutoff if bounded else None,
                newest_first=True,
                progress=True,
            ):
                m = bo3.parse_match(item)
                if m["bo3_id"] is not None:
                    raw[str(m["bo3_id"])] = m
            print(f"      +{len(raw) - n_before}")

        print(f"  matches fetched: {len(raw)}")
        if not raw:
            return 0

        # --- teams -------------------------------------------------------
        team_map = load_team_map(conn)
        wanted = {str(m[k]) for m in raw.values()
                  for k in ("team1_id", "team2_id") if m.get(k) is not None}
        unknown = sorted(int(t) for t in wanted - set(team_map))
        print(f"  teams referenced: {len(wanted)}  unknown: {len(unknown)}")

        if unknown:
            fetched = [bo3.parse_team(t) for t in client.teams(unknown)]
            fetched = [t for t in fetched if t.get("name")]
            if not dry_run:
                team_map.update(insert_teams(conn, fetched))
            print(f"  teams resolved: {len(fetched)}")

    if dry_run:
        print("  DRY RUN: nothing written. Resolution below will find no "
              "candidates, because the matches it searches were not saved.")
        return len(raw)

    # --- matches ---------------------------------------------------------
    match_map = load_match_map(conn)
    to_insert, to_update, skipped = [], [], 0

    for bo3_id, m in raw.items():
        a = team_map.get(str(m.get("team1_id")))
        b = team_map.get(str(m.get("team2_id")))
        if not a or not b or not m.get("start_date"):
            skipped += 1
            continue
        row = (a, b, m["start_date"], m["bo_type"], m["status"],
               m["bo3_status"], team_map.get(str(m.get("winner_team_id"))),
               m["decided_by_default"], m["tier"], m["tier_rank"], m["slug"],
               m.get("tournament_id"), m.get("stage_id"))
        local = match_map.get(bo3_id)
        if local:
            to_update.append({
                "id": local, "status": m["status"],
                "bo3_status": m["bo3_status"],
                "winner_team_id": team_map.get(str(m.get("winner_team_id"))),
                "decided_by_default": m["decided_by_default"],
                "scheduled_at": m["start_date"], "bo_type": m["bo_type"],
                "tournament_id": m.get("tournament_id"),
                "stage_id": m.get("stage_id"),
            })
        elif m.get("slug"):
            to_insert.append((bo3_id, row))
        else:
            skipped += 1          # no slug means we cannot map the id back

    print(f"  new: {len(to_insert)}  existing: {len(to_update)}  "
          f"skipped: {skipped}")

    for i in range(0, len(to_insert), 500):
        chunk = to_insert[i:i + 500]
        with conn.cursor() as cur:
            cur.executemany(INSERT_MATCHES, [r for _, r in chunk])
        print(f"    inserted {min(i + 500, len(to_insert))}/{len(to_insert)}",
              flush=True)

    # Re-read by slug to recover ids -- executemany cannot RETURNING. Same
    # pattern as insert_teams, kept deliberately identical so there is one
    # way this codebase does batch-insert-then-link.
    if to_insert:
        slugs = [r[10] for _, r in to_insert]
        rows = conn.execute(
            "SELECT id, bo3_slug FROM matches WHERE bo3_slug = ANY(%s)",
            (slugs,)).fetchall()
        by_slug: dict[str, int] = {}
        for r in rows:
            by_slug.setdefault(r["bo3_slug"], r["id"])
        links = [(by_slug[r[10]], bid) for bid, r in to_insert
                 if r[10] in by_slug]
        for i in range(0, len(links), 500):
            with conn.cursor() as cur:
                cur.executemany(LINK_MATCH, links[i:i + 500])
        print(f"    linked {len(links)}")

    for i in range(0, len(to_update), 500):
        with conn.cursor() as cur:
            cur.executemany(UPDATE_MATCH, to_update[i:i + 500])
    if to_update:
        print(f"    updated {len(to_update)}")

    conn.commit()
    return len(to_insert) + len(to_update)


def resolve_all(conn, dry_run: bool, limit: int | None = None,
                history: bool = False) -> tuple[int, int]:
    """Match unresolved Kalshi events to bo3 matches.

    Candidates are loaded ONCE and windowed with bisect. The previous version
    ran two queries per event; at 47 events that was invisible, and for the
    ~6,000 historical events it is 12,000 round trips to a remote database.
    """
    import json

    # v_unresolved covers live events only. The history view drops the
    # status filter so settled events — which carry both a result and a
    # closing price, and are therefore the entire backtest dataset — can be
    # bound too. Separate views so a routine sync does not re-scan them.
    view = "v_unresolved_history" if history else "v_unresolved"
    q = f"SELECT * FROM {view} ORDER BY scheduled_at"
    if limit:
        q += f" LIMIT {int(limit)}"
    pending = conn.execute(q).fetchall()
    print(f"\n  unresolved Kalshi events: {len(pending)}")
    if not pending:
        return 0, 0

    all_cands = [dict(c) for c in conn.execute(CANDIDATES).fetchall()]
    starts = [c["scheduled_at"] for c in all_cands]
    print(f"  candidate matches loaded: {len(all_cands)}")

    bound = queued = 0
    for n, ev in enumerate(pending, 1):
        names = [x.strip() for x in (ev["teams"] or "").split(" vs ") if x.strip()]
        if len(names) != 2 or not ev["scheduled_at"]:
            continue
        a, b = names
        event_name = fx.extract_event_name(ev["rules_primary"])

        # Try the tight window, then WIDEN WHENEVER IT DID NOT ACCEPT -- not
        # only when it came back empty. The old `if not cands` gate worked
        # while the match table was sparse and silently became dead code
        # after the 365-day backfill: with 16k matches a +/-30min window is
        # never empty, so a correct fixture 31 minutes away was unreachable.
        # That regressed pairs which had previously resolved.
        cands = fx.window_slice(all_cands, starts, ev["scheduled_at"],
                                fx.WINDOW)
        decision = fx.resolve(a, b, event_name, cands, ev["scheduled_at"])

        if decision["verdict"] == "queue":
            wide = fx.window_slice(all_cands, starts, ev["scheduled_at"],
                                   fx.WIDE_WINDOW)
            if len(wide) > len(cands):
                wider = fx.resolve(a, b, event_name, wide, ev["scheduled_at"])
                if wider["verdict"] != "queue" or (
                        wider["best"] and decision["best"] and
                        wider["best"]["score"] > decision["best"]["score"]):
                    decision = wider
        best = decision["best"]
        verbose = len(pending) <= 100

        if decision["verdict"] in ("accept", "fuzzy") and best:
            if verbose:
                print(f"    BIND  {ev['event_ticker'][:44]:46} {a} vs {b}"
                      f"  score={best['score']:.2f} ({decision['reason']})")
            if not dry_run:
                conn.execute(BIND, (best["match_id"], ev["event_ticker"]))
            bound += 1
        else:
            if verbose:
                print(f"    QUEUE {ev['event_ticker'][:44]:46} {a} vs {b}"
                      f"  ({decision['reason']})")
            if not dry_run:
                payload = json.dumps(
                    [{k: (str(v) if not isinstance(v, (int, float, str, type(None))) else v)
                      for k, v in c.items()} for c in decision["ranked"]])
                conn.execute(QUEUE, (ev["event_ticker"], payload))
            queued += 1

        if not verbose and n % 500 == 0:
            print(f"    {n}/{len(pending)}  bound={bound} queued={queued}",
                  flush=True)

    if not dry_run:
        conn.commit()
    return bound, queued


def link_market_teams(conn) -> int:
    """Fill kalshi_markets.team_id for newly bound events.

    Mirrors scripts/link_market_teams.py; kept here so a sync never leaves
    bound markets with a NULL side, which reads downstream as "no data"
    rather than "not linked".
    """
    from edgedesk.resolve import clans

    rows = conn.execute("""
        SELECT km.ticker, km.team_name, km.team_abbr,
               m.team_a_id, ta.canonical_name AS a_name, ta.acronym AS a_acr,
               m.team_b_id, tb.canonical_name AS b_name, tb.acronym AS b_acr
        FROM kalshi_markets km
        JOIN matches m  ON m.id = km.match_id
        JOIN teams   ta ON ta.id = m.team_a_id
        JOIN teams   tb ON tb.id = m.team_b_id
        WHERE km.team_id IS NULL AND km.team_name IS NOT NULL
    """).fetchall()
    updates = []
    for r in rows:
        v = clans.assign_side(
            r["team_name"] or r["team_abbr"],
            (r["team_a_id"], r["a_name"], r["a_acr"]),
            (r["team_b_id"], r["b_name"], r["b_acr"]))
        if v["winner_team_id"] is not None:
            updates.append((v["winner_team_id"], r["ticker"]))
    for i in range(0, len(updates), 500):
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE kalshi_markets SET team_id=%s WHERE ticker=%s",
                updates[i:i + 500])
    conn.commit()
    return len(updates)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14,
                    help="sync window; use 365 for the historical backfill")
    ap.add_argument("--resolve-only", action="store_true")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap events resolved in one pass")
    ap.add_argument("--history", action="store_true",
                    help="also resolve settled events (the backtest dataset)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.dry_run and not a.resolve_only:
        print("NOTE: --dry-run writes no matches, so resolution afterwards "
              "has nothing to search and will report 'no candidates in "
              "window' for every event. That is the flag, not a failure.\n")

    with db.connect() as conn:
        run_id = None if a.dry_run else db.start_run(conn, "bo3")
        try:
            n = 0 if a.resolve_only else sync(conn, a.days, a.dry_run)
            bound, queued = resolve_all(conn, a.dry_run, a.limit, a.history)
            if not a.dry_run:
                # Newly bound events have markets with no team_id yet, and
                # a NULL there silently empties every price lookup that
                # joins on it. Cheap, so it runs every pass.
                linked = link_market_teams(conn)
                print(f"  market sides linked: {linked}")
            print(f"\nOK  {n} matches synced, {bound} bound, {queued} queued")
            if run_id is not None:
                db.finish_run(conn, run_id, "ok", n + bound)
        except Exception as exc:                             # noqa: BLE001
            if run_id is not None:
                db.finish_run(conn, run_id, "error", 0, str(exc))
            raise


if __name__ == "__main__":
    main()
