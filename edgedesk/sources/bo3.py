"""bo3.gg public API client — the fixture spine.

bo3.gg is one of Kalshi's declared settlement sources for KXCS2GAME, which
makes it authoritative for match results as far as this project is concerned.

Verified response shapes (2026-09, against the live API)
-------------------------------------------------------
Envelope for every list endpoint:

    {"total": {"count": N, "pages": N, "offset": N, "limit": N},
     "results": [...], "links": {...}}

Match object carries `team1_id` / `team2_id` as INTEGERS ONLY — no team names.
Names require a separate /teams lookup, which is why TeamCache exists.

Game object reports scores as `winner_clan_score` / `loser_clan_score` with
`winner_clan_name` / `loser_clan_name`. Clan names are in-game tags set by
players and do NOT reliably equal the registered team name ("Diamant" vs
"Diamant Esports"). Never join on them without fuzzy matching, and always keep
the raw strings.

There are NO CT/T side splits and NO half-time scores anywhere in this API.
Round differential is computable; side splits are not. Do not spec them.

FILTER GRAMMAR WARNING
----------------------
Only [eq] and [in] are supported. Range operators are SILENTLY IGNORED —
`filter[matches.start_date][gte]=...` returns the entire 79k-row table rather
than erroring. Never rely on a date filter. Use status + sort + a client-side
cutoff with early termination instead, which is what iter_matches does.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

BASE = "https://api.bo3.gg/api/v1"
DISCIPLINE_CS2 = 1

# bo3 statuses seen in the wild
STATUS_UPCOMING = "upcoming"
STATUS_CURRENT = "current"
STATUS_FINISHED = "finished"
STATUS_DEFWIN = "defwin"        # decided by forfeit/default


class Bo3:
    def __init__(self, timeout: float = 20.0, retries: int = 5,
                 delay: float = 0.4):
        self._c = httpx.Client(
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "edgedesk/0.1"},
        )
        self.retries = retries
        self.delay = delay

    def close(self):
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- http

    def _get(self, path: str, **params) -> dict:
        url = f"{BASE}{path}"
        backoff = 1.0
        for _ in range(self.retries):
            r = self._c.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            r.raise_for_status()
        raise RuntimeError(f"bo3 GET failed after {self.retries} attempts: {url}")

    def _pages(self, path: str, page_size: int = 100, progress: bool = False,
               **params):
        """Yield result items across pages.

        PAGINATION PARAMS ARE `page[limit]` AND `page[offset]`.
        Plain `limit` / `offset` are SILENTLY IGNORED — the API returns page
        one every time while your offset counter climbs, which looks exactly
        like a hang. The self link in each response reveals the real names.

        A same-first-id guard aborts if pagination stops advancing, so a
        future parameter change surfaces as a loud error rather than an
        infinite loop.
        """
        offset = 0
        seen_first: str | None = None
        page_no = 0
        while True:
            data = self._get(path, **{
                "page[limit]": page_size,
                "page[offset]": offset,
                **params,
            })
            results = data.get("results") or []
            if not results:
                return

            first_id = str(results[0].get("id"))
            if seen_first is not None and first_id == seen_first:
                raise RuntimeError(
                    f"bo3 pagination stopped advancing at offset={offset}. "
                    "The page[limit]/page[offset] params are likely no longer "
                    "honoured. Check the self link in the response."
                )
            seen_first = first_id

            for item in results:
                yield item

            total = data.get("total") or {}
            count = total.get("count") or 0
            step = total.get("limit") or len(results)
            offset += step
            page_no += 1
            if progress:
                print(f"      page {page_no}  offset {offset}/{count}",
                      flush=True)
            if offset >= count:
                return
            time.sleep(self.delay)

    # ---------------------------------------------------------------- api

    def iter_matches(self, status: str, *, since: datetime | None = None,
                     newest_first: bool = True, page_size: int = 100,
                     progress: bool = False, max_pages: int = 500):
        """Iterate CS2 matches of one status.

        `since` applies a CLIENT-SIDE cutoff with early termination, because
        the API silently ignores date filters. Requires newest_first=True to
        terminate correctly.
        """
        sort = "-start_date" if newest_first else "start_date"
        seen_pages = 0
        for item in self._pages(
            "/matches",
            page_size=page_size,
            progress=progress,
            **{
                "filter[matches.discipline_id][eq]": DISCIPLINE_CS2,
                "filter[matches.status][eq]": status,
                "sort": sort,
            },
        ):
            if since is not None and newest_first:
                start = parse_ts(item.get("start_date"))
                if start and start < since:
                    return                      # early termination
            yield item
            seen_pages += 1
            if seen_pages > max_pages * 100:
                return

    def iter_tournaments(self, page_size: int = 100, progress: bool = False):
        """All tournaments (~3.1k). Carries `name`, `tier`, `tier_rank`."""
        yield from self._pages("/tournaments", page_size=page_size,
                               progress=progress,
                               **{"filter[tournaments.discipline_id][eq]":
                                  DISCIPLINE_CS2})

    def iter_stages(self, page_size: int = 100, progress: bool = False):
        """All stages (~10.3k). Note the field is `title`, not `name`."""
        yield from self._pages("/stages", page_size=page_size,
                               progress=progress,
                               **{"filter[stages.discipline_id][eq]":
                                  DISCIPLINE_CS2})

    def teams(self, ids: list[int]) -> list[dict]:
        """Batch team lookup. [in] is one of only two supported operators."""
        if not ids:
            return []
        out: list[dict] = []
        for chunk in _chunks(sorted(set(ids)), 50):
            data = self._get(
                "/teams",
                **{"filter[teams.id][in]": ",".join(str(i) for i in chunk),
                   "page[limit]": 100},
            )
            out.extend(data.get("results") or [])
            time.sleep(self.delay)
        return out

    def iter_players(self, page_size: int = 100, progress: bool = False):
        """Every player bo3 knows (~20k). No filtering is possible.

        `filter[players.team_id][eq]` is SILENTLY IGNORED -- probed 2026-09,
        it returned players from five unrelated teams. Same trap as the date
        filters. So the only way to get a team's roster is to pull the whole
        table and index it client-side, which at ~204 pages is roughly 90
        seconds and needs doing only when rosters are refreshed.

        bo3 has NO player performance data: /players_stats, /game_players,
        /rosters and /team_players are all 404, and game objects carry no
        player keys. Ratings require HLTV. Do not re-probe this.
        """
        yield from self._pages("/players", page_size=page_size,
                               progress=progress)

    def games(self, match_id: int) -> list[dict]:
        data = self._get(
            "/games", **{"filter[games.match_id][eq]": match_id}
        )
        return data.get("results") or []

    def games_batch(self, match_ids: list[int], chunk: int = 50,
                    progress: bool = False):
        """Games for many matches at once. Verified working 2026-09.

        `filter[games.match_id][in]` IS honoured — probed against the live
        API, which returned exactly the five requested match_ids and nothing
        else. That makes backfill ~50x cheaper than one request per match.

        MUST PAGINATE. A chunk of 50 matches averages ~130 games, and the
        page size is 100, so a single _get would silently drop the tail --
        the worst kind of bug here, because the result looks like a match
        that only played two maps rather than like an error. Going through
        _pages is what makes the chunk size independent of the page size.
        """
        for ids in _chunks(sorted(set(match_ids)), chunk):
            if progress:
                print(f"      games for {len(ids)} matches "
                      f"({ids[0]}..{ids[-1]})", flush=True)
            yield from self._pages(
                "/games",
                page_size=100,
                **{"filter[games.match_id][in]": ",".join(str(i) for i in ids)},
            )
            time.sleep(self.delay)

    def tournament(self, tournament_id: int) -> dict | None:
        data = self._get(
            "/tournaments", **{"filter[tournaments.id][eq]": tournament_id}
        )
        results = data.get("results") or []
        return results[0] if results else None


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------- helpers


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalise_map(name: str | None) -> str | None:
    """'de_ancient' -> 'Ancient'. Returns None for missing/TBA."""
    if not name:
        return None
    text = str(name).strip()
    if not text or text.lower() in ("tba", "unknown", "default"):
        return None
    for prefix in ("de_", "cs_"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    return text.capitalize()


def parse_match(m: dict) -> dict:
    """Flatten a bo3 match into the matches-row shape.

    `decided_by_default` is the forfeit flag — the highest-value risk signal
    in the whole system, and bo3 is the only source that exposes it cleanly.
    """
    status = m.get("status") or "unknown"
    return {
        "bo3_id": m.get("id"),
        "slug": m.get("slug"),
        "team1_id": m.get("team1_id"),
        "team2_id": m.get("team2_id"),
        "winner_team_id": m.get("winner_team_id"),
        "team1_score": m.get("team1_score"),
        "team2_score": m.get("team2_score"),
        "tournament_id": m.get("tournament_id"),
        "start_date": parse_ts(m.get("start_date")),
        "end_date": parse_ts(m.get("end_date")),
        "bo_type": m.get("bo_type"),
        "bo3_status": status,
        "status": map_status(status),
        "decided_by_default": status == STATUS_DEFWIN,
        "tier": m.get("tier"),
        "tier_rank": m.get("tier_rank"),
        "stage_id": m.get("stage_id"),
    }


def map_status(bo3_status: str) -> str:
    """bo3 status -> our matches.status CHECK constraint vocabulary."""
    return {
        STATUS_UPCOMING: "scheduled",
        STATUS_CURRENT: "live",
        STATUS_FINISHED: "finished",
        STATUS_DEFWIN: "finished",
    }.get(bo3_status, "scheduled")


def parse_team(t: dict) -> dict:
    return {
        "bo3_id": t.get("id"),
        "name": t.get("name"),
        "slug": t.get("slug"),
        "acronym": t.get("acronym"),
        "bo3_rank": t.get("rank"),
        "country_id": t.get("country_id"),
    }


def parse_tournament(t: dict) -> dict:
    return {
        "bo3_id": t.get("id"),
        "name": t.get("name"),
        "slug": t.get("slug"),
        "tier": t.get("tier"),
        "tier_rank": t.get("tier_rank"),
        "series_id": t.get("series_id"),
        "region_id": t.get("region_id"),
        "event_type": t.get("event_type"),
        "event_level": t.get("event_level"),
        "prize": t.get("prize"),
        "status": t.get("status"),
        "start_date": parse_ts(t.get("start_date")),
        "end_date": parse_ts(t.get("end_date")),
    }


def parse_stage(s: dict) -> dict:
    """Stages use `title`, tournaments use `name`. Do not swap them."""
    return {
        "bo3_id": s.get("id"),
        "tournament_bo3_id": s.get("tournament_id"),
        "title": s.get("title"),
        "format_type": s.get("format_type"),
    }


def parse_player(p: dict) -> dict:
    """Flatten a bo3 player. `team_id` is CURRENT membership, often null."""
    return {
        "bo3_id": p.get("id"),
        "nickname": p.get("nickname") or p.get("slug"),
        "first_name": p.get("first_name") or None,
        "last_name": p.get("last_name") or None,
        "country_id": p.get("country_id"),
        "birthday": p.get("birthday") or None,
        "role": p.get("role") or None,
        "slug": p.get("slug"),
        "team_bo3_id": p.get("team_id"),
    }


def parse_game(g: dict) -> dict:
    """Flatten a bo3 game (one map).

    Scores are winner/loser oriented, not team1/team2. Assignment to our
    team_a / team_b happens in the sync layer where team names are known.
    """
    return {
        "bo3_id": g.get("id"),
        "match_bo3_id": g.get("match_id"),
        "map_index": g.get("number"),
        "map_name": normalise_map(g.get("map_name")),
        "winner_clan_name": g.get("winner_clan_name"),
        "loser_clan_name": g.get("loser_clan_name"),
        "winner_score": g.get("winner_clan_score"),
        "loser_score": g.get("loser_clan_score"),
        "rounds_count": g.get("rounds_count"),
        "status": g.get("status"),
        "begin_at": parse_ts(g.get("begin_at")),
    }
