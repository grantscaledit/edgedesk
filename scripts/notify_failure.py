#!/usr/bin/env python3
"""Post a workflow failure to Discord. Called from CI's `if: failure()` step.

    python scripts/notify_failure.py "phase0-collect" "https://github.com/.../runs/123"

A separate file rather than a heredoc inside the workflow YAML: block
scalars, shell quoting and GitHub's ${{ }} substitution all interact badly,
and a broken alerting path only reveals itself at the exact moment something
else is already on fire.

Always exits 0. This runs when a job has ALREADY failed; the job's red status
is the signal, and an exception here would only mask the original error.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgedesk import notify                                      # noqa: E402

BODIES = {
    "phase0-collect":
        "Kalshi collection is not running. Order books and intraday price "
        "paths for this window CANNOT be backfilled — every missed run is a "
        "permanent hole.",
    "settled-sweep":
        "The settled sweep failed. Outcomes are recoverable later, so this "
        "is not urgent, but scoring will be incomplete until it runs.",
    "phase1-sync":
        "bo3 sync or map ingest failed. Fixtures will go stale and new "
        "Kalshi events will not bind to matches.",
}


def main() -> int:
    job = sys.argv[1] if len(sys.argv) > 1 else "unknown job"
    url = sys.argv[2] if len(sys.argv) > 2 else ""
    body = BODIES.get(job, "A scheduled job failed.")
    if url:
        body += f"\n{url}"
    delivered = notify.send(f"{job} FAILED", notify.redact(body))
    print("alert delivered" if delivered
          else "alert NOT delivered (no webhook configured, or send failed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
