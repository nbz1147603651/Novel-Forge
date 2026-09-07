"""Tests for CriticAgent-unavailable continuity fallback behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from novel_forge.core.schemas.chapter import AlignmentReport
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityReport,
)
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.memory.critic import CritiqueIssue, CritiqueReport
from novel_forge.pipeline.long.stages.continuity_repair import (
    _convert_critique_to_continuity,
    run_post_repair_checks,
)
from novel_forge.pipeline.long.stages.quality_checks import run_quality_checks
from novel_forge.story_kernel.schemas import StoryKernel


class _DummyStorage:
    def __init__(self) -> None:
        self.saved: dict[str, Any] = {}

    def save_json(self, path: Path, payload: dict[str, Any]) -> None:
        self.saved[str(path)] = payload

    def exists(self, _path: Path) -> bool:
        return False

    def load_json(self, _path: Path) -> dict[str, Any]:
        return {}


class _DummyLayout:
    def continuity_report_path(self, chapter_number: int) -> Path:
        return Path(f"reports/chapter_{chapter_number:03d}_continuity.json")

    def alignment_report_path(self, chapter_number: int) -> Path:
        return Path(f"reports/chapter_{chapter_number:03d}_alignment.json")

    def reading_power_report_path(self, chapter_number: int) -> Path:
        return Path(f"reports/chapter_{chapter_number:03d}_reading_power.json")

    def critic_report_path(self, chapter_number: int) -> Path:
        return Path(f"reports/chapter_{chapter_number:03d}_critic.json")


class _DummyRunner:
    def __init__(self) -> None:
        self._storage = _DummyStorage()
        self._router = object()
        self._builder = object()
        self._settings = SimpleNamespace(
            long_check_chapter_enabled=False,
            memory_critic_agent_enabled=True,
        )
        self.steps: list[tuple[str, Any]] = []
        self._project_id = "demo"
        self.audit_coordinator = None

    def _on_step(self, step: str, data: Any) -> None:
        self.steps.append((step, data))

    def has_memory_context(self) -> bool:
        return False

    def has_audit_coordinator(self) -> bool:
        return False


class _DummyMotifTracker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_motifs_for_prompt(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "active_motifs": [{"name": "纸灰", "category": "意象"}],
            "forbidden_repetition": ["冷月"],
            "suggested_callbacks": [{"motif": "焚书残页", "reason": "呼应火盆余波"}],
        }


class _DummyMemoryContext:
    def __init__(self, lookback: int = 4) -> None:
        self.motif_tracker = _DummyMotifTracker()
        self.settings = SimpleNamespace(memory_motif_related_lookback_chapters=lookback)


def _build_inputs() -> tuple[_DummyRunner, Any, Any, Any, Any]:
    runner = _DummyRunner()
    chapter_outline = ChapterOutline(
        chapter_number=2,
        title="测试章",
        goal="推进情节",
    )
    bundle = SimpleNamespace(
        layout=_DummyLayout(),
        canon_state=StoryKernel(project_id="demo"),
        chapter_outline=chapter_outline.model_copy(update={"pov_switch": False}),
        story_bible=SimpleNamespace(genre="fantasy"),
    )
    packet = ChapterStatePacket(
        chapter_number=2,
        chapter_outline=chapter_outline,
        canon_context={},
        previous_chapter_ending="",
    )
    bridge = ChapterBridge(
        from_chapter=1,
        to_chapter=2,
    )
    plan = ChapterPlan()
    return runner, bundle, packet, bridge, plan


def test_critic_continuity_conversion_issue_ids_are_stable() -> None:
    report = CritiqueReport(
        chapter_number=3,
        issues=[
            CritiqueIssue(
                issue_type="continuity_error",
                severity="high",
                summary="角色位置与上一章不一致",
                evidence="上一章结尾仍在地牢，本章开头直接站在山顶。",
                affected_chapters=[2, 3],
                suggested_fix="补充离开地牢并抵达山顶的转场。",
                confidence=0.9,
            )
        ],
    )

    first = _convert_critique_to_continuity(report, chapter_number=3)
    second = _convert_critique_to_continuity(report, chapter_number=3)

    assert first.issues[0].issue_id == second.issues[0].issue_id
    assert first.issues[0].issue_id.startswith("ch003-continuity-")
    assert first.issues[0].issue_type == "continuity_gap"


def test_quality_checks_use_continuity_eval_when_critic_unavailable(monkeypatch) -> None:
    runner, bundle, packet, bridge, plan = _build_inputs()

    async def _fake_continuity_eval_run(self, _input_data):
        return ContinuityReport(
            continuity_score=7.4,
            summary="fallback continuity eval",
            issues=[],
        )

    async def _fake_alignment_run(self, _input_data):
        return AlignmentReport(alignment_score=8.6, summary="ok")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner.ContinuityEvalStep.run",
        _fake_continuity_eval_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner.AlignmentStep.run",
        _fake_alignment_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner._push_audit_result_to_ui",
        lambda *_args, **_kwargs: None,
    )

    _alignment, continuity, chapter_repair = asyncio.run(
        run_quality_checks(
            runner=runner,
            bundle=bundle,
            packet=packet,
            bridge=bridge,
            plan=plan,
            current_text="示例正文",
            chapter_number=2,
            trace=object(),
        )
    )

    assert chapter_repair is None
    assert continuity.continuity_score == 7.4
    assert continuity.summary == "fallback continuity eval"
    assert continuity.summary != "CriticAgent 不可用，使用默认评估。"
    assert any(step == "continuity_eval_fallback" for step, _ in runner.steps)


def test_quality_checks_keep_default_when_continuity_eval_fallback_fails(monkeypatch) -> None:
    runner, bundle, packet, bridge, plan = _build_inputs()

    async def _raise_continuity_eval_error(self, _input_data):
        raise RuntimeError("fallback failed")

    async def _fake_alignment_run(self, _input_data):
        return AlignmentReport(alignment_score=8.1, summary="ok")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner.ContinuityEvalStep.run",
        _raise_continuity_eval_error,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner.AlignmentStep.run",
        _fake_alignment_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner._push_audit_result_to_ui",
        lambda *_args, **_kwargs: None,
    )

    _alignment, continuity, _chapter_repair = asyncio.run(
        run_quality_checks(
            runner=runner,
            bundle=bundle,
            packet=packet,
            bridge=bridge,
            plan=plan,
            current_text="示例正文",
            chapter_number=2,
            trace=object(),
        )
    )

    assert continuity.continuity_score == 7.0
    assert "保守评估" in continuity.summary
    assert not any(step == "continuity_eval_fallback" for step, _ in runner.steps)


def test_quality_checks_fallback_passes_dynamic_continuity_inputs(monkeypatch) -> None:
    runner, bundle, packet, bridge, plan = _build_inputs()
    runner._settings.dynamic_continuity_filter = True
    runner.memory_context = _DummyMemoryContext(lookback=5)
    captured: dict[str, Any] = {}

    async def _fake_continuity_eval_run(self, input_data):
        captured["input_data"] = input_data
        return ContinuityReport(
            continuity_score=7.6,
            summary="fallback continuity eval",
            issues=[],
        )

    async def _fake_alignment_run(self, _input_data):
        return AlignmentReport(alignment_score=8.5, summary="ok")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner.ContinuityEvalStep.run",
        _fake_continuity_eval_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner.AlignmentStep.run",
        _fake_alignment_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_lib._extract_anchor_terms_from_bible",
        lambda *_args, **_kwargs: ["镇北将军"],
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner._push_audit_result_to_ui",
        lambda *_args, **_kwargs: None,
    )

    _alignment, continuity, _chapter_repair = asyncio.run(
        run_quality_checks(
            runner=runner,
            bundle=bundle,
            packet=packet,
            bridge=bridge,
            plan=plan,
            current_text="示例正文",
            chapter_number=2,
            trace=object(),
        )
    )

    assert continuity.continuity_score == 7.6
    assert captured["input_data"].motif_context["active_motifs"] == [
        {"name": "纸灰", "category": "意象"}
    ]
    assert captured["input_data"].bible_anchor_terms == ["镇北将军"]
    assert runner.memory_context.motif_tracker.calls[0]["related_lookback_chapters"] == 5


def test_quality_checks_passes_full_canon_state_to_critic(monkeypatch) -> None:
    runner, bundle, packet, bridge, plan = _build_inputs()
    captured: dict[str, Any] = {}

    class _FakeCriticAgent:
        async def critique_chapter(self, **kwargs: Any) -> CritiqueReport:
            captured["canon_state"] = kwargs["canon_state"]
            return CritiqueReport(chapter_number=2, overall_score=9.0)

    runner.memory_context = SimpleNamespace(critic_agent=_FakeCriticAgent())
    runner.has_memory_context = lambda: True  # type: ignore[method-assign]

    async def _fake_alignment_run(self, _input_data):
        return AlignmentReport(alignment_score=8.8, summary="ok")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner.AlignmentStep.run",
        _fake_alignment_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_runner._push_audit_result_to_ui",
        lambda *_args, **_kwargs: None,
    )

    _alignment, continuity, chapter_repair = asyncio.run(
        run_quality_checks(
            runner=runner,
            bundle=bundle,
            packet=packet,
            bridge=bridge,
            plan=plan,
            current_text="示例正文" * 200,
            chapter_number=2,
            trace=object(),
        )
    )

    assert isinstance(captured["canon_state"], StoryKernel)
    assert captured["canon_state"].project_id == "demo"
    assert chapter_repair is None
    assert continuity.continuity_score == 10.0
    assert any(step == "critique_completed" for step, _ in runner.steps)
    critic_report = runner._storage.saved["reports/chapter_002_critic.json"]
    assert critic_report["report_type"] == "critic_agent_full_report"
    assert critic_report["overall_score"] == 9.0
    assert critic_report["source_text_hash"]
    assert any(step == "critic_report_persisted" for step, _ in runner.steps)


def test_post_repair_critic_uses_bundle_kernel_not_packet_projection(monkeypatch) -> None:
    runner, bundle, packet, bridge, plan = _build_inputs()
    captured: dict[str, Any] = {}

    class _FakeCriticAgent:
        async def critique_chapter(self, **kwargs: Any) -> CritiqueReport:
            captured["canon_state"] = kwargs["canon_state"]
            return CritiqueReport(chapter_number=2, overall_score=9.0)

    # This projection intentionally has legacy-only keys and cannot validate as
    # StoryKernel. The recheck must use bundle.canon_state instead.
    packet = packet.model_copy(
        update={"canon_context": {"characters": {"沈岸": {"location": "事务所"}}}}
    )
    runner.memory_context = SimpleNamespace(critic_agent=_FakeCriticAgent())
    runner.has_memory_context = lambda: True  # type: ignore[method-assign]

    async def _fake_alignment_run(self, _input_data):
        return AlignmentReport(alignment_score=8.8, summary="ok")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.chapter_repair_support.AlignmentStep.run",
        _fake_alignment_run,
    )

    _alignment, continuity, _chapter_repair = asyncio.run(
        run_post_repair_checks(
            runner=runner,
            bundle=bundle,
            packet=packet,
            bridge=bridge,
            plan=plan,
            pre_repair_text="修复前正文",
            current_text="修复后正文",
            chapter_number=2,
            alignment_report=AlignmentReport(alignment_score=8.0, summary="before"),
            continuity_report=ContinuityReport(continuity_score=6.4, summary="before"),
            chapter_repair_report=None,
            trace=object(),
        )
    )

    assert captured["canon_state"] is bundle.canon_state
    assert continuity.continuity_score == 10.0
    assert any(step == "continuity_recheck_via_critic" for step, _ in runner.steps)


def test_post_repair_eval_fallback_preserves_dynamic_continuity_inputs(monkeypatch) -> None:
    runner, bundle, packet, bridge, plan = _build_inputs()
    runner._settings.dynamic_continuity_filter = True
    runner._settings.memory_critic_agent_enabled = False
    runner.memory_context = _DummyMemoryContext(lookback=6)
    captured: dict[str, Any] = {}

    async def _fake_continuity_eval_run(self, input_data):
        captured["input_data"] = input_data
        return ContinuityReport(
            continuity_score=7.2,
            summary="post repair fallback",
            issues=[],
        )

    async def _fake_alignment_run(self, _input_data):
        return AlignmentReport(alignment_score=8.3, summary="ok")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.chapter_repair_support.ContinuityEvalStep.run",
        _fake_continuity_eval_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.chapter_repair_support.AlignmentStep.run",
        _fake_alignment_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.quality_checks_lib._extract_anchor_terms_from_bible",
        lambda *_args, **_kwargs: ["镇北将军"],
    )

    _alignment, continuity, chapter_repair = asyncio.run(
        run_post_repair_checks(
            runner=runner,
            bundle=bundle,
            packet=packet,
            bridge=bridge,
            plan=plan,
            pre_repair_text="修复前正文",
            current_text="修复后正文",
            chapter_number=2,
            alignment_report=AlignmentReport(alignment_score=8.0, summary="before"),
            continuity_report=ContinuityReport(continuity_score=6.4, summary="before"),
            chapter_repair_report=None,
            trace=object(),
        )
    )

    assert chapter_repair is None
    assert continuity.continuity_score == 7.2
    assert captured["input_data"].motif_context["active_motifs"] == [
        {"name": "纸灰", "category": "意象"}
    ]
    assert captured["input_data"].bible_anchor_terms == ["镇北将军"]
    assert runner.memory_context.motif_tracker.calls[0]["related_lookback_chapters"] == 6


def test_post_repair_eval_fallback_receives_targeted_recheck_context(monkeypatch) -> None:
    runner, bundle, packet, bridge, plan = _build_inputs()
    runner._settings.memory_critic_agent_enabled = False
    captured: dict[str, Any] = {}

    async def _fake_continuity_eval_run(self, input_data):
        captured["input_data"] = input_data
        return ContinuityReport(
            continuity_score=8.8,
            summary="targeted recheck",
            issues=[],
        )

    async def _fake_alignment_run(self, _input_data):
        return AlignmentReport(alignment_score=8.6, summary="ok")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.chapter_repair_support.ContinuityEvalStep.run",
        _fake_continuity_eval_run,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.chapter_repair_support.AlignmentStep.run",
        _fake_alignment_run,
    )

    asyncio.run(
        run_post_repair_checks(
            runner=runner,
            bundle=bundle,
            packet=packet,
            bridge=bridge,
            plan=plan,
            pre_repair_text="修复前正文",
            current_text="修复后正文",
            chapter_number=2,
            alignment_report=AlignmentReport(alignment_score=8.0, summary="before"),
            continuity_report=ContinuityReport(continuity_score=6.4, summary="before"),
            chapter_repair_report=None,
            trace=object(),
            prior_issues=[
                {
                    "issue_id": "cont-opening-1",
                    "issue_type": "opening_gap",
                    "severity": "critical",
                    "summary": "开场缺少上章动作接力。",
                    "location": "第1段",
                    "postconditions": [
                        {
                            "validator_id": "opening_transition_validator",
                            "description": "开头落地动作接力。",
                        }
                    ],
                }
            ],
            must_resolve_summaries=["开场缺少上章动作接力。"],
            repaired_issue_types=("opening_gap",),
            patch_only_repair=True,
        )
    )

    input_data = captured["input_data"]
    assert input_data.recheck_mode is True
    assert input_data.strict_review is True
    assert input_data.patch_only_repair is True
    assert input_data.repaired_issue_types == ["opening_gap"]
    assert input_data.must_resolve_summaries == ["开场缺少上章动作接力。"]
    assert input_data.prior_issues[0]["issue_id"] == "cont-opening-1"
    assert input_data.prior_issues[0]["postconditions"][0]["validator_id"] == (
        "opening_transition_validator"
    )
    assert any(step == "continuity_recheck_context" for step, _ in runner.steps)
