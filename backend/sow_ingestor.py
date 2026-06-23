from __future__ import annotations
import io
import json
import re

from pypdf import PdfReader

from config import complete
from knowledge_graph import ProjectKG
from models import KGEdge, KGNode, NodeSource, Rule

_EXTRACT_PROMPT = """
You are extracting structured data from a Statement of Work (SoW) to build a knowledge graph.

Extract entities of these types:
- goal: What success looks like for this project
- feature: Specific deliverables or capabilities
- component: Technical subsystems or modules
- constraint: Budget, timeline, tech, or regulatory limits
- actor: Stakeholders or user roles
- milestone: Key project milestones or deadlines

Also extract relationships between entities:
- depends_on: A depends on B to function
- implements: A implements or realizes B
- constrains: A limits or restricts B
- owned_by: A is owned/responsible-by actor B
- delivers: Actor A delivers feature/component B

Return ONLY valid JSON with no markdown, no explanation:
{{
  "nodes": [
    {{
      "id": "unique_snake_case_id",
      "label": "Short label (3-6 words)",
      "type": "goal|feature|component|constraint|actor|milestone",
      "description": "One sentence description",
      "sow_excerpt": "Exact short quote from the SoW (max 100 chars)"
    }}
  ],
  "edges": [
    {{
      "source": "node_id",
      "target": "node_id",
      "relation": "depends_on|implements|constrains|owned_by|delivers"
    }}
  ]
}}

SoW:
{sow_text}
"""

_RULES_PROMPT = """
You are generating business process rules from a Statement of Work (SoW) and its extracted knowledge graph.

Each rule captures a scope boundary, constraint, or alignment criterion that can be checked against future team discussions.

Return ONLY valid JSON array with no markdown:
[
  {{
    "name": "Rule name (short)",
    "salience": 10,
    "when": "Natural language condition — what triggers this rule (max 1 sentence)",
    "then": "Natural language action — what should be flagged or recommended (max 1 sentence)",
    "sow_excerpt": "Exact short quote from SoW that motivates this rule (max 100 chars)"
  }}
]

Generate 6-10 meaningful rules covering: scope, tech constraints, actor responsibilities, timeline, and quality.

SoW:
{sow_text}

Knowledge graph nodes:
{kg_nodes}
"""


def extract_pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _extract_json(text: str) -> str:
    text = text.strip()
    # Strip markdown code fences
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        return match.group(1).strip()
    return text


def ingest_sow(sow_text: str) -> tuple[ProjectKG, list[Rule]]:
    kg = ProjectKG()

    # --- Extract entities ---
    extracted = json.loads(_extract_json(complete(
        [{"role": "user", "content": _EXTRACT_PROMPT.format(sow_text=sow_text)}]
    )))

    for nd in extracted.get("nodes", []):
        node = KGNode(
            id=nd["id"],
            label=nd["label"],
            plane="origin",
            type=nd["type"],
            description=nd.get("description", ""),
            source=NodeSource(type="sow", ref=nd.get("sow_excerpt", "")),
        )
        kg.add_node(node)

    for ed in extracted.get("edges", []):
        if kg.has_node(ed["source"]) and kg.has_node(ed["target"]):
            edge = KGEdge(
                source=ed["source"],
                target=ed["target"],
                plane="origin",
                relation=ed["relation"],
            )
            kg.add_edge(edge)

    # --- Generate DRL rules ---
    kg_summary = json.dumps(
        [{"id": nid, "type": d.get("type"), "label": d.get("label")}
         for nid, d in kg.graph.nodes(data=True)],
        indent=2,
    )
    rules_data = json.loads(_extract_json(complete(
        [{"role": "user", "content": _RULES_PROMPT.format(sow_text=sow_text, kg_nodes=kg_summary)}]
    )))
    rules = [
        Rule(
            name=r["name"],
            salience=r.get("salience", 10),
            when=r["when"],
            then=r["then"],
            sow_excerpt=r.get("sow_excerpt", ""),
        )
        for r in rules_data
    ]

    return kg, rules
