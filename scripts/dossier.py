#!/usr/bin/env python3
"""Match dossier — the thing this whole project exists to produce.

    python scripts/dossier.py                     # the slate
    python scripts/dossier.py --match 12345       # one match
    python scripts/dossier.py --event KXCS2GAME-...
    python scripts/dossier.py --days 180          # narrow the sample

It assembles evidence. It does NOT tell you what to bet, produce a fair
value, or rank anything by edge. Every figure carries its sample size,
effective sample size and staleness, because the difference between a 100%
win rate over two matches and over forty is the entire question and a bare
percentage hides it.

Where data is missing it says so. A partial dossier is correct behaviour.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db, queries                                 # noqa: E402
from edgedesk.stats import h2h, maps, roster as rstats           # noqa: E402
from edgedesk.stats import team as tstats                        # noqa: E402

W = 78


def rule(char="-"):
    print(char * W)


def head(text):
    print()
    rule("=")
    print(f"  {text}")
    rule("=")


def section(text):
    print(f"\n  {text}")
    rule()


def row(label, stat, pct=True, places=None):
    """Print a Stat with its provenance. Never prints a bare number."""
    print(f"    {label:<22} {stat.render(pct=pct, places=places)}")


def show_slate(conn):
    rows = queries.slate(conn)
    head(f"SLATE — {len(rows)} events")
    if not rows:
        print("\n  Nothing listed. Has the collector run?"
              "  python scripts/healthcheck.py")
        return
    print(f"\n  {'':2}{'id':>7}  {'start':12}  {'teams':34} {'spr':>4} "
          f"{'ovr':>5} {'depth':>6}  ticker")
    rule("-")
    for r in rows:
        gate = "*" if r["gate_pass"] else " "
        when = r["scheduled_at"].strftime("%m-%d %H:%M") if r["scheduled_at"] else "?"
        teams = (r["teams"] or "?")[:34]
        spread = r["best_spread"] if r["best_spread"] is not None else "-"
        ovr = r["overround"] if r["overround"] is not None else "-"
        depth = r["top_depth"] if r["top_depth"] is not None else "-"
        mid = r["match_id"] if r["match_id"] else "unbound"
        print(f"  {gate} {str(mid):>7}  {when:12}  {teams:34} {spread:>4} "
              f"{ovr:>5} {depth:>6}  {r['event_ticker']}")
    rule("-")
    print("  * = passes the liquidity gate (2 sides, spread<=5c, "
          "overround<=106, depth>=25)")
    print("  Gate-failing events still get a full dossier — the gate governs "
          "price commentary only.")
    print("  'unbound' = no bo3 match linked, so no dossier can be built.")
    print("\n  Copy an id or a ticker from above:")
    print("    python scripts/dossier.py --match 501")
    print("    python scripts/dossier.py --match KXCS2GAME-26SEP031000HAVULAV")


def team_block(name, rows, map_rows, team_id, pool_ff, days,
               roster_rows=None, change_times=None):
    section(name)
    played = [r for r in rows if r.get("status") == "finished"]
    print(f"    sample: {len(played)} completed matches over {days}d, "
          f"{len(map_rows)} map rows")

    row("win rate", tstats.win_rate(rows, team_id))
    row("form (last 5)", tstats.form(rows, team_id, 5))
    print(f"    {'form string':<22} {tstats.form_string(rows, team_id, 8)} "
          "(most recent first, '-' = forfeit)")
    row("forfeit rate", tstats.forfeit_rate(rows, team_id))
    r = rstats.describe(roster_rows or [], change_times or [])
    churn = r["changes_30d"] if roster_rows else None
    ff = tstats.no_show_risk(rows, team_id, roster_changes_30d=churn)
    row("no-show risk", ff)
    if ff.note:
        print(f"    {'':22} note: {ff.note}")
    row("fatigue", tstats.fatigue(rows), pct=False, places=0)

    row("map win rate", maps.map_win_rate(map_rows, team_id))
    row("round win %", maps.round_win_pct(map_rows, team_id))
    row("avg round diff", maps.avg_round_diff(map_rows, team_id), pct=False)

    if roster_rows:
        names = ", ".join(p["nickname"] for p in r["players"])
        age = (f"{r['staleness_days']:.0f}d old"
               if r["staleness_days"] is not None else "age unknown")
        flag = "" if r["complete"] else f"  [{r['size']}/5 — INCOMPLETE]"
        print(f"    {'lineup':<22} {names}{flag}")
        print(f"    {'':22} captured {age}, "
              f"{r['changes_30d']} change(s) in 30d")
    else:
        print(f"    {'lineup':<22} not captured — run "
              "scripts/sync_players.py")

    pool = maps.map_pool(map_rows, team_id, min_maps=2)
    if pool:
        print(f"\n    {'map':<14} {'played':>6}  {'record':<10} "
              f"{'round%':<10} {'diff':>6}")
        for m in pool[:8]:
            wr = m["win_rate"]
            rwp = m["round_win_pct"]
            rd = m["round_diff"]
            print(f"    {m['map']:<14} {m['played']:>6}  {wr.raw:<10} "
                  f"{(f'{rwp.value*100:.0f}%' if rwp.value else '-'):<10} "
                  f"{(f'{rd.value:+.1f}' if rd.value is not None else '-'):>6}")
    else:
        print("\n    map pool: not enough resolved maps to show "
              "(need 2+ on a map)")


def show_match(conn, match_id, days):
    data = queries.dossier_rows(conn, match_id, days)
    if not data:
        print(f"No match with id {match_id}.")
        return

    m, ta, tb = data["match"], data["team_a"], data["team_b"]
    a_id, b_id = m["team_a_id"], m["team_b_id"]
    head(f"{ta['canonical_name']}  vs  {tb['canonical_name']}")
    when = m["scheduled_at"].strftime("%Y-%m-%d %H:%M UTC") if m["scheduled_at"] else "?"
    tier = f"{m['tier'] or '?'}{m.get('tier_rank') or ''}"
    print(f"  {when}   Bo{m['format_bo'] or '?'}   tier {tier}")

    # Tournament context changes how every figure below should be read: a
    # tier-S LAN and a tier-D online qualifier are different competitions
    # with different incentives, and forfeit risk in particular is not
    # comparable between them.
    if m.get("tournament"):
        bits = [m["tournament"]]
        if m.get("stage_title") and m["stage_title"] != m["tournament"]:
            bits.append(m["stage_title"])
        print(f"  {' — '.join(bits)}")
        extra = []
        if m.get("event_type"):
            extra.append(m["event_type"])
        if m.get("event_level"):
            extra.append(m["event_level"])
        if m.get("prize"):
            extra.append(f"prize {m['prize']:,}")
        if extra:
            print(f"  {'  ·  '.join(extra)}")
    else:
        print("  tournament: not linked — run scripts/sync_tournaments.py")
    if m["bo3_slug"]:
        print(f"  bo3.gg/matches/{m['bo3_slug']}")

    team_block(f"{ta['canonical_name']}", data["a_matches"], data["a_maps"],
               a_id, data["pool_forfeit"], days,
               data.get("a_roster"), data.get("a_roster_changes"))
    team_block(f"{tb['canonical_name']}", data["b_matches"], data["b_maps"],
               b_id, data["pool_forfeit"], days,
               data.get("b_roster"), data.get("b_roster_changes"))

    section("HEAD TO HEAD")
    rec = h2h.record(data["all_matches"], a_id, b_id)
    print(f"    {ta['canonical_name']} vs {tb['canonical_name']}: "
          f"{rec.render()}")
    if rec.note:
        print(f"    note: {rec.note}")

    mr = h2h.map_record(data["all_maps"], a_id, b_id)
    if mr:
        print(f"\n    {'map':<14} {'played':>6}  {'record':<8} {'diff':>6}")
        for e in mr:
            d = f"{e['avg_round_diff']:+.1f}" if e["avg_round_diff"] is not None else "-"
            print(f"    {e['map']:<14} {e['played']:>6}  {e['record']:<8} {d:>6}")

    co = h2h.common_opponents(data["all_matches"], a_id, b_id)
    print(f"\n    common opponents: {co['count']}")
    if co["count"]:
        print(f"    note: {co['note']}")

    section("DATA GAPS")
    print("    player ratings / talent gap   not collected — bo3 has no "
          "player stats at all (HLTV)")
    print("    roster continuity             needs per-map participation "
          "(HLTV); lineups themselves are collected")
    print("    CT/T side split               not available from bo3 at all")
    print("    veto data                     rarely published; irrelevant in Bo1")

    print()
    rule("=")
    print("  Evidence only. No fair value, no recommendation.")
    rule("=")


def main():
    ap = argparse.ArgumentParser()
    # str, not int: the slate shows both a numeric match id and a Kalshi
    # event ticker, and requiring the user to know which flag takes which
    # is a trap the tool can simply absorb.
    ap.add_argument("--match", type=str,
                    help="bo3 match id, or a Kalshi event ticker")
    ap.add_argument("--event", type=str, help="Kalshi event ticker")
    ap.add_argument("--days", type=int, default=365)
    a = ap.parse_args()

    # A ticker passed to --match is routed to the event path rather than
    # rejected. Same for a bare number passed to --event.
    target_event, target_match = a.event, None
    if a.match:
        if a.match.strip().isdigit():
            target_match = int(a.match)
        else:
            target_event = a.match.strip()
    if target_event and target_event.strip().isdigit():
        target_match, target_event = int(target_event.strip()), None

    with db.connect() as conn:
        if target_event:
            sides = queries.event_sides(conn, target_event)
            if not sides:
                print(f"No open markets for event {target_event}.")
                print("  The event may have closed. Run the slate to see "
                      "what is live:  python scripts/dossier.py")
                return
            head(f"EVENT {target_event}")
            for s in sides:
                print(f"    {s['team_name']:<28} bid {s['yes_bid']:>3}  "
                      f"ask {s['yes_ask']:>3}  spread {s['spread_cents']:>3}  "
                      f"depth {s['top_depth']}")
            mid = next((s["team_id"] for s in sides if s["team_id"]), None)
            match_row = conn.execute(
                "SELECT match_id FROM kalshi_markets WHERE event_ticker=%s "
                "AND match_id IS NOT NULL LIMIT 1", (target_event,)).fetchone()
            if match_row:
                show_match(conn, match_row["match_id"], a.days)
            else:
                print("\n  This event is not bound to a bo3 match, so no "
                      "dossier can be built.\n  Check resolution_queue.")
        elif target_match:
            show_match(conn, target_match, a.days)
        else:
            show_slate(conn)


if __name__ == "__main__":
    main()
