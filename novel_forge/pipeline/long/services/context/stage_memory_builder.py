"""Stage-aware memory builder for planning, draft, and finalize."""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, cast

from novel_forge.core.utils.field_extractor import field as extract_field
from novel_forge.core.utils.string import clean_str
from novel_forge.editorial.signals import (
    build_expression_channel_records,
    expression_profiles_to_records,
)
from novel_forge.memory.base import MemoryBudgetConfig
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.long.services.context.stage_memory_diagnostics import (
    persist_stage_memory_diagnostics_report,
)

_log = get_logger("pipeline.long.stage_memory")


class MemoryStage(str, Enum):
    PLANNING = "planning"
    DRAFT = "draft"
    FINALIZE = "finalize"


@dataclass(frozen=True)
class StageMemoryPolicy:
    stage: MemoryStage
    # The stage memory path consumes only L1 macro summaries. L0 duplicates
    # fixed source cards; L2/L3 duplicate the explicit episodic/Zvec routes.
    layered_layers: tuple[str, ...] = ("L1_core_memory",)
    include_relevant_history: bool = False
    include_previous_chapter_events: bool = False
    include_outline_context: bool = False
    include_motif_continuity: bool = False
    include_motif_suggestions: bool = False
    motif_suggestion_use_draft_context: bool = False
    reuse_prefetched_history: bool = False
    dynamic_history_window: bool = False
    history_lookback: int = 8
    history_top_k: int = 5
    history_min_relevance: float = 0.5
    previous_events_top_k: int = 4
    previous_events_min_relevance: float = 0.25
    outline_recent_chapters: int = 4
    outline_similar_top_k: int = 3


@dataclass
class StageMemoryBundle:
    policy: StageMemoryPolicy
    relevant_history: list[dict[str, Any]] = field(default_factory=list)
    previous_chapter_events: list[dict[str, Any]] = field(default_factory=list)
    outline_context: dict[str, Any] = field(default_factory=dict)
    motif_continuity: dict[str, Any] = field(default_factory=dict)
    motif_suggestions: list[dict[str, Any]] = field(default_factory=list)
    memory_prompt_context: dict[str, Any] = field(default_factory=dict)
    layered_context: dict[str, str] = field(default_factory=dict)
    unified_guidance: str = ""
    forbidden_repetition: list[str] = field(default_factory=list)
    expression_channel_records: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


_PLANNING_POLICY = StageMemoryPolicy(
    stage=MemoryStage.PLANNING,
    include_relevant_history=True,
    include_previous_chapter_events=True,
    include_outline_context=True,
    include_motif_continuity=True,
    include_motif_suggestions=True,
    dynamic_history_window=True,
)

_DRAFT_POLICY = StageMemoryPolicy(
    stage=MemoryStage.DRAFT,
    include_relevant_history=True,
    include_previous_chapter_events=True,
    include_motif_continuity=True,
    include_motif_suggestions=True,
    motif_suggestion_use_draft_context=True,
    reuse_prefetched_history=True,
)

_FINALIZE_POLICY = StageMemoryPolicy(
    stage=MemoryStage.FINALIZE,
    include_relevant_history=True,
    include_previous_chapter_events=True,
    include_motif_continuity=True,
    include_motif_suggestions=True,
    reuse_prefetched_history=True,
    history_top_k=4,
    history_lookback=6,
)

_STAGE_POLICIES = {
    MemoryStage.PLANNING: _PLANNING_POLICY,
    MemoryStage.DRAFT: _DRAFT_POLICY,
    MemoryStage.FINALIZE: _FINALIZE_POLICY,
}


def get_stage_memory_policy(stage: MemoryStage | str) -> StageMemoryPolicy:
    stage_enum = MemoryStage(stage)
    return _STAGE_POLICIES[stage_enum]


def _join_csv(values: list[str]) -> str:
    cleaned = [item for item in values if clean_str(item)]
    return ",".join(cleaned) if cleaned else "-"


def _trim_text(value: Any, *, limit: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return _compact_text_at_boundary(text, limit=limit)


def _compact_text_at_boundary(text: str, *, limit: int) -> str:
    """Compact prompt text without implying a mid-sentence continuation."""

    if limit <= 0:
        return ""
    candidate = text[:limit].rstrip()
    if not candidate:
        return ""
    boundary_chars = "。！？!?；;\n"
    min_boundary = max(8, int(limit * 0.45))
    best = -1
    for index, char in enumerate(candidate):
        if index + 1 >= min_boundary and char in boundary_chars:
            best = index + 1
    if best > 0:
        return candidate[:best].rstrip()

    # Short labels and identifiers often have no punctuation; keep a stable
    # prefix but do not append ellipses that look like usable prose context.
    return candidate


def _build_expression_memory_query_context(plan: Any, bridge: Any, outline: Any) -> str:
    parts: list[str] = []
    for key in ("chapter_type", "emotional_arc", "relationship_evolution"):
        value = extract_field(plan, key, "")
        if clean_str(value):
            parts.append(_trim_text(value, limit=80))
    for scene in list(extract_field(plan, "scene_intents", []) or [])[:3]:
        summary = extract_field(scene, "summary", "") or extract_field(scene, "purpose", "")
        beat = extract_field(scene, "emotional_beat", "")
        text = "；".join(item for item in (clean_str(summary), clean_str(beat)) if item)
        if text:
            parts.append(_trim_text(text, limit=100))
    bridge_summary = extract_field(bridge, "bridge_summary", "") or extract_field(
        bridge, "causal_link", ""
    )
    if clean_str(bridge_summary):
        parts.append(_trim_text(bridge_summary, limit=100))
    outline_goal = extract_field(outline, "goal", "") or extract_field(outline, "summary", "")
    if clean_str(outline_goal):
        parts.append(_trim_text(outline_goal, limit=100))
    return "；".join(parts[:6])


def _dedupe_expression_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        key = (str(record.get("channel_id", "") or ""), str(record.get("text", "") or ""))
        if key in seen:
            _merge_expression_record(result_record=by_key[key], incoming=record)
            continue
        seen.add(key)
        by_key[key] = record
        result.append(record)
    return result[:12]


def _merge_expression_record(
    *,
    result_record: dict[str, Any],
    incoming: dict[str, Any],
) -> None:
    incoming_hits = [
        item
        for item in list(incoming.get("recent_semantic_hits", []) or [])
        if isinstance(item, dict)
    ]
    if incoming_hits:
        existing_hits = [
            item
            for item in list(result_record.get("recent_semantic_hits", []) or [])
            if isinstance(item, dict)
        ]
        seen_quotes = {
            (str(item.get("chapter", "") or ""), str(item.get("quote", "") or ""))
            for item in existing_hits
        }
        for hit in incoming_hits:
            key = (str(hit.get("chapter", "") or ""), str(hit.get("quote", "") or ""))
            if key in seen_quotes:
                continue
            existing_hits.append(hit)
            seen_quotes.add(key)
            if len(existing_hits) >= 2:
                break
        result_record["recent_semantic_hits"] = existing_hits[:2]

    for scalar_field in ("semantic_hit_count", "last_seen_chapter"):
        result_record[scalar_field] = max(
            _safe_int(result_record.get(scalar_field), 0),
            _safe_int(incoming.get(scalar_field), 0),
        )
    result_record["semantic_similarity_max"] = max(
        _safe_float(result_record.get("semantic_similarity_max"), 0.0),
        _safe_float(incoming.get("semantic_similarity_max"), 0.0),
    )


def _editorial_expression_profiles(editorial_contract: Any) -> list[dict[str, Any]]:
    if editorial_contract is None:
        return []
    dump = getattr(editorial_contract, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
    elif isinstance(editorial_contract, dict):
        payload = editorial_contract
    else:
        payload = {}
    profiles = payload.get("expression_channel_profiles", [])
    return list(profiles or []) if isinstance(profiles, list) else []


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mapping_payload(source: Any) -> dict[str, Any]:
    """Return a prompt-safe mapping for dict/Pydantic/dataclass-like payloads."""
    if source is None:
        return {}
    if isinstance(source, dict):
        return dict(source)
    dump = getattr(source, "model_dump", None)
    if callable(dump):
        try:
            payload = dump(mode="json")
        except TypeError:
            payload = dump()
        return dict(payload) if isinstance(payload, dict) else {}
    if hasattr(source, "__dict__"):
        return {key: value for key, value in vars(source).items() if not key.startswith("_")}
    return {}


def _settings_int(settings: Any, name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _strip_layer_heading(value: Any) -> str:
    lines = [str(line).rstrip() for line in str(value or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].lstrip().startswith("## "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _compact_memory_prompt_context(raw: Any) -> dict[str, Any]:
    """Project auxiliary memory without making semantic selections locally.

    Summaries, character state, motifs, and episodic history have dedicated
    authoritative cards. This auxiliary path carries only due foreshadows,
    preventing the same fact from being injected through several wrappers.
    """
    if not isinstance(raw, dict):
        return {}
    foreshadow_due = _compact_foreshadow_due(raw.get("foreshadow_due", []))
    return {"foreshadow_due": foreshadow_due} if foreshadow_due else {}


def _compact_foreshadow_due(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    compacted: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        description = clean_str(raw.get("description", ""))
        if not description:
            continue
        compacted.append(
            {
                "entry_id": clean_str(raw.get("entry_id", "")),
                "description": description,
                "planted_chapter": extract_field(raw, "planted_chapter", 0),
                "promise_type": clean_str(raw.get("promise_type", "")),
            }
        )
    return [{key: value for key, value in item.items() if value} for item in compacted]


def _compact_memory_layered_context(
    raw: Any,
    *,
    enabled_layers: tuple[str, ...] | None = None,
) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}

    layer_names = ("L0_identity", "L1_core_memory", "L2_on_demand", "L3_deep_search")
    allowed = set(enabled_layers or layer_names)
    result: dict[str, str] = {}
    for key in layer_names:
        if key not in allowed:
            continue
        text = _strip_layer_heading(raw.get(key, ""))
        if text:
            result[key] = text
    return result


def _serialize_episodic_results(
    results: Any,
    *,
    max_chapter: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(results, list):
        return []
    serialized: list[dict[str, Any]] = []
    for item in results:
        if isinstance(item, dict):
            chapter = int(item.get("chapter_number", 0) or 0)
            summary = clean_str(item.get("event_summary", ""))
            relevance = round(float(item.get("relevance_score", 0.0) or 0.0), 3)
        else:
            chapter = int(getattr(item, "chapter_number", 0) or 0)
            summary = clean_str(getattr(item, "event_summary", ""))
            relevance = round(float(getattr(item, "relevance_score", 0.0) or 0.0), 3)
        if not summary:
            continue
        if max_chapter is not None and chapter > max_chapter:
            continue
        serialized.append(
            {
                "chapter_number": chapter,
                "event_summary": summary,
                "relevance_score": relevance,
            }
        )
    return serialized


def _serialize_history_results(
    raw: Any,
    *,
    max_chapter: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        event_summary = clean_str(item.get("event_summary", ""))
        if not event_summary:
            continue
        try:
            chapter_number = int(item.get("chapter_number", 0) or 0)
        except Exception:
            chapter_number = 0
        if max_chapter is not None and chapter_number > max_chapter:
            continue
        try:
            relevance = round(float(item.get("relevance_score", 0.0) or 0.0), 3)
        except Exception:
            relevance = 0.0
        normalized.append(
            {
                "chapter_number": chapter_number,
                "event_summary": event_summary,
                "relevance_score": relevance,
            }
        )
    return normalized


def _serialize_outline_context(raw: Any, *, max_chapter: int | None = None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        chapter_summary = clean_str(raw.get("chapter_summary", ""))
        unresolved = [
            clean_str(item)
            for item in list(raw.get("unresolved_questions", []) or [])
            if clean_str(item)
        ]
        relationship_changes = [
            clean_str(item)
            for item in list(raw.get("relationship_changes", []) or [])
            if clean_str(item)
        ]
        similar_events = _serialize_episodic_results(
            list(raw.get("similar_events", []) or []),
            max_chapter=max_chapter,
        )
        result = {
            "chapter_summary": chapter_summary,
            "unresolved_questions": unresolved,
            "relationship_changes": relationship_changes,
            "similar_events": similar_events,
        }
        return {k: v for k, v in result.items() if v}

    unresolved = [
        clean_str(item)
        for item in list(getattr(raw, "unresolved_questions", []) or [])
        if clean_str(item)
    ]
    object_relationship_changes: list[str] = []
    for rel in list(getattr(raw, "relationship_changes", []) or []):
        a = clean_str(getattr(rel, "character_a", ""))
        b = clean_str(getattr(rel, "character_b", ""))
        ctype = clean_str(getattr(rel, "change_type", ""))
        if a and b:
            object_relationship_changes.append(f"{a}↔{b}:{ctype}")
    similar_events = _serialize_episodic_results(
        list(getattr(raw, "similar_events", []) or []),
        max_chapter=max_chapter,
    )
    result = {
        "chapter_summary": clean_str(getattr(raw, "chapter_summary", "")),
        "unresolved_questions": unresolved,
        "relationship_changes": object_relationship_changes,
        "similar_events": similar_events,
    }
    return {k: v for k, v in result.items() if v}


def _serialize_motif_suggestions(raw: list[Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for item in list(raw or []):
        name = clean_str(getattr(item, "motif_name", ""))
        if not name:
            continue
        suggestions.append(
            {
                "motif_name": name,
                "reason": clean_str(getattr(item, "reason", "")),
                "priority": clean_str(getattr(item, "priority", "")),
                "suggested_context": clean_str(getattr(item, "suggested_context", "")),
                "retired": getattr(item, "retired", False),
            }
        )
    return suggestions


def _resolve_pov_characters(
    plan: Any | None = None,
    outline: Any | None = None,
) -> list[str] | None:
    """Resolve POV character(s) from plan context.

    For multi-POV chapters, collects unique POV characters from all
    ``scene_intents``. Falls back to the outline-level ``pov_character``
    when the plan has no scene intents.

    Returns ``None`` when no POV character can be resolved (backward
    compatible — callers should pass ``None`` to skip all character
    filters).
    """
    povs: list[str] = []
    scene_intents = getattr(plan, "scene_intents", None) or []
    for intent in scene_intents:
        pov = clean_str(getattr(intent, "pov_character", ""))
        if pov:
            povs.append(pov)
    if not povs:
        fallback = clean_str(getattr(outline, "pov_character", ""))
        if fallback:
            povs.append(fallback)
    if not povs:
        return None
    seen: set[str] = set()
    unique: list[str] = []
    for pov in povs:
        if pov not in seen:
            seen.add(pov)
            unique.append(pov)
    return unique


def _bridge_query_terms(
    bridge: Any | None,
) -> list[str]:
    if bridge is None:
        return []
    terms: list[str] = []
    for value in (
        extract_field(bridge, "action_handoff"),
        extract_field(extract_field(bridge, "causal_link", None), "causal_mechanism"),
        extract_field(extract_field(bridge, "causal_link", None), "unresolved_question"),
    ):
        text = clean_str(value)
        if text:
            terms.append(text)

    causal = extract_field(bridge, "causal_link", None)
    open_threads = extract_field(causal, "open_threads", [])
    if isinstance(open_threads, list):
        for item in open_threads:
            text = clean_str(item)
            if text:
                terms.append(text)

    for value in (
        extract_field(causal, "previous_event"),
        extract_field(bridge, "opening_location"),
        extract_field(bridge, "emotional_carryover"),
    ):
        text = clean_str(value)
        if text:
            terms.append(text)
    return list(dict.fromkeys(terms))


def _build_history_query(
    bundle: Any,
    bridge: Any | None = None,
) -> str:
    outline = bundle.chapter_outline
    points: list[str] = []
    for item in list(getattr(outline, "main_plot_points", []) or []):
        s = clean_str(item)
        if s:
            points.append(s)
    for item in list(getattr(outline, "subplot_points", []) or []):
        s = clean_str(item)
        if s:
            points.append(s)
    point_text = "；".join(points)

    req_chars: list[str] = []
    for item in list(getattr(outline, "involved_characters", []) or []):
        s = clean_str(item)
        if s:
            req_chars.append(s)
    if not req_chars:
        for item in list(getattr(outline, "required_characters", []) or []):
            s = clean_str(item)
            if s:
                req_chars.append(s)
    if not req_chars:
        for p in list(getattr(bundle, "character_profiles", []) or []):
            name = clean_str(p.get("name", "") if isinstance(p, dict) else getattr(p, "name", ""))
            if name:
                req_chars.append(name)

    location = clean_str(getattr(outline, "setting", "") or "")
    if not location:
        packet = getattr(bundle, "chapter_state_packet", None)
        if packet is not None:
            exit_s = getattr(packet, "previous_exit_state", None)
            if exit_s is not None:
                location = clean_str(
                    exit_s.get("location", "")
                    if isinstance(exit_s, dict)
                    else getattr(exit_s, "location", "")
                )

    open_qs: list[str] = []
    packet = getattr(bundle, "chapter_state_packet", None)
    if packet is not None:
        exit_s = getattr(packet, "previous_exit_state", None)
        if exit_s is not None:
            raw_qs = (
                exit_s.get("open_questions", [])
                if isinstance(exit_s, dict)
                else getattr(exit_s, "open_questions", [])
            ) or []
            for q in list(raw_qs):
                s = clean_str(q)
                if s:
                    open_qs.append(s)

    parts = [
        f"目标:{getattr(outline, 'goal', '')}",
        f"场景:{location}",
        f"POV:{getattr(outline, 'pov_character', '')}",
        f"推进点:{point_text}",
    ]
    if req_chars:
        parts.append(f"相关角色:{','.join(req_chars)}")
    if open_qs:
        parts.append(f"未解问题:{';'.join(open_qs)}")
    bridge_terms = _bridge_query_terms(bridge)
    if bridge_terms:
        parts.append(f"桥接因果:{';'.join(bridge_terms)}")
    return "；".join(parts)


def _calculate_dynamic_lookback(
    unresolved_questions: int = 0,
    active_plot_threads: int = 0,
    recent_critique_count: int = 0,
    *,
    base_lookback: int = 8,
    max_lookback: int = 15,
) -> int:
    return min(
        base_lookback
        + min(unresolved_questions, 3)
        + min(active_plot_threads, 2)
        + min(recent_critique_count, 2),
        max_lookback,
    )


class StageMemoryBuilder:
    def __init__(
        self,
        runner: Any,
        bundle: Any,
        chapter_number: int,
        *,
        plan: Any | None = None,
        bridge: Any | None = None,
        prefetched_hints: dict[str, Any] | None = None,
    ) -> None:
        self._runner = runner
        self._bundle = bundle
        self._chapter_number = chapter_number
        self._plan = plan
        self._bridge = bridge
        self._prefetched_hints = prefetched_hints or {}

    async def build(self, stage: MemoryStage | str) -> StageMemoryBundle:
        policy = get_stage_memory_policy(stage)
        bundle = StageMemoryBundle(
            policy=policy,
            diagnostics={
                "stage": policy.stage.value,
                "chapter_number": self._chapter_number,
                "layered_layers": list(policy.layered_layers),
                "requested_layers": list(policy.layered_layers),
                "history_reused": False,
                "planning_prefetch_reused": False,
                "draft_stage_extras_collected": False,
                "memory_context_available": False,
                "sources": {
                    "prompt_context": ("pendingpending"),
                    "layered_context": "pending" if policy.layered_layers else "disabled",
                    "relevant_history": "pending"
                    if policy.include_relevant_history
                    else "disabled",
                    "previous_chapter_events": (
                        "pending" if policy.include_previous_chapter_events else "disabled"
                    ),
                    "outline_context": "pending" if policy.include_outline_context else "disabled",
                    "motif_continuity": (
                        "pending" if policy.include_motif_continuity else "disabled"
                    ),
                    "motif_suggestions": (
                        "pending" if policy.include_motif_suggestions else "disabled"
                    ),
                    "expression_semantic": "pending",
                },
            },
        )
        started_at = time.perf_counter()
        runner_settings = getattr(self._runner, "_settings", None)
        expression_channels_enabled = bool(
            getattr(runner_settings, "expression_channel_detection_enabled", True)
        )
        expression_cooldown = int(
            getattr(runner_settings, "expression_channel_cooldown_chapters", 3) or 0
        )
        editorial_profiles: list[dict[str, Any]] = []
        if expression_channels_enabled:
            editorial_profiles = _editorial_expression_profiles(
                getattr(self._bundle, "editorial_contract", None)
            )
            bundle.expression_channel_records.extend(
                expression_profiles_to_records(
                    editorial_profiles,
                    source="editorial_contract",
                    level="soft",
                    max_records=12,
                )
            )
            if not editorial_profiles:
                bundle.diagnostics["sources"]["expression_semantic"] = "no_profiles"
        else:
            bundle.diagnostics["sources"]["expression_semantic"] = "disabled"

        has_memory_fn = getattr(self._runner, "has_memory_context", None)
        memory_ctx = getattr(self._runner, "memory_context", None)
        if not callable(has_memory_fn) or not has_memory_fn() or memory_ctx is None:
            bundle.expression_channel_records = _dedupe_expression_records(
                bundle.expression_channel_records
            )
            self._finalize_diagnostics(bundle, active_motif_ids=set(), started_at=started_at)
            self._emit_summary_log(bundle)
            return bundle
        bundle.diagnostics["memory_context_available"] = True

        pov_characters = _resolve_pov_characters(
            self._plan,
            getattr(self._bundle, "chapter_outline", None),
        )
        if pov_characters:
            bundle.diagnostics["pov_characters"] = pov_characters

        # ── Parallel collection of independent async data sources ──
        # _collect_prompt_context, _collect_history, and
        # _collect_expression_semantic_records read from the same memory_ctx
        # (read-only) and write to different bundle fields, so they can safely
        # execute concurrently.
        _need_expression_semantic = bool(expression_channels_enabled and editorial_profiles)
        _parallel_enabled = bool(
            getattr(runner_settings, "long_perf_memory_parallel_enabled", True)
        )

        if _parallel_enabled:

            async def _task_prompt_context() -> tuple[Any, str]:
                return await self._collect_prompt_context(memory_ctx, policy)

            async def _task_history() -> None:
                await self._collect_history(bundle, memory_ctx, policy)

            async def _task_expression_semantic() -> list[Any]:
                if not _need_expression_semantic:
                    return []
                return await self._collect_expression_semantic_records(
                    memory_ctx,
                    editorial_profiles,
                    cooldown_chapters=expression_cooldown,
                )

            _prompt_result, _, _semantic_records = await asyncio.gather(
                _task_prompt_context(),
                _task_history(),
                _task_expression_semantic(),
            )
        else:
            # Sequential fallback (env switch disabled)
            _prompt_result = await self._collect_prompt_context(memory_ctx, policy)
            await self._collect_history(bundle, memory_ctx, policy)
            _semantic_records = (
                await self._collect_expression_semantic_records(
                    memory_ctx,
                    editorial_profiles,
                    cooldown_chapters=expression_cooldown,
                )
                if _need_expression_semantic
                else []
            )

        prompt_ctx, prompt_source = _prompt_result
        bundle.diagnostics["sources"]["prompt_context"] = prompt_source
        bundle.diagnostics["prompt_context_scope"] = prompt_source
        bundle.memory_prompt_context = _compact_memory_prompt_context(prompt_ctx)

        if policy.layered_layers:
            layered_context, layered_source = self._collect_layered_context(memory_ctx, policy)
            bundle.layered_context = layered_context
            bundle.diagnostics["sources"]["layered_context"] = layered_source

        active_motif_ids = self._collect_motif_data(bundle, memory_ctx, policy)
        if _need_expression_semantic:
            semantic_records = _semantic_records
            if semantic_records:
                bundle.expression_channel_records.extend(semantic_records)
                bundle.diagnostics["sources"]["expression_semantic"] = "zvec"
            else:
                current_source = bundle.diagnostics["sources"].get("expression_semantic")
                if current_source not in {"no_profiles", "disabled", "unavailable"}:
                    bundle.diagnostics["sources"]["expression_semantic"] = "empty"
        if bundle.motif_continuity and expression_channels_enabled:
            bundle.expression_channel_records.extend(
                build_expression_channel_records(
                    bundle.motif_continuity.get("forbidden_repetition", []),
                    source="motif",
                    level="soft",
                    reason="motif_continuity forbidden_repetition",
                    cooldown_chapters=expression_cooldown,
                    profiles=editorial_profiles,
                )
            )
        bundle.expression_channel_records = _dedupe_expression_records(
            bundle.expression_channel_records
        )
        self._finalize_diagnostics(bundle, active_motif_ids=active_motif_ids, started_at=started_at)
        self._emit_summary_log(bundle)
        return bundle

    async def _collect_expression_semantic_records(
        self,
        memory_ctx: Any,
        editorial_profiles: list[dict[str, Any]],
        *,
        cooldown_chapters: int,
    ) -> list[dict[str, Any]]:
        expression_memory = getattr(memory_ctx, "expression_memory", None)
        if expression_memory is None:
            return []
        search = getattr(expression_memory, "search_expression_channel_memory", None)
        if not callable(search):
            return []
        settings = getattr(self._runner, "_settings", None)
        try:
            raw_records = await search(
                current_chapter=self._chapter_number,
                profiles=editorial_profiles,
                chapter_context=_build_expression_memory_query_context(
                    self._plan,
                    self._bridge,
                    getattr(self._bundle, "chapter_outline", None),
                ),
                cooldown_chapters=cooldown_chapters,
                top_k_per_profile=int(
                    getattr(settings, "expression_channel_zvec_top_k_per_profile", 2) or 2
                ),
                max_hits=6,
            )
            return cast(list[dict[str, Any]], raw_records)
        except Exception as exc:
            _log.debug(
                "expression_semantic_memory_search_failed | chapter=%d | error=%s",
                self._chapter_number,
                exc,
            )
            return []

    async def _collect_prompt_context(
        self,
        memory_ctx: Any,
        policy: StageMemoryPolicy,
    ) -> tuple[dict[str, Any], str]:
        return await self._collect_global_prompt_context_without_summary(
            memory_ctx,
            policy,
        )

    async def _collect_global_prompt_context_without_summary(
        self,
        memory_ctx: Any,
        policy: StageMemoryPolicy,
    ) -> tuple[dict[str, Any], str]:
        try:
            getter = getattr(memory_ctx, "aget_memory_context_for_prompt", None)
            if not callable(getter):
                getter = getattr(memory_ctx, "get_memory_context_for_prompt", None)
            if not callable(getter):
                return {}, "global_no_summary"
            prompt_ctx = getter(
                current_chapter=self._chapter_number,
                include_motifs=False,
                include_summaries=False,
                include_critiques=False,
            )
            if inspect.isawaitable(prompt_ctx):
                prompt_ctx = await prompt_ctx
            if isinstance(prompt_ctx, dict) and prompt_ctx:
                sanitized = dict(prompt_ctx)
                sanitized.pop("summary_context", None)
                sanitized.pop("character_prompt_contexts", None)
                return sanitized, "global_no_summary"
            return {}, "global_no_summary"
        except Exception as exc:
            _log.warning(
                "stage_memory_prompt_context_failed | stage=%s | chapter=%d | error=%s",
                policy.stage.value,
                self._chapter_number,
                exc,
            )
            return {}, "error"

    def _collect_layered_context(
        self,
        memory_ctx: Any,
        policy: StageMemoryPolicy,
    ) -> tuple[dict[str, str], str]:
        try:
            budget = MemoryBudgetConfig()
            for layer_name in ("L0_identity", "L2_on_demand", "L3_deep_search"):
                layer = budget.get_layer(layer_name)
                if layer is not None:
                    layer.enabled = False
            layered_raw = memory_ctx.get_layered_context(
                current_chapter=self._chapter_number,
                budget=budget,
            )
            layered_context = _compact_memory_layered_context(
                layered_raw,
                enabled_layers=policy.layered_layers,
            )
            if layered_context:
                return layered_context, "generated"
            return {}, "empty"
        except Exception as exc:
            _log.warning(
                "stage_memory_layered_context_failed | stage=%s | chapter=%d | error=%s",
                policy.stage.value,
                self._chapter_number,
                exc,
            )
            return {}, "error"

    async def _collect_history(
        self,
        bundle: StageMemoryBundle,
        memory_ctx: Any,
        policy: StageMemoryPolicy,
    ) -> None:
        # Init outline vectors describe a superseded proposal after publication.
        # Keep accepted event/relationship memory intact and use the current
        # source slice for planning; never treat the old outline as past fact.
        layout = getattr(self._bundle, "layout", None)
        plans_dir = getattr(layout, "plans_dir", None)
        if plans_dir is not None and (plans_dir / "active_planning_revision.json").exists():
            policy = replace(policy, include_outline_context=False)
            bundle.diagnostics.get("sources", {})["outline_context"] = (
                "invalidated_by_planning_revision"
            )
        prefetched_relevant = self._prefetched_hints.get("relevant_history")
        prefetched_previous = self._prefetched_hints.get("previous_chapter_events")
        prefetched_outline = self._prefetched_hints.get("outline_context")
        sources = bundle.diagnostics.get("sources", {})

        pov_characters = _resolve_pov_characters(
            self._plan, getattr(self._bundle, "chapter_outline", None)
        )
        if policy.reuse_prefetched_history and (
            prefetched_relevant or prefetched_previous or prefetched_outline
        ):
            bundle.diagnostics["history_reused"] = True
            bundle.diagnostics["planning_prefetch_reused"] = True
            if policy.include_relevant_history:
                bundle.relevant_history = _serialize_history_results(
                    list(prefetched_relevant or []),
                    max_chapter=self._chapter_number - 1,
                )
                sources["relevant_history"] = (
                    "prefetched" if bundle.relevant_history else "prefetched_empty"
                )
            if policy.include_previous_chapter_events:
                bundle.previous_chapter_events = _serialize_episodic_results(
                    list(prefetched_previous or []),
                    max_chapter=self._chapter_number - 1,
                )
                sources["previous_chapter_events"] = (
                    "prefetched" if bundle.previous_chapter_events else "prefetched_empty"
                )
            if policy.include_outline_context:
                bundle.outline_context = _serialize_outline_context(
                    prefetched_outline,
                    max_chapter=self._chapter_number - 1,
                )
                sources["outline_context"] = (
                    "prefetched" if bundle.outline_context else "prefetched_empty"
                )
            if bundle.relevant_history or bundle.previous_chapter_events or bundle.outline_context:
                return

        if pov_characters:
            bundle.diagnostics["pov_characters"] = pov_characters

        tasks: dict[str, Any] = {}
        episodic = getattr(memory_ctx, "episodic_memory", None)
        if (
            policy.include_relevant_history
            and episodic is not None
            and callable(getattr(episodic, "search_by_semantic", None))
        ):
            lookback = policy.history_lookback
            outline_recent = policy.outline_recent_chapters
            if policy.dynamic_history_window:
                lookback = await self._calculate_dynamic_history_lookback(memory_ctx, policy)
                outline_recent = max(lookback - 2, policy.outline_recent_chapters)
            tasks["relevant_history"] = episodic.search_by_semantic(
                query=_build_history_query(self._bundle, self._bridge),
                chapter_range=(max(1, self._chapter_number - lookback), self._chapter_number - 1),
                top_k=policy.history_top_k,
                min_relevance=policy.history_min_relevance,
                characters=pov_characters,
            )
            bundle.diagnostics["history_lookback"] = lookback
            bundle.diagnostics["outline_recent_chapters"] = outline_recent
            sources["relevant_history"] = "scheduled"
        else:
            outline_recent = policy.outline_recent_chapters
            if policy.include_relevant_history:
                sources["relevant_history"] = "unavailable"

        if episodic is not None:
            if (
                policy.include_previous_chapter_events
                and self._chapter_number > 1
                and callable(getattr(episodic, "search_by_semantic", None))
            ):
                previous_query = "上一章结尾 动作接力 开场因果"
                bridge_terms = _bridge_query_terms(self._bridge)
                if bridge_terms:
                    previous_query = previous_query + "；" + "；".join(bridge_terms)
                tasks["previous_chapter_events"] = episodic.search_by_semantic(
                    query=previous_query,
                    chapter_range=(self._chapter_number - 1, self._chapter_number - 1),
                    top_k=policy.previous_events_top_k,
                    min_relevance=policy.previous_events_min_relevance,
                    characters=pov_characters,
                )
                sources["previous_chapter_events"] = "scheduled"
            elif policy.include_previous_chapter_events and self._chapter_number <= 1:
                sources["previous_chapter_events"] = "not_applicable"
            if policy.include_outline_context and callable(
                getattr(episodic, "get_outline_context", None)
            ):
                tasks["outline_context"] = episodic.get_outline_context(
                    current_chapter=self._chapter_number,
                    recent_chapters=outline_recent,
                    similar_top_k=policy.outline_similar_top_k,
                )
                sources["outline_context"] = "scheduled"
            elif policy.include_outline_context:
                sources["outline_context"] = "unavailable"
        else:
            if policy.include_previous_chapter_events and self._chapter_number <= 1:
                sources["previous_chapter_events"] = "not_applicable"
            elif policy.include_previous_chapter_events:
                sources["previous_chapter_events"] = "unavailable"
            if policy.include_outline_context:
                sources["outline_context"] = "unavailable"

        if not tasks:
            return

        task_names = list(tasks.keys())
        results = await asyncio.gather(
            *(tasks[name] for name in task_names),
            return_exceptions=True,
        )
        for name, result in zip(task_names, results, strict=True):
            if isinstance(result, BaseException):
                _log.warning(
                    "stage_memory_fetch_failed | stage=%s | chapter=%d | part=%s | error=%s",
                    policy.stage.value,
                    self._chapter_number,
                    name,
                    result,
                )
                continue
            if name == "relevant_history":
                episodic_results = result if isinstance(result, list) else []
                bundle.relevant_history = _serialize_episodic_results(
                    episodic_results,
                    max_chapter=self._chapter_number - 1,
                )
                sources["relevant_history"] = (
                    "fetched" if bundle.relevant_history else "fetched_empty"
                )
            elif name == "previous_chapter_events":
                episodic_results = result if isinstance(result, list) else []
                bundle.previous_chapter_events = _serialize_episodic_results(
                    episodic_results,
                    max_chapter=self._chapter_number - 1,
                )
                sources["previous_chapter_events"] = (
                    "fetched" if bundle.previous_chapter_events else "fetched_empty"
                )
            elif name == "outline_context":
                bundle.outline_context = _serialize_outline_context(
                    result,
                    max_chapter=self._chapter_number - 1,
                )
                sources["outline_context"] = (
                    "fetched" if bundle.outline_context else "fetched_empty"
                )

    def _collect_motif_data(
        self,
        bundle: StageMemoryBundle,
        memory_ctx: Any,
        policy: StageMemoryPolicy,
    ) -> set[str]:
        motif_tracker = getattr(memory_ctx, "motif_tracker", None)
        sources = bundle.diagnostics.get("sources", {})
        if motif_tracker is None:
            if policy.include_motif_continuity:
                sources["motif_continuity"] = "unavailable"
            if policy.include_motif_suggestions:
                sources["motif_suggestions"] = "unavailable"
            return set()
        settings = getattr(memory_ctx, "settings", None) or getattr(self._runner, "_settings", None)

        if policy.include_motif_continuity:
            try:
                motif_data = motif_tracker.get_motifs_for_prompt(
                    current_chapter=self._chapter_number,
                    max_motifs=5,
                    include_recent_usage=True,
                    related_lookback_chapters=_settings_int(
                        settings,
                        "memory_motif_related_lookback_chapters",
                        2,
                    ),
                )
                bundle.motif_continuity = {
                    "forbidden_repetition": motif_data.get("forbidden_repetition", []),
                    "suggested_callbacks": motif_data.get("suggested_callbacks", []),
                    "active_motifs": motif_data.get("active_motifs", []),
                }
                bundle.forbidden_repetition = [
                    clean_str(item)
                    for item in list(motif_data.get("forbidden_repetition", []) or [])
                    if clean_str(item)
                ]
                raw_guidance = motif_data.get("unified_guidance")
                if raw_guidance is not None:
                    to_text = getattr(raw_guidance, "to_prompt_text", None)
                    bundle.unified_guidance = (
                        to_text() if callable(to_text) else clean_str(raw_guidance)
                    )
                    if bundle.unified_guidance:
                        bundle.motif_continuity["unified_guidance"] = bundle.unified_guidance
                sources["motif_continuity"] = "generated" if bundle.motif_continuity else "empty"
            except Exception as exc:
                _log.warning(
                    "stage_memory_motif_continuity_failed | stage=%s | chapter=%d | error=%s",
                    policy.stage.value,
                    self._chapter_number,
                    exc,
                )
                sources["motif_continuity"] = "error"

        if policy.include_motif_suggestions:
            try:
                current_context = ""
                if policy.motif_suggestion_use_draft_context:
                    current_context = (
                        f"{getattr(self._bundle.chapter_outline, 'goal', '')}；"
                        f"{getattr(self._plan, 'emotional_arc', '')}"
                    )
                suggestions_raw = motif_tracker.get_suggestions_for_chapter(
                    current_chapter=self._chapter_number,
                    current_context=current_context,
                    prompt_safe=True,
                )
                bundle.motif_suggestions = _serialize_motif_suggestions(suggestions_raw)
                sources["motif_suggestions"] = "generated" if bundle.motif_suggestions else "empty"
            except Exception as exc:
                _log.warning(
                    "stage_memory_motif_suggestions_failed | stage=%s | chapter=%d | error=%s",
                    policy.stage.value,
                    self._chapter_number,
                    exc,
                )
                sources["motif_suggestions"] = "error"
        active = bundle.motif_continuity.get("active_motifs", [])
        return {
            clean_str(item.get("motif_id", ""))
            for item in active
            if isinstance(item, dict) and clean_str(item.get("motif_id", ""))
        }

    async def _calculate_dynamic_history_lookback(
        self,
        memory_ctx: Any,
        policy: StageMemoryPolicy,
    ) -> int:
        unresolved_questions = 0
        packet = getattr(self._bundle, "chapter_state_packet", None)
        if packet is not None:
            exit_s = getattr(packet, "previous_exit_state", None)
            if exit_s is not None:
                raw_qs = (
                    exit_s.get("open_questions", [])
                    if isinstance(exit_s, dict)
                    else getattr(exit_s, "open_questions", [])
                ) or []
                unresolved_questions = len([q for q in raw_qs if clean_str(q)])

        active_plot_threads = 0
        try:
            strand_path = self._bundle.layout.root / "states" / "strand_tracker.json"
            if hasattr(self._runner, "_storage") and strand_path.exists():
                strand_raw = self._runner._storage.load_json(strand_path)
                if strand_raw:
                    from novel_forge.pipeline.long.services.generation.strand_weave import (
                        StrandTracker,
                    )

                    strand_tracker = StrandTracker.model_validate(strand_raw)
                    active_threads = getattr(strand_tracker, "active_threads", {}) or {}
                    active_plot_threads = len(active_threads)
        except Exception:
            pass

        recent_critique_count = 0
        critic = getattr(memory_ctx, "_critic_agent", None)
        if critic is not None:
            try:
                recent_critiques = getattr(critic, "get_recent_critiques", None)
                if callable(recent_critiques):
                    critiques = await recent_critiques(self._chapter_number, lookback=6)
                    recent_critique_count = sum(
                        1
                        for c in (critiques or [])
                        if clean_str(getattr(c, "severity", "")).lower() in {"critical", "high"}
                    )
            except Exception:
                pass

        return _calculate_dynamic_lookback(
            unresolved_questions=unresolved_questions,
            active_plot_threads=active_plot_threads,
            recent_critique_count=recent_critique_count,
            base_lookback=policy.history_lookback,
        )

    def _finalize_diagnostics(
        self,
        bundle: StageMemoryBundle,
        *,
        active_motif_ids: set[str],
        started_at: float,
    ) -> None:
        diagnostics = bundle.diagnostics
        sources = diagnostics.get("sources", {})
        if isinstance(sources, dict):
            for key, value in list(sources.items()):
                if value == "pending":
                    sources[key] = "unavailable"

        requested_layers = list(bundle.policy.layered_layers)
        resolved_layers = [
            layer for layer in requested_layers if clean_str(bundle.layered_context.get(layer, ""))
        ]
        missing_layers = [layer for layer in requested_layers if layer not in resolved_layers]

        diagnostics["resolved_layers"] = resolved_layers
        diagnostics["missing_layers"] = missing_layers
        diagnostics["layer_char_counts"] = {
            key: len(clean_str(value))
            for key, value in bundle.layered_context.items()
            if clean_str(value)
        }
        diagnostics["counts"] = {
            "relevant_history": len(bundle.relevant_history),
            "previous_chapter_events": len(bundle.previous_chapter_events),
            "outline_context_fields": len(bundle.outline_context),
            "motif_suggestions": len(bundle.motif_suggestions),
            "forbidden_repetition": len(bundle.forbidden_repetition),
            "active_motif_ids": len(active_motif_ids),
            "motif_active_entries": len(
                list(bundle.motif_continuity.get("active_motifs", []) or [])
            ),
            "prompt_context_fields": len(bundle.memory_prompt_context),
            "expression_channel_records": len(bundle.expression_channel_records),
        }
        diagnostics["flags"] = {
            "has_relevant_history": bool(bundle.relevant_history),
            "has_previous_chapter_events": bool(bundle.previous_chapter_events),
            "has_outline_context": bool(bundle.outline_context),
            "has_motif_continuity": bool(bundle.motif_continuity),
            "has_motif_suggestions": bool(bundle.motif_suggestions),
            "has_prompt_summary": bool(bundle.memory_prompt_context.get("summary_context")),
            "has_forbidden_repetition": bool(bundle.forbidden_repetition),
            "has_layered_context": bool(bundle.layered_context),
            "has_expression_channel_records": bool(bundle.expression_channel_records),
        }
        diagnostics["planning_prefetch_reused"] = bool(diagnostics.get("history_reused"))
        diagnostics["draft_stage_extras_collected"] = bool(
            bundle.policy.stage == MemoryStage.DRAFT
            and (
                bundle.memory_prompt_context
                or bundle.layered_context
                or bundle.motif_continuity
                or bundle.motif_suggestions
                or bundle.forbidden_repetition
                or bundle.expression_channel_records
            )
        )
        diagnostics["duration_ms"] = round(
            (time.perf_counter() - started_at) * 1000,
            2,
        )

    def _emit_summary_log(self, bundle: StageMemoryBundle) -> None:
        diagnostics = bundle.diagnostics
        counts = diagnostics.get("counts", {})
        flags = diagnostics.get("flags", {})
        sources = diagnostics.get("sources", {})
        _log.info(
            "stage_memory_summary | stage=%s | chapter=%d | duration_ms=%.2f | "
            "requested_layers=%s | resolved_layers=%s | relevant_history=%d | "
            "previous_events=%d | outline=%s | motif_continuity=%s | "
            "motif_suggestions=%d | forbidden_repetition=%d | "
            "history_reused=%s | sources=%s",
            diagnostics.get("stage", bundle.policy.stage.value),
            int(diagnostics.get("chapter_number", self._chapter_number) or 0),
            float(diagnostics.get("duration_ms", 0.0) or 0.0),
            _join_csv(list(diagnostics.get("requested_layers", []) or [])),
            _join_csv(list(diagnostics.get("resolved_layers", []) or [])),
            int(counts.get("relevant_history", 0) or 0),
            int(counts.get("previous_chapter_events", 0) or 0),
            str(bool(flags.get("has_outline_context", False))).lower(),
            str(bool(flags.get("has_motif_continuity", False))).lower(),
            int(counts.get("motif_suggestions", 0) or 0),
            int(counts.get("forbidden_repetition", 0) or 0),
            str(bool(diagnostics.get("history_reused", False))).lower(),
            _join_csv(
                [f"{key}:{value}" for key, value in sorted(sources.items()) if clean_str(value)]
            ),
        )


def to_planning_memory_hints(bundle: StageMemoryBundle) -> dict[str, Any]:
    result: dict[str, Any] = {
        "relevant_history": bundle.relevant_history,
        "previous_chapter_events": bundle.previous_chapter_events,
        "outline_context": bundle.outline_context,
        "expression_channel_records": bundle.expression_channel_records,
        "memory_diagnostics": dict(bundle.diagnostics),
    }
    if bundle.motif_continuity:
        result["motif_continuity"] = bundle.motif_continuity
    if bundle.layered_context:
        result["layered_context"] = bundle.layered_context
    if bundle.motif_suggestions:
        result["motif_suggestions"] = bundle.motif_suggestions
    if bundle.unified_guidance:
        result["unified_guidance"] = bundle.unified_guidance
    return {k: v for k, v in result.items() if v}


def to_draft_memory_hints(bundle: StageMemoryBundle) -> dict[str, Any]:
    result: dict[str, Any] = {
        "relevant_history": bundle.relevant_history,
        "previous_chapter_events": bundle.previous_chapter_events,
        "motif_suggestions": bundle.motif_suggestions,
        "memory_prompt_context": bundle.memory_prompt_context,
        "memory_layered_context": bundle.layered_context,
        "expression_channel_records": bundle.expression_channel_records,
        "memory_diagnostics": dict(bundle.diagnostics),
    }
    if bundle.motif_continuity:
        result["motif_continuity"] = bundle.motif_continuity
    if bundle.unified_guidance:
        result["unified_guidance"] = bundle.unified_guidance
    return {k: v for k, v in result.items() if v}


def to_finalize_eval_memory_context(bundle: StageMemoryBundle) -> dict[str, Any]:
    result: dict[str, Any] = {
        "memory_relevant_history": bundle.relevant_history,
        "memory_previous_chapter_events": bundle.previous_chapter_events,
        "memory_motif_suggestions": bundle.motif_suggestions,
        "memory_forbidden_repetition": bundle.forbidden_repetition,
        "memory_layered_context": bundle.layered_context,
        "memory_expression_channel_records": bundle.expression_channel_records,
        "memory_unified_guidance": bundle.unified_guidance,
        "memory_diagnostics": dict(bundle.diagnostics),
    }
    return {k: v for k, v in result.items() if v}


async def collect_planning_memory_hints(
    runner: Any,
    bundle: Any,
    chapter_number: int,
) -> dict[str, Any]:
    builder = StageMemoryBuilder(runner, bundle, chapter_number)
    memory_bundle = await builder.build(MemoryStage.PLANNING)
    result = to_planning_memory_hints(memory_bundle)
    # Inject forward-looking motif guidance
    try:
        memory_ctx = getattr(runner, "memory_context", None) or getattr(runner, "memory_ctx", None)
        motif_tracker = getattr(memory_ctx, "motif_tracker", None)
        if motif_tracker and hasattr(motif_tracker, "get_forward_looking_guidance"):
            chapter_outline = getattr(bundle, "chapter_outline", None)
            chapter_outline_payload = _mapping_payload(chapter_outline)
            settings = getattr(memory_ctx, "settings", None) or getattr(runner, "_settings", None)
            forward_data = await motif_tracker.get_forward_looking_guidance(
                current_chapter=chapter_number,
                chapter_outline=chapter_outline_payload or None,
                dormant_callback_min_chapters=_settings_int(
                    settings,
                    "motif_dormant_callback_min_chapters",
                    20,
                    minimum=1,
                ),
            )
            if forward_data:
                result["forward_motif_guidance"] = forward_data
    except Exception:
        pass  # Graceful degradation
    return result


async def collect_draft_memory_hints(
    runner: Any,
    bundle: Any,
    plan: Any,
    bridge: Any,
    chapter_number: int,
    *,
    planning_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    builder = StageMemoryBuilder(
        runner,
        bundle,
        chapter_number,
        plan=plan,
        bridge=bridge,
        prefetched_hints=planning_hints,
    )
    memory_bundle = await builder.build(MemoryStage.DRAFT)
    return to_draft_memory_hints(memory_bundle)


async def build_finalize_eval_memory_context(
    runner: Any,
    bundle: Any,
    chapter_number: int,
    *,
    memory_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    builder = StageMemoryBuilder(
        runner,
        bundle,
        chapter_number,
        prefetched_hints=memory_hints,
    )
    memory_bundle = await builder.build(MemoryStage.FINALIZE)
    return to_finalize_eval_memory_context(memory_bundle)


__all__ = [
    "MemoryStage",
    "StageMemoryPolicy",
    "StageMemoryBundle",
    "StageMemoryBuilder",
    "_resolve_pov_characters",
    "build_finalize_eval_memory_context",
    "collect_draft_memory_hints",
    "collect_planning_memory_hints",
    "get_stage_memory_policy",
    "persist_stage_memory_diagnostics_report",
    "to_draft_memory_hints",
    "to_finalize_eval_memory_context",
    "to_planning_memory_hints",
]
