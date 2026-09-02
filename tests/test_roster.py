from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edgedesk.stats.roster import (
    CHURN_WEIGHT, churn_term, continuity, describe, lineup, roster_changes,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def p(nick, pid):
    return {"player_id": pid, "nickname": nick, "captured_at": NOW - timedelta(days=2)}


def test_lineup_is_sorted_by_nickname():
    out = lineup([p("zywoo", 3), p("apEX", 1), p("Magisk", 2)])
    assert [x["nickname"] for x in out] == ["apEX", "Magisk", "zywoo"]


def test_roster_changes_counts_only_the_window():
    times = [NOW - timedelta(days=5), NOW - timedelta(days=20),
             NOW - timedelta(days=60)]
    assert roster_changes(times, 30, NOW) == 2


def test_roster_changes_ignores_future_and_missing():
    times = [NOW + timedelta(days=2), None, NOW - timedelta(days=1)]
    assert roster_changes(times, 30, NOW) == 1


def test_churn_term_matches_the_spec_coefficient():
    times = [NOW - timedelta(days=3), NOW - timedelta(days=9)]
    assert churn_term(times, 30, NOW) == pytest.approx(2 * CHURN_WEIGHT)


def test_continuity_is_unavailable_without_participation_data():
    """bo3 publishes no per-map player data, so this must read as a named
    gap rather than as zero."""
    s = continuity([], [1, 2, 3], {}, NOW)
    assert s.value is None
    assert "needs HLTV" in s.note


def test_continuity_computes_when_appearances_exist():
    s = continuity([], [1, 2], {1: 10, 2: 10, 3: 5}, NOW)
    assert s.value == pytest.approx(0.8)
    assert "20/25" in s.raw


def test_describe_flags_an_incomplete_lineup():
    d = describe([p("a", 1), p("b", 2)], [], NOW)
    assert d["size"] == 2 and d["complete"] is False


def test_describe_reports_a_full_five_and_its_age():
    rows = [p(f"pl{i}", i) for i in range(5)]
    d = describe(rows, [NOW - timedelta(days=4)], NOW)
    assert d["complete"] is True
    assert d["changes_30d"] == 1
    assert d["staleness_days"] == pytest.approx(2.0, abs=0.1)


def test_describe_with_no_roster_at_all():
    d = describe([], [], NOW)
    assert d["size"] == 0 and d["captured_at"] is None
