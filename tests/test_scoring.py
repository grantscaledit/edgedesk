"""Forecast scoring. The comparisons matter more than the absolute numbers."""
from __future__ import annotations

import pytest

from edgedesk.stats.scoring import (
    breakeven_edge_cents, brier, calibration, maker_fee, mean_brier,
    score_rows, skill_vs_market, tag_performance, taker_fee,
)


def d(p, result, market=None, tags=None):
    return {"prob_team_a": p, "result": result, "market_prob_a": market,
            "tags": tags or []}


# ---------------------------------------------------------------- brier


def test_brier_is_zero_for_a_perfect_call():
    assert brier(1.0, 1) == 0.0
    assert brier(0.0, 0) == 0.0


def test_brier_of_a_coin_flip_is_a_quarter():
    assert brier(0.5, 1) == 0.25
    assert brier(0.5, 0) == 0.25


def test_brier_punishes_confident_and_wrong():
    assert brier(0.9, 0) == pytest.approx(0.81)
    assert brier(0.9, 0) > brier(0.6, 0)


# ---------------------------------------------------------------- fees


def test_taker_fee_peaks_at_the_middle():
    """0.07 x C x (1-C): worst at 50c, cheap at the wings. This is why a
    1-cent edge on a coin-flip market is a losing trade."""
    assert taker_fee(50) == pytest.approx(1.75, abs=0.01)
    assert taker_fee(5) < taker_fee(50)
    assert taker_fee(95) < taker_fee(50)
    assert taker_fee(50) > taker_fee(20) > taker_fee(5)


def test_maker_fee_is_a_quarter_of_taker():
    assert maker_fee(50) == pytest.approx(taker_fee(50) * 0.25, abs=1e-4)


def test_breakeven_edge_matches_the_fee():
    assert breakeven_edge_cents(48) == taker_fee(48)


# ---------------------------------------------------------------- scoring


def test_score_rows_skips_fair_market_price_resolutions():
    """A pre-game forfeit resolves at market price, so it is not a forecast
    that came true or false -- scoring it would score a non-event."""
    rows = score_rows([d(0.7, "team_a"), d(0.7, "fmp"), d(0.6, "void")])
    assert len(rows) == 1


def test_score_rows_computes_both_briers_and_the_edge():
    row = score_rows([d(0.7, "team_a", market=0.5)])[0]
    assert row["brier"] == pytest.approx(0.09)
    assert row["market_brier"] == pytest.approx(0.25)
    assert row["brier_edge"] == pytest.approx(0.16)


def test_edge_is_none_without_a_market_price():
    row = score_rows([d(0.7, "team_a")])[0]
    assert row["market_brier"] is None and row["brier_edge"] is None


def test_losing_side_outcome_is_inverted_correctly():
    row = score_rows([d(0.7, "team_b", market=0.5)])[0]
    assert row["brier"] == pytest.approx(0.49)


def test_mean_brier_of_nothing_is_unavailable_not_zero():
    s = mean_brier([])
    assert s.value is None and "no scored decisions" in s.note


# ---------------------------------------------------------- vs market


def test_skill_vs_market_is_positive_when_you_beat_the_price():
    rows = score_rows([d(0.8, "team_a", market=0.5) for _ in range(5)])
    assert skill_vs_market(rows).value > 0


def test_skill_vs_market_is_negative_when_the_price_was_better():
    rows = score_rows([d(0.2, "team_a", market=0.5) for _ in range(5)])
    assert skill_vs_market(rows).value < 0


def test_small_samples_are_labelled_as_bookkeeping():
    """The number that would most tempt over-reading gets the loudest
    caveat: a handful of Brier differences is dominated by which way one
    coin flip landed."""
    rows = score_rows([d(0.8, "team_a", market=0.5) for _ in range(4)])
    assert "too few to distinguish skill from luck" in skill_vs_market(rows).note


def test_large_samples_drop_the_caveat():
    rows = score_rows([d(0.8, "team_a", market=0.5) for _ in range(40)])
    assert skill_vs_market(rows).note is None


# ---------------------------------------------------------- calibration


def test_calibration_buckets_forecasts_against_reality():
    rows = ([d(0.9, "team_a") for _ in range(8)]
            + [d(0.9, "team_b") for _ in range(2)])
    top = [b for b in calibration(rows, bins=5) if b["n"]][0]
    assert top["n"] == 10
    assert top["mean_forecast"] == pytest.approx(0.9)
    assert top["actual"] == pytest.approx(0.8)
    assert top["gap"] == pytest.approx(-0.1)


def test_calibration_includes_a_forecast_of_exactly_one():
    """An edge case that silently drops a row from the top bucket."""
    assert sum(b["n"] for b in calibration([d(1.0, "team_a")], bins=5)) == 1


def test_empty_buckets_are_reported_not_omitted():
    out = calibration([d(0.9, "team_a")], bins=5)
    assert len(out) == 5
    assert sum(1 for b in out if b["n"] == 0) == 4


# ---------------------------------------------------------- tags


def test_tag_performance_groups_by_reason():
    rows = score_rows([
        d(0.8, "team_a", market=0.5, tags=["map_pool", "form"]),
        d(0.8, "team_a", market=0.5, tags=["map_pool"]),
        d(0.2, "team_a", market=0.5, tags=["coin_flip"]),
    ])
    out = tag_performance(rows)
    assert out[0]["tag"] == "map_pool" and out[0]["n"] == 2
    assert {t["tag"] for t in out} == {"map_pool", "form", "coin_flip"}
    assert next(t for t in out if t["tag"] == "coin_flip")["mean_edge"] < 0
