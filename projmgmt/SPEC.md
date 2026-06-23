# SPEC: Team Management Chatbot

## Problem Statement

Project teams lose alignment over time. Discussions drift, features creep in, and sprint plans diverge from the original Statement of Work. This system gives the team a persistent, AI-driven advisor that was "present at the SoW" and can flag drift in real time.

---

## Implementation Status

| Area | Status |
|---|---|
| SPEC.md + CLAUDE.md | ✅ Done |
| Backend scaffolded (FastAPI) | ✅ Done |
| SoW ingestor (PDF + text) | ✅ Done |
| Dual-plane Knowledge Graph | ✅ Done |
| DRL rules engine | ✅ Done |
| Chat handler + alignment scoring | ✅ Done |
| KG viewer (Cytoscape.js, dual-plane) | ✅ Done |
| DRL viewer + coverage bar | ✅ Done |
| PDF upload (drag-and-drop modal) | ✅ Done |
| Groq LLM integration (.env config) | ✅ Done |
| Synthetic SOW test suite (10 PDFs) | ✅ Done |
| Automated pytest alignment tests | ✅ Done |
| Scenario runner frontend | ✅ Done |

---

## Core Flows

### 1. Project Initialization

```
User uploads SoW (PDF or plain text)
  → PDF text is extracted with pypdf
  → LLM (Groq / llama-3.3-70b-versatile) extracts entities and relationships
  → Origin Plane Knowledge Graph is built (nodes + edges) and persisted as JSON
  → LLM generates pseudo-DRL rules from scope boundaries and constraints
  → DRL rules are stored and rendered in the UI
```

### 2. Team Chat Session

```
Team member sends a message (feature idea, sprint plan, decision, concern)
  → Message is attributed to sender handle
  → Tags are applied — manually (#tag inline) or AI-suggested and confirmed
  → Chat handler finds semantically relevant Origin Plane KG nodes
  → DRL rules are evaluated against message content
  → LLM produces:
      - Alignment score (0–100)
      - In-scope / out-of-scope component lists
      - Deviation flags (rules violated or at risk)
      - Coverage delta (Origin goals newly addressed)
      - Architecture recommendations
      - User Plane entity extraction with proposed cross-plane links
  → User Plane KG is updated (new nodes + cross-plane edges)
  → Assistant response rendered in chat with score badge and metadata chips
```

### 3. Viewers

- **KG Viewer**: Cytoscape.js dual-plane graph. Origin Plane nodes (solid shapes) and User Plane nodes (outlined/dashed shapes) coexist. Cross-plane edges are dashed. Plane toggle (Origin / User / Both). Clicking a node shows label, type, description, and source (SoW excerpt or chat message ref).
- **DRL Viewer**: Rule cards with live violation status icons (✓ ok / ⚠ at_risk / ✗ violated). Coverage progress bar above the rule list.

### 4. Scenario Runner

```
User opens /scenarios.html in a parallel tab
  → Accordion list of all 10 synthetic SOW documents
  → User checks individual conversation entries (aligned / misaligned / edge)
  → Clicks "Run Selected"
  → Runner fetches real PDF from /test-scenarios/pdfs/<name> (or falls back to synthetic text)
  → Creates a project, broadcasts project_id to main UI via localStorage
  → Main UI (index.html) auto-loads the project — KG, rules, chat update live
  → Messages sent sequentially (1.2 s delay); checkboxes tick with pass/fail badges
  → Execution log shows per-message scores, assertion results, KG node counts
```

---

## Knowledge Graph Planes

The KG is a single NetworkX DiGraph. Every node and edge carries a `plane` field that determines how it is displayed and queried.

### Origin Plane

- **Source**: SoW document only
- **Mutability**: Read-only after initialization. Only `coverage_status` updates.
- **Node types**: `goal`, `feature`, `component`, `constraint`, `actor`, `milestone`
- **Edge types**: `depends_on`, `implements`, `constrains`, `owned_by`, `delivers`
- **Role**: Ground truth of project scope.

### User Plane

- **Source**: Team chat messages (incremental)
- **Mutability**: Append-only
- **Node types**: `decision`, `work_item`, `proposed_feature`, `concern`, `discussion_topic`, `blocker`
- **Edge types (intra-plane)**: `relates_to`, `blocks`, `leads_to`, `supersedes`
- **Role**: Living record of what the team has discussed and decided.

### Cross-Plane Edges

| Relation | Direction | Meaning |
|---|---|---|
| `addresses` | User → Origin goal | Discussion works toward this goal |
| `implements` | User proposed_feature → Origin feature | Elaborates an SoW feature |
| `violates` | User decision → Origin constraint | Contradicts a constraint |
| `extends` | User work_item → Origin component | Lives under this component |
| `out_of_scope` | User → (none) | No traceable link to any Origin node |

**Coverage** = fraction of Origin `goal` nodes with ≥1 incoming `addresses` edge.  
**Deviation** = User Plane nodes with no outgoing cross-plane edge of any kind.

---

## Chat Tagging

| Tag | Meaning | User Plane node type created |
|---|---|---|
| `#decision` | Locked choice | `decision` |
| `#feature` | New feature proposed | `proposed_feature` |
| `#concern` | Risk or worry | `concern` |
| `#sprint` | Sprint planning content | `work_item` |
| `#architecture` | Architecture-level discussion | `discussion_topic` |
| `#blocker` | Blocks progress | `blocker` |
| `#out-of-scope` | Explicit out-of-scope acknowledgment | triggers deviation flag |

Tags are applied inline (`#tag` in message text), toggled via the UI chip bar, or AI-suggested and confirmed before send. Messages are filterable by tag in chat history.

---

## Technology Choices

| Concern | Choice | Rationale |
|---|---|---|
| LLM | Groq (llama-3.3-70b-versatile) | Fast inference, free tier, OpenAI-compatible SDK |
| LLM client | `groq` Python SDK (`client.chat.completions.create`) | OpenAI-compatible; single `config.py` wrapper makes it swappable |
| Config | `.env` file + `python-dotenv` | `GROQ_API_KEY` and `LLM_MODEL`; validated at startup |
| Backend | FastAPI (Python 3.11+) | Simple, async, auto-docs |
| KG store | NetworkX in-memory `DiGraph` + JSON persistence | No infra; sufficient for project-scale graphs |
| KG serialization | `node_link_data` → JSON files | Round-trips cleanly with `ProjectKG.to_json` / `from_json` |
| KG visualization | Cytoscape.js (CDN) | Standalone JS, rich layout, no build step |
| DRL rules | Structured JSON objects (when/then/salience) rendered as pseudo-DRL | True Drools requires JVM; Python evaluation is equivalent at this scale |
| PDF ingestion | `pypdf` | Lightweight, no binary deps |
| PDF generation (tests) | `reportlab` | Used only in `tests/generate_sows.py` |
| Frontend | Single HTML + vanilla JS (no build step) | Zero toolchain friction |
| Persistence | JSON files under `data/projects/{project_id}/` | No DB needed at this scale |
| Test runner | `pytest` + `httpx` | Integration tests against live server |

---

## Directory Layout

```
.
├── CLAUDE.md
├── SPEC.md
├── .env                         # GROQ_API_KEY, LLM_MODEL (gitignored)
├── .env.example                 # Safe template
├── requirements.txt
├── backend/
│   ├── main.py                  # FastAPI app — 21 routes + static file mount
│   ├── config.py                # Loads .env, validates key, exposes complete()
│   ├── sow_ingestor.py          # PDF extraction → Origin Plane KG + DRL rules
│   ├── knowledge_graph.py       # ProjectKG: NetworkX DiGraph wrapper
│   ├── rules_engine.py          # Pseudo-DRL evaluation via LLM
│   ├── chat_handler.py          # Message processing, scoring, User Plane update
│   └── models.py                # Pydantic v2 models
├── frontend/
│   ├── index.html               # 3-panel SPA (KG viewer | Chat | Rules)
│   └── scenarios.html           # Scenario runner (select → run → visualize)
├── data/
│   └── projects/                # Per-project persisted KG + DRL state (JSON)
└── tests/
    ├── generate_sows.py         # Generates 10 synthetic SOW PDFs (reportlab)
    ├── conversations.py         # Expected conversations per document
    ├── conftest.py              # Pytest fixtures, api() helper
    ├── test_loading.py          # PDF upload, KG structure, rules generation tests
    ├── test_alignment.py        # Alignment score, deviation, coverage tests
    ├── pytest.ini
    └── sows/                    # 10 generated PDFs (gitignored)
        ├── it_01_ecommerce_platform.pdf
        ├── it_02_erp_implementation.pdf
        ├── it_03_cloud_migration.pdf
        ├── it_04_cybersecurity_infrastructure.pdf
        ├── it_05_healthcare_ehr.pdf
        ├── ds_01_churn_prediction.pdf
        ├── ds_02_fraud_detection.pdf
        ├── ds_03_supply_chain_analytics.pdf
        ├── ds_04_nlp_document_intelligence.pdf
        └── ds_05_predictive_maintenance.pdf
```

---

## API Endpoints

```
# Health
GET  /health

# Projects
POST  /projects                              # Create project (multipart: name + sow_text | sow_pdf)
GET   /projects                              # List all projects
GET   /projects/{id}                         # Get project metadata
DELETE /projects/{id}                        # Delete project

# Knowledge Graph
GET  /projects/{id}/kg                       # Full KG (both planes + cross edges)
GET  /projects/{id}/kg?plane=origin          # Origin Plane only
GET  /projects/{id}/kg?plane=user            # User Plane only
GET  /projects/{id}/kg/coverage              # Coverage summary

# Rules
GET  /projects/{id}/rules                    # All rules with current violation status

# Team Members
POST /projects/{id}/members                  # Add member
GET  /projects/{id}/members                  # List members

# Chat
POST  /projects/{id}/chat                    # Send message
GET   /projects/{id}/chat/history            # Full history (filterable by ?tag= or ?author=)
PATCH /projects/{id}/chat/{msg_id}/tags      # Update tags on a message
POST  /projects/{id}/chat/suggest-tags       # AI tag suggestions for a draft message

# Test Scenarios (for scenario runner UI)
GET  /test-scenarios/pdfs                    # List available synthetic SOW PDFs
GET  /test-scenarios/pdfs/{pdf_name}         # Serve a PDF file for browser upload
```

---

## Data Models

### KG Node

```json
{
  "id": "string",
  "label": "string",
  "plane": "origin | user",
  "type": "goal | feature | component | constraint | actor | milestone | decision | work_item | proposed_feature | concern | discussion_topic | blocker",
  "description": "string",
  "source": { "type": "sow | chat_message", "ref": "excerpt | message_id" },
  "coverage_status": "unaddressed | partial | covered"
}
```

### KG Edge

```json
{
  "id": "string",
  "source": "node_id",
  "target": "node_id",
  "plane": "origin | user | cross",
  "relation": "depends_on | implements | constrains | owned_by | delivers | relates_to | blocks | leads_to | supersedes | addresses | violates | extends | out_of_scope"
}
```

### Rule (pseudo-DRL)

```json
{
  "rule_id": "string",
  "name": "string",
  "salience": 10,
  "when": "string",
  "then": "string",
  "sow_excerpt": "string",
  "violation_status": "ok | at_risk | violated"
}
```

### Chat Message

```json
{
  "message_id": "uuid",
  "role": "user | assistant",
  "author": { "member_id": "uuid", "handle": "string" },
  "content": "string",
  "tags": ["#decision"],
  "timestamp": "iso8601",
  "metadata": {
    "alignment_score": 0,
    "in_scope": [],
    "out_of_scope": [],
    "deviations": [],
    "coverage_delta": [],
    "recommendations": [],
    "suggested_tags": [],
    "user_plane_nodes_created": [],
    "cross_plane_edges_created": []
  }
}
```

---

## LLM Prompt Strategy

All LLM calls go through `config.complete(messages, max_tokens)`. This is the single swap point if the model or provider changes.

### SoW Extraction (Origin Plane)

Extracts: goals, features, components, constraints, actors, milestones. Returns structured JSON matching the KG node/edge schema. JSON braces in prompt templates are escaped as `{{`/`}}` to prevent Python `.format()` interpolation.

### DRL Rule Generation

From the SoW text + extracted KG, generates rules with `when` / `then` / `salience` / `sow_excerpt`. Typical rules cover: scope boundaries, timeline constraints, security/compliance requirements, subcontracting clauses, IP ownership.

### Chat Processing (User Plane + Alignment)

Single LLM call per message that returns:
1. Alignment score (0–100)
2. In-scope / out-of-scope component lists
3. Deviation flags against active rules
4. Coverage delta (new Origin goals addressed)
5. Architecture recommendations
6. User Plane nodes extracted from the message
7. Cross-plane edges proposed (with relation type)

### Tag Suggestion

Fast call returning suggested tags from the fixed set with rationale. Used by the "Suggest tags" button in the UI and the scenario runner.

---

## Test Suite

### Synthetic SOW PDFs (`tests/generate_sows.py`)

10 documents generated with `reportlab`, covering two domains:

**IT Projects**
1. `it_01_ecommerce_platform.pdf` — RetailMax e-commerce (WI with DoD, PCI-DSS, CREST VAPT)
2. `it_02_erp_implementation.pdf` — ManufaCo ERP (8-plant rollout, SOC 2, quarterly VAPT)
3. `it_03_cloud_migration.pdf` — FinServe cloud migration (Terraform/tfsec, AWS Security Hub)
4. `it_04_cybersecurity_infrastructure.pdf` — GovProtect SOC (EDR, SOAR, monthly red team)
5. `it_05_healthcare_ehr.pdf` — MediCare EHR (HIPAA, FHIR R4, BAA, PHI controls)

**Data Science Projects**
6. `ds_01_churn_prediction.pdf` — TelecomPlus (AUC-ROC targets, bias audit, subscriber data IP)
7. `ds_02_fraud_detection.pdf` — PaySecure (50k TPS, PCI-DSS, HSM key management)
8. `ds_03_supply_chain_analytics.pdf` — GlobalLogix (MAPE targets, Tier-1 supplier risk)
9. `ds_04_nlp_document_intelligence.pdf` — LegalEagle (clause F1, hallucination rate, attorney-AI workflow)
10. `ds_05_predictive_maintenance.pdf` — IndustrialOps (IoT 500 streams, ICS security, SCADA)

Each document includes: project overview, work item table with DoD/entry/exit criteria, legal section (penalties, subcontracting, indemnity, IP), security requirements (SAST tool, VAPT frequency), project closure criteria, MVP definition.

### Automated Tests (`pytest`)

**`test_loading.py`** (10 parametrized + 3 standalone):
- PDF upload returns 201 with UUID
- Origin Plane KG has ≥5 nodes, ≥3 node types, ≥3 edges
- ≥4 rules generated per document with required fields
- Initial coverage = 0%

**`test_alignment.py`** (335 tests total):
- Aligned messages score ≥ `expect_score_min` (default 60)
- Aligned messages increase or maintain coverage
- Misaligned messages score ≤ `expect_score_max` (default 40)
- Misaligned messages flag out-of-scope and deviation items
- Misaligned messages generate recommendations
- Edge cases produce non-empty advisor responses
- After any chat, User Plane nodes are created
- After all aligned messages, coverage > 0%
- After all misaligned messages, ≥1 rule is `at_risk` or `violated`

### Running Tests

```bash
# Generate PDFs first (one-time)
python tests/generate_sows.py

# Start the backend
cd backend && uvicorn main:app --reload

# In another terminal
pytest
```

---

## UI Layout

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  Project Advisor  [project-name]  [loading…]       [Switch] [+ New] [⚗ Scenarios]│
├─────────────────────┬─────────────────────────────┬──────────────────────────────┤
│  KNOWLEDGE GRAPH    │  TEAM CHAT               [▼] │  RULES & COVERAGE            │
│  [Origin][User][Both│  [tag filter ▼]              │                              │
│                     │                              │  Goal coverage   42%         │
│  Origin (solid)     │  @alice [#decision]          │  ████████░░░░░░░░            │
│  ● goal             │    "Locking auth to OAuth2"  │                              │
│  ■ feature          │    [score:88] [✓ auth-goal]  │  ✓ Scope boundary            │
│  ▲ constraint       │                              │  ⚠ VAPT requirement          │
│                     │  assistant                   │  ✗ Subcontract clause        │
│  ─ ─ ─ ─ ─ ─ ─ ─   │    "Aligned. Addresses       │                              │
│                     │     goal:auth-security…"     │                              │
│  User (dashed)      │                              │                              │
│  ◆ decision         │  [@handle]                   │                              │
│  ◇ work_item        │  [message input…          ]  │                              │
│  ◈ concern          │  #decision #feature #concern │                              │
│                     │  #sprint #arch #blocker #oos │                              │
│  [node detail]      │  [Suggest tags]       [Send] │                              │
└─────────────────────┴─────────────────────────────┴──────────────────────────────┘
```

**Scenario Runner** (`/scenarios.html`):

```
┌──────────────────────────────────────────────────────────────────────┐
│  Scenario Runner  [idle]       [Deselect All] [Select All] [▶ Run]  │
├──────────────────────────────┬───────────────────────────────────────┤
│  SCENARIOS  [0 selected]     │  EXECUTION LOG                        │
│  [Expand All] [Collapse All] │                                       │
│                              │  📂 E-Commerce Platform               │
│  ▼ IT: E-Commerce  IT 5/7    │  Creating project from PDF…           │
│    ☑ aligned  Sprint ready ✅ │  Project created: a1b2c3d4…          │
│    ☑ aligned  CREST pentest✅ │  Sending [aligned]: We've completed… │
│    ☐ misalign Crypto pay  ❌  │  ┌ ALIGNED  score: 82  ✅ ≥60       │
│    ☐ misalign Skip VAPT   ─   │  │ ✓auth-security ✓pci-scope        │
│    ☐ edge     Delay notice─   │  └ 3 KG nodes · 2 cross-plane edges │
│                              │                                       │
│  ▶ DS: Churn Prediction  DS  │                                       │
└──────────────────────────────┴───────────────────────────────────────┘
```

---

## Configuration

```bash
# .env (never committed — see .env.example)
GROQ_API_KEY=gsk_...
LLM_MODEL=llama-3.3-70b-versatile
```

`backend/config.py` loads `.env` at import time, validates `GROQ_API_KEY` (exits with a clear message if missing), and exposes a single `complete(messages, max_tokens)` helper. All LLM calls in the backend go through this function.

---

## Running the Project

```bash
# Install dependencies
pip install -r requirements.txt

# Configure LLM
cp .env.example .env
# Edit .env and set GROQ_API_KEY

# Generate test SOW PDFs (one-time)
python tests/generate_sows.py

# Start the backend (serves frontend as static files)
cd backend && uvicorn main:app --reload

# Open in browser
# Main UI:         http://localhost:8000/
# Scenario runner: http://localhost:8000/scenarios.html
# API docs:        http://localhost:8000/docs
```

---

## Resolved Design Decisions

| Question | Decision |
|---|---|
| Session memory? | Full chat history is sent as context to the LLM on every alignment call |
| KG nodes update dynamically? | Yes — `coverage_status` updates on Origin nodes; User Plane is append-only |
| Multi-project support? | Yes — projects are isolated by `project_id`; the UI has a project switcher |
| Authentication? | None — single-user local tool |
| Prescriptive vs descriptive recommendations? | Both — LLM returns specific architecture observations and actionable suggestions |
| AI tags auto-applied or confirmed? | Suggested in the assistant response; user confirms or dismisses before the message is committed |
| User Plane nodes per-message or merged? | Per-message (fine-grained); LLM deduplicates by label when building cross-plane edges |
| Cross-plane edges user-overridable? | Not in v1 — LLM proposal is accepted; `PATCH /chat/{msg_id}/tags` allows tag correction |
| Cross-plane edges always visible? | Both planes + cross edges shown by default; plane toggle lets users isolate |
