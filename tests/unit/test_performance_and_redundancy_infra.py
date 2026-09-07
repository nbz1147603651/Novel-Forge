from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from novel_forge.app_service.contracts import JobKind
from novel_forge.app_service.performance_metrics import (
    CountingStorageProxy,
    RunPerformanceMetrics,
)
from novel_forge.app_service.workspace_commands import command_registry
from novel_forge.core.review.review_contracts import source_text_hash
from novel_forge.core.schemas.chapter import AlignmentReport, CausalValidationReport
from novel_forge.core.schemas.continuity import ContinuityReport
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.desktop.jobs.records import DesktopJobRecord, WaitingJob
from novel_forge.pipeline.long import chapter_flow_finalize
from novel_forge.pipeline.long.stages.report_freshness import (
    ReportRefreshPlanner,
    quality_report_evidence_binding,
    stamp_report_freshness,
)
from novel_forge.pipeline.long.stages.report_refresh import ReviewReportService
from novel_forge.pipeline.repair_orchestration.loop_components import RepairLoopExecutor
from novel_forge.workspace.artifact_cache import ChapterArtifactBundleLoader, ChapterArtifactCache
from novel_forge.workspace.chapter_run_io import ChapterRunIOContext
from novel_forge.workspace.helpers.execution_io import _ChapterDataCache
from novel_forge.workspace.repair_review_verification import (
    build_causal_recheck_payload,
    build_continuity_recheck_payload,
)
from novel_forge.workspace.sessions.chapter_session_state import (
    ReviewProgressState,
    load_creative_report_payload,
    load_review_progress,
)


class _Storage:
    def __init__(self) -> None:
        self.loads = 0
        self.saves = 0

    def load_json(self, path: Path) -> dict[str, Any]:
        self.loads += 1
        return json.loads(path.read_text(encoding="utf-8"))

    def save_json(self, path: Path, data: dict[str, Any]) -> None:
        self.saves += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def exists(self, path: Path) -> bool:
        return path.exists()


class _SessionLayout:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.states_dir = root / "states"
        self.reports_dir = root / "reports"
        self.characters_path = root / "character_bible.json"
        self.style_profile_path = root / "style_profile.json"
        self.states_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def chapter_review_progress_path(self, chapter_number: int) -> Path:
        return self.states_dir / f"chapter_{chapter_number:03d}_review_progress.json"

    def creative_report_path(self, chapter_number: int) -> Path:
        return self.reports_dir / f"chapter_{chapter_number:03d}_creative.json"

    def alignment_report_path(self, chapter_number: int) -> Path:
        return self.reports_dir / f"chapter_{chapter_number:03d}_alignment.json"

    def continuity_report_path(self, chapter_number: int) -> Path:
        return self.reports_dir / f"chapter_{chapter_number:03d}_continuity.json"

    def chapter_causal_report_path(self, chapter_number: int) -> Path:
        return self.reports_dir / f"chapter_{chapter_number:03d}_causal.json"

    def reading_power_report_path(self, chapter_number: int) -> Path:
        return self.reports_dir / f"chapter_{chapter_number:03d}_reading_power.json"


class _ArtifactModel(BaseModel):
    name: str
    value: int


def test_waiting_job_is_constructible() -> None:
    record = DesktopJobRecord(job_id="j1", kind="run_chapter", label="job")
    waiting = WaitingJob(record=record, app_command=None)  # type: ignore[arg-type]

    assert waiting.record is record
    assert waiting.app_command is None


def test_workspace_command_registry_covers_all_job_kinds() -> None:
    registry = command_registry()

    assert set(registry) == set(JobKind)
    assert all(callable(spec.run_handler) for spec in registry.values())


def test_repair_and_reevaluate_commands_use_chapter_io_context() -> None:
    root = Path(__file__).resolve().parents[2]
    targets = [
        root / "novel_forge/workspace/repair_ops/execution_repair_continuity.py",
        root / "novel_forge/workspace/repair_ops/execution_repair_causal.py",
        root / "novel_forge/workspace/book_ops/execution_book_reevaluate.py",
        root / "novel_forge/workspace/execution_polish.py",
    ]

    for path in targets:
        source = path.read_text(encoding="utf-8")
        assert "from novel_forge.workspace.chapter_run_io import ChapterRunIOContext" in source
        assert "runtime.storage.load_json" not in source


def test_reevaluate_uses_review_report_service_for_review_reports() -> None:
    root = Path(__file__).resolve().parents[2]
    helper_source = (root / "novel_forge/workspace/review_report_refresh.py").read_text(
        encoding="utf-8"
    )
    sources = [
        (root / "novel_forge/workspace/book_ops/execution_book_reevaluate.py").read_text(encoding="utf-8"),
        (root / "novel_forge/workspace/execution_polish.py").read_text(encoding="utf-8"),
    ]

    assert "ReviewReportService" in helper_source
    for source in sources:
        assert "refresh_workspace_review_reports" in source
        for forbidden in (
            "AlignmentStep",
            "ContinuityEvalStep",
            "CausalValidationStep",
            "ReadingPowerEvalStep",
            "recheck_alignment",
        ):
            assert forbidden not in source


def test_repair_commands_use_targeted_review_verification_service() -> None:
    root = Path(__file__).resolve().parents[2]
    helper_source = (root / "novel_forge/workspace/repair_review_verification.py").read_text(
        encoding="utf-8"
    )
    sources = [
        (root / "novel_forge/workspace/repair_ops/execution_repair_continuity.py").read_text(encoding="utf-8"),
        (root / "novel_forge/workspace/repair_ops/execution_repair_causal.py").read_text(encoding="utf-8"),
        (root / "novel_forge/workspace/sessions/chapter_session_handlers.py").read_text(encoding="utf-8"),
    ]

    assert "ContinuityEvalStep" in helper_source
    assert "CausalValidationStep" in helper_source
    for source in sources:
        assert "run_targeted_" in source
        for forbidden in (
            "ContinuityEvalStep",
            "CausalValidationStep",
            "ContinuityEvalInput",
            "CausalValidationInput",
        ):
            assert forbidden not in source


def test_repair_recheck_payload_stamps_current_text_hash() -> None:
    current_text = "new chapter text"
    continuity_payload = build_continuity_recheck_payload(
        ContinuityReport(source_text_hash="old"),
        issues=[{"issue_type": "continuity", "summary": "kept"}],
        current_text=current_text,
    )
    causal_payload = build_causal_recheck_payload(
        CausalValidationReport(source_text_hash="old"),
        issues=[{"issue_type": "causal", "summary": "kept"}],
        current_text=current_text,
    )

    assert continuity_payload["source_text_hash"] == source_text_hash(current_text)
    assert causal_payload["source_text_hash"] == source_text_hash(current_text)
    assert continuity_payload["issues"] == [{"issue_type": "continuity", "summary": "kept"}]
    assert causal_payload["issues"] == [{"issue_type": "causal", "summary": "kept"}]


def test_report_refresh_planner_reuses_current_provided_report() -> None:
    text_hash = source_text_hash("chapter text")
    report = AlignmentReport(source_text_hash=text_hash)
    planner = ReportRefreshPlanner(storage=_Storage(), current_text_hash=text_hash)

    decision = planner.decide(
        dimension="alignment",
        provided=report,
        path=None,
        model=AlignmentReport,
    )

    assert decision.should_reuse
    assert decision.reason == "provided_current"
    assert decision.report == report


def test_report_refresh_planner_forces_refresh_when_hash_changes(tmp_path: Path) -> None:
    report_path = tmp_path / "alignment.json"
    report_path.write_text(
        json.dumps(
            AlignmentReport(source_text_hash=source_text_hash("old")).model_dump(mode="json")
        ),
        encoding="utf-8",
    )
    planner = ReportRefreshPlanner(
        storage=_Storage(),
        current_text_hash=source_text_hash("new"),
    )

    decision = planner.decide(
        dimension="alignment",
        provided=None,
        path=report_path,
        model=AlignmentReport,
    )

    assert not decision.should_reuse
    assert decision.action == "refresh"
    assert decision.reason == "stale_source_text_hash"


def test_report_refresh_planner_uses_optional_context_hash(tmp_path: Path) -> None:
    text_hash = source_text_hash("chapter")
    report_path = tmp_path / "alignment.json"
    report_path.write_text(
        json.dumps(
            {
                **AlignmentReport(source_text_hash=text_hash).model_dump(mode="json"),
                "source_text_hash": text_hash,
                "report_context_hash": "old-context",
            }
        ),
        encoding="utf-8",
    )
    planner = ReportRefreshPlanner(
        storage=_Storage(),
        current_text_hash=text_hash,
        context_hash="new-context",
    )

    decision = planner.decide(
        dimension="alignment",
        provided=None,
        path=report_path,
        model=AlignmentReport,
    )

    assert not decision.should_reuse
    assert decision.reason == "stale_context_hash"


def test_report_refresh_planner_refreshes_legacy_report_without_context_hash(
    tmp_path: Path,
) -> None:
    text_hash = source_text_hash("chapter")
    report_path = tmp_path / "alignment.json"
    report_path.write_text(
        json.dumps(
            {
                **AlignmentReport(source_text_hash=text_hash).model_dump(mode="json"),
                "source_text_hash": text_hash,
            }
        ),
        encoding="utf-8",
    )
    planner = ReportRefreshPlanner(
        storage=_Storage(),
        current_text_hash=text_hash,
        context_hash="new-context",
    )

    decision = planner.decide(
        dimension="alignment",
        provided=None,
        path=report_path,
        model=AlignmentReport,
    )

    assert not decision.should_reuse
    assert decision.reason == "missing_report_context_hash"


def test_quality_report_evidence_binding_is_stable_and_sensitive_for_object_context() -> None:
    first = quality_report_evidence_binding(
        packet=SimpleNamespace(state="same", nested=SimpleNamespace(day=5)),
        bridge=SimpleNamespace(previous="archived"),
        plan=SimpleNamespace(required_outcome="停职五日"),
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(goal="停职五日受审"),
            chapter_source_slice={"author_lock": "不改结局"},
        ),
    )
    equivalent = quality_report_evidence_binding(
        packet=SimpleNamespace(nested=SimpleNamespace(day=5), state="same"),
        bridge=SimpleNamespace(previous="archived"),
        plan=SimpleNamespace(required_outcome="停职五日"),
        bundle=SimpleNamespace(
            chapter_source_slice={"author_lock": "不改结局"},
            chapter_outline=SimpleNamespace(goal="停职五日受审"),
        ),
    )
    changed = quality_report_evidence_binding(
        packet=SimpleNamespace(state="same", nested=SimpleNamespace(day=5)),
        bridge=SimpleNamespace(previous="archived"),
        plan=SimpleNamespace(required_outcome="停职三日"),
        bundle=SimpleNamespace(
            chapter_outline=SimpleNamespace(goal="停职五日受审"),
            chapter_source_slice={"author_lock": "不改结局"},
        ),
    )

    assert first == equivalent
    assert first["context_hash"] != changed["context_hash"]
    assert first["evidence_hashes"]["plan"] != changed["evidence_hashes"]["plan"]


def test_chapter_artifact_cache_reuses_and_invalidates_by_file_stat(tmp_path: Path) -> None:
    storage = _Storage()
    cache = ChapterArtifactCache(storage)
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text('{"name": "first", "value": 1}', encoding="utf-8")

    first = cache.load_model(artifact_path, _ArtifactModel)
    second = cache.load_model(artifact_path, _ArtifactModel)

    assert first == second
    assert storage.loads == 1
    assert cache.stats()["hits"] == 1

    artifact_path.write_text('{"name": "second", "value": 2}', encoding="utf-8")
    third = cache.load_model(artifact_path, _ArtifactModel)

    assert third.name == "second"
    assert storage.loads == 2


def test_chapter_artifact_bundle_loader_returns_isolated_copies(tmp_path: Path) -> None:
    storage = _Storage()
    loader = ChapterArtifactBundleLoader(storage)
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text('{"name": "first", "value": 1}', encoding="utf-8")

    first = loader.load_json(artifact_path)
    first["value"] = 99
    second = loader.load_json(artifact_path)

    assert second["value"] == 1
    assert storage.loads == 1
    assert loader.stats()["hits"] == 1


def test_chapter_run_io_context_reuses_typed_artifacts(tmp_path: Path) -> None:
    storage = _Storage()
    layout = _SessionLayout(tmp_path)
    context = ChapterRunIOContext(
        storage=storage,
        layout=layout,
        project_id="proj",
        chapter_number=1,
        source="unit",
    )
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text('{"name": "first", "value": 1}', encoding="utf-8")

    first = context.load_model(artifact_path, _ArtifactModel)
    second = context.load_model(artifact_path, _ArtifactModel)
    first_json = context.load_json(artifact_path)
    first_json["value"] = 99
    second_json = context.load_json(artifact_path)

    assert first == second
    assert second_json["value"] == 1
    assert storage.loads == 2  # one model cache entry, one json cache entry
    assert context.stats()["hits"] == 2


def test_chapter_run_io_context_strips_report_freshness_metadata(tmp_path: Path) -> None:
    storage = _Storage()
    layout = _SessionLayout(tmp_path)
    context = ChapterRunIOContext(
        storage=storage,
        layout=layout,
        project_id="proj",
        chapter_number=1,
        source="unit",
    )
    report_path = layout.alignment_report_path(1)
    storage.save_json(
        report_path,
        {
            **AlignmentReport(source_text_hash=source_text_hash("chapter")).model_dump(mode="json"),
            "canon_state_hash": "canon",
            "report_context_hash": "ctx",
            "report_freshness": {"context_hash": "ctx"},
        },
    )

    report = context.load_model(report_path, AlignmentReport)

    assert report.source_text_hash == source_text_hash("chapter")


def test_chapter_data_cache_can_share_chapter_io_context(tmp_path: Path) -> None:
    storage = _Storage()
    layout = _SessionLayout(tmp_path)
    layout.characters_path.write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "name": "A",
                        "role": "lead",
                        "backstory": "origin",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    context = ChapterRunIOContext(
        storage=storage,
        layout=layout,
        project_id="proj",
        chapter_number=1,
        source="unit",
    )
    cache = _ChapterDataCache(storage, layout, artifact_loader=context)

    assert cache.get_character_bible_data()["characters"][0]["name"] == "A"
    assert context.load_json(layout.characters_path)["characters"][0]["role"] == "lead"
    assert storage.loads == 1
    assert context.stats()["hits"] == 1


def test_chapter_session_artifact_loader_reuses_review_progress(tmp_path: Path) -> None:
    storage = _Storage()
    layout = _SessionLayout(tmp_path)
    loader = ChapterArtifactBundleLoader(storage)
    progress = ReviewProgressState(completed_stage="quality_done", current_text="draft")
    layout.chapter_review_progress_path(1).write_text(
        json.dumps(progress.model_dump(mode="json")),
        encoding="utf-8",
    )

    first = load_review_progress(storage, layout, 1, artifact_loader=loader)
    second = load_review_progress(storage, layout, 1, artifact_loader=loader)

    assert first is not None
    assert second is not None
    assert first.current_text == second.current_text == "draft"
    assert storage.loads == 1


def test_chapter_session_artifact_loader_reuses_creative_report(tmp_path: Path) -> None:
    storage = _Storage()
    layout = _SessionLayout(tmp_path)
    loader = ChapterArtifactBundleLoader(storage)
    layout.creative_report_path(1).write_text(
        '{"summary": "first", "items": [{"name": "x"}]}',
        encoding="utf-8",
    )

    first = load_creative_report_payload(
        None,
        storage=storage,
        layout=layout,
        chapter_number=1,
        artifact_loader=loader,
    )
    first["summary"] = "mutated"
    second = load_creative_report_payload(
        None,
        storage=storage,
        layout=layout,
        chapter_number=1,
        artifact_loader=loader,
    )

    assert second["summary"] == "first"
    assert storage.loads == 1


def test_performance_metrics_snapshot_schema(tmp_path: Path) -> None:
    metrics = RunPerformanceMetrics()
    storage = _Storage()
    proxy = CountingStorageProxy(storage, metrics)
    payload_path = tmp_path / "payload.json"
    payload_path.write_text('{"ok": true}', encoding="utf-8")

    metrics.observe_step("plan", {})
    metrics.observe_step("quality_reports_refresh_after_text_change", {})
    metrics.observe_step("quality_report_reused", {"dimension": "alignment"})
    metrics.observe_step(
        "text_changed_before_archive",
        {"reason": "humanize", "before_hash": "a", "after_hash": "b"},
    )
    metrics.observe_step("final_text_hash_verified", {"source_text_hash": "b"})
    metrics.observe_step("short_adaptive_revision_round", {"round": 2})
    metrics.observe_step("short_adaptive_diagnosis", {"intent_score": 8.5})
    metrics.observe_step("chapter_research_start", {"queries": 2})
    metrics.observe_step("chapter_research_cache_hit", {"queries": 2})
    metrics.observe_step(
        "chapter_research_ready",
        {"cards": 2, "inspiration_cards": 1, "inspiration_duplicates_omitted": 1},
    )
    metrics.observe_step(
        "guard_constraint_compliance_check",
        {"intent_conflicts": [{"field": "ending_style"}]},
    )
    metrics.observe_router_event(
        "api_call_done",
        {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7, "cost_usd": 0.01},
    )
    assert proxy.load_json(payload_path) == {"ok": True}
    snapshot = metrics.snapshot()

    assert snapshot["schema_version"] == 1
    assert "phase_timings" in snapshot
    assert snapshot["llm_calls"]["succeeded"] == 1
    assert snapshot["tokens"]["total"] == 7
    assert snapshot["storage_reads"]["load_json"] == 1
    assert snapshot["report_refreshes"]["requested"] == 1
    assert snapshot["report_refreshes"]["reused"] == 1
    assert snapshot["semantic_mutations"]["humanize"] == 1
    assert snapshot["final_hash_verifications"] == 1
    assert snapshot["short_revision_rounds"] == 2
    assert snapshot["intent_conflicts"]["short_intent_compliance"] == 1
    assert snapshot["intent_conflicts"]["ending_style"] == 1
    assert snapshot["research"]["provider_calls"] == 1
    assert snapshot["research"]["cache_hits"] == 1
    assert snapshot["research"]["inspiration_duplicates_omitted"] == 1


async def test_review_report_service_reuses_exact_evidence_bound_reports(tmp_path: Path) -> None:
    storage = _Storage()
    layout = _SessionLayout(tmp_path)
    text = "chapter text"
    text_hash = source_text_hash(text)
    packet = {"state": "same"}
    bridge = {"bridge": "same"}
    plan = {"plan": "same"}
    bundle = SimpleNamespace(
        layout=layout,
        project_id="proj",
        chapter_outline={"chapter_number": 1},
        chapter_source_slice=None,
    )
    binding = quality_report_evidence_binding(
        packet=packet,
        bridge=bridge,
        plan=plan,
        bundle=bundle,
    )

    def bound(report: Any) -> dict[str, Any]:
        payload = report.model_dump(mode="json")
        return stamp_report_freshness(
            payload,
            current_hash=text_hash,
            context_hash=str(binding["context_hash"]),
            evidence_hashes=dict(binding["evidence_hashes"]),
        )

    storage.save_json(
        layout.alignment_report_path(1),
        bound(AlignmentReport(source_text_hash=text_hash)),
    )
    storage.save_json(
        layout.continuity_report_path(1),
        bound(ContinuityReport(source_text_hash=text_hash)),
    )
    storage.save_json(
        layout.chapter_causal_report_path(1),
        bound(CausalValidationReport(source_text_hash=text_hash)),
    )
    storage.save_json(
        layout.reading_power_report_path(1),
        bound(ReadingPowerReport(chapter=1, source_text_hash=text_hash)),
    )
    storage.loads = 0
    storage.saves = 0
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(
        _storage=storage,
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    service = ReviewReportService(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=1,
        trace=None,
    )

    result = await service.ensure_current(
        current_text=text,
        alignment_report=None,
        continuity_report=None,
        causal_report=None,
        reading_power_report=None,
    )

    assert result.current_text_hash == text_hash
    assert isinstance(result.alignment_report, AlignmentReport)
    assert isinstance(result.continuity_report, ContinuityReport)
    assert isinstance(result.causal_report, CausalValidationReport)
    assert isinstance(result.reading_power_report, ReadingPowerReport)
    assert [step for step, _payload in events].count("quality_report_reused") == 4
    stamped = storage.load_json(layout.alignment_report_path(1))
    assert stamped["source_text_hash"] == text_hash
    assert stamped["report_context_hash"]
    assert stamped["report_freshness"]["source_text_hash"] == text_hash


async def test_review_report_service_report_kinds_skip_unrequested_dimensions(
    tmp_path: Path,
) -> None:
    storage = _Storage()
    layout = _SessionLayout(tmp_path)
    text = "chapter text"
    text_hash = source_text_hash(text)
    events: list[tuple[str, Any]] = []
    runner = SimpleNamespace(
        _storage=storage,
        _on_step=lambda step, payload: events.append((step, payload)),
    )
    bundle = SimpleNamespace(
        layout=layout,
        project_id="proj",
        chapter_outline={"chapter_number": 1},
        chapter_source_slice=None,
    )
    packet = {"state": "same"}
    bridge = {"bridge": "same"}
    plan = {"plan": "same"}
    binding = quality_report_evidence_binding(
        packet=packet,
        bridge=bridge,
        plan=plan,
        bundle=bundle,
    )
    continuity_payload = ContinuityReport(source_text_hash=text_hash).model_dump(mode="json")
    stamp_report_freshness(
        continuity_payload,
        current_hash=text_hash,
        context_hash=str(binding["context_hash"]),
        evidence_hashes=dict(binding["evidence_hashes"]),
    )
    storage.save_json(layout.continuity_report_path(1), continuity_payload)
    service = ReviewReportService(
        runner=runner,
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        chapter_number=1,
        trace=None,
    )
    alignment = AlignmentReport(source_text_hash="old")
    causal = CausalValidationReport(source_text_hash="old")
    reading_power = ReadingPowerReport(chapter=1, source_text_hash="old")

    result = await service.ensure_current(
        current_text=text,
        alignment_report=alignment,
        continuity_report=None,
        causal_report=causal,
        reading_power_report=reading_power,
        report_kinds=("continuity",),
    )

    assert result.alignment_report is alignment
    assert isinstance(result.continuity_report, ContinuityReport)
    assert result.causal_report is causal
    assert result.reading_power_report is reading_power
    reused_dimensions = [
        payload["dimension"]
        for step, payload in events
        if step == "quality_report_reused"
    ]
    assert reused_dimensions == ["continuity"]


async def test_guidance_mismatch_refresh_uses_unified_report_service(
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []
    events: list[tuple[str, Any]] = []

    class _FakeReviewReportService:
        def __init__(self, **kwargs: Any) -> None:
            calls.append({"init": kwargs})

        async def ensure_current(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return SimpleNamespace(
                alignment_report=kwargs["alignment_report"],
                continuity_report=ContinuityReport(continuity_score=8.6, issues=[]),
                causal_report=kwargs["causal_report"],
            )

    monkeypatch.setattr(
        chapter_flow_finalize,
        "ReviewReportService",
        _FakeReviewReportService,
    )
    context = SimpleNamespace(
        storage=SimpleNamespace(),
        router=object(),
        builder=object(),
        settings=SimpleNamespace(),
        config=SimpleNamespace(),
        on_step=lambda step, payload: events.append((step, payload)),
    )
    prepared = SimpleNamespace(
        bundle=SimpleNamespace(layout=SimpleNamespace(), project_id="proj"),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
    )

    alignment, continuity, causal = await chapter_flow_finalize._refresh_mismatched_guidance_reports(
        context,
        prepared=prepared,
        current_text="正文",
        chapter_number=1,
        trace=SimpleNamespace(),
        mismatch_sources={"continuity_report"},
        alignment_report=AlignmentReport(alignment_score=9.0),
        continuity_report=ContinuityReport(continuity_score=1.0),
        causal_report=CausalValidationReport(causal_score=9.0),
    )

    assert alignment.alignment_score == 9.0
    assert continuity.continuity_score == 8.6
    assert causal.causal_score == 9.0
    ensure_call = calls[1]
    assert ensure_call["report_kinds"] == ("continuity",)
    assert ensure_call["force_refresh"] is True
    assert ensure_call["continuity_recheck_mode"] is True
    assert ensure_call["continuity_strict_review"] is True
    assert [step for step, _payload in events] == [
        "guidance_report_recheck",
        "continuity_eval_guidance_recheck",
    ]


def test_repair_loop_executor_components_emit_audit_and_rollback() -> None:
    events: list[dict[str, Any]] = []

    class _Owner:
        _config = SimpleNamespace(must_fix_severity="critical", max_rounds=3)

        def __init__(self) -> None:
            self.step_events: list[tuple[str, Any]] = []

        def _on_step(self, step: str, payload: Any) -> None:
            self.step_events.append((step, payload))

        def filter_issues_for_round(
            self,
            issues: list[Any],
            current_round: int,
            ctx: Any,
        ) -> list[Any]:
            return issues

        def _promote_issues_below_hard_floor(
            self,
            issues: list[Any],
            must_fix_issues: list[Any],
            score: float,
        ) -> list[Any]:
            return must_fix_issues or issues

        def _focus_issues_for_round(self, issues: list[Any], ctx: Any) -> list[Any]:
            return issues

        def issue_signature(self, issue: Any) -> str:
            return str(issue.get("issue_id") or issue.get("summary"))

        def _emit_issues_audit_event(
            self,
            event_type: str,
            issues: list[Any],
            ctx: Any,
            **payload: Any,
        ) -> None:
            events.append({"event_type": event_type, "issues": issues, **payload})

        def _emit_loop_audit_summary(self, ctx: Any, result: Any, **payload: Any) -> None:
            events.append({"event_type": "summary", **payload})

    owner = _Owner()
    executor = RepairLoopExecutor[dict[str, Any]](owner)
    issue = {"issue_id": "i1", "severity": "critical", "summary": "fix me"}
    ctx = SimpleNamespace(
        issues=[issue],
        must_fix_issues=[],
        issue_attempts={},
        score=5.0,
        pre_round_text="before",
        current_text="after",
    )

    selected = executor.issue_selector.select_for_round(ctx, 0)
    ctx.must_fix_issues = selected.must_fix_issues
    attempts = executor.issue_selector.record_attempts(ctx)
    executor.audit.selected(ctx, selected.must_fix_issues, source_text_hash="h1")
    executor.audit.attempted(
        ctx,
        selected.must_fix_issues,
        source_text_hash="h1",
        attempts=attempts,
    )
    executor.audit.applied(
        ctx,
        selected.must_fix_issues,
        source_text_hash="h1",
        target_text_hash="h2",
    )
    executor.rollback_guard.rollback_to_pre_round(
        ctx,
        event_name="repair_rollback",
        event_payload={"ok": True},
        issues=selected.must_fix_issues,
        source_text_hash="h1",
        target_text_hash="h2",
        repair_action="unit",
        failure_kind="regression",
    )

    assert attempts == {"i1": 1}
    assert ctx.current_text == "before"
    assert [event["event_type"] for event in events] == [
        "selected",
        "attempted",
        "applied",
        "finalized",
    ]
    assert owner.step_events == [("repair_rollback", {"ok": True})]
