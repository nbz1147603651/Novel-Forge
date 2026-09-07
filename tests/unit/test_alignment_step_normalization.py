"""Tests for AlignmentStep output normalization and calibration."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.review.alignment_contracts import (
    adjudicate_targeted_alignment_recheck,
    verified_alignment_blockers,
)
from novel_forge.core.schemas.chapter import AlignmentReport
from novel_forge.pipeline.steps.alignment_step import AlignmentStep


def test_alignment_scope_preserves_complete_current_chapter_plan() -> None:
    long_value = "中段转折" * 100
    outline = SimpleNamespace(
        chapter_number=9,
        title="长章节",
        goal=long_value,
        main_plot_points=[f"主线{i}" for i in range(20)],
        subplot_points=[f"支线{i}" for i in range(18)],
        beats_summary=[f"节拍{i}" for i in range(24)],
    )
    plan = SimpleNamespace(
        scene_intents=[
            SimpleNamespace(
                summary=f"场景{i}" + long_value,
                required_outcome=f"结果{i}",
                owned_events=[f"事件{i}-{j}" for j in range(12)],
                owned_revelations=[f"揭示{i}-{j}" for j in range(10)],
                owned_state_changes=[f"状态{i}-{j}" for j in range(11)],
            )
            for i in range(17)
        ],
        required_state_transitions=["停职状态生效"],
        required_literals=[
            {
                "contract_id": "order",
                "scene_id": "scene_1",
                "literal": "停职五日",
                "reason": "程序文书回指",
                "placement_hint": "开场",
            }
        ],
        opening_contract="开场交代停职仪轨",
        closing_contract="保留未决选择",
    )

    scoped_outline = AlignmentStep._scope_outline(outline)
    scoped_plan = AlignmentStep._scope_plan(plan)

    assert scoped_outline.goal == long_value
    assert len(scoped_outline.main_plot_points) == 20
    assert len(scoped_outline.subplot_points) == 18
    assert len(scoped_outline.beats_summary) == 24
    assert len(scoped_plan.scene_intents) == 17
    assert scoped_plan.scene_intents[-1].summary.endswith(long_value)
    assert len(scoped_plan.scene_intents[-1].owned_events) == 12
    assert scoped_plan.required_state_transitions == ["停职状态生效"]
    assert scoped_plan.required_literals[0].literal == "停职五日"
    assert scoped_plan.opening_contract == "开场交代停职仪轨"


def test_alignment_normalization_penalizes_missing_and_weak_points() -> None:
    raw = {
        "alignment_score": "9.2",
        "risk_level": "low",
        "summary": "",
        "missing_main_points": ["主线触发点未落地"],
        "supportive_subplot_points": "支线互动强化了林屿的信任感",
        "weak_subplot_points": ["支线收束不够清晰"],
        "repair_actions": "补一段主线触发；补一处支线收束动作",
    }

    normalized = AlignmentStep._normalize_alignment_payload(raw)

    assert normalized["alignment_score"] == 8.2
    assert normalized["risk_level"] == "medium"
    assert "关键缺口" in normalized["summary"]
    assert normalized["supportive_subplot_points"] == ["支线互动强化了林屿的信任感"]
    assert normalized["repair_actions"] == ["补一段主线触发", "补一处支线收束动作"]


def test_alignment_normalization_escalates_to_high_when_main_points_missing_many() -> None:
    raw = {
        "alignment_score": 9.2,
        "risk_level": "low",
        "missing_main_points": ["主线点1缺失", "主线点2缺失"],
        "weak_subplot_points": [],
        "repair_actions": [],
    }

    normalized = AlignmentStep._normalize_alignment_payload(raw)

    assert normalized["risk_level"] == "high"
    assert normalized["alignment_score"] == 6.0


def test_alignment_normalization_counts_structured_finding_once() -> None:
    raw = {
        "alignment_score": 6.5,
        "risk_level": "medium",
        "missing_main_points": [
            {
                "id": "中层梦境缺失",
                "location": "梦境段落",
                "evidence": "伪记忆陷阱防御完全缺失。",
                "impact": "主线机制没有闭环。",
                "repair_actions": ["补写触碰节点、陷阱触发与受阻后果。"],
            },
            {
                "id": "结果反馈缺失",
                "location": "结尾",
                "evidence": "客户没有返回确认噩梦解除。",
                "impact": "委托流程缺少结果反馈。",
            },
        ],
        "supportive_subplot_points": [
            {"id": "糖果", "evidence": "薄荷糖完成情感回环。"},
            {"id": "监视", "evidence": "黑衣身影留下后续悬念。"},
        ],
        "weak_subplot_points": [],
        "repair_actions": [],
    }

    normalized = AlignmentStep._normalize_alignment_payload(raw)

    assert len(normalized["missing_main_points"]) == 2
    assert len(normalized["supportive_subplot_points"]) == 2
    assert normalized["alignment_score"] == 6.4
    assert normalized["risk_level"] == "high"


def test_alignment_normalization_avoids_double_penalty_on_missing_main_points() -> None:
    raw = {
        "alignment_score": 8.2,
        "risk_level": "medium",
        "missing_main_points": ["联合计划公告未出现"],
        "supportive_subplot_points": [],
        "weak_subplot_points": [],
        "repair_actions": [],
    }

    normalized = AlignmentStep._normalize_alignment_payload(raw)

    assert normalized["alignment_score"] == 8.0
    assert normalized["risk_level"] == "medium"


def test_alignment_detail_gaps_do_not_override_model_score_like_core_missing() -> None:
    raw = {
        "alignment_score": 7.2,
        "risk_level": "medium",
        "missing_main_points": [
            "签约仪式上的低语细节未体现",
            "怀表发烫这一回环细节未呈现",
        ],
        "supportive_subplot_points": [
            "合作关系正式确立",
            "危机承接清晰",
            "关系张力服务主线",
        ],
        "weak_subplot_points": ["环境压迫感还可加强", "旁支人物反应略弱"],
        "repair_actions": [],
    }

    normalized = AlignmentStep._normalize_alignment_payload(raw)

    assert normalized["alignment_score"] == 7.5
    assert normalized["risk_level"] == "medium"


def test_alignment_perspective_only_gaps_are_not_treated_as_core_missing() -> None:
    raw = {
        "alignment_score": 6.2,
        "risk_level": "high",
        "missing_main_points": [
            "陆云峥副视角完全缺失",
            "沈鹤卿旁观视角完全缺位",
            "第6条科技新贵伏笔未呈现",
        ],
        "supportive_subplot_points": ["闺蜜互动承接情绪", "职场压力服务主线"],
        "weak_subplot_points": ["旧书店支线略占篇幅", "记者支线落点偏弱"],
        "repair_actions": [],
    }

    normalized = AlignmentStep._normalize_alignment_payload(raw)

    assert normalized["alignment_score"] == 6.3
    assert normalized["alignment_score"] > 4.0


def test_structured_alignment_admits_only_source_bound_high_confidence_blockers() -> None:
    outline = SimpleNamespace(
        chapter_number=2,
        goal="沈昭在停职五日受审仪轨下退出晨钟验方一线",
        main_plot_points=["周慎签押御药重验卷宗"],
        subplot_points=[],
        beats_summary=[],
    )
    plan = SimpleNamespace(scene_intents=[])
    text = "她决定在三日停职结束前复验甘松。周慎已签押御药重验卷宗。"
    raw = {
        "alignment_score": 6.5,
        "risk_level": "high",
        "summary": "期限冲突，另有局部表达建议。",
        "findings": [
            {
                "source_ref": "outline.goal",
                "source_evidence": outline.goal,
                "coverage_status": "conflict",
                "severity": "high",
                "confidence": 0.96,
                "summary": "停职期限从五日变成三日",
                "chapter_evidence": "三日停职结束前",
                "repair_action": "仅将期限恢复为五日并校正剩余日数",
                "blocks_finalize": True,
            },
            {
                "source_ref": "outline.main_plot_points[1]",
                "source_evidence": outline.main_plot_points[0],
                "coverage_status": "advisory",
                "severity": "high",
                "confidence": 0.92,
                "summary": "签押动作可以写得更细",
                "chapter_evidence": "周慎已签押御药重验卷宗",
                "repair_action": "增加执笔细节",
                "blocks_finalize": True,
            },
            {
                "source_ref": "genre.expected_ritual",
                "source_evidence": "悬疑题材应有三次反转",
                "coverage_status": "missing",
                "severity": "critical",
                "confidence": 0.99,
                "summary": "缺少第三次反转",
                "chapter_evidence": "",
                "repair_action": "新增反转",
                "blocks_finalize": True,
            },
        ],
        "missing_main_points": ["模型原始列表不可直接信任"],
        "supportive_subplot_points": [],
        "weak_subplot_points": [],
        "repair_actions": ["不可直接信任"],
    }

    normalized = AlignmentStep._normalize_alignment_payload(
        raw,
        chapter_outline=outline,
        chapter_plan=plan,
        chapter_text=text,
        chapter_number=2,
    )

    assert normalized["review_contract_version"] == 1
    assert normalized["missing_main_points"] == ["停职期限从五日变成三日"]
    assert normalized["repair_actions"] == ["仅将期限恢复为五日并校正剩余日数"]
    assert len(normalized["review_findings"]) == 3
    assert len(normalized["repair_tickets"]) == 1
    assert normalized["alignment_score"] == 8.0


def test_targeted_alignment_recheck_closes_ticket_without_expanding_negative_list() -> None:
    outline = SimpleNamespace(
        chapter_number=2,
        goal="停职五日",
        main_plot_points=["周慎签押卷宗"],
        subplot_points=[],
        beats_summary=[],
    )
    plan = SimpleNamespace(scene_intents=[])
    before = "她决定在三日停职结束前复验。周慎签押卷宗。"
    after = "她决定在五日停职结束前复验。周慎签押卷宗。"
    previous = AlignmentStep._normalize_alignment_payload(
        {
            "alignment_score": 7.0,
            "risk_level": "high",
            "findings": [
                {
                    "source_ref": "outline.goal",
                    "source_evidence": outline.goal,
                    "coverage_status": "conflict",
                    "severity": "high",
                    "confidence": 0.95,
                    "summary": "期限冲突",
                    "chapter_evidence": "三日停职结束前",
                    "repair_action": "改回五日",
                }
            ],
            "missing_main_points": [],
            "supportive_subplot_points": [],
            "weak_subplot_points": [],
            "repair_actions": [],
        },
        chapter_outline=outline,
        chapter_plan=plan,
        chapter_text=before,
        chapter_number=2,
    )
    candidate = AlignmentStep._normalize_alignment_payload(
        {
            "alignment_score": 7.0,
            "risk_level": "high",
            "findings": [
                {
                    "source_ref": "outline.main_plot_points[1]",
                    "source_evidence": outline.main_plot_points[0],
                    "coverage_status": "conflict",
                    "severity": "high",
                    "confidence": 0.92,
                    "summary": "复审新提出签押描写不够细",
                    "chapter_evidence": "周慎签押卷宗",
                    "repair_action": "扩写签押",
                }
            ],
            "missing_main_points": [],
            "supportive_subplot_points": [],
            "weak_subplot_points": [],
            "repair_actions": [],
        },
        chapter_outline=outline,
        chapter_plan=plan,
        chapter_text=after,
        chapter_number=2,
    )
    previous.pop("_model_score", None)
    candidate.pop("_model_score", None)
    previous_report = AlignmentReport.model_validate(previous)
    candidate_report = AlignmentReport.model_validate(candidate)

    rechecked = adjudicate_targeted_alignment_recheck(
        previous_report=previous_report,
        candidate_report=candidate_report,
        before_text=before,
        after_text=after,
        repair_round=1,
    )

    assert verified_alignment_blockers(rechecked) == []
    assert rechecked.repair_tickets == []
    assert rechecked.missing_main_points == []
    assert rechecked.repair_verifications[0].status == "resolved"
    assert rechecked.review_findings[0].metadata["targeted_recheck_status"] == "new_observation"


def test_targeted_alignment_recheck_closes_blocker_when_only_partial_observation_remains() -> None:
    outline = SimpleNamespace(
        chapter_number=2,
        goal="停职五日",
        main_plot_points=[],
        subplot_points=[],
        beats_summary=[],
    )
    plan = SimpleNamespace(scene_intents=[])
    before = "她打算尽快复验。"
    after = "她在五日停职期间按日程复验。"

    def report(coverage: str, severity: str, confidence: float) -> AlignmentReport:
        payload = AlignmentStep._normalize_alignment_payload(
            {
                "alignment_score": 7.0,
                "risk_level": "medium",
                "findings": [
                    {
                        "source_ref": "outline.goal",
                        "source_evidence": outline.goal,
                        "coverage_status": coverage,
                        "severity": severity,
                        "confidence": confidence,
                        "summary": "停职期限覆盖不足",
                        "chapter_evidence": "",
                        "repair_action": "补明停职期限",
                    }
                ],
                "missing_main_points": [],
                "supportive_subplot_points": [],
                "weak_subplot_points": [],
                "repair_actions": [],
            },
            chapter_outline=outline,
            chapter_plan=plan,
            chapter_text=before if coverage == "missing" else after,
            chapter_number=2,
        )
        payload.pop("_model_score", None)
        return AlignmentReport.model_validate(payload)

    rechecked = adjudicate_targeted_alignment_recheck(
        previous_report=report("missing", "high", 0.95),
        candidate_report=report("partial", "medium", 0.8),
        before_text=before,
        after_text=after,
        repair_round=1,
    )

    assert verified_alignment_blockers(rechecked) == []
    assert rechecked.repair_tickets == []
    assert rechecked.repair_verifications[0].status == "resolved"
    assert rechecked.review_findings[0].metadata["coverage_status"] == "partial"


async def test_alignment_execute_forwards_only_chapter_scope_context(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    step = object.__new__(AlignmentStep)
    step._settings = SimpleNamespace(temp_check_alignment=0.15)

    def fake_dynamic_max_tokens(
        self,
        task_type,
        target_output_chars,
        *,
        prompt_overhead,
        min_tokens,
    ) -> int:
        captured["budget"] = {
            "task_type": task_type,
            "target_output_chars": target_output_chars,
            "prompt_overhead": prompt_overhead,
            "min_tokens": min_tokens,
        }
        return 4096

    async def fake_call_with_retry(self, task_type, context, **kwargs):
        captured["task_type"] = task_type
        captured["context"] = context
        captured["kwargs"] = kwargs
        return {
            "alignment_score": 9.0,
            "risk_level": "low",
            "summary": "章节与大纲总体一致。",
            "missing_main_points": [],
            "supportive_subplot_points": [],
            "weak_subplot_points": [],
            "repair_actions": [],
        }

    monkeypatch.setattr(AlignmentStep, "_dynamic_max_tokens", fake_dynamic_max_tokens)
    monkeypatch.setattr(AlignmentStep, "_call_with_retry", fake_call_with_retry)

    input_data = SimpleNamespace(
        chapter_outline={
            "goal": "只看本章目标",
            "global_phase": "不应转发的宏观阶段",
            "main_plot_points": ["主线点"],
        },
        chapter_plan={
            "scene_intents": [
                {
                    "summary": "场景目标",
                    "required_outcome": "必须结果",
                    "owned_events": ["先触发机关", "再取得账簿"],
                    "owned_revelations": ["账簿指向内鬼"],
                    "owned_state_changes": ["主角从未持有到账簿在手"],
                    "internal_notes": "不应转发的内部备注",
                }
            ],
            "forbidden_elements": ["不应转发的禁用元素"],
        },
        chapter_text="正文覆盖了本章目标。",
        narrative_context={"current_phase_name": "不应转发的宏观阶段"},
        genre="不应转发的题材",
        pov_hint="不应转发的视角比例",
    )

    report = await step._execute(input_data)

    assert captured["task_type"] == TaskType.CHECK_ALIGNMENT
    assert set(captured["context"]) == {"chapter_outline", "chapter_plan", "chapter_text"}
    assert "narrative_context" not in captured["context"]
    assert "genre" not in captured["context"]
    assert "pov_hint" not in captured["context"]
    assert not hasattr(captured["context"]["chapter_outline"], "global_phase")
    assert not hasattr(captured["context"]["chapter_plan"], "forbidden_elements")
    assert not hasattr(captured["context"]["chapter_plan"].scene_intents[0], "internal_notes")
    assert captured["context"]["chapter_plan"].scene_intents[0].owned_events == [
        "先触发机关",
        "再取得账簿",
    ]
    assert captured["context"]["chapter_plan"].scene_intents[0].owned_revelations == [
        "账簿指向内鬼"
    ]
    assert captured["kwargs"]["temperature"] == 0.15
    assert report.alignment_score == 9.6
