# Plan: Cloud Deployment — Online SLM + Streamlit Migration

## Objective

Make KnowledgeMind deployable to **Streamlit Cloud** by:
1. Replacing the local Ollama dependency with an online SLM (Groq fast tier)
2. Migrating the UI from Gradio to Streamlit
3. Replacing local-disk storage with cloud-compatible persistence
4. Loading all config from environment variables / Streamlit secrets (no setup wizard)
5. Handling Google OAuth in a server-side flow

---

## Current Architecture vs Target

| Concern | Current (local) | Target (cloud) |
|---|---|---|
| UI framework | Gradio (`ui/app.py`, `ui/setup.py`) | Streamlit (`streamlit_app.py`) |
| Entry point | `launcher.py` → setup wizard → main UI | `streamlit_app.py` (Streamlit Cloud runs this) |
| Local LLM | Ollama at `localhost:11434` | Groq fast tier (`llama-3.1-8b-instant`) |
| Config storage | `~/.config/KnowledgeMind/config.json` | `st.secrets` / env vars |
| KG storage | SQLite on local disk | SQLite on mounted volume OR in-memory (demo) |
| Vector store | ChromaDB on local disk | ChromaDB in-memory OR Pinecone (production) |
| Google OAuth | Interactive browser flow (local) | Server-side OAuth with redirect URI |
| Setup wizard | Interactive Gradio first-run screen | Skip — secrets injected at deploy time |

---

## Part 1 — Online SLM (replace Ollama)

### 1a. `config/store.py`

Add two new fields to `AppConfig`:

```python
local_provider: str = "ollama"                   # "ollama" | "groq"
online_slm_model: str = "llama-3.1-8b-instant"  # used when local_provider = "groq"
```

Update `is_ready()` so Ollama is not required when `local_provider == "groq"`:

```python
def is_ready(self) -> bool:
    if self.local_provider == "groq":
        return bool(self.groq_api_key and self.setup_complete)
    return bool(self.groq_api_key and self.local_model and self.setup_complete)
```

Extend `_ENV_OVERRIDES` to cover the new fields:

```python
"local_provider":    ("KM_LOCAL_PROVIDER",),
"online_slm_model":  ("KM_ONLINE_SLM_MODEL",),
```

### 1b. `config/models.py`

Add a helper listing the available online SLM options on Groq:

```python
def list_online_slm_models() -> list[str]:
    return [
        "llama-3.1-8b-instant",   # primary — lowest latency
        "llama-3.2-3b-preview",   # smaller, faster
        "llama-3.2-1b-preview",   # minimal footprint
        "gemma2-9b-it",           # Google, strong instruction following
    ]
```

### 1c. `agent/orchestrator.py`

**Rename** `_call_ollama` → `_call_local`, dispatch on `cfg.local_provider`:

```python
def _call_local(messages, system, tracker, node, agency_level, max_tokens=512):
    cfg = get_config()
    if cfg.local_provider == "groq":
        return _call_groq_fast(messages, system, tracker, node, agency_level, max_tokens)
    # Existing Ollama path with Groq fallback on error
    try:
        from ollama import Client
        ...
    except Exception as e:
        print(f"[Orchestrator] Ollama unavailable ({e}), falling back to Groq fast tier")
        return _call_groq_fast(messages, system, tracker, f"{node}_fallback", agency_level, max_tokens)
```

Replace all 4 `_call_ollama` call sites with `_call_local`:

| Location | Purpose |
|---|---|
| `_run_l1()` ~line 314 | Decision call when routing is LOCAL |
| `_run_l1()` ~line 357 | Synthesis for LOCAL-routed tool results |
| `HybridMindAgent.run()` ~line 671 | Greeting responses |
| `_parse_tool_params()` ~line 225 | Tool parameter parsing |

### 1d. `extraction/commitment.py`

Update `_default_llm_caller` to dispatch on `cfg.local_provider`:

```python
def _default_llm_caller(system_prompt: str, user_prompt: str) -> str:
    cfg = get_config()
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]
    if cfg.local_provider == "groq":
        from groq import Groq
        client = Groq(api_key=cfg.groq_api_key)
        response = client.chat.completions.create(
            model=cfg.online_slm_model,
            messages=messages,
            temperature=0.0,
            max_tokens=_MAX_TOKENS,
        )
        return response.choices[0].message.content
    # Existing Ollama path with Groq fallback
    ...
```

---

## Part 2 — Streamlit UI Migration

Replace the Gradio UI with Streamlit. The 5-tab structure is preserved; the Gradio
component model (event wiring, `gr.State`) is replaced with Streamlit's rerun model
(`st.session_state`).

### New file: `streamlit_app.py` (repo root)

Streamlit Cloud looks for this file. It replaces `launcher.py` as the cloud entry point.
`launcher.py` is kept untouched for local Gradio development.

```python
# streamlit_app.py
import streamlit as st
from config.store import reload_config
from ui.streamlit_ui import render_app

# Cloud: inject config from st.secrets before anything else reads get_config()
_inject_secrets_into_env()  # see Part 3
reload_config()
render_app()
```

### New file: `ui/streamlit_ui.py` (replaces `ui/app.py` and `ui/setup.py`)

Five-tab layout using native Streamlit components:

| Gradio component | Streamlit equivalent |
|---|---|
| `gr.Chatbot` | `st.chat_message()` loop over `st.session_state.messages` |
| `gr.Textbox` (chat input) | `st.chat_input()` |
| `gr.Radio` | `st.radio()` |
| `gr.Dropdown` | `st.selectbox()` |
| `gr.Checkbox` | `st.checkbox()` |
| `gr.Button` | `st.button()` |
| `gr.HTML` (KG iframe) | `st.components.v1.html()` |
| `gr.File` | `st.file_uploader()` |
| `gr.Accordion` | `st.expander()` |
| `gr.Tabs` / `gr.TabItem` | `st.tabs()` |
| `gr.Markdown` | `st.markdown()` |

**Agent state** — replace the global `_AGENT` singleton with `st.session_state`:

```python
if "agent" not in st.session_state:
    st.session_state.agent = HybridMindAgent()
agent = st.session_state.agent
```

**Chat tab** — Streamlit's rerun model means the whole script re-executes on every
interaction. Chat history is stored in `st.session_state.messages` (list of dicts):

```python
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Ask anything..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            result = agent.run(prompt, agency_level=selected_level)
        answer = result["answer"]
        st.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
```

**Settings tab** — In cloud mode, settings are displayed as read-only (sourced from
secrets). On-device users can still edit and save. Detect cloud mode via
`os.getenv("STREAMLIT_CLOUD", "")`.

**No setup wizard** — `ui/setup.py` is not used in cloud mode. When all required
secrets are present, the app renders directly. Add a sidebar warning if any required
secret is missing.

### Delete / retire

- `ui/setup.py` — replaced by secrets-based config (kept locally for on-device users)
- `launcher.py` — kept for local Gradio/Ollama users; not used by Streamlit Cloud

---

## Part 3 — Config from Environment Variables / Streamlit Secrets

### `.streamlit/secrets.toml` (gitignored — template only)

```toml
GROQ_API_KEY       = "gsk_..."
TAVILY_API_KEY     = "tvly-..."
SLACK_BOT_TOKEN    = "xoxb-..."
KM_LOCAL_PROVIDER  = "groq"
KM_ONLINE_SLM_MODEL = "llama-3.1-8b-instant"
```

### `config/cloud_config.py` (new file)

Reads `st.secrets` and pushes values into `os.environ` before `load_config()` runs,
so the existing `_ENV_OVERRIDES` mechanism picks them up without changes:

```python
def inject_streamlit_secrets() -> None:
    """Copy st.secrets into os.environ so load_config() ENV overrides work."""
    try:
        import streamlit as st
        for key, value in st.secrets.items():
            if isinstance(value, str) and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass  # not running under Streamlit — no-op
```

Call `inject_streamlit_secrets()` at the top of `streamlit_app.py`, before any
`get_config()` call.

Also extend `_ENV_OVERRIDES` in `config/store.py`:

```python
"local_provider":    ("KM_LOCAL_PROVIDER",),
"online_slm_model":  ("KM_ONLINE_SLM_MODEL",),
"setup_complete":    ("KM_SETUP_COMPLETE",),   # set to "true" via secrets
```

---

## Part 4 — Cloud-Compatible Persistence

Streamlit Cloud has an **ephemeral filesystem** — anything written to disk is lost on
restart. Two tiers of fix are described: Demo (minimal changes) and Production (proper
cloud storage).

### 4a. SQLite Knowledge Graph

Use an in-memory SQLite database when the configured `db_path` is not writable:

```python
# kg/schema.py — get_db_connection()
def get_db_connection(db_path: str):
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(db_path)
    except OSError:
        # Ephemeral filesystem (cloud) — fall back to in-memory DB
        print("[KG] WARNING: db_path not writable, using in-memory SQLite")
        return sqlite3.connect(":memory:")
```

Data is lost on Streamlit rerun but the agent remains functional for the session.

### 4b. ChromaDB Vector Store

Force ChromaDB into in-memory mode when the configured persist directory is not writable:

```python
# tools/rag.py — RagTool.__init__()
try:
    self._client = chromadb.PersistentClient(path=cfg.chroma_persist_dir)
except Exception:
    print("[RAG] WARNING: chroma_persist_dir not writable, using in-memory client")
    self._client = chromadb.EphemeralClient()
```

Indexed documents are lost on Streamlit rerun. Users must re-upload each session.

### 4c. Alerts log

The alerts JSONL file (`alerts.jsonl`) is also ephemeral. Store alerts in
`st.session_state` in-memory for the duration of the session.

---

## Part 5 — Google OAuth (Cloud)

The current `connector.connect()` opens a local browser for OAuth consent. This is
impossible in a cloud deployment.

Google Calendar and Gmail connectors fall back to mock data (already implemented in
`connectors/mock.py`). Add a `KM_GOOGLE_MOCK=true` secret to activate mock mode.
Hide the "Connect Google" buttons in the Settings tab when this flag is set.

---

## Part 6 — Dependencies (`requirements.txt`)

Add:
```
streamlit
```

Keep (still needed):
```
groq
ollama            # kept for local mode; no-ops in cloud when provider = "groq"
chromadb
sentence-transformers
torch
...all others unchanged
```

---

## Part 7 — Streamlit Cloud Deployment Config

### `packages.txt` (new file — system packages for Streamlit Cloud)

```
# System dependencies for spaCy and sentence-transformers
```

### `setup.sh` (new file — post-install hook)

```bash
#!/bin/bash
python -m spacy download en_core_web_sm
```

Alternatively, add the spaCy model directly in `requirements.txt` via its GitHub URL:
```
https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.7.1/en_core_web_sm-3.7.1-py3-none-any.whl
```

### `.streamlit/config.toml` (new file)

```toml
[server]
headless = true
port = 8501

[theme]
primaryColor = "#3b82f6"
backgroundColor = "#ffffff"
secondaryBackgroundColor = "#f0f2f6"
```

---

## Implementation Order

| Step | File(s) | Change |
|---|---|---|
| 1 | `config/store.py` | Add `local_provider`, `online_slm_model`; update `is_ready()`, `_ENV_OVERRIDES` |
| 2 | `config/models.py` | Add `list_online_slm_models()` |
| 3 | `config/cloud_config.py` | New — `inject_streamlit_secrets()` |
| 4 | `agent/orchestrator.py` | Rename `_call_ollama` → `_call_local`, dispatch on provider |
| 5 | `extraction/commitment.py` | Update `_default_llm_caller` to dispatch on provider |
| 6 | `kg/schema.py` | Graceful in-memory fallback when db_path not writable |
| 7 | `tools/rag.py` | Graceful in-memory ChromaDB fallback |
| 8 | `ui/streamlit_ui.py` | New — full Streamlit 5-tab UI |
| 9 | `streamlit_app.py` | New — Streamlit Cloud entry point |
| 10 | `requirements.txt` | Add `streamlit`; add spaCy model wheel |
| 11 | `.streamlit/secrets.toml` | New — local secrets template (gitignored) |
| 12 | `.streamlit/config.toml` | New — Streamlit server/theme config |
| 13 | `.gitignore` | Add `.streamlit/secrets.toml` |

---

## Testing Checklist

**SLM migration:**
- [ ] `python routing/router.py` smoke test passes (all ALWAYS_LOCAL_TOOLS stay LOCAL)
- [ ] `python extraction/commitment.py` smoke test passes (stub LLM caller)
- [ ] With `KM_LOCAL_PROVIDER=groq`: L1, L2, L3 all return answers without Ollama running
- [ ] With `KM_LOCAL_PROVIDER=ollama` (default): existing behaviour unchanged

**Streamlit UI:**
- [ ] `streamlit run streamlit_app.py` launches with no Ollama installed
- [ ] Chat tab: messages render, agency level radio works, routing log appears
- [ ] KG tab: graph renders (empty is fine for demo)
- [ ] Monitor tab: manual poll runs without crash
- [ ] Documents tab: PDF upload, indexing, listing works
- [ ] Settings tab: read-only in cloud mode, editable locally

**Cloud persistence (in-memory demo):**
- [ ] App starts with no writable filesystem: no crash, in-memory fallback activates
- [ ] Re-upload a document within the same session: RAG query returns relevant results

**Secrets / config:**
- [ ] All required values present via env vars → `cfg.is_ready()` returns True, no wizard shown
- [ ] Missing `GROQ_API_KEY` → sidebar warning rendered, agent calls return graceful error

**Streamlit Cloud (final deploy):**
- [ ] Push to GitHub, connect repo to Streamlit Cloud, add secrets via dashboard
- [ ] App loads at the assigned `*.streamlit.app` URL
- [ ] Chat query returns an answer end-to-end
