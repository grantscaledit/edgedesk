-- Observed alias dictionary.
--
-- bo3 supplies an acronym for only about half of teams (168 of 323 are NULL),
-- and the acronym is the second alias that makes "NAVI" reachable from
-- "Natus Vincere". Teams without one are matched on their registered name
-- alone, which is exactly the weak case fixture resolution keeps queueing.
--
-- Map ingest produces the fix as a by-product: every resolved map row pairs a
-- team_id with the in-game clan tag that side actually used. Those are real,
-- observed aliases -- not invented ones. Harvesting them back into a
-- dictionary makes the next resolution pass strictly better informed.
--
-- Column names avoid `alias` even though Postgres does not reserve it. We
-- have already lost a migration to `window` and `values`; the caution is
-- cheaper than the re-run.

CREATE TABLE IF NOT EXISTS team_aliases (
  id          bigserial PRIMARY KEY,
  team_id     bigint      NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  alias_name  text        NOT NULL,          -- as observed, verbatim
  alias_norm  text        NOT NULL,          -- normalised for matching
  source      text        NOT NULL,          -- 'clan' | 'manual' | 'bo3'
  times_seen  integer     NOT NULL DEFAULT 1,
  first_seen  timestamptz NOT NULL DEFAULT now(),
  last_seen   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (team_id, alias_norm)
);

CREATE INDEX IF NOT EXISTS team_aliases_norm_idx ON team_aliases (alias_norm);
CREATE INDEX IF NOT EXISTS team_aliases_team_idx ON team_aliases (team_id);

-- An alias seen for more than one team identifies nothing. "BIG" used by both
-- the parent club and its academy must never be allowed to bind either.
-- Excluded here rather than at every call site, so a future query cannot
-- forget the guard.
CREATE OR REPLACE VIEW v_team_aliases AS
SELECT a.team_id, a.alias_name, a.alias_norm, a.source, a.times_seen
FROM team_aliases a
WHERE a.alias_norm IN (
  SELECT alias_norm FROM team_aliases
  GROUP BY alias_norm HAVING count(DISTINCT team_id) = 1
);
