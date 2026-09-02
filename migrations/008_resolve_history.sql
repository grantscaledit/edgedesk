-- Make settled events resolvable.
--
-- v_unresolved filters `status IN ('active','open')`, which is right for a
-- routine sync — you only want to bind what is still tradeable. The side
-- effect was that every event that had ever settled became permanently
-- unbindable, so ~6,000 finalised events with known outcomes and recorded
-- closing prices sat unusable.
--
-- Those rows ARE the backtest dataset: a settled market has both a result
-- and a price, which is exactly what testing a signal against the market
-- requires. Without them vs_market.py had six events to work with.
--
-- Kept as a SEPARATE view rather than relaxing the filter on v_unresolved:
-- the routine sync should keep looking at a handful of live events, not
-- re-scan six thousand historical ones every four hours.

CREATE OR REPLACE VIEW v_unresolved_history AS
SELECT
  m.event_ticker,
  MIN(m.scheduled_at)                       AS scheduled_at,
  string_agg(DISTINCT m.team_name, ' vs ')  AS teams,
  MAX(m.rules_primary)                      AS rules_primary,
  bool_or(m.match_id IS NOT NULL)           AS resolved
FROM kalshi_markets m
WHERE m.series_ticker = 'KXCS2GAME'
GROUP BY m.event_ticker
HAVING NOT bool_or(m.match_id IS NOT NULL);
