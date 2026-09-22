"""SQLite connection + schema bootstrap for the Job Search Copilot."""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "copilot.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_MIGRATIONS = [
    "ALTER TABLE applications ADD COLUMN similarity_score INTEGER",
    "ALTER TABLE applications ADD COLUMN tailored_resume TEXT",
    "ALTER TABLE applications DROP COLUMN cover_letter_path",
]

def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        for migration in _MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
