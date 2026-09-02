#!/usr/bin/env python3
"""Ingest per-map results from bo3 into match_maps.

    python scripts/sync_maps.py                # matches missing map rows
    python scripts/sync_maps.py --limit 500    # cap the batch
    python scripts/sync_maps.py --since 365    # widen the lookback
    python scripts/sync_maps.py --dry-run      # report, write nothing

Standalone rather than folded into phase1_sync because map ingest has a
different shape: only FINISHED matches have maps, and once ingested they
never change, so this is a backfill-and-catch-up job rather than a poll.

Cost note: `filter[games.match_id][in]` is honoured (verified 2026-09), so
this fetches 50 matches per request instead of one. That is the difference
between minutes and hours on a 12-month backfill.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.sources import bo3                                 # noqa: E402
from edgedesk.resolve import clans                               # noqa: E402

# Finished matches that bo3 knows about and we have no map rows for.
NEEDS_MAPS = """
SELECT m.id            AS match_id,
       x.external_id   AS bo3_id,
       m.team_a_id, ta.canonical_name AS a_name, ta.acronym AS a_acr,
       m.team_b_id, tb.canonical_name AS b_name, tb.acronym AS b_acr,
       m.decided_by_default
FROM matches m
JOIN match_external_ids x ON x.match_id = m.id AND x.source = 'bo3'
JOIN teams ta ON ta.id = m.team_a_id
JOIN teams tb ON tb.id = m.team_b_id
LEFT JOIN match_maps mm ON mm.match_id = m.id
WHERE m.status = 'finished'
  AND m.scheduled_at > now() - (%s || ' days')::interval
  AND mm.id IS NULL
ORDER BY m.scheduled_at DESC
LIMIT %s;
"""

INSERT_MAP = """
INSERT INTO match_maps
  (match_id, map_index, map_name, team_a_rounds, team_b_rounds,
   winner_team_id, is_default, winner_clan_name, loser_clan_name,
   rounds_count, side_assignment)
VALUES (%(match_id)s, %(map_index)s, %(map_name)s, %(a_rounds)s,
        %(b_rounds)s, %(winner_team_id)s, %(is_default)s,
        %(winner_clan)s, %(loser_clan)s, %(rounds_count)s, %(confidence)s)
ON CONFLICT (match_id, map_index) DO NOTHING;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=400,
                    help="lookback in days (default 400)")
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--chunk", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with db.connect() as conn:
        run_id = None if a.dry_run else db.start_run(conn, "bo3_maps")
        try:
            todo = conn.execute(NEEDS_MAPS, (str(a.since), a.limit)).fetchall()
            print(f"  matches needing maps: {len(todo)}")
            if not todo:
                print("OK  nothing to do")
                if run_id is not None:
                    db.finish_run(conn, run_id, "ok", 0)
                return

            by_bo3 = {str(r["bo3_id"]): dict(r) for r in todo}
            ids = [int(k) for k in by_bo3]

            rows, unresolved, skipped = [], 0, 0
            with bo3.Bo3() as client:
                for g in client.games_batch(ids, chunk=a.chunk, progress=True):
                    game = bo3.parse_game(g)
                    m = by_bo3.get(str(game["match_bo3_id"]))
                    if not m or game["map_index"] is None:
                        skipped += 1
                        continue

                    verdict = clans.assign(
                        game["winner_clan_name"], game["loser_clan_name"],
                        (m["team_a_id"], m["a_name"], m["a_acr"]),
                        (m["team_b_id"], m["b_name"], m["b_acr"]),
                    )
                    if verdict["confidence"] == "unresolved":
                        unresolved += 1

                    a_r, b_r = clans.rounds_for(
                        game["winner_score"], game["loser_score"],
                        verdict["winner_team_id"], m["team_a_id"])

                    rows.append({
                        "match_id": m["match_id"],
                        "map_index": game["map_index"],
                        "map_name": game["map_name"],
                        "a_rounds": a_r,
                        "b_rounds": b_r,
                        "winner_team_id": verdict["winner_team_id"],
                        "is_default": bool(m["decided_by_default"]),
                        "winner_clan": game["winner_clan_name"],
                        "loser_clan": game["loser_clan_name"],
                        "rounds_count": game["rounds_count"],
                        "confidence": verdict["confidence"],
                    })

            print(f"  games parsed: {len(rows)}  "
                  f"unresolved winner: {unresolved}  skipped: {skipped}")

            if a.dry_run:
                print("OK  dry run, nothing written")
                return

            # One executemany, not one round trip per row -- a few thousand
            # sequential inserts against Neon is minutes of silence.
            for i in range(0, len(rows), 500):
                with conn.cursor() as cur:
                    cur.executemany(INSERT_MAP, rows[i:i + 500])
                print(f"    wrote {min(i + 500, len(rows))}/{len(rows)}",
                      flush=True)
            conn.commit()

            pct = 100 * unresolved / len(rows) if rows else 0
            print(f"\nOK  {len(rows)} map rows across {len(todo)} matches "
                  f"({pct:.1f}% unresolved winner)")
            if pct > 15:
                print("  WARNING: high unresolved rate. Clan tags may have "
                      "drifted from registered names -- inspect before "
                      "trusting map-level stats.")
            if run_id is not None:
                db.finish_run(conn, run_id, "ok", len(rows))
        except Exception as exc:                                 # noqa: BLE001
            if run_id is not None:
                db.finish_run(conn, run_id, "error", 0, str(exc))
            raise


if __name__ == "__main__":
    main()
