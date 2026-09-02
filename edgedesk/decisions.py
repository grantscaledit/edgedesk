"""Logging a decision, shared by the CLI and the web app.

Both entry points must capture the market benchmark the same way. If they
drift, half the log becomes incomparable to the other half and the
skill-vs-market number quietly stops meaning anything.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import queries

TAGS = ["talent_gap", "fatigue", "forfeit_risk", "map_pool", "form",
        "price_value", "liquidity", "roster_churn", "coin_flip", "h2h",
        "sample_too_thin"]

ACTIONS = ["bet_a", "bet_b", "no_bet"]

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
    after a market closes still captures a benchmark.
    """
    if not event_ticker:
        return None, None
    q = queries.last_quote(conn, event_ticker, team_a_id)
    if not q:
        return None, None
    bid, ask = q.get("yes_bid"), q.get("yes_ask")
    empty_book = bid is not None and ask is not None and bid <= 0 and ask >= 100
    if bid is None or ask is None or empty_book:
        # A 0/100 quote is the absence of a price, not a 50c opinion.
        if q.get("last_price") is None:
            return None, None
        prob = q["last_price"] / 100.0
    else:
        prob = ((bid + ask) / 2) / 100.0
    age = None
    if q.get("captured_at"):
        captured = q["captured_at"]
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - captured).total_seconds() / 60
    return round(prob, 4), age


def validate(prob_a, action, price, tags):
    """Return a list of problems. Empty means the input is loggable.

    Unknown tags are an ERROR, not a warning: a typo'd tag silently becomes
    its own category and splits the evidence for the reason it was meant to
    record.
    """
    problems = []
    try:
        p = float(prob_a)
    except (TypeError, ValueError):
        return ["probability must be a number between 0 and 1"]
    if not 0.0 <= p <= 1.0:
        problems.append("probability must be between 0 and 1")
    if action not in ACTIONS:
        problems.append(f"action must be one of {', '.join(ACTIONS)}")
    if action != "no_bet" and price in (None, ""):
        problems.append("a price is required when the action is a bet")
    unknown = [t for t in (tags or []) if t not in TAGS]
    if unknown:
        problems.append(f"unknown tag(s): {', '.join(unknown)}")
    return problems


def log(conn, match_id, prob_a, action, price=None, size=None, tags=None,
        user="default"):
    """Insert one decision. Returns (id, market_prob, quote_age_minutes).

    Raises ValueError on invalid input rather than writing a row that
    cannot be scored.
    """
    tags = list(tags or [])
    problems = validate(prob_a, action, price, tags)
    if problems:
        raise ValueError("; ".join(problems))

    m = queries.match(conn, match_id)
    if not m:
        raise ValueError(f"no match with id {match_id}")
    ev = conn.execute(FIND_EVENT, (match_id,)).fetchone()
    event_ticker = ev["event_ticker"] if ev else None
    market, age = market_prob_for(conn, event_ticker, m["team_a_id"])

    row = conn.execute(INSERT, {
        "user": user, "match": match_id, "event": event_ticker,
        "a": m["team_a_id"], "b": m["team_b_id"], "prob": float(prob_a),
        "action": action,
        "price": int(price) if price not in (None, "") else None,
        "size": int(size) if size not in (None, "") else None,
        "tags": tags, "market": market,
    }).fetchone()
    conn.commit()
    return row["id"], market, age
