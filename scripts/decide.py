#!/usr/bin/env python3
"""Log a decision — including, and especially, a no-bet.

    python scripts/decide.py --match 3 --prob-a 0.62 --action no_bet \
        --tags map_pool,liquidity

    python scripts/decide.py --match 3 --prob-a 0.62 --action bet_a \
        --price 55 --size 20 --tags map_pool,price_value

`--prob-a` is your probability that TEAM A wins, where team A is whichever
side the dossier lists first. Team B is implied.

Log the no-bets. A log of only the bets you placed is a biased sample of
the decisions you made, and it will flatter the tool: the calls you passed
on are exactly the ones where the dossier talked you out of something, and
you cannot measure that if you never wrote it down.

The market's implied probability is captured automatically at logging time.
It is unrecoverable afterwards, and it is the only benchmark that makes an
absolute Brier score mean anything.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db, queries                                 # noqa: E402
from edgedesk.stats import scoring                               # noqa: E402

TAGS = ["talent_gap", "fatigue", "forfeit_risk", "map_pool", "form",
        "price_value", "liquidity", "roster_churn", "coin_flip", "h2h",
        "sample_too_thin"]

INSERT = """
INSERT INTO decisions
  (user_id, match_id, kalshi_event_ticker, team_a_id, team_b_id,
   prob_team_a, action, price_cents, size_contracts, tags, market_prob_a)
VALUES (%(user)s, %(match)s, %(event)s, %(a)s, %(b)s, %(prob)s, %(action)s,
        %(price)s, %(size)s, %(tags)s, %(market)s)
RETURNING id;
"""

FIND_EVENT = """
SELECT event_ticker FROM kalshi_markets
WHERE match_id = %s AND series_ticker = 'KXCS2GAME' LIMIT 1;
"""


def market_prob_for(conn, event_ticker, team_a_id):
    """(implied probability for team A, quote age in minutes) or (None, None).

    Mid rather than ask: the ask is what you would pay, but the fair
    reference for scoring a forecast is the middle of the market, and using
    the side you happened to trade would bias the benchmark your way.

    Reads the last recorded snapshot rather than the live slate, so logging
    a decision after the market closes still captures a benchmark. Without
    it, any retrospective entry is unscoreable against the market forever.
    """
    if not event_ticker:
        return None, None
    q = queries.last_quote(conn, event_ticker, team_a_id)
    if not q:
        return None, None
    bid, ask = q.get("yes_bid"), q.get("yes_ask")
    if bid is None or ask is None:
        if q.get("last_price") is None:
            return None, None
        prob = q["last_price"] / 100.0
    else:
        prob = ((bid + ask) / 2) / 100.0
    age = None
    if q.get("captured_at"):
        from datetime import datetime, timezone
        captured = q["captured_at"]
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - captured).total_seconds() / 60
    return round(prob, 4), age


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", type=int, required=True)
    ap.add_argument("--prob-a", type=float, required=True,
                    help="your probability that team A wins (0-1)")
    ap.add_argument("--action", choices=["bet_a", "bet_b", "no_bet"],
                    required=True)
    ap.add_argument("--price", type=int, help="fill price in cents")
    ap.add_argument("--size", type=int, help="contracts")
    ap.add_argument("--tags", default="",
                    help=f"comma separated. known: {','.join(TAGS)}")
    ap.add_argument("--user", default="default")
    a = ap.parse_args()

    if not 0.0 <= a.prob_a <= 1.0:
        sys.exit("--prob-a must be between 0 and 1")
    tags = [t.strip() for t in a.tags.split(",") if t.strip()]
    unknown = [t for t in tags if t not in TAGS]
    if unknown:
        # Refused, not accepted-with-a-warning: a typo'd tag silently
        # becomes its own category and quietly splits the evidence for the
        # reason it was meant to record.
        sys.exit(f"unknown tag(s): {', '.join(unknown)}\nknown: {', '.join(TAGS)}")
    if a.action != "no_bet" and a.price is None:
        sys.exit("--price is required when the action is a bet")

    with db.connect() as conn:
        m = queries.match(conn, a.match)
        if not m:
            sys.exit(f"no match with id {a.match}")
        ev = conn.execute(FIND_EVENT, (a.match,)).fetchone()
        event_ticker = ev["event_ticker"] if ev else None
        market, quote_age = market_prob_for(conn, event_ticker, m["team_a_id"])

        row = conn.execute(INSERT, {
            "user": a.user, "match": a.match, "event": event_ticker,
            "a": m["team_a_id"], "b": m["team_b_id"], "prob": a.prob_a,
            "action": a.action, "price": a.price, "size": a.size,
            "tags": tags, "market": market,
        }).fetchone()
        conn.commit()

    print(f"\n  logged decision #{row['id']}")
    print(f"    {m['team_a_name']} vs {m['team_b_name']}")
    print(f"    your P(team A) : {a.prob_a:.0%}")
    if market is not None:
        diff = a.prob_a - market
        age = ""
        if quote_age is not None:
            age = (f"   [quote {quote_age/60:.1f}h old]" if quote_age > 90
                   else f"   [quote {quote_age:.0f}m old]")
        print(f"    market mid     : {market:.0%}   "
              f"({diff:+.1%} vs your number){age}")
        if quote_age is not None and quote_age > 90:
            print("                     WARNING: stale quote. It is still a "
                  "benchmark, but not\n                     the price you "
                  "would actually have traded against.")
        if a.price:
            fee = scoring.taker_fee(a.price)
            print(f"    taker fee      : {fee:.2f}c per contract at "
                  f"{a.price}c — needs {fee:.2f}c of edge to break even")
    else:
        print("    market mid     : NOT CAPTURED")
        print("                     No price snapshot exists for this event, "
              "so this decision\n                     can never be compared "
              "to the market — the one thing that\n                     "
              "separates forecasting skill from reading a price.")
    print(f"    action         : {a.action}"
          + (f"  {a.size or '?'} @ {a.price}c" if a.action != "no_bet" else ""))
    print(f"    tags           : {', '.join(tags) or 'none'}")
    print("\n  Score it once the match settles:  "
          "python scripts/review.py --score\n")


if __name__ == "__main__":
    main()
