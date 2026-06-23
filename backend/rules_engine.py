from __future__ import annotations
import json
import re

from config import complete
from models import Rule

_EVAL_PROMPT = """
Evaluate each business rule against the team chat message below.

Rules (JSON):
{rules}

Message: "{message}"

For each rule_id, determine if the message triggers the rule's `when` condition.
Return ONLY valid JSON with no markdown:
{{
  "evaluations": [
    {{
      "rule_id": "string",
      "status": "ok|at_risk|violated"
    }}
  ]
}}
"""


def _extract_json(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        return match.group(1).strip()
    return text


def evaluate_rules(rules: list[Rule], message: str) -> list[Rule]:
    if not rules:
        return rules

    rules_payload = json.dumps(
        [{"rule_id": r.rule_id, "name": r.name, "when": r.when, "then": r.then} for r in rules],
        indent=2,
    )
    try:
        result = json.loads(_extract_json(complete(
            [{"role": "user", "content": _EVAL_PROMPT.format(rules=rules_payload, message=message)}],
            max_tokens=2048,
        )))
        ev_map = {e["rule_id"]: e["status"] for e in result.get("evaluations", [])}
    except Exception:
        return rules

    for rule in rules:
        if rule.rule_id in ev_map:
            rule.violation_status = ev_map[rule.rule_id]

    return rules
