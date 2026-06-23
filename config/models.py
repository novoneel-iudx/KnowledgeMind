"""
config/models.py
----------------
Discovers available Ollama models by querying the local Ollama API.
Returns a list of model name strings for display in the setup UI.
"""

from __future__ import annotations

import urllib.request
import urllib.error
import json
from typing import Optional


def list_ollama_models(base_url: str = "http://localhost:11434") -> tuple[list[str], str | None]:
    """
    Query Ollama for available models.

    Returns:
        (model_names, error_message)
        model_names is [] if Ollama is unreachable.
        error_message is None on success, a string on failure.
    """
    try:
        url = f"{base_url.rstrip('/')}/api/tags"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        models = data.get("models", [])
        names = [m["name"] for m in models if "name" in m]

        if not names:
            return [], "Ollama is running but no models are installed. Run: ollama pull qwen2.5:3b"

        return sorted(names), None

    except urllib.error.URLError:
        return [], (
            "Ollama not detected at " + base_url + ". "
            "Please start Ollama first: https://ollama.com/download"
        )
    except Exception as e:
        return [], f"Unexpected error querying Ollama: {e}"


def is_ollama_running(base_url: str = "http://localhost:11434") -> bool:
    """Quick check — True if Ollama is reachable."""
    models, error = list_ollama_models(base_url)
    return error is None or "no models" in (error or "").lower()


def get_recommended_models() -> list[str]:
    """
    Return a curated list of models recommended for KnowledgeMind,
    in order of preference. Used to highlight suggestions in the UI.
    """
    return [
        "qwen2.5:3b",       # primary recommendation — best tool-call reliability at 3B
        "phi4-mini:latest", # Microsoft, good instruction following
        "llama3.2:3b",      # Meta, solid baseline
        "qwen2.5:7b",       # better reasoning if RAM allows
        "mistral:7b",       # good general purpose
    ]


def list_online_slm_models() -> list[str]:
    """
    Curated list of Groq-hosted SLMs usable as the local/fast tier.
    Ordered by latency (fastest first).
    """
    return [
        "llama-3.1-8b-instant",  # primary — lowest latency on Groq free tier
        "llama-3.2-3b-preview",  # smaller, even faster
        "llama-3.2-1b-preview",  # minimal footprint
        "gemma2-9b-it",          # Google, strong instruction following
    ]
