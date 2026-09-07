"""Implementation slice extracted from init_service.py (init_source_resume.py)."""

from __future__ import annotations

from typing import Literal

from novel_forge.pipeline.long.services.init.init_chapter_contracts import (
    _assert_chapter_contracts_ready_to_persist,
    _synchronize_chapter_contract_cast_plans,
)
from novel_forge.pipeline.long.services.init.init_common import (
    CHAPTER_CONTRACTS_ARTIFACT,
    CLAIM_LEDGER_JSON,
    OUTLINE_REVEAL_GUARD_VERSION,
    STATUS_BLOCKED,
    STATUS_NEEDS_REPAIR,
    STATUS_SUCCEEDED,
    UTC,
    Any,
    AutoRepairResult,
    BlueprintElementSelection,
    ChapterContract,
    CharacterBible,
    EditorialContract,
    EntityRegistry,
    InitCoherenceError,
    InitLongServiceContext,
    InitTruthGate,
    KnowledgeLedger,
    NarrativeBlueprint,
    NarrativeStateStore,
    Path,
    StoryBible,
    StoryKernelStore,
    StoryOutline,
    StorySpec,
    TaskType,
    ValidationError,
    _assert_canon_project_id_matches,
    _build_base_ctx,
    _ensure_chapter_contract_coverage,
    _log,
    _seed_canon_from_character_bible,
    auto_repair_coherence_issues,
    blocking_issues,
    build_downgraded_report,
    build_init_entity_catalog,
    build_init_readiness_report,
    build_kernel_from_init,
    build_relationship_prompt_overview,
    claim_cache_stats,
    coherence_blocks,
    collect_repair_scopes,
    copy,
    datetime,
    hash_payload,
    hashlib,
    init_coherence_artifact_hashes,
    init_coherence_issue_id,
    json,
    manifest_for_context,
    normalize_artifact_key,
    normalize_coherence_report,
    outline_reveal_guard_input_hashes,
    pre_normalize_blueprint_payload,
    re,
    readiness_payload_allows,
    semantic_compiler_runtime_fingerprint,
    stable_id,
    summarize_unresolved_issues,
    validate_editorial_contract,
    validate_plan_outline_context,
    verify_auto_repair_candidate,
)
from novel_forge.pipeline.long.services.init.init_outline_batch import (
    reconcile_outline_with_chapter_design_matrix,
)
from novel_forge.pipeline.long.services.init.init_repair_manifest import (
    _record_init_repair_manifest_round,
)
from novel_forge.pipeline.long.services.init.init_story_bible import (
    _INIT_RESUME_STAGE_MODES,
    InitResumeClassification,
    SourceArtifactsResumeBundle,
    _init_chapter_contracts_resume_ready,
    _init_readiness_blocks_only_contract_coherence,
    _init_readiness_blocks_only_source_artifacts,
    _llm_narrative_contract_input_hashes,
    _load_reusable_llm_narrative_contract,
    _persist_llm_narrative_contract,
    _source_artifacts_resume_prereqs_exist,
    _source_artifacts_resume_required_paths,
)
from novel_forge.pipeline.long.services.init.init_value_helpers import (
    _init_coherence_blocking_issue_ids,
    _init_coherence_list,
    _init_coherence_min_severity,
    _positive_int,
    _safe_init_int,
)


def _init_coherence_report_path(layout: Any, stage: str) -> Any:
    return layout.reports_dir / f"{stage}.json"


def _load_optional_json(ctx: InitLongServiceContext, path: Any) -> dict[str, Any] | None:
    if not ctx.storage.exists(path):
        return None
    try:
        return ctx.storage.load_json(path)
    except Exception as exc:
        _log.debug("init_optional_json_load_failed | path=%s | error=%s", path, exc)
        return None


def _readiness_stage_as_report(readiness: dict[str, Any], stage: str) -> dict[str, Any] | None:
    stages = readiness.get("stages")
    if not isinstance(stages, dict):
        return None
    payload = stages.get(stage)
    return dict(payload) if isinstance(payload, dict) else None


def _late_init_resume_base_readiness(
    readiness: dict[str, Any],
    *,
    blocked_stage: str,
) -> dict[str, Any]:
    """Return a clean readiness snapshot before retrying one late-init stage."""

    clean = copy.deepcopy(readiness)
    stages = clean.get("stages")
    if isinstance(stages, dict):
        stages.pop(blocked_stage, None)
    remaining = clean.get("remaining_issues")
    if isinstance(remaining, list):
        clean["remaining_issues"] = [
            issue
            for issue in remaining
            if not (isinstance(issue, dict) and issue.get("stage") == blocked_stage)
        ]
    clean["allowed"] = True
    clean["blocked"] = False
    clean["verdict"] = "accept"
    clean["status"] = "pass"
    label = {
        "source_artifacts": "source artifacts",
        "claim_contract_coverage": "Claim-to-Contract 覆盖",
    }.get(blocked_stage, blocked_stage)
    clean["summary"] = f"末端断点恢复：上游产物已复用，本轮仅重新校验 {label}。"
    clean["late_init_resume_from"] = {
        "stage": blocked_stage,
        "summary": str(readiness.get("summary") or ""),
        "remaining_issue_count": len(readiness.get("remaining_issues") or [])
        if isinstance(readiness.get("remaining_issues"), list)
        else 0,
    }
    return clean


def _source_artifacts_resume_base_readiness(readiness: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for the source-artifact retry snapshot."""
    clean = _late_init_resume_base_readiness(readiness, blocked_stage="source_artifacts")
    clean["source_artifacts_resume_from"] = dict(clean["late_init_resume_from"])
    return clean


def _init_readiness_blocks_only_claim_coverage(readiness: Any) -> bool:
    if not isinstance(readiness, dict) or readiness_payload_allows(readiness):
        return False
    stages = readiness.get("stages")
    if not isinstance(stages, dict):
        return False
    coverage = stages.get("claim_contract_coverage")
    if not isinstance(coverage, dict) or not bool(coverage.get("blocked", False)):
        return False
    for stage_name, stage in stages.items():
        if stage_name == "claim_contract_coverage" or not isinstance(stage, dict):
            continue
        if bool(stage.get("blocked", False)):
            return False
    remaining = readiness.get("remaining_issues")
    return (
        not isinstance(remaining, list)
        or not remaining
        or all(
            isinstance(issue, dict) and issue.get("stage") == "claim_contract_coverage"
            for issue in remaining
        )
    )


def _load_source_artifacts_resume_bundle(
    ctx: InitLongServiceContext,
    *,
    premise: str,
    total_chapters: int,
    words_per_chapter: int,
    use_volume_mode: bool,
) -> SourceArtifactsResumeBundle | None:
    """Load a strict late-initialization resume bundle, if safe."""

    readiness = _load_optional_json(ctx, ctx.layout.reports_dir / "init_readiness.json")
    if not isinstance(readiness, dict):
        return None
    if _init_readiness_blocks_only_source_artifacts(readiness):
        resume_mode = "source_artifacts_only"
        blocked_stage = "source_artifacts"
    elif _init_readiness_blocks_only_claim_coverage(readiness):
        resume_mode = "claim_coverage"
        blocked_stage = "claim_contract_coverage"
    elif _init_readiness_blocks_only_contract_coherence(readiness):
        resume_mode = "contract_coherence"
        blocked_stage = "contract_coherence"
    else:
        return None
    resume_step = _INIT_RESUME_STAGE_MODES[blocked_stage][1]
    resume_artifact = (
        CHAPTER_CONTRACTS_ARTIFACT
        if blocked_stage in {"contract_coherence", "claim_contract_coverage"}
        else "source_artifacts"
    )
    try:
        from novel_forge.core.schemas.init_v2 import (
            CharacterSystem,
            CreativeDirectorPacket,
            EntityGraph,
        )
        from novel_forge.core.schemas.style_profile import ProjectStyleProfile

        missing_paths = [
            str(path)
            for path in _source_artifacts_resume_required_paths(ctx)
            if not ctx.storage.exists(path)
        ]
        if missing_paths:
            _log.info(
                "source_artifacts_resume_skipped | reason=missing_paths | paths=%s",
                missing_paths,
            )
            fallback_payload = {
                "mode": resume_mode,
                "step": resume_step,
                "reason": "missing_paths",
                "missing_paths": missing_paths,
            }
            ctx.on_step("init_resume_fallback", fallback_payload)
            _record_init_resume_decision(
                ctx,
                stage=blocked_stage,
                artifact=resume_artifact,
                action="cache_rejected",
                reason="missing_paths",
                metadata=fallback_payload,
            )
            return None

        spec = StorySpec.model_validate(ctx.storage.load_json(ctx.layout.spec_path))
        story_bible = StoryBible.model_validate(ctx.storage.load_json(ctx.layout.bible_path))
        character_bible = ctx.coerce_character_bible(
            ctx.storage.load_json(ctx.layout.characters_path)
        )
        element_selection = BlueprintElementSelection.model_validate(
            ctx.storage.load_json(ctx.layout.blueprint_elements_path)
        )
        character_system = CharacterSystem.model_validate(
            ctx.storage.load_json(ctx.layout.states_dir / "init_v2" / "character_system.json")
        )
        entity_graph = EntityGraph.model_validate(
            ctx.storage.load_json(ctx.layout.narrative_state_dir / "entity_graph.json")
        )
        state_store = NarrativeStateStore(ctx.layout.root)
        entity_registry = state_store.load_entity_registry()
        if not entity_registry.entities:
            entity_registry = EntityRegistry(entities=entity_graph.entities)

        style_profile = (
            ProjectStyleProfile.model_validate(ctx.storage.load_json(ctx.layout.style_profile_path))
            if ctx.storage.exists(ctx.layout.style_profile_path)
            else None
        )
        creative_packet = CreativeDirectorPacket.model_validate(
            ctx.storage.load_json(ctx.layout.plans_dir / "creative_director_packet.json")
        )
        blueprint = NarrativeBlueprint.model_validate(
            pre_normalize_blueprint_payload(
                ctx.storage.load_json(ctx.layout.blueprint_path),
                total_chapters=total_chapters,
            )
        )
        outline = StoryOutline.model_validate(ctx.storage.load_json(ctx.layout.outline_path))
        (
            outline,
            chapter_design_matrix,
            migrated_outline_chapters,
        ) = reconcile_outline_with_chapter_design_matrix(
            outline=outline,
            blueprint=blueprint,
            entity_registry=entity_registry,
            character_bible=character_bible,
        )
        chapter_design_matrix_payload = chapter_design_matrix.model_dump(mode="json")
        ctx.storage.save_json(
            ctx.layout.plans_dir / "chapter_design_matrix.json",
            chapter_design_matrix_payload,
        )
        if migrated_outline_chapters:
            ctx.storage.save_json(ctx.layout.outline_path, outline.model_dump(mode="json"))
            ctx.on_step(
                "init_resume_outline_identity_migrated",
                {
                    "chapters": migrated_outline_chapters,
                    "count": len(migrated_outline_chapters),
                    "reason": "chapter_design_matrix_entity_catalog_changed",
                },
            )
        if not _source_artifacts_resume_prereqs_exist(ctx, outline=outline):
            fallback_payload = {
                "mode": resume_mode,
                "step": resume_step,
                "reason": "chapter_contracts_not_resume_ready",
            }
            ctx.on_step("init_resume_fallback", fallback_payload)
            _record_init_resume_decision(
                ctx,
                stage=blocked_stage,
                artifact=resume_artifact,
                action="cache_rejected",
                reason="chapter_contracts_not_resume_ready",
                metadata=fallback_payload,
            )
            return None
        deterministic_contract = ctx.storage.load_json(ctx.layout.narrative_contract_path)
        narrative_contract_input_hashes = _llm_narrative_contract_input_hashes(
            spec=spec,
            story_bible=story_bible,
            character_bible=character_bible,
            entity_registry=entity_registry,
            deterministic_contract=deterministic_contract,
        )
        narrative_contract = _load_reusable_llm_narrative_contract(
            ctx,
            input_hashes=narrative_contract_input_hashes,
        )
        if narrative_contract is None:
            fallback_payload = {
                "mode": resume_mode,
                "step": resume_step,
                "reason": "missing_reusable_narrative_contract",
            }
            ctx.on_step("init_resume_fallback", fallback_payload)
            _record_init_resume_decision(
                ctx,
                stage=blocked_stage,
                artifact=resume_artifact,
                action="cache_rejected",
                reason="missing_reusable_narrative_contract",
                metadata=fallback_payload,
            )
            return None
        # Migrate legacy self-invalidating contract envelopes while the bundle is
        # already validated.  This is deterministic and performs no model call.
        _persist_llm_narrative_contract(
            ctx,
            llm_contract=narrative_contract,
            state_store=None,
            input_hashes=narrative_contract_input_hashes,
        )
        raw_contracts = ctx.storage.load_json(ctx.layout.plans_dir / "chapter_contracts.json")
        chapter_contracts, contract_coverage = _ensure_chapter_contract_coverage(
            raw_contracts,
            outline,
            settings=ctx.settings,
        )
        migrated_contract_chapters = _synchronize_chapter_contract_cast_plans(
            chapter_contracts,
            outline,
        )
        if migrated_contract_chapters:
            ctx.storage.save_json(
                ctx.layout.plans_dir / "chapter_contracts.json",
                chapter_contracts,
            )
            ctx.on_step(
                "init_resume_contract_identity_migrated",
                {
                    "chapters": migrated_contract_chapters,
                    "count": len(migrated_contract_chapters),
                    "reason": "outline_cast_plan_refreshed",
                },
            )
        chapter_contracts["coverage"] = contract_coverage
        _assert_chapter_contracts_ready_to_persist(
            chapter_contracts,
            contract_coverage,
            allow_local_fallback=True,
        )
        if resume_mode == "claim_coverage":
            from novel_forge.pipeline.long.services.init.init_contract_flow import (
                _claim_coverage_artifacts,
                _claim_coverage_checkpoint_path,
                claim_coverage_checkpoint_matches,
            )

            checkpoint_path = _claim_coverage_checkpoint_path(ctx.layout)
            checkpoint_artifacts = _claim_coverage_artifacts(
                blueprint=blueprint,
                outline=outline,
                llm_contract=narrative_contract,
                chapter_contracts=chapter_contracts,
            )
            if ctx.storage.exists(checkpoint_path) and not claim_coverage_checkpoint_matches(
                ctx,
                artifacts=checkpoint_artifacts,
            ):
                fallback_payload = {
                    "mode": resume_mode,
                    "step": "init_claim_contract_coverage",
                    "reason": "claim_coverage_checkpoint_hash_mismatch",
                }
                ctx.on_step("init_resume_fallback", fallback_payload)
                _record_init_resume_decision(
                    ctx,
                    stage="claim_contract_coverage",
                    artifact=CHAPTER_CONTRACTS_ARTIFACT,
                    action="cache_rejected",
                    reason="claim_coverage_checkpoint_hash_mismatch",
                    metadata=fallback_payload,
                )
                return None

        effective_total_chapters = int(getattr(outline, "total_chapters", 0) or total_chapters)
        expected_total_words = effective_total_chapters * words_per_chapter
        base_ctx = _build_base_ctx(spec, expected_total_words, premise=premise)
        outline_ctx = {
            **base_ctx,
            "spec": spec,
            "story_bible": story_bible,
            "character_bible": character_bible.model_dump(mode="json"),
            "total_chapters": effective_total_chapters,
            "words_per_chapter": words_per_chapter,
            "use_volume_mode": use_volume_mode,
            "blueprint_element_selection": element_selection.model_dump(mode="json"),
            "style_profile": style_profile.model_dump(mode="json")
            if style_profile is not None
            else None,
            "character_system": character_system.model_dump(mode="json"),
            "relationship_overview": build_relationship_prompt_overview(character_system),
            "entity_graph": entity_graph.model_dump(mode="json"),
            "creative_director_packet": creative_packet.model_dump(mode="json"),
            "chapter_design_matrix": chapter_design_matrix_payload,
        }
        validate_plan_outline_context(
            outline_ctx,
            source="init_service.source_artifacts_resume.outline_ctx",
        )

        coherence_reports: dict[str, dict[str, Any] | None] = {
            "blueprint_coherence": _load_optional_json(
                ctx,
                ctx.layout.reports_dir / "blueprint_coherence.json",
            )
            or _readiness_stage_as_report(readiness, "blueprint_coherence"),
            "outline_inheritance": _load_optional_json(
                ctx,
                ctx.layout.reports_dir / "outline_inheritance.json",
            )
            or _readiness_stage_as_report(readiness, "outline_inheritance"),
            "contract_coherence": _load_optional_json(
                ctx,
                ctx.layout.reports_dir / "contract_coherence.json",
            )
            or _readiness_stage_as_report(readiness, "contract_coherence"),
            "claim_contract_coverage": _readiness_stage_as_report(
                readiness,
                "claim_contract_coverage",
            ),
            "source_artifacts": None,
        }

        return SourceArtifactsResumeBundle(
            spec=spec,
            story_bible=story_bible,
            character_bible=character_bible,
            character_system=character_system,
            entity_registry=entity_registry,
            entity_graph=entity_graph,
            style_profile=style_profile,
            creative_packet=creative_packet,
            blueprint=blueprint,
            outline=outline,
            narrative_contract=narrative_contract,
            chapter_contracts=chapter_contracts,
            readiness_report=_late_init_resume_base_readiness(
                readiness,
                blocked_stage=blocked_stage,
            ),
            coherence_reports=coherence_reports,
            outline_ctx=outline_ctx,
            total_chapters=effective_total_chapters,
            resume_mode=resume_mode,
        )
    except Exception as exc:
        _log.info("source_artifacts_resume_skipped | error=%s", exc, exc_info=True)
        fallback_payload = {
            "mode": resume_mode,
            "step": resume_step,
            "reason": "resume_bundle_validation_failed",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        ctx.on_step("init_resume_fallback", fallback_payload)
        _record_init_resume_decision(
            ctx,
            stage=blocked_stage,
            artifact=resume_artifact,
            action="cache_rejected",
            reason="resume_bundle_validation_failed",
            metadata=fallback_payload,
        )
        return None


async def _finish_init_after_source_artifacts(
    ctx: InitLongServiceContext,
    *,
    project_id: str,
    story_bible: StoryBible,
    character_bible: CharacterBible,
    outline: StoryOutline,
    entity_registry: EntityRegistry,
    entity_graph: Any,
) -> Any:
    """Run post-source-artifact init tail shared by source-artifact resume."""

    from novel_forge.pipeline.chapter_runner import (
        InitLongResult,  # noqa: PLC0415 — runtime import to avoid circular dependency
    )
    from novel_forge.story_kernel.store import CanonStore

    layout = ctx.layout
    settings = ctx.settings
    on_step = ctx.on_step

    canon_store = CanonStore(layout.root)
    existing_canon = _assert_canon_project_id_matches(layout, project_id)

    try:
        kernel_narrative_contract: dict[str, Any] | None = None
        if ctx.storage.exists(layout.narrative_contract_path):
            try:
                contract_payload = ctx.storage.load_json(layout.narrative_contract_path)
                kernel_narrative_contract = contract_payload.get("llm_contract") or contract_payload
            except Exception as exc:
                _log.warning(
                    "story_kernel_narrative_contract_load_failed | path=%s | error=%s",
                    layout.narrative_contract_path,
                    exc,
                )

        kernel = build_kernel_from_init(
            project_id=project_id,
            story_bible=story_bible,
            character_bible=character_bible,
            outline=outline,
            narrative_contract=kernel_narrative_contract,
            entity_registry=entity_registry,
            entity_graph=entity_graph,
        )

        for profile in character_bible.characters:
            kb = profile.knowledge_boundaries
            if not any([kb.known_facts, kb.suspected, kb.misbeliefs, kb.secrets_kept]):
                continue
            entity_id = profile.character_id or stable_id("char", profile.name)
            for fact in kb.known_facts:
                kernel.knowledge_ledger.append(
                    KnowledgeLedger(
                        entry_id=f"init_{entity_id}_known_{hash(fact) & 0xFFFFFFFF:08x}",
                        entity_id=entity_id,
                        fact=fact,
                        knowledge_type="known",
                        source_chapter=0,
                        visibility="private",
                        confidence=1.0,
                    )
                )
            for fact in kb.suspected:
                kernel.knowledge_ledger.append(
                    KnowledgeLedger(
                        entry_id=f"init_{entity_id}_suspected_{hash(fact) & 0xFFFFFFFF:08x}",
                        entity_id=entity_id,
                        fact=fact,
                        knowledge_type="suspected",
                        source_chapter=0,
                        visibility="private",
                        confidence=0.6,
                    )
                )
            for fact in kb.misbeliefs:
                kernel.knowledge_ledger.append(
                    KnowledgeLedger(
                        entry_id=f"init_{entity_id}_misbelief_{hash(fact) & 0xFFFFFFFF:08x}",
                        entity_id=entity_id,
                        fact=fact,
                        knowledge_type="misbelief",
                        source_chapter=0,
                        visibility="private",
                        confidence=0.8,
                    )
                )
            for fact in kb.secrets_kept:
                kernel.knowledge_ledger.append(
                    KnowledgeLedger(
                        entry_id=f"init_{entity_id}_secret_{hash(fact) & 0xFFFFFFFF:08x}",
                        entity_id=entity_id,
                        fact=fact,
                        knowledge_type="secret_kept",
                        source_chapter=0,
                        visibility="secret",
                        confidence=1.0,
                    )
                )

        gate_result = InitTruthGate.validate(kernel)
        on_step(
            "story_kernel_gate",
            {
                "passed": gate_result.passed,
                "violations": gate_result.violations,
                "warnings": gate_result.warnings,
            },
        )
        if not gate_result.passed:
            on_step(
                "story_kernel_gate_blocked",
                {
                    "passed": False,
                    "violations": gate_result.violations,
                    "warnings": gate_result.warnings,
                    "action": "block_init_completion",
                },
            )
            violation_summary = "；".join(str(item) for item in gate_result.violations[:8])
            raise InitCoherenceError(
                "Story Kernel Truth Gate 未通过，已阻断初始化完成："
                f"{violation_summary or '未知真值约束违反'}"
            )

        raw_db_path = getattr(settings, "story_kernel_db_path", "")
        db_path = str(raw_db_path).strip() if isinstance(raw_db_path, (str, Path)) else ""
        if not db_path:
            db_path = str(layout.story_kernel_db_path)

        async def _save_kernel_to_db() -> None:
            store = StoryKernelStore(
                db_path,
                wal_mode=bool(getattr(settings, "story_kernel_wal_mode", True)),
            )
            try:
                await store.init_db()
                await store.save_kernel(kernel)
                await store.save_snapshot(0)
            finally:
                await store.close()

        await _save_kernel_to_db()
        on_step(
            "story_kernel_saved",
            {
                "project_id": project_id,
                "db_path": db_path,
                "entities": len(kernel.entities),
                "relationships": len(kernel.relationships),
                "world_rules": len(kernel.world_rules),
                "timeline": len(kernel.timeline),
                "promise_ledger": len(kernel.promise_ledger),
            },
        )
    except InitCoherenceError:
        raise
    except Exception as exc:
        _log.warning(
            "story_kernel_build_failed | project_id=%s | error=%s",
            project_id,
            exc,
            exc_info=True,
        )
        on_step(
            "story_kernel_build_failed",
            {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "narrative_state_required": bool(
                    getattr(settings, "narrative_state_required", True)
                ),
            },
        )
        if bool(getattr(settings, "narrative_state_required", True)):
            raise InitCoherenceError(
                "故事内核初始化失败，已按 narrative_state_required 阻断："
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # Commit the legacy canon only after the Story Kernel has either passed
    # its truth gate or been explicitly treated as optional. This prevents a
    # failed truth gate from leaving a resumable half-initialized canon file.
    if existing_canon is not None:
        canon_state = existing_canon
        on_step("canon_resumed", canon_state)
    else:
        canon_state = canon_store.init(project_id)
        narrative_contract_for_canon: dict[str, Any] | None = None
        if ctx.storage.exists(layout.narrative_contract_path):
            try:
                narrative_contract_for_canon = ctx.storage.load_json(layout.narrative_contract_path)
            except Exception as exc:
                _log.warning(
                    "canon_seed_narrative_contract_load_failed | path=%s | error=%s",
                    layout.narrative_contract_path,
                    exc,
                )
        canon_state = _seed_canon_from_character_bible(
            canon_state,
            character_bible,
            story_bible,
            narrative_contract=narrative_contract_for_canon,
        )
        canon_store.save(canon_state, snapshot=False)
        on_step("canon_init", canon_state)

    return InitLongResult(
        project_id=project_id,
        story_bible=story_bible,
        character_bible=character_bible,
        outline=outline,
        canon_state=canon_state,
        trace_summary=ctx.trace.summary(),
    )


def _init_coherence_hashes_cover(
    recorded: Any,
    expected: dict[str, str],
) -> bool:
    if not isinstance(recorded, dict) or not expected:
        return False
    return all(str(recorded.get(artifact) or "") == digest for artifact, digest in expected.items())


def _load_init_coherence_ledger_stage(
    ctx: InitLongServiceContext,
    *,
    stage: str,
) -> dict[str, Any] | None:
    ledger = _load_optional_json(ctx, ctx.layout.memory_dir / CLAIM_LEDGER_JSON)
    if not ledger:
        return None
    stages = ledger.get("stages")
    stage_entry = stages.get(stage) if isinstance(stages, dict) else None
    return stage_entry if isinstance(stage_entry, dict) else None


def _init_resume_decisions_path(ctx: InitLongServiceContext) -> Any:
    return ctx.layout.reports_dir / "init_resume_decisions.json"


def _record_init_resume_decision(
    ctx: InitLongServiceContext,
    *,
    stage: str,
    artifact: str,
    action: Literal["reused", "regenerated", "cache_rejected", "rollback", "blocked"],
    reason: str,
    path: str = "",
    expected_hashes: dict[str, str] | None = None,
    recorded_hashes: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a best-effort resume/cache decision record for diagnostics."""

    record: dict[str, Any] = {
        "stage": stage,
        "artifact": artifact,
        "action": action,
        "reason": reason,
        "path": path,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if expected_hashes is not None:
        record["expected_hashes"] = dict(expected_hashes)
    if recorded_hashes is not None:
        record["recorded_hashes"] = dict(recorded_hashes)
    if metadata:
        record["metadata"] = dict(metadata)

    decisions_path = _init_resume_decisions_path(ctx)
    try:
        payload = (
            ctx.storage.load_json(decisions_path) if ctx.storage.exists(decisions_path) else {}
        )
    except Exception:
        payload = {}
    decisions = payload.get("decisions") if isinstance(payload, dict) else None
    if not isinstance(decisions, list):
        decisions = []
    decisions.append(record)
    try:
        ctx.storage.save_json(decisions_path, {"schema_version": 1, "decisions": decisions})
    except Exception as exc:
        _log.debug("init_resume_decision_persist_failed | error=%s", exc)


def _emit_init_resume_classified(
    ctx: InitLongServiceContext,
    classification: InitResumeClassification,
) -> None:
    if classification.mode == "none":
        return
    payload = classification.as_event_payload()
    ctx.on_step("init_resume_classified", payload)
    _record_init_resume_decision(
        ctx,
        stage="init_readiness",
        artifact="init_readiness",
        action="blocked" if classification.mode != "allowed" else "reused",
        reason=classification.reason,
        path=str(ctx.layout.reports_dir / "init_readiness.json"),
        metadata=payload,
    )


def _load_reusable_init_coherence_report(
    ctx: InitLongServiceContext,
    *,
    stage: str,
    artifact: str,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    report_path = _init_coherence_report_path(ctx.layout, stage)
    report = _load_optional_json(ctx, report_path)
    if not report:
        return None

    normalized = normalize_coherence_report(report, artifact=artifact)
    if str(normalized.get("schema_version") or "").strip() != "audit_v2":
        _record_init_resume_decision(
            ctx,
            stage=stage,
            artifact=artifact,
            action="cache_rejected",
            reason="stale_pre_audit_v2_report",
            path=str(report_path),
            expected_hashes=init_coherence_artifact_hashes(artifacts),
        )
        return None
    report_stage = str(normalized.get("stage") or stage).strip()
    report_artifact = normalize_artifact_key(normalized.get("artifact") or artifact)
    if report_stage != stage or report_artifact != artifact:
        return None
    if normalized.get("focus_chapters"):
        return None

    expected_hashes = init_coherence_artifact_hashes(artifacts)
    recorded_hashes = normalized.get("artifact_hashes")
    ledger_stage = _load_init_coherence_ledger_stage(ctx, stage=stage)
    if not ledger_stage:
        _record_init_resume_decision(
            ctx,
            stage=stage,
            artifact=artifact,
            action="cache_rejected",
            reason="missing_claim_ledger_stage",
            path=str(report_path),
            expected_hashes=expected_hashes,
        )
        return None
    if ledger_stage.get("focus_chapters"):
        _record_init_resume_decision(
            ctx,
            stage=stage,
            artifact=artifact,
            action="cache_rejected",
            reason="focused_claim_ledger_stage",
            path=str(report_path),
            expected_hashes=expected_hashes,
        )
        return None
    expected_runtime_fingerprint = semantic_compiler_runtime_fingerprint(ctx)
    recorded_runtime_fingerprint = str(
        ledger_stage.get("compiler_runtime_fingerprint") or ""
    )
    report_runtime_fingerprint = str(
        normalized.get("compiler_runtime_fingerprint") or ""
    )
    if (
        not recorded_runtime_fingerprint
        or recorded_runtime_fingerprint != expected_runtime_fingerprint
        or report_runtime_fingerprint != recorded_runtime_fingerprint
    ):
        _record_init_resume_decision(
            ctx,
            stage=stage,
            artifact=artifact,
            action="cache_rejected",
            reason="semantic_compiler_fingerprint_mismatch",
            path=str(report_path),
            expected_hashes=expected_hashes,
        )
        return None
    claim_coverage = ledger_stage.get("claim_coverage")
    if not isinstance(claim_coverage, dict) or str(claim_coverage.get("status") or "") != (
        "complete"
    ):
        _record_init_resume_decision(
            ctx,
            stage=stage,
            artifact=artifact,
            action="cache_rejected",
            reason="incomplete_claim_coverage",
            path=str(report_path),
            expected_hashes=expected_hashes,
        )
        return None
    ledger_hashes = ledger_stage.get("artifact_hashes")
    if not _init_coherence_hashes_cover(ledger_hashes, expected_hashes):
        _record_init_resume_decision(
            ctx,
            stage=stage,
            artifact=artifact,
            action="cache_rejected",
            reason="claim_ledger_hash_mismatch",
            path=str(report_path),
            expected_hashes=expected_hashes,
            recorded_hashes=ledger_hashes if isinstance(ledger_hashes, dict) else {},
        )
        return None
    if isinstance(recorded_hashes, dict) and not _init_coherence_hashes_cover(
        recorded_hashes,
        expected_hashes,
    ):
        _record_init_resume_decision(
            ctx,
            stage=stage,
            artifact=artifact,
            action="cache_rejected",
            reason="report_hash_mismatch",
            path=str(report_path),
            expected_hashes=expected_hashes,
            recorded_hashes=recorded_hashes,
        )
        return None
    if not _init_coherence_hashes_cover(recorded_hashes, expected_hashes):
        recorded_hashes = ledger_hashes
    if not _init_coherence_hashes_cover(recorded_hashes, expected_hashes):
        return None

    normalized["artifact_hashes"] = expected_hashes
    _record_init_resume_decision(
        ctx,
        stage=stage,
        artifact=artifact,
        action="reused",
        reason="report_and_claim_ledger_hash_match",
        path=str(report_path),
        expected_hashes=expected_hashes,
        recorded_hashes=recorded_hashes if isinstance(recorded_hashes, dict) else {},
    )
    status = (
        STATUS_BLOCKED
        if _init_coherence_blocks(ctx, normalized)
        else (
            STATUS_NEEDS_REPAIR
            if str(normalized.get("verdict") or "").strip().lower() == "needs_repair"
            else STATUS_SUCCEEDED
        )
    )
    manifest_for_context(ctx).record(
        artifact=f"init_coherence:{stage}",
        workflow="init_long",
        step=stage,
        status=status,
        input_hashes=expected_hashes,
        output_hashes={"report": hash_payload(normalized)},
        paths={"report": str(report_path)},
        metadata=_init_coherence_event_payload(normalized),
        reusable_failure=status in {STATUS_BLOCKED, STATUS_NEEDS_REPAIR},
    )
    ctx.on_step(
        "init_coherence_report_resumed",
        {
            **_init_coherence_event_payload(normalized),
            "stage": stage,
            "artifact": artifact,
            "source": f"reports/{stage}.json",
        },
    )
    _log.info(
        "init_coherence_report_resumed | project=%s | stage=%s | artifact=%s",
        ctx.layout.root.name,
        stage,
        artifact,
    )
    return normalized


def _selected_editorial_element_ids(element_selection: Any) -> set[str]:
    selected_element_ids: set[str] = set()
    for element in [
        *list(getattr(element_selection, "required_elements", []) or []),
        *list(getattr(element_selection, "extension_elements", []) or []),
    ]:
        element_id = str(getattr(element, "element_id", "") or "").strip()
        if element_id:
            selected_element_ids.add(element_id)
    return selected_element_ids


def _editorial_contract_input_hashes(
    *,
    title: str,
    total_chapters: int,
    story_bible: StoryBible,
    character_bible: CharacterBible,
    style_profile: Any,
    blueprint: NarrativeBlueprint,
    blueprint_elements: BlueprintElementSelection,
    creative_director_packet: Any,
) -> dict[str, str]:
    return {
        "voice_integrity_version": hash_payload("2026-07-13.all-canonical-voices-v1"),
        "title": hash_payload(str(title or "")),
        "total_chapters": hash_payload(int(total_chapters or 0)),
        "story_bible": hash_payload(story_bible),
        "character_bible": hash_payload(character_bible),
        "style_profile": hash_payload(style_profile or {}),
        "blueprint": hash_payload(blueprint),
        "blueprint_elements": hash_payload(blueprint_elements),
        "creative_director_packet": hash_payload(creative_director_packet),
    }


def _editorial_readiness_payload(
    editorial_contract: EditorialContract,
    editorial_readiness: Any,
    *,
    input_hashes: dict[str, str],
) -> dict[str, Any]:
    return {
        "status": editorial_readiness.metrics.get("status", "passed"),
        "summary": editorial_readiness.summary,
        "character_voice_count": len(editorial_contract.character_voices),
        "climax_marker_count": len(editorial_contract.climax_markers),
        "theme_policy_count": len(editorial_contract.theme_policies),
        "symbol_policy_count": len(editorial_contract.symbol_policies),
        "scene_resistance_rule_count": len(editorial_contract.scene_resistance_rules),
        "expression_channel_profile_count": len(editorial_contract.expression_channel_profiles),
        "revelation_step_count": len(editorial_contract.revelation_ladder),
        "element_directive_count": len(editorial_contract.editorial_element_directives),
        "findings": [item.model_dump(mode="json") for item in editorial_readiness.findings],
        "metrics": editorial_readiness.metrics,
        "input_hashes": input_hashes,
    }


def _load_reusable_editorial_contract(
    ctx: InitLongServiceContext,
    *,
    total_chapters: int,
    selected_element_ids: set[str],
    input_hashes: dict[str, str],
) -> EditorialContract | None:
    if not ctx.storage.exists(ctx.layout.editorial_contract_path):
        return None
    try:
        contract = EditorialContract.model_validate(
            ctx.storage.load_json(ctx.layout.editorial_contract_path)
        )
        readiness_path = ctx.layout.reports_dir / "init_editorial_readiness.json"
        readiness_payload = _load_optional_json(ctx, readiness_path) or {}
        recorded_hashes = readiness_payload.get("input_hashes")
        if isinstance(recorded_hashes, dict) and recorded_hashes != input_hashes:
            return None
        readiness = validate_editorial_contract(
            contract,
            total_chapters=total_chapters,
            selected_element_ids=selected_element_ids,
        )
        if any(item.severity == "critical" for item in readiness.findings):
            return None
        ctx.storage.save_json(
            readiness_path,
            _editorial_readiness_payload(contract, readiness, input_hashes=input_hashes),
        )
    except Exception as exc:
        _log.debug("editorial_contract_resume_rejected | error=%s", exc)
        return None

    ctx.on_step(
        "derive_editorial_contract_resumed",
        {
            "voices": len(contract.character_voices),
            "climax_markers": len(contract.climax_markers),
            "symbols": len(contract.symbol_policies),
            "path": str(ctx.layout.editorial_contract_path),
            "source": "cache",
        },
    )
    manifest_for_context(ctx).record_success(
        artifact="editorial_contract",
        workflow="init_long",
        step="derive_editorial_contract",
        input_hashes=input_hashes,
        output_hashes={"editorial_contract": hash_payload(contract)},
        paths={"editorial_contract": str(ctx.layout.editorial_contract_path)},
        metadata={
            "voices": len(contract.character_voices),
            "climax_markers": len(contract.climax_markers),
            "symbols": len(contract.symbol_policies),
            "source": "cache",
        },
    )
    return contract


def _cached_outline_matches_reveal_guard(
    ctx: InitLongServiceContext,
    *,
    editorial_contract: Any | None,
    outline: StoryOutline,
) -> bool:
    expected_hashes = outline_reveal_guard_input_hashes(editorial_contract)
    if not expected_hashes:
        return True
    record = manifest_for_context(ctx).matching_record(
        "outline",
        input_hashes=expected_hashes,
        output_hashes={"outline": hash_payload(outline)},
        statuses={STATUS_SUCCEEDED},
        allow_reusable_failure=False,
    )
    return record is not None


def _repair_outline_reveal_guard_manifest_from_downstream(
    ctx: InitLongServiceContext,
    *,
    editorial_contract: Any | None,
    outline: StoryOutline,
) -> bool:
    """Trust a complete cached outline when later contracts already validate it."""

    expected_hashes = outline_reveal_guard_input_hashes(editorial_contract)
    if not expected_hashes:
        return False
    if not _init_chapter_contracts_resume_ready(
        ctx,
        outline=outline,
        strict_noise=bool(getattr(ctx.settings, "init_chapter_contract_resume_strict_noise", True)),
    ):
        return False
    manifest_for_context(ctx).record_success(
        artifact="outline",
        workflow="init_long",
        step="plan_outline",
        input_hashes=expected_hashes,
        output_hashes={"outline": hash_payload(outline)},
        paths={"outline": str(ctx.layout.outline_path)},
        metadata={
            "reveal_guard_version": OUTLINE_REVEAL_GUARD_VERSION,
            "source": "validated_downstream_resume",
            "reason": "reveal_guard_manifest_mismatch",
        },
    )
    return True


_INIT_REPAIR_TARGET_CHAPTER_RE = re.compile(r"^[^:]+:(?P<chapter>\d+)(?::|$)")

_INIT_REPAIR_POINTER_CHAPTER_RE = re.compile(
    r"^/(?:chapters|chapter_contracts)/(?P<index>\d+)(?:/|$)"
)


def _init_coherence_expand_focus_chapters(
    ctx: InitLongServiceContext,
    *,
    chapters: set[int],
) -> list[int] | None:
    chapter_numbers = sorted(chapter for chapter in chapters if chapter >= 1)
    if not chapter_numbers:
        return None
    window = int(getattr(ctx.settings, "init_coherence_recheck_affected_window", 2) or 0)
    expanded: set[int] = set()
    for chapter in chapter_numbers:
        expanded.update(range(max(1, chapter - window), chapter + window + 1))
    return sorted(expanded)


def _init_coherence_focus_chapters(
    ctx: InitLongServiceContext,
    *,
    report: dict[str, Any],
    artifact: str,
) -> list[int] | None:
    scopes = collect_repair_scopes(report, default_artifact=artifact)
    chapters = {chapter for scope in scopes for chapter in scope.chapters if chapter >= 1}
    return _init_coherence_expand_focus_chapters(ctx, chapters=chapters)


def _init_repair_patch_chapters(repair: dict[str, Any]) -> set[int]:
    chapters: set[int] = set()
    patches = repair.get("patches")
    if not isinstance(patches, list):
        return chapters
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        for key in ("chapter_number", "chapter"):
            number = _positive_int(patch.get(key))
            if number is not None:
                chapters.add(number)
        target_id = str(patch.get("target_id") or "")
        match = _INIT_REPAIR_TARGET_CHAPTER_RE.match(target_id)
        if match:
            number = _positive_int(match.group("chapter"))
            if number is not None:
                chapters.add(number)
        pointer = str(patch.get("path") or patch.get("source_path") or "")
        match = _INIT_REPAIR_POINTER_CHAPTER_RE.match(pointer)
        if match:
            index = _positive_int(match.group("index"))
            if index is not None:
                chapters.add(index + 1)
    return chapters


def _init_coherence_latest_applied_repair(
    repairs: list[dict[str, Any]],
    *,
    artifact: str,
) -> dict[str, Any] | None:
    normalized_artifact = normalize_artifact_key(artifact)
    for repair in reversed(repairs):
        if not isinstance(repair, dict):
            continue
        if normalize_artifact_key(repair.get("artifact")) != normalized_artifact:
            continue
        if str(repair.get("status") or "") == "applied":
            return repair
        if _positive_int(repair.get("patch_count")):
            return repair
    return None


def _init_coherence_focus_chapters_after_repair(
    ctx: InitLongServiceContext,
    *,
    report: dict[str, Any],
    artifact: str,
    repairs: list[dict[str, Any]],
) -> list[int] | None:
    repair = _init_coherence_latest_applied_repair(repairs, artifact=artifact)
    if repair is not None:
        patch_chapters = _init_repair_patch_chapters(repair)
        focus = _init_coherence_expand_focus_chapters(ctx, chapters=patch_chapters)
        if focus:
            on_step = getattr(ctx, "on_step", None)
            if callable(on_step):
                on_step(
                    "init_coherence_focus_chapters",
                    {
                        "artifact": artifact,
                        "source": "applied_repair_patches",
                        "round": repair.get("round"),
                        "patch_chapters": sorted(patch_chapters),
                        "focus_chapters": focus,
                    },
                )
            return focus
    return _init_coherence_focus_chapters(ctx, report=report, artifact=artifact)


def _init_coherence_max_repair_rounds(settings: Any) -> int:
    try:
        value = int(getattr(settings, "init_coherence_max_repair_rounds", 2) or 0)
    except (TypeError, ValueError):
        value = 2
    return max(0, min(value, 3))


def _init_coherence_repair_source_issue_ids(
    repairs: list[dict[str, Any]],
    *,
    artifact: str,
) -> set[str]:
    issue_ids: set[str] = set()
    for repair in repairs:
        if not isinstance(repair, dict):
            continue
        if normalize_artifact_key(repair.get("artifact")) != artifact:
            continue
        issue_ids.update(
            str(item) for item in _init_coherence_list(repair.get("source_issue_ids")) if item
        )
        for patch in repair.get("patches") or []:
            if isinstance(patch, dict):
                issue_ids.update(
                    str(item) for item in _init_coherence_list(patch.get("issue_ids")) if item
                )
    return issue_ids


def _init_coherence_allows_llm_followup_repair(
    settings: Any,
    *,
    artifact: str,
    report: dict[str, Any],
    repairs: list[dict[str, Any]],
    repair_round: int,
    followup_used: bool,
) -> bool:
    if followup_used or repair_round <= 0:
        return False
    current_issue_ids = _init_coherence_blocking_issue_ids(settings, report)
    if not current_issue_ids:
        return False
    repaired_issue_ids = _init_coherence_repair_source_issue_ids(repairs, artifact=artifact)
    if not repaired_issue_ids:
        return False
    return current_issue_ids.isdisjoint(repaired_issue_ids)


def _init_coherence_blocking_issue_fingerprints(
    settings: Any,
    report: dict[str, Any],
) -> set[str]:
    fingerprints: set[str] = set()
    for issue in blocking_issues(
        report,
        min_severity=_init_coherence_min_severity(settings),
    ):
        if not isinstance(issue, dict):
            continue
        raw_scopes = issue.get("repair_scope")
        scopes = raw_scopes if isinstance(raw_scopes, list) else []
        normalized_scopes: list[dict[str, Any]] = []
        for scope in scopes:
            if not isinstance(scope, dict):
                continue
            chapters: set[int] = set()
            for chapter in _init_coherence_list(scope.get("chapters")):
                number = _safe_init_int(chapter)
                if number >= 1:
                    chapters.add(number)
            normalized_scopes.append(
                {
                    "artifact": normalize_artifact_key(scope.get("artifact")),
                    "chapters": sorted(chapters),
                    "fields": sorted(
                        str(field) for field in _init_coherence_list(scope.get("fields"))
                    ),
                }
            )
        description = " ".join(str(issue.get("description") or issue.get("summary") or "").split())[
            :300
        ]
        raw = json.dumps(
            {
                "severity": str(issue.get("severity") or "medium").strip().lower(),
                "description": description,
                "repair_scope": normalized_scopes,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        fingerprints.add(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16])
    return fingerprints


def _init_coherence_max_stagnant_repair_rounds(settings: Any) -> int:
    try:
        value = int(getattr(settings, "init_coherence_max_stagnant_repair_rounds", 1) or 0)
    except (TypeError, ValueError):
        value = 1
    return max(0, min(value, 3))


def _init_coherence_repair_stop_decision(
    settings: Any,
    report: dict[str, Any],
    *,
    previous_issue_keys: set[str] | None,
    stagnant_rounds: int,
) -> tuple[bool, str, set[str], int]:
    current_issue_keys = _init_coherence_blocking_issue_fingerprints(settings, report)
    explicit_reason = str(
        report.get("stopped_reason") or report.get("repair_loop_stop_reason") or ""
    ).strip()
    if explicit_reason:
        return True, explicit_reason, current_issue_keys, stagnant_rounds
    if not bool(getattr(settings, "init_coherence_stop_on_no_progress", True)):
        return False, "", current_issue_keys, stagnant_rounds
    max_stagnant = _init_coherence_max_stagnant_repair_rounds(settings)
    if max_stagnant <= 0 or not previous_issue_keys or not current_issue_keys:
        return False, "", current_issue_keys, 0 if not current_issue_keys else stagnant_rounds
    overlap = previous_issue_keys & current_issue_keys
    no_progress = bool(overlap) and len(current_issue_keys) >= len(previous_issue_keys)
    next_stagnant_rounds = stagnant_rounds + 1 if no_progress else 0
    if next_stagnant_rounds >= max_stagnant:
        return True, "no_blocking_issue_reduction", current_issue_keys, next_stagnant_rounds
    return False, "", current_issue_keys, next_stagnant_rounds


def _record_init_coherence_repair_loop_stop(
    ctx: InitLongServiceContext,
    *,
    stage: str,
    artifact: str,
    report: dict[str, Any],
    reason: str,
    previous_issue_keys: set[str] | None,
    current_issue_keys: set[str],
    stagnant_rounds: int,
) -> None:
    report["repair_loop_stop_reason"] = reason
    report["repair_loop_stagnant_rounds"] = stagnant_rounds
    report["repair_loop_current_blocking_issue_count"] = len(current_issue_keys)
    report["repair_loop_previous_blocking_issue_count"] = len(previous_issue_keys or set())
    ctx.on_step(
        "init_coherence_repair_loop_stopped",
        {
            "stage": stage,
            "artifact": artifact,
            "reason": reason,
            "current_blocking_issues": len(current_issue_keys),
            "previous_blocking_issues": len(previous_issue_keys or set()),
            "stagnant_rounds": stagnant_rounds,
            "summary": report.get("summary") or report.get("verdict"),
        },
    )


def _local_story_fallbacks_enabled(settings: Any) -> bool:
    return not bool(getattr(settings, "init_disable_local_story_fallbacks", True))


def _init_coherence_blocks(ctx: InitLongServiceContext, report: dict[str, Any]) -> bool:
    return coherence_blocks(
        report,
        min_severity=_init_coherence_min_severity(ctx.settings),
    )


def _init_coherence_claim_ledger(ctx: InitLongServiceContext) -> dict[str, Any] | None:
    """Load the init coherence claim ledger (if it exists) for auto-repair."""
    try:
        layout = getattr(ctx, "layout", None)
        storage = getattr(ctx, "storage", None)
        if layout is None or storage is None:
            return None
        path = layout.memory_dir / CLAIM_LEDGER_JSON
        result = storage.load_json(path)
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        _log.warning(
            "init_coherence_claim_ledger_invalid | path=%s | type=%s",
            path,
            type(result).__name__,
        )
        return {}
    except Exception as exc:
        _log.warning("init_coherence_claim_ledger_load_failed | error=%s", exc)
        return None


def _run_init_coherence_auto_repair(
    ctx: InitLongServiceContext,
    *,
    artifact: str,
    report: dict[str, Any],
    payload: dict[str, Any],
    repairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply deterministic LLM-free fixes to common v2 gate issue types.

    Returns the (possibly downgraded) report. If the repair fully
    resolves the blocking issues, the caller can simply re-check
    ``_init_coherence_blocks`` and break out of the repair loop. The
    actual mutation of ``payload`` is also reflected in the report's
    ``auto_repair_applied`` field so the operator can see what changed.
    """
    settings = ctx.settings
    enabled = bool(
        getattr(
            settings,
            "init_coherence_deterministic_repair",
            getattr(settings, "init_coherence_auto_repair_deterministic", True),
        )
    )
    if not enabled:
        return report
    if not isinstance(report, dict):
        return report
    if not isinstance(payload, dict):
        return report

    verdict = str(report.get("verdict") or "").strip().lower()
    if verdict not in {"needs_repair", "reject", "ambiguous"}:
        return report

    claim_ledger = _init_coherence_claim_ledger(ctx)
    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact=artifact,
        claim_ledger=claim_ledger,
    )
    if not result.has_fixes:
        return report

    if artifact == CHAPTER_CONTRACTS_ARTIFACT:
        _assert_auto_repaired_chapter_contracts_valid(result.payload)

    if not verify_auto_repair_candidate(
        result,
        report=report,
        artifact=artifact,
        claim_ledger=claim_ledger,
    ):
        ctx.on_step(
            "coherence_auto_repair_rejected",
            {
                "artifact": artifact,
                "reason": "original deterministic postconditions did not close",
                "verification": (
                    result.verification.model_dump(mode="json")
                    if result.verification is not None
                    else None
                ),
            },
        )
        return report

    # Only a verified, unapproved initialization intermediate may replace the
    # working payload. Configured projects are stopped by the versioned
    # foundation guard before this initialization path can run.
    payload.clear()
    payload.update(copy.deepcopy(result.payload))

    downgraded = build_downgraded_report(
        report,
        resolved_issue_ids=result.resolved_issue_ids,
        min_severity=_init_coherence_min_severity(ctx.settings),
    )
    downgraded["auto_repair_applied"] = result.to_dict()
    if repairs is not None:
        _record_init_coherence_deterministic_repair(
            ctx,
            artifact=artifact,
            report_before=report,
            report_after=downgraded,
            result=result,
            repairs=repairs,
        )
    ctx.on_step(
        "coherence_auto_repair_applied",
        {
            "artifact": artifact,
            "fix_count": len(result.fixes),
            "resolved_issue_ids": list(result.resolved_issue_ids),
            "unresolved_issue_ids": list(result.unresolved_issue_ids),
            "new_verdict": downgraded.get("verdict"),
            "candidate_hash": result.candidate.candidate_hash if result.candidate else "",
            "verification_passed": bool(result.verification and result.verification.passed),
        },
    )
    return downgraded


def _assert_auto_repaired_chapter_contracts_valid(payload: dict[str, Any]) -> None:
    items = payload.get("chapter_contracts")
    if not isinstance(items, list):
        raise InitCoherenceError("确定性修复后章节契约 schema 非法：chapter_contracts 必须是列表。")
    for item in items:
        if not isinstance(item, dict):
            raise InitCoherenceError("确定性修复后章节契约 schema 非法：存在非对象章节契约。")
        try:
            ChapterContract.model_validate(item)
        except ValidationError as exc:
            chapter_number = item.get("chapter_number")
            raise InitCoherenceError(
                f"确定性修复后章节契约 schema 非法：chapter={chapter_number} error={exc}"
            ) from exc


def _record_init_coherence_deterministic_repair(
    ctx: InitLongServiceContext,
    *,
    artifact: str,
    report_before: dict[str, Any],
    report_after: dict[str, Any],
    result: AutoRepairResult,
    repairs: list[dict[str, Any]],
) -> None:
    min_severity = _init_coherence_min_severity(ctx.settings)
    source_blocking_issues = blocking_issues(report_before, min_severity=min_severity)
    source_issue_ids = [
        init_coherence_issue_id(issue)
        for issue in source_blocking_issues
        if isinstance(issue, dict)
    ]
    patches = [
        {
            "op": "deterministic_fix",
            "path": "",
            "issue_ids": [fix.issue_id] if fix.issue_id else [],
            "issue_type": fix.issue_type,
            "chapters": list(fix.chapters_affected),
            "fields": list(fix.fields_affected),
            "rationale": fix.description,
            "removed_entry_keys": list(fix.removed_entry_keys),
            "moved_entry_keys": list(fix.moved_entry_keys),
        }
        for fix in result.fixes
    ]
    record = {
        "artifact": artifact,
        "round": 0,
        "status": "applied",
        "repair_type": "deterministic_coherence",
        "source_issue_ids": sorted(set(source_issue_ids)),
        "resolved_issue_ids": list(result.resolved_issue_ids),
        "unresolved_issue_ids": list(result.unresolved_issue_ids),
        "source_blocking_issue_count": len(source_blocking_issues),
        "remaining_blocking_issue_count": len(
            blocking_issues(report_after, min_severity=min_severity)
        ),
        "patch_count": len(patches),
        "skipped_patch_count": len(result.unresolved_issue_ids),
        "patches": patches,
        "skipped_patches": [
            {"issue_ids": [issue_id], "reason": "deterministic_handler_unresolved"}
            for issue_id in result.unresolved_issue_ids
            if issue_id
        ],
        "audit_issues": [issue.model_dump(mode="json") for issue in result.audit_issues],
        "candidate": (
            result.candidate.model_dump(mode="json") if result.candidate is not None else None
        ),
        "verification": (
            result.verification.model_dump(mode="json")
            if result.verification is not None
            else None
        ),
        "summary": f"确定性修复 {len(result.fixes)} 个初始化一致性问题。",
    }
    repairs.append(record)
    ctx.storage.save_json(
        ctx.layout.reports_dir / "init_artifact_repair.json",
        {"repairs": repairs},
    )
    _record_init_repair_manifest_round(ctx, record)


def _format_init_coherence_error(
    *,
    stage: str,
    report: dict[str, Any],
    repairs: list[dict[str, Any]],
) -> str:
    """Build a self-explanatory ``InitCoherenceError`` message.

    Surfaces the v2 gate summary, the residual issue types with their
    evidence, and the repair history so the operator can resolve the
    problem manually without re-running the entire 5-hour init.
    """
    summary = str(report.get("summary") or report.get("verdict") or "").strip()
    issue_block = summarize_unresolved_issues(report)
    repair_count = sum(1 for repair in repairs if isinstance(repair, dict))
    parts: list[str] = []
    if stage == "contract_coherence":
        parts.append("章节契约一致性裁判未通过")
    else:
        parts.append(f"{stage} 一致性裁判未通过")
    if summary:
        parts.append(f"：{summary}")
    if issue_block:
        parts.append("\n未修复问题：\n" + issue_block)
    if repair_count:
        parts.append(f"\n已尝试修复 {repair_count} 轮（见 init_coherence_repair_*.json）。")
    parts.append(
        "\n修复建议："
        "\n1) 直接编辑 data/<project>/plans/chapter_contracts.json 修正上述条目；"
        "\n2) 在 .env 中配置其他 provider 作为 NOVEL_FORGE_TASK_FALLBACK_ROUTING；"
        "\n3) 等待 provider 配额恢复后重跑 init-long（无需从头开始，已有断点续跑支持）。"
    )
    return "".join(parts)


def _init_coherence_event_payload(report: dict[str, Any]) -> dict[str, Any]:
    min_severity = "high"
    return {
        "verdict": report.get("verdict", "ambiguous"),
        "summary": report.get("summary", ""),
        "blocked": bool(report.get("blocked", False)),
        "issue_count": len(report.get("issues", []) or []),
        "high_or_critical": len(blocking_issues(report, min_severity=min_severity)),
    }


def _model_call_count_by_task(ctx: InitLongServiceContext) -> dict[str, int]:
    counts: dict[str, int] = {}
    for step in getattr(getattr(ctx, "trace", None), "steps", []) or []:
        for call in getattr(step, "model_calls", []) or []:
            task = str(getattr(call, "task", "") or "").strip()
            if task:
                counts[task] = counts.get(task, 0) + 1
    return dict(sorted(counts.items()))


def _init_efficiency_metadata(ctx: InitLongServiceContext) -> dict[str, Any]:
    metrics = getattr(ctx, "_init_efficiency_metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    cache_stats = claim_cache_stats(ctx)
    model_counts = _model_call_count_by_task(ctx)
    trace_summary: dict[str, Any] = {}
    summary_fn = getattr(getattr(ctx, "trace", None), "summary", None)
    if callable(summary_fn):
        try:
            raw_summary = summary_fn()
            if isinstance(raw_summary, dict):
                trace_summary = raw_summary
        except Exception:
            trace_summary = {}
    return {
        "model_call_count_by_task": model_counts,
        "stage_durations": trace_summary.get("stages", {}),
        "effective_batch_sizes": metrics.get("effective_batch_sizes", {}),
        "claim_cache_hits": cache_stats["claim_cache_hits"],
        "claim_cache_misses": cache_stats["claim_cache_misses"],
        "claim_ledger_hits": int(metrics.get("claim_ledger_hits", 0) or 0),
        "claim_ledger_misses": int(metrics.get("claim_ledger_misses", 0) or 0),
        "claim_chunks": int(metrics.get("claim_chunks", 0) or 0),
        "claim_chunks_by_stage": metrics.get("claim_chunks_by_stage", {}),
        "claim_chunks_by_artifact": metrics.get("claim_chunks_by_artifact", {}),
        "profile_mode": str(metrics.get("profile_mode") or "unknown"),
        "profile_call_count": int(
            metrics.get("profile_call_count")
            or model_counts.get(TaskType.REFINE_INIT_COHERENCE_PROFILE.value, 0)
        ),
        "entity_registry_mode": str(metrics.get("entity_registry_mode") or "unknown"),
    }


def _seed_init_coherence_profile(spec: StorySpec) -> dict[str, Any]:
    """Build the deterministic seed consumed by the single profile task."""
    return {
        "genre_tags": [spec.genre] if getattr(spec, "genre", "") else [],
        "narrative_modes": [],
        "project_ontology": {},
        "conflict_lens": [],
        "extraction_guidance": [],
        "summary": "single-task seed profile; refined after blueprint validation.",
    }


def _save_init_readiness(
    ctx: InitLongServiceContext,
    *,
    reports: dict[str, dict[str, Any] | None],
    repairs: list[dict[str, Any]],
) -> dict[str, Any]:
    readiness = build_init_readiness_report(
        reports=reports,
        repairs=repairs,
        min_severity=_init_coherence_min_severity(ctx.settings),
        required=bool(getattr(ctx.settings, "init_readiness_required", True)),
    )
    readiness["research"] = _init_research_status_metadata(ctx)
    readiness["upstream_health"] = _init_upstream_health_metadata(ctx)
    readiness["efficiency"] = _init_efficiency_metadata(ctx)
    ctx.storage.save_json(
        ctx.layout.reports_dir / "init_efficiency_metrics.json",
        readiness["efficiency"],
    )
    resume_decisions_path = _init_resume_decisions_path(ctx)
    if ctx.storage.exists(resume_decisions_path):
        readiness["resume_decisions_path"] = "reports/init_resume_decisions.json"
    ctx.storage.save_json(ctx.layout.reports_dir / "init_readiness.json", readiness)
    ctx.on_step("init_readiness", readiness)
    return readiness


def _init_research_status_metadata(ctx: InitLongServiceContext) -> dict[str, Any]:
    paths = {
        "web": ctx.layout.reports_dir / "init_web_research.json",
        "dossier": ctx.layout.reports_dir / "init_research_dossier.json",
        "outline_grounding": ctx.layout.reports_dir / "outline_research_grounding.json",
    }
    result: dict[str, Any] = {}
    for key, path in paths.items():
        if not ctx.storage.exists(path):
            result[key] = {"status": "missing", "path": str(path)}
            continue
        try:
            payload = ctx.storage.load_json(path)
        except Exception as exc:  # noqa: BLE001 - readiness should remain best effort
            result[key] = {"status": "unreadable", "error": f"{type(exc).__name__}: {exc}"}
            continue
        result[key] = {
            "status": str(payload.get("status") or "unknown")
            if isinstance(payload, dict)
            else "unknown",
            "enabled": bool(payload.get("enabled", False)) if isinstance(payload, dict) else False,
            "warnings": list(payload.get("warnings", []) or [])[:5]
            if isinstance(payload, dict)
            else [],
            "path": str(path),
        }
    return result


def _init_upstream_health_metadata(ctx: InitLongServiceContext) -> dict[str, Any]:
    path = ctx.layout.reports_dir / "init_upstream_health.json"
    if not ctx.storage.exists(path):
        return {"status": "missing", "blocked": False, "path": str(path)}
    try:
        payload = ctx.storage.load_json(path)
    except Exception as exc:  # noqa: BLE001 - readiness should remain best effort
        return {
            "status": "unreadable",
            "blocked": False,
            "error": f"{type(exc).__name__}: {exc}",
            "path": str(path),
        }
    if not isinstance(payload, dict):
        return {"status": "unknown", "blocked": False, "path": str(path)}
    return {
        "status": str(payload.get("status") or "unknown"),
        "blocked": bool(payload.get("blocked", False)),
        "warnings": list(payload.get("warnings", []) or [])[:8],
        "checks": {
            key: {
                "status": item.get("status"),
                "blocked": bool(item.get("blocked", False)),
                "issues": len(item.get("issues", []) or []),
            }
            for key, item in (payload.get("checks", {}) or {}).items()
            if isinstance(item, dict)
        },
        "path": str(path),
    }


def _with_init_entity_catalog(
    profile: dict[str, Any],
    *,
    entity_graph: Any,
    character_bible: Any,
    chapter_contracts: Any | None = None,
) -> dict[str, Any]:
    catalog = build_init_entity_catalog(
        entity_graph,
        character_bible,
        chapter_contracts=chapter_contracts,
    )
    if not catalog.get("allowed_entities"):
        return profile
    enriched = dict(profile)
    enriched["entity_catalog"] = catalog
    return enriched


def _emit_source_artifact_step(
    ctx: InitLongServiceContext,
    readiness_artifact: Any,
) -> None:
    ctx.on_step(
        "init_source_artifacts",
        {
            "status": readiness_artifact.quality_status,
            "source_artifacts": list(readiness_artifact.source_artifact_ids),
            "blocking_issues": [
                issue.model_dump(mode="json") for issue in readiness_artifact.blocking_issues[:12]
            ],
            "path": str(ctx.layout.source_artifacts_dir),
        },
    )
