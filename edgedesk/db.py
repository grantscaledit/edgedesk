"""Database access. Thin wrapper over psycopg — no ORM, raw SQL by design."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

_ENV_LOADED = False


def _load_env() -> None:
    """Load .env from the project root, once.

    Uses python-dotenv when present, falls back to a minimal parser so the
    collector still works in a bare environment (e.g. CI, where the variable
    is injected directly and no .env file exists).
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"

    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass

    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def dsn() -> str:
    _load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set.\n"
            "  1. Copy .env.example to .env  (PowerShell: copy .env.example .env)\n"
            "  2. Paste your Neon connection string after DATABASE_URL=\n"
            "  3. Make sure the file is named exactly .env (not .env.txt)"
        )
    return url


@contextmanager
def connect():
    """Yield a connection with dict rows. Commits on clean exit."""
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        yield conn


def start_run(conn, source: str) -> int:
    row = conn.execute(
        "INSERT INTO collection_runs (source, status) VALUES (%s, 'running') RETURNING id",
        (source,),
    ).fetchone()
    conn.commit()
    return row["id"]


def finish_run(conn, run_id: int, status: str, rows: int = 0, error: str | None = None):
    conn.execute(
        """UPDATE collection_runs
              SET finished_at = now(), status = %s, rows_written = %s, error = %s
            WHERE id = %s""",
        (status, rows, (error or "")[:2000] or None, run_id),
    )
    conn.commit()
