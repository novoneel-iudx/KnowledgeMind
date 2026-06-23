"""
runtime/briefing.py
-------------------
FR-2.3 — Daily-briefing composer.

Deterministically composes a digest of *today*:
  - commitments due today (KG)        — always available
  - scheduling conflicts (KG)         — always available
  - task load (Todoist signal)        — best-effort
  - readiness (sleep / recovery)      — best-effort

No LLM call — the composer is pure (it only reads SQLite + an optional signals
dict), so it runs offline and is unit-testable with an injected ``now``.

Signals source: ``GET /api/insights`` (readiness/correlation) is not yet shipped. It
is not shipped yet, so by default we read the latest connector snapshots from
``kg.connector_store`` and degrade gracefully when they are absent. Pass
``signals=...`` to inject them (tests, or a future insights feed) and ``signals={}``
to compose from commitments/conflicts alone.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from config.store import get_config
from kg.schema import get_db_connection
from kg.queries import conflict_edges


# ---------------------------------------------------------------------------
# Signal gathering (best-effort, never raises)
# ---------------------------------------------------------------------------

def _default_signals() -> dict[str, Any]:
    """Latest connector snapshots from connectors.db, or {} if unavailable."""
    signals: dict[str, Any] = {}
    try:
        from kg import connector_store
        for name in ("todoist", "apple_health", "strava", "spotify"):
            latest = connector_store.get_latest(name)
            if latest:
                signals[name] = latest
    except Exception:  # noqa: BLE001 — briefing must never crash on missing signals
        pass
    return signals


def _task_load(signals: dict[str, Any]) -> Optional[dict[str, Any]]:
    td = signals.get("todoist")
    if not td:
        return None
    return {
        "due_today": int(td.get("due_today_count") or 0),
        "overdue": int(td.get("overdue_count") or 0),
        "heavy_day": bool(td.get("heavy_day")),
    }


def _readiness(signals: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Prefer a Stream-1 'readiness'/'insights' block; else derive from health."""
    if signals.get("readiness"):
        return signals["readiness"]
    if signals.get("insights"):
        return signals["insights"]
    ah = signals.get("apple_health")
    if not ah:
        return None
    return {
        "recovery_status": ah.get("recovery_status"),
        "sleep_hours": ah.get("sleep_hours"),
        "sleep_quality": ah.get("sleep_quality"),
        "low_hrv": bool(ah.get("low_hrv")),
        "high_rhr": bool(ah.get("high_rhr")),
    }


# ---------------------------------------------------------------------------
# KG reads (scoped to the injected `now`)
# ---------------------------------------------------------------------------

def _commitments_for_day(conn, day: datetime.date) -> list[dict[str, Any]]:
    start = datetime.datetime.combine(day, datetime.time.min).timestamp()
    end = datetime.datetime.combine(day, datetime.time.max).timestamp()
    rows = conn.execute(
        """SELECT c.description, c.source, c.commitment_type, c.start_ts,
                  COALESCE(p.name, '(self)') AS who
           FROM commitments c LEFT JOIN persons p ON c.person_id = p.id
           WHERE c.start_ts BETWEEN ? AND ?
           ORDER BY c.start_ts""",
        (start, end),
    ).fetchall()
    return [
        {
            "description": r["description"],
            "who": r["who"],
            "source": r["source"],
            "type": r["commitment_type"],
            "at": datetime.datetime.fromtimestamp(r["start_ts"]).strftime("%H:%M"),
            "start_ts": r["start_ts"],
        }
        for r in rows
    ]


def _upcoming(conn, after_ts: float, limit: int = 3) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT c.description, c.start_ts, COALESCE(p.name, '(self)') AS who
           FROM commitments c LEFT JOIN persons p ON c.person_id = p.id
           WHERE c.start_ts > ? ORDER BY c.start_ts LIMIT ?""",
        (after_ts, limit),
    ).fetchall()
    return [
        {
            "description": r["description"],
            "who": r["who"],
            "when": datetime.datetime.fromtimestamp(r["start_ts"]).strftime("%a %d %b %H:%M"),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------

def compose_briefing(
    now: Optional[datetime.datetime] = None,
    *,
    db_path: Optional[str] = None,
    signals: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Build today's briefing digest.

    Args:
        now:     injectable clock (defaults to datetime.now()).
        db_path: KG database (defaults to cfg.db_path).
        signals: optional pre-fetched connector signals. None => read latest
                 snapshots; {} => compose from commitments/conflicts only.

    Returns a digest dict (see keys below) including a `formatted` markdown body
    and a `surface` flag (whether there is anything worth surfacing).
    """
    now = now or datetime.datetime.now()
    db_path = db_path or get_config().db_path
    signals = _default_signals() if signals is None else signals

    conn = get_db_connection(db_path)
    try:
        today = _commitments_for_day(conn, now.date())
        upcoming = _upcoming(conn, now.timestamp()) if not today else []
        conflicts = conflict_edges(conn, days=1).get("conflicts", [])
    finally:
        conn.close()

    task_load = _task_load(signals)
    readiness = _readiness(signals)

    # ── Build the human-facing digest ─────────────────────────────────────────
    lines: list[str] = []

    if today:
        lines.append(f"**Today ({len(today)} commitment(s)):**")
        for c in today:
            who = "" if c["who"] == "(self)" else f" — with {c['who']}"
            lines.append(f"- {c['at']} {c['description']}{who}")
    elif upcoming:
        lines.append("**Nothing scheduled today.** Next up:")
        for c in upcoming:
            lines.append(f"- {c['when']} {c['description']}")
    else:
        lines.append("**Nothing scheduled today.**")

    if conflicts:
        lines.append("")
        lines.append(f"**⚠ {len(conflicts)} scheduling conflict(s):**")
        for cf in conflicts:
            lines.append(
                f"- '{cf['a']}' overlaps '{cf['b']}' (~{cf['overlap_minutes']:.0f} min "
                f"around {cf['start']})"
            )

    if task_load:
        lines.append("")
        parts = [f"{task_load['due_today']} due today", f"{task_load['overdue']} overdue"]
        note = " — heavy day, consider reprioritising" if task_load["heavy_day"] else ""
        lines.append(f"**Tasks:** {', '.join(parts)}{note}.")

    if readiness:
        lines.append("")
        bits: list[str] = []
        if readiness.get("recovery_status"):
            bits.append(f"recovery {readiness['recovery_status']}")
        if readiness.get("sleep_hours") is not None:
            bits.append(f"slept {readiness['sleep_hours']}h")
        if readiness.get("low_hrv"):
            bits.append("low HRV")
        if readiness.get("high_rhr"):
            bits.append("elevated resting HR")
        if bits:
            lines.append(f"**Readiness:** {', '.join(bits)}.")
        # Signal fusion (morning_brief_skill rule): poor recovery + heavy day.
        if task_load and task_load["heavy_day"] and (
            readiness.get("low_hrv") or readiness.get("sleep_quality") == "poor"
        ):
            lines.append("- Recovery is low and task load is high — protect the essentials today.")

    # ── Headline + surface decision ───────────────────────────────────────────
    if conflicts:
        headline = f"{len(conflicts)} conflict(s) today — worth a look."
    elif today:
        headline = f"{len(today)} commitment(s) today."
    elif task_load and (task_load["overdue"] or task_load["due_today"]):
        headline = f"{task_load['overdue']} overdue, {task_load['due_today']} due today."
    else:
        headline = "Looks like a quiet day."

    surface = bool(today or conflicts or (task_load and (task_load["overdue"] or task_load["due_today"])))

    return {
        "date": now.date().isoformat(),
        "generated_iso": now.isoformat(timespec="seconds"),
        "headline": headline,
        "commitments_today": today,
        "upcoming": upcoming,
        "conflicts": conflicts,
        "task_load": task_load,
        "readiness": readiness,
        "surface": surface,
        "formatted": "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# Smoke test (offline, seeded KG)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    import time
    from pathlib import Path
    from kg.schema import init_db

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "brief.db")
        conn = init_db(db)
        now = datetime.datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
        at_ts = now.replace(hour=14).timestamp()
        conn.execute("INSERT INTO persons (id, name, created_at) VALUES (1, 'Lena', ?)", (time.time(),))
        conn.execute(
            """INSERT INTO commitments
               (person_id, description, start_ts, end_ts, source, commitment_type,
                confidence, raw_text, created_at, updated_at)
               VALUES (1, 'Lunch with Lena', ?, ?, 'calendar', 'SOFT', 0.8, '', ?, ?)""",
            (at_ts, at_ts + 3600, time.time(), time.time()),
        )
        conn.commit()
        conn.close()

        digest = compose_briefing(
            now=now,
            db_path=db,
            signals={"todoist": {"due_today_count": 4, "overdue_count": 2, "heavy_day": True},
                     "apple_health": {"recovery_status": "low", "sleep_hours": 5.0,
                                      "sleep_quality": "poor", "low_hrv": True}},
        )
        assert digest["surface"] is True
        assert len(digest["commitments_today"]) == 1
        assert digest["task_load"]["overdue"] == 2
        assert "Lunch with Lena" in digest["formatted"]
        assert "Recovery is low" in digest["formatted"], "fusion rule should fire"
        print(digest["formatted"])
        print("\nruntime/briefing.py smoke tests passed.")
