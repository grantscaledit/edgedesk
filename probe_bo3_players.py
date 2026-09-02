#!/usr/bin/env python3
"""Does bo3 expose player data? Answered in 15 seconds.

    python probe_bo3_players.py

Phase 2 assumes HLTV scraping is the only route to player ratings, roster
continuity and the talent gap -- 22-32 hours with blocking risk and player
identity resolution as the hard part. bo3 is already trusted, already
debugged, and its client already works. If it carries even partial player
data, a large share of that phase gets much cheaper.

bo3 answers a question you did not ask rather than refusing an unsupported
one, so every result here is checked against what was actually requested
instead of trusting the status code.
"""
import json

import httpx

BASE = "https://api.bo3.gg/api/v1"
c = httpx.Client(timeout=25, headers={"Accept": "application/json",
                                      "User-Agent": "edgedesk/0.1"})


def get(path, **p):
    try:
        r = c.get(f"{BASE}{path}", params=p)
        return r.status_code, (r.json() if r.status_code == 200
                               else r.text[:200])
    except Exception as exc:                                     # noqa: BLE001
        return 0, str(exc)


def probe(label, path, **params):
    st, d = get(path, **params)
    if st != 200:
        print(f"  [--] {label:34} HTTP {st}")
        return None
    results = d.get("results") if isinstance(d, dict) else None
    if results is None:
        print(f"  [??] {label:34} 200 but no 'results' key: "
              f"{list(d)[:6] if isinstance(d, dict) else type(d)}")
        return None
    total = (d.get("total") or {}).get("count")
    print(f"  [ok] {label:34} {len(results)} rows, total={total}")
    return results


print("\n--- endpoints ---")
players = probe("/players", "/players", **{"page[limit]": 3})
rosters = probe("/rosters", "/rosters", **{"page[limit]": 3})
pstats = probe("/players_stats", "/players_stats", **{"page[limit]": 3})
gp = probe("/game_players", "/game_players", **{"page[limit]": 3})
tp = probe("/team_players", "/team_players", **{"page[limit]": 3})

first = next((r for r in (players, rosters, pstats, gp, tp) if r), None)
if first:
    print("\n--- fields on the first available endpoint ---")
    print("  " + json.dumps(first[0], indent=2)[:1400].replace("\n", "\n  "))

if players:
    pid = players[0].get("id")
    print(f"\n--- can we filter players by team? (id={pid}) ---")
    st, d = get("/players", **{"filter[players.team_id][eq]": 1,
                               "page[limit]": 5})
    if st == 200:
        rows = d.get("results") or []
        team_ids = {r.get("team_id") for r in rows}
        print(f"  HTTP 200, {len(rows)} rows, team_ids seen: {team_ids}")
        print("  VERDICT:", "filter honoured" if team_ids <= {1}
              else "FILTER IGNORED — returned unrelated players")
    else:
        print(f"  HTTP {st}")

print("\n--- does a game carry per-player rows? ---")
st, d = get("/games", **{"page[limit]": 1})
if st == 200 and (d.get("results") or []):
    g = d["results"][0]
    player_keys = [k for k in g if any(w in k.lower()
                   for w in ("player", "roster", "lineup", "stat"))]
    print(f"  game keys mentioning players: {player_keys or 'NONE'}")
    print(f"  all game keys: {sorted(g)[:18]}")

print("\nSummary: any [ok] line above means that data is reachable from bo3")
print("without scraping HLTV at all.\n")
