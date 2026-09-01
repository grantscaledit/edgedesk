"""Fixture-based parser tests.

These catch the failure mode that actually kills collectors: an upstream shape
change producing silent nulls rather than a loud exception.

The fixture in tests/fixtures/kalshi_markets.json uses the REAL Kalshi field
spellings. Those are suffixed (`yes_bid_dollars`, `volume_fp`) and the values
are strings. An earlier parser looked for the obvious bare names, found
nothing, and reported "no quote" for all 258 markets on the board.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from edgedesk.sources.kalshi import (
    derive_scheduled_at, parse_market, parse_orderbook, parse_ts, pick,
    to_cents, to_float,
)

BSTA = "KXCS2GAME-26AUG251700BSTAUND-BSTA"
UND = "KXCS2GAME-26AUG251700BSTAUND-UND"
DIA = "KXCS2GAME-26AUG251430DIAUSS-DIA"
VIT = "KXCS2MAP-26AUG311230VITFUT-2-VIT"
SETTLED = "KXCS2GAME-26AUG311200BKBSTA-BSTA"


# ---------------------------------------------------------------- primitives


def test_to_cents_detects_by_type_not_magnitude():
    """Magnitude-based detection turns 1 cent into 100 cents and cannot
    distinguish 1 (one cent) from 1.0 (one dollar)."""
    assert to_cents("0.6600") == 66      # decimal string -> dollars
    assert to_cents(0.66) == 66          # float -> dollars
    assert to_cents(66) == 66            # int -> already cents
    assert to_cents(1) == 1              # one cent
    assert to_cents(1.0) == 100          # one dollar
    assert to_cents("63") == 63          # integer string -> cents
    assert to_cents(0) == 0
    assert to_cents(None) is None
    assert to_cents("") is None
    assert to_cents("garbage") is None
    assert to_cents(True) is None        # bool is an int subclass; reject it


def test_to_float_tolerates_junk():
    assert to_float("107.0000") == 107.0
    assert to_float(12) == 12.0
    assert to_float(None) is None
    assert to_float("") is None
    assert to_float("abc") is None


def test_pick_returns_first_present_non_empty():
    row = {"a": "", "b": None, "c": "0.63", "d": "0.99"}
    assert pick(row, "a", "b", "c", "d") == "0.63"
    assert pick(row, "missing") is None


def test_scheduled_at_is_close_minus_48h():
    close = parse_ts("2026-08-27T21:00:00Z")
    assert derive_scheduled_at(close) == datetime(2026, 8, 25, 21, 0, tzinfo=timezone.utc)


def test_parse_ts_handles_z_suffix_and_junk():
    assert parse_ts("2026-08-27T21:00:00Z").tzinfo is not None
    assert parse_ts(None) is None
    assert parse_ts("not a date") is None


# ---------------------------------------------------------------- field names


def test_real_field_spellings_are_used(markets):
    """Guards the exact regression: bare names do not exist in the API."""
    raw = markets[BSTA]
    assert "yes_bid_dollars" in raw and "yes_bid" not in raw
    assert "volume_fp" in raw and "volume" not in raw
    assert "open_interest_fp" in raw and "open_interest" not in raw

    row = parse_market(raw)
    assert row["yes_bid"] is not None, "suffixed field names must be read"
    assert row["volume"] == 107.0


def test_prices_come_back_as_integer_cents(markets):
    row = parse_market(markets[BSTA])
    assert row["yes_bid"] == 63
    assert row["yes_ask"] == 66
    assert row["last_price"] == 65
    assert all(isinstance(row[k], int)
               for k in ("yes_bid", "yes_ask", "last_price"))


def test_top_of_book_sizes_are_captured(markets):
    """These make the liquidity gate's depth test possible without an
    order-book request."""
    row = parse_market(markets[BSTA])
    assert row["yes_bid_size"] == 64.0
    assert row["yes_ask_size"] == 12.0


# ---------------------------------------------------------------- markets


def test_game_market_parses(markets):
    row = parse_market(markets[BSTA])
    assert row["series_ticker"] == "KXCS2GAME"
    assert row["event_ticker"] == "KXCS2GAME-26AUG251700BSTAUND"
    assert row["team_abbr"] == "BSTA"
    assert row["team_name"] == "BESTIA Academy"
    assert row["map_index"] is None
    assert row["status"] == "active"


def test_map_market_extracts_index_and_name(markets):
    row = parse_market(markets[VIT])
    assert row["series_ticker"] == "KXCS2MAP"
    assert row["map_index"] == 2
    assert row["team_abbr"] == "VIT"
    assert row["team_name"] == "Vitality"      # not "Vitality wins map 2"


def test_team_name_prefers_sub_title(markets):
    """yes_sub_title is authoritative; the title needs string surgery."""
    assert parse_market(markets[VIT])["team_name"] == "Vitality"


def test_settled_market_keeps_result(markets):
    row = parse_market(markets[SETTLED])
    assert row["status"] == "finalized"
    assert row["result"] == "yes"


def test_empty_result_normalises_to_none(markets):
    assert parse_market(markets[UND])["result"] is None


def test_illiquid_market_still_parses(markets):
    """A 61c spread with zero volume must parse, not crash."""
    row = parse_market(markets[DIA])
    assert row["yes_bid"] == 18 and row["yes_ask"] == 79
    assert row["volume"] == 0.0


@pytest.mark.parametrize("ticker,abbr", [
    (BSTA, "BSTA"),
    (UND, "UND"),
    (DIA, "DIA"),
    (VIT, "VIT"),
])
def test_abbr_comes_from_market_not_event_ticker(markets, ticker, abbr):
    """Event tickers concatenate abbreviations with no delimiter and are not
    splittable: BSTAUND = BSTA+UND, but EXMANAMAI = EXMANA+MAI. Only the child
    market ticker's final segment is safe."""
    assert parse_market(markets[ticker])["team_abbr"] == abbr


def test_every_fixture_market_yields_a_quote(markets):
    """Blanket guard. If a future shape change nulls the quotes again, this
    fails loudly instead of the collector silently skipping every market."""
    for ticker, raw in markets.items():
        row = parse_market(raw)
        assert row["yes_bid"] is not None, f"{ticker} lost its bid"
        assert row["yes_ask"] is not None, f"{ticker} lost its ask"


# ---------------------------------------------------------------- orderbook


def test_orderbook_normalises_to_cents(kalshi_fixture):
    yes, no = parse_orderbook(kalshi_fixture["orderbook"])
    assert yes == [[61, 108.0], [62, 52.0], [63, 201.0]]
    assert no == [[30, 253.0], [33, 6.0], [34, 134.0]]


def test_orderbook_tolerates_missing_sides():
    yes, no = parse_orderbook({"orderbook_fp": {}})
    assert yes == [] and no == []


def test_orderbook_tolerates_empty_payload():
    yes, no = parse_orderbook({})
    assert yes == [] and no == []


def test_orderbook_skips_malformed_levels():
    book = {"orderbook_fp": {"yes_dollars": [["0.61", "10"], ["bad"], None],
                             "no_dollars": []}}
    yes, no = parse_orderbook(book)
    assert yes == [[61, 10.0]]
    assert no == []
