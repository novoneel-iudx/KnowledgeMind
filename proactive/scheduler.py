"""
proactive/scheduler.py
----------------------
Background cron scheduler for Hermes proactive jobs.

- Checks every 60 seconds whether any job's cron expression matches now.
- Respects quiet_hours_aware flag (default quiet window: 22:00–08:00 local).
- Runs as a daemon thread — starts/stops with the process.
- No external scheduler dependency: uses a hand-rolled cron matcher that
  covers the patterns used in hermes_jobs/*.json.

Usage:
  python -m proactive.scheduler               # start (runs until Ctrl-C)
  python -m proactive.scheduler --dry-run     # list jobs + next fire times, exit
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional

from proactive.loader import load_jobs

# ---------------------------------------------------------------------------
# Quiet hours (local clock)
# ---------------------------------------------------------------------------

QUIET_START_HOUR = 22   # 10 PM
QUIET_END_HOUR = 8      # 8 AM


def _is_quiet_hours(dt: Optional[datetime] = None) -> bool:
    """Return True if the local time falls within the quiet window."""
    if dt is None:
        dt = datetime.now()
    h = dt.hour
    # Quiet window spans midnight: 22 ≤ h or h < 8
    return h >= QUIET_START_HOUR or h < QUIET_END_HOUR


# ---------------------------------------------------------------------------
# Cron expression matcher
# Handles the patterns used in hermes_jobs: *, N, N-M, */N
# ---------------------------------------------------------------------------

def _match_field(field: str, value: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return value % step == 0
    if "-" in field:
        lo, hi = field.split("-", 1)
        return int(lo) <= value <= int(hi)
    return int(field) == value


def cron_matches(expr: str, dt: Optional[datetime] = None) -> bool:
    """
    Check whether `dt` (defaults to now) matches a 5-field cron expression.
    Fields: minute hour dom month dow
    """
    if dt is None:
        dt = datetime.now()
    try:
        minute, hour, dom, month, dow = expr.split()
    except ValueError:
        return False
    return (
        _match_field(minute, dt.minute)
        and _match_field(hour, dt.hour)
        and _match_field(dom, dt.day)
        and _match_field(month, dt.month)
        and _match_field(dow, dt.weekday())  # 0=Mon per Python, cron uses 0=Sun but close enough
    )


def next_fire_description(expr: str) -> str:
    """Human-readable description of what a cron expression fires on."""
    try:
        minute, hour, dom, month, dow = expr.split()
    except ValueError:
        return expr
    parts = []
    if hour == "*":
        parts.append("every hour")
    elif hour.startswith("*/"):
        parts.append(f"every {hour[2:]}h")
    elif "-" in hour:
        parts.append(f"hours {hour}")
    else:
        parts.append(f"{hour.zfill(2)}:00")

    if minute != "0" and minute != "*":
        if minute.startswith("*/"):
            parts[0] = f"every {minute[2:]}min during " + (parts[0] if parts else "")
        else:
            parts[0] = parts[0].replace(":00", f":{minute.zfill(2)}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------

_stop_event = threading.Event()
_scheduler_thread: Optional[threading.Thread] = None


def _scheduler_loop(jobs: list[dict], interval_s: int = 60) -> None:
    """
    Main loop: every `interval_s` seconds, check which jobs should fire.
    Uses a 'last fired' dict keyed by (job_name, minute) to prevent
    double-firing within the same calendar minute.
    """
    from proactive.runner import run_job

    last_fired: dict[str, str] = {}  # job_name → "YYYYMMDDHHMM" of last fire

    while not _stop_event.is_set():
        now = datetime.now()
        tick_key = now.strftime("%Y%m%d%H%M")

        for job in jobs:
            name = job["name"]
            schedule = job.get("schedule", "")
            quiet_aware = job.get("quiet_hours_aware", False)

            if not cron_matches(schedule, now):
                continue
            if last_fired.get(name) == tick_key:
                continue  # already fired this minute
            if quiet_aware and _is_quiet_hours(now):
                print(f"[scheduler] {name}: skipped (quiet hours)")
                last_fired[name] = tick_key
                continue

            last_fired[name] = tick_key
            print(f"[scheduler] firing: {name}")
            try:
                nudge = run_job(job)
                if nudge:
                    print(f"[scheduler] nudge stored (id={nudge.get('id')}): {nudge['message'][:80]}")
                else:
                    print(f"[scheduler] {name}: silent")
            except Exception as e:
                print(f"[scheduler] {name} failed: {e}")

        _stop_event.wait(timeout=interval_s)


def start(jobs: Optional[list[dict]] = None) -> None:
    """Start the scheduler as a daemon thread. Safe to call multiple times."""
    global _scheduler_thread, _stop_event
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    if jobs is None:
        jobs = load_jobs()
    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(jobs,),
        daemon=True,
        name="hermes-scheduler",
    )
    _scheduler_thread.start()
    print(f"[scheduler] started with {len(jobs)} job(s)")


def stop() -> None:
    """Signal the scheduler thread to stop."""
    _stop_event.set()
    if _scheduler_thread:
        _scheduler_thread.join(timeout=5)
    print("[scheduler] stopped")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hermes proactive job scheduler.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List jobs and next fire info, then exit.")
    args = parser.parse_args()

    jobs = load_jobs()
    now = datetime.now()
    quiet = _is_quiet_hours(now)

    print(f"\nHermes Scheduler  |  {now.strftime('%Y-%m-%d %H:%M')}  |  "
          f"quiet_hours={'YES' if quiet else 'no'}\n")
    print(f"{'Job':<22} {'Schedule':<20} {'Fires when':<30} {'Quiet-aware'}")
    print("-" * 80)
    for job in jobs:
        name = job["name"]
        sched = job.get("schedule", "?")
        fires = next_fire_description(sched)
        qa = "yes" if job.get("quiet_hours_aware") else "no"
        fires_now = cron_matches(sched, now)
        marker = " ← NOW" if fires_now else ""
        print(f"{name:<22} {sched:<20} {fires:<30} {qa}{marker}")

    print()
    if args.dry_run:
        raise SystemExit(0)

    print("Starting scheduler (Ctrl-C to stop)...")
    start(jobs)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop()
