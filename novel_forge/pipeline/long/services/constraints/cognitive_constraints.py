"""Lossless structural projection for chapter cognitive constraints.

The projection is shared by planning, prose generation, repair, audit, and
state-write paths.  It does not rank, summarize, truncate, or reinterpret
LLM-authored narrative meaning.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from novel_forge.core.schemas.cognition import normalize_character_knowledge_coverage

_SEMANTIC_FIELDS = (
    "constraint_id",
    "claim_id",
    "claim_text",
    "cognitive_subjects",
    "cognitive_object",
    "cognitive_level",
    "action_level",
    "reader_awareness",
    "character_knowledge_coverage",
    "cognitive_chapter",
    "public_reveal_chapter",
    "foreshadow_chapters",
)


def project_cognitive_constraints(value: Any) -> list[dict[str, Any]]:
    """Return complete, ordered prompt/runtime records with one wire shape.

    Exact duplicate records are removed because they carry no additional
    information.  Records that share an id but differ semantically are both
    retained so local code never chooses which LLM-authored fact is true.
    """

    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    projected: list[dict[str, Any]] = []
    seen_exact: set[str] = set()
    for raw in values:
        item = _as_mapping(raw)
        if not item:
            continue
        record = _project_record(item)
        fingerprint = json.dumps(
            {field: record.get(field) for field in _SEMANTIC_FIELDS},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if fingerprint in seen_exact:
            continue
        seen_exact.add(fingerprint)
        projected.append(record)
    return projected


def cognitive_constraints_from_contract(contract: Any) -> list[dict[str, Any]]:
    """Read the canonical constraint list from a contract-like object."""

    mapping = _as_mapping(contract)
    return project_cognitive_constraints(mapping.get("cognitive_constraints", []))


def cognitive_constraints_from_source_cards(source_cards: Any) -> list[dict[str, Any]]:
    """Read constraints from a ChapterSourceSlice stage-card projection."""

    source = _as_mapping(source_cards)
    return cognitive_constraints_from_contract(source.get("chapter_contract", {}))


def _project_record(item: dict[str, Any]) -> dict[str, Any]:
    record = {
        "claim_id": "",
        "claim_text": "",
        "cognitive_subjects": [],
        "cognitive_object": "",
        "cognitive_level": "unaware",
        "action_level": "none",
        "reader_awareness": "unknown",
        "character_knowledge_coverage": {},
        "cognitive_chapter": None,
        "public_reveal_chapter": None,
        "foreshadow_chapters": [],
        **item,
    }
    record["claim_id"] = _text(record.get("claim_id"))
    record["claim_text"] = _text(record.get("claim_text"))
    record["cognitive_subjects"] = _string_list(record.get("cognitive_subjects"))
    record["cognitive_object"] = _text(record.get("cognitive_object"))
    record["cognitive_level"] = _text(record.get("cognitive_level")) or "unaware"
    record["action_level"] = _text(record.get("action_level")) or "none"
    record["reader_awareness"] = _text(record.get("reader_awareness")) or "unknown"
    record["character_knowledge_coverage"] = normalize_character_knowledge_coverage(
        record.get("character_knowledge_coverage"),
        none_as_empty=True,
    )
    record["cognitive_chapter"] = _optional_positive_int(record.get("cognitive_chapter"))
    record["public_reveal_chapter"] = _optional_positive_int(
        record.get("public_reveal_chapter")
    )
    record["foreshadow_chapters"] = _positive_int_list(record.get("foreshadow_chapters"))
    return record


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[str] = []
    for raw in values:
        text = _text(raw)
        if text and text not in result:
            result.append(text)
    return result


def _optional_positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_int_list(value: Any) -> list[int]:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[int] = []
    for raw in values:
        number = _optional_positive_int(raw)
        if number is not None and number not in result:
            result.append(number)
    return result
