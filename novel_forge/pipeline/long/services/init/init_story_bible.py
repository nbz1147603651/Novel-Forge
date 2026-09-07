"""Implementation slice extracted from init_service.py (init_story_bible.py)."""

from __future__ import annotations

import copy

from novel_forge.pipeline.long.services.init.init_chapter_contracts import (
    _chapter_contract_resume_quality_issues,
)
from novel_forge.pipeline.long.services.init.init_common import (
    CHAPTER_CONTRACTS_ARTIFACT,
    Any,
    BlueprintElementSelection,
    ChapterContract,
    CharacterBible,
    EntityRecord,
    EntityRegistry,
    EntityType,
    InitLongServiceContext,
    NarrativeBlueprint,
    NarrativeStateStore,
    Path,
    PipelineConstants,
    StoryBible,
    StoryOutline,
    StorySpec,
    ValidationError,
    _load_partial_outline_chapters_from_session,
    _log,
    asyncio,
    cast,
    coherence_blocks,
    dataclass,
    get_model_max_output_tokens,
    hash_payload,
    llm_h,
    normalize_coherence_report,
    normalize_llm_narrative_contract,
    outline_h,
    pre_normalize_blueprint_payload,
    re,
    readiness_payload_allows,
    stable_id,
)
from novel_forge.pipeline.long.services.init_repair.policies.chapter_contracts import (
    ensure_chapter_contract_coverage as _ensure_chapter_contract_coverage,
)


@dataclass(frozen=True)
class SourceArtifactsResumeBundle:
    """Validated artifacts needed to retry a late initialization gate."""

    spec: StorySpec
    story_bible: StoryBible
    character_bible: CharacterBible
    character_system: Any
    entity_registry: EntityRegistry
    entity_graph: Any
    style_profile: Any
    creative_packet: Any
    blueprint: NarrativeBlueprint
    outline: StoryOutline
    narrative_contract: dict[str, Any]
    chapter_contracts: dict[str, Any]
    readiness_report: dict[str, Any]
    coherence_reports: dict[str, dict[str, Any] | None]
    outline_ctx: dict[str, Any]
    total_chapters: int
    resume_mode: str = "source_artifacts_only"


@dataclass(frozen=True)
class InitResumeClassification:
    """Readiness-level resume classification for diagnostics and UI."""

    mode: str
    step: str
    label: str
    reason: str
    blocked_stages: tuple[str, ...] = ()
    source_artifacts_only: bool = False

    def as_event_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "step": self.step,
            "label": self.label,
            "reason": self.reason,
            "blocked_stages": list(self.blocked_stages),
            "source_artifacts_only": self.source_artifacts_only,
        }


CHARACTER_GENERATION_MODE_SPLIT = "split_v2"

_CHARACTER_GENERATION_MODES = frozenset({CHARACTER_GENERATION_MODE_SPLIT})

_CHARACTER_GENERATION_META_VERSION = 1

_BLUEPRINT_FULL_KEYS = (
    "synopsis",
    "volume_mode",
    "volumes",
    "narrative_phases",
    "key_turning_points",
    "character_arcs",
    "subplot_plan",
    "suspense_schedule",
    "ending_strategy",
)


async def _complete_init_repair_request_with_token_guard(
    adapter: Any,
    request: Any,
    *,
    label: str,
    max_retries: int = 4,
) -> Any:
    """Call a direct init-repair adapter with the same length retry policy.

    These degraded repair paths intentionally receive an adapter rather than a
    router, so they need a local guard to avoid accepting or immediately
    failing length-truncated responses.
    """
    current = request
    attempts = max(1, int(max_retries or 0) + 1)
    for attempt in range(1, attempts + 1):
        response = await adapter.complete(current)
        if getattr(response, "finish_reason", None) != "length":
            return response

        model_id = (
            getattr(response, "model_id", None)
            or getattr(current, "model_id", None)
            or getattr(adapter, "default_model", "")
        )
        model_limit = get_model_max_output_tokens(str(model_id or ""))
        new_max = llm_h.escalate_retry_tokens(
            int(getattr(current, "max_tokens", 1) or 1),
            getattr(getattr(current, "task_type", None), "value", ""),
            model_max_tokens=model_limit,
        )
        if new_max <= current.max_tokens or attempt >= attempts:
            raise ValueError(
                f"{label} response hit token limit "
                f"(max_tokens={current.max_tokens}, model_limit={model_limit})"
            )
        _log.warning(
            "%s_length_retry | attempt=%d/%d | max_tokens=%d -> %d | model_limit=%d",
            label,
            attempt,
            attempts,
            current.max_tokens,
            new_max,
            model_limit,
        )
        current = current.model_copy(update={"max_tokens": new_max})
        await asyncio.sleep(llm_h.compute_retry_backoff(attempt))

    raise RuntimeError(f"{label} retry loop ended unexpectedly")


_BLUEPRINT_FRAGMENT_PRECHECK_PREFIXES = {
    "overview": ("全书概述", "分卷"),
    "phases": ("叙事阶段",),
    "character_arcs": ("角色弧光",),
    "subplots": ("支线",),
    "suspense": ("悬念",),
}


def _blueprint_spine_budget(
    *,
    total_chapters: int,
    narrative_complexity: str,
    volume_mode: bool = False,
) -> tuple[int, int]:
    """Return target output chars and minimum tokens for quality-first spine.

    When *volume_mode* is enabled the model must emit detailed per-volume
    metadata (milestone_targets, main_conflicts, climax_hint, resolution_hint,
    notes) in addition to the core synopsis and narrative phases.  Empirical
    data shows this roughly doubles the output length, so we add a volume-mode
    multiplier to avoid JSON truncation.
    """
    complexity = str(narrative_complexity or "standard").strip().lower()
    chapter_count = max(1, int(total_chapters or 1))
    target_floor = {
        "simple": 7000,
        "standard": 9000,
        "complex": 11000,
        "epic": 12000,
    }.get(complexity, 9000)
    per_chapter = {
        "simple": 110,
        "standard": 140,
        "complex": 170,
        "epic": 200,
    }.get(complexity, 140)
    min_tokens = {
        "simple": 4096,
        "standard": 6144,
        "complex": 8192,
        "epic": 8192,
    }.get(complexity, 6144)
    # Volume mode produces significantly more output per volume
    # (milestone_targets, main_conflicts, climax_hint, resolution_hint, notes)
    volume_multiplier = 1.6 if volume_mode else 1.0
    target_chars = int(max(target_floor, chapter_count * per_chapter) * volume_multiplier)
    # Also scale min_tokens proportionally to avoid truncation
    scaled_min_tokens = int(min_tokens * volume_multiplier)
    return target_chars, scaled_min_tokens


def _compact_blueprint_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _plain_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _plain_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _compact_text_list(value: Any, *, limit: int = 5, text_limit: int = 120) -> list[str]:
    return [_compact_blueprint_text(item, text_limit) for item in _plain_list(value)[:limit]]


def _compact_chapter_list(value: Any, *, limit: int = 18) -> dict[str, Any]:
    chapters: list[int] = []
    for item in _plain_list(value):
        try:
            chapter = int(item)
        except (TypeError, ValueError):
            continue
        if chapter >= 1:
            chapters.append(chapter)
    chapters = sorted(dict.fromkeys(chapters))
    return {
        "count": len(chapters),
        "sample": chapters[:limit],
        "first": chapters[0] if chapters else None,
        "last": chapters[-1] if chapters else None,
    }


def _compact_blueprint_prompt_snapshot(
    fragments: dict[str, Any],
    *,
    fragment_key: str,
) -> dict[str, Any]:
    """Project prior fragments into compact causal anchors for prompt context."""
    available_fields = [key for key in _BLUEPRINT_FULL_KEYS if key in fragments]
    anchors: dict[str, Any] = {}

    if "synopsis" in fragments:
        anchors["synopsis"] = _compact_blueprint_text(fragments.get("synopsis"), 500)
    if "volume_mode" in fragments:
        anchors["volume_mode"] = bool(fragments.get("volume_mode"))
    if "volumes" in fragments:
        anchors["volumes"] = [
            {
                "volume_number": item.get("volume_number"),
                "title": _compact_blueprint_text(item.get("title"), 80),
                "start_chapter": item.get("start_chapter"),
                "end_chapter": item.get("end_chapter"),
                "arc_goal": _compact_blueprint_text(item.get("arc_goal"), 180),
                "milestone_targets": _compact_text_list(
                    item.get("milestone_targets"), limit=4, text_limit=100
                ),
                "climax_hint": _compact_blueprint_text(item.get("climax_hint"), 120),
                "resolution_hint": _compact_blueprint_text(item.get("resolution_hint"), 120),
            }
            for item in (_plain_mapping(entry) for entry in _plain_list(fragments.get("volumes")))
            if item
        ][:8]
    if "narrative_phases" in fragments:
        anchors["narrative_phases"] = [
            {
                "phase_name": _compact_blueprint_text(item.get("phase_name"), 80),
                "chapter_start": item.get("chapter_start"),
                "chapter_end": item.get("chapter_end"),
                "description": _compact_blueprint_text(item.get("description"), 180),
                "key_events": _compact_text_list(item.get("key_events"), limit=5, text_limit=100),
                "tension_level": _compact_blueprint_text(item.get("tension_level"), 80),
                "primary_locations": _compact_text_list(
                    item.get("primary_locations"), limit=4, text_limit=60
                ),
                "key_characters": _compact_text_list(
                    item.get("key_characters"), limit=6, text_limit=60
                ),
            }
            for item in (
                _plain_mapping(entry) for entry in _plain_list(fragments.get("narrative_phases"))
            )
            if item
        ][:8]
    if "key_turning_points" in fragments:
        anchors["key_turning_points"] = [
            {
                "chapter_number": item.get("chapter_number"),
                "description": _compact_blueprint_text(item.get("description"), 180),
                "location": _compact_blueprint_text(item.get("location"), 80),
                "characters_involved": _compact_text_list(
                    item.get("characters_involved"), limit=6, text_limit=60
                ),
            }
            for item in (
                _plain_mapping(entry) for entry in _plain_list(fragments.get("key_turning_points"))
            )
            if item
        ][:12]
    if "character_arcs" in fragments:
        anchors["character_arcs"] = [
            {
                "character": _compact_blueprint_text(item.get("character"), 80),
                "arc_summary": _compact_blueprint_text(item.get("arc_summary"), 180),
                "milestones": [
                    {
                        "chapter_start": milestone.get("chapter_start"),
                        "chapter_end": milestone.get("chapter_end"),
                        "description": _compact_blueprint_text(milestone.get("description"), 140),
                    }
                    for milestone in (
                        _plain_mapping(entry) for entry in _plain_list(item.get("milestones"))[:8]
                    )
                    if milestone
                ],
            }
            for item in (
                _plain_mapping(entry) for entry in _plain_list(fragments.get("character_arcs"))
            )
            if item
        ][:8]
    if "subplot_plan" in fragments:
        anchors["subplot_plan"] = [
            {
                "name": _compact_blueprint_text(item.get("name"), 90),
                "description": _compact_blueprint_text(item.get("description"), 180),
                "priority": item.get("priority"),
                "involved_chapters": _compact_chapter_list(item.get("involved_chapters")),
                "chapter_events": [
                    {
                        "chapter_number": event.get("chapter_number"),
                        "event": _compact_blueprint_text(event.get("event"), 120),
                        "weave_notes": _compact_blueprint_text(event.get("weave_notes"), 100),
                    }
                    for event in (
                        _plain_mapping(entry)
                        for entry in _plain_list(item.get("chapter_events"))[:8]
                    )
                    if event
                ],
                "weave_links": [
                    {
                        "source_type": link.get("source_type"),
                        "source_ref": _compact_blueprint_text(link.get("source_ref"), 100),
                        "target_subplot": _compact_blueprint_text(link.get("target_subplot"), 80),
                        "trigger_chapter": link.get("trigger_chapter"),
                        "link_type": link.get("link_type"),
                        "description": _compact_blueprint_text(link.get("description"), 120),
                    }
                    for link in (
                        _plain_mapping(entry) for entry in _plain_list(item.get("weave_links"))[:8]
                    )
                    if link
                ],
                "resolution_chapter": item.get("resolution_chapter"),
                "resolution_target": item.get("resolution_target"),
                "resolution_type": item.get("resolution_type"),
            }
            for item in (
                _plain_mapping(entry) for entry in _plain_list(fragments.get("subplot_plan"))
            )
            if item
        ][:10]
    if "suspense_schedule" in fragments:
        anchors["suspense_schedule"] = [
            {
                "suspense_id": item.get("suspense_id"),
                "suspense_type": item.get("suspense_type"),
                "introduce_chapter": item.get("introduce_chapter"),
                "resolve_chapter": item.get("resolve_chapter"),
                "description": _compact_blueprint_text(item.get("description"), 150),
                "urgency_level": item.get("urgency_level"),
                "related_subplot": _compact_blueprint_text(item.get("related_subplot"), 90),
                "strand_affinity": item.get("strand_affinity"),
            }
            for item in (
                _plain_mapping(entry) for entry in _plain_list(fragments.get("suspense_schedule"))
            )
            if item
        ][:12]
    if "ending_strategy" in fragments:
        anchors["ending_strategy"] = _compact_blueprint_text(fragments.get("ending_strategy"), 500)

    return {
        "snapshot_mode": "compact_causal_anchors",
        "target_fragment": fragment_key,
        "available_fields": available_fields,
        "anchors": anchors,
    }


def _partial_blueprint_validation_report(
    fragments: dict[str, Any],
    *,
    block_key: str,
    total_chapters: int,
    narrative_complexity: str,
) -> dict[str, Any]:
    prefixes = _BLUEPRINT_FRAGMENT_PRECHECK_PREFIXES.get(block_key)
    if not prefixes:
        return {}
    normalized = pre_normalize_blueprint_payload(fragments, total_chapters=total_chapters)
    if not isinstance(normalized, dict):
        normalized = fragments
    payload = {key: normalized.get(key) for key in _BLUEPRINT_FULL_KEYS if key in normalized}
    try:
        partial_blueprint = NarrativeBlueprint.model_validate(payload)
    except ValidationError as exc:
        return {
            "block": block_key,
            "status": "shape_error",
            "errors": [str(exc)],
            "warnings": [],
            "suggestions": [],
        }

    from novel_forge.pipeline.long.services.blueprint.blueprint_validation import validate_blueprint

    result = validate_blueprint(
        partial_blueprint,
        total_chapters=total_chapters,
        narrative_complexity=narrative_complexity,
    )

    def _relevant(messages: list[str]) -> list[str]:
        return [
            message
            for message in messages
            if any(message.startswith(prefix) or prefix in message for prefix in prefixes)
        ]

    errors = _relevant(result.errors)
    warnings = _relevant(result.warnings)
    suggestions = _relevant(result.suggestions)
    if not (errors or warnings or suggestions):
        return {}
    return {
        "block": block_key,
        "status": "issues_found" if errors else "warnings",
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
    }


def _load_valid_init_resume_artifact(
    ctx: InitLongServiceContext,
    path: Any,
    validator: Any,
) -> Any | None:
    """Best-effort read for UI resume anchoring; never mutates or rolls back."""
    try:
        if not ctx.storage.exists(path):
            return None
        return validator(ctx.storage.load_json(path))
    except Exception:
        return None


def _outline_resume_progress_snapshot(
    chapters: Any,
    *,
    total_chapters: int,
) -> dict[str, int]:
    """Summarize validated outline progress without assuming no chapter gaps."""
    total = max(1, int(total_chapters or 1))
    numbers: set[int] = set()
    if isinstance(chapters, list):
        for chapter in chapters:
            try:
                number = int(getattr(chapter, "chapter_number", 0) or 0)
            except (TypeError, ValueError):
                continue
            if not 1 <= number <= total:
                continue
            if getattr(chapter, "notes", "") == PipelineConstants.PLACEHOLDER_NOTE:
                continue
            if not getattr(chapter, "beats_summary", None):
                continue
            numbers.add(number)
    if not numbers:
        return {"chapters_done": 0, "latest_chapter": 0, "next_chapter": 1}
    next_chapter = next((number for number in range(1, total + 1) if number not in numbers), total)
    if len(numbers) == total:
        next_chapter = total
    return {
        "chapters_done": len(numbers),
        "latest_chapter": max(numbers),
        "next_chapter": next_chapter,
    }


def _detect_init_resume_anchor(
    ctx: InitLongServiceContext,
    *,
    total_chapters: int,
) -> dict[str, Any] | None:
    """Return the highest fully validated init artifact already on disk.

    This is only a progress/display anchor. The actual pipeline still reloads
    each dependency in order so stale or invalid downstream caches are not
    trusted accidentally.
    """
    from novel_forge.core.schemas.style_profile import ProjectStyleProfile

    layout = ctx.layout

    try:
        if ctx.storage.exists(layout.outline_session_path):
            session_payload = ctx.storage.load_json(layout.outline_session_path)
        else:
            session_payload = {}
    except Exception:
        session_payload = {}
    if isinstance(session_payload, dict):
        recovered_chapters = _load_partial_outline_chapters_from_session(
            ctx.storage,
            layout,
            total_chapters=total_chapters,
        )
        if recovered_chapters:
            progress = _outline_resume_progress_snapshot(
                recovered_chapters,
                total_chapters=total_chapters,
            )
            chapters_done = progress["chapters_done"]
            return {
                "step": "plan_outline",
                "artifact": "outline_session",
                "label": "章节大纲",
                "chapters_done": chapters_done,
                "chapters_total": total_chapters,
                "next_chapter": progress["next_chapter"],
                "partial": True,
                "recovered_from_session": True,
            }

    outline = _load_valid_init_resume_artifact(
        ctx, layout.outline_path, StoryOutline.model_validate
    )
    if outline is not None:
        outline_total = int(getattr(outline, "total_chapters", 0) or 0)
        progress = _outline_resume_progress_snapshot(
            getattr(outline, "chapters", []) or [],
            total_chapters=outline_total or total_chapters,
        )
        chapters_done = progress["chapters_done"]
        if outline_total > 0 and chapters_done > 0 and outline_h.is_outline_complete(outline):
            contract_anchor = _detect_init_contract_resume_anchor(ctx, outline=outline)
            if contract_anchor is not None:
                return contract_anchor
            return {
                "step": "plan_outline",
                "artifact": "outline",
                "label": "章节大纲",
                "chapters_done": chapters_done,
                "chapters_total": outline_total,
                "next_chapter": progress["next_chapter"],
                "partial": chapters_done < outline_total,
            }

    blueprint = _load_valid_init_resume_artifact(
        ctx,
        layout.blueprint_path,
        lambda payload: NarrativeBlueprint.model_validate(
            pre_normalize_blueprint_payload(payload, total_chapters=total_chapters)
        ),
    )
    if blueprint is not None:
        return {"step": "plan_blueprint", "artifact": "blueprint", "label": "叙事蓝图"}

    style_profile = _load_valid_init_resume_artifact(
        ctx,
        layout.style_profile_path,
        ProjectStyleProfile.model_validate,
    )
    if style_profile is not None:
        return {"step": "profile_style", "artifact": "style_profile", "label": "风格规范"}

    character_bible = _load_valid_init_resume_artifact(
        ctx,
        layout.characters_path,
        ctx.coerce_character_bible,
    )
    if character_bible is not None:
        return {
            "step": "init_character_bible",
            "artifact": "character_bible",
            "label": "角色设定",
        }

    element_selection = _load_valid_init_resume_artifact(
        ctx,
        layout.blueprint_elements_path,
        BlueprintElementSelection.model_validate,
    )
    if element_selection is not None:
        return {
            "step": "plan_blueprint_elements",
            "artifact": "blueprint_elements",
            "label": "叙事要素选择",
        }

    story_bible = _load_valid_init_resume_artifact(
        ctx,
        layout.bible_path,
        StoryBible.model_validate,
    )
    if story_bible is not None:
        return {"step": "init_story_bible", "artifact": "story_bible", "label": "世界观设定"}

    spec = _load_valid_init_resume_artifact(ctx, layout.spec_path, StorySpec.model_validate)
    if spec is not None:
        return {"step": "spec", "artifact": "spec", "label": "故事规格"}

    return None


def _detect_init_contract_resume_anchor(
    ctx: InitLongServiceContext,
    *,
    outline: StoryOutline,
) -> dict[str, Any] | None:
    """Return a validated post-outline init resume anchor, if available."""
    layout = ctx.layout
    readiness_path = layout.reports_dir / "init_readiness.json"
    try:
        if ctx.storage.exists(readiness_path):
            readiness = ctx.storage.load_json(readiness_path)
            if isinstance(readiness, dict) and isinstance(readiness.get("stages"), dict):
                allowed = readiness_payload_allows(readiness)
                if (
                    not allowed
                    and _init_readiness_blocks_only_source_artifacts(readiness)
                    and _source_artifacts_resume_prereqs_exist(
                        ctx,
                        outline=outline,
                    )
                ):
                    return {
                        "step": "init_source_artifacts",
                        "artifact": "source_artifacts",
                        "label": "源头准入修复",
                        "allowed": False,
                        "blocked": True,
                        "resumable": True,
                        "summary": str(readiness.get("summary") or "").strip(),
                    }
                from novel_forge.pipeline.long.services.init.init_source_resume import (
                    _init_readiness_blocks_only_claim_coverage,
                )

                if not allowed and _init_readiness_blocks_only_claim_coverage(readiness):
                    return {
                        "step": "init_claim_contract_coverage",
                        "artifact": "chapter_contracts",
                        "label": "契约覆盖修复",
                        "allowed": False,
                        "blocked": True,
                        "resumable": True,
                        "summary": str(readiness.get("summary") or "").strip(),
                    }
                return {
                    "step": "init_readiness",
                    "artifact": "init_readiness",
                    "label": "初始化准入" if allowed else "初始化准入未通过",
                    "allowed": allowed,
                    "blocked": not allowed,
                    "summary": str(readiness.get("summary") or "").strip(),
                }
    except Exception:
        pass

    if _init_contract_coverage_resume_ready(ctx):
        return {
            "step": "init_claim_contract_coverage",
            "artifact": "chapter_contracts",
            "label": "契约覆盖",
        }

    if _init_contract_coherence_resume_ready(ctx):
        return {
            "step": "adjudicate_contract_coherence",
            "artifact": "contract_coherence",
            "label": "契约裁判",
        }

    if _init_chapter_contracts_resume_ready(
        ctx,
        outline=outline,
        strict_noise=bool(getattr(ctx.settings, "init_chapter_contract_resume_strict_noise", True)),
    ):
        return {
            "step": "plan_chapter_contracts",
            "artifact": "chapter_contracts",
            "label": "章节契约",
        }

    if _init_narrative_contract_resume_ready(ctx):
        return {
            "step": "init_narrative_contract",
            "artifact": "narrative_contract",
            "label": "叙事契约",
        }

    return None


def _init_chapter_contracts_resume_ready(
    ctx: InitLongServiceContext,
    *,
    outline: StoryOutline,
    strict_noise: bool = True,
) -> bool:
    path = ctx.layout.plans_dir / "chapter_contracts.json"
    if not ctx.storage.exists(path):
        return False
    try:
        raw_payload = ctx.storage.load_json(path)
        chapter_contracts, coverage = _ensure_chapter_contract_coverage(
            raw_payload,
            outline,
            settings=getattr(ctx, "settings", None),
        )
        if _chapter_contract_resume_quality_issues(
            chapter_contracts,
            coverage,
            strict_noise=strict_noise,
        ):
            return False
        for item in chapter_contracts.get("chapter_contracts", []) or []:
            ChapterContract.model_validate(item)
    except Exception:
        return False
    return True


def _init_narrative_contract_resume_ready(ctx: InitLongServiceContext) -> bool:
    path = ctx.layout.narrative_contract_path
    if not ctx.storage.exists(path):
        return False
    try:
        payload = ctx.storage.load_json(path)
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    return any(payload.get(key) for key in ("plot_threads", "world_rules", "state_rules"))


def _init_contract_coherence_resume_ready(ctx: InitLongServiceContext) -> bool:
    path = ctx.layout.reports_dir / "contract_coherence.json"
    if not ctx.storage.exists(path):
        return False
    try:
        report = ctx.storage.load_json(path)
        if not isinstance(report, dict):
            return False
        normalized = normalize_coherence_report(
            report,
            artifact=CHAPTER_CONTRACTS_ARTIFACT,
        )
        return not coherence_blocks(
            normalized,
            min_severity=getattr(ctx.settings, "init_coherence_block_min_severity", "high"),
        )
    except Exception:
        return False


def _init_contract_coverage_resume_ready(ctx: InitLongServiceContext) -> bool:
    readiness_path = ctx.layout.reports_dir / "init_readiness.json"
    if not ctx.storage.exists(readiness_path):
        return False
    try:
        readiness = ctx.storage.load_json(readiness_path)
    except Exception:
        return False
    if not isinstance(readiness, dict):
        return False
    stages = readiness.get("stages")
    if not isinstance(stages, dict):
        return False
    coverage = stages.get("claim_contract_coverage")
    if not isinstance(coverage, dict):
        return False
    if bool(coverage.get("blocked", False)):
        return False
    verdict = str(coverage.get("verdict") or "").strip().lower()
    return verdict in {"accept", "passed", "ok"}


def _init_readiness_blocks_only_source_artifacts(readiness: Any) -> bool:
    if not isinstance(readiness, dict) or readiness_payload_allows(readiness):
        return False
    stages = readiness.get("stages")
    if not isinstance(stages, dict):
        return False
    source_stage = stages.get("source_artifacts")
    if not isinstance(source_stage, dict) or not bool(source_stage.get("blocked", False)):
        return False
    for stage_name, stage in stages.items():
        if stage_name == "source_artifacts" or not isinstance(stage, dict):
            continue
        if bool(stage.get("blocked", False)):
            return False
    remaining = readiness.get("remaining_issues")
    if isinstance(remaining, list) and remaining:
        return all(
            isinstance(issue, dict) and issue.get("stage") == "source_artifacts"
            for issue in remaining
        )
    return True


def _init_readiness_blocks_only_contract_coherence(readiness: Any) -> bool:
    """Return whether a validated init can resume at contract adjudication."""

    if not isinstance(readiness, dict) or readiness_payload_allows(readiness):
        return False
    stages = readiness.get("stages")
    if not isinstance(stages, dict):
        return False
    target = stages.get("contract_coherence")
    if not isinstance(target, dict) or not bool(target.get("blocked", False)):
        return False
    for stage_name, stage in stages.items():
        if stage_name == "contract_coherence" or not isinstance(stage, dict):
            continue
        if bool(stage.get("blocked", False)):
            return False
    remaining = readiness.get("remaining_issues")
    return (
        not isinstance(remaining, list)
        or not remaining
        or all(
            isinstance(issue, dict) and issue.get("stage") == "contract_coherence"
            for issue in remaining
        )
    )


_INIT_RESUME_STAGE_MODES: dict[str, tuple[str, str, str]] = {
    "source_artifacts": ("source_artifacts_only", "init_source_artifacts", "源头准入修复"),
    "contract_coherence": ("contract_coherence", "adjudicate_contract_coherence", "契约裁判修复"),
    "claim_contract_coverage": ("claim_coverage", "init_claim_contract_coverage", "契约覆盖修复"),
    "outline_inheritance": (
        "outline_inheritance",
        "adjudicate_outline_inheritance",
        "大纲继承修复",
    ),
    "blueprint_coherence": (
        "blueprint_coherence",
        "adjudicate_blueprint_coherence",
        "蓝图裁判修复",
    ),
}


def _classify_init_readiness_resume(readiness: Any) -> InitResumeClassification:
    if not isinstance(readiness, dict):
        return InitResumeClassification(
            mode="none",
            step="",
            label="",
            reason="missing_readiness",
        )
    if readiness_payload_allows(readiness):
        return InitResumeClassification(
            mode="allowed",
            step="canon_state",
            label="规范初始化",
            reason="readiness_allowed",
        )
    stages = readiness.get("stages")
    if not isinstance(stages, dict):
        return InitResumeClassification(
            mode="generic_blocked",
            step="init_readiness",
            label="初始化准入",
            reason="invalid_readiness_stages",
        )
    blocked_stages = tuple(
        str(stage_name or "").strip()
        for stage_name, stage_payload in stages.items()
        if isinstance(stage_payload, dict) and bool(stage_payload.get("blocked", False))
    )
    if _init_readiness_blocks_only_source_artifacts(readiness):
        mode, step, label = _INIT_RESUME_STAGE_MODES["source_artifacts"]
        return InitResumeClassification(
            mode=mode,
            step=step,
            label=label,
            reason="source_artifacts_only_blocked",
            blocked_stages=blocked_stages,
            source_artifacts_only=True,
        )
    for stage_name in blocked_stages:
        mapped = _INIT_RESUME_STAGE_MODES.get(stage_name)
        if mapped is None:
            continue
        mode, step, label = mapped
        return InitResumeClassification(
            mode=mode,
            step=step,
            label=label,
            reason=f"{stage_name}_blocked",
            blocked_stages=blocked_stages,
        )
    return InitResumeClassification(
        mode="generic_blocked",
        step="init_readiness",
        label="初始化准入",
        reason="readiness_blocked",
        blocked_stages=blocked_stages,
    )


def _source_artifacts_resume_required_paths(ctx: InitLongServiceContext) -> tuple[Path, ...]:
    layout = ctx.layout
    return (
        layout.spec_path,
        layout.bible_path,
        layout.characters_path,
        layout.blueprint_elements_path,
        layout.blueprint_path,
        layout.outline_path,
        layout.narrative_contract_path,
        layout.plans_dir / "creative_director_packet.json",
        layout.plans_dir / "chapter_contracts.json",
        layout.states_dir / "init_v2" / "character_system.json",
        layout.narrative_state_dir / "entity_graph.json",
    )


def _source_artifacts_resume_prereqs_exist(
    ctx: InitLongServiceContext,
    *,
    outline: StoryOutline,
) -> bool:
    if not all(ctx.storage.exists(path) for path in _source_artifacts_resume_required_paths(ctx)):
        return False
    return _init_chapter_contracts_resume_ready(
        ctx,
        outline=outline,
        strict_noise=False,
    )


def _supplemental_non_character_registry(
    registry: EntityRegistry,
    *,
    baseline_registry: EntityRegistry,
) -> EntityRegistry:
    """Keep only LLM-supplied entities that cannot be derived from CharacterSystem."""
    # An ``unknown`` baseline record is a pending semantic slot, not a reason
    # to discard the LLM's typed replacement for the same entity.
    baseline_ids = {
        str(entity.entity_id or "").strip()
        for entity in baseline_registry.entities
        if entity.entity_type != "unknown"
    }
    baseline_names = {
        str(entity.name or "").strip()
        for entity in baseline_registry.entities
        if entity.entity_type != "unknown"
    }
    supplemental = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for entity in registry.entities:
        entity_id = str(entity.entity_id or "").strip()
        name = str(entity.name or "").strip()
        if not name:
            continue
        if entity.entity_type == "character":
            continue
        if entity_id and entity_id in baseline_ids:
            continue
        if name in baseline_names:
            continue
        if entity_id and entity_id in seen_ids:
            continue
        if name in seen_names:
            continue
        supplemental.append(entity)
        if entity_id:
            seen_ids.add(entity_id)
        seen_names.add(name)
    return EntityRegistry(entities=supplemental)


_ENTITY_KEY_HINTS: dict[EntityType, tuple[str, ...]] = {
    "location": (
        "location",
        "locations",
        "place",
        "places",
        "city",
        "region",
        "venue",
        "地点",
        "场景",
        "城市",
        "地区",
        "世界",
    ),
    "organization": (
        "organization",
        "organizations",
        "faction",
        "guild",
        "school",
        "company",
        "court",
        "sect",
        "clan",
        "group",
        "势力",
        "组织",
        "宗门",
        "学院",
        "公司",
        "家族",
    ),
    "item": (
        "item",
        "items",
        "artifact",
        "object",
        "prop",
        "weapon",
        "relic",
        "token",
        "物品",
        "道具",
        "信物",
        "器物",
        "武器",
    ),
    "concept": (
        "theme",
        "themes",
        "motif",
        "motifs",
        "concept",
        "concepts",
        "主题",
        "概念",
        "意象",
    ),
}


def _entity_type_from_key(path: tuple[str, ...]) -> EntityType | None:
    """Classify only the current structured field, never an ancestor path.

    Ancestor matching used to turn every leaf below ``world_rule_book`` into a
    location and every leaf below ``extension_elements`` into a concept.  The
    extractor is an allow-list projection, so semantic ownership belongs to
    the nearest named field only; numeric list indexes inherit nothing.
    """
    if not path:
        return None
    key = str(path[-1]).strip().lower()
    if not key or key.isdigit():
        return None
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", key).strip("_")
    for entity_type, hints in _ENTITY_KEY_HINTS.items():
        if any(
            hint.lower() == normalized or normalized.endswith(f"_{hint.lower()}") for hint in hints
        ):
            return entity_type
    return None


def _entity_candidate_names(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        for sep in ("、", "，", ",", ";", "；", "/", "|"):
            text = text.replace(sep, "\n")
        return [
            item.strip(" \t\r\n\"'`[]()（）")
            for item in text.splitlines()
            if 1 < len(item.strip(" \t\r\n\"'`[]()（）")) <= 32
        ]
    if isinstance(value, dict):
        for key in ("name", "title", "label", "element_name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return _entity_candidate_names(candidate)
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            names.extend(_entity_candidate_names(item))
        return list(dict.fromkeys(names))
    return []


def _concept_candidate_names(value: Any) -> list[str]:
    """Extract compact concept labels, never narrative/theme prose."""
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            names.extend(_concept_candidate_names(item))
        return list(dict.fromkeys(names))
    if isinstance(value, dict):
        for key in ("name", "title", "label", "element_name"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return _concept_candidate_names(candidate)
        return []
    if not isinstance(value, str):
        return []
    text = value.strip(" \t\r\n\"'`[]()（）")
    if not text:
        return []
    for separator in ("：", ":"):
        if separator in text:
            label = text.split(separator, 1)[0].strip()
            return [label] if 1 < len(label) <= 16 else []
    if len(text) <= 12 and not any(mark in text for mark in ("。", "，", ",", "；", ";")):
        return [text]
    return []


def _deterministic_non_character_registry(*sources: Any) -> EntityRegistry:
    """Extract non-character entities from structured init artifacts."""
    by_name: dict[str, EntityRecord] = {}

    def add(name: str, entity_type: str, source_path: str) -> None:
        clean_name = name.strip()
        if not clean_name or clean_name in by_name:
            return
        # Normalize str to EntityType - use cast since we've validated the value
        valid_types: set[str] = {
            "character",
            "location",
            "item",
            "organization",
            "concept",
            "unknown",
        }
        normalized_type: EntityType = cast(
            EntityType, entity_type if entity_type in valid_types else "unknown"
        )
        by_name[clean_name] = EntityRecord(
            entity_id=stable_id(normalized_type[:4], clean_name),
            name=clean_name,
            entity_type=normalized_type,
            aliases=[],
            source="deterministic_structured_entity_registry",
            notes=f"extracted_from={source_path}",
        )

    def walk(value: Any, path: tuple[str, ...]) -> None:
        entity_type = _entity_type_from_key(path)
        candidates = (
            _concept_candidate_names(value)
            if entity_type == "concept"
            else _entity_candidate_names(value)
        )
        if entity_type:
            for candidate in candidates:
                add(candidate, entity_type, ".".join(path))
        if isinstance(value, dict):
            nested_type = entity_type
            if nested_type and not candidates:
                for key in ("name", "title", "label", "element_name"):
                    candidate_value = value.get(key)
                    if isinstance(candidate_value, str):
                        nested_candidates = (
                            _concept_candidate_names(candidate_value)
                            if nested_type == "concept"
                            else _entity_candidate_names(candidate_value)
                        )
                        for name in nested_candidates:
                            add(name, nested_type, ".".join((*path, key)))
            for key, child in value.items():
                if key in {"schema_version", "created_at"}:
                    continue
                walk(child, (*path, str(key)))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*path, str(index)))

    for index, source in enumerate(sources, start=1):
        walk(source, (f"source_{index}",))
    return EntityRegistry(
        entities=sorted(by_name.values(), key=lambda item: (item.entity_type, item.name))
    )


def _merge_entity_registries(*registries: EntityRegistry) -> EntityRegistry:
    by_name: dict[str, EntityRecord] = {}
    for registry in registries:
        for entity in registry.entities:
            name = str(entity.name or "").strip()
            if not name:
                continue
            existing = by_name.get(name)
            if existing is None:
                by_name[name] = entity
                continue
            aliases = list(dict.fromkeys([*existing.aliases, *entity.aliases]))
            by_name[name] = existing.model_copy(
                update={
                    "aliases": aliases,
                    "notes": existing.notes or entity.notes,
                    "source": existing.source or entity.source,
                    "entity_type": existing.entity_type
                    if existing.entity_type != "unknown"
                    else entity.entity_type,
                }
            )
    return EntityRegistry(
        entities=sorted(by_name.values(), key=lambda item: (item.entity_type, item.name))
    )


def _non_character_entity_coverage(registry: EntityRegistry) -> tuple[int, set[str]]:
    types = {
        str(entity.entity_type)
        for entity in registry.entities
        if entity.entity_type != "character" and str(entity.name or "").strip()
    }
    count = sum(
        1
        for entity in registry.entities
        if entity.entity_type != "character" and str(entity.name or "").strip()
    )
    return count, types


_ENTITY_REQUIRED_TYPES: frozenset[EntityType] = frozenset(
    {"location", "item", "organization", "concept"}
)


def _entity_registry_needs_llm_supplement(registry: EntityRegistry) -> bool:
    non_character_count, entity_types = _non_character_entity_coverage(registry)
    # Convert entity_types from set[str] to set[EntityType] for the set operation
    typed_entity_types: set[EntityType] = {
        cast(EntityType, t)
        for t in entity_types
        if t in {"character", "location", "item", "organization", "concept", "unknown"}
    }
    has_unknown = any(entity.entity_type == "unknown" for entity in registry.entities)
    return (
        has_unknown or non_character_count < 3 or bool(_ENTITY_REQUIRED_TYPES - typed_entity_types)
    )


def _set_entity_registry_mode_metric(ctx: Any, mode: str) -> None:
    metrics = getattr(ctx, "_init_efficiency_metrics", None)
    if not isinstance(metrics, dict):
        metrics = {}
        try:
            ctx._init_efficiency_metrics = metrics
        except Exception:
            return
    metrics["entity_registry_mode"] = mode


_DETERMINISTIC_CONTRACT_SNAPSHOT_KEY = "deterministic_contract_snapshot"
_NARRATIVE_CONTRACT_RUNTIME_KEYS = frozenset(
    {
        "llm_contract",
        "llm_contract_source",
        "llm_contract_input_hashes",
        _DETERMINISTIC_CONTRACT_SNAPSHOT_KEY,
    }
)


def _deterministic_narrative_contract_snapshot(payload: Any) -> dict[str, Any]:
    """Return the immutable deterministic portion of a persisted contract.

    ``narrative_contract.json`` is also the envelope for the adjudication contract.
    Hashing that mutable envelope made every persisted contract invalidate itself on
    resume.  New artifacts carry an explicit snapshot; legacy artifacts are projected
    by removing the runtime attachment fields.
    """

    if not isinstance(payload, dict):
        return {}
    snapshot = payload.get(_DETERMINISTIC_CONTRACT_SNAPSHOT_KEY)
    if isinstance(snapshot, dict):
        return copy.deepcopy(snapshot)
    return {
        str(key): copy.deepcopy(value)
        for key, value in payload.items()
        if str(key) not in _NARRATIVE_CONTRACT_RUNTIME_KEYS
    }


def _llm_narrative_contract_input_hashes(
    *,
    spec: Any,
    story_bible: Any,
    character_bible: Any,
    entity_registry: Any,
    deterministic_contract: Any,
) -> dict[str, str]:
    """Fingerprint the upstream artifacts that shape the narrative contract."""

    return {
        "spec": hash_payload(spec),
        "story_bible": hash_payload(story_bible),
        "character_bible": hash_payload(character_bible),
        "entity_registry": hash_payload(entity_registry),
        "deterministic_contract": hash_payload(
            _deterministic_narrative_contract_snapshot(deterministic_contract)
        ),
    }


def _load_reusable_llm_narrative_contract(
    ctx: InitLongServiceContext,
    *,
    input_hashes: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Load an already generated LLM narrative contract when it is structurally usable."""

    path = ctx.layout.narrative_contract_path
    if not ctx.storage.exists(path):
        return None
    try:
        payload = ctx.storage.load_json(path)
    except Exception as exc:
        _log.warning("llm_narrative_contract_resume_load_failed | path=%s | error=%s", path, exc)
        return None
    raw_contract = payload.get("llm_contract")
    if not isinstance(raw_contract, dict):
        return None
    if input_hashes is not None:
        stored_hashes = payload.get("llm_contract_input_hashes")
        if not isinstance(stored_hashes, dict):
            _log.info("llm_narrative_contract_resume_skipped | reason=missing_input_hashes")
            return None
        normalized_stored_hashes = {str(key): str(value) for key, value in stored_hashes.items()}
        mismatched_keys = {
            key
            for key in normalized_stored_hashes.keys() | input_hashes.keys()
            if normalized_stored_hashes.get(key) != input_hashes.get(key)
        }
        if mismatched_keys:
            is_legacy_deterministic_only_mismatch = (
                mismatched_keys == {"deterministic_contract"}
                and not isinstance(payload.get(_DETERMINISTIC_CONTRACT_SNAPSHOT_KEY), dict)
            )
            if not is_legacy_deterministic_only_mismatch:
                _log.info(
                    "llm_narrative_contract_resume_skipped | "
                    "reason=input_hash_mismatch | keys=%s",
                    sorted(mismatched_keys),
                )
                return None
            _log.info(
                "llm_narrative_contract_resume_legacy_hash_accepted | "
                "reason=deterministic_envelope_self_hash"
            )
    if not all(key in raw_contract for key in ("world_rules", "character_arcs", "plot_threads")):
        _log.info("llm_narrative_contract_resume_skipped | reason=missing_required_keys")
        return None
    try:
        normalized = normalize_llm_narrative_contract(raw_contract)
    except Exception as exc:
        _log.warning("llm_narrative_contract_resume_invalid | error=%s", exc)
        return None
    if not all(isinstance(normalized.get(key), list) for key in ("world_rules", "character_arcs")):
        _log.info("llm_narrative_contract_resume_skipped | reason=invalid_rule_or_arc_lists")
        return None
    if not isinstance(normalized.get("plot_threads"), list):
        _log.info("llm_narrative_contract_resume_skipped | reason=invalid_plot_threads")
        return None
    if not any(
        normalized.get(key)
        for key in ("world_rules", "character_arcs", "plot_threads", "promise_plan")
    ):
        _log.info("llm_narrative_contract_resume_skipped | reason=empty_contract")
        return None
    return normalized


def _persist_llm_narrative_contract(
    ctx: InitLongServiceContext,
    *,
    llm_contract: dict[str, Any],
    state_store: NarrativeStateStore | None,
    source: str = "",
    input_hashes: dict[str, str] | None = None,
) -> None:
    contract_payload = ctx.storage.load_json(ctx.layout.narrative_contract_path)
    contract_payload[_DETERMINISTIC_CONTRACT_SNAPSHOT_KEY] = (
        _deterministic_narrative_contract_snapshot(contract_payload)
    )
    contract_payload["llm_contract"] = llm_contract
    if source:
        contract_payload["llm_contract_source"] = source
    if input_hashes is not None:
        contract_payload["llm_contract_input_hashes"] = input_hashes
    ctx.storage.save_json(ctx.layout.narrative_contract_path, contract_payload)
    if state_store is not None:
        state_payload = dict(llm_contract)
        if source:
            state_payload["source"] = source
        if input_hashes is not None:
            state_payload["input_hashes"] = input_hashes
        ctx.storage.save_json(state_store.root / "narrative_contract.json", state_payload)
