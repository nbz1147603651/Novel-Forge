"""Implementation slice extracted from init_service.py (init_character_bible.py)."""

from __future__ import annotations

import logging
from typing import Any, cast

from novel_forge.core.domain.character_boundary import canonicalize_relationship_items
from novel_forge.pipeline.long.services.init.init_common import (
    BLUEPRINT_ARTIFACT,
    CHAPTER_CONTRACTS_ARTIFACT,
    CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION,
    OUTLINE_ARTIFACT,
    STATUS_SKIPPED,
    STATUS_SUCCEEDED,
    CharacterBible,
    CharacterKnowledgeBoundary,
    InitCoherenceError,
    InitLongServiceContext,
    NarrativeBlueprint,
    RunnerProtocol,
    StoryBible,
    StoryOutline,
    StorySpec,
    TaskType,
    _load_cached_model_or_rollback,
    _rollback_cached_init_step,
    build_character_system,
    build_init_context,
    build_shared_evidence_anchor,
    calculate_route_aware_max_tokens,
    dump_story_bible_for_prompt,
    hash_payload,
    init_coherence_artifact_hashes,
    load_reusable_init_coherence_profile,
    manifest_for_context,
    readiness_payload_allows,
    refine_init_coherence_profile,
    run_init_coherence_v2_gate,
    set_init_profile_mode_metric,
)
from novel_forge.pipeline.long.services.init.init_source_resume import (
    _load_optional_json,
    _load_reusable_init_coherence_report,
    _record_init_resume_decision,
    _save_init_readiness,
    _seed_init_coherence_profile,
)
from novel_forge.pipeline.long.services.init.init_story_bible import (
    _CHARACTER_GENERATION_META_VERSION,
    _CHARACTER_GENERATION_MODES,
    CHARACTER_GENERATION_MODE_SPLIT,
)

_log = logging.getLogger(__name__)

async def ensure_init_readiness_for_existing_project(
    runner: RunnerProtocol,
    *,
    project_id: str,
) -> dict[str, Any] | None:
    """Run a read-only init coherence preflight for old projects missing readiness."""
    required_runner_attrs = (
        "_router",
        "_builder",
        "_settings",
        "_config",
        "_on_step",
        "_call_with_retry",
        "_coerce_character_bible",
        "_is_outline_option_enabled_for_task",
    )
    if not all(hasattr(runner, attr) for attr in required_runner_attrs):
        return None
    ctx = build_init_context(runner, project_id)
    settings = ctx.settings
    if not getattr(settings, "init_readiness_required", True):
        return None
    readiness_path = ctx.layout.reports_dir / "init_readiness.json"
    if ctx.storage.exists(readiness_path):
        readiness = ctx.storage.load_json(readiness_path)
        if readiness_payload_allows(readiness):
            return readiness
        raise InitCoherenceError(
            "初始化准入未通过，章节生成已阻断："
            f"{readiness.get('summary') or '请查看 reports/init_readiness.json'}"
        )
    required_paths = (
        ctx.layout.spec_path,
        ctx.layout.bible_path,
        ctx.layout.characters_path,
        ctx.layout.blueprint_path,
        ctx.layout.outline_path,
    )
    if not all(ctx.storage.exists(path) for path in required_paths):
        return None

    spec = StorySpec.model_validate(ctx.storage.load_json(ctx.layout.spec_path))
    story_bible = StoryBible.model_validate(ctx.storage.load_json(ctx.layout.bible_path))
    character_bible = ctx.coerce_character_bible(ctx.storage.load_json(ctx.layout.characters_path))
    blueprint = NarrativeBlueprint.model_validate(ctx.storage.load_json(ctx.layout.blueprint_path))
    outline = StoryOutline.model_validate(ctx.storage.load_json(ctx.layout.outline_path))

    reports: dict[str, dict[str, Any] | None] = {
        "blueprint_coherence": None,
        "outline_inheritance": None,
        "contract_coherence": None,
        "source_artifacts": None,
    }
    repairs: list[dict[str, Any]] = []

    set_init_profile_mode_metric(ctx, "single_task")
    creative_director_packet = _load_optional_json(
        ctx,
        ctx.layout.plans_dir / "creative_director_packet.json",
    )
    blueprint_payload = blueprint.model_dump(mode="json")
    profile = load_reusable_init_coherence_profile(ctx, require_refined=True)
    if profile is None:
        profile = await refine_init_coherence_profile(
            ctx,
            current_profile=_seed_init_coherence_profile(spec),
            spec=spec.model_dump(mode="json"),
            story_bible=dump_story_bible_for_prompt(story_bible, mode="json"),
            character_bible=character_bible.model_dump(mode="json"),
            creative_director_packet=creative_director_packet,
            blueprint=blueprint_payload,
        )
    outline_payload = outline.model_dump(mode="json")
    blueprint_artifacts = {BLUEPRINT_ARTIFACT: blueprint_payload}
    reports["blueprint_coherence"] = _load_reusable_init_coherence_report(
        ctx,
        stage="blueprint_coherence",
        artifact=BLUEPRINT_ARTIFACT,
        artifacts=blueprint_artifacts,
    )
    if reports["blueprint_coherence"] is None:
        reports["blueprint_coherence"] = await run_init_coherence_v2_gate(
            ctx,
            stage="blueprint_coherence",
            repair_artifact=BLUEPRINT_ARTIFACT,
            profile=profile,
            artifacts=blueprint_artifacts,
        )
        _record_init_resume_decision(
            ctx,
            stage="blueprint_coherence",
            artifact=BLUEPRINT_ARTIFACT,
            action="regenerated",
            reason="cache_missing_or_rejected",
            expected_hashes=init_coherence_artifact_hashes(blueprint_artifacts),
        )

    outline_artifacts = {
        BLUEPRINT_ARTIFACT: blueprint_payload,
        OUTLINE_ARTIFACT: outline_payload,
    }
    reports["outline_inheritance"] = _load_reusable_init_coherence_report(
        ctx,
        stage="outline_inheritance",
        artifact=OUTLINE_ARTIFACT,
        artifacts=outline_artifacts,
    )
    if reports["outline_inheritance"] is None:
        reports["outline_inheritance"] = await run_init_coherence_v2_gate(
            ctx,
            stage="outline_inheritance",
            repair_artifact=OUTLINE_ARTIFACT,
            profile=profile,
            artifacts=outline_artifacts,
        )
        _record_init_resume_decision(
            ctx,
            stage="outline_inheritance",
            artifact=OUTLINE_ARTIFACT,
            action="regenerated",
            reason="cache_missing_or_rejected",
            expected_hashes=init_coherence_artifact_hashes(outline_artifacts),
        )

    chapter_contracts_path = ctx.layout.plans_dir / "chapter_contracts.json"
    if ctx.storage.exists(ctx.layout.narrative_contract_path) and ctx.storage.exists(
        chapter_contracts_path
    ):
        contract_payload = ctx.storage.load_json(ctx.layout.narrative_contract_path)
        llm_contract = contract_payload.get("llm_contract")
        if isinstance(llm_contract, dict):
            chapter_contracts = ctx.storage.load_json(chapter_contracts_path)
            contract_artifacts = {
                OUTLINE_ARTIFACT: outline.model_dump(mode="json"),
                "narrative_contract": llm_contract,
                CHAPTER_CONTRACTS_ARTIFACT: chapter_contracts,
            }
            contract_report = _load_reusable_init_coherence_report(
                ctx,
                stage="contract_coherence",
                artifact=CHAPTER_CONTRACTS_ARTIFACT,
                artifacts=contract_artifacts,
            )
            if contract_report is None:
                contract_report = await run_init_coherence_v2_gate(
                    ctx,
                    stage="contract_coherence",
                    repair_artifact=CHAPTER_CONTRACTS_ARTIFACT,
                    profile=profile,
                    artifacts=contract_artifacts,
                )
                _record_init_resume_decision(
                    ctx,
                    stage="contract_coherence",
                    artifact=CHAPTER_CONTRACTS_ARTIFACT,
                    action="regenerated",
                    reason="cache_missing_or_rejected",
                    expected_hashes=init_coherence_artifact_hashes(contract_artifacts),
                )
            reports["contract_coherence"] = contract_report
            ctx.storage.save_json(
                ctx.layout.reports_dir / "contract_coherence.json", contract_report
            )
            ctx.on_step("adjudicate_contract_coherence", contract_report)

    readiness = _save_init_readiness(ctx, reports=reports, repairs=repairs)
    if not readiness_payload_allows(readiness):
        raise InitCoherenceError(
            "旧项目只读初始化裁判未通过，章节生成已阻断："
            f"{readiness.get('summary') or '请查看 reports/init_readiness.json'}"
        )
    return readiness

def _character_relationship_matrix_path(ctx: InitLongServiceContext) -> Any:
    return ctx.layout.states_dir / "init_v2" / "character_relationship_matrix.json"

def _character_bible_source_path(ctx: InitLongServiceContext) -> Any:
    return ctx.layout.states_dir / "init_v2" / "character_bible_source.json"

def _character_generation_meta_path(ctx: InitLongServiceContext) -> Any:
    return ctx.layout.states_dir / "init_v2" / "character_generation.json"

def _character_generation_mode(settings: Any) -> str:
    _ = settings
    return CHARACTER_GENERATION_MODE_SPLIT

def _read_character_generation_metadata(ctx: InitLongServiceContext) -> dict[str, Any]:
    path = _character_generation_meta_path(ctx)
    try:
        if not ctx.storage.exists(path):
            return {}
        payload = ctx.storage.load_json(path)
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}

def _detect_existing_character_generation_mode(ctx: InitLongServiceContext) -> str | None:
    metadata = _read_character_generation_metadata(ctx)
    mode = str(metadata.get("generation_mode") or "").strip()
    if mode in _CHARACTER_GENERATION_MODES:
        return mode

    split_dir = ctx.layout.root / "initialization" / "fragments" / "character_bible"
    for filename in (
        "character_roster.checkpoint.json",
        "character_profiles.checkpoint.json",
        "character_relationships.checkpoint.json",
        "character_arcs.checkpoint.json",
    ):
        if (split_dir / filename).exists():
            return CHARACTER_GENERATION_MODE_SPLIT
    return None

def _save_character_generation_metadata(
    ctx: InitLongServiceContext,
    *,
    generation_mode: str,
    source_character_bible: CharacterBible,
    projected_character_bible: CharacterBible | None = None,
) -> None:
    projected = projected_character_bible or source_character_bible
    ctx.storage.save_json(
        _character_generation_meta_path(ctx),
        {
            "schema_version": _CHARACTER_GENERATION_META_VERSION,
            "generation_mode": generation_mode,
            "source_path": "states/init_v2/character_bible_source.json",
            "source_character_bible_hash": hash_payload(source_character_bible),
            "projected_character_bible_hash": hash_payload(projected),
        },
    )

def _character_source_metadata_matches(
    metadata: dict[str, Any],
    *,
    generation_mode: str,
    current_character_bible_hash: str,
    source_character_bible_hash: str,
) -> bool:
    if str(metadata.get("generation_mode") or "").strip() != generation_mode:
        return False
    if str(metadata.get("source_character_bible_hash") or "").strip() != (
        source_character_bible_hash
    ):
        return False
    projected_hash = str(metadata.get("projected_character_bible_hash") or "").strip()
    valid_current_hashes = {source_character_bible_hash}
    if projected_hash:
        valid_current_hashes.add(projected_hash)
    return current_character_bible_hash in valid_current_hashes

def _load_cached_character_bible_for_mode(
    ctx: InitLongServiceContext,
    *,
    expected_generation_mode: str,
) -> CharacterBible | None:
    cached = _load_cached_model_or_rollback(
        ctx,
        "character_bible",
        ctx.layout.characters_path,
        ctx.coerce_character_bible,
    )
    if cached is None:
        return None
    cached_mode = _detect_existing_character_generation_mode(ctx)
    if cached_mode != expected_generation_mode:
        _rollback_cached_init_step(
            ctx,
            "character_bible",
            ValueError(
                "cached character_bible was generated by "
                f"{cached_mode or 'unknown'}, but current architecture requires "
                f"{expected_generation_mode}"
            ),
        )
        return None
    return cast(CharacterBible, cached)

def _load_or_refresh_character_bible_source(
    ctx: InitLongServiceContext,
    *,
    character_bible: CharacterBible,
    generation_mode: str,
) -> CharacterBible:
    """Return the unprojected character source for the current generation mode."""
    path = _character_bible_source_path(ctx)
    current_hash = hash_payload(character_bible)
    metadata = _read_character_generation_metadata(ctx)
    if ctx.storage.exists(path):
        try:
            source_character_bible = cast(
                CharacterBible,
                ctx.coerce_character_bible(ctx.storage.load_json(path)),
            )
            source_hash = hash_payload(source_character_bible)
            if _character_source_metadata_matches(
                metadata,
                generation_mode=generation_mode,
                current_character_bible_hash=current_hash,
                source_character_bible_hash=source_hash,
            ):
                return source_character_bible
            if _character_bible_semantic_hash(source_character_bible) == (
                _character_bible_semantic_hash(character_bible)
            ):
                _save_character_generation_metadata(
                    ctx,
                    generation_mode=generation_mode,
                    source_character_bible=source_character_bible,
                    projected_character_bible=character_bible,
                )
                return source_character_bible
            if not metadata and source_hash == current_hash:
                _save_character_generation_metadata(
                    ctx,
                    generation_mode=generation_mode,
                    source_character_bible=source_character_bible,
                    projected_character_bible=character_bible,
                )
                return source_character_bible
        except Exception:
            pass

    ctx.storage.save_json(path, character_bible.model_dump(mode="json"))
    _save_character_generation_metadata(
        ctx,
        generation_mode=generation_mode,
        source_character_bible=character_bible,
        projected_character_bible=character_bible,
    )
    return character_bible

_KNOWLEDGE_BOUNDARY_FIELDS = (
    "known_facts",
    "suspected",
    "misbeliefs",
    "secrets_kept",
    "sensory_access_rules",
)

def _character_bible_without_knowledge_boundaries_payload(
    character_bible: CharacterBible,
) -> dict[str, Any]:
    payload = character_bible.model_dump(mode="json")
    characters = payload.get("characters")
    if isinstance(characters, list):
        cleaned_characters: list[Any] = []
        for raw in characters:
            if isinstance(raw, dict):
                cleaned = dict(raw)
                cleaned.pop("knowledge_boundaries", None)
                cleaned_characters.append(cleaned)
            else:
                cleaned_characters.append(raw)
        payload["characters"] = cleaned_characters
    return payload

def _character_bible_semantic_hash(character_bible: CharacterBible) -> str:
    """Hash character identity/content without derived knowledge-boundary side effects."""

    return hash_payload(_character_bible_without_knowledge_boundaries_payload(character_bible))

def _knowledge_boundary_is_populated(boundary: Any) -> bool:
    if boundary is None:
        return False
    for field in _KNOWLEDGE_BOUNDARY_FIELDS:
        if getattr(boundary, field, None):
            return True
    return False

def _knowledge_boundary_required_profiles(character_bible: CharacterBible) -> list[Any]:
    profiles = list(getattr(character_bible, "characters", []) or [])
    active = [
        profile
        for profile in profiles
        if str(getattr(profile, "status", "active") or "active").strip().lower() != "retired"
    ]
    return active or profiles

def _character_knowledge_boundaries_complete(character_bible: CharacterBible) -> bool:
    required_profiles = _knowledge_boundary_required_profiles(character_bible)
    return bool(required_profiles) and all(
        _knowledge_boundary_is_populated(getattr(profile, "knowledge_boundaries", None))
        for profile in required_profiles
    )

def _knowledge_boundary_payload_from_character_bible(
    character_bible: CharacterBible,
) -> dict[str, Any]:
    characters: list[dict[str, Any]] = []
    for profile in getattr(character_bible, "characters", []) or []:
        boundary = getattr(profile, "knowledge_boundaries", None)
        if not _knowledge_boundary_is_populated(boundary):
            continue
        characters.append(
            {
                "character_id": str(getattr(profile, "character_id", "") or ""),
                "name": str(getattr(profile, "name", "") or ""),
                "knowledge_boundaries": boundary.model_dump(mode="json")
                if hasattr(boundary, "model_dump")
                else boundary,
            }
        )
    return {"characters": characters}

def _apply_knowledge_boundaries_to_character_bible(
    character_bible: CharacterBible,
    payload: Any,
) -> int:
    kb_characters = payload.get("characters", []) if isinstance(payload, dict) else []
    kb_by_id: dict[str, dict[str, Any]] = {}
    kb_by_name: dict[str, dict[str, Any]] = {}
    for kb_item in kb_characters:
        if not isinstance(kb_item, dict):
            continue
        char_id = str(kb_item.get("character_id", "")).strip()
        char_name = str(kb_item.get("name", "")).strip()
        if char_id:
            kb_by_id[char_id] = kb_item
        if char_name:
            kb_by_name[char_name] = kb_item

    updated_profiles: list[Any] = []
    boundaries_applied = 0
    for profile in character_bible.characters:
        kb_data = kb_by_id.get(profile.character_id) or kb_by_name.get(profile.name)
        if kb_data and isinstance(kb_data.get("knowledge_boundaries"), dict):
            try:
                kb = CharacterKnowledgeBoundary.model_validate(kb_data["knowledge_boundaries"])
                profile.knowledge_boundaries = kb
                boundaries_applied += 1
            except Exception as kb_exc:
                _log.warning(
                    "knowledge_boundary_parse_failed | character=%s | error=%s",
                    profile.name,
                    kb_exc,
                )
        updated_profiles.append(profile)
    character_bible.characters = updated_profiles
    return boundaries_applied

def _knowledge_boundary_upstream_hashes(
    *,
    story_bible: StoryBible,
    character_bible: CharacterBible,
) -> dict[str, str]:
    return {
        "story_bible": hash_payload(story_bible),
        "character_bible_without_knowledge_boundaries": _character_bible_semantic_hash(
            character_bible
        ),
    }

def _character_relationship_matrix_roster(character_bible: CharacterBible) -> list[dict[str, Any]]:
    roster: list[dict[str, Any]] = []
    for profile in character_bible.characters:
        roster.append(
            {
                "name": profile.name,
                "role": profile.role,
                "age": profile.age,
                "gender": profile.gender,
                "status": profile.status,
                "time_layer": profile.time_layer,
            }
        )
    return roster

def _character_relationship_matrix_profiles(
    character_bible: CharacterBible,
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for profile in character_bible.characters:
        payload = profile.model_dump(mode="json")
        payload.pop("relationships", None)
        profiles.append(payload)
    return profiles

def _compact_relationship_evidence_snippet(text: str, target_name: str, *, limit: int = 160) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return ""
    index = compact.find(target_name)
    if index < 0:
        return compact[:limit]
    start = max(0, index - limit // 3)
    end = min(len(compact), index + len(target_name) + limit * 2 // 3)
    return compact[start:end]

def _character_relationship_candidate_evidence(
    character_bible: CharacterBible,
) -> list[dict[str, Any]]:
    names = [profile.name for profile in character_bible.characters if profile.name]
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    fields = (
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "notes",
    )
    for profile in character_bible.characters:
        if not profile.name:
            continue
        payload = profile.model_dump(mode="json")
        for target_name in names:
            if target_name == profile.name:
                continue
            for field in fields:
                text = str(payload.get(field) or "")
                if target_name not in text:
                    continue
                key = (profile.name, target_name, field)
                if key in seen:
                    continue
                seen.add(key)
                evidence.append(
                    {
                        "source": profile.name,
                        "target": target_name,
                        "field": field,
                        "snippet": _compact_relationship_evidence_snippet(text, target_name),
                    }
                )
                break
    return evidence

def _relationship_matrix_items_from_payload(payload: Any) -> list[dict[str, Any]]:
    raw_items = payload.get("relationship_matrix") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return []
    return [dict(item) for item in raw_items if isinstance(item, dict)]


def _relationship_matrix_items_for_roster(
    payload: Any,
    *,
    roster: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Enforce the same canonical endpoint boundary before every downstream consumer."""

    result = canonicalize_relationship_items(
        _relationship_matrix_items_from_payload(payload),
        roster=roster,
    )
    return result.accepted, [*result.rejected, *result.contaminated]

def _relationship_matrix_cache_fingerprint(
    *,
    generation_mode: str,
    story_bible: StoryBible,
    character_bible: CharacterBible,
) -> dict[str, str]:
    return {
        "generation_mode": generation_mode,
        "story_bible_hash": hash_payload(story_bible),
        "character_bible_hash": hash_payload(character_bible),
    }

def _relationship_matrix_cache_matches(
    payload: Any,
    *,
    expected: dict[str, str],
) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("prompt_version") != CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION:
        return False
    return all(str(payload.get(key) or "") == value for key, value in expected.items())

async def _load_or_generate_character_relationship_matrix(
    ctx: InitLongServiceContext,
    *,
    base_ctx: dict[str, Any],
    story_bible: StoryBible,
    character_bible: CharacterBible,
    generation_mode: str,
) -> list[dict[str, Any]]:
    """Load or generate the LLM-authored typed relationship matrix."""
    path = _character_relationship_matrix_path(ctx)
    expected_cache = _relationship_matrix_cache_fingerprint(
        generation_mode=generation_mode,
        story_bible=story_bible,
        character_bible=character_bible,
    )
    roster = _character_relationship_matrix_roster(character_bible)
    if ctx.storage.exists(path):
        try:
            cached_payload = ctx.storage.load_json(path)
            cached, cached_rejected = (
                _relationship_matrix_items_for_roster(cached_payload, roster=roster)
                if _relationship_matrix_cache_matches(cached_payload, expected=expected_cache)
                else ([], [])
            )
        except Exception:
            cached, cached_rejected = [], []
        if cached and not cached_rejected:
            return cached

    story_payload = dump_story_bible_for_prompt(story_bible, mode="json")
    profiles = _character_relationship_matrix_profiles(character_bible)
    relationship_candidate_evidence = _character_relationship_candidate_evidence(character_bible)
    matrix_ctx = {
        **base_ctx,
        "relationship_generation_phase": "final",
        "story_bible": story_payload,
        "character_roster": roster,
        "character_profiles": profiles,
        "relationship_candidate_evidence": relationship_candidate_evidence,
        "character_generation_mode": generation_mode,
    }
    matrix_ctx["shared_evidence_anchor"] = build_shared_evidence_anchor(
        "init_character_relationship_matrix.typed",
        matrix_ctx,
        source_keys=("premise", "genre", "tone", "story_bible", "character_roster"),
        max_string_chars=900,
    )
    payload = await ctx.call_with_retry(
        TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
        matrix_ctx,
        max_tokens=calculate_route_aware_max_tokens(
            ctx.router,
            TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
            2800,
            prompt_overhead=3000,
            min_tokens=2048,
            max_cap=8192,
        ),
        temperature=min(
            0.1,
            max(0.0, float(getattr(ctx.settings, "temp_init_character_bible", 0.7) or 0.7)),
        ),
        required_keys=("relationship_matrix",),
        max_retries=3,
        thinking=False,
        multi_turn=False,
    )
    matrix, rejected_matrix_items = _relationship_matrix_items_for_roster(
        payload,
        roster=roster,
    )
    relationship_retry_reasons = [
        {
            "code": issue.code,
            "character_name": issue.character_name,
            "message": issue.message,
        }
        for issue in build_character_system(
            character_bible,
            relationship_matrix=matrix,
        ).audit
        if issue.code == "isolated_active_character"
    ]
    if rejected_matrix_items:
        relationship_retry_reasons.append(
            {
                "code": "invalid_character_reference",
                "character_name": "",
                "message": "上一版包含 roster 外端点；原始错名已隔离，只能使用固定角色清单补边。",
            }
        )
    if relationship_retry_reasons:
        target_names = {
            str(item.get("character_name") or "").strip()
            for item in relationship_retry_reasons
            if str(item.get("character_name") or "").strip()
        }
        focused_evidence = [
            item
            for item in relationship_candidate_evidence
            if str(item.get("source") or "").strip() in target_names
            or str(item.get("target") or "").strip() in target_names
        ]
        relevant_names = set(target_names)
        for item in focused_evidence:
            relevant_names.add(str(item.get("source") or "").strip())
            relevant_names.add(str(item.get("target") or "").strip())
        retry_ctx = {
            "premise": base_ctx.get("premise", ""),
            "story_bible": {"premise": story_payload.get("premise", "")},
            "character_roster": roster,
            "target_character_roster": [
                item for item in roster if str(item.get("name") or "").strip() in target_names
            ],
            "character_profiles": [
                item
                for item in profiles
                if str(item.get("name") or "").strip() in relevant_names
            ],
            "relationship_candidate_evidence": focused_evidence,
            "character_generation_mode": generation_mode,
            "relationship_generation_phase": "repair",
            "relationship_generation_attempt": 2,
            "previous_relationship_matrix": matrix,
            "relationship_retry_reasons": relationship_retry_reasons,
        }
        retry_ctx["shared_evidence_anchor"] = build_shared_evidence_anchor(
            "init_character_relationship_matrix.repair",
            retry_ctx,
            source_keys=(
                "character_roster",
                "target_character_roster",
                "character_profiles",
                "relationship_candidate_evidence",
                "previous_relationship_matrix",
            ),
            max_string_chars=900,
        )
        retry_payload = await ctx.call_with_retry(
            TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
            retry_ctx,
            max_tokens=calculate_route_aware_max_tokens(
                ctx.router,
                TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
                3600,
                prompt_overhead=3000,
                min_tokens=2048,
                max_cap=8192,
            ),
            temperature=min(
                0.1,
                max(0.0, float(getattr(ctx.settings, "temp_init_character_bible", 0.7) or 0.7)),
            ),
            required_keys=("relationship_matrix",),
            max_retries=2,
            thinking=False,
            multi_turn=False,
        )
        retry_matrix, retry_rejected = _relationship_matrix_items_for_roster(
            retry_payload,
            roster=roster,
        )
        rejected_matrix_items.extend(retry_rejected)
        if retry_matrix:
            existing_pairs = {
                frozenset(
                    (
                        str(item.get("character_a") or "").strip(),
                        str(item.get("character_b") or "").strip(),
                    )
                )
                for item in matrix
            }
            matrix.extend(
                item
                for item in retry_matrix
                if frozenset(
                    (
                        str(item.get("character_a") or "").strip(),
                        str(item.get("character_b") or "").strip(),
                    )
                )
                not in existing_pairs
            )
    ctx.storage.save_json(
        path,
        {
            "prompt_version": CHARACTER_RELATIONSHIP_MATRIX_PROMPT_VERSION,
            **expected_cache,
            "relationship_generation_phase": "final",
            "relationship_matrix": matrix,
            "relationship_candidate_evidence": relationship_candidate_evidence,
            "relationship_retry_reasons": relationship_retry_reasons,
            "rejected_relationships": rejected_matrix_items,
        },
    )
    return matrix

def _outline_polish_report_path(ctx: InitLongServiceContext) -> Any:
    return ctx.layout.reports_dir / "outline_polish.json"

def _outline_polish_hint_hash(polish_hint: str) -> str:
    return hash_payload(" ".join(str(polish_hint or "").split()))

def _load_reusable_outline_polish(
    ctx: InitLongServiceContext,
    *,
    outline: StoryOutline,
    polish_hint: str,
) -> dict[str, Any] | None:
    payload = _load_optional_json(ctx, _outline_polish_report_path(ctx))
    if not payload:
        return None
    if str(payload.get("hint_hash") or "") != _outline_polish_hint_hash(polish_hint):
        return None
    if str(payload.get("output_outline_hash") or "") != hash_payload(outline):
        return None
    ctx.on_step(
        "plan_outline_polish_resumed",
        {
            "changed_chapters": payload.get("changed_chapters", []),
            "path": str(_outline_polish_report_path(ctx)),
            "source": "cache",
        },
    )
    manifest_for_context(ctx).record_success(
        artifact="outline_polish",
        workflow="init_long",
        step="plan_outline_polish",
        status=STATUS_SUCCEEDED if bool(payload.get("applied", False)) else STATUS_SKIPPED,
        input_hashes={"hint": _outline_polish_hint_hash(polish_hint)},
        output_hashes={"outline": hash_payload(outline)},
        paths={
            "report": str(_outline_polish_report_path(ctx)),
            "outline": str(ctx.layout.outline_path),
        },
        metadata={"source": "cache", "changed_chapters": payload.get("changed_chapters", [])},
    )
    return payload

def _save_outline_polish_report(
    ctx: InitLongServiceContext,
    *,
    polish_hint: str,
    input_outline_hash: str,
    output_outline: StoryOutline,
    changed_chapters: list[int] | None,
    applied: bool,
) -> None:
    ctx.storage.save_json(
        _outline_polish_report_path(ctx),
        {
            "hint_hash": _outline_polish_hint_hash(polish_hint),
            "input_outline_hash": input_outline_hash,
            "output_outline_hash": hash_payload(output_outline),
            "changed_chapters": changed_chapters or [],
            "applied": applied,
        },
    )
    manifest_for_context(ctx).record_success(
        artifact="outline_polish",
        workflow="init_long",
        step="plan_outline_polish",
        status=STATUS_SUCCEEDED if applied else STATUS_SKIPPED,
        input_hashes={
            "hint": _outline_polish_hint_hash(polish_hint),
            "outline": input_outline_hash,
        },
        output_hashes={"outline": hash_payload(output_outline)},
        paths={
            "report": str(_outline_polish_report_path(ctx)),
            "outline": str(ctx.layout.outline_path),
        },
        metadata={"changed_chapters": changed_chapters or [], "applied": applied},
    )
