from __future__ import annotations
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

load_dotenv(Path(__file__).parent.parent / ".env")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")

if not GROQ_API_KEY:
    print(
        "\n[ERROR] GROQ_API_KEY is not set.\n"
        "Add it to the .env file at the project root:\n\n"
        "  GROQ_API_KEY=your_key_here\n",
        file=sys.stderr,
    )
    sys.exit(1)

client = Groq(api_key=GROQ_API_KEY)


def complete(messages: list[dict], max_tokens: int = 4096) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return resp.choices[0].message.content
