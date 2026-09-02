#!/usr/bin/env python3
"""Ingest bo3 players and snapshot rosters when they change.

    python scripts/sync_players.py --dry-run
    python scripts/sync_players.py

bo3 cannot filter players by team (the filter is silently ignored), so this
pulls the whole ~20k player table and indexes it locally. That is ~204 pages,
roughly 90 seconds, and only needs running when rosters are refreshed --
daily is plenty.

A roster snapshot is written ONLY when a team's lineup differs from the last
one stored. bo3 exposes today's lineup and no history, so roster history is
something we can only accumulate going forward. Every day this does not run
is a roster change that cannot be recovered -- the same argument that put
the Kalshi collector first.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.sources import bo3                                 # noqa: E402

UPSERT_PLAYER = """
INSERT INTO players (bo3_id, nickname, first_name, last_name, country_id,
                     birthday, role, slug, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (bo3_id) DO UPDATE
  SET nickname = EXCLUDED.nickname,
      first_name = EXCLUDED.first_name,
      last_name = EXCLUDED.last_name,
      country_id = EXCLUDED.country_id,
      role = EXCLUDED.role,
      updated_at = now();
"""

CURRENT_ROSTERS = """
SELECT team_id, array_agg(player_id ORDER BY player_id) AS players
FROM v_roster_latest GROUP BY team_id;
"""

INSERT_ROSTER = """
INSERT INTO roster_snapshots (team_id, player_id, captured_at, source)
VALUES (%s, %s, now(), 'bo3');
"""

TEAM_MAP = "SELECT external_id, team_id FROM team_external_ids WHERE source='bo3';"
PLAYER_MAP = "SELECT bo3_id, id FROM players WHERE bo3_id IS NOT NULL;"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with db.connect() as conn:
        run_id = None if a.dry_run else db.start_run(conn, "bo3_players")
        try:
            raw = []
            with bo3.Bo3() as client:
                for p in client.iter_players(progress=True):
                    parsed = bo3.parse_player(p)
                    if parsed["bo3_id"] and parsed["nickname"]:
                        raw.append(parsed)
            print(f"  players fetched: {len(raw)}")
            with_team = [p for p in raw if p["team_bo3_id"]]
            print(f"  with a current team: {len(with_team)}")

            if a.dry_run:
                print("\n  sample:")
                for p in with_team[:5]:
                    name = " ".join(x for x in (p["first_name"], p["last_name"]) if x)
                    print(f"    {p['nickname']:<18} {name:<26} "
                          f"team={p['team_bo3_id']}")
                print("\nOK  dry run, nothing written")
                return

            rows = [(p["bo3_id"], p["nickname"], p["first_name"],
                     p["last_name"], p["country_id"], p["birthday"],
                     p["role"], p["slug"]) for p in raw]
            for i in range(0, len(rows), 500):
                with conn.cursor() as cur:
                    cur.executemany(UPSERT_PLAYER, rows[i:i + 500])
                print(f"    players {min(i + 500, len(rows))}/{len(rows)}",
                      flush=True)
            conn.commit()

            team_map = {r["external_id"]: r["team_id"] for r in
                        conn.execute(TEAM_MAP).fetchall()}
            player_map = {r["bo3_id"]: r["id"] for r in
                          conn.execute(PLAYER_MAP).fetchall()}
            existing = {r["team_id"]: set(r["players"]) for r in
                        conn.execute(CURRENT_ROSTERS).fetchall()}

            proposed: dict[int, set[int]] = {}
            for p in with_team:
                tid = team_map.get(str(p["team_bo3_id"]))
                pid = player_map.get(p["bo3_id"])
                if tid and pid:
                    proposed.setdefault(tid, set()).add(pid)

            changed = {t: pl for t, pl in proposed.items()
                       if existing.get(t) != pl}
            print(f"  teams with a roster: {len(proposed)}   "
                  f"changed since last snapshot: {len(changed)}")

            inserts = [(t, p) for t, players in changed.items()
                       for p in players]
            for i in range(0, len(inserts), 500):
                with conn.cursor() as cur:
                    cur.executemany(INSERT_ROSTER, inserts[i:i + 500])
            conn.commit()

            new_teams = sum(1 for t in changed if t not in existing)
            print(f"\nOK  {len(raw)} players, {len(inserts)} roster rows "
                  f"across {len(changed)} teams "
                  f"({new_teams} first-ever snapshots)")
            if new_teams == len(changed) and existing:
                print("  (first run for these teams — later runs will show "
                      "only genuine changes)")
            if run_id is not None:
                db.finish_run(conn, run_id, "ok", len(raw))
        except Exception as exc:                                 # noqa: BLE001
            if run_id is not None:
                db.finish_run(conn, run_id, "error", 0, str(exc))
            raise


if __name__ == "__main__":
    main()
