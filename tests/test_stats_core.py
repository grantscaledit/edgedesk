"""Weighting, shrinkage, and the Stat contract."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edgedesk.stats.core import (
    HALF_LIFE_DAYS, Stat, age_days, decay_weight, n_eff, rate, shrink,
    staleness,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def row(days_ago):
    return {"scheduled_at": NOW - timedelta(days=days_ago)}


# ---------------------------------------------------------------- decay


def test_decay_halves_at_the_half_life():
    assert decay_weight(NOW, NOW) == pytest.approx(1.0)
    assert decay_weight(NOW - timedelta(days=HALF_LIFE_DAYS), NOW) == pytest.approx(0.5)
    assert decay_weight(NOW - timedelta(days=2 * HALF_LIFE_DAYS), NOW) == pytest.approx(0.25)


def test_future_dates_clamp_to_one():
    """A scheduled match must never outweigh a played one."""
    assert decay_weight(NOW + timedelta(days=30), NOW) == 1.0


def test_missing_or_bad_dates_weigh_nothing():
    assert decay_weight(None, NOW) == 0.0
    assert decay_weight("not a date", NOW) == 0.0
    assert age_days(None, NOW) is None


def test_naive_datetimes_are_treated_as_utc():
    naive = datetime(2026, 9, 2, 12, 0)
    assert decay_weight(naive, NOW) == pytest.approx(1.0)


# ---------------------------------------------------------------- n_eff


def test_n_eff_is_the_headline_number_for_stale_samples():
    """Twenty matches all six months old is n=20 but n_eff about 5."""
    rows = [row(180) for _ in range(20)]
    assert n_eff(rows, now=NOW) == pytest.approx(5.0, abs=0.1)


def test_n_eff_equals_n_for_fresh_rows():
    assert n_eff([row(0) for _ in range(7)], now=NOW) == pytest.approx(7.0)


def test_n_eff_of_nothing_is_zero():
    assert n_eff([], now=NOW) == 0.0


def test_staleness_is_the_most_recent_row():
    assert staleness([row(30), row(3), row(200)], now=NOW) == pytest.approx(3.0)
    assert staleness([], now=NOW) is None


# ---------------------------------------------------------------- shrink


def test_shrink_pulls_a_hopeless_record_toward_the_prior():
    """0-6 is not a 0% team. The spec's worked example."""
    assert shrink(0, 6, 0.5, k=10) == pytest.approx(0.3125)


def test_shrink_barely_moves_a_large_sample():
    assert shrink(60, 100, 0.5, k=10) == pytest.approx(0.5909, abs=1e-3)


def test_shrink_of_an_empty_sample_is_the_prior():
    assert shrink(0, 0, 0.5, k=10) == 0.5


# ---------------------------------------------------------------- Stat


def test_rate_carries_the_raw_record_alongside_the_shrunk_value():
    s = rate(0, 6, [row(1) for _ in range(6)], pool_mean=0.5, now=NOW)
    assert s.value == pytest.approx(0.3125)
    assert s.raw == "0-6"
    assert s.shrunk is True
    assert s.n == 6


def test_rate_with_no_matches_is_unavailable_not_zero():
    """A zero here would read as 'they lose everything' rather than
    'we have nothing'."""
    s = rate(0, 0, [])
    assert s.value is None
    assert "no completed matches" in s.note


def test_unavailable_renders_as_an_explicit_gap():
    assert Stat.unavailable("bo3 has no side data").render() == (
        "no data — bo3 has no side data")


def test_render_always_shows_the_evidence():
    s = rate(13, 8, [row(6) for _ in range(21)], now=NOW)
    out = s.render()
    assert "13-8" in out and "n=21" in out and "n_eff=" in out and "6d old" in out


def test_render_marks_a_shrunk_figure():
    s = rate(0, 6, [row(3) for _ in range(6)], pool_mean=0.5, now=NOW)
    assert "adj." in s.render()
    assert "0-6" in s.render()


def test_thin_samples_are_flagged():
    assert rate(1, 1, [row(0), row(0)], now=NOW).is_thin is True
    assert rate(10, 10, [row(0) for _ in range(20)], now=NOW).is_thin is False
    assert Stat.unavailable("x").is_thin is False


def test_stat_is_immutable():
    """Frozen so a rendered figure cannot be edited apart from its
    provenance somewhere downstream."""
    s = rate(1, 1, [row(0), row(0)], now=NOW)
    with pytest.raises(Exception):
        s.value = 0.99


def test_values_are_rounded_to_four_places():
    """Deliberate: 4dp is 0.01% precision, enough for anything downstream
    (a Bo3 conversion moves the result by ~1e-4) and it keeps rendered
    output from carrying fifteen meaningless digits."""
    s = rate(2, 1, [row(1) for _ in range(3)], now=NOW)
    assert s.value == 0.6667
