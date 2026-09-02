"""Health verdicts. Pure functions — rows in, verdicts out, no I/O.

Split from the script so the thresholds are testable without a database.
A monitor whose own logic is untested is a monitor you cannot trust to be
silent, and a monitor you cannot trust to be silent gets ignored.
"""
from __future__ import annotations

OK, WARN, FAIL = "ok", "warn", "fail"

# A 15-minute collector that has not written for an hour has missed four runs.
COLLECT_WARN_MIN = 30
COLLECT_FAIL_MIN = 60

# Neon free tier, bytes.
STORAGE_LIMIT = 512 * 1024 * 1024
STORAGE_WARN = 0.75
STORAGE_FAIL = 0.90

# Expected scheduled runs in the health window, used to notice ABSENCE.
EXPECTED_RUNS_6H = 4        # be forgiving; a 15-min collector should give ~24

UNRESOLVED_WARN = 0.15      # share of ACTIVE Kalshi events with no bo3 match
MAP_UNRESOLVED_WARN = 0.15  # share of map rows with no winner bound


def worst(*statuses: str) -> str:
    for level in (FAIL, WARN):
        if level in statuses:
            return level
    return OK


def collection_gap(minutes: float | None) -> tuple[str, str]:
    if minutes is None:
        return FAIL, "no price snapshots at all"
    if minutes >= COLLECT_FAIL_MIN:
        h = minutes / 60
        return FAIL, (f"last snapshot {h:.1f}h ago — collection is DOWN. "
                      f"Order books for this window are lost permanently.")
    if minutes >= COLLECT_WARN_MIN:
        return WARN, f"last snapshot {minutes:.0f}m ago (expected <15m)"
    return OK, f"last snapshot {minutes:.0f}m ago"


def storage(bytes_used: int | None) -> tuple[str, str]:
    if not bytes_used:
        return OK, "unknown"
    pct = bytes_used / STORAGE_LIMIT
    mb = bytes_used / 1024 / 1024
    msg = f"{mb:.0f} MB of 512 MB ({pct:.0%})"
    if pct >= STORAGE_FAIL:
        return FAIL, msg + " — writes will start failing"
    if pct >= STORAGE_WARN:
        return WARN, msg + " — prune settled snapshots or upgrade"
    return OK, msg


def _share(part: int, total: int, warn_at: float, label: str,
           ok_note: str = "") -> tuple[str, str]:
    if not total:
        return OK, f"no {label} yet"
    share = part / total
    msg = f"{part}/{total} {label} ({share:.0%})"
    return (WARN if share > warn_at else OK), msg + ok_note


def unresolved_events(unbound: int, total: int) -> tuple[str, str]:
    """Scope this to the ACTIVE slate, never to every event ever collected.

    Counting all history makes this permanently yellow — settled events from
    weeks ago were never resolution targets and never will be. An alert that
    is always on is an alert that gets ignored, which is worse than no alert,
    because it also trains you to ignore the ones beside it.
    """
    return _share(unbound, total, UNRESOLVED_WARN,
                  "active Kalshi events unbound")


def unresolved_maps(unbound: int, total: int) -> tuple[str, str]:
    return _share(unbound, total, MAP_UNRESOLVED_WARN,
                  "map rows with no winner")


def failed_runs(rows: list[dict]) -> tuple[str, str]:
    """rows: {source, status, started_at, error} for recent runs.

    ABSENCE of runs is the more dangerous signal and used to read as "ok, 0
    recent runs, none failed". A job that never starts writes no failure row,
    so a monitor that only inspects rows that exist is blind to exactly the
    outage that matters most — a workflow that is not firing at all.
    """
    bad = [r for r in rows if r.get("status") == "error"]
    if not rows:
        return FAIL, ("NO runs recorded in the last 6h — the scheduled jobs "
                      "are not firing, or are failing before they can record "
                      "a run (bad DATABASE_URL fails at this point)")
    if len(rows) < EXPECTED_RUNS_6H and not bad:
        return WARN, (f"only {len(rows)} runs in 6h — expected ~24 from a "
                      "15-minute collector")
    if not bad:
        return OK, f"{len(rows)} recent runs, none failed"
    sources = sorted({r["source"] for r in bad})
    return FAIL, f"{len(bad)}/{len(rows)} recent runs failed ({', '.join(sources)})"
