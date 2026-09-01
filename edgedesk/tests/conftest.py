"""Pytest configuration.

Puts the project root on sys.path so `import edgedesk` works regardless of
where pytest is invoked from — important on Windows where `pytest` and
`python -m pytest` resolve paths differently.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def kalshi_fixture() -> dict:
    """Raw Kalshi response fixture, real field spellings."""
    return json.loads((FIXTURES / "kalshi_markets.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def markets(kalshi_fixture) -> dict:
    """Fixture markets keyed by ticker."""
    return {m["ticker"]: m for m in kalshi_fixture["markets"]}


@pytest.fixture
def clean_env(monkeypatch):
    """Isolate environment mutations and reset the .env load guard."""
    from edgedesk import db

    for key in ("DATABASE_URL", "BOOK_MAX_SPREAD", "BOOK_MIN_VOLUME",
                "DISCORD_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(db, "_ENV_LOADED", False, raising=False)
    yield
    monkeypatch.setattr(db, "_ENV_LOADED", False, raising=False)
