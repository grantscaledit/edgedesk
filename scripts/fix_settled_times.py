#!/usr/bin/env python3
"""Undo the damage from deriving settled start times off close_time.

    python scripts/fix_settled_times.py --dry-run
    python scripts/fix_settled_times.py

Settled markets carry can_close_early, so Kalshi rewrites close_time to the
moment a winner was declared. Deriving `close_time - 48h` put every settled
event's start about two days early, which is enough to land the
fixture-tuple window on the wrong day.

Two consequences to undo:

1. scheduled_at is wrong on ~12k rows. Re-collect with the settled sweep
   first so expiration_time is populated, then this re-derives from it.

2. Sixteen historical events were BOUND under the wrong window, and a bad
   link is worse than no link — it produces a confident dossier about a
   different match. Those bindings are cleared so they can be re-resolved
   against correct times. Live events, which bound at ~98% under a
   close_time that was still accurate, are left alone.

Run order:
    python scripts/phase0_collect.py --settled     # fills expiration_time
    python scripts/fix_settled_times.py
    python scripts/phase1_sync.py --resolve-only --history
    python scripts/link_market_teams.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402

COVERAGE = """
SELECT count(*) FILTER (WHERE expiration_time IS NOT NULL) AS with_exp,
       count(*) AS total
FROM kalshi_markets;
"""

DRIFT = """
SELECT count(*) AS n,
       round(avg(EXTRACT(EPOCH FROM
            (expiration_time - interval '48 hours') - scheduled_at))/3600, 1)
         AS mean_hours_off
FROM kalshi_markets
WHERE expiration_time IS NOT NULL
  AND scheduled_at IS DISTINCT FROM (expiration_time - interval '48 hours');
"""

REDERIVE = """
UPDATE kalshi_markets
SET scheduled_at = expiration_time - interval '48 hours', updated_at = now()
WHERE expiration_time IS NOT NULL
  AND scheduled_at IS DISTINCT FROM (expiration_time - interval '48 hours');
"""

# Only events that settled — live bindings were made against a close_time
# that was still correct and are not in question.
SUSPECT = """
SELECT count(DISTINCT event_ticker) AS events
FROM kalshi_markets
WHERE match_id IS NOT NULL AND status = 'finalized';
"""

UNBIND = """
UPDATE kalshi_markets SET match_id = NULL, team_id = NULL, updated_at = now()
WHERE match_id IS NOT NULL AND status = 'finalized';
"""

CLEAR_QUEUE = """
DELETE FROM resolution_queue
WHERE kalshi_event_ticker IN (
  SELECT DISTINCT event_ticker FROM kalshi_markets WHERE status = 'finalized');
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-bindings", action="store_true",
                    help="re-derive times but do not clear settled bindings")
    a = ap.parse_args()

    with db.connect() as conn:
        cov = conn.execute(COVERAGE).fetchone()
        print(f"  markets with expiration_time: "
              f"{cov['with_exp']}/{cov['total']}")
        if not cov["with_exp"]:
            print("\n  None yet. Run the settled sweep first, which is what "
                  "fetches it:\n    python scripts/phase0_collect.py --settled\n")
            return

        drift = conn.execute(DRIFT).fetchone()
        print(f"  rows with a wrong scheduled_at: {drift['n']}"
              + (f"   mean error {drift['mean_hours_off']:+.1f}h"
                 if drift["n"] else ""))
        suspect = conn.execute(SUSPECT).fetchone()["events"]
        print(f"  settled events bound under the wrong window: {suspect}")

        if a.dry_run:
            print("\nOK  dry run, nothing written")
            return

        conn.execute(REDERIVE)
        print(f"  re-derived {drift['n']} start times from expiration_time")

        if not a.keep_bindings and suspect:
            conn.execute(UNBIND)
            conn.execute(CLEAR_QUEUE)
            print(f"  cleared {suspect} settled binding(s) and their queue "
                  "rows for re-resolution")
        conn.commit()

    print("\nOK  now re-resolve against corrected times:")
    print("    python scripts/phase1_sync.py --resolve-only --history")
    print("    python scripts/link_market_teams.py")
    print("    python scripts/vs_market.py\n")


if __name__ == "__main__":
    main()
