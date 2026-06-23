"""
runtime/
--------
Proactive Runtime & Daily Briefing.

The "missing engine" that makes the agent act on a schedule instead of only on
request:

  loader.py    — parse hermes_jobs/*.json + hermes_skills/*.md into validated specs
  cron.py      — dependency-free cron matcher (injectable clock)
  outbox.py    — dismissable nudge outbox (text, skill, ts, dismissed)
  briefing.py  — deterministic daily-briefing composer
  runner.py    — scheduler: fire due jobs through the agent (→ router) → nudge

Privacy invariant: the runner never calls an LLM directly. It invokes
``agent.run()``, which routes every step through the privacy router. Tests inject
a stub ``agent_run`` so the whole runtime is exercisable offline (no Ollama/keys).
"""
