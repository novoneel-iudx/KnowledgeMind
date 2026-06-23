"""
Pytest configuration and shared fixtures.

Tests run against a live server. Set BASE_URL env var to override default.
The server must be running with a valid GROQ_API_KEY in .env.
"""
from __future__ import annotations
import os
import time
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
SOWS_DIR = Path(__file__).parent / "sows"
TIMEOUT = httpx.Timeout(180.0)   # LLM calls can take a while

SOW_FILES = sorted(SOWS_DIR.glob("*.pdf"))


def api(method: str, path: str, **kwargs) -> httpx.Response:
    url = BASE_URL + path
    kwargs.setdefault("timeout", TIMEOUT)
    resp = httpx.request(method, url, **kwargs)
    return resp


@pytest.fixture(scope="session", autouse=True)
def check_server():
    """Fail fast if the server is not reachable."""
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=5)
        assert r.status_code == 200, f"Health check failed: {r.text}"
    except Exception as exc:
        pytest.exit(
            f"Cannot reach server at {BASE_URL}. Start it with:\n"
            f"  cd backend && uvicorn main:app --reload\n\nError: {exc}",
            returncode=1,
        )


@pytest.fixture(scope="module")
def project_for(request):
    """
    Module-scoped fixture factory.  Each test module that declares
      pytestmark = pytest.mark.usefixtures("project_for")
    can call project_for(pdf_filename) to get a project_id.
    Projects are created once per module run and cached.
    """
    _cache: dict[str, str] = {}

    def _get(pdf_filename: str) -> str:
        if pdf_filename in _cache:
            return _cache[pdf_filename]
        pdf_path = SOWS_DIR / pdf_filename
        assert pdf_path.exists(), f"PDF not found: {pdf_path}. Run: python tests/generate_sows.py"
        with open(pdf_path, "rb") as f:
            resp = api(
                "POST", "/projects",
                files={"sow_pdf": (pdf_filename, f, "application/pdf")},
                data={"name": pdf_filename.replace(".pdf", "").replace("_", " ").title()},
            )
        assert resp.status_code == 201, f"Project creation failed ({resp.status_code}): {resp.text}"
        project_id = resp.json()["project_id"]
        _cache[pdf_filename] = project_id
        return project_id

    return _get


def send_chat(project_id: str, content: str, tags: list[str] | None = None) -> dict:
    resp = api(
        "POST", f"/projects/{project_id}/chat",
        json={
            "author_handle": "test_runner",
            "author_id": "test_runner",
            "content": content,
            "tags": tags or [],
        },
    )
    assert resp.status_code == 200, f"Chat failed ({resp.status_code}): {resp.text}"
    return resp.json()
