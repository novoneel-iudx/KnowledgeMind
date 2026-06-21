"""
connectors/calendar.py
----------------------
Google Calendar client (SPEC 4.2): read events, create events, and free/busy
queries over the user's primary calendar via OAuth 2.0.

Design note: unlike Slack, calendar entries are STRUCTURED and become HARD
commitments directly (SPEC 11, Week 3) -- they do not go through the free-text
LLM extractor. So this is intentionally NOT a BaseConnector (which yields
RawMessage for extraction). It instead exposes:
  - health_check() / connect()            -- OAuth lifecycle
  - list_events() / create_event() / free_busy()  -- back the google_calendar tool
  - fetch_commitments() -> list[CommitmentNode]    -- HARD commitments for the monitor

Auth is non-interactive everywhere except connect(): health_check() and the read
methods only succeed if a valid token already exists, so they never block on a
browser. connect() runs the one-time interactive consent flow (UI button).
Errors degrade to empty / failure dicts -- never raised (SPEC 8).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config.store import get_config
from kg.schema import CommitmentNode


_SCOPES: list[str] = ["https://www.googleapis.com/auth/calendar"]
_CALENDAR_ID: str = "primary"
_DEFAULT_DAYS_AHEAD: int = 7
_DEFAULT_MAX_RESULTS: int = 50


# ---------------------------------------------------------------------------
# Pure event mappers (no network -- unit-testable)
# ---------------------------------------------------------------------------

def _parse_event_time(time_obj: dict) -> Optional[float]:
    """Convert a Google event start/end object to epoch seconds, or None."""
    if not time_obj:
        return None
    value = time_obj.get("dateTime") or time_obj.get("date")
    if not value:
        return None
    try:
        # 'Z' (UTC) is not accepted by fromisoformat before 3.11 -> normalise.
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _event_to_commitment(event: dict) -> Optional[CommitmentNode]:
    """
    Map a Google Calendar event to a HARD CommitmentNode (confidence 1.0).

    person_name is "(self)" -- entries on the user's own calendar are their own
    commitments (person_id NULL), so they conflict with other self commitments
    and with un-attributed messages.
    """
    start_ts = _parse_event_time(event.get("start", {}))
    if start_ts is None:
        return None
    summary = event.get("summary", "(no title)")
    return CommitmentNode(
        id=0,
        person_name="(self)",
        description=summary,
        start_ts=start_ts,
        end_ts=_parse_event_time(event.get("end", {})),
        source="calendar",
        commitment_type="HARD",
        confidence=1.0,
        raw_text=event.get("description") or summary,
    )


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class GoogleCalendarConnector:
    """Read/create calendar events and surface them as HARD commitments."""

    source_name = "calendar"

    def __init__(self) -> None:
        cfg = get_config()
        self._creds_path = cfg.google_credentials_path
        self._token_path = cfg.google_token_path
        self._service: Any = None

    # -- auth --------------------------------------------------------------

    def _load_valid_credentials(self) -> Optional[Credentials]:
        """Load and refresh saved credentials WITHOUT the interactive flow."""
        token_path = Path(self._token_path) if self._token_path else None
        if token_path is None or not token_path.exists():
            return None
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
        except Exception:  # noqa: BLE001
            return None
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_path.write_text(creds.to_json(), encoding="utf-8")
                return creds
            except Exception:  # noqa: BLE001
                return None
        return None

    def _get_service(self) -> Any:
        """Build the Calendar service from existing creds (non-interactive)."""
        if self._service is not None:
            return self._service
        creds = self._load_valid_credentials()
        if creds is None:
            return None
        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def connect(self) -> dict:
        """
        Run the one-time interactive OAuth consent flow and save the token.
        Intended for a UI "Connect Google" button -- this WILL open a browser.
        """
        if not self._creds_path or not Path(self._creds_path).exists():
            return {"success": False, "error": "Google credentials file not found. Set it in Settings."}
        try:
            flow = InstalledAppFlow.from_client_secrets_file(self._creds_path, _SCOPES)
            creds = flow.run_local_server(port=0)
            token_path = Path(self._token_path)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            return {"success": True, "message": "Google Calendar connected."}
        except Exception as error:  # noqa: BLE001
            return {"success": False, "error": str(error)}

    def health_check(self) -> bool:
        """True only if credentials exist and a valid token is already present."""
        if not self._creds_path or not Path(self._creds_path).exists():
            return False
        return self._load_valid_credentials() is not None

    # -- reads -------------------------------------------------------------

    def list_events(
        self, days_ahead: int = _DEFAULT_DAYS_AHEAD, max_results: int = _DEFAULT_MAX_RESULTS
    ) -> list[dict]:
        """Return upcoming events in the next `days_ahead` days (empty on error)."""
        service = self._get_service()
        if service is None:
            return []
        now = dt.datetime.now(dt.timezone.utc)
        try:
            response = service.events().list(
                calendarId=_CALENDAR_ID,
                timeMin=now.isoformat(),
                timeMax=(now + dt.timedelta(days=days_ahead)).isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=max_results,
            ).execute()
            return response.get("items", [])
        except Exception as error:  # noqa: BLE001
            print(f"[Calendar] ERROR: list_events failed ({error}).")
            return []

    def fetch_commitments(self, days_ahead: int = _DEFAULT_DAYS_AHEAD) -> list[CommitmentNode]:
        """Upcoming events mapped to HARD commitments (for the monitor)."""
        commitments = [_event_to_commitment(event) for event in self.list_events(days_ahead)]
        return [commitment for commitment in commitments if commitment is not None]

    def free_busy(self, start_iso: str, end_iso: str) -> list[dict]:
        """Return busy intervals between two ISO timestamps (empty on error)."""
        service = self._get_service()
        if service is None:
            return []
        try:
            response = service.freebusy().query(body={
                "timeMin": start_iso, "timeMax": end_iso,
                "items": [{"id": _CALENDAR_ID}],
            }).execute()
            return response.get("calendars", {}).get(_CALENDAR_ID, {}).get("busy", [])
        except Exception as error:  # noqa: BLE001
            print(f"[Calendar] ERROR: free_busy failed ({error}).")
            return []

    # -- writes ------------------------------------------------------------

    def create_event(
        self, summary: str, start_iso: str, end_iso: str,
        attendees: Optional[list[str]] = None,
    ) -> dict:
        """Create an event. Returns {"success", "id"/"error"}."""
        service = self._get_service()
        if service is None:
            return {"success": False, "error": "Calendar not connected. Connect Google in Settings."}
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
        }
        if attendees:
            body["attendees"] = [{"email": email} for email in attendees]
        try:
            created = service.events().insert(calendarId=_CALENDAR_ID, body=body).execute()
            return {"success": True, "id": created.get("id"), "htmlLink": created.get("htmlLink")}
        except Exception as error:  # noqa: BLE001
            return {"success": False, "error": str(error)}


# ---------------------------------------------------------------------------
# Smoke test (offline -- no credentials / no network)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Pure mappers are testable without a service.
    sample_event = {
        "id": "evt-1",
        "summary": "1:1 with Priya",
        "description": "Atlas numbers",
        "start": {"dateTime": "2026-06-22T16:00:00+05:30"},
        "end": {"dateTime": "2026-06-22T16:45:00+05:30"},
    }
    commitment = _event_to_commitment(sample_event)
    assert commitment is not None
    assert commitment.commitment_type == "HARD" and commitment.confidence == 1.0
    assert commitment.source == "calendar" and commitment.person_name == "(self)"
    assert commitment.start_ts < commitment.end_ts
    print(f"=> event -> HARD commitment: '{commitment.description}' "
          f"(start_ts set, end_ts set)")

    # All-day event (date, not dateTime) still parses.
    allday = _event_to_commitment({"summary": "Holiday", "start": {"date": "2026-06-24"},
                                    "end": {"date": "2026-06-25"}})
    assert allday is not None and allday.start_ts > 0
    print("=> all-day event parses")

    # Event with no usable start is skipped.
    assert _event_to_commitment({"summary": "broken", "start": {}}) is None
    print("=> event with no start is skipped")

    # Without credentials configured: unhealthy + graceful degradation.
    connector = GoogleCalendarConnector()
    if not connector._creds_path:
        assert connector.health_check() is False
        assert connector.list_events() == [] and connector.fetch_commitments() == []
        create_result = connector.create_event("X", "2026-06-22T10:00:00", "2026-06-22T11:00:00")
        assert create_result["success"] is False
        print("=> no credentials: unhealthy, reads empty, create refused (graceful)")
    else:
        print("=> credentials configured; skipping no-credential assertions")

    print("All connectors/calendar.py smoke tests passed.")
