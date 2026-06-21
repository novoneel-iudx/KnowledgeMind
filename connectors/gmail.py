"""
connectors/gmail.py
-------------------
Gmail client (SPEC 4.2): read inbox, create drafts, and send -- with send gated
behind explicit user confirmation.

PRIVACY (rule 6 / rule 4): the agent may draft email but must NEVER send without
a user-visible confirmation in the UI. `send_message()` here exists ONLY for the
UI confirm gate to call after the user approves. No agent tool may call it -- the
`gmail` tool in agent/tools.py refuses the `send` action outright.

OAuth note: this uses a SEPARATE token file (`gmail_token.json`, a sibling of the
calendar token) so its Gmail scopes do not clash with the calendar token's
scopes. Auth is non-interactive except `connect()`; reads/health degrade to
empty/failure without a saved token. Nothing raises (SPEC 8).
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config.store import get_config


_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",  # drafts + send
]
_GMAIL_TOKEN_FILENAME: str = "gmail_token.json"
_USER_ID: str = "me"
_DEFAULT_MAX_RESULTS: int = 10
_DEFAULT_QUERY: str = "is:unread"


# ---------------------------------------------------------------------------
# Pure helpers (no network -- unit-testable)
# ---------------------------------------------------------------------------

def _header(headers: list[dict], name: str) -> str:
    """Return the value of a named header (case-insensitive), or ''."""
    target = name.lower()
    for header in headers:
        if header.get("name", "").lower() == target:
            return header.get("value", "")
    return ""


def _summarise_message(message: dict) -> dict:
    """Reduce a Gmail message resource to {id, from, subject, snippet}."""
    headers = message.get("payload", {}).get("headers", [])
    return {
        "id": message.get("id", ""),
        "from": _header(headers, "From"),
        "subject": _header(headers, "Subject"),
        "snippet": message.get("snippet", ""),
    }


def _build_raw_email(to: str, subject: str, body: str) -> str:
    """Build a base64url-encoded RFC 2822 message for the Gmail API."""
    mime = MIMEText(body)
    mime["to"] = to
    mime["subject"] = subject
    return base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class GmailConnector:
    """Reads inbox, drafts email, and (UI-gated) sends email."""

    source_name = "gmail"

    def __init__(self) -> None:
        cfg = get_config()
        self._creds_path = cfg.google_credentials_path
        # Sibling token file so Gmail scopes do not clash with the calendar token.
        token_dir = Path(cfg.google_token_path).parent if cfg.google_token_path else Path(".")
        self._token_path = token_dir / _GMAIL_TOKEN_FILENAME
        self._service: Any = None

    # -- auth --------------------------------------------------------------

    def _load_valid_credentials(self) -> Optional[Credentials]:
        """Load and refresh saved credentials WITHOUT the interactive flow."""
        if not self._token_path.exists():
            return None
        try:
            creds = Credentials.from_authorized_user_file(str(self._token_path), _SCOPES)
        except Exception:  # noqa: BLE001
            return None
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._token_path.write_text(creds.to_json(), encoding="utf-8")
                return creds
            except Exception:  # noqa: BLE001
                return None
        return None

    def _get_service(self) -> Any:
        """Build the Gmail service from existing creds (non-interactive)."""
        if self._service is not None:
            return self._service
        creds = self._load_valid_credentials()
        if creds is None:
            return None
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def connect(self) -> dict:
        """One-time interactive OAuth consent (UI button); opens a browser."""
        if not self._creds_path or not Path(self._creds_path).exists():
            return {"success": False, "error": "Google credentials file not found. Set it in Settings."}
        try:
            flow = InstalledAppFlow.from_client_secrets_file(self._creds_path, _SCOPES)
            creds = flow.run_local_server(port=0)
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(creds.to_json(), encoding="utf-8")
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            return {"success": True, "message": "Gmail connected."}
        except Exception as error:  # noqa: BLE001
            return {"success": False, "error": str(error)}

    def health_check(self) -> bool:
        """True only if credentials exist and a valid token is already present."""
        if not self._creds_path or not Path(self._creds_path).exists():
            return False
        return self._load_valid_credentials() is not None

    # -- reads -------------------------------------------------------------

    def list_messages(
        self, max_results: int = _DEFAULT_MAX_RESULTS, query: str = _DEFAULT_QUERY
    ) -> list[dict]:
        """Return summaries of recent messages matching `query` (empty on error)."""
        service = self._get_service()
        if service is None:
            return []
        try:
            listing = service.users().messages().list(
                userId=_USER_ID, maxResults=max_results, q=query,
            ).execute()
            summaries: list[dict] = []
            for entry in listing.get("messages", []):
                full = service.users().messages().get(
                    userId=_USER_ID, id=entry["id"], format="metadata",
                    metadataHeaders=["From", "Subject"],
                ).execute()
                summaries.append(_summarise_message(full))
            return summaries
        except Exception as error:  # noqa: BLE001
            print(f"[Gmail] ERROR: list_messages failed ({error}).")
            return []

    # -- drafts ------------------------------------------------------------

    def create_draft(self, to: str, subject: str, body: str) -> dict:
        """Create a draft. The agent may do this freely; it does not send."""
        service = self._get_service()
        if service is None:
            return {"success": False, "error": "Gmail not connected. Connect Google in Settings."}
        try:
            draft = service.users().drafts().create(
                userId=_USER_ID, body={"message": {"raw": _build_raw_email(to, subject, body)}},
            ).execute()
            return {"success": True, "draft_id": draft.get("id")}
        except Exception as error:  # noqa: BLE001
            return {"success": False, "error": str(error)}

    # -- send (UI-GATED) ---------------------------------------------------

    def send_message(self, to: str, subject: str, body: str) -> dict:
        """
        Send an email. PRIVACY-CRITICAL: call this ONLY from the UI confirmation
        gate after the user explicitly approves. No agent tool may invoke it --
        the `gmail` tool refuses the `send` action so this path is never reached
        autonomously.
        """
        service = self._get_service()
        if service is None:
            return {"success": False, "error": "Gmail not connected. Connect Google in Settings."}
        try:
            sent = service.users().messages().send(
                userId=_USER_ID, body={"raw": _build_raw_email(to, subject, body)},
            ).execute()
            return {"success": True, "id": sent.get("id")}
        except Exception as error:  # noqa: BLE001
            return {"success": False, "error": str(error)}


# ---------------------------------------------------------------------------
# Smoke test (offline -- no credentials / no network)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Pure helpers.
    sample = {
        "id": "m1",
        "snippet": "Can we move our 1:1 to 4pm?",
        "payload": {"headers": [
            {"name": "From", "value": "priya@example.com"},
            {"name": "Subject", "value": "Re: Atlas review"},
        ]},
    }
    summary = _summarise_message(sample)
    assert summary == {"id": "m1", "from": "priya@example.com",
                       "subject": "Re: Atlas review", "snippet": "Can we move our 1:1 to 4pm?"}
    assert _header(sample["payload"]["headers"], "from") == "priya@example.com"
    print(f"=> summarise: {summary['from']} / '{summary['subject']}'")

    raw = _build_raw_email("a@b.com", "Hi", "the body")
    decoded = base64.urlsafe_b64decode(raw).decode("utf-8")
    assert "a@b.com" in decoded and "Hi" in decoded and "the body" in decoded
    print("=> raw email builds + base64url round-trips with to/subject/body")

    # Without credentials: unhealthy, reads empty, draft/send refused.
    connector = GmailConnector()
    if not connector._creds_path:
        assert connector.health_check() is False
        assert connector.list_messages() == []
        assert connector.create_draft("a@b.com", "x", "y")["success"] is False
        assert connector.send_message("a@b.com", "x", "y")["success"] is False
        print("=> no credentials: unhealthy, reads empty, draft/send refused (graceful)")
    else:
        print("=> credentials configured; skipping no-credential assertions")

    print("All connectors/gmail.py smoke tests passed.")
