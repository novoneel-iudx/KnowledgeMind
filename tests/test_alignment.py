"""
test_alignment.py — Run simulated conversations against each project and
assert alignment scores, deviation flags, and out-of-scope detection
are consistent with the expected behaviour defined in conversations.py.
"""
from __future__ import annotations

import pytest

from conftest import project_for, send_chat, SOW_FILES
from conversations import CONVERSATIONS


# ── helpers ───────────────────────────────────────────────────────────────────

def _chat_meta(project_id: str, msg: dict) -> dict:
    result = send_chat(project_id, msg["content"], msg.get("tags", []))
    return result["assistant_message"]["metadata"]


# ── build parametrize lists ──────────────────────────────────────────────────

_aligned_cases = []
_misaligned_cases = []
_edge_cases = []

for _pdf_name, _conv in CONVERSATIONS.items():
    for _msg in _conv.get("aligned", []):
        _aligned_cases.append((_pdf_name, _msg))
    for _msg in _conv.get("misaligned", []):
        _misaligned_cases.append((_pdf_name, _msg))
    for _msg in _conv.get("edge", []):
        _edge_cases.append((_pdf_name, _msg))


def _idfn(val):
    if isinstance(val, dict):
        return val["content"][:60].replace("\n", " ")
    return str(val)


# ── aligned tests ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pdf_name,msg", _aligned_cases, ids=[_idfn(m) for _, m in _aligned_cases])
def test_aligned_message_scores_high(pdf_name, msg, project_for):
    """Aligned messages must have alignment_score >= expected minimum (default 60)."""
    pid = project_for(pdf_name)
    meta = _chat_meta(pid, msg)
    min_score = msg.get("expect_score_min", 60)
    assert meta["alignment_score"] >= min_score, (
        f"[{pdf_name}] Expected score >= {min_score}, got {meta['alignment_score']}.\n"
        f"Message: {msg['content'][:100]}\n"
        f"In-scope: {meta['in_scope']}\n"
        f"Deviations: {meta['deviations']}"
    )


@pytest.mark.parametrize("pdf_name,msg", _aligned_cases, ids=[_idfn(m) for _, m in _aligned_cases])
def test_aligned_message_no_out_of_scope(pdf_name, msg, project_for):
    """Aligned messages should not be flagged as out of scope."""
    if not msg.get("expect_out_of_scope") is False:
        pytest.skip("No out_of_scope assertion for this message")
    pid = project_for(pdf_name)
    meta = _chat_meta(pid, msg)
    assert len(meta["out_of_scope"]) == 0, (
        f"[{pdf_name}] Expected no out-of-scope items, got: {meta['out_of_scope']}"
    )


@pytest.mark.parametrize("pdf_name,msg", _aligned_cases, ids=[_idfn(m) for _, m in _aligned_cases])
def test_aligned_message_updates_coverage(pdf_name, msg, project_for):
    """Sending an aligned message should increase or maintain goal coverage."""
    pid = project_for(pdf_name)
    cov_before = _get_coverage(pid)
    _chat_meta(pid, msg)
    cov_after = _get_coverage(pid)
    assert cov_after >= cov_before, (
        f"[{pdf_name}] Coverage decreased from {cov_before}% to {cov_after}% after aligned message"
    )


# ── misaligned tests ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("pdf_name,msg", _misaligned_cases, ids=[_idfn(m) for _, m in _misaligned_cases])
def test_misaligned_message_scores_low(pdf_name, msg, project_for):
    """Misaligned messages must have alignment_score <= expected maximum (default 40)."""
    pid = project_for(pdf_name)
    meta = _chat_meta(pid, msg)
    max_score = msg.get("expect_score_max", 40)
    assert meta["alignment_score"] <= max_score, (
        f"[{pdf_name}] Expected score <= {max_score}, got {meta['alignment_score']}.\n"
        f"Message: {msg['content'][:100]}\n"
        f"In-scope: {meta['in_scope']}\n"
        f"Out-of-scope: {meta['out_of_scope']}"
    )


@pytest.mark.parametrize("pdf_name,msg", _misaligned_cases, ids=[_idfn(m) for _, m in _misaligned_cases])
def test_misaligned_message_flags_out_of_scope(pdf_name, msg, project_for):
    """Messages marked expect_out_of_scope must have non-empty out_of_scope list."""
    if not msg.get("expect_out_of_scope"):
        pytest.skip("No out_of_scope assertion for this message")
    pid = project_for(pdf_name)
    meta = _chat_meta(pid, msg)
    assert len(meta["out_of_scope"]) > 0, (
        f"[{pdf_name}] Expected out-of-scope items but got none.\n"
        f"Message: {msg['content'][:100]}"
    )


@pytest.mark.parametrize("pdf_name,msg", _misaligned_cases, ids=[_idfn(m) for _, m in _misaligned_cases])
def test_misaligned_message_flags_deviations(pdf_name, msg, project_for):
    """Messages marked expect_deviations must trigger at least one deviation flag."""
    if not msg.get("expect_deviations"):
        pytest.skip("No deviation assertion for this message")
    pid = project_for(pdf_name)
    meta = _chat_meta(pid, msg)
    assert len(meta["deviations"]) > 0, (
        f"[{pdf_name}] Expected deviation flags but got none.\n"
        f"Message: {msg['content'][:100]}\n"
        f"Score: {meta['alignment_score']}"
    )


@pytest.mark.parametrize("pdf_name,msg", _misaligned_cases, ids=[_idfn(m) for _, m in _misaligned_cases])
def test_misaligned_message_has_recommendations(pdf_name, msg, project_for):
    """Misaligned messages should generate at least one recommendation."""
    pid = project_for(pdf_name)
    meta = _chat_meta(pid, msg)
    assert len(meta["recommendations"]) > 0, (
        f"[{pdf_name}] Expected recommendations for misaligned message, got none."
    )


# ── edge case tests ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("pdf_name,msg", _edge_cases, ids=[_idfn(m) for _, m in _edge_cases])
def test_edge_case_produces_response(pdf_name, msg, project_for):
    """Edge cases should always produce a non-empty advisor response."""
    pid = project_for(pdf_name)
    result = send_chat(pid, msg["content"], msg.get("tags", []))
    content = result["assistant_message"]["content"]
    assert len(content) > 20, (
        f"[{pdf_name}] Edge case produced empty or trivial response.\n"
        f"Note: {msg.get('note', '')}"
    )


@pytest.mark.parametrize("pdf_name,msg", _edge_cases, ids=[_idfn(m) for _, m in _edge_cases])
def test_edge_case_has_concern_tags_in_suggested(pdf_name, msg, project_for):
    """Edge cases tagged #concern or #blocker should get relevant tag suggestions."""
    if "#concern" not in msg.get("tags", []) and "#blocker" not in msg.get("tags", []):
        pytest.skip("Not a concern/blocker edge case")
    pid = project_for(pdf_name)
    meta = _chat_meta(pid, msg)
    all_tags = meta.get("suggested_tags", [])
    concern_related = [t for t in all_tags if t in ("#concern", "#blocker", "#decision", "#out-of-scope")]
    assert len(concern_related) > 0, (
        f"[{pdf_name}] Expected concern-related tag suggestions, got: {all_tags}"
    )


# ── cross-cutting tests ───────────────────────────────────────────────────────

@pytest.mark.parametrize("pdf_name", list(CONVERSATIONS.keys()))
def test_user_plane_nodes_created_after_chat(pdf_name, project_for):
    """After any chat message, at least one User Plane node should be created."""
    pid = project_for(pdf_name)
    conv = CONVERSATIONS[pdf_name]
    first_msg = (conv.get("aligned") or conv.get("misaligned") or [])[0]
    meta = _chat_meta(pid, first_msg)
    assert len(meta["user_plane_nodes_created"]) > 0, (
        f"[{pdf_name}] No User Plane nodes created after first chat message"
    )


@pytest.mark.parametrize("pdf_name", list(CONVERSATIONS.keys()))
def test_coverage_increases_after_aligned_conversation(pdf_name, project_for):
    """After sending all aligned messages for a document, coverage must be > 0%."""
    pid = project_for(pdf_name)
    for msg in CONVERSATIONS[pdf_name].get("aligned", []):
        send_chat(pid, msg["content"], msg.get("tags", []))
    cov = _get_coverage(pid)
    assert cov > 0, f"[{pdf_name}] Coverage still 0% after all aligned messages"


@pytest.mark.parametrize("pdf_name", list(CONVERSATIONS.keys()))
def test_rule_violations_after_misaligned_conversation(pdf_name, project_for):
    """After misaligned messages, at least one rule should be at_risk or violated."""
    pid = project_for(pdf_name)
    for msg in CONVERSATIONS[pdf_name].get("misaligned", []):
        send_chat(pid, msg["content"], msg.get("tags", []))
    rules = _get_rules(pid)
    flagged = [r for r in rules if r["violation_status"] in ("at_risk", "violated")]
    assert len(flagged) > 0, (
        f"[{pdf_name}] No rules flagged after all misaligned messages.\n"
        f"Rules: {[(r['name'], r['violation_status']) for r in rules]}"
    )


# ── utilities ─────────────────────────────────────────────────────────────────

def _get_coverage(pid: str) -> int:
    from conftest import api
    return api("GET", f"/projects/{pid}/kg/coverage").json()["percentage"]


def _get_rules(pid: str) -> list[dict]:
    from conftest import api
    return api("GET", f"/projects/{pid}/rules").json()
