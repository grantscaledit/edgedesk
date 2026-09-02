#!/usr/bin/env python3
"""Is the system actually collecting? Answers it in one command.

    python scripts/healthcheck.py            # print a report
    python scripts/healthcheck.py --alert    # also post failures to Discord
    python scripts/healthcheck.py --hourly   # show the last 48h write pattern

Exit code is 0 when healthy, 1 when anything is FAIL — so it doubles as a CI
gate and as the thing a scheduled job calls to notice its own silence.

This exists because the collector went down and was discovered by looking at
a screenshot of the Actions tab. Order books cannot be backfilled, so a
silent collector is the one failure in this project that destroys work
permanently rather than just delaying it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import db, health, notify                          # noqa: E402

LAST_SNAPSHOT = """
SELECT EXTRACT(EPOCH FROM (now() - MAX(captured_at))) / 60 AS minutes
FROM kalshi_price_snapshots;
"""

HOURLY = """
SELECT date_trunc('hour', captured_at) AS hr, count(*) AS rows
FROM kalshi_price_snapshots
WHERE captured_at > now() - interval '48 hours'
GROUP BY 1 ORDER BY 1 DESC;
"""

RECENT_RUNS = """
SELECT source, status, started_at, error
FROM collection_runs
WHERE started_at > now() - interval '6 hours'
ORDER BY started_at DESC;
"""

LAST_OK_PER_SOURCE = """
SELECT source,
       MAX(started_at) FILTER (WHERE status = 'ok')    AS last_ok,
       MAX(started_at) FILTER (WHERE status = 'error') AS last_error
FROM collection_runs GROUP BY source ORDER BY source;
"""

# ACTIVE slate only. Every event ever collected includes weeks of settled
# markets that were never resolution targets, which pins this at ~99% and
# makes the check useless.
EVENTS = """
SELECT count(DISTINCT event_ticker) FILTER (WHERE match_id IS NULL) AS unbound,
       count(DISTINCT event_ticker)                                 AS total
FROM kalshi_markets
WHERE scheduled_at > now() - interval '2 days'
  AND scheduled_at < now() + interval '7 days';
"""

# Informational: how much of the historical board is bound. Backfill target,
# not an alert -- these are the rows a future backtest reads.
EVENTS_ALL = """
SELECT count(DISTINCT event_ticker) FILTER (WHERE match_id IS NOT NULL) AS bound,
       count(DISTINCT event_ticker)                                     AS total
FROM kalshi_markets;
"""

MAPS = """
SELECT count(*) FILTER (WHERE winner_team_id IS NULL) AS unbound,
       count(*)                                       AS total
FROM match_maps;
"""

COVERAGE = """
SELECT count(*) FILTER (WHERE mm.match_id IS NOT NULL) AS with_maps,
       count(*)                                        AS finished
FROM matches m
LEFT JOIN (SELECT DISTINCT match_id FROM match_maps) mm ON mm.match_id = m.id
WHERE m.status = 'finished';
"""

STORAGE = "SELECT pg_database_size(current_database()) AS bytes;"

BIGGEST = """
SELECT relname AS table, pg_total_relation_size(c.oid) AS bytes
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY 2 DESC LIMIT 5;
"""

ICON = {"ok": "  ok  ", "warn": " WARN ", "fail": " FAIL "}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert", action="store_true",
                    help="post to Discord when anything is FAIL")
    ap.add_argument("--hourly", action="store_true",
                    help="print the last 48h of write volume by hour")
    a = ap.parse_args()

    lines, statuses = [], []

    def check(label, result):
        status, msg = result
        statuses.append(status)
        lines.append(f"[{ICON[status]}] {label:22} {msg}")

    with db.connect() as conn:
        one = lambda q: conn.execute(q).fetchone()               # noqa: E731

        check("collection", health.collection_gap(one(LAST_SNAPSHOT)["minutes"]))
        check("recent runs", health.failed_runs(
            [dict(r) for r in conn.execute(RECENT_RUNS).fetchall()]))
        ev = one(EVENTS)
        check("event resolution",
              health.unresolved_events(ev["unbound"], ev["total"]))
        mp = one(MAPS)
        check("map winners",
              health.unresolved_maps(mp["unbound"], mp["total"]))
        check("storage", health.storage(one(STORAGE)["bytes"]))

        allev = one(EVENTS_ALL)
        share = (100 * allev["bound"] / allev["total"]) if allev["total"] else 0
        lines.append(f"[{ICON['ok']}] {'historical binding':22} "
                     f"{allev['bound']}/{allev['total']} events ever bound "
                     f"({share:.0f}%) — backfill target")

        cov = one(COVERAGE)
        pct = (100 * cov["with_maps"] / cov["finished"]) if cov["finished"] else 0
        lines.append(f"[{ICON['ok']}] {'map coverage':22} "
                     f"{cov['with_maps']}/{cov['finished']} finished matches "
                     f"({pct:.0f}%)")

        print("\n".join(lines))

        print("\n  last successful run by source")
        for r in conn.execute(LAST_OK_PER_SOURCE).fetchall():
            ok = r["last_ok"].strftime("%Y-%m-%d %H:%M") if r["last_ok"] else "never"
            err = f"   last error {r['last_error']:%Y-%m-%d %H:%M}" if r["last_error"] else ""
            print(f"    {r['source']:14} {ok}{err}")

        print("\n  largest tables")
        for r in conn.execute(BIGGEST).fetchall():
            print(f"    {r['table']:26} {r['bytes']/1024/1024:8.1f} MB")

        errs = [r for r in conn.execute(RECENT_RUNS).fetchall()
                if r["status"] == "error"]
        if errs:
            print("\n  recent errors")
            for r in errs[:5]:
                print(f"    {r['started_at']:%m-%d %H:%M} {r['source']}: "
                      f"{notify.redact((r['error'] or '')[:160])}")

        if a.hourly:
            print("\n  writes per hour (last 48h)")
            rows = conn.execute(HOURLY).fetchall()
            if not rows:
                print("    none")
            for r in rows:
                bar = "#" * min(40, int(r["rows"] / 25))
                print(f"    {r['hr']:%m-%d %H:00}  {r['rows']:6}  {bar}")

    overall = health.worst(*statuses)
    print(f"\n{overall.upper()}")

    if a.alert and overall == health.FAIL:
        body = "\n".join(line for line in lines if "FAIL" in line)
        notify.send("Edge Desk health check FAILED", notify.redact(body))

    sys.exit(1 if overall == health.FAIL else 0)


if __name__ == "__main__":
    main()
