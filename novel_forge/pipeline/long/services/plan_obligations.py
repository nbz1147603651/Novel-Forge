"""Mechanical verification for literal contracts declared by Planning LLM.

Semantic judgment remains model-owned. Runtime code neither scans quotes nor
infers which prose is rigid; it only transports explicit declarations and
checks their exact presence after generation or repair.
"""

from __future__ import annotations

from typing import Any

from novel_forge.core.utils.field_extractor import field
from novel_forge.core.utils.text_hash import source_text_hash


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _literal_present(literal: str, text: str) -> bool:
    compact_literal = "".join(str(literal or "").split())
    compact_text = "".join(str(text or "").split())
    return bool(compact_literal and compact_literal in compact_text)


def extract_plan_required_literals(plan: Any) -> list[dict[str, str]]:
    """Project literals explicitly declared by the Planning model.

    This function intentionally performs no quote scanning or narrative
    inference.  Planning owns the decision that wording is semantically rigid;
    runtime code only transports and mechanically verifies that declaration.
    """

    obligations: list[dict[str, str]] = []
    seen: set[str] = set()
    for contract in list(field(plan, "required_literals", []) or []):
        literal = _text(field(contract, "literal", ""))
        if not literal or literal in seen:
            continue
        seen.add(literal)
        semantics = field(contract, "requirement", {}) or {}
        obligations.append(
            {
                "contract_id": _text(field(contract, "contract_id", "")),
                "scene_id": _text(field(contract, "scene_id", "")),
                "literal": literal,
                "reason": _text(field(contract, "reason", "")),
                "placement_hint": _text(field(contract, "placement_hint", "")),
                "source_field": "required_literals",
                "source_text": _text(field(contract, "reason", "")),
                "source": _text(field(semantics, "source", "planning")),
            }
        )
    return obligations


def assess_plan_literal_coverage(plan: Any, text: str) -> dict[str, Any]:
    """Return objective literal coverage for *text* against *plan*."""

    required = extract_plan_required_literals(plan)
    missing = [item for item in required if not _literal_present(item["literal"], text)]
    return {
        "source_text_hash": source_text_hash(text),
        "fulfillment": [
            {
                "requirement_id": item["contract_id"],
                "source": item["source"],
                "scope": item["scene_id"],
                "satisfaction": "literal",
                "status": "pending" if item in missing else "fulfilled",
                "evidence": "" if item in missing else item["literal"],
            }
            for item in required
        ],
        "required_literal_count": len(required),
        "covered_literal_count": len(required) - len(missing),
        "missing_required_literal_count": len(missing),
        "missing_required_literals": missing,
    }


__all__ = [
    "assess_plan_literal_coverage",
    "extract_plan_required_literals",
]
