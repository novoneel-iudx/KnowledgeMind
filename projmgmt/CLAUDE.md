# Project: Team Management Chatbot

## Project Overview

A standalone chatbot system for project team management. Ingests a Statement of Work (SoW), builds a Knowledge Graph and DRL (Drools Rule Language) business rules, then acts as a real-time advisor during team discussions — flagging deviations, rating alignment, and suggesting architectural directions.

## Key Concepts

- **SoW Ingestion**: Project is bootstrapped from a Statement of Work document (text/PDF). Ask a user for it when project is initialized.
- **Knowledge Graph (KG)**: Entities (features, components, goals, constraints) and their relationships, extracted from the SoW
- **DRL Rules**: Business process rules derived from the SoW (scope boundaries, priorities, compliance constraints)
- **Alignment Rating**: Numeric score (0–100) indicating how closely a discussion topic or proposed feature aligns with project goals
- **Deviation Detection**: Identifies topics/features that are out of scope or contradict DRL rules
- **Coverage Tracking**: Shows which project goals/requirements have been addressed vs. untouched

## Stack Decisions

- See SPEC.md for detailed technology choices and rationale
- Keep the solution **standalone and simple** — no microservices, no heavy infra
- Single Python backend, single-page frontend

## Directory Layout (target)

```
.
├── CLAUDE.md
├── SPEC.md
├── backend/
│   ├── main.py              # FastAPI app entry point
│   ├── sow_ingestor.py      # SoW parsing → KG + DRL generation
│   ├── knowledge_graph.py   # KG operations (build, query, update)
│   ├── rules_engine.py      # DRL rule evaluation
│   ├── chat_handler.py      # Chat session logic + alignment scoring
│   └── models.py            # Pydantic data models
├── frontend/
│   ├── index.html           # Single HTML file (or Vite/React SPA)
│   ├── chat.js              # Chat UI
│   ├── kg_viewer.js         # Knowledge graph visualizer
│   └── rules_viewer.js      # DRL rules viewer
├── data/
│   └── projects/            # Per-project persisted KG + DRL state
└── requirements.txt
```

## Development Guidelines

- Python 3.11+
- Use `uv` for dependency management if available, else `pip`
- FastAPI for the backend API
- No mocking of core logic — alignment scoring and KG queries must be real
- Keep frontend dependencies minimal — prefer vanilla JS or a single framework
- All LLM calls go through a single wrapper so the model can be swapped
- Write no comments unless the WHY is non-obvious

## Running the Project

```bash
cd backend && uvicorn main:app --reload
# Frontend served from backend as static files, or open frontend/index.html directly
```

## Current Status

- [ ] SPEC.md written and agreed
- [ ] Backend scaffolded
- [ ] SoW ingestor working
- [ ] KG viewer working
- [ ] DRL viewer working
- [ ] Chat handler working
- [ ] Alignment scoring working
- [ ] Deviation detection working
