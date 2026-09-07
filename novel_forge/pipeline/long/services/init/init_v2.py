"""V2 initialization helpers.

The helpers in this module are deliberately deterministic. They keep the
initialization flow resumable, auditable, and locally composable while emitting
the compact files consumed by chapter generation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile, StoryBible
from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection
from novel_forge.core.schemas.init_v2 import (
    BlueprintFragments,
    CharacterAuditIssue,
    CharacterRosterEntry,
    CharacterSystem,
    CreativeDirectorPacket,
    EntityGraph,
    EntityLink,
    InitV2BlockRecord,
    RelationshipEdge,
)
from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.narrative_state.schemas import EntityRecord, EntityRegistry, EntityType, stable_id
from novel_forge.obs.project_logger import get_project_logger
from novel_forge.persistence.filesystem import atomic_write_text
from novel_forge.prompts.version import get_version_manager

_log = logging.getLogger(__name__)

_COMBO_SEP_RE = re.compile(r"[/／|、，,]+")

CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION = "2026-07-14.canonical-boundary-v5"
CHARACTER_FRAGMENT_CONTRACT_VERSION = "2026-07-14.character-fragments-v7"
CHARACTER_SYSTEM_BUILD_VERSION = "2026-07-14.canonical-boundary-v7"
CREATIVE_DIRECTOR_PACKET_BUILD_VERSION = "2026-05-26.relationship-ranked-v1"
ENTITY_GRAPH_BUILD_VERSION = "2026-07-14.structured-field-projection-v2"

RELATIONSHIP_TYPE_VALUES: frozenset[str] = frozenset(
    {
        "relationship",
        "romantic_tension",
        "family",
        "mentor_student",
        "professional",
        "alliance",
        "rivalry",
        "antagonism",
        "community",
        "identity_link",
    }
)

_RELATIONSHIP_TYPE_PRIORITY: dict[str, int] = {
    "romantic_tension": 0,
    "antagonism": 1,
    "family": 2,
    "alliance": 3,
    "rivalry": 4,
    "mentor_student": 5,
    "professional": 6,
    "community": 7,
    "relationship": 8,
}
IDENTITY_RELATION_TYPES: frozenset[str] = frozenset(
    {"identity_link", "reincarnation_of", "mistaken_as", "alias_of"}
)
IDENTITY_LINK_TYPE_VALUES: frozenset[str] = frozenset(
    {"reincarnation_of", "mistaken_as", "alias_of", "related_to"}
)


def _canonicalize_for_hash(value: Any) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): _canonicalize_for_hash(raw)
            for key, raw in sorted(value.items(), key=lambda item: str(item[0]))
            if key not in {"created_at"}
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_for_hash(item) for item in value]
    return value


def hash_payload(value: Any) -> str:
    """Return a stable hash for cache fingerprints and upstream dependencies."""
    raw = json.dumps(
        _canonicalize_for_hash(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_template_version_label() -> str:
    """Canonical template version label for cache-drift detection.

    Matches the keys used in ``_get_template_fingerprint_info`` inside
    ``init_cache.py`` so that ``template_version`` field changes predict
    fingerprint changes.
    """
    mgr = get_version_manager()
    parts = [f"sys={mgr.get_current_version()}"]
    for cat in ("initialization", "writing", "planning", "checking"):
        v = mgr.get_category_version(cat)
        if v:
            parts.append(f"{cat}={v.version}")
    return "|".join(parts)


def _safe_log_cache_event(event: str, block_key: str, **extra: Any) -> None:
    """Emit a cache observability event via project logger, falling back to std logging."""
    logger = get_project_logger()
    data: dict[str, Any] = {"block_key": block_key, **extra}
    if logger is not None:
        logger.log_event(event, data)
    else:
        _log.info("%s | block_key=%s %s", event, block_key, extra or "")


class InitV2BlockCache:
    """Small JSON cache for V2 initialization blocks."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root) / "states" / "init_v2" / "cache"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, block_key: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", block_key).strip("_") or "block"
        return self.root / f"{safe}.json"

    def load_success(
        self,
        block_key: str,
        *,
        request_fingerprint: str,
        upstream_hashes: dict[str, str],
    ) -> dict[str, Any] | None:
        path = self.path_for(block_key)
        if not path.exists():
            return None
        try:
            record = InitV2BlockRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None
        if record.status != "succeeded":
            return None
        if record.request_fingerprint != request_fingerprint:
            # Determine whether miss is caused by template drift or payload drift
            current_tv = _get_template_version_label()
            if record.template_version and record.template_version != current_tv:
                _safe_log_cache_event(
                    "init_v2_cache_miss_template_drift",
                    block_key,
                    stored=record.template_version,
                    current=current_tv,
                )
            else:
                _safe_log_cache_event("init_v2_cache_miss_payload_drift", block_key)
            return None
        if record.upstream_hashes != upstream_hashes:
            _safe_log_cache_event("init_v2_cache_miss_payload_drift", block_key)
            return None
        _safe_log_cache_event("init_v2_cache_hit", block_key)
        return record.payload

    def save_success(
        self,
        block_key: str,
        *,
        request_fingerprint: str,
        upstream_hashes: dict[str, str],
        payload: dict[str, Any],
        retry_count: int = 0,
    ) -> None:
        record = InitV2BlockRecord(
            block_key=block_key,
            request_fingerprint=request_fingerprint,
            template_version=_get_template_version_label(),
            upstream_hashes=upstream_hashes,
            status="succeeded",
            payload=payload,
            retry_count=retry_count,
            errors=[],
        )
        atomic_write_text(
            self.path_for(block_key),
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )

    def save_error(
        self,
        block_key: str,
        *,
        request_fingerprint: str,
        upstream_hashes: dict[str, str],
        error: BaseException,
        retry_count: int = 0,
    ) -> None:
        record = InitV2BlockRecord(
            block_key=block_key,
            request_fingerprint=request_fingerprint,
            template_version=_get_template_version_label(),
            upstream_hashes=upstream_hashes,
            status="failed",
            payload={},
            retry_count=retry_count,
            errors=[str(error)],
        )
        atomic_write_text(
            self.path_for(block_key),
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )

    def load_failure_diagnostic(self, block_key: str) -> dict[str, Any] | None:
        """Diagnose a prior failed block without treating it as a reusable artifact."""

        path = self.path_for(block_key)
        if not path.exists():
            return None
        try:
            record = InitV2BlockRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if record.status != "failed":
            return None
        current = _get_template_version_label()
        stale = bool(record.template_version and record.template_version != current)
        return {
            "error_kind": "runtime_version_stale" if stale else "prior_step_failed",
            "error_type": "运行版本陈旧" if stale else "先前步骤失败",
            "block_key": block_key,
            "stored_template_version": record.template_version,
            "current_template_version": current,
            "errors": list(record.errors),
            "action": "retry_failed_step_with_current_runtime",
        }


class StructuredTaskRunner:
    """Cache-aware wrapper for structured init sub-tasks.

    Provider-native structured output can be added behind this boundary without
    changing init orchestration.  Today it delegates to the existing
    ``call_with_retry`` path, which already performs JSON repair, format retry,
    and run-log emission.
    """

    def __init__(
        self,
        *,
        ctx: Any,
        cache: InitV2BlockCache,
        request_fingerprint: str,
    ) -> None:
        self._ctx = ctx
        self._cache = cache
        self._request_fingerprint = request_fingerprint

    async def run_json_block(
        self,
        *,
        block_key: str,
        task_type: TaskType,
        context: dict[str, Any],
        model_type: type[BaseModel],
        upstream_hashes: dict[str, str],
        max_tokens: int,
        temperature: float,
        required_keys: tuple[str, ...] = (),
        include_contract_required_keys: bool = True,
    ) -> BaseModel:
        cached = self._cache.load_success(
            block_key,
            request_fingerprint=self._request_fingerprint,
            upstream_hashes=upstream_hashes,
        )
        if cached is not None:
            return model_type.model_validate(cached)

        diagnostic = self._cache.load_failure_diagnostic(block_key)
        if diagnostic is not None:
            self._ctx.on_step("init_failed_step_retry", diagnostic)

        try:
            payload = await self._ctx.call_with_retry(
                task_type,
                context,
                max_tokens=max_tokens,
                temperature=temperature,
                required_keys=required_keys,
                include_contract_required_keys=include_contract_required_keys,
            )
            model = model_type.model_validate(payload)
        except Exception as exc:
            self._cache.save_error(
                block_key,
                request_fingerprint=self._request_fingerprint,
                upstream_hashes=upstream_hashes,
                error=exc,
            )
            raise

        self._cache.save_success(
            block_key,
            request_fingerprint=self._request_fingerprint,
            upstream_hashes=upstream_hashes,
            payload=model.model_dump(mode="json"),
        )
        return model


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _split_combo_key(value: str) -> list[str]:
    return [item.strip() for item in _COMBO_SEP_RE.split(value or "") if item.strip()]


def _infer_time_layer(profile: CharacterProfile) -> str:
    explicit = _clean_text(getattr(profile, "time_layer", ""))
    if explicit and explicit != "default":
        return explicit

    text = " ".join(
        [
            profile.name,
            profile.role,
            profile.age,
            profile.backstory,
            profile.arc,
            profile.notes,
        ]
    )
    modern_hits = sum(
        1 for token in ("今生", "现代", "当代", "202", "AI", "公司", "职场") if token in text
    )
    past_hits = sum(
        1 for token in ("前世", "民国", "旧时", "百年", "上一世", "192", "193") if token in text
    )
    memory_only_hits = sum(
        1 for token in ("牺牲", "已故", "档案", "照片", "梦里", "回忆") if token in text
    )

    if modern_hits and past_hits:
        return "cross_temporal"
    if past_hits and memory_only_hits and not modern_hits:
        return "memory_only"
    if past_hits:
        return "past"
    if modern_hits:
        return "modern"
    return "default"


def _clean_relation_type(value: Any) -> str:
    """Return a schema relation type supplied by the relationship-matrix LLM."""
    raw = _clean_text(value).lower()
    return raw if raw in RELATIONSHIP_TYPE_VALUES else "relationship"


def _clean_identity_link_type(value: Any) -> str:
    raw = _clean_text(value).lower()
    if raw in {"identity_link", ""}:
        return "related_to"
    return raw if raw in IDENTITY_LINK_TYPE_VALUES else "related_to"


def _coerce_confidence(value: Any, *, default: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, number))


def _relationship_matrix_items(value: Any) -> list[dict[str, Any]]:
    raw_items = value.get("relationship_matrix") if isinstance(value, dict) else value
    if not isinstance(raw_items, list):
        return []
    return [dict(item) for item in raw_items if isinstance(item, dict)]


def build_character_system(
    character_bible: CharacterBible,
    *,
    relationship_matrix: Any | None = None,
) -> CharacterSystem:
    """Build a typed character system and audit relationship pollution."""
    roster: list[CharacterRosterEntry] = []
    profiles: list[dict[str, Any]] = []
    relationship_edges: list[RelationshipEdge] = []
    identity_links: list[EntityLink] = []
    audit: list[CharacterAuditIssue] = []

    for index, profile in enumerate(character_bible.characters):
        name = _clean_text(profile.name)
        if not name:
            continue
        character_id = _clean_text(getattr(profile, "character_id", "")) or stable_id("char", name)
        role = _clean_text(profile.role) or "supporting"
        priority = max(10, 100 - index * 8)
        if role == "protagonist":
            priority = 100
        elif role == "antagonist":
            priority = max(priority, 85)
        elif role == "deuteragonist":
            priority = max(priority, 80)
        roster.append(
            CharacterRosterEntry(
                character_id=character_id,
                name=name,
                role=role,
                time_layer=_infer_time_layer(profile),
                priority=priority,
            )
        )
        profiles.append(profile.model_dump(mode="json"))

    name_to_id = {item.name: item.character_id for item in roster}
    seen_edges: set[tuple[str, str, str]] = set()
    seen_links: set[tuple[str, str, str]] = set()
    matrix_pairs: set[frozenset[str]] = set()

    for item in _relationship_matrix_items(relationship_matrix):
        source_name = _clean_text(
            item.get("character_a")
            or item.get("source_name")
            or item.get("source")
            or item.get("from")
        )
        target_name = _clean_text(
            item.get("character_b")
            or item.get("target_name")
            or item.get("target")
            or item.get("to")
        )
        description = _clean_text(item.get("description") or item.get("summary"))
        relation_type = _clean_relation_type(item.get("relation_type") or item.get("type"))
        raw_relation_type = _clean_text(item.get("relation_type") or item.get("type"))

        if not source_name or not target_name or not description:
            audit.append(
                CharacterAuditIssue(
                    severity="warning",
                    code="invalid_relationship_matrix_item",
                    message="关系矩阵条目缺少角色名或描述，已跳过。",
                    character_name=source_name,
                    target_name=target_name,
                    suggested_action="让关系矩阵分片按固定 JSON schema 重新生成。",
                )
            )
            continue
        if source_name == target_name:
            audit.append(
                CharacterAuditIssue(
                    severity="warning",
                    code="self_relationship",
                    message="关系矩阵条目指向自身，已跳过。",
                    character_name=source_name,
                    target_name=target_name,
                    suggested_action="删除该条或改写为人物内在弧光。",
                )
            )
            continue
        if source_name not in name_to_id or target_name not in name_to_id:
            audit.append(
                CharacterAuditIssue(
                    severity="warning",
                    code="relationship_matrix_unknown_character",
                    message="关系矩阵引用了固定角色清单外的名称，已跳过。",
                    character_name=source_name,
                    target_name=target_name,
                    suggested_action="只输出 roster 内人物，非人物转入实体图。",
                )
            )
            continue
        if (
            raw_relation_type
            and relation_type == "relationship"
            and raw_relation_type != "relationship"
        ):
            audit.append(
                CharacterAuditIssue(
                    severity="warning",
                    code="unknown_relation_type",
                    message="关系矩阵 relation_type 不在固定枚举内，已降级为 relationship。",
                    character_name=source_name,
                    target_name=target_name,
                    suggested_action="让关系矩阵分片使用约定英文 relation_type。",
                )
            )
        source_id = name_to_id[source_name]
        target_id = name_to_id[target_name]
        sorted_ids = sorted((source_id, target_id))
        matrix_pairs.add(frozenset((source_name, target_name)))

        if relation_type in IDENTITY_RELATION_TYPES:
            link_type = _clean_identity_link_type(
                item.get("identity_link_type") or item.get("link_type") or relation_type
            )
            link_key = (source_id, target_id, link_type)
            if link_key not in seen_links:
                seen_links.add(link_key)
                identity_links.append(
                    EntityLink(
                        source_id=source_id,
                        target_id=target_id,
                        source_name=source_name,
                        target_name=target_name,
                        link_type=link_type,
                        description=description,
                        confidence=_coerce_confidence(item.get("confidence"), default=0.8),
                        source="relationship_matrix",
                    )
                )
            continue

        edge_key = (sorted_ids[0], sorted_ids[1], relation_type)
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            relationship_edges.append(
                RelationshipEdge(
                    source_id=source_id,
                    target_id=target_id,
                    source_name=source_name,
                    target_name=target_name,
                    relation_type=relation_type,
                    description=description,
                    confidence=_coerce_confidence(item.get("confidence"), default=1.0),
                    source="relationship_matrix",
                )
            )

    for profile in character_bible.characters:
        source_name = _clean_text(profile.name)
        source_id = name_to_id.get(source_name)
        if not source_id:
            continue
        for raw_target, raw_description in (profile.relationships or {}).items():
            target_name = _clean_text(raw_target)
            description = _clean_text(raw_description)
            if not target_name or not description:
                continue
            if target_name == source_name:
                audit.append(
                    CharacterAuditIssue(
                        severity="warning",
                        code="self_relationship",
                        message="人物关系指向自身，已从人物关系层中过滤。",
                        character_name=source_name,
                        target_name=target_name,
                        suggested_action="删除或改写为人物内在弧光。",
                    )
                )
                continue

            if target_name in name_to_id:
                target_id = name_to_id[target_name]
                if frozenset((source_name, target_name)) in matrix_pairs:
                    continue
                relation_type = "relationship"
                sorted_ids = sorted((source_id, target_id))
                edge_key = (sorted_ids[0], sorted_ids[1], relation_type)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    relationship_edges.append(
                        RelationshipEdge(
                            source_id=source_id,
                            target_id=target_id,
                            source_name=source_name,
                            target_name=target_name,
                            relation_type=relation_type,
                            description=description,
                            source="character_bible",
                        )
                    )
                continue

            parts = _split_combo_key(target_name)
            known_parts = [part for part in parts if part in name_to_id]
            if len(parts) > 1:
                audit.append(
                    CharacterAuditIssue(
                        severity="warning",
                        code="compound_relationship_key",
                        message="关系 key 包含组合称谓，已从人物关系投影中过滤并转入实体图候选。",
                        character_name=source_name,
                        target_name=target_name,
                        suggested_action="拆成 character→character 关系或 alias/identity link。",
                    )
                )
                for known in known_parts:
                    alias_parts = [part for part in parts if part != known]
                    for alias in alias_parts:
                        alias_id = stable_id("alias", alias)
                        link_key = (alias_id, name_to_id[known], "alias_of")
                        if link_key not in seen_links:
                            seen_links.add(link_key)
                            identity_links.append(
                                EntityLink(
                                    source_id=alias_id,
                                    target_id=name_to_id[known],
                                    source_name=alias,
                                    target_name=known,
                                    link_type="alias_of",
                                    description=description,
                                    source="compound_relationship_key",
                                )
                            )
                continue

            audit.append(
                CharacterAuditIssue(
                    severity="warning",
                    code="non_character_relationship_target",
                    message="关系目标不是已知人物，已从人物关系投影中过滤并转入实体图候选。",
                    character_name=source_name,
                    target_name=target_name,
                    suggested_action="转为 item/concept/organization 的 entity_link。",
                )
            )

    connected_names = {
        name for edge in relationship_edges for name in (edge.source_name, edge.target_name) if name
    }
    connected_names.update(
        name
        for link in identity_links
        for name in (link.source_name, link.target_name)
        if name in name_to_id
    )
    profile_by_name = {
        profile.name: profile for profile in character_bible.characters if profile.name
    }
    if len(roster) > 1:
        for item in roster:
            profile = profile_by_name.get(item.name)
            status = _clean_text(getattr(profile, "status", "")) if profile is not None else ""
            if (
                item.name
                and item.name not in connected_names
                and item.role in {"protagonist", "deuteragonist", "supporting", "antagonist"}
                and status != "retired"
            ):
                audit.append(
                    CharacterAuditIssue(
                        severity="warning",
                        code="isolated_active_character",
                        message="活跃关键人物没有任何人物关系或身份链接，可能是命名漂移或关系矩阵漏连。",
                        character_name=item.name,
                        target_name="",
                        suggested_action=(
                            "检查 roster、档案分片和关系矩阵是否使用同一规范名；"
                            "若确为独立人物，应在弧线或备注中说明其孤立原因。"
                        ),
                    )
                )

    return CharacterSystem(
        roster=roster,
        profiles=profiles,
        relationship_edges=relationship_edges,
        identity_links=identity_links,
        audit=audit,
    )


def project_character_bible(
    character_bible: CharacterBible, system: CharacterSystem
) -> CharacterBible:
    """Project CharacterSystem into the compact CharacterBible used by chapters."""
    allowed_names = {item.name for item in system.roster}
    relationship_pairs = {
        frozenset((edge.source_name, edge.target_name))
        for edge in system.relationship_edges
        if edge.source_name and edge.target_name
    }
    edge_descriptions: dict[frozenset[str], str] = {
        frozenset((edge.source_name, edge.target_name)): edge.description
        for edge in system.relationship_edges
        if edge.source_name and edge.target_name and edge.description
    }
    projected_profiles: list[CharacterProfile] = []
    for profile in character_bible.characters:
        filtered_relationships = {
            str(target).strip(): str(description).strip()
            for target, description in (profile.relationships or {}).items()
            if str(target).strip() in allowed_names
            and str(target).strip() != profile.name
            and frozenset((str(target).strip(), profile.name)) in relationship_pairs
            and str(description).strip()
        }
        for pair, description in edge_descriptions.items():
            if profile.name not in pair:
                continue
            target_names = [name for name in pair if name != profile.name]
            if not target_names:
                continue
            target_name = target_names[0]
            if target_name in allowed_names and target_name not in filtered_relationships:
                filtered_relationships[target_name] = description
        projected_profiles.append(
            profile.model_copy(update={"relationships": filtered_relationships})
        )
    return CharacterBible(characters=projected_profiles)


def _merge_entity(existing: EntityRecord | None, incoming: EntityRecord) -> EntityRecord:
    if existing is None:
        return incoming
    aliases = list(dict.fromkeys([*existing.aliases, *incoming.aliases]))
    notes = existing.notes or incoming.notes
    source = existing.source or incoming.source
    entity_type = (
        existing.entity_type if existing.entity_type != "unknown" else incoming.entity_type
    )
    return existing.model_copy(
        update={
            "aliases": aliases,
            "notes": notes,
            "source": source,
            "entity_type": entity_type,
        }
    )


def build_entity_graph(
    *,
    registry: EntityRegistry,
    character_system: CharacterSystem,
    original_character_bible: CharacterBible,
) -> tuple[EntityRegistry, EntityGraph]:
    """Merge registry entities with character-system links and audit artifacts."""
    by_name: dict[str, EntityRecord] = {}
    by_id: dict[str, EntityRecord] = {}
    canonical_character_names = {item.name for item in character_system.roster if item.name}

    for entity in registry.entities:
        if not entity.name:
            continue
        if entity.entity_type == "character" and entity.name not in canonical_character_names:
            continue
        by_name[entity.name] = _merge_entity(by_name.get(entity.name), entity)
        by_id[entity.entity_id] = by_name[entity.name]

    for roster in character_system.roster:
        record = EntityRecord(
            entity_id=roster.character_id,
            name=roster.name,
            entity_type="character",
            aliases=[],
            source="character_system",
        )
        by_name[roster.name] = _merge_entity(by_name.get(roster.name), record)
        by_id[record.entity_id] = by_name[roster.name]

    links: list[EntityLink] = list(character_system.identity_links)
    seen_links = {(link.source_id, link.target_id, link.link_type) for link in links}

    # Materialize alias nodes referenced by identity links.
    for link in list(links):
        if link.link_type == "alias_of" and link.source_name and link.source_id not in by_id:
            by_id[link.source_id] = EntityRecord(
                entity_id=link.source_id,
                name=link.source_name,
                entity_type="unknown",
                aliases=[],
                source="alias_link",
                notes="称谓/别名节点，仅用于实体指代图。",
            )

    char_names = {item.name for item in character_system.roster}
    name_to_id = character_system.name_to_id()
    for profile in original_character_bible.characters:
        source_name = _clean_text(profile.name)
        source_id = name_to_id.get(source_name)
        if not source_id:
            continue
        for raw_target, raw_description in (profile.relationships or {}).items():
            target_name = _clean_text(raw_target)
            description = _clean_text(raw_description)
            if not target_name or target_name in char_names or not description:
                continue
            parts = _split_combo_key(target_name)
            if len(parts) > 1 and any(part in char_names for part in parts):
                continue

            existing_entity = by_name.get(target_name)
            entity_type = existing_entity.entity_type if existing_entity else "unknown"
            entity_id = (
                existing_entity.entity_id if existing_entity else stable_id("ent", target_name)
            )
            entity = existing_entity or EntityRecord(
                entity_id=entity_id,
                name=target_name,
                entity_type=cast(EntityType, entity_type),
                aliases=[],
                source="character_relationship_unadjudicated",
                notes=description[:160],
            )
            by_name[target_name] = _merge_entity(by_name.get(target_name), entity)
            by_id[entity_id] = by_name[target_name]

            link_type = "related_to"
            link_key = (source_id, entity_id, link_type)
            if link_key not in seen_links:
                seen_links.add(link_key)
                links.append(
                    EntityLink(
                        source_id=source_id,
                        target_id=entity_id,
                        source_name=source_name,
                        target_name=target_name,
                        link_type=link_type,
                        description=description,
                        source=(
                            "entity_registry_relationship"
                            if existing_entity is not None
                            else "character_relationship_unadjudicated"
                        ),
                    )
                )

    entities = sorted(
        by_id.values(), key=lambda item: (item.entity_type, item.name, item.entity_id)
    )
    merged_registry = EntityRegistry(entities=entities)
    graph = EntityGraph(
        entities=entities,
        entity_links=links,
        audit=character_system.audit,
    )
    return merged_registry, graph


def _relationship_endpoint_priority(character_system: CharacterSystem, name: str) -> int:
    for item in character_system.roster:
        if item.name == name:
            return int(item.priority or 0)
    return 0


def _rank_relationship_edges(character_system: CharacterSystem) -> list[RelationshipEdge]:
    """Return relationship edges ordered for prompt-facing narrative value."""
    return sorted(
        character_system.relationship_edges,
        key=lambda edge: (
            _RELATIONSHIP_TYPE_PRIORITY.get(edge.relation_type, 99),
            -max(
                _relationship_endpoint_priority(character_system, edge.source_name),
                _relationship_endpoint_priority(character_system, edge.target_name),
            ),
            -min(
                _relationship_endpoint_priority(character_system, edge.source_name),
                _relationship_endpoint_priority(character_system, edge.target_name),
            ),
            edge.source_name,
            edge.target_name,
        ),
    )


def build_relationship_prompt_overview(
    character_system: CharacterSystem,
    *,
    limit: int = 32,
) -> list[str]:
    """Compact relationship lines with both endpoints preserved for planning prompts."""
    lines: list[str] = []
    for edge in _rank_relationship_edges(character_system)[: max(0, limit)]:
        if not edge.source_name or not edge.target_name or not edge.description:
            continue
        lines.append(f"{edge.source_name}↔{edge.target_name}：{edge.description}")
    return lines


def build_creative_director_packet(
    *,
    story_bible: StoryBible,
    character_system: CharacterSystem,
    element_selection: BlueprintElementSelection | None = None,
    entity_graph: EntityGraph | None = None,
) -> CreativeDirectorPacket:
    """Build deterministic creative guidance from accepted init artifacts."""
    themes = [_clean_text(item) for item in getattr(story_bible, "themes", []) if _clean_text(item)]
    rules = [_clean_text(item) for item in getattr(story_bible, "rules", []) if _clean_text(item)]
    motifs = [
        entity.name
        for entity in (entity_graph.entities if entity_graph else [])
        if entity.entity_type in {"item", "concept"} and entity.name
    ][:8]
    tensions = build_relationship_prompt_overview(character_system, limit=16)
    selected = []
    if element_selection is not None:
        for item in [
            *(getattr(element_selection, "required_elements", []) or []),
            *(getattr(element_selection, "extension_elements", []) or []),
        ]:
            name = getattr(item, "name", "") or getattr(item, "element_id", "")
            if name:
                selected.append(str(name))

    return CreativeDirectorPacket(
        emotional_engine=tensions[:5],
        thematic_promises=themes[:6] or [_clean_text(getattr(story_bible, "premise", ""))],
        signature_motifs=motifs,
        relationship_tensions=tensions,
        anti_cliche_rules=[
            "每个关键场景必须改变信息、关系或选择，避免只写氛围。",
            "前世/身份线索必须通过行动、物件或误认推进，不直接解释完。",
            *rules[:3],
        ],
        scene_potential=selected[:6],
        notes="由初始化 V2 确定性汇总生成，供蓝图和章节大纲提升创作密度。",
    )


def blueprint_to_fragments(blueprint: NarrativeBlueprint) -> BlueprintFragments:
    """Persist the accepted blueprint as fragments for V2 resume/UI."""
    data = blueprint.model_dump(mode="json")
    return BlueprintFragments(
        synopsis=data.get("synopsis", ""),
        volume_mode=bool(data.get("volume_mode", False)),
        volumes=data.get("volumes", []) or [],
        narrative_phases=data.get("narrative_phases", []) or [],
        key_turning_points=data.get("key_turning_points", []) or [],
        character_arcs=data.get("character_arcs", []) or [],
        subplot_plan=data.get("subplot_plan", []) or [],
        suspense_schedule=data.get("suspense_schedule", []) or [],
        ending_strategy=data.get("ending_strategy", ""),
        emotional_arcs=data.get("emotional_arcs", []) or [],
        causal_chains=data.get("causal_chains", []) or [],
        subplot_collisions=data.get("subplot_collisions", []) or [],
        subversion_points=data.get("subversion_points", []) or [],
    )


def assemble_blueprint_from_fragments(
    fragments: BlueprintFragments | dict[str, Any],
    *,
    element_selection: BlueprintElementSelection | None = None,
) -> NarrativeBlueprint:
    """Assemble and validate the final NarrativeBlueprint from block fragments."""
    model = (
        fragments
        if isinstance(fragments, BlueprintFragments)
        else BlueprintFragments.model_validate(fragments)
    )
    blueprint = NarrativeBlueprint.model_validate(
        {
            "synopsis": model.synopsis,
            "volume_mode": model.volume_mode,
            "volumes": model.volumes,
            "narrative_phases": model.narrative_phases,
            "key_turning_points": model.key_turning_points,
            "character_arcs": model.character_arcs,
            "subplot_plan": model.subplot_plan,
            "suspense_schedule": model.suspense_schedule,
            "ending_strategy": model.ending_strategy,
        }
    )
    return blueprint.model_copy(update={"element_selection": element_selection})
