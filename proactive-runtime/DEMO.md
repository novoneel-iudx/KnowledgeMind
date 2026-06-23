# Demo Guide — Proactive Runtime & Daily Briefing

Everything below runs **offline, no API keys needed**. The demo works in two modes:
- **No Groq key** — skills produce deterministic canned output (good enough for a video demo)
- **With Groq key** — live LLM skills run through the privacy router (shows the full system)

---

## Step 1 — Run the offline tests (30 seconds)

This is the fastest way to show the whole system is wired and working.

```bash
python -m runtime.tests
```

Expected output:
```
runtime stub-LLM unit tests
  PASS  loader: 5 real jobs + skills load with no errors
  PASS  loader: malformed specs raise clear, file-named SpecErrors
  PASS  loader: job referencing an unknown skill is dropped + reported
  PASS  scheduler: due job at fixed now fires its skill (stub) -> nudge in outbox
  PASS  quiet hours: due job is queued (suppressed), not delivered
  PASS  briefing: composes commitments + conflicts + signals (with fusion)
  PASS  outbox: list / dismiss / idempotent dismiss / active count
  PASS  privacy: runner reaches the agent; personal skill observed LOCAL
All runtime tests passed.
```

What to say while this runs: *"These run fully offline — no Ollama, no API keys. The scheduler fires jobs, routes them through the privacy layer, and writes nudges to the outbox. The quiet-hours test verifies that a nudge generated at 11 PM is queued but not delivered."*

---

## Step 2 — Start the app

```bash
python launcher.py
```

Or if you want to see backend logs live (recommended for a demo):
```bash
uvicorn api.main:app --reload --port 8000
```

The app seeds the demo database on startup, so you'll have a commitment already in the KG.

---

## Step 3 — Open the interactive API docs

Go to **http://127.0.0.1:8000/docs** in your browser.

This is FastAPI's built-in UI — every endpoint is listed, expandable, and executable from the browser. No curl, no Postman needed. Great for a screen recording.

---

## Step 4 — The demo flow (in order)

### 4a. Show today's briefing

**Endpoint:** `GET /api/briefing`

Click **Try it out → Execute**.

What you'll see:
```json
{
  "briefing": {
    "date": "2026-06-23",
    "headline": "1 commitment(s) today.",
    "commitments_today": [
      { "description": "Project Atlas design review", "at": "14:00", "type": "HARD" }
    ],
    "conflicts": [],
    "task_load": null,
    "readiness": null,
    "surface": true,
    "formatted": "**Today (1 commitment(s)):**\n- 14:00 Project Atlas design review"
  }
}
```

What to say: *"The briefing is deterministic — no LLM call. It reads today's commitments and conflicts directly from the knowledge graph, then folds in health and task signals when they're available. `task_load` and `readiness` are null here because no connectors have run yet — they populate as soon as the scheduler fires."*

---

### 4b. Fire the scheduler (the main demo moment)

**Endpoint:** `POST /api/runtime/tick`

Set `force` = `true` (this runs all 5 jobs regardless of their schedule — otherwise only jobs due at this exact minute would fire).

Click **Try it out → Execute**.

What you'll see (5 jobs fired, 3 produce nudges, 2 correctly stay silent):
```json
{
  "count": 5,
  "fired": [
    {
      "job": "discord_unread_check",
      "surfaced": false,
      "nudge_id": null,
      "reason": "skill chose to stay silent"
    },
    {
      "job": "evening_tasks",
      "surfaced": true,
      "nudge_id": 1,
      "answer": "Evening check: 3 tasks still open today. Want to reschedule any?"
    },
    {
      "job": "fitness_check",
      "surfaced": true,
      "nudge_id": 2,
      "answer": "No workout logged today and recovery looks fine — a short walk would help."
    },
    {
      "job": "mood_check",
      "surfaced": false,
      "nudge_id": null,
      "reason": "skill chose to stay silent"
    },
    {
      "job": "morning_brief",
      "surfaced": true,
      "nudge_id": 3,
      "routing_log": [{ "tool": "demo", "decision": "LOCAL" }],
      "answer": "**Today (1 commitment(s)):**\n- 14:00 Project Atlas design review"
    }
  ]
}
```

What to say: *"The scheduler just ran all 5 skills. Discord and mood stayed silent — the skill recipes say to surface only if there's something actionable, and right now there isn't. The evening task check, fitness check, and morning brief all produced nudges. Notice the `routing_log` — every skill goes through the privacy router. Personal data stays LOCAL."*

---

### 4c. Show the nudge feed

**Endpoint:** `GET /api/nudges`

What you'll see:
```json
{
  "nudges": [
    { "id": 3, "text": "**Today (1 commitment(s)):**\n- 14:00 Project Atlas design review",
      "skill": "morning_brief_skill", "job": "morning_brief",
      "iso": "2026-06-23T08:00:00", "dismissed": false, "suppressed": false },
    { "id": 2, "text": "No workout logged today and recovery looks fine — a short walk would help.",
      "skill": "fitness_skill", "job": "fitness_check", "dismissed": false },
    { "id": 1, "text": "Evening check: 3 tasks still open today. Want to reschedule any?",
      "skill": "tasks_skill", "job": "evening_tasks", "dismissed": false }
  ],
  "active_count": 3
}
```

What to say: *"This is the nudge outbox — what the UI will render as the notification feed. `active_count` is the badge number. The `suppressed` flag would be true for nudges generated during quiet hours — they're queued but not delivered until morning."*

---

### 4d. Dismiss a nudge

**Endpoint:** `POST /api/nudges/{nudge_id}/dismiss`

Set `nudge_id` = `3` (or whichever id appeared above).

Response:
```json
{ "ok": true, "id": 3, "dismissed": true }
```

Call `GET /api/nudges` again — `active_count` drops from 3 to 2. Calling dismiss a second time on the same id returns `"dismissed": false` — idempotent.

---

### 4e. Show the job catalog

**Endpoint:** `GET /api/runtime/jobs`

```json
{
  "jobs": [
    { "id": "morning_brief",   "schedule": "0 8 * * *",      "agency_level": "L2", "last_fired": "2026-06-23 08:00" },
    { "id": "fitness_check",   "schedule": "0 17 * * *",     "agency_level": "L2", "last_fired": "2026-06-23 08:00" },
    { "id": "evening_tasks",   "schedule": "30 18 * * *",    "agency_level": "L2", "last_fired": "2026-06-23 08:00" },
    { "id": "mood_check",      "schedule": "*/30 9-21 * * *","agency_level": "L2", "last_fired": "2026-06-23 08:00" },
    { "id": "discord_unread_check", "schedule": "0 */3 * * *","agency_level": "L2", "last_fired": "2026-06-23 08:00" }
  ],
  "errors": []
}
```

What to say: *"These are loaded directly from `hermes_jobs/*.json` at startup. Adding a new job is just dropping a JSON file — the loader and scheduler pick it up automatically, no code change needed."*

---

## Optional: demo with curl instead of the browser

If you prefer a terminal demo:

```bash
# Start the app first (separate terminal)
uvicorn api.main:app --port 8000

# Today's briefing
curl -s http://localhost:8000/api/briefing | python -m json.tool

# Fire all jobs
curl -s -X POST "http://localhost:8000/api/runtime/tick?force=true" | python -m json.tool

# Nudge feed
curl -s http://localhost:8000/api/nudges | python -m json.tool

# Dismiss nudge id=1
curl -s -X POST http://localhost:8000/api/nudges/1/dismiss | python -m json.tool

# Job catalog
curl -s http://localhost:8000/api/runtime/jobs | python -m json.tool
```

---

## Optional: show quiet hours working

Run this Python snippet to show a nudge being suppressed at 11 PM:

```python
from runtime.runner import ProactiveRuntime, demo_agent_run
from runtime import outbox
import datetime, tempfile, os

with tempfile.TemporaryDirectory() as tmp:
    db = os.path.join(tmp, "demo.db")
    rt = ProactiveRuntime(agent_run=demo_agent_run, db_path=db)

    # Fire at 11 PM — quiet hours are 22:00 to 08:00
    night = datetime.datetime(2026, 6, 23, 23, 0)
    results = rt.run_due_jobs(night, force=True)

    for r in results:
        if r["surfaced"]:
            print(f"DELIVERED: [{r['job']}] {r['answer'][:60]}")
        elif r.get("suppressed"):
            print(f"SUPPRESSED (quiet hours): [{r['job']}]")
        else:
            print(f"SILENT: [{r['job']}]")

    delivered = outbox.list_nudges(include_suppressed=False, db_path=db)
    suppressed = outbox.list_nudges(include_suppressed=True, db_path=db)
    print(f"\nDelivered: {len(delivered)} | Total queued: {len(suppressed)}")
```

---

## What each part of the demo proves

| Demo step | What it shows |
|---|---|
| `python -m runtime.tests` | Entire system tested offline, no keys needed |
| `GET /api/briefing` | Deterministic KG read → structured daily digest |
| `POST /api/runtime/tick?force=true` | Scheduler fires all skills; 2 of 5 stay correctly silent |
| `routing_log` in tick response | Every skill routes LOCAL — privacy invariant visible |
| `GET /api/nudges` | Persistent outbox, `active_count` badge |
| Dismiss → re-fetch | Idempotent dismiss, active count decrements |
| Quiet hours snippet | Nudges suppressed at 11 PM, queued not delivered |
| `GET /api/runtime/jobs` | Live job catalog loaded from `hermes_jobs/*.json` |
