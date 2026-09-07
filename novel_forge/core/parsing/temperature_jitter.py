"""Creative temperature jitter policy.

This module keeps the creative-temperature classification and sampling math
small, explicit, and reusable by both gateway routing and desktop settings UI.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from math import floor
from typing import Literal

from novel_forge.common.constants import TaskType

TemperatureJitterScope = Literal["recommended", "chapter_core", "init_and_chapter", "custom"]

CREATIVE_TEMPERATURE_SCOPE_RECOMMENDED = "recommended"
CREATIVE_TEMPERATURE_SCOPE_CHAPTER_CORE = "chapter_core"
CREATIVE_TEMPERATURE_SCOPE_INIT_AND_CHAPTER = "init_and_chapter"
CREATIVE_TEMPERATURE_SCOPE_CUSTOM = "custom"

CHAPTER_CORE_TEMPERATURE_TASKS: frozenset[TaskType] = frozenset(
    {
        TaskType.PLAN_CHAPTER,
        TaskType.PLAN_CHAPTER_SCENES,
        TaskType.BRIDGE_CHAPTER,
        TaskType.DRAFT_CHAPTER,
        TaskType.DRAFT_SCENE,
        TaskType.EDIT_CHAPTER,
        TaskType.POLISH_CHAPTER,
        TaskType.POLISH_SUBPLOT,
    }
)

INIT_CREATIVE_TEMPERATURE_TASKS: frozenset[TaskType] = frozenset(
    {
        TaskType.INIT_STORY_BIBLE,
        TaskType.INIT_STORY_CORE_PREMISE,
        TaskType.INIT_STORY_WORLD_RULES,
        TaskType.INIT_STORY_CONTINUITY_RULES,
        TaskType.INIT_STORY_THEMES_AND_SYMBOLS,
        TaskType.INIT_CHARACTER_BIBLE,
        TaskType.INIT_CHARACTER_ROSTER,
        TaskType.INIT_CHARACTER_PROFILE_BATCH,
        TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
        TaskType.INIT_CHARACTER_ARC_PLAN,
        TaskType.PROFILE_STYLE,
        TaskType.PROFILE_STRUCTURE,
        TaskType.DERIVE_EDITORIAL_CONTRACT,
        TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
        TaskType.DERIVE_EDITORIAL_STRUCTURE,
        TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
        TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES,
        TaskType.PLAN_OUTLINE,
        TaskType.PLAN_OUTLINE_BATCH,
        TaskType.PLAN_OUTLINE_CONTINUE,
        TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS,
        TaskType.POLISH_OUTLINE,
    }
)

RECOMMENDED_CREATIVE_TEMPERATURE_TASKS: frozenset[TaskType] = frozenset(
    {
        # Short story creation
        TaskType.SPEC_ENRICH,
        TaskType.BLUEPRINT_ELEMENT_SELECT,
        TaskType.BEATS,
        TaskType.DRAFT,
        TaskType.EDIT,
        TaskType.SHORT_BLUEPRINT,
        # Long initialization and planning
        *INIT_CREATIVE_TEMPERATURE_TASKS,
        # Long chapter creation
        *CHAPTER_CORE_TEMPERATURE_TASKS,
        # Character and user-facing creative tools
        TaskType.ENRICH_CHARACTER,
        TaskType.INTRODUCE_CHARACTER,
        TaskType.ADJUST_OUTLINE,
        TaskType.GENERATE_CONFIG,
        TaskType.POLISH_CONFIG,
    }
)

INIT_AND_CHAPTER_TEMPERATURE_TASKS: frozenset[TaskType] = frozenset(
    {*INIT_CREATIVE_TEMPERATURE_TASKS, *CHAPTER_CORE_TEMPERATURE_TASKS}
)

PROTECTED_TEMPERATURE_TASKS: frozenset[TaskType] = frozenset(
    {
        TaskType.EVALUATE,
        TaskType.EXTRACT_CANON,
        TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT,
        TaskType.EXTRACT_CANON_DELTA,
        TaskType.EXTRACT_CREATIVE_REPORT,
        TaskType.EXTRACT_CHARACTER_STATE_DELTAS,
        TaskType.EXTRACT_RELATIONSHIP_DELTAS,
        TaskType.EXTRACT_PLOT_THREAD_DELTAS,
        TaskType.CHECK_ALIGNMENT,
        TaskType.VALIDATE_SCENE_PLAN,
        TaskType.ELEMENT_PROGRESS_ARBITER,
        TaskType.CHECK_CHAPTER,
        TaskType.CHECK_CONTINUITY,
        TaskType.REPAIR_CONTINUITY,
        TaskType.REPAIR_CAUSAL,
        TaskType.VALIDATE_CAUSAL,
        TaskType.PATCH_CHAPTER,
        TaskType.VOLUME_AUDIT,
        TaskType.ADJUDICATE_CHARACTER_INTRODUCTION,
        TaskType.CONTEXT_COMPRESS,
        TaskType.ADAPTIVE_COMPRESS,
        TaskType.PLOT_GUARD_JUDGE,
        TaskType.BOOK_CONSISTENCY,
        TaskType.BOOK_CONSISTENCY_NAMING,
        TaskType.BOOK_CONSISTENCY_TIMELINE,
        TaskType.BOOK_CONSISTENCY_WORLD_RULE,
        TaskType.BOOK_CONSISTENCY_CHARACTER_STATE,
        TaskType.BOOK_CONSISTENCY_PLOT_THREAD,
        TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT,
        TaskType.BOOK_CONSISTENCY_VERIFY,
        TaskType.SHORT_CREATIVE_SUMMARY,
        TaskType.VERIFY_COMPRESSION,
        TaskType.EXTRACT_MOTIFS,
        TaskType.CRITIC_CONTINUITY,
        TaskType.CRITIC_CHARACTER,
        TaskType.CRITIC_CAUSAL,
        TaskType.CRITIC_STRENGTHS,
        TaskType.SUMMARIZE_CHAPTER,
        TaskType.SUMMARIZE_VOLUME,
        TaskType.SUMMARIZE_ARC,
        TaskType.SUMMARIZE_SCENE,
        TaskType.EVALUATE_READING_POWER,
        TaskType.CHECK_EDITORIAL,
        TaskType.BOOK_EDITORIAL_AUDIT,
        TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT,
        TaskType.BOOK_EDITORIAL_VOICE_AUDIT,
        TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT,
        TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT,
        TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT,
        TaskType.REPAIR_READING_POWER,
        TaskType.REPAIR_SEMANTIC_VERIFY,
        TaskType.GUARD_CONSTRAINT_CHECK,
        TaskType.MACRO_GUARD_AUDIT,
        TaskType.KNOWLEDGE_BOUNDARY_AUDIT,
        TaskType.REPAIR_GUARDRAIL,
        TaskType.INIT_ENTITY_REGISTRY,
        TaskType.INIT_NARRATIVE_CONTRACT,
        TaskType.PLAN_CHAPTER_CONTRACTS,
        TaskType.DERIVE_INIT_COHERENCE_PROFILE,
        TaskType.REFINE_INIT_COHERENCE_PROFILE,
        TaskType.INIT_COHERENCE_ONTOLOGY,
        TaskType.INIT_COHERENCE_EXTRACTION_GUIDE,
        TaskType.INIT_COHERENCE_CONFLICT_RULES,
        TaskType.INIT_COHERENCE_PAYOFF_RULES,
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
        TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
        TaskType.ADJUDICATE_BLUEPRINT_COHERENCE,
        TaskType.ADJUDICATE_OUTLINE_INHERITANCE,
        TaskType.ADJUDICATE_CONTRACT_COHERENCE,
        TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
        TaskType.ADJUDICATE_STATE_DELTA,
        TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        TaskType.ADJUDICATE_FACT_CONFLICT,
        TaskType.ADJUDICATE_FINAL_STATE,
        TaskType.REPAIR_ADJUDICATED_ISSUE,
        TaskType.HUMANIZE_SCAN,
    }
)

# Backward-compatible name used by existing tests/UI code.
CREATIVE_TEMPERATURE_TASKS = RECOMMENDED_CREATIVE_TEMPERATURE_TASKS


@dataclass(frozen=True)
class TemperatureJitterResult:
    """Resolved temperature and metadata for one routed model request."""

    base_temperature: float
    actual_temperature: float
    range_min: float
    range_max: float
    enabled: bool
    reason: str


def clamp_temperature(value: float) -> float:
    """Clamp a model temperature into the gateway-supported range."""

    return max(0.0, min(2.0, float(value)))


def _round_jitter_temperature(value: float) -> float:
    """Round sampled jitter temperature to a practical one-decimal value."""

    clamped = clamp_temperature(value)
    return clamp_temperature(floor(clamped * 10 + 0.5) / 10)


def normalize_temperature_jitter_scope(scope: str | None) -> TemperatureJitterScope:
    """Return a valid scope, falling back to the recommended preset."""

    normalized = str(scope or "").strip().lower()
    if normalized in {
        CREATIVE_TEMPERATURE_SCOPE_RECOMMENDED,
        CREATIVE_TEMPERATURE_SCOPE_CHAPTER_CORE,
        CREATIVE_TEMPERATURE_SCOPE_INIT_AND_CHAPTER,
        CREATIVE_TEMPERATURE_SCOPE_CUSTOM,
    }:
        return normalized  # type: ignore[return-value]
    return CREATIVE_TEMPERATURE_SCOPE_RECOMMENDED


def parse_temperature_task_keys(value: str | Iterable[str | TaskType] | None) -> frozenset[TaskType]:
    """Parse comma-separated or iterable task keys into valid ``TaskType`` values."""

    if value is None:
        return frozenset()
    if isinstance(value, str):
        raw_items = [part.strip() for part in value.split(",")]
    else:
        raw_items = [part if isinstance(part, TaskType) else str(part or "").strip() for part in value]

    parsed: set[TaskType] = set()
    for raw in raw_items:
        if isinstance(raw, TaskType):
            parsed.add(raw)
            continue
        if not raw:
            continue
        try:
            parsed.add(TaskType(raw))
        except ValueError:
            continue
    return frozenset(parsed)


def is_temperature_jitter_protected(task_type: TaskType) -> bool:
    """Return whether a task is intentionally excluded from jitter."""

    return task_type in PROTECTED_TEMPERATURE_TASKS


def resolve_temperature_jitter_tasks(
    *,
    scope: str | None = CREATIVE_TEMPERATURE_SCOPE_RECOMMENDED,
    custom_tasks: str | Iterable[str | TaskType] | None = None,
) -> frozenset[TaskType]:
    """Resolve the user-facing scope/custom selection into an effective task set."""

    normalized = normalize_temperature_jitter_scope(scope)
    if normalized == CREATIVE_TEMPERATURE_SCOPE_CHAPTER_CORE:
        selected = CHAPTER_CORE_TEMPERATURE_TASKS
    elif normalized == CREATIVE_TEMPERATURE_SCOPE_INIT_AND_CHAPTER:
        selected = INIT_AND_CHAPTER_TEMPERATURE_TASKS
    elif normalized == CREATIVE_TEMPERATURE_SCOPE_CUSTOM:
        parsed = parse_temperature_task_keys(custom_tasks)
        selected = parsed
    else:
        selected = RECOMMENDED_CREATIVE_TEMPERATURE_TASKS

    return frozenset(task for task in selected if not is_temperature_jitter_protected(task))


def serialize_temperature_task_keys(tasks: Iterable[TaskType | str]) -> str:
    """Serialize task keys for env storage."""

    keys = []
    for task in tasks:
        if isinstance(task, TaskType):
            keys.append(task.value)
        else:
            key = str(task or "").strip()
            if key:
                keys.append(key)
    return ",".join(sorted(set(keys)))


def is_creative_temperature_task(
    task_type: TaskType,
    *,
    scope: str | None = CREATIVE_TEMPERATURE_SCOPE_RECOMMENDED,
    custom_tasks: str | Iterable[str | TaskType] | None = None,
) -> bool:
    """Return whether a task can use creative temperature jitter."""

    return task_type in resolve_temperature_jitter_tasks(scope=scope, custom_tasks=custom_tasks)


def resolve_temperature_jitter(
    *,
    task_type: TaskType,
    base_temperature: float,
    enabled: bool,
    up_delta: float,
    down_delta: float,
    scope: str | None = CREATIVE_TEMPERATURE_SCOPE_RECOMMENDED,
    custom_tasks: str | Iterable[str | TaskType] | None = None,
    allowed: bool = True,
    sampler: Callable[[float, float], float] | None = None,
) -> TemperatureJitterResult:
    """Resolve the actual temperature for a single model call.

    ``base_temperature`` is the user's visible setting.  When jitter is enabled
    and the task is creative, the actual call samples from:
    ``[base - down_delta, base + up_delta]`` after clamping to ``0.0..2.0``.
    """

    base = clamp_temperature(base_temperature)
    up = max(0.0, float(up_delta))
    down = max(0.0, float(down_delta))

    if not allowed:
        return TemperatureJitterResult(
            base_temperature=base,
            actual_temperature=base,
            range_min=base,
            range_max=base,
            enabled=False,
            reason="request_disabled",
        )
    if not enabled:
        return TemperatureJitterResult(
            base_temperature=base,
            actual_temperature=base,
            range_min=base,
            range_max=base,
            enabled=False,
            reason="disabled",
        )
    if is_temperature_jitter_protected(task_type):
        return TemperatureJitterResult(
            base_temperature=base,
            actual_temperature=base,
            range_min=base,
            range_max=base,
            enabled=False,
            reason="protected_task",
        )
    if not is_creative_temperature_task(task_type, scope=scope, custom_tasks=custom_tasks):
        return TemperatureJitterResult(
            base_temperature=base,
            actual_temperature=base,
            range_min=base,
            range_max=base,
            enabled=False,
            reason="fixed_task",
        )

    range_min = clamp_temperature(base - down)
    range_max = clamp_temperature(base + up)
    if range_max < range_min:
        range_min, range_max = range_max, range_min

    sample = (sampler or random.uniform)(range_min, range_max)
    actual = _round_jitter_temperature(sample)
    return TemperatureJitterResult(
        base_temperature=base,
        actual_temperature=actual,
        range_min=range_min,
        range_max=range_max,
        enabled=True,
        reason="creative_task",
    )
