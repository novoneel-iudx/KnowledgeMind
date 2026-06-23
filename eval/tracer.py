"""
eval/tracer.py
--------------
Wraps agent run() output into canonical trace records and persists them.

Trace schema (Contract 2 extension — additive only):
  trace_id        uuid4
  ts              ISO-8601
  golden_id       str | None   — set when run against the golden set
  input           str
  answer          str
  routing_log     list[dict]   — from run() verbatim
  token_summary   dict         — flattened TokenSummary (nulls → 0)
  agency_level    str
  elapsed         float        — seconds
  session_id      str
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

_TRACES_DIR = Path(__file__).parent / "traces"


def _serialise_token_summary(summary) -> dict:
    """Handle TokenSummary dataclass, plain dict, or None."""
    if summary is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "local_tokens": 0, "cloud_tokens": 0}
    if dataclasses.is_dataclass(summary) and not isinstance(summary, type):
        d = dataclasses.asdict(summary)
        # TokenSummary uses total_prompt_tokens / total_completion_tokens
        return {
            "prompt_tokens": d.get("total_prompt_tokens", 0),
            "completion_tokens": d.get("total_completion_tokens", 0),
            "total_tokens": d.get("total_tokens", 0),
            "local_tokens": d.get("local_tokens", 0),
            "cloud_tokens": d.get("cloud_tokens", 0),
        }
    if isinstance(summary, dict):
        return summary
    return {"total_tokens": 0}


def record_trace(run_output: dict, golden_id: str | None = None) -> dict:
    """
    Convert a run() dict into a canonical trace, persist to eval/traces/, and return it.
    Safe to call from any thread — writes are atomic (write-then-rename pattern).
    """
    _TRACES_DIR.mkdir(parents=True, exist_ok=True)

    trace = {
        "trace_id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "golden_id": golden_id,
        "input": run_output.get("input", ""),
        "answer": run_output.get("answer", ""),
        "routing_log": run_output.get("routing_log", []),
        "token_summary": _serialise_token_summary(run_output.get("token_summary")),
        "agency_level": run_output.get("agency_level", ""),
        "elapsed": run_output.get("elapsed", 0.0),
        "session_id": run_output.get("session_id", ""),
    }

    ts_prefix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = _TRACES_DIR / f"{ts_prefix}_{trace['trace_id'][:8]}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(trace, indent=2))
    tmp.rename(path)

    return trace


def list_traces(limit: int = 50) -> list[dict]:
    """Return metadata for the most recent traces (no full answer body)."""
    if not _TRACES_DIR.exists():
        return []
    files = sorted(_TRACES_DIR.glob("*.json"), reverse=True)[:limit]
    result = []
    for f in files:
        try:
            t = json.loads(f.read_text())
            result.append({
                "trace_id": t["trace_id"],
                "ts": t["ts"],
                "golden_id": t.get("golden_id"),
                "input_preview": t["input"][:100],
                "answer_preview": t["answer"][:100],
                "elapsed": t.get("elapsed", 0.0),
                "tokens": t.get("token_summary", {}).get("total_tokens", 0),
                "agency_level": t.get("agency_level", ""),
            })
        except Exception:
            pass
    return result


def get_trace(trace_id: str) -> dict | None:
    """Load a full trace by its UUID."""
    if not _TRACES_DIR.exists():
        return None
    for f in _TRACES_DIR.glob("*.json"):
        try:
            t = json.loads(f.read_text())
            if t.get("trace_id") == trace_id:
                return t
        except Exception:
            pass
    return None
