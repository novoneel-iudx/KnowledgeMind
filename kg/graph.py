"""
kg/graph.py
-----------
NetworkX graph builder and temporal conflict detection over the SQLite KG.

The graph is rebuilt from SQLite on demand (per monitor cycle / UI refresh) and
is never persisted -- SQLite is the source of truth. Conflict detection uses
half-open intervals [start, end) and the rule from SPEC 4.4: two commitments
conflict if they overlap >= 5 minutes on the user's personal timeline. Detection
is person-AGNOSTIC -- every stored commitment (calendar events plus commitments
extracted from the user's own messages) sits on that one timeline, so a Slack
soft commitment attributed to its sender can conflict with a calendar event the
user owns. TENTATIVE commitments (confidence < 0.60) never trigger conflicts.
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
    # Tentative commitments are too uncertain to alert on (SPEC 4.3).
    if new_commitment.commitment_type == "TENTATIVE":
        return []

    # Person-agnostic: compare against every other non-tentative commitment on
    # the user's timeline, regardless of person attribution. (Bucketing by
    # person_id previously made cross-channel self-vs-named conflicts -- the
    # headline scenario -- impossible.) Same-real-event records across channels
    # may surface here; cross-source event de-dup is future work, out of scope.
    candidates = conn.execute(
        "SELECT id FROM commitments WHERE id != ? AND commitment_type != 'TENTATIVE'",
        (new_commitment_id,),
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
    from datetime import datetime, time as dtime, timedelta
    from pathlib import Path

    from kg.schema import init_db

    with tempfile.TemporaryDirectory() as tmp:
        conn = init_db(str(Path(tmp) / "graph.db"))

        # Use the REAL write path (insert_commitment) so person bucketing matches
        # production: calendar events are the user's own ("(self)" -> person_id
        # NULL); a Slack soft commitment is attributed to its sender ("Priya" ->
        # a non-NULL person). The headline Scenario-0 conflict is therefore
        # CROSS-bucket -- it only fires with person-agnostic detection.
        tomorrow = datetime.now().date() + timedelta(days=1)
        at_1600 = datetime.combine(tomorrow, dtime(16, 0)).timestamp()
        at_1645 = datetime.combine(tomorrow, dtime(16, 45)).timestamp()

        cal_id = insert_commitment(conn, CommitmentNode(
            id=0, person_name="(self)", description="1:1 with Priya",
            start_ts=at_1600, end_ts=at_1645, source="calendar",
            commitment_type="HARD", confidence=1.0, raw_text="1:1 with Priya"),
            external_id="cal-1")
        slack_id = insert_commitment(conn, CommitmentNode(
            id=0, person_name="Priya", description="see you at 4",
            start_ts=at_1600, end_ts=None, source="slack",
            commitment_type="SOFT", confidence=0.75, raw_text="see you at 4"),
            external_id="slack-1")

        # Confirm the two really are in different person buckets (the bug's root).
        cal_pid = conn.execute("SELECT person_id FROM commitments WHERE id=?", (cal_id,)).fetchone()["person_id"]
        slack_pid = conn.execute("SELECT person_id FROM commitments WHERE id=?", (slack_id,)).fetchone()["person_id"]
        assert cal_pid is None and slack_pid is not None, "expected cross-bucket setup (self vs named)"

        # THE FIX: a cross-channel, cross-person conflict must be detected.
        new_conflicts = detect_new_conflicts(conn, slack_id)
        assert len(new_conflicts) == 1, f"expected 1 cross-channel conflict, got {len(new_conflicts)}"
        print(f"=> cross-channel conflict detected (self calendar vs Priya slack), "
              f"overlap={new_conflicts[0].overlap_minutes:.0f} min")

        # TENTATIVE commitments must NOT trigger conflicts (README / SPEC 4.3).
        tentative_id = insert_commitment(conn, CommitmentNode(
            id=0, person_name="(self)", description="maybe gym", start_ts=at_1600,
            end_ts=at_1645, source="slack", commitment_type="TENTATIVE",
            confidence=0.4, raw_text="maybe gym at 4?"), external_id="slack-2")
        assert detect_new_conflicts(conn, tentative_id) == [], "TENTATIVE must not raise conflicts"
        print("=> TENTATIVE commitment correctly skipped (no alert)")

        open_conflicts = find_conflicts(conn, window_hours=48)
        assert len(open_conflicts) == 1, "expected 1 unalerted conflict"

        people = get_person_commitments(conn, "Priya", days=2)
        assert len(people) == 1, f"expected 1 Priya commitment, got {len(people)}"

        # Write helpers: idempotency (dedup by external_id) + person reuse.
        assert insert_commitment(conn, CommitmentNode(
            id=0, person_name="Priya", description="see you at 4", start_ts=at_1600,
            end_ts=None, source="slack", commitment_type="SOFT", confidence=0.75,
            raw_text="see you at 4"), external_id="slack-1") == slack_id, "dedup failed"
        assert get_or_create_person(conn, "Priya") == slack_pid, "person should be reused"

        graph = build_graph(conn)
        print(f"=> graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges; "
              f"dedup + person reuse ok")
        conn.close()

    print("All kg/graph.py smoke tests passed.")
