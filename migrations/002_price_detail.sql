-- Adds fields the Kalshi REST response actually exposes: the opposite side's
-- quote, top-of-book sizes, Kalshi's own liquidity measure, and 24h volume.
-- Top-of-book size means the liquidity gate no longer needs an order-book
-- fetch for its depth test. NOTE: the 25-contract top-of-book threshold is a
-- starting guess, not measured. Recalibrate once you have a day of real data
-- (see the size distribution query in the README).

BEGIN;

ALTER TABLE kalshi_price_snapshots
  ADD COLUMN IF NOT EXISTS no_bid       integer,
  ADD COLUMN IF NOT EXISTS no_ask       integer,
  ADD COLUMN IF NOT EXISTS yes_bid_size numeric,
  ADD COLUMN IF NOT EXISTS yes_ask_size numeric,
  ADD COLUMN IF NOT EXISTS liquidity    integer,
  ADD COLUMN IF NOT EXISTS volume_24h   numeric;

DROP VIEW IF EXISTS v_slate;
DROP VIEW IF EXISTS v_price_latest;

CREATE VIEW v_price_latest AS
SELECT DISTINCT ON (ticker) *
FROM kalshi_price_snapshots
ORDER BY ticker, captured_at DESC;

CREATE VIEW v_slate AS
WITH latest AS (
  SELECT DISTINCT ON (ticker) *
  FROM kalshi_price_snapshots
  ORDER BY ticker, captured_at DESC
),
event_ask AS (
  SELECT m.event_ticker, SUM(l.yes_ask) AS overround, COUNT(*) AS sides
  FROM kalshi_markets m
  JOIN latest l ON l.ticker = m.ticker
  WHERE m.status IN ('active','open')
  GROUP BY m.event_ticker
)
SELECT
  m.ticker, m.event_ticker, m.series_ticker, m.team_name, m.team_abbr,
  m.map_index, m.status, m.scheduled_at, m.close_time,
  l.yes_bid, l.yes_ask, l.no_bid, l.no_ask, l.last_price,
  l.yes_bid_size, l.yes_ask_size, l.liquidity,
  l.volume, l.volume_24h, l.open_interest,
  (l.yes_ask - l.yes_bid)                          AS spread_cents,
  LEAST(l.yes_bid_size, l.yes_ask_size)            AS top_depth,
  e.overround, e.sides,
  (e.sides = 2
   AND (l.yes_ask - l.yes_bid) <= 5
   AND e.overround <= 106
   AND COALESCE(l.volume, 0) > 0
   AND COALESCE(LEAST(l.yes_bid_size, l.yes_ask_size), 0) >= 25)  AS gate_pass,
  l.captured_at
FROM kalshi_markets m
JOIN latest l ON l.ticker = m.ticker
LEFT JOIN event_ask e ON e.event_ticker = m.event_ticker
WHERE m.status IN ('active','open');

COMMIT;
