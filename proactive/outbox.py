"""
proactive/outbox.py
-------------------
SQLite-backed nudge store. Reads/writes the nudges table added to the KM DB.
"""

from __future__ import annotations

import json
import sqlite3
import time


def store_nudge(
    conn: sqlite3.Connection,
    job_name: str,
    skill: str,
    message: str,
    signals: dict,
) -> int:
    """Insert a nudge and return its row id."""
    cursor = conn.execute(
        """INSERT INTO nudges (job_name, skill, message, signals_json, generated_at, dismissed)
           VALUES (?, ?, ?, ?, ?, 0)""",
        (job_name, skill, message, json.dumps(signals), time.time()),
    )
    conn.commit()
    return cursor.lastrowid


def list_nudges(
    conn: sqlite3.Connection,
    limit: int = 20,
    undismissed_only: bool = True,
) -> list[dict]:
    """Return recent nudges as plain dicts, newest first."""
    where = "WHERE dismissed = 0" if undismissed_only else ""
    rows = conn.execute(
        f"""SELECT id, job_name, skill, message, signals_json, generated_at, dismissed
            FROM nudges {where}
            ORDER BY generated_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["signals"] = json.loads(d.pop("signals_json"))
        except Exception:
            d["signals"] = {}
        result.append(d)
    return result


def dismiss_nudge(conn: sqlite3.Connection, nudge_id: int) -> bool:
    """Mark a nudge as dismissed. Returns True if the row existed."""
    cursor = conn.execute(
        "UPDATE nudges SET dismissed = 1 WHERE id = ?", (nudge_id,)
    )
    conn.commit()
    return cursor.rowcount > 0
