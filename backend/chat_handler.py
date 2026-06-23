from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from config import complete
from knowledge_graph import ProjectKG
from models import (
    ChatMessage,
    KGEdge,
    KGNode,
    MessageAuthor,
    MessageMetadata,
    NodeSource,
    Rule,
)
from rules_engine import evaluate_rules

_TAG_PROMPT = """
Given this team chat message, suggest relevant tags.

Message: "{message}"

Available tags (only suggest from this list):
#decision  – team has locked a choice
#feature   – new feature proposed or discussed
#concern   – risk, question, or worry
#sprint    – sprint planning content
#architecture – architecture-level discussion
#blocker   – something blocking progress
#out-of-scope – explicit acknowledgment that a topic is out of scope

Return ONLY valid JSON with no markdown:
{{"suggested_tags": ["#tag1", "#tag2"]}}
"""

_USER_PLANE_PROMPT = """
Analyze this team chat message and extract User Plane knowledge graph entities.

Message tags: {tags}
Author: @{author}
Message: "{message}"

Origin Plane nodes (project scope):
{origin_nodes}

Extract entities from the message as User Plane nodes.
Tag hints: #decision → decision, #feature → proposed_feature, #sprint → work_item,
           #concern → concern, #architecture → discussion_topic, #blocker → blocker

For each extracted node, propose cross-plane edges to Origin Plane nodes using these relations:
- addresses: User node works toward an Origin goal
- implements: User proposed_feature elaborates an Origin feature
- violates: User decision contradicts an Origin constraint
- extends: User work_item lives under an Origin component

If a node has no traceable link to any Origin node, leave cross_edges empty for it.

Return ONLY valid JSON with no markdown:
{{
  "user_nodes": [
    {{
      "id": "unique_snake_case_id",
      "label": "Short label",
      "type": "decision|work_item|proposed_feature|concern|discussion_topic|blocker",
      "description": "One sentence description"
    }}
  ],
  "cross_edges": [
    {{
      "user_node_id": "id from user_nodes above",
      "origin_node_id": "id from origin nodes",
      "relation": "addresses|implements|violates|extends"
    }}
  ]
}}
"""

_ALIGNMENT_PROMPT = """
You are an AI project advisor. Analyze this team message for alignment with the project SoW.

Origin Plane (project scope — goals, features, constraints):
{origin_nodes}

Active rules:
{rules}

Relevant nodes for this message:
{relevant_nodes}

Author: @{author}
Tags: {tags}
Message: "{message}"

Return ONLY valid JSON with no markdown:
{{
  "alignment_score": <0-100 integer>,
  "in_scope": ["Origin node labels this message aligns with"],
  "out_of_scope": ["topics or components mentioned with no SoW basis"],
  "deviations": ["rule names or constraints this message risks violating"],
  "coverage_delta": ["Origin node IDs that this message newly addresses"],
  "recommendations": ["brief actionable recommendations (max 3)"],
  "suggested_tags": ["tags from: #decision #feature #concern #sprint #architecture #blocker #out-of-scope"],
  "response_text": "2-4 sentence natural language advisor response to the team"
}}
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        return match.group(1).strip()
    return text


def suggest_tags(message: str) -> list[str]:
    try:
        result = json.loads(_extract_json(complete(
            [{"role": "user", "content": _TAG_PROMPT.format(message=message)}],
            max_tokens=256,
        )))
        return result.get("suggested_tags", [])
    except Exception:
        return []


def process_message(
    message: str,
    author_handle: str,
    author_id: str,
    tags: list[str],
    kg: ProjectKG,
    rules: list[Rule],
    chat_history: list[ChatMessage],
) -> tuple[ChatMessage, ChatMessage, ProjectKG, list[Rule]]:
    now = datetime.now(timezone.utc).isoformat()

    origin_nodes = kg.get_origin_nodes()
    origin_nodes_text = json.dumps(
        [{"id": n["id"], "type": n["type"], "label": n["label"], "description": n.get("description", "")}
         for n in origin_nodes],
        indent=2,
    )

    relevant_nodes = kg.find_relevant_nodes(message)
    relevant_text = json.dumps(
        [{"id": n["id"], "label": n["label"], "type": n["type"]} for n in relevant_nodes],
        indent=2,
    )

    rules_text = json.dumps(
        [{"rule_id": r.rule_id, "name": r.name, "when": r.when, "then": r.then} for r in rules],
        indent=2,
    )

    tags_str = ", ".join(tags) if tags else "none"

    # Run alignment scoring and user-plane extraction in two parallel-ish calls
    # (sequential here for simplicity; can be parallelised with asyncio.gather)
    try:
        alignment = json.loads(_extract_json(complete(
            [{"role": "user", "content": _ALIGNMENT_PROMPT.format(
                origin_nodes=origin_nodes_text,
                rules=rules_text,
                relevant_nodes=relevant_text,
                author=author_handle,
                message=message,
                tags=tags_str,
            )}],
            max_tokens=2048,
        )))
    except Exception:
        alignment = {"alignment_score": 0, "in_scope": [], "out_of_scope": [],
                     "deviations": [], "coverage_delta": [], "recommendations": [],
                     "suggested_tags": [], "response_text": "Could not analyze alignment."}

    try:
        user_plane = json.loads(_extract_json(complete(
            [{"role": "user", "content": _USER_PLANE_PROMPT.format(
                tags=tags_str,
                origin_nodes=origin_nodes_text,
                author=author_handle,
                message=message,
            )}],
            max_tokens=2048,
        )))
    except Exception:
        user_plane = {"user_nodes": [], "cross_edges": []}

    # Record user message
    msg_id = str(uuid4())
    user_msg = ChatMessage(
        message_id=msg_id,
        role="user",
        author=MessageAuthor(member_id=author_id, handle=author_handle),
        content=message,
        tags=tags,
        timestamp=now,
    )

    # Populate User Plane KG
    nodes_created: list[str] = []
    for nd in user_plane.get("user_nodes", []):
        node_id = nd.get("id", "")
        if not node_id or kg.has_node(node_id):
            continue
        node = KGNode(
            id=node_id,
            label=nd.get("label", node_id),
            plane="user",
            type=nd.get("type", "discussion_topic"),
            description=nd.get("description", ""),
            source=NodeSource(type="chat_message", ref=msg_id),
        )
        kg.add_node(node)
        nodes_created.append(node_id)

    edges_created: list[str] = []
    for ed in user_plane.get("cross_edges", []):
        u_id = ed.get("user_node_id", "")
        o_id = ed.get("origin_node_id", "")
        if not (kg.has_node(u_id) and kg.has_node(o_id)):
            continue
        edge = KGEdge(source=u_id, target=o_id, plane="cross", relation=ed.get("relation", "addresses"))
        kg.add_edge(edge)
        edges_created.append(edge.id)
        if ed.get("relation") in ("addresses", "implements", "extends"):
            kg.mark_node_covered(o_id, "covered")

    # Re-evaluate rules
    rules = evaluate_rules(rules, message)

    metadata = MessageMetadata(
        alignment_score=alignment.get("alignment_score", 0),
        in_scope=alignment.get("in_scope", []),
        out_of_scope=alignment.get("out_of_scope", []),
        deviations=alignment.get("deviations", []),
        coverage_delta=alignment.get("coverage_delta", []),
        recommendations=alignment.get("recommendations", []),
        suggested_tags=alignment.get("suggested_tags", []),
        user_plane_nodes_created=nodes_created,
        cross_plane_edges_created=edges_created,
    )

    assistant_msg = ChatMessage(
        role="assistant",
        content=alignment.get("response_text", ""),
        timestamp=now,
        metadata=metadata,
    )

    return user_msg, assistant_msg, kg, rules
