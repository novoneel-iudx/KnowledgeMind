# Merge Spec: projmgmt as an Isolated Add-on to KnowledgeMind

## Guiding Principle

projmgmt remains its **own self-contained repository**. No projmgmt logic is ported into KnowledgeMind, and no KnowledgeMind code is imported into projmgmt. The integration has exactly two seams:

1. **Credential bootstrap** — projmgmt reads its LLM key from KnowledgeMind's config through a three-level resolution chain. Zero extra configuration once KM is set up.
2. **ASGI sub-app mount** — KM's FastAPI instance mounts projmgmt's FastAPI app at `/projmgmt` inside the same process on the same port. One server, one URL, one command to start everything.

Everything else — projmgmt's database, storage, business logic, frontend, and test suite — remains exactly as it is.

---

## Deployment Picture

```
  python launcher.py          (or: uvicorn api.main:app)
         │
         ▼
  KnowledgeMind FastAPI — http://localhost:8000
  ┌────────────────────────────────────────────────────────┐
  │  /api/*           KnowledgeMind API routes             │
  │  /                React SPA (frontend/dist)            │
  │                                                        │
  │  /projmgmt/*   ──► projmgmt FastAPI sub-app            │
  │                    ┌──────────────────────────────┐    │
  │                    │  /projects                   │    │
  │                    │  /projects/{id}/kg           │    │
  │                    │  /projects/{id}/rules        │    │
  │                    │  /projects/{id}/chat         │    │
  │                    │  /test-scenarios/pdfs        │    │
  │                    │  /                           │    │
  │                    │    projmgmt vanilla-JS SPA   │    │
  │                    │    (frontend/index.html)     │    │
  │                    │  /scenarios.html             │    │
  │                    │    scenario runner           │    │
  │                    └──────────────────────────────┘    │
  └────────────────────────────────────────────────────────┘

  KM nav: Tests → Project Management → /projmgmt/scenarios.html
```

---

## System Comparison

| Dimension | KnowledgeMind | projmgmt |
|---|---|---|
| **Scope** | Personal (commitments, calendar, Slack) | Project (SoW, KG, team chat) |
| **KG storage** | SQLite + NetworkX | NetworkX + JSON files |
| **LLM** | Groq + Ollama (privacy-routed) | Groq only |
| **Backend** | FastAPI (`api/main.py`) | FastAPI (`backend/main.py`) |
| **Agent** | L1/L2/L3 ReAct (LangGraph) | Single LLM call per chat message |
| **Frontend** | React/Vite SPA, 6 views | Vanilla JS SPA, 3 panels |
| **Proactive** | LangGraph FSM monitor | None |
| **Privacy** | LOCAL/CLOUD router | CLOUD-only (org/project data) |
| **Port** | :8000 | Mounted inside KM — no own port |

---

## Seam 1 — Credential Bootstrap (projmgmt only)

### Problem

`backend/config.py` was named `config` — colliding with KnowledgeMind's `config/` package when both share the same Python process.

### Fix: rename to `backend/pm_config.py`

The file is renamed (not just edited) and gains the three-level resolution chain:

```
Priority for GROQ_API_KEY:
  1. projmgmt/.env                          ← own override (checked first)
  2. ../KnowledgeMind/.env                  ← sibling KM repo
  3. ~/.config/KnowledgeMind/config.json    ← KM's installed app config
```

If KM is already configured with a Groq key, projmgmt inherits it automatically. `GROQ_API_KEY` in projmgmt's own `.env` overrides it.

### Files changed in projmgmt

| File | Change |
|---|---|
| `backend/config.py` | **Deleted** — replaced by `pm_config.py` |
| `backend/pm_config.py` | **New** — three-level resolution + `complete()` helper |
| `backend/main.py` | `import config as _config` → `import pm_config as _config` |
| `backend/sow_ingestor.py` | `from config import complete` → `from pm_config import complete` |
| `backend/rules_engine.py` | `from config import complete` → `from pm_config import complete` |
| `backend/chat_handler.py` | `from config import complete` → `from pm_config import complete` |

Total: 4 one-line import changes + 1 file rename/rewrite.

---

## Seam 2 — ASGI Sub-App Mount (KnowledgeMind only)

### How it works

FastAPI's `app.mount()` accepts any ASGI application, including another FastAPI instance. KM mounts projmgmt's app at the path prefix `/projmgmt`. The ASGI machinery strips the prefix before forwarding requests to projmgmt, so projmgmt's own routes (`/projects`, `/health`, etc.) see URLs without the prefix — no changes needed inside projmgmt's route handlers.

projmgmt's `StaticFiles` mount (which serves `frontend/`) sits inside the sub-app, so:
- `GET /projmgmt/` → `frontend/index.html`
- `GET /projmgmt/scenarios.html` → `frontend/scenarios.html`
- `GET /projmgmt/projects` → projmgmt's `POST /projects` route

### Addition to `KnowledgeMind/api/main.py`

Inserted **before** the existing static-file mount block, near the bottom of `api/main.py`:

```python
import sys
from pathlib import Path

# ── projmgmt addon ────────────────────────────────────────────────────────────
_PM_BACKEND = Path(__file__).resolve().parent.parent.parent / "projmgmt" / "backend"

if _PM_BACKEND.exists():
    sys.path.insert(0, str(_PM_BACKEND))
    try:
        import main as _pm_main          # projmgmt/backend/main.py
        app.mount("/projmgmt", _pm_main.app, name="projmgmt")
        print("[KM] projmgmt addon mounted at /projmgmt")
    except Exception as _pm_err:
        print(f"[KM] projmgmt addon not loaded: {_pm_err}")
    finally:
        if str(_PM_BACKEND) in sys.path:
            sys.path.remove(str(_PM_BACKEND))
```

**Why `sys.path.insert` + immediate removal works**: Python caches imported modules in `sys.modules`. Once projmgmt's modules are imported (`main`, `pm_config`, `chat_handler`, etc.) their entries are in `sys.modules` and don't need the path to be present anymore. Removing the path after import prevents any accidental later lookups into projmgmt's directory.

**Why there is no naming conflict**: KM's `config` is a package (`config/store.py`). projmgmt's renamed `pm_config` is a flat module. No overlap in `sys.modules`.

### Frontend URL auto-detection

projmgmt's frontend files use:

```javascript
// index.html and scenarios.html
const API = window.location.pathname.startsWith('/projmgmt') ? '/projmgmt' : '';
```

When accessed at `/projmgmt/index.html`, `API = '/projmgmt'`, so `fetch(API + '/projects')` becomes `fetch('/projmgmt/projects')` → routed to the sub-app → received by projmgmt as `GET /projects`. ✓

When projmgmt runs standalone (own port, own process), `pathname` does not start with `/projmgmt`, so `API = ''` — identical to the original behaviour. ✓

### KM nav — Tests → Project Management

`KnowledgeMind/frontend/src/App.jsx` (already implemented):

```jsx
const PROJMGMT_PREFIX = import.meta.env.VITE_PROJMGMT_PREFIX || "/projmgmt";

const TEST_SUITES = [
  { id: "project-mgmt", label: "Project Management",
    icon: "beaker", href: `${PROJMGMT_PREFIX}/scenarios.html` },
];
```

The "Tests" accordion in the left sidebar opens the scenario runner at `/projmgmt/scenarios.html` — same origin, no CORS, no separate port.

---

## What Does NOT Change

| Area | Status |
|---|---|
| projmgmt's route handlers, KG logic, DRL engine, alignment scorer | **Unchanged** |
| projmgmt's JSON persistence under `data/projects/` | **Unchanged** |
| projmgmt's vanilla JS frontend (panels, Cytoscape.js, chat UI) | **Unchanged** |
| projmgmt's test suite (335 pytest tests, 10 synthetic SOW PDFs) | **Unchanged** |
| projmgmt's scenario runner (`scenarios.html`) | **Unchanged** |
| KM's personal KG, SQLite schema, privacy router, agents, FSM monitor | **Unchanged** |
| KM's existing React SPA (6 views, Dashboard → Settings) | **Unchanged** |
| KM's privacy contract (`ALWAYS_LOCAL_TOOLS`, routing invariants) | **Unchanged** |
| KM's benchmark tests (`benchmark.py`, `demo_conflicts.py`) | **Unchanged** |

---

## Complete File Inventory

### projmgmt changes

| File | Type | Description |
|---|---|---|
| `backend/config.py` | Deleted | Replaced by `pm_config.py` |
| `backend/pm_config.py` | New/rewritten | Three-level credential resolution |
| `backend/main.py` | 1-line edit | `import config` → `import pm_config` |
| `backend/sow_ingestor.py` | 1-line edit | `from config import` → `from pm_config import` |
| `backend/rules_engine.py` | 1-line edit | `from config import` → `from pm_config import` |
| `backend/chat_handler.py` | 1-line edit | `from config import` → `from pm_config import` |
| `frontend/index.html` | 1-line edit | `const API = ''` → pathname-based detection |
| `frontend/scenarios.html` | 1-line edit | `const API = 'http://...'` → pathname-based detection |

### KnowledgeMind changes

| File | Type | Description |
|---|---|---|
| `api/main.py` | ~15-line addition | `sys` import + sub-app mount block |
| `frontend/src/App.jsx` | Addition (already done) | Tests accordion + Project Management deep-link |
| `frontend/src/styles.css` | Addition (already done) | `.nav-group`, `.nav-sub`, `.nav-chevron` styles |

---

## projmgmt `.env.example` (updated)

```env
# ── LLM ──────────────────────────────────────────────────────────────────────
# Leave blank to inherit from KnowledgeMind's config automatically.
# Set here only to override the value KM has configured.
GROQ_API_KEY=

LLM_MODEL=llama-3.3-70b-versatile
```

`KM_BRIDGE_URL` and `LAUNCH_PROJMGMT` entries from the previous spec are removed — they are no longer needed since projmgmt runs inside KM's process.

---

## Running the Combined Stack

```bash
# One-time: build the React frontend (if not already built)
cd KnowledgeMind/frontend && npm install && npm run build && cd ../..

# One-time: generate test SOW PDFs
cd projmgmt && python tests/generate_sows.py && cd ..

# Start everything — KM's launcher mounts projmgmt automatically
cd KnowledgeMind && python launcher.py

# http://localhost:8000          → KnowledgeMind
# http://localhost:8000/projmgmt → projmgmt (main UI)
# http://localhost:8000/projmgmt/scenarios.html → scenario runner
# http://localhost:8000/docs     → KM's API docs
# http://localhost:8000/projmgmt/docs → projmgmt's API docs

# Run projmgmt's test suite (server must already be running at :8000)
cd projmgmt && BASE_URL=http://localhost:8000/projmgmt pytest tests/
```

projmgmt can still be run standalone for development:

```bash
cd projmgmt/backend && uvicorn main:app --reload --port 8001
# http://localhost:8001 → projmgmt standalone (API = '' mode)
```

---

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| KM's `config` package and projmgmt's old `config.py` collide in `sys.modules` | High | **Fixed** — projmgmt's module renamed to `pm_config`; KM's `config/` package is unaffected |
| `sys.path.insert` temporarily exposes projmgmt's directory to other imports | Low | Path is removed in the `finally` block immediately after import |
| KM startup fails if projmgmt has a broken import | Medium | `try/except` around the mount — KM prints a warning and continues without projmgmt |
| projmgmt's `StaticFiles("/")` mount shadows KM's own `"/"` mount | None | projmgmt's static mount is scoped inside the sub-app; KM's static mount is at the parent-app level and is unaffected |
| projmgmt's `CORS` middleware runs inside the sub-app, doubling headers | Low | CORS headers added twice are harmless (browser uses the first); can be removed from projmgmt's `main.py` since KM's CORS middleware already covers `/*` |
| `pytest` tests need `BASE_URL` pointed at `/projmgmt` prefix | Low | `BASE_URL=http://localhost:8000/projmgmt pytest tests/` — documented in running instructions |

---

## Open Decisions

1. **projmgmt CORS middleware**: Now redundant since KM's middleware covers all routes including `/projmgmt/*`. It can be removed from `projmgmt/backend/main.py` to avoid the double-header issue. Recommendation: remove it and let KM's CORS settings control everything.

2. **projmgmt `ACCESS_KEY`**: KM's access-key auth middleware guards `/api/*` but not `/projmgmt/*`. If KM is deployed with `ACCESS_KEY` set, projmgmt's routes would be unguarded. Recommendation: extend KM's auth middleware to also cover `/projmgmt/` paths, or add a matching middleware inside projmgmt that reads `KM_ACCESS_KEY` from the environment (same three-level resolution as `GROQ_API_KEY`).

3. **`/projmgmt/docs`**: projmgmt's OpenAPI docs are available at `/projmgmt/docs` automatically because FastAPI generates them relative to the sub-app's root. No action needed.
