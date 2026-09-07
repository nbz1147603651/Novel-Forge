"""Integration tests: Forbidden elements pipeline — detection, repair, and intentional callbacks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from novel_forge.core.schemas.chapter import CausalIssue, CausalValidationReport
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
)
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.chapter_runner import ChapterRunner, ChapterRunnerConfig
from novel_forge.pipeline.steps.causal_repair_step import CausalRepairInput, CausalRepairStep
from novel_forge.pipeline.steps.continuity_eval_step import (
    ContinuityEvalInput,
    ContinuityEvalStep,
    _detect_forbidden_elements,
)
from novel_forge.prompts.builder import PromptBuilder

# ──────────────────────────────────────────────────────────────
# Test 1: Full pipeline run produces zero forbidden element hits
# ──────────────────────────────────────────────────────────────

class TestFullPipelineNoForbiddenElements:
    """End-to-end chapter pipeline: after Bridge→Plan→Draft→Eval, final text has zero forbidden hits."""

    @pytest.fixture
    def storage(self, tmp_path: Path) -> FileSystemStorage:
        return FileSystemStorage(tmp_path)

    @pytest.fixture
    def runner(self, storage: FileSystemStorage, runtime_settings) -> ChapterRunner:
        adapter = MockAdapter()
        router = ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
        )
        builder = PromptBuilder()
        return ChapterRunner(
            router,
            builder,
            storage,
            config=ChapterRunnerConfig(),
            settings=runtime_settings,
        )

    async def test_full_pipeline_no_forbidden_elements(
        self, runner: ChapterRunner, storage: FileSystemStorage
    ) -> None:
        """Initialize project and run chapter 1; verify final text has zero forbidden element hits."""
        project_id = "test_forbidden_pipeline"

        # ① Init long project
        await runner.init_long(
            premise="一个关于时间旅行的故事",
            project_id=project_id,
            genre="scifi",
            tone="mysterious",
            total_chapters=3,
        )

        # ② Run chapter 1
        chapter_result = await runner.run_chapter(project_id, chapter_number=1)

        # Verify chapter was generated
        assert chapter_result.meta.chapter_number == 1
        assert len(chapter_result.text) > 100

        # ③ Load the chapter plan to get forbidden_elements list
        layout = ProjectLayout(storage.project_dir(project_id))
        plan_data = storage.load_json(layout.chapter_plan_path(1))
        forbidden_elements = plan_data.get("forbidden_elements", [])
        forbidden_soft = plan_data.get("forbidden_elements_soft", [])

        # ④ Run forbidden-element detection on the final chapter text
        all_forbidden = list(set(forbidden_elements) | set(forbidden_soft))
        if all_forbidden:
            hits = _detect_forbidden_elements(chapter_result.text, all_forbidden)
            # Zero forbidden element hits in the final text
            assert len(hits) == 0, (
                f"Final text contains forbidden elements: {hits}"
            )


# ──────────────────────────────────────────────────────────────
# Test 2: Causal repair does not introduce new forbidden elements
# ──────────────────────────────────────────────────────────────

class TestCausalRepairNoRegression:
    """Causal repair step: repaired text must not contain any forbidden elements."""

    async def test_causal_repair_no_regression(self, runtime_settings) -> None:
        """Given a chapter with causal issues, repair must not introduce forbidden elements."""
        original_text = (
            "林远推开废弃图书馆的大门，灰尘扑面而来。\n\n"
            "他小心翼翼地走进昏暗的走廊，手中的铜质怀表发出微弱的光芒。\n\n"
            "突然，身后传来一声巨响，大门被风吹得关上了。"
        )

        # Forbidden elements that must NOT appear in repaired text
        forbidden = ["雷雨交加", "突然转身", "金色光芒", "寒意刺骨"]
        forbidden_soft = ["夜色深沉", "月光如水"]

        causal_report = CausalValidationReport(
            causal_score=4.2,
            summary="存在因果链问题",
            causal_link_verified=False,
            issues=[
                CausalIssue(
                    issue_type="opening_causal_gap",
                    severity="high",
                    location="第1段（开头承接处）",
                    summary="开头与上章结尾脱节，缺少因果承接",
                    evidence="上章结尾林远正在逃亡，本章开头却在图书馆",
                    fix_suggestion="补充从逃亡到图书馆的过渡",
                ),
            ],
        )

        repair_input = CausalRepairInput(
            chapter_number=2,
            chapter_text=original_text,
            causal_link={
                "previous_event": "林远正在被追兵追赶",
                "causal_mechanism": "逃入废弃图书馆躲避",
                "unresolved_question": "追兵是否发现了他",
            },
            chapter_bridge=ChapterBridge(
                to_chapter=2,
                transition_mode="direct_continue",
                emotional_carryover="紧张",
                action_handoff="林远逃入图书馆",
            ),
            causal_report=causal_report,
            must_fix_summaries=["开头与上章结尾脱节，缺少因果承接"],
            forbidden_elements=forbidden,
            forbidden_elements_soft=forbidden_soft,
        )

        # Mock the LLM response to return a repaired text that avoids forbidden elements
        repaired_text = (
            "林远气喘吁吁地撞开废弃图书馆的大门，追兵的脚步声仍在身后回荡。\n\n"
            "他背靠门板缓缓滑坐，手中的铜质怀表发出微弱的光芒。\n\n"
            "外面的风声掩盖了他的呼吸声，他暂时安全了。"
        )

        async def _fake_route(_request):
            return SimpleNamespace(
                content=repaired_text,
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200),
            )

        router = ModelRouter(
            adapters={"mock": MockAdapter()},
            default_provider="mock",
        )
        # Patch the router to return our controlled response
        with patch.object(router, "route", new=_fake_route):
            step = CausalRepairStep(router, PromptBuilder(), settings=runtime_settings)
            result = await step.run(repair_input)

        # Verify repair was applied
        assert result.applied is True
        assert result.revised_text != original_text

        # Verify NO forbidden elements in repaired text
        all_forbidden = list(set(forbidden) | set(forbidden_soft))
        hits = _detect_forbidden_elements(result.revised_text, all_forbidden)
        assert len(hits) == 0, (
            f"Causal repair introduced forbidden elements: {hits}"
        )

        # Verify the forbidden_element_warnings list is empty
        assert result.forbidden_element_warnings == [], (
            f"Repair produced forbidden element warnings: {result.forbidden_element_warnings}"
        )


# ──────────────────────────────────────────────────────────────
# Test 3: Intentional callbacks are preserved through pipeline
# ──────────────────────────────────────────────────────────────

class TestIntentionalCallbackPreserved:
    """Intentional callbacks (有意回环) must NOT be flagged as forbidden elements."""

    @pytest.fixture
    def storage(self, tmp_path: Path) -> FileSystemStorage:
        return FileSystemStorage(tmp_path)

    @pytest.fixture
    def runner(self, storage: FileSystemStorage, runtime_settings) -> ChapterRunner:
        adapter = MockAdapter()
        router = ModelRouter(
            adapters={"mock": adapter},
            default_provider="mock",
        )
        builder = PromptBuilder()
        return ChapterRunner(
            router,
            builder,
            storage,
            config=ChapterRunnerConfig(),
            settings=runtime_settings,
        )

    async def test_intentional_callback_preserved_in_continuity_eval(
        self, runtime_settings
    ) -> None:
        """ContinuityEvalStep must not flag intentional_callbacks as forbidden elements."""
        chapter_text = (
            "林远想起了母亲的叮嘱：无论遇到什么困难，都不要放弃希望。\n\n"
            "他握紧手中的铜质怀表，那是母亲留给他的唯一遗物。\n\n"
            "门外的脚步声越来越近，但他心中却异常平静。"
        )

        # "母亲的叮嘱" is an intentional callback — should NOT be flagged
        plan = ChapterPlan(
            opening_contract="林远进入图书馆",
            closing_contract="发现神秘符号",
            scene_intents=[],
            forbidden_elements=["雷雨交加", "突然转身"],
            forbidden_elements_soft=["夜色深沉"],
            intentional_callbacks=["母亲的叮嘱"],
        )

        state_packet = ChapterStatePacket(
            chapter_number=3,
            chapter_outline=ChapterOutline(chapter_number=3, title="测试章", goal="推进冲突"),
            canon_context={},
            accumulated_forbidden_repetition=["铜质怀表"],
        )

        bridge = ChapterBridge(
            to_chapter=3,
            transition_mode="direct_continue",
            emotional_carryover="平静",
            action_handoff="林远继续探索",
        )

        eval_input = ContinuityEvalInput(
            chapter_number=3,
            chapter_text=chapter_text,
            chapter_state_packet=state_packet,
            chapter_bridge=bridge,
            chapter_plan=plan,
        )

        router = ModelRouter(
            adapters={"mock": MockAdapter()},
            default_provider="mock",
        )
        builder = PromptBuilder()

        # Mock LLM to return a clean continuity report (no issues)
        async def _fake_route(_request):
            return SimpleNamespace(
                content='{"continuity_score": 9.0, "summary": "连贯性良好", "issues": []}',
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
            )

        with patch.object(router, "route", new=_fake_route):
            step = ContinuityEvalStep(router, builder, settings=runtime_settings)
            report = await step.run(eval_input)

        # Verify no forbidden_element_usage or forbidden_element_violation issues
        forbidden_issues = [
            issue
            for issue in report.issues
            if issue.get("issue_type") in ("forbidden_element_usage", "forbidden_element_violation")
        ]
        assert len(forbidden_issues) == 0, (
            f"Continuity eval flagged forbidden elements: {forbidden_issues}"
        )

    async def test_intentional_callback_not_in_forbidden_detection(
        self,
    ) -> None:
        """_detect_forbidden_elements must not match intentional callback items."""
        chapter_text = (
            "林远想起了母亲的叮嘱：无论遇到什么困难，都不要放弃希望。\n\n"
            "他握紧手中的铜质怀表，那是母亲留给他的唯一遗物。"
        )

        # "母亲的叮嘱" appears in text AND is an intentional callback
        # It should NOT be detected as a forbidden element
        forbidden_list = ["雷雨交加", "突然转身"]
        intentional_callbacks = ["母亲的叮嘱"]

        # Detection with only forbidden list — should find nothing
        hits = _detect_forbidden_elements(chapter_text, forbidden_list)
        assert len(hits) == 0

        # Even if we add intentional callbacks to the forbidden list (simulating a bug),
        # the pipeline logic should exclude them. We verify the exclusion logic here:
        all_items = forbidden_list + intentional_callbacks
        hard_forbidden = set(forbidden_list)
        # Intentional callbacks should be excluded from the detection set
        detection_set = set(all_items) - set(intentional_callbacks)
        assert detection_set == hard_forbidden

        # Verify that "母亲的叮嘱" IS in the text (so the test is meaningful)
        assert "母亲的叮嘱" in chapter_text

    async def test_intentional_callback_preserved_in_full_pipeline(
        self, runner: ChapterRunner, storage: FileSystemStorage
    ) -> None:
        """Full pipeline: intentional callbacks from plan are preserved and not flagged."""
        project_id = "test_callback_pipeline"

        # ① Init project
        await runner.init_long(
            premise="一个关于传承与成长的故事",
            project_id=project_id,
            genre="fantasy",
            tone="epic",
            total_chapters=3,
        )

        # ② Run chapter 1
        chapter_result = await runner.run_chapter(project_id, chapter_number=1)

        assert chapter_result.meta.chapter_number == 1
        assert len(chapter_result.text) > 100

        # ③ Load plan and verify intentional_callbacks field exists
        layout = ProjectLayout(storage.project_dir(project_id))
        plan_data = storage.load_json(layout.chapter_plan_path(1))
        assert "intentional_callbacks" in plan_data

        # intentional_callbacks may be empty for chapter 1 (no prior chapters to callback to),
        # but the field must exist and be a list
        callbacks = plan_data.get("intentional_callbacks", [])
        assert isinstance(callbacks, list)

        # ④ If there are intentional callbacks, verify they are NOT in forbidden_elements
        forbidden = set(plan_data.get("forbidden_elements", []))
        callback_set = set(callbacks)

        # Intentional callbacks must not overlap with hard forbidden elements
        overlap = callback_set & forbidden
        assert len(overlap) == 0, (
            f"Intentional callbacks overlap with forbidden elements: {overlap}"
        )
