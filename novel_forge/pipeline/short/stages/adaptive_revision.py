"""Issue-driven short-story revision with bounded, resumable model calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_forge.core.exceptions import PipelineError
from novel_forge.core.schemas.audit import (
    AuditEvidence,
    AuditIssueV2,
    AuditLocator,
    AuditPostcondition,
    AuditRepairIntent,
)
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.pipeline.short._shared import clean_text, unique_texts
from novel_forge.pipeline.short.quality_gate import (
    evaluate_short_quality_gate,
    serialize_short_quality_gate_report,
)
from novel_forge.pipeline.short.stages.edit import (
    check_short_story_completeness,
    collect_short_quality_repair_issues,
    eval_flags_incomplete_story,
    run_adaptive_edit,
)
from novel_forge.pipeline.short.stages.evaluate import run_final_evaluation

if TYPE_CHECKING:
    from novel_forge.core.schemas.beats import StoryBeats
    from novel_forge.core.schemas.short_blueprint import ShortBlueprint
    from novel_forge.core.schemas.spec import StorySpec
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.short_runner import ShortStoryRunner


_INTENT_DIMENSION = "intent_compliance"
_INTENT_FLOOR = 9.0


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _revision_checkpoint_path(layout: ProjectLayout) -> Path:
    return layout.reports_dir / "short_revision_checkpoint.json"


def _score_for(eval_report: EvalReport, dimension: str) -> float | None:
    target = clean_text(dimension).lower()
    for item in eval_report.scores:
        if clean_text(item.dimension).lower() == target:
            return float(item.score)
    return None


def _assert_trustworthy_evaluation(eval_report: EvalReport) -> None:
    if eval_report.is_fallback or clean_text(eval_report.evaluation_status).lower() not in {
        "",
        "ok",
    }:
        raise PipelineError(
            "short_adaptive_revision",
            "需要可信的评估结果；提供商/网络评估失败，"
            "已保留上一个已验证文本。",
        )


@dataclass(frozen=True)
class ShortRevisionDiagnosis:
    issues: tuple[str, ...]
    hard_issue_count: int
    overall_score: float
    passed: bool
    completeness: dict[str, Any]
    quality_gate: dict[str, Any]
    intent_score: float | None
    diagnosis_hash: str
    structured_issues: tuple[AuditIssueV2, ...] = ()

    def rank(self) -> tuple[int, int, float]:
        return (self.hard_issue_count, len(self.issues), -self.overall_score)

    def to_payload(self) -> dict[str, Any]:
        return {
            "issues": list(self.issues),
            "hard_issue_count": self.hard_issue_count,
            "overall_score": self.overall_score,
            "passed": self.passed,
            "completeness": self.completeness,
            "quality_gate": self.quality_gate,
            "intent_score": self.intent_score,
            "diagnosis_hash": self.diagnosis_hash,
            "structured_issues": [
                issue.model_dump(mode="json") for issue in self.structured_issues
            ],
        }


def _structured_short_issues(
    text: str,
    issues: tuple[str, ...],
    *,
    hard_issue_count: int,
) -> tuple[AuditIssueV2, ...]:
    """Bind legacy string findings to one exact short-story source version.

    Short-story completeness and aggregate quality findings legitimately apply
    to the whole story.  The locator therefore names the stable story node and
    the exact full character interval instead of pretending that the evaluator
    supplied a sentence-level quote.
    """

    text_hash = source_text_hash(text)
    preview = text[:240]
    structured: list[AuditIssueV2] = []
    for index, message in enumerate(issues):
        issue_id = "short-" + _stable_hash(
            {"source_text_hash": text_hash, "index": index, "message": message}
        )[:24]
        severity = "high" if index < hard_issue_count else "medium"
        locator = AuditLocator(
            target_format="prose_text",
            role="repair",
            surface="short_text",
            confidence=1.0,
            artifact="chapters/short_story.md",
            field_path="$text",
            stable_node_id="short-story:1",
            container_hash=text_hash,
            chapter_number=1,
            scene_id="short-story:1",
            text_hash=text_hash,
            char_start=0,
            char_end=len(text),
            comparator_id="short_full_evaluation_v1",
            expected_raw="full_evaluation_passed",
            actual_raw=message,
            expected_normalized=True,
            actual_normalized=False,
            source_version=text_hash,
        )
        structured.append(
            AuditIssueV2(
                issue_id=issue_id,
                dimension="quality",
                issue_type="short_full_evaluation",
                severity=severity,
                blocking=severity == "high",
                summary=message,
                description=message,
                evidence=[
                    AuditEvidence(
                        quote=preview,
                        source="short_final_evaluation",
                        locator=locator,
                        confidence=1.0,
                    )
                ],
                repair_targets=[locator],
                reference_targets=[],
                repair_intent=AuditRepairIntent(
                    operation="window_rewrite",
                    target_policy="single_exact_story_node",
                    rationale="短篇完整性与综合质量必须对同一全文候选复验",
                    preserve=[
                        "作者硬意图",
                        "人物与关系",
                        "POV",
                        "结局",
                        "世界规则",
                        "locked 要素",
                    ],
                    allowed_strategies=["bounded_fulltext_rewrite"],
                ),
                postconditions=[
                    AuditPostcondition(
                        validator_id="short_full_evaluation_v1",
                        description="重新运行完整短篇评估和质量门",
                        required=True,
                    ),
                    AuditPostcondition(
                        validator_id="short_intent_compliance_v1",
                        description="作者硬意图维度不得低于门槛",
                        required=True,
                    ),
                ],
                metadata={"source_text_hash": text_hash, "legacy_issue_index": index},
            )
        )
    return tuple(structured)


@dataclass(frozen=True)
class AdaptiveShortRevisionResult:
    text: str
    eval_report: EvalReport
    quality_gate: dict[str, Any]
    rounds_used: int
    diagnosis_hash: str
    unresolved_issues: tuple[str, ...] = ()
    resumed: bool = False


def diagnose_short_revision(
    runner: ShortStoryRunner,
    spec: StorySpec,
    execution_plan: dict[str, Any],
    text: str,
    eval_report: EvalReport,
    *,
    attempted_repair: bool,
) -> ShortRevisionDiagnosis:
    """Merge intent, completeness, word-count and quality signals once."""

    _assert_trustworthy_evaluation(eval_report)
    completeness = check_short_story_completeness(runner, text, spec, execution_plan)
    thresholds = runner._build_short_quality_gate_thresholds()
    gate_report = evaluate_short_quality_gate(
        eval_report,
        thresholds,
        require_intent_compliance=True,
    )
    gate_payload = serialize_short_quality_gate_report(
        gate_report,
        thresholds,
        attempted_repair=attempted_repair,
        best_effort_accepted=False,
    )

    issues: list[str] = []
    hard_count = 0
    intent_score = _score_for(eval_report, _INTENT_DIMENSION)
    if intent_score is None:
        issues.append("评估缺少 intent_compliance 维度，无法证明未改写用户硬意图。")
        hard_count += 1
    elif intent_score < _INTENT_FLOOR:
        issues.append(
            f"用户意图合规仅 {intent_score:.1f} 分：必须恢复人物、关系、POV、"
            "结局、世界规则或 locked 要素中被改写的部分。"
        )
        hard_count += 1

    completeness_issues = [clean_text(item) for item in completeness.get("issues", [])]
    if eval_flags_incomplete_story(eval_report) and not completeness_issues:
        completeness_issues.append("评估认为开头、结尾或整体收束仍不完整。")
    if completeness_issues:
        issues.extend(completeness_issues)
        hard_count += len(completeness_issues)

    failed_gate_messages = [
        clean_text(item.get("message"))
        for item in gate_payload.get("checks", [])
        if not item.get("passed", True) and clean_text(item.get("message"))
    ]
    issues.extend(failed_gate_messages)
    issues.extend(collect_short_quality_repair_issues(eval_report, max_issues=6))

    target = max(500, int(spec.length_target or 3000))
    current_words = count_chapter_words(text)
    ratio = current_words / max(target, 1)
    if ratio < 0.7 or ratio > 1.5:
        issues.append(
            f"当前字数约 {current_words}，目标 {target}，需在不填充空转内容的前提下收敛偏差。"
        )

    for suggestion in eval_report.repair_suggestions:
        if clean_text(suggestion.priority).lower() != "high":
            continue
        issue = clean_text(suggestion.issue)
        fix = clean_text(suggestion.suggestion)
        location = clean_text(suggestion.location)
        if issue or fix:
            issues.append("：".join(part for part in (location, issue or fix) if part))

    normalized = tuple(unique_texts([item for item in issues if clean_text(item)])[:10])
    passed = hard_count == 0 and not normalized
    structured_issues = _structured_short_issues(
        text,
        normalized,
        hard_issue_count=hard_count,
    )
    payload = {
        "issues": normalized,
        "hard_issue_count": hard_count,
        "overall_score": float(eval_report.overall_score),
        "completeness": completeness,
        "quality_gate": gate_payload,
        "intent_score": intent_score,
    }
    return ShortRevisionDiagnosis(
        issues=normalized,
        hard_issue_count=hard_count,
        overall_score=float(eval_report.overall_score),
        passed=passed,
        completeness=completeness,
        quality_gate=gate_payload,
        intent_score=intent_score,
        diagnosis_hash=_stable_hash(payload),
        structured_issues=structured_issues,
    )


def _checkpoint_text(
    runner: ShortStoryRunner,
    payload: dict[str, Any],
) -> str | None:
    path_value = clean_text(payload.get("best_text_path"))
    if not path_value:
        return None
    path = Path(path_value)
    try:
        text = runner._storage.load_text(path)
    except (FileNotFoundError, OSError):
        return None
    if source_text_hash(text) != clean_text(payload.get("best_text_hash")):
        return None
    return text


def _save_checkpoint(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    *,
    source_hash: str,
    status: str,
    rounds_used: int,
    best_text: str,
    best_text_path: Path,
    best_eval: EvalReport,
    diagnosis: ShortRevisionDiagnosis,
    candidate_text_path: Path | None = None,
) -> None:
    runner._storage.save_json(
        _revision_checkpoint_path(layout),
        {
            "schema_version": 1,
            "source_draft_hash": source_hash,
            "status": status,
            "rounds_used": rounds_used,
            "diagnosis_hash": diagnosis.diagnosis_hash,
            "diagnosis": diagnosis.to_payload(),
            "best_text_hash": source_text_hash(best_text),
            "best_text_path": str(best_text_path),
            "best_eval": best_eval.model_dump(mode="json"),
            "candidate_text_path": str(candidate_text_path) if candidate_text_path else "",
            "evaluation_state": "pending" if status == "awaiting_evaluation" else "complete",
        },
    )


async def run_adaptive_short_revision(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    draft_text: str,
) -> AdaptiveShortRevisionResult:
    """Run zero to two EDIT calls, keeping only the best evaluated text."""

    source_hash = source_text_hash(draft_text)
    checkpoint_path = _revision_checkpoint_path(layout)
    cap = min(max(0, int(runner._max_edit)), 2)
    checkpoint: dict[str, Any] = {}
    if runner._storage.exists(checkpoint_path):
        try:
            loaded = runner._storage.load_json(checkpoint_path)
            if clean_text(loaded.get("source_draft_hash")) == source_hash:
                checkpoint = loaded
        except Exception:
            checkpoint = {}

    if checkpoint.get("status") == "complete":
        resumed_text = _checkpoint_text(runner, checkpoint)
        if resumed_text is not None:
            eval_report = EvalReport.model_validate(checkpoint.get("best_eval") or {})
            diagnosis = diagnose_short_revision(
                runner,
                spec,
                execution_plan,
                resumed_text,
                eval_report,
                attempted_repair=int(checkpoint.get("rounds_used") or 0) > 0,
            )
            resumed_gate = dict(diagnosis.quality_gate)
            if diagnosis.issues:
                resumed_gate["best_effort_accepted"] = True
            runner._storage.save_json(layout.eval_report_path(), eval_report.model_dump(mode="json"))
            runner._storage.save_json(layout.short_quality_gate_report_path(), resumed_gate)
            runner._on_step(
                "short_adaptive_revision_resumed",
                {"rounds_used": int(checkpoint.get("rounds_used") or 0)},
            )
            return AdaptiveShortRevisionResult(
                text=resumed_text,
                eval_report=eval_report,
                quality_gate=resumed_gate,
                rounds_used=int(checkpoint.get("rounds_used") or 0),
                diagnosis_hash=diagnosis.diagnosis_hash,
                unresolved_issues=diagnosis.issues,
                resumed=True,
            )

    best_text = draft_text
    best_path = layout.short_draft_path(0)
    rounds_used = 0
    best_eval: EvalReport
    best_diagnosis: ShortRevisionDiagnosis

    if checkpoint:
        restored_text = _checkpoint_text(runner, checkpoint)
        try:
            restored_eval = EvalReport.model_validate(checkpoint.get("best_eval") or {})
        except Exception:
            restored_eval = None
        if restored_text is not None and restored_eval is not None:
            best_text = restored_text
            best_path = Path(clean_text(checkpoint.get("best_text_path")))
            best_eval = restored_eval
            rounds_used = min(cap, max(0, int(checkpoint.get("rounds_used") or 0)))
            best_diagnosis = diagnose_short_revision(
                runner,
                spec,
                execution_plan,
                best_text,
                best_eval,
                attempted_repair=rounds_used > 0,
            )
        else:
            checkpoint = {}

    if not checkpoint:
        best_eval = await run_final_evaluation(
            runner,
            layout,
            spec,
            beats,
            blueprint,
            execution_plan,
            best_text,
            require_intent_compliance=True,
        )
        best_diagnosis = diagnose_short_revision(
            runner,
            spec,
            execution_plan,
            best_text,
            best_eval,
            attempted_repair=False,
        )
        _save_checkpoint(
            runner,
            layout,
            source_hash=source_hash,
            status="diagnosed",
            rounds_used=0,
            best_text=best_text,
            best_text_path=best_path,
            best_eval=best_eval,
            diagnosis=best_diagnosis,
        )

    resume_stopped = False
    if checkpoint.get("status") == "awaiting_evaluation":
        candidate_path = Path(clean_text(checkpoint.get("candidate_text_path")))
        try:
            candidate_text = runner._storage.load_text(candidate_path)
        except (FileNotFoundError, OSError):
            candidate_text = ""
        if candidate_text:
            candidate_eval = await run_final_evaluation(
                runner,
                layout,
                spec,
                beats,
                blueprint,
                execution_plan,
                candidate_text,
                require_intent_compliance=True,
            )
            candidate_diagnosis = diagnose_short_revision(
                runner,
                spec,
                execution_plan,
                candidate_text,
                candidate_eval,
                attempted_repair=True,
            )
            if candidate_diagnosis.rank() < best_diagnosis.rank():
                best_text = candidate_text
                best_path = candidate_path
                best_eval = candidate_eval
                best_diagnosis = candidate_diagnosis
            else:
                resume_stopped = True
                runner._on_step(
                    "short_adaptive_revision_rollback",
                    {
                        "round": rounds_used,
                        "reason": "resumed_diagnosis_not_improved",
                    },
                )
        checkpoint = {}

    runner._on_step("short_adaptive_diagnosis", best_diagnosis.to_payload())
    previous_diagnosis = best_diagnosis
    while rounds_used < cap and not best_diagnosis.passed and not resume_stopped:
        iteration = rounds_used + 1
        candidate_path = layout.short_draft_path(iteration)
        candidate_text = await run_adaptive_edit(
            runner,
            layout,
            spec,
            beats,
            blueprint,
            execution_plan,
            best_text,
            list(best_diagnosis.issues),
            iteration=iteration,
            max_rounds=cap,
            eval_report=best_eval,
        )
        rounds_used = iteration
        _save_checkpoint(
            runner,
            layout,
            source_hash=source_hash,
            status="awaiting_evaluation",
            rounds_used=rounds_used,
            best_text=best_text,
            best_text_path=best_path,
            best_eval=best_eval,
            diagnosis=best_diagnosis,
            candidate_text_path=candidate_path,
        )

        candidate_eval = await run_final_evaluation(
            runner,
            layout,
            spec,
            beats,
            blueprint,
            execution_plan,
            candidate_text,
            require_intent_compliance=True,
        )
        candidate_diagnosis = diagnose_short_revision(
            runner,
            spec,
            execution_plan,
            candidate_text,
            candidate_eval,
            attempted_repair=True,
        )
        improved = candidate_diagnosis.rank() < previous_diagnosis.rank()
        if improved:
            best_text = candidate_text
            best_path = candidate_path
            best_eval = candidate_eval
            best_diagnosis = candidate_diagnosis
        else:
            runner._on_step(
                "short_adaptive_revision_rollback",
                {
                    "round": rounds_used,
                    "reason": "diagnosis_not_improved",
                    "previous": previous_diagnosis.to_payload(),
                    "candidate": candidate_diagnosis.to_payload(),
                },
            )
            break
        runner._on_step(
            "short_adaptive_revision_round",
            {"round": rounds_used, "diagnosis": best_diagnosis.to_payload()},
        )
        _save_checkpoint(
            runner,
            layout,
            source_hash=source_hash,
            status="round_evaluated",
            rounds_used=rounds_used,
            best_text=best_text,
            best_text_path=best_path,
            best_eval=best_eval,
            diagnosis=best_diagnosis,
        )
        if best_diagnosis.passed:
            break
        previous_diagnosis = best_diagnosis

    final_gate = dict(best_diagnosis.quality_gate)
    if best_diagnosis.issues:
        final_gate["best_effort_accepted"] = True
        best_eval = best_eval.model_copy(update={"passed": False})
    runner._storage.save_json(layout.eval_report_path(), best_eval.model_dump(mode="json"))
    runner._storage.save_json(layout.short_quality_gate_report_path(), final_gate)
    _save_checkpoint(
        runner,
        layout,
        source_hash=source_hash,
        status="complete",
        rounds_used=rounds_used,
        best_text=best_text,
        best_text_path=best_path,
        best_eval=best_eval,
        diagnosis=best_diagnosis,
    )
    return AdaptiveShortRevisionResult(
        text=best_text,
        eval_report=best_eval,
        quality_gate=final_gate,
        rounds_used=rounds_used,
        diagnosis_hash=best_diagnosis.diagnosis_hash,
        unresolved_issues=best_diagnosis.issues,
    )


__all__ = [
    "AdaptiveShortRevisionResult",
    "ShortRevisionDiagnosis",
    "diagnose_short_revision",
    "run_adaptive_short_revision",
]
