"""
connectors/slack.py
-------------------
Slack read connector (SPEC 4.2). Authenticates with the bot token from config
and pulls recent messages from the channels the bot belongs to, normalising
them into RawMessage objects for the extractor.

Resilience (SPEC 8): a failed fetch logs an ERROR and returns the last
successful poll's messages (cached) instead of raising. health_check() returns
False when the token is missing or auth fails, which makes the monitor fall back
to the mock connector for this source.
"""

from __future__ import annotations

from typing import Callable, Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config.store import get_config
from connectors.base import BaseConnector, RawMessage


# Caps to keep a single poll bounded.
_MAX_CHANNELS: int = 25
_HISTORY_LIMIT: int = 100

# Message subtypes that are channel housekeeping, not human commitments.
_SKIP_SUBTYPES: frozenset[str] = frozenset({
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "channel_archive", "channel_unarchive", "bot_message",
    "message_changed", "message_deleted", "thread_broadcast",
})


def _parse_message(
    channel_id: str,
    message: dict,
    since_ts: float,
    resolve_user: Callable[[Optional[str]], str],
) -> Optional[RawMessage]:
    """
    Convert one Slack message dict to a RawMessage, or None to skip it.

    Skips non-message events, housekeeping subtypes, empty text, and anything
    older than since_ts. Pure (no network) so it can be unit-tested -- user
    resolution is delegated to the injected `resolve_user`.
    """
    if message.get("type") != "message":
        return None
    if message.get("subtype") in _SKIP_SUBTYPES:
        return None
    text = (message.get("text") or "").strip()
    if not text:
        return None
    try:
        timestamp = float(message["ts"])
    except (KeyError, ValueError, TypeError):
        return None
    if timestamp < since_ts:
        return None

    return RawMessage(
        source="slack",
        channel_id=channel_id,
        sender=resolve_user(message.get("user")),
        text=text,
        timestamp=timestamp,
        external_id=message["ts"],
    )


class SlackConnector(BaseConnector):
    """Reads recent Slack messages from the bot's channels."""

    source_name = "slack"

    def __init__(self, channels: Optional[list[str]] = None) -> None:
        """
        Args:
            channels: explicit channel IDs to read. When None, the connector
                auto-discovers every channel the bot is a member of.
        """
        cfg = get_config()
        self._token = cfg.slack_bot_token
        self._client: Optional[WebClient] = WebClient(token=self._token) if self._token else None
        self._channels = channels
        self._user_cache: dict[str, str] = {}
        self._cache: list[RawMessage] = []  # last successful fetch (SPEC 8)

    # -- health ------------------------------------------------------------

    def health_check(self) -> bool:
        """True if a token is present and auth succeeds."""
        if self._client is None:
            return False
        try:
            return bool(self._client.auth_test().get("ok"))
        except SlackApiError as error:
            print(f"[Slack] WARNING: auth_test failed ({error.response.get('error', error)}).")
            return False
        except Exception as error:  # noqa: BLE001 -- never crash on health check
            print(f"[Slack] WARNING: auth_test error ({error}).")
            return False

    # -- helpers -----------------------------------------------------------

    def _resolve_user(self, user_id: Optional[str]) -> str:
        """Resolve a user id to a display name, cached; fall back to the id."""
        if not user_id:
            return "unknown"
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        name = user_id
        try:
            info = self._client.users_info(user=user_id)  # type: ignore[union-attr]
            profile = info.get("user", {})
            name = profile.get("real_name") or profile.get("name") or user_id
        except Exception:  # noqa: BLE001 -- name resolution is best-effort
            name = user_id
        self._user_cache[user_id] = name
        return name

    def _discover_channels(self) -> list[str]:
        """Return channel ids the bot is a member of (best-effort)."""
        try:
            response = self._client.conversations_list(  # type: ignore[union-attr]
                types="public_channel,private_channel", limit=_MAX_CHANNELS,
            )
            return [
                channel["id"]
                for channel in response.get("channels", [])
                if channel.get("is_member")
            ]
        except Exception as error:  # noqa: BLE001
            print(f"[Slack] WARNING: channel discovery failed ({error}).")
            return []

    # -- fetch -------------------------------------------------------------

    def fetch_recent(self, since_ts: float) -> list[RawMessage]:
        """Pull messages newer than since_ts; on error return cached messages."""
        if self._client is None:
            return []
        try:
            channel_ids = self._channels if self._channels is not None else self._discover_channels()
            messages: list[RawMessage] = []
            for channel_id in channel_ids:
                history = self._client.conversations_history(
                    channel=channel_id, oldest=str(since_ts), limit=_HISTORY_LIMIT,
                )
                for raw in history.get("messages", []):
                    parsed = _parse_message(channel_id, raw, since_ts, self._resolve_user)
                    if parsed is not None:
                        messages.append(parsed)
            messages.sort(key=lambda message: message.timestamp)
            self._cache = messages  # remember last good poll
            return messages
        except SlackApiError as error:
            print(f"[Slack] ERROR: fetch failed ({error.response.get('error', error)}); "
                  f"returning {len(self._cache)} cached message(s).")
            return self._cache
        except Exception as error:  # noqa: BLE001
            print(f"[Slack] ERROR: fetch failed ({error}); returning cached.")
            return self._cache

    # -- send --------------------------------------------------------------

    def send_message(self, channel: str, text: str) -> dict:
        """
        Post a message to a Slack channel. Returns {"success", "ts"/"error"}.

        Slack send is a LOCAL action with no UI-confirm gate (unlike Gmail);
        the agent may send directly. Never raises (SPEC 8).
        """
        if self._client is None:
            return {"success": False, "error": "Slack not configured (no bot token)."}
        try:
            response = self._client.chat_postMessage(channel=channel, text=text)
            return {"success": bool(response.get("ok")),
                    "ts": response.get("ts"), "channel": channel}
        except SlackApiError as error:
            return {"success": False, "error": error.response.get("error", str(error))}
        except Exception as error:  # noqa: BLE001
            return {"success": False, "error": str(error)}


# ---------------------------------------------------------------------------
# Smoke test (no network / no token needed)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Without a token: unhealthy, and fetch yields nothing -> mock fallback.
    no_token = SlackConnector()
    if not no_token._token:
        assert no_token.health_check() is False, "no token should be unhealthy"
        assert no_token.fetch_recent(0.0) == [], "no token should yield no messages"
        send_result = no_token.send_message("general", "hi")
        assert send_result["success"] is False, "send without token should fail gracefully"
        print("=> no token: unhealthy + empty + send refused (monitor falls back to mock)")
    else:
        print("=> token present in config; skipping no-token assertions")

    # _parse_message logic is pure and testable offline.
    resolver = lambda uid: {"U1": "Priya"}.get(uid, uid or "unknown")

    good = _parse_message(
        "C1", {"type": "message", "user": "U1", "text": "see you at 4", "ts": "1750000000.0002"},
        0.0, resolver,
    )
    assert good is not None and good.sender == "Priya" and good.source == "slack"
    assert good.external_id == "1750000000.0002"
    print(f"=> parsed message: [{good.channel_id}] {good.sender}: {good.text}")

    # Housekeeping subtype is skipped.
    assert _parse_message("C1", {"type": "message", "subtype": "channel_join",
                                 "text": "joined", "ts": "1750000000.1"}, 0.0, resolver) is None
    # Empty text skipped.
    assert _parse_message("C1", {"type": "message", "user": "U1", "text": "  ",
                                 "ts": "1750000000.2"}, 0.0, resolver) is None
    # Older than since_ts skipped.
    assert _parse_message("C1", {"type": "message", "user": "U1", "text": "old",
                                 "ts": "100.0"}, 1750000000.0, resolver) is None
    print("=> skip rules ok (subtype / empty / older-than-cutoff)")

    print("All connectors/slack.py smoke tests passed.")
