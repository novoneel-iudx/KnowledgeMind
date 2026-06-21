"""
connectors/factory.py
---------------------
Builds the monitor's default connector list.

Each live source is wrapped in a FallbackConnector so that, per SPEC 4.2, an
unhealthy live source (missing/invalid token, API down) transparently falls back
to the offline mock connector for that source -- without ever running both at
once. Today that is just Slack; calendar/gmail connectors join the list here
once they exist.
"""

from __future__ import annotations

from typing import Protocol

from connectors.base import BaseConnector, FallbackConnector
from connectors.calendar import GoogleCalendarConnector
from connectors.mock import MockConnector, MockCalendarSource
from connectors.slack import SlackConnector
from kg.schema import CommitmentNode


def build_default_connectors() -> list[BaseConnector]:
    """
    Return the default message-connector list for the monitor.

    Slack-with-mock-fallback: uses the live Slack connector when its bot token
    is present and auth succeeds, otherwise the mock connector.
    """
    return [
        FallbackConnector(primary=SlackConnector(), fallback=MockConnector()),
    ]


# ---------------------------------------------------------------------------
# Commitment sources (structured -> HARD commitments, bypass extraction)
# ---------------------------------------------------------------------------

class CommitmentSource(Protocol):
    """Anything that yields structured commitments and reports its health."""

    source_name: str

    def health_check(self) -> bool: ...

    def fetch_commitments(self, days_ahead: int = 7) -> list[CommitmentNode]: ...


class FallbackCommitmentSource:
    """
    Commitment-source analogue of FallbackConnector: uses the primary source
    when its health_check() passes, otherwise the fallback (SPEC 4.2).
    """

    def __init__(self, primary: CommitmentSource, fallback: CommitmentSource) -> None:
        self.primary = primary
        self.fallback = fallback
        self.source_name = f"{primary.source_name}->{fallback.source_name}"

    def health_check(self) -> bool:
        return self.primary.health_check() or self.fallback.health_check()

    def fetch_commitments(self, days_ahead: int = 7) -> list[CommitmentNode]:
        if self.primary.health_check():
            return self.primary.fetch_commitments(days_ahead)
        return self.fallback.fetch_commitments(days_ahead)


def build_commitment_sources() -> list[CommitmentSource]:
    """
    Return the default structured-commitment sources for the monitor.

    Google Calendar with a mock-calendar fallback: live calendar events when the
    user has connected Google, otherwise data/mock_calendar.json.
    """
    return [
        FallbackCommitmentSource(
            primary=GoogleCalendarConnector(), fallback=MockCalendarSource()
        ),
    ]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    connectors = build_default_connectors()
    assert connectors, "factory returned no connectors"

    pair = connectors[0]
    assert isinstance(pair, FallbackConnector), "expected a FallbackConnector"
    # With no Slack token configured in this environment, the pair routes to
    # the mock connector and yields the mock messages.
    messages = pair.fetch_recent(0.0)
    print(f"=> default connectors: {pair.source_name}; "
          f"fetched {len(messages)} message(s) (mock fallback if no Slack token)")
    assert pair.health_check() is True, "default pair should be usable"

    sources = build_commitment_sources()
    assert sources, "factory returned no commitment sources"
    cal_pair = sources[0]
    assert isinstance(cal_pair, FallbackCommitmentSource), "expected FallbackCommitmentSource"
    commitments = cal_pair.fetch_commitments()
    print(f"=> commitment sources: {cal_pair.source_name}; "
          f"{len(commitments)} commitment(s) (mock calendar if Google not connected)")
    assert cal_pair.health_check() is True, "commitment source should be usable"

    print("All connectors/factory.py smoke tests passed.")
