"""
eval/judge.py
-------------
Pluggable LLM-as-judge for evaluating agent answer quality.

Backends:
  stub   — offline heuristic: catches empty answers, error prefixes,
           and suspiciously short replies. Zero API calls.
  groq   — Groq-hosted classifier using a pinned model version.
           Opt-in: requires GROQ_API_KEY in config.

Bias-correction note:
  Dev-set golden examples must NEVER appear in the judge prompt.
  The judge prompt contains only the rubric, not labelled examples,
  to avoid contamination and ensure TPR/TNR measure generalisation.

Model pin:
  JUDGE_MODEL is frozen at class level. Change it only after re-validating
  TPR/TNR on the full golden set and updating eval/golden/cases.json version.
"""

from __future__ import annotations

import json as _json

_ERROR_PREFIXES = (
    "i encountered an error",
    "the cloud model",
    "groq api key invalid",
    "rate limit",
    "error:",
)

_JUDGE_SYSTEM = (
    "You are a strict answer-quality judge for a personal AI assistant. "
    "Given a user question and the assistant's answer, reply ONLY with valid JSON "
    "on a single line: "
    '{"correct": <true|false>, "reason": "<one sentence>", "confidence": <0.0–1.0>}. '
    "Mark correct=true when: the answer is relevant to the question, non-empty, and "
    "free of error messages or hallucinated facts. "
    "Mark correct=false for: empty answers, error/rate-limit messages, off-topic "
    "replies, or clearly wrong factual claims. "
    "Do not include examples in your response — only the JSON object."
)


class Judge:
    """
    LLM-as-judge with pluggable backends.

    Usage:
        judge = Judge(backend="stub")
        result = judge.evaluate(question="...", answer="...")
        # -> {"correct": bool, "reason": str, "confidence": float, "backend": str}
    """

    JUDGE_MODEL = "llama-3.1-8b-instant"   # pinned — see docstring

    def __init__(self, backend: str = "stub") -> None:
        if backend not in ("stub", "groq"):
            raise ValueError(f"Unknown judge backend: {backend!r}. Use 'stub' or 'groq'.")
        self.backend = backend

    def evaluate(self, question: str, answer: str, context: str = "") -> dict:
        """Return a verdict dict for the (question, answer) pair."""
        if self.backend == "groq":
            return self._groq_judge(question, answer, context)
        return self._stub_judge(answer)

    # ------------------------------------------------------------------
    # Stub (offline) judge — the silent-failure detector baseline
    # ------------------------------------------------------------------

    def _stub_judge(self, answer: str) -> dict:
        a = answer.strip()

        if not a:
            return {
                "correct": False,
                "reason": "Empty answer — no content produced.",
                "confidence": 1.0,
                "backend": "stub",
            }

        a_lower = a.lower()
        for prefix in _ERROR_PREFIXES:
            if a_lower.startswith(prefix):
                return {
                    "correct": False,
                    "reason": f"Error/failure prefix detected: '{a[:60]}'",
                    "confidence": 0.95,
                    "backend": "stub",
                }

        if len(a) < 15:
            return {
                "correct": False,
                "reason": f"Answer suspiciously short ({len(a)} chars).",
                "confidence": 0.85,
                "backend": "stub",
            }

        return {
            "correct": True,
            "reason": "Answer is non-empty, no error prefix, plausible length.",
            "confidence": 0.7,
            "backend": "stub",
        }

    # ------------------------------------------------------------------
    # Groq judge (live, opt-in)
    # ------------------------------------------------------------------

    def _groq_judge(self, question: str, answer: str, context: str) -> dict:
        from config.store import get_config
        try:
            from groq import Groq
        except ImportError:
            return {**self._stub_judge(answer), "note": "groq package not installed, fell back to stub"}

        cfg = get_config()
        if not cfg.groq_api_key:
            return {**self._stub_judge(answer), "note": "GROQ_API_KEY not set, fell back to stub"}

        user_msg = f"Question: {question}\n\nAnswer: {answer}"
        if context:
            user_msg += f"\n\nContext: {context}"

        try:
            client = Groq(api_key=cfg.groq_api_key)
            resp = client.chat.completions.create(
                model=self.JUDGE_MODEL,
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=150,
                temperature=0.0,
            )
            raw = resp.choices[0].message.content.strip()
            parsed = _json.loads(raw)
            return {
                "correct": bool(parsed.get("correct", False)),
                "reason": str(parsed.get("reason", "")),
                "confidence": float(parsed.get("confidence", 0.5)),
                "backend": f"groq/{self.JUDGE_MODEL}",
            }
        except _json.JSONDecodeError as e:
            return {
                **self._stub_judge(answer),
                "note": f"Judge JSON parse error: {e}. Fell back to stub.",
            }
        except Exception as e:
            return {
                **self._stub_judge(answer),
                "note": f"Groq judge call failed: {e}. Fell back to stub.",
            }
