"""
monitor/fsm.py
--------------
Proactive monitoring finite-state machine, built on LangGraph.

One cycle walks the states from SPEC 4.5:

    IDLE -> POLLING -> EXTRACTING -> UPDATING -> CHECKING -> ALERTING -> IDLE
                                                                  |
                                              <--(5 min sleep)-- ERROR

Each node returns partial MonitorState updates. If any node sets `error`, the
flow short-circuits to the ERROR node and ends the cycle; the runner then sleeps
5 minutes before the next cycle (SPEC 8). New conflicts are written to
alerts.jsonl (one JSON object per line) and a threading.Event is set so the UI
can refresh.

The MonitorRunner owns the connectors, the (injectable) extractor, and the
background loop. fetch -> extract -> insert -> detect-conflict -> alert.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, TypedDict

from langgraph.graph import StateGraph, START, END

from config.store import get_config
from connectors.base import BaseConnector, RawMessage
from connectors.factory import build_default_connectors, build_commitment_sources
from connectors.mock import MockConnector
from extraction.commitment import ExtractionResult, extract_commitments
from extraction.ner import extract_entities
from kg.schema import CommitmentNode, ConflictEdge, get_db_connection
from kg.graph import detect_new_conflicts, insert_commitment


# Sleep after an error before retrying (SPEC 8: "sleep 5 min").
ERROR_SLEEP_SECONDS: int = 300

# Signature of a swappable extractor: (message, ner_candidates) -> result|None.
Extractor = Callable[[RawMessage, list[tuple[str, str]]], Optional[ExtractionResult]]


# ---------------------------------------------------------------------------
# State (SPEC 4.5)
# ---------------------------------------------------------------------------

class MonitorState(TypedDict):
    last_poll_ts: float
    new_messages: list[RawMessage]
    new_commitments: list[CommitmentNode]
    new_conflicts: list[ConflictEdge]
    alerts_fired: int
    cycle_count: int
    error: Optional[str]


# ---------------------------------------------------------------------------
# Alert formatting
# ---------------------------------------------------------------------------

def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _format_alert(edge: ConflictEdge) -> dict:
    """Build the alert record written to alerts.jsonl (SPEC 4.5)."""
    first = edge.commitment_a
    second = edge.commitment_b
    return {
        "timestamp": time.time(),
        "type": "conflict",
        "commitment_a": {"description": first.description, "source": first.source,
                         "start_ts": first.start_ts},
        "commitment_b": {"description": second.description, "source": second.source,
                         "start_ts": second.start_ts},
        "overlap_minutes": round(edge.overlap_minutes, 1),
        "message": (
            f"Conflict: '{first.description[:40]}' ({first.source}) overlaps "
            f"'{second.description[:40]}' ({second.source}) at {_hhmm(first.start_ts)}"
        ),
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class MonitorRunner:
    """Owns the connectors, the FSM, and the background polling loop."""

    def __init__(
        self,
        connectors: Optional[list[BaseConnector]] = None,
        extractor: Optional[Extractor] = None,
        commitment_sources: Optional[list] = None,
        error_sleep_seconds: int = ERROR_SLEEP_SECONDS,
    ) -> None:
        # Default: live sources (Slack) each wrapped with a mock fallback, so
        # the system always has a usable source (SPEC 4.2). Tests pass an
        # explicit list (e.g. [MockConnector()]).
        self.connectors: list[BaseConnector] = (
            connectors if connectors is not None else build_default_connectors()
        )
        # Structured commitment sources (calendar) bypass the LLM extractor and
        # yield HARD commitments directly. Default: live calendar -> mock.
        self.commitment_sources: list = (
            commitment_sources if commitment_sources is not None
            else build_commitment_sources()
        )
        self.extractor: Extractor = extractor or self._default_extractor
        self.error_sleep_seconds = error_sleep_seconds

        self.last_poll_ts: float = 0.0
        self.cycle_count: int = 0
        self.latest_state: Optional[MonitorState] = None
        self.alert_event = threading.Event()

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._graph = self._build_graph()

    # -- default extractor -------------------------------------------------

    @staticmethod
    def _default_extractor(
        message: RawMessage, candidates: list[tuple[str, str]]
    ) -> Optional[ExtractionResult]:
        return extract_commitments(message, candidates)

    # -- graph construction ------------------------------------------------

    def _build_graph(self):
        builder: StateGraph = StateGraph(MonitorState)
        builder.add_node("polling", self._polling)
        builder.add_node("extracting", self._extracting)
        builder.add_node("updating", self._updating)
        builder.add_node("checking", self._checking)
        builder.add_node("alerting", self._alerting)
        builder.add_node("error", self._error)

        builder.add_edge(START, "polling")
        # After each working node, continue or divert to ERROR if one failed.
        builder.add_conditional_edges("polling", self._route, {"go": "extracting", "error": "error"})
        builder.add_conditional_edges("extracting", self._route, {"go": "updating", "error": "error"})
        builder.add_conditional_edges("updating", self._route, {"go": "checking", "error": "error"})
        builder.add_conditional_edges("checking", self._route, {"go": "alerting", "error": "error"})
        builder.add_edge("alerting", END)
        builder.add_edge("error", END)
        return builder.compile()

    @staticmethod
    def _route(state: MonitorState) -> str:
        return "error" if state.get("error") else "go"

    # -- nodes -------------------------------------------------------------

    def _polling(self, state: MonitorState) -> dict:
        """Fetch messages newer than last_poll_ts from healthy connectors."""
        try:
            poll_start = time.time()
            messages: list[RawMessage] = []
            for connector in self.connectors:
                if connector.health_check():
                    messages.extend(connector.fetch_recent(state["last_poll_ts"]))
                else:
                    print(f"[Monitor] WARNING: connector '{connector.source_name}' "
                          f"unhealthy; skipping this cycle.")
            return {"new_messages": messages, "last_poll_ts": poll_start}
        except Exception as error:  # noqa: BLE001
            return {"error": f"polling: {error}"}

    def _extracting(self, state: MonitorState) -> dict:
        """
        Produce commitments for this cycle from two paths:
          - free-text messages -> NER + LLM extractor
          - structured sources (calendar) -> HARD commitments, no LLM
        """
        try:
            commitments: list[CommitmentNode] = []
            for message in state["new_messages"]:
                candidates = extract_entities(message.text)
                result = self.extractor(message, candidates)
                if result is not None:
                    commitments.extend(result.commitments)
            # Structured commitment sources bypass the extractor.
            for source in self.commitment_sources:
                commitments.extend(source.fetch_commitments())
            return {"new_commitments": commitments}
        except Exception as error:  # noqa: BLE001
            return {"error": f"extracting: {error}"}

    def _updating(self, state: MonitorState) -> dict:
        """Insert extracted commitments into the KG, capturing assigned ids."""
        try:
            conn = get_db_connection(get_config().db_path)
            try:
                inserted: list[CommitmentNode] = []
                for commitment in state["new_commitments"]:
                    new_id = insert_commitment(conn, commitment)
                    inserted.append(replace(commitment, id=new_id))
            finally:
                conn.close()
            return {"new_commitments": inserted}
        except Exception as error:  # noqa: BLE001
            return {"error": f"updating: {error}"}

    def _checking(self, state: MonitorState) -> dict:
        """Detect new conflicts for each freshly inserted commitment."""
        try:
            conn = get_db_connection(get_config().db_path)
            try:
                conflicts: list[ConflictEdge] = []
                for commitment in state["new_commitments"]:
                    if commitment.id > 0:
                        conflicts.extend(detect_new_conflicts(conn, commitment.id))
            finally:
                conn.close()
            return {"new_conflicts": conflicts}
        except Exception as error:  # noqa: BLE001
            return {"error": f"checking: {error}"}

    def _alerting(self, state: MonitorState) -> dict:
        """Append new conflicts to alerts.jsonl and mark them alerted."""
        try:
            conflicts = state["new_conflicts"]
            if not conflicts:
                return {"alerts_fired": 0}

            cfg = get_config()
            alerts_path = Path(cfg.alerts_log_path)
            alerts_path.parent.mkdir(parents=True, exist_ok=True)

            conn = get_db_connection(cfg.db_path)
            try:
                with alerts_path.open("a", encoding="utf-8") as alert_file:
                    for edge in conflicts:
                        alert_file.write(json.dumps(_format_alert(edge)) + "\n")
                        conn.execute(
                            "UPDATE conflicts SET alerted = 1 WHERE id = ?", (edge.id,)
                        )
                conn.commit()
            finally:
                conn.close()

            self.alert_event.set()  # signal the UI to refresh
            return {"alerts_fired": len(conflicts)}
        except Exception as error:  # noqa: BLE001
            return {"error": f"alerting: {error}"}

    def _error(self, state: MonitorState) -> dict:
        """Terminal error node: log. The runner handles the 5-minute sleep."""
        print(f"[Monitor] ERROR in cycle {state.get('cycle_count')}: {state.get('error')}")
        return {}

    # -- cycle + loop ------------------------------------------------------

    def run_once(self) -> MonitorState:
        """Run exactly one FSM cycle and return the final state."""
        self.cycle_count += 1
        initial: MonitorState = {
            "last_poll_ts": self.last_poll_ts,
            "new_messages": [],
            "new_commitments": [],
            "new_conflicts": [],
            "alerts_fired": 0,
            "cycle_count": self.cycle_count,
            "error": None,
        }
        final: MonitorState = self._graph.invoke(initial)
        # Only advance the poll cursor on a clean cycle.
        if not final.get("error"):
            self.last_poll_ts = final.get("last_poll_ts", self.last_poll_ts)
        self.latest_state = final
        return final

    def _loop(self) -> None:
        interval_seconds = get_config().monitor_interval_minutes * 60
        while not self._stop.is_set():
            state = self.run_once()
            sleep_for = self.error_sleep_seconds if state.get("error") else interval_seconds
            self._stop.wait(sleep_for)

    def start(self) -> None:
        """Start the background polling loop (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="monitor-fsm", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the background loop to stop."""
        self._stop.set()


# Shared singleton (started by the launcher after the main UI loads).
monitor_runner = MonitorRunner()


# ---------------------------------------------------------------------------
# Smoke test (uses MockConnector + a stub extractor -- no Ollama/network)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import tempfile

    from kg.schema import init_db

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "monitor.db")
        alerts_path = str(Path(tmp) / "alerts.jsonl")
        os.environ["KM_DB_PATH"] = db_path
        cfg = get_config()
        cfg.db_path = db_path
        cfg.alerts_log_path = alerts_path

        # Seed a calendar commitment that the incoming Slack commitment collides with.
        seed_conn = init_db(db_path)
        noon = datetime.combine(datetime.now().date(), datetime.min.time()).timestamp() + 12 * 3600
        seed_conn.execute(
            "INSERT INTO persons (id, name, created_at) VALUES (1, 'Priya', ?)", (time.time(),)
        )
        seed_conn.execute(
            """INSERT INTO commitments
               (person_id, description, start_ts, end_ts, source, commitment_type,
                confidence, created_at, updated_at)
               VALUES (1, 'Team standup', ?, ?, 'calendar', 'HARD', 1.0, ?, ?)""",
            (noon, noon + 1800, time.time(), time.time()),
        )
        seed_conn.commit()
        seed_conn.close()

        # Stub extractor: turn any Slack message into a Priya commitment that
        # overlaps the seeded calendar standup -> guaranteed conflict.
        def _stub_extractor(message: RawMessage, _candidates: list[tuple[str, str]]):
            commitment = CommitmentNode(
                id=0, person_name="Priya", description="see you at noon",
                start_ts=noon + 300, end_ts=noon + 2100, source="slack",
                commitment_type="SOFT", confidence=0.75, raw_text=message.text,
            )
            return ExtractionResult([commitment], message.text, "stub", 0.0)

        # Message path only (commitment_sources=[] keeps this assertion isolated).
        runner = MonitorRunner(
            connectors=[MockConnector()], extractor=_stub_extractor,
            commitment_sources=[],
        )
        final_state = runner.run_once()

        assert final_state["error"] is None, f"cycle errored: {final_state['error']}"
        print(f"=> cycle {final_state['cycle_count']}: "
              f"{len(final_state['new_messages'])} msgs, "
              f"{len(final_state['new_commitments'])} commitments, "
              f"{len(final_state['new_conflicts'])} conflicts, "
              f"{final_state['alerts_fired']} alerts")
        assert final_state["alerts_fired"] >= 1, "expected at least one conflict alert"

        # The alert must be persisted to alerts.jsonl in the SPEC format.
        lines = Path(alerts_path).read_text(encoding="utf-8").strip().splitlines()
        assert lines, "alerts.jsonl is empty"
        record = json.loads(lines[0])
        assert record["type"] == "conflict" and "overlap_minutes" in record
        print(f"=> alert written: {record['message']}")

        # Idempotency: a second cycle (same poll cursor reset) must not
        # re-insert duplicates -> no new conflicts/alerts.
        runner.last_poll_ts = 0.0
        second = runner.run_once()
        assert second["alerts_fired"] == 0, "duplicate alerts on re-poll"
        print("=> re-poll produced no duplicate alerts (dedup holds)")

        # Calendar path: structured HARD commitments ingested with NO messages
        # and NO LLM -- straight from the calendar commitment source.
        from connectors.mock import MockCalendarSource
        cal_runner = MonitorRunner(
            connectors=[], extractor=_stub_extractor,
            commitment_sources=[MockCalendarSource()],
        )
        cal_state = cal_runner.run_once()
        assert cal_state["error"] is None, f"calendar cycle errored: {cal_state['error']}"
        expected = len(MockCalendarSource().fetch_commitments())
        assert len(cal_state["new_commitments"]) == expected, "calendar commitments not ingested"
        check_conn = get_db_connection(cfg.db_path)
        calendar_rows = check_conn.execute(
            "SELECT COUNT(*) AS n FROM commitments WHERE source = 'calendar'"
        ).fetchone()
        check_conn.close()
        assert calendar_rows["n"] >= expected, "calendar commitments not persisted to KG"
        print(f"=> calendar path: {expected} HARD/structured commitment(s) ingested "
              f"(no message, no LLM)")

    print("All monitor/fsm.py smoke tests passed.")
