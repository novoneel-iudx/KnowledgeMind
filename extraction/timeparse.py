"""
extraction/timeparse.py
-----------------------
Deterministic resolution of a natural-language time expression to an absolute
epoch timestamp, relative to a base time (the message's receipt time).

Why this exists
===============
The few-shot commitment extractor (extraction/prompts.py) returns the *words*
that describe a time ("at 4", "tomorrow morning", "EOD") but emits
``normalized_ts: null`` -- it does not do the date arithmetic. Without this
module, a soft commitment fell back to its *message-receipt* time, so
"see you at 4" sent at 08:40 was stored as 08:40, never 16:00, and could not
overlap a real calendar event. (See SPEC 4.3 / the conflict-detection fix.)

Doing the arithmetic deterministically in Python -- rather than asking the LLM
to compute epochs, which models do unreliably -- keeps it testable and exact.
The parser is intentionally *narrow*: it handles the patterns the extractor
actually produces and returns ``None`` for anything vague ("soon", "next week",
"end of the month"), letting the caller fall back. It is not a general
natural-language date parser and deliberately adds no third-party dependency.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Optional


# Bare hours in this inclusive range with no am/pm and no 24h form are read as
# afternoon/evening (PM): "see you at 4" means 16:00, not 04:00. Social/meeting
# messages almost never mean the small-hours AM reading.
_PM_HEURISTIC_MIN_HOUR: int = 1
_PM_HEURISTIC_MAX_HOUR: int = 7

# Default clock times for fuzzy parts of day / end-of-day shorthands.
_EOD_HOUR: int = 17          # "EOD" / "end of day"
_MORNING_HOUR: int = 9
_AFTERNOON_HOUR: int = 15
_EVENING_HOUR: int = 19
_NIGHT_HOUR: int = 20        # "tonight"

_WEEKDAYS: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _next_weekday(from_date, weekday_index: int):
    """Return the next date (>= from_date) whose weekday is weekday_index.

    "Thursday" said on a Sunday resolves to the coming Thursday. If the named
    day is *today*, today is returned (the time-of-day decides if it is past).
    """
    days_ahead = (weekday_index - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


def _resolve_date(expr: str, base: datetime):
    """Pick the target calendar date from relative-day words in `expr`."""
    if "tomorrow" in expr:
        return (base + timedelta(days=1)).date()
    if "today" in expr or "tonight" in expr:
        return base.date()
    for name, index in _WEEKDAYS.items():
        if name in expr:
            return _next_weekday(base.date(), index)
    # No explicit day word -> assume the base day (e.g. bare "at 4").
    return base.date()


def _resolve_time(expr: str) -> Optional[tuple[int, int]]:
    """Pick (hour, minute) from `expr`, or None if it names no resolvable time."""
    # End-of-day shorthands first (they contain no digits).
    if "eod" in expr or "end of day" in expr or "end of the day" in expr:
        return (_EOD_HOUR, 0)

    # Explicit 24h or h:mm, e.g. "17:30", "12:30".
    match = re.search(r"\b(\d{1,2}):(\d{2})\b", expr)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour, minute)

    # "3pm", "3 pm", "11am".
    match = re.search(r"\b(\d{1,2})\s*(am|pm)\b", expr)
    if match:
        hour = int(match.group(1)) % 12
        if match.group(2) == "pm":
            hour += 12
        return (hour, 0)

    # Bare hour, e.g. "at 4", "by 9". Apply the PM heuristic for small hours.
    match = re.search(r"\b(\d{1,2})\b", expr)
    if match:
        hour = int(match.group(1))
        if 0 <= hour <= 23:
            if _PM_HEURISTIC_MIN_HOUR <= hour <= _PM_HEURISTIC_MAX_HOUR:
                hour += 12
            return (hour, 0)

    # Fuzzy parts of day (no digit present).
    if "tonight" in expr:
        return (_NIGHT_HOUR, 0)
    if "morning" in expr:
        return (_MORNING_HOUR, 0)
    if "afternoon" in expr:
        return (_AFTERNOON_HOUR, 0)
    if "evening" in expr:
        return (_EVENING_HOUR, 0)

    return None


def resolve_timestamp(time_expression: str, base_ts: float) -> Optional[float]:
    """
    Resolve `time_expression` to an absolute epoch (seconds), relative to
    `base_ts` (the message-receipt time used as "now").

    Returns None when the expression names no concrete time we can pin down
    (e.g. "soon", "next week", "end of the month", "") -- the caller should
    fall back to another signal.
    """
    if not time_expression or not time_expression.strip():
        return None
    expr = time_expression.strip().lower()

    resolved_time = _resolve_time(expr)
    if resolved_time is None:
        return None  # no clock anchor -> let the caller fall back

    base = datetime.fromtimestamp(base_ts)
    target_date = _resolve_date(expr, base)
    hour, minute = resolved_time
    return datetime.combine(target_date, time(hour, minute)).timestamp()


# ---------------------------------------------------------------------------
# Smoke test  (run: python -m extraction.timeparse)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Base = the mock Priya message receipt time: 2026-06-22 08:40 (a Monday).
    base_dt = datetime(2026, 6, 22, 8, 40, 0)
    base = base_dt.timestamp()

    def _r(expr: str) -> Optional[datetime]:
        ts = resolve_timestamp(expr, base)
        return datetime.fromtimestamp(ts) if ts is not None else None

    # --- the load-bearing case: "at 4" must land on the SAME day at 16:00 ----
    got = _r("at 4")
    assert got == datetime(2026, 6, 22, 16, 0), f'"at 4" -> {got}, expected 2026-06-22 16:00'
    assert _r("at 4 today") == datetime(2026, 6, 22, 16, 0), '"at 4 today" mismatch'
    print(f'=> "at 4" (base {base_dt:%Y-%m-%d %H:%M}) -> {got:%Y-%m-%d %H:%M}  (PM heuristic, base date)')

    # --- exact few-shot strings from extraction/prompts.py FEW_SHOT_EXAMPLES --
    cases: list[tuple[str, Optional[datetime]]] = [
        ("3pm Tuesday",      datetime(2026, 6, 23, 15, 0)),   # next Tue, 15:00
        ("10 tomorrow",      datetime(2026, 6, 23, 10, 0)),   # tomorrow, 10:00 (>7 -> AM)
        ("17:30",            datetime(2026, 6, 22, 17, 30)),  # same day, 24h
        ("tomorrow morning", datetime(2026, 6, 23, 9, 0)),    # tomorrow, default morning
        ("EOD",              datetime(2026, 6, 22, 17, 0)),   # same day, end-of-day
        ("2pm",              datetime(2026, 6, 22, 14, 0)),   # same day, pm
        ("next week",        None),                            # too vague
        ("end of the month", None),                            # too vague
        ("Thursday",         None),                            # day but no time -> None
        ("soon",             None),
        ("then",             None),
        ("",                 None),
    ]
    for expr, expected in cases:
        got = _r(expr)
        assert got == expected, f'"{expr}" -> {got}, expected {expected}'
        print(f'   {expr!r:20} -> {got}')

    # --- weekday + time resolves (the Lena "Lunch Thursday 12:30" case) ------
    lena = _r("Thursday 12:30")
    assert lena == datetime(2026, 6, 25, 12, 30), f'"Thursday 12:30" -> {lena}'
    print(f'   {"Thursday 12:30"!r:20} -> {lena}  (next Thursday)')

    print("All extraction/timeparse.py smoke tests passed.")
