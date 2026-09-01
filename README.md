# Edge Desk

Research terminal for Kalshi CS2 prediction markets. Assembles a statistical
dossier per match — team history, map performance, roster quality, risk
factors. Not an edge-finder; it shows evidence, you form the judgment.

**Currently at Phase 0:** Kalshi market collection only.

---

## Quick start

```bash
git clone <your-repo> edgedesk && cd edgedesk
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env          # then paste your Neon connection string in
python migrations/run.py      # creates 14 tables + 4 views
python scripts/phase0_collect.py
```

Expected output:

```
  KXCS2GAME: 42 rows so far
  KXCS2MAP: 78 rows so far
OK  81 rows  (3 books)
```

Verify:

```sql
SELECT team_name, spread_cents, overround, gate_pass, volume
FROM v_slate
WHERE series_ticker = 'KXCS2GAME'
ORDER BY gate_pass DESC, spread_cents;
```

Tests:

```bash
pytest tests/ -q
```

---

## Layout

```
config/settings.toml      non-secret config (intervals, gate, stats params)
migrations/001_init.sql   canonical DDL — the schema is defined here
migrations/run.py         applies numbered .sql files once, in order
edgedesk/db.py            psycopg connection + collection_runs helpers
edgedesk/sources/kalshi.py  API client + parsers (no auth required)
scripts/phase0_collect.py   the Phase 0 collector
tests/                    fixture-based parser tests
docs/SPEC.md              condensed spec — point your AI tooling here
CLAUDE.md / .cursorrules  agent instructions (same content)
```

---

## Why Phase 0 first

Kalshi order books and intraday price paths **cannot be backfilled**. Outcomes
and closing prices can be recovered from settled markets; the microstructure
cannot. Every day the collector isn't running is a permanent hole.

The CS2 board launched around August 2026, so starting now captures a large
share of all the history that will ever exist.

---

## Running it continuously

Two GitHub Actions workflows are included — collection every 15 minutes and a
settled sweep every 6 hours. Add `DATABASE_URL` as a repository secret and
they run with no server.

Public repos get unlimited Actions minutes; private repos get 2,000/month,
and a 15-minute collector uses roughly 1,400.

Phase 3 migrates collection to a VPS under APScheduler.

---

## Non-negotiables

1. `*_snapshots` tables are INSERT-only. Read current state from `v_*_latest`.
2. Never split a Kalshi event ticker to get team abbreviations — read the
   child markets.
3. Derive match start as `close_time - 48h`.
4. Every displayed rate carries sample size, effective n, and staleness.

Full rules in `CLAUDE.md`.
