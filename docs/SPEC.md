# Edge Desk — condensed specification

Agent-readable reference. Full narrative versions live as published documents;
this is the version to point Cursor and Claude Code at.

---

## 1. Product

A research terminal for Kalshi CS2 prediction markets. For every listed match
it generates a **dossier**: team history, map performance, roster quality,
head-to-head, and risk factors. Pricing is shown only where the market is
liquid enough to act on.

Explicitly out of scope for v1: fair-value modelling, bookmaker odds ingestion,
automated execution, live in-play repricing.

---

## 2. Verified platform behaviour (Kalshi)

Confirmed against the live API, August 2026. Re-verify before Phase 1.

| Fact | Value |
|---|---|
| Base URL | `https://api.elections.kalshi.com/trade-api/v2` |
| Auth for market data | **none required** |
| Rate limit (public) | ~30 req/s |
| Series | `KXCS2GAME` (match winner), `KXCS2MAP` (per-map winner) |
| Settlement sources | bo3.gg → HLTV → egamersworld |
| Fee | `taker = 0.07 × C × (1−C)`; `maker = 0.25 × taker` |
| Settled history | nothing closed before 2026-08-01 |

**Ticker grammar**

```
KXCS2GAME-{YY}{MON}{DD}{HHMM}{ABBR_A}{ABBR_B}-{WINNER_ABBR}
KXCS2MAP -{YY}{MON}{DD}{HHMM}{ABBR_A}{ABBR_B}-{MAP_NO}-{WINNER_ABBR}
```

`HHMM` is **US Eastern**. Derive start as `close_time - 48h` instead.

**Do not split the event ticker.** Abbreviations are concatenated with no
delimiter and vary in length (`BSTAUND` = `BSTA`+`UND`; `EXMANAMAI` =
`EXMANA`+`MAI`). Read the child markets.

**Forfeit rule (verbatim):** *"If a match is forfeited, cancelled, or otherwise
not played before gameplay begins, the market will resolve to Fair Market
Price."* A pre-game forfeit is therefore a **scratch at market price, not a
loss** — no-show risk measures position-unwind risk. A forfeit after gameplay
begins resolves normally.

---

## 3. Data architecture

```
Kalshi API          fixtures, prices, order books        (free, no auth)
   ↓ fixture-tuple resolution: (team A, team B, start ±30m)
bo3.gg API          SPINE — schedule, results, per-map scores, defwin flags
   ↓ enrich
HLTV                team stats where covered → PLAYER stats where not
   ↓ cross-check
scores24 / tips.gg / egamersworld    agreement scoring, conflict flags
```

HLTV cannot be the spine: it has no coverage of ESEA Advanced or Gamers Club
Liga Série A, and serves inconsistent cached responses.

---

## 4. Schema rules

- `*_snapshots` tables are **INSERT-only**. Current state via `v_*_latest`
  views using `DISTINCT ON`.
- Prices stored as **integer cents**.
- Timestamps `timestamptz`, UTC.
- Reserved-word renames: `stat_window` (not `window`), `source_values`
  (not `values`).

Phase 0 tables: `teams`, `team_external_ids`, `events`, `matches`,
`match_external_ids`, `match_maps`, `match_vetoes`, `team_stat_snapshots`,
`kalshi_markets`, `kalshi_price_snapshots`, `kalshi_book_snapshots`,
`collection_runs`, `resolution_queue`, `source_conflicts`.

Later phases add: `map_stat_snapshots`, `roster_snapshots`,
`player_stat_snapshots`, `raw_documents`, `dossiers`, `decisions`.

Canonical DDL: `migrations/001_init.sql`.

---

## 5. Liquidity gate

Price commentary renders only when **all** hold:

```
sides            = 2
spread_cents    <= 5
overround       <= 106      # sum of both sides' asks
depth_within_3c >= 100      # phase 3+, needs book data
volume           > 0
```

Computed as `gate_pass` in the `v_slate` view. Gate-failing dossiers still
render in full, labelled research-only.

Reference — a real four-match slate: spreads of 3¢, 27¢, 38¢, 59¢; overrounds
104, 121, 133, 159. One passed.

---

## 6. Resolution

1. **Cached** — `kalshi_markets.match_id` already set.
2. **Fixture tuple** — names from child market titles, start from
   `close_time − 48h`, event from `rules_primary`. Score candidates in a
   ±30 min window: `0.45·sim(A) + 0.45·sim(B) + 0.10·sim(event)`. Accept ≥ 0.85.
3. **Fuzzy fallback** — widen to ±3h, rapidfuzz `token_set_ratio`, both names
   ≥ 0.92, flag reduced confidence.
4. **Review queue** — top-5 candidates, resolved once by hand, cached forever.

**Players:** candidate pool = current/recent roster ∪ opponents faced in 90 days
∪ nickname search. Exact match binds; fuzzy ≥ 0.90 *within pool* binds as
"probable"; fuzzy outside pool shows both candidates and binds neither.

**Conflicts:** store highest-trust value, write a `source_conflicts` row,
surface a disagreement badge.

---

## 7. Tier-1 statistics

Pure functions, no I/O.

```
n_eff(rows, half_life=90)   = Σ 0.5^(age_days / half_life)
staleness(rows)             = days since most recent row
shrink(w, n, mean, k=10)    = (w + k·mean) / (n + k)

no_show_risk(team)          = clamp(forfeit_rate
                                    + 0.02·roster_changes_30d
                                    + 0.01·(concurrent_tournaments − 1)
                                    + 0.01·matches_next_48h, 0, 0.40)

roster_continuity(team)     = maps_by_current_five / maps_in_sample
                              → None when rosters unpublished ("unverified")

round_win_pct(maps)         = Σ rounds_won / Σ rounds_played
avg_round_diff(maps)        = mean(won − lost)
ct_t_split(maps)            = None when half-time data absent

talent_components(roster)   = {combined_maps, avg_rating,
                               count_below_1.00, count_inactive_90d,
                               roster_days_together, coverage}
```

`talent_components` returns a **dict, never a composite score**.

Deferred: Glicko-2 RD, opponent-adjusted win rate, pistol rate, close-game
record, star dependency, veto leverage, source-agreement score.

---

## 8. Application

| Route | Purpose |
|---|---|
| `GET /` | Slate, gate-passing first |
| `GET /match/<id>` | 13-panel dossier |
| `POST /match/<id>/refresh` | Rebuild from views (does not scrape) |
| `POST /match/<id>/decision` | Insert decision row |
| `GET /queue` | Resolution review |
| `GET /health` | Per-source last success, failures, cache stats |
| `GET /log` | Decision history + calibration |

**Decision log:** `prob_team_a`, `prob_team_b`, `action` (bet_a / bet_b /
no_bet), `price_cents`, `size_contracts`, `tags[]`. No free text. Tags:
`talent_gap`, `fatigue`, `forfeit_risk`, `map_pool`, `form`, `price_value`,
`liquidity`, `roster_churn`, `coin_flip`.

Submit enabled on every dossier — **logging a no-bet is the point.**

**Scoring:** the settled sweep writes `result`, back-fills `outcome_team_id`,
`scored_at`, `brier`. Markets resolving to Fair Market Price are excluded from
scoring and recorded separately.

**Charts (ECharts, CDN, no build step):** map-pool heatmap, player rating
distribution, round-differential strip. No price-history chart in v1.

---

## 9. Collector cadence

| Source | Interval | Delay | Cache |
|---|---|---|---|
| Kalshi markets | 5 min | none | none |
| Kalshi books (gate-passing) | 5 min | none | none |
| Kalshi books (other) | 30 min | none | none |
| Kalshi settled sweep | 6 h | none | none |
| bo3.gg | 30 min | 1 s | 15 min |
| HLTV team | 12 h | 3–5 s | 12 h |
| HLTV player | 24 h | 3–5 s | 24 h |
| HLTV match watch | 15 min, T−6h → T+0 | 3–5 s | none |
| Secondary sources | 60 min | 3 s | 60 min |

**Match watch** scans for: forfeit language (`forfeit`, `unable to field`,
`default`, `walkover`), veto publication, lineup diffs, schedule changes.
Alerts immediately on any hit.

**Alert on a parser returning zero rows where it previously returned some** —
that is the silent-breakage detector.

---

## 10. Phases

| Phase | Scope | Hours |
|---|---|---|
| 0 | Kalshi collector → Postgres, GH Actions cron | 10–14 |
| 1 | bo3.gg client, resolution, CLI dossier, backfill | 20–28 |
| 2 | HLTV scrapers + player fallback, match watch | 22–32 |
| 3 | Tier-1 stats, secondary sources, VPS deploy | 10–16 |
| 4 | Flask app, decision log, scoring | 22–34 |
| 5 | ECharts panels | 15–25 |

Backfill: **12 months, one hop** from Kalshi-listed teams. Separate one-off
script, checkpointed, not part of the schedule.

Retention: prune `raw_documents` older than **30 days**; parsed data kept
forever.

---

## 11. Known gaps

- CT/T splits best-effort — half-time data often unavailable.
- Veto data mostly unavailable (`veto_bans: "not_provided"` on ESEA) and
  irrelevant in Bo1, which is most of the board.
- Roster continuity often uncomputable at tier D → "unverified".
- HLTV cache inconsistency is unfixable at the fetch layer; mitigate by
  reading twice and comparing.
- Order books and intraday price paths are **forward-only**. Only outcomes and
  closing prices are backfillable.

**Open discovery tasks:** enumerate `/series` to locate the totals/spread
market; confirm bo3.gg filter grammar and whether half-time rounds are exposed;
measure the one-hop team universe before the full backfill; verify Kalshi's
terms on automated collection.
