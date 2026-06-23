"""
test_loading.py — Verify that all 10 synthetic SOW PDFs can be uploaded,
ingested, and produce a non-empty Knowledge Graph and DRL rules.
"""
from __future__ import annotations

import pytest

from conftest import SOW_FILES, SOWS_DIR, api, TIMEOUT
import httpx


@pytest.mark.parametrize("pdf_path", SOW_FILES, ids=[p.name for p in SOW_FILES])
def test_pdf_uploads_and_creates_project(pdf_path):
    """POST /projects with PDF — should return 201 with a project_id."""
    with open(pdf_path, "rb") as f:
        resp = api(
            "POST", "/projects",
            files={"sow_pdf": (pdf_path.name, f, "application/pdf")},
            data={"name": pdf_path.stem.replace("_", " ").title()},
        )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "project_id" in body
    assert len(body["project_id"]) == 36  # UUID


@pytest.mark.parametrize("pdf_path", SOW_FILES, ids=[p.name for p in SOW_FILES])
def test_kg_has_origin_nodes(pdf_path, project_for):
    """After ingestion, the Origin Plane KG must have at least 5 nodes."""
    pid = project_for(pdf_path.name)
    resp = api("GET", f"/projects/{pid}/kg", params={"plane": "origin"})
    assert resp.status_code == 200
    elements = resp.json()["elements"]
    nodes = [e for e in elements if "source" not in e["data"]]
    assert len(nodes) >= 5, f"Too few Origin nodes: {len(nodes)} — KG extraction may have failed"


@pytest.mark.parametrize("pdf_path", SOW_FILES, ids=[p.name for p in SOW_FILES])
def test_kg_has_multiple_node_types(pdf_path, project_for):
    """KG should contain at least 3 distinct node types (goal, feature, constraint, etc.)."""
    pid = project_for(pdf_path.name)
    resp = api("GET", f"/projects/{pid}/kg", params={"plane": "origin"})
    elements = resp.json()["elements"]
    types = {e["data"].get("type") for e in elements if "source" not in e["data"]}
    assert len(types) >= 3, f"Only {len(types)} node types found: {types}"


@pytest.mark.parametrize("pdf_path", SOW_FILES, ids=[p.name for p in SOW_FILES])
def test_kg_has_edges(pdf_path, project_for):
    """KG should have at least 3 edges representing relationships."""
    pid = project_for(pdf_path.name)
    resp = api("GET", f"/projects/{pid}/kg", params={"plane": "origin"})
    elements = resp.json()["elements"]
    edges = [e for e in elements if "source" in e["data"]]
    assert len(edges) >= 3, f"Too few edges: {len(edges)}"


@pytest.mark.parametrize("pdf_path", SOW_FILES, ids=[p.name for p in SOW_FILES])
def test_rules_generated(pdf_path, project_for):
    """DRL rules must be generated — at least 4 rules per document."""
    pid = project_for(pdf_path.name)
    resp = api("GET", f"/projects/{pid}/rules")
    assert resp.status_code == 200
    rules = resp.json()
    assert len(rules) >= 4, f"Too few rules generated: {len(rules)}"


@pytest.mark.parametrize("pdf_path", SOW_FILES, ids=[p.name for p in SOW_FILES])
def test_rules_have_required_fields(pdf_path, project_for):
    """Each rule must have name, when, then, and a valid violation_status."""
    pid = project_for(pdf_path.name)
    rules = api("GET", f"/projects/{pid}/rules").json()
    for rule in rules:
        assert rule.get("name"), "Rule missing name"
        assert rule.get("when"), "Rule missing when condition"
        assert rule.get("then"), "Rule missing then action"
        assert rule.get("violation_status") in ("ok", "at_risk", "violated")


@pytest.mark.parametrize("pdf_path", SOW_FILES, ids=[p.name for p in SOW_FILES])
def test_coverage_initially_zero(pdf_path, project_for):
    """Before any chat, coverage should be 0% (no goals addressed yet)."""
    pid = project_for(pdf_path.name)
    cov = api("GET", f"/projects/{pid}/kg/coverage").json()
    assert cov["total"] > 0, "No goal nodes found — KG may be malformed"
    assert cov["percentage"] == 0, f"Expected 0% coverage before chat, got {cov['percentage']}%"


def test_sows_directory_has_ten_pdfs():
    """Pre-flight: the sows/ directory must have exactly 10 PDFs."""
    pdfs = list(SOWS_DIR.glob("*.pdf"))
    assert len(pdfs) == 10, (
        f"Expected 10 PDFs, found {len(pdfs)}. "
        "Run: python tests/generate_sows.py"
    )


def test_text_only_project_creation():
    """Creating a project with plain text SoW (no PDF) must also work."""
    sow_text = (
        "Project: Widget Factory Automation. "
        "Goal: Automate widget assembly line. "
        "Feature: Robotic arm control system. "
        "Constraint: Budget $100,000. "
        "Milestone: Pilot live by December 2026."
    )
    resp = api("POST", "/projects", data={"name": "Text SoW Test", "sow_text": sow_text})
    assert resp.status_code == 201
    assert "project_id" in resp.json()


def test_missing_sow_returns_400():
    """POST /projects with neither text nor PDF must return 400."""
    resp = api("POST", "/projects", data={"name": "Empty SoW"})
    assert resp.status_code == 400
