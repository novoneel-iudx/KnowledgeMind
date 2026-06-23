"""
pm_config.py — Credential resolution and LLM client for projmgmt.

Named pm_config (not config) to avoid colliding with KnowledgeMind's
config/ package when projmgmt is mounted as a sub-application inside KM.

Priority order for GROQ_API_KEY:
  1. projmgmt/.env                          (own override — checked first)
  2. ../KnowledgeMind/.env                  (sibling KM repo, if present)
  3. ~/.config/KnowledgeMind/config.json    (KM's installed app config)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

_HERE = Path(__file__).parent        # backend/
_ROOT = _HERE.parent                 # projmgmt/
_KM_REPO = _ROOT.parent              # KnowledgeMind/ (projmgmt now lives inside KM)


def _km_app_config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "KnowledgeMind" / "config.json"


def _resolve(key: str) -> str:
    val = os.environ.get(key, "")
    if val:
        return val

    km_env = _KM_REPO / ".env"
    if km_env.exists():
        load_dotenv(km_env, override=False)
        val = os.environ.get(key, "")
        if val:
            return val

    cfg_path = _km_app_config_path()
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text())
            val = cfg.get(key.lower(), "")
            if val:
                return val
        except (json.JSONDecodeError, OSError):
            pass

    return ""


load_dotenv(_ROOT / ".env")

GROQ_API_KEY = _resolve("GROQ_API_KEY")
MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")

if not GROQ_API_KEY:
    _msg = (
        "\n[projmgmt] GROQ_API_KEY not found.\n"
        "Set it in one of:\n"
        f"  {_ROOT / '.env'}                    (projmgmt override)\n"
        f"  {_KM_REPO / '.env'}  (KnowledgeMind repo)\n"
        f"  {_km_app_config_path()}  (KM app config)\n"
    )
    raise ImportError(_msg)

client = Groq(api_key=GROQ_API_KEY)


def complete(messages: list[dict], max_tokens: int = 4096) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return resp.choices[0].message.content
