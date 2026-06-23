"""
agent/tools.py
--------------
Tool registry, dispatcher, and tool implementations.

Every tool:
  - takes a single dict of parameters
  - returns a dict with at least {"success": bool} plus "formatted" (str for the
    LLM) on success or "error" (str) on failure
  - never raises -- dispatch_tool() is the single catch-all boundary

KG / calendar / gmail / send / code tools route LOCAL (privacy floors enforced
by routing/router.py). web_search is the only cloud-safe tool. Connector-backed
tools (calendar / gmail / slack) degrade gracefully to mock data or a
"not configured" result when real credentials are absent -- they never crash.
"""

from __future__ import annotations

import ast
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from duckduckgo_search import DDGS
from tavily import TavilyClient

from config.store import get_config
from kg.schema import get_db_connection
from kg.queries import query_kg as _kg_query
from kg.queries import find_free_slots as _kg_free_slots
from kg.queries import conflict_edges as _kg_conflicts
from tools.rag import rag_tool


# Repo-root data directory for mock connector data.
_DATA_DIR: Path = Path(__file__).resolve().parent.parent / "data"

# code_execution sandbox timeout (SPEC 4.8 / 8).
CODE_TIMEOUT_SECONDS: int = 30

# Default web search result count.
_DEFAULT_WEB_RESULTS: int = 5


# ---------------------------------------------------------------------------
# Knowledge-graph tools (LOCAL, always)
# ---------------------------------------------------------------------------

def _tool_query_kg(params: dict[str, Any]) -> dict[str, Any]:
    query = params.get("query", "")
    conn = get_db_connection(get_config().db_path)
    try:
        return _kg_query(conn, query)
    finally:
        conn.close()


def _tool_find_free_slots(params: dict[str, Any]) -> dict[str, Any]:
    # Default to today on a missing/invalid date instead of erroring -- the LLM
    # often omits the date for "find free time" with no explicit day.
    date_str = (params.get("date") or "").strip()
    try:
        datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        date_str = datetime.date.today().isoformat()
    duration = int(params.get("duration_minutes", 60))
    conn = get_db_connection(get_config().db_path)
    try:
        return _kg_free_slots(conn, date_str, duration)
    finally:
        conn.close()


def _tool_conflict_edges(params: dict[str, Any]) -> dict[str, Any]:
    days = int(params.get("days", 7))
    conn = get_db_connection(get_config().db_path)
    try:
        return _kg_conflicts(conn, days)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Web search (CLOUD-safe) -- Tavily, falling back to DuckDuckGo
# ---------------------------------------------------------------------------

def _tool_web_search(params: dict[str, Any]) -> dict[str, Any]:
    query = params.get("query", "")
    if not query.strip():
        return {"success": False, "error": "No search query provided."}
    max_results = int(params.get("max_results", _DEFAULT_WEB_RESULTS))

    cfg = get_config()
    results: list[dict[str, str]] = []
    source = "duckduckgo"

    # Prefer Tavily when a key is set; fall back to DuckDuckGo if Tavily errors
    # (bad key / quota) OR returns nothing, so web search is never fully dead.
    if cfg.tavily_api_key:
        try:
            client = TavilyClient(api_key=cfg.tavily_api_key)
            response = client.search(query=query, max_results=max_results)
            for item in response.get("results", []):
                results.append({"title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "content": item.get("content", "")})
            source = "tavily"
        except Exception as error:  # noqa: BLE001 -- degrade to DuckDuckGo
            print(f"[web_search] Tavily failed ({error}); falling back to DuckDuckGo.")

    if not results:
        source = "duckduckgo"
        with DDGS() as ddgs:
            for item in ddgs.text(query, max_results=max_results):
                results.append({"title": item.get("title", ""),
                                "url": item.get("href", ""),
                                "content": item.get("body", "")})

    if not results:
        return {"success": True, "formatted": "No web results found.", "results": []}

    lines = [f"- {r['title']}: {r['content'][:200]} ({r['url']})" for r in results]
    return {"success": True, "formatted": "\n".join(lines), "results": results, "source": source}


# ---------------------------------------------------------------------------
# Code execution (LOCAL subprocess sandbox)
# ---------------------------------------------------------------------------

def _wrap_last_expression(code: str, tree: ast.Module) -> str:
    """If the final statement is a bare expression, print its repr (REPL-style)."""
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        assign = ast.parse("___km_last = None").body[0]
        assign.value = tree.body[-1].value  # type: ignore[attr-defined]
        printer = ast.parse(
            "if ___km_last is not None:\n    print(repr(___km_last))"
        ).body[0]
        tree.body = tree.body[:-1] + [assign, printer]
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)
    return code


def _tool_code_execution(params: dict[str, Any]) -> dict[str, Any]:
    code = params.get("code", "")
    if not code.strip():
        return {"success": False, "error": "No code provided."}

    # Syntax-validate before running.
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return {"success": False, "error": f"SyntaxError: {error}"}

    wrapped = _wrap_last_expression(code, tree)

    # Separate process with a hard timeout. Best-effort sandbox: isolated
    # interpreter, no shell. (Full network isolation is week-8 hardening.)
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-c", wrapped],
            capture_output=True, text=True, timeout=CODE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Timed out after {CODE_TIMEOUT_SECONDS}s"}

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        return {"success": False, "error": stderr or "Non-zero exit",
                "formatted": (stdout + "\n" + stderr).strip()}
    return {"success": True, "output": stdout, "formatted": stdout or "(no output)"}


# ---------------------------------------------------------------------------
# RAG query (LOCAL)
# ---------------------------------------------------------------------------

def _tool_rag_query(params: dict[str, Any]) -> dict[str, Any]:
    query = params.get("query", "")
    top_k = int(params.get("top_k", 5))
    return rag_tool.query(query, top_k)


# ---------------------------------------------------------------------------
# Connector-backed tools (LOCAL) -- graceful mock / not-configured fallbacks
# ---------------------------------------------------------------------------

def _load_mock(filename: str) -> Any:
    path = _DATA_DIR / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _tool_google_calendar(params: dict[str, Any]) -> dict[str, Any]:
    from connectors.calendar import GoogleCalendarConnector
    action = params.get("action", "list")
    connector = GoogleCalendarConnector()
    live = connector.health_check()

    if action == "create":
        if not live:
            return {"success": False,
                    "formatted": "Calendar not connected. Connect Google in Settings to create events.",
                    "error": "google_calendar not configured"}
        event = params.get("event", {})
        summary = params.get("summary") or event.get("summary", "(no title)")
        start = params.get("start") or event.get("start")
        end = params.get("end") or event.get("end")
        if not start or not end:
            return {"success": False, "formatted": "Create requires ISO 'start' and 'end' times.",
                    "error": "missing start/end"}
        result = connector.create_event(summary, start, end, params.get("attendees"))
        if result.get("success"):
            return {"success": True, "formatted": f"Created event '{summary}'.",
                    "id": result.get("id"), "htmlLink": result.get("htmlLink")}
        return {"success": False, "formatted": f"Create failed: {result.get('error')}",
                "error": result.get("error")}

    # list / free_slots: prefer the live calendar, else fall back to mock data.
    if live:
        events = connector.list_events()
        lines = [
            f"- {event.get('summary', 'Event')} at "
            f"{event.get('start', {}).get('dateTime', event.get('start', {}).get('date', '?'))}"
            for event in events
        ]
        body = "\n".join(lines) if lines else "(no upcoming events)"
        return {"success": True, "formatted": "Calendar events (live):\n" + body, "events": events}

    # Mock fallback: events with dates computed relative to today.
    from connectors.mock import mock_calendar_events
    events = mock_calendar_events()
    if not events:
        return {"success": True, "formatted": "No calendar data available (mock or live).",
                "events": []}
    lines = [
        f"- {event.get('summary', 'Event')} at {event.get('start', '?')}"
        for event in events
    ]
    return {"success": True, "formatted": "Calendar events (mock):\n" + "\n".join(lines),
            "events": events}


def _tool_gmail(params: dict[str, Any]) -> dict[str, Any]:
    from connectors.gmail import GmailConnector
    action = params.get("action", "list")

    # PRIVACY rule 6: the agent may draft but must NEVER send. Send is refused
    # here -- the only send path is the UI confirmation gate in ui/app.py, which
    # calls GmailConnector.send_message() directly. This tool never does.
    if action == "send":
        return {"success": False,
                "formatted": "Sending email requires explicit confirmation in the UI. Draft instead.",
                "error": "send blocked: UI confirmation required"}

    connector = GmailConnector()
    live = connector.health_check()

    if action == "draft":
        message = params.get("message", {})
        recipient = params.get("to") or message.get("to", "")
        subject = params.get("subject") or message.get("subject", "(no subject)")
        body = params.get("body") or message.get("body", "")
        if live:
            result = connector.create_draft(recipient, subject, body)
            if result.get("success"):
                return {"success": True,
                        "formatted": f"Draft created: '{subject}' to {recipient or '(unspecified)'}. "
                                     f"Review and confirm in the UI to send.",
                        "draft_id": result.get("draft_id")}
            return {"success": False, "formatted": f"Draft failed: {result.get('error')}",
                    "error": result.get("error")}
        return {"success": True,
                "formatted": f"Draft prepared (mock): '{subject}' to {recipient or '(unspecified)'}. "
                             f"Review and confirm in the UI to send.",
                "draft": {"to": recipient, "subject": subject, "body": body}}

    # list / read: prefer the live inbox, else mock data.
    if live:
        messages = connector.list_messages()
        lines = [f"- {message['from']}: {message['subject']}" for message in messages]
        body = "\n".join(lines) if lines else "(no messages)"
        return {"success": True, "formatted": "Inbox (live):\n" + body, "messages": messages}

    messages = _load_mock("mock_gmail.json")
    if messages is None:
        return {"success": True, "formatted": "No email data available (mock or live).",
                "messages": []}
    lines = [f"- {m.get('from', '?')}: {m.get('subject', '')}" for m in messages]
    return {"success": True, "formatted": "Inbox (mock):\n" + "\n".join(lines), "messages": messages}


def _tool_send_message(params: dict[str, Any]) -> dict[str, Any]:
    from connectors.slack import SlackConnector
    channel = params.get("channel", "")
    text = params.get("text", "")
    if not text.strip():
        return {"success": False, "error": "No message text provided."}

    connector = SlackConnector()
    if not connector.health_check():
        # Dry-run when Slack is not configured / unreachable -- never crash.
        return {"success": True,
                "formatted": f"[dry-run] Would send to '{channel or 'default'}': {text}",
                "dry_run": True}

    result = connector.send_message(channel, text)
    if result.get("success"):
        return {"success": True, "formatted": f"Message sent to {channel}.",
                "ts": result.get("ts")}
    return {"success": False, "formatted": f"Send failed: {result.get('error')}",
            "error": result.get("error")}


# ---------------------------------------------------------------------------
# Registry + dispatcher
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "query_kg":        _tool_query_kg,
    "find_free_slots": _tool_find_free_slots,
    "conflict_edges":  _tool_conflict_edges,
    "web_search":      _tool_web_search,
    "code_execution":  _tool_code_execution,
    "rag_query":       _tool_rag_query,
    "google_calendar": _tool_google_calendar,
    "gmail":           _tool_gmail,
    "send_message":    _tool_send_message,
}


def dispatch_tool(tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """
    Look up and run a tool by name. Single catch-all boundary: any exception a
    tool raises is converted to {"success": False, "error": ...} so the agent
    loop never crashes (SPEC 4.8 / 8).
    """
    handler = TOOL_REGISTRY.get(tool_name)
    if handler is None:
        return {"success": False, "error": f"Unknown tool '{tool_name}'.",
                "formatted": f"No such tool: {tool_name}"}
    try:
        return handler(parameters or {})
    except Exception as error:  # noqa: BLE001 -- tools must never crash the loop
        return {"success": False, "error": str(error),
                "formatted": f"Tool '{tool_name}' failed: {error}"}


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["KM_DB_PATH"] = str(Path(tmp) / "tools.db")
        cfg = get_config()
        cfg.db_path = os.environ["KM_DB_PATH"]

        # Unknown tool -> graceful failure, no raise.
        unknown = dispatch_tool("does_not_exist", {})
        assert unknown["success"] is False, "unknown tool should fail gracefully"
        print("=> unknown tool handled gracefully")

        # code_execution: last-expression capture.
        code_result = dispatch_tool("code_execution", {"code": "x = 6 * 7\nx"})
        assert code_result["success"] and code_result["formatted"] == "42", code_result
        print(f"=> code_execution returned: {code_result['formatted']}")

        # code_execution timeout path is honoured (quick sanity, not a long sleep).
        syntax = dispatch_tool("code_execution", {"code": "def ("})
        assert syntax["success"] is False, "syntax error should fail"
        print("=> code_execution rejects bad syntax")

        # gmail send must be refused (privacy rule 6).
        send = dispatch_tool("gmail", {"action": "send", "message": {"to": "x@y.z"}})
        assert send["success"] is False, "gmail send must be blocked"
        print("=> gmail send correctly blocked (needs UI confirm)")

        # send_message dry-runs without a Slack token.
        slack = dispatch_tool("send_message", {"channel": "general", "text": "hi"})
        assert slack["success"] and slack.get("dry_run"), "expected dry-run send"
        print("=> send_message dry-run ok")

        # query_kg works against an empty DB.
        kg = dispatch_tool("query_kg", {"query": "anything"})
        assert kg["success"], "query_kg should succeed on empty DB"
        print("=> query_kg ok on empty DB")

    print("All agent/tools.py smoke tests passed.")
