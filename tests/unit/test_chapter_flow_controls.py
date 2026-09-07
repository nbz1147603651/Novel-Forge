"""Tests for chapter flow control gates."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import novel_forge.pipeline.long.chapter_flow as chapter_flow
import novel_forge.pipeline.long.chapter_flow_finalize as chapter_flow_finalize
import novel_forge.pipeline.long.chapter_flow_orchestrate as chapter_flow_orchestrate
import novel_forge.pipeline.long.chapter_flow_review as chapter_flow_review
import novel_forge.pipeline.long.stages.draft as draft_stage
import novel_forge.pipeline.long.stages.finalize_persist as finalize_stage
from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalIssue,
    CausalValidationReport,
    ChapterRepairReport,
)
from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.core.schemas.eval_schema import EvalReport, EvalScore
from novel_forge.core.schemas.review import RepairTicket, ReviewFinding
from novel_forge.core.utils.pipeline_helpers import normalize_threshold
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.pipeline.long.chapter_flow import (
    _alignment_meets_threshold,
    _archive_policy_block_messages,
    _build_normalized_review_contracts,
    _build_quality_gate,
    _build_repair_metrics_payload,
    _enforce_alignment_threshold,
    _guidance_report_mismatch_sources,
    _persist_quality_gate_report,
    _recover_latest_draft,
    _repair_contract_execution_audit_block,
    _save_reading_power_next_chapter_constraints,
    _should_include_pre_final_evaluation,
)
from novel_forge.pipeline.long.decisions import resolve_total_repair_rounds_cap
from novel_forge.pipeline.long.execution_models import (
    ChapterReviewArtifacts,
    PreparedChapterArtifacts,
)
from novel_forge.pipeline.long.loop import _detect_kernel_persist_pending_marker
from novel_forge.pipeline.long.stages.prompt_leak_repair import PromptLeakRepairResult
from novel_forge.pipeline.quality_gate import QualityGate


class _ConfigStub:
    def __init__(self, threshold: float) -> None:
        self.alignment_threshold = threshold


class _RunnerStub:
    def __init__(self, threshold: float) -> None:
        self._config = _ConfigStub(threshold)
        self.events: list[tuple[str, object]] = []

    def _on_step(self, step: str, data: object) -> None:
        self.events.append((step, data))


def test_alignment_meets_threshold_clamps_values() -> None:
    """Test alignment threshold validation with clamped values."""
    assert normalize_threshold(12.8) == 10.0
    assert normalize_threshold(-1.0) == 0.0
    assert normalize_threshold("bad-value") == 7.0


def test_alignment_meets_threshold_comparison() -> None:
    """Test alignment score meets threshold comparison."""
    assert _alignment_meets_threshold(8.0, 7.0) is True
    assert _alignment_meets_threshold(7.0, 7.0) is True
    assert _alignment_meets_threshold(6.0, 7.0) is False


def test_recover_latest_draft_preserves_edit_iteration() -> None:
    text = "已经编辑后的章节正文。" * 20

    class _Storage:
        def exists(self, path: object) -> bool:
            return path == "chapter_3_v2"

        def load_text(self, path: object) -> str:
            assert path == "chapter_3_v2"
            return text

    layout = SimpleNamespace(chapter_draft_path=lambda chapter, ver: f"chapter_{chapter}_v{ver}")
    recovered = _recover_latest_draft(_Storage(), layout, 3)

    assert recovered is not None
    assert recovered.text == text
    assert recovered.performed_edits == 2


def test_recover_latest_draft_uses_wave_handoff_before_legacy_edits() -> None:
    wave_text = "经过 WAVE 编织后的可审初稿。" * 20
    edited_text = "旧版编辑稿。" * 30

    class _Storage:
        def exists(self, path: object) -> bool:
            return path in {"chapter_3_wave", "chapter_3_v2"}

        def load_text(self, path: object) -> str:
            if path == "chapter_3_wave":
                return wave_text
            if path == "chapter_3_v2":
                return edited_text
            raise AssertionError(f"unexpected path: {path!r}")

    layout = SimpleNamespace(
        chapter_wave_draft_path=lambda chapter: f"chapter_{chapter}_wave",
        chapter_draft_path=lambda chapter, ver: f"chapter_{chapter}_v{ver}",
    )
    recovered = _recover_latest_draft(_Storage(), layout, 3)

    assert recovered is not None
    assert recovered.text == wave_text
    assert recovered.performed_edits == 1


def test_recover_latest_draft_does_not_resume_from_raw_v0_draft() -> None:
    raw_text = "只经过 DRAFT、尚未 WAVE 的原稿。" * 20

    class _Storage:
        def exists(self, path: object) -> bool:
            return path == "chapter_3_v0"

        def load_text(self, path: object) -> str:
            assert path == "chapter_3_v0"
            return raw_text

    layout = SimpleNamespace(chapter_draft_path=lambda chapter, ver: f"chapter_{chapter}_v{ver}")

    assert _recover_latest_draft(_Storage(), layout, 3) is None


def test_pov_drift_ticket_projects_into_continuity_repair_issue() -> None:
    events: list[tuple[str, object]] = []
    report = ContinuityReport(continuity_score=10.0, summary="clean", issues=[])
    ticket = RepairTicket(
        ticket_id="ticket_pov_drift_3_0",
        chapter_number=3,
        source_module="pov_drift_audit",
        dimension="pov",
        issue_type="unmarked_interior_switch",
        severity="high",
        target_summary="第0段：非POV角色 林岫 获得内心视角",
        repair_goal="移除非POV角色的内心描写",
        target_paragraph_start=0,
        target_paragraph_end=0,
        metadata={
            "evidence_quote": "林岫心想这一步终于成了。",
            "characters_with_interior": ["林岫"],
            "pov_character": "沈昭",
        },
    )

    merged = chapter_flow_review._merge_pov_drift_into_continuity_report(  # noqa: SLF001
        report,
        pov_tickets=[ticket],
        on_step=lambda step, data: events.append((step, data)),
        chapter_number=3,
    )

    assert merged.continuity_score == 8.0
    assert len(merged.issues) == 1
    issue = merged.issues[0]
    assert issue.issue_type == "pov_intrusion"
    assert issue.severity == "critical"
    assert issue.paragraph_start == 1
    assert issue.paragraph_end == 1
    assert issue.rewrite_scope == "paragraph"
    assert issue.blocking is True
    assert any("不得扩大为全章视角重写" in item for item in issue.forbidden_changes)
    assert events[0][0] == "pov_drift_continuity_injected"
    assert events[0][1]["injected_issue_count"] == 1


def test_pov_drift_medium_ticket_is_not_auto_injected_into_continuity() -> None:
    events: list[tuple[str, object]] = []
    report = ContinuityReport(continuity_score=10.0, summary="clean", issues=[])
    ticket = RepairTicket(
        ticket_id="ticket_pov_drift_3_2",
        chapter_number=3,
        source_module="pov_drift_audit",
        dimension="pov",
        issue_type="unmarked_interior_switch",
        severity="medium",
        target_summary="全知视角候选，仅作为诊断",
        target_paragraph_start=2,
        target_paragraph_end=2,
    )

    merged = chapter_flow_review._merge_pov_drift_into_continuity_report(  # noqa: SLF001
        report,
        pov_tickets=[ticket],
        on_step=lambda step, data: events.append((step, data)),
        chapter_number=3,
    )

    assert merged is report
    assert merged.issues == []
    assert events == []


@pytest.mark.asyncio
async def test_rp_text_change_uses_hash_bound_reports_without_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current_text = "追读力修复后正文。" * 20
    current_hash = chapter_flow_review.source_text_hash(current_text)
    events: list[tuple[str, object]] = []

    class _Storage:
        def load_json(self, path: Path) -> dict[str, object]:
            return json.loads(path.read_text(encoding="utf-8"))

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    alignment_path = reports_dir / "alignment.json"
    continuity_path = reports_dir / "continuity.json"
    causal_path = reports_dir / "causal.json"
    alignment_path.write_text(
        json.dumps(
            AlignmentReport(alignment_score=9.1, summary="新对齐").model_dump(mode="json")
            | {"source_text_hash": current_hash},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    continuity_path.write_text(
        json.dumps(
            ContinuityReport(continuity_score=9.2, summary="新连续").model_dump(mode="json")
            | {"source_text_hash": current_hash},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    causal_path.write_text(
        json.dumps(
            CausalValidationReport(causal_score=9.3, summary="新因果", issues=[]).model_dump(
                mode="json"
            )
            | {"source_text_hash": current_hash},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    async def _unexpected_refresh(**_kwargs: object) -> object:
        raise AssertionError("hash-bound reports should avoid LLM refresh")

    monkeypatch.setattr(
        chapter_flow_review,
        "refresh_quality_reports_after_semantic_text_change",
        _unexpected_refresh,
    )

    (
        alignment,
        continuity,
        causal,
        chapter_repair,
        rp_report,
    ) = await chapter_flow_review._ensure_reports_current_after_text_change(
        runner=SimpleNamespace(
            _storage=_Storage(),
            _on_step=lambda step, payload=None: events.append((step, payload)),
        ),
        bundle=SimpleNamespace(
            layout=SimpleNamespace(
                alignment_report_path=lambda _chapter: alignment_path,
                continuity_report_path=lambda _chapter: continuity_path,
                chapter_causal_report_path=lambda _chapter: causal_path,
            )
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        current_text=current_text,
        chapter_number=1,
        trace=SimpleNamespace(),
        alignment_report=AlignmentReport(alignment_score=1.0),
        continuity_report=ContinuityReport(continuity_score=1.0),
        causal_report=CausalValidationReport(causal_score=1.0, issues=[]),
        chapter_repair_report=ChapterRepairReport(source_text_hash="old"),
        reading_power_report=SimpleNamespace(overall_score=8.0),
        window_manager=None,
        window_config=None,
        stale_reason="reading_power_repair_text_changed",
    )

    assert alignment.alignment_score == pytest.approx(9.1)
    assert continuity.continuity_score == pytest.approx(9.2)
    assert causal is not None and causal.causal_score == pytest.approx(9.3)
    assert chapter_repair is None
    assert rp_report.overall_score == pytest.approx(8.0)
    assert events[0][0] == "quality_reports_rebound_after_text_change"
    assert events[0][1]["source"] == "persisted_hash_match"


@pytest.mark.asyncio
async def test_rp_text_change_refreshes_reports_when_hash_bound_reports_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current_text = "追读力修复后正文。" * 20
    current_hash = chapter_flow_review.source_text_hash(current_text)
    refresh_calls: list[dict[str, object]] = []

    class _Storage:
        def load_json(self, _path: Path) -> dict[str, object]:
            raise AssertionError("no persisted report should be loaded")

    async def _fake_refresh(**kwargs: object) -> object:
        refresh_calls.append(dict(kwargs))
        return SimpleNamespace(
            alignment_report=AlignmentReport(alignment_score=8.8, source_text_hash=current_hash),
            continuity_report=ContinuityReport(
                continuity_score=8.9,
                source_text_hash=current_hash,
            ),
            causal_report=CausalValidationReport(
                causal_score=9.0,
                issues=[],
                source_text_hash=current_hash,
            ),
            chapter_repair_report=None,
            reading_power_report=SimpleNamespace(overall_score=8.1),
        )

    monkeypatch.setattr(
        chapter_flow_review,
        "refresh_quality_reports_after_semantic_text_change",
        _fake_refresh,
    )

    (
        alignment,
        continuity,
        causal,
        chapter_repair,
        rp_report,
    ) = await chapter_flow_review._ensure_reports_current_after_text_change(
        runner=SimpleNamespace(_storage=_Storage(), _on_step=lambda *_args: None),
        bundle=SimpleNamespace(
            layout=SimpleNamespace(
                alignment_report_path=lambda _chapter: tmp_path / "missing_alignment.json",
                continuity_report_path=lambda _chapter: tmp_path / "missing_continuity.json",
                chapter_causal_report_path=lambda _chapter: tmp_path / "missing_causal.json",
            )
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        current_text=current_text,
        chapter_number=2,
        trace=SimpleNamespace(),
        alignment_report=AlignmentReport(alignment_score=1.0),
        continuity_report=ContinuityReport(continuity_score=1.0),
        causal_report=CausalValidationReport(causal_score=1.0, issues=[]),
        chapter_repair_report=ChapterRepairReport(source_text_hash="old"),
        reading_power_report=SimpleNamespace(overall_score=7.0),
        window_manager=SimpleNamespace(),
        window_config=None,
        stale_reason="reading_power_repair_text_changed",
    )

    assert refresh_calls
    assert refresh_calls[0]["stale_reason"] == "reading_power_repair_text_changed"
    assert alignment.source_text_hash == current_hash
    assert continuity.source_text_hash == current_hash
    assert causal is not None and causal.source_text_hash == current_hash
    assert chapter_repair is None
    assert rp_report.overall_score == pytest.approx(8.1)


@pytest.mark.asyncio
async def test_pre_wave_chapter_check_writes_diagnostics_not_authoritative_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved_json: dict[Path, dict[str, object]] = {}
    saved_text: dict[Path, str] = {}

    class _Storage:
        def save_text(self, path: Path, text: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            saved_text[path] = text

        def save_json(self, path: Path, payload: dict[str, object]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            saved_json[path] = payload

    class _DraftStep:
        async def run(self, _input: object) -> object:
            return SimpleNamespace(text="生成阶段正文。" * 80)

    class _ChapterRepairStep:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self, _input: object) -> ChapterRepairReport:
            return ChapterRepairReport(prompt_leaks=["模板残留"], repair_actions=["删除模板残留"])

    monkeypatch.setattr(draft_stage, "dump_story_bible_for_prompt", lambda _story_bible: {})
    monkeypatch.setattr(draft_stage, "format_address_rules_for_prompt", lambda _story_bible: "")
    monkeypatch.setattr(draft_stage, "render_world_context_rules", lambda _story_bible: "")

    import novel_forge.pipeline.long.services.constraints.constraint_router as constraint_router
    import novel_forge.pipeline.long.services.context.source_artifacts as source_artifacts
    import novel_forge.pipeline.steps.check_chapter_step as check_chapter_step

    monkeypatch.setattr(constraint_router, "build_draft_cards", lambda **_kwargs: {})
    monkeypatch.setattr(source_artifacts, "load_stage_artifact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(source_artifacts, "persist_stage_artifact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(check_chapter_step, "ChapterRepairStep", _ChapterRepairStep)

    layout = SimpleNamespace(
        reports_dir=tmp_path / "reports",
        chapter_draft_path=lambda chapter, version: tmp_path / f"draft_{chapter}_{version}.md",
        chapter_repair_report_path=lambda chapter: tmp_path / "reports" / f"repair_{chapter}.json",
    )
    ctx = draft_stage.GenerateContext(
        runner=SimpleNamespace(
            _config=SimpleNamespace(writing_mode="whole_chapter"),
            _settings=SimpleNamespace(
                long_pre_wave_chapter_check_enabled=True,
                long_check_chapter_enabled=True,
                expression_channel_detection_enabled=True,
            ),
            _storage=_Storage(),
            _router=object(),
            _builder=object(),
            _on_step=lambda *_args: None,
        ),
        bundle=SimpleNamespace(
            layout=layout,
            chapter_outline=SimpleNamespace(chapter_number=4, pov_character="林远"),
            story_bible=SimpleNamespace(),
            style_profile=None,
            editorial_contract=None,
            editorial_readiness=None,
            narrative_contract=None,
            pov_hint="",
        ),
        packet=SimpleNamespace(
            canon_context={},
            character_profiles=[],
            previous_chapter_ending="",
            known_characters=[],
        ),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        chapter_number=4,
        trace=SimpleNamespace(),
        planning_hints=None,
        reading_power_hint=None,
        draft_step=_DraftStep(),
        target_word_count=600,
        budgeted_plan=SimpleNamespace(
            forbidden_elements=[],
            forbidden_elements_soft=[],
            expression_channel_records=[],
            intentional_callbacks=[],
            scene_intents=[],
        ),
        draft_memory_hints={},
        draft_canon={},
        prev_known_issues=[],
        focus_ids=[],
        element_selection_payload=None,
        weak_senses=[],
        draft_kernel_context={},
        chapter_repair_kernel_context={},
        wave_kernel_context={},
        chapter_position={},
    )

    result = await draft_stage.generate_draft(ctx)

    authoritative_path = layout.chapter_repair_report_path(4)
    diagnostic_path = layout.reports_dir / "chapter_004_pre_wave_chapter_repair_report.json"
    assert result.chapter_repair_report is not None
    assert authoritative_path not in saved_json
    assert diagnostic_path in saved_json
    assert saved_json[diagnostic_path]["pipeline_stage"] == "pre_wave_draft_check"
    assert saved_json[diagnostic_path]["diagnostic_only"] is True


def test_build_repair_metrics_payload_summarizes_repair_dimensions() -> None:
    continuity_result = SimpleNamespace(
        rounds_used=2,
        continuity_repair=SimpleNamespace(applied=True),
        repair_exhausted=False,
        best_effort_accepted=False,
        needs_human_review=False,
        rollback_history=[],
    )
    causal_result = SimpleNamespace(
        rounds_used=1,
        applied=False,
        rolled_back=True,
        repair_exhausted=True,
        best_effort_accepted=True,
        needs_human_review=True,
    )
    reading_power_result = SimpleNamespace(
        rounds_used=1,
        repair_exhausted=False,
        best_effort_accepted=False,
        needs_human_review=False,
        applied=True,
        rolled_back=False,
        report=SimpleNamespace(overall_score=6.35, issues=["hook_missing"]),
    )
    pre_wave_check = SimpleNamespace(
        review_mode="full_review",
        risk_level="medium",
        prompt_leaks=["模板残留"],
        factual_errors=[],
        continuity_errors=[],
        expression_errors=["动作重复"],
        forbidden_element_candidates=[],
        forbidden_element_findings=[],
        repair_tickets=[],
        repair_actions=["删除模板残留"],
    )

    payload = _build_repair_metrics_payload(
        chapter_number=26,
        continuity_result=continuity_result,
        continuity_report=SimpleNamespace(continuity_score=9.25, issues=[]),
        causal_result=causal_result,
        causal_report=SimpleNamespace(causal_score=7.1, issues=["motivation_gap"]),
        reading_power_result=reading_power_result,
        total_rounds_used=4,
        total_rounds_cap=8,
        cumulative_change_ratio=0.12345,
        repair_exhausted=True,
        skipped_quality_stage=False,
        draft_meta={"text_chars": 1200, "scene_stitch_report": {"scene_count": 3}},
        wave_meta={
            "warnings": ["cross-ref miss"],
            "cross_ref_hits": ["scene-1"],
            "scenes_woven": 3,
            "woven_chars": 1500,
            "final_word_count": 1200,
        },
        pre_wave_chapter_repair_report=pre_wave_check,
        performed_edits=1,
        writing_mode="scene_level",
        text_change_history=[
            {
                "stage": "continuity_repair",
                "before_hash": "before",
                "after_hash": "after",
                "changed": True,
                "applied": True,
            }
        ],
    )

    assert payload["schema_version"] == 2
    assert payload["chapter"] == 26
    assert payload["generate"]["writing_mode"] == "scene_level"
    assert payload["generate"]["draft"]["scene_stitch_report_available"] is True
    assert payload["generate"]["wave"]["warnings_count"] == 1
    assert payload["generate"]["pre_wave_chapter_check"]["issues_found"] is True
    assert payload["generate"]["pre_wave_chapter_check"]["issue_count"] == 2
    assert payload["total_rounds_used"] == 4
    assert payload["cumulative_change_ratio"] == 0.1235
    assert payload["post_wave_repair"]["any_loop_entered"] is True
    assert payload["post_wave_repair"]["any_text_changed"] is True
    assert payload["post_wave_repair"]["any_rolled_back"] is True
    assert payload["dimensions"]["continuity"]["applied"] is True
    assert payload["dimensions"]["continuity"]["score_after"] == 9.25
    assert payload["dimensions"]["continuity"]["settled"] is True
    assert payload["dimensions"]["causal"]["rolled_back"] is True
    assert payload["dimensions"]["causal"]["best_effort_accepted"] is True
    assert payload["dimensions"]["causal"]["issue_count_after"] == 1
    assert payload["dimensions"]["reading_power"]["rounds_used"] == 1
    assert payload["dimensions"]["reading_power"]["score_after"] == 6.35
    assert payload["text_change_history"][0]["stage"] == "continuity_repair"


def test_normalized_review_contracts_do_not_duplicate_existing_knowledge_ticket() -> None:
    finding = ReviewFinding(
        finding_id="kb_finding",
        chapter_number=4,
        source_module="knowledge_boundary_audit",
        dimension="knowledge_boundary",
        issue_type="knowledge_leak",
        severity="high",
        summary="角色提前知道了秘密",
        evidence_quote="她知道账册藏在门后。",
        repair_goal="移除越界认知。",
        blocks_finalize=True,
        signature="kb_sig",
    )
    existing_ticket = RepairTicket(
        ticket_id="ticket_kb_finding",
        chapter_number=4,
        finding_ids=["kb_finding"],
        source_module="knowledge_boundary_audit",
        dimension="knowledge_boundary",
        issue_type="knowledge_leak",
        severity="high",
        target_summary="角色提前知道了秘密",
        repair_goal="移除越界认知。",
        repair_mode="window",
        blocking=True,
    )
    review = SimpleNamespace(
        current_text="她知道账册藏在门后。",
        review_findings=[finding],
        repair_tickets=[existing_ticket],
        alignment_report=None,
        chapter_repair_report=None,
        continuity_report=None,
        causal_report=None,
        reading_power_report=None,
    )

    findings, tickets = _build_normalized_review_contracts(review, chapter_number=4)

    assert [item.finding_id for item in findings if item.dimension == "knowledge_boundary"] == [
        "kb_finding"
    ]
    assert [item.ticket_id for item in tickets if item.dimension == "knowledge_boundary"] == [
        "ticket_kb_finding"
    ]


@pytest.mark.asyncio
async def test_persist_results_blocks_on_knowledge_boundary_hard_gate(monkeypatch) -> None:
    async def _no_prompt_leak_repair(**kwargs):
        return PromptLeakRepairResult(
            text=kwargs["current_text"],
            chapter_repair_report=kwargs["chapter_repair_report"],
        )

    async def _skip_contract_audit(*_args, **_kwargs) -> None:
        return None

    blocking_finding = ReviewFinding(
        finding_id="knowledge_boundary_4_1_secret",
        chapter_number=4,
        source_module="knowledge_boundary_audit",
        dimension="knowledge_boundary",
        issue_type="knowledge_boundary_leak",
        severity="high",
        confidence=0.95,
        summary="非 POV 角色秘密进入正文判断。",
        evidence_quote="她知道账册藏在门后。",
        repair_goal="移除越界认知。",
        blocks_finalize=True,
    )

    async def _blocking_knowledge_audit(**kwargs):
        assert kwargs["stage"] == "archive_pre_persist"
        assert kwargs["block_high_confidence"] is True
        return [blocking_finding]

    async def _exhausted_knowledge_repair(**kwargs):
        from novel_forge.pipeline.long.stages.knowledge_boundary_repair import (
            KnowledgeBoundaryRepairResult,
        )

        return KnowledgeBoundaryRepairResult(
            current_text=kwargs["current_text"],
            repair_exhausted=True,
            rounds_used=1,
            findings_before=[blocking_finding],
            findings_after=[blocking_finding],
            repair_attempted=True,
        )

    monkeypatch.setattr(
        finalize_stage,
        "repair_confirmed_prompt_leaks_with_patch",
        _no_prompt_leak_repair,
    )
    monkeypatch.setattr(
        finalize_stage,
        "_clean_and_validate_chapter_text",
        lambda _runner, _bundle, _chapter_number, current_text, **_kwargs: current_text,
    )
    monkeypatch.setattr(
        finalize_stage,
        "_run_contract_execution_audit_without_state_adjudication",
        _skip_contract_audit,
    )
    monkeypatch.setattr(
        finalize_stage,
        "run_knowledge_boundary_audit",
        _blocking_knowledge_audit,
    )
    monkeypatch.setattr(
        finalize_stage,
        "run_knowledge_boundary_repair_loop",
        _exhausted_knowledge_repair,
    )

    class _Runner:
        def __init__(self) -> None:
            self._router = None
            self._builder = None
            self._settings = SimpleNamespace(narrative_state_enabled=False)
            self._storage = SimpleNamespace()
            self.events: list[tuple[str, object]] = []

        def _on_step(self, step: str, data: object) -> None:
            self.events.append((step, data))

    runner = _Runner()
    bundle = SimpleNamespace(style_profile=None, layout=SimpleNamespace(root=Path("/tmp")))

    with pytest.raises(ConsistencyViolationError) as exc:
        await finalize_stage.persist_results(
            runner=runner,
            bundle=bundle,
            packet=SimpleNamespace(chapter_contract={}),
            bridge=None,
            plan=None,
            outcome=SimpleNamespace(),
            current_text="她知道账册藏在门后。",
            performed_edits=0,
            alignment_report=None,
            chapter_repair_report=None,
            continuity_report=None,
            causal_report=None,
            repair_plan=None,
            trace=SimpleNamespace(add=lambda *_a, **_k: None),
            chapter_number=4,
        )

    assert "knowledge_boundary_leak" in exc.value.violations[0]
    blocked_events = [event for event in runner.events if event[0] == "knowledge_boundary_blocked"]
    assert blocked_events
    assert blocked_events[0][1]["blockers"][0].startswith("[high] knowledge_boundary_leak")


def test_enforce_alignment_threshold_records_pass_event() -> None:
    runner = _RunnerStub(threshold=7.5)

    _enforce_alignment_threshold(
        runner=runner,
        chapter_number=6,
        alignment_score=8.1,
        repair_actions=[],
    )

    assert runner.events
    step, payload = runner.events[-1]
    assert step == "alignment_threshold_check"
    assert isinstance(payload, dict)
    assert payload["passed"] is True


def test_enforce_alignment_threshold_raises_on_low_score() -> None:
    runner = _RunnerStub(threshold=7.0)

    with pytest.raises(ConsistencyViolationError) as exc:
        _enforce_alignment_threshold(
            runner=runner,
            chapter_number=6,
            alignment_score=6.4,
            repair_actions=["补齐主线转折点"],
        )

    assert "低于阈值" in exc.value.violations[0]
    assert "补齐主线转折点" in exc.value.violations[0]


def test_guidance_report_mismatch_sources_uses_report_metadata() -> None:
    findings = [
        SimpleNamespace(
            issue_type="report_issue_summary_mismatch",
            source_module="guidance_contract_audit",
            metadata={"source_module": "causal_report"},
        ),
        SimpleNamespace(
            issue_type="outline_main_point_missing",
            source_module="check_alignment",
            metadata={},
        ),
    ]

    assert _guidance_report_mismatch_sources(findings) == {"causal_report"}


@pytest.mark.asyncio
async def test_contract_audit_block_can_run_targeted_repair_before_replan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    original_text = "第一段。\n\n沈知微想，如果后续要采集数据，他应该是最合适的人选。\n\n第三段。"
    repaired_text = "第一段。\n\n沈知微只把眼前的脉案记下，暂不越过今日问诊。\n\n第三段。"

    class _Storage:
        def load_json(self, _path: object) -> dict:
            return {
                "should_block_archive": True,
                "repair_or_replan_decision": "repair",
                "severity": "high",
                "contract_completion_score": 9.0,
                "future_leak_hits": ["沈知微后续数据合作意向"],
                "forbidden_progression_hits": [],
                "evidence_quotes": ["如果后续要采集数据，他应该是最合适的人选。"],
            }

    async def _fake_run_continuity_repair(_step: object, payload: object) -> SimpleNamespace:
        assert payload.must_fix_issues[0].paragraph_start == 2
        return SimpleNamespace(
            applied=True,
            revised_text=repaired_text,
            repair_plan=SimpleNamespace(name="contract-audit-repair"),
            failure_reason="",
        )

    async def _fake_extract_and_validate(*_args, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(refreshed=True)

    monkeypatch.setattr(
        "novel_forge.pipeline.long.repair.run_continuity_repair",
        _fake_run_continuity_repair,
    )
    monkeypatch.setattr(chapter_flow_finalize, "extract_and_validate", _fake_extract_and_validate)

    layout = SimpleNamespace(contract_execution_report_path=lambda _chapter: "report.json")
    bundle = SimpleNamespace(
        layout=layout,
        chapter_outline=SimpleNamespace(chapter_number=2, title="第二章", goal="目标"),
        style_profile=None,
    )
    prepared = PreparedChapterArtifacts(
        bundle=bundle,
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
    )
    review = ChapterReviewArtifacts(
        prepared=prepared,
        current_text=original_text,
        performed_edits=1,
        outcome=SimpleNamespace(),
        alignment_report=SimpleNamespace(),
        chapter_repair_report=None,
        continuity_report=ContinuityReport(continuity_score=9.0, summary="ok", issues=[]),
        repair_plan=SimpleNamespace(name="before"),
    )
    context = SimpleNamespace(
        storage=_Storage(),
        router=object(),
        builder=object(),
        settings=SimpleNamespace(),
        config=SimpleNamespace(),
        merger=object(),
        rules=object(),
        on_step=lambda step, payload: events.append((step, payload)),
    )

    repair_attempt = await _repair_contract_execution_audit_block(
        context,
        review=review,
        trace=SimpleNamespace(),
    )

    assert repair_attempt.status == "repaired"
    repaired = repair_attempt.review
    assert repaired is not None
    assert repaired.current_text == repaired_text
    assert repaired.outcome.refreshed is True
    assert repaired.repair_tickets
    assert repaired.repair_tickets[0].source_module == "contract_execution_audit"
    assert repaired.quality_reports_stale_after_text_change is True
    assert repaired.quality_reports_stale_reason == "contract_execution_repair_before_archive"
    assert [step for step, _payload in events] == [
        "contract_execution_repair_start",
        "contract_execution_repair_complete",
    ]


def test_archive_policy_blocks_extremely_low_reading_power() -> None:
    review = SimpleNamespace(
        reading_power_report=SimpleNamespace(
            chapter=3,
            overall_score=2.4,
            hook_type="mystery",
            hook_strength="medium",
            prev_hook_fulfilled=True,
            micro_payoffs=["线索兑现"],
            is_fallback=False,
        ),
        guard_compliance_report=None,
    )
    settings = SimpleNamespace(
        long_reading_power_archive_policy="floor_only",
        long_reading_power_hard_block_threshold=3.0,
        long_guard_archive_policy="warn",
    )

    messages = _archive_policy_block_messages(review, settings=settings, chapter_number=3)

    assert messages
    assert "追读力分 2.4" in messages[0]


def test_archive_policy_can_keep_reader_pull_subjective() -> None:
    review = SimpleNamespace(
        reading_power_report=SimpleNamespace(
            chapter=3,
            overall_score=2.4,
            hook_type="none",
            hook_strength="weak",
            prev_hook_fulfilled=False,
            micro_payoffs=[],
            is_fallback=False,
        ),
        guard_compliance_report=None,
    )
    settings = SimpleNamespace(
        long_reading_power_archive_policy="off",
        long_reading_power_hard_block_threshold=3.0,
        long_guard_archive_policy="warn",
    )

    assert _archive_policy_block_messages(review, settings=settings, chapter_number=3) == []


def test_archive_policy_optionally_blocks_core_high_reading_power_issue() -> None:
    review = SimpleNamespace(
        reading_power_report=SimpleNamespace(
            chapter=3,
            overall_score=8.0,
            hook_type="none",
            hook_strength="medium",
            prev_hook_fulfilled=True,
            micro_payoffs=["线索兑现"],
            is_fallback=False,
        ),
        guard_compliance_report=None,
    )
    settings = SimpleNamespace(
        long_reading_power_archive_policy="floor_or_core_high",
        long_reading_power_hard_block_threshold=3.0,
        long_reading_power_archive_block_issue_types=["hook_missing"],
        long_guard_archive_policy="warn",
    )

    messages = _archive_policy_block_messages(review, settings=settings, chapter_number=3)

    assert messages
    assert "核心追读力阻断问题" in messages[0]


def test_archive_policy_optionally_blocks_actionable_guard_violation() -> None:
    current_text = "最终正文" * 80
    review = SimpleNamespace(
        current_text=current_text,
        reading_power_report=None,
        guard_compliance_report={
            "source_text_hash": chapter_flow.source_text_hash(current_text),
            "compliance_results": [
                {
                    "constraint": "必须回应上一章留下的行动交接",
                    "status": "non_compliant",
                    "confidence": 0.91,
                    "repairable": True,
                }
            ],
        },
    )
    settings = SimpleNamespace(
        long_reading_power_archive_policy="off",
        long_guard_archive_policy="block_actionable",
        long_guard_archive_block_statuses=["non_compliant"],
        long_guard_archive_block_min_confidence=0.8,
    )

    messages = _archive_policy_block_messages(review, settings=settings, chapter_number=3)

    assert messages
    assert "AI 护栏违约" in messages[0]


def test_archive_policy_skips_stale_guard_report_hash() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    review = SimpleNamespace(
        current_text="最终正文" * 80,
        reading_power_report=None,
        guard_compliance_report={
            "source_text_hash": chapter_flow.source_text_hash("旧正文" * 80),
            "compliance_results": [
                {
                    "constraint": "必须回应上一章留下的行动交接",
                    "status": "non_compliant",
                    "confidence": 0.99,
                    "repairable": True,
                }
            ],
        },
    )
    settings = SimpleNamespace(
        long_reading_power_archive_policy="off",
        long_guard_archive_policy="block_actionable",
        long_guard_archive_block_statuses=["non_compliant"],
        long_guard_archive_block_min_confidence=0.8,
    )

    messages = _archive_policy_block_messages(
        review,
        settings=settings,
        chapter_number=3,
        on_step=lambda step, payload: events.append((step, payload)),
    )

    assert messages == []
    assert events
    assert events[0][0] == "guard_report_stale_skipped"


def test_pre_final_evaluation_runs_when_auto_polish_can_trigger() -> None:
    settings = SimpleNamespace(
        long_polish_enabled=False,
        long_polish_auto_trigger_threshold=7.0,
    )

    assert _should_include_pre_final_evaluation(settings) is True


def test_pre_final_evaluation_can_be_fully_disabled() -> None:
    settings = SimpleNamespace(
        long_polish_enabled=False,
        long_polish_auto_trigger_threshold=0.0,
    )

    assert _should_include_pre_final_evaluation(settings) is False


def test_pre_final_evaluation_runs_for_manual_polish() -> None:
    settings = SimpleNamespace(
        long_polish_enabled=True,
        long_polish_auto_trigger_threshold=0.0,
    )

    assert _should_include_pre_final_evaluation(settings) is True


def test_causal_high_score_skip_result_accepts_clean_high_score_report() -> None:
    current_text = "最终正文" * 120
    source_hash = chapter_flow.source_text_hash(current_text)
    causal_report = CausalValidationReport(
        causal_score=9.8,
        validation_status="ok",
        source_text_hash=source_hash,
        issues=[],
    )
    alignment_report = SimpleNamespace(alignment_score=9.1)
    continuity_report = ContinuityReport(continuity_score=9.2)
    events: list[tuple[str, dict[str, object]]] = []

    result = chapter_flow_review._causal_high_score_skip_result(
        settings=SimpleNamespace(
            long_causal_high_score_skip_enabled=True,
            long_causal_skip_threshold=9.5,
        ),
        initial_causal_report=causal_report,
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        chapter_repair_report=None,
        chapter_number=5,
        max_causal_rounds=2,
        on_step=lambda step, payload: events.append((step, payload)),
    )

    assert result is not None
    assert result.current_text == current_text
    assert result.causal_report is causal_report
    assert result.alignment_report is alignment_report
    assert result.continuity_report is continuity_report
    assert result.rounds_used == 0
    assert result.applied is False
    assert events[0][0] == "causal_repair_skipped_high_score"
    assert events[0][1]["score"] == 9.8
    assert events[0][1]["threshold"] == 9.5
    assert events[0][1]["source_text_hash"] == source_hash


def test_causal_high_score_skip_result_falls_back_on_unsafe_inputs() -> None:
    current_text = "最终正文" * 120
    source_hash = chapter_flow.source_text_hash(current_text)

    def _attempt(
        report: CausalValidationReport,
        *,
        enabled: bool = True,
        threshold: float = 9.5,
        max_rounds: int = 2,
    ) -> object | None:
        events: list[tuple[str, dict[str, object]]] = []
        result = chapter_flow_review._causal_high_score_skip_result(
            settings=SimpleNamespace(
                long_causal_high_score_skip_enabled=enabled,
                long_causal_skip_threshold=threshold,
            ),
            initial_causal_report=report,
            current_text=current_text,
            alignment_report=SimpleNamespace(alignment_score=9.1),
            continuity_report=ContinuityReport(continuity_score=9.2),
            chapter_repair_report=None,
            chapter_number=5,
            max_causal_rounds=max_rounds,
            on_step=lambda step, payload: events.append((step, payload)),
        )
        assert events == []
        return result

    assert (
        _attempt(
            CausalValidationReport(
                causal_score=9.8,
                source_text_hash=source_hash,
                issues=[CausalIssue(severity="high")],
            )
        )
        is None
    )
    assert (
        _attempt(
            CausalValidationReport(
                causal_score=9.8,
                validation_status="unavailable",
                source_text_hash=source_hash,
            )
        )
        is None
    )
    assert (
        _attempt(
            CausalValidationReport(
                causal_score=9.8,
                source_text_hash=chapter_flow.source_text_hash("旧正文"),
            )
        )
        is None
    )
    assert (
        _attempt(
            CausalValidationReport(causal_score=9.8, source_text_hash=source_hash),
            enabled=False,
        )
        is None
    )
    assert (
        _attempt(
            CausalValidationReport(causal_score=9.8, source_text_hash=source_hash),
            threshold=0.0,
        )
        is None
    )
    assert (
        _attempt(
            CausalValidationReport(causal_score=9.8, source_text_hash=source_hash),
            max_rounds=0,
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "threshold", "score", "expected_reason"),
    [
        (True, 0.0, 9.0, "long_polish_enabled"),
        (False, 7.0, 6.9, "auto_score_below_threshold"),
        (False, 0.0, 1.0, None),
    ],
)
async def test_run_polish_uses_enabled_or_auto_threshold(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    threshold: float,
    score: float,
    expected_reason: str | None,
) -> None:
    calls: list[str] = []

    async def _fake_polish_layer(*_args, **kwargs):
        calls.append(kwargs["trigger_reason"])
        return "精修后正文", True

    monkeypatch.setattr(chapter_flow_review, "run_post_repair_polish_layer", _fake_polish_layer)
    state = chapter_flow._ReviewPhaseState(current_text="原正文")

    result = await chapter_flow._run_polish(
        context=SimpleNamespace(
            settings=SimpleNamespace(
                long_polish_enabled=enabled,
                long_polish_auto_trigger_threshold=threshold,
            )
        ),
        prepared=SimpleNamespace(),
        state=state,
        trace=SimpleNamespace(),
        eval_report=EvalReport(overall_score=score, passed=score >= 7.0),
    )

    assert calls == ([expected_reason] if expected_reason else [])
    assert result.current_text == ("精修后正文" if expected_reason else "原正文")
    assert result.post_repair_polish_modified_text is bool(expected_reason)


def test_kernel_persist_pending_marker_is_detected(tmp_path) -> None:
    project_dir = tmp_path / "demo"
    states_dir = project_dir / "states"
    states_dir.mkdir(parents=True)
    marker_path = states_dir / "kernel_persist_pending_ch2.json"
    marker_path.write_text(
        json.dumps(
            {
                "chapter_number": 2,
                "source_text_hash": "abc123",
                "error_type": "RuntimeError",
                "error": "sqlite locked",
                "requires_replay": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    events: list[tuple[str, dict[str, object]]] = []
    runner = SimpleNamespace(
        _storage=FileSystemStorage(tmp_path),
        _on_step=lambda step, payload: events.append((step, payload)),
    )

    _detect_kernel_persist_pending_marker(runner, "demo", 3)

    assert events
    assert events[0][0] == "kernel_persist_pending_detected"
    assert events[0][1]["pending_chapter"] == 2
    assert events[0][1]["requires_replay"] is True


def test_total_repair_rounds_cap_uses_settings_default_when_missing() -> None:
    assert resolve_total_repair_rounds_cap(SimpleNamespace()) == 5


def test_review_phase_state_groups_legacy_fields() -> None:
    state = chapter_flow._ReviewPhaseState(current_text="原正文", total_repair_rounds_used=2)

    state.current_text = "新正文"
    state.alignment_report = SimpleNamespace(alignment_score=8.8)
    state.review_warnings.append("提示")
    state.total_rounds_cap = 5
    state.text_change_history.append({"stage": "manual_test", "applied": True})

    assert state.text.current_text == "新正文"
    assert state.text.text_change_history == [{"stage": "manual_test", "applied": True}]
    assert state.reports.alignment_report.alignment_score == 8.8
    assert state.diagnostics.review_warnings == ["提示"]
    assert state.budget.total_repair_rounds_used == 2
    assert state.budget.total_rounds_cap == 5


@pytest.mark.asyncio
async def test_terminal_word_count_polish_marks_quality_reports_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, object]] = []
    saved_texts: list[str] = []

    class _Storage:
        def exists(self, _path: object) -> bool:
            return False

        def save_json(self, _path: object, _payload: object) -> None:
            return None

        def save_text(self, _path: object, text: str) -> None:
            saved_texts.append(text)

    class _Assessment:
        def __init__(self, actual: int, target: int, band: str = "soft") -> None:
            self.actual = actual
            self.target = target
            self.band = band

        def as_dict(self) -> dict[str, object]:
            return {"actual": self.actual, "target": self.target, "band": self.band}

    class _WordCountResult:
        accepted = True
        changed = True
        text = "字数精修后正文"
        mode = "expand"
        reason = "accepted"
        before = _Assessment(300, 600)
        after = _Assessment(590, 600)

        def event_payload(self) -> dict[str, object]:
            return {"accepted": True, "changed": True}

    async def _fake_word_count(**_kwargs: object) -> object:
        return _WordCountResult()

    async def _fake_extract(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(name="new_outcome")

    async def _fake_eval(*_args: object, **_kwargs: object) -> EvalReport:
        return EvalReport(overall_score=8.0, passed=True)

    monkeypatch.setattr(
        chapter_flow_orchestrate,
        "run_word_count_restructure",
        _fake_word_count,
    )
    monkeypatch.setattr(chapter_flow_orchestrate, "extract_and_validate", _fake_extract)
    monkeypatch.setattr(chapter_flow_orchestrate, "evaluate_chapter_text", _fake_eval)

    layout = SimpleNamespace(
        states_dir=tmp_path / "states",
        chapter_draft_path=lambda chapter, version: tmp_path / f"draft_{chapter}_{version}.md",
    )
    layout.states_dir.mkdir()
    prepared = PreparedChapterArtifacts(
        bundle=SimpleNamespace(
            layout=layout,
            chapter_outline=SimpleNamespace(chapter_number=3),
        ),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
    )
    review = ChapterReviewArtifacts(
        prepared=prepared,
        current_text="字数精修前正文",
        performed_edits=1,
        outcome=SimpleNamespace(name="old_outcome"),
        alignment_report=SimpleNamespace(),
        chapter_repair_report=None,
        continuity_report=SimpleNamespace(),
        causal_report=None,
        repair_plan=SimpleNamespace(),
    )
    context = SimpleNamespace(
        storage=_Storage(),
        router=SimpleNamespace(route=lambda *_args, **_kwargs: None),
        builder=SimpleNamespace(build=lambda *_args, **_kwargs: None),
        settings=SimpleNamespace(long_word_count_archive_gate_enabled=True),
        on_step=lambda step, payload=None: events.append((step, payload)),
    )

    result = await chapter_flow._apply_terminal_word_count_polish(
        context,
        review,
        SimpleNamespace(),
    )

    text_change_event = next(
        payload for step, payload in events if step == "text_changed_before_archive"
    )
    assert text_change_event["reason"] == "word_count_polish_before_archive"
    assert text_change_event["refresh_quality"] is True
    assert result.quality_reports_stale_after_text_change is True
    assert result.quality_reports_stale_reason == "word_count_polish_before_archive"
    assert saved_texts == ["字数精修后正文"]


def test_quality_gate_payload_declares_report_is_not_archive_blocker(tmp_path) -> None:
    saved: dict[str, object] = {}

    class _Storage:
        def save_json(self, path, payload):  # noqa: ANN001
            saved[str(path)] = payload

    class _Layout:
        def quality_gate_report_path(self, chapter_number: int):
            return tmp_path / f"chapter_{chapter_number:03d}_quality_gate.json"

    gate = QualityGate()
    gate.check_word_count(current_words=100, target_words=300)

    payload = _persist_quality_gate_report(
        _Storage(),
        _Layout(),
        3,
        gate,
    )

    assert payload["report_type"] == "post_review_quality_report"
    assert payload["archive_blocking"] is False
    assert payload["failed_dimensions"] == ["word_count"]
    assert any(path.endswith("chapter_003_quality_gate.json") for path in saved)


def test_quality_gate_flags_low_plot_progression_dimension(tmp_path) -> None:
    review = SimpleNamespace(
        eval_report=EvalReport(
            scores=[
                EvalScore(dimension="consistency", score=7.0),
                EvalScore(dimension="plot_progression", score=4.0),
            ],
            overall_score=6.5,
            passed=True,
            threshold=6.0,
        ),
        current_text="这一章主要重复旧信息，没有实质变化。",
        alignment_report=SimpleNamespace(alignment_score=8.0, issues=[]),
        continuity_report=SimpleNamespace(continuity_score=8.0, issues=[]),
        causal_report=None,
        chapter_repair_report=None,
        reading_power_report=None,
        guard_compliance_report=None,
        prepared=SimpleNamespace(
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(chapter_number=3),
                layout=SimpleNamespace(
                    state_adjudication_report_path=lambda _chapter: tmp_path / "missing.json"
                ),
            )
        ),
    )
    settings = SimpleNamespace(
        max_revelations_per_chapter=2,
        long_plot_progression_quality_floor=5.5,
    )

    gate = _build_quality_gate(review, settings=settings, target_word_count=0)
    report = gate.report()

    plot_check = next(c for c in report.checks if c.dimension == "eval_plot_progression")
    assert plot_check.passed is False
    assert "plot_progression" in report.summary


def test_quality_gate_skips_word_count_when_archive_gate_disabled(tmp_path) -> None:
    review = SimpleNamespace(
        eval_report=None,
        current_text="字" * 2000,
        alignment_report=SimpleNamespace(alignment_score=8.0, issues=[]),
        continuity_report=SimpleNamespace(continuity_score=8.0, issues=[]),
        causal_report=None,
        chapter_repair_report=None,
        reading_power_report=None,
        guard_compliance_report=None,
        prepared=SimpleNamespace(
            bundle=SimpleNamespace(
                chapter_outline=SimpleNamespace(chapter_number=3),
                layout=SimpleNamespace(
                    state_adjudication_report_path=lambda _chapter: tmp_path / "missing.json"
                ),
            )
        ),
    )
    settings = SimpleNamespace(
        max_revelations_per_chapter=2,
        long_word_count_archive_gate_enabled=False,
        long_plot_progression_quality_floor=5.5,
    )

    gate = _build_quality_gate(review, settings=settings, target_word_count=100)
    dimensions = [check.dimension for check in gate.report().checks]

    assert "word_count" not in dimensions


def test_save_next_chapter_constraints_persists_only_from_accepted_flow(tmp_path) -> None:
    saved: dict[str, object] = {}
    events: list[tuple[str, object]] = []

    class _Storage:
        def save_json(self, path, payload):  # noqa: ANN001
            saved[str(path)] = payload

    class _WindowManager:
        saved_state = False

        def get_next_chapter_constraints(self):
            return {"next_chapter_constraints": ["下章回应章尾钩子"]}

        def save_timeline_state(self):
            self.saved_state = True

    wm = _WindowManager()
    context = SimpleNamespace(
        storage=_Storage(),
        on_step=lambda step, data: events.append((step, data)),
    )
    prepared = SimpleNamespace(
        window_manager=wm,
        bundle=SimpleNamespace(layout=SimpleNamespace(root=tmp_path)),
    )

    _save_reading_power_next_chapter_constraints(context, prepared, chapter_number=3)

    assert wm.saved_state is True
    assert any(path.endswith("reading_power_constraints_ch4.json") for path in saved)
    assert events == [("reading_power_next_chapter_constraints", {"chapter": 3, "next_chapter": 4})]


def test_input_integrity_gate_blocks_missing_semantic_sources() -> None:
    events: list[tuple[str, object]] = []
    context = SimpleNamespace(on_step=lambda step, data: events.append((step, data)))
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            pov_character="",
            goal="",
            setting="旧城档案馆",
            involved_characters=["沈念卿"],
        )
    )

    with pytest.raises(ConsistencyViolationError) as exc_info:
        chapter_flow_orchestrate._emit_input_integrity_check(
            context,
            stage="planning_input",
            chapter_number=3,
            bundle=bundle,
            block_on_error=True,
        )

    exc = exc_info.value
    assert exc.violation_kind == "input_contract"
    assert exc.failed_stage == "planning_input"
    assert exc.replan_target is RecoveryTarget.MANUAL
    assert exc.block_kind == "input_integrity"
    assert "chapter_outline.pov_character" in " ".join(exc.violations)
    assert "chapter_outline.goal" in " ".join(exc.violations)
    assert events[-1][0] == "input_integrity_check"
    assert events[-1][1]["status"] == "error"  # type: ignore[index]
    assert events[-1][1]["recovery_target"] == RecoveryTarget.MANUAL.value  # type: ignore[index]
    assert events[-1][1]["missing_by_owner"]["source"] == [  # type: ignore[index]
        "chapter_outline.pov_character",
        "chapter_outline.goal",
    ]


@pytest.mark.asyncio
async def test_prepare_blocks_source_duration_conflict_before_packet_or_model_work(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, object]] = []
    saved: dict[str, object] = {}

    class _Storage:
        def save_json(self, path: object, payload: object) -> None:
            saved[str(path)] = payload

    memory = SimpleNamespace(build_packet=AsyncMock())
    context = SimpleNamespace(
        settings=SimpleNamespace(
            long_upstream_compass_enabled=True,
            long_upstream_compass_blocking=True,
        ),
        storage=_Storage(),
        on_step=lambda step, payload: events.append((step, payload)),
        compress_prompt_context=AsyncMock(),
    )
    bundle = SimpleNamespace(
        chapter_outline={
            "goal": "停职五日受审仪轨正式执行",
            "expected_hook": {"hook_description": "三日停职结束前决定复验顺序"},
        },
        layout=SimpleNamespace(reports_dir=tmp_path),
    )

    with pytest.raises(ConsistencyViolationError) as exc_info:
        await chapter_flow_orchestrate.prepare_chapter_plan(
            context,
            bundle=bundle,
            chapter_number=2,
            trace=SimpleNamespace(),
            memory=memory,
        )

    assert exc_info.value.violation_kind == "upstream_source_conflict"
    assert exc_info.value.replan_target is RecoveryTarget.MANUAL
    memory.build_packet.assert_not_awaited()
    context.compress_prompt_context.assert_not_awaited()
    assert events[0][0] == "upstream_source_preflight"
    assert any(path.endswith("chapter_002_upstream_compass.json") for path in saved)


def test_input_integrity_gate_replans_only_plan_owned_artifacts() -> None:
    context = SimpleNamespace(on_step=lambda _step, _data: None)
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            pov_character="沈念卿",
            goal="找出档案失窃者",
            setting="旧城档案馆",
            involved_characters=["沈念卿"],
        )
    )
    bridge = SimpleNamespace(
        bridge_summary="",
        opening_location="旧城档案馆",
        action_handoff="沈念卿推门进入阅览室",
        pending_questions=[],
    )
    plan = SimpleNamespace(
        scene_intents=[],
        opening_contract="",
        closing_contract="",
        required_state_transitions=[],
    )

    with pytest.raises(ConsistencyViolationError) as exc_info:
        chapter_flow_orchestrate._emit_input_integrity_check(
            context,
            stage="review_input",
            chapter_number=3,
            bundle=bundle,
            bridge=bridge,
            plan=plan,
            block_on_error=True,
        )

    assert exc_info.value.replan_target is RecoveryTarget.PLAN


def test_input_integrity_report_remains_advisory_for_diagnostics() -> None:
    events: list[tuple[str, object]] = []
    context = SimpleNamespace(on_step=lambda step, data: events.append((step, data)))
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            pov_character="",
            goal="",
            setting="",
            involved_characters=[],
        )
    )

    payload = chapter_flow_orchestrate._emit_input_integrity_check(
        context,
        stage="diagnostic_only",
        chapter_number=3,
        bundle=bundle,
    )

    assert payload["status"] == "error"
    assert payload["missing_required"] == [
        "chapter_outline.pov_character",
        "chapter_outline.goal",
    ]
