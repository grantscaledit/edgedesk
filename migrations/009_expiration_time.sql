-- Store expiration_time, and stop deriving start times from close_time.
--
-- CLAUDE.md rule 3 said "derive match start as close_time - 48h". That is
-- true for OPEN markets and badly wrong for settled ones: these markets
-- carry can_close_early = true ("this market will close and expire after a
-- winner is declared"), so Kalshi REWRITES close_time to the actual early
-- close once a winner exists. Measured on a real settled market, the
-- derived start was 47.3 hours early.
--
-- Consequence: every settled event had a scheduled_at about two days off,
-- the fixture-tuple window landed on the wrong day, and resolution either
-- found nothing (2,279 of 2,296 queued) or bound something unrelated —
-- "Atreides" bound to a match between Entropy and SAW.
--
-- expiration_time is the scheduled expiry and is NOT rewritten. Measured
-- against the same market, expiration_time - 48h hit the start time
-- exactly.

ALTER TABLE kalshi_markets
  ADD COLUMN IF NOT EXISTS expiration_time timestamptz;
