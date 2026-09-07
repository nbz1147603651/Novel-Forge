"""Lightweight repair policies for core initialization artifacts."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from novel_forge.core.schemas.bible import CharacterBible, StoryBible
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.utils.repair_target_resolver import escape_json_pointer
from novel_forge.narrative_state.schemas import EntityRecord, EntityRegistry, stable_id
from novel_forge.pipeline.long.services.init_repair.models import (
    InitArtifact,
    InitRepairContext,
    InitRepairIssue,
    InitRepairIssueKind,
    InitRepairReport,
)


class StoryBibleRepairPolicy:
    """Normalize and validate ``StoryBible`` without inventing semantic detail."""

    artifact = InitArtifact.STORY_BIBLE

    def normalize(self, payload: Any, ctx: InitRepairContext) -> StoryBible:
        return StoryBible.model_validate(_payload_dict(payload))

    def validate(self, payload: Any, ctx: InitRepairContext) -> InitRepairReport:
        try:
            bible = self.normalize(payload, ctx)
        except ValidationError as exc:
            return _validation_report("story_bible", exc)

        errors: list[str] = []
        warnings: list[str] = []
        issues: list[InitRepairIssue] = []
        if not bible.premise.strip():
            errors.append("story_bible.premise 不能为空。")
            issues.append(_issue(errors[-1], field="premise"))
        if not (bible.rules or bible.geography.strip() or bible.magic_or_tech.strip()):
            warnings.append("story_bible 缺少明确世界规则或世界设定信息。")
            issues.append(
                _issue(
                    warnings[-1],
                    field="rules",
                    severity="warning",
                    kind=InitRepairIssueKind.RANGE_OR_COVERAGE,
                )
            )
        return InitRepairReport(errors=errors, warnings=warnings, issues=issues, raw=bible)

    async def llm_repair(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> StoryBible:
        raise RuntimeError("story bible repair is local-only")

    def local_fallback(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> StoryBible | None:
        data = _payload_dict(payload)
        spec = _story_spec_from_context(ctx)
        if spec is not None:
            if not str(data.get("title") or "").strip():
                data["title"] = spec.title
            if not str(data.get("premise") or "").strip():
                data["premise"] = spec.theme
            if not str(data.get("tone") or "").strip():
                data["tone"] = spec.tone
            if not str(data.get("geography") or "").strip():
                data["geography"] = spec.world_hint
            if not str(data.get("magic_or_tech") or "").strip():
                data["magic_or_tech"] = spec.world_hint
            if not data.get("themes"):
                data["themes"] = [spec.theme] if spec.theme else []
            if spec.world_hint and not data.get("rules"):
                data["rules"] = [spec.world_hint]
        if not str(data.get("premise") or "").strip():
            return None
        return StoryBible.model_validate(data)


class CharacterBibleRepairPolicy:
    """Normalize, validate, and minimally backfill character profiles."""

    artifact = InitArtifact.CHARACTER_BIBLE

    def normalize(self, payload: Any, ctx: InitRepairContext) -> CharacterBible:
        return CharacterBible.model_validate(_payload_dict(payload))

    def validate(self, payload: Any, ctx: InitRepairContext) -> InitRepairReport:
        try:
            bible = self.normalize(payload, ctx)
        except ValidationError as exc:
            return _validation_report("character_bible", exc)

        errors: list[str] = []
        issues: list[InitRepairIssue] = []
        names = [profile.name.strip() for profile in bible.characters if profile.name.strip()]
        if not names:
            errors.append("character_bible.characters 至少需要一个具名角色。")
            issues.append(_issue(errors[-1], field="characters"))
        if len(names) != len(set(names)):
            errors.append("character_bible.characters 存在重复角色名。")
            issues.append(
                _issue(
                    errors[-1],
                    field="characters",
                    kind=InitRepairIssueKind.RANGE_OR_COVERAGE,
                )
            )
        return InitRepairReport(errors=errors, issues=issues, raw=bible)

    async def llm_repair(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> CharacterBible:
        raise RuntimeError("character bible repair is local-only")

    def local_fallback(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> CharacterBible | None:
        data = _payload_dict(payload)
        characters = [
            item for item in data.get("characters", []) if isinstance(item, dict)
        ]
        repaired: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in characters:
            name = str(item.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            repaired.append(item)
        if not repaired:
            name = _first_character_hint(_story_spec_from_context(ctx))
            if not name:
                return None
            repaired.append(
                {
                    "name": name,
                    "role": "protagonist",
                    "personality": "由初始化修复策略从创作简述中保留的主角占位。",
                    "notes": "本地回填：原角色圣经缺少可用角色。",
                }
            )
        return CharacterBible.model_validate({"characters": repaired})


class EntityRegistryRepairPolicy:
    """Normalize and derive a minimal registry from validated init artifacts."""

    artifact = InitArtifact.ENTITY_REGISTRY

    def normalize(self, payload: Any, ctx: InitRepairContext) -> EntityRegistry:
        return EntityRegistry.model_validate(_payload_dict(payload))

    def validate(self, payload: Any, ctx: InitRepairContext) -> InitRepairReport:
        try:
            registry = self.normalize(payload, ctx)
        except ValidationError as exc:
            return _validation_report("entity_registry", exc)

        errors: list[str] = []
        warnings: list[str] = []
        issues: list[InitRepairIssue] = []
        ids = [item.entity_id for item in registry.entities if item.entity_id]
        if not registry.entities:
            errors.append("entity_registry.entities 不能为空。")
            issues.append(_issue(errors[-1], field="entities"))
        if len(ids) != len(set(ids)):
            errors.append("entity_registry.entities 存在重复 entity_id。")
            issues.append(
                _issue(
                    errors[-1],
                    field="entities",
                    kind=InitRepairIssueKind.RANGE_OR_COVERAGE,
                )
            )
        nameless = [item.entity_id for item in registry.entities if not item.name.strip()]
        if nameless:
            warnings.append(f"entity_registry 存在缺少 name 的实体：{nameless[:5]}")
            issues.append(
                _issue(
                    warnings[-1],
                    field="entities",
                    severity="warning",
                    kind=InitRepairIssueKind.SCHEMA_SHAPE,
                )
            )
        return InitRepairReport(errors=errors, warnings=warnings, issues=issues, raw=registry)

    async def llm_repair(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> EntityRegistry:
        raise RuntimeError("entity registry repair is local-only")

    def local_fallback(
        self,
        payload: Any,
        report: InitRepairReport,
        ctx: InitRepairContext,
    ) -> EntityRegistry | None:
        entities: list[EntityRecord] = []
        seen: set[str] = set()

        character_bible = _character_bible_from_context(ctx)
        if character_bible is not None:
            for profile in character_bible.characters:
                entity_id = profile.character_id or stable_id("char", profile.name)
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                entities.append(
                    EntityRecord(
                        entity_id=entity_id,
                        name=profile.name,
                        entity_type="character",
                        aliases=[],
                        source="character_bible",
                        notes=profile.notes or profile.personality,
                    )
                )

        story_bible = _story_bible_from_context(ctx)
        if story_bible is not None:
            for name in _split_location_names(story_bible.geography):
                entity_id = stable_id("loc", name)
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                entities.append(
                    EntityRecord(
                        entity_id=entity_id,
                        name=name,
                        entity_type="location",
                        source="story_bible.geography",
                    )
                )

        if not entities:
            return None
        return EntityRegistry(entities=entities)


def _payload_dict(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, dict) else {}
    if isinstance(payload, dict):
        return dict(payload)
    return {}


def _story_spec_from_context(ctx: InitRepairContext) -> StorySpec | None:
    raw = ctx.artifacts.get("spec")
    if isinstance(raw, StorySpec):
        return raw
    if isinstance(raw, dict):
        try:
            return StorySpec.model_validate(raw)
        except ValidationError:
            return None
    return None


def _story_bible_from_context(ctx: InitRepairContext) -> StoryBible | None:
    raw = ctx.artifacts.get("story_bible")
    if isinstance(raw, StoryBible):
        return raw
    if isinstance(raw, dict):
        try:
            return StoryBible.model_validate(raw)
        except ValidationError:
            return None
    return None


def _character_bible_from_context(ctx: InitRepairContext) -> CharacterBible | None:
    raw = ctx.artifacts.get("character_bible")
    if isinstance(raw, CharacterBible):
        return raw
    if isinstance(raw, dict):
        try:
            return CharacterBible.model_validate(raw)
        except ValidationError:
            return None
    return None


def _first_character_hint(spec: StorySpec | None) -> str:
    if spec is None:
        return ""
    hint = str(spec.characters_hint or "").strip()
    for token in re.split(r"[、,，;；\s]+", hint):
        clean = token.strip("：:。.!！?？「」《》")
        if 2 <= len(clean) <= 8:
            return clean
    return "主角" if spec.theme else ""


def _split_location_names(text: str) -> list[str]:
    names: list[str] = []
    for token in re.split(r"[、,，;；/／\n]+", str(text or "")):
        clean = token.strip(" 。.!！?？「」《》")
        if 2 <= len(clean) <= 24 and clean not in names:
            names.append(clean)
    return names[:12]


def _validation_report(field: str, exc: ValidationError) -> InitRepairReport:
    message = str(exc)
    issues: list[InitRepairIssue] = []
    for error in exc.errors(include_url=False):
        location = [str(item) for item in error.get("loc", ())]
        # ``field`` is the artifact label in legacy callers; Pydantic ``loc``
        # is the actual path inside that artifact.
        path_parts = location or ([field] if field else [])
        json_pointer = "/" + "/".join(escape_json_pointer(item) for item in path_parts)
        issues.append(
            _issue(
                str(error.get("msg") or message),
                field=".".join(path_parts),
                metadata={
                    "error_type": str(error.get("type") or type(exc).__name__),
                    "json_pointer": json_pointer,
                    "actual_raw": error.get("input"),
                    "comparator_id": "pydantic_schema_v1",
                },
            )
        )
    return InitRepairReport(
        errors=[message],
        issues=issues
        or [
            _issue(
                message,
                field=field,
                metadata={"error_type": type(exc).__name__},
            )
        ],
        raw=exc,
    )


def _issue(
    message: str,
    *,
    field: str,
    severity: str = "error",
    kind: InitRepairIssueKind = InitRepairIssueKind.SCHEMA_SHAPE,
    metadata: dict[str, Any] | None = None,
) -> InitRepairIssue:
    return InitRepairIssue(
        kind=kind,
        message=message,
        field=field,
        severity=severity,
        metadata=metadata or {},
    )


__all__ = [
    "CharacterBibleRepairPolicy",
    "EntityRegistryRepairPolicy",
    "StoryBibleRepairPolicy",
]
