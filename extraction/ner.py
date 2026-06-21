"""
extraction/ner.py
-----------------
spaCy NER pass that surfaces candidate (person, time-expression) pairs from a
message. These are hints for the commitment extractor -- the LLM makes the final
call; NER just narrows what to look at (SPEC 4.3).

The spaCy model loads lazily and is cached. If en_core_web_sm is not installed,
extraction degrades to returning no candidates (with a one-time warning) rather
than crashing -- the LLM extractor still works on the raw text.
"""

from __future__ import annotations

import threading
from typing import Optional

import spacy
from spacy.language import Language


_MODEL_NAME: str = "en_core_web_sm"

# spaCy entity labels we care about.
_PERSON_LABELS: frozenset[str] = frozenset({"PERSON"})
_TIME_LABELS: frozenset[str] = frozenset({"DATE", "TIME", "EVENT"})


class _NerPipeline:
    """Lazy, thread-safe holder for the spaCy pipeline."""

    def __init__(self) -> None:
        self._nlp: Optional[Language] = None
        self._loaded: bool = False
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> Optional[Language]:
        if self._loaded:
            return self._nlp
        with self._lock:
            if self._loaded:
                return self._nlp
            try:
                self._nlp = spacy.load(_MODEL_NAME)
            except OSError as error:
                print(
                    f"[NER] WARNING: spaCy model '{_MODEL_NAME}' not available "
                    f"({error}). Run: python -m spacy download {_MODEL_NAME}"
                )
                self._nlp = None
            self._loaded = True
        return self._nlp

    def extract(self, text: str) -> list[tuple[str, str]]:
        """Return (person, time_expression) candidate pairs for `text`."""
        nlp = self._ensure_loaded()
        if nlp is None or not text.strip():
            return []

        document = nlp(text)
        persons = [ent.text for ent in document.ents if ent.label_ in _PERSON_LABELS]
        time_expressions = [ent.text for ent in document.ents if ent.label_ in _TIME_LABELS]

        # A commitment needs a time anchor: with no time expression there is
        # nothing to schedule, so yield no candidates.
        if not time_expressions:
            return []

        # Pair each time expression with the first detected person (or "" when
        # the message has no explicit person -> treated as a self-commitment).
        default_person = persons[0] if persons else ""
        return [(default_person, time_expr) for time_expr in time_expressions]


_pipeline = _NerPipeline()


def extract_entities(text: str) -> list[tuple[str, str]]:
    """Module-level entry point: (person, time_expression) candidates for `text`."""
    return _pipeline.extract(text)


def is_model_available() -> bool:
    """True if the spaCy model loaded successfully."""
    return _pipeline._ensure_loaded() is not None


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not is_model_available():
        # Graceful path: model missing -> empty candidates, still a clean pass.
        assert extract_entities("Lunch with Priya tomorrow at 4pm") == []
        print("=> spaCy model unavailable; degraded to empty candidates (run "
              "`python -m spacy download en_core_web_sm` to enable NER)")
    else:
        # Contract test: a message with a time anchor yields pairs, each
        # carrying a time_expression. We do not assert specific entity strings
        # -- en_core_web_sm is the small model and its labels are approximate.
        pairs = extract_entities("Lunch with Priya tomorrow at 4pm")
        assert pairs, "expected NER candidates for a message with a time anchor"
        assert all(time_expr for _person, time_expr in pairs), "every pair needs a time expression"
        times = {time_expr for _, time_expr in pairs}
        print(f"=> {len(pairs)} candidate pair(s); time_expressions={times}")

        # A message with no temporal expression yields no candidates. (A
        # greeting like "how are you today?" would still surface "today" as a
        # DATE -- that is correct; the LLM is what rules it a non-commitment.)
        assert extract_entities("how are you doing?") == []
        print("=> message with no time expression yields no candidates")

    print("All extraction/ner.py smoke tests passed.")
