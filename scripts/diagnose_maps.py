#!/usr/bin/env python3
"""Audit ingested map rows. Read-only.

    python scripts/diagnose_maps.py

Answers two questions that a successful-looking bulk ingest cannot:

1. Did pagination silently truncate?  A chunk of 50 matches averages ~100
   games against a page size of 100, so the batch sits exactly on the
   boundary. Truncation would not error -- it would look like a Bo3 that
   only played one map. Comparing map count against the match's declared
   bo_type is what makes that visible.

2. Why is a winner unresolved?  An 11.5% unresolved rate is either fine
   (forfeits and missing tags, nothing to recover) or a scorer problem
   (real tags we failed to match). Those need completely different
   responses, and the aggregate number cannot tell them apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.resolve import clans                               # noqa: E402

SHAPE = """
SELECT m.format_bo, count(DISTINCT m.id) AS matches,
       count(mm.id) AS map_rows,
       round(count(mm.id)::numeric / NULLIF(count(DISTINCT m.id), 0), 2) AS avg_maps
FROM matches m
JOIN match_maps mm ON mm.match_id = m.id
GROUP BY 1 ORDER BY 1;
"""

SUSPECT_TRUNCATION = """
SELECT m.id, m.format_bo, m.bo3_slug, count(mm.id) AS maps
FROM matches m
JOIN match_maps mm ON mm.match_id = m.id
WHERE m.status = 'finished'
GROUP BY 1, 2, 3
HAVING count(mm.id) < CASE WHEN m.format_bo >= 3 THEN 2 ELSE 1 END
ORDER BY m.format_bo DESC
LIMIT 20;
"""

SPLIT = """
SELECT count(*)                                                  AS unresolved,
       count(*) FILTER (WHERE winner_clan_name IS NULL
                          OR winner_clan_name = '')              AS no_tag,
       count(*) FILTER (WHERE is_default)                        AS forfeit,
       count(*) FILTER (WHERE winner_clan_name IS NOT NULL
                          AND winner_clan_name <> ''
                          AND NOT is_default)                    AS real_tags
FROM match_maps WHERE winner_team_id IS NULL;
"""

SAMPLE = """
SELECT mm.winner_clan_name AS wc, mm.loser_clan_name AS lc,
       ta.canonical_name AS a, ta.acronym AS a_acr,
       tb.canonical_name AS b, tb.acronym AS b_acr,
       mm.is_default
FROM match_maps mm
JOIN matches m  ON m.id  = mm.match_id
JOIN teams   ta ON ta.id = m.team_a_id
JOIN teams   tb ON tb.id = m.team_b_id
WHERE mm.winner_team_id IS NULL
  AND mm.winner_clan_name IS NOT NULL AND mm.winner_clan_name <> ''
  AND NOT mm.is_default
LIMIT 40;
"""

MISSING_ACRONYM = """
SELECT count(*) FILTER (WHERE acronym IS NULL OR acronym = '') AS no_acronym,
       count(*) AS total FROM teams;
"""


def main():
    with db.connect() as conn:
        print("map rows by declared format")
        for r in conn.execute(SHAPE).fetchall():
            print(f"    bo{r['format_bo'] or '?':<3} {r['matches']:5} matches  "
                  f"{r['map_rows']:6} rows   avg {r['avg_maps']}")

        sus = conn.execute(SUSPECT_TRUNCATION).fetchall()
        print(f"\npossible truncation: {len(sus)} finished matches have fewer "
              f"maps than their format implies")
        for r in sus[:10]:
            print(f"    bo{r['format_bo']}  {r['maps']} map(s)  {r['bo3_slug']}")
        if not sus:
            print("    none — pagination looks intact")

        s = conn.execute(SPLIT).fetchone()
        print(f"\nunresolved winners: {s['unresolved']}")
        print(f"    no clan tag at all : {s['no_tag']:5}   (nothing to recover)")
        print(f"    forfeit / defwin   : {s['forfeit']:5}   (expected)")
        print(f"    real tags, failed  : {s['real_tags']:5}   <- the fixable ones")

        acr = conn.execute(MISSING_ACRONYM).fetchone()
        print(f"\nteams without an acronym: {acr['no_acronym']}/{acr['total']} "
              "(acronym is a second alias; missing one weakens every match)")

        rows = conn.execute(SAMPLE).fetchall()
        if rows:
            print(f"\nsample of fixable failures, rescored:")
            print(f"    {'winner tag':16} {'loser tag':16} "
                  f"{'team A':22} {'team B':22} score")
            for r in rows:
                v = clans.assign(r["wc"], r["lc"],
                                 (1, r["a"], r["a_acr"]),
                                 (2, r["b"], r["b_acr"]))
                print(f"    {str(r['wc'])[:15]:16} {str(r['lc'])[:15]:16} "
                      f"{str(r['a'])[:21]:22} {str(r['b'])[:21]:22} "
                      f"{v['score']}")


if __name__ == "__main__":
    main()
