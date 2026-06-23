"""
kg/janitor.py
-------------
KG memory management — archives stale commitments and prunes old turns.

Archival rules (status flipped to 'archived', nothing deleted):
  TENTATIVE  — end-time > 7 days past   (uncertain, short shelf-life)
  SOFT       — past-dated at all        (best-effort commitments expire quickly)
  HARD       — end-time > 30 days past  (keep recent history for context)

Conflicts are archived when both linked commitments are archived, so the
conflict detector never re-examines dead pairs.

Turns pruning: rows older than 90 days are deleted outright (chat history,
not commitments — no archival needed).

Usage:
  python -m kg.janitor               # dry-run (prints counts, no writes)
  python -m kg.janitor --apply       # apply changes
  python -m kg.janitor --apply --db /path/to/km.db
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_TENTATIVE_GRACE_DAYS = 7
_SOFT_GRACE_DAYS = 0        # archive once end-time has passed
_HARD_GRACE_DAYS = 30
_TURNS_RETENTION_DAYS = 90

_DEFAULT_DURATION_S = 3600  # 1 h assumed when end_ts is NULL


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class JanitorResult:
    archived_tentative: int = 0
    archived_soft: int = 0
    archived_hard: int = 0
    archived_conflicts: int = 0
    deleted_turns: int = 0

    @property
    def total_archived(self) -> int:
        return self.archived_tentative + self.archived_soft + self.archived_hard

    def summary(self) -> str:
        return (
            f"Archived commitments: {self.total_archived} "
            f"(TENTATIVE={self.archived_tentative}, SOFT={self.archived_soft}, "
            f"HARD={self.archived_hard}), "
            f"archived conflicts: {self.archived_conflicts}, "
            f"deleted turns: {self.deleted_turns}"
        )


# ---------------------------------------------------------------------------
# Core janitor
# ---------------------------------------------------------------------------

def run_janitor(
    conn: sqlite3.Connection,
    apply: bool = True,
    now: float | None = None,
) -> JanitorResult:
    """
    Scan active commitments and archive stale ones according to the thresholds.

    Args:
        conn:  Open SQLite connection (from get_db_connection).
        apply: When False, counts what would change but writes nothing (dry-run).
        now:   Unix timestamp to treat as "now". Defaults to time.time().

    Returns:
        JanitorResult with counts of archived/deleted rows.
    """
    if now is None:
        now = time.time()

    result = JanitorResult()

    # --- Fetch all active commitments ---
    rows = conn.execute(
        """SELECT id, commitment_type, start_ts, end_ts
           FROM commitments
           WHERE status = 'active'"""
    ).fetchall()

    tentative_ids: list[int] = []
    soft_ids: list[int] = []
    hard_ids: list[int] = []

    for row in rows:
        eff_end = row["end_ts"] if row["end_ts"] else row["start_ts"] + _DEFAULT_DURATION_S
        age_days = (now - eff_end) / 86400.0
        ctype = row["commitment_type"]

        if ctype == "TENTATIVE" and age_days >= _TENTATIVE_GRACE_DAYS:
            tentative_ids.append(row["id"])
        elif ctype == "SOFT" and age_days >= _SOFT_GRACE_DAYS:
            soft_ids.append(row["id"])
        elif ctype == "HARD" and age_days >= _HARD_GRACE_DAYS:
            hard_ids.append(row["id"])

    result.archived_tentative = len(tentative_ids)
    result.archived_soft = len(soft_ids)
    result.archived_hard = len(hard_ids)

    all_ids = tentative_ids + soft_ids + hard_ids

    # --- Archive conflicts whose both commitments are being archived ---
    if all_ids:
        id_set = set(all_ids)
        conflict_rows = conn.execute(
            """SELECT id, commitment_a_id, commitment_b_id
               FROM conflicts
               WHERE commitment_a_id IN ({ph}) OR commitment_b_id IN ({ph})""".format(
                ph=",".join("?" * len(all_ids))
            ),
            all_ids + all_ids,
        ).fetchall()

        # Only archive the conflict if BOTH sides are going away.
        # We also need to check already-archived commitments.
        archived_commitment_ids = set(
            row["id"]
            for row in conn.execute(
                "SELECT id FROM commitments WHERE status = 'archived'"
            ).fetchall()
        ) | id_set

        conflict_ids_to_archive = [
            row["id"]
            for row in conflict_rows
            if row["commitment_a_id"] in archived_commitment_ids
            and row["commitment_b_id"] in archived_commitment_ids
        ]
        result.archived_conflicts = len(conflict_ids_to_archive)

    # --- Turns older than retention window ---
    cutoff_ts = now - _TURNS_RETENTION_DAYS * 86400.0
    turns_count = conn.execute(
        "SELECT COUNT(*) FROM turns WHERE timestamp < ?", (cutoff_ts,)
    ).fetchone()[0]
    result.deleted_turns = turns_count

    if not apply:
        return result

    # --- Apply writes ---
    updated_at = now

    def _archive_batch(ids: list[int]) -> None:
        if not ids:
            return
        conn.execute(
            "UPDATE commitments SET status = 'archived', updated_at = ? WHERE id IN ({})".format(
                ",".join("?" * len(ids))
            ),
            [updated_at] + ids,
        )

    _archive_batch(all_ids)

    if all_ids and conflict_ids_to_archive:
        conn.execute(
            "UPDATE conflicts SET alerted = 1 WHERE id IN ({})".format(
                ",".join("?" * len(conflict_ids_to_archive))
            ),
            conflict_ids_to_archive,
        )

    if turns_count:
        conn.execute("DELETE FROM turns WHERE timestamp < ?", (cutoff_ts,))

    conn.commit()
    return result


# ---------------------------------------------------------------------------
# Convenience wrapper (called from API lifespan + POST /api/kg/janitor)
# ---------------------------------------------------------------------------

def run_janitor_for_config(apply: bool = True) -> JanitorResult:
    """Load config, open the KG connection, run the janitor, close."""
    from config.store import get_config
    from kg.schema import get_db_connection

    cfg = get_config()
    conn = get_db_connection(cfg.db_path)
    try:
        return run_janitor(conn, apply=apply)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Smoke test / CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import tempfile
    from pathlib import Path

    parser = argparse.ArgumentParser(description="KG janitor — archive stale commitments.")
    parser.add_argument("--apply", action="store_true",
                        help="Apply changes (default: dry-run).")
    parser.add_argument("--db", default=None,
                        help="Path to KM SQLite DB. Defaults to a temp DB for smoke test.")
    args = parser.parse_args()

    if args.db:
        from kg.schema import get_db_connection
        conn = get_db_connection(args.db)
        result = run_janitor(conn, apply=args.apply)
        conn.close()
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[{mode}] {result.summary()}")
    else:
        # Smoke test: build a temp DB, insert commitments of each type at
        # various ages, run the janitor, assert archival counts are correct.
        with tempfile.TemporaryDirectory() as tmp:
            from kg.schema import init_db
            db_path = str(Path(tmp) / "test.db")
            conn = init_db(db_path)

            now = time.time()

            def _insert(ctype: str, age_days: float) -> int:
                end_ts = now - age_days * 86400.0
                start_ts = end_ts - 3600.0
                cursor = conn.execute(
                    """INSERT INTO commitments
                       (person_id, description, start_ts, end_ts, source,
                        commitment_type, confidence, created_at, updated_at, status)
                       VALUES (NULL, ?, ?, ?, 'test', ?, 1.0, ?, ?, 'active')""",
                    (f"{ctype} {age_days}d ago", start_ts, end_ts, ctype, now, now),
                )
                conn.commit()
                return cursor.lastrowid

            # Should be archived
            _insert("TENTATIVE", 8)    # > 7 days
            _insert("SOFT", 1)         # past-dated
            _insert("HARD", 31)        # > 30 days

            # Should stay active
            _insert("TENTATIVE", 5)    # < 7 days
            _insert("SOFT", -1)        # ends tomorrow — not yet past
            _insert("HARD", 10)        # < 30 days

            # Dry-run: no writes
            dry = run_janitor(conn, apply=False, now=now)
            assert dry.total_archived == 3, f"Expected 3 archived, got {dry.total_archived}"
            active_count = conn.execute(
                "SELECT COUNT(*) FROM commitments WHERE status = 'active'"
            ).fetchone()[0]
            assert active_count == 6, "Dry-run must not write"

            # Apply
            result = run_janitor(conn, apply=True, now=now)
            assert result.total_archived == 3
            assert result.archived_tentative == 1
            assert result.archived_soft == 1
            assert result.archived_hard == 1

            active_after = conn.execute(
                "SELECT COUNT(*) FROM commitments WHERE status = 'active'"
            ).fetchone()[0]
            archived_after = conn.execute(
                "SELECT COUNT(*) FROM commitments WHERE status = 'archived'"
            ).fetchone()[0]
            assert active_after == 3, f"Expected 3 active after, got {active_after}"
            assert archived_after == 3, f"Expected 3 archived after, got {archived_after}"

            conn.close()

        print("=> kg/janitor.py smoke test passed.")
        print(f"   {result.summary()}")
