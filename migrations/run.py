#!/usr/bin/env python3
"""Minimal migration runner. Applies numbered .sql files once, in order."""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edgedesk import db  # noqa: E402

DIR = Path(__file__).resolve().parent

DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  filename   text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
"""

def main():
    files = sorted(p for p in DIR.glob("*.sql"))
    with db.connect() as conn:
        conn.execute(DDL)
        conn.commit()
        done = {r["filename"] for r in
                conn.execute("SELECT filename FROM schema_migrations").fetchall()}
        for path in files:
            if path.name in done:
                print(f"skip  {path.name}")
                continue
            print(f"apply {path.name}")
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)",
                         (path.name,))
            conn.commit()
    print("migrations up to date")

if __name__ == "__main__":
    main()
