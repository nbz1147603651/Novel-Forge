"""Narrative-state and chapter-contract init orchestration."""

from __future__ import annotations

from novel_forge.pipeline.long.services.init.init_chapter_contracts import (
    _assert_chapter_contracts_ready_to_persist,
    _batched_generate_chapter_contracts,
    _generate_llm_narrative_contract,
    _load_reusable_chapter_contracts,
    _persist_chapter_contract_runtime_artifacts,
    _source_artifact_resume_rebuild_chapters,
    _validate_and_persist_repaired_chapter_contracts,
    merge_reusable_and_rebuilt_chapter_contracts,
)
from novel_forge.pipeline.long.services.init.init_common import (
    BLUEPRINT_ARTIFACT,
    CHAPTER_CONTRACTS_ARTIFACT,
    OUTLINE_ARTIFACT,
    Any,
    CharacterBible,
    EntityRegistry,
    InitArtifact,
    InitCoherenceError,
    InitLongServiceContext,
    InitRepairContext,
    InitRepairOrchestrator,
    NarrativeBlueprint,
    NarrativeStateStore,
    StoryBible,
    StoryOutline,
    StorySpec,
    _build_and_persist_narrative_contract,
    _local_cognitive_backfill,
    _log,
    blocking_issues,
    build_adjudication_narrative_contract,
    build_plot_milestone_index,
    cast,
    copy,
    get_init_repair_policy,
    hash_payload,
    init_coherence_artifact_hashes,
    init_coherence_issue_id,
    load_reusable_init_coherence_profile,
    run_init_claim_contract_coverage_audit,
    run_init_coherence_v2_gate,
)
from novel_forge.pipeline.long.services.init.init_repair_manifest import (
    _persist_init_repair_outcome,
)
from novel_forge.pipeline.long.services.init.init_repair_targets import (
    _init_coherence_repair_round_start,
    _repair_init_artifact_payload,
)
from novel_forge.pipeline.long.services.init.init_source_resume import (
    _format_init_coherence_error,
    _init_coherence_allows_llm_followup_repair,
    _init_coherence_blocks,
    _init_coherence_focus_chapters_after_repair,
    _init_coherence_max_repair_rounds,
    _init_coherence_repair_stop_decision,
    _load_reusable_init_coherence_report,
    _record_init_coherence_repair_loop_stop,
    _record_init_resume_decision,
    _run_init_coherence_auto_repair,
    _save_init_readiness,
)
from novel_forge.pipeline.long.services.init.init_story_bible import (
    _llm_narrative_contract_input_hashes,
    _load_reusable_llm_narrative_contract,
    _persist_llm_narrative_contract,
)
from novel_forge.pipeline.long.services.init.init_value_helpers import (
    _init_coherence_min_severity,
)

CLAIM_COVERAGE_CHECKPOINT = "claim_contract_coverage_checkpoint.json"


def _claim_coverage_checkpoint_path(layout: Any) -> Any:
    return layout.states_dir / "init_v2" / CLAIM_COVERAGE_CHECKPOINT


def _claim_coverage_artifacts(
    *,
    blueprint: NarrativeBlueprint,
    outline: StoryOutline,
    llm_contract: dict[str, Any],
    chapter_contracts: dict[str, Any],
    coverage_outline: StoryOutline | None = None,
) -> dict[str, dict[str, Any]]:
    """Build coverage inputs without replacing the source commitment frontier.

    Chapter-contract generation receives a bounded outline whose ``total_chapters``
    equals the current hard frontier.  That shape is required to validate the
    partial contract payload, but it must not redefine which Claim obligations are
    current.  Coverage therefore keeps the original progressive outline when one
    is available.
    """
    audit_outline = coverage_outline or outline
    return {
        BLUEPRINT_ARTIFACT: blueprint.model_dump(mode="json"),
        OUTLINE_ARTIFACT: audit_outline.model_dump(mode="json"),
        "narrative_contract": llm_contract,
        CHAPTER_CONTRACTS_ARTIFACT: chapter_contracts,
    }


def _save_claim_coverage_checkpoint(
    ctx: InitLongServiceContext,
    *,
    status: str,
    artifacts: dict[str, dict[str, Any]],
    report: dict[str, Any] | None,
    repair_round: int,
    strategy: str,
) -> None:
    items = report.get("items") if isinstance(report, dict) else []
    uncovered_claim_ids = [
        str(item.get("claim_id") or "")
        for item in (items or [])
        if isinstance(item, dict) and item.get("coverage_status") == "uncovered"
    ]
    ctx.storage.save_json(
        _claim_coverage_checkpoint_path(ctx.layout),
        {
            "schema_version": 1,
            "stage": "init_claim_contract_coverage",
            "status": status,
            "artifact_hashes": init_coherence_artifact_hashes(artifacts),
            "repair_round": repair_round,
            "strategy": strategy,
            "report_path": "reports/init_claim_contract_coverage.json",
            "verdict": str((report or {}).get("verdict") or ""),
            "blocked": bool((report or {}).get("blocked", False)),
            "uncovered_claim_ids": uncovered_claim_ids,
            "summary": str((report or {}).get("summary") or ""),
        },
    )


def _legacy_progressive_coverage_artifacts(
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]] | None:
    """Reconstruct the bounded outline shape used by pre-frontier checkpoints.

    Earlier progressive initialization passed the contract-validation outline to
    the coverage checkpoint.  That outline had its total and planned bounds
    collapsed to the hard frontier.  The reconstruction is intentionally narrow:
    it is available only when the supplied source outline has a genuine future
    preview beyond a positive hard frontier.
    """
    source_outline = artifacts.get(OUTLINE_ARTIFACT)
    if not isinstance(source_outline, dict):
        return None
    try:
        total_chapters = int(source_outline.get("total_chapters") or 0)
        hard_through = int(source_outline.get("hard_through_chapter") or 0)
    except (TypeError, ValueError):
        return None
    if isinstance(source_outline.get("total_chapters"), bool) or isinstance(
        source_outline.get("hard_through_chapter"), bool
    ):
        return None
    if not 0 < hard_through < total_chapters:
        return None

    chapters = source_outline.get("chapters")
    if not isinstance(chapters, list):
        return None
    bounded_chapters: list[dict[str, Any]] = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            return None
        try:
            chapter_number = int(chapter.get("chapter_number") or 0)
        except (TypeError, ValueError):
            return None
        if isinstance(chapter.get("chapter_number"), bool):
            return None
        if 0 < chapter_number <= hard_through:
            bounded_chapters.append(chapter)
    if not bounded_chapters:
        return None

    legacy_outline = dict(source_outline)
    legacy_outline.update(
        {
            "total_chapters": hard_through,
            "hard_through_chapter": hard_through,
            "planned_through_chapter": hard_through,
            "chapters": bounded_chapters,
        }
    )
    return {**artifacts, OUTLINE_ARTIFACT: legacy_outline}


def claim_coverage_checkpoint_matches(
    ctx: InitLongServiceContext,
    *,
    artifacts: dict[str, dict[str, Any]],
) -> bool:
    """Return whether the late-init checkpoint belongs to the current artifacts."""
    path = _claim_coverage_checkpoint_path(ctx.layout)
    if not ctx.storage.exists(path):
        return False
    try:
        payload = ctx.storage.load_json(path)
    except Exception:
        return False
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        return False
    if str(payload.get("stage") or "") != "init_claim_contract_coverage":
        return False
    recorded = payload.get("artifact_hashes")
    if not isinstance(recorded, dict):
        return False
    normalized_recorded = {str(key): str(value) for key, value in recorded.items()}
    if normalized_recorded == init_coherence_artifact_hashes(artifacts):
        return True

    # Accept only failed/candidate legacy progressive checkpoints.  A successful
    # checkpoint never needs this migration, and any change outside the former
    # bounded outline remains hash-protected.
    if str(payload.get("status") or "") not in {"candidate", "needs_repair"}:
        return False
    legacy_artifacts = _legacy_progressive_coverage_artifacts(artifacts)
    return (
        legacy_artifacts is not None
        and normalized_recorded == init_coherence_artifact_hashes(legacy_artifacts)
    )


async def repair_claim_contract_coverage(
    *,
    ctx: InitLongServiceContext,
    blueprint: NarrativeBlueprint,
    outline: StoryOutline,
    llm_contract: dict[str, Any],
    chapter_contracts: dict[str, Any],
    settings: Any,
    on_step: Any,
    project_id: str,
    init_entity_catalog: dict[str, Any],
    outline_ctx: dict[str, Any],
    total_chapters: int,
    narrative_complexity: str,
    init_coherence_reports: dict[str, dict[str, Any] | None],
    init_repairs: list[dict[str, Any]],
    outline_research_grounding: dict[str, Any] | None = None,
    coverage_outline: StoryOutline | None = None,
) -> dict[str, Any]:
    """Repair and verify Claim-to-Contract coverage as a resumable late-init stage.

    ``outline`` validates the chapter-contract payload; ``coverage_outline``
    preserves the source outline's commitment frontier for Claim auditing.
    """
    repair_round = 0
    max_rounds = _init_coherence_max_repair_rounds(settings)
    last_signature: tuple[int, tuple[str, ...]] | None = None
    stagnant_rounds = 0

    while True:
        artifacts = _claim_coverage_artifacts(
            blueprint=blueprint,
            outline=outline,
            llm_contract=llm_contract,
            chapter_contracts=chapter_contracts,
            coverage_outline=coverage_outline,
        )
        coverage_report = run_init_claim_contract_coverage_audit(
            ctx,
            artifacts=artifacts,
            chapter_contracts=chapter_contracts,
        )
        init_coherence_reports["claim_contract_coverage"] = coverage_report
        blocking_ids = tuple(
            sorted(
                init_coherence_issue_id(issue)
                for issue in blocking_issues(
                    coverage_report,
                    min_severity=_init_coherence_min_severity(settings),
                )
                if isinstance(issue, dict)
            )
        )
        signature = (int(coverage_report.get("uncovered_claims", 0) or 0), blocking_ids)
        if last_signature == signature:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0
        last_signature = signature

        if not _init_coherence_blocks(ctx, coverage_report):
            _save_claim_coverage_checkpoint(
                ctx,
                status="succeeded",
                artifacts=artifacts,
                report=coverage_report,
                repair_round=repair_round,
                strategy="verified",
            )
            return chapter_contracts

        _save_claim_coverage_checkpoint(
            ctx,
            status="needs_repair",
            artifacts=artifacts,
            report=coverage_report,
            repair_round=repair_round,
            strategy="audit",
        )
        if bool(coverage_report.get("degraded", False)):
            _record_init_resume_decision(
                ctx,
                stage="init_claim_contract_coverage",
                artifact=CHAPTER_CONTRACTS_ARTIFACT,
                action="blocked",
                reason=str(
                    coverage_report.get("degraded_reason") or "claim_contract_coverage_degraded"
                ),
                path=str(ctx.layout.reports_dir / "init_claim_contract_coverage.json"),
                metadata={
                    "summary": str(coverage_report.get("summary") or ""),
                    "stale_claims": int(coverage_report.get("stale_claims", 0) or 0),
                },
            )
            _save_init_readiness(
                ctx,
                reports=init_coherence_reports,
                repairs=init_repairs,
            )
            raise InitCoherenceError(
                "Claim-to-Contract 覆盖审计未通过："
                f"{coverage_report.get('summary') or coverage_report.get('verdict')}"
            )

        auto_repair = bool(getattr(settings, "init_coherence_auto_repair", True))
        if not auto_repair or repair_round >= max_rounds or stagnant_rounds >= 2:
            _save_init_readiness(
                ctx,
                reports=init_coherence_reports,
                repairs=init_repairs,
            )
            raise InitCoherenceError(
                "Claim-to-Contract 覆盖审计未通过："
                f"{coverage_report.get('summary') or coverage_report.get('verdict')}"
            )

        repair_round += 1
        _log.info(
            "init_claim_coverage_repair | round=%d | blocked=%d | stagnant=%d",
            repair_round,
            len(blocking_ids),
            stagnant_rounds,
        )

        # Domain-specific upsert is authoritative for cognitive coverage gaps.
        # It can add a missing constraint, unlike the generic replace-only patcher.
        backfilled = _local_cognitive_backfill(
            chapter_contracts,
            coverage_report,
            entity_catalog=init_entity_catalog,
        )
        if backfilled is not chapter_contracts:
            (
                chapter_contracts,
                contract_coverage,
                milestone_index,
                coverage_repair_outcome,
            ) = await _validate_and_persist_repaired_chapter_contracts(
                ctx,
                payload=backfilled,
                outline=outline,
                outline_ctx=outline_ctx,
                total_chapters=total_chapters,
                narrative_complexity=narrative_complexity,
                llm_contract=llm_contract,
                project_id=project_id,
                entity_catalog=init_entity_catalog,
                outline_research_grounding=outline_research_grounding,
                failure_prefix="Claim-to-Contract 本地回填",
            )
            on_step(
                "init_claim_coverage_backfilled",
                {
                    "round": repair_round,
                    "coverage": contract_coverage,
                    "milestone_count": len(milestone_index.milestones),
                    "repair_attempts": coverage_repair_outcome.attempts_as_dicts(),
                    "chapters_touched": sorted(
                        {
                            int(chapter)
                            for issue in coverage_report.get("issues", [])
                            if isinstance(issue, dict)
                            for scope in issue.get("repair_scope", [])
                            if isinstance(scope, dict)
                            for chapter in scope.get("chapters", [])
                            if int(chapter) > 0
                        }
                    ),
                },
            )
            _save_claim_coverage_checkpoint(
                ctx,
                status="candidate",
                artifacts=_claim_coverage_artifacts(
                    blueprint=blueprint,
                    outline=outline,
                    llm_contract=llm_contract,
                    chapter_contracts=chapter_contracts,
                    coverage_outline=coverage_outline,
                ),
                report=coverage_report,
                repair_round=repair_round,
                strategy="deterministic_upsert",
            )
            continue

        repaired_payload = await _repair_init_artifact_payload(
            ctx,
            artifact=CHAPTER_CONTRACTS_ARTIFACT,
            payload=chapter_contracts,
            report=coverage_report,
            round_index=repair_round,
            repairs=init_repairs,
        )
        (
            chapter_contracts,
            contract_coverage,
            milestone_index,
            coverage_repair_outcome,
        ) = await _validate_and_persist_repaired_chapter_contracts(
            ctx,
            payload=repaired_payload,
            outline=outline,
            outline_ctx=outline_ctx,
            total_chapters=total_chapters,
            narrative_complexity=narrative_complexity,
            llm_contract=llm_contract,
            project_id=project_id,
            entity_catalog=init_entity_catalog,
            outline_research_grounding=outline_research_grounding,
            failure_prefix="Claim-to-Contract LLM 修复",
        )
        on_step(
            "init_claim_coverage_repaired",
            {
                "round": repair_round,
                "coverage": contract_coverage,
                "milestone_count": len(milestone_index.milestones),
                "repair_attempts": coverage_repair_outcome.attempts_as_dicts(),
            },
        )


def _chapter_contract_items_by_number(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in payload.get("chapter_contracts", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            chapter_number = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        if chapter_number > 0:
            result[chapter_number] = item
    return result


def _changed_chapter_contract_numbers(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[int]:
    """Return chapters whose executable contract payload changed."""

    before_items = _chapter_contract_items_by_number(before)
    after_items = _chapter_contract_items_by_number(after)
    return sorted(
        chapter_number
        for chapter_number in set(before_items) | set(after_items)
        if hash_payload(before_items.get(chapter_number))
        != hash_payload(after_items.get(chapter_number))
    )


async def _run_targeted_contract_coherence_reaudit(
    *,
    ctx: InitLongServiceContext,
    outline: StoryOutline,
    llm_contract: dict[str, Any],
    chapter_contracts: dict[str, Any],
    profile: dict[str, Any],
    focus_chapters: list[int],
    init_coherence_reports: dict[str, dict[str, Any] | None],
    init_repairs: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    artifacts = {
        OUTLINE_ARTIFACT: outline.model_dump(mode="json"),
        "narrative_contract": llm_contract,
        CHAPTER_CONTRACTS_ARTIFACT: chapter_contracts,
    }
    report = await run_init_coherence_v2_gate(
        ctx,
        stage="contract_coherence",
        repair_artifact=CHAPTER_CONTRACTS_ARTIFACT,
        profile=profile,
        artifacts=artifacts,
        focus_chapters=focus_chapters,
    )
    init_coherence_reports["contract_coherence"] = report
    ctx.storage.save_json(ctx.layout.reports_dir / "contract_coherence.json", report)
    ctx.on_step(
        "init_repair_reaudit_stage",
        {
            "stage": "contract_coherence",
            "phase": phase,
            "status": "failed" if _init_coherence_blocks(ctx, report) else "passed",
            "focus_chapters": focus_chapters,
            "verdict": report.get("verdict"),
            "summary": report.get("summary"),
        },
    )
    if _init_coherence_blocks(ctx, report):
        raise InitCoherenceError(
            _format_init_coherence_error(
                stage="contract_coherence",
                report=report,
                repairs=init_repairs,
            )
        )
    return report


async def reaudit_changed_chapter_contracts(
    *,
    ctx: InitLongServiceContext,
    blueprint: NarrativeBlueprint,
    outline: StoryOutline,
    llm_contract: dict[str, Any],
    chapter_contracts: dict[str, Any],
    project_id: str,
    init_entity_catalog: dict[str, Any],
    outline_ctx: dict[str, Any],
    total_chapters: int,
    narrative_complexity: str,
    init_coherence_reports: dict[str, dict[str, Any] | None],
    init_repairs: list[dict[str, Any]],
    focus_chapters: list[int],
    init_coherence_profile: dict[str, Any] | None = None,
    outline_research_grounding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Close the repair loop before changed chapter contracts may be admitted."""

    normalized_focus: set[int] = set()
    for number in focus_chapters:
        try:
            chapter_number = int(number)
        except (TypeError, ValueError):
            continue
        if chapter_number > 0:
            normalized_focus.add(chapter_number)
    focus = sorted(normalized_focus)
    if not focus:
        return chapter_contracts
    ctx.on_step(
        "init_repair_reaudit_started",
        {
            "artifact": CHAPTER_CONTRACTS_ARTIFACT,
            "focus_chapters": focus,
            "stages": ["contract_coherence", "claim_contract_coverage"],
        },
    )
    profile = init_coherence_profile or load_reusable_init_coherence_profile(
        ctx,
        require_refined=True,
    )
    if profile is None:
        _save_init_readiness(
            ctx,
            reports=init_coherence_reports,
            repairs=init_repairs,
        )
        ctx.on_step(
            "init_repair_reaudit_failed",
            {
                "artifact": CHAPTER_CONTRACTS_ARTIFACT,
                "focus_chapters": focus,
                "error_type": "InitCoherenceError",
                "error": "修复后定向复审缺少初始化一致性画像。",
            },
        )
        raise InitCoherenceError("修复后定向复审缺少初始化一致性画像。")
    profile = dict(profile)
    if init_entity_catalog.get("allowed_entities"):
        profile["entity_catalog"] = init_entity_catalog

    try:
        await _run_targeted_contract_coherence_reaudit(
            ctx=ctx,
            outline=outline,
            llm_contract=llm_contract,
            chapter_contracts=chapter_contracts,
            profile=profile,
            focus_chapters=focus,
            init_coherence_reports=init_coherence_reports,
            init_repairs=init_repairs,
            phase="after_contract_change",
        )
        before_coverage_repair = copy.deepcopy(chapter_contracts)
        audited_contracts = await repair_claim_contract_coverage(
            ctx=ctx,
            blueprint=blueprint,
            outline=outline,
            llm_contract=llm_contract,
            chapter_contracts=chapter_contracts,
            settings=ctx.settings,
            on_step=ctx.on_step,
            project_id=project_id,
            init_entity_catalog=init_entity_catalog,
            outline_ctx=outline_ctx,
            total_chapters=total_chapters,
            narrative_complexity=narrative_complexity,
            init_coherence_reports=init_coherence_reports,
            init_repairs=init_repairs,
            outline_research_grounding=outline_research_grounding,
        )
        ctx.on_step(
            "init_repair_reaudit_stage",
            {
                "stage": "claim_contract_coverage",
                "phase": "after_contract_change",
                "status": "passed",
                "focus_chapters": focus,
                "verdict": (
                    (init_coherence_reports.get("claim_contract_coverage") or {}).get("verdict")
                ),
            },
        )

        coverage_changed_chapters = _changed_chapter_contract_numbers(
            before_coverage_repair,
            audited_contracts,
        )
        if coverage_changed_chapters:
            final_focus = sorted(set(focus) | set(coverage_changed_chapters))
            await _run_targeted_contract_coherence_reaudit(
                ctx=ctx,
                outline=outline,
                llm_contract=llm_contract,
                chapter_contracts=audited_contracts,
                profile=profile,
                focus_chapters=final_focus,
                init_coherence_reports=init_coherence_reports,
                init_repairs=init_repairs,
                phase="after_claim_coverage_repair",
            )

            # The targeted coherence gate refreshes the Claims ledger against
            # the repaired chapter-contract artifact. Entity adjudication may
            # legitimately canonicalize the same upstream claim differently
            # (for example an alias becoming its canonical item name), so the
            # refreshed ledger is a new coverage input. Reconcile it once more
            # before the final regression check instead of judging it against
            # constraints projected from the previous ledger snapshot.
            before_ledger_refresh_repair = audited_contracts
            audited_contracts = await repair_claim_contract_coverage(
                ctx=ctx,
                blueprint=blueprint,
                outline=outline,
                llm_contract=llm_contract,
                chapter_contracts=audited_contracts,
                settings=ctx.settings,
                on_step=ctx.on_step,
                project_id=project_id,
                init_entity_catalog=init_entity_catalog,
                outline_ctx=outline_ctx,
                total_chapters=total_chapters,
                narrative_complexity=narrative_complexity,
                init_coherence_reports=init_coherence_reports,
                init_repairs=init_repairs,
                outline_research_grounding=outline_research_grounding,
            )
            ledger_refresh_changed_chapters = _changed_chapter_contract_numbers(
                before_ledger_refresh_repair,
                audited_contracts,
            )
            final_focus = sorted(set(final_focus) | set(ledger_refresh_changed_chapters))
            ctx.on_step(
                "init_repair_reaudit_stage",
                {
                    "stage": "claim_contract_coverage",
                    "phase": "after_claim_ledger_refresh",
                    "status": "passed",
                    "focus_chapters": final_focus,
                    "changed_chapters": ledger_refresh_changed_chapters,
                    "verdict": (
                        (init_coherence_reports.get("claim_contract_coverage") or {}).get(
                            "verdict"
                        )
                    ),
                },
            )
            final_coverage_report = run_init_claim_contract_coverage_audit(
                ctx,
                artifacts=_claim_coverage_artifacts(
                    blueprint=blueprint,
                    outline=outline,
                    llm_contract=llm_contract,
                    chapter_contracts=audited_contracts,
                ),
                chapter_contracts=audited_contracts,
            )
            init_coherence_reports["claim_contract_coverage"] = final_coverage_report
            ctx.on_step(
                "init_repair_reaudit_stage",
                {
                    "stage": "claim_contract_coverage",
                    "phase": "final_regression_check",
                    "status": (
                        "failed" if _init_coherence_blocks(ctx, final_coverage_report) else "passed"
                    ),
                    "focus_chapters": final_focus,
                    "verdict": final_coverage_report.get("verdict"),
                    "summary": final_coverage_report.get("summary"),
                },
            )
            if _init_coherence_blocks(ctx, final_coverage_report):
                raise InitCoherenceError(
                    "Claim-to-Contract 修复后回归复审未通过："
                    f"{final_coverage_report.get('summary') or final_coverage_report.get('verdict')}"
                )

        readiness = _save_init_readiness(
            ctx,
            reports=init_coherence_reports,
            repairs=init_repairs,
        )
        ctx.on_step(
            "init_repair_reaudit_passed",
            {
                "artifact": CHAPTER_CONTRACTS_ARTIFACT,
                "focus_chapters": sorted(set(focus) | set(coverage_changed_chapters)),
                "allowed": bool(readiness.get("allowed", False)),
                "stages": ["contract_coherence", "claim_contract_coverage"],
            },
        )
        return audited_contracts
    except Exception as exc:
        _save_init_readiness(
            ctx,
            reports=init_coherence_reports,
            repairs=init_repairs,
        )
        ctx.on_step(
            "init_repair_reaudit_failed",
            {
                "artifact": CHAPTER_CONTRACTS_ARTIFACT,
                "focus_chapters": focus,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise


async def initialize_narrative_state_and_contracts(
    *,
    ctx: InitLongServiceContext,
    layout: Any,
    story_bible: StoryBible,
    character_bible: CharacterBible,
    blueprint: NarrativeBlueprint,
    outline: StoryOutline,
    language: str,
    words_per_chapter: int,
    settings: Any,
    on_step: Any,
    project_id: str,
    entity_registry: EntityRegistry,
    init_entity_catalog: dict[str, Any],
    outline_ctx: dict[str, Any],
    total_chapters: int,
    enriched_spec: StorySpec,
    init_coherence_profile: dict[str, Any] | None,
    init_coherence_reports: dict[str, dict[str, Any] | None],
    init_repairs: list[dict[str, Any]],
    outline_research_grounding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    llm_contract: dict[str, Any] = {}
    chapter_contracts: dict[str, Any] = {}

    # ③b Generate narrative contract (rule-based extraction, no LLM call)
    _build_and_persist_narrative_contract(
        storage=ctx.storage,
        layout=layout,
        story_bible=story_bible,
        character_bible=character_bible,
        blueprint_data=ctx.storage.load_json(layout.blueprint_path)
        if ctx.storage.exists(layout.blueprint_path)
        else None,
        language=language,
        words_per_chapter=words_per_chapter,
        continuity_defaults={
            "enabled": getattr(settings, "init_continuity_protocol_enabled", True),
            "location_transition_required": getattr(
                settings, "init_continuity_location_transition_required", True
            ),
            "location_transition_window_sentences": getattr(
                settings, "init_continuity_location_transition_window_sentences", 3
            ),
            "bridge_echo_ratio": getattr(settings, "init_continuity_bridge_echo_ratio", 0.28),
            "bridge_echo_min_chars": getattr(
                settings, "init_continuity_bridge_echo_min_chars", 450
            ),
            "bridge_echo_max_chars": getattr(
                settings, "init_continuity_bridge_echo_max_chars", 1400
            ),
            "time_notation_profile": getattr(
                settings, "init_continuity_time_notation_profile", "auto"
            ),
            "traditional_time_ke_range": getattr(
                settings, "init_continuity_traditional_time_ke_range", "一至四刻"
            ),
            "pov_visibility_rule": getattr(
                settings,
                "init_continuity_pov_visibility_rule",
                "限知视角仅描写可观察事实，禁止直接写非POV角色内心。",
            ),
            "forbidden_repetition_rule": getattr(
                settings,
                "init_continuity_forbidden_repetition_rule",
                "禁复用仅针对修辞性意象；人物、实体与剧情锚点不在此限。命中后必须替换为全新意象，不得使用近义改写。",
            ),
            "max_key_revelations_per_chapter": getattr(
                settings, "init_continuity_max_key_revelations_per_chapter", 2
            ),
            "min_unresolved_threads_to_keep": getattr(
                settings, "init_continuity_min_unresolved_threads_to_keep", 1
            ),
        },
    )
    try:
        deterministic_contract = ctx.storage.load_json(layout.narrative_contract_path)
    except Exception as exc:
        _log.warning(
            "deterministic_narrative_contract_load_failed | project=%s | error=%s",
            project_id,
            exc,
        )
        deterministic_contract = {}
    llm_contract_input_hashes = _llm_narrative_contract_input_hashes(
        spec=enriched_spec,
        story_bible=story_bible,
        character_bible=character_bible,
        entity_registry=entity_registry,
        deterministic_contract=deterministic_contract,
    )
    reusable_llm_contract = _load_reusable_llm_narrative_contract(
        ctx,
        input_hashes=llm_contract_input_hashes,
    )
    narrative_state_enabled = bool(getattr(settings, "narrative_state_enabled", True))
    if not narrative_state_enabled:
        reusable_llm_contract = None
        on_step(
            "init_narrative_contract_skipped",
            {
                "reason": "narrative_state_disabled",
                "fallback": "deterministic_contracts",
            },
        )
    narrative_init_step = "init_narrative_contract"
    try:
        state_store = NarrativeStateStore(layout.root) if narrative_state_enabled else None
        if state_store is not None:
            state_store.save_entity_registry(entity_registry)

        if reusable_llm_contract is not None:
            llm_contract = reusable_llm_contract
            _persist_llm_narrative_contract(
                ctx,
                llm_contract=llm_contract,
                state_store=state_store,
                source="cached",
                input_hashes=llm_contract_input_hashes,
            )
            on_step(
                "init_narrative_contract_resumed",
                {
                    "status": "cached",
                    "plot_threads": len(llm_contract.get("plot_threads", []) or []),
                },
            )
        else:
            llm_contract_source = "deterministic_projection"
            if narrative_state_enabled and bool(
                getattr(settings, "init_narrative_contract_llm_enabled", False)
            ):
                try:
                    llm_contract = await _generate_llm_narrative_contract(
                        ctx,
                        spec=enriched_spec,
                        story_bible=story_bible,
                        character_bible=character_bible,
                        entity_registry=entity_registry,
                    )
                    llm_contract_source = "llm"
                except Exception as exc:
                    _log.warning(
                        "init_narrative_contract_llm_failed | project=%s | error=%s",
                        project_id,
                        exc,
                    )
                    on_step(
                        "init_narrative_contract_llm_failed",
                        {
                            "status": "fallback",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    llm_contract = build_adjudication_narrative_contract(
                        deterministic_contract,
                    )
                    llm_contract_source = "deterministic_fallback"
            else:
                llm_contract = build_adjudication_narrative_contract(deterministic_contract)
                if not narrative_state_enabled:
                    llm_contract_source = "deterministic_projection_narrative_state_disabled"
            _persist_llm_narrative_contract(
                ctx,
                llm_contract=llm_contract,
                state_store=state_store,
                source=llm_contract_source,
                input_hashes=llm_contract_input_hashes,
            )
            on_step(
                "init_narrative_contract",
                {
                    "status": llm_contract_source,
                    "plot_threads": len(llm_contract.get("plot_threads", []) or []),
                },
            )

        narrative_init_step = "plan_chapter_contracts"
        contract_outline = outline.model_copy(
            update={
                "total_chapters": total_chapters,
                "hard_through_chapter": total_chapters,
                "planned_through_chapter": total_chapters,
                "chapters": [
                    chapter
                    for chapter in outline.chapters
                    if chapter.chapter_number <= total_chapters
                ],
            }
        )
        resume_strict_noise = bool(
            getattr(settings, "init_chapter_contract_resume_strict_noise", True)
        )
        source_artifact_rebuild_chapters = _source_artifact_resume_rebuild_chapters(
            ctx,
            outline=contract_outline,
        )
        reusable_contracts = _load_reusable_chapter_contracts(
            ctx,
            outline=contract_outline,
            narrative_contract=llm_contract,
            entity_catalog=init_entity_catalog,
            outline_research_grounding=outline_research_grounding,
            strict_noise=resume_strict_noise,
            force_rebuild_chapters=source_artifact_rebuild_chapters,
        )
        if reusable_contracts is not None:
            if reusable_contracts.rebuild_chapters:
                (
                    rebuilt_contracts,
                    _rebuilt_coverage,
                ) = await _batched_generate_chapter_contracts(
                    ctx,
                    outline=contract_outline,
                    narrative_contract=llm_contract,
                    project_id=project_id,
                    entity_catalog=init_entity_catalog,
                    focus_chapters=reusable_contracts.rebuild_chapters,
                    outline_research_grounding=outline_research_grounding,
                )
                chapter_contracts, contract_coverage = merge_reusable_and_rebuilt_chapter_contracts(
                    reusable_contracts.payload,
                    rebuilt_contracts,
                    outline=contract_outline,
                    settings=settings,
                )
                on_step(
                    "plan_chapter_contracts_partial_resume",
                    {
                        "reused": reusable_contracts.reusable_chapters,
                        "rebuilt": reusable_contracts.rebuild_chapters,
                        "coverage": contract_coverage,
                    },
                )
            else:
                chapter_contracts, contract_coverage = reusable_contracts
            chapter_contracts["coverage"] = contract_coverage
            on_step(
                "plan_chapter_contracts_resumed",
                {
                    "count": len(chapter_contracts.get("chapter_contracts", []) or []),
                    "coverage": contract_coverage,
                },
            )
        else:
            chapter_contracts, contract_coverage = await _batched_generate_chapter_contracts(
                ctx,
                outline=contract_outline,
                narrative_contract=llm_contract,
                project_id=project_id,
                entity_catalog=init_entity_catalog,
                outline_research_grounding=outline_research_grounding,
            )
        contract_repair_outcome = await InitRepairOrchestrator(
            get_init_repair_policy(InitArtifact.CHAPTER_CONTRACTS)
        ).repair(
            chapter_contracts,
            InitRepairContext(
                service_ctx=ctx,
                outline_ctx=outline_ctx,
                total_chapters=total_chapters,
                narrative_complexity=enriched_spec.narrative_complexity,
                artifacts={
                    "outline": contract_outline,
                    "narrative_contract": llm_contract,
                    "project_id": project_id,
                },
            ),
        )
        _persist_init_repair_outcome(
            ctx,
            artifact="chapter_contracts",
            outcome=contract_repair_outcome,
        )
        chapter_contracts = cast(dict[str, Any], contract_repair_outcome.payload)
        contract_coverage = dict(chapter_contracts.get("coverage") or {})
        if contract_repair_outcome.repaired:
            on_step(
                "plan_chapter_contracts_repaired",
                {
                    "coverage": contract_coverage,
                    "repair_attempts": contract_repair_outcome.attempts_as_dicts(),
                },
            )
        if not contract_repair_outcome.report.is_valid:
            raise InitCoherenceError(
                f"章节契约修复后仍未通过：{'; '.join(contract_repair_outcome.report.errors)}"
            )
        if contract_coverage["backfilled_chapters"]:
            _log.warning(
                "chapter_contracts_backfilled | project=%s | chapters=%s | "
                "discarded=%s | duplicates=%s",
                project_id,
                contract_coverage["backfilled_chapters"],
                contract_coverage["discarded_count"],
                contract_coverage["duplicate_count"],
            )
        _assert_chapter_contracts_ready_to_persist(
            chapter_contracts,
            contract_coverage,
            allow_local_fallback=True,
        )
        milestone_index = _persist_chapter_contract_runtime_artifacts(
            ctx,
            outline=contract_outline,
            chapter_contracts=chapter_contracts,
            llm_contract=llm_contract,
            project_id=project_id,
            entity_catalog=init_entity_catalog,
            outline_research_grounding=outline_research_grounding,
        )
        on_step(
            "plan_chapter_contracts",
            {
                "count": len(chapter_contracts.get("chapter_contracts", []) or []),
                "coverage": contract_coverage,
                "milestone_count": len(milestone_index.milestones),
            },
        )

        narrative_init_step = "adjudicate_contract_coherence"
        repair_round = 0
        contract_followup_repair_used = False
        contract_last_issue_keys: set[str] | None = None
        contract_stagnant_rounds = 0
        focus_chapters = None
        while True:
            if init_coherence_profile is None:
                raise InitCoherenceError("初始化一致性 v2 缺少项目画像。")
            contract_artifacts = {
                # Claims must retain the source outline's identity/frontier.
                # The bounded contract_outline is only a validation view; using
                # it here invalidates outline Claims at the next coverage audit.
                OUTLINE_ARTIFACT: outline.model_dump(mode="json"),
                "narrative_contract": llm_contract,
                CHAPTER_CONTRACTS_ARTIFACT: chapter_contracts,
            }
            coherence = _load_reusable_init_coherence_report(
                ctx,
                stage="contract_coherence",
                artifact=CHAPTER_CONTRACTS_ARTIFACT,
                artifacts=contract_artifacts,
            )
            if coherence is None:
                coherence = await run_init_coherence_v2_gate(
                    ctx,
                    stage="contract_coherence",
                    repair_artifact=CHAPTER_CONTRACTS_ARTIFACT,
                    profile=init_coherence_profile,
                    artifacts=contract_artifacts,
                    focus_chapters=focus_chapters,
                )
                _record_init_resume_decision(
                    ctx,
                    stage="contract_coherence",
                    artifact=CHAPTER_CONTRACTS_ARTIFACT,
                    action="regenerated",
                    reason="cache_missing_or_rejected",
                    expected_hashes=init_coherence_artifact_hashes(contract_artifacts),
                )
            init_coherence_reports["contract_coherence"] = coherence
            ctx.storage.save_json(layout.reports_dir / "contract_coherence.json", coherence)
            on_step("adjudicate_contract_coherence", coherence)

            coherence = _run_init_coherence_auto_repair(
                ctx,
                artifact=CHAPTER_CONTRACTS_ARTIFACT,
                report=coherence,
                payload=chapter_contracts,
                repairs=init_repairs,
            )
            if coherence is not init_coherence_reports["contract_coherence"]:
                init_coherence_reports["contract_coherence"] = coherence
                ctx.storage.save_json(layout.reports_dir / "contract_coherence.json", coherence)
                (
                    chapter_contracts,
                    contract_coverage,
                    milestone_index,
                    contract_auto_repair_outcome,
                ) = await _validate_and_persist_repaired_chapter_contracts(
                    ctx,
                    payload=chapter_contracts,
                    outline=contract_outline,
                    outline_ctx=outline_ctx,
                    total_chapters=total_chapters,
                    narrative_complexity=enriched_spec.narrative_complexity,
                    llm_contract=llm_contract,
                    project_id=project_id,
                    entity_catalog=init_entity_catalog,
                    outline_research_grounding=outline_research_grounding,
                    failure_prefix="章节契约确定性修复",
                )
                on_step(
                    "adjudicate_contract_coherence",
                    {
                        **coherence,
                        "coverage": contract_coverage,
                        "milestone_count": len(milestone_index.milestones),
                        "repair_attempts": contract_auto_repair_outcome.attempts_as_dicts(),
                    },
                )
            repair_round = max(
                repair_round,
                _init_coherence_repair_round_start(
                    settings,
                    init_repairs,
                    artifact=CHAPTER_CONTRACTS_ARTIFACT,
                    report=coherence,
                ),
            )
            if not _init_coherence_blocks(ctx, coherence):
                break
            (
                stop_repair,
                stop_reason,
                contract_current_issue_keys,
                contract_stagnant_rounds,
            ) = _init_coherence_repair_stop_decision(
                settings,
                coherence,
                previous_issue_keys=contract_last_issue_keys,
                stagnant_rounds=contract_stagnant_rounds,
            )
            if stop_repair:
                _record_init_coherence_repair_loop_stop(
                    ctx,
                    stage="contract_coherence",
                    artifact=CHAPTER_CONTRACTS_ARTIFACT,
                    report=coherence,
                    reason=stop_reason,
                    previous_issue_keys=contract_last_issue_keys,
                    current_issue_keys=contract_current_issue_keys,
                    stagnant_rounds=contract_stagnant_rounds,
                )
                _save_init_readiness(
                    ctx,
                    reports=init_coherence_reports,
                    repairs=init_repairs,
                )
                raise InitCoherenceError(
                    _format_init_coherence_error(
                        stage="contract_coherence",
                        report=coherence,
                        repairs=init_repairs,
                    )
                )
            contract_last_issue_keys = contract_current_issue_keys
            auto_repair = bool(getattr(settings, "init_coherence_auto_repair", True))
            repair_limit_reached = repair_round >= _init_coherence_max_repair_rounds(settings)
            allow_followup_repair = (
                auto_repair
                and repair_limit_reached
                and (
                    _init_coherence_allows_llm_followup_repair(
                        settings,
                        artifact=CHAPTER_CONTRACTS_ARTIFACT,
                        report=coherence,
                        repairs=init_repairs,
                        repair_round=repair_round,
                        followup_used=contract_followup_repair_used,
                    )
                )
            )
            if not auto_repair or (repair_limit_reached and not allow_followup_repair):
                _save_init_readiness(
                    ctx,
                    reports=init_coherence_reports,
                    repairs=init_repairs,
                )
                raise InitCoherenceError(
                    _format_init_coherence_error(
                        stage="contract_coherence",
                        report=coherence,
                        repairs=init_repairs,
                    )
                )
            if allow_followup_repair:
                contract_followup_repair_used = True
            repair_round += 1
            repaired_contract_payload = await _repair_init_artifact_payload(
                ctx,
                artifact=CHAPTER_CONTRACTS_ARTIFACT,
                payload=chapter_contracts,
                report=coherence,
                round_index=repair_round,
                repairs=init_repairs,
            )
            contract_repair_outcome = await InitRepairOrchestrator(
                get_init_repair_policy(InitArtifact.CHAPTER_CONTRACTS)
            ).repair(
                repaired_contract_payload,
                InitRepairContext(
                    service_ctx=ctx,
                    outline_ctx=outline_ctx,
                    total_chapters=total_chapters,
                    narrative_complexity=enriched_spec.narrative_complexity,
                    artifacts={
                        "outline": contract_outline,
                        "narrative_contract": llm_contract,
                        "project_id": project_id,
                    },
                ),
            )
            _persist_init_repair_outcome(
                ctx,
                artifact="chapter_contracts",
                outcome=contract_repair_outcome,
            )
            chapter_contracts = cast(dict[str, Any], contract_repair_outcome.payload)
            contract_coverage = dict(chapter_contracts.get("coverage") or {})
            if contract_repair_outcome.repaired:
                on_step(
                    "plan_chapter_contracts_repaired",
                    {
                        "coverage": contract_coverage,
                        "repair_attempts": contract_repair_outcome.attempts_as_dicts(),
                    },
                )
            if not contract_repair_outcome.report.is_valid:
                raise InitCoherenceError(
                    "章节契约补丁修复后仍未通过："
                    f"{'; '.join(contract_repair_outcome.report.errors)}"
                )
            ctx.storage.save_json(
                layout.plans_dir / "chapter_contracts.json",
                chapter_contracts,
            )
            focus_chapters = _init_coherence_focus_chapters_after_repair(
                ctx,
                report=coherence,
                artifact=CHAPTER_CONTRACTS_ARTIFACT,
                repairs=init_repairs,
            )
            milestone_index = build_plot_milestone_index(
                outline=contract_outline,
                chapter_contracts=chapter_contracts,
                narrative_contract=llm_contract,
                project_id=project_id,
            )
            ctx.storage.save_json(
                layout.plans_dir / "plot_milestone_index.json",
                milestone_index.model_dump(mode="json"),
            )

        narrative_init_step = "init_claim_contract_coverage"
        chapter_contracts = await repair_claim_contract_coverage(
            ctx=ctx,
            blueprint=blueprint,
            outline=contract_outline,
            llm_contract=llm_contract,
            chapter_contracts=chapter_contracts,
            settings=settings,
            on_step=on_step,
            project_id=project_id,
            init_entity_catalog=init_entity_catalog,
            outline_ctx=outline_ctx,
            total_chapters=total_chapters,
            narrative_complexity=enriched_spec.narrative_complexity,
            init_coherence_reports=init_coherence_reports,
            init_repairs=init_repairs,
            outline_research_grounding=outline_research_grounding,
            coverage_outline=outline,
        )
    except Exception as exc:
        _log.warning(
            "llm_narrative_state_init_failed | step=%s | error=%s",
            narrative_init_step,
            exc,
            exc_info=True,
        )
        narrative_state_required = bool(getattr(settings, "narrative_state_required", True))
        on_step(
            f"{narrative_init_step}_failed",
            {
                "error": str(exc),
                "error_type": type(exc).__name__,
                "narrative_state_required": narrative_state_required,
                "fallback": (
                    "raise"
                    if narrative_state_required or isinstance(exc, InitCoherenceError)
                    else "continue_without_narrative_state"
                ),
            },
        )
        if isinstance(exc, InitCoherenceError):
            raise
        if narrative_state_required:
            raise

    return {
        "llm_contract": llm_contract,
        "chapter_contracts": chapter_contracts,
    }
