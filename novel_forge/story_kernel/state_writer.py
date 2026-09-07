"""Centralized StoryKernel write service."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Literal

from novel_forge.story_kernel.contract_guard import enforce_contract_write
from novel_forge.story_kernel.entity_projection import dedupe_entities, stable_entity_id
from novel_forge.story_kernel.init_adapter import build_kernel_from_init
from novel_forge.story_kernel.merger import StoryKernelMerger
from novel_forge.story_kernel.schemas import (
    Entity,
    PromiseLedger,
    StoryKernel,
    StoryKernelStructuredWarning,
    TimelineAnchor,
)
from novel_forge.story_kernel.store import StoryKernelStore

ProjectMode = Literal["long", "short"]


class StoryKernelStateWriter:
    """Single write boundary for project-level structured narrative state."""

    def __init__(self, store: StoryKernelStore) -> None:
        self._store = store

    @staticmethod
    def _contract_mode() -> str:
        """Resolve kernel contract enforcement mode from settings (lazy)."""
        try:
            from novel_forge.core.config import get_settings

            mode = str(getattr(get_settings(), "kernel_contract_mode", "warn") or "warn")
        except Exception:
            mode = "warn"
        return mode.strip().lower()

    def _enforce_contracts(
        self,
        step_names: str | Iterable[str] | None,
        *,
        before: StoryKernel,
        after: StoryKernel,
    ) -> None:
        """Validate a pending kernel mutation against field contracts.

        No-op when no step names are provided (unattributed writes such as
        project init or the short pipeline) or when enforcement is off.
        """
        if not step_names:
            return
        mode = self._contract_mode()
        if mode == "off":
            return
        enforce_contract_write(step_names, before=before, after=after, mode=mode)

    async def load_or_create_kernel(
        self,
        project_id: str,
        mode: ProjectMode = "long",
    ) -> StoryKernel:
        await self._store.init_db()
        try:
            kernel = await self._store.load_kernel(project_id)
        except ValueError:
            kernel = await self._store.create_kernel(project_id)
        if kernel.project_mode != mode:
            kernel = kernel.model_copy(update={"project_mode": mode})
            await self._store.save_kernel(kernel)
        return kernel

    async def initialize_long_project(
        self,
        *,
        project_id: str,
        story_bible: Any,
        character_bible: Any,
        outline: Any,
        narrative_contract: Any | None = None,
        entity_registry: Any | None = None,
        entity_graph: Any | None = None,
    ) -> StoryKernel:
        kernel = build_kernel_from_init(
            project_id=project_id,
            story_bible=story_bible,
            character_bible=character_bible,
            outline=outline,
            narrative_contract=narrative_contract,
            entity_registry=entity_registry,
            entity_graph=entity_graph,
        )
        await self._store.init_db()
        await self._store.save_kernel(kernel)
        try:
            await self._store.save_snapshot(0)
        except ValueError as exc:
            if "in-memory" not in str(exc):
                raise
        return kernel

    async def initialize_short_project(
        self,
        *,
        project_id: str,
        spec: Any,
        blueprint: Any | None,
        beats: Any,
        execution_plan: dict[str, Any] | None = None,
        artifact_refs: dict[str, dict[str, Any]] | None = None,
    ) -> StoryKernel:
        kernel = StoryKernel(
            project_id=project_id,
            project_mode="short",
            current_chapter=0,
            title=str(getattr(spec, "title", "") or ""),
            premise=str(getattr(spec, "theme", "") or ""),
            entities=dedupe_entities(
                _short_entities(spec, blueprint, beats),
                prefer_existing_attributes=False,
            ),
            timeline=_short_timeline(blueprint, beats),
            promise_ledger=_short_promises(spec, blueprint),
            notes=_short_notes(spec, blueprint, execution_plan),
            artifact_refs=dict(artifact_refs or {}),
        )
        await self._store.init_db()
        await self._store.save_kernel(kernel)
        return kernel

    async def upsert_entities_from_known_names(
        self,
        *,
        project_id: str,
        names: list[str],
        entity_type: str = "character",
        source: str = "known_names",
        chapter_number: int = 0,
        step_names: str | Iterable[str] | None = None,
    ) -> StoryKernel:
        kernel = await self.load_or_create_kernel(project_id, "long")
        updated = upsert_entities(kernel, names, entity_type, source, chapter_number)
        if updated.model_dump(mode="json") != kernel.model_dump(mode="json"):
            self._enforce_contracts(step_names, before=kernel, after=updated)
            await self._store.save_kernel(updated)
        return updated

    async def apply_adjudication_report(
        self,
        *,
        project_id: str,
        report: Any,
        step_names: str | Iterable[str] | None = None,
    ) -> StoryKernel:
        from novel_forge.pipeline.steps.state_adjudication_step import (
            apply_adjudication_to_kernel,
        )

        kernel = await self.load_or_create_kernel(project_id, "long")
        updated = apply_adjudication_to_kernel(kernel, report)
        if updated.model_dump(mode="json") != kernel.model_dump(mode="json"):
            self._enforce_contracts(step_names, before=kernel, after=updated)
            await self._store.save_kernel(updated)
        return updated

    async def merge_chapter_outcome(
        self,
        *,
        project_id: str,
        outcome: Any,
        report: Any | None = None,
        step_names: str | Iterable[str] | None = None,
    ) -> StoryKernel:
        kernel = await self.load_or_create_kernel(project_id, "long")
        updated = StoryKernelMerger().merge_outcome(kernel, outcome)
        if report is not None:
            from novel_forge.pipeline.steps.state_adjudication_step import (
                apply_adjudication_to_kernel,
            )

            updated = apply_adjudication_to_kernel(updated, report)
        if updated.model_dump(mode="json") != kernel.model_dump(mode="json"):
            self._enforce_contracts(step_names, before=kernel, after=updated)
            await self._store.save_kernel(updated)
        return updated

    async def merge_short_outcome(
        self,
        *,
        project_id: str,
        final_text: str,
        eval_report: Any,
        creative_summary: Any | None,
        final_story_path: Path,
    ) -> StoryKernel:
        kernel = await self.load_or_create_kernel(project_id, "short")
        summary = _short_final_summary(eval_report, creative_summary)
        chapter_summaries = dict(kernel.chapter_summaries)
        if summary:
            chapter_summaries[1] = summary

        artifact_refs = dict(kernel.artifact_refs)
        artifact_refs["short_story_final"] = {
            "path": _relative_project_path(final_story_path),
            "sha1": hashlib.sha1(final_text.encode("utf-8")).hexdigest(),
            "chars": len(final_text),
            "source": "short_finalize",
        }
        artifact_refs["short_eval"] = {
            "overall_score": float(getattr(eval_report, "overall_score", 0.0) or 0.0),
            "passed": bool(getattr(eval_report, "passed", False)),
            "source": "short_finalize",
        }

        entities = _merge_short_creative_entities(kernel.entities, creative_summary)
        updated = kernel.model_copy(
            update={
                "current_chapter": 1,
                "entities": entities,
                "chapter_summaries": chapter_summaries,
                "artifact_refs": artifact_refs,
            }
        )
        await self._store.save_kernel(updated)
        return updated

    async def save_chapter_snapshot(self, chapter: int) -> None:
        await self._store.save_snapshot(chapter)

    def invalidate_projection_cache(
        self,
        *,
        db_path: str | Path | None = None,
        project_id: str = "",
        chapter_number: int | None = None,
    ) -> None:
        from novel_forge.pipeline.long.services.context.story_kernel_context import (
            invalidate_story_kernel_composer_cache,
        )

        invalidate_story_kernel_composer_cache(
            db_path=db_path,
            project_id=project_id,
            chapter_number=chapter_number,
        )


def upsert_entities(
    kernel: StoryKernel,
    names: list[str],
    entity_type: str,
    source: str,
    chapter_number: int,
) -> StoryKernel:
    entities = list(kernel.entities)
    by_key = {
        (str(getattr(item.entity_type, "value", item.entity_type)), item.name): index
        for index, item in enumerate(entities)
    }
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not name:
            continue
        key = (entity_type, name)
        if key in by_key:
            entity = entities[by_key[key]]
            entities[by_key[key]] = entity.model_copy(
                update={
                    "last_seen_chapter": max(entity.last_seen_chapter, chapter_number),
                }
            )
            continue
        entities.append(
            Entity(
                entity_id=stable_entity_id(entity_type, name),
                name=name,
                entity_type=entity_type,
                attributes={"source": source},
                source_chapter=chapter_number,
                last_seen_chapter=chapter_number,
            )
        )
        by_key[key] = len(entities) - 1
    return kernel.model_copy(update={"entities": entities})


def append_warning(
    kernel: StoryKernel,
    *,
    warning_type: str,
    source: str,
    details: dict[str, Any],
    entity_id: str = "",
    chapter_number: int = 0,
    work_unit: str = "",
) -> StoryKernel:
    warnings = list(kernel.structured_warnings)
    warnings.append(
        StoryKernelStructuredWarning(
            warning_type=warning_type,
            source=source,
            entity_id=entity_id,
            details=details,
            chapter_number=chapter_number,
            work_unit=work_unit,
        )
    )
    return kernel.model_copy(update={"structured_warnings": warnings[-200:]})


def _short_entities(spec: Any, blueprint: Any | None, beats: Any) -> list[Entity]:
    entities: list[Entity] = []

    def add(entity_type: str, name: Any, *, attrs: dict[str, Any] | None = None) -> None:
        text = str(name or "").strip()
        if not text:
            return
        entities.append(
            Entity(
                entity_id=stable_entity_id(entity_type, text),
                name=text,
                entity_type=entity_type,
                attributes={"source": "short_init", **(attrs or {})},
            )
        )

    for raw in (getattr(spec, "characters_hint", ""),):
        for name in _split_hint_names(raw):
            add("character", name)
    add("concept", getattr(spec, "genre", ""), attrs={"category": "genre"})
    add("concept", getattr(spec, "tone", ""), attrs={"category": "tone"})

    if blueprint is not None:
        anchor = getattr(blueprint, "anchor_elements", None)
        for character in getattr(anchor, "core_characters", []) or []:
            add(
                "character",
                getattr(character, "name", ""),
                attrs={"role": getattr(character, "role", "")},
            )
        for location in getattr(anchor, "primary_locations", []) or []:
            add("location", location)
        for phase in getattr(blueprint, "narrative_phases", []) or []:
            add("location", getattr(phase, "location", ""))
            for name in getattr(phase, "characters_present", []) or []:
                add("character", name)

    for beat in getattr(beats, "beats", []) or []:
        add("location", getattr(beat, "setting", ""))
        for name in getattr(beat, "characters_involved", []) or []:
            add("character", name)

    return entities


def _short_timeline(blueprint: Any | None, beats: Any) -> list[TimelineAnchor]:
    timeline: list[TimelineAnchor] = []
    for beat in getattr(beats, "beats", []) or []:
        sequence = int(getattr(beat, "sequence", 0) or 0)
        summary = str(getattr(beat, "summary", "") or "").strip()
        if not sequence or not summary:
            continue
        timeline.append(
            TimelineAnchor(
                anchor_id=f"short_beat_{sequence}",
                chapter=1,
                event=summary,
                characters_involved=[
                    stable_entity_id("character", name)
                    for name in (getattr(beat, "characters_involved", []) or [])
                    if str(name or "").strip()
                ],
                location=str(getattr(beat, "setting", "") or ""),
                significance="major" if sequence == 1 else "minor",
                tags=["short", "beat"],
            )
        )
    if timeline or blueprint is None:
        return timeline

    for index, phase in enumerate(getattr(blueprint, "narrative_phases", []) or [], start=1):
        event = str(
            getattr(phase, "key_event", "") or getattr(phase, "description", "") or ""
        ).strip()
        if not event:
            continue
        timeline.append(
            TimelineAnchor(
                anchor_id=f"short_phase_{index}",
                chapter=1,
                event=event,
                location=str(getattr(phase, "location", "") or ""),
                significance="major" if index == 1 else "minor",
                tags=["short", "phase"],
            )
        )
    return timeline


def _short_promises(spec: Any, blueprint: Any | None) -> list[PromiseLedger]:
    promises: list[PromiseLedger] = []

    def add(entry_id: str, description: Any, promise_type: str, status: str = "planted") -> None:
        text = str(description or "").strip()
        if not text:
            return
        promises.append(
            PromiseLedger(
                entry_id=entry_id,
                description=text,
                promise_type=promise_type,
                planted_chapter=1,
                status=status,
            )
        )

    add("short_conflict", getattr(spec, "conflict_hint", ""), "suspense")
    if blueprint is not None:
        anchor = getattr(blueprint, "anchor_elements", None)
        add("short_central_event", getattr(anchor, "central_event", ""), "promise")
        add("short_ending_strategy", getattr(blueprint, "ending_strategy", ""), "payoff", "paid")
    return promises


def _short_notes(spec: Any, blueprint: Any | None, execution_plan: dict[str, Any] | None) -> str:
    parts = [
        f"短篇类型：{getattr(spec, 'genre', '')}",
        f"短篇基调：{getattr(spec, 'tone', '')}",
    ]
    emotional_arc = ""
    if blueprint is not None:
        emotional_arc = str(blueprint.emotional_arc or "")
    if emotional_arc:
        parts.append(f"情感弧线：{emotional_arc}")
    if execution_plan:
        segment_count = len(execution_plan.get("segments", []) or [])
        if segment_count:
            parts.append(f"分段计划：{segment_count} 段")
    return "\n".join(part for part in parts if part and not part.endswith("："))


def _merge_short_creative_entities(
    entities: list[Entity],
    creative_summary: Any | None,
) -> list[Entity]:
    if creative_summary is None:
        return list(entities)
    merged = list(entities)
    by_id = {entity.entity_id: index for index, entity in enumerate(merged)}
    for character in getattr(creative_summary, "characters", []) or []:
        name = str(getattr(character, "name", "") or "").strip()
        if not name:
            continue
        entity_id = stable_entity_id("character", name)
        attrs = {
            "role": getattr(character, "role", ""),
            "arc_summary": getattr(character, "arc_summary", ""),
            "key_traits": list(getattr(character, "key_traits", []) or []),
            "source": "short_creative_summary",
        }
        if entity_id in by_id:
            entity = merged[by_id[entity_id]]
            merged[by_id[entity_id]] = entity.model_copy(
                update={
                    "attributes": {**entity.attributes, **attrs},
                    "last_seen_chapter": 1,
                }
            )
        else:
            merged.append(
                Entity(
                    entity_id=entity_id,
                    name=name,
                    entity_type="character",
                    attributes=attrs,
                    source_chapter=1,
                    last_seen_chapter=1,
                )
            )
            by_id[entity_id] = len(merged) - 1
    return dedupe_entities(merged, prefer_existing_attributes=False)


def _short_final_summary(eval_report: Any, creative_summary: Any | None) -> str:
    if creative_summary is not None:
        narrative = getattr(creative_summary, "narrative_analysis", None)
        ending = str(getattr(narrative, "ending_impact", "") or "").strip()
        pacing = str(getattr(narrative, "pacing_assessment", "") or "").strip()
        if ending or pacing:
            return "；".join(part for part in (pacing, ending) if part)
    return str(getattr(eval_report, "summary", "") or "").strip()


def _split_hint_names(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    normalized = text.replace("，", ",").replace("、", ",").replace("；", ",")
    parts = [part.strip() for part in normalized.split(",")]
    return [part for part in parts if 1 <= len(part) <= 24][:12]


def _relative_project_path(path: Path) -> str:
    parts = path.parts
    for marker in ("chapters", "reports", "plans", "drafts", "states"):
        if marker in parts:
            idx = parts.index(marker)
            return str(Path(*parts[idx:]))
    return path.name


__all__ = [
    "ProjectMode",
    "StoryKernelStateWriter",
    "append_warning",
    "upsert_entities",
]
