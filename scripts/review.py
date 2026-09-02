#!/usr/bin/env python3
"""Review the decision log: score settled calls, then report.

    python scripts/review.py            # history + calibration
    python scripts/review.py --score    # back-fill outcomes first
    python scripts/review.py --open     # only calls not yet settled

Scoring resolves each decision from the bo3 match result. Matches decided
by default are recorded as 'fmp' and EXCLUDED from scoring: under Kalshi's
rules a pre-game forfeit resolves to Fair Market Price, so the position is
unwound at the prevailing price rather than won or lost, and scoring it
would be scoring an event that did not happen.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.stats import scoring                               # noqa: E402

UNSCORED = """
SELECT d.id, d.match_id, d.prob_team_a, d.market_prob_a, d.team_a_id,
       d.team_b_id, m.status, m.winner_team_id, m.decided_by_default
FROM decisions d
JOIN matches m ON m.id = d.match_id
WHERE d.scored_at IS NULL AND m.status = 'finished';
"""

APPLY = """
UPDATE decisions SET result=%(result)s, outcome_team_id=%(winner)s,
       brier=%(brier)s, market_brier=%(market_brier)s, scored_at=now()
WHERE id=%(id)s;
"""

ALL_ROWS = """
SELECT d.*, ta.canonical_name AS team_a_name, tb.canonical_name AS team_b_name,
       m.scheduled_at, m.status AS match_status, m.bo3_status,
       m.winner_team_id AS match_winner
FROM decisions d
LEFT JOIN teams ta ON ta.id = d.team_a_id
LEFT JOIN teams tb ON tb.id = d.team_b_id
LEFT JOIN matches m ON m.id = d.match_id
WHERE d.user_id = %(user)s
ORDER BY d.created_at DESC;
"""

W = 78


def rule(c="-"):
    print(c * W)


def pending_reason(row) -> str:
    """Why an unscored decision is still unscored.

    'open' is uninformative and looks identical whether the match is being
    played right now or the local database simply has not been refreshed.
    Those need different actions, so they get different words.
    """
    status = row.get("match_status")
    if row.get("match_id") is None:
        return "no match"
    if status == "live":
        return "playing"
    if status == "scheduled":
        return "not started"
    if status == "finished" and row.get("match_winner") is None:
        return "no winner"
    if status == "finished":
        return "run --score"
    return status or "unknown"


def do_score(conn) -> int:
    rows = conn.execute(UNSCORED).fetchall()
    applied = 0
    for r in rows:
        if r["decided_by_default"]:
            result, brier, mbrier = "fmp", None, None
        elif r["winner_team_id"] is None:
            result, brier, mbrier = "void", None, None
        else:
            is_a = r["winner_team_id"] == r["team_a_id"]
            result = "team_a" if is_a else "team_b"
            outcome = 1 if is_a else 0
            brier = scoring.brier(float(r["prob_team_a"]), outcome)
            mbrier = (scoring.brier(float(r["market_prob_a"]), outcome)
                      if r["market_prob_a"] is not None else None)
        conn.execute(APPLY, {"id": r["id"], "result": result,
                             "winner": r["winner_team_id"],
                             "brier": brier, "market_brier": mbrier})
        applied += 1
    conn.commit()
    return applied


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--user", default="default")
    a = ap.parse_args()

    with db.connect() as conn:
        if a.score:
            print(f"  scored {do_score(conn)} newly settled decisions")

        rows = [dict(r) for r in conn.execute(
            ALL_ROWS, {"user": a.user}).fetchall()]

    if not rows:
        print("\n  No decisions logged yet.")
        print("  python scripts/decide.py --match <id> --prob-a 0.62 "
              "--action no_bet --tags map_pool\n")
        return

    if a.open:
        rows = [r for r in rows if r["scored_at"] is None]

    print()
    rule("=")
    print(f"  DECISION LOG — {len(rows)} entries")
    rule("=")
    print(f"\n  {'#':>4}  {'date':11} {'matchup':32} {'you':>5} {'mkt':>5} "
          f"{'action':7} {'result':7}")
    rule()
    for r in rows[:40]:
        when = r["created_at"].strftime("%m-%d %H:%M") if r["created_at"] else "?"
        pair = f"{r['team_a_name'] or '?'} v {r['team_b_name'] or '?'}"[:32]
        mkt = f"{float(r['market_prob_a']):.0%}" if r["market_prob_a"] is not None else "-"
        shown = r["result"] or pending_reason(r)
        print(f"  {r['id']:>4}  {when:11} {pair:32} "
              f"{float(r['prob_team_a']):>4.0%} {mkt:>5} "
              f"{r['action']:7} {shown:11}")
    rule()

    stale = [r for r in rows
             if r["scored_at"] is None and r.get("match_status") == "scheduled"
             and r.get("scheduled_at") and r["scheduled_at"] < r["created_at"]]
    if stale:
        print(f"  {len(stale)} decision(s) show 'not started' for a match "
              "whose start time has passed —")
        print("  the local match record is stale. Refresh, then score:")
        print("    python scripts/phase1_sync.py --days 3")
        print("    python scripts/review.py --score")

    missing_mkt = [r for r in rows if r["market_prob_a"] is None]
    if missing_mkt:
        print(f"  {len(missing_mkt)} decision(s) have no market price and can "
              "never be compared to it.")

    scored = [r for r in rows if r["result"] in ("team_a", "team_b")]
    fmp = [r for r in rows if r["result"] == "fmp"]
    scored = scoring.score_rows(scored)

    print(f"\n  SCORED: {len(scored)}   open: "
          f"{sum(1 for r in rows if r['scored_at'] is None)}   "
          f"fair-market-price (excluded): {len(fmp)}")

    if not scored:
        print("\n  Nothing settled yet. Run with --score after matches "
              "finish.\n")
        return

    print()
    rule()
    print(f"    {'your brier':<22} {scoring.mean_brier(scored).render(pct=False)}")
    print(f"    {'market brier':<22} "
          f"{scoring.mean_brier(scored, 'market_brier').render(pct=False)}")
    edge = scoring.skill_vs_market(scored)
    print(f"    {'skill vs market':<22} {edge.render(pct=False)}")
    if edge.note:
        print(f"    {'':22} note: {edge.note}")
    print(f"    {'':22} positive = your forecast beat the price")

    print("\n  CALIBRATION  (of calls made at X%, how many happened)")
    rule()
    print(f"    {'bucket':<12} {'n':>4}  {'you said':>9} {'actual':>8} {'gap':>7}")
    for b in scoring.calibration(scored, bins=5):
        if not b["n"]:
            continue
        print(f"    {b['lo']:.0%}-{b['hi']:.0%}".ljust(16)
              + f"{b['n']:>4}  {b['mean_forecast']:>9.0%} "
              f"{b['actual']:>8.0%} {b['gap']:>+7.0%}")

    tags = scoring.tag_performance(scored)
    if tags:
        print("\n  BY TAG  (mean skill vs market; n is what matters here)")
        rule()
        for t in tags:
            edge_s = f"{t['mean_edge']:+.4f}" if t["mean_edge"] is not None else "-"
            print(f"    {t['tag']:<20} n={t['n']:<4} {edge_s}")

    bets = [r for r in scored if r["action"] != "no_bet"]
    print(f"\n  {len(bets)} of {len(scored)} scored decisions were bets; "
          f"{len(scored) - len(bets)} were no-bets.")
    print("  A log of only the bets would be a biased sample — the passes "
          "are where\n  the dossier may have earned its keep.")
    print()


if __name__ == "__main__":
    main()
