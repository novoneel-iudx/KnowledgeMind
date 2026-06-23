"""
extraction/commitment.py
------------------------
Few-shot local-LLM commitment extractor.

Takes a RawMessage plus spaCy NER candidates, asks the local model (Ollama,
falling back to Groq fast per SPEC 6) whether the message is a time-bound
commitment, and returns an ExtractionResult. The commitment_type is assigned
from the confidence thresholds in SPEC 4.3 -- not taken from the model verbatim
-- so the HARD/SOFT/TENTATIVE boundary is deterministic.

Error policy (SPEC 4.3 / 8): on JSON parse failure after retries, return None.
Never raise.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from config.store import get_config
from connectors.base import RawMessage
from extraction.prompts import EXTRACTION_SYSTEM_PROMPT, build_extraction_prompt
from kg.schema import CommitmentNode


# Total LLM attempts before giving up (initial + 2 retries, SPEC 4.3).
MAX_EXTRACTION_ATTEMPTS: int = 3

# Confidence thresholds (SPEC 4.3).
_HARD_THRESHOLD: float = 0.85
_SOFT_THRESHOLD: float = 0.60

# Local model generation budget for extraction (SPEC 6).
_MAX_TOKENS: int = 256

# Signature of a swappable LLM caller: (system_prompt, user_prompt) -> text.
LlmCaller = Callable[[str, str], str]


@dataclass
class ExtractionResult:
    """Outcome of extracting commitments from one message (SPEC 3.2)."""
    commitments: list[CommitmentNode]
    raw_message: str
    model_used: str         # 'spacy'|'local_llm'
    latency_ms: float


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_commitment_type(confidence: float) -> str:
    """Map a confidence score to HARD / SOFT / TENTATIVE (SPEC 4.3)."""
    if confidence >= _HARD_THRESHOLD:
        return "HARD"
    if confidence >= _SOFT_THRESHOLD:
        return "SOFT"
    return "TENTATIVE"


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> Optional[dict[str, Any]]:
    """Best-effort JSON extraction from an LLM response."""
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, AttributeError):
        pass
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


# ---------------------------------------------------------------------------
# Default LLM caller (Ollama -> Groq fast fallback)
# ---------------------------------------------------------------------------

def _default_llm_caller(system_prompt: str, user_prompt: str) -> str:
    """Call the local/SLM model; fall back to Groq fast if Ollama is unreachable."""
    cfg = get_config()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    if cfg.local_provider == "groq":
        from groq import Groq
        client = Groq(api_key=cfg.groq_api_key)
        response = client.chat.completions.create(
            model=cfg.online_slm_model,
            messages=messages,
            temperature=0.0,
            max_tokens=_MAX_TOKENS,
        )
        return response.choices[0].message.content
    try:
        from ollama import Client
        client = Client(host=cfg.ollama_base_url)
        response = client.chat(
            model=cfg.local_model,
            messages=messages,
            options={"temperature": 0.0, "num_predict": _MAX_TOKENS},
        )
        return response.message.content
    except Exception as ollama_error:  # noqa: BLE001 -- fall back, never crash
        print(f"[Extraction] WARNING: Ollama unreachable ({ollama_error}); using Groq fast.")
        from groq import Groq
        client = Groq(api_key=cfg.groq_api_key)
        response = client.chat.completions.create(
            model=cfg.cloud_model_fast,
            messages=messages,
            temperature=0.0,
            max_tokens=_MAX_TOKENS,
        )
        return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_commitments(
    raw_message: RawMessage,
    ner_candidates: list[tuple[str, str]],
    llm_caller: Optional[LlmCaller] = None,
) -> Optional[ExtractionResult]:
    """
    Extract a commitment from a single message.

    Args:
        raw_message: the inbound message.
        ner_candidates: (person, time_expression) hints from extraction.ner.
        llm_caller: override the LLM call (used for testing); defaults to the
            Ollama-with-Groq-fallback caller.
    Returns:
        ExtractionResult (possibly with an empty commitments list when the
        message is not a commitment), or None if the model never produced
        parseable JSON.
    """
    caller = llm_caller or _default_llm_caller
    prompt = build_extraction_prompt(raw_message.text, ner_candidates)

    started = time.perf_counter()
    parsed: Optional[dict[str, Any]] = None
    for _attempt in range(MAX_EXTRACTION_ATTEMPTS):
        try:
            response = caller(EXTRACTION_SYSTEM_PROMPT, prompt)
        except Exception as error:  # noqa: BLE001 -- treat as a failed attempt
            print(f"[Extraction] WARNING: LLM call failed ({error}).")
            continue
        parsed = _parse_json(response)
        if parsed is not None:
            break

    latency_ms = (time.perf_counter() - started) * 1000.0

    if parsed is None:
        return None  # parse failure after retries -> None, never crash

    if not parsed.get("is_commitment"):
        return ExtractionResult(
            commitments=[], raw_message=raw_message.text,
            model_used="local_llm", latency_ms=latency_ms,
        )

    confidence = float(parsed.get("confidence", 0.0))
    commitment_type = classify_commitment_type(confidence)

    normalized_ts = parsed.get("normalized_ts")
    # Fall back to the message timestamp when the model could not resolve an
    # absolute time -- start_ts is NOT NULL in the schema, so it must be set.
    start_ts = float(normalized_ts) if normalized_ts is not None else raw_message.timestamp

    person_name = ner_candidates[0][0] if (ner_candidates and ner_candidates[0][0]) else raw_message.sender
    commitment = CommitmentNode(
        id=0,  # assigned by the KG on insert
        person_name=person_name or "(self)",
        description=raw_message.text.strip()[:200],
        start_ts=start_ts,
        end_ts=None,
        source=raw_message.source,
        commitment_type=commitment_type,
        confidence=confidence,
        raw_text=raw_message.text,
    )
    return ExtractionResult(
        commitments=[commitment], raw_message=raw_message.text,
        model_used="local_llm", latency_ms=latency_ms,
    )


# ---------------------------------------------------------------------------
# Smoke test (uses a stub LLM caller -- no Ollama/network needed)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Threshold mapping is pure logic.
    assert classify_commitment_type(0.9) == "HARD"
    assert classify_commitment_type(0.7) == "SOFT"
    assert classify_commitment_type(0.4) == "TENTATIVE"
    print("=> confidence -> type mapping correct (HARD/SOFT/TENTATIVE)")

    message = RawMessage(
        source="slack", channel_id="C1", sender="Priya",
        text="See you at 4 today.", timestamp=1_750_000_000.0, external_id="ts-1",
    )

    def _stub_hard(_system: str, _user: str) -> str:
        return ('{"is_commitment": true, "confidence": 0.95, "time_expression": "at 4", '
                '"normalized_ts": 1750009999.0, "commitment_type": "HARD"}')

    result = extract_commitments(message, [("Priya", "at 4")], llm_caller=_stub_hard)
    assert result is not None and len(result.commitments) == 1, "expected one commitment"
    commitment = result.commitments[0]
    assert commitment.commitment_type == "HARD" and commitment.person_name == "Priya"
    assert commitment.start_ts == 1750009999.0
    print(f"=> extracted {commitment.commitment_type} commitment for {commitment.person_name} "
          f"(conf={commitment.confidence})")

    def _stub_non(_system: str, _user: str) -> str:
        return '{"is_commitment": false, "confidence": 0.02, "time_expression": "", "normalized_ts": null, "commitment_type": "TENTATIVE"}'

    non = extract_commitments(message, [], llm_caller=_stub_non)
    assert non is not None and non.commitments == [], "non-commitment should yield empty list"
    print("=> non-commitment yields empty commitments list")

    def _stub_garbage(_system: str, _user: str) -> str:
        return "sorry, I cannot do that"

    failed = extract_commitments(message, [], llm_caller=_stub_garbage)
    assert failed is None, "unparseable response should yield None"
    print("=> unparseable LLM output -> None (no crash)")

    # normalized_ts null falls back to the message timestamp.
    def _stub_no_ts(_system: str, _user: str) -> str:
        return '{"is_commitment": true, "confidence": 0.7, "time_expression": "soon", "normalized_ts": null, "commitment_type": "SOFT"}'

    soft = extract_commitments(message, [], llm_caller=_stub_no_ts)
    assert soft.commitments[0].start_ts == message.timestamp, "null ts should fall back to message ts"
    print("=> null normalized_ts falls back to message timestamp")

    print("All extraction/commitment.py smoke tests passed.")
