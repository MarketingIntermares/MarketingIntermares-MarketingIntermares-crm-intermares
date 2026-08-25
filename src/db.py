from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings


SQLITE_PATH = Path("data/crm_intermares.sqlite3")


@contextmanager
def connection():
    if settings.database_url:
        import psycopg
        conn = psycopg.connect(settings.database_url)
        try:
            yield conn
        finally:
            conn.close()
    else:
        SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(SQLITE_PATH)
        try:
            yield conn
        finally:
            conn.close()


def initialize_db() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            records_read INTEGER DEFAULT 0,
            records_written INTEGER DEFAULT 0,
            message TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            entity_key TEXT,
            payload TEXT
        )
        """,
    ]

    with connection() as conn:
        cur = conn.cursor()
        for stmt in statements:
            cur.execute(stmt)
        conn.commit()
