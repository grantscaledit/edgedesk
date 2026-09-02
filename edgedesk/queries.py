"""Row fetching for the stats layer.

The stats modules are pure — rows in, values out — so something has to do
the SQL. That is this module, and it does ONLY that: it returns rows and
computes nothing. Keeping the split sharp is what lets every statistic be
tested without a database.

One query per team per kind, not one per statistic. A dossier calls maybe
fifteen stats functions over the same two row sets; fetching inside each
would be fifteen round trips to Neon for data we already had.
"""
from __future__ import annotations

# Matches involving one team, either side. `days` bounds the sample; the
# stats layer applies time decay on top, so a generous window is fine.
TEAM_MATCHES = """
SELECT m.id, m.scheduled_at, m.status, m.winner_team_id,
       m.decided_by_default, m.team_a_id, m.team_b_id, m.format_bo,
       m.tier, m.bo3_slug
FROM matches m
WHERE (m.team_a_id = %(team)s OR m.team_b_id = %(team)s)
  AND m.scheduled_at > now() - (%(days)s || ' days')::interval
ORDER BY m.scheduled_at DESC;
"""

# Map rows carry their match's date and sides so the stats layer can orient
# scores without a second lookup.
TEAM_MAPS = """
SELECT mm.match_id, mm.map_index, mm.map_name,
       mm.team_a_rounds, mm.team_b_rounds, mm.winner_team_id,
       mm.is_default, mm.side_assignment,
       m.scheduled_at, m.team_a_id, m.team_b_id, m.tier
FROM match_maps mm
JOIN matches m ON m.id = mm.match_id
WHERE (m.team_a_id = %(team)s OR m.team_b_id = %(team)s)
  AND m.scheduled_at > now() - (%(days)s || ' days')::interval
ORDER BY m.scheduled_at DESC;
"""

TEAM = """
SELECT t.id, t.canonical_name, t.acronym, t.bo3_rank, t.country,
       (SELECT string_agg(alias_name, ', ')
        FROM v_team_aliases WHERE team_id = t.id) AS aliases
FROM teams t WHERE t.id = %(team)s;
"""

# Upcoming bound events with their two sides and current pricing.
SLATE = """
SELECT s.event_ticker,
       MIN(s.scheduled_at)                                AS scheduled_at,
       string_agg(DISTINCT s.team_name, ' vs ')           AS teams,
       MAX(km.match_id)                                   AS match_id,
       MIN(s.spread_cents)                                AS best_spread,
       MAX(s.overround)                                   AS overround,
       MAX(s.top_depth)                                   AS top_depth,
       bool_or(s.gate_pass)                               AS gate_pass,
       SUM(s.volume)                                      AS volume,
       MAX(s.captured_at)                                 AS captured_at
FROM v_slate s
JOIN kalshi_markets km ON km.ticker = s.ticker
WHERE s.series_ticker = 'KXCS2GAME'
  AND s.scheduled_at > now() - interval '6 hours'
GROUP BY s.event_ticker
ORDER BY gate_pass DESC, scheduled_at;
"""

# Both sides of one event, with the price for each.
EVENT_SIDES = """
SELECT s.ticker, s.team_name, s.team_abbr, km.team_id,
       s.yes_bid, s.yes_ask, s.last_price, s.spread_cents, s.top_depth,
       s.volume, s.gate_pass, s.overround, s.captured_at
FROM v_slate s
JOIN kalshi_markets km ON km.ticker = s.ticker
WHERE s.event_ticker = %(event)s AND s.series_ticker = 'KXCS2GAME'
ORDER BY s.yes_ask NULLS LAST;
"""

# Last recorded quote for one team's market, OPEN OR CLOSED.
#
# v_slate deliberately shows only live markets, which is right for the
# slate and wrong here: a decision logged after a match closes would lose
# its market benchmark forever, and that benchmark is the only thing that
# separates forecasting skill from reading a price. The snapshot table is
# append-only precisely so this stays recoverable.
LAST_QUOTE = """
SELECT p.yes_bid, p.yes_ask, p.last_price, p.captured_at, m.status
FROM kalshi_price_snapshots p
JOIN kalshi_markets m ON m.ticker = p.ticker
WHERE m.event_ticker = %(event)s
  AND m.series_ticker = 'KXCS2GAME'
  AND m.team_id = %(team)s
  -- Only quotes taken while the market was still trading. After settlement
  -- the book empties to 0/100, whose mid is 50c -- recording that as the
  -- market's opinion would put a fabricated coin flip on every decision
  -- logged after a match finished.
  AND (m.close_time IS NULL OR p.captured_at < m.close_time)
ORDER BY p.captured_at DESC
LIMIT 1;
"""

ROSTER = """
SELECT team_id, player_id, captured_at, source, nickname, first_name,
       last_name, country_id, role
FROM v_roster_latest WHERE team_id = %(team)s;
"""

# Capture moments AFTER the first: the first snapshot is a change from
# nothing on our side, not a roster move, and counting it would overstate
# churn for every team on the day we start watching them.
ROSTER_CHANGES = """
SELECT captured_at FROM (
  SELECT captured_at,
         row_number() OVER (ORDER BY captured_at) AS rn
  FROM v_roster_changes WHERE team_id = %(team)s
) t WHERE rn > 1 ORDER BY captured_at DESC;
"""

# Implied probability for one side over time, up to kickoff.
# Normalised against the other side so the pair sums to 1 — the raw quote
# carries the spread, and a line that "moved" only because the book widened
# is not news.
PRICE_HISTORY = """
WITH sides AS (
  SELECT km.ticker, km.team_id, km.scheduled_at
  FROM kalshi_markets km
  WHERE km.match_id = %(match)s AND km.series_ticker = 'KXCS2GAME'
),
quotes AS (
  SELECT s.team_id, p.captured_at, p.yes_bid, p.yes_ask
  FROM kalshi_price_snapshots p
  JOIN sides s ON s.ticker = p.ticker
  WHERE p.yes_bid IS NOT NULL AND p.yes_ask IS NOT NULL
    AND NOT (p.yes_bid <= 0 AND p.yes_ask >= 100)
    AND (p.yes_ask - p.yes_bid) <= 25
    AND (s.scheduled_at IS NULL OR p.captured_at <= s.scheduled_at)
)
SELECT date_trunc('hour', captured_at) AS at, team_id,
       avg((yes_bid + yes_ask) / 2.0) AS mid
FROM quotes GROUP BY 1, 2 ORDER BY 1;
"""

HOURLY_WRITES = """
SELECT date_trunc('hour', captured_at) AS hour, count(*) AS rows
FROM kalshi_price_snapshots
WHERE captured_at > now() - interval '48 hours'
GROUP BY 1 ORDER BY 1;
"""

MATCH_TEAMS = """
SELECT m.id, m.scheduled_at, m.format_bo, m.tier, m.tier_rank, m.bo3_slug,
       m.stage_title,
       m.team_a_id, ta.canonical_name AS team_a_name,
       m.team_b_id, tb.canonical_name AS team_b_name,
       t.name AS tournament, t.tier AS tournament_tier,
       t.tier_rank AS tournament_tier_rank, t.prize, t.event_type,
       t.event_level
FROM matches m
JOIN teams ta ON ta.id = m.team_a_id
JOIN teams tb ON tb.id = m.team_b_id
LEFT JOIN tournaments t ON t.bo3_id = m.bo3_tournament_id
WHERE m.id = %(match)s;
"""

POOL_FORFEIT = """
SELECT AVG(CASE WHEN decided_by_default THEN 1.0 ELSE 0.0 END) AS mean
FROM matches
WHERE status = 'finished'
  AND scheduled_at > now() - (%(days)s || ' days')::interval;
"""


def team_matches(conn, team_id: int, days: int = 365) -> list[dict]:
    return [dict(r) for r in conn.execute(
        TEAM_MATCHES, {"team": team_id, "days": str(days)}).fetchall()]


def team_maps(conn, team_id: int, days: int = 365) -> list[dict]:
    return [dict(r) for r in conn.execute(
        TEAM_MAPS, {"team": team_id, "days": str(days)}).fetchall()]


def team(conn, team_id: int) -> dict | None:
    row = conn.execute(TEAM, {"team": team_id}).fetchone()
    return dict(row) if row else None


def last_quote(conn, event_ticker: str, team_id: int) -> dict | None:
    """Most recent quote for a team's market, regardless of market status."""
    if not event_ticker or team_id is None:
        return None
    row = conn.execute(LAST_QUOTE,
                       {"event": event_ticker, "team": team_id}).fetchone()
    return dict(row) if row else None


def roster(conn, team_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(ROSTER, {"team": team_id}).fetchall()]


def roster_change_times(conn, team_id: int) -> list:
    return [r["captured_at"] for r in
            conn.execute(ROSTER_CHANGES, {"team": team_id}).fetchall()]


def price_history(conn, match_id: int, team_a_id: int) -> list[dict]:
    """[{at, p}] — team A's implied probability per hour, up to kickoff.

    Hours where only one side quoted are dropped: a one-sided book cannot
    be normalised, and guessing the other half would invent the movement
    this chart exists to show.
    """
    rows = conn.execute(PRICE_HISTORY, {"match": match_id}).fetchall()
    by_hour: dict = {}
    for r in rows:
        by_hour.setdefault(r["at"], {})[r["team_id"]] = float(r["mid"])
    out = []
    for at in sorted(by_hour):
        mids = by_hour[at]
        if len(mids) != 2 or team_a_id not in mids:
            continue
        total = sum(mids.values())
        if total <= 0:
            continue
        out.append({"at": at, "p": mids[team_a_id] / total})
    return out


def hourly_writes(conn) -> list[dict]:
    """Price-snapshot volume per hour over 48h, with missing hours filled.

    A gap must exist as a row, not be absent from the list — an hour with
    no data is the thing this is for, and a list that simply skips it looks
    like continuous collection.
    """
    from datetime import timedelta
    rows = {r["hour"]: r["rows"] for r in
            conn.execute(HOURLY_WRITES).fetchall()}
    if not rows:
        return []
    start, end = min(rows), max(rows)
    out, cur = [], start
    while cur <= end:
        out.append({"hour": cur, "rows": rows.get(cur, 0)})
        cur += timedelta(hours=1)
    return out


def slate(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(SLATE).fetchall()]


def event_sides(conn, event_ticker: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        EVENT_SIDES, {"event": event_ticker}).fetchall()]


def match(conn, match_id: int) -> dict | None:
    row = conn.execute(MATCH_TEAMS, {"match": match_id}).fetchone()
    return dict(row) if row else None


def pool_forfeit_rate(conn, days: int = 365) -> float:
    """Measured pool mean for forfeit shrinkage.

    The default prior in stats.team is a constant taken from an early look
    at the board. Measuring it means shrinkage tracks the population the
    team is actually being compared against, rather than a number that was
    true once.
    """
    row = conn.execute(POOL_FORFEIT, {"days": str(days)}).fetchone()
    return float(row["mean"]) if row and row["mean"] is not None else 0.03


def dossier_rows(conn, match_id: int, days: int = 365) -> dict | None:
    """Everything one dossier needs, in five queries.

    Returns None when the match is unknown. Both teams' full match and map
    histories come back together because head-to-head and common-opponent
    analysis need both sides, and re-querying per statistic would multiply
    round trips for rows already in memory.
    """
    info = match(conn, match_id)
    if not info:
        return None
    a, b = info["team_a_id"], info["team_b_id"]
    a_matches = team_matches(conn, a, days)
    b_matches = team_matches(conn, b, days)

    seen, combined = set(), []
    for r in a_matches + b_matches:
        if r["id"] not in seen:
            seen.add(r["id"])
            combined.append(r)

    # A map from a match BETWEEN these two teams appears in both teams'
    # map lists, so concatenating them double-counts every head-to-head
    # map. Deduped here rather than at the call site: the caller cannot
    # reasonably be expected to know the two lists overlap.
    a_maps = team_maps(conn, a, days)
    b_maps = team_maps(conn, b, days)
    seen_maps, all_maps = set(), []
    for r in a_maps + b_maps:
        key = (r["match_id"], r["map_index"])
        if key not in seen_maps:
            seen_maps.add(key)
            all_maps.append(r)

    return {
        "match": info,
        "team_a": team(conn, a),
        "team_b": team(conn, b),
        "a_matches": a_matches,
        "b_matches": b_matches,
        "all_matches": combined,
        "a_maps": a_maps,
        "b_maps": b_maps,
        "all_maps": all_maps,
        "pool_forfeit": pool_forfeit_rate(conn, days),
        "a_roster": roster(conn, a),
        "b_roster": roster(conn, b),
        "a_roster_changes": roster_change_times(conn, a),
        "b_roster_changes": roster_change_times(conn, b),
    }
