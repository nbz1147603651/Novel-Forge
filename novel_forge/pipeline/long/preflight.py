"""Preflight loading and schema validation for long-form chapter runs."""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.core.schemas.artifacts import ChapterSourceSliceArtifact
from novel_forge.core.schemas.bible import CharacterBible, StoryBible
from novel_forge.core.schemas.outline import (
    ChapterOutline,
    NarrativeBlueprint,
    StoryOutline,
    VolumeOutline,
)
from novel_forge.editorial.schemas import EditorialContract
from novel_forge.memory.style_golden_retriever import StyleGoldenRetriever
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    build_upstream_revision_fingerprint,
    scoped_stale_chapters,
)
from novel_forge.pipeline.long.artifact_cache import (
    ArtifactLoadContext,
    ChapterArtifactBundleLoader,
)
from novel_forge.pipeline.long.services.context.source_artifacts import (
    load_chapter_source_slice,
    load_init_readiness_artifact,
)
from novel_forge.pipeline.style_profile_helpers import (
    merge_style_profile_overrides,
)
from novel_forge.story_kernel.schemas import StoryKernel
from novel_forge.story_kernel.store import StoryKernelStore

_SUPPORTED_SCHEMA_VERSION = "2.0"
_LEGACY_SCHEMA_ERROR = "当前版本不兼容旧项目，请重新 init-long 或等待迁移工具"
_logger = logging.getLogger(__name__)


@dataclass
class LongProjectBundle:
    """All structured inputs needed for one long chapter run."""

    layout: ProjectLayout
    project_id: str
    canon_store: StoryKernelStore
    canon_state: StoryKernel
    outline: StoryOutline
    chapter_outline: ChapterOutline
    current_volume: VolumeOutline | None
    story_bible: StoryBible
    character_bible: CharacterBible
    world_setting_brief: str = ""
    pov_hint: str = ""
    weak_senses: list[str] = field(default_factory=list)
    style_profile: dict[str, Any] | None = None
    narrative_contract: dict[str, Any] | None = None
    editorial_contract: EditorialContract | None = None
    editorial_readiness: dict[str, Any] | None = None
    blueprint: NarrativeBlueprint | None = None
    chapter_source_slice: ChapterSourceSliceArtifact | None = None
    upstream_revision_fingerprint: dict[str, Any] = field(default_factory=dict)
    replan_context: Any | None = None
    character_silence: bool = False
    backstory_reveals: list[dict[str, Any]] = field(default_factory=list)
    style_golden_retriever: Any = None
    """M3.5 — Optional StyleGoldenRetriever instance for in-context style examples.

    When set, the chapter flow will pass it to PromptBuilder as part of the
    DRAFT_CHAPTER / WAVE_CHAPTER render context, and the retriever will be
    queried with the active scene_intent to materialize a few high-scoring
    paragraphs from the same project as positive examples.
    """


def _find_chapter_outline(outline: StoryOutline, chapter_number: int) -> ChapterOutline:
    for chapter in outline.chapters:
        if chapter.chapter_number == chapter_number:
            return chapter
    raise ValueError(f"Chapter {chapter_number} not found in outline")


def _find_volume_for_chapter(
    outline: StoryOutline,
    chapter_number: int,
) -> VolumeOutline | None:
    for volume in outline.volumes:
        if volume.start_chapter <= chapter_number <= volume.end_chapter:
            return volume
    return None


def _assert_supported_schema_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    version = str(payload.get("schema_version", "")).strip()
    if version != _SUPPORTED_SCHEMA_VERSION:
        raise ValueError(_LEGACY_SCHEMA_ERROR)


def _assert_init_readiness(storage: FileSystemStorage, layout: ProjectLayout) -> None:
    load_init_readiness_artifact(storage, layout)


async def prepare_long_project(
    *,
    storage: FileSystemStorage,
    project_id: str,
    chapter_number: int,
    force_regenerate: bool = False,
) -> LongProjectBundle:
    """Load core project assets and enforce v2-only schema support."""
    layout = ProjectLayout(storage.existing_project_dir(project_id))
    canon_store = StoryKernelStore(layout.story_kernel_db_path)
    try:
        return await _prepare_long_project(
            storage=storage,
            project_id=project_id,
            chapter_number=chapter_number,
            force_regenerate=force_regenerate,
            canon_store=canon_store,
        )
    except BaseException:
        await canon_store.close()
        raise


async def close_long_project(bundle: LongProjectBundle) -> None:
    """Release the chapter-owned database before its worker event loop exits."""
    close = getattr(getattr(bundle, "canon_store", None), "close", None)
    if callable(close):
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:
            _logger.warning("long_project_store_close_failed", exc_info=True)


async def _prepare_long_project(
    *,
    storage: FileSystemStorage,
    project_id: str,
    chapter_number: int,
    force_regenerate: bool,
    canon_store: StoryKernelStore,
) -> LongProjectBundle:
    layout = ProjectLayout(storage.existing_project_dir(project_id))
    from novel_forge.persistence.planning_revision import assert_planning_publish_complete

    assert_planning_publish_complete(layout.root)
    artifacts = ChapterArtifactBundleLoader(
        storage,
        context=ArtifactLoadContext(
            source="long_preflight",
            project_id=project_id,
            chapter_number=chapter_number,
        ),
    )
    stale_chapters = scoped_stale_chapters(storage, layout)
    _assert_init_readiness(storage, layout)

    first_stale = min(stale_chapters) if stale_chapters else None
    if first_stale is not None and chapter_number > first_stale:
        raise ValueError(
            f"第 {first_stale} 章之后的内容已因上游重写失效。"
            f"请先重新生成第 {first_stale} 章，再继续第 {chapter_number} 章。"
        )

    for path in (
        layout.spec_path,
        layout.bible_path,
        layout.characters_path,
        layout.outline_path,
    ):
        _assert_supported_schema_file(path)

    await canon_store.init_db()
    try:
        canon_state = await canon_store.load_kernel(project_id)
    except ValueError:
        canon_state = await canon_store.create_kernel(project_id)

    if canon_state.current_chapter >= chapter_number:
        if force_regenerate:
            canon_state = await canon_store.rollback_to(chapter_number - 1)
        else:
            raise ValueError(
                f"Chapter {chapter_number} has already been generated. "
                f"Canon is at chapter {canon_state.current_chapter}."
            )

    if canon_state.project_id != project_id:
        raise ValueError(
            f"Canon project_id mismatch: '{canon_state.project_id}' != '{project_id}'."
        )

    outline = artifacts.load_model(layout.outline_path, StoryOutline)
    story_bible = artifacts.load_model(layout.bible_path, StoryBible)
    character_bible = artifacts.load_model(layout.characters_path, CharacterBible)

    world_setting_brief = ""
    character_silence = False
    backstory_reveals: list[dict[str, Any]] = []
    if storage.exists(layout.spec_path):
        spec_data = artifacts.load_json(layout.spec_path)
        world_hint = spec_data.get("world_hint", "")
        if world_hint:
            world_setting_brief = world_hint
        pov_hint = str(spec_data.get("pov_hint", "") or "").strip()
        raw_weak = spec_data.get("weak_senses", [])
        weak_senses = [str(s) for s in raw_weak] if isinstance(raw_weak, list) else []
        character_silence = bool(spec_data.get("character_silence", False))
        raw_reveals = spec_data.get("backstory_reveals", [])
        backstory_reveals = (
            [item for item in raw_reveals if isinstance(item, dict)]
            if isinstance(raw_reveals, list)
            else []
        )
    else:
        pov_hint = ""
        weak_senses = []

    # Load style profile if it exists
    style_profile: dict[str, Any] | None = None
    if storage.exists(layout.style_profile_path):
        try:
            style_profile = merge_style_profile_overrides(
                artifacts.load_json(layout.style_profile_path)
            )
        except (json.JSONDecodeError, OSError):
            style_profile = None

    # Load narrative contract if it exists
    narrative_contract: dict[str, Any] | None = None
    if storage.exists(layout.narrative_contract_path):
        try:
            narrative_contract = artifacts.load_json(layout.narrative_contract_path)
        except (json.JSONDecodeError, OSError):
            narrative_contract = None

    if not storage.exists(layout.editorial_contract_path):
        raise ValueError("项目缺少 plans/editorial_contract.json，请重新执行长篇初始化。")
    editorial_contract = artifacts.load_model(layout.editorial_contract_path, EditorialContract)
    editorial_readiness: dict[str, Any] | None = None
    editorial_readiness_path = layout.reports_dir / "init_editorial_readiness.json"
    if storage.exists(editorial_readiness_path):
        try:
            raw_editorial_readiness = artifacts.load_json(editorial_readiness_path)
            if isinstance(raw_editorial_readiness, dict):
                editorial_readiness = raw_editorial_readiness
        except (json.JSONDecodeError, OSError, ValueError):
            editorial_readiness = None

    # Load narrative blueprint if it exists (carries element_selection)
    blueprint: NarrativeBlueprint | None = None
    if storage.exists(layout.blueprint_path):
        try:
            blueprint = artifacts.load_model(layout.blueprint_path, NarrativeBlueprint)
        except (json.JSONDecodeError, OSError, ValueError):
            blueprint = None

    chapter_source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id=project_id,
        chapter_number=chapter_number,
    )
    try:
        style_golden_retriever = StyleGoldenRetriever(
            layout.root,
            layout.root / "_chapter_meta_cache.json",
        )
    except Exception as exc:
        _logger.warning(
            "style_golden_retriever_init_failed | project=%s | error=%s",
            project_id,
            exc,
        )
        style_golden_retriever = None

    return LongProjectBundle(
        layout=layout,
        project_id=project_id,
        canon_store=canon_store,
        canon_state=canon_state,
        outline=outline,
        chapter_outline=_find_chapter_outline(outline, chapter_number),
        current_volume=_find_volume_for_chapter(outline, chapter_number),
        story_bible=story_bible,
        character_bible=character_bible,
        world_setting_brief=world_setting_brief,
        pov_hint=pov_hint,
        weak_senses=weak_senses,
        style_profile=style_profile,
        narrative_contract=narrative_contract,
        editorial_contract=editorial_contract,
        editorial_readiness=editorial_readiness,
        blueprint=blueprint,
        chapter_source_slice=chapter_source_slice,
        upstream_revision_fingerprint=build_upstream_revision_fingerprint(
            storage,
            layout,
            chapter_number,
        ),
        character_silence=character_silence,
        backstory_reveals=backstory_reveals,
        style_golden_retriever=style_golden_retriever,
    )
