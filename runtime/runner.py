"""
runtime/runner.py
-----------------
FR-2.2 — Runner / scheduler.

Fires due jobs, runs each skill **through the agent** (which routes every step via
the privacy router — the runner never calls an LLM directly), and writes the
result to the nudge outbox. Quiet hours suppress delivery (the nudge is still
queued, just flagged ``suppressed``).

Two seams keep it fully offline-testable:
  * the clock is injectable — pass ``now`` to :func:`ProactiveRuntime.run_due_jobs`
    / :func:`run_job`, or a ``get_now`` callable to the loop;
  * the agent is injectable — pass ``agent_run(prompt, level) -> run_dict``. The
    default uses the real ``HybridMindAgent`` (→ router); tests inject a stub LLM.

Privacy invariant (SPEC §3): every skill goes through ``agent.run()``. There is no
direct cloud call here, so personal-data skills route LOCAL exactly as on-request
chats do (observable in each result's ``routing_log``).
"""

from __future__ import annotations

import asyncio
import datetime
import json
from typing import Any, Callable, Optional

from config.store import AppConfig, get_config
from runtime import outbox
from runtime.cron import cron_matches
from runtime.loader import (
    Job,
    LoadResult,
    SILENT_SENTINEL,
    load_runtime,
)

# agent_run(prompt, level) -> run() dict (Contract 2: must contain "answer").
AgentRun = Callable[[str, str], dict]

DEFAULT_TICK_SECONDS = 60


# ---------------------------------------------------------------------------
# Quiet hours + due check (pure, injectable clock)
# ---------------------------------------------------------------------------

def in_quiet_hours(now: datetime.datetime, cfg: Optional[AppConfig] = None) -> bool:
    """True if `now` falls inside [quiet_start, quiet_end), handling midnight wrap."""
    cfg = cfg or get_config()
    start = int(cfg.preemptive_quiet_hours_start)
    end = int(cfg.preemptive_quiet_hours_end)
    hour = now.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight (e.g. 22 -> 8)


def is_due(job: Job, now: datetime.datetime) -> bool:
    """True if the job's cron schedule matches `now` (minute resolution)."""
    return cron_matches(job.schedule, now)


# ---------------------------------------------------------------------------
# Skill output handling
# ---------------------------------------------------------------------------

def _is_silent(answer: str) -> bool:
    """A skill stays silent by emitting {"surface": false} (recipe convention)."""
    if not answer or not answer.strip():
        return True
    # Match the sentinel regardless of whitespace/quote style.
    normalised = answer.replace("'", '"').replace(" ", "")
    if SILENT_SENTINEL.replace(" ", "") in normalised:
        return True
    # Tolerate a bare JSON object {"surface": false}.
    try:
        obj = json.loads(answer.strip())
        if isinstance(obj, dict) and obj.get("surface") is False:
            return True
    except (json.JSONDecodeError, ValueError):
        pass
    return False


def _build_prompt(job: Job, recipe: str) -> str:
    """Compose the skill prompt: recipe (the 'how') + the job's instruction."""
    instruction = job.prompt.strip()
    parts = [recipe.strip()]
    if instruction:
        parts.append(f"## This run's instruction\n{instruction}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Default agent_run — the real, router-backed path
# ---------------------------------------------------------------------------

def default_agent_run(prompt: str, level: str) -> dict:
    """Run a skill prompt through the real agent (lazy import; goes via router)."""
    from agent.orchestrator import HybridMindAgent, AgencyLevel
    try:
        agency = AgencyLevel[level.upper()]
    except (KeyError, AttributeError):
        agency = AgencyLevel.L2_WORKFLOW
    return HybridMindAgent(session_id="proactive").run(prompt, agency)


# ---------------------------------------------------------------------------
# Demo agent_run — offline, no-key fallback (mirrors api/main.py demo mode)
# ---------------------------------------------------------------------------

def _recipe_title(prompt: str) -> str:
    """First markdown heading of the skill recipe (identifies the skill)."""
    for line in prompt.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def demo_agent_run(prompt: str, level: str) -> dict:
    """
    Deterministic, LLM-free skill runner used when no Groq key is configured, so
    the proactive demo still produces nudges offline. The Morning Brief reuses the
    real :func:`runtime.briefing.compose_briefing`; other skills return a canned,
    clearly-illustrative line (or stay silent).
    """
    title = _recipe_title(prompt)
    log = [{"tool": "demo", "decision": "LOCAL"}]
    if title == "Morning Brief":
        from runtime.briefing import compose_briefing
        digest = compose_briefing()
        answer = digest["formatted"] if digest["surface"] else '{"surface": false}'
        return {"answer": answer, "routing_log": log, "agency_level": level, "demo_mode": True}

    canned = {
        "Task Manager": "Evening check: 3 tasks still open today. Want to reschedule any?",
        "Fitness Coach": "No workout logged today and recovery looks fine — a short walk would help.",
        "Mood": '{"surface": false}',
        "Communication": '{"surface": false}',
    }
    return {"answer": canned.get(title, '{"surface": false}'),
            "routing_log": log, "agency_level": level, "demo_mode": True}


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class ProactiveRuntime:
    """Loads jobs/skills and fires them on schedule into the nudge outbox."""

    def __init__(
        self,
        agent_run: Optional[AgentRun] = None,
        *,
        jobs_dir=None,
        skills_dir=None,
        db_path: Optional[str] = None,
        get_now: Optional[Callable[[], datetime.datetime]] = None,
        tick_seconds: int = DEFAULT_TICK_SECONDS,
    ) -> None:
        self.agent_run: AgentRun = agent_run or default_agent_run
        self.db_path = db_path
        self.get_now = get_now or datetime.datetime.now
        self.tick_seconds = tick_seconds
        self._jobs_dir = jobs_dir
        self._skills_dir = skills_dir

        self.load: LoadResult = LoadResult()
        # Dedupe: job id -> "YYYY-MM-DD HH:MM" of last fire (avoid re-firing a
        # matching minute if the loop ticks more than once inside it).
        self._last_fired: dict[str, str] = {}
        self._task: Optional[asyncio.Task] = None
        self.reload()

    # -- loading -------------------------------------------------------------

    def reload(self) -> LoadResult:
        kwargs: dict[str, Any] = {}
        if self._jobs_dir is not None:
            kwargs["jobs_dir"] = self._jobs_dir
        if self._skills_dir is not None:
            kwargs["skills_dir"] = self._skills_dir
        self.load = load_runtime(**kwargs)
        return self.load

    def catalog(self) -> list[dict]:
        """Job catalog for the UI: schedule + last/next run hints."""
        out = []
        for job in self.load.jobs:
            out.append({
                "id": job.id,
                "skill": job.skill,
                "schedule": job.schedule,
                "agency_level": job.agency_level,
                "quiet_hours_aware": job.quiet_hours_aware,
                "last_fired": self._last_fired.get(job.id),
            })
        return out

    # -- execution -----------------------------------------------------------

    def run_job(self, job: Job, now: Optional[datetime.datetime] = None) -> dict:
        """
        Force-run a single job's skill now (ignores the due check). Returns a
        result describing what happened, including the agent's routing_log so the
        UI can render the agent-activity panel.
        """
        now = now or self.get_now()
        skill = self.load.skills.get(job.skill)
        result: dict[str, Any] = {
            "job": job.id,
            "skill": job.skill,
            "agency_level": job.agency_level,
            "fired": True,
            "surfaced": False,
            "suppressed": False,
            "nudge_id": None,
            "ts": now.isoformat(timespec="seconds"),
        }
        if skill is None:
            result.update(fired=False, error=f"skill {job.skill!r} not loaded")
            return result

        prompt = _build_prompt(job, skill.recipe)
        try:
            run_dict = self.agent_run(prompt, job.agency_level)
        except Exception as err:  # noqa: BLE001 — a failing skill must not kill the loop
            result.update(error=f"agent run failed: {err}")
            return result

        answer = str(run_dict.get("answer", "")).strip()
        # Pass through the agent's trace (additive consumption of Contract 2).
        result["routing_log"] = run_dict.get("routing_log", [])
        result["token_summary"] = run_dict.get("token_summary")
        result["answer"] = answer

        if _is_silent(answer):
            result["reason"] = "skill chose to stay silent"
            return result

        quiet = job.quiet_hours_aware and in_quiet_hours(now)
        nudge_id = outbox.add_nudge(
            answer,
            skill=job.skill,
            job=job.id,
            suppressed=quiet,
            ts=now.timestamp(),
            db_path=self.db_path,
        )
        result.update(nudge_id=nudge_id, suppressed=quiet, surfaced=not quiet)
        return result

    def run_due_jobs(
        self,
        now: Optional[datetime.datetime] = None,
        *,
        force: bool = False,
    ) -> list[dict]:
        """
        Fire every job due at `now`. The injectable `now` makes this the unit-test
        entry point. `force=True` runs all jobs regardless of schedule (manual
        'run now' from the UI / tick endpoint).
        """
        now = now or self.get_now()
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        fired: list[dict] = []
        for job in self.load.jobs:
            if not force:
                if not is_due(job, now):
                    continue
                if self._last_fired.get(job.id) == minute_key:
                    continue  # already fired this minute
            self._last_fired[job.id] = minute_key
            fired.append(self.run_job(job, now))
        return fired

    # -- background loop (gated; started from the FastAPI lifespan) ----------

    async def _loop(self) -> None:
        while True:
            try:
                self.run_due_jobs(self.get_now())
            except Exception as err:  # noqa: BLE001 — keep the loop alive
                print(f"[ProactiveRuntime] tick error: {err}")
            await asyncio.sleep(self.tick_seconds)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            print(f"[ProactiveRuntime] scheduler started ({len(self.load.jobs)} jobs, "
                  f"tick {self.tick_seconds}s)")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            print("[ProactiveRuntime] scheduler stopped")
        self._task = None


# ---------------------------------------------------------------------------
# Smoke test (offline stub agent, fixed clock)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    # Stub agent: returns a fixed answer without touching any LLM.
    def stub_agent(prompt: str, level: str) -> dict:
        return {"answer": "Heads up: 3 tasks due today.",
                "routing_log": [{"tool": "todoist", "decision": "LOCAL"}],
                "agency_level": level}

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "runner.db")
        rt = ProactiveRuntime(agent_run=stub_agent, db_path=db)
        assert rt.load.ok, rt.load.errors

        # 08:00 -> morning_brief is due (and 08:00 is the quiet-hours boundary =
        # delivered, since quiet hours end at 08:00).
        morning = datetime.datetime(2026, 6, 23, 8, 0)
        fired = rt.run_due_jobs(morning)
        ids = [f["job"] for f in fired]
        assert "morning_brief" in ids, ids
        brief = next(f for f in fired if f["job"] == "morning_brief")
        assert brief["surfaced"] and brief["nudge_id"], brief

        # Re-running the same minute does not double-fire.
        assert rt.run_due_jobs(morning) == []

        # Quiet hours (23:00): force-run a quiet-aware job -> queued, suppressed.
        night = datetime.datetime(2026, 6, 23, 23, 0)
        res = rt.run_job(rt.load.jobs[0], night)
        assert res["suppressed"] and not res["surfaced"], res

        delivered = outbox.list_nudges(include_suppressed=False, db_path=db)
        assert all(not n["suppressed"] for n in delivered)
        print(f"Fired morning jobs: {ids}")
        print("runtime/runner.py smoke tests passed.")
