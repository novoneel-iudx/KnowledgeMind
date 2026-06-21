"""
extraction/prompts.py
---------------------
Few-shot prompt for the local-LLM commitment extractor.

The model receives the system prompt + labelled examples, then a single new
message, and must reply with JSON only. The example set (>= 15, SPEC 4.3) spans:
  - direct time      ("at 4")
  - relative time    ("tomorrow", "EOD", "next week")
  - hedged           ("maybe", "I think")
  - social           ("see you then")
  - non-commitments  ("how are you", "lol")

These are strings only -- no logic lives here (kept separate so prompts can be
iterated without touching commitment.py).
"""

from __future__ import annotations


# Required JSON output schema, restated for the model on every call.
EXTRACTION_SYSTEM_PROMPT: str = """You are the Commitment Extractor for KnowledgeMind.

You read a single chat/email/calendar message and decide whether it contains a
time-bound commitment (a meeting, call, deadline, or plan tied to a time).

Respond with JSON ONLY, no prose, in exactly this shape:
{
  "is_commitment": true | false,
  "confidence": 0.0 to 1.0,
  "time_expression": "the exact words describing the time, or empty string",
  "normalized_ts": <epoch seconds as a number> | null,
  "commitment_type": "HARD" | "SOFT" | "TENTATIVE"
}

Guidance:
- HARD  (confidence >= 0.85): explicit, firm time ("3pm Tuesday", "standup at 10").
- SOFT  (0.60 - 0.85): a real plan with a softer time ("see you at 4", "lunch tomorrow").
- TENTATIVE (< 0.60): vague or hedged ("maybe next week", "sometime soon").
- Greetings, reactions, and chit-chat are NOT commitments (is_commitment=false).
- If you cannot resolve an absolute time, set normalized_ts to null.
Output ONLY the JSON object.
"""


# Labelled few-shot examples (message -> expected JSON). 16 examples.
FEW_SHOT_EXAMPLES: list[tuple[str, str]] = [
    # --- direct time -------------------------------------------------------
    ("Let's meet at 3pm Tuesday in room 4.",
     '{"is_commitment": true, "confidence": 0.95, "time_expression": "3pm Tuesday", "normalized_ts": null, "commitment_type": "HARD"}'),
    ("Standup is at 10 tomorrow, please be on time.",
     '{"is_commitment": true, "confidence": 0.9, "time_expression": "10 tomorrow", "normalized_ts": null, "commitment_type": "HARD"}'),
    ("Call me at 17:30 sharp.",
     '{"is_commitment": true, "confidence": 0.88, "time_expression": "17:30", "normalized_ts": null, "commitment_type": "HARD"}'),
    # --- relative time -----------------------------------------------------
    ("Can we sync tomorrow morning about the report?",
     '{"is_commitment": true, "confidence": 0.78, "time_expression": "tomorrow morning", "normalized_ts": null, "commitment_type": "SOFT"}'),
    ("I'll send the deck by EOD.",
     '{"is_commitment": true, "confidence": 0.82, "time_expression": "EOD", "normalized_ts": null, "commitment_type": "SOFT"}'),
    ("Let's grab lunch next week sometime.",
     '{"is_commitment": true, "confidence": 0.55, "time_expression": "next week", "normalized_ts": null, "commitment_type": "TENTATIVE"}'),
    ("Review is due end of the month.",
     '{"is_commitment": true, "confidence": 0.7, "time_expression": "end of the month", "normalized_ts": null, "commitment_type": "SOFT"}'),
    # --- hedged ------------------------------------------------------------
    ("Maybe we could meet Thursday? Not sure yet.",
     '{"is_commitment": true, "confidence": 0.45, "time_expression": "Thursday", "normalized_ts": null, "commitment_type": "TENTATIVE"}'),
    ("I think I can join the 2pm call.",
     '{"is_commitment": true, "confidence": 0.6, "time_expression": "2pm", "normalized_ts": null, "commitment_type": "SOFT"}'),
    ("We should probably catch up soon.",
     '{"is_commitment": false, "confidence": 0.3, "time_expression": "soon", "normalized_ts": null, "commitment_type": "TENTATIVE"}'),
    # --- social ------------------------------------------------------------
    ("See you at 4!",
     '{"is_commitment": true, "confidence": 0.75, "time_expression": "at 4", "normalized_ts": null, "commitment_type": "SOFT"}'),
    ("See you then.",
     '{"is_commitment": false, "confidence": 0.25, "time_expression": "then", "normalized_ts": null, "commitment_type": "TENTATIVE"}'),
    ("Catch you later, have a good one.",
     '{"is_commitment": false, "confidence": 0.1, "time_expression": "", "normalized_ts": null, "commitment_type": "TENTATIVE"}'),
    # --- non-commitments ---------------------------------------------------
    ("how are you doing today?",
     '{"is_commitment": false, "confidence": 0.02, "time_expression": "", "normalized_ts": null, "commitment_type": "TENTATIVE"}'),
    ("lol that meme was great",
     '{"is_commitment": false, "confidence": 0.01, "time_expression": "", "normalized_ts": null, "commitment_type": "TENTATIVE"}'),
    ("Thanks for the update, looks good!",
     '{"is_commitment": false, "confidence": 0.02, "time_expression": "", "normalized_ts": null, "commitment_type": "TENTATIVE"}'),
]


def build_extraction_prompt(message_text: str, ner_candidates: list[tuple[str, str]]) -> str:
    """
    Assemble the user-side prompt: few-shot examples + NER hints + the new
    message to classify.

    Args:
        message_text: the message to extract a commitment from.
        ner_candidates: (person, time_expression) hints from spaCy NER.
    Returns:
        The full user prompt string for the LLM.
    """
    example_block = "\n".join(
        f"Message: {message}\nJSON: {label}"
        for message, label in FEW_SHOT_EXAMPLES
    )

    if ner_candidates:
        hint_lines = ", ".join(
            f"person='{person or '?'}' time='{time_expr}'"
            for person, time_expr in ner_candidates
        )
        hints = f"\nNER hints (candidates, may be noisy): {hint_lines}"
    else:
        hints = ""

    return (
        f"{example_block}\n\n"
        f"Now classify this message.{hints}\n"
        f"Message: {message_text}\nJSON:"
    )
