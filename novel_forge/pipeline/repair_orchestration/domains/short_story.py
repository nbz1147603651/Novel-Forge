"""Short-story repair adapters for Repair Orchestration v2."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from novel_forge.core.schemas.audit import AuditIssueV2, ResolvedRepairTarget
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.repair import (
    RepairArtifactSnapshot,
    RepairPatchRecord,
    RepairValidatorResult,
    RepairVerificationBundle,
)
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.persistence.repair_case_store import RepairCaseStore, repair_content_hash
from novel_forge.pipeline.repair_orchestration.audit_events import (
    emit_repair_audit_event,
    issue_audit_payload,
    text_hash,
)
from novel_forge.pipeline.repair_orchestration.domains._shared import (
    record_domain_repair_ledger,
    resolve_mode,
    restore_text_path,
    snapshot_text_path,
    text_change_ratio,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairArtifactChange,
    RepairControlMode,
    RepairDomain,
    RepairExecutionResult,
    RepairMission,
    RepairPlanCandidate,
    RepairStrategy,
    RepairSurface,
    RepairTarget,
    RepairVerificationResult,
)
from novel_forge.pipeline.repair_orchestration.orchestrator import RepairOrchestrator
from novel_forge.pipeline.repair_orchestration.plugins import (
    RepairCandidateDraft,
    RepairCandidateMaterial,
    RepairPluginRegistry,
)
from novel_forge.pipeline.repair_orchestration.registry import RepairHandlerRegistry
from novel_forge.pipeline.repair_orchestration.snapshot import RepairSnapshotStore

ShortRepairKind = Literal["completion", "quality"]


@dataclass(frozen=True)
class ShortCandidateRepairOutcome:
    """Exact candidate and the independent full evaluation that accepted it."""

    text: str
    eval_report: EvalReport
    case_id: str = ""
    applied: bool = False


def _aggregate_short_issue(issues: tuple[AuditIssueV2, ...]) -> AuditIssueV2:
    if not issues:
        raise ValueError("short candidate repair requires structured issues")
    first = issues[0]
    summary = "；".join(issue.summary for issue in issues[:4])
    return first.model_copy(
        update={
            "issue_id": "short-batch-"
            + hashlib.sha256(
                "|".join(issue.issue_id for issue in issues).encode("utf-8")
            ).hexdigest()[:20],
            "severity": (
                "high"
                if any(issue.severity in {"critical", "high"} for issue in issues)
                else "medium"
            ),
            "blocking": any(issue.blocking for issue in issues),
            "summary": summary,
            "description": "\n".join(issue.description for issue in issues),
            "evidence": [evidence for issue in issues for evidence in issue.evidence][:8],
            "metadata": {
                **first.metadata,
                "batched_issue_ids": [issue.issue_id for issue in issues],
                "structured_issues": [
                    issue.model_dump(mode="json") for issue in issues
                ],
            },
        }
    )


class ShortStoryCandidatePlugin:
    """Candidate-safe short repair backed by the original full evaluator."""

    name = "short_story_candidate_v1"
    content_types: Sequence[str] = ("short_story",)
    candidate_safe: Literal[True] = True

    def __init__(
        self,
        *,
        runner: Any,
        layout: Any,
        spec: Any,
        beats: Any,
        blueprint: Any | None,
        execution_plan: dict[str, Any],
        baseline_text: str,
        baseline_eval: EvalReport,
        baseline_diagnosis: Any,
        repair_kind: ShortRepairKind,
        issues: list[str],
        pass_label: str,
        iteration: int,
        aggregate_issue: AuditIssueV2,
    ) -> None:
        self.runner = runner
        self.layout = layout
        self.spec = spec
        self.beats = beats
        self.blueprint = blueprint
        self.execution_plan = execution_plan
        self.baseline_text = baseline_text
        self.baseline_eval = baseline_eval
        self.baseline_diagnosis = baseline_diagnosis
        self.repair_kind = repair_kind
        self.issues = issues
        self.pass_label = pass_label
        self.iteration = iteration
        self.aggregate_issue = aggregate_issue

    async def audit(self, snapshot: RepairArtifactSnapshot) -> list[AuditIssueV2]:
        if snapshot.source_hash != repair_content_hash(self.baseline_text):
            raise ValueError("short repair snapshot does not match immutable baseline")
        return [self.aggregate_issue]

    def locate(
        self,
        issue: AuditIssueV2,
        snapshot: RepairArtifactSnapshot,
    ) -> list[ResolvedRepairTarget]:
        locator = issue.repair_targets[0]
        return [
            ResolvedRepairTarget(
                target_id=f"{issue.issue_id}:story",
                target_format="prose_text",
                surface="short_text",
                locator=locator,
                path="",
                window={"char_start": 0, "char_end": len(self.baseline_text)},
                current_value=snapshot.payload,
                current_hash=snapshot.source_hash,
                issue_ids=[issue.issue_id],
                allowed_operation="window_rewrite",
                confidence=1.0,
            )
        ]

    async def propose(
        self,
        issue: AuditIssueV2,
        targets: Sequence[ResolvedRepairTarget],
        snapshot: RepairArtifactSnapshot,
    ) -> RepairCandidateDraft:
        del issue
        if len(targets) != 1 or snapshot.payload != self.baseline_text:
            raise ValueError("short repair candidate requires one unchanged story target")
        from novel_forge.pipeline.short.stages.edit import (
            run_completion_repair,
            run_quality_repair,
        )

        if self.repair_kind == "completion":
            candidate_text = await run_completion_repair(
                self.runner,
                self.layout,
                self.spec,
                self.beats,
                self.blueprint,
                self.execution_plan,
                self.baseline_text,
                self.issues,
                pass_label=self.pass_label,
                iteration=self.iteration,
                eval_report=self.baseline_eval,
                persist_draft=False,
            )
        else:
            candidate_text = await run_quality_repair(
                self.runner,
                self.layout,
                self.spec,
                self.beats,
                self.blueprint,
                self.execution_plan,
                self.baseline_text,
                self.issues,
                pass_label=self.pass_label,
                iteration=self.iteration,
                eval_report=self.baseline_eval,
                persist_draft=False,
            )
        target = targets[0]
        patches: tuple[RepairPatchRecord, ...] = ()
        if candidate_text != self.baseline_text:
            patches = (
                RepairPatchRecord(
                    target_id=target.target_id,
                    expected_hash=target.current_hash,
                    replacement_hash=repair_content_hash(candidate_text),
                    operation="window_rewrite",
                    field_path="$text",
                    rationale=f"short_{self.repair_kind}_candidate",
                ),
            )
        return RepairCandidateDraft(
            payload=candidate_text,
            origin="model",
            patches=patches,
            change_ratio=text_change_ratio(self.baseline_text, candidate_text),
            protected_items=(
                "作者硬意图",
                "人物与关系",
                "POV",
                "结局",
                "世界规则",
                "locked 要素",
            ),
            metadata={"repair_kind": self.repair_kind, "iteration": self.iteration},
        )

    async def verify(
        self,
        candidate: RepairCandidateMaterial,
        issues: Sequence[AuditIssueV2],
    ) -> RepairVerificationBundle:
        from novel_forge.pipeline.short.stages.adaptive_revision import (
            diagnose_short_revision,
        )
        from novel_forge.pipeline.short.stages.evaluate import run_final_evaluation

        candidate_text = str(candidate.payload or "")
        candidate_eval = await run_final_evaluation(
            self.runner,
            self.layout,
            self.spec,
            self.beats,
            self.blueprint,
            self.execution_plan,
            candidate_text,
            require_intent_compliance=True,
            persist=False,
            event_name="short_repair_candidate_verification",
        )
        candidate_diagnosis = diagnose_short_revision(
            self.runner,
            self.spec,
            self.execution_plan,
            candidate_text,
            candidate_eval,
            attempted_repair=True,
        )
        improved = candidate_diagnosis.rank() < self.baseline_diagnosis.rank()
        completeness_passed = bool(candidate_diagnosis.completeness.get("passed"))
        intent_passed = (
            candidate_diagnosis.intent_score is not None
            and candidate_diagnosis.intent_score >= 9.0
        )
        source_bound = candidate_eval.source_text_hash == source_text_hash(candidate_text)
        passed = bool(candidate_text.strip()) and all(
            (improved, completeness_passed, intent_passed, source_bound)
        )
        original_ids = [issue.issue_id for issue in issues]
        regressions: list[str] = []
        if candidate_diagnosis.hard_issue_count > self.baseline_diagnosis.hard_issue_count:
            regressions.append("short_hard_issue_regression")
        if (
            self.baseline_diagnosis.intent_score is not None
            and candidate_diagnosis.intent_score is not None
            and candidate_diagnosis.intent_score < self.baseline_diagnosis.intent_score
        ):
            regressions.append("short_intent_regression")
        if regressions:
            passed = False
        validators = [
            RepairValidatorResult(
                validator_id="short_full_evaluation_v1",
                passed=source_bound,
                details=[f"overall={candidate_eval.overall_score:.2f}"],
                evidence={"source_text_hash": candidate_eval.source_text_hash},
            ),
            RepairValidatorResult(
                validator_id="short_candidate_improvement_v1",
                passed=improved,
                details=[
                    f"baseline_rank={self.baseline_diagnosis.rank()}",
                    f"candidate_rank={candidate_diagnosis.rank()}",
                ],
            ),
            RepairValidatorResult(
                validator_id="short_completeness_v1",
                passed=completeness_passed,
                details=list(candidate_diagnosis.completeness.get("issues") or ()),
            ),
            RepairValidatorResult(
                validator_id="short_intent_compliance_v1",
                passed=intent_passed,
                details=[f"score={candidate_diagnosis.intent_score}"],
            ),
        ]
        return RepairVerificationBundle(
            case_id=candidate.candidate.case_id,
            candidate_version=candidate.candidate.version,
            candidate_hash=candidate.candidate.candidate_hash,
            passed=passed,
            resolved_issue_ids=original_ids if passed else [],
            residual_issue_ids=[] if passed else original_ids,
            regression_issue_ids=regressions,
            validators=validators,
            details=([] if passed else ["candidate did not improve every required short gate"]),
            metadata={
                "eval_report": candidate_eval.model_dump(mode="json"),
                "baseline_diagnosis": self.baseline_diagnosis.to_payload(),
                "candidate_diagnosis": candidate_diagnosis.to_payload(),
                "repair_kind": self.repair_kind,
            },
        )


def _candidate_outcome_from_case(
    store: RepairCaseStore,
    case: Any,
    *,
    baseline_text: str,
    baseline_eval: EvalReport,
) -> ShortCandidateRepairOutcome:
    if (
        case.status != "verified"
        or case.latest_candidate is None
        or case.verification is None
        or not case.verification.passed
    ):
        return ShortCandidateRepairOutcome(baseline_text, baseline_eval, case.case_id, False)
    payload = store.get_blob(case.latest_candidate.blob_hash)
    eval_payload = case.verification.metadata.get("eval_report")
    if not isinstance(payload, str) or not isinstance(eval_payload, dict):
        return ShortCandidateRepairOutcome(baseline_text, baseline_eval, case.case_id, False)
    candidate_eval = EvalReport.model_validate(eval_payload)
    if candidate_eval.source_text_hash != source_text_hash(payload):
        return ShortCandidateRepairOutcome(baseline_text, baseline_eval, case.case_id, False)
    return ShortCandidateRepairOutcome(payload, candidate_eval, case.case_id, payload != baseline_text)


async def run_short_story_candidate_repair(
    *,
    runner: Any,
    layout: Any,
    spec: Any,
    beats: Any,
    blueprint: Any | None,
    execution_plan: dict[str, Any],
    current_text: str,
    issues: list[str],
    repair_kind: ShortRepairKind,
    pass_label: str,
    iteration: int,
    baseline_eval: EvalReport,
    control_mode: RepairControlMode | str | None = None,
) -> ShortCandidateRepairOutcome:
    """Prepare and fully verify one isolated short-story working candidate."""

    from novel_forge.pipeline.short.stages.adaptive_revision import (
        diagnose_short_revision,
    )

    mode = resolve_mode(control_mode, getattr(runner, "_settings", None))
    if mode == RepairControlMode.MANUAL:
        return ShortCandidateRepairOutcome(current_text, baseline_eval)
    baseline_diagnosis = diagnose_short_revision(
        runner,
        spec,
        execution_plan,
        current_text,
        baseline_eval,
        attempted_repair=False,
    )
    aggregate_issue = _aggregate_short_issue(baseline_diagnosis.structured_issues)
    plugin = ShortStoryCandidatePlugin(
        runner=runner,
        layout=layout,
        spec=spec,
        beats=beats,
        blueprint=blueprint,
        execution_plan=execution_plan,
        baseline_text=current_text,
        baseline_eval=baseline_eval,
        baseline_diagnosis=baseline_diagnosis,
        repair_kind=repair_kind,
        issues=issues,
        pass_label=pass_label,
        iteration=iteration,
        aggregate_issue=aggregate_issue,
    )
    registry = RepairPluginRegistry()
    registry.register(plugin)
    store = RepairCaseStore(layout.root)
    orchestrator = RepairOrchestrator(
        plugin_registry=registry,
        case_store=store,
        on_step=getattr(runner, "_on_step", None),
    )
    baseline_hash = repair_content_hash(current_text)
    context_hash = repair_content_hash(
        {
            "repair_kind": repair_kind,
            "pass_label": pass_label,
            "iteration": iteration,
            "issues": issues,
            "spec": spec.model_dump(mode="json") if hasattr(spec, "model_dump") else str(spec),
            "beats": (
                beats.model_dump(mode="json") if hasattr(beats, "model_dump") else str(beats)
            ),
            "blueprint": (
                blueprint.model_dump(mode="json")
                if blueprint is not None and hasattr(blueprint, "model_dump")
                else None
            ),
            "execution_plan": execution_plan,
            "baseline_eval": baseline_eval.model_dump(mode="json"),
        }
    )
    case_seed = f"{repair_kind}:{iteration}:{baseline_hash}:{context_hash}"
    case_id = "short-" + hashlib.sha256(case_seed.encode("utf-8")).hexdigest()[:24]
    existing = store.load_case(case_id)
    if existing is not None:
        if existing.status == "verified":
            return _candidate_outcome_from_case(
                store,
                existing,
                baseline_text=current_text,
                baseline_eval=baseline_eval,
            )
        if existing.status == "candidate_ready":
            existing = store.append_event(
                case_id,
                "verification_requested",
                {},
                expected_version=existing.version,
            )
        if existing.status == "needs_verification" and existing.latest_candidate is not None:
            verified = await orchestrator.verify_case(
                case_id,
                expected_case_version=existing.version,
                expected_candidate_version=existing.latest_candidate.version,
            )
            return _candidate_outcome_from_case(
                store,
                verified,
                baseline_text=current_text,
                baseline_eval=baseline_eval,
            )
        case_id = f"{case_id}-{existing.event_seq}"
    snapshot = RepairArtifactSnapshot(
        project_id=str(getattr(layout, "project_id", "") or layout.root.name),
        content_type="short_story",
        artifact_id=f"short_story:draft:{iteration}",
        source_version=f"{source_text_hash(current_text)}:{context_hash}",
        source_hash=baseline_hash,
        payload=current_text,
        metadata={
            "repair_kind": repair_kind,
            "iteration": iteration,
            "context_hash": context_hash,
        },
    )
    case = await orchestrator.run_case(
        snapshot,
        issue=aggregate_issue,
        case_id=case_id,
        source="short_pipeline",
        authority="automatic_working_candidate",
        input_version=context_hash,
        max_attempts=1,
    )
    return _candidate_outcome_from_case(
        store,
        case,
        baseline_text=current_text,
        baseline_eval=baseline_eval,
    )


class ShortStoryRepairHandler:
    """V2 adapter for short-story completion and quality repairs."""

    name = "short_story_repair"

    def supports(self, target: RepairTarget) -> bool:
        return target.domain == RepairDomain.SHORT_STORY and target.surface == RepairSurface.SHORT_TEXT

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        del mission
        strategy = RepairStrategy.LLM_PATCH
        raw_strategy = target.payload.get("strategy") or target.payload.get("repair_strategy")
        if raw_strategy:
            try:
                strategy = RepairStrategy(str(raw_strategy))
            except ValueError:
                strategy = RepairStrategy.LLM_PATCH
        return RepairPlanCandidate(
            target_id=target.id,
            strategy=strategy,
            summary=target.summary or "short story repair",
            rationale="wrap_existing_short_story_repair_step",
            preview=str(target.payload.get("preview") or target.evidence or ""),
            estimated_change_ratio=float(target.payload.get("estimated_change_ratio") or 0.2),
            crosses_artifact_boundary=False,
            verification_required=True,
        )

    async def snapshot(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> dict[str, Any]:
        del target, plan
        ctx = mission.source_context
        snapshots: list[dict[str, Any]] = []
        layout = ctx.get("layout")
        iteration = int(ctx.get("iteration") or 0)
        short_draft_path = getattr(layout, "short_draft_path", None)
        if callable(short_draft_path):
            snapshots.append(snapshot_text_path(short_draft_path(iteration)))
        return {
            "current_text": str(ctx.get("current_text") or ""),
            "paths": snapshots,
            "short_story_result": ctx.get("short_story_result"),
        }

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del target, plan
        from novel_forge.pipeline.short.stages.edit import (
            run_completion_repair,
            run_quality_repair,
        )

        ctx = mission.source_context
        required = (
            "runner",
            "layout",
            "spec",
            "beats",
            "execution_plan",
            "current_text",
            "issues",
            "pass_label",
            "iteration",
        )
        missing = [key for key in required if key not in ctx]
        if missing:
            raise ValueError(f"short_story repair mission missing context keys: {', '.join(missing)}")

        before = str(ctx["current_text"])
        repair_kind = str(ctx.get("repair_kind") or "quality")
        if repair_kind == "completion":
            repaired_text = await run_completion_repair(
                ctx["runner"],
                ctx["layout"],
                ctx["spec"],
                ctx["beats"],
                ctx.get("blueprint"),
                ctx["execution_plan"],
                before,
                list(ctx.get("issues") or ()),
                pass_label=str(ctx["pass_label"]),
                iteration=int(ctx["iteration"]),
                eval_report=ctx.get("eval_report"),
            )
        else:
            eval_report = ctx.get("eval_report")
            if eval_report is None:
                raise ValueError("short quality repair requires eval_report")
            repaired_text = await run_quality_repair(
                ctx["runner"],
                ctx["layout"],
                ctx["spec"],
                ctx["beats"],
                ctx.get("blueprint"),
                ctx["execution_plan"],
                before,
                list(ctx.get("issues") or ()),
                pass_label=str(ctx["pass_label"]),
                iteration=int(ctx["iteration"]),
                eval_report=eval_report,
            )
        ctx["short_story_result"] = repaired_text
        return RepairExecutionResult(
            applied=repaired_text != before,
            payload={"repair_kind": repair_kind},
            changed_artifacts=["in_memory:current_text", f"short_draft:{ctx['iteration']}"],
            change_ratio=text_change_ratio(before, repaired_text),
        )

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del target, plan, result
        repaired_text = str(mission.source_context.get("short_story_result") or "")
        if not repaired_text.strip():
            return RepairVerificationResult(
                verified=False,
                confidence=0.2,
                reason="short_story_repair_empty_text",
            )
        return RepairVerificationResult(
            verified=True,
            confidence=0.75,
            reason="short_story_repair_completed",
        )

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        del target
        mission.source_context["current_text"] = str(snapshot_payload.get("current_text") or "")
        if snapshot_payload.get("short_story_result") is not None:
            mission.source_context["short_story_result"] = snapshot_payload["short_story_result"]
        else:
            mission.source_context.pop("short_story_result", None)
        for item in snapshot_payload.get("paths") or []:
            if isinstance(item, dict):
                restore_text_path(item)

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del target, plan
        mission.source_context["current_text"] = str(
            mission.source_context.get("short_story_result")
            or mission.source_context.get("current_text")
            or ""
        )
        return [
            RepairArtifactChange(artifact=artifact, metadata={"handler": self.name})
            for artifact in result.changed_artifacts
        ]


async def run_short_story_repair_v2(
    *,
    runner: Any,
    layout: Any,
    spec: Any,
    beats: Any,
    blueprint: Any | None,
    execution_plan: dict[str, Any],
    current_text: str,
    issues: list[str],
    repair_kind: ShortRepairKind,
    pass_label: str,
    iteration: int,
    eval_report: Any | None = None,
    control_mode: RepairControlMode | str | None = None,
    snapshot_store: RepairSnapshotStore | None = None,
) -> str:
    """Run short-story repair through v2 and return repaired text or the original text."""

    settings = getattr(runner, "_settings", None)
    mode = resolve_mode(control_mode, settings)
    source_context: dict[str, Any] = {
        "runner": runner,
        "layout": layout,
        "spec": spec,
        "beats": beats,
        "blueprint": blueprint,
        "execution_plan": execution_plan,
        "current_text": current_text,
        "issues": issues,
        "repair_kind": repair_kind,
        "pass_label": pass_label,
        "iteration": iteration,
        "eval_report": eval_report,
    }
    on_step = getattr(runner, "_on_step", None)
    source_hash = text_hash(current_text)
    issue_audit_payloads: list[dict[str, Any]] = []
    for index, issue in enumerate(issues):
        payload = issue_audit_payload(
            {
                "issue_id": f"short-story-{repair_kind}-{iteration}-{index}",
                "issue_type": f"short_story_{repair_kind}",
                "severity": "high" if repair_kind == "completion" else "medium",
                "summary": str(issue or ""),
            },
            event_type="selected",
            dimension=RepairDomain.SHORT_STORY.value,
            surface=RepairSurface.SHORT_TEXT.value,
            status="selected",
            repair_action="gate_batch",
            source_text_hash=source_hash,
            location_mode="whole_text",
            extra={"batch_mode": True, "iteration": iteration},
        )
        issue_audit_payloads.append(payload)
        emit_repair_audit_event(on_step, payload)
        emit_repair_audit_event(
            on_step,
            {
                **payload,
                "event_type": "attempted",
                "status": "attempted",
                "repair_action": "gate_batch",
            },
        )
    target = RepairTarget(
        domain=RepairDomain.SHORT_STORY,
        surface=RepairSurface.SHORT_TEXT,
        issue_ref=f"short_story:{repair_kind}:{iteration}",
        severity="high" if repair_kind == "completion" else "medium",
        summary=f"short story {repair_kind} repair",
        payload={"repair_kind": repair_kind},
    )
    mission = RepairMission(
        project_id=str(getattr(layout, "project_id", "") or getattr(runner, "project_id", "") or ""),
        control_mode=mode,
        targets=[target],
        policy={
            "change_budget": float(
                getattr(settings, "short_repair_max_change_ratio", 0.65) or 0.65
            )
        },
        source_context=source_context,
    )
    registry = RepairHandlerRegistry()
    registry.register(ShortStoryRepairHandler())
    orchestrator = RepairOrchestrator(
        registry=registry,
        snapshot_store=snapshot_store,
        on_step=on_step,
    )
    outcome = await orchestrator.run(mission)
    record_domain_repair_ledger(mission=mission, outcome=outcome, sources=(layout,))
    target_hash = text_hash(str(mission.source_context.get("short_story_result") or current_text))
    for payload in issue_audit_payloads:
        emit_repair_audit_event(
            on_step,
            {
                **payload,
                "event_type": "finalized",
                "status": "finalized" if outcome.applied else "coarse_unverified",
                "repair_action": "gate_batch_finalize",
                "target_text_hash": target_hash,
                "fallback_action": "coarse_mode" if not outcome.applied else "",
            },
        )
    return str(mission.source_context.get("short_story_result") or current_text)
