from __future__ import annotations

from edgedesk.health import (
    FAIL, OK, WARN, collection_gap, failed_runs, storage, unresolved_events,
    unresolved_maps, worst,
)


def test_worst_picks_the_most_severe():
    assert worst(OK, OK) == OK
    assert worst(OK, WARN) == WARN
    assert worst(WARN, FAIL, OK) == FAIL


def test_collection_gap_thresholds():
    assert collection_gap(5)[0] == OK
    assert collection_gap(40)[0] == WARN
    assert collection_gap(180)[0] == FAIL
    assert collection_gap(None)[0] == FAIL


def test_collection_failure_says_the_data_is_unrecoverable():
    """The operator needs to know this is not a retryable error -- order
    books cannot be backfilled, so the gap is permanent."""
    _, msg = collection_gap(300)
    assert "permanently" in msg


def test_storage_thresholds():
    assert storage(100 * 1024 * 1024)[0] == OK
    assert storage(int(0.80 * 512 * 1024 * 1024))[0] == WARN
    assert storage(int(0.95 * 512 * 1024 * 1024))[0] == FAIL
    assert storage(None)[0] == OK


def test_unresolved_shares():
    assert unresolved_events(1, 47)[0] == OK
    assert unresolved_events(20, 47)[0] == WARN
    assert unresolved_maps(2, 100)[0] == OK
    assert unresolved_maps(30, 100)[0] == WARN


def test_empty_totals_do_not_divide_by_zero():
    assert unresolved_events(0, 0)[0] == OK
    assert unresolved_maps(0, 0)[0] == OK


def test_absence_of_runs_is_a_failure_not_an_ok():
    """Regression from real output: an empty list read as "ok, 0 recent runs,
    none failed" while the collector had been dead for hours. A job that
    never starts writes no failure row."""
    status, msg = failed_runs([])
    assert status == FAIL
    assert "NO runs" in msg


def test_too_few_runs_warns():
    status, _ = failed_runs([{"source": "kalshi", "status": "ok"}])
    assert status == WARN


def test_failed_runs_names_the_source():
    rows = [{"source": "kalshi", "status": "error"},
            {"source": "kalshi", "status": "ok"},
            {"source": "bo3", "status": "ok"},
            {"source": "bo3", "status": "ok"},
            {"source": "bo3", "status": "ok"}]
    status, msg = failed_runs(rows)
    assert status == FAIL
    assert "kalshi" in msg and "bo3" not in msg


def test_no_failures_is_ok():
    ok_rows = [{"source": "kalshi", "status": "ok"} for _ in range(6)]
    assert failed_runs(ok_rows)[0] == OK
