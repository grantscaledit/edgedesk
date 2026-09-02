#!/usr/bin/env python3
"""Ingest tournaments and stage titles, then attach them to matches.

    python scripts/sync_tournaments.py --dry-run
    python scripts/sync_tournaments.py

Run this BEFORE resolution. Until it has run, every match has a NULL event
name, event_score is 0, and no candidate can score above 0.90 against an
ACCEPT threshold of 0.85 -- which quietly makes the real bar a team score of
0.944 instead of 0.85.

~3.1k tournaments and ~10.3k stages: about 130 pages, roughly a minute.
Tournaments change slowly, so daily is ample.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.sources import bo3                                 # noqa: E402

UPSERT = """
INSERT INTO tournaments (bo3_id, name, slug, tier, tier_rank, series_id,
                         region_id, event_type, event_level, prize, status,
                         start_date, end_date, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (bo3_id) DO UPDATE
  SET name = EXCLUDED.name, tier = EXCLUDED.tier,
      tier_rank = EXCLUDED.tier_rank, status = EXCLUDED.status,
      prize = EXCLUDED.prize, end_date = EXCLUDED.end_date,
      updated_at = now();
"""

SET_STAGE_TITLE = "UPDATE matches SET stage_title = %s WHERE bo3_stage_id = %s;"

COVERAGE = """
SELECT count(*) FILTER (WHERE bo3_tournament_id IS NOT NULL) AS with_tid,
       count(*) FILTER (WHERE stage_title IS NOT NULL)       AS with_stage,
       count(*)                                              AS total
FROM matches;
"""

JOINABLE = """
SELECT count(*) AS n FROM matches m
JOIN tournaments t ON t.bo3_id = m.bo3_tournament_id;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with db.connect() as conn:
        run_id = None if a.dry_run else db.start_run(conn, "bo3_tournaments")
        try:
            with bo3.Bo3() as client:
                print("  fetching tournaments...")
                tours = [bo3.parse_tournament(t)
                         for t in client.iter_tournaments(progress=True)]
                tours = [t for t in tours if t["bo3_id"] and t["name"]]
                print(f"  tournaments: {len(tours)}")

                print("  fetching stages...")
                stages = [bo3.parse_stage(s)
                          for s in client.iter_stages(progress=True)]
                stages = [s for s in stages if s["bo3_id"] and s["title"]]
                print(f"  stages: {len(stages)}")

            if a.dry_run:
                print("\n  sample tournaments:")
                for t in tours[:5]:
                    print(f"    [{t['tier']}{t['tier_rank']}] {t['name'][:58]}")
                print("\n  sample stages:")
                for s in stages[:5]:
                    print(f"    {s['title'][:58]}  ({s['format_type']})")
                print("\nOK  dry run, nothing written")
                return

            rows = [(t["bo3_id"], t["name"], t["slug"], t["tier"],
                     t["tier_rank"], t["series_id"], t["region_id"],
                     t["event_type"], t["event_level"], t["prize"],
                     t["status"], t["start_date"], t["end_date"])
                    for t in tours]
            for i in range(0, len(rows), 500):
                with conn.cursor() as cur:
                    cur.executemany(UPSERT, rows[i:i + 500])
                print(f"    tournaments {min(i + 500, len(rows))}/{len(rows)}",
                      flush=True)
            conn.commit()

            titles = [(s["title"], s["bo3_id"]) for s in stages]
            for i in range(0, len(titles), 500):
                with conn.cursor() as cur:
                    cur.executemany(SET_STAGE_TITLE, titles[i:i + 500])
                print(f"    stage titles {min(i + 500, len(titles))}/"
                      f"{len(titles)}", flush=True)
            conn.commit()

            cov = conn.execute(COVERAGE).fetchone()
            joinable = conn.execute(JOINABLE).fetchone()["n"]
            print(f"\nOK  {len(tours)} tournaments, {len(stages)} stages")
            print(f"  matches with a tournament id : "
                  f"{cov['with_tid']}/{cov['total']}")
            print(f"  matches joinable to a tournament: {joinable}")
            print(f"  matches with a stage title   : "
                  f"{cov['with_stage']}/{cov['total']}")
            if not cov["with_tid"]:
                print("\n  NOTE: no match carries bo3_tournament_id yet. "
                      "Re-run phase1_sync\n  to populate it, then run this "
                      "again:\n    python scripts/phase1_sync.py --days 365")
            if run_id is not None:
                db.finish_run(conn, run_id, "ok", len(tours))
        except Exception as exc:                                 # noqa: BLE001
            if run_id is not None:
                db.finish_run(conn, run_id, "error", 0, str(exc))
            raise


if __name__ == "__main__":
    main()
