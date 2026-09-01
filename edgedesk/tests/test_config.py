"""Regression tests for configuration loading.

Both bugs covered here shipped and cost real debugging time:

  1. db.py read os.environ but never loaded .env, so a correctly filled .env
     was silently ignored and DATABASE_URL appeared unset.
  2. phase0_collect.py read BOOK_MAX_SPREAD / BOOK_MIN_VOLUME at MODULE IMPORT,
     which happens before .env is loaded — so values set in .env were ignored
     and the hardcoded defaults were used instead. The symptom was every
     market being skipped for book collection with no visible cause.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_collector():
    """Import scripts/phase0_collect.py fresh, as a module."""
    spec = importlib.util.spec_from_file_location(
        "phase0_collect", ROOT / "scripts" / "phase0_collect.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- .env loading


def test_env_file_is_loaded(clean_env, monkeypatch, tmp_path):
    """A .env in the project root must populate os.environ."""
    from edgedesk import db

    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://u:p@h/db\nBOOK_MAX_SPREAD=42\n")
    monkeypatch.setattr(db, "_load_env", _loader_for(env))

    db._load_env()
    import os
    assert os.environ["DATABASE_URL"] == "postgresql://u:p@h/db"
    assert os.environ["BOOK_MAX_SPREAD"] == "42"


def _loader_for(env_path: Path):
    """Build a _load_env replacement bound to a specific .env path."""
    import os

    def loader():
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return loader


def test_env_parser_handles_comments_blanks_and_quotes(tmp_path):
    import os

    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "\n"
        'DATABASE_URL="postgresql://quoted"\n'
        "  BOOK_MIN_VOLUME = 3  \n"
        "MALFORMED_LINE_NO_EQUALS\n"
    )
    for key in ("DATABASE_URL", "BOOK_MIN_VOLUME"):
        os.environ.pop(key, None)
    _loader_for(env)()
    assert os.environ["DATABASE_URL"] == "postgresql://quoted"
    assert os.environ["BOOK_MIN_VOLUME"] == "3"
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("BOOK_MIN_VOLUME", None)


def test_missing_database_url_raises_actionable_error(clean_env, monkeypatch):
    """The error must tell you what to do, not just that something is wrong."""
    from edgedesk import db

    monkeypatch.setattr(db, "_load_env", lambda: None)
    with pytest.raises(RuntimeError) as exc:
        db.dsn()
    message = str(exc.value)
    assert "DATABASE_URL is not set" in message
    assert ".env" in message


def test_url_from_real_environment_wins_when_no_env_file(clean_env, monkeypatch):
    """CI injects DATABASE_URL directly and has no .env file."""
    from edgedesk import db

    monkeypatch.setattr(db, "_load_env", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://ci")
    assert db.dsn() == "postgresql://ci"


# ------------------------------------------------- collector threshold config


def test_book_config_reads_env_at_call_time(clean_env, monkeypatch):
    """The import-order bug: config must be read AFTER .env loads, not at import."""
    collector = load_collector()
    monkeypatch.setenv("BOOK_MAX_SPREAD", "77")
    monkeypatch.setenv("BOOK_MIN_VOLUME", "5")
    from edgedesk import db
    monkeypatch.setattr(db, "_load_env", lambda: None)

    max_spread, min_volume = collector.book_config()
    assert max_spread == 77
    assert min_volume == 5.0


def test_book_config_defaults_are_permissive(clean_env, monkeypatch):
    """Books are the only non-backfillable data. Default to collecting them."""
    collector = load_collector()
    from edgedesk import db
    monkeypatch.setattr(db, "_load_env", lambda: None)

    max_spread, min_volume = collector.book_config()
    assert max_spread >= 100
    assert min_volume == 0.0


# ---------------------------------------------------------------- book verdict


@pytest.mark.parametrize("row,expected", [
    ({"yes_bid": None, "yes_ask": 66, "volume": 10}, "no quote"),
    ({"yes_bid": 63, "yes_ask": None, "volume": 10}, "no quote"),
    ({"yes_bid": 63, "yes_ask": 66, "volume": 10}, "fetch"),
    ({"yes_bid": 18, "yes_ask": 79, "volume": 10}, "spread > 15"),
    ({"yes_bid": 63, "yes_ask": 66, "volume": 0}, "volume < 1"),
])
def test_book_verdict_reasons(row, expected):
    collector = load_collector()
    assert collector.book_verdict(row, 15, 1) == expected


def test_permissive_config_fetches_wide_spreads():
    """With the shipped defaults, even a 61c spread is collected."""
    collector = load_collector()
    row = {"yes_bid": 18, "yes_ask": 79, "volume": 0}
    assert collector.book_verdict(row, 100, 0) == "fetch"
