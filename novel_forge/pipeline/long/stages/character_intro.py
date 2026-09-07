"""Character introduction — two-phase lifecycle for new character profiles.

Phase 1 (pre-chapter): ``introduce_new_characters``
    Detect characters expected in the upcoming chapter from the outline /
    narrative blueprint but not yet in character_bible.json.  For each one,
    call the AI to generate a skeleton profile (role + relationships + rough
    personality/backstory inferred from the outline) and persist it so the
    draft prompt has something to work with.

Phase 2 (post-chapter): ``enrich_introduced_characters``
    After the chapter text has been produced, call ``enrich_character`` for
    each character that was newly introduced this chapter.  The AI now reads
    the *actual prose* and overwrites appearance / personality / backstory /
    arc with values derived from real textual evidence, correcting any
    skeleton inaccuracies.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Callable, Iterable

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.character_identity import (
    clean_character_name,
    dedupe_candidate_character_names,
    normalize_character_role,
)
from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile
from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.pipeline.long.decisions import should_update_stable_text
from novel_forge.pipeline.long.services.character_intro_policy import (
    apply_pending_retry_policy,
    creative_character_has_carry_forward,
    creative_importance_allows_auto_register,
    intro_pending_max_attempts,
    load_character_alias_lookup,
    normalize_gender_update,
    resolve_known_character_name,
    sanitize_relationships,
    sort_auto_new_names,
    upsert_character_alias,
    usable_profile_text,
)
from novel_forge.pipeline.long.services.context.story_kernel_context import story_kernel_db_path
from novel_forge.pipeline.long.services.generation.llm_helpers import route_json_object_with_retry
from novel_forge.pipeline.long.stages.character_intro_candidates import (
    _collect_chapter_character_candidate_signals as _collect_chapter_character_candidate_signals,
)
from novel_forge.pipeline.long.stages.character_intro_candidates import (
    _collect_chapter_character_candidates as _collect_chapter_character_candidates,
)
from novel_forge.pipeline.long.stages.character_intro_candidates import (
    _collect_new_character_candidate_names as _collect_new_character_candidate_names,
)
from novel_forge.pipeline.long.stages.character_intro_candidates import (
    _is_functional_character_label as _is_functional_character_label,
)
from novel_forge.pipeline.long.stages.character_intro_candidates import (
    _looks_like_character_name as _looks_like_character_name,
)
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.story_kernel.relationship_sync import sync_relationships_from_bible_to_kernel
from novel_forge.story_kernel.store import StoryKernelStore

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.obs.tracer import PipelineTrace
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.pipeline.long.preflight import LongProjectBundle
    from novel_forge.prompts.builder import PromptBuilder
    from novel_forge.story_kernel.composer import ContextComposer

logger = logging.getLogger(__name__)

# ── Internal helpers ──────────────────────────────────────────────────────────


def _existing_names(bible: CharacterBible) -> set[str]:
    return {clean_character_name(c.name) for c in bible.characters if clean_character_name(c.name)}


async def _sync_character_relationships_to_canon(
    *,
    bundle: "LongProjectBundle",
    settings: Any,
    on_step: Callable[[str, Any], None],
) -> int:
    raw_project_id = getattr(bundle, "project_id", "")
    project_id = raw_project_id.strip() if isinstance(raw_project_id, str) else ""
    if not project_id:
        return 0
    store = StoryKernelStore(
        story_kernel_db_path(settings, bundle.layout),
        wal_mode=bool(getattr(settings, "story_kernel_wal_mode", True)),
    )
    try:
        await store.init_db()
        try:
            kernel = await store.load_kernel(project_id)
        except ValueError:
            return 0
        added = sync_relationships_from_bible_to_kernel(bundle.character_bible, kernel)
        if added:
            await store.save_kernel(kernel)
            on_step("character_relationship_sync", {"added": added})
        return added
    except Exception as exc:
        logger.warning("character_relationship_sync_failed | error=%s", exc)
        on_step("character_relationship_sync_failed", {"error": str(exc)})
        return 0
    finally:
        await store.close()


_CREATE_PROFILE_VERDICTS = frozenset({"create_profile", "introduce", "add_to_bible"})
_SKIP_PROFILE_IMPORTANCE = frozenset({"incidental", "none", "label", "unknown"})
_PENDING_INTRO_FILENAME = "character_intro_pending.json"


def _load_chapter_contract(
    *,
    storage: "FileSystemStorage",
    bundle: "LongProjectBundle",
    chapter_number: int,
) -> dict[str, Any]:
    contract_path = bundle.layout.plans_dir / "chapter_contracts.json"
    if not storage.exists(contract_path):
        return {}
    try:
        payload = storage.load_json(contract_path)
    except Exception:
        logger.warning("introduce_character: could not load chapter contracts")
        return {}
    for item in payload.get("chapter_contracts", []) or []:
        if int(item.get("chapter_number") or 0) == int(chapter_number):
            return item if isinstance(item, dict) else {}
    return {}


def _load_chapter_contracts_payload(
    *,
    storage: "FileSystemStorage",
    bundle: "LongProjectBundle",
) -> dict[str, Any]:
    contract_path = bundle.layout.plans_dir / "chapter_contracts.json"
    if not storage.exists(contract_path):
        return {}
    try:
        payload = storage.load_json(contract_path)
    except Exception:
        logger.warning("introduce_character: could not load chapter contracts payload")
        return {}
    return payload if isinstance(payload, dict) else {}


def _future_chapter_contracts_payload(
    *,
    storage: "FileSystemStorage",
    bundle: "LongProjectBundle",
    chapter_number: int,
) -> dict[str, Any]:
    payload = _load_chapter_contracts_payload(storage=storage, bundle=bundle)
    contracts = payload.get("chapter_contracts")
    if not isinstance(contracts, list):
        return {}
    future: list[Any] = []
    for item in contracts:
        if not isinstance(item, dict):
            continue
        try:
            item_chapter = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            item_chapter = 0
        if item_chapter > int(chapter_number):
            future.append(item)
    return {"chapter_contracts": future}


def _auto_introduce_limit(settings: Any) -> int:
    try:
        return max(0, int(getattr(settings, "long_auto_introduce_max_new_characters", 2) or 0))
    except (TypeError, ValueError):
        return 2


def _limit_auto_new_names(
    names: list[str],
    *,
    limit: int,
    chapter_number: int,
    on_step: Callable[[str, Any], None] | None,
    event_name: str,
) -> list[str]:
    if limit <= 0:
        if names:
            logger.info(
                "%s: chapter %d — auto-registration disabled by max_new_characters=0; skipped: %s",
                event_name,
                chapter_number,
                ", ".join(names),
            )
        return []
    if len(names) <= limit:
        return names
    kept = names[:limit]
    skipped = names[limit:]
    logger.info(
        "%s: chapter %d — limited new characters to %d; skipped: %s",
        event_name,
        chapter_number,
        limit,
        ", ".join(skipped),
    )
    if on_step:
        on_step(
            f"{event_name}_limited",
            {
                "chapter": chapter_number,
                "limit": limit,
                "kept": kept,
                "skipped": skipped,
            },
        )
    return kept


def _pending_intro_path(bundle: "LongProjectBundle") -> Any:
    return bundle.layout.states_dir / _PENDING_INTRO_FILENAME


def _load_pending_intro_payload(
    storage: "FileSystemStorage",
    bundle: "LongProjectBundle",
) -> dict[str, Any]:
    path = _pending_intro_path(bundle)
    if not storage.exists(path):
        return {"pending": []}
    try:
        payload = storage.load_json(path)
    except Exception:
        logger.warning("character_intro_pending: failed to load pending intro file")
        return {"pending": []}
    pending = payload.get("pending", [])
    return {"pending": pending if isinstance(pending, list) else []}


def _save_pending_intro_payload(
    storage: "FileSystemStorage",
    bundle: "LongProjectBundle",
    payload: dict[str, Any],
) -> None:
    storage.save_json(_pending_intro_path(bundle), payload)


def _record_pending_character_intro(
    *,
    storage: "FileSystemStorage",
    bundle: "LongProjectBundle",
    chapter_number: int,
    character_name: str,
    phase: str,
    error: str,
    sources: list[str] | None = None,
) -> None:
    name = clean_character_name(character_name)
    if not name:
        return
    payload = _load_pending_intro_payload(storage, bundle)
    pending = list(payload.get("pending", []) or [])
    now = datetime.now(UTC).isoformat()
    key = (int(chapter_number), name, phase)
    updated = False
    for item in pending:
        if not isinstance(item, dict):
            continue
        item_key = (
            int(item.get("chapter") or 0),
            clean_character_name(item.get("character")),
            str(item.get("phase") or ""),
        )
        if item_key != key:
            continue
        item.update(
            {
                "error": str(error or ""),
                "sources": list(sources or item.get("sources") or []),
                "attempts": int(item.get("attempts") or 0) + 1,
                "updated_at": now,
            }
        )
        updated = True
        break
    if not updated:
        pending.append(
            {
                "chapter": int(chapter_number),
                "character": name,
                "phase": phase,
                "error": str(error or ""),
                "sources": list(sources or []),
                "attempts": 1,
                "created_at": now,
                "updated_at": now,
            }
        )
    payload["pending"] = pending
    _save_pending_intro_payload(storage, bundle, payload)


def _clear_pending_character_intro(
    *,
    storage: "FileSystemStorage",
    bundle: "LongProjectBundle",
    character_name: str,
) -> None:
    name = clean_character_name(character_name)
    if not name:
        return
    payload = _load_pending_intro_payload(storage, bundle)
    pending = [
        item
        for item in list(payload.get("pending", []) or [])
        if not isinstance(item, dict) or clean_character_name(item.get("character")) != name
    ]
    if len(pending) != len(payload.get("pending", []) or []):
        payload["pending"] = pending
        _save_pending_intro_payload(storage, bundle, payload)


def _extract_character_context(
    character_name: str,
    chapter_number: int,
    bundle: "LongProjectBundle",
    blueprint: NarrativeBlueprint | None,
) -> str:
    """Build a textual description of the character's role in the story from outline/blueprint."""
    parts: list[str] = []

    if blueprint:
        # Character arc summary and milestones
        for arc in blueprint.character_arcs:
            if arc.character == character_name:
                if arc.arc_summary:
                    parts.append(f"人物弧光概述：{arc.arc_summary}")
                for mil in arc.milestones:
                    if mil.description:
                        parts.append(
                            f"弧光段落（第{mil.chapter_start}-{mil.chapter_end}章）：{mil.description}"
                        )
                break

        # Narrative phase descriptions where this character is key
        for phase in blueprint.narrative_phases:
            if character_name in phase.key_characters:
                lines = [
                    f"叙事阶段「{phase.phase_name}」（第{phase.chapter_start}-{phase.chapter_end}章）：{phase.description}"
                ]
                if phase.key_events:
                    lines.append("阶段关键事件：" + "；".join(phase.key_events[:3]))
                parts.append("\n".join(lines))

        # Turning point descriptions involving this character
        for tp in blueprint.key_turning_points:
            if character_name in tp.characters_involved:
                parts.append(f"关键转折（第{tp.chapter_number}章）：{tp.description}")

    # Chapter outline goal and beats for the introduction chapter
    ch = bundle.chapter_outline
    if ch.goal:
        parts.append(f"第{chapter_number}章目标：{ch.goal}")
    if ch.beats_summary:
        parts.append("章节情节节拍：" + "；".join(ch.beats_summary[:4]))

    # Relationship mentions from existing characters
    for existing in bundle.character_bible.characters:
        rel = existing.relationships.get(character_name, "").strip()
        if rel:
            parts.append(f"现有角色「{existing.name}」对其的描述：{rel}")

    if not parts:
        return f"该角色将在第{chapter_number}章首次登场，需根据世界观框架构建其背景。"

    return "\n\n".join(parts)


def _build_intro_prompt_context(
    character_name: str,
    chapter_number: int,
    bundle: "LongProjectBundle",
    blueprint: NarrativeBlueprint | None,
    kernel_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the Jinja2 template context for the introduce_character prompt."""
    sb = bundle.story_bible
    outline_ctx = _extract_character_context(character_name, chapter_number, bundle, blueprint)

    existing_ch = [
        {
            "name": c.name,
            "role": c.role,
            "personality": c.personality,
            "relationships": c.relationships,
        }
        for c in bundle.character_bible.characters
    ]

    ctx: dict[str, Any] = {
        "character_name": character_name,
        "chapter_number": chapter_number,
        "genre": getattr(sb, "genre", ""),
        "tone": getattr(sb, "tone", ""),
        "premise": getattr(sb, "premise", ""),
        "world_setting": getattr(sb, "magic_or_tech", ""),
        "existing_characters": existing_ch,
        "outline_context": outline_ctx,
    }

    if kernel_context:
        for key, value in kernel_context.items():
            prefixed = f"kernel_{key}"
            if prefixed not in ctx:
                ctx[prefixed] = value

    return ctx


def _json_text(value: Any, *, limit: int | None = None) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _chapter_outline_payload(bundle: "LongProjectBundle") -> dict[str, Any]:
    outline = bundle.chapter_outline
    if hasattr(outline, "model_dump"):
        return outline.model_dump(mode="json")
    return dict(outline) if isinstance(outline, dict) else {}


def _blueprint_context_payload(
    blueprint: NarrativeBlueprint | None,
    chapter_number: int,
) -> dict[str, Any]:
    if blueprint is None:
        return {}
    phases: list[Any] = []
    for phase in list(getattr(blueprint, "narrative_phases", []) or []):
        if phase.chapter_start <= chapter_number <= phase.chapter_end:
            phases.append(phase.model_dump(mode="json") if hasattr(phase, "model_dump") else phase)

    turning_points: list[Any] = []
    for tp in list(getattr(blueprint, "key_turning_points", []) or []):
        if int(getattr(tp, "chapter_number", 0) or 0) == int(chapter_number):
            turning_points.append(tp.model_dump(mode="json") if hasattr(tp, "model_dump") else tp)

    arcs: list[Any] = []
    for arc in list(getattr(blueprint, "character_arcs", []) or []):
        milestones = []
        for milestone in list(getattr(arc, "milestones", []) or []):
            if milestone.chapter_start <= chapter_number <= milestone.chapter_end:
                milestones.append(
                    milestone.model_dump(mode="json")
                    if hasattr(milestone, "model_dump")
                    else milestone
                )
        if milestones:
            arcs.append(
                {
                    "character": getattr(arc, "character", ""),
                    "arc_summary": getattr(arc, "arc_summary", ""),
                    "milestones": milestones,
                }
            )

    return {
        "narrative_phases": phases,
        "key_turning_points": turning_points,
        "character_arcs": arcs,
    }


def _candidate_records_payload(
    candidate_names: set[str],
    signals: dict[str, set[str]],
    *,
    chapter_contract: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    upstream_candidates = (
        chapter_contract.get("new_character_candidates", []) if chapter_contract else []
    )
    records: list[dict[str, Any]] = []
    for name in sorted(candidate_names):
        upstream_matches: list[Any] = []
        for item in list(upstream_candidates or []):
            item_names = _collect_new_character_candidate_names(item)
            if name in item_names:
                upstream_matches.append(item)
        records.append(
            {
                "name": name,
                "sources": sorted(signals.get(name, set())),
                "upstream_new_character_candidates": upstream_matches[:3],
            }
        )
    return records


def _existing_character_payload(bundle: "LongProjectBundle") -> list[dict[str, Any]]:
    return [
        {
            "name": c.name,
            "role": c.role,
            "relationships": c.relationships,
            "notes": c.notes,
        }
        for c in bundle.character_bible.characters
    ]


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "是", "需要", "建档"}


def _adjudicated_name_from_decision(decision: dict[str, Any], fallback_name: str) -> str | None:
    matched_existing = clean_character_name(decision.get("matched_existing_name"))
    if matched_existing:
        return None
    verdict = str(decision.get("verdict") or "").strip().lower()
    importance = str(decision.get("importance") or "").strip().lower()
    should_create = _coerce_bool(decision.get("should_create_profile"))
    if verdict and verdict not in _CREATE_PROFILE_VERDICTS:
        return None
    if not should_create:
        return None
    if importance in _SKIP_PROFILE_IMPORTANCE:
        return None
    canonical_name = clean_character_name(decision.get("canonical_name")) or fallback_name
    if _is_functional_character_label(canonical_name):
        return None
    return canonical_name if _looks_like_character_name(canonical_name) else None


def _select_adjudicated_character_names(
    raw_payload: dict[str, Any],
    *,
    candidate_names: set[str],
    existing_names: set[str],
    candidate_signals: dict[str, set[str]] | None = None,
    alias_lookup: dict[str, str] | None = None,
) -> list[str]:
    decisions = raw_payload.get("decisions", [])
    if not isinstance(decisions, list):
        return []

    candidate_lookup = {clean_character_name(name): name for name in candidate_names}
    selected: list[str] = []
    seen: set[str] = set()
    for raw_decision in decisions:
        if not isinstance(raw_decision, dict):
            continue
        candidate_name = clean_character_name(
            raw_decision.get("candidate_name")
            or raw_decision.get("name")
            or raw_decision.get("candidate")
        )
        if candidate_name not in candidate_lookup:
            continue
        canonical_name = _adjudicated_name_from_decision(
            raw_decision, candidate_lookup[candidate_name]
        )
        if not canonical_name:
            continue
        if (
            resolve_known_character_name(
                canonical_name,
                existing_names,
                alias_lookup=alias_lookup,
            )
            is not None
        ):
            continue
        if canonical_name in seen:
            continue
        seen.add(canonical_name)
        selected.append(canonical_name)

    deduped = dedupe_candidate_character_names(selected, existing_names=existing_names)
    return sort_auto_new_names(
        deduped,
        signals=candidate_signals,
    )


def _merge_aliases_from_adjudication_payload(
    raw_payload: dict[str, Any],
    *,
    candidate_names: set[str],
    existing_names: set[str],
    alias_lookup: dict[str, str] | None = None,
) -> dict[str, str]:
    decisions = raw_payload.get("decisions", [])
    if not isinstance(decisions, list):
        return {}
    candidate_lookup = {clean_character_name(name): name for name in candidate_names}
    aliases: dict[str, str] = {}
    for raw_decision in decisions:
        if not isinstance(raw_decision, dict):
            continue
        candidate_name = clean_character_name(
            raw_decision.get("candidate_name")
            or raw_decision.get("name")
            or raw_decision.get("candidate")
        )
        if candidate_name not in candidate_lookup:
            continue
        matched_existing = clean_character_name(raw_decision.get("matched_existing_name"))
        canonical_name = clean_character_name(raw_decision.get("canonical_name"))
        verdict = str(raw_decision.get("verdict") or "").strip().lower()
        if verdict != "merge_existing" and not matched_existing:
            continue
        resolved = resolve_known_character_name(
            matched_existing or canonical_name,
            existing_names,
            alias_lookup=alias_lookup,
        )
        if resolved and candidate_name != clean_character_name(resolved):
            aliases[candidate_name] = resolved
    return aliases


async def _adjudicate_character_intro_candidates(
    *,
    router: "ModelRouter",
    builder: "PromptBuilder",
    storage: "FileSystemStorage",
    settings: Any,
    bundle: "LongProjectBundle",
    blueprint: NarrativeBlueprint | None,
    chapter_number: int,
    chapter_contract: dict[str, Any] | None,
    candidate_names: set[str],
    candidate_signals: dict[str, set[str]],
    existing_names: set[str],
    on_step: Callable[[str, Any], None] | None = None,
    alias_lookup: dict[str, str] | None = None,
    selected_source_names: dict[str, str] | None = None,
) -> list[str]:
    if not candidate_names:
        return []

    _on_step = on_step or (lambda s, d: None)
    records = _candidate_records_payload(
        candidate_names,
        candidate_signals,
        chapter_contract=chapter_contract,
    )
    context = {
        "chapter_number": chapter_number,
        "existing_characters": _existing_character_payload(bundle),
        "candidate_records": _json_text(records, limit=8000),
        "chapter_outline": _json_text(_chapter_outline_payload(bundle), limit=6000),
        "chapter_contract": _json_text(chapter_contract or {}, limit=8000),
        "blueprint_context": _json_text(
            _blueprint_context_payload(blueprint, chapter_number),
            limit=8000,
        ),
    }
    request = builder.build(
        TaskType.ADJUDICATE_CHARACTER_INTRODUCTION,
        context,
        max_tokens=calculate_route_aware_max_tokens(
            router,
            TaskType.ADJUDICATE_CHARACTER_INTRODUCTION,
            1200 + len(records) * 220,
            prompt_overhead=2600,
            min_tokens=1024,
        ),
        temperature=getattr(settings, "temp_adjudicate_character_introduction", 0.1),
    )

    _on_step(
        "adjudicate_character_introduction_start",
        {"chapter": chapter_number, "candidates": sorted(candidate_names)},
    )
    try:
        payload = await route_json_object_with_retry(
            router,
            request,
            task_type=TaskType.ADJUDICATE_CHARACTER_INTRODUCTION,
            context=context,
            retry_temperature=0.0,
        )
    except Exception as exc:
        logger.warning(
            "adjudicate_character_introduction: chapter %d failed, skipping auto intro: %s",
            chapter_number,
            exc,
        )
        _on_step(
            "adjudicate_character_introduction_failed",
            {"chapter": chapter_number, "error": str(exc)},
        )
        for name in sorted(candidate_names):
            _record_pending_character_intro(
                storage=storage,
                bundle=bundle,
                chapter_number=chapter_number,
                character_name=name,
                phase="adjudicate",
                error=str(exc),
                sources=sorted(candidate_signals.get(name, set())),
            )
        return []

    selected = _select_adjudicated_character_names(
        payload,
        candidate_names=candidate_names,
        existing_names=existing_names,
        candidate_signals=candidate_signals,
        alias_lookup=alias_lookup,
    )
    candidate_lookup = {clean_character_name(name): name for name in candidate_names}
    selected_set = set(selected)
    selected_candidate_names: set[str] = set()
    if selected_source_names is not None:
        selected_source_names.clear()
    for raw_decision in list(payload.get("decisions", []) or []):
        if not isinstance(raw_decision, dict):
            continue
        candidate_name = clean_character_name(
            raw_decision.get("candidate_name")
            or raw_decision.get("name")
            or raw_decision.get("candidate")
        )
        canonical = clean_character_name(raw_decision.get("canonical_name")) or candidate_name
        if canonical in selected_set and candidate_name in candidate_lookup:
            selected_candidate_names.add(candidate_lookup[candidate_name])
            if selected_source_names is not None:
                selected_source_names[canonical] = candidate_lookup[candidate_name]
    merge_aliases = _merge_aliases_from_adjudication_payload(
        payload,
        candidate_names=candidate_names,
        existing_names=existing_names,
        alias_lookup=alias_lookup,
    )
    for alias, canonical in merge_aliases.items():
        try:
            changed = upsert_character_alias(
                project_root=bundle.layout.root,
                canonical_name=canonical,
                alias=alias,
            )
            if changed:
                _on_step(
                    "character_intro_alias_recorded",
                    {"alias": alias, "canonical_name": canonical},
                )
        except Exception as exc:
            logger.warning("character_intro_alias_record_failed | alias=%s error=%s", alias, exc)
        _clear_pending_character_intro(
            storage=storage,
            bundle=bundle,
            character_name=alias,
        )
    rejected = sorted(
        name
        for name in candidate_names
        if name not in selected_set
        and name not in selected_candidate_names
        and name not in merge_aliases
    )
    for name in rejected:
        _clear_pending_character_intro(
            storage=storage,
            bundle=bundle,
            character_name=name,
        )
    _on_step(
        "adjudicate_character_introduction_done",
        {
            "chapter": chapter_number,
            "selected": selected,
            "rejected": rejected,
            "merge_aliases": merge_aliases,
            "summary": str(payload.get("summary") or ""),
        },
    )
    logger.info(
        "adjudicate_character_introduction: chapter %d — selected=%s rejected=%s",
        chapter_number,
        selected,
        rejected,
    )
    return selected


# Fields allowed in CharacterProfile — whitelist for LLM output sanitisation.
# Includes VersionedSchema meta-fields (schema_version, created_at) plus all
# fields defined in CharacterProfile._correct_fields.  The model uses
# extra="forbid", so any unknown key causes a ValidationError.
_ALLOWED_CHARACTER_FIELDS: frozenset[str] = frozenset(
    CharacterProfile._correct_fields | {"schema_version", "created_at"}
)
_PROFILE_WRAPPER_KEYS: tuple[str, ...] = ("character_profile", "profile", "character")
_ENRICH_WRAPPER_KEYS: tuple[str, ...] = (
    "enriched_profile",
    "enrichment",
    "character_profile",
    "profile",
    "character",
)


def _parse_json_dict_response(
    raw_content: str,
    *,
    wrapper_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    parsed = safe_parse_json(raw_content)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
    for key in wrapper_keys:
        nested = parsed.get(key)
        if isinstance(nested, dict):
            return nested
    return parsed


def _parse_character_profile(raw: dict[str, Any], character_name: str) -> CharacterProfile | None:
    """Parse AI response into CharacterProfile. Returns None on failure."""
    try:
        # Strip any extra fields the LLM may have inserted — they would cause a
        # ValidationError under extra="forbid".
        clean_raw = {k: v for k, v in raw.items() if k in _ALLOWED_CHARACTER_FIELDS}
        # Ensure the name matches what we requested (guard against AI drift)
        clean_raw["name"] = character_name
        clean_raw["role"] = normalize_character_role(clean_raw.get("role"))
        return CharacterProfile.model_validate(clean_raw)
    except Exception:
        logger.warning("introduce_character: failed to parse profile for %r", character_name)
        return None


def _profile_with_sanitized_relationships(
    profile: CharacterProfile,
    *,
    known_names: Iterable[str],
    alias_lookup: dict[str, str] | None = None,
) -> CharacterProfile:
    relationships, dropped = sanitize_relationships(
        profile.relationships,
        self_name=profile.name,
        known_names=known_names,
        alias_lookup=alias_lookup,
    )
    if relationships == profile.relationships and not dropped:
        return profile
    data = profile.model_dump(mode="python")
    data["relationships"] = relationships
    if dropped:
        note = str(data.get("notes") or "").strip()
        audit = "关系审计：未自动写入非已知人物关系：" + "；".join(dropped[:5])
        data["notes"] = f"{note}；{audit}" if note else audit
    return CharacterProfile.model_validate(data)


# ── Public API ────────────────────────────────────────────────────────────────


async def introduce_new_characters(
    *,
    router: "ModelRouter",
    builder: "PromptBuilder",
    storage: "FileSystemStorage",
    settings: Any,
    bundle: "LongProjectBundle",
    chapter_number: int,
    trace: "PipelineTrace",
    on_step: Callable[[str, Any], None] | None = None,
    remaining_slots: int | None = None,
    composer: "ContextComposer | None" = None,
) -> list[str]:
    """Detect characters new to this chapter and auto-generate their profiles.

    Profiles are appended to character_bible.json on disk and the bundle's
    ``character_bible`` attribute is updated in place.

    Args:
        router: Model router for LLM calls.
        builder: Prompt builder.
        storage: Filesystem storage for reading/writing JSON.
        settings: Global Settings instance.
        bundle: Loaded project bundle (mutated in-place on new profiles).
        chapter_number: Chapter about to be generated.
        trace: Pipeline trace for observability.
        on_step: Optional step-progress callback.
        composer: Optional ContextComposer for StoryKernel field slices.

    Returns:
        List of character names that were newly generated.
    """
    _on_step = on_step or (lambda s, d: None)

    kernel_context: dict[str, Any] | None = None
    if composer is not None:
        try:
            kernel_context = composer.compose_for_step("character_intro", chapter_number)
        except Exception:
            logger.debug("introduce_character: composer failed, proceeding without kernel context")

    # ── Load blueprint and chapter contract if available ────────────────────
    blueprint: NarrativeBlueprint | None = getattr(bundle, "blueprint", None)
    if storage.exists(bundle.layout.blueprint_path):
        try:
            bp_data = storage.load_json(bundle.layout.blueprint_path)
            blueprint = NarrativeBlueprint.model_validate(bp_data)
        except Exception:
            logger.warning(
                "introduce_character: could not load/parse blueprint, skipping blueprint sources"
            )
    chapter_contract = _load_chapter_contract(
        storage=storage,
        bundle=bundle,
        chapter_number=chapter_number,
    )

    # ── Detect and adjudicate new characters ────────────────────────────────
    candidate_signals = _collect_chapter_character_candidate_signals(
        bundle,
        blueprint,
        chapter_number,
        chapter_contract=chapter_contract,
    )
    pending_payload = _load_pending_intro_payload(storage, bundle)
    candidate_signals, exhausted_pending = apply_pending_retry_policy(
        candidate_signals,
        pending_payload,
        max_attempts=intro_pending_max_attempts(settings),
    )
    if exhausted_pending:
        _on_step(
            "character_intro_pending_exhausted",
            {
                "chapter": chapter_number,
                "characters": exhausted_pending,
            },
        )
    existing = _existing_names(bundle.character_bible)
    alias_lookup = load_character_alias_lookup(bundle.layout.root)
    candidate_names = set(
        name
        for name in dedupe_candidate_character_names(candidate_signals, existing_names=existing)
        if resolve_known_character_name(name, existing, alias_lookup=alias_lookup) is None
    )
    new_names = await _adjudicate_character_intro_candidates(
        router=router,
        builder=builder,
        storage=storage,
        settings=settings,
        bundle=bundle,
        blueprint=blueprint,
        chapter_number=chapter_number,
        chapter_contract=chapter_contract,
        candidate_names=candidate_names,
        candidate_signals=candidate_signals,
        existing_names=existing,
        on_step=_on_step,
        alias_lookup=alias_lookup,
    )
    limit = (
        max(0, int(remaining_slots))
        if remaining_slots is not None
        else _auto_introduce_limit(settings)
    )
    sorted_new_names = sort_auto_new_names(new_names, signals=candidate_signals)
    new_names = _limit_auto_new_names(
        sorted_new_names,
        limit=limit,
        chapter_number=chapter_number,
        on_step=_on_step,
        event_name="introduce_character",
    )

    if not new_names:
        return []

    logger.info(
        "introduce_character: chapter %d — detected %d new character(s): %s",
        chapter_number,
        len(new_names),
        ", ".join(new_names),
    )
    _on_step(
        "introduce_character_detected",
        {"chapter": chapter_number, "new_characters": new_names},
    )

    # ── Generate a profile for each new character ────────────────────────────
    introduced: list[str] = []
    temperature = getattr(settings, "temp_introduce_character", 0.75)
    max_tokens = calculate_route_aware_max_tokens(
        router,
        TaskType.INTRODUCE_CHARACTER,
        1800,
        prompt_overhead=1800,
        min_tokens=2048,
    )

    for name in new_names:
        prompt_ctx = _build_intro_prompt_context(
            name, chapter_number, bundle, blueprint, kernel_context=kernel_context
        )
        request = builder.build(
            TaskType.INTRODUCE_CHARACTER,
            prompt_ctx,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        try:
            raw_dict = await route_json_object_with_retry(
                router,
                request,
                task_type=TaskType.INTRODUCE_CHARACTER,
                context=prompt_ctx,
                wrapper_keys=_PROFILE_WRAPPER_KEYS,
                retry_temperature=0.0,
            )
        except Exception as exc:
            logger.warning("introduce_character: LLM call failed for %r: %s", name, exc)
            _on_step(
                "introduce_character_failed",
                {"chapter": chapter_number, "character": name, "error": str(exc)},
            )
            _record_pending_character_intro(
                storage=storage,
                bundle=bundle,
                chapter_number=chapter_number,
                character_name=name,
                phase="introduce",
                error=str(exc),
                sources=sorted(candidate_signals.get(name, set())),
            )
            continue

        profile = _parse_character_profile(raw_dict, name)
        if profile is None:
            _record_pending_character_intro(
                storage=storage,
                bundle=bundle,
                chapter_number=chapter_number,
                character_name=name,
                phase="parse_profile",
                error="INTRODUCE_CHARACTER response could not be parsed as CharacterProfile",
                sources=sorted(candidate_signals.get(name, set())),
            )
            continue

        profile = _profile_with_sanitized_relationships(
            profile,
            known_names=_existing_names(bundle.character_bible),
            alias_lookup=alias_lookup,
        )

        # Append to in-memory bible
        updated_characters = list(bundle.character_bible.characters) + [profile]
        new_bible = CharacterBible(characters=updated_characters)

        # Persist to disk immediately (so subsequent characters see updated bible)
        storage.save_json(
            bundle.layout.characters_path,
            new_bible.model_dump(mode="json"),
        )

        # Update bundle in place so downstream pipeline stages see the new profile
        bundle.character_bible = new_bible
        await _sync_character_relationships_to_canon(
            bundle=bundle, settings=settings, on_step=_on_step
        )

        _clear_pending_character_intro(
            storage=storage,
            bundle=bundle,
            character_name=name,
        )
        introduced.append(name)
        logger.info("introduce_character: generated profile for %r", name)
        _on_step(
            "introduce_character_done",
            {
                "chapter": chapter_number,
                "character": name,
                "role": profile.role,
            },
        )

    if introduced:
        with trace.step("introduce_characters"):
            pass  # trace marker — actual work is done above

    return introduced


# ── Phase 2: post-chapter enrichment ─────────────────────────────────────────

# Fields that enrich_character may return; all others are preserved from the
# existing skeleton profile
_ENRICH_UPDATABLE_FIELDS: tuple[str, ...] = (
    "appearance",
    "personality",
    "backstory",
    "arc",
    "voice",
)
_ENRICH_STABLE_UPDATABLE_FIELDS: tuple[str, ...] = (
    "gender",
    "social_status",
    "abilities",
)


def _merge_enriched(
    profile: CharacterProfile,
    enriched: dict[str, Any],
    *,
    known_names: Iterable[str] = (),
    alias_lookup: dict[str, str] | None = None,
) -> CharacterProfile:
    """Return a new CharacterProfile with enrich results merged in.

    Only useful, non-placeholder strings from ``enriched`` overwrite the
    corresponding skeleton field. Stable identity fields are updated only when
    the old value is empty or the new value is clearly more specific.

    Relationships from ``enriched`` are merged additively: existing entries are
    never overwritten, and only new (previously absent) relationship keys are
    added.
    """
    data = profile.model_dump(mode="python")
    for field in _ENRICH_UPDATABLE_FIELDS:
        value = usable_profile_text(enriched.get(field, ""))
        if value:
            data[field] = value
    gender_update = normalize_gender_update(data.get("gender"), enriched.get("gender"))
    if gender_update:
        data["gender"] = gender_update
    for field in ("social_status", "abilities"):
        if should_update_stable_text(data.get(field), enriched.get(field)):
            data[field] = str(enriched.get(field) or "").strip()
    # ── relationships merge: supplement, never overwrite ──
    new_rels, dropped_rels = sanitize_relationships(
        enriched.get("relationships"),
        self_name=profile.name,
        known_names=known_names,
        alias_lookup=alias_lookup,
    )
    if new_rels:
        existing_rels: dict[str, Any] = data.get("relationships") or {}
        if not existing_rels:
            data["relationships"] = new_rels
        else:
            merged = dict(existing_rels)
            for key, val in new_rels.items():
                if key not in merged:
                    merged[key] = val
            data["relationships"] = merged
    if dropped_rels:
        note = str(data.get("notes") or "").strip()
        audit = "关系审计：未自动写入非已知人物关系：" + "；".join(dropped_rels[:5])
        data["notes"] = f"{note}；{audit}" if note else audit
    return CharacterProfile.model_validate(data)


async def enrich_introduced_characters(
    *,
    router: "ModelRouter",
    builder: "PromptBuilder",
    storage: "FileSystemStorage",
    settings: Any,
    bundle: "LongProjectBundle",
    chapter_number: int,
    chapter_text: str,
    introduced_names: list[str],
    trace: "PipelineTrace",
    on_step: Callable[[str, Any], None] | None = None,
) -> list[str]:
    """Phase 2: refine newly-introduced character profiles from the actual chapter text.

    For each name in ``introduced_names``, calls ``enrich_character`` with the
    finished prose and merges the returned appearance / personality / backstory /
    arc into the existing skeleton profile.  Structural fields are preserved.

    Args:
        router: Model router for LLM calls.
        builder: Prompt builder.
        storage: Filesystem storage for persisting updates.
        settings: Global Settings instance.
        bundle: Project bundle (character_bible mutated in-place on success).
        chapter_number: Chapter that was just generated.
        chapter_text: Final prose of the chapter.
        introduced_names: Names returned by ``introduce_new_characters`` earlier.
        trace: Pipeline trace for observability.
        on_step: Optional step-progress callback.

    Returns:
        List of character names whose profiles were successfully enriched.
    """
    if not introduced_names or not chapter_text.strip():
        return []

    _on_step = on_step or (lambda s, d: None)

    # Index current bible for fast lookup
    bible_index: dict[str, CharacterProfile] = {
        c.name: c for c in bundle.character_bible.characters
    }
    alias_lookup = load_character_alias_lookup(bundle.layout.root)

    temperature = getattr(settings, "temp_enrich_character", 0.7)
    # Use the complete finished prose as the evidence source.  The model may
    # decide which passages matter, but the local layer should not bias it by
    # only sending the opening.
    text_for_prompt = chapter_text
    max_tokens = calculate_route_aware_max_tokens(
        router,
        TaskType.ENRICH_CHARACTER,
        max(1800, min(len(text_for_prompt) // 2, 3600)),
        prompt_overhead=2200,
        min_tokens=2048,
    )

    enriched_names: list[str] = []

    for name in introduced_names:
        profile = bible_index.get(name)
        if profile is None:
            logger.warning("enrich_introduced: profile for %r not found in bible, skipping", name)
            continue

        prompt_ctx = {
            "character_name": name,
            "role": profile.role,
            "chapter_number": chapter_number,
            "relationships": profile.relationships,
            "chapter_text": text_for_prompt,
        }

        request = builder.build(
            TaskType.ENRICH_CHARACTER,
            prompt_ctx,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        try:
            enriched_data = await route_json_object_with_retry(
                router,
                request,
                task_type=TaskType.ENRICH_CHARACTER,
                context=prompt_ctx,
                wrapper_keys=_ENRICH_WRAPPER_KEYS,
                retry_temperature=0.0,
            )
        except Exception as exc:
            logger.warning("enrich_introduced: LLM call failed for %r: %s", name, exc)
            _on_step(
                "enrich_introduced_failed",
                {"chapter": chapter_number, "character": name, "error": str(exc)},
            )
            continue

        if not isinstance(enriched_data, dict):
            logger.warning("enrich_introduced: unexpected response type for %r", name)
            continue

        updated_profile = _merge_enriched(
            profile,
            enriched_data,
            known_names=_existing_names(bundle.character_bible),
            alias_lookup=alias_lookup,
        )

        # Replace profile in the bible
        updated_characters = [
            updated_profile if c.name == name else c for c in bundle.character_bible.characters
        ]
        new_bible = CharacterBible(characters=updated_characters)

        # Persist and update in-memory bundle
        storage.save_json(
            bundle.layout.characters_path,
            new_bible.model_dump(mode="json"),
        )
        bundle.character_bible = new_bible
        bible_index[name] = updated_profile
        await _sync_character_relationships_to_canon(
            bundle=bundle, settings=settings, on_step=_on_step
        )

        enriched_names.append(name)
        logger.info("enrich_introduced: refined profile for %r (chapter %d)", name, chapter_number)
        _on_step(
            "enrich_introduced_done",
            {
                "chapter": chapter_number,
                "character": name,
                "updated_fields": [
                    f
                    for f in (*_ENRICH_UPDATABLE_FIELDS, *_ENRICH_STABLE_UPDATABLE_FIELDS)
                    if usable_profile_text(enriched_data.get(f, ""))
                ],
            },
        )

    if enriched_names:
        with trace.step("enrich_introduced_characters"):
            pass

    return enriched_names


# ── Phase 2b: auto-register creative-report discoveries ──────────────────────


async def auto_register_from_creative_report(
    *,
    router: "ModelRouter",
    builder: "PromptBuilder",
    storage: "FileSystemStorage",
    settings: Any,
    bundle: "LongProjectBundle",
    chapter_number: int,
    chapter_text: str,
    creative_report: Any,
    trace: "PipelineTrace",
    on_step: Callable[[str, Any], None] | None = None,
    remaining_slots: int | None = None,
) -> list[str]:
    """Phase 2b: auto-register characters discovered in the creative report.

    The extraction step marks genuinely new characters with
    ``should_add_to_bible=True``.  During a fully-automated pipeline run
    there is no user at the keyboard to confirm the CLI prompt, so these
    characters would silently fall through: ``known_characters`` for the
    *next* chapter would contain their name (from canon_state) but the
    ``character_profiles`` context would have no rich profile for them,
    leading to inconsistency in future chapters.

    This function auto-creates minimal bible profiles for such characters and
    then enriches them from the actual chapter prose — the same two steps the
    CLI maintenance command performs interactively, but executed automatically
    so subsequent chapters have full context.

    Characters already present in ``character_bible.json`` are skipped.

    Args:
        router: Model router for LLM calls.
        builder: Prompt builder.
        storage: Filesystem storage.
        settings: Global settings instance.
        bundle: Project bundle (mutated in-place on new profiles).
        chapter_number: Chapter that was just generated.
        chapter_text: Final prose of the chapter.
        creative_report: ``ChapterResult.creative_report`` (or any object with
            a ``new_characters`` attribute / ``CreativeReport`` instance).
        trace: Pipeline trace.
        on_step: Optional step-progress callback.

    Returns:
        List of character names that were registered and enriched.
    """
    _on_step = on_step or (lambda s, d: None)
    from novel_forge.core.domain.guardrails import is_system_artifact_name

    new_chars = getattr(creative_report, "new_characters", None) or []
    if not new_chars:
        return []

    existing_names = _existing_names(bundle.character_bible)
    alias_lookup = load_character_alias_lookup(bundle.layout.root)
    future_contracts = _future_chapter_contracts_payload(
        storage=storage,
        bundle=bundle,
        chapter_number=chapter_number,
    )
    candidates: list[tuple[str, Any]] = []
    candidate_by_name: dict[str, Any] = {}
    candidate_signals: dict[str, set[str]] = {}
    for nc in new_chars:
        matched_existing = clean_character_name(getattr(nc, "matched_existing_name", ""))
        raw_name = clean_character_name(
            getattr(nc, "canonical_name", "") or getattr(nc, "name", "")
        )
        canonical_name = clean_character_name(getattr(nc, "canonical_name", ""))
        importance = str(getattr(nc, "importance", "") or "").strip().lower()
        has_carry_forward = creative_character_has_carry_forward(
            raw_name,
            creative_report,
            future_contracts_payload=future_contracts,
        )
        has_specific_canonical = (
            bool(canonical_name)
            and canonical_name != raw_name
            and not _is_functional_character_label(canonical_name)
        )
        if raw_name and _is_functional_character_label(raw_name) and not has_specific_canonical:
            _on_step(
                "auto_register_character_label_deferred",
                {
                    "chapter": chapter_number,
                    "character": raw_name,
                    "reason": "functional_label_without_canonical_name",
                },
            )
            continue
        if (
            not getattr(nc, "should_add_to_bible", False)
            or matched_existing
            or not creative_importance_allows_auto_register(
                importance,
                has_carry_forward=has_carry_forward,
            )
            or not raw_name
            or not _looks_like_character_name(raw_name)
            or is_system_artifact_name(raw_name)
            or resolve_known_character_name(raw_name, existing_names, alias_lookup=alias_lookup)
            is not None
        ):
            continue
        candidate_by_name.setdefault(raw_name, nc)
        candidate_signals.setdefault(raw_name, {"creative_report"})
        if has_carry_forward:
            candidate_signals[raw_name].add("creative_carry_forward")

    deduped_names = dedupe_candidate_character_names(
        candidate_by_name,
        existing_names=existing_names,
    )
    for name in deduped_names:
        source = candidate_by_name.get(name)
        if source is None:
            source = next(
                (
                    nc
                    for candidate_name, nc in candidate_by_name.items()
                    if clean_character_name(candidate_name).startswith(name)
                ),
                None,
            )
        if source is not None:
            candidates.append((name, source))

    if not candidates:
        return []

    creative_candidate_payload = [
        nc.model_dump(mode="json") if hasattr(nc, "model_dump") else getattr(nc, "__dict__", {})
        for _name, nc in candidates
    ]
    selected_source_names: dict[str, str] = {}
    adjudicated_names = await _adjudicate_character_intro_candidates(
        router=router,
        builder=builder,
        storage=storage,
        settings=settings,
        bundle=bundle,
        blueprint=getattr(bundle, "blueprint", None),
        chapter_number=chapter_number,
        chapter_contract={"new_character_candidates": creative_candidate_payload},
        candidate_names={name for name, _nc in candidates},
        candidate_signals=candidate_signals,
        existing_names=existing_names,
        on_step=_on_step,
        alias_lookup=alias_lookup,
        selected_source_names=selected_source_names,
    )
    if not adjudicated_names:
        return []
    keep_after_adjudication = set(adjudicated_names)
    original_candidates = list(candidates)
    candidates = [
        (name if name in keep_after_adjudication else selected_name, nc)
        for name, nc in candidates
        for selected_name in [
            name
            if name in keep_after_adjudication
            else clean_character_name(getattr(nc, "canonical_name", ""))
        ]
        if name in keep_after_adjudication
        or clean_character_name(getattr(nc, "canonical_name", "")) in keep_after_adjudication
    ]
    for selected_name in adjudicated_names:
        if any(name == selected_name for name, _nc in candidates):
            continue
        source_name = selected_source_names.get(selected_name, "")
        source = next(
            (
                nc
                for name, nc in original_candidates
                if clean_character_name(name) == source_name
                or clean_character_name(getattr(nc, "canonical_name", "")) == selected_name
                or clean_character_name(name) == selected_name
            ),
            None,
        )
        if source is not None:
            candidates.append((selected_name, source))
    if not candidates:
        return []

    limit = (
        max(0, int(remaining_slots))
        if remaining_slots is not None
        else _auto_introduce_limit(settings)
    )
    kept_names = _limit_auto_new_names(
        sort_auto_new_names(
            [name for name, _nc in candidates],
            signals=candidate_signals,
        ),
        limit=limit,
        chapter_number=chapter_number,
        on_step=_on_step,
        event_name="auto_register_character",
    )
    if not kept_names:
        return []
    keep_set = set(kept_names)
    candidates = [(name, nc) for name, nc in candidates if name in keep_set]

    logger.info(
        "auto_register_from_creative_report: chapter %d — %d candidate(s): %s",
        chapter_number,
        len(candidates),
        ", ".join(name for name, _nc in candidates),
    )

    registered: list[str] = []
    temperature = getattr(settings, "temp_enrich_character", 0.7)
    text_for_prompt = chapter_text
    max_tokens = calculate_route_aware_max_tokens(
        router,
        TaskType.ENRICH_CHARACTER,
        max(1800, min(len(text_for_prompt) // 2, 3600)),
        prompt_overhead=2200,
        min_tokens=2048,
    )

    for name, nc in candidates:
        was_enriched = False
        _on_step(
            "auto_register_character_start",
            {
                "chapter": chapter_number,
                "character": name,
                "role": getattr(nc, "role_in_story", ""),
            },
        )

        # ── Step 1: create minimal skeleton from creative_report data ──
        try:
            stub = CharacterProfile.model_validate(
                {
                    "name": name,
                    "role": normalize_character_role(getattr(nc, "role_in_story", "")),
                    "appearance": "",
                    "personality": "",
                    "backstory": getattr(nc, "description", "") or "",
                    "arc": "",
                    "relationships": getattr(nc, "relationship_to_existing", {}) or {},
                    "gender": "不明",
                    "age": "",
                    "social_status": "",
                    "abilities": "",
                    "notes": (
                        f"首次出现：第{chapter_number}章；来源：creative_report；"
                        f"准入理由：LLM 裁决通过，importance={getattr(nc, 'importance', '') or 'unknown'}；"
                        f"预期叙事职能：{getattr(nc, 'role_in_story', '') or '待章节承接确认'}"
                    ),
                }
            )
            stub = _profile_with_sanitized_relationships(
                stub,
                known_names=_existing_names(bundle.character_bible),
                alias_lookup=alias_lookup,
            )
        except Exception as exc:
            logger.warning("auto_register: stub parse failed for %r: %s", name, exc)
            continue

        # ── Step 2: enrich skeleton from actual prose ──
        try:
            enrich_request = builder.build(
                TaskType.ENRICH_CHARACTER,
                {
                    "character_name": name,
                    "role": stub.role,
                    "chapter_number": chapter_number,
                    "relationships": stub.relationships,
                    "chapter_text": text_for_prompt,
                },
                max_tokens=max_tokens,
                temperature=temperature,
            )
            enriched_data = await route_json_object_with_retry(
                router,
                enrich_request,
                task_type=TaskType.ENRICH_CHARACTER,
                context={
                    "character_name": name,
                    "role": stub.role,
                    "chapter_number": chapter_number,
                    "relationships": stub.relationships,
                    "chapter_text": text_for_prompt,
                },
                wrapper_keys=_ENRICH_WRAPPER_KEYS,
                retry_temperature=0.0,
            )
            stub = _merge_enriched(
                stub,
                enriched_data,
                known_names=_existing_names(bundle.character_bible),
                alias_lookup=alias_lookup,
            )
            was_enriched = True
        except Exception as exc:
            logger.warning("auto_register: enrich failed for %r (using stub): %s", name, exc)
            # Continue with plain stub; at least the name+role is recorded

        # ── Step 3: persist to character_bible ──
        try:
            updated_characters = list(bundle.character_bible.characters) + [stub]
            new_bible = CharacterBible(characters=updated_characters)
            storage.save_json(
                bundle.layout.characters_path,
                new_bible.model_dump(mode="json"),
            )
            bundle.character_bible = new_bible
            await _sync_character_relationships_to_canon(
                bundle=bundle,
                settings=settings,
                on_step=_on_step,
            )
            registered.append(name)
            logger.info(
                "auto_register: persisted profile for %r (chapter %d)", name, chapter_number
            )
            _on_step(
                "auto_register_character_done",
                {
                    "chapter": chapter_number,
                    "character": name,
                    "enriched": was_enriched,
                },
            )
        except Exception as exc:
            logger.warning("auto_register: persist failed for %r: %s", name, exc)

    if registered:
        with trace.step("auto_register_from_creative_report"):
            pass

    return registered
