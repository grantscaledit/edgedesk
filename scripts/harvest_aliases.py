#!/usr/bin/env python3
"""Learn team aliases from observed in-game clan tags.

    python scripts/harvest_aliases.py --dry-run
    python scripts/harvest_aliases.py

Every resolved map row pairs a team with the clan tag that side actually
played under. Collected across a season those tags are a better alias
dictionary than anything we could invent, and they cost nothing extra to
obtain -- the map ingest already fetched them.

Only tags from maps whose winner we bound are used. Learning aliases from
rows we could not resolve would teach the resolver its own mistakes, which
compounds: a wrong alias makes the next wrong binding easier.

Tags equal to the team's existing name or acronym are skipped -- they add a
row without adding information.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.resolve.fixtures import normalise                  # noqa: E402

# Winner side: the tag and the team we bound it to.
# Loser side: match_maps stores no loser_team_id, so it is the other side of
# the match -- derived here rather than stored, to keep one source of truth.
OBSERVED = """
SELECT mm.winner_clan_name AS tag, mm.winner_team_id AS team_id
FROM match_maps mm
WHERE mm.winner_team_id IS NOT NULL
  AND mm.winner_clan_name IS NOT NULL AND mm.winner_clan_name <> ''
UNION ALL
SELECT mm.loser_clan_name AS tag,
       CASE WHEN mm.winner_team_id = m.team_a_id THEN m.team_b_id
            ELSE m.team_a_id END AS team_id
FROM match_maps mm
JOIN matches m ON m.id = mm.match_id
WHERE mm.winner_team_id IS NOT NULL
  AND mm.loser_clan_name IS NOT NULL AND mm.loser_clan_name <> '';
"""

KNOWN = "SELECT id, canonical_name, acronym FROM teams;"

UPSERT = """
INSERT INTO team_aliases (team_id, alias_name, alias_norm, source, times_seen)
VALUES (%s, %s, %s, 'clan', %s)
ON CONFLICT (team_id, alias_norm) DO UPDATE
  SET times_seen = team_aliases.times_seen + EXCLUDED.times_seen,
      last_seen  = now();
"""

AMBIGUOUS = """
SELECT alias_norm, count(DISTINCT team_id) AS teams
FROM team_aliases GROUP BY 1 HAVING count(DISTINCT team_id) > 1
ORDER BY 2 DESC LIMIT 10;
"""

COVERAGE = """
SELECT count(DISTINCT t.id) FILTER (
         WHERE t.acronym IS NULL OR t.acronym = '')              AS no_acronym,
       count(DISTINCT t.id) FILTER (
         WHERE (t.acronym IS NULL OR t.acronym = '')
           AND a.team_id IS NOT NULL)                            AS rescued,
       count(DISTINCT t.id)                                      AS total
FROM teams t
LEFT JOIN v_team_aliases a ON a.team_id = t.id;
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-seen", type=int, default=1,
                    help="only keep aliases observed at least this often")
    a = ap.parse_args()

    with db.connect() as conn:
        teams = {r["id"]: r for r in conn.execute(KNOWN).fetchall()}
        rows = conn.execute(OBSERVED).fetchall()
        print(f"  observed clan tags: {len(rows)}")

        counts: dict[tuple[int, str], list] = {}
        skipped_known = 0
        for r in rows:
            tid, tag = r["team_id"], (r["tag"] or "").strip()
            norm = normalise(tag)
            if not norm or tid not in teams:
                continue
            t = teams[tid]
            if norm in (normalise(t["canonical_name"]), normalise(t["acronym"])):
                skipped_known += 1
                continue
            key = (tid, norm)
            if key in counts:
                counts[key][1] += 1
            else:
                counts[key] = [tag, 1]

        kept = {k: v for k, v in counts.items() if v[1] >= a.min_seen}
        print(f"  already known (name/acronym): {skipped_known}")
        print(f"  distinct new aliases: {len(kept)}")

        sample = sorted(kept.items(), key=lambda kv: -kv[1][1])[:15]
        print("\n  most-seen new aliases")
        for (tid, norm), (tag, n) in sample:
            t = teams[tid]
            print(f"    {tag[:22]:24} -> {str(t['canonical_name'])[:26]:28} "
                  f"x{n}")

        if a.dry_run:
            print("\nOK  dry run, nothing written")
            return

        payload = [(tid, tag, norm, n) for (tid, norm), (tag, n) in kept.items()]
        for i in range(0, len(payload), 500):
            with conn.cursor() as cur:
                cur.executemany(UPSERT, payload[i:i + 500])
        conn.commit()

        amb = conn.execute(AMBIGUOUS).fetchall()
        print(f"\n  ambiguous tags (excluded from matching): {len(amb)}")
        for r in amb:
            print(f"    {r['alias_norm'][:28]:30} used by {r['teams']} teams")

        cov = conn.execute(COVERAGE).fetchone()
        print(f"\nOK  {len(payload)} aliases stored")
        print(f"  teams with no acronym: {cov['no_acronym']}/{cov['total']}"
              f"  —  {cov['rescued']} of them now have at least one alias")


if __name__ == "__main__":
    main()
