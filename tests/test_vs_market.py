"""Pairing settled markets with outcomes.

The overround normalisation is the subtle part: Kalshi's two sides sum to
more than 100, and comparing a forecast against an inflated price would
flatter the forecast — the exact direction of error that would convince you
of an edge you do not have.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def load():
    spec = importlib.util.spec_from_file_location(
        "vs_market", ROOT / "scripts" / "vs_market.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vs_market"] = mod
    spec.loader.exec_module(mod)
    return mod


def side(event, team_id, bid, ask, winner, a=1, b=2, defwin=False,
         last=None, lead_mins=90):
    return {"event_ticker": event, "ticker": f"{event}-{team_id}",
            "team_id": team_id, "result": "yes", "match_id": 5,
            "scheduled_at": NOW, "yes_bid": bid, "yes_ask": ask,
            "last_price": last, "winner_team_id": winner, "team_a_id": a,
            "team_b_id": b, "decided_by_default": defwin, "status": "finished",
            "captured_at": NOW - timedelta(minutes=lead_mins)}


def test_two_sides_pair_into_one_event():
    m = load()
    ev = m.build_events([side("E1", 1, 55, 59, 1), side("E1", 2, 41, 45, 1)])
    assert len(ev) == 1 and ev[0]["a_won"] is True


def test_overround_is_normalised_away():
    """Raw mids of 0.57 and 0.43 sum to 1.00; inflated ones must still
    produce a probability, not a number above 1."""
    m = load()
    ev = m.build_events([side("E1", 1, 58, 62, 1), side("E1", 2, 44, 48, 1)])[0]
    assert 0 < ev["market_a"] < 1
    assert ev["overround"] > 100
    assert ev["market_a"] == pytest.approx(0.60 / (0.60 + 0.46), abs=1e-3)


def test_a_one_sided_event_is_dropped():
    m = load()
    assert m.build_events([side("E1", 1, 55, 59, 1)]) == []


def test_forfeits_are_dropped():
    """A pre-game forfeit resolves at Fair Market Price, so it is neither a
    win nor a loss for a forecast."""
    m = load()
    rows = [side("E1", 1, 55, 59, 1, defwin=True),
            side("E1", 2, 41, 45, 1, defwin=True)]
    assert m.build_events(rows) == []


def test_events_with_no_winner_are_dropped():
    m = load()
    rows = [side("E1", 1, 55, 59, None), side("E1", 2, 41, 45, None)]
    assert m.build_events(rows) == []


def test_last_price_is_the_fallback_when_the_book_is_empty():
    m = load()
    rows = [side("E1", 1, None, None, 1, last=60),
            side("E1", 2, None, None, 1, last=40)]
    ev = m.build_events(rows)
    assert len(ev) == 1
    assert ev[0]["market_a"] == pytest.approx(0.6, abs=1e-3)


def test_events_without_any_price_are_dropped():
    m = load()
    rows = [side("E1", 1, None, None, 1), side("E1", 2, None, None, 1)]
    assert m.build_events(rows) == []


def test_the_losing_side_is_recorded_correctly():
    m = load()
    ev = m.build_events([side("E1", 1, 55, 59, 2), side("E1", 2, 41, 45, 2)])[0]
    assert ev["a_won"] is False


def test_events_come_back_in_time_order():
    m = load()
    rows = []
    for i, ticker in enumerate(["E2", "E1"]):
        for t in (1, 2):
            r = side(ticker, t, 50, 54, 1)
            r["scheduled_at"] = NOW + timedelta(days=-i)
            rows.append(r)
    ev = m.build_events(rows)
    assert ev[0]["scheduled_at"] < ev[1]["scheduled_at"]


def test_quote_lead_is_recorded():
    """How far ahead of the match the benchmark was taken. A near-zero
    median means the 'closing line' is really a last-second price, taken
    when the result may already be visible."""
    m = load()
    ev = m.build_events([side("E1", 1, 55, 59, 1, lead_mins=120),
                         side("E1", 2, 41, 45, 1, lead_mins=120)])[0]
    assert ev["quote_lead_mins"] == pytest.approx(120, abs=1)


def test_a_quote_taken_after_kickoff_reports_zero_lead():
    m = load()
    ev = m.build_events([side("E1", 1, 55, 59, 1, lead_mins=-30),
                         side("E1", 2, 41, 45, 1, lead_mins=-30)])[0]
    assert ev["quote_lead_mins"] == 0.0


def test_an_empty_book_is_still_rejected():
    """The first wrong benchmark: a settled book of 0/100 mids to 50c on
    both sides and normalises to exactly 0.5."""
    m = load()
    assert m.build_events([side("E1", 1, 0, 100, 1),
                           side("E1", 2, 0, 100, 1)]) == []


# ------------------------------------------------- what counts as a price


def test_a_nominal_wide_book_is_not_a_price():
    """Regression: 1/99 has a 98c spread and mids to 50c on both sides —
    overround exactly 100, indistinguishable from an empty book except that
    it slips past a 0/100 check. Real events showed a median overround of
    exactly 100 for this reason."""
    m = load()
    assert m.build_events([side("E1", 1, 1, 99, 1),
                           side("E1", 2, 1, 99, 1)]) == []


def test_a_tradeable_book_is_kept():
    m = load()
    ev = m.build_events([side("E1", 1, 55, 59, 1), side("E1", 2, 41, 45, 1)])
    assert len(ev) == 1


def test_the_spread_threshold_is_the_boundary():
    m = load()
    from importlib import import_module
    limit = m.MAX_BENCHMARK_SPREAD
    ok = m.build_events([side("E1", 1, 40, 40 + limit, 1),
                         side("E1", 2, 40, 40 + limit, 1)])
    too_wide = m.build_events([side("E2", 1, 40, 41 + limit, 1),
                               side("E2", 2, 40, 41 + limit, 1)])
    assert len(ok) == 1 and too_wide == []


def test_quote_lead_is_none_when_captured_at_is_absent():
    """My own diagnostic was broken: captured_at was never SELECTed, so the
    lead time silently reported 0 on every row — the number telling us
    whether the benchmark was trustworthy was itself untrustworthy."""
    m = load()
    rows = [side("E1", 1, 55, 59, 1), side("E1", 2, 41, 45, 1)]
    for r in rows:
        del r["captured_at"]
    assert m.build_events(rows)[0]["quote_lead_mins"] is None


def test_spread_is_recorded_for_reporting():
    m = load()
    ev = m.build_events([side("E1", 1, 55, 59, 1), side("E1", 2, 41, 45, 1)])[0]
    assert ev["spread_a"] == 4


def test_overround_is_ask_sum_not_mid_sum():
    """Regression: mid-sum is ~100 by construction for ANY two-sided book,
    so warning on 'overround == 100' fired on every healthy market. The
    104-186 baseline was measured on ask-sum (v_slate's definition), and
    mixing the two produced a false alarm I then read as evidence."""
    m = load()
    tight = m.build_events([side("E1", 1, 55, 57, 1), side("E1", 2, 43, 45, 1)])[0]
    assert tight["overround"] == pytest.approx(102)
    assert tight["market_a"] == pytest.approx(0.56, abs=0.01)


def test_a_wider_book_shows_a_higher_overround():
    m = load()
    wide = m.build_events([side("E1", 1, 45, 60, 1), side("E1", 2, 35, 50, 1)])[0]
    assert wide["overround"] > 105
