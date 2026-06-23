"""
kg/schema.py
------------
SQLite schema, connection helpers, and the shared graph dataclasses.

This is the lowest layer of the knowledge-graph stack: every other kg module
(graph.py, queries.py) and the memory / rag modules open their connection
through get_db_connection() here. The DDL is taken verbatim from SPEC.md 3.1
and is idempotent -- calling init_db() on an existing database is safe.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Shared dataclasses (SPEC 3.2)
# ---------------------------------------------------------------------------

@dataclass
class CommitmentNode:
    """A single commitment extracted into the knowledge graph."""
    id: int
    person_name: str
    description: str
    start_ts: float
    end_ts: Optional[float]
    source: str             # 'calendar'|'slack'|'email'|'whatsapp'|'mock'
    commitment_type: str    # 'HARD'|'SOFT'|'TENTATIVE'
    confidence: float       # 0.0-1.0
    raw_text: Optional[str]


@dataclass
class ConflictEdge:
    """A detected temporal conflict between two commitments."""
    id: int
    commitment_a: CommitmentNode
    commitment_b: CommitmentNode
    overlap_minutes: float
    alerted: bool


# ---------------------------------------------------------------------------
# Schema DDL (verbatim from SPEC 3.1)
# ---------------------------------------------------------------------------

SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS persons (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    aliases    TEXT,
    embedding  BLOB,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS commitments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id       INTEGER REFERENCES persons(id),
    description     TEXT NOT NULL,
    start_ts        REAL NOT NULL,
    end_ts          REAL,
    source          TEXT NOT NULL,
    commitment_type TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 1.0,
    raw_text        TEXT,
    channel_id      TEXT,
    external_id     TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS conflicts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    commitment_a_id INTEGER REFERENCES commitments(id),
    commitment_b_id INTEGER REFERENCES commitments(id),
    overlap_minutes REAL NOT NULL,
    detected_at     REAL NOT NULL,
    alerted         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    timestamp        REAL NOT NULL,
    tool_name        TEXT,
    routing_decision TEXT,
    token_estimate   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rag_documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    chunk_count  INTEGER NOT NULL,
    indexed_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS nudges (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name     TEXT NOT NULL,
    skill        TEXT NOT NULL,
    message      TEXT NOT NULL,
    signals_json TEXT NOT NULL,
    generated_at REAL NOT NULL,
    dismissed    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_commitments_time   ON commitments (start_ts, end_ts);
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments (status, start_ts);
CREATE INDEX IF NOT EXISTS idx_turns_session      ON turns (session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_conflicts_alert    ON conflicts (alerted, detected_at);
CREATE INDEX IF NOT EXISTS idx_nudges_time        ON nudges (generated_at DESC, dismissed);
"""

# Migration: add status column to existing databases that predate this field.
_MIGRATE_STATUS_SQL: str = """
ALTER TABLE commitments ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
"""

# SQLite busy timeout (ms) -- covers the 'SQLite locked' retry policy in SPEC 8
_BUSY_TIMEOUT_MS: int = 3000


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def init_db(path: str) -> sqlite3.Connection:
    """
    Create (or open) the database at `path`, ensure all tables and indices
    exist, and return an open connection. Idempotent.

    Args:
        path: filesystem path to the SQLite file. Parent dirs are created.
    Returns:
        An open sqlite3.Connection with Row factory and a busy timeout set.
    """
    db_file = Path(path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # check_same_thread=False: the Gradio request threads and the monitor
    # thread both touch the DB. busy_timeout serialises concurrent writers.
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    # Idempotent migration: add status column if it predates this field.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(commitments)")}
    if "status" not in cols:
        conn.executescript(_MIGRATE_STATUS_SQL)
    conn.commit()
    return conn


class _NoCloseConn:
    """
    Wrap a persistent in-memory connection so caller close() calls are no-ops.
    In-memory SQLite databases are destroyed on close, so the shared singleton
    must survive for the lifetime of the process.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def close(self) -> None:  # callers own closing; keep the singleton alive
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


_in_memory_conn: Optional[sqlite3.Connection] = None


def get_db_connection(path: str) -> sqlite3.Connection:
    """
    Return a ready-to-use connection with the schema guaranteed to exist.

    Normally a thin wrapper over init_db(). When `path` is not writable (e.g. an
    ephemeral / read-only cloud filesystem), falls back to a shared in-memory
    SQLite database -- data survives the process lifetime but is lost on restart.
    Callers own closing it (a no-op for the in-memory singleton).
    """
    global _in_memory_conn
    try:
        return init_db(path)
    except (OSError, sqlite3.OperationalError):
        if _in_memory_conn is None:
            print("[KG] WARNING: db_path not writable -- using in-memory SQLite "
                  "(data resets on restart)")
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            _in_memory_conn = conn
        return _NoCloseConn(_in_memory_conn)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "smoke.db")

        connection = init_db(db_path)
        # Idempotency: second call must not raise.
        connection.close()
        connection = get_db_connection(db_path)

        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [row["name"] for row in table_rows]
        connection.close()

        expected = {"persons", "commitments", "conflicts", "turns", "rag_documents"}
        missing = expected - set(table_names)
        assert not missing, f"Missing tables: {missing}"
        print(f"=> tables present: {', '.join(sorted(table_names))}")

        # Fallback: a path whose parent is a regular file is never writable
        # (deterministic across OSes/permissions) -> shared in-memory SQLite.
        blocker = Path(tmp) / "blocker"
        blocker.write_text("x")
        fb = get_db_connection(str(blocker / "sub" / "fallback.db"))
        fb.execute("INSERT INTO persons (name, created_at) VALUES ('x', 0)")
        assert fb.execute("SELECT COUNT(*) FROM persons").fetchone()[0] >= 1
        fb.close()  # no-op for the in-memory singleton
        assert fb.execute("SELECT 1").fetchone()[0] == 1, "in-memory conn closed early"
        print("=> unwritable path -> in-memory SQLite fallback (close() is a no-op)")

    print("All kg/schema.py smoke tests passed.")
