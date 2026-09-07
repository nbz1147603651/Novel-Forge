"""Engine-owned persistence for character and relationship source artifacts.

The service updates every derived artifact as one coherent operation: the
character bible, typed relationship matrix, entity graph, StoryKernel mirror,
and downstream chapter-staleness record.  It deliberately has no Qt dependency
so every client invokes identical durable rules.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from novel_forge.core.domain.character_identity import clean_character_name
from novel_forge.core.schemas import (
    CharacterBible,
    CharacterProfile,
    StoryBible,
    StoryOutline,
    StorySpec,
)
from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.narrative_state.store import NarrativeStateStore
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.foundation_guard import (
    is_foundation_candidate,
    require_versioned_foundation_write,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    RevisionScope,
    UpstreamArtifactKind,
    record_upstream_artifact_revision,
)
from novel_forge.pipeline.long.services.context.source_artifacts import (
    persist_init_source_artifacts,
)
from novel_forge.pipeline.long.services.init.init_v2 import (
    build_character_system,
    build_entity_graph,
    project_character_bible,
)

RELATIONSHIP_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("relationship", "一般关系"),
    ("romantic_tension", "情感张力"),
    ("family", "亲属"),
    ("mentor_student", "师徒"),
    ("professional", "职场/组织"),
    ("alliance", "同盟"),
    ("rivalry", "竞争"),
    ("antagonism", "对抗"),
    ("community", "社群"),
    ("identity_link", "身份映射"),
)
RELATIONSHIP_TYPES: frozenset[str] = frozenset(key for key, _label in RELATIONSHIP_TYPE_OPTIONS)
_AsyncResultT = TypeVar("_AsyncResultT")


@dataclass(frozen=True)
class CharacterArtifactWriteResult:
    character_bible: CharacterBible
    character_count: int
    relationship_count: int
    warnings: tuple[str, ...] = field(default_factory=tuple)
    invalidated_chapters: tuple[int, ...] = field(default_factory=tuple)
    story_kernel_synced: bool = False


class CharacterArtifactMutationError(ValueError):
    """A client submitted an invalid Engine-owned character mutation."""


class CharacterArtifactConflictError(CharacterArtifactMutationError):
    """The character source artifact changed after the client read it."""


@dataclass(frozen=True)
class CharacterArtifactMutationResult:
    """Durable outcome returned to all UI transports after one mutation."""

    revision: str
    write_result: CharacterArtifactWriteResult
    proposal_id: str = ""


_NARRATIVE_PROFILE_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "role",
        "age",
        "gender",
        "status",
        "time_layer",
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "voice",
        "notes",
    }
)


def load_character_bible(project_dir: Path) -> CharacterBible:
    layout = ProjectLayout(project_dir)
    payload = _storage_for(project_dir).load_json(layout.characters_path)
    return CharacterBible.model_validate(payload)


def save_narrative_character(
    storage: FileSystemStorage,
    project_id: str,
    *,
    character_id: str | None,
    profile_patch: dict[str, Any],
    expected_revision: str,
) -> CharacterArtifactMutationResult:
    """Create or patch one character without exposing a raw-file write to clients.

    The command only accepts fields represented by the narrative workbench.  It
    merges those into the existing schema object, preserving fields owned by
    other Engine modules (visual identity, TTS hints, knowledge boundaries).
    """

    normalized_patch = _validated_narrative_profile_patch(profile_patch)
    requested_id = str(character_id or "").strip()

    def _write(project_dir: Path) -> CharacterArtifactWriteResult:
        if not requested_id:
            return add_character(project_dir, normalized_patch)
        current = _character_profile_for_id(project_dir, requested_id)
        merged = current.model_dump(mode="json")
        merged.update(normalized_patch)
        return update_character_profile(project_dir, current.name, merged)

    return _mutate_character_artifacts(
        storage,
        project_id,
        expected_revision=expected_revision,
        write=_write,
    )


def retire_narrative_character(
    storage: FileSystemStorage,
    project_id: str,
    *,
    character_id: str,
    expected_revision: str,
) -> CharacterArtifactMutationResult:
    """Mark one existing character retired through the shared artifact writer."""

    requested_id = str(character_id or "").strip()
    if not requested_id:
        raise CharacterArtifactMutationError("缺少角色标识。")

    def _write(project_dir: Path) -> CharacterArtifactWriteResult:
        return retire_character(
            project_dir, _character_profile_for_id(project_dir, requested_id).name
        )

    return _mutate_character_artifacts(
        storage,
        project_id,
        expected_revision=expected_revision,
        write=_write,
    )


def save_narrative_relationship(
    storage: FileSystemStorage,
    project_id: str,
    *,
    source_character_id: str,
    target_character_id: str,
    relation_type: str,
    description: str,
    expected_revision: str,
) -> CharacterArtifactMutationResult:
    """Create or update an undirected typed relationship through Engine state."""

    source_id = str(source_character_id or "").strip()
    target_id = str(target_character_id or "").strip()
    if not source_id or not target_id:
        raise CharacterArtifactMutationError("缺少关系两端角色标识。")

    def _write(project_dir: Path) -> CharacterArtifactWriteResult:
        source = _character_profile_for_id(project_dir, source_id)
        target = _character_profile_for_id(project_dir, target_id)
        return write_relationship_edge(
            project_dir,
            source.name,
            target.name,
            relation_type,
            description,
        )

    return _mutate_character_artifacts(
        storage,
        project_id,
        expected_revision=expected_revision,
        write=_write,
    )


def remove_narrative_relationship(
    storage: FileSystemStorage,
    project_id: str,
    *,
    source_character_id: str,
    target_character_id: str,
    expected_revision: str,
) -> CharacterArtifactMutationResult:
    """Remove a relationship from both source profiles and the typed matrix."""

    source_id = str(source_character_id or "").strip()
    target_id = str(target_character_id or "").strip()
    if not source_id or not target_id:
        raise CharacterArtifactMutationError("缺少关系两端角色标识。")

    def _write(project_dir: Path) -> CharacterArtifactWriteResult:
        source = _character_profile_for_id(project_dir, source_id)
        target = _character_profile_for_id(project_dir, target_id)
        return remove_relationship_edge(project_dir, source.name, target.name)

    return _mutate_character_artifacts(
        storage,
        project_id,
        expected_revision=expected_revision,
        write=_write,
    )


def _mutate_character_artifacts(
    storage: FileSystemStorage,
    project_id: str,
    *,
    expected_revision: str,
    write: Callable[[Path], CharacterArtifactWriteResult],
) -> CharacterArtifactMutationResult:
    """Serialize a multi-artifact write and enforce optimistic concurrency."""

    normalized_project_id = project_id.strip()
    if not normalized_project_id:
        raise CharacterArtifactMutationError("缺少项目标识。")
    if not expected_revision:
        raise CharacterArtifactMutationError("缺少角色产物版本；请刷新后再保存。")

    with storage.project_lock(normalized_project_id):
        try:
            project_dir = storage.existing_project_dir(normalized_project_id)
        except (FileNotFoundError, ValueError) as exc:
            raise CharacterArtifactMutationError("项目不存在或项目标识无效。") from exc
        path = ProjectLayout(project_dir).characters_path
        if not path.is_file():
            raise CharacterArtifactMutationError("角色设定文件不存在。")
        current_revision = _artifact_revision(path)
        if expected_revision != current_revision:
            raise CharacterArtifactConflictError("角色设定已被其他窗口更新；请刷新后合并修改。")
        if (project_dir / "authoring_policy.json").is_file():
            from novel_forge.app_service.authoring_foundation import propose_character_mutation

            proposal, candidate = propose_character_mutation(storage, project_dir, write)
            return CharacterArtifactMutationResult(
                revision=current_revision,
                write_result=candidate,
                proposal_id=proposal.id,
            )
        result = write(project_dir)
        return CharacterArtifactMutationResult(
            revision=_artifact_revision(path),
            write_result=result,
        )


def _artifact_revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _character_profile_for_id(project_dir: Path, character_id: str) -> CharacterProfile:
    requested_id = str(character_id or "").strip()
    for profile in load_character_bible(project_dir).characters:
        profile_id = str(profile.character_id or "").strip()
        if requested_id in {profile_id, clean_character_name(profile.name)}:
            return profile
    raise CharacterArtifactMutationError("未找到指定角色；请刷新角色列表后重试。")


def _validated_narrative_profile_patch(profile_patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile_patch, dict):
        raise CharacterArtifactMutationError("角色资料必须是对象。")
    aliases = {
        "timeLayer": "time_layer",
        "socialStatus": "social_status",
    }
    normalized_patch = {
        aliases.get(str(key), str(key)): value for key, value in profile_patch.items()
    }
    unknown_fields = sorted(set(normalized_patch) - _NARRATIVE_PROFILE_FIELDS)
    if unknown_fields:
        raise CharacterArtifactMutationError(
            f"角色资料包含不支持的字段：{'、'.join(unknown_fields)}。"
        )
    if not normalized_patch:
        raise CharacterArtifactMutationError("角色资料不能为空。")
    return normalized_patch


def write_character_bible(
    project_dir: Path, payload: dict[str, Any] | CharacterBible
) -> CharacterArtifactWriteResult:
    character_bible = (
        payload if isinstance(payload, CharacterBible) else CharacterBible.model_validate(payload)
    )
    return _persist_character_artifacts(project_dir, character_bible)


def update_character_profile(
    project_dir: Path,
    character_name: str,
    profile_payload: dict[str, Any],
) -> CharacterArtifactWriteResult:
    character_bible = load_character_bible(project_dir)
    target_name = clean_character_name(character_name)
    updated_profile = CharacterProfile.model_validate(profile_payload)
    characters: list[CharacterProfile] = []
    found = False
    for profile in character_bible.characters:
        if clean_character_name(profile.name) == target_name:
            characters.append(updated_profile)
            found = True
        else:
            characters.append(profile)
    if not found:
        raise ValueError(f"未找到角色：{character_name}")
    return _persist_character_artifacts(
        project_dir,
        CharacterBible(characters=characters),
    )


def add_character(
    project_dir: Path,
    profile_payload: dict[str, Any],
) -> CharacterArtifactWriteResult:
    character_bible = load_character_bible(project_dir)
    profile = CharacterProfile.model_validate(profile_payload)
    name = clean_character_name(profile.name)
    if not name:
        raise ValueError("角色名不能为空。")
    if any(clean_character_name(item.name) == name for item in character_bible.characters):
        raise ValueError(f"角色已存在：{name}")
    return _persist_character_artifacts(
        project_dir,
        CharacterBible(characters=[*character_bible.characters, profile]),
    )


def retire_character(project_dir: Path, character_name: str) -> CharacterArtifactWriteResult:
    character_bible = load_character_bible(project_dir)
    target_name = clean_character_name(character_name)
    characters: list[CharacterProfile] = []
    found = False
    for profile in character_bible.characters:
        if clean_character_name(profile.name) == target_name:
            characters.append(profile.model_copy(update={"status": "retired"}))
            found = True
        else:
            characters.append(profile)
    if not found:
        raise ValueError(f"未找到角色：{character_name}")
    return _persist_character_artifacts(project_dir, CharacterBible(characters=characters))


def write_relationship_edge(
    project_dir: Path,
    source_name: str,
    target_name: str,
    relation_type: str,
    description: str,
) -> CharacterArtifactWriteResult:
    require_versioned_foundation_write(project_dir)
    source = clean_character_name(source_name)
    target = clean_character_name(target_name)
    relation_type = str(relation_type or "relationship").strip()
    description = " ".join(str(description or "").split())
    if not source or not target:
        raise ValueError("关系两端角色名不能为空。")
    if source == target:
        raise ValueError("不能创建角色自身关系。")
    if relation_type not in RELATIONSHIP_TYPES:
        raise ValueError(f"未知关系类型：{relation_type}")
    if not description:
        raise ValueError("关系描述不能为空。")

    character_bible = load_character_bible(project_dir)
    known_names = {clean_character_name(item.name) for item in character_bible.characters}
    if source not in known_names or target not in known_names:
        raise ValueError("关系两端必须是已存在角色。")

    characters: list[CharacterProfile] = []
    for profile in character_bible.characters:
        clean_name = clean_character_name(profile.name)
        relationships = dict(profile.relationships or {})
        if clean_name == source:
            relationships[target] = description
            profile = profile.model_copy(update={"relationships": relationships})
        elif clean_name == target:
            relationships[source] = description
            profile = profile.model_copy(update={"relationships": relationships})
        characters.append(profile)

    previous_relationship_matrix = _load_relationship_matrix(project_dir)
    _upsert_relationship_matrix_edge(
        project_dir,
        source_name=source,
        target_name=target,
        relation_type=relation_type,
        description=description,
    )
    return _persist_character_artifacts(
        project_dir,
        CharacterBible(characters=characters),
        previous_relationship_matrix=previous_relationship_matrix,
    )


def remove_relationship_edge(
    project_dir: Path,
    source_name: str,
    target_name: str,
) -> CharacterArtifactWriteResult:
    require_versioned_foundation_write(project_dir)
    source = clean_character_name(source_name)
    target = clean_character_name(target_name)
    if not source or not target:
        raise ValueError("关系两端角色名不能为空。")
    character_bible = load_character_bible(project_dir)
    characters: list[CharacterProfile] = []
    for profile in character_bible.characters:
        clean_name = clean_character_name(profile.name)
        relationships = dict(profile.relationships or {})
        if clean_name == source:
            relationships.pop(target, None)
            profile = profile.model_copy(update={"relationships": relationships})
        elif clean_name == target:
            relationships.pop(source, None)
            profile = profile.model_copy(update={"relationships": relationships})
        characters.append(profile)
    previous_relationship_matrix = _load_relationship_matrix(project_dir)
    _remove_relationship_matrix_edge(project_dir, source_name=source, target_name=target)
    return _persist_character_artifacts(
        project_dir,
        CharacterBible(characters=characters),
        previous_relationship_matrix=previous_relationship_matrix,
    )


def _persist_character_artifacts(
    project_dir: Path,
    source_character_bible: CharacterBible,
    *,
    previous_relationship_matrix: list[dict[str, Any]] | None = None,
) -> CharacterArtifactWriteResult:
    require_versioned_foundation_write(project_dir)
    candidate_only = is_foundation_candidate(project_dir)
    storage = _storage_for(project_dir)
    layout = ProjectLayout(project_dir)
    warnings: list[str] = []
    previous_character_bible = _load_previous_character_bible(storage, layout)
    previous_hash = (
        hashlib.sha256(layout.characters_path.read_bytes()).hexdigest()
        if layout.characters_path.exists()
        else ""
    )
    relationship_matrix = _load_relationship_matrix(project_dir)
    if previous_relationship_matrix is None:
        previous_relationship_matrix = relationship_matrix
    previous_character_system = (
        build_character_system(
            previous_character_bible,
            relationship_matrix=previous_relationship_matrix,
        )
        if previous_character_bible is not None
        else None
    )

    character_system = build_character_system(
        source_character_bible,
        relationship_matrix=relationship_matrix,
    )
    projected_character_bible = project_character_bible(source_character_bible, character_system)

    storage.save_json(layout.characters_path, projected_character_bible.model_dump(mode="json"))
    storage.save_json(
        layout.states_dir / "init_v2" / "character_system.json",
        character_system.model_dump(mode="json"),
    )

    state_store = NarrativeStateStore(layout.root)
    try:
        existing_registry = state_store.load_entity_registry()
    except Exception as exc:  # noqa: BLE001
        if candidate_only:
            raise ValueError("实体注册表不可读；未用空注册表替换作者数据") from exc
        warnings.append(f"实体注册表加载失败，已使用空注册表重建：{exc}")
        from novel_forge.narrative_state.schemas import EntityRegistry

        existing_registry = EntityRegistry()
    registry, entity_graph = build_entity_graph(
        registry=existing_registry,
        character_system=character_system,
        original_character_bible=source_character_bible,
    )
    state_store.save_entity_registry(registry)
    storage.save_json(
        layout.narrative_state_dir / "entity_graph.json",
        entity_graph.model_dump(mode="json"),
    )

    _refresh_source_artifacts(
        storage=storage,
        layout=layout,
        character_bible=projected_character_bible,
        character_system=character_system,
        entity_graph=entity_graph,
        warnings=warnings,
    )
    story_kernel_synced = False
    try:
        if not candidate_only:
            story_kernel_synced = _sync_character_artifacts_to_story_kernel(
                layout=layout,
                character_bible=projected_character_bible,
                character_system=character_system,
            )
    except Exception as exc:  # noqa: BLE001 - keep the file artifact save authoritative
        warnings.append(f"StoryKernel 角色同步失败：{exc}")

    invalidated_chapters: tuple[int, ...] = ()
    try:
        revision_scope = _character_revision_scope(
            previous_character_bible,
            projected_character_bible,
            previous_character_system=previous_character_system,
            current_character_system=character_system,
        )
        revision_record = record_upstream_artifact_revision(
            storage,
            layout,
            artifact_kind=UpstreamArtifactKind.CHARACTER_BIBLE,
            previous_hash=previous_hash,
            scope=revision_scope,
            reason="engine_character_artifacts",
            apply_invalidation=not candidate_only,
        )
        invalidated_chapters = tuple(revision_record.invalidated_chapters)
    except Exception as exc:  # noqa: BLE001 - do not fail a successful artifact save
        if candidate_only:
            raise
        warnings.append(f"角色册影响传播记录失败：{exc}")
    if candidate_only and warnings:
        raise ValueError("设定候选未完成来源同步：" + "；".join(warnings))
    return CharacterArtifactWriteResult(
        character_bible=projected_character_bible,
        character_count=len(projected_character_bible.characters),
        relationship_count=len(character_system.relationship_edges),
        warnings=tuple(warnings),
        invalidated_chapters=invalidated_chapters,
        story_kernel_synced=story_kernel_synced,
    )


def _sync_character_artifacts_to_story_kernel(
    *,
    layout: ProjectLayout,
    character_bible: CharacterBible,
    character_system: Any,
) -> bool:
    """Mirror manual character edits into the SQLite StoryKernel state."""

    async def _sync() -> None:
        from novel_forge.narrative_state.schemas import stable_id
        from novel_forge.story_kernel.init_adapter import InitAdapter
        from novel_forge.story_kernel.schemas import Entity, Relationship, StoryKernel
        from novel_forge.story_kernel.store import StoryKernelStore

        character_data = InitAdapter.map_character_bible_to_kernel(character_bible)
        character_entities = [
            Entity.model_validate(item) for item in character_data.get("entities", [])
        ]
        relationships = _kernel_relationships_from_character_system(
            character_system=character_system,
            fallback_relationships=character_data.get("relationships", []),
            relationship_model=Relationship,
            stable_id_fn=stable_id,
        )

        store = StoryKernelStore(layout.story_kernel_db_path)
        try:
            await store.init_db()
            kernel = await _load_story_kernel_for_layout(store, layout, StoryKernel)
            _apply_character_artifacts_to_kernel(
                kernel=kernel,
                character_entities=character_entities,
                character_relationships=relationships,
            )
            await store.save_kernel(kernel)
        finally:
            await store.close()

    _run_async_to_completion(_sync)
    return True


def _run_async_to_completion(
    coro_factory: Callable[[], Coroutine[Any, Any, _AsyncResultT]],
) -> _AsyncResultT:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    result: dict[str, _AsyncResultT] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001 - re-raised on caller thread
            error["error"] = exc

    thread = threading.Thread(target=_runner, name="novel-forge-story-kernel-sync")
    thread.start()
    thread.join()
    if error:
        raise error["error"]
    return result["value"]


def _load_previous_character_bible(
    storage: FileSystemStorage,
    layout: ProjectLayout,
) -> CharacterBible | None:
    if not layout.characters_path.exists():
        return None
    try:
        return CharacterBible.model_validate(storage.load_json(layout.characters_path))
    except Exception:  # noqa: BLE001 - invalid legacy payload should not block saving
        return None


def _character_revision_scope(
    previous_character_bible: CharacterBible | None,
    current_character_bible: CharacterBible,
    *,
    previous_character_system: Any | None = None,
    current_character_system: Any | None = None,
) -> RevisionScope:
    if previous_character_bible is None:
        return RevisionScope.FORWARD_ONLY
    previous_ids = _character_identity_set(previous_character_bible)
    current_ids = _character_identity_set(current_character_bible)
    if previous_ids - current_ids:
        return RevisionScope.WHOLE_BOOK
    if _renamed_character_ids(previous_character_bible, current_character_bible):
        return RevisionScope.WHOLE_BOOK
    if _relationship_type_changed(previous_character_system, current_character_system):
        return RevisionScope.WHOLE_BOOK
    return RevisionScope.FORWARD_ONLY


def _character_identity_set(character_bible: CharacterBible) -> set[str]:
    identities: set[str] = set()
    for profile in character_bible.characters:
        character_id = str(getattr(profile, "character_id", "") or "").strip()
        identities.add(character_id or clean_character_name(profile.name))
    return {identity for identity in identities if identity}


def _renamed_character_ids(
    previous_character_bible: CharacterBible,
    current_character_bible: CharacterBible,
) -> set[str]:
    previous_names = _character_name_by_identity(previous_character_bible)
    current_names = _character_name_by_identity(current_character_bible)
    return {
        character_id
        for character_id, previous_name in previous_names.items()
        if character_id in current_names and current_names[character_id] != previous_name
    }


def _character_name_by_identity(character_bible: CharacterBible) -> dict[str, str]:
    names: dict[str, str] = {}
    for profile in character_bible.characters:
        name = clean_character_name(profile.name)
        if not name:
            continue
        character_id = str(getattr(profile, "character_id", "") or "").strip() or name
        names[character_id] = name
    return names


def _relationship_type_changed(
    previous_character_system: Any | None,
    current_character_system: Any | None,
) -> bool:
    if previous_character_system is None or current_character_system is None:
        return False
    previous_types = _relationship_type_by_pair(previous_character_system)
    current_types = _relationship_type_by_pair(current_character_system)
    return any(
        pair in current_types and current_types[pair] != previous_type
        for pair, previous_type in previous_types.items()
    )


def _relationship_type_by_pair(character_system: Any) -> dict[frozenset[str], str]:
    relationship_types: dict[frozenset[str], str] = {}
    for edge in list(getattr(character_system, "relationship_edges", []) or []):
        source_id = str(getattr(edge, "source_id", "") or "").strip()
        target_id = str(getattr(edge, "target_id", "") or "").strip()
        relation_type = str(getattr(edge, "relation_type", "") or "").strip()
        if not source_id or not target_id or not relation_type:
            continue
        relationship_types[frozenset((source_id, target_id))] = relation_type
    return relationship_types


async def _load_story_kernel_for_layout(
    store: Any,
    layout: ProjectLayout,
    story_kernel_model: Any,
) -> Any:
    project_id = layout.root.name
    try:
        return await store.load_kernel(project_id)
    except ValueError:
        pass

    try:
        current = await store.query_field_slice(["project_id"], {})
    except Exception:  # noqa: BLE001
        current = {}
    existing_project_id = str(current.get("project_id") or "").strip()
    if existing_project_id and existing_project_id != project_id:
        try:
            return await store.load_kernel(existing_project_id)
        except ValueError:
            pass
    return story_kernel_model(project_id=project_id)


def _kernel_relationships_from_character_system(
    *,
    character_system: Any,
    fallback_relationships: list[Any],
    relationship_model: Any,
    stable_id_fn: Any,
) -> list[Any]:
    edges = list(getattr(character_system, "relationship_edges", []) or [])
    if not edges:
        return [relationship_model.model_validate(item) for item in fallback_relationships]

    relationships: list[Any] = []
    relationship_counts: dict[tuple[str, str, str, str], int] = {}
    for edge in edges:
        source_id = str(getattr(edge, "source_id", "") or "").strip()
        target_id = str(getattr(edge, "target_id", "") or "").strip()
        description = str(getattr(edge, "description", "") or "").strip()
        if not source_id or not target_id or not description:
            continue
        relation_type = _story_kernel_relation_type(getattr(edge, "relation_type", ""))
        relationship_key = (source_id, target_id, relation_type, description)
        occurrence = relationship_counts.get(relationship_key, 0)
        relationship_counts[relationship_key] = occurrence + 1
        confidence = getattr(edge, "confidence", 1.0)
        relationships.append(
            relationship_model(
                relationship_id=stable_id_fn("rel", *relationship_key, occurrence),
                source_entity_id=source_id,
                target_entity_id=target_id,
                relation_type=relation_type,
                label=description,
                trust=1.0 if confidence is None else float(confidence),
                notes="从角色关系矩阵同步",
            )
        )
    return relationships


def _story_kernel_relation_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return {
        "relationship": "acquaintance",
        "romantic_tension": "romantic",
        "family": "family",
        "mentor_student": "mentor_student",
        "professional": "business",
        "alliance": "ally",
        "rivalry": "rival",
        "antagonism": "enemy",
        "community": "friend",
    }.get(raw, "acquaintance")


def _apply_character_artifacts_to_kernel(
    *,
    kernel: Any,
    character_entities: list[Any],
    character_relationships: list[Any],
) -> None:
    character_entity_ids = {entity.entity_id for entity in character_entities}
    existing_entities_by_id = {entity.entity_id: entity for entity in kernel.entities}
    removed_init_character_ids = {
        entity.entity_id
        for entity in kernel.entities
        if _is_story_kernel_character(entity)
        and int(getattr(entity, "source_chapter", 0) or 0) == 0
        and entity.entity_id not in character_entity_ids
    }

    merged_characters = [
        _merge_kernel_character_entity(existing_entities_by_id.get(entity.entity_id), entity)
        for entity in character_entities
    ]
    retained_entities = [
        entity
        for entity in kernel.entities
        if entity.entity_id not in character_entity_ids
        and entity.entity_id not in removed_init_character_ids
    ]
    kernel.entities = [*merged_characters, *retained_entities]
    kernel.relationships = _merge_kernel_character_relationships(
        existing_relationships=list(kernel.relationships),
        incoming_relationships=character_relationships,
        managed_character_ids=character_entity_ids | removed_init_character_ids,
        removed_character_ids=removed_init_character_ids,
    )


def _merge_kernel_character_entity(existing: Any | None, incoming: Any) -> Any:
    if existing is None:
        return incoming
    attributes = dict(getattr(existing, "attributes", {}) or {})
    attributes.update(dict(getattr(incoming, "attributes", {}) or {}))
    return existing.model_copy(
        update={
            "name": incoming.name,
            "entity_type": incoming.entity_type,
            "aliases": incoming.aliases or existing.aliases,
            "status": incoming.status,
            "attributes": attributes,
            "notes": incoming.notes or existing.notes,
        }
    )


def _merge_kernel_character_relationships(
    *,
    existing_relationships: list[Any],
    incoming_relationships: list[Any],
    managed_character_ids: set[str],
    removed_character_ids: set[str],
) -> list[Any]:
    existing_by_id = {rel.relationship_id: rel for rel in existing_relationships}
    existing_by_pair: dict[tuple[str, str], Any] = {}
    for rel in existing_relationships:
        existing_by_pair.setdefault(_relationship_pair(rel), rel)

    merged: list[Any] = []
    used_existing_ids: set[str] = set()
    incoming_pairs = {_relationship_pair(rel) for rel in incoming_relationships}
    for incoming in incoming_relationships:
        existing = existing_by_id.get(incoming.relationship_id)
        if existing is None:
            existing = existing_by_pair.get(_relationship_pair(incoming))
        if existing is not None:
            used_existing_ids.add(existing.relationship_id)
        merged.append(_merge_kernel_character_relationship(existing, incoming))

    for existing in existing_relationships:
        if existing.relationship_id in used_existing_ids:
            continue
        pair = _relationship_pair(existing)
        if (
            existing.source_entity_id in removed_character_ids
            or existing.target_entity_id in removed_character_ids
        ):
            continue
        if (
            int(getattr(existing, "established_chapter", 0) or 0) == 0
            and existing.source_entity_id in managed_character_ids
            and existing.target_entity_id in managed_character_ids
            and pair not in incoming_pairs
        ):
            continue
        merged.append(existing)
    return merged


def _merge_kernel_character_relationship(existing: Any | None, incoming: Any) -> Any:
    if existing is None:
        return incoming
    return existing.model_copy(
        update={
            "source_entity_id": incoming.source_entity_id,
            "target_entity_id": incoming.target_entity_id,
            "relation_type": incoming.relation_type,
            "label": incoming.label,
            "notes": incoming.notes or existing.notes,
        }
    )


def _relationship_pair(relationship: Any) -> tuple[str, str]:
    return (
        str(getattr(relationship, "source_entity_id", "") or ""),
        str(getattr(relationship, "target_entity_id", "") or ""),
    )


def _is_story_kernel_character(entity: Any) -> bool:
    entity_type = getattr(entity, "entity_type", "")
    return str(getattr(entity_type, "value", entity_type) or "") == "character"


def _refresh_source_artifacts(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    character_bible: CharacterBible,
    character_system: Any,
    entity_graph: Any,
    warnings: list[str],
) -> None:
    required_paths = {
        "spec": layout.spec_path,
        "story_bible": layout.bible_path,
        "blueprint": layout.blueprint_path,
        "outline": layout.outline_path,
        "narrative_contract": layout.narrative_contract_path,
        "chapter_contracts": layout.plans_dir / "chapter_contracts.json",
    }
    missing = [name for name, path in required_paths.items() if not path.exists()]
    if missing:
        warnings.append("源产物未刷新，缺少：" + "、".join(missing))
        return
    try:
        spec = StorySpec.model_validate(storage.load_json(layout.spec_path))
        story_bible = StoryBible.model_validate(storage.load_json(layout.bible_path))
        blueprint = NarrativeBlueprint.model_validate(storage.load_json(layout.blueprint_path))
        outline = StoryOutline.model_validate(storage.load_json(layout.outline_path))
        narrative_contract = storage.load_json(layout.narrative_contract_path)
        chapter_contracts = storage.load_json(layout.plans_dir / "chapter_contracts.json")
        style_profile = (
            storage.load_json(layout.style_profile_path)
            if layout.style_profile_path.exists()
            else None
        )
        creative_packet_path = layout.plans_dir / "creative_director_packet.json"
        creative_packet = (
            storage.load_json(creative_packet_path) if creative_packet_path.exists() else {}
        )
        readiness_path = layout.reports_dir / "init_readiness.json"
        readiness_report = storage.load_json(readiness_path) if readiness_path.exists() else {}
        persist_init_source_artifacts(
            storage=storage,
            layout=layout,
            project_id=layout.root.name,
            spec=spec,
            story_bible=story_bible,
            character_bible=character_bible,
            character_system=character_system,
            entity_graph=entity_graph,
            style_profile=style_profile,
            creative_packet=creative_packet,
            blueprint=blueprint,
            outline=outline,
            narrative_contract=narrative_contract,
            chapter_contracts=chapter_contracts,
            readiness_report=readiness_report,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"源产物刷新失败：{exc}")


def _storage_for(project_dir: Path) -> FileSystemStorage:
    return FileSystemStorage(project_dir.parent)


def _relationship_matrix_path(project_dir: Path) -> Path:
    return ProjectLayout(project_dir).states_dir / "init_v2" / "character_relationship_matrix.json"


def _load_relationship_matrix_payload(project_dir: Path) -> dict[str, Any]:
    path = _relationship_matrix_path(project_dir)
    if not path.exists():
        return {"relationship_generation_phase": "manual_ui", "relationship_matrix": []}
    try:
        payload = _storage_for(project_dir).load_json(path)
    except Exception:
        return {"relationship_generation_phase": "manual_ui", "relationship_matrix": []}
    if not isinstance(payload.get("relationship_matrix"), list):
        payload["relationship_matrix"] = []
    return payload


def _load_relationship_matrix(project_dir: Path) -> list[dict[str, Any]]:
    payload = _load_relationship_matrix_payload(project_dir)
    return [dict(item) for item in payload.get("relationship_matrix", []) if isinstance(item, dict)]


def _same_pair(item: dict[str, Any], source_name: str, target_name: str) -> bool:
    left = clean_character_name(
        item.get("character_a") or item.get("source_name") or item.get("source") or item.get("from")
    )
    right = clean_character_name(
        item.get("character_b") or item.get("target_name") or item.get("target") or item.get("to")
    )
    return {left, right} == {source_name, target_name}


def _upsert_relationship_matrix_edge(
    project_dir: Path,
    *,
    source_name: str,
    target_name: str,
    relation_type: str,
    description: str,
) -> None:
    payload = _load_relationship_matrix_payload(project_dir)
    matrix = [
        dict(item) for item in payload.get("relationship_matrix", []) if isinstance(item, dict)
    ]
    updated = False
    for item in matrix:
        if _same_pair(item, source_name, target_name):
            item.update(
                {
                    "character_a": source_name,
                    "character_b": target_name,
                    "relation_type": relation_type,
                    "description": description,
                    "source": "manual_ui",
                }
            )
            updated = True
            break
    if not updated:
        matrix.append(
            {
                "character_a": source_name,
                "character_b": target_name,
                "relation_type": relation_type,
                "description": description,
                "confidence": 1.0,
                "source": "manual_ui",
            }
        )
    payload["relationship_generation_phase"] = "manual_ui"
    payload["relationship_matrix"] = matrix
    _storage_for(project_dir).save_json(_relationship_matrix_path(project_dir), payload)


def _remove_relationship_matrix_edge(
    project_dir: Path,
    *,
    source_name: str,
    target_name: str,
) -> None:
    payload = _load_relationship_matrix_payload(project_dir)
    matrix = [
        dict(item)
        for item in payload.get("relationship_matrix", [])
        if isinstance(item, dict) and not _same_pair(item, source_name, target_name)
    ]
    payload["relationship_generation_phase"] = "manual_ui"
    payload["relationship_matrix"] = matrix
    _storage_for(project_dir).save_json(_relationship_matrix_path(project_dir), payload)
