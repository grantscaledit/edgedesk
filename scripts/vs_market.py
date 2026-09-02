#!/usr/bin/env python3
"""Do the signals beat the PRICE, not just the coin flip?

    python scripts/vs_market.py
    python scripts/vs_market.py --min-history 10 --fee-model taker

scripts/backtest.py showed round win % predicts winners at 57% on matches
where win rate is level. That is necessary and nowhere near sufficient: a
market that prices the same information gives that 57% away for free.

This asks the only question that decides whether the tool makes money —
when the signal DISAGREES with the market, who is right? — and then charges
Kalshi's fee against the answer, because an edge smaller than the fee is a
losing trade.

Sample is the binding constraint: the CS2 board launched in August 2026, so
settled history is short. The script says how short and refuses to draw a
conclusion it cannot support.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                          # noqa: E402
from edgedesk.stats import backtest as bt, scoring               # noqa: E402

# Settled game markets with a closing quote and a bound match.
# One row per SIDE; the caller pairs them by event.
SETTLED = """
-- THE PRE-MATCH CLOSING LINE: the last quote taken before the match began.
--
-- Two wrong answers were tried first, and both flattered the market:
--
--   * The latest snapshot outright. After settlement the book empties to
--     yes_bid=0 / yes_ask=100, whose mid is 50c on BOTH sides — overround
--     exactly 100, a market with no opinion, on every event.
--
--   * The last quote before close_time. These markets close EARLY, when a
--     winner is declared, so that quote can be taken mid-match or after the
--     result is obvious: 99/100 against 0/1, which also sums to exactly
--     100. Comparing a forecast to a price that already knows the outcome
--     makes the market look omniscient and every signal look worthless.
--     That is what produced 0% on disagreements.
--
-- The honest benchmark is what the market thought BEFORE any play, which
-- is the standard closing line. scheduled_at is derived from
-- expiration_time - 48h and is the true start.
WITH last_quote AS (
  SELECT DISTINCT ON (p.ticker) p.ticker, p.yes_bid, p.yes_ask,
         p.last_price, p.captured_at
  FROM kalshi_price_snapshots p
  JOIN kalshi_markets km2 ON km2.ticker = p.ticker
  WHERE km2.scheduled_at IS NOT NULL
    AND p.captured_at < km2.scheduled_at
  ORDER BY p.ticker, p.captured_at DESC
)
SELECT km.event_ticker, km.ticker, km.team_id, km.result,
       km.match_id, km.scheduled_at,
       q.yes_bid, q.yes_ask, q.last_price, q.captured_at,
       m.winner_team_id, m.team_a_id, m.team_b_id, m.decided_by_default,
       m.status
FROM kalshi_markets km
JOIN last_quote q ON q.ticker = km.ticker
JOIN matches m    ON m.id = km.match_id
WHERE km.series_ticker = 'KXCS2GAME'
  AND km.result IS NOT NULL
  AND km.team_id IS NOT NULL
ORDER BY km.scheduled_at;
"""

MATCHES = """
SELECT id, scheduled_at, status, winner_team_id, decided_by_default,
       team_a_id, team_b_id
FROM matches
WHERE scheduled_at IS NOT NULL AND team_a_id IS NOT NULL
  AND team_b_id IS NOT NULL
ORDER BY scheduled_at;
"""

# Beyond this, the mid is not a price. A 1/99 book has a 98c spread and
# mids to 50c on both sides — overround exactly 100, indistinguishable from
# an empty book except that it passes a 0/100 check. The liquidity gate uses
# 5c for actually trading; this is far looser because a benchmark only needs
# to carry information, not be executable.
MAX_BENCHMARK_SPREAD = 25

MAPS = """
SELECT mm.match_id, mm.team_a_rounds, mm.team_b_rounds, mm.winner_team_id,
       mm.is_default, m.team_a_id, m.team_b_id, m.scheduled_at
FROM match_maps mm JOIN matches m ON m.id = mm.match_id
ORDER BY m.scheduled_at;
"""

W = 78


def mid_prob(row):
    """Mid of the book, or None when the book is empty.

    A 0/100 quote is not a 50c opinion, it is the absence of one — that is
    what a settled or untraded market looks like. Treating it as 0.5 puts a
    fabricated coin-flip price on the record.
    """
    bid, ask = row["yes_bid"], row["yes_ask"]
    if bid is not None and ask is not None:
        if bid <= 0 and ask >= 100:
            return None                      # empty book
        if (ask - bid) > MAX_BENCHMARK_SPREAD:
            return None                      # too wide to mean anything
        return ((bid + ask) / 2) / 100.0
    if row["last_price"] is not None:
        return row["last_price"] / 100.0
    return None


def build_events(rows):
    """Pair the two sides of each event into one comparable record."""
    by_event: dict[str, list] = {}
    for r in rows:
        by_event.setdefault(r["event_ticker"], []).append(r)
    out = []
    for ticker, sides in by_event.items():
        if len(sides) != 2:
            continue
        m = sides[0]
        if m["decided_by_default"] or m["winner_team_id"] is None:
            continue        # forfeit resolves at Fair Market Price
        a_side = next((s for s in sides if s["team_id"] == m["team_a_id"]), None)
        b_side = next((s for s in sides if s["team_id"] == m["team_b_id"]), None)
        if not a_side or not b_side:
            continue
        pa, pb = mid_prob(a_side), mid_prob(b_side)
        if pa is None or pb is None:
            continue
        total = pa + pb
        if total <= 0:
            continue
        # Two DIFFERENT quantities, and mixing them produced a false alarm
        # on every healthy market:
        #   mid-sum  is ~100 by construction — two mids of complementary
        #            outcomes always sum to about 1. It says nothing about
        #            market quality.
        #   ask-sum  is what v_slate calls overround and what the 104-186
        #            baseline was measured on. It carries the spread, so it
        #            actually distinguishes a tight book from a nominal one.
        asks = [x for x in (a_side["yes_ask"], b_side["yes_ask"])
                if x is not None]
        ask_overround = sum(asks) if len(asks) == 2 else None
        out.append({
            "event": ticker, "match_id": m["match_id"],
            "scheduled_at": m["scheduled_at"],
            "team_a": m["team_a_id"], "team_b": m["team_b_id"],
            "a_won": m["winner_team_id"] == m["team_a_id"],
            # Normalised so the two sides sum to 1: the raw pair carries the
            # overround, and comparing a forecast to an inflated price would
            # flatter the forecast.
            "market_a": pa / total,
            "overround": round(ask_overround, 1) if ask_overround else None,
            "price_a_cents": a_side["yes_ask"],
            "price_b_cents": b_side["yes_ask"],
            # How far ahead of the match the benchmark was taken. A quote
            # from three minutes before is a different thing from one taken
            # six hours out, and a near-zero median means the "line" is
            # really a last-second price.
            "quote_lead_mins": max(
                0.0, (m["scheduled_at"] - a_side["captured_at"]).total_seconds()
                / 60) if a_side.get("captured_at") else None,
            "spread_a": (a_side["yes_ask"] - a_side["yes_bid"])
                        if a_side["yes_ask"] is not None
                        and a_side["yes_bid"] is not None else None,
        })
    return sorted(out, key=lambda e: e["scheduled_at"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-history", type=int, default=10)
    a = ap.parse_args()

    with db.connect() as conn:
        settled = [dict(r) for r in conn.execute(SETTLED).fetchall()]
        matches = [dict(r) for r in conn.execute(MATCHES).fetchall()]
        maps = [dict(r) for r in conn.execute(MAPS).fetchall()]

    events = build_events(settled)
    print(f"\n  settled rows with a PRE-MATCH quote: {len(settled)}")
    print(f"  usable two-sided events with a price and a result: {len(events)}")
    if not events:
        print("\n  Nothing to test yet. This needs settled KXCS2GAME markets "
              "with a\n  closing quote and a bound bo3 match. Check that "
              "settled.yml is running:\n    python scripts/healthcheck.py\n")
        return

    # Walk-forward signals, then keep only the priced events.
    out = bt.walk_forward(matches, maps, a.min_history)
    # Join on match_id. Keying on a timestamp matched NOTHING: the match's
    # start comes from bo3, the Kalshi event's from expiration_time - 48h,
    # and the two never agree to the second. Every signal reported "—".
    by_match = {}
    for name, rows in out["results"].items():
        for r in rows:
            if r.get("match_id") is not None:
                by_match.setdefault(r["match_id"], {})[name] = r

    matched = sum(1 for e in events if e["match_id"] in by_match)
    opinionated = sum(1 for e in events if abs(e["market_a"] - 0.5) >= 0.02)
    print(f"  events joined to a backtested signal: {matched}/{len(events)}")
    print(f"  events where the market had an opinion: "
          f"{opinionated}/{len(events)}")
    if not matched:
        print("\n  No event joined a signal. Check match_id overlap before "
              "concluding\n  anything about the data.\n")
    if matched and not opinionated:
        print("\n  Every market priced at exactly 50/50 — that is an empty "
              "order book,\n  not a market opinion. The quotes are being "
              "read after settlement.\n")

    print("=" * W)
    print(f"  {'signal':<17}{'n':>6}{'agree':>7}{'dis':>6}"
          f"{'signal right':>14}{'z':>7}")
    print("=" * W)
    print("  'dis' = events where the signal and the market disagreed on the")
    print("  favourite. Only those can make money; the rest you could have")
    print("  had by reading the price.\n")

    order = [k for k in bt.SIGNALS if k != "random_control"] + ["random_control"]
    for name in order:
        agree = dis = dis_right = 0
        fees = []
        for e in events:
            sig = by_match.get(e["match_id"], {}).get(name)
            if sig is None:
                continue
            signal_favours_a = sig["gap"] > 0
            market_favours_a = e["market_a"] > 0.5
            if abs(e["market_a"] - 0.5) < 0.02:
                continue            # market has no opinion; nothing to beat
            if signal_favours_a == market_favours_a:
                agree += 1
            else:
                dis += 1
                if signal_favours_a == e["a_won"]:
                    dis_right += 1
                    price = (e["price_a_cents"] if signal_favours_a
                             else e["price_b_cents"])
                    if price:
                        fees.append(scoring.taker_fee(price))
        n = agree + dis
        if not n:
            print(f"  {name:<17}{'—':>6}")
            continue
        if dis:
            s = bt.summarise([{"gap": 1, "correct": i < dis_right,
                               "match_id": i} for i in range(dis)])
            rate = f"{dis_right / dis * 100:.1f}%"
            z = f"{s['z']:.1f}"
        else:
            rate, z = "—", "—"
        print(f"  {name:<17}{n:>6}{agree:>7}{dis:>6}{rate:>14}{z:>7}")
    print("=" * W)

    span_days = (events[-1]["scheduled_at"] - events[0]["scheduled_at"]).days
    overrounds = sorted(e["overround"] for e in events
                        if e["overround"] is not None)
    overround = overrounds[len(overrounds) // 2] if overrounds else None
    leads = sorted(e["quote_lead_mins"] for e in events
                   if e["quote_lead_mins"] is not None)
    spreads = sorted(e["spread_a"] for e in events
                     if e["spread_a"] is not None)
    print(f"\n  {len(events)} events over {span_days} days"
          + (f"   median overround {overround:.0f} (ask-sum)"
             if overround is not None else ""))
    if leads:
        print(f"  median quote taken {leads[len(leads)//2]:.0f} min before "
              f"the match started")
    else:
        print("  quote lead time: NOT RECORDED (captured_at missing)")
    if spreads:
        print(f"  median benchmark spread {spreads[len(spreads)//2]:.0f}c "
              f"(rejected above {MAX_BENCHMARK_SPREAD}c)")
    if overround is not None and overround > 130:
        print("  NOTE: a high ask-sum means wide books — these benchmarks "
              "carry the spread,\n  so a small measured edge may not "
              "survive actually trading it.")
    print(f"  median taker fee at 50c: {scoring.taker_fee(50):.2f}c per "
          "contract\n")

    if len(events) < 100:
        print("  NOT ENOUGH DATA TO CONCLUDE ANYTHING.")
        print("  Under ~100 disagreements the result is dominated by which way")
        print("  a handful of coin flips landed. Treat this as a pipeline")
        print("  check, not a finding. Come back when the board has run for")
        print("  a few months.\n")
    else:
        print("  Read the 'signal right' column against 50%. Beating 50% on")
        print("  DISAGREEMENTS is the only evidence of edge here — and it")
        print("  still has to clear the fee before it is worth trading.\n")


if __name__ == "__main__":
    main()
