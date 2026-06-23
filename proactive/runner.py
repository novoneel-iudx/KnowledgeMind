"""
proactive/runner.py
-------------------
Executes a single Hermes job:
  1. Gather signals by calling each listed tool via dispatch_tool()
  2. Ask Groq (or fallback) whether to nudge, using the skill's system prompt
  3. If the decision is to surface, store a nudge via the outbox
  4. Return the nudge dict, or None if the skill decided to stay silent

Tool name mapping (hermes job spec → dispatch_tool):
  km_apple_health_summary → apple_health
  km_strava_summary       → strava
  km_todoist_summary      → todoist
  km_todoist_tasks        → todoist
  km_calendar             → google_calendar
  km_conflict_edges       → conflict_edges
  km_spotify_mood         → spotify
  km_query_kg             → query_kg
"""

from __future__ import annotations

import json
import time
from typing import Optional

from proactive.loader import load_jobs, load_skill

# ---------------------------------------------------------------------------
# Tool name mapping
# ---------------------------------------------------------------------------

_KM_TOOL_MAP: dict[str, tuple[str, dict]] = {
    "km_apple_health_summary": ("apple_health", {}),
    "km_strava_summary":       ("strava", {}),
    "km_todoist_summary":      ("todoist", {}),
    "km_todoist_tasks":        ("todoist", {"filter": "today | overdue"}),
    "km_calendar":             ("google_calendar", {"action": "list_events"}),
    "km_conflict_edges":       ("conflict_edges", {}),
    "km_spotify_mood":         ("spotify", {}),
    "km_query_kg":             ("query_kg", {"query": "recent activity and commitments"}),
}

# Sentinel that means "stay silent — do not store a nudge"
_SILENT_SENTINELS = ('{"surface": false}', '{"surface":false}')

_NUDGE_MODEL = "llama-3.3-70b-versatile"   # richer generation than the fast judge model


# ---------------------------------------------------------------------------
# Signal gathering — calls tools directly to avoid agent.tools' heavy imports
# ---------------------------------------------------------------------------

def _call_tool_direct(km_name: str, params: dict) -> dict:
    """
    Call a tool without going through agent/tools.py (which imports all
    optional connectors at module level). Falls back gracefully on any error.
    """
    try:
        if km_name in ("km_apple_health_summary",):
            from hermes_tools.apple_health_tool import apple_health_summary
            return apple_health_summary()

        if km_name in ("km_strava_summary",):
            from hermes_tools.strava_tool import strava_summary
            return strava_summary()

        if km_name in ("km_todoist_summary", "km_todoist_tasks"):
            from hermes_tools.todoist_tool import todoist_summary, todoist_tasks
            if km_name == "km_todoist_tasks":
                return todoist_tasks(params.get("filter", "today | overdue"))
            return todoist_summary()

        if km_name in ("km_spotify_mood",):
            from hermes_tools.spotify_tool import spotify_mood
            return spotify_mood()

        if km_name in ("km_conflict_edges",):
            from config.store import get_config
            from kg.schema import get_db_connection
            from kg.graph import find_conflicts
            cfg = get_config()
            conn = get_db_connection(cfg.db_path)
            try:
                conflicts = find_conflicts(conn, window_hours=24.0)
                return {
                    "success": True,
                    "conflicts": [
                        {"a": c.commitment_a.description, "b": c.commitment_b.description,
                         "overlap_minutes": c.overlap_minutes}
                        for c in conflicts
                    ],
                    "summary": f"{len(conflicts)} conflict(s) in next 24h",
                }
            finally:
                conn.close()

        if km_name in ("km_query_kg",):
            from config.store import get_config
            from kg.schema import get_db_connection
            cfg = get_config()
            conn = get_db_connection(cfg.db_path)
            try:
                rows = conn.execute(
                    """SELECT description, commitment_type, start_ts
                       FROM commitments WHERE status = 'active'
                       ORDER BY start_ts ASC LIMIT 10"""
                ).fetchall()
                items = [dict(r) for r in rows]
                return {"success": True, "commitments": items,
                        "summary": f"{len(items)} active commitment(s)"}
            finally:
                conn.close()

        if km_name in ("km_calendar",):
            from connectors.mock import MockCalendarSource
            events = MockCalendarSource().get_events()
            return {"success": True, "events": events[:5],
                    "summary": f"{len(events)} upcoming event(s)"}

    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": False, "error": f"unmapped tool: {km_name}"}


def _gather_signals(tool_names: list[str]) -> dict[str, dict]:
    """Call each tool and return {km_tool_name: result_dict}."""
    signals: dict[str, dict] = {}
    for km_name in tool_names:
        entry = _KM_TOOL_MAP.get(km_name)
        params = entry[1] if entry else {}
        signals[km_name] = _call_tool_direct(km_name, params)
    return signals


def _signals_to_text(signals: dict[str, dict]) -> str:
    """Convert signal dict into a human-readable block for the LLM prompt."""
    lines = []
    for km_name, data in signals.items():
        label = km_name.replace("km_", "").replace("_", " ").title()
        if not data.get("success", True):
            lines.append(f"[{label}] unavailable")
        else:
            summary = data.get("summary") or data.get("formatted") or json.dumps(data, indent=2)[:300]
            lines.append(f"[{label}] {summary}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Nudge generation: Groq or fallback
# ---------------------------------------------------------------------------

def _call_groq(skill_content: str, signals_text: str, job_prompt: str) -> Optional[str]:
    """
    Ask Groq to generate a nudge (or return {"surface": false}).
    Returns the raw text response, or None on failure.
    """
    from config.store import get_config
    cfg = get_config()
    if not cfg.groq_api_key:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=cfg.groq_api_key)
        user_content = (
            f"Current signals:\n{signals_text}\n\n"
            f"Task: {job_prompt}\n\n"
            "Reply with your nudge, or exactly {\"surface\": false} if no nudge is warranted."
        )
        resp = client.chat.completions.create(
            model=_NUDGE_MODEL,
            messages=[
                {"role": "system", "content": skill_content or "You are a helpful personal AI."},
                {"role": "user", "content": user_content},
            ],
            max_tokens=300,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[runner] Groq call failed: {e}")
        return None


def _fallback_nudge(job: dict, signals: dict[str, dict]) -> Optional[str]:
    """
    Template-based nudge when Groq is unavailable. Derives a simple message
    from the most informative signal. Returns None if nothing warrants surfacing.
    """
    job_name = job.get("name", "")
    parts: list[str] = []

    health = signals.get("km_apple_health_summary", {})
    strava = signals.get("km_strava_summary", {})
    todoist = signals.get("km_todoist_summary", {})
    conflicts = signals.get("km_conflict_edges", {})

    if "morning_brief" in job_name or "evening_tasks" in job_name:
        overdue = todoist.get("overdue_count", 0)
        due_today = todoist.get("due_today_count", 0)
        if overdue:
            parts.append(f"{overdue} overdue task(s)")
        if due_today:
            parts.append(f"{due_today} task(s) due today")
        gap = strava.get("gap_threshold_exceeded")
        sleep_q = health.get("sleep_quality", "unknown")
        if gap and sleep_q in ("good", "fair"):
            days = strava.get("days_since_last_activity", "?")
            parts.append(f"{days} days since last activity — recovery looks fine")
        elif gap and sleep_q == "poor":
            parts.append("Activity gap detected but sleep was poor — consider rest")
        conflict_list = conflicts.get("conflicts") or []
        if conflict_list:
            parts.append(f"{len(conflict_list)} scheduling conflict(s) detected")
        if not parts:
            return None  # {"surface": false}

    elif "fitness" in job_name:
        gap = strava.get("gap_threshold_exceeded")
        recovery = health.get("recovery_status", "unknown")
        sleep_q = health.get("sleep_quality", "unknown")
        if not gap:
            return None
        days = strava.get("days_since_last_activity", "?")
        if recovery == "good" and sleep_q != "poor":
            parts.append(f"{days} days since last run — recovery looks fine for activity")
        elif recovery == "low" or sleep_q == "poor":
            parts.append(f"{days} days since last run, but recovery suggests rest today")
        else:
            parts.append(f"{days} days since last activity")

    elif "mood" in job_name:
        spotify = signals.get("km_spotify_mood", {})
        mood = spotify.get("mood", "neutral")
        deep_work = spotify.get("deep_work_session", False)
        days_inactive = strava.get("days_since_last_activity", 0)
        if deep_work:
            parts.append("Flow state detected — check your upcoming calendar")
        elif mood == "melancholic" and days_inactive >= 2:
            parts.append(f"Low energy + {days_inactive} days since last activity — a short walk might help")
        else:
            return None

    if not parts:
        return None

    return "• " + "\n• ".join(parts)


# ---------------------------------------------------------------------------
# Main job runner
# ---------------------------------------------------------------------------

def run_job(job: dict, dry_run: bool = False) -> Optional[dict]:
    """
    Execute a single Hermes job.

    Returns a nudge dict {job_name, skill, message, signals, generated_at},
    or None if the skill decided to stay silent.
    Does NOT write to the DB if dry_run=True.
    """
    job_name = job["name"]
    skill_name = job.get("skill", "")
    tool_names: list[str] = job.get("tools", [])
    job_prompt: str = job.get("prompt", "")

    skill_content = load_skill(skill_name)
    signals = _gather_signals(tool_names)
    signals_text = _signals_to_text(signals)

    # Try Groq first, then fallback
    raw_response = _call_groq(skill_content, signals_text, job_prompt)

    if raw_response is not None:
        # Check for silent sentinel
        stripped = raw_response.strip()
        if any(s in stripped for s in _SILENT_SENTINELS):
            return None
        message = stripped
    else:
        # Template fallback
        fallback = _fallback_nudge(job, signals)
        if fallback is None:
            return None
        message = fallback

    nudge = {
        "job_name": job_name,
        "skill": skill_name,
        "message": message,
        "signals": signals,
        "generated_at": time.time(),
    }

    if not dry_run:
        from config.store import get_config
        from kg.schema import get_db_connection
        from proactive.outbox import store_nudge
        cfg = get_config()
        conn = get_db_connection(cfg.db_path)
        try:
            nudge["id"] = store_nudge(conn, job_name, skill_name, message, signals)
        finally:
            conn.close()

    return nudge


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a single Hermes job manually.")
    parser.add_argument("--job", required=True,
                        help="Job name (e.g. morning_brief, fitness_check)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Gather signals and generate nudge but do not store.")
    args = parser.parse_args()

    jobs = {j["name"]: j for j in load_jobs()}
    if args.job not in jobs:
        print(f"Unknown job '{args.job}'. Available: {list(jobs)}")
        raise SystemExit(1)

    print(f"Running job: {args.job} (dry_run={args.dry_run})")
    nudge = run_job(jobs[args.job], dry_run=args.dry_run)
    if nudge is None:
        print("=> silent (skill decided not to surface)")
    else:
        print(f"=> nudge generated (id={nudge.get('id', 'unsaved')}):")
        print(nudge["message"])
