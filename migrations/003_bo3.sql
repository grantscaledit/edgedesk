-- bo3.gg spine columns.
--
-- Verified against the live API 2026-09:
--   * games expose winner/loser oriented scores with in-game CLAN names,
--     which do not reliably equal registered team names. Keep the raw
--     strings so a bad name join can always be re-derived.
--   * there are NO CT/T splits and NO half-time scores anywhere in bo3.
--     001's team_a_ct_rounds / team_a_t_rounds stay permanently NULL from
--     this source. Round differential IS computable.

BEGIN;

ALTER TABLE teams
  ADD COLUMN IF NOT EXISTS acronym   text,
  ADD COLUMN IF NOT EXISTS bo3_rank  integer,
  ADD COLUMN IF NOT EXISTS bo3_slug  text;

ALTER TABLE matches
  ADD COLUMN IF NOT EXISTS bo3_status text,
  ADD COLUMN IF NOT EXISTS tier       text,
  ADD COLUMN IF NOT EXISTS tier_rank  smallint,
  ADD COLUMN IF NOT EXISTS bo3_slug   text;

ALTER TABLE match_maps
  ADD COLUMN IF NOT EXISTS winner_clan_name text,
  ADD COLUMN IF NOT EXISTS loser_clan_name  text,
  ADD COLUMN IF NOT EXISTS rounds_count     smallint,
  ADD COLUMN IF NOT EXISTS side_assignment  text;   -- exact|fuzzy|unresolved

ALTER TABLE events
  ADD COLUMN IF NOT EXISTS bo3_id integer;

CREATE INDEX IF NOT EXISTS matches_bo3_status_idx ON matches (bo3_status);
CREATE INDEX IF NOT EXISTS teams_acronym_idx ON teams (lower(acronym));
CREATE INDEX IF NOT EXISTS teams_name_idx ON teams (lower(canonical_name));

-- Matches Kalshi lists, with resolution state. The Phase 1 working view.
CREATE OR REPLACE VIEW v_unresolved AS
SELECT DISTINCT
  m.event_ticker,
  MIN(m.scheduled_at)                       AS scheduled_at,
  string_agg(DISTINCT m.team_name, ' vs ')  AS teams,
  MAX(m.rules_primary)                      AS rules_primary,
  bool_or(m.match_id IS NOT NULL)           AS resolved
FROM kalshi_markets m
WHERE m.series_ticker = 'KXCS2GAME'
  AND m.status IN ('active','open')
GROUP BY m.event_ticker
HAVING NOT bool_or(m.match_id IS NOT NULL);

COMMIT;
