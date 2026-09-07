"""Tests for project inspection and workspace summaries."""

from __future__ import annotations

from pathlib import Path

from novel_forge.core.project_state import ProjectOperation, ProjectState, ProjectStateMachine
from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile, StoryBible
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.core.utils.text_validation import display_word_count
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _save_outline_batch_checkpoint,
)
from novel_forge.story_kernel.schemas import StoryKernel
from novel_forge.workspace.projects import ProjectInspector


def test_project_inspector_reads_short_and_long_projects(tmp_storage, router) -> None:
    short_layout = ProjectLayout(tmp_storage.project_dir("short_demo"))
    short_layout.ensure_dirs()
    short_spec = StorySpec(
        title="短篇示例",
        theme="一枚旧印章牵出前朝密约",
        genre="historical",
        tone="mysterious",
        length_target=2600,
    )
    tmp_storage.save_json(short_layout.spec_path, short_spec.model_dump(mode="json"))
    tmp_storage.save_text(
        short_layout.chapters_dir / "short_story.md",
        "雨夜里，印泥上的朱砂像一滴尚未凝固的血。主角循着旧印章的来路，翻出了被尘封的密约。",
    )

    long_layout = ProjectLayout(tmp_storage.project_dir("long_demo"))
    long_layout.ensure_dirs()
    tmp_storage.save_json(
        long_layout.spec_path,
        StorySpec(
            title="遗物人生",
            theme="整理遗物的人渐渐拼出逝者的另一生",
            genre="literary",
            tone="warm",
            length_target=72000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        long_layout.bible_path,
        StoryBible(
            title="遗物人生",
            premise="一名遗物整理师在旧物中不断看见被忽略的人生纹理。",
            tone="warm",
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        long_layout.characters_path,
        CharacterBible(
            characters=[
                CharacterProfile(name="沈砚", role="protagonist", notes="遗物整理师"),
            ]
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        long_layout.outline_path,
        StoryOutline(
            total_chapters=12,
            synopsis="围绕十二件遗物，拼出一段隐秘家史。",
            volume_mode=False,
            chapters=[
                ChapterOutline(
                    chapter_number=1,
                    title="旧怀表",
                    goal="建立工作与第一位委托人的关系。",
                    expected_word_count=3200,
                ),
            ],
        ).model_dump(mode="json"),
    )
    canon = StoryKernel(project_id="long_demo")
    tmp_storage.save_json(
        long_layout.canon_dir / "canon_current.json",
        canon.model_dump(mode="json"),
    )
    chapter_text = "怀表盖弹开的那一刻，沈砚听见了屋里最轻的一声叹息。"
    tmp_storage.save_text(long_layout.chapter_path(1), chapter_text)
    tmp_storage.save_json(
        long_layout.chapter_meta_path(1),
        {"title": "旧怀表", "word_count": 3210},
    )
    tmp_storage.save_json(
        long_layout.eval_report_path(1),
        EvalReport(overall_score=8.4, passed=True).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        long_layout.continuity_report_path(1),
        {"continuity_score": 8.8, "issues": []},
    )

    inspector = ProjectInspector(tmp_storage)

    projects = inspector.list_projects()
    assert {item.project_id for item in projects} == {"short_demo", "long_demo"}

    long_detail = inspector.get_project_detail("long_demo")
    assert long_detail.mode == "long"
    assert long_detail.completed_chapters == 1
    assert long_detail.total_chapters == 12
    assert long_detail.chapters[0].word_count == display_word_count(chapter_text)
    assert long_detail.chapters[0].overall_score == 8.4
    assert long_detail.project_state == "writing"
    assert long_detail.project_state_source == "inferred"
    assert "complete" in long_detail.allowed_operations

    overview = inspector.overview(router)
    assert overview.total_projects == 2
    assert overview.long_projects == 1
    assert overview.short_projects == 1


def test_project_inspector_detects_long_init_before_first_long_artifact(tmp_storage) -> None:
    """An early failed init must remain a resumable long-form project."""
    layout = ProjectLayout(tmp_storage.project_dir("early_init_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="早期中断的长篇",
            theme="尚未生成故事圣经时中断",
            genre="mystery",
            tone="neutral",
            length_target=480000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.init_request_meta_path,
        {"schema_version": 1, "request": {"init_input": {"premise": "早期中断"}}},
    )

    detail = ProjectInspector(tmp_storage).get_project_detail("early_init_demo")

    assert detail.mode == "long"
    assert detail.init_resume_available is True
    assert detail.init_resume_step == "init_story_bible"


def test_project_inspector_filters_storage_data_dirs(tmp_storage, router) -> None:
    tmp_storage.save_text(
        tmp_storage.root / "default" / "reports" / "polish_outline_history.jsonl",
        "{}\n",
    )
    tmp_storage.save_json(tmp_storage.root / "data_index" / "cache.json", {"kind": "cache"})
    (tmp_storage.root / "dlq").mkdir()

    layout = ProjectLayout(tmp_storage.project_dir("reader_project"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="可见项目",
            theme="真实创作项目",
            genre="literary",
            tone="warm",
            length_target=3000,
        ).model_dump(mode="json"),
    )

    inspector = ProjectInspector(tmp_storage)

    assert inspector.list_project_ids() == ["reader_project"]
    assert {item.project_id for item in inspector.list_projects()} == {"reader_project"}
    assert inspector.overview(router).total_projects == 1


def test_project_inspector_filters_generated_test_project_names(tmp_storage) -> None:
    for project_id in ("test-project", "test_short_job", "smoke_long", "project_a", "project_b"):
        layout = ProjectLayout(tmp_storage.project_dir(project_id))
        layout.ensure_dirs()
        tmp_storage.save_json(
            layout.spec_path,
            StorySpec(
                title="测试项目",
                theme="测试主题",
                genre="fiction",
                tone="neutral",
                length_target=3000,
            ).model_dump(mode="json"),
        )

    visible_layout = ProjectLayout(tmp_storage.project_dir("reader_project"))
    visible_layout.ensure_dirs()
    tmp_storage.save_json(
        visible_layout.spec_path,
        StorySpec(
            title="读者项目",
            theme="真实创作项目",
            genre="fiction",
            tone="neutral",
            length_target=3000,
        ).model_dump(mode="json"),
    )

    inspector = ProjectInspector(tmp_storage)

    assert inspector.list_project_ids() == ["reader_project"]
    assert inspector.get_project_detail("test-project").title == "测试项目"


def test_project_inspector_hides_empty_auto_generated_run_traces(tmp_storage) -> None:
    empty_layout = ProjectLayout(tmp_storage.project_dir("short_1234abcd"))
    empty_layout.ensure_dirs()
    tmp_storage.save_json(empty_layout.states_dir / "short_run_meta.json", {"status": "failed"})

    authored_layout = ProjectLayout(tmp_storage.project_dir("short_deadbeef"))
    authored_layout.ensure_dirs()
    tmp_storage.save_json(
        authored_layout.spec_path,
        StorySpec(
            title="保留的短篇",
            theme="已写入创作规格，不应被当作测试残留。",
            genre="fiction",
            tone="neutral",
            length_target=3000,
        ).model_dump(mode="json"),
    )

    inspector = ProjectInspector(tmp_storage)

    assert inspector.list_project_ids() == ["short_deadbeef"]
    candidates = inspector.list_test_project_cleanup_candidates()
    assert [(item.project_id, item.reason) for item in candidates] == [
        ("short_1234abcd", "empty_generated_run")
    ]


def test_project_inspector_cleanup_requires_a_reviewed_non_active_candidate(tmp_storage) -> None:
    named_test_layout = ProjectLayout(tmp_storage.project_dir("test-project"))
    named_test_layout.ensure_dirs()
    tmp_storage.save_json(named_test_layout.spec_path, {"title": "测试项目"})

    empty_layout = ProjectLayout(tmp_storage.project_dir("short_1234abcd"))
    empty_layout.ensure_dirs()
    tmp_storage.save_json(empty_layout.states_dir / "short_run_meta.json", {"status": "failed"})

    inspector = ProjectInspector(tmp_storage)
    result = inspector.cleanup_test_project_dirs(
        ["test-project", "short_1234abcd", "reader_project"],
        protected_project_ids={"short_1234abcd"},
    )

    assert result.removed_project_ids == ("test-project",)
    assert result.skipped_project_ids == ("reader_project", "short_1234abcd")
    assert not tmp_storage.project_path("test-project").exists()
    assert tmp_storage.project_path("short_1234abcd").is_dir()


def test_project_inspector_builds_chapter_workspace_snapshot(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("long_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="遗物人生",
            theme="整理遗物的人渐渐拼出逝者的另一生",
            genre="literary",
            tone="warm",
            length_target=72000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.bible_path,
        StoryBible(
            title="遗物人生",
            premise="一名遗物整理师在旧物中不断看见被忽略的人生纹理。",
            tone="warm",
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.characters_path,
        CharacterBible(characters=[CharacterProfile(name="沈砚")]).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=3,
            synopsis="围绕三件遗物，拼出一段隐秘家史。",
            volume_mode=False,
            chapters=[
                ChapterOutline(chapter_number=1, title="旧怀表", goal="建立工作与委托人的关系。"),
                ChapterOutline(chapter_number=2, title="旧信纸", goal="查出逝者留下的第一重秘密。"),
                ChapterOutline(chapter_number=3, title="旧唱片", goal="把旧案的真相推到台前。"),
            ],
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.canon_dir / "canon_current.json",
        StoryKernel(project_id="long_demo", current_chapter=1).model_dump(mode="json"),
    )
    tmp_storage.save_text(
        layout.chapter_path(1), "怀表盖弹开的那一刻，沈砚听见了屋里最轻的一声叹息。"
    )
    tmp_storage.save_json(
        layout.chapter_meta_path(1),
        {"title": "旧怀表", "word_count": 3210},
    )
    tmp_storage.save_json(
        layout.creative_report_path(1),
        {
            "must_carry_forward": ["怀表中的旧刻字", "委托人不愿提起的旧址"],
            "suggestions_for_next_chapter": "让第 2 章先围绕旧信纸里的地址推进。",
        },
    )
    tmp_storage.save_json(
        layout.chapter_exit_state_path(1),
        {"chapter_number": 1, "must_carry_forward": ["怀表中的旧刻字"]},
    )
    tmp_storage.save_json(
        layout.chapter_checkpoint_path(2),
        {
            "checkpoint_id": "plan-002-demo",
            "checkpoint_type": "plan_checkpoint",
            "summary": "章节方案已备好。",
            "prompt": "先确认方案。",
            "options": [
                {
                    "option_id": "write_now",
                    "label": "直接写作",
                    "description": "继续写作",
                    "is_recommended": True,
                }
            ],
            "related_artifacts": [],
        },
    )
    tmp_storage.save_json(
        layout.chapter_plan_path(2),
        {"scene_intents": [], "opening_contract": "旧信纸从怀表盒夹层里滑出。"},
    )
    tmp_storage.save_json(
        layout.chapter_causal_report_path(2),
        {
            "causal_score": 8.9,
            "issues": [
                {
                    "issue_type": "opening_causal_gap",
                    "severity": "high",
                    "summary": "开头承接偏弱",
                },
                {
                    "issue_type": "event_without_cause",
                    "severity": "medium",
                    "summary": "转折铺垫略弱",
                },
            ],
        },
    )
    tmp_storage.save_json(
        layout.chapter_memory_diagnostics_path(2),
        {
            "report_type": "chapter_memory_diagnostics",
            "chapter_number": 2,
            "summary": {
                "available_stages": ["planning", "draft"],
                "latest_stage": "draft",
            },
            "stages": {
                "planning": {"resolved_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"]},
                "draft": {
                    "resolved_layers": [
                        "L0_identity",
                        "L1_core_memory",
                        "L2_on_demand",
                        "L3_deep_search",
                    ]
                },
            },
        },
    )
    tmp_storage.save_json(
        layout.knowledge_boundary_report_path(2),
        {
            "verdict": "pass",
            "findings": [],
            "hidden_candidate_count": 1,
            "prescreen_hit_count": 0,
        },
    )

    inspector = ProjectInspector(tmp_storage)
    snapshot = inspector.get_chapter_workspace_snapshot("long_demo", 2)

    assert snapshot.project_id == "long_demo"
    assert snapshot.chapter_number == 2
    assert snapshot.current_title == "旧信纸"
    assert snapshot.previous_title == "旧怀表"
    assert snapshot.next_title == "旧唱片"
    assert snapshot.pending_checkpoint is not None
    assert snapshot.pending_checkpoint.checkpoint_type == "plan_checkpoint"
    assert "怀表中的旧刻字" in snapshot.carry_forward
    assert snapshot.causal_score == 8.9
    assert snapshot.causal_issue_count == 2
    assert "因果链校验发现 2 个问题，其中高优先级 1 个，建议人工复核。" in snapshot.warnings
    assert (
        "因果校验报告未绑定正文版本，当前问题可能来自旧版文本，建议重新评估。" in snapshot.warnings
    )
    assert any(
        artifact.relative_path == "plans/chapter_002_plan.json" for artifact in snapshot.artifacts
    )
    assert any(
        artifact.relative_path == "reports/chapter_002_memory_diagnostics.json"
        for artifact in snapshot.artifacts
    )
    assert any(
        artifact.relative_path == "reports/chapter_002_knowledge_boundary_verification.json"
        and artifact.label == "知识边界审计"
        for artifact in snapshot.artifacts
    )


def test_project_inspector_refreshes_guard_checkpoint_summary_from_latest_reports(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("latest_score_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="遗物人生",
            theme="整理遗物的人渐渐拼出逝者的另一生",
            genre="literary",
            tone="warm",
            length_target=72000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.bible_path,
        StoryBible(title="遗物人生", premise="旧物追索人生。", tone="warm").model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.characters_path,
        CharacterBible(characters=[CharacterProfile(name="沈砚")]).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=2,
            synopsis="围绕两件遗物推进。",
            volume_mode=False,
            chapters=[
                ChapterOutline(chapter_number=1, title="旧怀表", goal="建立委托关系。"),
                ChapterOutline(chapter_number=2, title="旧信纸", goal="追索第一重秘密。"),
            ],
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.canon_dir / "canon_current.json",
        StoryKernel(project_id="latest_score_demo", current_chapter=1).model_dump(mode="json"),
    )
    tmp_storage.save_text(layout.chapter_path(1), "第一章正文")
    tmp_storage.save_text(layout.chapter_review_draft_path(2), "第二章正文\n经过修复后的版本。")
    tmp_storage.save_json(
        layout.chapter_checkpoint_path(2),
        {
            "checkpoint_id": "guard-002-latest",
            "checkpoint_type": "guard_checkpoint",
            "summary": (
                "章节草稿已完成，当前 4,943 字。\n"
                "对齐 6.2 / 连贯 9.3 / 质量 9.7 / 因果 9.0。\n"
                "AI 判定：continue_with_constraints / 风险 high。"
            ),
            "prompt": "请选择下一步。",
            "options": [
                {
                    "option_id": "accept_and_finalize",
                    "label": "接受并归档",
                    "description": "",
                    "is_recommended": True,
                }
            ],
            "related_artifacts": [],
        },
    )
    tmp_storage.save_json(
        layout.chapter_session_path(2),
        {
            "stage": "guard_checkpoint",
            "checkpoint_id": "guard-002-latest",
            "project_id": "latest_score_demo",
            "chapter_number": 2,
            "pending_result": {"warnings": []},
        },
    )
    tmp_storage.save_json(layout.alignment_report_path(2), {"alignment_score": 4.0})
    tmp_storage.save_json(layout.continuity_report_path(2), {"continuity_score": 9.2})
    tmp_storage.save_json(layout.eval_report_path(2), {"overall_score": 9.67})
    tmp_storage.save_json(layout.chapter_causal_report_path(2), {"causal_score": 9.0})

    snapshot = ProjectInspector(tmp_storage).get_chapter_workspace_snapshot("latest_score_demo", 2)

    assert snapshot.pending_checkpoint is not None
    summary = snapshot.pending_checkpoint.summary
    assert "对齐 4.0 / 连贯 9.2 / 质量 9.7 / 因果 9.0" in summary
    assert "对齐 6.2" not in summary
    assert "AI 判定：continue_with_constraints / 风险 high。" in summary


def test_project_inspector_keeps_completed_previous_chapter_done_in_next_view(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("chapter_done_next_view"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="魂玉",
            theme="以残玉牵出旧案与婚约。",
            genre="historical",
            tone="tense",
            length_target=360000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.bible_path,
        StoryBible(title="魂玉", premise="残玉与旧案交织。", tone="tense").model_dump(
            mode="json"
        ),
    )
    tmp_storage.save_json(
        layout.characters_path,
        CharacterBible(characters=[CharacterProfile(name="沈清漪")]).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=2,
            synopsis="第一章归档后进入第二章筹备。",
            volume_mode=False,
            chapters=[
                ChapterOutline(chapter_number=1, title="红烛铁灰", goal="完成大婚开局。"),
                ChapterOutline(chapter_number=2, title="空盏旧廊", goal="承接第一章线索。"),
            ],
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.canon_dir / "canon_current.json",
        StoryKernel(project_id="chapter_done_next_view", current_chapter=1).model_dump(
            mode="json"
        ),
    )
    tmp_storage.save_text(layout.chapter_path(1), "第一章正文已经归档。")
    tmp_storage.save_json(
        layout.quality_gate_report_path(1),
        {
            "verdict": "fail",
            "failed_dimensions": ["chapter_quality"],
        },
    )

    snapshot = ProjectInspector(tmp_storage).get_chapter_workspace_snapshot(
        "chapter_done_next_view",
        2,
    )

    chapter_1 = next(ch for ch in snapshot.chapters if ch.chapter_number == 1)
    chapter_2 = next(ch for ch in snapshot.chapters if ch.chapter_number == 2)
    assert chapter_1.status == "done"
    assert chapter_1.status_label == "已完成"
    assert chapter_2.status == "current"
    assert chapter_2.status_label == "当前章"


def test_project_inspector_hides_stale_pending_checkpoint_after_upstream_rewrite(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("stale_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="遗物人生",
            theme="整理遗物的人渐渐拼出逝者的另一生",
            genre="literary",
            tone="warm",
            length_target=72000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.bible_path,
        StoryBible(
            title="遗物人生",
            premise="一名遗物整理师在旧物中不断看见被忽略的人生纹理。",
            tone="warm",
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.characters_path,
        CharacterBible(characters=[CharacterProfile(name="沈砚")]).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=4,
            synopsis="围绕四件遗物，拼出一段隐秘家史。",
            volume_mode=False,
            chapters=[
                ChapterOutline(chapter_number=1, title="旧怀表", goal="建立工作与委托人的关系。"),
                ChapterOutline(chapter_number=2, title="旧信纸", goal="查出逝者留下的第一重秘密。"),
                ChapterOutline(chapter_number=3, title="旧唱片", goal="把旧案的真相推到台前。"),
                ChapterOutline(chapter_number=4, title="旧录音机", goal="让真相彻底落地。"),
            ],
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.canon_dir / "canon_current.json",
        StoryKernel(project_id="stale_demo", current_chapter=1).model_dump(mode="json"),
    )
    tmp_storage.save_text(layout.chapter_path(1), "第一章正文")
    tmp_storage.save_text(layout.chapter_path(2), "旧版第二章正文")
    tmp_storage.save_json(
        layout.chapter_checkpoint_path(2),
        {
            "checkpoint_id": "guard-002-stale",
            "checkpoint_type": "guard_checkpoint",
            "summary": "旧 guard checkpoint",
            "prompt": "不应该再显示",
            "options": [
                {
                    "option_id": "accept_and_finalize",
                    "label": "接受并归档",
                    "description": "",
                    "is_recommended": True,
                }
            ],
            "related_artifacts": [],
        },
    )
    tmp_storage.save_json(
        layout.chapter_session_path(2),
        {
            "stage": "guard_checkpoint",
            "checkpoint_id": "guard-002-stale",
            "project_id": "stale_demo",
            "chapter_number": 2,
            "pending_result": {"warnings": ["旧链路提醒"]},
        },
    )
    tmp_storage.save_json(
        layout.chapter_checkpoint_path(3),
        {
            "checkpoint_id": "plan-003-stale",
            "checkpoint_type": "plan_checkpoint",
            "summary": "旧 plan checkpoint",
            "prompt": "也不应该再显示",
            "options": [
                {
                    "option_id": "write_now",
                    "label": "继续写作",
                    "description": "",
                    "is_recommended": True,
                }
            ],
            "related_artifacts": [],
        },
    )

    inspector = ProjectInspector(tmp_storage)

    chapter_2_snapshot = inspector.get_chapter_workspace_snapshot("stale_demo", 2)
    chapter_3_snapshot = inspector.get_chapter_workspace_snapshot("stale_demo", 3)

    chapter_2_status = next(
        chapter.status for chapter in chapter_2_snapshot.chapters if chapter.chapter_number == 2
    )
    chapter_3_status = next(
        chapter.status for chapter in chapter_3_snapshot.chapters if chapter.chapter_number == 3
    )

    assert chapter_2_snapshot.pending_checkpoint is None
    assert chapter_3_snapshot.pending_checkpoint is None
    assert chapter_2_status == "stale"
    assert chapter_3_status == "stale"
    assert chapter_3_snapshot.warnings[0] == "第 2 章之后的内容基于旧链路，需先从第 2 章重新生成。"


def test_project_inspector_keeps_fresh_checkpoint_visible_on_stale_cutoff(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("stale_fresh_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="遗物人生",
            theme="整理遗物的人渐渐拼出逝者的另一生",
            genre="literary",
            tone="warm",
            length_target=72000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.bible_path,
        StoryBible(
            title="遗物人生",
            premise="一名遗物整理师在旧物中不断看见被忽略的人生纹理。",
            tone="warm",
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.characters_path,
        CharacterBible(characters=[CharacterProfile(name="沈砚")]).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=3,
            synopsis="围绕三件遗物推进。",
            volume_mode=False,
            chapters=[
                ChapterOutline(chapter_number=1, title="旧怀表", goal="建立工作与委托人的关系。"),
                ChapterOutline(chapter_number=2, title="旧信纸", goal="查出逝者留下的第一重秘密。"),
                ChapterOutline(chapter_number=3, title="旧唱片", goal="把旧案的真相推到台前。"),
            ],
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.canon_dir / "canon_current.json",
        StoryKernel(project_id="stale_fresh_demo", current_chapter=1).model_dump(mode="json"),
    )
    tmp_storage.save_text(layout.chapter_path(1), "第一章正文")
    tmp_storage.save_text(layout.chapter_path(2), "旧版第二章正文")
    tmp_storage.save_json(
        layout.chapter_checkpoint_path(2),
        {
            "checkpoint_id": "plan-002-fresh",
            "checkpoint_type": "plan_checkpoint",
            "summary": "新链路方案 checkpoint",
            "prompt": "可继续写作",
            "options": [
                {
                    "option_id": "write_now",
                    "label": "继续写作",
                    "description": "",
                    "is_recommended": True,
                }
            ],
            "related_artifacts": [],
        },
    )
    tmp_storage.save_json(
        layout.chapter_session_path(2),
        {
            "stage": "plan_checkpoint",
            "checkpoint_id": "plan-002-fresh",
            "project_id": "stale_fresh_demo",
            "chapter_number": 2,
            "canon_watermark": 1,
            "trace_summary": {},
        },
    )

    snapshot = ProjectInspector(tmp_storage).get_chapter_workspace_snapshot("stale_fresh_demo", 2)
    chapter_2_status = next(
        chapter.status for chapter in snapshot.chapters if chapter.chapter_number == 2
    )

    assert snapshot.pending_checkpoint is not None
    assert snapshot.pending_checkpoint.checkpoint_id == "plan-002-fresh"
    assert chapter_2_status == "rewriting"


def test_project_inspector_keeps_broken_projects_visible(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("broken_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.outline_path,
        {
            "total_chapters": "bad-data",
            "volume_mode": False,
            "chapters": [],
        },
    )

    projects = ProjectInspector(tmp_storage).list_projects()
    broken = next(item for item in projects if item.project_id == "broken_demo")

    assert broken.mode == "long"
    assert broken.load_status == "degraded"
    assert "ValidationError" in broken.load_error


def test_project_inspector_skips_files_that_disappear_during_recent_scan(
    tmp_storage,
    monkeypatch,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("race_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="竞态项目",
            theme="扫描时 SQLite sidecar 可能消失",
            genre="romance",
            tone="suspenseful",
            length_target=3000,
        ).model_dump(mode="json"),
    )
    transient = layout.root / "story_kernel.db-wal"
    tmp_storage.save_text(transient, "")
    original_stat = Path.stat

    def flaky_stat(path: Path, *args, **kwargs):
        if path == transient:
            raise FileNotFoundError(str(path))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    detail = ProjectInspector(tmp_storage).get_project_detail("race_demo")

    assert detail.load_status == "ok"
    assert "story_kernel.db-wal" not in detail.recent_files


def test_project_inspector_marks_resumable_long_init_progress(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("resume_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="遗物人生",
            theme="整理遗物的人渐渐拼出逝者的另一生",
            genre="romance",
            tone="suspenseful",
            language="zh",
            characters_hint="遗物整理师、被覆盖身份的高层",
            world_hint="近未来海港都市",
            conflict_hint="越靠近真相，人生越会被重写",
            pov_hint="女主主视角",
            opening_style="高概念开场",
            ending_style="余韵式 HE",
            extra_instructions="强化情绪拉扯",
            length_target=162000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.bible_path,
        StoryBible(
            title="我替死人整理人生",
            premise="数字遗物整理师追查一位死去三次的男人。",
            tone="warm",
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.characters_path,
        CharacterBible(characters=[CharacterProfile(name="陆栖")]).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=36,
            synopsis="围绕三十六章案件与主线真相推进。",
            volume_mode=False,
            chapters=[
                ChapterOutline(
                    chapter_number=num,
                    title=f"第{num}章",
                    goal=f"推进第{num}章剧情",
                    beats_summary=[f"第{num}章节拍"],
                )
                for num in range(1, 31)
            ],
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "total_chapters": 36,
            "chapters_done": 30,
            "latest_chapter_number": 30,
            "conversation_history": [],
        },
    )

    detail = ProjectInspector(tmp_storage).get_project_detail("resume_demo")

    assert detail.init_resume_available is True
    assert detail.init_resume_step == "plan_outline"
    assert detail.init_resume_progress_label == "大纲暂存 30/36 章"
    assert detail.init_resume_next_chapter == 31
    assert detail.init_resume_progress_percent > 74


def test_project_inspector_reports_verified_outline_session_safe_point(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("resume_session_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="遗物人生",
            theme="整理遗物的人渐渐拼出逝者的另一生",
            genre="romance",
            tone="suspenseful",
            language="zh",
            length_target=162000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.bible_path,
        StoryBible(
            title="我替死人整理人生",
            premise="数字遗物整理师追查一位死去三次的男人。",
            tone="warm",
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.characters_path,
        CharacterBible(characters=[CharacterProfile(name="陆栖")]).model_dump(mode="json"),
    )
    tmp_storage.save_json(layout.blueprint_path, {"synopsis": "围绕三十六章案件推进。"})
    session_id = "safe-session"
    checkpoint = _save_outline_batch_checkpoint(
        tmp_storage,
        layout,
        total_chapters=36,
        batch_start=1,
        batch_end=6,
        chapters=[
            ChapterOutline(
                chapter_number=num,
                title=f"潮汐暗线{num}",
                goal=f"追查第{num}处海港密账。",
                beats_summary=[f"第{num}章节拍"],
            )
            for num in range(1, 7)
        ],
        missing_chapters=[],
        status="accepted",
        session_id=session_id,
    )
    tmp_storage.save_json(
        layout.outline_session_path,
        {
            "schema_version": "1.1",
            "status": "recoverable",
            "session_id": session_id,
            "total_chapters": 36,
            "chapters_done": 6,
            "latest_chapter_number": 6,
            "last_safe_chapter": 6,
            "accepted_batches": [
                {
                    "batch_start": 1,
                    "batch_end": 6,
                    "accepted_chapters": [1, 2, 3, 4, 5, 6],
                    "checkpoint_file": "batch_001_006.json",
                    "content_hash": checkpoint["content_hash"],
                    "source_hash": checkpoint.get("source_hash", ""),
                    "session_id": session_id,
                }
            ],
        },
    )

    detail = ProjectInspector(tmp_storage).get_project_detail("resume_session_demo")

    assert detail.init_resume_available is True
    assert detail.init_resume_step == "plan_outline"
    assert detail.init_resume_progress_label == "大纲已安全保存到第6章（6/36章）"
    assert detail.init_resume_next_chapter == 7
    assert detail.outline_generated_count == 6


def test_project_inspector_prefers_persisted_project_state_over_inference(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("paused_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="暂停中的长篇",
            theme="档案整理师追查旧案",
            genre="mystery",
            tone="cold",
            length_target=60000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=10,
            synopsis="追查十年前悬案。",
            volume_mode=False,
            chapters=[
                ChapterOutline(chapter_number=1, title="档案室", goal="引出旧案"),
            ],
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.canon_dir / "canon_current.json",
        StoryKernel(project_id="paused_demo", current_chapter=1).model_dump(mode="json"),
    )
    tmp_storage.save_text(layout.chapter_path(1), "第一章正文")

    machine = ProjectStateMachine("paused_demo", project_dir=layout.root)
    machine.execute(ProjectOperation.INIT)
    machine.execute(ProjectOperation.COMPLETE_INIT)
    machine.execute(ProjectOperation.START_WRITING)
    machine.execute(ProjectOperation.PAUSE)

    detail = ProjectInspector(tmp_storage).get_project_detail("paused_demo")

    assert detail.project_state == ProjectState.PAUSED.value
    assert detail.project_state_label == "已暂停"
    assert detail.project_state_source == "state_file"
    assert detail.allowed_operations == ["archive", "resume"]
