"""
api/main.py
-----------
FastAPI backend for the KnowledgeMind web UI.

Wraps the existing engine (knowledge graph, monitor, agent) behind a small JSON
API and serves the static front-end. Designed to run with ZERO setup:

  * Proactive-conflict + KG features work fully offline — they use the bundled
    mock data and a stub LLM caller (the same pattern as demo_conflicts.py), so
    the headline demo fires without Ollama or any API key.
  * The Assistant uses the real agent when a model is configured, and falls back
    to a clearly-labelled demo response otherwise.

A dedicated demo database (ui_demo.db, reset on launch) is used so the UI never
touches your real KnowledgeMind data.

Run:  .venv/bin/uvicorn api.main:app --port 8000
  or: .venv/bin/python -m api.main
Then open http://127.0.0.1:8000
"""

from __future__ import annotations

import hmac
import os
import re
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.store import get_config
from connectors.mock import MockConnector, MockCalendarSource
from extraction.commitment import extract_commitments
from kg.schema import get_db_connection
from monitor.fsm import MonitorRunner

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ---------------------------------------------------------------------------
# Offline stub LLM (same approach as demo_conflicts.py) so /scan works w/o keys
# ---------------------------------------------------------------------------

_CANNED = {
    "see you at 4": '{"is_commitment": true, "confidence": 0.78, "time_expression": "at 4 today", "normalized_ts": null, "commitment_type": "SOFT"}',
    "eod today": '{"is_commitment": true, "confidence": 0.82, "time_expression": "EOD today", "normalized_ts": null, "commitment_type": "SOFT"}',
    "lunch tomorrow": '{"is_commitment": true, "confidence": 0.70, "time_expression": "tomorrow 12:30", "normalized_ts": null, "commitment_type": "SOFT"}',
    "review my pr": '{"is_commitment": true, "confidence": 0.70, "time_expression": "tomorrow at 10", "normalized_ts": null, "commitment_type": "SOFT"}',
    "timesheets": '{"is_commitment": true, "confidence": 0.65, "time_expression": "Friday EOD", "normalized_ts": null, "commitment_type": "SOFT"}',
    "sprint planning": '{"is_commitment": true, "confidence": 0.75, "time_expression": "in 3 days at 11am", "normalized_ts": null, "commitment_type": "SOFT"}',
    "grab coffee": '{"is_commitment": true, "confidence": 0.45, "time_expression": "day after tomorrow", "normalized_ts": null, "commitment_type": "TENTATIVE"}',
}
_NON_COMMITMENT = '{"is_commitment": false, "confidence": 0.05, "time_expression": "", "normalized_ts": null, "commitment_type": "TENTATIVE"}'


def _stub_llm_caller(_system_prompt: str, user_prompt: str) -> str:
    last_block = user_prompt.rsplit("Message: ", 1)[-1]
    text = last_block.split("\nJSON:")[0].strip().lower()
    for needle, response in _CANNED.items():
        if needle in text:
            return response
    return _NON_COMMITMENT


def _stub_extractor(message, candidates):
    return extract_commitments(message, candidates, llm_caller=_stub_llm_caller)


# ---------------------------------------------------------------------------
# Demo database (separate from the real app DB; reset on launch)
# ---------------------------------------------------------------------------

def _use_demo_db():
    cfg = get_config()
    demo_db = Path(cfg.db_path).with_name("ui_demo.db")
    cfg.db_path = str(demo_db)
    cfg.alerts_log_path = str(demo_db.with_name("ui_alerts.jsonl"))
    return cfg


def _run_scan() -> dict:
    """Run one monitor cycle over the bundled mock data into the demo DB."""
    runner = MonitorRunner(
        connectors=[MockConnector()],
        extractor=_stub_extractor,
        commitment_sources=[MockCalendarSource()],
    )
    state = runner.run_once()
    return {
        "messages": len(state["new_messages"]),
        "commitments": len(state["new_commitments"]),
        "conflicts": len(state["new_conflicts"]),
        "alerts": state["alerts_fired"],
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = _use_demo_db()
    for path in (cfg.db_path, cfg.alerts_log_path):
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass
    _run_scan()  # seed so the dashboard has data immediately
    yield


app = FastAPI(title="KnowledgeMind", lifespan=lifespan)

# --- access-key auth ("static auth") ----------------------------------------
# Set ACCESS_KEY in the environment to lock the app: every /api/* request must
# then carry a matching X-Access-Key header (else 401). When ACCESS_KEY is unset
# (local dev / tests) the API is open. The key is never stored in the repo/build.
ACCESS_KEY = os.environ.get("ACCESS_KEY", "").strip()


@app.middleware("http")
async def access_key_guard(request: Request, call_next):
    path = request.url.path
    if ACCESS_KEY and path.startswith("/api/") and request.method != "OPTIONS":
        provided = request.headers.get("X-Access-Key", "")
        if not hmac.compare_digest(provided, ACCESS_KEY):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


# CORS added after the auth middleware so it stays outermost -> CORS headers are
# present even on 401s. ALLOWED_ORIGINS (comma-separated) only matters when the
# front-end is served from a different origin; the HF Space serves it same-origin.
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

_STOP = {"today", "tomorrow", "around", "about", "with", "have", "ready", "numbers", "sync"}


def _topic_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


def _commitments() -> list[dict]:
    cfg = get_config()
    conn = get_db_connection(cfg.db_path)
    rows = conn.execute(
        """SELECT c.id, c.description, c.source, c.commitment_type, c.start_ts,
                  c.end_ts, c.confidence, COALESCE(p.name, '(self)') AS who
           FROM commitments c LEFT JOIN persons p ON c.person_id = p.id
           ORDER BY c.start_ts"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _conflicts() -> list[dict]:
    cfg = get_config()
    conn = get_db_connection(cfg.db_path)
    rows = conn.execute(
        """SELECT cf.id, cf.overlap_minutes, cf.alerted,
                  a.description AS a_desc, a.source AS a_src, a.start_ts AS a_start,
                  b.description AS b_desc, b.source AS b_src, b.start_ts AS b_start
           FROM conflicts cf
           JOIN commitments a ON cf.commitment_a_id = a.id
           JOIN commitments b ON cf.commitment_b_id = b.id
           ORDER BY a.start_ts"""
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["same_event"] = bool(
            d["a_src"] != d["b_src"]
            and abs(d["a_start"] - d["b_start"]) < 60
            and (_topic_tokens(d["a_desc"]) & _topic_tokens(d["b_desc"]))
        )
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/status")
def status() -> dict:
    cfg = get_config()
    has_key = bool(cfg.groq_api_key)
    return {
        "app": "KnowledgeMind",
        "ready": cfg.is_ready(),
        "assistant_mode": "live" if has_key else "demo",
        "local_model": cfg.local_model,
        "cloud_model": cfg.cloud_model,
    }


@app.post("/api/scan")
def scan() -> dict:
    summary = _run_scan()
    return {**summary, "conflicts_detail": _conflicts()}


@app.get("/api/commitments")
def commitments() -> dict:
    return {"commitments": _commitments()}


@app.get("/api/conflicts")
def conflicts() -> dict:
    items = _conflicts()
    real = [c for c in items if not c["same_event"]]
    return {"conflicts": items, "real_count": len(real),
            "duplicate_count": len(items) - len(real)}


class ChatIn(BaseModel):
    message: str
    level: str = "L1"


_agent = None


def _get_agent():
    global _agent
    if _agent is None:
        from agent.orchestrator import HybridMindAgent
        _agent = HybridMindAgent()
    return _agent


def _to_level(name: str):
    from agent.orchestrator import AgencyLevel
    try:
        return AgencyLevel[name.upper()]
    except Exception:
        try:
            return AgencyLevel(name)
        except Exception:
            return list(AgencyLevel)[0]


def _demo_chat(message: str) -> dict:
    """Canned, clearly-labelled responses + synthetic routing log (no LLM call)."""
    m = message.lower()
    if any(k in m for k in ("chatgpt", "openai", "send my", "upload my")):
        return {"answer": "That request would send personal data off-device, so I won't do it. "
                          "I can summarise it locally instead.",
                "routing_log": [{"action": "privacy_check", "decision": "LOCAL (refused)"}],
                "demo_mode": True}
    if any(k in m for k in ("paper", "news", "latest", "search", "web", "weather")):
        return {"answer": "(demo) That's a public-info lookup, so I'd route it to the cloud model "
                          "and run a web search — no personal data involved.",
                "routing_log": [{"action": "web_search", "decision": "CLOUD"}],
                "demo_mode": True}
    if any(k in m for k in ("book", "doctor", "4 pm", "4pm", "schedule", "appointment")):
        return {"answer": "(demo) 4 PM clashes with your 'Dentist appointment' on your calendar. "
                          "I can book it at 5 PM instead — all checks ran on-device.",
                "routing_log": [{"action": "query_kg", "decision": "LOCAL"},
                                 {"action": "find_free_slots", "decision": "LOCAL"},
                                 {"action": "book_slot", "decision": "LOCAL"}],
                "demo_mode": True}
    if any(k in m for k in ("conflict", "clash", "this week", "priya", "deadline")):
        return {"answer": "(demo) From your knowledge graph: a Slack note 'see you at 4' overlaps "
                          "your 'Dentist appointment' (~16:00). Everything stayed local.",
                "routing_log": [{"action": "query_kg", "decision": "LOCAL"},
                                 {"action": "conflict_edges", "decision": "LOCAL"}],
                "demo_mode": True}
    return {"answer": "This is a demo response. Add a Groq key (free) to enable live answers — "
                      "personal-data tasks still stay on-device.",
            "routing_log": [{"action": "respond", "decision": "LOCAL"}],
            "demo_mode": True}


@app.post("/api/chat")
def chat(inp: ChatIn) -> dict:
    cfg = get_config()
    if cfg.groq_api_key:
        try:
            result = _get_agent().run(inp.message, _to_level(inp.level))
            result["demo_mode"] = False
            return result
        except Exception as error:  # noqa: BLE001 -- never 500 the UI
            fallback = _demo_chat(inp.message)
            fallback["answer"] = f"(Live model unavailable: {error}) " + fallback["answer"]
            return fallback
    return _demo_chat(inp.message)


# ---------------------------------------------------------------------------
# Documents (RAG over local files)
# ---------------------------------------------------------------------------

@app.get("/api/documents")
def list_documents() -> dict:
    from tools.rag import rag_tool
    return {"documents": rag_tool.list_documents()}


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...)) -> dict:
    from tools.rag import rag_tool
    tmp_dir = Path(tempfile.mkdtemp(prefix="km_upload_"))
    dest = tmp_dir / (file.filename or "upload.txt")
    dest.write_bytes(await file.read())
    try:
        result = rag_tool.add_documents([str(dest)])
    finally:
        try:
            dest.unlink()
            tmp_dir.rmdir()
        except OSError:
            pass
    return result


class RagQuery(BaseModel):
    query: str


@app.post("/api/rag/query")
def rag_query(q: RagQuery) -> dict:
    from tools.rag import rag_tool
    return rag_tool.query(q.query)


# ---------------------------------------------------------------------------
# Settings (config) -- model, keys, threshold
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config_api() -> dict:
    cfg = get_config()
    return {
        "local_model": cfg.local_model,
        "ollama_base_url": cfg.ollama_base_url,
        "cloud_model": cfg.cloud_model,
        "complexity_threshold": cfg.complexity_threshold,
        "google_credentials_path": cfg.google_credentials_path,
        "groq_api_key_set": bool(cfg.groq_api_key),
        "tavily_api_key_set": bool(cfg.tavily_api_key),
        "slack_bot_token_set": bool(cfg.slack_bot_token),
    }


class ConfigUpdate(BaseModel):
    local_model: Optional[str] = None
    groq_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    slack_bot_token: Optional[str] = None
    google_credentials_path: Optional[str] = None
    complexity_threshold: Optional[float] = None


@app.post("/api/config")
def set_config_api(upd: ConfigUpdate) -> dict:
    from config.store import update_config
    fields = {k: v for k, v in upd.model_dump().items() if v is not None and v != ""}
    if fields:
        update_config(**fields)            # persist to config.json (real paths)
        cfg = get_config()                 # live-update the running singleton,
        for key, value in fields.items():  # keeping the demo db_path override
            setattr(cfg, key, value)
    return {"ok": True, "saved": list(fields.keys())}


# ---------------------------------------------------------------------------
# Connectors (Hermes signal tools) -- fitness / health / tasks / music
# ---------------------------------------------------------------------------

@app.get("/api/connectors")
def connectors() -> dict:
    """Latest derived signals for each Hermes connector (live or mock).

    Each call runs the tool, which derives signals locally and records a
    snapshot to the connector store. Returns per-connector results for the UI.
    """
    from agent.tools import dispatch_tool
    names = ["strava", "apple_health", "todoist", "spotify"]
    return {"connectors": {name: dispatch_tool(name, {}) for name in names}}


# ---------------------------------------------------------------------------
# projmgmt addon — mount as ASGI sub-application at /projmgmt
# ---------------------------------------------------------------------------
# projmgmt's backend/ directory is added to sys.path only for the duration of
# the import. The module name pm_config (not config) avoids colliding with KM's
# own config/ package.

_PM_BACKEND = Path(__file__).resolve().parent.parent.parent / "projmgmt" / "backend"

if _PM_BACKEND.exists():
    sys.path.insert(0, str(_PM_BACKEND))
    try:
        import main as _pm_main  # projmgmt/backend/main.py
        app.mount("/projmgmt", _pm_main.app, name="projmgmt")
        print("[KM] projmgmt addon mounted at /projmgmt")
    except Exception as _pm_err:
        print(f"[KM] projmgmt addon not loaded: {_pm_err}")
    finally:
        # Remove from path after import — all modules are cached in sys.modules
        if str(_PM_BACKEND) in sys.path:
            sys.path.remove(str(_PM_BACKEND))


# ---------------------------------------------------------------------------
# Static front-end (built React SPA in frontend/dist)
# ---------------------------------------------------------------------------

DIST_DIR = FRONTEND_DIR / "dist"

if DIST_DIR.exists():
    # html=True serves index.html at "/" and the hashed assets under /assets.
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="spa")
else:
    @app.get("/")
    def _needs_build():
        return JSONResponse(
            {"detail": "Frontend not built. Run: cd frontend && npm install && npm run build"},
            status_code=200,
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=False)
