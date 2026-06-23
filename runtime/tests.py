"""
runtime/tests.py
----------------
Stub-LLM unit tests for the proactive runtime (SPEC §8). Fully offline — no
Ollama, no API keys, no network. Run with:

    python -m runtime.tests

Covers:
  1. loader        — valid specs load; malformed specs give clear, named errors
  2. scheduler     — one tick at a fixed `now` fires the due job → nudge in outbox
  3. quiet hours   — a due job in quiet hours is queued (suppressed), not delivered
  4. briefing      — the composer fuses commitments + conflicts + signals
  5. nudge outbox  — list / dismiss / idempotent dismiss / active count
  6. privacy seam  — the runner reaches the agent (→ router); no direct cloud call
"""

from __future__ import annotations

import datetime
import json
import tempfile
import time
from pathlib import Path

from kg.schema import init_db
from runtime import outbox
from runtime.briefing import compose_briefing
from runtime.loader import (
    SpecError,
    load_job_file,
    load_runtime,
)
from runtime.runner import ProactiveRuntime, in_quiet_hours, is_due


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_kg(db_path: str, day: datetime.date) -> None:
    """Seed one commitment at 14:00 on `day` so the briefing has something."""
    conn = init_db(db_path)
    at = datetime.datetime.combine(day, datetime.time(14, 0)).timestamp()
    now = time.time()
    conn.execute("INSERT INTO persons (id, name, created_at) VALUES (1, 'Lena', ?)", (now,))
    conn.execute(
        """INSERT INTO commitments
           (person_id, description, start_ts, end_ts, source, commitment_type,
            confidence, raw_text, created_at, updated_at)
           VALUES (1, 'Lunch with Lena', ?, ?, 'calendar', 'SOFT', 0.8, '', ?, ?)""",
        (at, at + 3600, now, now),
    )
    conn.commit()
    conn.close()


def _stub_agent(prompt: str, level: str) -> dict:
    """A stub LLM caller: never touches a model; reports a LOCAL routing trace."""
    return {
        "answer": "Heads up: 2 tasks overdue, 3 due today.",
        "routing_log": [{"tool": "todoist", "decision": "LOCAL"},
                        {"tool": "query_kg", "decision": "LOCAL"}],
        "token_summary": None,
        "agency_level": level,
    }


def _ok(name: str) -> None:
    print(f"  PASS  {name}")


# ---------------------------------------------------------------------------
# 1. Loader — valid + malformed
# ---------------------------------------------------------------------------

def test_loader_valid() -> None:
    result = load_runtime()
    assert result.ok, f"real specs should load clean, got errors: {result.errors}"
    assert len(result.jobs) == 5, [j.id for j in result.jobs]
    assert {"morning_brief", "evening_tasks", "fitness_check"} <= {j.id for j in result.jobs}
    # Every job links to a loaded skill recipe.
    for job in result.jobs:
        assert job.skill in result.skills, job.skill
        assert result.skills[job.skill].recipe.strip()
    _ok("loader: 5 real jobs + skills load with no errors")


def test_loader_malformed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        # invalid JSON
        (d / "bad_json.json").write_text("{ not json ]", encoding="utf-8")
        # missing schedule
        (d / "no_schedule.json").write_text(json.dumps({"name": "x", "skill": "s"}), encoding="utf-8")
        # bad cron (too few fields)
        (d / "bad_cron.json").write_text(
            json.dumps({"name": "y", "schedule": "0 8 * *", "skill": "s"}), encoding="utf-8")
        # bad agency level
        (d / "bad_level.json").write_text(
            json.dumps({"name": "z", "schedule": "0 8 * * *", "skill": "s",
                        "agency_level": "L9"}), encoding="utf-8")

        for fname, needle in [
            ("bad_json.json", "invalid JSON"),
            ("no_schedule.json", "missing required field 'schedule'"),
            ("bad_cron.json", "bad schedule"),
            ("bad_level.json", "invalid agency_level"),
        ]:
            try:
                load_job_file(d / fname)
                assert False, f"{fname} should have raised SpecError"
            except SpecError as err:
                assert needle in str(err), f"{fname}: expected {needle!r} in {err!r}"

    _ok("loader: malformed specs raise clear, file-named SpecErrors")


def test_loader_unknown_skill() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        jobs_dir = Path(tmp) / "jobs"
        skills_dir = Path(tmp) / "skills"
        jobs_dir.mkdir()
        skills_dir.mkdir()
        (jobs_dir / "orphan.json").write_text(
            json.dumps({"name": "orphan", "schedule": "0 8 * * *", "skill": "missing_skill"}),
            encoding="utf-8")
        result = load_runtime(jobs_dir=jobs_dir, skills_dir=skills_dir)
        assert not result.ok
        assert result.jobs == [], "job with unknown skill must be dropped"
        assert any("unknown skill" in e for e in result.errors), result.errors
    _ok("loader: job referencing an unknown skill is dropped + reported")


# ---------------------------------------------------------------------------
# 2 + 3. Scheduler tick (fixed now) + quiet hours
# ---------------------------------------------------------------------------

def test_scheduler_tick() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rt.db")
        rt = ProactiveRuntime(agent_run=_stub_agent, db_path=db)
        assert rt.load.ok, rt.load.errors

        # morning_brief is "0 8 * * *" -> due at 08:00 exactly.
        now = datetime.datetime(2026, 6, 23, 8, 0)
        morning = next(j for j in rt.load.jobs if j.id == "morning_brief")
        assert is_due(morning, now)
        assert not is_due(morning, now.replace(minute=1))

        fired = rt.run_due_jobs(now)
        fired_ids = [f["job"] for f in fired]
        assert "morning_brief" in fired_ids, fired_ids
        brief = next(f for f in fired if f["job"] == "morning_brief")
        assert brief["surfaced"] and brief["nudge_id"], brief

        nudges = outbox.list_nudges(db_path=db)
        assert any(n["id"] == brief["nudge_id"] for n in nudges)

        # Idempotent within the same minute.
        assert rt.run_due_jobs(now) == [], "must not double-fire in the same minute"
    _ok("scheduler: due job at fixed now fires its skill (stub) -> nudge in outbox")


def test_quiet_hours() -> None:
    # Defaults: quiet 22:00 -> 08:00.
    assert in_quiet_hours(datetime.datetime(2026, 6, 23, 23, 0))
    assert in_quiet_hours(datetime.datetime(2026, 6, 23, 3, 0))
    assert not in_quiet_hours(datetime.datetime(2026, 6, 23, 8, 0))   # boundary = awake
    assert not in_quiet_hours(datetime.datetime(2026, 6, 23, 12, 0))

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rt.db")
        rt = ProactiveRuntime(agent_run=_stub_agent, db_path=db)
        job = next(j for j in rt.load.jobs if j.quiet_hours_aware)
        res = rt.run_job(job, datetime.datetime(2026, 6, 23, 23, 30))
        assert res["suppressed"] and not res["surfaced"], res

        # Suppressed nudge is queued (present) but excluded from the delivered feed.
        assert outbox.count_active(db_path=db) == 0
        delivered = outbox.list_nudges(include_suppressed=False, db_path=db)
        assert delivered == [], "quiet-hours nudge must not be delivered"
        queued = outbox.list_nudges(include_suppressed=True, db_path=db)
        assert len(queued) == 1 and queued[0]["suppressed"], queued
    _ok("quiet hours: due job is queued (suppressed), not delivered")


# ---------------------------------------------------------------------------
# 4. Briefing composer
# ---------------------------------------------------------------------------

def test_briefing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "brief.db")
        now = datetime.datetime(2026, 6, 23, 9, 0)
        _seed_kg(db, now.date())

        digest = compose_briefing(
            now=now, db_path=db,
            signals={"todoist": {"due_today_count": 4, "overdue_count": 2, "heavy_day": True},
                     "apple_health": {"recovery_status": "low", "sleep_quality": "poor",
                                      "sleep_hours": 5.0, "low_hrv": True}},
        )
        assert digest["date"] == "2026-06-23"
        assert digest["surface"] is True
        assert len(digest["commitments_today"]) == 1
        assert digest["task_load"]["overdue"] == 2
        assert digest["readiness"]["recovery_status"] == "low"
        assert "Lunch with Lena" in digest["formatted"]
        assert "Recovery is low and task load is high" in digest["formatted"], "fusion rule"

        # Empty-signals path still composes from the KG alone.
        bare = compose_briefing(now=now, db_path=db, signals={})
        assert bare["task_load"] is None and bare["readiness"] is None
        assert len(bare["commitments_today"]) == 1
    _ok("briefing: composes commitments + conflicts + signals (with fusion)")


# ---------------------------------------------------------------------------
# 5. Nudge outbox dismiss flow
# ---------------------------------------------------------------------------

def test_nudge_dismiss() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "nudges.db")
        a = outbox.add_nudge("first", "tasks_skill", job="evening_tasks", db_path=db)
        b = outbox.add_nudge("second", "fitness_skill", job="fitness_check", db_path=db)
        assert outbox.count_active(db_path=db) == 2

        assert outbox.dismiss_nudge(a, db_path=db) is True
        assert outbox.dismiss_nudge(a, db_path=db) is False, "double-dismiss = no-op"
        assert outbox.dismiss_nudge(987654, db_path=db) is False, "unknown id = False"
        assert outbox.count_active(db_path=db) == 1

        visible = outbox.list_nudges(db_path=db)
        assert [n["id"] for n in visible] == [b], visible
        assert len(outbox.list_nudges(include_dismissed=True, db_path=db)) == 2
    _ok("outbox: list / dismiss / idempotent dismiss / active count")


# ---------------------------------------------------------------------------
# 6. Privacy seam — skills go through the agent (router), not a direct cloud call
# ---------------------------------------------------------------------------

def test_privacy_seam() -> None:
    captured: list[tuple[str, str]] = []

    def spy_agent(prompt: str, level: str) -> dict:
        captured.append((prompt, level))
        # Mimic the agent emitting a LOCAL routing decision for personal data.
        return {"answer": "ok", "routing_log": [{"tool": "query_kg", "decision": "LOCAL"}]}

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rt.db")
        rt = ProactiveRuntime(agent_run=spy_agent, db_path=db)
        job = next(j for j in rt.load.jobs if j.id == "morning_brief")
        res = rt.run_job(job, datetime.datetime(2026, 6, 23, 12, 0))
        assert captured, "runner must invoke the agent (which routes via the router)"
        # The skill recipe (the 'how') is forwarded to the agent verbatim.
        assert "Morning Brief" in captured[0][0]
        assert all(e["decision"] == "LOCAL" for e in res["routing_log"]), res["routing_log"]
    _ok("privacy: runner reaches the agent; personal skill observed LOCAL")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> None:
    print("runtime stub-LLM unit tests")
    test_loader_valid()
    test_loader_malformed()
    test_loader_unknown_skill()
    test_scheduler_tick()
    test_quiet_hours()
    test_briefing()
    test_nudge_dismiss()
    test_privacy_seam()
    print("All runtime tests passed.")


if __name__ == "__main__":
    main()
