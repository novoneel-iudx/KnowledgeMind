"""
runtime/loader.py
-----------------
FR-2.1 — Job + skill loader.

Parses ``hermes_jobs/*.json`` (the schedule + which skill to run) and
``hermes_skills/*.md`` (the skill recipe / prompt) into validated dataclasses.
Malformed specs produce clear, file-named errors instead of crashing the runtime.

Job JSON schema (additive — extra keys are ignored, so the existing files load
unchanged):
    {
      "id" | "name":      str    (required) — unique job id
      "schedule":         str    (required) — 5-field cron expression
      "skill":            str    (required) — basename of a hermes_skills/*.md file
      "agency_level":     str    (optional) — "L1" | "L2" | "L3"  (default "L2")
      "prompt":           str    (optional) — extra instruction appended to the recipe
      "tools":            [str]  (optional) — advisory; the agent picks tools itself
      "platform":         str    (optional) — delivery hint (in-app outbox regardless)
      "quiet_hours_aware":bool   (optional, default True) — obey quiet hours
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from runtime.cron import validate_cron

# Repo-root defaults.
_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOBS_DIR = _ROOT / "hermes_jobs"
DEFAULT_SKILLS_DIR = _ROOT / "hermes_skills"

_VALID_LEVELS = {"L1", "L2", "L3"}
DEFAULT_AGENCY_LEVEL = "L2"

# Convention shared with the skill recipes: a skill emits this to stay silent.
SILENT_SENTINEL = '"surface": false'


class SpecError(ValueError):
    """A job or skill spec failed validation. Message names the offending file."""


@dataclass
class Skill:
    """A skill recipe loaded from a hermes_skills/*.md file."""
    name: str
    recipe: str
    path: str


@dataclass
class Job:
    """A scheduled job loaded from a hermes_jobs/*.json file."""
    id: str
    schedule: str
    skill: str
    agency_level: str = DEFAULT_AGENCY_LEVEL
    prompt: str = ""
    tools: list[str] = field(default_factory=list)
    platform: str = "in_app"
    quiet_hours_aware: bool = True
    path: str = ""


@dataclass
class LoadResult:
    """Outcome of loading the runtime: validated jobs/skills + per-file errors."""
    jobs: list[Job] = field(default_factory=list)
    skills: dict[str, Skill] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

def load_skill_file(path: Path) -> Skill:
    """Load and validate a single skill .md file. Raises SpecError on failure."""
    try:
        recipe = path.read_text(encoding="utf-8")
    except OSError as err:
        raise SpecError(f"{path.name}: cannot read skill file ({err})") from err
    if not recipe.strip():
        raise SpecError(f"{path.name}: skill recipe is empty")
    return Skill(name=path.stem, recipe=recipe, path=str(path))


def load_skills(skills_dir: Path = DEFAULT_SKILLS_DIR) -> tuple[dict[str, Skill], list[str]]:
    """Load every *.md in `skills_dir`. Returns (skills_by_name, errors)."""
    skills: dict[str, Skill] = {}
    errors: list[str] = []
    if not skills_dir.exists():
        return skills, [f"skills dir not found: {skills_dir}"]
    for path in sorted(skills_dir.glob("*.md")):
        try:
            skill = load_skill_file(path)
            skills[skill.name] = skill
        except SpecError as err:
            errors.append(str(err))
    return skills, errors


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def load_job_file(path: Path) -> Job:
    """Load and validate a single job .json file. Raises SpecError on failure."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise SpecError(f"{path.name}: invalid JSON ({err})") from err
    except OSError as err:
        raise SpecError(f"{path.name}: cannot read job file ({err})") from err

    if not isinstance(raw, dict):
        raise SpecError(f"{path.name}: job spec must be a JSON object")

    job_id = (raw.get("id") or raw.get("name") or "").strip()
    if not job_id:
        raise SpecError(f"{path.name}: missing required field 'id' (or 'name')")

    schedule = (raw.get("schedule") or "").strip()
    if not schedule:
        raise SpecError(f"{path.name}: missing required field 'schedule'")
    cron_err = validate_cron(schedule)
    if cron_err:
        raise SpecError(f"{path.name}: bad schedule — {cron_err}")

    skill = (raw.get("skill") or "").strip()
    if not skill:
        raise SpecError(f"{path.name}: missing required field 'skill'")

    level = str(raw.get("agency_level") or DEFAULT_AGENCY_LEVEL).upper()
    if level not in _VALID_LEVELS:
        raise SpecError(
            f"{path.name}: invalid agency_level {level!r} (expected one of {sorted(_VALID_LEVELS)})"
        )

    tools = raw.get("tools") or []
    if not isinstance(tools, list):
        raise SpecError(f"{path.name}: 'tools' must be a list")

    return Job(
        id=job_id,
        schedule=schedule,
        skill=skill,
        agency_level=level,
        prompt=str(raw.get("prompt") or ""),
        tools=[str(t) for t in tools],
        platform=str(raw.get("platform") or "in_app"),
        quiet_hours_aware=bool(raw.get("quiet_hours_aware", True)),
        path=str(path),
    )


def load_jobs(jobs_dir: Path = DEFAULT_JOBS_DIR) -> tuple[list[Job], list[str]]:
    """Load every *.json in `jobs_dir`. Returns (jobs, errors). Ids must be unique."""
    jobs: list[Job] = []
    errors: list[str] = []
    if not jobs_dir.exists():
        return jobs, [f"jobs dir not found: {jobs_dir}"]
    seen: set[str] = set()
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            job = load_job_file(path)
            if job.id in seen:
                errors.append(f"{path.name}: duplicate job id {job.id!r}")
                continue
            seen.add(job.id)
            jobs.append(job)
        except SpecError as err:
            errors.append(str(err))
    return jobs, errors


# ---------------------------------------------------------------------------
# Combined load + skill linkage
# ---------------------------------------------------------------------------

def load_runtime(
    jobs_dir: Path = DEFAULT_JOBS_DIR,
    skills_dir: Path = DEFAULT_SKILLS_DIR,
) -> LoadResult:
    """
    Load all jobs + skills and verify every job references a known skill.

    Errors are collected (not raised) so one malformed file never blocks the
    rest. A job whose skill is missing is reported and dropped.
    """
    skills, skill_errors = load_skills(skills_dir)
    jobs, job_errors = load_jobs(jobs_dir)

    linked: list[Job] = []
    link_errors: list[str] = []
    for job in jobs:
        if job.skill not in skills:
            link_errors.append(
                f"{Path(job.path).name}: references unknown skill {job.skill!r} "
                f"(known: {sorted(skills) or 'none'})"
            )
            continue
        linked.append(job)

    return LoadResult(
        jobs=linked,
        skills=skills,
        errors=skill_errors + job_errors + link_errors,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = load_runtime()
    print(f"Loaded {len(result.jobs)} jobs, {len(result.skills)} skills.")
    for job in result.jobs:
        print(f"  - {job.id:24s} {job.schedule:16s} -> {job.skill} ({job.agency_level})")
    if result.errors:
        print("Errors:")
        for err in result.errors:
            print(f"  ! {err}")
    else:
        print("No errors — all specs valid.")
