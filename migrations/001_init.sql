-- Edge Desk :: 001_init.sql
-- Phase 0 subset: identity, fixtures, Kalshi market data, operations.
-- Snapshot tables are INSERT-ONLY. Current state is read through views.
-- Later phases add: map_stat_snapshots, roster_snapshots, player_stat_snapshots,
-- raw_documents, dossiers, decisions.

BEGIN;

-- ---------------------------------------------------------------- identity

CREATE TABLE IF NOT EXISTS teams (
  id             bigserial PRIMARY KEY,
  canonical_name text        NOT NULL,
  country        text,
  is_org         boolean,
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_external_ids (
  team_id       bigint      NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  source        text        NOT NULL,
  external_id   text        NOT NULL,
  external_name text        NOT NULL,
  confidence    real        NOT NULL DEFAULT 1.0,
  linked_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source, external_id)
);
CREATE INDEX IF NOT EXISTS team_ext_team_idx ON team_external_ids (team_id);
CREATE INDEX IF NOT EXISTS team_ext_name_idx ON team_external_ids (lower(external_name));

CREATE TABLE IF NOT EXISTS events (
  id          bigserial PRIMARY KEY,
  source      text NOT NULL,
  external_id text NOT NULL,
  name        text NOT NULL,
  tier        text,
  start_date  date,
  end_date    date,
  prize_pool  text,
  UNIQUE (source, external_id)
);

-- ---------------------------------------------------------------- fixtures

CREATE TABLE IF NOT EXISTS matches (
  id                    bigserial PRIMARY KEY,
  event_id              bigint REFERENCES events(id),
  team_a_id             bigint NOT NULL REFERENCES teams(id),
  team_b_id             bigint NOT NULL REFERENCES teams(id),
  scheduled_at          timestamptz NOT NULL,
  format_bo             smallint,
  status                text NOT NULL DEFAULT 'scheduled',
  winner_team_id        bigint REFERENCES teams(id),
  decided_by_default    boolean NOT NULL DEFAULT false,
  resolution_confidence real,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT matches_status_ck CHECK (
    status IN ('scheduled','live','finished','cancelled','postponed'))
);
CREATE INDEX IF NOT EXISTS matches_sched_idx ON matches (scheduled_at);
CREATE INDEX IF NOT EXISTS matches_team_a_idx ON matches (team_a_id, scheduled_at);
CREATE INDEX IF NOT EXISTS matches_team_b_idx ON matches (team_b_id, scheduled_at);

CREATE TABLE IF NOT EXISTS match_external_ids (
  match_id    bigint NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  source      text   NOT NULL,
  external_id text   NOT NULL,
  PRIMARY KEY (source, external_id)
);

CREATE TABLE IF NOT EXISTS match_maps (
  id               bigserial PRIMARY KEY,
  match_id         bigint   NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  map_index        smallint NOT NULL,
  map_name         text,
  team_a_rounds    smallint,
  team_b_rounds    smallint,
  winner_team_id   bigint REFERENCES teams(id),
  team_a_ct_rounds smallint,   -- nullable: half-time data often unavailable
  team_a_t_rounds  smallint,   -- nullable
  is_default       boolean NOT NULL DEFAULT false,
  UNIQUE (match_id, map_index)
);

CREATE TABLE IF NOT EXISTS match_vetoes (
  id       bigserial PRIMARY KEY,
  match_id bigint   NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  step     smallint NOT NULL,
  action   text     NOT NULL,
  team_id  bigint REFERENCES teams(id),
  map_name text     NOT NULL,
  UNIQUE (match_id, step),
  CONSTRAINT veto_action_ck CHECK (action IN ('ban','pick','decider'))
);

-- ---------------------------------------------------------------- team stats

CREATE TABLE IF NOT EXISTS team_stat_snapshots (
  id             bigserial PRIMARY KEY,
  team_id        bigint      NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  source         text        NOT NULL,
  captured_at    timestamptz NOT NULL DEFAULT now(),
  stat_window    text        NOT NULL,
  win_rate       real,
  matches_played integer,
  rank_hltv      integer,
  rank_valve     integer,
  payload        jsonb
);
CREATE INDEX IF NOT EXISTS team_stat_latest_idx
  ON team_stat_snapshots (team_id, source, stat_window, captured_at DESC);

-- ---------------------------------------------------------------- kalshi

CREATE TABLE IF NOT EXISTS kalshi_markets (
  ticker        text PRIMARY KEY,
  event_ticker  text NOT NULL,
  series_ticker text NOT NULL,
  match_id      bigint REFERENCES matches(id),
  team_id       bigint REFERENCES teams(id),
  team_abbr     text,
  team_name     text,
  map_index     smallint,          -- NULL for KXCS2GAME
  title         text NOT NULL,
  status        text NOT NULL,
  result        text,              -- yes | no | NULL
  close_time    timestamptz,
  scheduled_at  timestamptz,       -- derived: close_time - 48h
  rules_primary text,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS kalshi_event_idx  ON kalshi_markets (event_ticker);
CREATE INDEX IF NOT EXISTS kalshi_match_idx  ON kalshi_markets (match_id);
CREATE INDEX IF NOT EXISTS kalshi_sched_idx  ON kalshi_markets (scheduled_at);
CREATE INDEX IF NOT EXISTS kalshi_status_idx ON kalshi_markets (status);

CREATE TABLE IF NOT EXISTS kalshi_price_snapshots (
  id            bigserial PRIMARY KEY,
  ticker        text        NOT NULL REFERENCES kalshi_markets(ticker) ON DELETE CASCADE,
  captured_at   timestamptz NOT NULL DEFAULT now(),
  yes_bid       integer,
  yes_ask       integer,
  last_price    integer,
  volume        numeric,
  open_interest numeric
);
CREATE INDEX IF NOT EXISTS kalshi_price_latest_idx
  ON kalshi_price_snapshots (ticker, captured_at DESC);

CREATE TABLE IF NOT EXISTS kalshi_book_snapshots (
  id          bigserial PRIMARY KEY,
  ticker      text        NOT NULL REFERENCES kalshi_markets(ticker) ON DELETE CASCADE,
  captured_at timestamptz NOT NULL DEFAULT now(),
  yes_levels  jsonb       NOT NULL,   -- [[price_cents, size], ...]
  no_levels   jsonb       NOT NULL
);
CREATE INDEX IF NOT EXISTS kalshi_book_latest_idx
  ON kalshi_book_snapshots (ticker, captured_at DESC);

-- ---------------------------------------------------------------- ops

CREATE TABLE IF NOT EXISTS collection_runs (
  id           bigserial PRIMARY KEY,
  source       text        NOT NULL,
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  status       text        NOT NULL DEFAULT 'running',
  rows_written integer     NOT NULL DEFAULT 0,
  error        text
);
CREATE INDEX IF NOT EXISTS collection_runs_idx ON collection_runs (source, started_at DESC);

CREATE TABLE IF NOT EXISTS resolution_queue (
  id                  bigserial PRIMARY KEY,
  kalshi_event_ticker text NOT NULL UNIQUE,
  candidates          jsonb NOT NULL,
  status              text NOT NULL DEFAULT 'open',
  resolved_match_id   bigint REFERENCES matches(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  resolved_at         timestamptz
);

CREATE TABLE IF NOT EXISTS source_conflicts (
  id          bigserial PRIMARY KEY,
  entity_type text   NOT NULL,
  entity_id   bigint NOT NULL,
  field       text   NOT NULL,
  source_values jsonb NOT NULL,
  detected_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS source_conflicts_idx ON source_conflicts (entity_type, entity_id);

-- ---------------------------------------------------------------- views

CREATE OR REPLACE VIEW v_price_latest AS
SELECT DISTINCT ON (ticker) *
FROM kalshi_price_snapshots
ORDER BY ticker, captured_at DESC;

CREATE OR REPLACE VIEW v_book_latest AS
SELECT DISTINCT ON (ticker) *
FROM kalshi_book_snapshots
ORDER BY ticker, captured_at DESC;

CREATE OR REPLACE VIEW v_team_stats_latest AS
SELECT DISTINCT ON (team_id, source, stat_window) *
FROM team_stat_snapshots
ORDER BY team_id, source, stat_window, captured_at DESC;

-- Open CS2 game markets with their newest price, spread and overround.
-- Overround is computed per EVENT (both sides' asks), so it is attached
-- via the event_ticker join rather than per market.
CREATE OR REPLACE VIEW v_slate AS
WITH latest AS (
  SELECT DISTINCT ON (ticker) ticker, captured_at, yes_bid, yes_ask,
         last_price, volume, open_interest
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
  l.yes_bid, l.yes_ask, l.last_price, l.volume, l.open_interest,
  (l.yes_ask - l.yes_bid) AS spread_cents,
  e.overround, e.sides,
  (e.sides = 2
   AND (l.yes_ask - l.yes_bid) <= 5
   AND e.overround <= 106
   AND COALESCE(l.volume, 0) > 0)          AS gate_pass,
  l.captured_at
FROM kalshi_markets m
JOIN latest l    ON l.ticker = m.ticker
LEFT JOIN event_ask e ON e.event_ticker = m.event_ticker
WHERE m.status IN ('active','open');

COMMIT;
