#!/usr/bin/env python3
"""Interactive explorer for the bo3.gg API. Read-only.

The API is undocumented, so everything we know about it was found by
probing. This is the tool for that, instead of a new throwaway script each
time.

    # which endpoints exist?
    python scripts/explore_bo3.py --discover

    # what fields does one return, and how often are they populated?
    python scripts/explore_bo3.py --endpoint /players --fields

    # show whole records
    python scripts/explore_bo3.py --endpoint /players --raw --limit 2

    # IS a filter actually honoured? (bo3 silently ignores unsupported ones)
    python scripts/explore_bo3.py --endpoint /players \
        --filter players.team_id --op eq --value 648

    # keep the JSON to inspect or send on
    python scripts/explore_bo3.py --endpoint /matches --save matches.json

THE ONE RULE FOR THIS API: it answers a question you did not ask rather than
refusing an unsupported one. A filter it does not support returns HTTP 200
and the whole table. So --filter always checks the rows it got back against
what was requested, and never trusts the status code.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

import httpx

BASE = "https://api.bo3.gg/api/v1"

# Known-good, plus plausible names worth testing. Cheap: one request each.
CANDIDATES = [
    "/matches", "/teams", "/games", "/tournaments", "/players",
    "/events", "/leagues", "/series", "/seasons", "/stages", "/brackets",
    "/maps", "/rounds", "/round_stats", "/economy",
    "/vetoes", "/veto", "/map_picks", "/picks", "/bans",
    "/rosters", "/lineups", "/participants", "/transfers",
    "/players_stats", "/player_stats", "/game_players", "/match_players",
    "/team_players", "/team_stats", "/teams_stats", "/game_stats",
    "/standings", "/rankings", "/ratings",
    "/countries", "/disciplines", "/organizations", "/streams",
    "/news", "/articles", "/odds", "/predictions",
]


def client():
    return httpx.Client(timeout=25, headers={
        "Accept": "application/json", "User-Agent": "edgedesk/0.1"})


def get(c, path, **params):
    try:
        r = c.get(f"{BASE}{path}", params=params)
        if r.status_code != 200:
            return r.status_code, r.text[:200]
        return 200, r.json()
    except Exception as exc:                                     # noqa: BLE001
        return 0, str(exc)


def discover(c):
    print(f"\nprobing {len(CANDIDATES)} endpoint names\n")
    found = []
    for path in CANDIDATES:
        st, d = get(c, path, **{"page[limit]": 1})
        if st != 200:
            print(f"  [{st:>3}] {path}")
            continue
        rows = d.get("results") if isinstance(d, dict) else None
        if rows is None:
            print(f"  [200] {path:24} no 'results' key "
                  f"(keys: {list(d)[:5]})")
            continue
        total = (d.get("total") or {}).get("count")
        print(f"  [200] {path:24} total={total}")
        found.append((path, total))
    print(f"\n{len(found)} usable endpoint(s)")
    if found:
        print("\ninspect one with:")
        print(f"  python scripts/explore_bo3.py --endpoint {found[0][0]} --fields")


def fields(rows):
    """Field names, inferred types, and how often each is populated.

    The populated rate is the useful column: a field that exists on every
    record and is null 95% of the time is not a field you can build on.
    """
    n = len(rows)
    keys = sorted({k for r in rows for k in r})
    print(f"\n  {n} sample rows\n")
    print(f"  {'field':<24} {'type':<10} {'populated':>10}  example")
    print("  " + "-" * 74)
    for k in keys:
        vals = [r.get(k) for r in rows]
        filled = [v for v in vals if v not in (None, "", [], {})]
        types = Counter(type(v).__name__ for v in filled) or {"—": 0}
        example = ""
        if filled:
            example = str(filled[0])
            if len(example) > 30:
                example = example[:27] + "..."
        print(f"  {k:<24} {list(types)[0]:<10} "
              f"{len(filled):>4}/{n:<5} {example}")


def test_filter(c, path, field, op, value, limit):
    print(f"\n  requesting filter[{field}][{op}]={value}\n")
    st, d = get(c, path, **{f"filter[{field}][{op}]": value,
                            "page[limit]": limit})
    if st != 200:
        print(f"  HTTP {st}: {d}")
        return
    rows = d.get("results") or []
    total = (d.get("total") or {}).get("count")
    print(f"  HTTP 200, {len(rows)} rows, total={total}")

    # The field the filter names, e.g. players.team_id -> team_id
    key = field.split(".")[-1]
    seen = [r.get(key) for r in rows]
    print(f"  '{key}' values returned: {seen}")

    wanted = {str(v).strip() for v in str(value).split(",")}
    got = {str(v) for v in seen if v is not None}
    if not rows:
        print("\n  VERDICT: no rows. Filter may be honoured with no matches, "
              "or the field name is wrong.")
    elif got <= wanted:
        print("\n  VERDICT: FILTER HONOURED — every row matches the request.")
    else:
        print(f"\n  VERDICT: FILTER IGNORED — got values outside the request "
              f"({sorted(got - wanted)[:5]}).")
        print("  bo3 returns 200 and the whole table for unsupported "
              "filters. Do not rely on this one.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--endpoint")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--fields", action="store_true")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--sort")
    ap.add_argument("--filter", dest="filter_field")
    ap.add_argument("--op", default="eq", help="eq or in (others are ignored)")
    ap.add_argument("--value")
    ap.add_argument("--save")
    a = ap.parse_args()

    with client() as c:
        if a.discover:
            discover(c)
            return
        if not a.endpoint:
            ap.error("pass --discover or --endpoint")

        if a.filter_field:
            if a.value is None:
                ap.error("--filter needs --value")
            test_filter(c, a.endpoint, a.filter_field, a.op, a.value, a.limit)
            return

        params = {"page[limit]": a.limit}
        if a.sort:
            params["sort"] = a.sort
        st, d = get(c, a.endpoint, **params)
        if st != 200:
            print(f"HTTP {st}: {d}")
            sys.exit(1)

        rows = d.get("results") or []
        print(f"\n  {a.endpoint}   total={(d.get('total') or {}).get('count')}"
              f"   envelope keys={list(d)}")
        if not rows:
            print("  no rows")
            return

        if a.save:
            with open(a.save, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
            print(f"  saved {a.save}")
        if a.raw:
            for r in rows:
                print("\n" + json.dumps(r, indent=2)[:3000])
        else:
            fields(rows)
            print("\n  add --raw to see whole records, "
                  "--save FILE to keep the JSON")


if __name__ == "__main__":
    main()
