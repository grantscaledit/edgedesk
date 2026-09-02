"""Team statistics. The forfeit-handling rules are the substance here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edgedesk.stats.team import (
    fatigue, forfeit_rate, form, form_string, matches_in_window,
    no_show_risk, win_rate,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
A, B = 1, 2


def match(days_ago, winner, defwin=False, status="finished", a=A, b=B):
    return {"scheduled_at": NOW - timedelta(days=days_ago),
            "status": status, "winner_team_id": winner,
            "decided_by_default": defwin, "team_a_id": a, "team_b_id": b}


def test_win_rate_excludes_forfeits_from_the_denominator():
    """A walkover says nothing about how a team plays, and Kalshi resolves
    a pre-game forfeit to Fair Market Price rather than a loss."""
    rows = [match(1, A), match(2, A), match(3, B), match(4, B, defwin=True)]
    s = win_rate(rows, A, shrunk=False, now=NOW)
    assert s.raw == "2-1"
    assert s.n == 3
    assert s.value == pytest.approx(2 / 3, abs=1e-4)   # rounded to 4dp


def test_win_rate_ignores_unfinished_matches():
    rows = [match(1, A), match(-1, None, status="scheduled")]
    assert win_rate(rows, A, shrunk=False, now=NOW).n == 1


def test_win_rate_shrinks_by_default():
    rows = [match(i, B) for i in range(1, 7)]
    assert win_rate(rows, A, now=NOW).value == pytest.approx(0.3125)
    assert win_rate(rows, A, shrunk=False, now=NOW).value == 0.0


def test_win_rate_with_nothing_played_is_unavailable():
    assert win_rate([], A, now=NOW).value is None
    assert win_rate([match(1, None, status="scheduled")], A, now=NOW).value is None


def test_forfeit_rate_counts_forfeits_in_its_own_denominator():
    """Forfeits are excluded from win rate but ARE the numerator here, and
    the denominator is all completed matches including them."""
    rows = [match(1, A), match(2, A), match(3, B, defwin=True)]
    s = forfeit_rate(rows, A, shrunk=False, now=NOW)
    assert s.raw == "1-2"
    assert s.value == pytest.approx(1 / 3, abs=1e-4)


def test_forfeit_rate_prior_is_low_not_half():
    """Shrinking toward 0.5 would imply every team forfeits half its
    matches, which would make the risk signal useless."""
    rows = [match(i, A) for i in range(1, 4)]
    assert forfeit_rate(rows, A, now=NOW).value < 0.05


def test_form_is_not_shrunk():
    """Form is a small-sample figure by design; shrinking it erases the
    only thing it measures."""
    rows = [match(i, A) for i in range(1, 6)]
    s = form(rows, A, now=NOW)
    assert s.value == 1.0
    assert s.shrunk is False


def test_form_takes_the_most_recent_matches():
    rows = [match(1, A), match(2, A), match(30, B), match(40, B), match(50, B)]
    assert form(rows, A, last=2, now=NOW).raw == "2-0"


def test_form_string_marks_forfeits():
    rows = [match(1, A), match(2, B), match(3, A, defwin=True)]
    assert form_string(rows, A) == "WL-"


def test_matches_in_window_separates_past_from_future():
    rows = [match(1, A), match(5, A), match(-1, None, status="scheduled")]
    assert matches_in_window(rows, 2.0, NOW) == 1
    assert matches_in_window(rows, 2.0, NOW, future=True) == 1


def test_fatigue_reports_a_count_not_a_rate():
    rows = [match(0.5, A), match(1.5, A), match(10, A)]
    s = fatigue(rows, now=NOW)
    assert s.value == 2.0
    assert "48h" in s.raw


def test_no_show_risk_is_clamped():
    rows = [match(i, A, defwin=True) for i in range(1, 20)]
    s = no_show_risk(rows, A, concurrent_tournaments=5, now=NOW)
    assert 0.0 <= s.value <= 0.40


def test_no_show_risk_declares_its_missing_input():
    """A risk score that hides which of its inputs are missing is worse
    than no score."""
    rows = [match(1, A), match(2, A)]
    s = no_show_risk(rows, A, now=NOW)
    assert "roster churn" in s.note


def test_no_show_risk_rises_with_concurrent_tournaments():
    rows = [match(1, A), match(2, A)]
    one = no_show_risk(rows, A, concurrent_tournaments=1, now=NOW)
    three = no_show_risk(rows, A, concurrent_tournaments=3, now=NOW)
    assert three.value > one.value


def test_no_show_risk_unavailable_without_history():
    assert no_show_risk([], A, now=NOW).value is None


def test_measured_pool_mean_overrides_the_module_constant():
    """The constant is a figure from an early look at the board. Shrinking
    toward a stale prior compares a team against a population that no
    longer exists, so the caller passes the measured rate when it has it."""
    rows = [match(i, A) for i in range(1, 4)]
    default = forfeit_rate(rows, A, now=NOW)
    measured = forfeit_rate(rows, A, pool_mean=0.20, now=NOW)
    assert measured.value > default.value
    assert measured.raw == default.raw          # same evidence, different prior


def test_pool_mean_is_ignored_when_not_shrinking():
    rows = [match(1, A, defwin=True), match(2, A)]
    assert forfeit_rate(rows, A, shrunk=False, pool_mean=0.9,
                        now=NOW).value == pytest.approx(0.5)


def test_no_show_risk_passes_the_pool_mean_through():
    rows = [match(i, A) for i in range(1, 4)]
    low = no_show_risk(rows, A, pool_mean=0.01, now=NOW)
    high = no_show_risk(rows, A, pool_mean=0.30, now=NOW)
    assert high.value > low.value
