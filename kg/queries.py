"""
kg/queries.py
-------------
Tool-callable read queries over the knowledge graph. These back the
query_kg / find_free_slots / conflict_edges tools.

PRIVACY: query_kg returns anonymised node summaries only -- description,
start time, source channel type, commitment_type, confidence. It never
returns raw_text, so the orchestrator's KG-context injection cannot leak
verbatim personal text to the cloud planner (SPEC privacy rule 1 and 7).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import time
from typing import Any

from kg.graph import find_conflicts


# Working hours for free-slot search (SPEC 4.4).
WORK_START_HOUR: int = 8
WORK_END_HOUR: int = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hhmm(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def _summarise_commitment(row: sqlite3.Row) -> dict[str, Any]:
    """Anonymised summary safe to pass to the cloud planner (no raw_text)."""
    return {
        "description": row["description"],
        "start": _hhmm(row["start_ts"]),
        "source_type": row["source"],
        "type": row["commitment_type"],
        "confidence": round(row["confidence"], 2),
    }


# ---------------------------------------------------------------------------
# query_kg
# ---------------------------------------------------------------------------

def query_kg(conn: sqlite3.Connection, query_text: str) -> dict[str, Any]:
    """
    Natural-language lookup over commitments. Matches the query terms against
    descriptions and person names; if nothing matches, returns upcoming
    commitments. Returns anonymised node summaries.

    Returns:
        {"success": bool, "formatted": str, "nodes": list[dict]}
    """
    terms = [term for term in query_text.lower().split() if len(term) > 2]
    rows = conn.execute(
        """SELECT c.*, p.name AS person_name
           FROM commitments c LEFT JOIN persons p ON c.person_id = p.id
           ORDER BY c.start_ts ASC"""
    ).fetchall()

    def matches(row: sqlite3.Row) -> bool:
        haystack = f"{row['description']} {row['person_name'] or ''}".lower()
        return any(term in haystack for term in terms)

    matched = [row for row in rows if matches(row)] if terms else []
    # Fall back to upcoming commitments when nothing matched the query.
    selected = matched if matched else [r for r in rows if r["start_ts"] >= time.time()]
    selected = selected[:10]

    nodes = [_summarise_commitment(row) for row in selected]

    if not nodes:
        return {"success": True, "formatted": "No matching commitments found.", "nodes": []}

    lines = [
        f"- {node['description']} at {node['start']} "
        f"({node['source_type']}, {node['type']}, conf={node['confidence']})"
        for node in nodes
    ]
    return {
        "success": True,
        "formatted": "Commitments:\n" + "\n".join(lines),
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# find_free_slots
# ---------------------------------------------------------------------------

def find_free_slots(
    conn: sqlite3.Connection, date_str: str, duration_minutes: int = 60
) -> dict[str, Any]:
    """
    Find open slots of at least `duration_minutes` within working hours
    (08:00-20:00) on the given date.

    Args:
        date_str: 'YYYY-MM-DD'.
        duration_minutes: minimum slot length.
    Returns:
        {"success": bool, "formatted": str, "slots": list[dict]}
    """
    try:
        day = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return {"success": False, "error": f"Invalid date '{date_str}', expected YYYY-MM-DD."}

    day_start = dt.datetime.combine(day, dt.time(WORK_START_HOUR, 0)).timestamp()
    day_end = dt.datetime.combine(day, dt.time(WORK_END_HOUR, 0)).timestamp()

    busy = conn.execute(
        """SELECT start_ts, end_ts FROM commitments
           WHERE start_ts < ? AND COALESCE(end_ts, start_ts + 3600) > ?
           ORDER BY start_ts ASC""",
        (day_end, day_start),
    ).fetchall()

    # Walk the day, collecting gaps >= duration between busy blocks.
    duration_seconds = duration_minutes * 60.0
    cursor = day_start
    slots: list[dict[str, str]] = []
    for block in busy:
        block_start = max(block["start_ts"], day_start)
        block_end = min(block["end_ts"] or (block["start_ts"] + 3600), day_end)
        if block_start - cursor >= duration_seconds:
            slots.append({"start": _hhmm(cursor), "end": _hhmm(block_start)})
        cursor = max(cursor, block_end)
    if day_end - cursor >= duration_seconds:
        slots.append({"start": _hhmm(cursor), "end": _hhmm(day_end)})

    if not slots:
        return {
            "success": True,
            "formatted": f"No free {duration_minutes}-minute slot on {date_str}.",
            "slots": [],
        }

    lines = [f"- {slot['start']} to {slot['end']}" for slot in slots]
    return {
        "success": True,
        "formatted": f"Free slots on {date_str}:\n" + "\n".join(lines),
        "slots": slots,
    }


# ---------------------------------------------------------------------------
# conflict_edges
# ---------------------------------------------------------------------------

def conflict_edges(conn: sqlite3.Connection, days: int = 7) -> dict[str, Any]:
    """
    Return scheduling conflicts within the next `days` days.

    Returns:
        {"success": bool, "formatted": str, "conflicts": list[dict]}
    """
    edges = find_conflicts(conn, window_hours=days * 24.0)
    if not edges:
        return {"success": True, "formatted": "No scheduling conflicts detected.", "conflicts": []}

    conflicts: list[dict[str, Any]] = []
    lines: list[str] = []
    for edge in edges:
        conflicts.append({
            "a": edge.commitment_a.description,
            "b": edge.commitment_b.description,
            "overlap_minutes": round(edge.overlap_minutes, 1),
            "start": _hhmm(edge.commitment_a.start_ts),
        })
        lines.append(
            f"- '{edge.commitment_a.description}' ({edge.commitment_a.source}) overlaps "
            f"'{edge.commitment_b.description}' ({edge.commitment_b.source}) "
            f"by {edge.overlap_minutes:.0f} min around {_hhmm(edge.commitment_a.start_ts)}"
        )
    return {
        "success": True,
        "formatted": "Conflicts:\n" + "\n".join(lines),
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    from kg.schema import init_db
    from kg.graph import detect_new_conflicts

    with tempfile.TemporaryDirectory() as tmp:
        conn = init_db(str(Path(tmp) / "queries.db"))
        now = time.time()
        today = dt.date.today().isoformat()
        noon = dt.datetime.combine(dt.date.today(), dt.time(12, 0)).timestamp()

        conn.execute("INSERT INTO persons (id, name, created_at) VALUES (1, 'Priya', ?)", (now,))
        conn.execute(
            """INSERT INTO commitments
               (id, person_id, description, start_ts, end_ts, source,
                commitment_type, confidence, raw_text, created_at, updated_at)
               VALUES (1, 1, 'Lunch with Priya', ?, ?, 'calendar', 'HARD', 1.0,
                       'SECRET raw slack text', ?, ?)""",
            (noon, noon + 3600, now, now),
        )
        conn.commit()

        kg_result = query_kg(conn, "lunch priya")
        assert kg_result["success"] and kg_result["nodes"], "query_kg returned nothing"
        # Privacy: raw_text must never appear in the summary payload.
        assert "SECRET" not in str(kg_result["nodes"]), "raw_text leaked into KG summary!"
        print(f"=> query_kg returned {len(kg_result['nodes'])} anonymised node(s)")

        slots = find_free_slots(conn, today, 60)
        assert slots["success"], "find_free_slots failed"
        print(f"=> find_free_slots: {len(slots['slots'])} slot(s)")

        conn.execute(
            """INSERT INTO commitments
               (id, person_id, description, start_ts, end_ts, source,
                commitment_type, confidence, created_at, updated_at)
               VALUES (2, 1, 'standup', ?, ?, 'slack', 'SOFT', 0.7, ?, ?)""",
            (noon + 600, noon + 4200, now, now),
        )
        conn.commit()
        detect_new_conflicts(conn, 2)
        conflicts = conflict_edges(conn, days=30)
        assert conflicts["conflicts"], "expected at least one conflict"
        print(f"=> conflict_edges: {len(conflicts['conflicts'])} conflict(s)")
        conn.close()

    print("All kg/queries.py smoke tests passed.")
