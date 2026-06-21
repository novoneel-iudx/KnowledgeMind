"""
connectors/base.py
------------------
Connector abstraction shared by every ingestion source.

A connector turns an external source (Slack, Calendar, Gmail, WhatsApp, or the
offline mock) into a uniform stream of RawMessage objects that the commitment
extractor and monitor FSM consume. The contract is intentionally tiny
(SPEC 4.2): fetch_recent() to pull new messages, health_check() so the monitor
can fall back to mock data when a real source is unavailable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Union


# Repo-root data directory holding the bundled mock_*.json files.
# (PyInstaller bundles this directory; see build_windows.spec.)
DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# Uniform message envelope (SPEC 3.2)
# ---------------------------------------------------------------------------

@dataclass
class RawMessage:
    """A single inbound message normalised across all connector sources."""
    source: str             # 'slack'|'calendar'|'email'|'whatsapp'|'mock'
    channel_id: str
    sender: str
    text: str
    timestamp: float        # epoch seconds
    external_id: str


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def parse_timestamp(value: Union[str, int, float]) -> float:
    """
    Normalise a timestamp to epoch seconds.

    Mock JSON files use ISO 8601 strings for readability; live APIs hand back
    epoch numbers. Both are accepted here so connectors do not each re-implement
    this. An unparseable value yields 0.0 rather than raising.
    """
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Connector ABC
# ---------------------------------------------------------------------------

class BaseConnector(ABC):
    """Abstract base for all ingestion connectors."""

    source_name: str = "base"

    @abstractmethod
    def fetch_recent(self, since_ts: float) -> list[RawMessage]:
        """Return messages newer than `since_ts`, oldest-first."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> bool:
        """True if the source is reachable/usable; False triggers mock fallback."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Per-source fallback composition (SPEC 4.2)
# ---------------------------------------------------------------------------

class FallbackConnector(BaseConnector):
    """
    Wraps a primary connector with a fallback (typically the mock connector).

    Each cycle it uses the primary when the primary's health_check() passes,
    otherwise the fallback. This implements SPEC 4.2's "use mock data for that
    source when the connector is unhealthy" without running both at once
    (which would double-ingest the same messages).
    """

    def __init__(self, primary: BaseConnector, fallback: BaseConnector) -> None:
        self.primary = primary
        self.fallback = fallback
        self.source_name = f"{primary.source_name}->{fallback.source_name}"

    def health_check(self) -> bool:
        """Usable whenever either the primary or the fallback is available."""
        return self.primary.health_check() or self.fallback.health_check()

    def fetch_recent(self, since_ts: float) -> list[RawMessage]:
        if self.primary.health_check():
            return self.primary.fetch_recent(since_ts)
        print(f"[Connector] WARNING: '{self.primary.source_name}' unhealthy; "
              f"falling back to '{self.fallback.source_name}'.")
        return self.fallback.fetch_recent(since_ts)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # parse_timestamp accepts ISO strings, epoch numbers, and bad input.
    iso = parse_timestamp("2026-06-22T16:00:00")
    assert iso > 0, "ISO timestamp failed to parse"
    assert parse_timestamp(1750000000.0) == 1750000000.0, "epoch passthrough failed"
    assert parse_timestamp("not-a-date") == 0.0, "bad input should yield 0.0"
    print(f"=> parse_timestamp ISO -> {iso:.0f}, epoch passthrough, bad -> 0.0")

    # A trivial subclass must satisfy the ABC contract.
    class _Echo(BaseConnector):
        source_name = "echo"

        def fetch_recent(self, since_ts: float) -> list[RawMessage]:
            return [RawMessage("echo", "c1", "me", "hi", since_ts + 1, "e1")]

        def health_check(self) -> bool:
            return True

    echo = _Echo()
    assert echo.health_check() is True
    assert echo.fetch_recent(0.0)[0].source == "echo"
    print("=> BaseConnector subclass satisfies the contract")

    # FallbackConnector: picks primary when healthy, else the fallback.
    class _Down(BaseConnector):
        source_name = "down"

        def fetch_recent(self, since_ts: float) -> list[RawMessage]:
            return [RawMessage("down", "c", "x", "primary", since_ts, "d1")]

        def health_check(self) -> bool:
            return False

    class _Mock(BaseConnector):
        source_name = "mock"

        def fetch_recent(self, since_ts: float) -> list[RawMessage]:
            return [RawMessage("mock", "c", "x", "fallback", since_ts, "m1")]

        def health_check(self) -> bool:
            return True

    healthy_pair = FallbackConnector(primary=_Echo(), fallback=_Mock())
    assert healthy_pair.fetch_recent(0.0)[0].text == "hi", "healthy primary not used"
    down_pair = FallbackConnector(primary=_Down(), fallback=_Mock())
    assert down_pair.fetch_recent(0.0)[0].text == "fallback", "fallback not used when primary down"
    assert down_pair.health_check() is True, "pair should be usable via fallback"
    print("=> FallbackConnector routes primary-when-healthy, else fallback")

    print("All connectors/base.py smoke tests passed.")
