"""The collector's write path.

The settled sweep issued two row-by-row executes per market against a
remote database — ~24,000 sequential round trips for 12k markets, roughly
twelve minutes of silence. It looked like a hang and got interrupted.

Third occurrence in this project of the same pattern: the slow thing was
also the unlogged thing. These tests pin both halves of the fix.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "phase0_collect", ROOT / "scripts" / "phase0_collect.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase0_collect"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def executemany(self, sql, rows):
        self.log.append(("executemany", len(list(rows))))

    def execute(self, sql, params=None):
        self.log.append(("execute", 1))


class FakeConn:
    def __init__(self):
        self.log = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self.log)

    def execute(self, sql, params=None):
        self.log.append(("execute", 1))

    def commit(self):
        self.commits += 1


def rows(n):
    return [{"ticker": f"T{i}"} for i in range(n)]


def test_writes_are_batched_not_row_by_row():
    mod = load()
    conn = FakeConn()
    mod.write_markets(conn, rows(1200), chunk=500)
    singles = [c for c in conn.log if c[0] == "execute"]
    batches = [c for c in conn.log if c[0] == "executemany"]
    assert not singles, "row-by-row writes reintroduced"
    # three chunks x two statements (markets + prices)
    assert len(batches) == 6
    assert sum(n for _, n in batches) == 2400


def test_every_row_is_written_exactly_once_per_statement():
    mod = load()
    conn = FakeConn()
    assert mod.write_markets(conn, rows(1201), chunk=500) == 1201


def test_progress_is_printed_for_a_long_run():
    """Batching alone is not enough — a silent four-minute job still reads
    as a hang."""
    mod = load()
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.write_markets(FakeConn(), rows(1500), chunk=500, label="settled")
    out = buf.getvalue()
    assert "500/1500" in out and "1500/1500" in out
    assert "settled" in out


def test_a_short_run_stays_quiet():
    """Progress lines on a 130-row open sweep would just be noise."""
    mod = load()
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod.write_markets(FakeConn(), rows(130), chunk=500)
    assert buf.getvalue() == ""


def test_each_chunk_commits_so_an_interrupt_keeps_prior_work():
    """If it is interrupted again, everything already written stays."""
    mod = load()
    conn = FakeConn()
    mod.write_markets(conn, rows(1500), chunk=500)
    assert conn.commits == 3


def test_empty_input_writes_nothing():
    mod = load()
    conn = FakeConn()
    assert mod.write_markets(conn, []) == 0
    assert conn.log == []
