"""
connectors/mock.py
------------------
Offline / demo connector. Loads message records from a bundled data/mock_*.json
file and yields them as RawMessage objects. This is the fallback the monitor FSM
uses when a real connector's health_check() fails (SPEC 4.2), and the default
source in demo mode when no live credentials are configured.

It reads message-shaped files (mock_messages.json: source/channel_id/sender/
text/timestamp/external_id). Calendar/Gmail mock data have their own schemas and
are consumed directly by their tools until the live connectors land.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from connectors.base import BaseConnector, RawMessage, DATA_DIR, parse_timestamp
from kg.schema import CommitmentNode


DEFAULT_MESSAGES_FILE: str = "mock_messages.json"
DEFAULT_CALENDAR_FILE: str = "mock_calendar.json"


class MockConnector(BaseConnector):
    """Reads a message-shaped mock JSON file into RawMessage objects."""

    source_name = "mock"

    def __init__(
        self,
        data_file: str = DEFAULT_MESSAGES_FILE,
        source_override: Optional[str] = None,
    ) -> None:
        """
        Args:
            data_file: filename under the data/ directory to load.
            source_override: if set, force this `source` on every message
                (otherwise each record's own `source` field is used).
        """
        self.data_path: Path = DATA_DIR / data_file
        self.source_override = source_override

    def _load_records(self) -> list[dict[str, Any]]:
        if not self.data_path.exists():
            return []
        try:
            return json.loads(self.data_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            print(f"[MockConnector] WARNING: bad JSON in {self.data_path.name}: {error}")
            return []

    def fetch_recent(self, since_ts: float) -> list[RawMessage]:
        """Return mock messages with timestamp >= since_ts, oldest-first."""
        messages: list[RawMessage] = []
        for record in self._load_records():
            timestamp = parse_timestamp(record.get("timestamp", 0))
            if timestamp < since_ts:
                continue
            messages.append(RawMessage(
                source=self.source_override or record.get("source", "mock"),
                channel_id=record.get("channel_id", ""),
                sender=record.get("sender", "unknown"),
                text=record.get("text", ""),
                timestamp=timestamp,
                external_id=record.get("external_id", ""),
            ))
        messages.sort(key=lambda message: message.timestamp)
        return messages

    def health_check(self) -> bool:
        """Mock is healthy when its data file is present."""
        return self.data_path.exists()


# ---------------------------------------------------------------------------
# Mock calendar commitment source (structured -> HARD commitments)
# ---------------------------------------------------------------------------

def _calendar_record_to_commitment(record: dict) -> Optional[CommitmentNode]:
    """Map a mock_calendar.json record to a CommitmentNode (HARD by default)."""
    start_ts = parse_timestamp(record.get("start", ""))
    if not start_ts:
        return None
    end_value = record.get("end")
    summary = record.get("summary", "(no title)")
    return CommitmentNode(
        id=0,
        person_name="(self)",
        description=summary,
        start_ts=start_ts,
        end_ts=parse_timestamp(end_value) if end_value else None,
        source="calendar",
        commitment_type=record.get("commitment_type", "HARD"),
        confidence=1.0,
        raw_text=summary,
    )


class MockCalendarSource:
    """
    Offline calendar source: reads mock_calendar.json into HARD CommitmentNodes.

    This is a *commitment source* (not a BaseConnector): it yields structured
    commitments directly, mirroring GoogleCalendarConnector.fetch_commitments,
    so it can be the offline fallback for the live calendar in the monitor.
    """

    source_name = "calendar-mock"

    def __init__(self, data_file: str = DEFAULT_CALENDAR_FILE) -> None:
        self.data_path: Path = DATA_DIR / data_file

    def health_check(self) -> bool:
        return self.data_path.exists()

    def fetch_commitments(self, days_ahead: int = 7) -> list[CommitmentNode]:
        """Return all mock calendar entries as commitments (days_ahead ignored)."""
        if not self.data_path.exists():
            return []
        try:
            records = json.loads(self.data_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            print(f"[MockCalendarSource] WARNING: bad JSON in {self.data_path.name}: {error}")
            return []
        commitments = [_calendar_record_to_commitment(record) for record in records]
        return [commitment for commitment in commitments if commitment is not None]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    connector = MockConnector()

    assert connector.health_check() is True, "mock_messages.json should exist"
    print(f"=> health_check ok ({connector.data_path.name})")

    everything = connector.fetch_recent(0.0)
    assert everything, "expected mock messages to load"
    assert everything == sorted(everything, key=lambda m: m.timestamp), "not oldest-first"
    print(f"=> fetched {len(everything)} message(s), oldest-first")
    print(f"   first: [{everything[0].source}/{everything[0].channel_id}] "
          f"{everything[0].sender}: {everything[0].text[:40]}")

    # since_ts filtering: a cutoff after the earliest message drops it.
    cutoff = everything[0].timestamp + 1.0
    later = connector.fetch_recent(cutoff)
    assert len(later) < len(everything), "since_ts filter did not drop older messages"
    print(f"=> since_ts filter: {len(everything)} -> {len(later)} after cutoff")

    # Missing file degrades to empty, never raises.
    empty = MockConnector(data_file="does_not_exist.json")
    assert empty.health_check() is False and empty.fetch_recent(0.0) == []
    print("=> missing file degrades to empty + unhealthy")

    # MockCalendarSource yields HARD commitments from mock_calendar.json.
    calendar_source = MockCalendarSource()
    assert calendar_source.health_check() is True, "mock_calendar.json should exist"
    commitments = calendar_source.fetch_commitments()
    assert commitments, "expected mock calendar commitments"
    assert all(c.source == "calendar" and c.commitment_type in ("HARD", "SOFT")
               for c in commitments), "calendar commitments malformed"
    print(f"=> MockCalendarSource: {len(commitments)} commitment(s), "
          f"first '{commitments[0].description}' ({commitments[0].commitment_type})")

    print("All connectors/mock.py smoke tests passed.")
