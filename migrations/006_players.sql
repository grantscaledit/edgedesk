-- Player identity and roster history.
--
-- bo3 gives identity and CURRENT team membership, and no performance data
-- whatsoever (/players_stats and /game_players are 404, and game objects
-- carry no player keys). Ratings still require HLTV. Rosters do not.
--
-- That matters more than it first appears, because the hardest part of
-- scraping HLTV was never fetching -- it was knowing which "Twistzz" is
-- which. With nickname, real name, country and current team from a source
-- we already trust, the candidate pool for player resolution is exact
-- rather than inferred.
--
-- roster_snapshots is APPEND-ONLY and written only when a team's roster
-- actually changes. bo3 exposes today's lineup and no history, so roster
-- history is something we can only accumulate going forward -- the same
-- reason Phase 0 could not wait. It is also what finally supplies
-- roster_changes_30d, the input no_show_risk has been declaring missing.

CREATE TABLE IF NOT EXISTS players (
  id         bigserial PRIMARY KEY,
  bo3_id     integer UNIQUE,
  nickname   text NOT NULL,
  first_name text,
  last_name  text,
  country_id integer,
  birthday   date,
  role       text,
  slug       text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS players_nickname_idx ON players (lower(nickname));

-- One row per (team, player) at the moment a roster change was observed.
-- Never UPDATE. Current lineup comes from v_roster_latest.
CREATE TABLE IF NOT EXISTS roster_snapshots (
  id          bigserial PRIMARY KEY,
  team_id     bigint      NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  player_id   bigint      NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  captured_at timestamptz NOT NULL DEFAULT now(),
  source      text        NOT NULL DEFAULT 'bo3'
);

CREATE INDEX IF NOT EXISTS roster_team_idx ON roster_snapshots (team_id, captured_at DESC);

-- The most recently captured lineup per team.
CREATE OR REPLACE VIEW v_roster_latest AS
WITH last_capture AS (
  SELECT team_id, MAX(captured_at) AS at
  FROM roster_snapshots GROUP BY team_id
)
SELECT r.team_id, r.player_id, r.captured_at, r.source,
       p.nickname, p.first_name, p.last_name, p.country_id, p.role
FROM roster_snapshots r
JOIN last_capture l ON l.team_id = r.team_id AND l.at = r.captured_at
JOIN players p ON p.id = r.player_id;

-- Distinct capture moments per team: each one is an observed roster change,
-- which is what roster_changes_30d counts.
CREATE OR REPLACE VIEW v_roster_changes AS
SELECT team_id, captured_at, count(*) AS players
FROM roster_snapshots
GROUP BY team_id, captured_at;
