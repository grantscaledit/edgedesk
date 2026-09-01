"""Kalshi public market-data client.

No authentication required for any endpoint used here — verified against the
live API. Credentials only become necessary if a later phase reads portfolio
or fills.

Ticker grammar
--------------
    KXCS2GAME-{YY}{MON}{DD}{HHMM}{ABBR_A}{ABBR_B}-{WINNER_ABBR}
    KXCS2MAP -{YY}{MON}{DD}{HHMM}{ABBR_A}{ABBR_B}-{MAP_NO}-{WINNER_ABBR}

The team abbreviations are concatenated with NO delimiter and are variable
length (BSTAUND = BSTA+UND, but EXMANAMAI = EXMANA+MAI). There is no reliable
way to split the event ticker. Recover the abbreviations from the child market
tickers instead — each ends in one team's abbreviation, and each market title
carries that team's full name. The event ticker is an identifier, never a
data source.

Start time is derived as close_time - 48h, which is more reliable than parsing
the ticker's Eastern-time HHMM field.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = ("KXCS2GAME", "KXCS2MAP")
CLOSE_OFFSET = timedelta(hours=48)


class Kalshi:
    def __init__(self, timeout: float = 20.0, retries: int = 5):
        self._c = httpx.Client(
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "edgedesk/0.1"},
        )
        self.retries = retries

    def close(self):
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- http

    def _get(self, path: str, **params) -> dict:
        url = f"{BASE}{path}"
        delay = 1.0
        for attempt in range(self.retries):
            r = self._c.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
        raise RuntimeError(f"Kalshi GET failed after {self.retries} attempts: {url}")

    def _paged(self, path: str, key: str, **params):
        """Yield every item across cursor pages."""
        cursor = None
        while True:
            p = dict(params)
            if cursor:
                p["cursor"] = cursor
            data = self._get(path, **p)
            items = data.get(key) or []
            for it in items:
                yield it
            cursor = data.get("cursor") or ""
            if not cursor or not items:
                return

    # ---------------------------------------------------------------- api

    def markets(self, series_ticker: str, status: str = "open", limit: int = 200):
        yield from self._paged(
            "/markets", "markets",
            series_ticker=series_ticker, status=status, limit=limit,
        )

    def orderbook(self, ticker: str, depth: int = 30) -> dict:
        return self._get(f"/markets/{ticker}/orderbook", depth=depth)

    def series(self, series_ticker: str) -> dict:
        return self._get(f"/series/{series_ticker}")


# ---------------------------------------------------------------- helpers


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def to_cents(value) -> int | None:
    """Normalise Kalshi prices to integer cents.

    The API mixes representations: integers are already cents (63), floats and
    decimal strings are dollars (0.63, "0.6300"). Detect by TYPE, not by
    magnitude -- a magnitude test wrongly turns 1 cent into 100 cents, and
    cannot tell 1 (one cent) from 1.0 (one dollar).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value                      # already cents
    if isinstance(value, float):
        return int(round(value * 100))    # dollars
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(round(float(text) * 100)) if "." in text else int(text)
    except ValueError:
        return None


def derive_scheduled_at(close_time: datetime | None) -> datetime | None:
    return close_time - CLOSE_OFFSET if close_time else None


def pick(m: dict, *names):
    """First present, non-empty value among candidate key spellings.

    Kalshi's REST fields are suffixed: yes_bid_dollars ("0.1300"),
    volume_fp ("0.00"). Bare names (yes_bid, volume) appear in some
    responses. Try the documented spelling first, then fall back.
    """
    for n in names:
        if n in m and m[n] not in (None, ""):
            return m[n]
    return None


def to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_market(m: dict) -> dict:
    """Flatten one Kalshi market into the kalshi_markets + price row shape."""
    ticker = m["ticker"]
    event_ticker = m.get("event_ticker") or ticker.rsplit("-", 1)[0]
    parts = ticker.split("-")
    team_abbr = parts[-1] if len(parts) >= 2 else None

    map_index = None
    if ticker.startswith("KXCS2MAP") and len(parts) >= 4 and parts[-2].isdigit():
        map_index = int(parts[-2])

    title = m.get("title") or ""
    team_name = (m.get("yes_sub_title")
                 or title.split(" wins")[0].strip()
                 or None)

    close_time = parse_ts(m.get("close_time"))

    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "series_ticker": ticker.split("-")[0],
        "team_abbr": team_abbr,
        "team_name": team_name,
        "map_index": map_index,
        "title": title,
        "status": m.get("status") or "unknown",
        "result": (m.get("result") or None) or None,
        "close_time": close_time,
        "scheduled_at": derive_scheduled_at(close_time),
        "rules_primary": m.get("rules_primary"),
        # ---- price snapshot ----
        "yes_bid":       to_cents(pick(m, "yes_bid_dollars", "yes_bid")),
        "yes_ask":       to_cents(pick(m, "yes_ask_dollars", "yes_ask")),
        "no_bid":        to_cents(pick(m, "no_bid_dollars", "no_bid")),
        "no_ask":        to_cents(pick(m, "no_ask_dollars", "no_ask")),
        "last_price":    to_cents(pick(m, "last_price_dollars", "last_price")),
        "yes_bid_size":  to_float(pick(m, "yes_bid_size_fp", "yes_bid_size")),
        "yes_ask_size":  to_float(pick(m, "yes_ask_size_fp", "yes_ask_size")),
        "liquidity":     to_cents(pick(m, "liquidity_dollars", "liquidity")),
        "volume":        to_float(pick(m, "volume_fp", "volume")),
        "volume_24h":    to_float(pick(m, "volume_24h_fp", "volume_24h")),
        "open_interest": to_float(pick(m, "open_interest_fp", "open_interest")),
    }


def parse_orderbook(ob: dict) -> tuple[list, list]:
    """Return (yes_levels, no_levels) as [[price_cents, size], ...].

    The API returns either orderbook_fp (dollar strings) or orderbook
    (integer cents) depending on the response shape.
    """
    book = ob.get("orderbook_fp") or ob.get("orderbook") or {}
    def norm(levels):
        out = []
        for lvl in levels or []:
            try:
                price, size = lvl[0], lvl[1]
                out.append([to_cents(price), float(size)])
            except (IndexError, TypeError, ValueError):
                continue
        return out
    yes = norm(book.get("yes_dollars") or book.get("yes"))
    no = norm(book.get("no_dollars") or book.get("no"))
    return yes, no
