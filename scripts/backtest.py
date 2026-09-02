#!/usr/bin/env python3
"""Which dossier signals actually predicted match outcomes?

    python scripts/backtest.py
    python scripts/backtest.py --min-history 20 --tier a,b,s

Walks 16k historical matches in chronological order, computing each signal
from PRIOR matches only, and checks whether the higher-signal side won.

Read the control row first. `random_control` is a random number pretending
to be a signal; it must land at 50%. If it does not, the harness is broken
and nothing else on the page means anything.

Then read n and z, not the percentage. 55% over 200 matches and 52% over
16,000 are opposite findings, and only one of them is a reason to change
how you bet.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.stats import backtest as bt                        # noqa: E402

MATCHES = """
SELECT id, scheduled_at, status, winner_team_id, decided_by_default,
       team_a_id, team_b_id, tier, format_bo
FROM matches
WHERE scheduled_at IS NOT NULL
  AND team_a_id IS NOT NULL AND team_b_id IS NOT NULL
ORDER BY scheduled_at;
"""

MAPS = """
SELECT mm.match_id, mm.map_index, mm.map_name, mm.team_a_rounds,
       mm.team_b_rounds, mm.winner_team_id, mm.is_default,
       m.team_a_id, m.team_b_id, m.scheduled_at
FROM match_maps mm JOIN matches m ON m.id = mm.match_id
ORDER BY m.scheduled_at;
"""

W = 78


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-history", type=int, default=bt.MIN_HISTORY)
    ap.add_argument("--tier", help="comma separated, e.g. a,b,s")
    a = ap.parse_args()

    with db.connect() as conn:
        matches = [dict(r) for r in conn.execute(MATCHES).fetchall()]
        maps = [dict(r) for r in conn.execute(MAPS).fetchall()]

    if a.tier:
        keep = {t.strip().lower() for t in a.tier.split(",")}
        matches = [m for m in matches if (m.get("tier") or "").lower() in keep]
        ids = {m["id"] for m in matches}
        maps = [r for r in maps if r["match_id"] in ids]

    print(f"\n  loaded {len(matches)} matches, {len(maps)} map rows")
    out = bt.walk_forward(matches, maps, a.min_history)
    print(f"  finished matches: {out['matches_considered']}   "
          f"skipped for thin history: {out['skipped_insufficient_history']}")
    print(f"  each side needed {a.min_history}+ prior matches\n")

    print("=" * W)
    print(f"  {'signal':<17}{'n':>7}{'acc':>8}{'z':>7}   verdict")
    print("=" * W)

    order = [k for k in bt.SIGNALS if k != "random_control"] + ["random_control"]
    summaries = {}
    for name in order:
        s = bt.summarise(out["results"][name])
        summaries[name] = s
        if not s["n"]:
            print(f"  {name:<17}{'—':>7}")
            continue
        acc = f"{s['accuracy'] * 100:.1f}%"
        mark = "  <- control" if name == "random_control" else ""
        print(f"  {name:<17}{s['n']:>7}{acc:>8}{s['z']:>7.1f}   "
              f"{bt.verdict(s)}{mark}")
    print("=" * W)

    ctrl = summaries.get("random_control", {})
    if ctrl.get("n") and abs(ctrl["accuracy"] - 0.5) > 0.03:
        print("\n  WARNING: the random control is not at 50%. The harness is "
              "suspect;\n  do not act on anything above.")

    print("\n  accuracy by gap quintile  (each signal on its own scale)")
    print("-" * W)
    for name in order:
        s = summaries[name]
        if not s["n"] or not s["buckets"]:
            continue
        cells = "  ".join(f"{b['accuracy'] * 100:>5.1f}%" for b in s["buckets"])
        rng = (f"gap {s['buckets'][0]['gap_lo']:g}"
               f"-{s['buckets'][-1]['gap_hi']:g}")
        print(f"  {name:<17}{cells}   ({rng})")
    print(f"  {'':17}{'smallest gaps':<14}{'':22}{'largest gaps':>13}")
    print("-" * W)

    # The question that actually changes a decision.
    inc = bt.incremental(out["results"], base="win_rate",
                         close_threshold=0.05)
    print(f"\n  INCREMENTAL VALUE  —  the {inc['close_matches']} matches "
          f"where win rate is within {inc['threshold']:.0%}")
    print("-" * W)
    print("  Every signal here measures roughly 'is this team better', so")
    print("  each looks strong alone. This asks a narrower question: when")
    print("  win rate says the sides are level, does anything still know?")
    print()
    print(f"  {'signal':<28}{'n':>7}{'acc':>8}{'z':>7}   verdict")
    for name, s in sorted(inc["signals"].items(),
                          key=lambda kv: -(kv[1]["z"] or 0)):
        if not s["n"]:
            continue
        print(f"  {name:<28}{s['n']:>7}{s['accuracy'] * 100:>7.1f}%"
              f"{s['z']:>7.1f}   {bt.verdict(s)}")
    print("-" * W)
    print("""
  How to read this

  A signal whose accuracy does NOT rise with the gap is almost certainly
  noise, however good its headline number looks: a real edge should be
  worth more when it is larger.

  z is measured against a null of 50% and is unadjusted for testing several
  signals at once, so treat anything between 2 and 3 as weaker than it
  appears.

  The incremental table is the one that should change what the dossier
  leads with. A signal that only works when win rate already agrees adds
  nothing you did not have.

  This predicts OUTCOMES, not prices. A signal that beats a coin flip can
  still lose money if the market already knows it -- which it usually does.
  Beating the closing price is the harder question and needs more settled
  Kalshi history than exists yet.
""")


if __name__ == "__main__":
    main()
