#!/usr/bin/env python3
"""Fill kalshi_markets.team_id — which side of the match each market is.

    python scripts/link_market_teams.py --dry-run
    python scripts/link_market_teams.py

The column has existed since the first migration and nothing ever wrote to
it. Two things depended on it and both failed silently:

  * decisions.market_prob_for could never find a quote, so EVERY logged
    decision recorded a NULL market price — the one number that makes a
    Brier score mean anything.
  * vs_market.py filtered on it and reported zero settled events, which
    read as "the board is too young" rather than "the join is empty".

Neither errored. That is the failure mode worth remembering: a NULL column
in a WHERE clause returns no rows, and no rows looks exactly like no data.

Each market is one side of an event already bound to a bo3 match, so the
candidate pool is exactly two teams. Ambiguous names refuse rather than
guess: a market bound to the wrong side would invert every price it feeds.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.resolve import clans                               # noqa: E402

UNLINKED = """
SELECT km.ticker, km.team_name, km.team_abbr,
       m.team_a_id, ta.canonical_name AS a_name, ta.acronym AS a_acr,
       m.team_b_id, tb.canonical_name AS b_name, tb.acronym AS b_acr,
       (SELECT string_agg(alias_name, ',') FROM v_team_aliases
        WHERE team_id = ta.id) AS a_aliases,
       (SELECT string_agg(alias_name, ',') FROM v_team_aliases
        WHERE team_id = tb.id) AS b_aliases
FROM kalshi_markets km
JOIN matches m  ON m.id = km.match_id
JOIN teams   ta ON ta.id = m.team_a_id
JOIN teams   tb ON tb.id = m.team_b_id
WHERE km.team_id IS NULL AND km.team_name IS NOT NULL;
"""

SET_TEAM = "UPDATE kalshi_markets SET team_id = %s WHERE ticker = %s;"

COVERAGE = """
SELECT count(*) FILTER (WHERE team_id IS NOT NULL) AS linked,
       count(*) FILTER (WHERE match_id IS NOT NULL) AS bound,
       count(*) AS total
FROM kalshi_markets;
"""


def aliases(row, side):
    """Registered name, acronym, and any harvested clan tags."""
    extra = row[f"{side}_aliases"] or ""
    return tuple(x for x in ([row[f"{side}_name"], row[f"{side}_acr"]]
                             + [t.strip() for t in extra.split(",") if t.strip()])
                 if x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(UNLINKED).fetchall()]
        print(f"  markets bound to a match but with no team_id: {len(rows)}")

        updates, unresolved = [], []
        for r in rows:
            v = clans.assign_side(
                r["team_name"] or r["team_abbr"],
                (r["team_a_id"], *aliases(r, "a")),
                (r["team_b_id"], *aliases(r, "b")))
            if v["winner_team_id"] is None:
                unresolved.append(r)
            else:
                updates.append((v["winner_team_id"], r["ticker"]))

        print(f"  resolved: {len(updates)}   ambiguous, left NULL: "
              f"{len(unresolved)}")
        if unresolved:
            print("\n  a sample of the refusals (left unlinked on purpose):")
            for r in unresolved[:8]:
                print(f"    {str(r['team_name'])[:22]:24} between "
                      f"{str(r['a_name'])[:20]:22} and {r['b_name']}")

        if a.dry_run:
            print("\nOK  dry run, nothing written")
            return

        for i in range(0, len(updates), 500):
            with conn.cursor() as cur:
                cur.executemany(SET_TEAM, updates[i:i + 500])
            print(f"    linked {min(i + 500, len(updates))}/{len(updates)}",
                  flush=True)
        conn.commit()

        cov = conn.execute(COVERAGE).fetchone()
        print(f"\nOK  {cov['linked']}/{cov['bound']} bound markets now carry "
              f"a team_id  ({cov['total']} markets total)")
        if cov["linked"]:
            print("  Decision logging can now capture a market price, and "
                  "vs_market.py\n  has something to test against.")


if __name__ == "__main__":
    main()
