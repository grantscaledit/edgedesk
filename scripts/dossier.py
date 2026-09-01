#!/usr/bin/env python3
"""Phase 1 CLI dossier.

    python scripts/dossier.py                       # today's slate
    python scripts/dossier.py --gate                # gate-passing only
    python scripts/dossier.py KXCS2GAME-26SEP...    # one match

Phase 1 shows what the spine knows: teams, event, format, price, gate status,
recent form, head-to-head, forfeit history. Team and player statistics arrive
in Phase 2 with the HLTV layer.

Display contract: every rate carries its sample size. A rate without an n is
a claim, not information.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                      # noqa: E402

SLATE = """
SELECT s.event_ticker,
       string_agg(s.team_name || ' ' || s.yes_ask || 'c', '  |  '
                  ORDER BY s.yes_ask DESC)      AS sides,
       MIN(s.scheduled_at)                      AS scheduled_at,
       MIN(s.spread_cents)                      AS spread,
       MIN(s.overround)                         AS overround,
       MAX(s.volume)                            AS volume,
       bool_or(s.gate_pass)                     AS gate_pass,
       MAX(km.match_id)                         AS match_id
FROM v_slate s
JOIN kalshi_markets km ON km.ticker = s.ticker
WHERE s.series_ticker = 'KXCS2GAME'
  AND s.scheduled_at BETWEEN now() - interval '2 hours' AND now() + interval '48 hours'
GROUP BY s.event_ticker
ORDER BY bool_or(s.gate_pass) DESC, MIN(s.scheduled_at);
"""

MATCH = """
SELECT m.id, m.scheduled_at, m.format_bo, m.status, m.bo3_status, m.tier,
       m.decided_by_default, m.bo3_slug,
       ta.id AS a_id, ta.canonical_name AS a_name, ta.bo3_rank AS a_rank,
       tb.id AS b_id, tb.canonical_name AS b_name, tb.bo3_rank AS b_rank
FROM matches m
JOIN teams ta ON ta.id = m.team_a_id
JOIN teams tb ON tb.id = m.team_b_id
WHERE m.id = %s;
"""

FORM = """
SELECT m.scheduled_at, m.status, m.decided_by_default, m.format_bo,
       CASE WHEN m.team_a_id = %(team)s THEN tb.canonical_name
            ELSE ta.canonical_name END        AS opponent,
       CASE WHEN m.winner_team_id = %(team)s THEN 'W'
            WHEN m.winner_team_id IS NULL     THEN '-'
            ELSE 'L' END                      AS result
FROM matches m
JOIN teams ta ON ta.id = m.team_a_id
JOIN teams tb ON tb.id = m.team_b_id
WHERE (m.team_a_id = %(team)s OR m.team_b_id = %(team)s)
  AND m.scheduled_at < now() AND m.status = 'finished'
ORDER BY m.scheduled_at DESC LIMIT 10;
"""

RISK = """
SELECT
  COUNT(*)                                                  AS played,
  COUNT(*) FILTER (WHERE decided_by_default)                AS forfeits,
  COUNT(*) FILTER (WHERE scheduled_at > now() - interval '72 hours') AS last_72h,
  COUNT(*) FILTER (WHERE scheduled_at > now() - interval '7 days')   AS last_7d
FROM matches
WHERE (team_a_id = %(team)s OR team_b_id = %(team)s)
  AND scheduled_at < now() AND status = 'finished';
"""

H2H = """
SELECT m.scheduled_at, m.format_bo, m.decided_by_default,
       CASE WHEN m.winner_team_id = %(a)s THEN 'A'
            WHEN m.winner_team_id = %(b)s THEN 'B' ELSE '-' END AS winner
FROM matches m
WHERE m.status='finished'
  AND ((m.team_a_id=%(a)s AND m.team_b_id=%(b)s)
    OR (m.team_a_id=%(b)s AND m.team_b_id=%(a)s))
ORDER BY m.scheduled_at DESC LIMIT 10;
"""


def rule(char="─", n=74):
    print(char * n)


def show_slate(conn, gate_only: bool):
    rows = conn.execute(SLATE).fetchall()
    if gate_only:
        rows = [r for r in rows if r["gate_pass"]]
    rule("━")
    print(f"  CS2 SLATE — next 48h        {len(rows)} events"
          f"{'  (gate-passing only)' if gate_only else ''}")
    rule("━")
    print(f"  {'GATE':5} {'START (UTC)':17} {'SPR':>4} {'OVR':>4} {'VOL':>9}  MATCH")
    rule()
    for r in rows:
        flag = "PASS " if r["gate_pass"] else "  -  "
        link = "linked" if r["match_id"] else "UNLINKED"
        when = r["scheduled_at"].strftime("%m-%d %H:%M") if r["scheduled_at"] else "?"
        vol = f"{float(r['volume'] or 0):,.0f}"
        print(f"  {flag} {when:17} {r['spread']:>4} {r['overround'] or 0:>4} {vol:>9}  {link}")
        print(f"        {r['sides']}")
        if r["match_id"]:
            print(f"        dossier: python scripts/dossier.py --match {r['match_id']}")
        print()


def show_match(conn, match_id: int):
    m = conn.execute(MATCH, (match_id,)).fetchone()
    if not m:
        print(f"no match {match_id}")
        return

    rule("━")
    print(f"  {m['a_name']}  vs  {m['b_name']}")
    rule("━")
    fmt = f"Bo{m['format_bo']}" if m["format_bo"] else "format ?"
    when = m["scheduled_at"].strftime("%Y-%m-%d %H:%M UTC") if m["scheduled_at"] else "?"
    print(f"  {when}   {fmt}   tier {m['tier'] or '?'}   status {m['bo3_status'] or m['status']}")
    if m["decided_by_default"]:
        print("  ** DECIDED BY DEFAULT (forfeit) **")
    print(f"  bo3: {m['bo3_slug'] or '-'}")

    for side, tid, name, rank in (("A", m["a_id"], m["a_name"], m["a_rank"]),
                                  ("B", m["b_id"], m["b_name"], m["b_rank"])):
        print()
        rule()
        print(f"  [{side}] {name}    bo3 rank {rank if rank is not None else 'unranked'}")
        rule()

        risk = conn.execute(RISK, {"team": tid}).fetchone()
        n = risk["played"] or 0
        ff = risk["forfeits"] or 0
        ff_rate = f"{ff / n:.1%}" if n else "n/a"
        print(f"  forfeits      {ff}/{n} ({ff_rate})     "
              f"[n={n}] {'** ELEVATED **' if n and ff / n > 0.05 else ''}")
        print(f"  fatigue       {risk['last_72h']} in 72h, {risk['last_7d']} in 7d")

        form = conn.execute(FORM, {"team": tid}).fetchall()
        if form:
            seq = "".join(f["result"] for f in form)
            wins = seq.count("W")
            print(f"  form          {seq}  ({wins}/{len(form)}) [n={len(form)}]")
            for f in form[:5]:
                d = f["scheduled_at"].strftime("%m-%d")
                dflt = " DEFAULT" if f["decided_by_default"] else ""
                print(f"                  {d}  {f['result']}  vs {f['opponent']}"
                      f"  Bo{f['format_bo'] or '?'}{dflt}")
        else:
            print("  form          no finished matches in the local window [n=0]")

    print()
    rule()
    h2h = conn.execute(H2H, {"a": m["a_id"], "b": m["b_id"]}).fetchall()
    if h2h:
        a_w = sum(1 for h in h2h if h["winner"] == "A")
        b_w = sum(1 for h in h2h if h["winner"] == "B")
        print(f"  HEAD TO HEAD  {m['a_name']} {a_w} - {b_w} {m['b_name']}  [n={len(h2h)}]")
        for h in h2h:
            print(f"                  {h['scheduled_at'].strftime('%Y-%m-%d')}  "
                  f"{'A' if h['winner']=='A' else 'B' if h['winner']=='B' else '?'} won"
                  f"{'  DEFAULT' if h['decided_by_default'] else ''}")
    else:
        print("  HEAD TO HEAD  no prior meetings [n=0]")

    print()
    rule()
    print("  Phase 2 adds: rosters, player ratings, talent gap, map pool,")
    print("  HLTV rankings, and the forfeit/lineup watcher.")
    rule()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", type=int, help="local match id")
    ap.add_argument("--gate", action="store_true", help="gate-passing only")
    a = ap.parse_args()
    with db.connect() as conn:
        if a.match:
            show_match(conn, a.match)
        else:
            show_slate(conn, a.gate)


if __name__ == "__main__":
    main()
