"""
ui/app.py
---------
Main Gradio UI for KnowledgeMind.

Five tabs:
  1. Chat      — message input + agency level selector + routing log + token panel
  2. KG View   — live pyvis knowledge graph
  3. Monitor   — FSM status + alert feed
  4. Documents — RAG file upload + indexed doc list
  5. Settings  — re-expose config (model, keys)
"""

from __future__ import annotations

import html as html_lib
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gradio as gr
from config.store import get_config, save_config, reload_config, AppConfig
from agent.orchestrator import HybridMindAgent, AgencyLevel, LEVEL_LABELS

# ---------------------------------------------------------------------------
# Global agent (one per UI session — Gradio shares state across requests
# via gr.State, but agent holds session_id so we use one per browser session)
# ---------------------------------------------------------------------------

_AGENT: HybridMindAgent | None = None

def get_agent() -> HybridMindAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = HybridMindAgent()
    return _AGENT


# ---------------------------------------------------------------------------
# Level trade-off table (from course slides — static reference)
# ---------------------------------------------------------------------------

LEVEL_TRADEOFF_HTML = """
<table style="width:100%; font-size:0.82em; border-collapse:collapse">
  <thead>
    <tr style="background:#1B3A6B; color:white">
      <th style="padding:6px 8px; text-align:left">Dimension</th>
      <th style="padding:6px 8px; text-align:center">L1 Augmented</th>
      <th style="padding:6px 8px; text-align:center">L2 Workflow</th>
      <th style="padding:6px 8px; text-align:center">L3 Autonomous</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background:rgba(128,128,128,0.15)">
      <td style="padding:5px 8px">Autonomy</td>
      <td style="padding:5px 8px; text-align:center">🟢 Low</td>
      <td style="padding:5px 8px; text-align:center">🟡 Medium</td>
      <td style="padding:5px 8px; text-align:center">🔴 High</td>
    </tr>
    <tr>
      <td style="padding:5px 8px">Predictability</td>
      <td style="padding:5px 8px; text-align:center">🟢 High</td>
      <td style="padding:5px 8px; text-align:center">🟡 Medium</td>
      <td style="padding:5px 8px; text-align:center">🔴 Low</td>
    </tr>
    <tr style="background:rgba(128,128,128,0.15)">
      <td style="padding:5px 8px">Token Cost</td>
      <td style="padding:5px 8px; text-align:center">🟢 Low</td>
      <td style="padding:5px 8px; text-align:center">🟡 Medium</td>
      <td style="padding:5px 8px; text-align:center">🔴 High</td>
    </tr>
    <tr>
      <td style="padding:5px 8px">Flexibility</td>
      <td style="padding:5px 8px; text-align:center">🔴 Low</td>
      <td style="padding:5px 8px; text-align:center">🟡 Medium</td>
      <td style="padding:5px 8px; text-align:center">🟢 High</td>
    </tr>
    <tr style="background:rgba(128,128,128,0.15)">
      <td style="padding:5px 8px">Control Flow</td>
      <td style="padding:5px 8px; text-align:center" colspan="2">Engineer-defined</td>
      <td style="padding:5px 8px; text-align:center">LLM-directed</td>
    </tr>
    <tr>
      <td style="padding:5px 8px">Replanning</td>
      <td style="padding:5px 8px; text-align:center">None</td>
      <td style="padding:5px 8px; text-align:center">None</td>
      <td style="padding:5px 8px; text-align:center">Up to 3×</td>
    </tr>
    <tr style="background:rgba(128,128,128,0.15)">
      <td style="padding:5px 8px">Typical tokens</td>
      <td style="padding:5px 8px; text-align:center">~650</td>
      <td style="padding:5px 8px; text-align:center">~1,800</td>
      <td style="padding:5px 8px; text-align:center">~4,500</td>
    </tr>
  </tbody>
</table>
<p style="font-size:0.75em; color:#888; margin:4px 0 0 0">
Note: token counts are highly use-case dependent. L3 agents can replan up to 3 times, so may consume more tokens than L2.
</p>
"""


# ---------------------------------------------------------------------------
# Routing log renderer
# ---------------------------------------------------------------------------

def _render_routing_log(routing_log: list[dict]) -> str:
    if not routing_log:
        return ""
    # Rich markdown with green/yellow badges. NO pipe '|' (gradio can parse it
    # as a table) and NO &nbsp; entities -- both of those hid the block before.
    lines = ["**Routing decisions**", ""]
    for log in routing_log:
        decision = log["decision"].upper()
        badge = "🟢 LOCAL" if decision == "LOCAL" else "🟡 CLOUD"
        escalated = " ↑escalated" if log.get("escalated") else ""
        lines.append(
            f"- **Step {log['step_id']}** — `{log['tool']}` → **{badge}**{escalated} "
            f"*(privacy {log['privacy_score']:.2f}, complexity {log['complexity_score']:.2f})*"
        )
        lines.append(f"  - {log['reason']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Token panel renderer
# ---------------------------------------------------------------------------

def _render_token_panel(token_summary, agency_level: str) -> str:
    if token_summary is None:
        return ""

    level_emoji = {"L1": "⚡", "L2": "⚙️", "L3": "🤖"}.get(agency_level, "")
    header = f"**{level_emoji} Token Consumption — {token_summary.level_label}**\n\n"
    body = f"```\n{token_summary.formatted_breakdown()}\n```"
    return header + body


# ---------------------------------------------------------------------------
# Chat handler
# ---------------------------------------------------------------------------

# Map radio label -> AgencyLevel enum.
_LEVEL_MAP = {
    "L1 — Augmented LLM (single call, lowest tokens)":   AgencyLevel.L1_AUGMENTED,
    "L2 — Workflow (plan→execute→critique)":             AgencyLevel.L2_WORKFLOW,
    "L3 — Autonomous Agent (ReAct loop, most capable)":  AgencyLevel.L3_AUTONOMOUS,
}


def _message_text(content) -> str:
    """Extract plain text from a gradio message 'content' (str or parts list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content)


def user_turn(message: str, history: list) -> tuple[str, list]:
    """
    Step 1 (instant): echo the user's message into the chat and clear the box,
    BEFORE the agent runs. Returns (cleared_input, updated_history).
    """
    if not message.strip():
        return message, history
    history = (history or []) + [{"role": "user", "content": message}]
    return "", history


def bot_turn(
    history: list,
    agency_level_str: str,
    show_routing: bool,
    show_tokens: bool,
) -> tuple[list, str, str]:
    """
    Step 2 (after echo): run the agent on the latest user message and append the
    reply. Returns (updated_history, routing_md, token_md).
    """
    if not history:
        return history, "", ""
    message = _message_text(history[-1].get("content", ""))

    agent = get_agent()
    agency_level = _LEVEL_MAP.get(agency_level_str, AgencyLevel.L2_WORKFLOW)
    result = agent.run(message, agency_level=agency_level)

    answer        = result.get("answer", "No answer returned.")
    routing_log   = result.get("routing_log", [])
    token_summary = result.get("token_summary")
    elapsed       = result.get("elapsed", 0)
    al            = result.get("agency_level", "L2")
    step_count    = len(routing_log)

    step_str = f"{step_count} tool call{'s' if step_count != 1 else ''}" if step_count else "direct answer"
    meta = f"\n\n---\n*{LEVEL_LABELS.get(agency_level, al)} · {step_str} · {elapsed:.1f}s · Session: {agent.session_id}*"

    history = history + [{"role": "assistant", "content": answer + meta}]
    routing_md = _render_routing_log(routing_log) if show_routing else ""
    token_md   = _render_token_panel(token_summary, al) if show_tokens else ""
    return history, routing_md, token_md


# ---------------------------------------------------------------------------
# Document upload
# ---------------------------------------------------------------------------

def upload_document(file) -> str:
    if file is None:
        return "No file selected."
    # gradio 6 gr.File passes a string filepath; older versions pass an object
    # with .name. Handle both so the upload does not crash in the browser.
    path = file if isinstance(file, str) else getattr(file, "name", str(file))
    agent = get_agent()
    result = agent.add_document(path)
    added   = result.get("added", [])
    skipped = result.get("skipped", [])
    chunks  = result.get("chunks", 0)
    parts = []
    if added:
        parts.append(f"✓ Indexed: {', '.join(added)} ({chunks} chunks)")
    if skipped:
        parts.append(f"⚠ Skipped: {', '.join(skipped)}")
    return "\n".join(parts) if parts else "Nothing indexed."


def list_documents() -> str:
    try:
        from tools.rag import rag_tool
        docs = rag_tool.list_documents()
        if not docs:
            return "No documents indexed yet."
        return "**Indexed documents:**\n" + "\n".join(f"• {d}" for d in docs)
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Gmail send-confirmation gate (PRIVACY rule 6 / rule 4)
# ---------------------------------------------------------------------------

def send_email(to: str, subject: str, body: str, confirmed: bool) -> str:
    """
    The ONLY path in the system that actually sends email. The agent's `gmail`
    tool refuses `send`; sending requires the user to tick the confirmation box
    and click Send here. This gate must never be reachable from an agent tool.
    """
    if not confirmed:
        return "Tick 'I confirm sending this email' first."
    if not to.strip() or not body.strip():
        return "Recipient and body are required."
    from connectors.gmail import GmailConnector
    connector = GmailConnector()
    if not connector.health_check():
        return "Gmail not connected. Connect Google in Settings first."
    result = connector.send_message(to.strip(), subject.strip(), body)
    if result.get("success"):
        return f"✓ Sent to {to.strip()} (message id {result.get('id')})."
    return f"Send failed: {result.get('error')}"


# ---------------------------------------------------------------------------
# KG Visualisation
# ---------------------------------------------------------------------------

def render_kg() -> str:
    """Render the KG as a pyvis HTML graph and return as HTML string."""
    try:
        from pyvis.network import Network
        import networkx as nx
        from kg.graph import build_graph
        from kg.schema import get_db_connection
        from config.store import get_config

        cfg = get_config()
        conn = get_db_connection(cfg.db_path)
        G = build_graph(conn)
        conn.close()

        if len(G.nodes) == 0:
            return "<p style='color:#888; padding:20px'>Knowledge graph is empty. Connect a data source or load mock data.</p>"

        # cdn_resources='remote': load vis.js from a CDN instead of local
        # lib/ files (which do not exist on the gradio server).
        net = Network(height="400px", width="100%", bgcolor="#f8f9fa",
                      font_color="#1B3A6B", cdn_resources="remote")
        net.from_nx(G)

        # Style nodes by type
        for node in net.nodes:
            ntype = node.get("type", "")
            if ntype == "Person":
                node["color"] = "#2E6DB4"
                node["size"]  = 20
            elif ntype == "Commitment":
                ctype = node.get("commitment_type", "")
                node["color"] = "#1A6B3A" if ctype == "HARD" else "#E07B00" if ctype == "SOFT" else "#888888"
                node["size"]  = 14
            elif ntype == "TimeSlot":
                node["color"] = "#8B0000"
                node["size"]  = 10

        net.set_options('{"physics": {"stabilization": {"iterations": 100}}}')
        document = net.generate_html(notebook=False)

        # Embed in an iframe via srcdoc so the vis.js <script> tags actually
        # execute (scripts injected directly into gr.HTML do not run).
        return (
            f'<iframe srcdoc="{html_lib.escape(document, quote=True)}" '
            f'style="width:100%; height:430px; border:none;"></iframe>'
        )

    except Exception as e:
        return f"<p style='color:red; padding:20px'>KG render error: {e}</p>"


# ---------------------------------------------------------------------------
# Monitor panel
# ---------------------------------------------------------------------------

def get_monitor_status() -> str:
    cfg = get_config()
    alerts_path = Path(cfg.alerts_log_path)
    if not alerts_path.exists():
        return "No alerts yet. Monitor loop hasn't run or no conflicts detected."

    try:
        lines = alerts_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            return "No alerts yet."
        # Show last 10 alerts
        recent = lines[-10:]
        formatted = []
        for line in reversed(recent):
            try:
                alert = json.loads(line)
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(alert.get("timestamp", 0)))
                formatted.append(f"**{ts}** — {alert.get('message', 'Alert')}")
            except json.JSONDecodeError:
                formatted.append(line)
        return "\n\n".join(formatted)
    except Exception as e:
        return f"Error reading alerts: {e}"


def get_monitor_state() -> str:
    """Render the FSM status indicator + last poll time for the Monitor tab."""
    from monitor.fsm import monitor_runner

    state = monitor_runner.latest_state
    if state is None:
        return "**FSM status:** idle — no cycle has run yet."

    last_poll = monitor_runner.last_poll_ts
    when = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_poll))
        if last_poll else "never"
    )
    status = "ERROR" if state.get("error") else "OK"
    lines = [
        f"**FSM status:** {status}",
        f"**Cycles run:** {state.get('cycle_count', 0)}",
        f"**Last poll:** {when}",
        (f"**Last cycle:** {len(state.get('new_messages', []))} msgs, "
         f"{len(state.get('new_commitments', []))} commitments, "
         f"{len(state.get('new_conflicts', []))} conflicts, "
         f"{state.get('alerts_fired', 0)} alerts"),
    ]
    if state.get("error"):
        lines.append(f"**Error:** {state['error']}")
    return "  \n".join(lines)


def run_monitor_cycle() -> tuple[str, str]:
    """Manual poll trigger: run one FSM cycle, then refresh state + alert feed."""
    from monitor.fsm import monitor_runner

    monitor_runner.run_once()
    return get_monitor_state(), get_monitor_status()


def refresh_monitor_view() -> tuple[str, str]:
    """Display-only refresh (used by the auto-refresh timer): no new poll."""
    return get_monitor_state(), get_monitor_status()


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

def reset_session() -> tuple[list, str, str, str]:
    global _AGENT
    _AGENT = HybridMindAgent()
    return [], "", "", f"Session reset. New ID: {_AGENT.session_id}"


# ---------------------------------------------------------------------------
# Settings save
# ---------------------------------------------------------------------------

def save_settings(local_model, groq_key, tavily_key, slack_token, google_creds, threshold_str) -> str:
    try:
        threshold = float(threshold_str)
    except ValueError:
        return "Invalid complexity threshold — must be a number between 0 and 1."
    cfg = get_config()
    cfg.local_model              = local_model
    cfg.groq_api_key             = groq_key
    cfg.tavily_api_key           = tavily_key
    cfg.slack_bot_token          = slack_token
    cfg.google_credentials_path  = google_creds.strip()
    cfg.complexity_threshold     = threshold
    save_config(cfg)
    reload_config()
    return "✓ Settings saved."


# ---------------------------------------------------------------------------
# Google OAuth connect (Calendar / Gmail)
# ---------------------------------------------------------------------------

def _connect_google(creds_path: str, service: str) -> str:
    """
    Persist the credentials path, then run the interactive OAuth consent for the
    chosen Google service. This opens a browser for the user to authorise; it
    blocks until they finish. Returns a human-readable status.
    """
    creds_path = (creds_path or "").strip()
    if not creds_path:
        return "Enter the Google OAuth credentials path first, then connect."
    cfg = get_config()
    cfg.google_credentials_path = creds_path
    save_config(cfg)
    reload_config()

    if service == "calendar":
        from connectors.calendar import GoogleCalendarConnector
        result = GoogleCalendarConnector().connect()
    else:
        from connectors.gmail import GmailConnector
        result = GmailConnector().connect()

    if result.get("success"):
        return f"✓ {result.get('message', 'Connected.')}"
    return f"Connection failed: {result.get('error')}"


def connect_calendar(creds_path: str) -> str:
    return _connect_google(creds_path, "calendar")


def connect_gmail(creds_path: str) -> str:
    return _connect_google(creds_path, "gmail")


# Cloud-model dropdown options (label -> uses the 8B fast model when True).
_CLOUD_MODEL_FAST_LABEL = "Llama 3.1 8B (fast, saves tokens)"
_CLOUD_MODEL_QUALITY_LABEL = "Llama 3.3 70B (higher quality)"
_CLOUD_MODEL_CHOICES = [_CLOUD_MODEL_FAST_LABEL, _CLOUD_MODEL_QUALITY_LABEL]


def _cloud_model_label(use_fast: bool) -> str:
    return _CLOUD_MODEL_FAST_LABEL if use_fast else _CLOUD_MODEL_QUALITY_LABEL


def set_cloud_model(label: str) -> None:
    """Persist the cloud planning/critique model choice from the dropdown."""
    cfg = get_config()
    cfg.use_fast_cloud_model = (label == _CLOUD_MODEL_FAST_LABEL)
    save_config(cfg)  # the singleton is updated in place; _call_groq reads it


# ---------------------------------------------------------------------------
# Build UI
# ---------------------------------------------------------------------------

def build_main_ui(cfg: AppConfig) -> gr.Blocks:
    with gr.Blocks(
        title="KnowledgeMind",
        theme=gr.themes.Soft(primary_hue="blue"),
        css="""
            footer { display: none !important; }
            .token-panel { font-family: monospace; font-size: 0.82em; }
            .level-table { margin-bottom: 8px; }
            /* Force the trade-off table header cells white (dark-blue bg) so they
               are readable in light mode too (theme otherwise makes them black). */
            .level-table th { color: #ffffff !important; }
        """,
    ) as demo:

        gr.HTML("""
            <div style="text-align:center; padding:16px 0 8px 0">
                <h1 style="color:#3b82f6; font-size:2em; margin:0">🧠 KnowledgeMind</h1>
                <p style="color:var(--body-text-color-subdued); margin:2px 0">
                    Privacy-Aware Personal AI Agent · IISc Bengaluru
                </p>
            </div>
        """)

        with gr.Tabs():

            # ── Tab 1: Chat ────────────────────────────────────────────────
            with gr.TabItem("💬 Chat"):
                with gr.Row():

                    # Left column: chat + controls
                    with gr.Column(scale=3):
                        # Cloud model selector, on top of the chat window.
                        cloud_model_dropdown = gr.Dropdown(
                            choices=_CLOUD_MODEL_CHOICES,
                            value=_cloud_model_label(cfg.use_fast_cloud_model),
                            label="Cloud model (planning / critique)",
                            info="8B saves Groq free-tier tokens; 70B is higher quality.",
                            interactive=True,
                        )
                        chatbot = gr.Chatbot(
                            label="Conversation",
                            height=420,
                            # gradio 6 uses the dict messages format exclusively
                            # (no `type` arg); chat() appends {"role","content"}.
                            render_markdown=True,
                        )
                        with gr.Row():
                            msg_input = gr.Textbox(
                                placeholder="Ask anything — scheduling, web search, documents, calendar...",
                                label="Message",
                                show_label=False,
                                container=False,
                                scale=5,
                            )
                            send_btn = gr.Button("Send", variant="primary", scale=1)

                        # Agency Level selector
                        gr.Markdown("### Agency Level")
                        agency_radio = gr.Radio(
                            choices=[
                                "L1 — Augmented LLM (single call, lowest tokens)",
                                "L2 — Workflow (plan→execute→critique)",
                                "L3 — Autonomous Agent (ReAct loop, most capable)",
                            ],
                            value="L2 — Workflow (plan→execute→critique)",
                            label="Agency level",
                            show_label=False,
                            info="Select agentic autonomy level. Higher = more capable but more tokens.",
                        )

                        with gr.Row():
                            show_routing = gr.Checkbox(label="Show routing log", value=True)
                            show_tokens  = gr.Checkbox(label="Show token consumption", value=True)
                            reset_btn    = gr.Button("Reset session", variant="secondary")

                        routing_panel = gr.Markdown(label="Routing Log")
                        token_panel   = gr.Markdown(label="Token Consumption", elem_classes=["token-panel"])

                        # Gmail send-confirmation gate (PRIVACY rule 6 / rule 4):
                        # the only place email is actually sent. The agent can
                        # draft but never send; the user must confirm + click.
                        with gr.Accordion("Compose & Send Email (confirmation gate)", open=False):
                            email_to      = gr.Textbox(label="To", placeholder="name@example.com")
                            email_subject = gr.Textbox(label="Subject")
                            email_body    = gr.Textbox(label="Body", lines=4)
                            email_confirm = gr.Checkbox(label="I confirm sending this email", value=False)
                            email_send_btn = gr.Button("Send email", variant="stop")
                            email_status  = gr.Markdown()

                    # Right column: level reference table
                    with gr.Column(scale=1):
                        gr.Markdown("### Level Trade-offs")
                        gr.HTML(LEVEL_TRADEOFF_HTML, elem_classes=["level-table"])

                        gr.Markdown("### Example queries")
                        gr.Markdown("""
**L1 (fast):**
- What is attention in transformers?
- Define softmax

**L2 (structured):**
- What's on my calendar today?
- Book a 1hr slot tomorrow at 3pm

**L3 (autonomous):**
- Research recent LLM papers and check if any conflict with my meetings
- Find free time, check my emails for pending items, summarise my week
""")
                        reset_status = gr.Textbox(label="Status", interactive=False, lines=1)

            # ── Tab 2: Knowledge Graph ─────────────────────────────────────
            with gr.TabItem("🕸️ Knowledge Graph"):
                gr.Markdown("Live view of your personal knowledge graph. Auto-refreshes every 60s.")
                with gr.Row():
                    refresh_kg_btn = gr.Button("Refresh Graph", variant="secondary")
                kg_html = gr.HTML(value="<p style='color:#888'>Loading graph…</p>")
                kg_timer = gr.Timer(60)  # auto-refresh every 60s

            # ── Tab 3: Monitor ─────────────────────────────────────────────
            with gr.TabItem("📡 Monitor"):
                gr.Markdown("Background monitor status and proactive conflict alerts. Auto-refreshes every 15s.")
                monitor_state_md = gr.Markdown(value=get_monitor_state())
                with gr.Row():
                    run_poll_btn = gr.Button("Run poll now", variant="primary")
                    refresh_monitor_btn = gr.Button("Refresh Alerts", variant="secondary")
                gr.Markdown("### Alerts")
                monitor_output = gr.Markdown(value=get_monitor_status())
                monitor_timer = gr.Timer(15)  # auto-refresh every 15s

            # ── Tab 4: Documents ───────────────────────────────────────────
            with gr.TabItem("📄 Documents"):
                gr.Markdown("Upload documents to the local RAG knowledge base.")
                upload_input  = gr.File(label="Upload PDF / TXT / MD", file_types=[".pdf", ".txt", ".md"])
                upload_status = gr.Textbox(label="Upload status", interactive=False)
                gr.HTML("<hr>")
                doc_list_btn  = gr.Button("List indexed documents")
                doc_list_out  = gr.Markdown()

            # ── Tab 5: Settings ────────────────────────────────────────────
            with gr.TabItem("⚙️ Settings"):
                gr.Markdown("Update configuration without restarting. Saved immediately.")
                settings_model    = gr.Textbox(label="Local model", value=cfg.local_model)
                settings_groq     = gr.Textbox(label="Groq API Key", value=cfg.groq_api_key, type="password")
                settings_tavily   = gr.Textbox(label="Tavily API Key (optional)", value=cfg.tavily_api_key, type="password")
                settings_slack    = gr.Textbox(label="Slack Bot Token (optional)", value=cfg.slack_bot_token, type="password")
                settings_google   = gr.Textbox(
                    label="Google OAuth credentials path (Calendar + Gmail)",
                    value=cfg.google_credentials_path,
                    placeholder="./credentials.json",
                )
                settings_threshold = gr.Textbox(
                    label="Complexity threshold (L2/L3 cloud routing cutoff, 0.0–1.0)",
                    value=str(cfg.complexity_threshold),
                )
                settings_save_btn = gr.Button("Save settings", variant="primary")
                settings_status   = gr.Textbox(label="Status", interactive=False)

                gr.Markdown("### Connect Google")
                gr.Markdown(
                    "Authorise Calendar and Gmail. Each opens a browser for "
                    "one-time consent and saves a token locally."
                )
                with gr.Row():
                    connect_calendar_btn = gr.Button("Connect Google Calendar", variant="secondary")
                    connect_gmail_btn    = gr.Button("Connect Gmail", variant="secondary")
                google_status = gr.Markdown()

        # ── Event wiring ───────────────────────────────────────────────────

        # Two-step chain: user_turn echoes the message instantly (queue=False),
        # then bot_turn runs the agent and appends the reply.
        send_btn.click(
            user_turn,
            inputs=[msg_input, chatbot],
            outputs=[msg_input, chatbot],
            queue=False,
        ).then(
            bot_turn,
            inputs=[chatbot, agency_radio, show_routing, show_tokens],
            outputs=[chatbot, routing_panel, token_panel],
        )

        msg_input.submit(
            user_turn,
            inputs=[msg_input, chatbot],
            outputs=[msg_input, chatbot],
            queue=False,
        ).then(
            bot_turn,
            inputs=[chatbot, agency_radio, show_routing, show_tokens],
            outputs=[chatbot, routing_panel, token_panel],
        )

        reset_btn.click(
            reset_session,
            outputs=[chatbot, routing_panel, token_panel, reset_status],
        )

        refresh_kg_btn.click(render_kg, outputs=kg_html)
        run_poll_btn.click(run_monitor_cycle, outputs=[monitor_state_md, monitor_output])
        refresh_monitor_btn.click(get_monitor_status, outputs=monitor_output)

        # Auto-refresh timers (display-only; do not trigger a monitor poll).
        kg_timer.tick(render_kg, outputs=kg_html)
        monitor_timer.tick(refresh_monitor_view, outputs=[monitor_state_md, monitor_output])

        # Initial render of the KG on page load (timer only fires after 60s).
        demo.load(render_kg, outputs=kg_html)
        demo.load(refresh_monitor_view, outputs=[monitor_state_md, monitor_output])

        # The send-confirmation gate: the sole caller of Gmail send.
        email_send_btn.click(
            send_email,
            inputs=[email_to, email_subject, email_body, email_confirm],
            outputs=email_status,
        )

        upload_input.change(upload_document, inputs=upload_input, outputs=upload_status)
        doc_list_btn.click(list_documents, outputs=doc_list_out)

        settings_save_btn.click(
            save_settings,
            inputs=[settings_model, settings_groq, settings_tavily, settings_slack,
                    settings_google, settings_threshold],
            outputs=settings_status,
        )

        connect_calendar_btn.click(connect_calendar, inputs=settings_google, outputs=google_status)
        connect_gmail_btn.click(connect_gmail, inputs=settings_google, outputs=google_status)

        # Cloud-model dropdown: persists immediately, applies to the next query.
        cloud_model_dropdown.change(set_cloud_model, inputs=cloud_model_dropdown, queue=False)

    return demo


# ---------------------------------------------------------------------------
# Standalone entry (used by launcher.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = get_config()
    demo = build_main_ui(cfg)
    demo.launch(server_name="127.0.0.1", server_port=7860, show_error=True)
