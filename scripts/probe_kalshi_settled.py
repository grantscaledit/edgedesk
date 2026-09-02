#!/usr/bin/env python3
"""Why are there no settled markets? Read-only probe.

    python scripts/probe_kalshi_settled.py

vs_market.py found 0 settled rows. Either settled.yml has not run yet, or
the status token we ask for is wrong and Kalshi is returning HTTP 200 with
an empty list — the same silent-empty failure bo3 kept producing, where the
status code says success and the answer is nothing.

This asks the API directly which status values return rows, and shows the
fields on a real settled market so we can confirm the result field is
called what the parser thinks it is.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx                                                     # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ["KXCS2GAME", "KXCS2MAP"]
STATUSES = ["open", "closed", "settled", "finalized", "determined",
            "unopened", "inactive"]

c = httpx.Client(timeout=25, headers={"Accept": "application/json"})


def get(path, **params):
    try:
        r = c.get(f"{BASE}{path}", params=params)
        return r.status_code, (r.json() if r.status_code == 200
                               else r.text[:200])
    except Exception as exc:                                     # noqa: BLE001
        return 0, str(exc)


for series in SERIES:
    print(f"\n=== {series} ===")
    for status in STATUSES:
        st, d = get("/markets", series_ticker=series, status=status, limit=200)
        if st != 200:
            print(f"  [{st:>3}] status={status}")
            continue
        rows = d.get("markets") or []
        with_result = [m for m in rows if (m.get("result") or "").strip()]
        print(f"  [200] status={status:<11} {len(rows):>4} markets, "
              f"{len(with_result):>4} with a non-empty result")

    # No status filter at all: what does Kalshi return by default?
    st, d = get("/markets", series_ticker=series, limit=200)
    if st == 200:
        rows = d.get("markets") or []
        seen = {}
        for m in rows:
            seen[m.get("status")] = seen.get(m.get("status"), 0) + 1
        print(f"  [200] no status filter  {len(rows):>4} markets  "
              f"statuses seen: {seen}")

st, d = get("/markets", series_ticker="KXCS2GAME", status="settled", limit=5)
rows = (d.get("markets") or []) if st == 200 else []
if not rows:
    st, d = get("/markets", series_ticker="KXCS2GAME", limit=200)
    rows = [m for m in (d.get("markets") or [])
            if (m.get("result") or "").strip()][:5] if st == 200 else []

if rows:
    print("\n=== a real settled market, every field ===")
    print(json.dumps(rows[0], indent=2)[:2200])
    print("\n=== result values seen ===")
    print(" ", {m.get("ticker"): m.get("result") for m in rows})
else:
    print("\n  No market with a populated result was found by any route.")
    print("  If the board only launched in August and every listed match is")
    print("  still upcoming, that is the whole explanation and nothing is")
    print("  broken.")
