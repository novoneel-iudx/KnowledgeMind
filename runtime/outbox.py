"""
runtime/outbox.py
-----------------
FR-2.4 — Nudge outbox.

A dismissable store of proactive nudges produced by the runner. Distinct from
``kg.connector_store.preemptive_nudges`` (a Hermes connector history log with no
dismiss): this is the user-facing outbox, surfaced by ``GET /api/nudges``
and cleared item-by-item via ``POST /api/nudges/{id}/dismiss``.

Each nudge row:
    id          int   — primary key, used by the dismiss endpoint
    text        str   — the nudge body shown to the user
    skill       str   — which skill produced it
    job         str   — which scheduled job fired it (optional)
    ts          float — generation time (epoch seconds; injectable for tests)
    dismissed   bool  — user dismissed it
    suppressed  bool  — generated during quiet hours: queued, not delivered

The table lives in the KG database (``cfg.db_path``) as an additive table, so it
is created lazily and wiped together with the demo DB on each launch. Tests pass
an explicit ``db_path`` for isolation.
"""

from __future__ import annotations

import datetime
import time
from typing import Optional

from config.store import get_config
from kg.schema import get_db_connection

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS nudges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT    NOT NULL,
    skill       TEXT    NOT NULL DEFAULT '',
    job         TEXT    NOT NULL DEFAULT '',
    ts          REAL    NOT NULL,
    dismissed   INTEGER NOT NULL DEFAULT 0,
    suppressed  INTEGER NOT NULL DEFAULT 0
);
"""


def _connect(db_path: Optional[str] = None):
    conn = get_db_connection(db_path or get_config().db_path)
    conn.execute(_CREATE_SQL)
    conn.commit()
    return conn


def _row_to_dict(row) -> dict:
    ts = float(row["ts"])
    return {
        "id": row["id"],
        "text": row["text"],
        "skill": row["skill"],
        "job": row["job"],
        "ts": ts,
        "iso": datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
        "dismissed": bool(row["dismissed"]),
        "suppressed": bool(row["suppressed"]),
    }


def add_nudge(
    text: str,
    skill: str = "",
    *,
    job: str = "",
    suppressed: bool = False,
    ts: Optional[float] = None,
    db_path: Optional[str] = None,
) -> int:
    """Persist a nudge. Returns its new id. `ts` is injectable for offline tests."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO nudges (text, skill, job, ts, dismissed, suppressed) "
            "VALUES (?,?,?,?,0,?)",
            (text, skill, job, float(ts if ts is not None else time.time()), int(suppressed)),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_nudges(
    *,
    include_dismissed: bool = False,
    include_suppressed: bool = True,
    limit: int = 100,
    db_path: Optional[str] = None,
) -> list[dict]:
    """Return nudges newest-first. Dismissed are hidden unless asked for."""
    conn = _connect(db_path)
    try:
        clauses: list[str] = []
        if not include_dismissed:
            clauses.append("dismissed = 0")
        if not include_suppressed:
            clauses.append("suppressed = 0")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM nudges{where} ORDER BY ts DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_nudge(nudge_id: int, *, db_path: Optional[str] = None) -> Optional[dict]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM nudges WHERE id = ?", (int(nudge_id),)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def dismiss_nudge(nudge_id: int, *, db_path: Optional[str] = None) -> bool:
    """Mark a nudge dismissed. Returns True if a row was updated."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE nudges SET dismissed = 1 WHERE id = ? AND dismissed = 0",
            (int(nudge_id),),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def count_active(*, db_path: Optional[str] = None) -> int:
    """Number of delivered, not-yet-dismissed nudges (the badge count)."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM nudges WHERE dismissed = 0 AND suppressed = 0"
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()


def clear_nudges(*, db_path: Optional[str] = None) -> None:
    """Delete all nudges (used by the per-launch demo reset and by tests)."""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM nudges")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "outbox.db")
        a = add_nudge("You have 3 overdue tasks.", "tasks_skill", job="evening_tasks", db_path=db)
        b = add_nudge("Quiet-hours nudge (queued).", "mood_skill", suppressed=True, db_path=db)

        active = list_nudges(db_path=db)
        assert len(active) == 2, active
        assert count_active(db_path=db) == 1, "suppressed nudge must not count as active"

        assert dismiss_nudge(a, db_path=db) is True
        assert dismiss_nudge(a, db_path=db) is False, "double-dismiss should be a no-op"
        assert dismiss_nudge(99999, db_path=db) is False, "unknown id should be False"

        remaining = list_nudges(db_path=db)
        assert [n["id"] for n in remaining] == [b], remaining
        assert len(list_nudges(include_dismissed=True, db_path=db)) == 2

        clear_nudges(db_path=db)
        assert list_nudges(include_dismissed=True, db_path=db) == []

    print("runtime/outbox.py smoke tests passed.")
