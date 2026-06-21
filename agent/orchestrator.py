"""
agent/orchestrator.py
---------------------
LangGraph-based orchestrator supporting three agency levels:

  L1 — Augmented LLM   : single LLM call + tool injection, no loop
  L2 — Workflow         : plan → execute×N → critique (fixed flow)
  L3 — Autonomous Agent : ReAct loop (thought→action→observe) + replan

Token consumption is tracked via TokenTracker and returned alongside
the answer for display in the UI.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import time
import uuid
from enum import Enum
from typing import Any, Optional, TypedDict

from dotenv import load_dotenv

load_dotenv()

from agent.prompts import (
    PLANNER_SYSTEM_PROMPT,
    LOCAL_EXECUTOR_SYSTEM_PROMPT,
    CRITIC_SYSTEM_PROMPT,
    DIRECT_ANSWER_PROMPT,
    REACT_THOUGHT_PROMPT,
    L1_AGENT_PROMPT,
)
from agent.token_tracker import TokenTracker, TokenEvent
from config.store import get_config
from routing.router import router, RoutingDecision
from memory.memory_manager import memory_manager

MAX_REACT_ITERATIONS = int(os.getenv("MAX_REACT_ITERATIONS", "5"))
MAX_REPLAN_ATTEMPTS  = 3


# ---------------------------------------------------------------------------
# Agency Level
# ---------------------------------------------------------------------------

class AgencyLevel(str, Enum):
    L1_AUGMENTED  = "L1"   # Single LLM call + tools
    L2_WORKFLOW   = "L2"   # Plan → execute × N → critique
    L3_AUTONOMOUS = "L3"   # ReAct loop + replan

LEVEL_LABELS = {
    AgencyLevel.L1_AUGMENTED:  "L1 — Augmented LLM (single call)",
    AgencyLevel.L2_WORKFLOW:   "L2 — Workflow (plan→execute→critique)",
    AgencyLevel.L3_AUTONOMOUS: "L3 — Autonomous Agent (ReAct loop)",
}


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _call_groq(
    messages: list[dict],
    system: str,
    tracker: TokenTracker,
    node: str,
    agency_level: str,
    temperature: float = 0.1,
    max_tokens: int = 2000,
) -> str:
    cfg = get_config()
    from groq import Groq
    client = Groq(api_key=cfg.groq_api_key)
    model = cfg.cloud_model

    full_messages = [{"role": "system", "content": system}] + messages
    response = client.chat.completions.create(
        model=model,
        messages=full_messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    tracker.record(TokenEvent(
        node=node,
        model=model,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        agency_level=agency_level,
    ))

    return response.choices[0].message.content


def _call_groq_fast(
    messages: list[dict],
    system: str,
    tracker: TokenTracker,
    node: str,
    agency_level: str,
    max_tokens: int = 512,
) -> str:
    cfg = get_config()
    from groq import Groq
    client = Groq(api_key=cfg.groq_api_key)
    model = cfg.cloud_model_fast

    full_messages = [{"role": "system", "content": system}] + messages
    response = client.chat.completions.create(
        model=model,
        messages=full_messages,
        temperature=0.0,
        max_tokens=max_tokens,
    )

    tracker.record(TokenEvent(
        node=node,
        model=model,
        prompt_tokens=response.usage.prompt_tokens,
        completion_tokens=response.usage.completion_tokens,
        agency_level=agency_level,
    ))

    return response.choices[0].message.content


def _call_ollama(
    messages: list[dict],
    system: str,
    tracker: TokenTracker,
    node: str,
    agency_level: str,
    max_tokens: int = 512,
) -> str:
    cfg = get_config()

    try:
        from ollama import Client
        client = Client(host=cfg.ollama_base_url)
        model = cfg.local_model

        full_messages = [{"role": "system", "content": system}] + messages
        response = client.chat(model=model, messages=full_messages, options={"temperature": 0.1})

        # Ollama token counts
        prompt_tokens     = getattr(response, "prompt_eval_count", 0) or 0
        completion_tokens = getattr(response, "eval_count", 0) or 0

        tracker.record(TokenEvent(
            node=node,
            model=f"{model} (local)",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            agency_level=agency_level,
        ))

        return response.message.content

    except Exception as e:
        # Fallback to Groq fast tier if Ollama unreachable
        print(f"[Orchestrator] Ollama unavailable ({e}), falling back to Groq fast tier")
        return _call_groq_fast(messages, system, tracker, f"{node}_fallback", agency_level, max_tokens)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    for pattern in [r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```", r"\{.*\}"]:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                candidate = match.group(1) if "```" in pattern else match.group(0)
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return None


# ---------------------------------------------------------------------------
# Tool dispatch (imported lazily to avoid circular imports)
# ---------------------------------------------------------------------------

def _dispatch_tool(tool_name: str, parameters: dict) -> dict:
    from agent.tools import dispatch_tool
    return dispatch_tool(tool_name, parameters)


def _heuristic_params(tool_name: str, step_input: str) -> dict:
    if tool_name in ("web_search", "rag_query", "query_kg"):
        return {"query": step_input[:200]}
    elif tool_name == "code_execution":
        return {"code": f"print(repr('{step_input[:80]}'))", "description": step_input[:80]}
    elif tool_name == "google_calendar":
        return {"action": "list"}
    elif tool_name == "gmail":
        return {"action": "list", "query": "is:unread"}
    elif tool_name == "find_free_slots":
        return {"date": datetime.date.today().isoformat(), "duration_minutes": 60}
    return {"input": step_input[:200]}


def _parse_tool_params(
    tool_name: str,
    step_input: str,
    tracker: TokenTracker,
    agency_level: str,
    prefer_local: bool = True,
) -> tuple[dict, bool]:
    """Parse tool params via LLM. Returns (params, success)."""
    # Tell the model today's date so it resolves relative dates ("this week",
    # "tomorrow") correctly instead of hallucinating a training-era date.
    today = datetime.date.today().isoformat()
    prompt = f"Today's date is {today}.\nTool: {tool_name}\nInstruction: {step_input}"
    call_fn = _call_ollama if prefer_local else _call_groq_fast

    for attempt in range(MAX_REPLAN_ATTEMPTS):
        try:
            response = call_fn(
                [{"role": "user", "content": prompt}],
                LOCAL_EXECUTOR_SYSTEM_PROMPT,
                tracker,
                "execute",
                agency_level,
            )
            parsed = _extract_json(response)
            if parsed:
                return parsed.get("parameters", parsed), True
        except Exception:
            pass

        if attempt == 1 and prefer_local:
            # Escalate to cloud on second failure
            call_fn = _call_groq_fast

    return _heuristic_params(tool_name, step_input), False


# ---------------------------------------------------------------------------
# KG context injection (no LLM, no tokens)
# ---------------------------------------------------------------------------

def _inject_kg_context(user_input: str) -> str:
    """
    Query KG for relevant commitments and return as structured JSON string.
    This runs locally without any LLM call — zero token cost.
    """
    try:
        from agent.tools import dispatch_tool
        result = dispatch_tool("query_kg", {"query": user_input})
        if result.get("success") and result.get("nodes"):
            nodes = result["nodes"][:5]  # cap to avoid context bloat
            return json.dumps({"commitments": nodes}, indent=2)
    except Exception:
        pass
    return "{}"


# ---------------------------------------------------------------------------
# Trivial-input short circuit (answer locally, no cloud tokens)
# ---------------------------------------------------------------------------

_GREETING_PHRASES: frozenset[str] = frozenset({
    "hi", "hello", "hey", "yo", "hiya", "sup", "hi there", "hello there",
    "hey there", "good morning", "good afternoon", "good evening", "howdy",
    "thanks", "thank you", "thx", "ty", "ok", "okay", "cool", "great", "nice",
    "bye", "goodbye", "see you", "see ya", "cheers",
})


def _is_greeting(user_input: str) -> bool:
    """True for trivial greetings/pleasantries that need no cloud planning."""
    cleaned = user_input.strip().lower().strip("!.?,")
    return cleaned in _GREETING_PHRASES


# ---------------------------------------------------------------------------
# L1 — Augmented LLM
# ---------------------------------------------------------------------------

def _run_l1(
    user_input: str,
    history: list[dict],
    tracker: TokenTracker,
    routing_log: list[dict],
) -> str:
    """
    Augmented LLM: one decision call that either answers directly OR runs ONE
    tool, whose result is then synthesised into the answer (SPEC 5.1). The router
    decides local vs cloud; privacy-pinned tools keep their synthesis local so
    personal results never reach the cloud.
    """
    agency_level = "L1"
    kg_context = _inject_kg_context(user_input)

    # ── Decision call: answer directly or pick one tool ──────────────────────
    decide_routing = router.route(user_input, tool_name=None)
    decide_system = (
        f"{L1_AGENT_PROMPT}\n\n"
        f"Knowledge-graph context (the user's commitments):\n{kg_context}"
    )
    messages = history + [{"role": "user", "content": user_input}]
    if decide_routing.decision == RoutingDecision.LOCAL:
        decision_raw = _call_ollama(messages, decide_system, tracker, "single_call", agency_level)
    else:
        decision_raw = _call_groq(messages, decide_system, tracker, "single_call", agency_level, max_tokens=1000)

    parsed = _extract_json(decision_raw)

    # ── Direct answer (no tool) -- the lightweight 1-call path ───────────────
    if not parsed or "tool" not in parsed:
        routing_log.append({
            "step_id": 1, "tool": "single_call",
            "decision": decide_routing.decision.value,
            "privacy_score": decide_routing.privacy_score,
            "complexity_score": decide_routing.complexity_score,
            "reason": decide_routing.reason,
        })
        if parsed and parsed.get("answer"):
            return str(parsed["answer"]).strip()
        return decision_raw.strip()

    # ── One tool call, then synthesise ───────────────────────────────────────
    tool_name = parsed.get("tool", "")
    tool_input = parsed.get("input", user_input) or user_input
    tool_routing = router.route(tool_input, tool_name=tool_name)
    routing_log.append({
        "step_id": 1, "tool": tool_name,
        "decision": tool_routing.decision.value,
        "privacy_score": tool_routing.privacy_score,
        "complexity_score": tool_routing.complexity_score,
        "reason": tool_routing.reason,
    })

    # Heuristic params keep L1 lean (no extra param-parse LLM call).
    params = _heuristic_params(tool_name, tool_input)
    result = _dispatch_tool(tool_name, params)
    observation = result.get("formatted", result.get("output", json.dumps(result)[:600]))

    synth_input = (
        f"{user_input}\n\nResult from the {tool_name} tool:\n{observation}\n\n"
        f"Answer the user using this result."
    )
    # Privacy: synthesise locally for LOCAL-routed (personal) tools so their
    # results never reach the cloud; CLOUD-safe tools may synthesise on Groq.
    if tool_routing.decision == RoutingDecision.LOCAL:
        answer = _call_ollama([{"role": "user", "content": synth_input}],
                              DIRECT_ANSWER_PROMPT, tracker, "single_call", agency_level)
    else:
        answer = _call_groq([{"role": "user", "content": synth_input}],
                            DIRECT_ANSWER_PROMPT, tracker, "single_call", agency_level, max_tokens=1000)
    return answer.strip() or observation


# ---------------------------------------------------------------------------
# L2 — Workflow (Plan → Execute × N → Critique)
# ---------------------------------------------------------------------------

def _run_l2(
    user_input: str,
    history: list[dict],
    tracker: TokenTracker,
    routing_log: list[dict],
) -> str:
    """
    Orchestrated workflow: Groq plans steps, local LLM dispatches tools,
    Groq critiques results. Control flow is engineer-defined. No loop.
    """
    agency_level = "L2"
    kg_context = _inject_kg_context(user_input)

    # ── Plan ────────────────────────────────────────────────────────────────
    # The KG context is REFERENCE only. Without this framing the planner treats
    # the injected schedule as a task and runs schedule tools even for "Hi".
    plan_input = f"""User request: {user_input}

Background from the user's knowledge graph (reference only -- do NOT act on it
unless the request is explicitly about the schedule, commitments, or free time):
{kg_context}

Break ONLY the user's request into the minimal tool steps needed. If it is a
greeting, small talk, an acknowledgement, or a general-knowledge question that
needs no personal data or tools, return an empty "steps" list."""

    plan_response = _call_groq(
        history + [{"role": "user", "content": plan_input}],
        PLANNER_SYSTEM_PROMPT,
        tracker, "plan", agency_level, max_tokens=2000,
    )

    plan = _extract_json(plan_response)
    if not plan:
        # Graceful fallback — treat as direct answer
        return plan_response

    steps = plan.get("steps", [])
    if not steps:
        # No tools needed (greeting, general question). Produce a real answer:
        # final_answer_instruction is internal planner guidance, NOT the
        # user-facing reply, so it must never be returned verbatim.
        direct = _call_groq(
            history + [{"role": "user", "content": user_input}],
            DIRECT_ANSWER_PROMPT,
            tracker, "critique", agency_level, max_tokens=1000,
        )
        return direct.strip() or "How can I help?"

    # ── Execute each step ───────────────────────────────────────────────────
    step_results: dict[int, Any] = {}

    for step in steps:
        step_id   = step.get("step_id", 0)
        tool_name = step.get("tool", "")
        step_input = step.get("input", "")

        # Inject dependency results
        dep_ids = step.get("depends_on", [])
        if dep_ids:
            dep_ctx = "\n".join(
                f"[Step {d} result]: {json.dumps(step_results.get(d, {}))[:400]}"
                for d in dep_ids if d in step_results
            )
            if dep_ctx:
                step_input = f"{step_input}\n\nContext:\n{dep_ctx}"

        # Route
        routing_result = router.route(step_input, tool_name=tool_name)
        routing_log.append({
            "step_id": step_id,
            "tool": tool_name,
            "decision": routing_result.decision.value,
            "privacy_score": routing_result.privacy_score,
            "complexity_score": routing_result.complexity_score,
            "reason": routing_result.reason,
        })

        # Parse params
        prefer_local = routing_result.decision == RoutingDecision.LOCAL
        params, _ = _parse_tool_params(tool_name, step_input, tracker, agency_level, prefer_local)

        # Dispatch
        result = _dispatch_tool(tool_name, params)
        step_results[step_id] = result

    # ── Critique ─────────────────────────────────────────────────────────────
    results_text = "\n\n".join(
        f"Step {sid} ({steps[i].get('tool', '?')}):\n"
        + str(r.get("formatted", r.get("output", json.dumps(r)[:400])))
        for i, (sid, r) in enumerate(step_results.items())
    )

    critique_input = f"""Original request: {user_input}

Step results:
{results_text}

Synthesis instruction: {plan.get('final_answer_instruction', 'Summarise the results clearly.')}"""

    critique_response = _call_groq(
        [{"role": "user", "content": critique_input}],
        CRITIC_SYSTEM_PROMPT,
        tracker, "critique", agency_level, max_tokens=1500,
    )

    critique = _extract_json(critique_response)
    if critique:
        final_answer = (critique.get("final_answer") or "").strip()
        if final_answer:
            return final_answer

    # Critic produced no usable answer (e.g. a tool returned nothing, like a
    # rate-limited web search). Rather than echo the empty tool output, answer
    # directly from the model's own knowledge, using whatever results we have.
    direct = _call_groq(
        [{"role": "user", "content":
          f"{user_input}\n\nContext gathered from tools (may be empty or partial):\n{results_text}"}],
        DIRECT_ANSWER_PROMPT,
        tracker, "critique", agency_level, max_tokens=1000,
    )
    return direct.strip() or results_text or "I could not produce an answer for that request."


# ---------------------------------------------------------------------------
# L3 — Autonomous Agent (ReAct Loop + Replan)
# ---------------------------------------------------------------------------

def _run_l3(
    user_input: str,
    history: list[dict],
    tracker: TokenTracker,
    routing_log: list[dict],
) -> str:
    """
    Autonomous ReAct loop: LLM reasons, acts, observes, repeats.
    Includes replanning on critique failure (up to MAX_REPLAN_ATTEMPTS).
    """
    agency_level = "L3"
    kg_context = _inject_kg_context(user_input)

    # Accumulating context across iterations
    react_history: list[dict] = history.copy()
    react_history.append({
        "role": "user",
        "content": f"{user_input}\n\nKG context (personal, keep local):\n{kg_context}"
    })

    observations: list[str] = []
    iteration = 0

    while iteration < MAX_REACT_ITERATIONS:
        iteration += 1

        # ── Thought ──────────────────────────────────────────────────────────
        thought_response = _call_groq(
            react_history,
            REACT_THOUGHT_PROMPT,
            tracker, "react_thought", agency_level, max_tokens=1000,
        )

        thought_parsed = _extract_json(thought_response)

        # Check if LLM thinks we're done
        if thought_parsed and thought_parsed.get("done"):
            break

        # Extract next action
        if not thought_parsed or "tool" not in thought_parsed:
            # Treat as final answer if no tool call
            observations.append(f"Thought: {thought_response}")
            break

        tool_name  = thought_parsed.get("tool", "")
        tool_input = thought_parsed.get("input", "")

        # ── Route ─────────────────────────────────────────────────────────────
        routing_result = router.route(tool_input, tool_name=tool_name)
        routing_log.append({
            "step_id": iteration,
            "tool": tool_name,
            "decision": routing_result.decision.value,
            "privacy_score": routing_result.privacy_score,
            "complexity_score": routing_result.complexity_score,
            "reason": routing_result.reason,
        })

        # ── Action (parse params) ──────────────────────────────────────────────
        prefer_local = routing_result.decision == RoutingDecision.LOCAL
        params, _ = _parse_tool_params(
            tool_name, tool_input, tracker, agency_level, prefer_local
        )

        # ── Observe (execute tool) ─────────────────────────────────────────────
        result = _dispatch_tool(tool_name, params)
        observation_text = result.get(
            "formatted", result.get("output", json.dumps(result)[:600])
        )

        obs_entry = f"[Iteration {iteration}] Tool: {tool_name}\nObservation: {observation_text[:500]}"
        observations.append(obs_entry)

        # Feed observation back into context
        react_history.append({"role": "assistant", "content": thought_response})
        react_history.append({"role": "user", "content": f"Observation: {observation_text[:500]}"})

    # ── Critique + Replan ─────────────────────────────────────────────────────
    all_observations = "\n\n".join(observations)
    replan_count = 0

    while replan_count <= MAX_REPLAN_ATTEMPTS:
        critique_input = f"""Original request: {user_input}

Observations from {iteration} ReAct iteration(s):
{all_observations}

Synthesise a final answer. If the information is insufficient, state what is missing."""

        critique_response = _call_groq(
            [{"role": "user", "content": critique_input}],
            CRITIC_SYSTEM_PROMPT,
            tracker, "critique", agency_level, max_tokens=1500,
        )

        critique = _extract_json(critique_response)
        if critique:
            verdict = critique.get("verdict", "complete")
            if verdict == "complete" or replan_count >= MAX_REPLAN_ATTEMPTS:
                return critique.get("final_answer", critique_response)

            # Replan
            replan_count += 1
            replan_response = _call_groq(
                [{"role": "user", "content": f"""The previous attempt was incomplete.
Missing: {critique.get('missing_info', 'unclear')}
Original request: {user_input}
Previous observations: {all_observations[:800]}
Generate a revised plan for what to do next."""}],
                PLANNER_SYSTEM_PROMPT,
                tracker, "replan", agency_level, max_tokens=1000,
            )
            # Append replan context and do another quick observe
            all_observations += f"\n\n[Replan {replan_count}]: {replan_response[:400]}"
        else:
            return critique_response

    return "I was unable to fully complete this task after multiple attempts. Here is what I found:\n\n" + all_observations


# ---------------------------------------------------------------------------
# Main agent interface
# ---------------------------------------------------------------------------

class HybridMindAgent:
    """
    Main agent. Call run() with agency_level to select L1/L2/L3.
    Returns answer + routing_log + token_summary.
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.tracker = TokenTracker()

    def run(
        self,
        user_input: str,
        agency_level: AgencyLevel = AgencyLevel.L2_WORKFLOW,
        verbose: bool = False,
    ) -> dict:
        """
        Process user request at specified agency level.

        Returns:
            {
                "answer": str,
                "routing_log": [...],
                "token_summary": TokenSummary,
                "agency_level": str,
                "elapsed": float,
                "session_id": str,
            }
        """
        start = time.time()
        self.tracker.mark_call_start()
        memory_manager.add_user_message(self.session_id, user_input)

        history = memory_manager.get_context(self.session_id)
        routing_log: list[dict] = []

        try:
            level = agency_level.value if hasattr(agency_level, "value") else str(agency_level)

            # Trivial greetings answer locally for every level: no cloud planning,
            # no Groq tokens -- works even when the cloud daily limit is reached.
            if _is_greeting(user_input):
                answer = _call_ollama(
                    history + [{"role": "user", "content": user_input}],
                    DIRECT_ANSWER_PROMPT, self.tracker, "single_call", level,
                ).strip() or "Hello! How can I help?"
            elif level == "L1":
                answer = _run_l1(user_input, history, self.tracker, routing_log)
            elif level == "L3":
                answer = _run_l3(user_input, history, self.tracker, routing_log)
            else:
                answer = _run_l2(user_input, history, self.tracker, routing_log)

        except Exception as e:
            # User-friendly messages for the common cloud failures (SPEC 7).
            error_text = str(e)
            lowered = error_text.lower()
            if "429" in error_text or "rate limit" in lowered or "tokens per day" in lowered:
                answer = ("The cloud model (Groq free tier) has reached its rate/token limit. "
                          "It resets shortly -- wait a few minutes and retry, or use the L1 "
                          "level for lighter token usage.")
            elif "401" in error_text or "invalid api key" in lowered:
                answer = "Groq API key invalid -- update it in the Settings tab."
            else:
                answer = f"I encountered an error: {error_text[:300]}"

        elapsed = round(time.time() - start, 2)
        memory_manager.add_assistant_message(self.session_id, answer)

        return {
            "answer":        answer,
            "routing_log":   routing_log,
            "token_summary": self.tracker.get_last_call_summary(),
            "agency_level":  agency_level.value if hasattr(agency_level, "value") else str(agency_level),
            "elapsed":       elapsed,
            "session_id":    self.session_id,
        }

    def add_document(self, file_path: str) -> dict:
        from tools.rag import add_documents_to_rag
        return add_documents_to_rag([file_path])

    def reset_session(self):
        memory_manager.conversation.clear_session(self.session_id)
        self.tracker.reset()
        self.session_id = str(uuid.uuid4())[:8]
