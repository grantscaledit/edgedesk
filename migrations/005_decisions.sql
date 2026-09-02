-- Decision log and calibration.
--
-- The point of this table is not record-keeping, it is finding out whether
-- the dossiers actually improve judgement. That requires logging the calls
-- BEFORE outcomes are known, including the ones where nothing was bet --
-- a log of only the bets placed is a biased sample of the decisions made,
-- and would flatter the tool.
--
-- Two Brier scores are stored per row: the user's forecast and the market's
-- implied probability at the same moment. Absolute accuracy is close to
-- meaningless in a market this efficient; the only question that matters is
-- whether the forecast beat the price it was looking at.

CREATE TABLE IF NOT EXISTS decisions (
  id                  bigserial PRIMARY KEY,
  user_id             text        NOT NULL DEFAULT 'default',
  match_id            bigint      REFERENCES matches(id) ON DELETE SET NULL,
  kalshi_event_ticker text,
  team_a_id           bigint      REFERENCES teams(id),
  team_b_id           bigint      REFERENCES teams(id),

  -- The forecast. team_b is implied: probabilities sum to 1 by definition,
  -- so storing both invites them to disagree.
  prob_team_a         numeric(5,4) NOT NULL
                      CHECK (prob_team_a >= 0 AND prob_team_a <= 1),

  action              text        NOT NULL
                      CHECK (action IN ('bet_a', 'bet_b', 'no_bet')),
  price_cents         smallint,
  size_contracts      integer,

  -- Structured only. Free text cannot be aggregated, and a reason you
  -- cannot count is a reason you cannot learn from.
  tags                text[]      NOT NULL DEFAULT '{}',

  -- The market's implied probability for team A when the call was made.
  -- Captured at decision time because it is unrecoverable afterwards.
  market_prob_a       numeric(5,4),
  created_at          timestamptz NOT NULL DEFAULT now(),

  -- Scoring, filled in by scripts/review.py --score once settled.
  result              text CHECK (result IN ('team_a','team_b','fmp','void')),
  outcome_team_id     bigint REFERENCES teams(id),
  brier               numeric(6,5),
  market_brier        numeric(6,5),
  scored_at           timestamptz
);

CREATE INDEX IF NOT EXISTS decisions_user_idx    ON decisions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS decisions_match_idx   ON decisions (match_id);
CREATE INDEX IF NOT EXISTS decisions_unscored_idx ON decisions (scored_at)
  WHERE scored_at IS NULL;

-- Scored decisions only. FMP and void are excluded: under Kalshi's rules a
-- pre-game forfeit resolves to Fair Market Price, so the position is
-- unwound at the prevailing price rather than won or lost. Scoring it as
-- either would be scoring an event that never happened.
CREATE OR REPLACE VIEW v_decision_scores AS
SELECT d.*,
       (d.market_brier - d.brier) AS brier_edge
FROM decisions d
WHERE d.scored_at IS NOT NULL
  AND d.result IN ('team_a', 'team_b');
