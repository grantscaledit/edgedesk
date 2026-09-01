# Edge Desk — agent instructions

Read this before touching anything. Full spec: `docs/SPEC.md`.

## What this is

A **research terminal** for Kalshi CS2 prediction markets. It assembles a
statistical dossier per match. It is **not** an edge-finder, not a fair-value
model, and never places orders.

## Stack

Python 3.11 · Flask + Jinja2 · PostgreSQL (Neon in dev) · ECharts · APScheduler
· curl_cffi + selectolax for scraping · pytest.

No ORM. Raw SQL by design — this project needs hand-written analytical queries.
No JS build step. Server-rendered HTML only.

## Rules that are not negotiable

1. **Append-only.** Any table named `*_snapshots` is INSERT-only. Never UPDATE
   one. Current state comes from the `v_*_latest` views. This is what makes
   historical reconstruction possible; breaking it silently destroys the
   project's second phase.

2. **Never split a Kalshi event ticker to get team abbreviations.** They are
   concatenated with no delimiter and are variable length: `BSTAUND` = `BSTA`
   + `UND`, but `EXMANAMAI` = `EXMANA` + `MAI`. Read the event's child markets
   — each ends in one team's abbreviation and its title carries the full name.

3. **Derive match start as `close_time - 48h`**, not by parsing the ticker's
   HHMM (which is US Eastern and easy to get wrong).

4. **The display contract.** Every rate rendered in the UI carries its sample
   size, effective sample size, and staleness. Every shrunk figure shows the
   raw record beside it (`0-6 (adj. 31%)`). Every roster-derived stat shows
   coverage (`3/5 resolved`). No exceptions — this is what stops the tool
   lying by omission.

5. **`stats/` modules are pure functions.** They take rows and return values.
   No database access, no network, no globals. That is what makes them
   testable.

6. **Fetching goes through the `Fetcher` protocol** in `edgedesk/fetch/base.py`.
   Sources take a fetcher by injection; never instantiate an HTTP client
   directly inside a source module.

7. **Reserved words.** Postgres reserves `window` and `values` — the columns
   are `stat_window` and `source_values`. Don't "fix" them back.

## bo3.gg API traps (verified 2026-09)

- **Only `[eq]` and `[in]` filters work.** Range operators like `[gte]` are
  **silently ignored** — a date filter returns the entire 79k-row table
  instead of erroring. Never rely on a date filter; use `status` + `sort` +
  a client-side cutoff with early termination.
- Match objects carry `team1_id` / `team2_id` **as integers only**. Team
  names require a separate `/teams` lookup.
- Game scores are `winner_clan_score` / `loser_clan_score` with in-game
  **clan names**, which do not reliably equal registered team names
  ("Diamant" vs "Diamant Esports"). Fuzzy-map them and keep the raw strings.
- **No CT/T splits and no half-time scores exist anywhere in bo3.** Round
  differential is computable; side splits are not. Do not spec them.
- `limit` is ignored for small values — read the page size from the
  envelope's `total.limit`.

## Source trust order

`bo3.gg` → `HLTV` → `scores24` → `tips.gg` → `egamersworld`

This mirrors Kalshi's own declared settlement precedence. On disagreement,
store the highest-trust value AND write a `source_conflicts` row. Never
silently pick a winner.

## Degradation

Missing data renders as an explicit gap, never a silent omission and never a
guess. A partial dossier is correct behaviour. Player identity is resolved
only within a team-context candidate pool; an ambiguous match shows both
candidates and binds neither.

## Current phase

**Phase 0** — Kalshi collector only. No scraping, no parsing of third-party
HTML, no web app. Files in scope:

```
migrations/001_init.sql
migrations/run.py
edgedesk/db.py
edgedesk/sources/kalshi.py
scripts/phase0_collect.py
tests/test_parsers.py
```

Do not add scrapers, Flask routes, or statistics modules until Phase 0 is
running continuously and writing snapshots.

## Testing

`pytest tests/ -q`. Parser tests run against saved fixtures in
`tests/fixtures/`. When you add a source, capture a real response as a fixture
first, then write the parser against it. A parser that returns `None` instead
of raising on a shape change is the failure mode these tests exist to catch.

## Conventions

- Prices stored as **integer cents**, never floats. `to_cents()` normalises
  the API's mixed dollar/cent representation.
- All timestamps `timestamptz`, stored UTC. Display timezone is config.
- Every collector run writes a `collection_runs` row, success or failure.
- Commit per logical unit of work, not per file.
