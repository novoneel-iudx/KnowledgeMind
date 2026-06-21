"""
kg/graph.py
-----------
NetworkX graph builder and temporal conflict detection over the SQLite KG.

The graph is rebuilt from SQLite on demand (per monitor cycle / UI refresh) and
is never persisted -- SQLite is the source of truth. Conflict detection uses
half-open intervals [start, end) and the rule from SPEC 4.4: two commitments
conflict if they overlap >= 5 minutes AND share a person (or both are self,
person_id IS NULL).
"""

from __future__ import annotations

import sqlite3
import time
from typing import Optional

import networkx as nx

from kg.schema import CommitmentNode, ConflictEdge


# Minimum overlap to count as a conflict (SPEC 4.4).
MIN_OVERLAP_MINUTES: float = 5.0

# Assumed duration when a commitment has no explicit end time.
DEFAULT_DURATION_MINUTES: float = 60.0


# ---------------------------------------------------------------------------
# Row -> dataclass helpers
# ---------------------------------------------------------------------------

def _effective_end(start_ts: float, end_ts: Optional[float]) -> float:
    """Return end_ts, or start + default duration when end is missing."""
    if end_ts is not None:
        return end_ts
    return start_ts + DEFAULT_DURATION_MINUTES * 60.0


def _overlap_minutes(
    a_start: float, a_end: Optional[float],
    b_start: float, b_end: Optional[float],
) -> float:
    """Overlap of two half-open intervals in minutes (0 if disjoint)."""
    a_end_eff = _effective_end(a_start, a_end)
    b_end_eff = _effective_end(b_start, b_end)
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end_eff, b_end_eff)
    overlap_seconds = earliest_end - latest_start
    return max(overlap_seconds / 60.0, 0.0)


def _row_to_commitment(row: sqlite3.Row, person_name: str) -> CommitmentNode:
    return CommitmentNode(
        id=row["id"],
        person_name=person_name,
        description=row["description"],
        start_ts=row["start_ts"],
        end_ts=row["end_ts"],
        source=row["source"],
        commitment_type=row["commitment_type"],
        confidence=row["confidence"],
        raw_text=row["raw_text"],
    )


def _load_commitment(conn: sqlite3.Connection, commitment_id: int) -> Optional[CommitmentNode]:
    row = conn.execute(
        """SELECT c.*, p.name AS person_name
           FROM commitments c LEFT JOIN persons p ON c.person_id = p.id
           WHERE c.id = ?""",
        (commitment_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_commitment(row, row["person_name"] or "(self)")


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph(conn: sqlite3.Connection) -> nx.DiGraph:
    """
    Rebuild the full knowledge graph from SQLite as a NetworkX DiGraph.

    Node id conventions:
        person:<id>     -> type='Person'
        commitment:<id> -> type='Commitment', commitment_type='HARD'|'SOFT'|...
    Edges:
        person -> commitment  (label 'has_commitment')
        commitment <-> commitment (label 'conflict') for detected conflicts.
    """
    graph: nx.DiGraph = nx.DiGraph()

    for person in conn.execute("SELECT id, name FROM persons").fetchall():
        graph.add_node(
            f"person:{person['id']}",
            label=person["name"],
            type="Person",
        )

    commitment_rows = conn.execute(
        """SELECT c.*, p.name AS person_name
           FROM commitments c LEFT JOIN persons p ON c.person_id = p.id"""
    ).fetchall()
    for row in commitment_rows:
        node_id = f"commitment:{row['id']}"
        graph.add_node(
            node_id,
            label=row["description"][:40],
            type="Commitment",
            commitment_type=row["commitment_type"],
            source=row["source"],
            start_ts=row["start_ts"],
        )
        if row["person_id"] is not None:
            graph.add_edge(f"person:{row['person_id']}", node_id, label="has_commitment")

    for conflict in conn.execute(
        "SELECT commitment_a_id, commitment_b_id, overlap_minutes FROM conflicts"
    ).fetchall():
        node_a = f"commitment:{conflict['commitment_a_id']}"
        node_b = f"commitment:{conflict['commitment_b_id']}"
        if graph.has_node(node_a) and graph.has_node(node_b):
            graph.add_edge(
                node_a, node_b,
                label="conflict",
                overlap_minutes=conflict["overlap_minutes"],
            )

    return graph


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def detect_new_conflicts(conn: sqlite3.Connection, new_commitment_id: int) -> list[ConflictEdge]:
    """
    Check a freshly inserted commitment against existing ones and persist any
    new conflicts into the `conflicts` table. Returns the new ConflictEdges.
    """
    new_commitment = _load_commitment(conn, new_commitment_id)
    if new_commitment is None:
        return []

    new_row = conn.execute(
        "SELECT person_id FROM commitments WHERE id = ?", (new_commitment_id,)
    ).fetchone()
    new_person_id = new_row["person_id"] if new_row else None

    # Candidate commitments share the person (or both self) and are not self.
    if new_person_id is None:
        candidates = conn.execute(
            "SELECT id FROM commitments WHERE person_id IS NULL AND id != ?",
            (new_commitment_id,),
        ).fetchall()
    else:
        candidates = conn.execute(
            "SELECT id FROM commitments WHERE person_id = ? AND id != ?",
            (new_person_id, new_commitment_id),
        ).fetchall()

    found: list[ConflictEdge] = []
    now = time.time()

    for candidate in candidates:
        other = _load_commitment(conn, candidate["id"])
        if other is None:
            continue

        overlap = _overlap_minutes(
            new_commitment.start_ts, new_commitment.end_ts,
            other.start_ts, other.end_ts,
        )
        if overlap < MIN_OVERLAP_MINUTES:
            continue

        # Skip if this pair already recorded (either ordering).
        existing = conn.execute(
            """SELECT id FROM conflicts
               WHERE (commitment_a_id = ? AND commitment_b_id = ?)
                  OR (commitment_a_id = ? AND commitment_b_id = ?)""",
            (new_commitment_id, candidate["id"], candidate["id"], new_commitment_id),
        ).fetchone()
        if existing is not None:
            continue

        cursor = conn.execute(
            """INSERT INTO conflicts
               (commitment_a_id, commitment_b_id, overlap_minutes, detected_at, alerted)
               VALUES (?, ?, ?, ?, 0)""",
            (new_commitment_id, candidate["id"], overlap, now),
        )
        conn.commit()
        found.append(ConflictEdge(
            id=cursor.lastrowid,
            commitment_a=new_commitment,
            commitment_b=other,
            overlap_minutes=overlap,
            alerted=False,
        ))

    return found


def find_conflicts(conn: sqlite3.Connection, window_hours: float = 24.0) -> list[ConflictEdge]:
    """
    Return unalerted conflicts whose commitments start within `window_hours`
    from now. Reads the persisted `conflicts` table.
    """
    now = time.time()
    window_end = now + window_hours * 3600.0

    rows = conn.execute(
        """SELECT cf.id AS conflict_id, cf.overlap_minutes, cf.alerted,
                  cf.commitment_a_id, cf.commitment_b_id
           FROM conflicts cf
           JOIN commitments ca ON cf.commitment_a_id = ca.id
           WHERE cf.alerted = 0 AND ca.start_ts <= ?
           ORDER BY cf.detected_at DESC""",
        (window_end,),
    ).fetchall()

    edges: list[ConflictEdge] = []
    for row in rows:
        commitment_a = _load_commitment(conn, row["commitment_a_id"])
        commitment_b = _load_commitment(conn, row["commitment_b_id"])
        if commitment_a is None or commitment_b is None:
            continue
        edges.append(ConflictEdge(
            id=row["conflict_id"],
            commitment_a=commitment_a,
            commitment_b=commitment_b,
            overlap_minutes=row["overlap_minutes"],
            alerted=bool(row["alerted"]),
        ))
    return edges


def get_person_commitments(
    conn: sqlite3.Connection, person_name: str, days: int = 7
) -> list[CommitmentNode]:
    """Return a person's commitments starting within the next `days` days."""
    now = time.time()
    window_end = now + days * 86400.0
    rows = conn.execute(
        """SELECT c.*, p.name AS person_name
           FROM commitments c JOIN persons p ON c.person_id = p.id
           WHERE p.name = ? AND c.start_ts BETWEEN ? AND ?
           ORDER BY c.start_ts ASC""",
        (person_name, now, window_end),
    ).fetchall()
    return [_row_to_commitment(row, row["person_name"]) for row in rows]


# ---------------------------------------------------------------------------
# Writes (used by the monitor UPDATING node)
# ---------------------------------------------------------------------------

def get_or_create_person(conn: sqlite3.Connection, name: str) -> int:
    """Return the id of a person, inserting them if not already present."""
    row = conn.execute("SELECT id FROM persons WHERE name = ?", (name,)).fetchone()
    if row is not None:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO persons (name, created_at) VALUES (?, ?)", (name, time.time())
    )
    conn.commit()
    return cursor.lastrowid


def insert_commitment(
    conn: sqlite3.Connection,
    commitment: CommitmentNode,
    external_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> int:
    """
    Insert a commitment, resolving (and creating) its person. Idempotent:
    returns the existing id when the same commitment is already present.

    Dedup key: `external_id` when provided (calendar event id / slack ts),
    otherwise the (description, start_ts, source) triple -- so re-polling the
    same message does not create duplicates.
    """
    if external_id:
        existing = conn.execute(
            "SELECT id FROM commitments WHERE external_id = ?", (external_id,)
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id FROM commitments WHERE description = ? AND start_ts = ? AND source = ?",
            (commitment.description, commitment.start_ts, commitment.source),
        ).fetchone()
    if existing is not None:
        return existing["id"]

    person_id: Optional[int] = None
    if commitment.person_name and commitment.person_name != "(self)":
        person_id = get_or_create_person(conn, commitment.person_name)

    now = time.time()
    cursor = conn.execute(
        """INSERT INTO commitments
           (person_id, description, start_ts, end_ts, source, commitment_type,
            confidence, raw_text, channel_id, external_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (person_id, commitment.description, commitment.start_ts, commitment.end_ts,
         commitment.source, commitment.commitment_type, commitment.confidence,
         commitment.raw_text, channel_id, external_id, now, now),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    from kg.schema import init_db

    with tempfile.TemporaryDirectory() as tmp:
        conn = init_db(str(Path(tmp) / "graph.db"))
        now = time.time()

        conn.execute(
            "INSERT INTO persons (id, name, created_at) VALUES (1, 'Priya', ?)", (now,)
        )
        # Two overlapping commitments for the same person -> should conflict.
        conn.execute(
            """INSERT INTO commitments
               (id, person_id, description, start_ts, end_ts, source,
                commitment_type, confidence, created_at, updated_at)
               VALUES (1, 1, 'Team standup', ?, ?, 'calendar', 'HARD', 1.0, ?, ?)""",
            (now + 3600, now + 5400, now, now),
        )
        conn.execute(
            """INSERT INTO commitments
               (id, person_id, description, start_ts, end_ts, source,
                commitment_type, confidence, created_at, updated_at)
               VALUES (2, 1, 'see you at 4', ?, ?, 'slack', 'SOFT', 0.7, ?, ?)""",
            (now + 3900, now + 5700, now, now),
        )
        conn.commit()

        new_conflicts = detect_new_conflicts(conn, 2)
        assert len(new_conflicts) == 1, f"expected 1 conflict, got {len(new_conflicts)}"
        print(f"=> detected conflict, overlap={new_conflicts[0].overlap_minutes:.0f} min")

        graph = build_graph(conn)
        print(f"=> graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")
        assert graph.number_of_nodes() == 3, "expected 1 person + 2 commitments"

        open_conflicts = find_conflicts(conn, window_hours=48)
        assert len(open_conflicts) == 1, "expected 1 unalerted conflict"

        people = get_person_commitments(conn, "Priya", days=1)
        assert len(people) == 2, f"expected 2 commitments, got {len(people)}"

        # Write helpers: insert + idempotency + person reuse.
        new_node = CommitmentNode(
            id=0, person_name="Arjun", description="design sync", start_ts=now + 7200,
            end_ts=now + 9000, source="slack", commitment_type="SOFT",
            confidence=0.7, raw_text="design sync at 6",
        )
        inserted_id = insert_commitment(conn, new_node)
        assert inserted_id > 0, "insert_commitment did not return an id"
        # Re-inserting the same content must return the same id (dedup).
        assert insert_commitment(conn, new_node) == inserted_id, "dedup failed"
        priya_id = get_or_create_person(conn, "Priya")
        assert priya_id == 1, "existing person should be reused, not duplicated"
        print(f"=> insert_commitment id={inserted_id}, dedup + person reuse ok")
        conn.close()

    print("All kg/graph.py smoke tests passed.")
