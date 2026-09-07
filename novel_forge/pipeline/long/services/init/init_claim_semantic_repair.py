"""Evidence-bounded semantic patching for init-coherence claims.

The extractor may return valid JSON while placing a value from one narrative
enum into a sibling field.  This module never guesses the replacement locally:
it builds a minimal LLM mission for the rejected paths, accepts only an exact
patch set, applies it to a deep copy, and leaves every other claim byte-for-byte
equivalent at the Python-value level.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from novel_forge.core.format_contracts import FormatSchemaIssue
from novel_forge.pipeline.long.services.claim_field_policy import (
    CHARACTER_KNOWLEDGE_COVERAGE_VALUES,
    CLAIM_ENUM_VALUES,
    LLM_REPAIRABLE_CLAIM_FIELDS,
)

_CLAIM_PATH_RE = re.compile(r"^\$\.claims\[(\d+)]\.([A-Za-z_][A-Za-z0-9_]*)(?:\.(.+))?$")
_BASE_EVIDENCE_FIELDS = (
    "claim_id",
    "artifact",
    "source_path",
    "chapter_numbers",
    "claim_text",
    "evidence",
)
_COGNITIVE_EVIDENCE_FIELDS = (
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
_CLASSIFICATION_EVIDENCE_FIELDS = (
    "subject_text",
    "axis",
    "claim_type",
    "state_before",
    "state_after",
    "event_type",
    "payoff_id",
    "payoff_kind",
    "irreversible",
    "temporality",
    "confidence",
)
_COGNITIVE_REPAIR_FIELDS = frozenset(_COGNITIVE_EVIDENCE_FIELDS)

INIT_CLAIM_SEMANTIC_PATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["repairs"],
    "properties": {
        "repairs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "claim_index",
                    "claim_id",
                    "field",
                    "key",
                    "value",
                    "evidence",
                ],
                "properties": {
                    "claim_index": {"type": "integer", "minimum": 0},
                    "claim_id": {"type": "string"},
                    "field": {"type": "string"},
                    "key": {"type": ["string", "null"]},
                    "value": {},
                    "evidence": {"type": "string"},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class InitClaimRepairTarget:
    """One rejected semantic slot that the repair LLM must re-adjudicate."""

    claim_index: int
    claim_id: str
    field: str
    key: str | None

    @property
    def identity(self) -> tuple[int, str, str | None]:
        return self.claim_index, self.field, self.key

    @property
    def path(self) -> str:
        suffix = f".{self.key}" if self.key is not None else ""
        return f"$.claims[{self.claim_index}].{self.field}{suffix}"


def collect_init_claim_repair_targets(
    data: dict[str, Any],
    issues: tuple[FormatSchemaIssue, ...],
) -> tuple[InitClaimRepairTarget, ...]:
    """Return the exact LLM-repairable paths from a rejected claim batch."""

    claims = data.get("claims")
    if not isinstance(claims, list):
        return ()

    targets: list[InitClaimRepairTarget] = []
    seen: set[tuple[int, str, str | None]] = set()
    for issue in issues:
        match = _CLAIM_PATH_RE.fullmatch(str(issue.path or ""))
        if match is None:
            continue
        claim_index = int(match.group(1))
        field = match.group(2)
        key = match.group(3) or None
        if field not in LLM_REPAIRABLE_CLAIM_FIELDS or claim_index >= len(claims):
            continue
        claim = claims[claim_index]
        if not isinstance(claim, dict):
            continue
        identity = (claim_index, field, key)
        if identity in seen:
            continue
        seen.add(identity)
        targets.append(
            InitClaimRepairTarget(
                claim_index=claim_index,
                claim_id=str(claim.get("claim_id") or "").strip(),
                field=field,
                key=key,
            )
        )
    return tuple(targets)


def build_init_claim_semantic_repair_prompt(
    data: dict[str, Any],
    targets: tuple[InitClaimRepairTarget, ...],
) -> str:
    """Build a minimal evidence-only repair mission for invalid claim slots."""

    raw_claims = data.get("claims")
    claims: list[Any] = raw_claims if isinstance(raw_claims, list) else []
    targets_by_claim: dict[int, list[InitClaimRepairTarget]] = {}
    for target in targets:
        targets_by_claim.setdefault(target.claim_index, []).append(target)
    affected_claims = {
        str(claim_index): _project_claim_repair_evidence(claim, claim_targets)
        for claim_index, claim_targets in targets_by_claim.items()
        if claim_index < len(claims) and isinstance((claim := claims[claim_index]), dict)
    }
    enum_contract = _target_enum_contract(targets)
    mission = {
        "targets": _target_manifest(targets),
        "affected_claims": affected_claims,
        "enum_contract": enum_contract,
    }
    return (
        "你是初始化一致性 Claim 的局部语义修复裁判。"
        "只根据每条 Claim 已有的 claim_text、evidence、认知对象和章节锚点，"
        "重新裁定 targets 指定的字段；不得新增剧情事实，不得改写 Claim。\n\n"
        "硬约束：\n"
        "1. targets 中每个 slot 的每个 key 必须且只能返回一条 repair；"
        "keys=[] 表示修复字段本身。不能增加、遗漏或合并。\n"
        "2. claim_index、claim_id、field、key 必须原样返回。\n"
        "3. value 必须符合 enum_contract；证据不足时选择该字段允许的保守值，"
        "但仍由你根据证据裁定。\n"
        "4. evidence 用短句指出已有 Claim 中支持该值的证据；不得补写输入中不存在的事实。\n"
        '5. 只输出 JSON 对象 {"repairs":[...]}，不要输出解释、Markdown 或完整 Claims。\n\n'
        "修复任务：\n"
        f"{json.dumps(mission, ensure_ascii=False, separators=(',', ':'))}"
    )


def _project_claim_repair_evidence(
    claim: dict[str, Any],
    targets: list[InitClaimRepairTarget],
) -> dict[str, Any]:
    """Project only evidence fields needed to re-judge the requested slots."""

    target_fields = {target.field for target in targets}
    fields = set(_BASE_EVIDENCE_FIELDS)
    fields.update(target_fields)
    if target_fields & _COGNITIVE_REPAIR_FIELDS:
        fields.update(_COGNITIVE_EVIDENCE_FIELDS)
    if target_fields - _COGNITIVE_REPAIR_FIELDS:
        fields.update(_CLASSIFICATION_EVIDENCE_FIELDS)
    return {field: claim[field] for field in fields if field in claim}


def _target_manifest(
    targets: tuple[InitClaimRepairTarget, ...],
) -> list[dict[str, Any]]:
    """Group exact paths without repeating claim identity for every map key."""

    grouped: dict[tuple[int, str], dict[str, list[str | None]]] = {}
    claim_ids: dict[tuple[int, str], str] = {}
    for target in targets:
        claim_key = (target.claim_index, target.claim_id)
        claim_ids[claim_key] = target.claim_id
        grouped.setdefault(claim_key, {}).setdefault(target.field, []).append(target.key)
    return [
        {
            "claim_index": claim_index,
            "claim_id": claim_ids[(claim_index, claim_id)],
            "slots": [
                {
                    "field": field,
                    "keys": [key for key in keys if key is not None],
                }
                for field, keys in fields.items()
            ],
        }
        for (claim_index, claim_id), fields in grouped.items()
    ]


def _target_enum_contract(
    targets: tuple[InitClaimRepairTarget, ...],
) -> dict[str, Any]:
    """Return only validation rules used by this patch mission."""

    target_fields = {target.field for target in targets}
    contract: dict[str, Any] = {
        field: sorted(CLAIM_ENUM_VALUES[field])
        for field in target_fields
        if field in CLAIM_ENUM_VALUES
    }
    if "character_knowledge_coverage" in target_fields:
        contract["character_knowledge_coverage"] = sorted(CHARACTER_KNOWLEDGE_COVERAGE_VALUES)
    if "irreversible" in target_fields:
        contract["irreversible"] = [False, True]
    if "confidence" in target_fields:
        contract["confidence"] = "number between 0 and 1"
    for field in ("cognitive_chapter", "public_reveal_chapter"):
        if field in target_fields:
            contract[field] = "positive integer or null"
    if "foreshadow_chapters" in target_fields:
        contract["foreshadow_chapters"] = "array of positive chapter integers"
    return contract


def apply_init_claim_semantic_patch(
    data: dict[str, Any],
    targets: tuple[InitClaimRepairTarget, ...],
    patch_payload: dict[str, Any],
) -> dict[str, Any]:
    """Apply an exact target set; reject any extra/missing/misdirected repair."""

    repairs = patch_payload.get("repairs")
    if not isinstance(repairs, list):
        raise ValueError("Init claim semantic patch must contain a repairs array")

    expected = {target.identity: target for target in targets}
    required_repair_keys = {
        "claim_index",
        "claim_id",
        "field",
        "key",
        "value",
        "evidence",
    }
    received: dict[tuple[int, str, str | None], dict[str, Any]] = {}
    for raw_repair in repairs:
        if not isinstance(raw_repair, dict):
            raise ValueError("Every init claim semantic repair must be an object")
        if set(raw_repair) != required_repair_keys:
            raise ValueError(
                "Every init claim semantic repair must contain exactly: "
                + ", ".join(sorted(required_repair_keys))
            )
        raw_index = raw_repair.get("claim_index")
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            raise ValueError("Semantic repair claim_index must be an integer")
        field = str(raw_repair.get("field") or "").strip()
        raw_key = raw_repair.get("key")
        key = None if raw_key is None else str(raw_key)
        identity = (raw_index, field, key)
        target = expected.get(identity)
        if target is None:
            raise ValueError(f"Semantic repair targeted an unapproved field: {identity!r}")
        if identity in received:
            raise ValueError(f"Duplicate semantic repair target: {identity!r}")
        if str(raw_repair.get("claim_id") or "").strip() != target.claim_id:
            raise ValueError(f"Semantic repair claim_id mismatch for target: {identity!r}")
        if "value" not in raw_repair:
            raise ValueError(f"Semantic repair omitted value for target: {identity!r}")
        if not str(raw_repair.get("evidence") or "").strip():
            raise ValueError(f"Semantic repair omitted evidence for target: {identity!r}")
        _validate_semantic_repair_value(target, raw_repair.get("value"))
        received[identity] = raw_repair

    missing = set(expected) - set(received)
    if missing:
        raise ValueError(f"Semantic repair omitted targets: {sorted(missing)!r}")

    repaired = deepcopy(data)
    claims = repaired.get("claims")
    if not isinstance(claims, list):
        raise ValueError("Init claim semantic patch source has no claims array")
    for identity, target in expected.items():
        claim = claims[target.claim_index]
        if not isinstance(claim, dict):
            raise ValueError(f"Claim at index {target.claim_index} is not an object")
        value = received[identity]["value"]
        if target.key is None:
            claim[target.field] = value
            continue
        mapping = claim.get(target.field)
        if not isinstance(mapping, dict):
            mapping = {}
            claim[target.field] = mapping
        mapping[target.key] = value
    return repaired


def _validate_semantic_repair_value(target: InitClaimRepairTarget, value: Any) -> None:
    """Reject an invalid focused patch before it can trigger full re-extraction.

    Native JSON Schema cannot express the value contract because ``value`` is
    field-dependent.  Keeping the check beside the exact-path validator makes
    prompt-only and native-schema providers obey the same semantic boundary.
    """

    field = target.field
    if field == "character_knowledge_coverage":
        if value not in CHARACTER_KNOWLEDGE_COVERAGE_VALUES:
            raise ValueError(
                "character_knowledge_coverage repair values must be "
                "unknown, partial, or full"
            )
        return
    allowed = CLAIM_ENUM_VALUES.get(field)
    if allowed is not None:
        if value not in allowed:
            raise ValueError(
                f"{field} repair value must be one of {sorted(allowed)!r}; got {value!r}"
            )
        return
    if field == "irreversible":
        if not isinstance(value, bool):
            raise ValueError("irreversible repair value must be a boolean")
        return
    if field == "confidence":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("confidence repair value must be a number between 0 and 1")
        if not 0 <= float(value) <= 1:
            raise ValueError("confidence repair value must be a number between 0 and 1")
        return
    if field in {"cognitive_chapter", "public_reveal_chapter"}:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise ValueError(f"{field} repair value must be a positive integer or null")
        return
    if field == "foreshadow_chapters":
        if not isinstance(value, list) or any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in value
        ):
            raise ValueError(
                "foreshadow_chapters repair value must be an array of positive integers"
            )


__all__ = [
    "INIT_CLAIM_SEMANTIC_PATCH_SCHEMA",
    "InitClaimRepairTarget",
    "apply_init_claim_semantic_patch",
    "build_init_claim_semantic_repair_prompt",
    "collect_init_claim_repair_targets",
]
