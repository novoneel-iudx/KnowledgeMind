"""
ui/streamlit_ui.py
------------------
Streamlit implementation of the KnowledgeMind UI.

Five tabs matching the Gradio UI:
  1. Chat      — message input, agency level, routing log, token panel
  2. KG View   — live pyvis knowledge graph
  3. Monitor   — FSM status + alert feed
  4. Documents — RAG file upload + indexed doc list
  5. Settings  — config (read-only in cloud mode)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import streamlit as st

from agent.orchestrator import HybridMindAgent, AgencyLevel, LEVEL_LABELS
from config.store import get_config, save_config, reload_config


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

def _init_state() -> None:
    defaults = {
        "agent":             None,
        "messages":          [],
        "routing_log":       [],
        "token_summary":     None,
        "last_agency_level": "L2",
        "alerts":            [],
        "agency_radio": "L2 — Workflow (plan→execute→critique)",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if st.session_state.agent is None:
        st.session_state.agent = HybridMindAgent()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_LEVEL_MAP: dict[str, AgencyLevel] = {
    "L1 — Augmented LLM (single call, lowest tokens)":  AgencyLevel.L1_AUGMENTED,
    "L2 — Workflow (plan→execute→critique)":            AgencyLevel.L2_WORKFLOW,
    "L3 — Autonomous Agent (ReAct loop, most capable)": AgencyLevel.L3_AUTONOMOUS,
}

_CLOUD_MODEL_LABELS: dict[str, bool] = {
    "Llama 3.1 8B — fast, saves tokens": True,
    "Llama 3.3 70B — higher quality":    False,
}


def _render_routing_log(routing_log: list[dict]) -> str:
    if not routing_log:
        return ""
    lines = ["**Routing decisions**", ""]
    for log in routing_log:
        badge = "🟢 LOCAL" if log["decision"].upper() == "LOCAL" else "🟡 CLOUD"
        escalated = " ↑escalated" if log.get("escalated") else ""
        lines.append(
            f"- **Step {log['step_id']}** — `{log['tool']}` → **{badge}**{escalated} "
            f"*(privacy {log['privacy_score']:.2f}, complexity {log['complexity_score']:.2f})*"
        )
        lines.append(f"  - {log['reason']}")
    return "\n".join(lines)


def _render_token_panel(token_summary, agency_level: str) -> str:
    if token_summary is None:
        return ""
    emoji = {"L1": "⚡", "L2": "⚙️", "L3": "🤖"}.get(agency_level, "")
    return (
        f"**{emoji} Token Consumption — {token_summary.level_label}**\n\n"
        f"```\n{token_summary.formatted_breakdown()}\n```"
    )


# ---------------------------------------------------------------------------
# Tab 1: Chat
# ---------------------------------------------------------------------------

def _tab_chat() -> None:
    cfg = get_config()

    # ── Render existing chat history ────────────────────────────────────────
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── Handle new user input ───────────────────────────────────────────────
    if prompt := st.chat_input("Ask anything — scheduling, web search, documents, calendar..."):
        agency_level = _LEVEL_MAP.get(
            st.session_state.agency_radio, AgencyLevel.L2_WORKFLOW
        )

        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                result = st.session_state.agent.run(prompt, agency_level=agency_level)

            answer       = result.get("answer", "No answer returned.")
            routing_log  = result.get("routing_log", [])
            token_summary = result.get("token_summary")
            elapsed      = result.get("elapsed", 0)
            al           = result.get("agency_level", "L2")
            step_count   = len(routing_log)
            step_str     = (f"{step_count} tool call{'s' if step_count != 1 else ''}"
                            if step_count else "direct answer")
            meta = (
                f"\n\n---\n*{LEVEL_LABELS.get(agency_level, al)} · "
                f"{step_str} · {elapsed:.1f}s · "
                f"Session: {st.session_state.agent.session_id}*"
            )
            st.markdown(answer + meta)

        st.session_state.messages.append({"role": "assistant", "content": answer + meta})
        st.session_state.routing_log       = routing_log
        st.session_state.token_summary     = token_summary
        st.session_state.last_agency_level = al

    # ── Controls (rendered after prompt block so they see updated state) ────
    st.divider()
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        st.radio(
            "Agency level",
            options=list(_LEVEL_MAP.keys()),
            index=1,
            key="agency_radio",
        )

    with col2:
        cloud_label = st.selectbox(
            "Cloud model",
            options=list(_CLOUD_MODEL_LABELS.keys()),
            index=0 if cfg.use_fast_cloud_model else 1,
            key="cloud_model_select",
        )
        if _CLOUD_MODEL_LABELS[cloud_label] != cfg.use_fast_cloud_model:
            cfg.use_fast_cloud_model = _CLOUD_MODEL_LABELS[cloud_label]
            save_config(cfg)

        show_routing = st.checkbox("Show routing log", value=True, key="show_routing")
        show_tokens  = st.checkbox("Show token consumption", value=True, key="show_tokens")

    with col3:
        if st.button("Reset session", key="reset_btn"):
            st.session_state.agent         = HybridMindAgent()
            st.session_state.messages      = []
            st.session_state.routing_log   = []
            st.session_state.token_summary = None
            st.rerun()

    # ── Routing + token panels ──────────────────────────────────────────────
    if show_routing and st.session_state.routing_log:
        with st.expander("Routing log", expanded=True):
            st.markdown(_render_routing_log(st.session_state.routing_log))

    if show_tokens and st.session_state.token_summary:
        with st.expander("Token consumption", expanded=True):
            st.markdown(_render_token_panel(
                st.session_state.token_summary,
                st.session_state.last_agency_level,
            ))

    # ── Email compose gate ──────────────────────────────────────────────────
    with st.expander("Compose & Send Email (confirmation gate)", expanded=False):
        email_to      = st.text_input("To", placeholder="name@example.com", key="email_to")
        email_subject = st.text_input("Subject", key="email_subject")
        email_body    = st.text_area("Body", key="email_body")
        confirmed     = st.checkbox("I confirm sending this email", key="email_confirm")
        if st.button("Send email", key="email_send"):
            st.write(_send_email(email_to, email_subject, email_body, confirmed))

    # ── Reference table (sidebar) ──────────────────────────────────────────
    with st.sidebar:
        st.markdown("### Level trade-offs")
        st.markdown("""
| | L1 | L2 | L3 |
|---|:---:|:---:|:---:|
| Autonomy | Low | Med | High |
| Predictability | High | Med | Low |
| Token cost | Low | Med | High |
| Typical tokens | ~650 | ~1 800 | ~4 500 |
""")
        st.markdown("### Example queries")
        st.markdown("""
**L1:** What is attention in transformers?

**L2:** What's on my calendar today?

**L3:** Research recent LLM papers and summarise my week
""")


def _send_email(to: str, subject: str, body: str, confirmed: bool) -> str:
    if not confirmed:
        return "Tick 'I confirm sending this email' first."
    if not to.strip() or not body.strip():
        return "Recipient and body are required."
    try:
        from connectors.gmail import GmailConnector
        connector = GmailConnector()
        if not connector.health_check():
            return "Gmail not connected. Connect Google in Settings first."
        result = connector.send_message(to.strip(), subject.strip(), body)
        if result.get("success"):
            return f"✓ Sent to {to.strip()} (id {result.get('id')})."
        return f"Send failed: {result.get('error')}"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tab 2: Knowledge Graph
# ---------------------------------------------------------------------------

def _tab_kg() -> None:
    st.markdown("Live view of your personal knowledge graph.")
    if st.button("Refresh Graph", key="kg_refresh"):
        html = _build_kg_html()
        st.components.v1.html(html, height=460, scrolling=False)
    else:
        st.info("Click **Refresh Graph** to load the graph.")


def _build_kg_html() -> str:
    try:
        from pyvis.network import Network
        from kg.graph import build_graph
        from kg.schema import get_db_connection

        cfg = get_config()
        conn = get_db_connection(cfg.db_path)
        G = build_graph(conn)
        conn.close()

        if len(G.nodes) == 0:
            return "<p style='color:#888;padding:20px'>Knowledge graph is empty. Connect a data source or load mock data.</p>"

        net = Network(height="420px", width="100%", bgcolor="#f8f9fa",
                      font_color="#1B3A6B", cdn_resources="remote")
        net.from_nx(G)
        for node in net.nodes:
            ntype = node.get("type", "")
            if ntype == "Person":
                node["color"] = "#2E6DB4"; node["size"] = 20
            elif ntype == "Commitment":
                ctype = node.get("commitment_type", "")
                node["color"] = ("#1A6B3A" if ctype == "HARD"
                                 else "#E07B00" if ctype == "SOFT" else "#888888")
                node["size"] = 14
            elif ntype == "TimeSlot":
                node["color"] = "#8B0000"; node["size"] = 10
        net.set_options('{"physics":{"stabilization":{"iterations":100}}}')
        return net.generate_html(notebook=False)
    except Exception as e:
        return f"<p style='color:red;padding:20px'>KG render error: {e}</p>"


# ---------------------------------------------------------------------------
# Tab 3: Monitor
# ---------------------------------------------------------------------------

def _tab_monitor() -> None:
    st.markdown("Background monitor status and proactive conflict alerts.")

    col1, col2 = st.columns(2)
    with col1:
        run_poll = st.button("Run poll now", type="primary", key="run_poll")
    with col2:
        st.button("Refresh", key="refresh_alerts")

    st.markdown("### FSM Status")
    st.markdown(_monitor_state_md())

    if run_poll:
        from monitor.fsm import monitor_runner
        monitor_runner.run_once()
        st.rerun()

    st.markdown("### Alerts")
    st.markdown(_monitor_alerts_md())


def _monitor_state_md() -> str:
    from monitor.fsm import monitor_runner
    state = monitor_runner.latest_state
    if state is None:
        return "**FSM status:** idle — no cycle has run yet."
    when = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(monitor_runner.last_poll_ts))
            if monitor_runner.last_poll_ts else "never")
    status = "ERROR" if state.get("error") else "OK"
    lines = [
        f"**FSM status:** {status}",
        f"**Cycles run:** {state.get('cycle_count', 0)}",
        f"**Last poll:** {when}",
        f"**Last cycle:** {len(state.get('new_messages', []))} msgs, "
        f"{len(state.get('new_commitments', []))} commitments, "
        f"{len(state.get('new_conflicts', []))} conflicts, "
        f"{state.get('alerts_fired', 0)} alerts",
    ]
    if state.get("error"):
        lines.append(f"**Error:** {state['error']}")
    return "  \n".join(lines)


def _monitor_alerts_md() -> str:
    # In-memory alerts (cloud mode)
    if st.session_state.alerts:
        lines = []
        for alert in reversed(st.session_state.alerts[-10:]):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(alert.get("timestamp", 0)))
            lines.append(f"**{ts}** — {alert.get('message', 'Alert')}")
        return "\n\n".join(lines)

    # Disk-based alerts (local mode)
    alerts_path = Path(get_config().alerts_log_path)
    if not alerts_path.exists():
        return "No alerts yet. Monitor loop hasn't run or no conflicts detected."
    try:
        raw_lines = alerts_path.read_text(encoding="utf-8").strip().splitlines()
        if not raw_lines:
            return "No alerts yet."
        lines = []
        for line in reversed(raw_lines[-10:]):
            try:
                alert = json.loads(line)
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(alert.get("timestamp", 0)))
                lines.append(f"**{ts}** — {alert.get('message', 'Alert')}")
            except json.JSONDecodeError:
                lines.append(line)
        return "\n\n".join(lines)
    except Exception as e:
        return f"Error reading alerts: {e}"


# ---------------------------------------------------------------------------
# Tab 4: Documents
# ---------------------------------------------------------------------------

def _tab_documents() -> None:
    st.markdown("Upload documents to the RAG knowledge base.")
    if os.getenv("KM_GOOGLE_MOCK", "").lower() == "true":
        st.info("Cloud mode: documents are stored in-memory and reset on each app restart. Re-upload each session.")

    uploaded = st.file_uploader(
        "Upload PDF / TXT / MD",
        type=["pdf", "txt", "md"],
        key="doc_upload",
    )
    if uploaded is not None:
        import tempfile
        suffix = Path(uploaded.name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded.getvalue())
            tmp_path = tmp.name

        result = st.session_state.agent.add_document(tmp_path)
        added   = result.get("added", [])
        skipped = result.get("skipped", [])
        chunks  = result.get("chunks", 0)
        parts = []
        if added:
            parts.append(f"✓ Indexed: {', '.join(added)} ({chunks} chunks)")
        if skipped:
            parts.append(f"⚠ Skipped: {', '.join(skipped)}")
        if parts:
            st.success("\n".join(parts))
        else:
            st.info("Nothing indexed.")

    st.divider()
    if st.button("List indexed documents", key="list_docs"):
        try:
            from tools.rag import rag_tool
            docs = rag_tool.list_documents()
            if docs:
                st.markdown("**Indexed documents:**\n" + "\n".join(f"• {d}" for d in docs))
            else:
                st.info("No documents indexed yet.")
        except Exception as e:
            st.error(f"Error: {e}")


# ---------------------------------------------------------------------------
# Tab 5: Settings
# ---------------------------------------------------------------------------

def _tab_settings() -> None:
    cfg = get_config()
    cloud_mode = os.getenv("KM_GOOGLE_MOCK", "").lower() == "true"

    st.markdown("Update configuration. Changes are saved immediately.")

    if cloud_mode:
        st.info(
            "Running in cloud mode — API keys are loaded from Streamlit secrets "
            "and cannot be changed here. The complexity threshold can still be adjusted."
        )

    with st.form("settings_form"):
        local_model = st.text_input(
            "Local model (Ollama)", value=cfg.local_model,
            disabled=cloud_mode, key="s_local_model",
        )
        groq_key = st.text_input(
            "Groq API Key", value=cfg.groq_api_key,
            type="password", disabled=cloud_mode, key="s_groq",
        )
        tavily_key = st.text_input(
            "Tavily API Key (optional)", value=cfg.tavily_api_key,
            type="password", disabled=cloud_mode, key="s_tavily",
        )
        slack_token = st.text_input(
            "Slack Bot Token (optional)", value=cfg.slack_bot_token,
            type="password", disabled=cloud_mode, key="s_slack",
        )
        threshold = st.text_input(
            "Complexity threshold (cloud routing cutoff, 0.0–1.0)",
            value=str(cfg.complexity_threshold), key="s_threshold",
        )
        submitted = st.form_submit_button("Save settings", type="primary")

    if submitted:
        try:
            cfg.complexity_threshold = float(threshold)
            if not cloud_mode:
                cfg.local_model    = local_model
                cfg.groq_api_key   = groq_key
                cfg.tavily_api_key = tavily_key
                cfg.slack_bot_token = slack_token
            save_config(cfg)
            reload_config()
            st.success("✓ Settings saved.")
        except ValueError:
            st.error("Invalid complexity threshold — must be a number between 0 and 1.")

    # Google connector (local mode only)
    if not cloud_mode:
        st.divider()
        st.markdown("### Connect Google")
        st.markdown(
            "Authorise Calendar and Gmail. Each opens a browser for "
            "one-time consent and saves a token locally."
        )
        google_creds = st.text_input(
            "Google OAuth credentials path",
            value=cfg.google_credentials_path,
            placeholder="./credentials.json",
            key="s_google_creds",
        )
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Connect Google Calendar", key="connect_cal"):
                st.write(_connect_google(google_creds, "calendar"))
        with col2:
            if st.button("Connect Gmail", key="connect_gmail"):
                st.write(_connect_google(google_creds, "gmail"))
    else:
        st.info("Google connectors are using mock data in cloud mode (KM_GOOGLE_MOCK=true).")


def _connect_google(creds_path: str, service: str) -> str:
    creds_path = (creds_path or "").strip()
    if not creds_path:
        return "Enter the Google OAuth credentials path first, then connect."
    cfg = get_config()
    cfg.google_credentials_path = creds_path
    save_config(cfg)
    reload_config()
    try:
        if service == "calendar":
            from connectors.calendar import GoogleCalendarConnector
            result = GoogleCalendarConnector().connect()
        else:
            from connectors.gmail import GmailConnector
            result = GmailConnector().connect()
        return (f"✓ {result.get('message', 'Connected.')}"
                if result.get("success") else f"Connection failed: {result.get('error')}")
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

def render_app() -> None:
    st.set_page_config(
        page_title="KnowledgeMind",
        page_icon="🧠",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _init_state()

    cfg = get_config()
    if not cfg.is_ready():
        st.error(
            "**Missing configuration.** Set the following environment variables "
            "(or Streamlit secrets via the dashboard):\n\n"
            "- `GROQ_API_KEY` *(required)*\n"
            "- `KM_LOCAL_PROVIDER=groq` *(required for cloud mode)*\n"
            "- `KM_SETUP_COMPLETE=true`\n\n"
            "Then restart the app."
        )
        st.stop()

    st.markdown(
        "<h1 style='text-align:center;color:#3b82f6'>🧠 KnowledgeMind</h1>"
        "<p style='text-align:center;color:#888;margin-top:-8px'>"
        "Privacy-Aware Personal AI Agent · IISc Bengaluru</p>",
        unsafe_allow_html=True,
    )

    chat_tab, kg_tab, monitor_tab, docs_tab, settings_tab = st.tabs([
        "💬 Chat", "🕸️ Knowledge Graph", "📡 Monitor", "📄 Documents", "⚙️ Settings",
    ])

    with chat_tab:
        _tab_chat()
    with kg_tab:
        _tab_kg()
    with monitor_tab:
        _tab_monitor()
    with docs_tab:
        _tab_documents()
    with settings_tab:
        _tab_settings()
