from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chat_handler import process_message, suggest_tags
from knowledge_graph import ProjectKG
from models import ChatMessage, Project, Rule, TeamMember
from sow_ingestor import extract_pdf_text, ingest_sow

import config as _config  # noqa: F401 — validates key and exits if missing

app = FastAPI(title="Project Advisor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_DIR = Path(__file__).parent.parent / "data" / "projects"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# In-memory state
_projects: dict[str, Project] = {}
_kgs: dict[str, ProjectKG] = {}
_rules: dict[str, list[Rule]] = {}
_chats: dict[str, list[ChatMessage]] = {}


# ── persistence ──────────────────────────────────────────────────────────────

def _save(pid: str):
    path = DATA_DIR / pid
    path.mkdir(exist_ok=True)
    (path / "project.json").write_text(json.dumps(_projects[pid].model_dump(), indent=2))
    (path / "kg.json").write_text(json.dumps(_kgs[pid].to_json(), indent=2))
    (path / "rules.json").write_text(json.dumps([r.model_dump() for r in _rules.get(pid, [])], indent=2))
    (path / "chat.json").write_text(json.dumps([m.model_dump() for m in _chats.get(pid, [])], indent=2))


def _load_all():
    for d in DATA_DIR.iterdir():
        if not d.is_dir():
            continue
        pid = d.name
        try:
            _projects[pid] = Project(**json.loads((d / "project.json").read_text()))
            _kgs[pid] = ProjectKG.from_json(json.loads((d / "kg.json").read_text()))
            _rules[pid] = [Rule(**r) for r in json.loads((d / "rules.json").read_text())]
            _chats[pid] = [ChatMessage(**m) for m in json.loads((d / "chat.json").read_text())]
        except Exception:
            pass


_load_all()


# ── request bodies ────────────────────────────────────────────────────────────

# CreateProjectBody removed — endpoint uses multipart Form + File


class AddMemberBody(BaseModel):
    handle: str
    display_name: str


class SendMessageBody(BaseModel):
    author_handle: str
    author_id: str
    content: str
    tags: list[str] = []


class UpdateTagsBody(BaseModel):
    tags: list[str]


class SuggestTagsBody(BaseModel):
    message: str


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_project(pid: str):
    if pid not in _projects:
        raise HTTPException(404, "Project not found")


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# Projects
@app.post("/projects", status_code=201)
async def create_project(
    name: str = Form(...),
    sow_text: Optional[str] = Form(None),
    sow_pdf: Optional[UploadFile] = File(None),
):
    if sow_pdf and sow_pdf.filename:
        raw = await sow_pdf.read()
        sow_text = extract_pdf_text(raw)
    if not sow_text or not sow_text.strip():
        raise HTTPException(400, "Provide either sow_text or a sow_pdf file")
    project = Project(name=name, sow_text=sow_text)
    pid = project.project_id
    kg, rules = ingest_sow(sow_text)
    _projects[pid] = project
    _kgs[pid] = kg
    _rules[pid] = rules
    _chats[pid] = []
    _save(pid)
    return {"project_id": pid, "name": project.name}


@app.get("/projects")
def list_projects():
    return [
        {"project_id": p.project_id, "name": p.name, "created_at": p.created_at}
        for p in _projects.values()
    ]


@app.get("/projects/{pid}")
def get_project(pid: str):
    _require_project(pid)
    return _projects[pid]


# KG
@app.get("/projects/{pid}/kg")
def get_kg(pid: str, plane: Optional[str] = None):
    _require_project(pid)
    return _kgs[pid].to_cytoscape(plane=plane)


@app.get("/projects/{pid}/kg/coverage")
def get_coverage(pid: str):
    _require_project(pid)
    return _kgs[pid].get_coverage()


@app.get("/projects/{pid}/kg/deviations")
def get_deviations(pid: str):
    _require_project(pid)
    return _kgs[pid].get_deviations()


# Rules
@app.get("/projects/{pid}/rules")
def get_rules(pid: str):
    _require_project(pid)
    return [r.model_dump() for r in _rules.get(pid, [])]


# Members
@app.post("/projects/{pid}/members", status_code=201)
def add_member(pid: str, body: AddMemberBody):
    _require_project(pid)
    member = TeamMember(handle=body.handle, display_name=body.display_name)
    _projects[pid].members.append(member)
    _save(pid)
    return member


@app.get("/projects/{pid}/members")
def list_members(pid: str):
    _require_project(pid)
    return _projects[pid].members


# Chat
@app.post("/projects/{pid}/chat")
def send_message(pid: str, body: SendMessageBody):
    _require_project(pid)
    user_msg, asst_msg, updated_kg, updated_rules = process_message(
        message=body.content,
        author_handle=body.author_handle,
        author_id=body.author_id,
        tags=body.tags,
        kg=_kgs[pid],
        rules=_rules[pid],
        chat_history=_chats.get(pid, []),
    )
    _kgs[pid] = updated_kg
    _rules[pid] = updated_rules
    _chats.setdefault(pid, []).extend([user_msg, asst_msg])
    _save(pid)
    return {"user_message": user_msg.model_dump(), "assistant_message": asst_msg.model_dump()}


@app.get("/projects/{pid}/chat/history")
def get_history(pid: str, tag: Optional[str] = None, author: Optional[str] = None):
    _require_project(pid)
    msgs = _chats.get(pid, [])
    if tag:
        msgs = [m for m in msgs if tag in m.tags]
    if author:
        msgs = [m for m in msgs if m.author and m.author.handle == author]
    return [m.model_dump() for m in msgs]


@app.patch("/projects/{pid}/chat/{msg_id}/tags")
def update_tags(pid: str, msg_id: str, body: UpdateTagsBody):
    _require_project(pid)
    for msg in _chats.get(pid, []):
        if msg.message_id == msg_id:
            msg.tags = body.tags
            _save(pid)
            return msg.model_dump()
    raise HTTPException(404, "Message not found")


@app.post("/projects/{pid}/chat/suggest-tags")
def suggest_message_tags(pid: str, body: SuggestTagsBody):
    _require_project(pid)
    return {"suggested_tags": suggest_tags(body.message)}


# ── Test scenarios ────────────────────────────────────────────────────────────

SOWS_DIR = Path(__file__).parent.parent / "tests" / "sows"


@app.get("/test-scenarios/pdfs")
def list_test_pdfs():
    """Return list of synthetic SOW PDFs available for scenario testing."""
    if not SOWS_DIR.exists():
        return {"pdfs": [], "message": "Run python tests/generate_sows.py first"}
    pdfs = sorted(p.name for p in SOWS_DIR.glob("*.pdf"))
    return {"pdfs": pdfs}


@app.get("/test-scenarios/pdfs/{pdf_name}")
async def get_test_pdf(pdf_name: str):
    from fastapi.responses import FileResponse
    safe_name = Path(pdf_name).name
    path = SOWS_DIR / safe_name
    if not path.exists() or path.suffix != ".pdf":
        raise HTTPException(404, "PDF not found")
    return FileResponse(path, media_type="application/pdf", filename=safe_name)


# Serve frontend last so API routes take priority
app.mount("/", StaticFiles(directory=str(Path(__file__).parent.parent / "frontend"), html=True), name="static")
