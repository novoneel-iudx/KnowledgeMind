"""
runtime/cron.py
---------------
A tiny, dependency-free cron matcher — just enough to evaluate the schedules in
``hermes_jobs/*.json`` against an injectable ``datetime``. No external library
(croniter / APScheduler) is pulled in.

Supported per-field syntax (standard 5-field cron: minute hour dom month dow):
  *            any value
  a            a single value
  a,b,c        a list
  a-b          an inclusive range
  */n          every n within the field's full range
  a-b/n        every n within a range

Day-of-week is 0-6 with 0 = Sunday (7 is also accepted as Sunday). The standard
Vixie-cron dom/dow rule is honoured: when BOTH day-of-month and day-of-week are
restricted (neither is ``*``) the match is their OR; otherwise it is a plain AND
across all fields.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

# (name, low, high) for the five fields, in order.
_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day-of-month", 1, 31),
    ("month", 1, 12),
    ("day-of-week", 0, 7),  # 0 and 7 both mean Sunday
)


class CronError(ValueError):
    """Raised when a cron expression is malformed."""


def _parse_field(field: str, lo: int, hi: int, name: str) -> set[int]:
    """Expand one cron field into the explicit set of integers it matches."""
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise CronError(f"empty term in {name} field")

        step = 1
        if "/" in part:
            base, _, step_str = part.partition("/")
            if not step_str.isdigit() or int(step_str) < 1:
                raise CronError(f"invalid step '{step_str}' in {name} field")
            step = int(step_str)
        else:
            base = part

        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            if not (a.isdigit() and b.isdigit()):
                raise CronError(f"invalid range '{base}' in {name} field")
            start, end = int(a), int(b)
        else:
            if not base.isdigit():
                raise CronError(f"invalid value '{base}' in {name} field")
            start = end = int(base)

        if start > end:
            raise CronError(f"range start > end ('{base}') in {name} field")
        if start < lo or end > hi:
            raise CronError(f"value out of range [{lo},{hi}] ('{base}') in {name} field")

        values.update(range(start, end + 1, step))

    return values


def parse_cron(expr: str) -> list[tuple[set[int], bool]]:
    """
    Parse a 5-field cron expression.

    Returns one (allowed_values, is_wildcard) pair per field, in field order.
    Raises CronError on any malformation (clear, field-named messages).
    """
    if not isinstance(expr, str) or not expr.strip():
        raise CronError("schedule is empty")
    tokens = expr.split()
    if len(tokens) != 5:
        raise CronError(
            f"expected 5 cron fields (minute hour dom month dow), got {len(tokens)}: {expr!r}"
        )

    parsed: list[tuple[set[int], bool]] = []
    for token, (name, lo, hi) in zip(tokens, _FIELDS):
        allowed = _parse_field(token, lo, hi, name)
        if name == "day-of-week" and 7 in allowed:
            allowed = (allowed - {7}) | {0}
        parsed.append((allowed, token.strip() == "*"))
    return parsed


def validate_cron(expr: str) -> Optional[str]:
    """Return an error string if `expr` is invalid, else None."""
    try:
        parse_cron(expr)
        return None
    except CronError as err:
        return str(err)


def cron_matches(expr: str, when: _dt.datetime) -> bool:
    """True if `when` (minute resolution) satisfies the cron expression."""
    minute_f, hour_f, dom_f, month_f, dow_f = parse_cron(expr)
    cron_dow = when.isoweekday() % 7  # Mon=1..Sat=6, Sun=0

    if when.minute not in minute_f[0]:
        return False
    if when.hour not in hour_f[0]:
        return False
    if when.month not in month_f[0]:
        return False

    dom_ok = when.day in dom_f[0]
    dow_ok = cron_dow in dow_f[0]
    # Vixie rule: both restricted -> OR; otherwise AND.
    if not dom_f[1] and not dow_f[1]:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 08:00 daily
    assert cron_matches("0 8 * * *", _dt.datetime(2026, 6, 23, 8, 0))
    assert not cron_matches("0 8 * * *", _dt.datetime(2026, 6, 23, 8, 1))
    # every 30 min between 09:00-21:00
    assert cron_matches("*/30 9-21 * * *", _dt.datetime(2026, 6, 23, 9, 30))
    assert cron_matches("*/30 9-21 * * *", _dt.datetime(2026, 6, 23, 21, 0))
    assert not cron_matches("*/30 9-21 * * *", _dt.datetime(2026, 6, 23, 21, 15))
    assert not cron_matches("*/30 9-21 * * *", _dt.datetime(2026, 6, 23, 8, 30))
    # every 3 hours on the hour
    assert cron_matches("0 */3 * * *", _dt.datetime(2026, 6, 23, 3, 0))
    assert not cron_matches("0 */3 * * *", _dt.datetime(2026, 6, 23, 4, 0))
    # malformed
    assert validate_cron("0 8 * *") is not None       # too few fields
    assert validate_cron("99 8 * * *") is not None     # out of range
    assert validate_cron("0 8 * * *") is None          # ok
    print("runtime/cron.py smoke tests passed.")
