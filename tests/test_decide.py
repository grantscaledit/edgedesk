"""Capturing the market benchmark at decision time.

Without a market probability a decision is unscoreable against the price,
which is the only comparison that distinguishes forecasting skill from
reading a favourite off the board. So the lookup has to survive a closed
market, a partial quote, and a stale one.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)


def load():
    spec = importlib.util.spec_from_file_location(
        "decide", ROOT / "scripts" / "decide.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["decide"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, quote):
        self.quote = quote

    def execute(self, sql, params=None):
        if "kalshi_price_snapshots" in sql:
            return FakeResult([self.quote] if self.quote else [])
        return FakeResult([])


def quote(bid, ask, minutes_old=5, last=None, status="closed"):
    return {"yes_bid": bid, "yes_ask": ask, "last_price": last,
            "captured_at": NOW - timedelta(minutes=minutes_old),
            "status": status}


def test_mid_price_is_used_not_the_ask():
    """The ask is what you would pay; the mid is the fair benchmark.
    Scoring against the side you traded would flatter your record."""
    prob, _ = load().market_prob_for(FakeConn(quote(48, 52)), "EV", 1)
    assert prob == pytest.approx(0.50)


def test_a_closed_market_still_yields_a_benchmark():
    """Regression: the first real decision logged 'not available' because
    the lookup used the live-slate view, which excludes closed markets. The
    snapshot table is append-only precisely so this stays recoverable."""
    prob, age = load().market_prob_for(
        FakeConn(quote(60, 64, minutes_old=200, status="closed")), "EV", 1)
    assert prob == pytest.approx(0.62)
    assert age > 90


def test_last_price_is_the_fallback_when_the_book_is_empty():
    prob, _ = load().market_prob_for(
        FakeConn(quote(None, None, last=71)), "EV", 1)
    assert prob == pytest.approx(0.71)


def test_no_snapshot_at_all_returns_none():
    prob, age = load().market_prob_for(FakeConn(None), "EV", 1)
    assert prob is None and age is None


def test_missing_event_ticker_returns_none():
    assert load().market_prob_for(FakeConn(quote(48, 52)), None, 1) == (None, None)


def test_quote_age_is_reported_so_staleness_is_visible():
    _, age = load().market_prob_for(FakeConn(quote(48, 52, minutes_old=30)), "EV", 1)
    assert 29 <= age <= 31


def test_known_tag_list_is_not_empty_and_has_the_documented_ones():
    tags = set(load().TAGS)
    assert {"map_pool", "forfeit_risk", "price_value", "coin_flip"} <= tags


def test_an_empty_book_is_not_a_fifty_cent_opinion():
    """Regression: after settlement the book empties to 0/100, whose mid is
    50c. Recorded as the market price that is a fabricated coin flip — and
    it normalised to an overround of exactly 100 across 1,553 events, which
    made every one of them look like a market with no opinion."""
    prob, _ = load().market_prob_for(
        FakeConn(quote(0, 100, last=88)), "EV", 1)
    assert prob == pytest.approx(0.88)      # falls back to the last trade


def test_an_empty_book_with_no_trade_yields_nothing():
    prob, _ = load().market_prob_for(FakeConn(quote(0, 100)), "EV", 1)
    assert prob is None
