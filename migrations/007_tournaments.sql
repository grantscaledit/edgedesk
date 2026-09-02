-- Tournament context, and the fix for a scoring ceiling.
--
-- Resolution scores candidates as team_score*0.9 + event_score*0.1, but the
-- CANDIDATES query supplied `NULL::text AS event_name`, so event_score was
-- always 0 and no candidate could ever exceed 0.90. Against ACCEPT = 0.85
-- that made the real bar a team score of 0.944 rather than 0.85 -- far
-- stricter than designed, and the reason borderline pairs kept queueing.
-- Every successful bind in the logs printed exactly "score=0.90", which was
-- the ceiling showing itself.
--
-- bo3 has carried tournament_id on every match all along; we parsed it and
-- discarded it.
--
-- Tournament `name` and stage `title` are both stored because Kalshi's
-- rules text sometimes names the tournament ("Gamers Club Liga Serie A
-- 2026") and sometimes something closer to the stage. Best-of-both is the
-- same approach that works for team aliases.

CREATE TABLE IF NOT EXISTS tournaments (
  id           bigserial PRIMARY KEY,
  bo3_id       integer UNIQUE,
  name         text NOT NULL,
  slug         text,
  tier         text,
  tier_rank    smallint,
  series_id    integer,
  region_id    integer,
  event_type   text,        -- online | lan
  event_level  text,        -- regular | major | ...
  prize        integer,
  status       text,
  start_date   timestamptz,
  end_date     timestamptz,
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tournaments_bo3_idx ON tournaments (bo3_id);

ALTER TABLE matches
  ADD COLUMN IF NOT EXISTS bo3_tournament_id integer,
  ADD COLUMN IF NOT EXISTS bo3_stage_id      integer,
  ADD COLUMN IF NOT EXISTS stage_title       text;

CREATE INDEX IF NOT EXISTS matches_tournament_idx ON matches (bo3_tournament_id);
