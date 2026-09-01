#!/usr/bin/env python3
"""Phase 0 collector.

Standalone by design — no scraping, no parsing of third-party HTML, no app.
Its only job is to capture Kalshi CS2 market state, which is NOT backfillable:
order books and intraday price paths exist only if you recorded them.

Runs from GitHub Actions or a cron. Idempotent: markets upsert, snapshots append.

    python scripts/phase0_collect.py                 # open markets + books
    python scripts/phase0_collect.py --settled       # also sweep settled results
    python scripts/phase0_collect.py --no-books      # skip books (fast)
    python scripts/phase0_collect.py --diagnose      # explain, write nothing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db                                    # noqa: E402
from edgedesk.sources.kalshi import (                      # noqa: E402
    SERIES, Kalshi, parse_market, parse_orderbook,
)

UPSERT_MARKET = """
INSERT INTO kalshi_markets
  (ticker, event_ticker, series_ticker, team_abbr, team_name, map_index,
   title, status, result, close_time, scheduled_at, rules_primary, updated_at)
VALUES
  (%(ticker)s, %(event_ticker)s, %(series_ticker)s, %(team_abbr)s, %(team_name)s,
   %(map_index)s, %(title)s, %(status)s, %(result)s, %(close_time)s,
   %(scheduled_at)s, %(rules_primary)s, now())
ON CONFLICT (ticker) DO UPDATE SET
  status        = EXCLUDED.status,
  result        = COALESCE(EXCLUDED.result, kalshi_markets.result),
  close_time    = EXCLUDED.close_time,
  scheduled_at  = EXCLUDED.scheduled_at,
  team_name     = COALESCE(EXCLUDED.team_name, kalshi_markets.team_name),
  rules_primary = COALESCE(EXCLUDED.rules_primary, kalshi_markets.rules_primary),
  updated_at    = now();
"""

INSERT_PRICE = """
INSERT INTO kalshi_price_snapshots
  (ticker, yes_bid, yes_ask, no_bid, no_ask, last_price,
   yes_bid_size, yes_ask_size, liquidity, volume, volume_24h, open_interest)
VALUES (%(ticker)s, %(yes_bid)s, %(yes_ask)s, %(no_bid)s, %(no_ask)s, %(last_price)s,
        %(yes_bid_size)s, %(yes_ask_size)s, %(liquidity)s, %(volume)s,
        %(volume_24h)s, %(open_interest)s);
"""

INSERT_BOOK = """
INSERT INTO kalshi_book_snapshots (ticker, yes_levels, no_levels)
VALUES (%s, %s, %s);
"""


def book_config() -> tuple[int, float]:
    """Read thresholds AT CALL TIME, after .env has been loaded.

    Reading these at module import would evaluate them before db._load_env()
    runs, silently ignoring anything set in .env.
    """
    db._load_env()
    return (
        int(os.environ.get("BOOK_MAX_SPREAD", "100")),
        float(os.environ.get("BOOK_MIN_VOLUME", "0")),
    )


def book_verdict(row: dict, max_spread: int, min_volume: float) -> str:
    """Return 'fetch' or a reason string. Used for both filtering and stats."""
    bid, ask = row.get("yes_bid"), row.get("yes_ask")
    if bid is None or ask is None:
        return "no quote"
    if (ask - bid) > max_spread:
        return f"spread > {max_spread}"
    if float(row.get("volume") or 0) < min_volume:
        return f"volume < {min_volume:g}"
    return "fetch"


def collect(settled: bool = False, books: bool = True, diagnose: bool = False) -> int:
    max_spread, min_volume = book_config()
    print(f"book filter: max_spread={max_spread}  min_volume={min_volume:g}"
          f"{'   [DIAGNOSE — no writes]' if diagnose else ''}")

    written = 0
    reasons: Counter[str] = Counter()
    targets: list[str] = []

    with db.connect() as conn, Kalshi() as k:
        run_id = None if diagnose else db.start_run(conn, "kalshi")
        try:
            statuses = ["open"] + (["settled"] if settled else [])

            for series in SERIES:
                seen = 0
                for raw in k.markets(series, status=statuses[0]):
                    row = parse_market(raw)
                    seen += 1
                    if not diagnose:
                        conn.execute(UPSERT_MARKET, row)
                        conn.execute(INSERT_PRICE, row)
                        written += 1
                    if books:
                        verdict = book_verdict(row, max_spread, min_volume)
                        reasons[f"{series}: {verdict}"] += 1
                        if verdict == "fetch":
                            targets.append(row["ticker"])
                print(f"  {series} open: {seen} markets")

                if settled:
                    n = 0
                    for raw in k.markets(series, status="settled"):
                        row = parse_market(raw)
                        n += 1
                        if not diagnose:
                            conn.execute(UPSERT_MARKET, row)
                            conn.execute(INSERT_PRICE, row)
                            written += 1
                    print(f"  {series} settled: {n} markets")
                if not diagnose:
                    conn.commit()

            if books:
                print("\nbook eligibility:")
                for reason, n in sorted(reasons.items()):
                    print(f"  {n:4d}  {reason}")

            if books and not diagnose:
                ok = 0
                for ticker in targets:
                    try:
                        yes, no = parse_orderbook(k.orderbook(ticker))
                        conn.execute(INSERT_BOOK, (ticker, json.dumps(yes), json.dumps(no)))
                        written += 1
                        ok += 1
                    except Exception as exc:                 # noqa: BLE001
                        print(f"  book {ticker} failed: {exc}", file=sys.stderr)
                    time.sleep(0.05)
                conn.commit()
                print(f"\nbooks written: {ok}/{len(targets)}")

            if not diagnose:
                db.finish_run(conn, run_id, "ok", written)
                print(f"OK  {written} rows")
        except Exception as exc:                             # noqa: BLE001
            if run_id is not None:
                db.finish_run(conn, run_id, "error", written, str(exc))
            raise
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--settled", action="store_true", help="also sweep settled markets")
    ap.add_argument("--no-books", action="store_true", help="skip order books")
    ap.add_argument("--diagnose", action="store_true",
                    help="report eligibility without writing anything")
    a = ap.parse_args()
    collect(settled=a.settled, books=not a.no_books, diagnose=a.diagnose)


if __name__ == "__main__":
    main()
