"""Tests for lightweight narrative element progress tracking."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.chapter import AlignmentReport, ChapterRepairReport
from novel_forge.core.schemas.continuity import (
    ChapterPlan,
    ContinuityIssue,
    ContinuityReport,
    SceneIntent,
)
from novel_forge.gateway.types import ModelResponse
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.element_progress import (
    build_planning_hint,
    finalize_chapter_progress,
    finalize_chapter_progress_with_optional_arbiter,
    load_element_progress,
    record_schedule,
    suggest_dynamic_focus_ids,
)


def test_record_and_finalize_element_progress(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_progress_demo"))
    layout.ensure_dirs()

    scheduled = record_schedule(
        tmp_storage,
        layout,
        chapter_number=1,
        scheduled_ids=["romance_emotional_barriers"],
        focus_source="outline",
    )
    assert scheduled["scheduled_element_ids"] == ["romance_emotional_barriers"]

    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(
                scene_id="scene_01",
                summary="主角直面情感障碍并尝试沟通",
                purpose="推进关系线",
                conflict="误解与防御心理",
                required_outcome="关系出现可见松动",
            )
        ],
        emotional_arc="从防御到试探性信任",
    )
    chapter_data = finalize_chapter_progress(
        tmp_storage,
        layout,
        chapter_number=1,
        fallback_scheduled_ids=[],
        focus_source="outline",
        element_cards_by_id={
            "romance_emotional_barriers": {
                "element_id": "romance_emotional_barriers",
                "name": "情感障碍",
                "category": "言情机制",
                "prompt_hint": "设计情感障碍的形成与突破过程。",
                "description": "推进角色关系障碍与突破。",
            }
        },
        plan=plan,
        chapter_text="她终于承认自己设置的情感障碍，让两人关系开始松动。",
        alignment_report=AlignmentReport(alignment_score=8.2, summary="主线推进明确"),
        chapter_repair_report=ChapterRepairReport(summary="基本稳定"),
        continuity_report=ContinuityReport(continuity_score=8.5, summary="承接自然"),
    )
    assert chapter_data is not None
    assert chapter_data["summary"]["hit"] == 1
    assert chapter_data["results"][0]["status"] == "hit"

    payload = load_element_progress(tmp_storage, layout)
    assert payload["totals"]["hit"] == 1
    assert payload["pending_element_ids"] == []


def test_structural_verification_anchors_can_score_abstract_elements(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_progress_structural"))
    layout.ensure_dirs()
    record_schedule(
        tmp_storage,
        layout,
        chapter_number=1,
        scheduled_ids=["literary_silence_subtext"],
        focus_source="outline",
    )

    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(scene_id="scene_01", summary="关键对话以沉默和环境细节承载情绪")
        ]
    )
    chapter_data = finalize_chapter_progress(
        tmp_storage,
        layout,
        chapter_number=1,
        fallback_scheduled_ids=[],
        focus_source="outline",
        element_cards_by_id={
            "literary_silence_subtext": {
                "element_id": "literary_silence_subtext",
                "name": "沉默与潜台词",
                "category": "文学风格",
                "prompt_hint": "用沉默和动作承载潜台词。",
                "description": "未说出口的信息要能被读者读出。",
                "implementation_guide": "至少一处用沉默、停顿或环境细节承载潜台词。",
                "verification_anchors": ["未直说的情绪段落", "环境细节承载心理"],
                "verification_mode": "structural",
            }
        },
        plan=plan,
        chapter_text="她没有回答，只盯着窗外。雨声敲在杯沿上，他也没有再问。",
        alignment_report=AlignmentReport(alignment_score=8.0, summary="主线稳定"),
        chapter_repair_report=ChapterRepairReport(summary="无明显错误"),
        continuity_report=ContinuityReport(continuity_score=8.2, summary="承接自然"),
    )

    assert chapter_data is not None
    assert chapter_data["results"][0]["status"] == "hit"
    assert "环境细节承载心理" in chapter_data["results"][0]["evidence"]["structural_hits"]


def test_build_planning_hint_prefers_miss_and_weak(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_progress_hint"))
    layout.ensure_dirs()

    # Chapter 1: miss
    record_schedule(
        tmp_storage,
        layout,
        chapter_number=1,
        scheduled_ids=["mystery_clue_ledger"],
        focus_source="outline",
    )
    finalize_chapter_progress(
        tmp_storage,
        layout,
        chapter_number=1,
        fallback_scheduled_ids=[],
        focus_source="outline",
        element_cards_by_id={
            "mystery_clue_ledger": {
                "element_id": "mystery_clue_ledger",
                "name": "线索台账",
                "category": "悬疑推理",
                "prompt_hint": "记录线索并推进破案。",
                "description": "对线索进行追踪。",
            }
        },
        plan=ChapterPlan(scene_intents=[]),
        chapter_text="本章主要是环境铺垫。",
        alignment_report=AlignmentReport(alignment_score=6.8, summary="存在推进不足"),
        chapter_repair_report=ChapterRepairReport(
            summary="线索推进缺失",
            expression_errors=["线索部分未体现"],
        ),
        continuity_report=ContinuityReport(
            continuity_score=7.1,
            summary="承接正常",
            issues=[
                ContinuityIssue(
                    issue_type="plot",
                    severity="medium",
                    summary="线索推进薄弱",
                )
            ],
        ),
    )

    # Chapter 2: weak
    record_schedule(
        tmp_storage,
        layout,
        chapter_number=2,
        scheduled_ids=["romance_sweet_bitter_ratio"],
        focus_source="outline",
    )
    finalize_chapter_progress(
        tmp_storage,
        layout,
        chapter_number=2,
        fallback_scheduled_ids=[],
        focus_source="outline",
        element_cards_by_id={
            "romance_sweet_bitter_ratio": {
                "element_id": "romance_sweet_bitter_ratio",
                "name": "甜虐配比",
                "category": "言情机制",
                "prompt_hint": "平衡甜蜜与挫折节奏。",
                "description": "关系推进节奏平衡。",
            }
        },
        plan=ChapterPlan(
            scene_intents=[
                SceneIntent(
                    scene_id="scene_01",
                    summary="两人短暂和解后再度误会",
                )
            ]
        ),
        chapter_text="他们和解后又因旧事发生争执。",
        alignment_report=AlignmentReport(alignment_score=7.5, summary="基本达标"),
        chapter_repair_report=ChapterRepairReport(summary="有轻微节奏问题"),
        continuity_report=ContinuityReport(continuity_score=7.8, summary="稳定"),
    )

    hint = build_planning_hint(tmp_storage, layout, chapter_number=3)
    assert hint is not None
    recommended = set(hint.get("recommended_focus_ids", []))
    assert "mystery_clue_ledger" in recommended or "romance_sweet_bitter_ratio" in recommended
    assert hint.get("recent_missed") or hint.get("recent_weak")


def test_build_planning_hint_exposes_remediation_hint(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_progress_remediation"))
    layout.ensure_dirs()
    record_schedule(
        tmp_storage,
        layout,
        chapter_number=1,
        scheduled_ids=["mystery_clue_ledger"],
        focus_source="outline",
    )
    finalize_chapter_progress(
        tmp_storage,
        layout,
        chapter_number=1,
        fallback_scheduled_ids=[],
        focus_source="outline",
        element_cards_by_id={
            "mystery_clue_ledger": {
                "element_id": "mystery_clue_ledger",
                "name": "线索账本",
                "category": "悬疑推理",
                "prompt_hint": "植入可回溯线索。",
                "description": "记录线索投放和回收。",
                "implementation_guide": "下一章至少植入一条可回溯线索。",
                "verification_anchors": ["新出现的可记录物件"],
                "verification_mode": "structural",
            }
        },
        plan=ChapterPlan(scene_intents=[]),
        chapter_text="本章主要是环境铺垫。",
        alignment_report=AlignmentReport(alignment_score=8.0, summary="主线稳定"),
        chapter_repair_report=ChapterRepairReport(summary="无明显错误"),
        continuity_report=ContinuityReport(continuity_score=8.2, summary="承接自然"),
    )

    hint = build_planning_hint(tmp_storage, layout, chapter_number=2)

    assert hint is not None
    assert hint["recent_missed"][0]["miss_reason"].startswith("未检测到")
    assert hint["recent_missed"][0]["remediation_hint"] == "下一章至少植入一条可回溯线索。"


def test_suggest_dynamic_focus_only_when_outline_focus_empty() -> None:
    class _Outline:
        def __init__(self, focus: list[str]) -> None:
            self.element_focus = focus

    hint = {"recommended_focus_ids": ["mystery_clue_ledger", "unknown_id"]}
    dynamic_focus = suggest_dynamic_focus_ids(
        chapter_outline=_Outline([]),
        extension_ids=["mystery_clue_ledger", "romance_sweet_bitter_ratio"],
        planning_hint=hint,
    )
    assert dynamic_focus == ["mystery_clue_ledger"]

    dynamic_focus_when_locked = suggest_dynamic_focus_ids(
        chapter_outline=_Outline(["romance_sweet_bitter_ratio"]),
        extension_ids=["mystery_clue_ledger", "romance_sweet_bitter_ratio"],
        planning_hint=hint,
    )
    assert dynamic_focus_when_locked == []


def test_finalize_progress_with_optional_arbiter(tmp_storage) -> None:
    class _Router:
        def __init__(self) -> None:
            self.calls = 0
            self.last_task_type = None

        async def route(self, _request):
            self.calls += 1
            self.last_task_type = _request.task_type
            return ModelResponse(
                content='{"status":"hit","confidence":0.83,"reason":"正文已有明确障碍与突破描写"}',
                model_id="mock-model",
                prompt_tokens=10,
                completion_tokens=8,
                total_tokens=18,
                latency_ms=1.0,
                cost_usd=0.0,
            )

    layout = ProjectLayout(tmp_storage.project_dir("element_progress_arbiter"))
    layout.ensure_dirs()
    record_schedule(
        tmp_storage,
        layout,
        chapter_number=1,
        scheduled_ids=["test_element_gate"],
        focus_source="outline",
    )

    plan = ChapterPlan(scene_intents=[SceneIntent(scene_id="scene_01", summary="障碍门出现但未解决")])
    # 先产出 weak（灰区）结果，再由仲裁改为 hit
    router = _Router()
    settings = SimpleNamespace(
        element_progress_llm_arbiter_enabled=True,
        element_progress_llm_arbiter_max_items_per_chapter=1,
        element_progress_llm_gray_score_low=0.0,
        element_progress_llm_gray_score_high=2.0,
        element_progress_llm_arbiter_max_tokens=256,
        element_progress_llm_arbiter_temperature=0.0,
    )
    chapter_data = asyncio.run(
        finalize_chapter_progress_with_optional_arbiter(
            tmp_storage,
            layout,
            chapter_number=1,
            fallback_scheduled_ids=[],
            focus_source="outline",
            element_cards_by_id={
                "test_element_gate": {
                    "element_id": "test_element_gate",
                    "name": "障碍门",
                    "category": "测试机制",
                    "prompt_hint": "必须通过障碍门才能推进。",
                    "description": "障碍门推进测试。",
                }
            },
            plan=plan,
            chapter_text="本章只发生了普通对话，没有真正解决核心障碍。",
            alignment_report=AlignmentReport(alignment_score=8.0, summary="主线可接受"),
            chapter_repair_report=ChapterRepairReport(summary="无明显错误"),
            continuity_report=ContinuityReport(continuity_score=8.1, summary="承接稳定"),
            router=router,
            settings=settings,
        )
    )
    assert chapter_data is not None
    assert router.calls == 1
    assert router.last_task_type == TaskType.ELEMENT_PROGRESS_ARBITER
    assert chapter_data["results"][0]["status"] == "hit"
    assert chapter_data["arbiter"]["changed_count"] == 1
    payload = load_element_progress(tmp_storage, layout)
    assert payload["arbiter_totals"]["runs"] == 1
    assert payload["arbiter_totals"]["reviewed"] == 1
    assert payload["arbiter_totals"]["changed"] == 1


def test_llm_verification_mode_enters_arbiter_without_gray_score(tmp_storage) -> None:
    class _Router:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, _request):
            self.calls += 1
            return ModelResponse(
                content='{"status":"miss","confidence":0.75,"reason":"没有可验证的潜台词落实"}',
                model_id="mock-model",
                prompt_tokens=10,
                completion_tokens=8,
                total_tokens=18,
                latency_ms=1.0,
                cost_usd=0.0,
            )

    layout = ProjectLayout(tmp_storage.project_dir("element_progress_llm_mode"))
    layout.ensure_dirs()
    record_schedule(
        tmp_storage,
        layout,
        chapter_number=1,
        scheduled_ids=["literary_silence_subtext"],
        focus_source="outline",
    )
    router = _Router()
    settings = SimpleNamespace(
        element_progress_llm_arbiter_enabled=True,
        element_progress_llm_arbiter_max_items_per_chapter=1,
        element_progress_llm_gray_score_low=1.1,
        element_progress_llm_gray_score_high=1.2,
        element_progress_llm_arbiter_max_tokens=256,
        element_progress_llm_arbiter_temperature=0.0,
    )

    chapter_data = asyncio.run(
        finalize_chapter_progress_with_optional_arbiter(
            tmp_storage,
            layout,
            chapter_number=1,
            fallback_scheduled_ids=[],
            focus_source="outline",
            element_cards_by_id={
                "literary_silence_subtext": {
                    "element_id": "literary_silence_subtext",
                    "name": "沉默与潜台词",
                    "category": "文学风格",
                    "prompt_hint": "用沉默承载潜台词。",
                    "description": "未说出口的信息要能被读者读出。",
                    "implementation_guide": "至少一处用沉默或环境细节承载潜台词。",
                    "verification_anchors": ["未直说的情绪段落"],
                    "verification_mode": "llm",
                }
            },
            plan=ChapterPlan(scene_intents=[]),
            chapter_text="本章主要完成地点移动。",
            alignment_report=AlignmentReport(alignment_score=8.0, summary="主线可接受"),
            chapter_repair_report=ChapterRepairReport(summary="无明显错误"),
            continuity_report=ContinuityReport(continuity_score=8.1, summary="承接稳定"),
            router=router,
            settings=settings,
        )
    )

    assert chapter_data is not None
    assert router.calls == 1
    assert chapter_data["results"][0]["status"] == "miss"


def test_finalize_progress_with_optional_arbiter_retries_after_missing_required_key(tmp_storage) -> None:
    class _Router:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, _request):
            self.calls += 1
            if self.calls == 1:
                content = '{"status":"hit","confidence":0.61}'
            else:
                content = '{"status":"hit","confidence":0.83,"reason":"正文已有明确障碍与突破描写"}'
            return ModelResponse(
                content=content,
                model_id="mock-model",
                prompt_tokens=10,
                completion_tokens=8,
                total_tokens=18,
                latency_ms=1.0,
                cost_usd=0.0,
            )

    layout = ProjectLayout(tmp_storage.project_dir("element_progress_retry"))
    layout.ensure_dirs()
    record_schedule(
        tmp_storage,
        layout,
        chapter_number=1,
        scheduled_ids=["test_element_gate"],
        focus_source="outline",
    )

    plan = ChapterPlan(scene_intents=[SceneIntent(scene_id="scene_01", summary="障碍门出现但未解决")])
    router = _Router()
    settings = SimpleNamespace(
        element_progress_llm_arbiter_enabled=True,
        element_progress_llm_arbiter_max_items_per_chapter=1,
        element_progress_llm_gray_score_low=0.0,
        element_progress_llm_gray_score_high=2.0,
        element_progress_llm_arbiter_max_tokens=256,
        element_progress_llm_arbiter_temperature=0.2,
    )
    chapter_data = asyncio.run(
        finalize_chapter_progress_with_optional_arbiter(
            tmp_storage,
            layout,
            chapter_number=1,
            fallback_scheduled_ids=[],
            focus_source="outline",
            element_cards_by_id={
                "test_element_gate": {
                    "element_id": "test_element_gate",
                    "name": "障碍门",
                    "category": "测试机制",
                    "prompt_hint": "必须通过障碍门才能推进。",
                    "description": "障碍门推进测试。",
                }
            },
            plan=plan,
            chapter_text="本章只发生了普通对话，没有真正解决核心障碍。",
            alignment_report=AlignmentReport(alignment_score=8.0, summary="主线可接受"),
            chapter_repair_report=ChapterRepairReport(summary="无明显错误"),
            continuity_report=ContinuityReport(continuity_score=8.1, summary="承接稳定"),
            router=router,
            settings=settings,
        )
    )

    assert chapter_data is not None
    assert router.calls == 2
    assert chapter_data["results"][0]["status"] == "hit"



def test_build_planning_hint_exposes_latest_reason(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_progress_reason_hint"))
    layout.ensure_dirs()

    tmp_storage.save_json(
        layout.element_progress_path,
        {
            "schema_version": "1.0",
            "updated_at": "2026-04-09T00:00:00+00:00",
            "chapters": {
                "1": {
                    "chapter_number": 1,
                    "results": [
                        {
                            "element_id": "mystery_clue_ledger",
                            "status": "weak",
                            "score": 1.0,
                            "evidence": {"plan_hits": [], "text_hits": [], "penalties": []},
                            "arbiter": {"status": "weak", "confidence": 0.8, "reason": "线索出现但未形成推进链"},
                        }
                    ],
                }
            },
            "totals": {"scheduled": 0, "hit": 0, "weak": 1, "miss": 0},
            "pending_element_ids": ["mystery_clue_ledger"],
            "arbiter_totals": {"runs": 1, "reviewed": 1, "changed": 0},
        },
    )

    hint = build_planning_hint(tmp_storage, layout, chapter_number=2)
    assert hint is not None
    assert hint["recent_weak"][0]["element_id"] == "mystery_clue_ledger"
    assert hint["recent_weak"][0]["latest_reason"] == "线索出现但未形成推进链"


def test_build_planning_hint_derives_reason_when_evidence_is_empty(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("element_progress_empty_reason_hint"))
    layout.ensure_dirs()

    tmp_storage.save_json(
        layout.element_progress_path,
        {
            "schema_version": "1.0",
            "updated_at": "2026-04-09T00:00:00+00:00",
            "chapters": {
                "1": {
                    "chapter_number": 1,
                    "results": [
                        {
                            "element_id": "romance_emotional_barriers",
                            "status": "miss",
                            "score": 0.0,
                            "evidence": {"plan_hits": [], "text_hits": [], "penalties": []},
                        }
                    ],
                }
            },
            "totals": {"scheduled": 0, "hit": 0, "weak": 0, "miss": 1},
            "pending_element_ids": ["romance_emotional_barriers"],
            "arbiter_totals": {"runs": 0, "reviewed": 0, "changed": 0},
        },
    )

    hint = build_planning_hint(tmp_storage, layout, chapter_number=2)

    assert hint is not None
    assert hint["recent_missed"][0]["element_id"] == "romance_emotional_barriers"
    assert hint["recent_missed"][0]["latest_reason"] == "缺少可验证的计划或正文命中证据"
