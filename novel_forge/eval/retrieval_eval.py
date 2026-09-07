"""Phase 0a planning-stage retrieval evaluation.

The evaluator is intentionally side-effect free except for the optional
``persist_retrieval_eval_report`` helper. It does not call models and does not
feed metrics back into generation decisions.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from novel_forge.core.schemas.continuity import ChapterPlan
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.core.schemas.retrieval_eval import (
    RetrievalEvalGoldPayload,
    RetrievalEvalReportPayload,
    RetrievalEvalRetrievedPayload,
    RetrievalEvalSceneMetricPayload,
)
from novel_forge.core.utils.field_extractor import field as extract_field
from novel_forge.core.utils.string import clean_str
from novel_forge.eval.retrieval_tokenizer import tokenize_for_recall

EVALUATOR_VERSION = "phase0a.v0.1"


@dataclass(frozen=True)
class GoldSet:
    characters: frozenset[str] = frozenset()
    event_tokens: frozenset[str] = frozenset()
    scene_count: int = 0
    source_fields: tuple[str, ...] = ()

    def to_payload(self) -> RetrievalEvalGoldPayload:
        return RetrievalEvalGoldPayload(
            characters=sorted(self.characters),
            event_tokens=sorted(self.event_tokens),
            scene_count=self.scene_count,
            source_fields=list(self.source_fields),
        )


@dataclass(frozen=True)
class RetrievedSet:
    character_names: frozenset[str] = frozenset()
    noisy_entity_names: frozenset[str] = frozenset()
    event_tokens: frozenset[str] = frozenset()
    source_counts: dict[str, int] = field(default_factory=dict)

    def to_payload(self) -> RetrievalEvalRetrievedPayload:
        return RetrievalEvalRetrievedPayload(
            character_names=sorted(self.character_names),
            noisy_entity_names=sorted(self.noisy_entity_names),
            event_tokens=sorted(self.event_tokens),
            source_counts=dict(self.source_counts),
        )


@dataclass(frozen=True)
class SceneRetrievalProjection:
    scene_id: str
    gold_characters: frozenset[str] = frozenset()
    gold_event_tokens: frozenset[str] = frozenset()
    character_recall: float | None = None
    event_token_recall: float | None = None
    missed_characters: tuple[str, ...] = ()
    missed_event_tokens: tuple[str, ...] = ()

    def to_payload(self) -> RetrievalEvalSceneMetricPayload:
        return RetrievalEvalSceneMetricPayload(
            scene_id=self.scene_id,
            gold_characters=sorted(self.gold_characters),
            gold_event_tokens=sorted(self.gold_event_tokens),
            character_recall=self.character_recall,
            event_token_recall=self.event_token_recall,
            missed_characters=list(self.missed_characters),
            missed_event_tokens=list(self.missed_event_tokens),
        )


@dataclass(frozen=True)
class RetrievalEvalReport:
    chapter: int
    point: Literal["A_planning"] = "A_planning"
    evaluator_version: str = EVALUATOR_VERSION
    retrieval_scope: Literal["plan_prompt_context"] = "plan_prompt_context"
    duration_ms: float = 0.0
    gold: GoldSet = field(default_factory=GoldSet)
    retrieved: RetrievedSet = field(default_factory=RetrievedSet)
    aggregate: dict[str, float | None] = field(default_factory=dict)
    scene_metrics: tuple[SceneRetrievalProjection, ...] = ()
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_payload(self, *, created_at: str | None = None) -> RetrievalEvalReportPayload:
        return RetrievalEvalReportPayload(
            chapter=self.chapter,
            point=self.point,
            evaluator_version=self.evaluator_version,
            retrieval_scope=self.retrieval_scope,
            duration_ms=self.duration_ms,
            gold=self.gold.to_payload(),
            retrieved=self.retrieved.to_payload(),
            aggregate=dict(self.aggregate),
            scene_metrics=[item.to_payload() for item in self.scene_metrics],
            config_snapshot=dict(self.config_snapshot),
            created_at=created_at if created_at is not None else self.created_at,
        )


def derive_gold_set(
    *,
    plan: ChapterPlan,
    chapter_outline: ChapterOutline,
    max_scenes: int | None = None,
) -> GoldSet:
    """Derive deterministic chapter gold anchors from the final plan and outline."""

    characters: set[str] = set()
    event_tokens: set[str] = set()
    source_fields: set[str] = set()

    _add_text(characters, extract_field(chapter_outline, "pov_character", ""))
    _add_text(characters, extract_field(chapter_outline, "pov_character_name", ""))
    _add_many(characters, extract_field(chapter_outline, "involved_characters", []))
    _add_many(characters, extract_field(chapter_outline, "involved_character_names", []))
    if characters:
        source_fields.add("chapter_outline.characters")

    scenes = _limited_scenes(plan, max_scenes=max_scenes)
    for scene in scenes:
        before = len(characters)
        _add_text(characters, extract_field(scene, "pov_character", ""))
        _add_many(characters, extract_field(scene, "required_characters", []))
        if len(characters) > before:
            source_fields.add("scene_intents.characters")

        scene_text = _scene_event_text(scene)
        if scene_text:
            event_tokens.update(tokenize_for_recall(scene_text))
            source_fields.add("scene_intents.events")

    plan_text = _join_texts(
        extract_field(plan, "key_revelations", []),
        extract_field(plan, "foreshadowing_plan", []),
    )
    if plan_text:
        event_tokens.update(tokenize_for_recall(plan_text))
        source_fields.add("chapter_plan.event_fields")

    return GoldSet(
        characters=frozenset(characters),
        event_tokens=frozenset(event_tokens),
        scene_count=len(scenes),
        source_fields=tuple(sorted(source_fields)),
    )


def extract_retrieved_set(
    *,
    plan_canon_context: dict[str, Any] | None,
    plan_memory_hints: dict[str, Any] | None,
) -> RetrievedSet:
    """Extract retrieved anchors from the actual prompt-bound Plan contexts."""

    canon = plan_canon_context or {}
    memory = plan_memory_hints or {}
    character_names: set[str] = set()
    noisy_entity_names: set[str] = set()
    event_tokens: set[str] = set()
    source_counts: dict[str, int] = {}

    raw_chars = canon.get("characters")
    if isinstance(raw_chars, dict):
        for name, payload in raw_chars.items():
            clean_name = clean_str(name)
            if not clean_name:
                continue
            entity_type = _entity_type(payload)
            if entity_type and entity_type != "character":
                noisy_entity_names.add(clean_name)
                continue
            character_names.add(clean_name)
        source_counts["canon.characters"] = len(raw_chars)

    for key, text_key in (
        ("recent_events", "event"),
        ("active_foreshadowing", "description"),
    ):
        values = _mapping_text_values(canon.get(key), text_key)
        if values:
            event_tokens.update(tokenize_for_recall(" ".join(values)))
        source_counts[f"canon.{key}"] = len(values)

    for key in ("relevant_history", "previous_chapter_events"):
        values = _mapping_text_values(memory.get(key), "event_summary")
        if values:
            event_tokens.update(tokenize_for_recall(" ".join(values)))
        source_counts[f"memory.{key}"] = len(values)

    return RetrievedSet(
        character_names=frozenset(character_names),
        noisy_entity_names=frozenset(noisy_entity_names),
        event_tokens=frozenset(event_tokens),
        source_counts=source_counts,
    )


def evaluate_retrieval_against_plan(
    *,
    plan: ChapterPlan,
    chapter_outline: ChapterOutline,
    plan_canon_context: dict[str, Any] | None,
    plan_memory_hints: dict[str, Any] | None,
    chapter_number: int,
    max_scenes: int | None = None,
    config_snapshot: dict[str, Any] | None = None,
) -> RetrievalEvalReport:
    """Evaluate Point-A Plan prompt context against deterministic plan anchors."""

    started_at = time.perf_counter()
    gold = derive_gold_set(
        plan=plan,
        chapter_outline=chapter_outline,
        max_scenes=max_scenes,
    )
    retrieved = extract_retrieved_set(
        plan_canon_context=plan_canon_context,
        plan_memory_hints=plan_memory_hints,
    )
    aggregate = {
        "character_recall": _recall(retrieved.character_names, gold.characters),
        "event_token_recall": _recall(retrieved.event_tokens, gold.event_tokens),
        "entity_noise_rate": _safe_rate(
            len(retrieved.noisy_entity_names),
            len(retrieved.noisy_entity_names) + len(retrieved.character_names),
        ),
    }
    scene_metrics = tuple(
        _evaluate_scene_projection(scene, retrieved, max_event_tokens=20)
        for scene in _limited_scenes(plan, max_scenes=max_scenes)
    )

    return RetrievalEvalReport(
        chapter=chapter_number,
        duration_ms=(time.perf_counter() - started_at) * 1000,
        gold=gold,
        retrieved=retrieved,
        aggregate=aggregate,
        scene_metrics=scene_metrics,
        config_snapshot=config_snapshot or {},
        created_at=_utc_now_iso(),
    )


def persist_retrieval_eval_report(
    storage: Any,
    layout: Any,
    chapter_number: int,
    report: RetrievalEvalReport,
) -> Path:
    """Persist a retrieval evaluation report using the project storage backend."""

    created_at = report.created_at or _utc_now_iso()
    payload = report.to_payload(created_at=created_at).model_dump(mode="json")
    out_path = Path(layout.retrieval_eval_report_path(chapter_number))
    storage.save_json(out_path, payload)
    return out_path


def _evaluate_scene_projection(
    scene: Any,
    retrieved: RetrievedSet,
    *,
    max_event_tokens: int,
) -> SceneRetrievalProjection:
    scene_id = clean_str(extract_field(scene, "scene_id", "")) or "scene"
    gold_characters: set[str] = set()
    _add_text(gold_characters, extract_field(scene, "pov_character", ""))
    _add_many(gold_characters, extract_field(scene, "required_characters", []))
    gold_event_tokens = tokenize_for_recall(_scene_event_text(scene))

    missed_characters = tuple(sorted(gold_characters - retrieved.character_names))
    missed_event_tokens = tuple(sorted(gold_event_tokens - retrieved.event_tokens)[:max_event_tokens])
    return SceneRetrievalProjection(
        scene_id=scene_id,
        gold_characters=frozenset(gold_characters),
        gold_event_tokens=frozenset(gold_event_tokens),
        character_recall=_recall(retrieved.character_names, gold_characters),
        event_token_recall=_recall(retrieved.event_tokens, gold_event_tokens),
        missed_characters=missed_characters,
        missed_event_tokens=missed_event_tokens,
    )


def _limited_scenes(plan: ChapterPlan, *, max_scenes: int | None) -> list[Any]:
    scenes = list(extract_field(plan, "scene_intents", []) or [])
    if max_scenes is None:
        return scenes
    return scenes[: max(0, int(max_scenes))]


def _scene_event_text(scene: Any) -> str:
    return _join_texts(
        extract_field(scene, "summary", ""),
        extract_field(scene, "owned_events", []),
        extract_field(scene, "owned_revelations", []),
        extract_field(scene, "required_outcome", ""),
        extract_field(scene, "dramatic_question", ""),
    )


def _join_texts(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        for item in _iter_values(value):
            text = clean_str(item)
            if text:
                parts.append(text)
    return " ".join(parts)


def _add_text(target: set[str], value: Any) -> None:
    text = clean_str(value)
    if text:
        target.add(text)


def _add_many(target: set[str], value: Any) -> None:
    for item in _iter_values(value):
        _add_text(target, item)


def _iter_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        return value.values()
    if isinstance(value, list | tuple | set):
        return value
    return [value]


def _mapping_text_values(value: Any, key: str) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = clean_str(item.get(key, ""))
        else:
            text = clean_str(extract_field(item, key, ""))
        if text:
            result.append(text)
    return result


def _entity_type(payload: Any) -> str:
    if isinstance(payload, dict):
        return clean_str(payload.get("entity_type", "")).lower()
    return clean_str(extract_field(payload, "entity_type", "")).lower()


def _recall(retrieved: frozenset[str] | set[str], gold: frozenset[str] | set[str]) -> float | None:
    if not gold:
        return None
    return len(set(retrieved) & set(gold)) / len(gold)


def _safe_rate(num: int, denom: int) -> float | None:
    if denom <= 0:
        return None
    return num / denom


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
