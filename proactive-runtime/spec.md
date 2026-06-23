# SPEC — Stream 2: Proactive Runtime & Daily Briefing (Owner: Agent, Tools & MCP)

## 1. Overview & goal
Build the **missing engine** that makes the agent act on a schedule. `hermes_skills/*.md` +
`hermes_jobs/*.json` exist as design specs with **no loader/runner** today. Build the runtime
that fires jobs on a cron, runs each skill **through the privacy router**, and emits **proactive
nudges + a daily briefing** — respecting the `preemptive_quiet_hours` config that already exists.
Continue as **steward of Contract 2** (`orchestrator.run()`).

**User value:** the product goes from "answers when asked" to "tells me what matters before I ask."

## 2. Owns / scope boundary
- `agent/` (orchestrator, tools, prompts, `token_tracker`), new `runtime/` (loader + runner),
  `hermes_skills/`, `hermes_jobs/`, `mcp_serve.py`.
- **Steward of Contract 2**: keep `run()`'s return dict additive-only. New tools/skills via the
  **registry**, never by editing the `_run_*` core.

## 3. Integration contracts
- **Consumes:** the KG + Stream 1's `GET /api/insights` (readiness) for the briefing; the router
  (Stream 3).
- **Produces:** the **job/skill interface**, a **nudge outbox**, and a briefing; `GET /api/nudges`,
  `GET /api/briefing`.
- **Seam rule:** Stream 3's guardrails wrap `run()` as middleware — agree the hook day-1; don't
  both edit the orchestrator body. **Every skill executes through the router** (no cloud bypass).

## 4. Functional requirements

### Core (P0)
- **FR-2.1 — Job + skill loader.** Parse `hermes_jobs/*.json` (`id, schedule, skill, agency_level`)
  + `hermes_skills/*.md` (recipe/prompt). Validate on load; clear errors for malformed specs.
- **FR-2.2 — Runner / scheduler.** A cron-like loop (asyncio task in the FastAPI lifespan, or a
  sidecar like `mcp_serve.py`) that fires due jobs, runs the skill via the agent, and writes a
  nudge to the outbox. **Respect `preemptive_quiet_hours_start/end`.** Clock is **injectable**
  (a `now` parameter) so it is testable offline.
- **FR-2.3 — Daily-briefing skill.** Compose today's commitments + conflicts + Stream-1 readiness
  + task load into a digest; expose `GET /api/briefing`.
- **FR-2.4 — Nudge outbox + API.** Persist nudges `(text, skill, ts, dismissed)`;
  `GET /api/nudges`, `POST /api/nudges/{id}/dismiss`.

### Extended (P1)
- **FR-2.5 — Auto agency-level.** Pick L1/L2/L3 from task complexity instead of a manual choice.
- **FR-2.6 — Typed tools.** `pydantic` argument schemas + validation for every tool in the registry.
- **FR-2.7 — Long-term memory.** Wire the currently-unused ChromaDB memory so the agent recalls
  cross-session context (personalized nudges/briefings).
- **FR-2.8 — More skills.** "you promised X by EOD" follow-ups; free-slot proposals; morning-briefing variants.

### Stretch (P2)
- **FR-2.9** — MCP **client** (consume external MCP servers); planner/executor/critic split;
  human-in-the-loop gates for confirm-gated skills (e.g. draft-a-reply).

## 5. Non-goals
- Routing/privacy policy (Stream 3 — skills *call* the router). KG internals + correlation (Stream 1).
- UI rendering (Person 5). Delivery channels beyond in-app (email optional; Discord is dropped).

## 6. UI surface needed (Person 5)
- **Daily Briefing card** on the dashboard; a **nudge feed** with dismiss.
- **Agent-activity panel:** plan steps, each tool call + args + result, replans, agency level.
- **Skill/Job catalog:** scheduled jobs + last run + next run.
- **APIs:** `GET /api/briefing`, `GET /api/nudges`, `POST /api/nudges/{id}/dismiss`.

## 7. Acceptance criteria
- **Given** a job whose schedule is due at an injected `now`, **then** the runner fires its skill
  **offline (stub LLM)** and a nudge appears in the outbox.
- **Given** quiet hours, **then** nudges are suppressed (queued, not delivered).
- **Given** `GET /api/briefing`, **then** it returns a digest combining commitments + conflicts + readiness.
- **Given** a personal-data skill, **then** it is observed to route LOCAL (no direct cloud call).

## 8. Testing
- Stub-LLM unit tests: loader, a scheduler tick (fixed `now`), the briefing composer, each new
  tool via `dispatch_tool`.
- MCP server smoke test (start + list tools) in CI.

## 9. Definition of done
Runtime fires a scheduled skill offline → nudge; briefing endpoint live; `run()` dict additive +
documented; quiet-hours respected; "UI surface needed" handed to Person 5; spec finalized + reflection.
