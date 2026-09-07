"""Canonical init source artifacts and chapter source projections."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, cast

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.artifacts import (
    ArtifactEnvelope,
    ArtifactIssue,
    ArtifactScope,
    BlueprintArtifact,
    CanonicalEntityRef,
    ChapterContractIndexArtifact,
    ChapterSourceSliceArtifact,
    CharacterSystemArtifact,
    CreativeDirectionArtifact,
    EntityGraphArtifact,
    InitReadinessArtifact,
    NarrativeContractArtifact,
    OutlineArtifact,
    ProjectSpecArtifact,
    StageArtifact,
    StoryFoundationArtifact,
    StyleVoiceArtifact,
)
from novel_forge.core.user_intent import (
    build_persisted_user_intent_card,
    with_chapter_instruction,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.semantic_consistency import semantic_consistency_view
from novel_forge.pipeline.long.services.constraints.world_rule_governance import (
    build_world_rule_card,
    coerce_world_rule_book,
    project_world_rule_card_for_stage,
    validate_world_rule_book,
)
from novel_forge.pipeline.long.services.contract_field_semantics import (
    KNOWLEDGE_OP_FACT_KEYS,
    is_chapter_contract_entity_id_path,
    is_chapter_contract_entity_ref_map_path,
    is_chapter_contract_entity_ref_path,
    is_chapter_contract_fact_text_path,
)

_log_control_plane = logging.getLogger("novel_forge.control_plane.source_artifacts")

SOURCE_ARTIFACT_TYPES: tuple[str, ...] = (
    "project_spec",
    "story_foundation",
    "character_system",
    "entity_graph",
    "style_voice",
    "creative_direction",
    "blueprint",
    "outline",
    "narrative_contract",
    "chapter_contract_index",
)

_ARTIFACT_MODEL_BY_TYPE: dict[str, type[ArtifactEnvelope]] = {
    "project_spec": ProjectSpecArtifact,
    "story_foundation": StoryFoundationArtifact,
    "character_system": CharacterSystemArtifact,
    "entity_graph": EntityGraphArtifact,
    "style_voice": StyleVoiceArtifact,
    "creative_direction": CreativeDirectionArtifact,
    "blueprint": BlueprintArtifact,
    "outline": OutlineArtifact,
    "narrative_contract": NarrativeContractArtifact,
    "chapter_contract_index": ChapterContractIndexArtifact,
}

_UNKNOWN_ENTITY_PLACEHOLDERS = frozenset(
    {
        "unknown",
        "none",
        "null",
        "n/a",
        "na",
        "reader",
        "narrator",
        "pov",
        "读者",
        "未知",
        "不明",
        "未明",
        "未定",
        "待定",
        "未指定",
        "未说明",
        "未提供",
        "未填写",
        "不详",
        "不适用",
        "无",
        "暂无",
    }
)
_TASK_TYPE_VALUE_SET: frozenset[str] = frozenset(str(member.value) for member in TaskType)


def hash_payload(payload: Any) -> str:
    """Return a deterministic hash for JSON-like payloads."""

    text = json.dumps(_dump(payload), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_init_entity_catalog(
    entity_graph: Any,
    character_bible: Any,
    *,
    chapter_contracts: Any | None = None,
    max_aliases_per_entity: int = 12,
    extra_refs: list[CanonicalEntityRef] | None = None,
) -> dict[str, Any]:
    """Build the canonical entity allow-list shared by init prompts and repair.

    ``extra_refs`` allows callers (e.g. entity reconciliation) to inject
    additional canonical refs that were discovered after the initial catalog
    was built.  These are merged into the same allow-list and alias index.
    """

    refs = _canonical_entity_refs(
        entity_graph,
        character_bible,
        chapter_contracts=chapter_contracts,
    )
    if extra_refs:
        refs = _augment_character_aliases(_dedupe_canonical_refs([*refs, *extra_refs]))
    return _entity_catalog_from_refs(
        refs,
        max_aliases_per_entity=max_aliases_per_entity,
    )


def _entity_catalog_from_refs(
    refs: list[CanonicalEntityRef],
    *,
    max_aliases_per_entity: int = 12,
) -> dict[str, Any]:
    """Build an entity catalog directly from canonical refs."""

    allowed_entities: list[dict[str, Any]] = []
    alias_to_entity: dict[str, dict[str, str]] = {}
    for ref in refs:
        aliases = _dedupe([alias for alias in ref.aliases if alias != ref.canonical_name])
        item = {
            "entity_id": ref.entity_id,
            "canonical_name": ref.canonical_name,
            "entity_type": ref.entity_type,
            "aliases": aliases[: max(0, max_aliases_per_entity)],
        }
        allowed_entities.append(item)
        for token in _dedupe([ref.entity_id, ref.canonical_name, *aliases]):
            alias_to_entity.setdefault(
                token,
                {
                    "entity_id": ref.entity_id,
                    "canonical_name": ref.canonical_name,
                    "entity_type": ref.entity_type,
                },
            )
    return {
        "allowed_entities": allowed_entities,
        "allowed_entity_ids": [item["entity_id"] for item in allowed_entities],
        "allowed_entity_names": [item["canonical_name"] for item in allowed_entities],
        "alias_to_entity": alias_to_entity,
        "policy": (
            "所有角色/实体引用必须使用 allowed_entities 中的 entity_id 或 canonical_name；"
            "alias 只能用于识别输入，不得作为输出规范名。无法确认映射时保持空值或交给修复裁判。"
        ),
    }


def project_init_entity_catalog(
    entity_catalog: dict[str, Any] | None,
    context_payload: Any,
    *,
    max_entities: int = 96,
    max_aliases_per_entity: int = 4,
) -> dict[str, Any]:
    """Project a bounded prompt catalog from the canonical full catalog.

    Identity remains governed by the full catalog outside the prompt.  This
    projection reserves all character anchors and then includes entities
    explicitly mentioned by the current batch, preventing a global alias map from being
    repeated in every recursive batch split.
    """
    if not isinstance(entity_catalog, dict):
        return {}
    raw_entities = entity_catalog.get("allowed_entities")
    if not isinstance(raw_entities, list):
        return {}
    entities = [item for item in raw_entities if isinstance(item, dict)]
    limit = max(1, int(max_entities or 1))
    if len(entities) <= limit:
        return entity_catalog
    haystack = json.dumps(_dump(context_payload), ensure_ascii=False, default=str)

    def _tokens(item: dict[str, Any]) -> list[str]:
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        return _dedupe(
            [
                str(item.get("entity_id") or "").strip(),
                str(item.get("canonical_name") or "").strip(),
                *[str(alias or "").strip() for alias in aliases],
            ]
        )

    mentioned = [
        item
        for item in entities
        if any(len(token) >= 2 and token in haystack for token in _tokens(item))
    ]
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in [
        *[item for item in entities if str(item.get("entity_type") or "") == "character"],
        *mentioned,
    ]:
        entity_id = str(item.get("entity_id") or "").strip()
        if not entity_id or entity_id in seen_ids:
            continue
        selected.append(item)
        seen_ids.add(entity_id)
        if len(selected) >= limit:
            break

    refs = [
        CanonicalEntityRef(
            entity_id=str(item.get("entity_id") or "").strip(),
            canonical_name=str(item.get("canonical_name") or "").strip(),
            entity_type=str(item.get("entity_type") or "unknown"),
            aliases=item.get("aliases") if isinstance(item.get("aliases"), list) else [],
        )
        for item in selected
        if str(item.get("entity_id") or "").strip()
        and str(item.get("canonical_name") or "").strip()
    ]
    projected = _entity_catalog_from_refs(
        refs,
        max_aliases_per_entity=max_aliases_per_entity,
    )
    projected["projection"] = {
        "mode": "characters_plus_batch_exact_mentions",
        "selected_count": len(refs),
        "full_count": len(entities),
        "max_entities": limit,
    }
    return projected


def normalize_cognitive_subjects(
    subjects: list[str],
    entity_catalog: dict[str, Any] | None,
) -> list[str]:
    """Compatibility helper that executes exact catalog decisions only.

    For each subject:
    - Exact match in ``alias_to_entity`` → replace with canonical_name.
    - No match → drop (prompt contract says "无法确认映射时置空").

    The active init and chapter pipelines do not call this function to decide
    identity. They preserve unresolved text for LLM adjudication. This helper
    remains exported for callers that already hold an LLM-approved exact alias
    catalog; it performs no fuzzy matching, typo repair, or semantic inference.
    """
    if not isinstance(entity_catalog, dict):
        return [s for s in subjects if s]
    alias_to_entity = entity_catalog.get("alias_to_entity")
    if not isinstance(alias_to_entity, dict) or not alias_to_entity:
        return [s for s in subjects if s]

    resolved: list[str] = []
    for subject in subjects:
        text = str(subject or "").strip()
        if not text:
            continue
        if _is_unknown_entity_placeholder(text):
            continue
        entry = alias_to_entity.get(text)
        if isinstance(entry, dict):
            canonical = str(entry.get("canonical_name") or "").strip()
            if canonical and canonical not in resolved:
                resolved.append(canonical)
        # No match → per prompt contract: drop silently.
    return resolved


def normalize_chapter_contract_entity_references(
    payload: dict[str, Any],
    *,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply exact catalog mappings without guessing or deleting unknown names.

    Unknown and malformed references remain visible so validation can route
    them to LLM reconciliation.  Local code is only a mechanical executor for
    identity decisions already recorded in ``alias_to_entity``.
    """

    if not entity_catalog or not entity_catalog.get("alias_to_entity"):
        return payload
    contracts = payload.get("by_chapter")
    if isinstance(contracts, dict):
        for contract in contracts.values():
            if isinstance(contract, dict):
                _normalize_contract_entity_payload(contract, entity_catalog)
        return payload
    contracts = payload.get("chapter_contracts")
    if isinstance(contracts, list):
        for contract in contracts:
            if isinstance(contract, dict):
                _normalize_contract_entity_payload(contract, entity_catalog)
    return payload


def _normalize_contract_entity_payload(value: Any, entity_catalog: dict[str, Any]) -> Any:
    return _normalize_contract_entity_payload_for_path(value, entity_catalog, field_path=())


def _normalize_contract_entity_payload_for_path(
    value: Any,
    entity_catalog: dict[str, Any],
    *,
    field_path: tuple[str, ...],
) -> Any:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            key_text = str(key)
            child_path = (*field_path, key_text)
            if key_text == "character_knowledge_coverage":
                value[key] = _normalize_character_knowledge_coverage_keys(item, entity_catalog)
            elif is_chapter_contract_fact_text_path(child_path):
                value[key] = _normalize_knowledge_op_fact_value(item, entity_catalog)
            elif is_chapter_contract_entity_ref_path(child_path):
                value[key] = _normalize_entity_reference_value(
                    item,
                    entity_catalog,
                    drop_unresolved=key_text == "cognitive_subjects",
                    field_path=child_path,
                    prefer_entity_id=is_chapter_contract_entity_id_path(child_path),
                )
            else:
                value[key] = _normalize_contract_entity_payload_for_path(
                    item,
                    entity_catalog,
                    field_path=child_path,
                )
        return value
    if isinstance(value, list):
        return [
            _normalize_contract_entity_payload_for_path(
                item,
                entity_catalog,
                field_path=field_path,
            )
            for item in value
        ]
    if isinstance(value, str):
        return value
    return value


def _normalize_character_knowledge_coverage_keys(
    value: Any,
    entity_catalog: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for raw_name, awareness in value.items():
        name = _normalize_entity_reference_string(
            str(raw_name or ""),
            entity_catalog,
            drop_unresolved=True,
        )
        if name:
            result[name] = awareness
    return result


def _normalize_entity_reference_value(
    value: Any,
    entity_catalog: dict[str, Any],
    *,
    drop_unresolved: bool,
    field_path: tuple[str, ...],
    prefer_entity_id: bool,
) -> Any:
    if isinstance(value, str):
        return _normalize_entity_reference_string(
            value,
            entity_catalog,
            drop_unresolved=drop_unresolved,
            prefer_entity_id=prefer_entity_id,
        )
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            if isinstance(item, str):
                normalized = _normalize_entity_reference_string(
                    item,
                    entity_catalog,
                    drop_unresolved=drop_unresolved,
                    prefer_entity_id=prefer_entity_id,
                )
                if normalized and normalized not in result:
                    result.append(normalized)
            else:
                result.append(
                    _normalize_contract_entity_payload_for_path(
                        item,
                        entity_catalog,
                        field_path=field_path,
                    )
                )
        return result
    if isinstance(value, dict):
        return _normalize_contract_entity_payload_for_path(
            value,
            entity_catalog,
            field_path=field_path,
        )
    return value


def _normalize_knowledge_op_fact_value(value: Any, entity_catalog: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_normalize_knowledge_op_fact_value(item, entity_catalog) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_knowledge_op_fact_value(item, entity_catalog)
            for key, item in value.items()
        }
    return value


def _normalize_entity_reference_string(
    value: str,
    entity_catalog: dict[str, Any],
    *,
    drop_unresolved: bool,
    prefer_entity_id: bool = False,
) -> str:
    del drop_unresolved
    text = str(value or "").strip()
    if not text:
        return ""
    if _is_unknown_entity_placeholder(text):
        return ""
    alias_to_entity = entity_catalog.get("alias_to_entity")
    if isinstance(alias_to_entity, dict):
        entry = alias_to_entity.get(text)
        if isinstance(entry, dict):
            if prefer_entity_id:
                return str(entry.get("entity_id") or "").strip()
            return str(entry.get("canonical_name") or "").strip()
    return text


def source_artifact_hashes(storage: FileSystemStorage, layout: ProjectLayout) -> dict[str, str]:
    """Return current hashes for persisted init source artifacts."""

    hashes: dict[str, str] = {}
    for artifact_type in SOURCE_ARTIFACT_TYPES:
        path = layout.source_artifact_path(artifact_type)
        if storage.exists(path):
            hashes[artifact_type] = hash_payload(storage.load_json(path))
    return hashes


def _stale_source_hash_keys(
    expected_hashes: dict[str, str],
    current_hashes: dict[str, str],
) -> list[str]:
    return [key for key, expected in expected_hashes.items() if current_hashes.get(key) != expected]


def _normalize_init_readiness_consistency(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    readiness: InitReadinessArtifact,
) -> InitReadinessArtifact:
    """Reconcile source artifact envelopes with the readiness registry.

    ``init_readiness`` is the aggregate, post-repair gate.  Historical projects
    may still have child source artifacts carrying stale blocking issues from
    an earlier registry.  Recompute each child gate from the same canonical
    registry, then recompute readiness from those normalized children so pass
    and fail states cannot diverge.
    """

    artifacts = {
        artifact_type: _load_source_artifact(storage, layout, artifact_type)
        for artifact_type in SOURCE_ARTIFACT_TYPES
    }
    canonical_refs = _normalized_readiness_canonical_refs(readiness, artifacts)
    entity_catalog = _entity_catalog_from_refs(canonical_refs)
    normalized_artifacts: dict[str, ArtifactEnvelope] = {}

    for artifact_type, artifact in artifacts.items():
        issues = _source_payload_issues(
            artifact_type=artifact_type,
            payload=artifact.payload,
            canonical_refs=canonical_refs,
            entity_catalog=entity_catalog,
        )
        quality_status = "fail" if issues else "pass"
        normalized = artifact.model_copy(
            update={
                "canonical_entity_refs": canonical_refs,
                "quality_status": quality_status,
                "blocking_issues": issues,
            }
        )
        if _artifact_gate_needs_update(
            artifact,
            canonical_refs=canonical_refs,
            quality_status=quality_status,
            blocking_issues=issues,
        ):
            storage.save_json(
                layout.source_artifact_path(artifact_type),
                normalized.model_dump(mode="json"),
            )
        normalized_artifacts[artifact_type] = normalized

    source_hashes = {
        artifact_type: hash_payload(artifact.model_dump(mode="json"))
        for artifact_type, artifact in normalized_artifacts.items()
    }
    readiness_report = _as_mapping(readiness.payload.get("readiness_report"))
    blocking_issues = _readiness_issues(readiness_report, normalized_artifacts)
    quality_status = "fail" if blocking_issues else "pass"
    normalized_readiness = readiness.model_copy(
        update={
            "source_hashes": source_hashes,
            "canonical_entity_refs": canonical_refs,
            "quality_status": quality_status,
            "blocking_issues": blocking_issues,
        }
    )
    if _readiness_gate_needs_update(
        readiness,
        canonical_refs=canonical_refs,
        source_hashes=source_hashes,
        quality_status=quality_status,
        blocking_issues=blocking_issues,
    ):
        storage.save_json(
            layout.init_readiness_artifact_path,
            normalized_readiness.model_dump(mode="json"),
        )
    return normalized_readiness


def _normalized_readiness_canonical_refs(
    readiness: InitReadinessArtifact,
    artifacts: dict[str, ArtifactEnvelope],
) -> list[CanonicalEntityRef]:
    refs = list(readiness.canonical_entity_refs)
    if not refs:
        for artifact in artifacts.values():
            refs.extend(artifact.canonical_entity_refs)
    return _dedupe_canonical_refs(refs)


def _artifact_gate_needs_update(
    artifact: ArtifactEnvelope,
    *,
    canonical_refs: list[CanonicalEntityRef],
    quality_status: str,
    blocking_issues: list[ArtifactIssue],
) -> bool:
    return (
        _canonical_ref_signatures(artifact.canonical_entity_refs)
        != _canonical_ref_signatures(canonical_refs)
        or artifact.quality_status != quality_status
        or _issue_signatures(artifact.blocking_issues) != _issue_signatures(blocking_issues)
    )


def _readiness_gate_needs_update(
    readiness: InitReadinessArtifact,
    *,
    canonical_refs: list[CanonicalEntityRef],
    source_hashes: dict[str, str],
    quality_status: str,
    blocking_issues: list[ArtifactIssue],
) -> bool:
    return (
        _canonical_ref_signatures(readiness.canonical_entity_refs)
        != _canonical_ref_signatures(canonical_refs)
        or readiness.source_hashes != source_hashes
        or readiness.quality_status != quality_status
        or _issue_signatures(readiness.blocking_issues) != _issue_signatures(blocking_issues)
    )


def _canonical_ref_signatures(
    refs: list[CanonicalEntityRef],
) -> list[tuple[str, str, str, tuple[str, ...]]]:
    return [
        (
            ref.entity_id,
            ref.canonical_name,
            ref.entity_type,
            tuple(ref.aliases),
        )
        for ref in refs
    ]


def _issue_signatures(
    issues: list[ArtifactIssue],
) -> list[tuple[str, str, str, str, str]]:
    return [
        (
            issue.code,
            issue.message,
            issue.severity,
            issue.source,
            issue.path,
        )
        for issue in issues
    ]


def persist_init_source_artifacts(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    project_id: str,
    spec: Any,
    story_bible: Any,
    character_bible: Any,
    character_system: Any,
    entity_graph: Any,
    style_profile: Any,
    creative_packet: Any,
    blueprint: Any,
    outline: Any,
    narrative_contract: Any,
    chapter_contracts: Any,
    readiness_report: dict[str, Any],
    extra_refs: list[CanonicalEntityRef] | None = None,
) -> InitReadinessArtifact:
    """Persist canonical source artifacts and the envelope readiness artifact."""

    canonical_refs = _canonical_entity_refs(
        entity_graph,
        character_bible,
        chapter_contracts=chapter_contracts,
    )
    if extra_refs:
        canonical_refs = _augment_character_aliases(
            _dedupe_canonical_refs([*canonical_refs, *extra_refs])
        )
    entity_catalog = build_init_entity_catalog(
        entity_graph,
        character_bible,
        chapter_contracts=chapter_contracts,
        extra_refs=extra_refs,
    )
    source_inputs: dict[str, dict[str, Any]] = {
        "project_spec": _project_spec_payload(spec),
        "story_foundation": _story_foundation_payload(story_bible),
        "character_system": _character_system_payload(character_bible, character_system),
        "entity_graph": _entity_graph_payload(entity_graph),
        "style_voice": _style_voice_payload(style_profile, character_bible),
        "creative_direction": _dump(creative_packet),
        "blueprint": _dump(blueprint),
        "outline": _dump(outline),
        "narrative_contract": _effective_narrative_contract(narrative_contract),
        "chapter_contract_index": normalize_chapter_contract_entity_references(
            _chapter_contract_index_payload(
                chapter_contracts,
                outline=outline,
                canonical_refs=canonical_refs,
            ),
            entity_catalog=entity_catalog,
        ),
    }

    persisted: dict[str, ArtifactEnvelope] = {}
    for artifact_type in SOURCE_ARTIFACT_TYPES:
        payload = source_inputs[artifact_type]
        issues = _source_payload_issues(
            artifact_type=artifact_type,
            payload=payload,
            canonical_refs=canonical_refs,
            entity_catalog=entity_catalog,
        )
        artifact = _build_source_artifact(
            artifact_type=artifact_type,
            project_id=project_id,
            payload=payload,
            canonical_refs=canonical_refs,
            blocking_issues=issues,
        )
        storage.save_json(
            layout.source_artifact_path(artifact_type),
            artifact.model_dump(mode="json"),
        )
        persisted[artifact_type] = artifact

    source_hashes = {
        artifact_type: hash_payload(artifact.model_dump(mode="json"))
        for artifact_type, artifact in persisted.items()
    }
    blocking_issues = _readiness_issues(readiness_report, persisted)
    readiness = InitReadinessArtifact(
        project_id=project_id,
        artifact_id="init_readiness",
        scope=ArtifactScope(kind="project", ids=[project_id]),
        source_artifact_ids=list(SOURCE_ARTIFACT_TYPES),
        source_hashes=source_hashes,
        canonical_entity_refs=canonical_refs,
        quality_status="fail" if blocking_issues else "pass",
        blocking_issues=blocking_issues,
        payload={
            "readiness_report": readiness_report,
            "source_artifact_types": list(SOURCE_ARTIFACT_TYPES),
        },
    )
    storage.save_json(layout.init_readiness_artifact_path, readiness.model_dump(mode="json"))
    return readiness


def load_init_readiness_artifact(
    storage: FileSystemStorage,
    layout: ProjectLayout,
) -> InitReadinessArtifact:
    """Load and validate the canonical init readiness artifact."""

    if not storage.exists(layout.init_readiness_artifact_path):
        raise ValueError(
            "项目缺少 source_artifacts/init_readiness.json，请按新 artifact 架构重新初始化。"
        )
    readiness = InitReadinessArtifact.model_validate(
        storage.load_json(layout.init_readiness_artifact_path)
    )
    # A legacy readiness file can otherwise look healthy even though its
    # source foundation predates the rule ledger.  Refuse the chapter run at
    # the authority boundary instead of attempting an unsafe migration.
    story_foundation = _load_source_artifact(storage, layout, "story_foundation")
    _require_current_world_rule_book(story_foundation.payload)
    current_hashes = source_artifact_hashes(storage, layout)
    stale = _stale_source_hash_keys(readiness.source_hashes, current_hashes)
    if stale:
        raise ValueError(
            "初始化源头 artifact hash 已失效，请重新初始化：" + "、".join(sorted(stale))
        )
    readiness = _normalize_init_readiness_consistency(storage, layout, readiness)
    if readiness.quality_status != "pass":
        messages = [issue.message for issue in readiness.blocking_issues]
        raise ValueError("初始化源头 artifact 未通过准入：" + "；".join(messages[:5]))
    current_hashes = source_artifact_hashes(storage, layout)
    stale = _stale_source_hash_keys(readiness.source_hashes, current_hashes)
    if stale:
        raise ValueError(
            "初始化源头 artifact hash 已失效，请重新初始化：" + "、".join(sorted(stale))
        )
    return readiness


def _require_current_world_rule_book(story_foundation: dict[str, Any]) -> None:
    """Reject legacy projects before they can build a chapter source slice."""

    raw_book = story_foundation.get("world_rule_book")
    if not raw_book:
        raise ValueError(
            "项目缺少 WorldRuleBook；历史项目不支持自动迁移，请从初始化流程重新生成全部源头产物。"
        )
    try:
        errors = validate_world_rule_book(
            coerce_world_rule_book(raw_book),
            magic_or_tech=str(story_foundation.get("magic_or_tech") or ""),
        )
    except Exception as exc:
        raise ValueError("WorldRuleBook 无法解析；请重新初始化全部源头产物。") from exc
    if errors:
        raise ValueError("WorldRuleBook 未通过初始化准入：" + "；".join(errors[:5]))


def _load_runtime_user_intent_card(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    sources: dict[str, ArtifactEnvelope],
) -> dict[str, Any]:
    """Keep explicit requests distinct from accepted, possibly AI-enriched facts."""

    if storage.exists(layout.init_request_meta_path):
        try:
            metadata = storage.load_json(layout.init_request_meta_path)
            request_payload = metadata.get("request") if isinstance(metadata, dict) else None
            if isinstance(request_payload, dict):
                return build_persisted_user_intent_card(metadata, sources["project_spec"].payload)
        except Exception as exc:
            _log_control_plane.warning(
                "chapter_user_intent_meta_load_failed | path=%s | error=%s",
                layout.init_request_meta_path,
                exc,
            )
    return build_persisted_user_intent_card({}, sources["project_spec"].payload)


def build_and_persist_chapter_source_slice(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    project_id: str,
    chapter_number: int,
) -> ChapterSourceSliceArtifact:
    """Create the deterministic source projection consumed by a chapter run."""

    readiness = load_init_readiness_artifact(storage, layout)
    sources = {
        artifact_type: _load_source_artifact(storage, layout, artifact_type)
        for artifact_type in SOURCE_ARTIFACT_TYPES
    }
    source_hashes = {
        artifact_type: hash_payload(artifact.model_dump(mode="json"))
        for artifact_type, artifact in sources.items()
    }
    if source_hashes != readiness.source_hashes:
        raise ValueError("章节源头切片生成失败：source artifact hash 与 readiness 不一致。")

    contract_index = sources["chapter_contract_index"].payload
    contract_by_chapter = contract_index.get("by_chapter", {})
    chapter_key = str(chapter_number)
    chapter_contract = contract_by_chapter.get(chapter_key)
    if not isinstance(chapter_contract, dict):
        raise ValueError(f"章节源头切片缺少第 {chapter_number} 章契约。")

    outline_payload = sources["outline"].payload
    chapter_outline = _chapter_outline_payload(outline_payload, chapter_number)
    if not chapter_outline:
        raise ValueError(f"章节源头切片缺少第 {chapter_number} 章大纲。")

    canonical_refs = _chapter_slice_canonical_refs(readiness, sources)
    entity_lookup = _entity_lookup(canonical_refs)
    contract_names = _relevant_names(chapter_contract)
    outline_names = _relevant_names(chapter_outline)
    relevant_names = _dedupe([*contract_names, *outline_names])
    relevant_entities, _ = _resolve_relevant_entities(relevant_names, entity_lookup)
    _, unresolved_contract_names = _resolve_relevant_entities(contract_names, entity_lookup)
    blocking_issues = [
        ArtifactIssue(
            code="unresolved_entity_ref",
            message=f"第 {chapter_number} 章引用未登记角色/实体：{name}",
            severity="critical",
            source="chapter_source_slice",
            path=f"chapter_contracts[{chapter_number}]",
        )
        for name in unresolved_contract_names
    ]
    forbidden_reveals = _forbidden_reveal_boundaries(chapter_contract)
    user_intent = _load_runtime_user_intent_card(storage, layout, sources)

    legacy_payload = {
        "chapter_number": chapter_number,
        "chapter_outline": chapter_outline,
        "chapter_contract": chapter_contract,
        "story_foundation": _project_story_foundation(
            sources["story_foundation"].payload,
            chapter_contract=chapter_contract,
        ),
        "style_voice": _project_style_voice(
            sources["style_voice"].payload,
            relevant_entities=relevant_entities,
        ),
        "creative_direction": _project_creative_direction(
            sources["creative_direction"].payload,
            chapter_contract=chapter_contract,
        ),
        "narrative_contract": _project_narrative_contract(
            sources["narrative_contract"].payload,
            chapter_contract=chapter_contract,
        ),
        "relevant_entities": [ref.model_dump(mode="json") for ref in relevant_entities],
        "forbidden_reveal_boundaries": forbidden_reveals,
    }
    archive_refs = _build_archive_refs(sources)
    runtime_payload = _build_runtime_capsule_payload(
        sources=sources,
        chapter_number=chapter_number,
        chapter_outline=chapter_outline,
        chapter_contract=chapter_contract,
        relevant_entities=relevant_entities,
        forbidden_reveals=forbidden_reveals,
        user_intent=user_intent,
    )
    audit_payload = _build_audit_capsule_payload(
        sources=sources,
        chapter_number=chapter_number,
        chapter_outline=chapter_outline,
        chapter_contract=chapter_contract,
        relevant_entities=relevant_entities,
        forbidden_reveals=forbidden_reveals,
    )

    artifact = ChapterSourceSliceArtifact(
        project_id=project_id,
        artifact_id=f"chapter_source_slice:{chapter_number:03d}",
        scope=ArtifactScope(kind="chapter", ids=[str(chapter_number)]),
        source_artifact_ids=list(SOURCE_ARTIFACT_TYPES),
        source_hashes=source_hashes,
        canonical_entity_refs=relevant_entities,
        quality_status="fail" if blocking_issues else "pass",
        blocking_issues=blocking_issues,
        semantic_consistency=semantic_consistency_view(storage, layout),
        payload={
            **legacy_payload,
            "archive_refs": archive_refs,
            "runtime": runtime_payload,
            "audit": audit_payload,
        },
    )
    if artifact.quality_status != "pass":
        messages = [issue.message for issue in artifact.blocking_issues]
        raise ValueError("章节源头切片未通过准入：" + "；".join(messages[:5]))
    storage.save_json(
        layout.chapter_source_slice_path(chapter_number), artifact.model_dump(mode="json")
    )
    return artifact


def _chapter_slice_canonical_refs(
    readiness: InitReadinessArtifact,
    sources: dict[str, ArtifactEnvelope],
) -> list[CanonicalEntityRef]:
    """Return the authoritative entity registry for chapter source slices.

    Init readiness is the post-repair aggregate registry. Individual source
    artifacts may carry older entity_graph-only refs, so chapter slicing must
    start from readiness and only then merge per-artifact refs.
    """

    refs: list[CanonicalEntityRef] = list(readiness.canonical_entity_refs)
    for artifact in sources.values():
        refs.extend(artifact.canonical_entity_refs)
    return _dedupe_canonical_refs(refs)


def load_chapter_source_slice(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    project_id: str,
    chapter_number: int,
) -> ChapterSourceSliceArtifact:
    """Load a valid chapter source slice, generating it when absent."""

    # Validate source authority even when a prior chapter slice happens to be
    # cached.  Otherwise a historical project could bypass the mandatory
    # WorldRuleBook reinitialization simply by reusing an old slice.
    load_init_readiness_artifact(storage, layout)
    path = layout.chapter_source_slice_path(chapter_number)
    if storage.exists(path):
        artifact = ChapterSourceSliceArtifact.model_validate(storage.load_json(path))
        semantic = semantic_consistency_view(storage, layout)
        current_hashes = source_artifact_hashes(storage, layout)
        stale = [
            key
            for key, expected in artifact.source_hashes.items()
            if current_hashes.get(key) != expected
        ]
        runtime = artifact.payload.get("runtime")
        has_user_intent = isinstance(runtime, dict) and bool(runtime.get("user_intent"))
        semantic_current = (
            artifact.semantic_consistency.source_fingerprint == semantic.source_fingerprint
            and artifact.semantic_consistency.ledger_hash == semantic.ledger_hash
            and artifact.semantic_consistency.report_id == semantic.report_id
            and artifact.semantic_consistency.status == semantic.status
        )
        if (
            not stale
            and artifact.quality_status == "pass"
            and has_user_intent
            and semantic_current
        ):
            return artifact
    return build_and_persist_chapter_source_slice(
        storage=storage,
        layout=layout,
        project_id=project_id,
        chapter_number=chapter_number,
    )


def project_stage_source_cards(
    chapter_source_slice: Any,
    *,
    stage: str,
) -> dict[str, Any]:
    """Return the stage-appropriate source projection from ChapterSourceSlice.

    The raw init artifacts remain behind this projection boundary.  Runtime
    stages should see only the bounded chapter slice and derived cards they can
    execute safely; full project_spec, character_system, entity_graph, and
    blueprint payloads are intentionally not passed through as stage cards.
    """

    artifact = _coerce_slice(chapter_source_slice)
    payload = artifact.payload
    stage_name = str(stage or "").strip().lower()
    runtime = _runtime_capsule_from_payload(payload)
    audit = (
        _audit_capsule_from_payload(payload)
        if stage_name
        in {
            "audit",
            "book_audit",
            "book_consistency",
        }
        else {}
    )
    runtime_contract = _as_mapping(runtime.get("chapter_contract"))
    runtime_world = _as_mapping(runtime.get("world"))
    runtime_style = _as_mapping(runtime.get("style"))
    runtime_entities = list(runtime.get("entities") or [])
    legacy_style_voice = _as_mapping(payload.get("style_voice"))
    style_voice = {
        "style_profile": runtime_style or legacy_style_voice.get("style_profile", {}),
        "character_voices": runtime_style.get("character_voices")
        or legacy_style_voice.get("character_voices", []),
    }
    research_pack, research_uncertainty = _project_chapter_research_for_stage(
        runtime,
        stage=stage_name,
    )
    cards: dict[str, Any] = {
        "source_artifact_id": artifact.artifact_id,
        "source_hashes": dict(artifact.source_hashes),
        "audit": audit,
        "chapter_contract": runtime_contract or payload.get("chapter_contract", {}),
        "literary_contract": runtime_contract.get("literary_contract", {}),
        "forbidden_reveal_boundaries": runtime_contract.get("p0_forbidden_boundaries")
        or payload.get("forbidden_reveal_boundaries", []),
        "relevant_entities": runtime_entities or payload.get("relevant_entities", []),
        "world_rule_card": project_world_rule_card_for_stage(
            runtime.get("world_rule_card", {}), stage=stage_name
        ),
        "user_intent": _as_mapping(runtime.get("user_intent")),
        "research_evidence_pack": research_pack,
        "research_uncertainty": research_uncertainty,
    }
    if stage_name in {
        "bridge",
        "plan",
        "draft",
        "wave",
        "review",
        "repair",
        "continuity_repair",
        "causal_repair",
        "polish",
        "humanize",
    }:
        cards["story_foundation"] = runtime_world or payload.get("story_foundation", {})
    if stage_name in {"plan", "draft", "wave", "polish"}:
        cards["style_voice"] = style_voice
    if stage_name == "plan":
        cards["creative_direction"] = runtime.get("creative_direction", {})
        cards["narrative_contract"] = runtime.get("narrative_contract", {})
    if stage_name in {"review", "repair", "continuity_repair", "causal_repair"}:
        cards = {
            "source_artifact_id": artifact.artifact_id,
            "source_hashes": dict(artifact.source_hashes),
            "chapter_contract": runtime_contract or payload.get("chapter_contract", {}),
            "literary_contract": runtime_contract.get("literary_contract", {}),
            "forbidden_reveal_boundaries": runtime_contract.get("p0_forbidden_boundaries")
            or payload.get("forbidden_reveal_boundaries", []),
            "relevant_entities": runtime_entities or payload.get("relevant_entities", []),
            "world_rule_card": project_world_rule_card_for_stage(
                runtime.get("world_rule_card", {}), stage=stage_name
            ),
            "story_foundation": runtime_world or payload.get("story_foundation", {}),
            "user_intent": _as_mapping(runtime.get("user_intent")),
            "research_evidence_pack": research_pack,
            "research_uncertainty": research_uncertainty,
        }
    return cards


def _project_chapter_research_for_stage(
    runtime: dict[str, Any],
    *,
    stage: str,
) -> tuple[dict[str, Any], list[str]]:
    """Apply the fact/inspiration permission boundary before prompt rendering."""

    raw_pack = _as_mapping(runtime.get("research_evidence_pack"))
    if not raw_pack.get("pack_id"):
        return {}, []
    stage_name = str(stage or "").strip().lower()
    inspiration_limits = {"bridge": 3, "plan": 3, "draft": 2}
    fact_only_stages = {
        "review",
        "repair",
        "continuity_repair",
        "causal_repair",
        "reading_power_repair",
        "check",
    }
    if stage_name not in {*inspiration_limits, *fact_only_stages}:
        return {}, []
    inspiration_limit = inspiration_limits.get(stage_name, 0)
    projected_cards: list[dict[str, Any]] = []
    inspiration_count = 0
    for raw_card in list(raw_pack.get("evidence_cards") or []):
        card = _as_mapping(raw_card)
        kind = str(card.get("kind") or "").strip()
        if kind == "external_fact":
            projected_cards.append(card)
        elif kind == "external_inspiration" and inspiration_count < inspiration_limit:
            projected_cards.append(card)
            inspiration_count += 1
    projected_pack = dict(raw_pack)
    projected_pack["evidence_cards"] = projected_cards
    uncertainty = [
        str(item).strip()
        for item in list(runtime.get("research_uncertainty") or [])
        if str(item).strip()
    ][:6]
    return projected_pack, uncertainty


def attach_chapter_instruction_to_source_slice(
    chapter_source_slice: Any,
    instruction: str,
    *,
    chapter_number: int,
) -> ChapterSourceSliceArtifact:
    """Overlay a run-local human instruction without mutating persisted source artifacts."""

    artifact = _coerce_slice(chapter_source_slice)
    if not str(instruction or "").strip():
        return artifact
    payload = dict(artifact.payload)
    runtime = dict(_runtime_capsule_from_payload(payload))
    runtime["user_intent"] = with_chapter_instruction(
        _as_mapping(runtime.get("user_intent")),
        instruction,
        chapter_number=chapter_number,
    )
    payload["runtime"] = runtime
    return artifact.model_copy(update={"payload": payload})


def attach_chapter_research_to_source_slice(
    chapter_source_slice: Any,
    *,
    evidence_pack: dict[str, Any] | None,
    uncertainty: list[str] | tuple[str, ...] = (),
) -> ChapterSourceSliceArtifact:
    """Overlay cached run evidence without mutating the persisted source slice."""

    artifact = _coerce_slice(chapter_source_slice)
    if not evidence_pack:
        return artifact
    payload = dict(artifact.payload)
    runtime = dict(_runtime_capsule_from_payload(payload))
    runtime["research_evidence_pack"] = dict(evidence_pack)
    runtime["research_uncertainty"] = [
        str(item).strip() for item in uncertainty if str(item).strip()
    ][:6]
    payload["runtime"] = runtime
    return artifact.model_copy(update={"payload": payload})


def _build_archive_refs(sources: dict[str, ArtifactEnvelope]) -> dict[str, Any]:
    return {
        artifact_type: {
            "artifact_id": artifact.artifact_id,
            "hash": hash_payload(artifact.model_dump(mode="json")),
        }
        for artifact_type, artifact in sources.items()
    }


def _build_runtime_capsule_payload(
    *,
    sources: dict[str, ArtifactEnvelope],
    chapter_number: int,
    chapter_outline: dict[str, Any],
    chapter_contract: dict[str, Any],
    relevant_entities: list[CanonicalEntityRef],
    forbidden_reveals: list[dict[str, Any]],
    user_intent: dict[str, Any],
) -> dict[str, Any]:
    story_foundation = _project_story_foundation(
        sources["story_foundation"].payload,
        chapter_contract=chapter_contract,
    )
    style_voice = _project_style_voice(
        sources["style_voice"].payload,
        relevant_entities=relevant_entities,
    )
    creative_direction = _project_creative_direction(
        sources["creative_direction"].payload,
        chapter_contract=chapter_contract,
    )
    narrative_contract = _project_narrative_contract(
        sources["narrative_contract"].payload,
        chapter_contract=chapter_contract,
    )
    entities = _runtime_entity_capsules(relevant_entities, style_voice)
    world = _runtime_world_capsule(story_foundation)
    world_rule_card = build_world_rule_card(
        rule_book=story_foundation.get("world_rule_book"),
        chapter_number=chapter_number,
        chapter_contract=chapter_contract,
        chapter_outline=chapter_outline,
        relevant_entities=relevant_entities,
    )
    style = _runtime_style_capsule(style_voice)
    narrative_capsule = _runtime_narrative_contract_capsule(narrative_contract)
    literary_contract = _build_literary_contract(
        chapter_number=chapter_number,
        chapter_outline=chapter_outline,
        chapter_contract=chapter_contract,
        story_foundation=sources["story_foundation"].payload,
        blueprint=sources["blueprint"].payload,
        narrative_contract=narrative_capsule,
        style=style,
        relevant_entities=entities,
        forbidden_reveals=forbidden_reveals,
    )
    contract = _runtime_chapter_contract(
        chapter_contract,
        chapter_outline=chapter_outline,
        relevant_entities=entities,
        forbidden_reveals=forbidden_reveals,
        literary_contract=literary_contract,
    )
    from novel_forge.core.guidance import project_guidance_requirements

    contract["guidance_requirements"] = project_guidance_requirements(
        chapter_contract,
        world_rules=literary_contract["setting"]["world_rules"],
        chapter_number=chapter_number,
    )
    return {
        "schema": "runtime_capsule_v1",
        "chapter_number": chapter_number,
        "chapter_contract": contract,
        "world": world,
        "world_rule_card": world_rule_card.model_dump(mode="json"),
        "entities": entities,
        "style": style,
        "creative_direction": _runtime_creative_capsule(creative_direction),
        "narrative_contract": narrative_capsule,
        "user_intent": user_intent,
    }


def _build_audit_capsule_payload(
    *,
    sources: dict[str, ArtifactEnvelope],
    chapter_number: int,
    chapter_outline: dict[str, Any],
    chapter_contract: dict[str, Any],
    relevant_entities: list[CanonicalEntityRef],
    forbidden_reveals: list[dict[str, Any]],
) -> dict[str, Any]:
    narrative_contract = _project_narrative_contract(
        sources["narrative_contract"].payload,
        chapter_contract=chapter_contract,
    )
    return {
        "schema": "audit_capsule_v1",
        "chapter_number": chapter_number,
        "dimension_refs": {
            "continuity": ["chapter_contract", "forbidden_reveal_boundaries"],
            "causal": ["chapter_outline", "required_progressions", "completion_criteria"],
            "character": ["entity_refs", "knowledge_boundaries"],
            "promise": ["promise_plan_refs", "future_leak_risks"],
            "motif": ["creative_direction.signature_motifs"],
            "reading_power": ["chapter_outline", "completion_criteria"],
            "literary_contract": ["runtime.chapter_contract.literary_contract"],
        },
        "chapter_evidence_anchor": {
            "chapter_number": chapter_number,
            "title": chapter_outline.get("title", ""),
            "required_progressions": _string_list(
                chapter_contract.get("required_progressions", [])
            ),
            "required_events": _string_list(chapter_contract.get("required_events", [])),
            "completion_criteria": _string_list(chapter_contract.get("completion_criteria", [])),
            "forbidden_reveal_boundaries": forbidden_reveals,
        },
        "entity_refs": [ref.model_dump(mode="json") for ref in relevant_entities],
        "promise_refs": narrative_contract.get("promise_plan_refs", []),
        "claim_refs": list(chapter_contract.get("cognitive_constraints", []) or []),
        "source_refs": [
            {
                "artifact_type": artifact_type,
                "artifact_id": artifact.artifact_id,
            }
            for artifact_type, artifact in sources.items()
        ],
    }


def _runtime_capsule_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    runtime = payload.get("runtime")
    if isinstance(runtime, dict) and runtime:
        return runtime
    raise ValueError("ChapterSourceSlice 缺少 runtime_capsule_v1，必须重建该章的 source slice。")


def _audit_capsule_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    audit = payload.get("audit")
    if isinstance(audit, dict) and audit:
        return audit
    raise ValueError("ChapterSourceSlice 缺少 audit_capsule_v1，必须重建该章的 source slice。")


def _runtime_chapter_contract(
    contract: dict[str, Any],
    *,
    chapter_outline: dict[str, Any],
    relevant_entities: list[Any],
    forbidden_reveals: list[dict[str, Any]],
    literary_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required_progressions = _merge_contract_lists(
        contract.get("required_progressions", []),
        contract.get("required_events", []),
        limit=None,
    )
    required_events = _string_list(contract.get("required_events", []))
    forbidden_changes = _string_list(contract.get("forbidden_changes", []))
    forbidden_progressions = _string_list(contract.get("forbidden_progressions", []))
    future_leak_risks = _string_list(contract.get("future_leak_risks", []))
    p0_forbidden = _forbidden_boundary_capsule(
        forbidden_reveals,
        forbidden_changes=forbidden_changes,
        forbidden_progressions=forbidden_progressions,
        future_leak_risks=future_leak_risks,
    )
    knowledge_boundaries = list(
        contract.get("cognitive_constraints") or contract.get("knowledge_ops") or []
    )
    return {
        "schema": "RuntimeChapterContract",
        "chapter_number": _chapter_number(contract)
        or _safe_int(chapter_outline.get("chapter_number")),
        "title": contract.get("title") or chapter_outline.get("title") or "",
        "entry_state_requirements": _string_list(contract.get("entry_state_requirements", [])),
        "required_events": required_events,
        "required_progressions": required_progressions,
        "p0_required_progressions": required_progressions,
        "allowed_progressions": _string_list(contract.get("allowed_progressions", [])),
        "forbidden_changes": forbidden_changes,
        "forbidden_progressions": forbidden_progressions,
        "future_leak_risks": future_leak_risks,
        "p0_forbidden_boundaries": p0_forbidden,
        "exit_state_targets": _string_list(contract.get("exit_state_targets", [])),
        "completion_criteria": (
            _string_list(contract.get("completion_criteria", [])) or required_progressions
        ),
        "knowledge_boundaries": knowledge_boundaries,
        "cognitive_constraints": knowledge_boundaries,
        "entity_refs": relevant_entities,
        "pov_character_id": contract.get("pov_character_id")
        or chapter_outline.get("pov_character_id")
        or "",
        "pov_character": contract.get("pov_character_name")
        or chapter_outline.get("pov_character_name")
        or chapter_outline.get("pov_character")
        or "",
        "involved_character_ids": list(contract.get("involved_character_ids", []) or []),
        "required_character_ids": list(contract.get("required_character_ids", []) or []),
        "literary_contract": literary_contract or {},
        "provenance": {
            "required_progressions": [
                "chapter_contract.required_progressions",
                "chapter_contract.required_events",
            ],
            "forbidden_boundaries": [
                "chapter_contract.forbidden_changes",
                "chapter_contract.forbidden_progressions",
                "chapter_contract.future_leak_risks",
            ],
            "knowledge_boundaries": [
                "chapter_contract.cognitive_constraints",
                "chapter_contract.knowledge_ops",
            ],
        },
    }


def _build_literary_contract(
    *,
    chapter_number: int,
    chapter_outline: dict[str, Any],
    chapter_contract: dict[str, Any],
    story_foundation: dict[str, Any],
    blueprint: dict[str, Any],
    narrative_contract: dict[str, Any],
    style: dict[str, Any],
    relevant_entities: list[Any],
    forbidden_reveals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the chapter-scoped six-element contract consumed before drafting."""

    entity_by_id, name_by_id = _literary_entity_indexes(relevant_entities)
    pov_name = (
        str(chapter_contract.get("pov_character_name") or "").strip()
        or str(chapter_outline.get("pov_character_name") or "").strip()
        or str(chapter_outline.get("pov_character") or "").strip()
    )
    required_ids = _string_list(chapter_contract.get("required_character_ids", []))
    support_ids = _string_list(chapter_contract.get("support_character_ids", []))
    involved_names = _string_list(
        chapter_contract.get("involved_characters", [])
        or chapter_outline.get("involved_characters", [])
    )
    required_names = _names_for_entity_ids(required_ids, name_by_id) or involved_names
    support_names = _names_for_entity_ids(support_ids, name_by_id)

    themes = _string_list(
        story_foundation.get("themes", [])
        or story_foundation.get("theme", [])
        or chapter_contract.get("themes", [])
    )
    if not themes:
        spec_theme = story_foundation.get("central_theme") or story_foundation.get("premise_theme")
        if spec_theme:
            themes = [str(spec_theme)]

    character_arcs = list(blueprint.get("character_arcs") or [])
    narrative_phases = list(blueprint.get("narrative_phases") or [])
    active_arc_milestones = _active_literary_arc_milestones(
        character_arcs,
        chapter_number=chapter_number,
    )
    current_phase = _literary_current_phase(narrative_phases, chapter_number)
    primary_theme = themes[0] if themes else ""

    world_rules = _merge_contract_lists(
        story_foundation.get("rules", []),
        story_foundation.get("banned_intent_rules", []),
        story_foundation.get("dialogue_register_rules", []),
        limit=None,
    )
    scene_design_goals = _string_list(
        chapter_contract.get("scene_design_goals", [])
        or chapter_outline.get("scene_design_goals", [])
    )
    required_progressions = _merge_contract_lists(
        chapter_contract.get("required_progressions", []),
        chapter_contract.get("required_events", []),
        limit=None,
    )
    promise_refs = list(narrative_contract.get("promise_plan_refs") or [])

    return {
        "schema": "literary_contract_v1",
        "chapter_number": chapter_number,
        "character": {
            "pov_character": pov_name,
            "required_characters": required_names,
            "support_characters": support_names,
            "emotional_plan": chapter_contract.get("emotional_plan")
            or chapter_outline.get("emotional_plan")
            or {},
            "arc_milestones": active_arc_milestones,
            "relationship_duties": _merge_contract_lists(
                chapter_contract.get("relationship_evolution", []),
                chapter_contract.get("relationship_ops", []),
                chapter_contract.get("required_relationship_changes", []),
                limit=None,
            ),
        },
        "plot": {
            "required_events": _string_list(chapter_contract.get("required_events", [])),
            "required_progressions": required_progressions,
            "promise_refs": promise_refs,
            "forbidden_progressions": _string_list(
                chapter_contract.get("forbidden_progressions", [])
            ),
            "completion_criteria": _string_list(chapter_contract.get("completion_criteria", [])),
            "exit_state_targets": _string_list(chapter_contract.get("exit_state_targets", [])),
        },
        "setting": {
            "primary_location": chapter_contract.get("setting")
            or chapter_outline.get("setting")
            or "",
            "scene_design_goals": scene_design_goals,
            "world_rules": world_rules,
            "available_resources": _merge_contract_lists(
                chapter_contract.get("available_resources", []),
                chapter_contract.get("object_refs", []),
                chapter_contract.get("item_ops", []),
                limit=None,
            ),
            "constraints": _merge_contract_lists(
                chapter_contract.get("setting_constraints", []),
                chapter_contract.get("world_constraints", []),
                chapter_contract.get("guard_constraints", []),
                limit=None,
            ),
        },
        "pov": {
            "pov_character": pov_name,
            "pov_character_id": chapter_contract.get("pov_character_id")
            or chapter_outline.get("pov_character_id")
            or _entity_id_for_name(pov_name, entity_by_id),
            "pov_scope": chapter_contract.get("pov_scope")
            or chapter_outline.get("pov_scope")
            or "limited",
            "cognitive_constraints": list(chapter_contract.get("cognitive_constraints") or []),
            "forbidden_reveal_boundaries": list(forbidden_reveals),
        },
        "theme": {
            "primary_theme": primary_theme,
            "themes": themes,
            "phase_context": current_phase,
            "arc_milestones": active_arc_milestones,
            "theme_duties": _merge_contract_lists(
                chapter_contract.get("theme_duties", []),
                chapter_contract.get("thematic_progressions", []),
                limit=None,
            ),
        },
        "style": {
            "narrative_voice": style.get("narrative_voice", ""),
            "pov_distance": style.get("pov_distance", ""),
            "dialogue_ratio": style.get("dialogue_ratio", ""),
            "forbidden_phrases": list(style.get("forbidden_phrases", []) or []),
            "style_rules": list(style.get("style_rules", []) or []),
            "character_voices": list(style.get("character_voices", []) or []),
        },
    }


def _literary_entity_indexes(
    entities: list[Any],
) -> tuple[dict[str, str], dict[str, str]]:
    entity_by_name: dict[str, str] = {}
    name_by_id: dict[str, str] = {}
    for item in entities:
        if isinstance(item, dict):
            entity_id = str(item.get("entity_id") or "").strip()
            name = str(item.get("canonical_name") or item.get("name") or "").strip()
        else:
            entity_id = str(getattr(item, "entity_id", "") or "").strip()
            name = str(
                getattr(item, "canonical_name", "") or getattr(item, "name", "") or ""
            ).strip()
        if entity_id and name:
            name_by_id[entity_id] = name
            entity_by_name[name] = entity_id
    return entity_by_name, name_by_id


def _names_for_entity_ids(ids: list[str], name_by_id: dict[str, str]) -> list[str]:
    result: list[str] = []
    for raw in ids:
        key = str(raw or "").strip()
        if not key:
            continue
        result.append(name_by_id.get(key, key))
    return _dedupe(result)


def _entity_id_for_name(name: str, entity_by_name: dict[str, str]) -> str:
    return entity_by_name.get(str(name or "").strip(), "")


def _active_literary_arc_milestones(
    character_arcs: list[Any],
    *,
    chapter_number: int,
) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for arc in character_arcs:
        arc_map = _as_mapping(arc)
        character = str(arc_map.get("character") or arc_map.get("character_name") or "").strip()
        arc_summary = str(arc_map.get("arc_summary") or arc_map.get("summary") or "").strip()
        for milestone in list(arc_map.get("milestones") or []):
            item = _as_mapping(milestone)
            start = _safe_int(item.get("chapter_start") or item.get("start_chapter"))
            end = _safe_int(item.get("chapter_end") or item.get("end_chapter"))
            if start <= 0 and end <= 0:
                continue
            if start <= chapter_number <= max(start, end):
                active.append(
                    {
                        "character": character,
                        "arc_summary": arc_summary,
                        "milestone_description": str(
                            item.get("description") or item.get("milestone") or ""
                        ).strip(),
                        "chapter_range": [start, max(start, end)],
                    }
                )
    return _limit_list(active, 8)


def _literary_current_phase(
    narrative_phases: list[Any],
    chapter_number: int,
) -> dict[str, Any]:
    for phase in narrative_phases:
        item = _as_mapping(phase)
        start = _safe_int(item.get("chapter_start") or item.get("start_chapter"))
        end = _safe_int(item.get("chapter_end") or item.get("end_chapter"))
        if start <= chapter_number <= max(start, end):
            return {
                "phase_name": item.get("phase_name") or item.get("name") or "",
                "description": item.get("description") or "",
                "chapter_range": [start, max(start, end)],
            }
    return {}


def _runtime_world_capsule(payload: dict[str, Any]) -> dict[str, Any]:
    rules = _merge_contract_lists(
        payload.get("rules", []),
        payload.get("banned_intent_rules", []),
        payload.get("anachronism_blacklist", []),
        limit=None,
    )
    return {
        "schema": "RuntimeWorldCapsule",
        "era": payload.get("era", ""),
        "geography": payload.get("geography", ""),
        "rules": rules,
        "world_rules": rules,
        "time_convention": payload.get("time_convention", ""),
        "address_rules": _string_list(payload.get("address_rules", [])),
        "self_reference_rules": _string_list(payload.get("self_reference_rules", [])),
        "etiquette_rules": _string_list(payload.get("etiquette_rules", [])),
        "institution_terms": _string_list(payload.get("institution_terms", [])),
        "dialogue_register_rules": _string_list(payload.get("dialogue_register_rules", [])),
        "banned_intent_rules": _string_list(payload.get("banned_intent_rules", [])),
    }


def _runtime_entity_capsules(
    relevant_entities: list[CanonicalEntityRef],
    style_voice: dict[str, Any],
) -> list[dict[str, Any]]:
    voice_by_name = {
        str(item.get("name") or "").strip(): item
        for item in list(style_voice.get("character_voices") or [])
        if isinstance(item, dict)
    }
    entities: list[dict[str, Any]] = []
    for ref in relevant_entities:
        voice = voice_by_name.get(ref.canonical_name, {})
        entities.append(
            {
                "schema": "RuntimeEntityCapsule",
                "entity_id": ref.entity_id,
                "canonical_name": ref.canonical_name,
                "entity_type": ref.entity_type,
                "aliases": list(ref.aliases),
                "voice": voice.get("voice", ""),
                "dialogue_style": voice.get("dialogue_style", ""),
                "speech_patterns": list(voice.get("speech_patterns", []) or []),
            }
        )
    return entities


def _runtime_style_capsule(style_voice: dict[str, Any]) -> dict[str, Any]:
    style_profile = _as_mapping(style_voice.get("style_profile"))
    return {
        "schema": "RuntimeStyleCapsule",
        "narrative_voice": style_profile.get("narrative_voice", ""),
        "sentence_style": style_profile.get("sentence_style", ""),
        "dialogue_style": style_profile.get("dialogue_style", ""),
        "pov_distance": style_profile.get("pov_distance", ""),
        "dialogue_ratio": style_profile.get("dialogue_ratio", ""),
        "forbidden_phrases": _string_list(
            style_profile.get("forbidden_phrases", []) or style_profile.get("banned_phrases", [])
        ),
        "style_rules": _merge_contract_lists(
            style_profile.get("rules", []),
            style_profile.get("sentence_rules", []),
            style_profile.get("dialogue_register_rules", []),
            limit=None,
        ),
        "character_voices": list(style_voice.get("character_voices", []) or []),
    }


def _runtime_creative_capsule(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "RuntimeCreativeCapsule",
        "chapter_focus": _string_list(payload.get("chapter_focus", [])),
        "signature_motifs": list(payload.get("signature_motifs", []) or []),
        "relationship_tensions": list(payload.get("relationship_tensions", []) or []),
        "thematic_promises": list(payload.get("thematic_promises", []) or []),
    }


def _runtime_narrative_contract_capsule(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "RuntimeNarrativeContractCapsule",
        "continuity_protocol": payload.get("continuity_protocol", {}),
        "world_rules": _string_list(payload.get("world_rules", [])),
        "promise_plan_refs": list(payload.get("promise_plan_refs", []) or []),
        "knowledge_boundaries": list(payload.get("knowledge_boundaries", []) or []),
    }


def _forbidden_boundary_capsule(
    forbidden_reveals: list[dict[str, Any]],
    *,
    forbidden_changes: list[str],
    forbidden_progressions: list[str],
    future_leak_risks: list[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source, values in (
        ("future_leak_risks", future_leak_risks),
        ("forbidden_progressions", forbidden_progressions),
        ("forbidden_changes", forbidden_changes),
    ):
        for item in values:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append({"source": source, "rule": text})
    for reveal in forbidden_reveals:
        if not isinstance(reveal, dict):
            continue
        text = str(reveal.get("rule") or reveal.get("description") or reveal).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(reveal)
    return result


def _merge_contract_lists(*values: Any, limit: int | None) -> list[str]:
    merged = _string_list([item for value in values for item in _iter_items(value)])
    return merged if limit is None else _limit_list(merged, limit)


def _iter_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in _iter_items(value):
        if isinstance(item, dict):
            text = str(
                item.get("description")
                or item.get("summary")
                or item.get("rule")
                or item.get("claim_text")
                or item.get("name")
                or ""
            ).strip()
        else:
            text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _limit_list(value: Any, limit: int) -> list[Any]:
    items = list(value) if isinstance(value, (list, tuple, set)) else _iter_items(value)
    if limit < 0:
        return items
    return items[:limit]


def load_stage_artifact(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    chapter_number: int,
    artifact_type: str,
) -> StageArtifact | None:
    """Load a persisted chapter-stage artifact if it exists."""

    path = _stage_artifact_path(layout, chapter_number, artifact_type)
    if path is None or not hasattr(storage, "exists") or not hasattr(storage, "load_json"):
        return None
    if not storage.exists(path):
        return None
    return StageArtifact.model_validate(storage.load_json(path))


def persist_stage_artifact(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    project_id: str,
    chapter_number: int,
    artifact_type: str,
    payload: dict[str, Any],
    chapter_source_slice: Any | None = None,
    previous_artifact: Any | None = None,
    event_ledger: list[dict[str, Any]] | None = None,
    forbidden_reveal_boundaries: list[dict[str, Any]] | None = None,
    prompt_fingerprint: str = "legacy_unknown",
    config_fingerprint: str = "legacy_unknown",
    model_fingerprint: str = "legacy_unknown",
    execution_quality_status: str = "actual",
    degradation_reason: str = "",
    derivation_status: str = "fresh",
    reuse_policy: str | None = None,
    run_attempt_id: str = "",
) -> StageArtifact:
    """Persist a signed stage artifact after validating its parent handoff."""

    source_artifact_ids: list[str] = []
    source_hashes: dict[str, str] = {}
    canonical_refs: list[CanonicalEntityRef] = []
    if chapter_source_slice is not None:
        source_slice = _coerce_slice(chapter_source_slice)
        source_artifact_ids.append(source_slice.artifact_id)
        source_hashes[source_slice.artifact_id] = hash_payload(source_slice.model_dump(mode="json"))
        canonical_refs = list(source_slice.canonical_entity_refs)
        if forbidden_reveal_boundaries is None:
            forbidden_reveal_boundaries = list(
                source_slice.payload.get("forbidden_reveal_boundaries", []) or []
            )

    previous_artifact_id = ""
    previous_artifact_type = ""
    parent_artifact_versions: dict[str, int] = {}
    if previous_artifact is not None:
        previous = _coerce_stage_artifact(previous_artifact)
        _validate_previous_stage_handoff(
            previous,
            project_id=project_id or "unknown",
            chapter_number=chapter_number,
        )
        previous_artifact_id = previous.artifact_id
        previous_artifact_type = previous.artifact_type
        if previous_artifact_id not in source_artifact_ids:
            source_artifact_ids.append(previous_artifact_id)
        source_hashes[previous_artifact_id] = hash_payload(previous.model_dump(mode="json"))
        parent_artifact_versions[previous_artifact_id] = previous.output_version

    from novel_forge.control_plane.registry import get_stage_definition
    from novel_forge.control_plane.stage_recorder import stage_name_for_artifact_type

    stage_name = stage_name_for_artifact_type(artifact_type)
    stage_definition = get_stage_definition(stage_name) if stage_name else None
    if (
        stage_definition is not None
        and previous_artifact_type
        and not stage_definition.can_read_artifact(previous_artifact_type)
    ):
        raise ValueError(
            f"stage {stage_name} cannot consume parent artifact {previous_artifact_type}"
        )
    workflow_version = (
        stage_definition.workflow_version if stage_definition is not None else "novel.chapter.v2"
    )
    artifact_schema_version = (
        stage_definition.artifact_schema_version if stage_definition is not None else 2
    )
    effective_reuse_policy = reuse_policy or (
        stage_definition.reuse_policy if stage_definition is not None else "same_run_resume"
    )
    source_text_hash = _stage_source_text_hash(payload)
    signature_source_text_hash = str(payload.get("source_text_hash", "") or "").strip()
    if (
        not signature_source_text_hash
        and stage_definition is not None
        and stage_definition.editable_text_window == "none"
    ):
        signature_source_text_hash = source_text_hash
    effective_run_attempt_id = run_attempt_id or _active_run_attempt_id()
    input_signature = hash_payload(
        {
            "workflow_version": workflow_version,
            "artifact_schema_version": artifact_schema_version,
            "stage": stage_name or artifact_type,
            "artifact_type": artifact_type,
            "project_id": project_id or "unknown",
            "chapter_number": chapter_number,
            "source_hashes": source_hashes,
            "source_text_hash": signature_source_text_hash,
            "prompt_fingerprint": prompt_fingerprint,
            "config_fingerprint": config_fingerprint,
            "model_fingerprint": model_fingerprint,
        }
    )
    output_hash = hash_payload(payload)
    existing = load_stage_artifact(
        storage,
        layout,
        chapter_number=chapter_number,
        artifact_type=artifact_type,
    )
    output_version = 1
    if existing is not None:
        output_version = max(1, existing.output_version)
        if existing.output_hash != output_hash or existing.input_signature != input_signature:
            output_version += 1

    artifact = StageArtifact(
        artifact_type=artifact_type,
        project_id=project_id or "unknown",
        artifact_id=f"{artifact_type}:{chapter_number:03d}",
        scope=ArtifactScope(kind="chapter", ids=[str(chapter_number)]),
        source_artifact_ids=source_artifact_ids,
        source_hashes=source_hashes,
        canonical_entity_refs=canonical_refs,
        payload=payload,
        previous_artifact_id=previous_artifact_id,
        workflow_version=workflow_version,
        artifact_schema_version=artifact_schema_version,
        input_signature=input_signature,
        output_hash=output_hash,
        output_version=output_version,
        parent_artifact_versions=parent_artifact_versions,
        source_text_hash=source_text_hash,
        prompt_fingerprint=prompt_fingerprint,
        config_fingerprint=config_fingerprint,
        model_fingerprint=model_fingerprint,
        execution_quality_status=execution_quality_status,
        degradation_reason=degradation_reason,
        derivation_status=derivation_status,
        reuse_policy=effective_reuse_policy,
        run_attempt_id=effective_run_attempt_id,
        event_ledger=event_ledger or [],
        forbidden_reveal_boundaries=forbidden_reveal_boundaries or [],
    )
    path = _stage_artifact_path(layout, chapter_number, artifact_type)
    if path is not None and hasattr(storage, "save_json"):
        storage.save_json(path, artifact.model_dump(mode="json"))
    _record_control_plane_stage_artifact(
        artifact=artifact,
        artifact_path=path,
        chapter_number=chapter_number,
        artifact_type=artifact_type,
        source_has_slice=chapter_source_slice is not None,
        previous_artifact_type=previous_artifact_type,
    )
    return artifact


def _stage_source_text_hash(payload: dict[str, Any]) -> str:
    """Extract the authoritative prose hash carried by a stage payload."""

    for key in ("source_text_hash", "text_hash", "final_text_hash"):
        value = str(payload.get(key, "") or "").strip()
        if value:
            return value
    reports = payload.get("reports")
    if isinstance(reports, dict):
        for report in reports.values():
            if not isinstance(report, dict):
                continue
            value = str(report.get("source_text_hash", "") or "").strip()
            if value:
                return value
    return ""


def _active_run_attempt_id() -> str:
    try:
        from novel_forge.control_plane.context import get_current_execution_context

        context = get_current_execution_context()
        return str(getattr(context, "run_attempt_id", "") or "")
    except Exception:
        return ""


def _validate_previous_stage_handoff(
    previous: StageArtifact,
    *,
    project_id: str,
    chapter_number: int,
) -> None:
    """Reject cross-project, cross-chapter, stale, or corrupted parents."""

    if previous.project_id not in {project_id, "unknown"} and project_id != "unknown":
        raise ValueError(
            f"stage parent project mismatch: expected {project_id}, got {previous.project_id}"
        )
    if previous.scope.kind != "chapter" or str(chapter_number) not in previous.scope.ids:
        raise ValueError(
            "stage parent chapter mismatch: "
            f"expected chapter {chapter_number}, got {previous.scope.kind}:{previous.scope.ids}"
        )
    if previous.quality_status == "fail" or previous.execution_quality_status == "blocked":
        raise ValueError(f"blocked stage parent cannot be consumed: {previous.artifact_id}")
    if previous.derivation_status in {"stale", "conflict", "blocked"}:
        raise ValueError(
            f"non-fresh stage parent cannot be consumed: "
            f"{previous.artifact_id} ({previous.derivation_status})"
        )
    if previous.output_hash != "legacy_unknown":
        actual_hash = hash_payload(previous.payload)
        if previous.output_hash != actual_hash:
            raise ValueError(f"stage parent output hash mismatch: {previous.artifact_id}")


def _record_control_plane_stage_artifact(
    *,
    artifact: StageArtifact,
    artifact_path: Any | None,
    chapter_number: int,
    artifact_type: str,
    source_has_slice: bool,
    previous_artifact_type: str,
) -> None:
    """Mirror a persisted stage artifact into the active control-plane attempt.

    The project filesystem remains canonical.  This hook only records the
    artifact's hash, source lineage, and the completed stage execution after
    the normal atomic persistence succeeded.  It intentionally does nothing
    outside a JobService-bound execution context, preventing ad-hoc utility
    calls and unit tests from creating unowned ledger rows.

    All control-plane recording is best-effort: if the control plane is
    disabled, the execution context is absent, or any recording step fails,
    the already-persisted filesystem artifact is unaffected.  Harness
    violations in enforce mode are logged but never propagated, so a
    permission violation cannot corrupt the artifact that was just written.
    """

    try:
        from novel_forge.control_plane.context import get_current_execution_context
        from novel_forge.control_plane.harness import StageHarness
        from novel_forge.control_plane.plane import get_cached_control_plane
        from novel_forge.control_plane.stage_recorder import (
            hash_json as control_plane_hash_json,
        )
        from novel_forge.control_plane.stage_recorder import (
            stage_name_for_artifact_type,
        )

        execution_context = get_current_execution_context()
        if execution_context is None or not execution_context.run_attempt_id:
            return
        stage_name = stage_name_for_artifact_type(artifact_type)
        if stage_name is None:
            return

        control_plane = get_cached_control_plane()
        if control_plane is None or not control_plane.enabled:
            return
        recorder = control_plane.recorder

        # Harness checks operate on the typed source/previous artifacts,
        # never raw project payloads.  Violations are logged (advisory) or
        # raised (enforce), but we catch them here so the already-persisted
        # artifact is not left in an inconsistent state.
        harness = StageHarness(stage_name)
        if source_has_slice:
            harness.check_input_artifact("chapter_source_slice")
        if previous_artifact_type:
            harness.check_input_artifact(previous_artifact_type)
        harness.check_output_proposal(artifact_type)

        artifact_payload = artifact.model_dump(mode="json")
        output_hash = control_plane_hash_json(artifact_payload)
        idempotency_key = f"{stage_name}:{chapter_number}:{artifact.input_signature[:24]}"
        stage_execution_id = recorder.begin_stage(
            execution_context.run_attempt_id,
            stage_name,
            task_type=artifact_type,
            input_artifact_hashes=artifact.source_hashes,
            idempotency_key=idempotency_key,
        )
        recorder.record_stage_artifact(
            project_id=artifact.project_id,
            chapter_number=chapter_number,
            artifact_type=artifact_type,
            artifact_payload=artifact_payload,
            source_hashes=artifact.source_hashes,
            previous_artifact_id=artifact.previous_artifact_id,
            content_path=str(artifact_path or ""),
            run_attempt_id=execution_context.run_attempt_id,
            stage_execution_id=stage_execution_id,
        )
        recorder.end_stage(
            stage_execution_id,
            output_artifact_hash=output_hash,
            committed_version=artifact.output_version,
        )
    except Exception:
        _log_control_plane.warning(
            "Control-plane stage artifact recording failed for %s (non-fatal)",
            artifact_type,
            exc_info=True,
        )


def _stage_artifact_path(
    layout: Any,
    chapter_number: int,
    artifact_type: str,
) -> Any | None:
    path_fn = getattr(layout, "chapter_artifact_path", None)
    if not callable(path_fn):
        return None
    return path_fn(chapter_number, artifact_type)


def _build_source_artifact(
    *,
    artifact_type: str,
    project_id: str,
    payload: dict[str, Any],
    canonical_refs: list[CanonicalEntityRef],
    blocking_issues: list[ArtifactIssue],
) -> ArtifactEnvelope:
    model: Any = _ARTIFACT_MODEL_BY_TYPE[artifact_type]
    artifact = model(
        project_id=project_id,
        artifact_id=artifact_type,
        scope=ArtifactScope(kind="project", ids=[project_id]),
        canonical_entity_refs=canonical_refs,
        quality_status="fail" if blocking_issues else "pass",
        blocking_issues=blocking_issues,
        payload=payload,
    )
    return cast(ArtifactEnvelope, artifact)


def _load_source_artifact(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    artifact_type: str,
) -> ArtifactEnvelope:
    path = layout.source_artifact_path(artifact_type)
    if not storage.exists(path):
        raise ValueError(f"缺少初始化源头 artifact：source_artifacts/{artifact_type}.json")
    model = _ARTIFACT_MODEL_BY_TYPE[artifact_type]
    return model.model_validate(storage.load_json(path))


def _coerce_slice(value: Any) -> ChapterSourceSliceArtifact:
    if isinstance(value, ChapterSourceSliceArtifact):
        return value
    if isinstance(value, dict):
        return ChapterSourceSliceArtifact.model_validate(value)
    if hasattr(value, "model_dump"):
        return ChapterSourceSliceArtifact.model_validate(value.model_dump(mode="json"))
    raise TypeError("chapter_source_slice must be a ChapterSourceSliceArtifact")


def _coerce_stage_artifact(value: Any) -> StageArtifact:
    if isinstance(value, StageArtifact):
        return value
    if isinstance(value, dict):
        return StageArtifact.model_validate(value)
    if hasattr(value, "model_dump"):
        return StageArtifact.model_validate(value.model_dump(mode="json"))
    raise TypeError("previous_artifact must be a StageArtifact")


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dump(item) for item in value]
    return value


def _project_spec_payload(spec: Any) -> dict[str, Any]:
    payload = _as_mapping(_dump(spec))
    return {
        key: payload.get(key)
        for key in (
            "title",
            "genre",
            "theme",
            "tone",
            "length_target",
            "language",
            "characters_hint",
            "world_hint",
            "conflict_hint",
            "pov_hint",
            "opening_style",
            "ending_style",
            "extra_instructions",
            "narrative_complexity",
        )
        if key in payload
    }


def _story_foundation_payload(story_bible: Any) -> dict[str, Any]:
    payload = _as_mapping(_dump(story_bible))
    keys = (
        "title",
        "premise",
        "era",
        "geography",
        "culture",
        "magic_or_tech",
        "rules",
        "world_rule_book",
        "tone",
        "themes",
        "banned_intent_rules",
        "time_convention",
        "address_rules",
        "self_reference_rules",
        "etiquette_rules",
        "institution_terms",
        "material_culture",
        "anachronism_blacklist",
        "dialogue_register_rules",
        "max_key_revelations_per_chapter",
        "min_unresolved_threads_to_keep",
    )
    return {key: payload.get(key) for key in keys if key in payload}


def _character_system_payload(character_bible: Any, character_system: Any) -> dict[str, Any]:
    system = _as_mapping(_dump(character_system))
    bible = _as_mapping(_dump(character_bible))
    characters = bible.get("characters", [])
    voices = []
    if isinstance(characters, list):
        for item in characters:
            if not isinstance(item, dict):
                continue
            voices.append(
                {
                    "name": item.get("name", ""),
                    "character_id": item.get("character_id", ""),
                    "role": item.get("role", ""),
                    "voice": item.get("voice", ""),
                    "dialogue_style": item.get("dialogue_style", ""),
                    "knowledge_boundaries": item.get("knowledge_boundaries", {}),
                }
            )
    return {
        "roster": system.get("roster", []),
        "relationship_edges": system.get("relationship_edges", []),
        "identity_links": system.get("identity_links", []),
        "audit": system.get("audit", []),
        "voices": voices,
    }


def _entity_graph_payload(entity_graph: Any) -> dict[str, Any]:
    payload = _as_mapping(_dump(entity_graph))
    return {
        "entities": payload.get("entities", []),
        "entity_links": payload.get("entity_links", []),
        "audit": payload.get("audit", []),
    }


def _style_voice_payload(style_profile: Any, character_bible: Any) -> dict[str, Any]:
    style = _as_mapping(_dump(style_profile))
    bible = _as_mapping(_dump(character_bible))
    voices: list[dict[str, Any]] = []
    for item in bible.get("characters", []) if isinstance(bible.get("characters"), list) else []:
        if not isinstance(item, dict):
            continue
        voices.append(
            {
                "name": item.get("name", ""),
                "character_id": item.get("character_id", ""),
                "voice": item.get("voice", ""),
                "dialogue_style": item.get("dialogue_style", ""),
                "speech_patterns": item.get("speech_patterns", []),
            }
        )
    return {
        "style_profile": style,
        "character_voices": voices,
    }


def _effective_narrative_contract(narrative_contract: Any) -> dict[str, Any]:
    payload = _as_mapping(_dump(narrative_contract))
    llm_contract = payload.get("llm_contract")
    return _as_mapping(llm_contract) if isinstance(llm_contract, dict) else payload


def _chapter_contract_index_payload(
    chapter_contracts: Any,
    *,
    outline: Any,
    canonical_refs: list[CanonicalEntityRef],
) -> dict[str, Any]:
    payload = _as_mapping(_dump(chapter_contracts))
    items = payload.get("chapter_contracts", [])
    by_chapter: dict[str, dict[str, Any]] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            number = _chapter_number(item)
            if number <= 0:
                continue
            by_chapter[str(number)] = _normalize_contract_refs(item, canonical_refs)
    outline_payload = _as_mapping(_dump(outline))
    coverage = dict(_as_mapping(payload.get("coverage")))
    if not by_chapter:
        by_chapter = _fallback_contracts_from_outline(outline_payload, canonical_refs)
        coverage["outline_fallback_generated"] = True
    return {
        "by_chapter": by_chapter,
        "coverage": coverage,
        "total_chapters": outline_payload.get("total_chapters") or len(by_chapter),
    }


def _fallback_contracts_from_outline(
    outline_payload: dict[str, Any],
    canonical_refs: list[CanonicalEntityRef],
) -> dict[str, dict[str, Any]]:
    chapters = outline_payload.get("chapters", [])
    if not isinstance(chapters, list):
        return {}
    by_chapter: dict[str, dict[str, Any]] = {}
    for item in chapters:
        if not isinstance(item, dict):
            continue
        number = _chapter_number(item)
        if number <= 0:
            continue
        goal = str(item.get("goal") or "").strip()
        main_points = item.get("main_plot_points") if isinstance(item, dict) else []
        if not isinstance(main_points, list):
            main_points = []
        exit_target = str(item.get("ending_hook") or item.get("notes") or goal).strip()
        contract = {
            "chapter_number": number,
            "title": item.get("title") or f"第{number}章",
            "pov_character_id": item.get("pov_character_id", ""),
            "pov_character_name": item.get("pov_character_name", item.get("pov_character", "")),
            "involved_character_ids": item.get("involved_character_ids", []),
            "required_character_ids": item.get("required_character_ids", []),
            "support_character_ids": item.get("support_character_ids", []),
            "involved_character_names": item.get(
                "involved_character_names",
                item.get("involved_characters", []),
            ),
            "cast_plan": item.get("cast_plan", {}),
            "emotional_plan": item.get("emotional_plan", {}),
            "scene_design_goals": item.get("scene_design_goals", []),
            "entry_state_requirements": [],
            "required_events": [goal] if goal else [],
            "allowed_changes": [],
            "forbidden_changes": [],
            "promise_ops": [],
            "relationship_ops": [],
            "item_ops": [],
            "knowledge_ops": [],
            "cognitive_constraints": [],
            "new_character_candidates": [],
            "exit_state_targets": [exit_target] if exit_target else [],
            "required_progressions": [str(point) for point in main_points if str(point).strip()],
            "allowed_progressions": [],
            "forbidden_progressions": [],
            "completion_criteria": [goal] if goal else [],
            "future_leak_risks": [],
            "source": "outline_fallback",
        }
        by_chapter[str(number)] = _normalize_contract_refs(contract, canonical_refs)
    return by_chapter


def _normalize_contract_refs(
    contract: dict[str, Any],
    canonical_refs: list[CanonicalEntityRef],
) -> dict[str, Any]:
    normalized = dict(contract)
    lookup = _entity_lookup(canonical_refs)
    names = _relevant_names({}, contract)
    names.extend(_registered_knowledge_op_fact_names(contract, lookup))
    normalized["canonical_entity_ids"] = [
        lookup[name].entity_id for name in _dedupe(names) if name in lookup
    ]
    return normalized


def _canonical_entity_refs(
    entity_graph: Any,
    character_bible: Any,
    *,
    chapter_contracts: Any | None = None,
) -> list[CanonicalEntityRef]:
    graph = _as_mapping(_dump(entity_graph))
    entities = graph.get("entities", [])
    refs: list[CanonicalEntityRef] = []
    if isinstance(entities, list):
        for item in entities:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not entity_id or not name:
                continue
            refs.append(
                CanonicalEntityRef(
                    entity_id=entity_id,
                    canonical_name=name,
                    entity_type=str(item.get("entity_type") or "unknown"),
                    aliases=item.get("aliases", []),
                )
            )

    bible = _as_mapping(_dump(character_bible))
    for item in bible.get("characters", []) if isinstance(bible.get("characters"), list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if _ref_exists(refs, name=name, entity_id=str(item.get("character_id") or "")):
            continue
        refs.append(
            CanonicalEntityRef(
                entity_id=str(item.get("character_id") or f"char_{name}"),
                canonical_name=name,
                entity_type="character",
                aliases=item.get("aliases", []),
            )
        )

    refs.extend(_chapter_contract_candidate_refs(chapter_contracts))
    refs.extend(_chapter_contract_referenced_world_entity_refs(chapter_contracts, refs))
    return _dedupe_canonical_refs(_augment_character_aliases(refs))


_CHAPTER_CONTRACT_NEW_ENTITY_FIELDS: dict[str, str] = {
    "new_character_candidates": "character",
    "new_entity_candidates": "unknown",
    "new_group_candidates": "organization",
    "new_collective_candidates": "organization",
    "new_organization_candidates": "organization",
    "new_location_candidates": "location",
    "new_item_candidates": "item",
    "new_concept_candidates": "concept",
}

_ENTITY_ID_PREFIX_BY_TYPE: dict[str, str] = {
    "character": "char",
    "location": "loc",
    "item": "item",
    "organization": "org",
    "group": "org",
    "concept": "conc",
}


def _chapter_contract_candidate_refs(chapter_contracts: Any | None) -> list[CanonicalEntityRef]:
    refs: list[CanonicalEntityRef] = []
    for contract in _chapter_contract_items(chapter_contracts):
        for field, default_type in _CHAPTER_CONTRACT_NEW_ENTITY_FIELDS.items():
            for candidate in _candidate_items(contract.get(field)):
                ref = _candidate_ref(candidate, default_type=default_type)
                if ref is not None:
                    refs.append(ref)
    return refs


def _chapter_contract_items(chapter_contracts: Any | None) -> list[dict[str, Any]]:
    payload = _as_mapping(_dump(chapter_contracts))
    contracts = payload.get("chapter_contracts", [])
    if isinstance(contracts, list):
        return [contract for contract in contracts if isinstance(contract, dict)]
    by_chapter = payload.get("by_chapter")
    if isinstance(by_chapter, dict):
        return [contract for contract in by_chapter.values() if isinstance(contract, dict)]
    return []


def _candidate_items(value: Any) -> list[dict[str, Any]]:
    values = value if isinstance(value, list) else ([] if value is None else [value])
    result: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str) and item.strip():
            result.append({"name": item.strip()})
    return result


def _candidate_ref(candidate: dict[str, Any], *, default_type: str) -> CanonicalEntityRef | None:
    name = str(
        candidate.get("canonical_name")
        or candidate.get("character_name")
        or candidate.get("entity_name")
        or candidate.get("group_name")
        or candidate.get("organization_name")
        or candidate.get("location_name")
        or candidate.get("item_name")
        or candidate.get("concept_name")
        or candidate.get("name")
        or ""
    ).strip()
    if not name:
        return None
    entity_type = str(candidate.get("entity_type") or default_type or "unknown").strip()
    aliases = candidate.get("aliases") or candidate.get("alias") or []
    entity_id = str(
        candidate.get("entity_id")
        or candidate.get("character_id")
        or candidate.get("organization_id")
        or candidate.get("location_id")
        or candidate.get("item_id")
        or candidate.get("concept_id")
        or ""
    ).strip()
    return CanonicalEntityRef(
        entity_id=entity_id or _derived_entity_id(name, entity_type),
        canonical_name=name,
        entity_type=entity_type,
        aliases=aliases,
    )


def _chapter_contract_referenced_world_entity_refs(
    chapter_contracts: Any | None,
    existing_refs: list[CanonicalEntityRef],
) -> list[CanonicalEntityRef]:
    lookup = _entity_lookup(existing_refs)
    refs: list[CanonicalEntityRef] = []
    for contract in _chapter_contract_items(chapter_contracts):
        _collect_referenced_world_entity_refs(
            contract,
            refs,
            lookup=lookup,
            field_path=(),
        )
        if refs:
            lookup = _entity_lookup([*existing_refs, *refs])
    return refs


def _collect_referenced_world_entity_refs(
    value: Any,
    refs: list[CanonicalEntityRef],
    *,
    lookup: dict[str, CanonicalEntityRef],
    field_path: tuple[str, ...],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = (*field_path, str(key))
            if is_chapter_contract_entity_ref_map_path(child_path):
                continue
            if is_chapter_contract_entity_ref_path(child_path):
                for token in _entity_reference_tokens(item):
                    ref = _world_entity_ref_from_token(token, child_path, lookup)
                    if ref is not None and not _ref_exists(
                        refs,
                        name=ref.canonical_name,
                        entity_id=ref.entity_id,
                    ):
                        refs.append(ref)
                continue
            _collect_referenced_world_entity_refs(
                item,
                refs,
                lookup=lookup,
                field_path=child_path,
            )
        return
    if isinstance(value, list):
        for item in value:
            _collect_referenced_world_entity_refs(
                item,
                refs,
                lookup=lookup,
                field_path=field_path,
            )


def _entity_reference_tokens(value: Any) -> list[str]:
    tokens: list[str] = []
    _collect_name_value(value, tokens)
    return _dedupe([token for token in tokens if _looks_like_name(token)])


def _world_entity_ref_from_token(
    token: str,
    field_path: tuple[str, ...],
    lookup: dict[str, CanonicalEntityRef],
) -> CanonicalEntityRef | None:
    name = _clean_name_token(token)
    if not name or name in lookup or _is_unknown_entity_placeholder(name):
        return None
    # Handle hash-based derived entity IDs (e.g. ``item_a084157bbf75``).
    # These are produced by chapter-contract generation for new entities
    # not present in the initial entity graph.  Auto-register them so
    # validation can resolve the reference without a repair cycle.
    inferred_type = _entity_type_from_id(name)
    if inferred_type and inferred_type != "character":
        return CanonicalEntityRef(
            entity_id=name,
            canonical_name=name,
            entity_type=inferred_type,
            aliases=[],
        )
    # Human-readable names without an explicit registry record or model-authored
    # candidate are intentionally left unresolved.  The source-artifact repair
    # loop sends them to RECONCILE_ENTITIES with chapter evidence; suffixes such
    # as "国" or "阁" are not reliable narrative type decisions.
    return None


def _infer_world_entity_type(name: str, field_path: tuple[str, ...]) -> str:
    """Infer entity type from name and field context.

    This helper exposes only structural certainty.  Human-readable CJK names
    remain ``unknown`` so the caller can delegate semantic classification to
    LLM-driven reconciliation.
    """
    if not _is_cjk_entity_name(name):
        return ""
    if _path_is_character_reference(field_path):
        return "character"
    return "unknown"


def _is_cjk_entity_name(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text and re.search(r"[\u4e00-\u9fff]", text) and not re.search(r"[A-Za-z]", text))


def _path_is_character_reference(field_path: tuple[str, ...]) -> bool:
    normalized = [part.strip().lower() for part in field_path if str(part or "").strip()]
    return any("character" in part for part in normalized) or any(
        part in {"pov_character", "involved_characters", "required_characters"}
        for part in normalized
    )


def _derived_entity_id(name: str, entity_type: str) -> str:
    normalized_type = str(entity_type or "").strip().lower()
    if normalized_type == "character":
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
        return f"char_{digest}"
    prefix = _ENTITY_ID_PREFIX_BY_TYPE.get(normalized_type, "ent")
    digest = hashlib.sha1(f"{entity_type}:{name}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _derived_character_id(name: str) -> str:
    return _derived_entity_id(name, "character")


_DERIVED_ENTITY_ID_RE = re.compile(r"^(char|item|loc|org|conc|ent)_([0-9a-f]{12})$")

# Reverse mapping from prefix to entity_type for _entity_type_from_id.
_ENTITY_TYPE_BY_PREFIX: dict[str, str] = {v: k for k, v in _ENTITY_ID_PREFIX_BY_TYPE.items()}
_ENTITY_TYPE_BY_PREFIX["ent"] = "unknown"


def _is_derived_entity_id(value: str) -> bool:
    """Return True when *value* looks like a hash-based derived entity id.

    Matches patterns produced by :func:`_derived_entity_id`:
    ``<prefix>_<12 hex chars>`` where *prefix* is one of the known entity-id
    prefixes (char, item, loc, org, conc, ent).
    """
    return bool(value and _DERIVED_ENTITY_ID_RE.match(value.strip()))


def _entity_type_from_id(value: str) -> str:
    """Infer entity type from a derived entity id prefix, or ``''``."""
    m = _DERIVED_ENTITY_ID_RE.match(value.strip()) if value else None
    if not m:
        return ""
    return _ENTITY_TYPE_BY_PREFIX.get(m.group(1), "")


def _ref_exists(
    refs: list[CanonicalEntityRef],
    *,
    name: str,
    entity_id: str = "",
) -> bool:
    for ref in refs:
        if entity_id and ref.entity_id == entity_id:
            return True
        if ref.canonical_name == name:
            return True
    return False


def _dedupe_canonical_refs(refs: list[CanonicalEntityRef]) -> list[CanonicalEntityRef]:
    by_key: dict[str, CanonicalEntityRef] = {}
    name_to_key: dict[str, str] = {}
    order: list[str] = []
    for ref in refs:
        key = name_to_key.get(ref.canonical_name) or ref.entity_id or ref.canonical_name
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = ref
            name_to_key[ref.canonical_name] = key
            order.append(key)
            continue
        aliases = _dedupe([*existing.aliases, *ref.aliases])
        by_key[key] = existing.model_copy(update={"aliases": aliases})
    return [by_key[key] for key in order]


_COMPOUND_SURNAMES: tuple[str, ...] = (
    "欧阳",
    "太史",
    "端木",
    "上官",
    "司马",
    "东方",
    "独孤",
    "南宫",
    "万俟",
    "闻人",
    "夏侯",
    "诸葛",
    "尉迟",
    "公羊",
    "赫连",
    "澹台",
    "皇甫",
    "宗政",
    "濮阳",
    "公冶",
    "太叔",
    "申屠",
    "公孙",
    "慕容",
    "仲孙",
    "钟离",
    "长孙",
    "宇文",
    "司徒",
    "鲜于",
    "司空",
    "闾丘",
    "子车",
    "亓官",
    "司寇",
    "巫马",
    "公西",
    "颛孙",
    "壤驷",
    "公良",
    "漆雕",
    "乐正",
    "宰父",
    "谷梁",
    "拓跋",
    "夹谷",
    "轩辕",
    "令狐",
    "段干",
    "百里",
    "呼延",
    "东郭",
    "南门",
    "羊舌",
    "微生",
    "公户",
    "公玉",
    "公仪",
    "梁丘",
    "公仲",
    "公上",
    "公门",
    "公山",
    "公坚",
    "左丘",
    "公伯",
    "西门",
    "公祖",
    "第五",
    "公乘",
    "贯丘",
    "公皙",
    "南荣",
    "东里",
    "东宫",
    "仲长",
    "子书",
    "子桑",
    "即墨",
    "达奚",
    "褚师",
)


def _augment_character_aliases(refs: list[CanonicalEntityRef]) -> list[CanonicalEntityRef]:
    canonical_names = {ref.canonical_name for ref in refs}
    alias_owner: dict[str, str] = {}
    result: list[CanonicalEntityRef] = []
    for ref in refs:
        aliases = _dedupe(
            [
                *ref.aliases,
                *_parenthetical_entity_aliases(ref),
                *_natural_character_aliases(ref),
            ]
        )
        safe_aliases: list[str] = []
        for alias in aliases:
            if alias == ref.canonical_name:
                continue
            existing_owner = alias_owner.get(alias)
            if existing_owner and existing_owner != ref.entity_id:
                continue
            if alias in canonical_names and alias != ref.canonical_name:
                continue
            alias_owner[alias] = ref.entity_id
            safe_aliases.append(alias)
        result.append(ref.model_copy(update={"aliases": safe_aliases}))
    return result


def _parenthetical_entity_aliases(ref: CanonicalEntityRef) -> list[str]:
    """Derive an exact display alias from ``名称（限定说明）`` forms.

    This is a structural alias, not a fuzzy identity guess.  Collision checks
    in ``_augment_character_aliases`` still prevent it from shadowing another
    canonical entity.
    """

    name = ref.canonical_name.strip()
    match = re.fullmatch(r"(.{2,16}?)[（(][^（）()]{2,}[）)]", name)
    if match is None:
        return []
    alias = match.group(1).strip()
    return [alias] if alias else []


def _natural_character_aliases(ref: CanonicalEntityRef) -> list[str]:
    if ref.entity_type != "character":
        return []
    name = ref.canonical_name.strip()
    if len(name) < 3 or len(name) > 4:
        return []
    if name.startswith(("老", "小", "阿")):
        return []
    for surname in _COMPOUND_SURNAMES:
        if name.startswith(surname):
            alias = name[len(surname) :]
            return [alias] if len(alias) >= 2 else []
    if len(name) != 3:
        return []
    alias = name[1:]
    return [alias] if len(alias) >= 2 else []


def _normalize_chapter_contract_cognitive_subjects(
    payload: dict[str, Any],
    *,
    entity_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper for older tests/imports."""
    return normalize_chapter_contract_entity_references(
        payload,
        entity_catalog=entity_catalog,
    )


def _source_payload_issues(
    *,
    artifact_type: str,
    payload: dict[str, Any],
    canonical_refs: list[CanonicalEntityRef],
    entity_catalog: dict[str, Any] | None = None,
) -> list[ArtifactIssue]:
    issues: list[ArtifactIssue] = []
    if artifact_type == "story_foundation":
        raw_book = payload.get("world_rule_book")
        if not raw_book:
            issues.append(
                ArtifactIssue(
                    code="missing_world_rule_book",
                    message="StoryFoundationArtifact 缺少 world_rule_book；已有项目必须重新初始化。",
                    severity="critical",
                    source=artifact_type,
                    path="world_rule_book",
                )
            )
        else:
            try:
                book = coerce_world_rule_book(raw_book)
                validation_errors = validate_world_rule_book(
                    book,
                    magic_or_tech=str(payload.get("magic_or_tech") or ""),
                )
            except Exception as exc:
                validation_errors = [f"world_rule_book 无法解析：{exc}"]
            for error in validation_errors:
                issues.append(
                    ArtifactIssue(
                        code="world_rule_book_invalid",
                        message=error,
                        severity="critical",
                        source=artifact_type,
                        path="world_rule_book",
                    )
                )
    if artifact_type == "entity_graph" and not canonical_refs:
        issues.append(
            ArtifactIssue(
                code="missing_entity_registry",
                message="EntityGraphArtifact 没有任何 canonical entity，无法约束下游引用。",
                severity="critical",
                source=artifact_type,
            )
        )
    if artifact_type == "chapter_contract_index":
        lookup = _entity_lookup(canonical_refs)
        contracts_by_chapter = _as_mapping(payload.get("by_chapter"))
        coverage = _as_mapping(payload.get("coverage"))
        if not contracts_by_chapter:
            issues.append(
                ArtifactIssue(
                    code="missing_chapter_contracts",
                    message="ChapterContractIndexArtifact 没有任何章节契约。",
                    severity="critical",
                    source=artifact_type,
                )
            )
        fallback_chapters = [
            chapter
            for chapter, contract in contracts_by_chapter.items()
            if isinstance(contract, dict)
            and str(contract.get("source") or "") in {"outline_fallback", "outline_backfill"}
        ]
        if fallback_chapters and not bool(coverage.get("local_fallback_accepted")):
            issues.append(
                ArtifactIssue(
                    code="unconfirmed_outline_fallback_contract",
                    message=(
                        "ChapterContractIndexArtifact 使用大纲回填契约，"
                        "但上游没有确认 local_fallback_accepted；"
                        f"受影响章节：{fallback_chapters[:12]}"
                    ),
                    severity="critical",
                    source=artifact_type,
                    path="by_chapter",
                )
            )
        for chapter, contract in contracts_by_chapter.items():
            raw_names = _relevant_names({}, contract)
            unresolved = [name for name in raw_names if name not in lookup]
            for name in unresolved:
                issues.append(
                    ArtifactIssue(
                        code="unresolved_contract_entity",
                        message=f"第 {chapter} 章契约引用未登记角色/实体：{name}",
                        severity="critical",
                        source=artifact_type,
                        path=f"by_chapter.{chapter}",
                    )
                )
    return issues


def _readiness_issues(
    readiness_report: dict[str, Any],
    artifacts: dict[str, ArtifactEnvelope],
) -> list[ArtifactIssue]:
    issues: list[ArtifactIssue] = []
    for artifact_type, artifact in artifacts.items():
        for issue in artifact.blocking_issues:
            issues.append(issue.model_copy(update={"source": artifact_type}))
    verdict = str(readiness_report.get("verdict") or readiness_report.get("status") or "").lower()
    blocked = bool(readiness_report.get("blocked", False))
    allows = verdict in {"pass", "passed", "ok", "accept", "allow"} or bool(
        readiness_report.get("allowed", False)
    )
    if blocked or (verdict and not allows):
        issues.append(
            ArtifactIssue(
                code="init_readiness_report_blocked",
                message=str(readiness_report.get("summary") or "初始化 readiness 报告未通过。"),
                severity="critical",
                source="init_readiness_report",
            )
        )
    return issues


def _chapter_outline_payload(
    outline_payload: dict[str, Any], chapter_number: int
) -> dict[str, Any]:
    chapters = outline_payload.get("chapters", [])
    if not isinstance(chapters, list):
        return {}
    for item in chapters:
        if isinstance(item, dict) and _chapter_number(item) == chapter_number:
            return item
    return {}


def _project_story_foundation(
    payload: dict[str, Any],
    *,
    chapter_contract: dict[str, Any],
) -> dict[str, Any]:
    keys = (
        "era",
        "geography",
        "rules",
        "world_rule_book",
        "time_convention",
        "address_rules",
        "self_reference_rules",
        "etiquette_rules",
        "institution_terms",
        "material_culture",
        "anachronism_blacklist",
        "dialogue_register_rules",
        "banned_intent_rules",
        "max_key_revelations_per_chapter",
        "min_unresolved_threads_to_keep",
    )
    projected = {key: payload.get(key) for key in keys if payload.get(key) not in (None, "", [])}
    projected["chapter_hard_rules"] = chapter_contract.get("hard_rules", [])
    return projected


def _project_style_voice(
    payload: dict[str, Any],
    *,
    relevant_entities: list[CanonicalEntityRef],
) -> dict[str, Any]:
    wanted = {ref.canonical_name for ref in relevant_entities}
    voices = []
    for item in (
        payload.get("character_voices", [])
        if isinstance(payload.get("character_voices"), list)
        else []
    ):
        if not isinstance(item, dict):
            continue
        if wanted and str(item.get("name") or "") not in wanted:
            continue
        voices.append(item)
    style_profile = _as_mapping(payload.get("style_profile"))
    # Collect forbidden phrases from both the top-level ``forbidden_phrases``
    # field and ``global_style.banned_phrases``. Older style profiles only
    # populate the latter; the chapter template reads ``forbidden_phrases``
    # so we merge both sources into a single deduplicated list.
    forbidden_phrases: list[Any] = []
    top_level = style_profile.get("forbidden_phrases")
    if isinstance(top_level, list):
        forbidden_phrases.extend(top_level)
    global_style = _as_mapping(style_profile.get("global_style"))
    banned = global_style.get("banned_phrases")
    if isinstance(banned, list):
        for phrase in banned:
            if phrase not in forbidden_phrases:
                forbidden_phrases.append(phrase)
    projected_style_profile: dict[str, Any] = {
        key: style_profile.get(key)
        for key in (
            "narrative_voice",
            "sentence_style",
            "dialogue_style",
            "weak_senses",
            "reading_power_window_config",
        )
        if key in style_profile
    }
    if forbidden_phrases:
        projected_style_profile["forbidden_phrases"] = forbidden_phrases
    return {
        "style_profile": projected_style_profile,
        "character_voices": voices,
    }


def _project_creative_direction(
    payload: dict[str, Any],
    *,
    chapter_contract: dict[str, Any],
) -> dict[str, Any]:
    return {
        "thematic_promises": payload.get("thematic_promises", [])
        if isinstance(payload.get("thematic_promises"), list)
        else [],
        "relationship_tensions": payload.get("relationship_tensions", [])
        if isinstance(payload.get("relationship_tensions"), list)
        else [],
        "chapter_focus": chapter_contract.get("required_progressions", [])
        or chapter_contract.get("required_events", []),
    }


def _project_narrative_contract(
    payload: dict[str, Any],
    *,
    chapter_contract: dict[str, Any],
) -> dict[str, Any]:
    raw_boundaries = chapter_contract.get("cognitive_constraints", [])
    if not raw_boundaries:
        raw_boundaries = chapter_contract.get("knowledge_ops", [])
    return {
        "continuity_protocol": payload.get("continuity_protocol", {}),
        "world_rules": payload.get("world_rules", []),
        "promise_plan_refs": _promise_refs_for_chapter(payload, chapter_contract),
        "knowledge_boundaries": list(raw_boundaries) if isinstance(raw_boundaries, list) else [],
    }


def _promise_refs_for_chapter(
    payload: dict[str, Any],
    chapter_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    chapter_number = _chapter_number(chapter_contract)
    result: list[dict[str, Any]] = []
    for item in (
        payload.get("promise_plan", []) if isinstance(payload.get("promise_plan"), list) else []
    ):
        if not isinstance(item, dict):
            continue
        chapters = {
            _safe_int(item.get("setup_chapter")),
            _safe_int(item.get("payoff_chapter")),
            *[
                _safe_int(value)
                for value in item.get("chapters", [])
                if isinstance(item.get("chapters"), list)
            ],
        }
        if chapter_number in chapters:
            result.append(item)
    return result


def _forbidden_reveal_boundaries(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key in ("future_leak_risks", "forbidden_changes", "forbidden_progressions"):
        value = contract.get(key)
        if isinstance(value, list):
            result.extend(
                item if isinstance(item, dict) else {"rule": str(item)} for item in value if item
            )
    return result


def _entity_lookup(refs: list[CanonicalEntityRef]) -> dict[str, CanonicalEntityRef]:
    lookup: dict[str, CanonicalEntityRef] = {}
    for ref in refs:
        if ref.entity_id:
            lookup[ref.entity_id] = ref
        lookup[ref.canonical_name] = ref
        for alias in ref.aliases:
            lookup[alias] = ref
    return lookup


def _resolve_relevant_entities(
    names: list[str],
    lookup: dict[str, CanonicalEntityRef],
) -> tuple[list[CanonicalEntityRef], list[str]]:
    refs: list[CanonicalEntityRef] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for name in names:
        ref = lookup.get(name)
        if ref is None:
            unresolved.append(name)
            continue
        if ref.entity_id not in seen:
            refs.append(ref)
            seen.add(ref.entity_id)
    return refs, unresolved


def _relevant_names(*payloads: Any) -> list[str]:
    names: list[str] = []
    for payload in payloads:
        _collect_relevant_names(_dump(payload), names, field_path=())
    return _dedupe([name for name in names if _looks_like_name(name)])


def _collect_relevant_names(
    value: Any,
    names: list[str],
    *,
    field_path: tuple[str, ...],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            next_path = (*field_path, str(key))
            if is_chapter_contract_entity_ref_map_path(next_path):
                if isinstance(item, dict):
                    _collect_name_value(list(item.keys()), names)
                continue
            if is_chapter_contract_entity_ref_path(next_path):
                _collect_name_value(item, names)
            else:
                _collect_relevant_names(item, names, field_path=next_path)
        return
    if isinstance(value, list):
        for item in value:
            _collect_relevant_names(item, names, field_path=field_path)


def _collect_name_value(value: Any, names: list[str]) -> None:
    if isinstance(value, str):
        for part in re.split(r"[,，、/；;和与及\s]+", value):
            # Split pure-ASCII tokens on camelCase boundaries to break
            # concatenated entity references (e.g. "concept_netViolence"
            # → "concept_net" + "Violence") before name filtering.
            if part and part.isascii() and re.search(r"[a-z][A-Z]", part):
                for camel_part in re.split(r"(?<=[a-z])(?=[A-Z])", part):
                    text = _clean_name_token(camel_part)
                    if text:
                        names.append(text)
            else:
                text = _clean_name_token(part)
                if text:
                    names.append(text)
        return
    if isinstance(value, list):
        for item in value:
            _collect_name_value(item, names)
        return
    if isinstance(value, dict):
        for key in ("name", "character", "character_name", "entity", "owner", "pov_character"):
            if key in value:
                _collect_name_value(value[key], names)


def _looks_like_name(value: str) -> bool:
    if not value or len(value) > 24:
        return False
    if _is_unknown_entity_placeholder(value):
        return False
    if value in _TASK_TYPE_VALUE_SET:
        return False
    # LLM tokenization artifacts: ASCII letter run glued to CJK (e.g. "on轨").
    if re.search(r"^[a-zA-Z]+[\u4e00-\u9fff]", value):
        return False
    # LLM concatenation artifacts: camelCase or PascalCase boundaries in pure
    # ASCII tokens (e.g. "netViolence" from split of "concept_netViolence").
    if value.isascii() and len(value) > 3 and re.search(r"[a-z][A-Z]", value):
        return False
    return True


looks_like_entity_name = _looks_like_name


def _clean_name_token(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"（[^）]*）", "", text)
    text = re.sub(r"\([^)]*\)", "", text)
    return text.strip(" \t\r\n:：'\"“”‘’")


def _registered_knowledge_op_fact_names(
    contract: dict[str, Any],
    lookup: dict[str, CanonicalEntityRef],
) -> list[str]:
    result: list[str] = []
    ops = contract.get("knowledge_ops")
    if not isinstance(ops, list):
        return result
    for op in ops:
        if not isinstance(op, dict):
            continue
        for key in KNOWLEDGE_OP_FACT_KEYS:
            value = op.get(key)
            if not isinstance(value, str):
                continue
            token = _clean_name_token(value)
            if token in lookup and token not in result:
                result.append(token)
    return result


def _is_unknown_entity_placeholder(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    normalized = re.sub(r"[\s　]+", "", text)
    normalized = normalized.strip(" \t\r\n:：'\"“”‘’.,，。;；/、-—_")
    return normalized in _UNKNOWN_ENTITY_PLACEHOLDERS


def _chapter_number(payload: dict[str, Any]) -> int:
    for key in ("chapter_number", "chapter", "number", "chapter_id"):
        value = payload.get(key)
        number = _safe_int(value)
        if number > 0:
            return number
    return 0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


__all__ = [
    "SOURCE_ARTIFACT_TYPES",
    "attach_chapter_instruction_to_source_slice",
    "attach_chapter_research_to_source_slice",
    "build_and_persist_chapter_source_slice",
    "build_init_entity_catalog",
    "hash_payload",
    "load_chapter_source_slice",
    "load_init_readiness_artifact",
    "load_stage_artifact",
    "normalize_chapter_contract_entity_references",
    "normalize_cognitive_subjects",
    "persist_init_source_artifacts",
    "persist_stage_artifact",
    "project_init_entity_catalog",
    "project_stage_source_cards",
    "source_artifact_hashes",
]
