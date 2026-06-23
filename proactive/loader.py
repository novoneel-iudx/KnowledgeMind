"""
proactive/loader.py
-------------------
Reads hermes_jobs/*.json and hermes_skills/*.md from the project root.
Returns plain dicts/strings — no side effects.
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_JOBS_DIR = _ROOT / "hermes_jobs"
_SKILLS_DIR = _ROOT / "hermes_skills"


def load_jobs() -> list[dict]:
    """Return all valid job specs from hermes_jobs/*.json, sorted by name."""
    jobs = []
    for f in sorted(_JOBS_DIR.glob("job_*.json")):
        try:
            jobs.append(json.loads(f.read_text()))
        except Exception as e:
            print(f"[loader] skipping {f.name}: {e}")
    return jobs


def load_skill(skill_name: str) -> str:
    """Return the markdown content of a skill, or '' if not found."""
    path = _SKILLS_DIR / f"{skill_name}.md"
    if path.exists():
        return path.read_text()
    return ""
