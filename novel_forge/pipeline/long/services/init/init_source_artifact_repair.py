"""Implementation slice extracted from init_service.py (init_source_artifact_repair.py)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from novel_forge.core.schemas.artifacts import CanonicalEntityRef
from novel_forge.pipeline.long.services.context.source_artifacts import (
    _derived_entity_id,
    _is_unknown_entity_placeholder,
)
from novel_forge.pipeline.long.services.contract_field_semantics import (
    is_chapter_contract_entity_ref_map_path,
    is_chapter_contract_entity_ref_path,
)
from novel_forge.pipeline.long.services.init.init_chapter_contracts import (
    _assert_chapter_contracts_ready_to_persist,
    _persist_chapter_contract_runtime_artifacts,
)
from novel_forge.pipeline.long.services.init.init_common import (
    CHAPTER_CONTRACTS_ARTIFACT,
    Any,
    CharacterBible,
    InitArtifact,
    InitCoherenceError,
    InitLongServiceContext,
    InitRepairContext,
    InitRepairOrchestrator,
    NarrativeBlueprint,
    StoryBible,
    StoryOutline,
    StorySpec,
    TaskType,
    _ensure_chapter_contract_coverage,
    _log,
    build_init_entity_catalog,
    calculate_route_aware_max_tokens,
    cast,
    copy,
    dataclass,
    get_init_repair_policy,
    json,
    persist_init_source_artifacts,
    re,
)
from novel_forge.pipeline.long.services.init.init_entity_references import (
    build_entity_candidate_packs,
)
from novel_forge.pipeline.long.services.init.init_repair_manifest import (
    _persist_init_repair_outcome,
    _record_init_repair_manifest_round,
)
from novel_forge.pipeline.long.services.init.init_repair_targets import (
    _init_coherence_repair_round_start,
    _repair_init_artifact_payload,
)
from novel_forge.pipeline.long.services.init.init_source_resume import (
    _emit_source_artifact_step,
    _init_coherence_repair_stop_decision,
    _load_optional_json,
    _record_init_coherence_repair_loop_stop,
    _record_init_resume_decision,
    _save_init_readiness,
)
from novel_forge.pipeline.long.services.init.init_value_helpers import (
    _init_coherence_list,
    _positive_int,
)


@dataclass(frozen=True)
class SourceArtifactRepairPlan:
    """Classifies source artifact gate issues before any automatic repair."""

    repairable_issues: list[dict[str, Any]]
    non_repairable_issues: list[dict[str, Any]]

    @property
    def repairable_count(self) -> int:
        return len(self.repairable_issues)

    @property
    def non_repairable_count(self) -> int:
        return len(self.non_repairable_issues)

    @property
    def blocking_count(self) -> int:
        return self.repairable_count + self.non_repairable_count


@dataclass(frozen=True)
class SourceArtifactEntityAliasRepairResult:
    """Deterministic source-artifact entity alias repair outcome."""

    payload: dict[str, Any]
    changed: bool
    repair_record: dict[str, Any]


_SOURCE_ARTIFACT_CANDIDATE_FIELDS: tuple[str, ...] = (
    "new_character_candidates",
    "new_entity_candidates",
    "new_group_candidates",
    "new_collective_candidates",
    "new_organization_candidates",
    "new_location_candidates",
    "new_item_candidates",
    "new_concept_candidates",
)


def _init_source_artifact_repair_rounds(settings: Any) -> int:
    try:
        value = int(getattr(settings, "init_source_artifact_repair_rounds", 2) or 0)
    except (TypeError, ValueError):
        value = 2
    return max(0, min(value, 4))


def _source_artifact_issue_to_dict(raw_issue: Any) -> dict[str, Any]:
    if hasattr(raw_issue, "model_dump"):
        return raw_issue.model_dump(mode="json")
    if isinstance(raw_issue, dict):
        return dict(raw_issue)
    return {}


def _source_artifact_repair_plan(readiness_artifact: Any) -> SourceArtifactRepairPlan:
    repairable: list[dict[str, Any]] = []
    non_repairable: list[dict[str, Any]] = []
    for raw_issue in getattr(readiness_artifact, "blocking_issues", []) or []:
        issue = _source_artifact_issue_to_dict(raw_issue)
        if (
            issue.get("code") == "unresolved_contract_entity"
            and _source_artifact_issue_chapter(issue) > 0
            and _source_artifact_unresolved_name(issue)
        ):
            repairable.append(issue)
        else:
            non_repairable.append(issue)
    return SourceArtifactRepairPlan(
        repairable_issues=repairable,
        non_repairable_issues=non_repairable,
    )


def _source_artifact_blocking_issue_codes(readiness_artifact: Any) -> list[str]:
    codes: list[str] = []
    for raw_issue in getattr(readiness_artifact, "blocking_issues", []) or []:
        issue = _source_artifact_issue_to_dict(raw_issue)
        code = str(issue.get("code") or "unknown").strip() or "unknown"
        if code not in codes:
            codes.append(code)
    return codes


def _source_artifact_readiness_stage_report(
    readiness_artifact: Any,
    *,
    repair_plan: SourceArtifactRepairPlan | None = None,
    attempts: int = 0,
    stop_reason: str = "",
) -> dict[str, Any]:
    quality_status = str(getattr(readiness_artifact, "quality_status", "") or "").strip().lower()
    blocking_raw = list(getattr(readiness_artifact, "blocking_issues", []) or [])
    blocked = quality_status != "pass" or bool(blocking_raw)
    if repair_plan is None:
        repair_plan = _source_artifact_repair_plan(readiness_artifact)

    issues: list[dict[str, Any]] = []
    for index, raw_issue in enumerate(blocking_raw):
        issue = _source_artifact_issue_to_dict(raw_issue)
        code = str(issue.get("code") or "unknown").strip() or "unknown"
        source = str(issue.get("source") or "source_artifacts").strip() or "source_artifacts"
        path = str(issue.get("path") or "").strip()
        repairable = any(issue is item or issue == item for item in repair_plan.repairable_issues)
        issues.append(
            {
                "id": f"source_artifacts_{code}_{index}",
                "severity": str(issue.get("severity") or "critical"),
                "type": code,
                "description": str(issue.get("message") or code),
                "resolution": (
                    "自动修复将尝试归一化章节契约实体引用。"
                    if repairable
                    else "该 source artifact 问题不可自动修复，需要回到上游 artifact 或人工处理。"
                ),
                "source": source,
                "path": path,
                "repairable": repairable,
                "repair_scope": [
                    {
                        "artifact": CHAPTER_CONTRACTS_ARTIFACT,
                        "chapters": [_source_artifact_issue_chapter(issue)]
                        if _source_artifact_issue_chapter(issue) > 0
                        else [],
                        "fields": [],
                        "operation": "replace",
                    }
                ]
                if repairable
                else [],
                "issue_code": code,
            }
        )

    summary = (
        "source_artifacts 准入通过。"
        if not blocked
        else (
            f"source_artifacts 准入未通过："
            f"可自动修复 {repair_plan.repairable_count} 个；"
            f"不可自动修复 {repair_plan.non_repairable_count} 个。"
        )
    )
    if stop_reason:
        summary = f"{summary} 停止原因：{stop_reason}。"
    return {
        "schema_version": 1,
        "stage": "source_artifacts",
        "artifact": "source_artifacts",
        "verdict": "accept" if not blocked else "needs_repair",
        "blocked": blocked,
        "summary": summary,
        "issues": issues,
        "quality_status": quality_status or ("fail" if blocked else "pass"),
        "repairable_issue_count": repair_plan.repairable_count,
        "non_repairable_issue_count": repair_plan.non_repairable_count,
        "attempts": attempts,
        "stop_reason": stop_reason,
        "remaining_issue_codes": _source_artifact_blocking_issue_codes(readiness_artifact),
    }


def _source_artifact_repair_issue_ids(repair_plan: SourceArtifactRepairPlan) -> list[str]:
    issue_ids: list[str] = []
    for index, issue in enumerate(repair_plan.repairable_issues):
        chapter = _source_artifact_issue_chapter(issue)
        issue_ids.append(f"source_artifacts_unresolved_entity_{chapter}_{index}")
    return issue_ids


def _record_source_artifact_gate_repair(
    ctx: InitLongServiceContext,
    *,
    repairs: list[dict[str, Any]],
    status: str,
    attempt: int,
    before_plan: SourceArtifactRepairPlan,
    before_readiness: Any,
    after_readiness: Any | None = None,
    stop_reason: str = "",
) -> None:
    after_count = (
        len(getattr(after_readiness, "blocking_issues", []) or [])
        if after_readiness is not None
        else before_plan.blocking_count
    )
    record = {
        "artifact": "source_artifacts",
        "repair_type": "source_artifact_gate",
        "status": status,
        "attempt": attempt,
        "repairable_issue_count": before_plan.repairable_count,
        "non_repairable_issue_count": before_plan.non_repairable_count,
        "source_blocking_issue_count": len(getattr(before_readiness, "blocking_issues", []) or []),
        "remaining_blocking_issue_count": after_count,
        "remaining_issue_codes": _source_artifact_blocking_issue_codes(
            after_readiness or before_readiness
        ),
        "source_issue_ids": _source_artifact_repair_issue_ids(before_plan),
        "logs_path": str(ctx.layout.logs_dir),
    }
    if stop_reason:
        record["stop_reason"] = stop_reason
    repairs.append(record)
    ctx.storage.save_json(
        ctx.layout.reports_dir / "init_artifact_repair.json", {"repairs": repairs}
    )
    ctx.on_step("init_source_artifacts_repair_gate", record)


def _format_source_artifact_failure(
    ctx: InitLongServiceContext,
    *,
    readiness_artifact: Any,
    repair_plan: SourceArtifactRepairPlan,
    attempts: int,
    stop_reason: str = "",
) -> str:
    issue_messages = []
    for issue in list(getattr(readiness_artifact, "blocking_issues", []) or [])[:5]:
        issue_dict = _source_artifact_issue_to_dict(issue)
        issue_messages.append(str(getattr(issue, "message", "") or issue_dict.get("message") or ""))
    summary = "；".join(message for message in issue_messages if message)
    remaining_codes = ", ".join(_source_artifact_blocking_issue_codes(readiness_artifact))
    fragments = [
        f"初始化源头 artifact 未通过准入：{summary or '无可展示摘要'}",
        f"可修复问题 {repair_plan.repairable_count} 个",
        f"不可自动修复问题 {repair_plan.non_repairable_count} 个",
        f"已尝试 {attempts} 轮",
        f"剩余 issue codes: {remaining_codes or 'none'}",
        f"运行日志目录: {ctx.layout.logs_dir}",
    ]
    if stop_reason:
        fragments.insert(4, f"停止原因: {stop_reason}")
    return "；".join(fragments)


async def _persist_source_artifacts_from_init(
    ctx: InitLongServiceContext,
    *,
    project_id: str,
    spec: StorySpec,
    story_bible: StoryBible,
    character_bible: CharacterBible,
    character_system: Any,
    entity_graph: Any,
    style_profile: Any,
    creative_packet: Any,
    blueprint: NarrativeBlueprint,
    outline: StoryOutline,
    narrative_contract: dict[str, Any],
    chapter_contracts: dict[str, Any],
    readiness_report: dict[str, Any],
    coherence_reports: dict[str, dict[str, Any] | None],
    repairs: list[dict[str, Any]],
    outline_ctx: dict[str, Any],
    total_chapters: int,
    post_repair_reaudit: (
        Callable[[dict[str, Any], list[int]], Awaitable[dict[str, Any]]] | None
    ) = None,
) -> dict[str, Any]:
    """Persist canonical source artifacts after init data has stabilized."""

    entity_catalog = build_init_entity_catalog(
        entity_graph,
        character_bible,
        chapter_contracts=chapter_contracts,
    )
    readiness_artifact = persist_init_source_artifacts(
        storage=ctx.storage,
        layout=ctx.layout,
        project_id=project_id,
        spec=spec,
        story_bible=story_bible,
        character_bible=character_bible,
        character_system=character_system,
        entity_graph=entity_graph,
        style_profile=style_profile,
        creative_packet=creative_packet,
        blueprint=blueprint,
        outline=outline,
        narrative_contract=narrative_contract,
        chapter_contracts=chapter_contracts,
        readiness_report=readiness_report,
    )
    _emit_source_artifact_step(ctx, readiness_artifact)
    if readiness_artifact.quality_status == "pass":
        coherence_reports["source_artifacts"] = _source_artifact_readiness_stage_report(
            readiness_artifact,
            attempts=0,
        )
        _save_init_readiness(ctx, reports=coherence_reports, repairs=repairs)
        return chapter_contracts

    max_rounds = _init_source_artifact_repair_rounds(ctx.settings)
    auto_repair = bool(getattr(ctx.settings, "init_source_artifact_auto_repair", True))
    current_contracts = chapter_contracts
    last_issue_keys: set[str] | None = None
    stagnant_rounds = 0
    stop_reason = ""
    attempts_used = 0
    reconciliation_done = False
    reconciled_extra_refs: list[CanonicalEntityRef] = []
    initial_plan = _source_artifact_repair_plan(readiness_artifact)
    coherence_reports["source_artifacts"] = _source_artifact_readiness_stage_report(
        readiness_artifact,
        repair_plan=initial_plan,
        attempts=0,
    )
    _save_init_readiness(ctx, reports=coherence_reports, repairs=repairs)
    if not auto_repair or max_rounds <= 0:
        stop_reason = "auto_repair_disabled" if not auto_repair else "repair_rounds_disabled"
        _record_source_artifact_gate_repair(
            ctx,
            repairs=repairs,
            status="blocked",
            attempt=0,
            before_plan=initial_plan,
            before_readiness=readiness_artifact,
            stop_reason=stop_reason,
        )
        max_rounds = 0
    for attempt in range(1, max_rounds + 1):
        repair_plan = _source_artifact_repair_plan(readiness_artifact)
        if (
            not auto_repair
            or repair_plan.non_repairable_issues
            or not repair_plan.repairable_issues
        ):
            reason = (
                "auto_repair_disabled"
                if not auto_repair
                else "non_repairable_source_artifact_issue"
                if repair_plan.non_repairable_issues
                else "no_repairable_source_artifact_issue"
            )
            _record_source_artifact_gate_repair(
                ctx,
                repairs=repairs,
                status="blocked",
                attempt=attempt,
                before_plan=repair_plan,
                before_readiness=readiness_artifact,
                stop_reason=reason,
            )
            stop_reason = reason
            break
        repair_report = _source_artifact_contract_repair_report(
            readiness_artifact,
            chapter_contracts=current_contracts,
            entity_catalog=entity_catalog,
            repair_plan=repair_plan,
        )
        if repair_report is None:
            stop_reason = "no_repair_report"
            break
        round_index = (
            _init_coherence_repair_round_start(
                ctx.settings,
                repairs,
                artifact=CHAPTER_CONTRACTS_ARTIFACT,
                report=repair_report,
            )
            + 1
        )
        ctx.on_step(
            "init_source_artifacts_repair",
            {
                "round": round_index,
                "attempt": attempt,
                "issues": len(repair_report.get("issues", []) or []),
                "repair_scope": repair_report.get("repair_scope", []),
            },
        )
        attempts_used = attempt
        before_readiness_artifact = readiness_artifact
        local_entity_repair = _apply_source_artifact_entity_alias_repair(
            current_contracts,
            report=repair_report,
            entity_catalog=entity_catalog,
            round_index=round_index,
        )
        ctx.on_step(
            "source_artifacts_repair_targeted",
            {
                "round": round_index,
                "attempt": attempt,
                "status": local_entity_repair.repair_record.get("status"),
                "patch_count": local_entity_repair.repair_record.get("patch_count", 0),
                "skipped_patch_count": local_entity_repair.repair_record.get(
                    "skipped_patch_count",
                    0,
                ),
                "source_issue_ids": local_entity_repair.repair_record.get(
                    "source_issue_ids",
                    [],
                ),
            },
        )
        if local_entity_repair.changed:
            repair_record = local_entity_repair.repair_record
            repairs.append(repair_record)
            ctx.storage.save_json(
                ctx.layout.reports_dir / "init_artifact_repair.json",
                {"repairs": repairs},
            )
            _record_init_repair_manifest_round(ctx, repair_record)
            ctx.on_step("repair_init_artifact_patch", repair_record)
            repaired_payload = local_entity_repair.payload
        else:
            repaired_payload = None
        (
            should_stop,
            stop_reason,
            current_issue_keys,
            stagnant_rounds,
        ) = _init_coherence_repair_stop_decision(
            ctx.settings,
            repair_report,
            previous_issue_keys=last_issue_keys,
            stagnant_rounds=stagnant_rounds,
        )
        if should_stop and repaired_payload is None:
            _record_init_coherence_repair_loop_stop(
                ctx,
                stage="source_artifacts",
                artifact=CHAPTER_CONTRACTS_ARTIFACT,
                report=repair_report,
                reason=stop_reason,
                previous_issue_keys=last_issue_keys,
                current_issue_keys=current_issue_keys,
                stagnant_rounds=stagnant_rounds,
            )
            _record_source_artifact_gate_repair(
                ctx,
                repairs=repairs,
                status="stopped",
                attempt=attempt,
                before_plan=repair_plan,
                before_readiness=readiness_artifact,
                stop_reason=stop_reason,
            )
            break
        last_issue_keys = current_issue_keys
        # ── LLM-driven entity reconciliation (one-shot) ─────────────
        # Runs between deterministic fast-path and LLM repair so the
        # enriched catalog benefits the subsequent LLM repair call.
        if repaired_payload is None and not reconciliation_done:
            reconciliation_done = True
            try:
                outline_payload = (
                    outline.model_dump(mode="json")
                    if hasattr(outline, "model_dump")
                    else (outline if isinstance(outline, dict) else {})
                )
                new_refs, reconcile_record = await reconcile_unresolved_entities(
                    ctx,
                    chapter_contracts=current_contracts,
                    entity_catalog=entity_catalog,
                    repair_plan=repair_plan,
                    outline=outline_payload,
                )
                if reconcile_record.get("resolution_count"):
                    repair_report = _source_repair_report_with_reconciliation(
                        repair_report,
                        reconcile_record,
                    )
                    repairs.append(
                        {
                            "artifact": CHAPTER_CONTRACTS_ARTIFACT,
                            "round": round_index,
                            "status": "applied" if new_refs else "recorded",
                            "repair_type": "entity_reconciliation",
                            **reconcile_record,
                        }
                    )
                if new_refs:
                    reconciled_extra_refs.extend(new_refs)
                    entity_catalog = build_init_entity_catalog(
                        entity_graph,
                        character_bible,
                        chapter_contracts=current_contracts,
                        extra_refs=reconciled_extra_refs,
                    )
                    ctx.on_step(
                        "entity_reconciliation_applied",
                        {"new_ref_count": len(new_refs), "round": round_index},
                    )
                    if _reconciliation_registers_all_unresolved(
                        reconcile_record,
                        repair_plan,
                    ):
                        repaired_payload = current_contracts
                if reconcile_record.get("resolution_count") and repaired_payload is None:
                    reconciled_alias_repair = _apply_source_artifact_entity_alias_repair(
                        current_contracts,
                        report=repair_report,
                        entity_catalog=entity_catalog,
                        round_index=round_index,
                    )
                    if reconciled_alias_repair.changed:
                        repair_record = reconciled_alias_repair.repair_record
                        repair_record["repair_type"] = "entity_reconciliation_alias_patch"
                        repairs.append(repair_record)
                        ctx.storage.save_json(
                            ctx.layout.reports_dir / "init_artifact_repair.json",
                            {"repairs": repairs},
                        )
                        _record_init_repair_manifest_round(ctx, repair_record)
                        ctx.on_step("repair_init_artifact_patch", repair_record)
                        repaired_payload = reconciled_alias_repair.payload
            except Exception as exc:
                _log.warning("entity_reconciliation_integration_failed: %s", exc)
                ctx.on_step(
                    "entity_reconciliation_integration_error",
                    {"error": str(exc)},
                )
        if repaired_payload is None:
            repaired_payload = await _repair_init_artifact_payload(
                ctx,
                artifact=CHAPTER_CONTRACTS_ARTIFACT,
                payload=current_contracts,
                report=repair_report,
                round_index=round_index,
                repairs=repairs,
            )
        contract_repair_outcome = await InitRepairOrchestrator(
            get_init_repair_policy(InitArtifact.CHAPTER_CONTRACTS)
        ).repair(
            repaired_payload,
            InitRepairContext(
                service_ctx=ctx,
                outline_ctx=outline_ctx,
                total_chapters=total_chapters,
                narrative_complexity=spec.narrative_complexity,
                artifacts={
                    "outline": outline,
                    "narrative_contract": narrative_contract,
                    "project_id": project_id,
                    "entity_catalog": entity_catalog,
                },
            ),
        )
        _persist_init_repair_outcome(
            ctx,
            artifact="chapter_contracts",
            outcome=contract_repair_outcome,
        )
        current_contracts = cast(dict[str, Any], contract_repair_outcome.payload)
        contract_coverage = dict(current_contracts.get("coverage") or {})
        if not contract_repair_outcome.report.is_valid:
            raise InitCoherenceError(
                "源头 artifact 修复后章节契约仍未通过："
                f"{'; '.join(contract_repair_outcome.report.errors)}"
            )
        repair_focus_chapters = sorted(
            {
                chapter
                for issue in repair_plan.repairable_issues
                if (chapter := _source_artifact_issue_chapter(issue)) > 0
            }
        )
        if post_repair_reaudit is not None and repair_focus_chapters:
            current_contracts = await post_repair_reaudit(
                current_contracts,
                repair_focus_chapters,
            )
            entity_catalog = build_init_entity_catalog(
                entity_graph,
                character_bible,
                chapter_contracts=current_contracts,
                extra_refs=reconciled_extra_refs or None,
            )
        current_contracts, contract_coverage = _ensure_chapter_contract_coverage(
            current_contracts,
            outline,
            settings=ctx.settings,
        )
        current_contracts["coverage"] = contract_coverage
        _assert_chapter_contracts_ready_to_persist(
            current_contracts,
            contract_coverage,
            allow_local_fallback=True,
        )
        milestone_index = _persist_chapter_contract_runtime_artifacts(
            ctx,
            outline=outline,
            chapter_contracts=current_contracts,
            llm_contract=narrative_contract,
            project_id=project_id,
            entity_catalog=entity_catalog,
        )
        ctx.on_step(
            "init_source_artifacts_repaired",
            {
                "round": round_index,
                "coverage": contract_coverage,
                "milestone_count": len(milestone_index.milestones),
            },
        )
        readiness_report = _save_init_readiness(
            ctx,
            reports=coherence_reports,
            repairs=repairs,
        )
        entity_catalog = build_init_entity_catalog(
            entity_graph,
            character_bible,
            chapter_contracts=current_contracts,
            extra_refs=reconciled_extra_refs or None,
        )
        readiness_artifact = persist_init_source_artifacts(
            storage=ctx.storage,
            layout=ctx.layout,
            project_id=project_id,
            spec=spec,
            story_bible=story_bible,
            character_bible=character_bible,
            character_system=character_system,
            entity_graph=entity_graph,
            style_profile=style_profile,
            creative_packet=creative_packet,
            blueprint=blueprint,
            outline=outline,
            narrative_contract=narrative_contract,
            chapter_contracts=current_contracts,
            readiness_report=readiness_report,
            extra_refs=reconciled_extra_refs or None,
        )
        _emit_source_artifact_step(ctx, readiness_artifact)
        _record_source_artifact_gate_repair(
            ctx,
            repairs=repairs,
            status="resolved" if readiness_artifact.quality_status == "pass" else "residual_errors",
            attempt=attempt,
            before_plan=repair_plan,
            before_readiness=before_readiness_artifact,
            after_readiness=readiness_artifact,
        )
        coherence_reports["source_artifacts"] = _source_artifact_readiness_stage_report(
            readiness_artifact,
            attempts=attempt,
            stop_reason="" if readiness_artifact.quality_status == "pass" else stop_reason,
        )
        _save_init_readiness(ctx, reports=coherence_reports, repairs=repairs)
        if readiness_artifact.quality_status == "pass":
            return current_contracts

    repair_plan = _source_artifact_repair_plan(readiness_artifact)
    coherence_reports["source_artifacts"] = _source_artifact_readiness_stage_report(
        readiness_artifact,
        repair_plan=repair_plan,
        attempts=attempts_used,
        stop_reason=stop_reason,
    )
    _record_init_resume_decision(
        ctx,
        stage="source_artifacts",
        artifact="source_artifacts",
        action="blocked",
        reason=stop_reason or "source_artifacts_readiness_failed",
        path=str(ctx.layout.init_readiness_artifact_path),
        metadata={
            "attempts": attempts_used,
            "remaining_issue_codes": _source_artifact_blocking_issue_codes(readiness_artifact),
            "repairable_issue_count": repair_plan.repairable_count,
            "non_repairable_issue_count": repair_plan.non_repairable_count,
        },
    )
    _save_init_readiness(ctx, reports=coherence_reports, repairs=repairs)
    raise InitCoherenceError(
        _format_source_artifact_failure(
            ctx,
            readiness_artifact=readiness_artifact,
            repair_plan=repair_plan,
            attempts=attempts_used,
            stop_reason=stop_reason,
        )
    )


def _source_artifact_contract_repair_report(
    readiness_artifact: Any,
    *,
    chapter_contracts: dict[str, Any],
    entity_catalog: dict[str, Any],
    repair_plan: SourceArtifactRepairPlan | None = None,
) -> dict[str, Any] | None:
    issues: list[dict[str, Any]] = []
    repair_scope: list[dict[str, Any]] = []
    plan = repair_plan or _source_artifact_repair_plan(readiness_artifact)
    for index, issue in enumerate(plan.repairable_issues):
        chapter = _source_artifact_issue_chapter(issue)
        if chapter <= 0:
            continue
        unresolved = _source_artifact_unresolved_name(issue)
        fields = _contract_fields_containing_value(
            chapter_contracts,
            chapter_number=chapter,
            needle=unresolved,
        )
        if not fields:
            fields = [
                "cognitive_constraints",
                "knowledge_ops",
                "item_ops",
                "relationship_ops",
                *_SOURCE_ARTIFACT_CANDIDATE_FIELDS,
                "required_events",
                "required_progressions",
                "completion_criteria",
                "future_leak_risks",
            ]
        fields = [*fields, *_SOURCE_ARTIFACT_CANDIDATE_FIELDS]
        scope = {
            "artifact": CHAPTER_CONTRACTS_ARTIFACT,
            "chapters": [chapter],
            "fields": sorted(set(fields)),
            "operation": "field_replace",
            "issue_ids": [f"source_artifacts_unresolved_entity_{chapter}_{index}"],
        }
        repair_scope.append(scope)
        issues.append(
            {
                "id": f"source_artifacts_unresolved_entity_{chapter}_{index}",
                "type": "unresolved_contract_entity",
                "severity": str(issue.get("severity") or "critical"),
                "message": issue.get("message")
                or f"第 {chapter} 章契约引用未登记角色/实体：{unresolved}",
                "source": issue.get("source") or "chapter_contract_index",
                "source_path": issue.get("path") or f"by_chapter.{chapter}",
                "evidence": unresolved,
                "unresolved_entity": unresolved,
                "repair_scope": [scope],
            }
        )
    if not issues:
        return None
    catalog_for_prompt = {
        "allowed_entities": entity_catalog.get("allowed_entities", []),
        "policy": entity_catalog.get("policy", ""),
    }
    return {
        "artifact": CHAPTER_CONTRACTS_ARTIFACT,
        "verdict": "needs_repair",
        "issues": issues,
        "source_refs": [
            {
                "source": "init_source_artifacts",
                "artifact": "chapter_contract_index",
                "code": "unresolved_contract_entity",
            }
        ],
        "repair_scope": repair_scope,
        "preserve": [
            "保留未被 source artifact 标记的章节契约字段。",
            (
                "若未登记引用经本章大纲/契约证据确认是新增人物、群体、地点、组织、物品或概念，"
                "必须登记到对应 new_*_candidates 字段，不得只在自然语言字段里裸写新名字。"
            ),
            "不得为了通过校验而删除章节必须发生的 P0 事件。",
        ],
        "change_intent": json.dumps(
            {
                "goal": (
                    "修复章节契约中的未登记角色/实体引用。请根据 entity_catalog 判断："
                    "若未登记引用是已登记实体的别名/误称，改为 canonical_name 或 entity_id；"
                    "若本章大纲、required_events、required_progressions 或 completion_criteria "
                    "证明它是叙事需要的新实体，补入对应 new_*_candidates 并在同章字段使用同一规范名称；"
                    "若既无法确认映射也无新增证据，才移除该引用或改写为不含未登记实体的约束。"
                ),
                "entity_catalog": catalog_for_prompt,
                "candidate_fields": [
                    *_SOURCE_ARTIFACT_CANDIDATE_FIELDS,
                ],
            },
            ensure_ascii=False,
        ),
        "blocked": True,
        "summary": f"source_artifacts 发现 {len(issues)} 个未登记章节契约实体引用，需要修复。",
    }


def _apply_source_artifact_entity_alias_repair(
    chapter_contracts: dict[str, Any],
    *,
    report: dict[str, Any],
    entity_catalog: dict[str, Any],
    round_index: int,
) -> SourceArtifactEntityAliasRepairResult:
    """Locally normalize high-confidence entity near-misses before LLM repair."""

    issues = [
        item
        for item in report.get("issues", []) or []
        if isinstance(item, dict) and item.get("type") == "unresolved_contract_entity"
    ]
    source_issue_ids = sorted(
        {str(item.get("id") or "") for item in issues if str(item.get("id") or "").strip()}
    )
    patched = copy.deepcopy(chapter_contracts)
    patches: list[dict[str, Any]] = []
    skipped_patches: list[dict[str, Any]] = []
    reconciled_remove_names = _source_reconciled_remove_names(report)

    for issue in issues:
        issue_id = str(issue.get("id") or "").strip()
        unresolved = str(issue.get("unresolved_entity") or issue.get("evidence") or "").strip()
        if not issue_id or not unresolved:
            continue
        remove_by_reconciliation = unresolved in reconciled_remove_names
        if _is_unknown_entity_placeholder(unresolved) or remove_by_reconciliation:
            changed_paths: list[str] = []
            scopes = (
                issue.get("repair_scope") if isinstance(issue.get("repair_scope"), list) else []
            )
            for scope in scopes:
                if not isinstance(scope, dict):
                    continue
                chapters = [
                    _positive_int(chapter)
                    for chapter in _init_coherence_list(scope.get("chapters"))
                    if _positive_int(chapter) > 0
                ]
                fields = [
                    str(field).strip()
                    for field in _init_coherence_list(scope.get("fields"))
                    if str(field).strip()
                ]
                for chapter in chapters:
                    contract, contract_index = _chapter_contract_entry_by_number(
                        patched,
                        chapter,
                    )
                    if not contract:
                        continue
                    for field in fields:
                        if field not in contract:
                            continue
                        base_path = _source_artifact_json_pointer(
                            ["chapter_contracts", str(contract_index), field]
                        )
                        new_value, field_paths = _clear_placeholder_entity_reference_in_payload(
                            contract[field],
                            placeholder=unresolved,
                            path=base_path,
                            field_path=(field,),
                            allow_explicit_reference=remove_by_reconciliation,
                        )
                        if field_paths:
                            contract[field] = new_value
                            changed_paths.extend(field_paths)

            if not changed_paths:
                skipped_patches.append(
                    {
                        "issue_ids": [issue_id],
                        "unresolved_entity": unresolved,
                        "reason": "placeholder_entity_not_found_in_scope",
                    }
                )
                continue
            patches.append(
                {
                    "path": sorted(set(changed_paths))[0],
                    "paths": sorted(set(changed_paths)),
                    "issue_ids": [issue_id],
                    "unresolved_entity": unresolved,
                    "operation": (
                        "clear_reconciled_entity_reference"
                        if remove_by_reconciliation
                        else "clear_placeholder_entity_reference"
                    ),
                    "confidence": 1.0,
                    "precondition": {"old_value_is_placeholder": unresolved},
                }
            )
            continue

        match = _resolve_source_artifact_entity_alias(unresolved, entity_catalog)
        if match is None:
            skipped_patches.append(
                {
                    "issue_ids": [issue_id],
                    "unresolved_entity": unresolved,
                    "reason": "no_confident_entity_mapping",
                }
            )
            continue
        canonical = str(match.get("canonical_name") or "").strip()
        if not canonical or canonical == unresolved:
            skipped_patches.append(
                {
                    "issue_ids": [issue_id],
                    "unresolved_entity": unresolved,
                    "reason": "canonical_mapping_not_actionable",
                }
            )
            continue

        changed_paths: list[str] = []
        scopes = issue.get("repair_scope") if isinstance(issue.get("repair_scope"), list) else []
        for scope in scopes:
            if not isinstance(scope, dict):
                continue
            chapters = [
                _positive_int(chapter)
                for chapter in _init_coherence_list(scope.get("chapters"))
                if _positive_int(chapter) > 0
            ]
            fields = [
                str(field).strip()
                for field in _init_coherence_list(scope.get("fields"))
                if str(field).strip()
            ]
            for chapter in chapters:
                contract, contract_index = _chapter_contract_entry_by_number(
                    patched,
                    chapter,
                )
                if not contract:
                    continue
                for field in fields:
                    if field not in contract:
                        continue
                    base_path = _source_artifact_json_pointer(
                        ["chapter_contracts", str(contract_index), field]
                    )
                    new_value, field_paths = _replace_entity_alias_in_payload(
                        contract[field],
                        unresolved=unresolved,
                        canonical=canonical,
                        path=base_path,
                    )
                    if field_paths:
                        contract[field] = new_value
                        changed_paths.extend(field_paths)

        if not changed_paths:
            skipped_patches.append(
                {
                    "issue_ids": [issue_id],
                    "unresolved_entity": unresolved,
                    "canonical_name": canonical,
                    "reason": "unresolved_entity_not_found_in_scope",
                }
            )
            continue
        patches.append(
            {
                "path": sorted(set(changed_paths))[0],
                "paths": sorted(set(changed_paths)),
                "issue_ids": [issue_id],
                "unresolved_entity": unresolved,
                "canonical_name": canonical,
                "entity_id": match.get("entity_id", ""),
                "match_reason": match.get("match_reason", ""),
                "confidence": _source_artifact_match_confidence(
                    str(match.get("match_reason") or "")
                ),
                "operation": "replace_entity_alias",
                "precondition": {"old_value_contains": unresolved},
            }
        )

    changed = bool(patches)
    repair_record = {
        "artifact": CHAPTER_CONTRACTS_ARTIFACT,
        "round": round_index,
        "status": "applied" if changed else "no_effect",
        "repair_type": "deterministic_source_entity_alias",
        "source_issue_ids": source_issue_ids,
        "source_blocking_issue_count": len(issues),
        "patch_count": len(patches),
        "skipped_patch_count": len(skipped_patches),
        "patches": patches,
        "skipped_patches": skipped_patches,
        "summary": (
            f"本地修复 {len(patches)} 个 source artifact 未登记实体引用；"
            f"跳过 {len(skipped_patches)} 个无唯一映射项。"
        ),
    }
    return SourceArtifactEntityAliasRepairResult(
        payload=patched if changed else chapter_contracts,
        changed=changed,
        repair_record=repair_record,
    )


def _source_artifact_match_confidence(match_reason: str) -> float:
    reason = str(match_reason or "").strip()
    if reason == "catalog_alias":
        return 1.0
    if reason.startswith("collective_suffix:"):
        return 0.95
    if reason.startswith("unique_entity_id_near_match"):
        return 0.95
    if reason.startswith("unique_near_match") or reason.startswith("unique_character_near_match"):
        return 0.9
    if reason == "unique_single_character_prefix":
        return 0.85
    return 0.8


# ── Entity Reconciliation (LLM-driven) ──────────────────────────────────

_RECONCILE_CONFIDENCE_THRESHOLD = 0.7


def _extract_unresolved_names_from_plan(
    repair_plan: SourceArtifactRepairPlan,
) -> list[str]:
    """Extract unique unresolved entity names from a repair plan."""
    seen: set[str] = set()
    names: list[str] = []
    for issue in repair_plan.repairable_issues:
        name = _source_artifact_unresolved_name(issue)
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _build_reconciliation_chapter_context(
    chapter_contracts: dict[str, Any],
    repair_plan: SourceArtifactRepairPlan,
    outline: dict[str, Any] | None = None,
) -> str:
    """Build a compact chapter context string for the reconciliation prompt."""
    affected_chapters: set[int] = set()
    for issue in repair_plan.repairable_issues:
        ch = _source_artifact_issue_chapter(issue)
        if ch > 0:
            affected_chapters.add(ch)
    if not affected_chapters:
        return "(无受影响的章节上下文)"

    lines: list[str] = []
    for ch_num in sorted(affected_chapters):
        contract, _ = _chapter_contract_entry_by_number(chapter_contracts, ch_num)
        if not contract:
            continue
        summary = str(contract.get("summary") or contract.get("premise") or "").strip()
        pov = contract.get("pov_character") or ""
        required = contract.get("required_characters") or []
        events = contract.get("required_events") or []
        new_chars = contract.get("new_character_candidates") or []
        new_entities = contract.get("new_entity_candidates") or []

        outline_ch = ""
        if isinstance(outline, dict):
            chapters = outline.get("chapters", [])
            if isinstance(chapters, list):
                for item in chapters:
                    if (
                        isinstance(item, dict)
                        and _positive_int(item.get("chapter_number")) == ch_num
                    ):
                        outline_ch = str(item.get("summary") or item.get("premise") or "").strip()
                        break

        lines.append(f"### 第 {ch_num} 章")
        if outline_ch:
            lines.append(f"大纲摘要：{outline_ch}")
        if summary:
            lines.append(f"契约摘要：{summary}")
        if pov:
            lines.append(f"POV：{pov}")
        if required:
            lines.append(f"必要角色：{', '.join(str(r) for r in required if r)}")
        if events:
            event_strs = [str(e) for e in events if e][:5]
            lines.append(f"关键事件：{'; '.join(event_strs)}")
        if new_chars:
            lines.append(f"新角色候选：{', '.join(str(c) for c in new_chars if c)}")
        if new_entities:
            lines.append(f"新实体候选：{', '.join(str(e) for e in new_entities if e)}")
    return "\n".join(lines) if lines else "(无受影响的章节上下文)"


def _coerce_reconciliation_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))


def _source_repair_report_with_reconciliation(
    repair_report: dict[str, Any],
    reconcile_record: dict[str, Any],
) -> dict[str, Any]:
    """Append reconciliation evidence to the existing source-artifact repair intent."""

    updated = copy.deepcopy(repair_report)
    raw_intent = str(updated.get("change_intent") or "")
    try:
        intent: dict[str, Any] = json.loads(raw_intent) if raw_intent else {}
    except json.JSONDecodeError:
        intent = {"goal": raw_intent}
    if not isinstance(intent, dict):
        intent = {"goal": raw_intent}

    intent["entity_reconciliation"] = {
        "status": reconcile_record.get("status"),
        "accepted": reconcile_record.get("accepted", []),
        "rejected": reconcile_record.get("rejected", []),
        "new_refs": reconcile_record.get("new_refs", []),
        "instructions": (
            "Follow high-confidence accepted decisions: alias means rewrite the "
            "unresolved reference to canonical_name; new_entity means preserve "
            "the reference and register/use it consistently; remove means remove "
            "or rewrite only the invalid reference without deleting required P0 events."
        ),
    }
    updated["change_intent"] = json.dumps(intent, ensure_ascii=False)
    return updated


def _source_reconciled_remove_names(report: dict[str, Any]) -> set[str]:
    """Return high-confidence unresolved names approved for structural removal."""

    raw_intent = str(report.get("change_intent") or "").strip()
    if not raw_intent:
        return set()
    try:
        intent = json.loads(raw_intent)
    except (json.JSONDecodeError, TypeError):
        return set()
    if not isinstance(intent, dict):
        return set()
    reconciliation = intent.get("entity_reconciliation")
    if not isinstance(reconciliation, dict):
        return set()
    accepted = reconciliation.get("accepted")
    if not isinstance(accepted, list):
        return set()
    result: set[str] = set()
    for decision in accepted:
        if not isinstance(decision, dict):
            continue
        if str(decision.get("resolution") or "").strip() != "remove":
            continue
        try:
            confidence = float(decision.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        name = str(decision.get("unresolved_name") or "").strip()
        if name and confidence >= 0.8:
            result.add(name)
    return result


def _reconciliation_registers_all_unresolved(
    reconcile_record: dict[str, Any],
    repair_plan: SourceArtifactRepairPlan,
) -> bool:
    unresolved = set(_extract_unresolved_names_from_plan(repair_plan))
    if not unresolved:
        return False
    accepted = [
        item for item in reconcile_record.get("accepted", []) or [] if isinstance(item, dict)
    ]
    if reconcile_record.get("rejected"):
        return False
    registered = {
        str(item.get("unresolved_name") or "").strip()
        for item in accepted
        if item.get("resolution") == "new_entity"
    }
    return registered == unresolved


async def reconcile_unresolved_entities(
    ctx: InitLongServiceContext,
    *,
    chapter_contracts: dict[str, Any],
    entity_catalog: dict[str, Any],
    repair_plan: SourceArtifactRepairPlan,
    outline: dict[str, Any] | None = None,
) -> tuple[list[CanonicalEntityRef], dict[str, Any]]:
    """LLM-driven entity reconciliation for unresolved chapter-contract references.

    Returns a tuple of (new_refs, reconciliation_record).
    ``new_refs`` contains ``CanonicalEntityRef`` objects for entities that should
    be added to the catalog. Alias decisions update ``entity_catalog`` in place
    so the next deterministic repair pass can rewrite references.
    """
    unresolved_names = _extract_unresolved_names_from_plan(repair_plan)
    if not unresolved_names:
        return [], {"status": "no_unresolved", "resolution_count": 0}

    chapter_context = _build_reconciliation_chapter_context(chapter_contracts, repair_plan, outline)
    candidate_packs, _catalog_cards, catalog_revision = await build_entity_candidate_packs(
        ctx,
        entity_catalog=entity_catalog,
        mentions=unresolved_names,
        purpose="source_artifact_entity_reconciliation",
    )
    candidates_by_name: dict[str, list[dict[str, str]]] = {}
    candidate_entity_ids: set[str] = set()
    for unresolved_name, pack in zip(unresolved_names, candidate_packs, strict=True):
        candidates: list[dict[str, str]] = []
        for card in pack.evidence_cards:
            if card.kind != "entity":
                continue
            for entity_id in card.entity_ids:
                candidate_entity_ids.add(entity_id)
                candidates.append(
                    {
                        "entity_id": entity_id,
                        "canonical_name": card.canonical_name,
                        "source_ref": card.source_ref,
                    }
                )
        candidates_by_name[unresolved_name] = candidates
    catalog_for_prompt = {
        "allowed_entities": [
            entity
            for entity in entity_catalog.get("allowed_entities", [])
            if str(entity.get("entity_id") or "") in candidate_entity_ids
        ],
    }
    retrieval_view = {
        "catalog_revision": catalog_revision,
        "fixed_registry_storage": "structured_source_of_truth",
        "fixed_registry_vectorized": False,
        "candidates_by_unresolved_name": candidates_by_name,
        "evidence_packs": [pack.model_dump(mode="json") for pack in candidate_packs],
    }
    ctx.on_step(
        "entity_reconciliation_start",
        {
            "unresolved_count": len(unresolved_names),
            "unresolved_names": unresolved_names[:20],
        },
    )
    try:
        response = await ctx.call_with_retry(
            TaskType.RECONCILE_ENTITIES,
            {
                "unresolved_names": unresolved_names,
                "entity_catalog": catalog_for_prompt,
                "entity_retrieval": retrieval_view,
                "chapter_context": chapter_context,
            },
            max_tokens=calculate_route_aware_max_tokens(
                ctx.router,
                TaskType.RECONCILE_ENTITIES,
                4096,
                prompt_overhead=6000,
                min_tokens=2048,
            ),
            temperature=getattr(ctx.settings, "temp_reconcile_entities", 0.2),
            max_retries=2,
        )
    except Exception as exc:
        _log.warning("entity_reconciliation_failed: %s", exc)
        ctx.on_step(
            "entity_reconciliation_error",
            {"error": str(exc)},
        )
        return [], {
            "status": "llm_failed",
            "error": str(exc),
            "resolution_count": 0,
        }

    resolutions: list[Any] = []
    if isinstance(response, dict):
        resolutions = response.get("resolutions") or []
    if not isinstance(resolutions, list):
        resolutions = []

    new_refs: list[CanonicalEntityRef] = []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    expected_names = set(unresolved_names)
    seen_names: set[str] = set()
    alias_to_entity = entity_catalog.setdefault("alias_to_entity", {})
    if not isinstance(alias_to_entity, dict):
        alias_to_entity = {}
        entity_catalog["alias_to_entity"] = alias_to_entity

    for res in resolutions:
        if not isinstance(res, dict):
            continue
        resolution = str(res.get("resolution") or "").strip()
        unresolved = str(res.get("unresolved_name") or "").strip()
        if not resolution or not unresolved:
            rejected.append({**res, "reason": "missing_fields"})
            continue
        if unresolved not in expected_names:
            rejected.append({**res, "reason": "unrequested_unresolved_name"})
            continue
        if unresolved in seen_names:
            rejected.append({**res, "reason": "duplicate_unresolved_name"})
            continue
        seen_names.add(unresolved)
        confidence = _coerce_reconciliation_confidence(res.get("confidence"))
        if confidence < _RECONCILE_CONFIDENCE_THRESHOLD:
            rejected.append({**res, "reason": "below_threshold"})
            continue

        if resolution == "alias":
            canonical = str(res.get("canonical_name") or "").strip()
            if not canonical:
                rejected.append({**res, "reason": "missing_canonical"})
                continue
            existing = None
            for ent in entity_catalog.get("allowed_entities", []):
                if ent.get("canonical_name") == canonical:
                    existing = ent
                    break
            if existing is None:
                rejected.append({**res, "reason": "canonical_not_in_catalog"})
                continue
            allowed_for_mention = {
                item["canonical_name"]
                for item in candidates_by_name.get(unresolved, [])
                if item.get("canonical_name")
            }
            if canonical not in allowed_for_mention:
                rejected.append({**res, "reason": "canonical_not_in_retrieved_candidates"})
                continue
            entity_type = str(existing.get("entity_type") or "unknown")
            entity_id = str(existing.get("entity_id") or "")
            # Register the alias in the catalog for downstream resolution
            if unresolved not in alias_to_entity:
                alias_to_entity[unresolved] = {
                    "entity_id": entity_id,
                    "canonical_name": canonical,
                    "entity_type": entity_type,
                }
            accepted.append(
                {
                    "unresolved_name": unresolved,
                    "resolution": "alias",
                    "canonical_name": canonical,
                    "entity_id": entity_id,
                    "confidence": confidence,
                }
            )
        elif resolution == "new_entity":
            entity_type = str(res.get("entity_type") or "unknown").strip()
            if entity_type not in (
                "character",
                "location",
                "item",
                "organization",
                "concept",
            ):
                entity_type = "unknown"
            entity_id = _derived_entity_id(unresolved, entity_type)
            new_ref = CanonicalEntityRef(
                entity_id=entity_id,
                canonical_name=unresolved,
                entity_type=entity_type,
                aliases=[],
            )
            new_refs.append(new_ref)
            accepted.append(
                {
                    "unresolved_name": unresolved,
                    "resolution": "new_entity",
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "confidence": confidence,
                }
            )
        elif resolution == "remove":
            accepted.append(
                {
                    "unresolved_name": unresolved,
                    "resolution": "remove",
                    "confidence": confidence,
                }
            )
        else:
            rejected.append({**res, "reason": f"unknown_resolution_{resolution}"})

    for missing_name in unresolved_names:
        if missing_name not in seen_names:
            rejected.append(
                {
                    "unresolved_name": missing_name,
                    "reason": "missing_llm_resolution",
                }
            )

    ctx.on_step(
        "entity_reconciliation_complete",
        {
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "new_entity_count": len(new_refs),
        },
    )
    record = {
        "status": "completed",
        "accepted": accepted,
        "rejected": rejected,
        "new_refs": [ref.model_dump(mode="json") for ref in new_refs],
        "retrieval": {
            "catalog_revision": catalog_revision,
            "fixed_registry_vectorized": False,
            "candidate_counts": {
                name: len(candidates) for name, candidates in candidates_by_name.items()
            },
        },
        "resolution_count": len(accepted),
    }
    return new_refs, record


def _resolve_source_artifact_entity_alias(
    unresolved: str,
    entity_catalog: dict[str, Any],
) -> dict[str, Any] | None:
    """Apply only an exact alias decision already present in the catalog.

    The catalog may be updated by ``RECONCILE_ENTITIES``.  This function is a
    mechanical patch executor, not an identity matcher: spelling distance,
    prefixes, suffixes, and name-shape guesses are intentionally unsupported.
    """
    token = str(unresolved or "").strip()
    if not token:
        return None

    alias_to_entity = entity_catalog.get("alias_to_entity")
    if isinstance(alias_to_entity, dict):
        exact = alias_to_entity.get(token)
        if isinstance(exact, dict):
            canonical = str(exact.get("canonical_name") or "").strip()
            if canonical and canonical != token:
                return {
                    "entity_id": str(exact.get("entity_id") or ""),
                    "canonical_name": canonical,
                    "entity_type": str(exact.get("entity_type") or ""),
                    "match_reason": "catalog_alias",
                }
    return None


def _replace_entity_alias_in_payload(
    value: Any,
    *,
    unresolved: str,
    canonical: str,
    path: str,
) -> tuple[Any, list[str]]:
    if isinstance(value, str):
        replaced = _replace_entity_alias_in_text(value, unresolved, canonical)
        return replaced, [path] if replaced != value else []
    if isinstance(value, list):
        changed_paths: list[str] = []
        result: list[Any] = []
        for index, item in enumerate(value):
            item_path = _source_artifact_json_pointer(
                [*_split_source_artifact_pointer(path), str(index)]
            )
            replaced, item_paths = _replace_entity_alias_in_payload(
                item,
                unresolved=unresolved,
                canonical=canonical,
                path=item_path,
            )
            result.append(replaced)
            changed_paths.extend(item_paths)
        return result, changed_paths
    if isinstance(value, dict):
        changed_paths = []
        result: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key) if isinstance(key, str) else key
            new_key = (
                _replace_entity_alias_in_text(key_text, unresolved, canonical)
                if isinstance(key_text, str)
                else key_text
            )
            key_path = _source_artifact_json_pointer(
                [*_split_source_artifact_pointer(path), str(key)]
            )
            replaced, item_paths = _replace_entity_alias_in_payload(
                item,
                unresolved=unresolved,
                canonical=canonical,
                path=key_path,
            )
            if new_key != key:
                changed_paths.append(key_path)
            if new_key in result and new_key != key:
                result[new_key] = _prefer_non_empty_payload(result[new_key], replaced)
            else:
                result[new_key] = replaced
            changed_paths.extend(item_paths)
        return result, changed_paths
    return value, []


def _replace_entity_alias_in_text(text: str, unresolved: str, canonical: str) -> str:
    if not unresolved or unresolved == canonical:
        return text
    if len(unresolved) == 1:
        return canonical if text.strip() == unresolved else text
    return text.replace(unresolved, canonical)


def _clear_placeholder_entity_reference_in_payload(
    value: Any,
    *,
    placeholder: str,
    path: str,
    field_path: tuple[str, ...],
    allow_explicit_reference: bool = False,
) -> tuple[Any, list[str]]:
    if isinstance(value, str):
        if (
            is_chapter_contract_entity_ref_path(field_path)
            and value.strip() == placeholder
            and (allow_explicit_reference or _is_unknown_entity_placeholder(value))
        ):
            return "", [path]
        return value, []
    if isinstance(value, list):
        changed_paths: list[str] = []
        result: list[Any] = []
        for index, item in enumerate(value):
            item_path = _source_artifact_json_pointer(
                [*_split_source_artifact_pointer(path), str(index)]
            )
            replaced, item_paths = _clear_placeholder_entity_reference_in_payload(
                item,
                placeholder=placeholder,
                path=item_path,
                field_path=field_path,
                allow_explicit_reference=allow_explicit_reference,
            )
            changed_paths.extend(item_paths)
            if item_paths and isinstance(item, str) and replaced == "":
                continue
            result.append(replaced)
        return result, changed_paths
    if isinstance(value, dict):
        changed_paths: list[str] = []
        result: dict[Any, Any] = {}
        is_entity_map = is_chapter_contract_entity_ref_map_path(field_path)
        for key, item in value.items():
            key_path = _source_artifact_json_pointer(
                [*_split_source_artifact_pointer(path), str(key)]
            )
            if (
                isinstance(key, str)
                and is_entity_map
                and key.strip() == placeholder
                and (allow_explicit_reference or _is_unknown_entity_placeholder(key))
            ):
                changed_paths.append(key_path)
                continue
            next_path = field_path if is_entity_map else (*field_path, str(key))
            replaced, item_paths = _clear_placeholder_entity_reference_in_payload(
                item,
                placeholder=placeholder,
                path=key_path,
                field_path=next_path,
                allow_explicit_reference=allow_explicit_reference,
            )
            result[key] = replaced
            changed_paths.extend(item_paths)
        return result, changed_paths
    return value, []


def _prefer_non_empty_payload(existing: Any, incoming: Any) -> Any:
    if existing in (None, "", [], {}):
        return incoming
    return existing


def _source_artifact_json_pointer(parts: list[str]) -> str:
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(escaped)


def _split_source_artifact_pointer(path: str) -> list[str]:
    if not path or path == "/":
        return []
    return [part.replace("~1", "/").replace("~0", "~") for part in path.split("/")[1:]]


def _source_artifact_issue_chapter(issue: dict[str, Any]) -> int:
    path = str(issue.get("path") or "")
    match = re.search(r"by_chapter\.(\d+)", path)
    if match:
        return int(match.group(1))
    message = str(issue.get("message") or "")
    match = re.search(r"第\s*(\d+)\s*章", message)
    return int(match.group(1)) if match else 0


def _source_artifact_unresolved_name(issue: dict[str, Any]) -> str:
    message = str(issue.get("message") or "")
    if "：" in message:
        return message.rsplit("：", 1)[-1].strip()
    return str(issue.get("unresolved_entity") or issue.get("evidence") or "").strip()


def _contract_fields_containing_value(
    chapter_contracts: dict[str, Any],
    *,
    chapter_number: int,
    needle: str,
) -> list[str]:
    if not needle:
        return []
    contract = _chapter_contract_by_number(chapter_contracts, chapter_number)
    if not contract:
        return []
    fields: list[str] = []
    for field, value in contract.items():
        if field in {"chapter_number", "title", "source"}:
            continue
        if _payload_contains_text(value, needle):
            fields.append(str(field))
    return fields


def _chapter_contract_by_number(
    chapter_contracts: dict[str, Any],
    chapter_number: int,
) -> dict[str, Any]:
    contract, _ = _chapter_contract_entry_by_number(chapter_contracts, chapter_number)
    return contract


def _chapter_contract_entry_by_number(
    chapter_contracts: dict[str, Any],
    chapter_number: int,
) -> tuple[dict[str, Any], int]:
    items = chapter_contracts.get("chapter_contracts")
    if not isinstance(items, list):
        return {}, -1
    for index, item in enumerate(items):
        if isinstance(item, dict) and _positive_int(item.get("chapter_number")) == chapter_number:
            return item, index
    return {}, -1


def _payload_contains_text(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, list):
        return any(_payload_contains_text(item, needle) for item in value)
    if isinstance(value, dict):
        return any(
            needle in str(key) or _payload_contains_text(item, needle)
            for key, item in value.items()
        )
    return False


def _load_init_artifact_repairs(ctx: InitLongServiceContext) -> list[dict[str, Any]]:
    payload = _load_optional_json(ctx, ctx.layout.reports_dir / "init_artifact_repair.json")
    repairs = payload.get("repairs") if isinstance(payload, dict) else None
    if not isinstance(repairs, list):
        return []
    return [dict(item) for item in repairs if isinstance(item, dict)]
