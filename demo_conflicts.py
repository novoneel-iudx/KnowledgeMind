"""
demo_conflicts.py
-----------------
Self-contained demonstration of KnowledgeMind's *proactive conflict detection*
(README Scenarios 0 & 4) running end-to-end on the bundled mock data.

It drives the REAL pipeline -- MockConnector -> spaCy NER -> the few-shot
commitment extractor + deterministic time resolver -> SQLite KG -> person-
agnostic conflict detection -> alerts -- and only stubs the single LLM call
(canned JSON per message) so the demo runs without Ollama or any API key. Every
other component is the production code path.

Run:  python demo_conflicts.py
It uses a throwaway temp database, so it never touches your real config/DB.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

from config.store import get_config
from connectors.mock import MockConnector, MockCalendarSource
from extraction.commitment import extract_commitments
from kg.schema import get_db_connection
from monitor.fsm import MonitorRunner


# --- a canned local-LLM caller: maps a message to realistic extractor JSON -----
# Keyed by a distinctive lowercase substring of the message text. normalized_ts
# is left null on purpose -- the deterministic resolver turns the time words into
# an absolute timestamp, which is exactly what the fix exercises.
_CANNED: dict[str, str] = {
    "see you at 4": '{"is_commitment": true, "confidence": 0.78, "time_expression": "at 4 today", "normalized_ts": null, "commitment_type": "SOFT"}',
    "eod monday":   '{"is_commitment": true, "confidence": 0.82, "time_expression": "EOD Monday", "normalized_ts": null, "commitment_type": "SOFT"}',
    "lunch thursday": '{"is_commitment": true, "confidence": 0.70, "time_expression": "Thursday 12:30", "normalized_ts": null, "commitment_type": "SOFT"}',
    "next week":    '{"is_commitment": true, "confidence": 0.45, "time_expression": "next week", "normalized_ts": null, "commitment_type": "TENTATIVE"}',
}
_NON_COMMITMENT = '{"is_commitment": false, "confidence": 0.05, "time_expression": "", "normalized_ts": null, "commitment_type": "TENTATIVE"}'


def _stub_llm_caller(_system_prompt: str, user_prompt: str) -> str:
    """Return canned extractor JSON for the message under classification."""
    last_block = user_prompt.rsplit("Message: ", 1)[-1]
    text = last_block.split("\nJSON:")[0].strip().lower()
    for needle, response in _CANNED.items():
        if needle in text:
            return response
    return _NON_COMMITMENT


def _stub_extractor(message, candidates):
    """Real extractor + resolver, but with the stubbed (offline) LLM call."""
    return extract_commitments(message, candidates, llm_caller=_stub_llm_caller)


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%a %Y-%m-%d %H:%M")


_STOPWORDS = {"today", "tomorrow", "around", "about", "with", "this", "that",
              "have", "hey", "lmk", "ready", "numbers", "sync"}


def _topic_tokens(text: str) -> set[str]:
    import re
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOPWORDS}


def _looks_like_same_event(alert: dict) -> bool:
    """Heuristic: different channels + overlapping topic words => one real event
    seen on two channels (e.g. a Slack 'lunch...' and a calendar 'Lunch with
    Lena'), not a genuine clash. Cross-source event de-dup is future work."""
    ca, cb = alert["commitment_a"], alert["commitment_b"]
    if ca["source"] == cb["source"]:
        return False
    return bool(_topic_tokens(ca["description"]) & _topic_tokens(cb["description"]))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = get_config()
        cfg.db_path = str(Path(tmp) / "demo.db")
        cfg.alerts_log_path = str(Path(tmp) / "alerts.jsonl")

        print("=" * 72)
        print("KnowledgeMind - proactive conflict detection demo (mock data, offline)")
        print("=" * 72)

        runner = MonitorRunner(
            connectors=[MockConnector()],            # Slack mock messages
            extractor=_stub_extractor,               # real extractor, stubbed LLM
            commitment_sources=[MockCalendarSource()],  # calendar (HARD, no LLM)
        )
        state = runner.run_once()
        assert state["error"] is None, f"monitor cycle errored: {state['error']}"

        print(f"\nMonitor cycle: {len(state['new_messages'])} message(s) polled, "
              f"{len(state['new_commitments'])} commitment(s) ingested, "
              f"{len(state['new_conflicts'])} conflict(s), "
              f"{state['alerts_fired']} alert(s) fired.\n")

        conn = get_db_connection(cfg.db_path)
        rows = conn.execute(
            """SELECT c.description, c.source, c.commitment_type, c.start_ts,
                      COALESCE(p.name, '(self)') AS who
               FROM commitments c LEFT JOIN persons p ON c.person_id = p.id
               ORDER BY c.start_ts"""
        ).fetchall()
        print("Commitments on the user's timeline:")
        for r in rows:
            print(f"  - [{r['source']:8} {r['commitment_type']:9}] {_fmt(r['start_ts'])}  "
                  f"{r['description'][:38]:38}  (with: {r['who']})")
        conn.close()

        print("\nConflicts detected (person-agnostic, TENTATIVE excluded):")
        alerts = []
        alerts_file = Path(cfg.alerts_log_path)
        if alerts_file.exists():
            alerts = [json.loads(line) for line in alerts_file.read_text().splitlines() if line.strip()]
        if not alerts:
            print("  (none)")
        for a in alerts:
            ca, cb = a["commitment_a"], a["commitment_b"]
            tag = ("DUPLICATE - same event on two channels (entity-dedup is future "
                   "work, not a real clash)" if _looks_like_same_event(a)
                   else "REAL scheduling conflict")
            print(f"  * {ca['description'][:34]!r} ({ca['source']}) "
                  f"<-> {cb['description'][:34]!r} ({cb['source']})")
            print(f"      overlap {a['overlap_minutes']:.0f} min at {_fmt(ca['start_ts'])}  ->  {tag}")

        duplicates = [a for a in alerts if _looks_like_same_event(a)]
        real = [a for a in alerts if a not in duplicates]
        print(f"\nSummary: {len(real)} real conflict(s), {len(duplicates)} same-event duplicate(s).")
        ok = any("1:1 with Priya" in (a["commitment_a"]["description"] + a["commitment_b"]["description"])
                 for a in real)
        print("Scenario 0 (Slack 'see you at 4' vs calendar '1:1 with Priya'): "
              + ("FIRED as a real conflict ✓" if ok else "NOT fired ✗"))
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
