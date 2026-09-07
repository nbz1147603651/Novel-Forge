"""Tests for chapter-studio extraction modules and document renderer facade."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components import ActionButton, Badge, CollapsibleSection
from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.chapter_studio.action_panel import (
    ActionPanelRenderContext,
    ChapterStudioActionPanelPresenter,
)
from novel_forge.desktop.pages.chapter_studio.autorun import (
    AutoPilotContext,
    decide_autopilot_action,
    should_auto_submit_repair,
)
from novel_forge.desktop.pages.chapter_studio.inspector import (
    ChapterStudioInspectorPresenter,
)
from novel_forge.desktop.pages.document_renderers import smart_render_document
from novel_forge.workspace.contracts import (
    ChapterWorkspaceChapter,
    ChapterWorkspaceSnapshot,
    DecisionCheckpoint,
    DecisionOption,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _build_snapshot(*, current_status: str = "current") -> ChapterWorkspaceSnapshot:
    return ChapterWorkspaceSnapshot(
        project_id="project-demo",
        project_title="项目示例",
        chapter_number=3,
        total_chapters=8,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, status="done"),
            ChapterWorkspaceChapter(chapter_number=2, status="done"),
            ChapterWorkspaceChapter(chapter_number=3, status=current_status),
            ChapterWorkspaceChapter(chapter_number=4, status="pending"),
        ],
        current_title="第3章",
        continuity_issue_count=1,
        continuity_issues=[
            {"issue_type": "opening_gap", "severity": "medium", "summary": "承接仍有缺口"}
        ],
    )


def test_decide_autopilot_refreshes_context_for_stale_checkpoint() -> None:
    snapshot = _build_snapshot()
    snapshot.pending_checkpoint = DecisionCheckpoint(
        checkpoint_id="cp-1",
        checkpoint_type="guard_checkpoint",
        options=[
            DecisionOption(
                option_id="accept",
                label="采用推荐方案",
                is_recommended=True,
            )
        ],
    )
    job = DesktopJobRecord(
        job_id="job-1",
        kind="resolve_chapter_checkpoint",
        label="归档章节",
        project_id="project-demo",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 3},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=job,
        last_submitted_checkpoint_id="cp-1",
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="project-demo",
    ))

    assert decision.action == "refresh_context"


def test_decide_autopilot_resolves_recommended_checkpoint() -> None:
    snapshot = _build_snapshot()
    snapshot.pending_checkpoint = DecisionCheckpoint(
        checkpoint_id="cp-2",
        checkpoint_type="plan_checkpoint",
        options=[
            DecisionOption(option_id="manual", label="人工修改"),
            DecisionOption(
                option_id="recommended",
                label="采用推荐方案",
                is_recommended=True,
            ),
        ],
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=None,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="project-demo",
    ))

    assert decision.action == "resolve_checkpoint"
    assert decision.option is not None
    assert decision.option.option_id == "recommended"
    assert decision.delay_ms == 600


def test_decide_autopilot_prepares_when_book_auto_idle_without_jobs() -> None:
    """book_auto 首轮决策：无任务、无检查点、当前章未完成 → 直接提交 prepare_chapter。"""
    snapshot = _build_snapshot()

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=None,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="project-demo",
    ))

    assert decision.action == "prepare_chapter"
    assert decision.delay_ms == 600


def test_decide_autopilot_none_when_project_id_mismatch() -> None:
    """快照项目与当前项目不一致时不得误提交任何动作。"""
    snapshot = _build_snapshot()

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=None,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="other-project",
    ))

    assert decision.action == "none"


def test_decide_autopilot_none_when_studio_snapshot_none() -> None:
    """工作区快照尚未加载时不得误提交任何动作（等待事件驱动评估）。"""
    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=None,
        latest_job=None,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="project-demo",
    ))

    assert decision.action == "none"


def test_decide_book_autopilot_rewinds_to_first_stale_chapter() -> None:
    snapshot = ChapterWorkspaceSnapshot(
        project_id="project-demo",
        project_title="项目示例",
        chapter_number=3,
        total_chapters=8,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, status="done"),
            ChapterWorkspaceChapter(chapter_number=2, status="stale"),
            ChapterWorkspaceChapter(chapter_number=3, status="stale"),
            ChapterWorkspaceChapter(chapter_number=4, status="pending"),
        ],
        current_title="第3章",
    )
    job = DesktopJobRecord(
        job_id="job-stale",
        kind="prepare_chapter",
        label="章节方案准备",
        project_id="project-demo",
        status=DesktopJobState.FAILED,
        result={"chapter_number": 3},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="project-demo",
    ))

    assert decision.action == "advance_chapter"
    assert decision.next_chapter == 2


def test_decide_autopilot_ignores_failed_job_from_previous_chapter() -> None:
    snapshot = ChapterWorkspaceSnapshot(
        project_id="project-demo",
        project_title="项目示例",
        chapter_number=2,
        total_chapters=8,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, status="done"),
            ChapterWorkspaceChapter(chapter_number=2, status="stale"),
            ChapterWorkspaceChapter(chapter_number=3, status="pending"),
        ],
        current_title="第2章",
    )
    previous_job = DesktopJobRecord(
        job_id="job-old",
        kind="prepare_chapter",
        label="章节方案准备",
        project_id="project-demo",
        status=DesktopJobState.FAILED,
        result={"chapter_number": 3},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=previous_job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="project-demo",
    ))

    assert decision.action == "prepare_chapter"
    assert decision.delay_ms == 600


def test_regen_all_does_not_skip_done_chapter_when_stale_downstream() -> None:
    """全部重新生成模式下，若当前章已完成、下游有失效章节，不应跳章，应从当前章重新生成。"""
    snapshot = ChapterWorkspaceSnapshot(
        project_id="project-demo",
        project_title="项目示例",
        chapter_number=1,
        total_chapters=8,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, status="done"),
            ChapterWorkspaceChapter(chapter_number=2, status="stale"),
            ChapterWorkspaceChapter(chapter_number=3, status="stale"),
        ],
        current_title="第1章",
    )

    # 无任何内存中任务（典型的全新会话场景）
    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=None,
        last_submitted_checkpoint_id=None,
        current_chapter_done=True,
        book_auto_skip_done=False,  # 全部重新生成
        current_project_id="project-demo",
    ))

    # 不应跳到失效章节2，应就地生成当前章节1
    assert decision.action == "prepare_chapter"


def test_regen_all_with_old_succeeded_job_prepares_not_advances() -> None:
    """全部重新生成模式下，同会话中有旧的SUCCEEDED任务，不应误判为本次完成并推进。"""
    snapshot = ChapterWorkspaceSnapshot(
        project_id="project-demo",
        project_title="项目示例",
        chapter_number=1,
        total_chapters=8,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, status="done"),
            ChapterWorkspaceChapter(chapter_number=2, status="stale"),
        ],
        current_title="第1章",
    )
    old_job = DesktopJobRecord(
        job_id="job-old-resolve",
        kind="resolve_chapter_checkpoint",
        label="章节方案确认 · 第 1 章",
        project_id="project-demo",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 1, "status": "done"},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=old_job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=True,
        book_auto_skip_done=False,            # 全部重新生成
        chapter_prepared_this_run=False,       # 本次自动驾驶尚未准备本章
        current_project_id="project-demo",
    ))

    # 旧任务不应触发推进，应重新准备第1章
    assert decision.action == "prepare_chapter"


def test_decide_autopilot_refreshes_when_archive_done_but_snapshot_stale() -> None:
    """自动驾驶竞态回归：归档决策刚成功（resolve_chapter_checkpoint SUCCEEDED），
    但快照尚未刷新（current_chapter_done=False），
    应返回 refresh_context 而非触发 force=False 的 prepare_chapter。
    复现场景：18:23:45 归档第7章，18:23:46 以 force=False 重触发第7章 prepare → 预飞检查抛出
    "Chapter 7 has already been generated. Canon is at chapter 7."
    """
    snapshot = ChapterWorkspaceSnapshot(
        project_id="浮京一梦",
        project_title="浮京一梦",
        chapter_number=7,
        total_chapters=20,
        chapters=[ChapterWorkspaceChapter(chapter_number=7, status="current")],
        current_title="第7章",
    )
    archive_job = DesktopJobRecord(
        job_id="job-archive-ch7",
        kind="resolve_chapter_checkpoint",
        label="归档决策 · 第 7 章",
        project_id="浮京一梦",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 7, "status": "completed"},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=archive_job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,   # 快照尚未反映归档完成
        book_auto_skip_done=True,
        current_project_id="浮京一梦",
    ))

    # 不得立即重触发 prepare_chapter（会以 force=False 打到 preflight 报错）
    assert decision.action == "refresh_context", (
        f"期望 refresh_context，实际得到 {decision.action}；"
        "这会导致 prepare_chapter force=False 触发 already-generated 错误"
    )


def test_decide_autopilot_finishes_when_final_chapter_archive_done_but_snapshot_stale() -> None:
    """终章收尾：最后一章刚归档成功时，应结束连跑，而不是继续准备同一章。"""
    snapshot = ChapterWorkspaceSnapshot(
        project_id="与君共赴",
        project_title="与君共赴",
        chapter_number=61,
        total_chapters=61,
        chapters=[ChapterWorkspaceChapter(chapter_number=61, status="current")],
        current_title="第61章",
    )
    archive_job = DesktopJobRecord(
        job_id="job-archive-ch61",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档执行（阶段 2/2） · 与君共赴 / 第 61 章",
        project_id="与君共赴",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 61, "status": "completed"},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=archive_job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="与君共赴",
    ))

    assert decision.action == "finish"


def test_decide_autopilot_finishes_final_chapter_with_label_chapter_fallback() -> None:
    """终章归档任务的 result 短暂为空时，也能从标题识别章节并正确收尾。"""
    snapshot = ChapterWorkspaceSnapshot(
        project_id="与君共赴",
        project_title="与君共赴",
        chapter_number=61,
        total_chapters=61,
        chapters=[ChapterWorkspaceChapter(chapter_number=61, status="current")],
        current_title="第61章",
    )
    archive_job = DesktopJobRecord(
        job_id="job-archive-ch61",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档执行（阶段 2/2） · 与君共赴 / 第 61 章",
        project_id="与君共赴",
        status=DesktopJobState.SUCCEEDED,
        result={},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=archive_job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="与君共赴",
    ))

    assert decision.action == "finish"


def test_decide_autopilot_does_not_finish_final_chapter_after_pause() -> None:
    """终章选择暂停人工处理时，不应被 finalize 类型兜底误判为完结。"""
    snapshot = ChapterWorkspaceSnapshot(
        project_id="与君共赴",
        project_title="与君共赴",
        chapter_number=61,
        total_chapters=61,
        chapters=[ChapterWorkspaceChapter(chapter_number=61, status="current")],
        current_title="第61章",
    )
    paused_job = DesktopJobRecord(
        job_id="job-pause-ch61",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档暂停（阶段 2/2） · 与君共赴 / 第 61 章",
        project_id="与君共赴",
        status=DesktopJobState.SUCCEEDED,
        result={},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=paused_job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="与君共赴",
    ))

    assert decision.action != "finish"


def test_regen_all_advances_after_fresh_preparation() -> None:
    """全部重新生成模式下，本次自动驾驶刚完成本章，应正常推进到下一章。"""
    snapshot = ChapterWorkspaceSnapshot(
        project_id="project-demo",
        project_title="项目示例",
        chapter_number=1,
        total_chapters=8,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, status="done"),
            ChapterWorkspaceChapter(chapter_number=2, status="stale"),
        ],
        current_title="第1章",
    )
    fresh_job = DesktopJobRecord(
        job_id="job-new-resolve",
        kind="resolve_chapter_checkpoint",
        label="章节方案确认 · 第 1 章",
        project_id="project-demo",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 1, "status": "done"},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=fresh_job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=True,
        book_auto_skip_done=False,            # 全部重新生成
        chapter_prepared_this_run=True,        # 本次自动驾驶已准备并完成本章
        current_project_id="project-demo",
    ))

    # 本次刚完成，应推进到第2章
    assert decision.action == "advance_chapter"
    assert decision.next_chapter == 2


def test_stale_shortcut_does_not_jump_while_current_chapter_has_paused_job() -> None:
    """全书连跑：当前章节有 PAUSED job（如 guard_checkpoint 82%）时，
    stale 快捷跳转不应把 UI 推到下一章，否则 prepare pipeline 会在 canon
    回滚状态下启动并抛出"上游重写失效"RuntimeError。
    """
    # 章节轨道：第 2 章 rewriting（有 PAUSED job），第 3 章及以后全为 stale
    snapshot = ChapterWorkspaceSnapshot(
        project_id="project-demo",
        project_title="记忆人生",
        chapter_number=2,
        total_chapters=30,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, status="done"),
            ChapterWorkspaceChapter(chapter_number=2, status="rewriting"),
            ChapterWorkspaceChapter(chapter_number=3, status="stale"),
            ChapterWorkspaceChapter(chapter_number=4, status="stale"),
        ],
        current_title="第2章",
        pending_checkpoint=DecisionCheckpoint(
            checkpoint_id="guard-002",
            checkpoint_type="guard_checkpoint",
            options=[
                DecisionOption(option_id="accept", label="确认归档", is_recommended=True)
            ],
        ),
    )
    # chapter 2 job: PAUSED at 82% guard_checkpoint
    paused_job = DesktopJobRecord(
        job_id="job-ch2-paused",
        kind="resolve_chapter_checkpoint",
        label="章节方案确认 · 第 2 章",
        project_id="project-demo",
        status=DesktopJobState.PAUSED,
        result={"chapter_number": 2, "status": "needs_decision"},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=paused_job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="project-demo",
    ))

    # 应自动解析 guard_checkpoint，而不是跳到第 3 章（stale）
    assert decision.action == "resolve_checkpoint", (
        f"Expected resolve_checkpoint but got {decision.action!r}; "
        "stale shortcut must not fire while current chapter has PAUSED job"
    )
    assert decision.option is not None
    assert decision.option.option_id == "accept"


def test_stale_shortcut_does_not_jump_when_studio_has_pending_checkpoint_no_paused_job() -> None:
    """即使没有 PAUSED job（如快照刚刷新但任务尚未进入 jobs 列表），
    只要 studio.pending_checkpoint 不为 None，就不应触发 stale 跳转。
    """
    snapshot = ChapterWorkspaceSnapshot(
        project_id="project-demo",
        project_title="记忆人生",
        chapter_number=2,
        total_chapters=30,
        chapters=[
            ChapterWorkspaceChapter(chapter_number=1, status="done"),
            ChapterWorkspaceChapter(chapter_number=2, status="stale"),
            ChapterWorkspaceChapter(chapter_number=3, status="stale"),
        ],
        current_title="第2章",
        pending_checkpoint=DecisionCheckpoint(
            checkpoint_id="plan-002",
            checkpoint_type="plan_checkpoint",
            options=[
                DecisionOption(option_id="write_now", label="确认方案并写作", is_recommended=True)
            ],
        ),
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=None,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        current_project_id="project-demo",
    ))

    assert decision.action == "resolve_checkpoint", (
        f"Expected resolve_checkpoint but got {decision.action!r}"
    )


def test_should_auto_submit_repair_stops_when_only_low_issues_remain() -> None:
    snapshot = _build_snapshot()
    snapshot.continuity_issues = [
        {"issue_type": "tone", "severity": "low", "summary": "措辞还能更顺"},
        {"issue_type": "detail", "severity": "low", "summary": "描写可再补一笔"},
    ]

    decision = should_auto_submit_repair(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_repair_pending=False,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=DesktopJobRecord(
            job_id="job-2",
            kind="resolve_chapter_checkpoint",
            label="归档章节",
            project_id="project-demo",
            status=DesktopJobState.SUCCEEDED,
            result={"chapter_number": 3},
        ),
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,
        book_auto_skip_done=True,
        repair_attempts=0,
        max_repair_attempts=2,
        current_project_id="project-demo",
    ))

    assert decision is False


def test_smart_render_document_keeps_story_bible_facade_working(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "story_bible.json"
    path.write_text(
        json.dumps(
            {
                "title": "雾港纪事",
                "premise": "一座靠海的城市在潮汐中埋着旧神契约。",
                "era": "蒸汽时代余晖",
                "geography": "雾港、灯塔、旧船坞",
                "culture": "行会与潮祭并存",
                "rules": ["涨潮时不得鸣钟"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_keeps_short_blueprint_facade_working(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "short_blueprint.json"
    path.write_text(
        json.dumps(
            {
                "synopsis": "一名旧城记者在一夜潮汐里追查失踪船只。",
                "anchor_elements": {
                    "time_frame": "一夜之间",
                    "primary_locations": ["雾港码头", "钟楼"],
                    "core_characters": [{"name": "林渺", "role": "调查者"}],
                    "central_event": "失踪船只重新靠岸",
                },
                "narrative_phases": [
                    {
                        "phase_name": "开场",
                        "position_start": 0,
                        "position_end": 30,
                        "description": "记者追到码头。",
                        "tension_level": 4,
                        "emotional_focus": "不安",
                    }
                ],
                "turning_points": [],
                "emotional_arc": "从怀疑转向决绝",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_renders_bridge_with_block_separators(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "chapter_001_bridge.json"
    path.write_text(
        json.dumps(
            {
                "from_chapter": 0,
                "to_chapter": 1,
                "transition_mode": "synthetic",
                "opening_pov": "崔令仪",
                "opening_time": "夜半时分",
                "opening_location": "崔氏崇仁坊账册密室",
                "bridge_summary": "本章为视角切换章。",
                "emotional_carryover": "决心坚定；内心平静",
                "action_handoff": "崔令仪走向火盆。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "章节桥接\n第 0 章 → 第 1 章" in text
    assert "开场设定\n时间：夜半时分\n地点：崔氏崇仁坊账册密室" in text
    assert "桥接概述\n本章为视角切换章。" in text
    assert "情感承接\n决心坚定；内心平静" in text
    assert "行动交接\n崔令仪走向火盆。" in text

    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_renders_story_bible_with_block_separators(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "story_bible.json"
    path.write_text(
        json.dumps(
            {
                "title": "朱批录",
                "premise": "以算筹破局权场迷局。",
                "era": "武周末年",
                "geography": "神都洛阳与崇仁坊",
                "culture": "礼法与门阀并行",
                "rules": ["夜禁后不得擅入内卫署。"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "朱批录\n故事前提\n以算筹破局权场迷局。" in text
    assert "时代背景\n武周末年" in text
    assert "地理场景\n神都洛阳与崇仁坊" in text
    assert "社会文化\n礼法与门阀并行" in text
    assert "世界规则\n夜禁后不得擅入内卫署。" in text

    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_renders_eval_report_with_block_separators(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "quality_eval.json"
    path.write_text(
        json.dumps(
            {
                "overall_score": 9.7,
                "passed": True,
                "threshold": 6.0,
                "scores": [
                    {"dimension": "causal_chain", "score": 10.0, "comment": "因果链完整"},
                    {"dimension": "tension", "score": 8.0, "comment": "张力有起伏"},
                ],
                "summary": "整体吸引力强。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "质量评估报告\n9.7\n✓ 通过\n阈值 6.0" in text
    assert "各维度评分\n因果链" in text
    assert "10.0\n因果链完整" in text
    assert "张力控制\n\n8.0\n张力有起伏" in text
    assert "总结\n整体吸引力强。" in text

    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_renders_memory_diagnostics_report(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "chapter_002_memory_diagnostics.json"
    path.write_text(
        json.dumps(
            {
                "report_type": "chapter_memory_diagnostics",
                "chapter_number": 2,
                "updated_at": "2026-04-22T10:32:00+00:00",
                "summary": {
                    "available_stages": ["planning", "draft"],
                    "latest_stage": "draft",
                    "history_reused_stages": ["draft"],
                    "context_available_stages": ["planning", "draft"],
                    "counts": {
                        "relevant_history": 4,
                        "previous_chapter_events": 1,
                        "motif_suggestions": 2,
                        "forbidden_repetition": 1,
                    },
                },
                "stages": {
                    "planning": {
                        "requested_layers": [
                            "L0_identity",
                            "L1_core_memory",
                            "L2_on_demand",
                        ],
                        "resolved_layers": [
                            "L0_identity",
                            "L1_core_memory",
                            "L2_on_demand",
                        ],
                        "missing_layers": [],
                        "duration_ms": 38.1,
                        "sources": {
                            "layered_context": "generated",
                            "relevant_history": "fetched",
                            "previous_chapter_events": "fetched",
                            "outline_context": "fetched",
                            "prompt_context": "generated",
                            "motif_continuity": "fetched",
                            "motif_suggestions": "disabled",
                        },
                        "counts": {
                            "relevant_history": 2,
                            "previous_chapter_events": 1,
                            "outline_context_fields": 3,
                        },
                        "flags": {
                            "has_relevant_history": True,
                            "has_previous_chapter_events": True,
                            "has_outline_context": True,
                            "has_critique_context": True,
                            "has_layered_context": True,
                        },
                        "layer_char_counts": {
                            "L0_identity": 48,
                            "L1_core_memory": 162,
                            "L2_on_demand": 97,
                        },
                    },
                    "draft": {
                        "requested_layers": [
                            "L0_identity",
                            "L1_core_memory",
                            "L2_on_demand",
                            "L3_deep_search",
                        ],
                        "resolved_layers": [
                            "L0_identity",
                            "L1_core_memory",
                            "L2_on_demand",
                            "L3_deep_search",
                        ],
                        "missing_layers": [],
                        "duration_ms": 64.9,
                        "history_reused": True,
                        "sources": {
                            "layered_context": "generated",
                            "relevant_history": "prefetched",
                            "previous_chapter_events": "prefetched",
                            "outline_context": "disabled",
                            "prompt_context": "generated",
                            "motif_continuity": "disabled",
                            "motif_suggestions": "fetched",
                        },
                        "counts": {
                            "relevant_history": 2,
                            "previous_chapter_events": 1,
                            "motif_suggestions": 2,
                            "forbidden_repetition": 1,
                        },
                        "flags": {
                            "has_relevant_history": True,
                            "has_previous_chapter_events": True,
                            "has_motif_suggestions": True,
                            "has_prompt_summary": True,
                            "has_forbidden_repetition": True,
                            "has_layered_context": True,
                        },
                        "layer_char_counts": {
                            "L0_identity": 48,
                            "L1_core_memory": 188,
                            "L2_on_demand": 104,
                            "L3_deep_search": 143,
                        },
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "第 2 章" in text
    assert "记忆诊断" in text
    assert "总体概览" in text
    assert "总体命中" in text
    assert "规划阶段" in text
    assert "起草阶段" in text
    assert "已命中层" in text
    assert "L0 项目身份" in text
    assert "L3 深层检索" in text
    assert "数据来源" in text
    assert "相关历史" in text
    assert "附加信号" in text
    assert "批注上下文" in text
    assert "复用历史" in text

    widget.deleteLater()
    qapp.processEvents()


def test_decide_autopilot_refreshes_context_when_prepare_chapter_succeeded_but_snapshot_stale() -> None:
    """回归：全书连跑只跑一章的根因。

    prepare_chapter 任务刚完成（SUCCEEDED），磁盘上已写入 pending_checkpoint，
    但 _studio 快照尚未刷新（pending_checkpoint=None，current_chapter_done=False）。
    此时决策引擎不能再次提交 prepare_chapter（会覆盖刚生成的方案），
    而应返回 refresh_context 等待快照同步。
    """
    snapshot = _build_snapshot(current_status="current")
    # 快照滞后：pending_checkpoint 还不可见
    assert snapshot.pending_checkpoint is None

    job = DesktopJobRecord(
        job_id="job-prepare",
        kind="prepare_chapter",
        label="章节方案 · project-demo / 第3章",
        project_id="project-demo",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 3},
    )

    decision = decide_autopilot_action(AutoPilotContext(
        mode="book_auto",
        auto_started=True,
        auto_pilot_pending=False,
        studio=snapshot,
        latest_job=job,
        last_submitted_checkpoint_id=None,
        current_chapter_done=False,  # 快照滞后，章节尚未显示为已完成
        book_auto_skip_done=True,
        chapter_prepared_this_run=True,
        current_project_id="project-demo",
    ))

    assert decision.action == "refresh_context", (
        f"Expected refresh_context (wait for snapshot sync) but got {decision.action!r} — "
        "this would cause a spurious second prepare_chapter that overwrites the plan "
        "and eventually FAILs, stopping 全书连跑 after the first chapter."
    )


def test_decide_autopilot_stops_after_failed_checkpoint_finalize() -> None:
    snapshot = _build_snapshot(current_status="current")
    snapshot.pending_checkpoint = DecisionCheckpoint(
        checkpoint_id="guard-1",
        checkpoint_type="guard_checkpoint",
        options=[
            DecisionOption(
                option_id="accept",
                label="采用推荐方案",
                is_recommended=True,
            )
        ],
    )
    job = DesktopJobRecord(
        job_id="job-finalize-failed",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档章节 · project-demo / 第3章",
        project_id="project-demo",
        status=DesktopJobState.FAILED,
        result={"chapter_number": 3},
    )

    decision = decide_autopilot_action(
        AutoPilotContext(
            mode="book_auto",
            auto_started=True,
            auto_pilot_pending=False,
            studio=snapshot,
            latest_job=job,
            last_submitted_checkpoint_id=None,
            current_chapter_done=False,
            book_auto_skip_done=True,
            current_project_id="project-demo",
        )
    )

    assert decision.action == "stop"


def _build_inspector_presenter() -> tuple[
    ChapterStudioInspectorPresenter,
    QWidget,
    QLabel,
    list[str],
    QWidget,
]:
    rel_section = QWidget()
    rel_body = QLabel()
    continuity_section = QWidget()
    issues_section_title = QLabel()
    issues_host = QWidget()
    issues_layout = QVBoxLayout(issues_host)
    repair_hint = QLabel()
    causal_section = QWidget()
    causal_section_title = QLabel()
    causal_host = QWidget()
    causal_layout = QVBoxLayout(causal_host)
    scheduled: list[str] = []
    presenter = ChapterStudioInspectorPresenter(
        rel_section=rel_section,
        rel_body=rel_body,
        continuity_section=continuity_section,
        issues_section_title=issues_section_title,
        issues_checklist_layout=issues_layout,
        repair_hint=repair_hint,
        queue_auto_submit_repair=lambda: scheduled.append("repair"),
        causal_section=causal_section,
        causal_section_title=causal_section_title,
        causal_checklist_layout=causal_layout,
    )
    return presenter, continuity_section, issues_section_title, scheduled, issues_host


def _build_action_panel_presenter() -> tuple[
    ChapterStudioActionPanelPresenter,
    QLabel,
    QLabel,
    Badge,
    QLabel,
    QWidget,
    QLabel,
    CollapsibleSection,
    QTextEdit,
    QWidget,
    list[str],
]:
    action_title = QLabel()
    action_summary = QLabel()
    action_badge = Badge("待命")
    job_hint = QLabel()
    ai_suggestion_frame = QWidget()
    ai_suggestion_label = QLabel(ai_suggestion_frame)
    notes_section = CollapsibleSection("备注", expanded=False)
    notes = QTextEdit()
    notes_section.body_layout.addWidget(notes)
    rewrite_strategy_row = QWidget()
    buttons_host = QWidget()
    action_buttons = QVBoxLayout(buttons_host)
    events: list[str] = []
    presenter = ChapterStudioActionPanelPresenter(
        action_title=action_title,
        action_summary=action_summary,
        action_badge=action_badge,
        job_hint=job_hint,
        ai_suggestion_frame=ai_suggestion_frame,
        ai_suggestion_label=ai_suggestion_label,
        notes_section=notes_section,
        notes=notes,
        rewrite_strategy_row=rewrite_strategy_row,
        action_buttons=action_buttons,
        on_submit_prepare=lambda *args, **kwargs: events.append("prepare"),
        on_cancel_running_job=lambda: events.append("cancel"),
        on_stop_auto_pilot=lambda: events.append("stop"),
        on_start_auto_pilot=lambda: events.append("start"),
        on_switch_to_suggest=lambda: events.append("switch"),
        on_handle_checkpoint_option=lambda option: events.append(option.option_id),
        on_resume_from_progress=lambda *args, **kwargs: events.append("resume_progress"),
        on_resume_auto_pilot=lambda: events.append("resume"),
        on_submit_regen_with_continuity_check=lambda: events.append("regen"),
        on_submit_polish_with_continuity_check=lambda: events.append("polish"),
        on_go_to_next_chapter=lambda: events.append("next"),
    )
    return (
        presenter,
        action_title,
        action_summary,
        action_badge,
        job_hint,
        ai_suggestion_frame,
        ai_suggestion_label,
        notes_section,
        notes,
        buttons_host,
        events,
    )


def _action_button_texts(buttons_host: QWidget) -> list[str]:
    return [button.text() for button in buttons_host.findChildren(ActionButton)]


def test_inspector_presenter_defaults_to_high_severity_in_suggest_mode() -> None:
    snapshot = _build_snapshot()
    snapshot.continuity_issues = [
        {"issue_type": "causal_gap", "severity": "high", "summary": "事件因果缺口"},
        {"issue_type": "tone", "severity": "low", "summary": "语气还能再顺一点"},
    ]
    presenter, _section, issues_title, scheduled, _issues_host = _build_inspector_presenter()

    presenter.render_continuity_checklist(
        mode="suggest",
        studio=snapshot,
        jobs=[],
        auto_repair_attempts={},
        max_attempts=2,
    )

    assert presenter.selected_issue_indices() == [0]
    assert issues_title.text() == "连贯性问题（2 条）"
    assert scheduled == []


def test_action_panel_presenter_renders_default_prepare_state() -> None:
    (
        presenter,
        action_title,
        action_summary,
        _action_badge,
        _job_hint,
        _ai_frame,
        _ai_label,
        notes_section,
        _notes,
        buttons_host,
        events,
    ) = _build_action_panel_presenter()

    presenter.render(
        ActionPanelRenderContext(
            mode="manual",
            auto_started=False,
            stopped_from_auto=False,
            latest_job=None,
            studio=None,
            workspace=None,
            current_chapter_done=False,
            notes_expanded=False,
            resume_auto_label="本章自动",
        )
    )

    assert action_title.text() == "准备章节方案"
    assert "载入一个长篇项目后" in action_summary.text()
    assert notes_section.isHidden() is False
    assert _action_button_texts(buttons_host) == ["准备章节方案 →"]
    assert events == []


def test_action_panel_presenter_blocks_plan_retry_for_upstream_source_conflict() -> None:
    snapshot = _build_snapshot()
    failed_job = DesktopJobRecord(
        job_id="job-source-conflict",
        kind="prepare_chapter",
        label="章节方案",
        project_id="project-demo",
        status=DesktopJobState.FAILED,
        error="审查上游的「停职」期限冲突：大纲=[3, 5]日，Plan=[3, 5]日。",
        error_summary={
            "cause_code": "upstream_source_conflict",
            "context": {
                "violation_kind": "upstream_source_conflict",
                "replan_target": "manual",
            },
        },
    )
    (
        presenter,
        action_title,
        action_summary,
        _action_badge,
        _job_hint,
        _ai_frame,
        _ai_label,
        _notes_section,
        _notes,
        buttons_host,
        events,
    ) = _build_action_panel_presenter()

    presenter.render(
        ActionPanelRenderContext(
            mode="book_auto",
            auto_started=False,
            stopped_from_auto=True,
            latest_job=failed_job,
            studio=snapshot,
            workspace=None,
            current_chapter_done=False,
            notes_expanded=False,
            resume_auto_label="章节连跑",
        )
    )

    assert action_title.text() == "上游大纲需修订"
    assert "重做章节 Plan 无法解决" in action_summary.text()
    assert _action_button_texts(buttons_host) == ["请先修订上游大纲"]
    assert events == []


def test_action_panel_presenter_shows_recommended_checkpoint_hint() -> None:
    snapshot = _build_snapshot()
    snapshot.pending_checkpoint = DecisionCheckpoint(
        checkpoint_id="cp-3",
        checkpoint_type="plan_checkpoint",
        summary="请确认本章方案",
        options=[
            DecisionOption(option_id="manual", label="人工修改"),
            DecisionOption(
                option_id="recommended",
                label="采用推荐方案",
                description="优先保留当前冲突线并压缩支线信息。",
                is_recommended=True,
            ),
        ],
    )
    (
        presenter,
        action_title,
        action_summary,
        _action_badge,
        _job_hint,
        ai_frame,
        ai_label,
        notes_section,
        _notes,
        buttons_host,
        _events,
    ) = _build_action_panel_presenter()

    presenter.render(
        ActionPanelRenderContext(
            mode="suggest",
            auto_started=False,
            stopped_from_auto=False,
            latest_job=None,
            studio=snapshot,
            workspace=None,
            current_chapter_done=False,
            notes_expanded=False,
            resume_auto_label="本章自动",
        )
    )

    assert action_title.text() == "章节方案待确认"
    assert action_summary.text() == "请确认本章方案"
    assert ai_frame.isHidden() is False
    assert "优先保留当前冲突线" in ai_label.text()
    assert notes_section.isHidden() is False
    assert _action_button_texts(buttons_host) == ["人工修改", "采用推荐方案 →"]


def test_action_panel_presenter_surfaces_replan_failure_reason_in_checkpoint_state() -> None:
    snapshot = _build_snapshot()
    snapshot.pending_checkpoint = DecisionCheckpoint(
        checkpoint_id="cp-replan",
        checkpoint_type="plan_checkpoint",
        summary="请确认重规划后的方案",
        prompt=(
            "⚠️ 上一轮写作未通过质量校验（上一章关键伏笔未接续），已自动重新规划。第 1/2 次重试。\n"
            "确认方案后将重新生成正文。"
        ),
        options=[
            DecisionOption(
                option_id="write_now",
                label="确认并继续",
                is_recommended=True,
            ),
        ],
    )
    (
        presenter,
        action_title,
        action_summary,
        _action_badge,
        _job_hint,
        _ai_frame,
        _ai_label,
        _notes_section,
        _notes,
        _buttons_host,
        _events,
    ) = _build_action_panel_presenter()

    presenter.render(
        ActionPanelRenderContext(
            mode="manual",
            auto_started=False,
            stopped_from_auto=False,
            latest_job=None,
            studio=snapshot,
            workspace=None,
            current_chapter_done=False,
            notes_expanded=False,
            resume_auto_label="本章自动",
        )
    )

    assert action_title.text() == "章节方案待确认"
    assert "上一轮未通过原因：上一章关键伏笔未接续" in action_summary.text()
    assert "确认方案后将重新生成正文。" in action_summary.text()
    assert action_summary.text().count("上一章关键伏笔未接续") == 1


def test_action_panel_presenter_running_state_shows_replan_trigger_reason() -> None:
    snapshot = _build_snapshot()
    running_job = DesktopJobRecord(
        job_id="job-replan-running",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · project-demo / 第 3 章",
        project_id="project-demo",
        status=DesktopJobState.RUNNING,
        current_step="bridge",
        result={"chapter_number": 3},
        events=[
            DesktopJobEvent(
                at="2026-04-08T10:00:00+00:00",
                step="consistency_replan",
                payload={"chapter": 3},
            )
        ],
    )
    (
        presenter,
        _action_title,
        action_summary,
        _action_badge,
        _job_hint,
        _ai_frame,
        _ai_label,
        _notes_section,
        _notes,
        _buttons_host,
        _events,
    ) = _build_action_panel_presenter()

    presenter.render(
        ActionPanelRenderContext(
            mode="manual",
            auto_started=False,
            stopped_from_auto=False,
            latest_job=running_job,
            studio=snapshot,
            workspace=None,
            current_chapter_done=False,
            notes_expanded=False,
            resume_auto_label="本章自动",
        )
    )

    assert "触发原因：上一轮未通过质量校验，系统正在自动重规划后重试。" in action_summary.text()


def test_action_panel_rebuild_hides_stop_button_before_unparenting(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running auto button must not surface as a macOS top-level window.

    Stop changes auto-pilot state synchronously, which rebuilds this panel
    from the button's own click handler.  ``clear_layout`` must hide the old
    visible child before releasing its parent; otherwise macOS briefly shows
    it as a small native window containing only the stop button.
    """
    from novel_forge.desktop.pages.chapter_studio import action_panel as action_panel_module

    class TrackingActionButton(ActionButton):
        detached_visibility: list[bool] = []

        def setParent(self, parent: QWidget | None) -> None:  # noqa: N802
            if parent is None:
                type(self).detached_visibility.append(self.isVisible())
            super().setParent(parent)

    monkeypatch.setattr(action_panel_module, "ActionButton", TrackingActionButton)
    (
        presenter,
        _action_title,
        _action_summary,
        _action_badge,
        _job_hint,
        _ai_frame,
        _ai_label,
        _notes_section,
        _notes,
        buttons_host,
        _events,
    ) = _build_action_panel_presenter()
    buttons_host.resize(420, 120)
    buttons_host.show()
    qapp.processEvents()

    snapshot = _build_snapshot()
    running_job = DesktopJobRecord(
        job_id="job-book-auto",
        kind="run_chapter",
        label="章节连跑",
        project_id="project-demo",
        status=DesktopJobState.RUNNING,
        current_step="draft",
    )
    presenter.render(
        ActionPanelRenderContext(
            mode="book_auto",
            auto_started=True,
            stopped_from_auto=False,
            latest_job=running_job,
            studio=snapshot,
            workspace=None,
            current_chapter_done=False,
            notes_expanded=False,
            resume_auto_label="章节连跑",
        )
    )
    qapp.processEvents()
    assert _action_button_texts(buttons_host) == ["⏹ 停止章节连跑"]

    # Mimic the synchronous rebuild that occurs after the stop action changes
    # auto-pilot state.  The old button is detached during this render.
    presenter.render(
        ActionPanelRenderContext(
            mode="manual",
            auto_started=False,
            stopped_from_auto=True,
            latest_job=None,
            studio=snapshot,
            workspace=None,
            current_chapter_done=False,
            notes_expanded=False,
            resume_auto_label="章节连跑",
        )
    )

    assert TrackingActionButton.detached_visibility == [False]
    buttons_host.hide()


def test_inspector_presenter_queues_auto_repair_when_auto_mode_can_continue(
    qapp: QApplication,
) -> None:
    snapshot = _build_snapshot()
    snapshot.continuity_issues = [
        {"issue_type": "causal_gap", "severity": "high", "summary": "事件因果缺口"},
    ]
    presenter, _section, _issues_title, scheduled, _issues_host = _build_inspector_presenter()
    job = DesktopJobRecord(
        job_id="job-3",
        kind="run_chapter",
        label="生成正文",
        project_id="project-demo",
        status=DesktopJobState.SUCCEEDED,
        result={"chapter_number": 3},
    )

    presenter.render_continuity_checklist(
        mode="auto",
        studio=snapshot,
        jobs=[job],
        auto_repair_attempts={},
        max_attempts=2,
        ctx=AutoPilotContext(
            mode="auto",
            auto_started=True,
            auto_pilot_pending=False,
            auto_repair_pending=False,
            studio=snapshot,
            latest_job=job,
            last_submitted_checkpoint_id=None,
            current_chapter_done=False,
            book_auto_skip_done=True,
            current_project_id="project-demo",
        ),
    )
    qapp.processEvents()

    assert presenter.selected_issue_indices() == [0]
    assert scheduled == ["repair"]


# ── Task 4.2: stopped_from_auto + pending_checkpoint → no dialog ────────


def test_stopped_from_auto_with_checkpoint_does_not_popup_dialog() -> None:
    """When book_auto/auto just stopped (stopped_from_auto=True, mode=manual),
    a leftover pending_checkpoint must NOT trigger the floating dialog.
    Instead, inline option buttons + resume button should be rendered."""
    snapshot = _build_snapshot()
    snapshot.pending_checkpoint = DecisionCheckpoint(
        checkpoint_id="cp-leftover",
        checkpoint_type="plan_checkpoint",
        summary="章节方案待确认（连跑遗留）",
        options=[
            DecisionOption(
                option_id="accept_plan",
                label="采用方案",
                description="按当前计划继续。",
                is_recommended=True,
            ),
            DecisionOption(option_id="rewrite", label="重新规划"),
        ],
    )

    action_title = QLabel()
    action_summary = QLabel()
    action_badge = Badge("待命")
    job_hint = QLabel()
    ai_suggestion_frame = QWidget()
    ai_suggestion_label = QLabel(ai_suggestion_frame)
    notes_section = CollapsibleSection("备注", expanded=False)
    notes = QTextEdit()
    notes_section.body_layout.addWidget(notes)
    rewrite_strategy_row = QWidget()
    buttons_host = QWidget()
    action_buttons = QVBoxLayout(buttons_host)
    dialog_calls: list[str] = []
    events: list[str] = []

    presenter = ChapterStudioActionPanelPresenter(
        action_title=action_title,
        action_summary=action_summary,
        action_badge=action_badge,
        job_hint=job_hint,
        ai_suggestion_frame=ai_suggestion_frame,
        ai_suggestion_label=ai_suggestion_label,
        notes_section=notes_section,
        notes=notes,
        rewrite_strategy_row=rewrite_strategy_row,
        action_buttons=action_buttons,
        on_submit_prepare=lambda *a, **kw: events.append("prepare"),
        on_cancel_running_job=lambda: events.append("cancel"),
        on_stop_auto_pilot=lambda: events.append("stop"),
        on_start_auto_pilot=lambda: events.append("start"),
        on_switch_to_suggest=lambda: events.append("switch"),
        on_handle_checkpoint_option=lambda opt: events.append(opt.option_id),
        on_resume_from_progress=lambda *a, **kw: events.append("resume_progress"),
        on_resume_auto_pilot=lambda: events.append("resume"),
        on_submit_regen_with_continuity_check=lambda: events.append("regen"),
        on_submit_polish_with_continuity_check=lambda: events.append("polish"),
        on_go_to_next_chapter=lambda: events.append("next"),
        on_show_checkpoint_dialog=lambda cp, txt: dialog_calls.append(cp.checkpoint_id),
        is_checkpoint_dialog_dismissed=lambda cp: False,
    )

    # mode="manual" because _stop_auto_pilot calls _set_mode(MODE_MANUAL)
    presenter.render(
        ActionPanelRenderContext(
            mode="manual",
            auto_started=False,
            stopped_from_auto=True,
            latest_job=None,
            studio=snapshot,
            workspace=None,
            current_chapter_done=False,
            notes_expanded=False,
            resume_auto_label="章节连跑",
        )
    )

    # Dialog must NOT have been called
    assert dialog_calls == [], (
        f"Dialog should not popup, but was called for: {dialog_calls}"
    )
    # Inline buttons must include checkpoint options
    btn_texts = _action_button_texts(buttons_host)
    assert "采用方案" in btn_texts
    assert "重新规划" in btn_texts
    # Resume button must be present (stopped_from_auto=True)
    assert any("继续章节连跑" in t for t in btn_texts)


# ── Task 4.3: _is_checkpoint_worth_dialog filter ───────────────────────


def test_is_checkpoint_worth_dialog_filter_results() -> None:
    """Verify _is_checkpoint_worth_dialog for different checkpoint types."""
    presenter, *_ = _build_action_panel_presenter()
    _filter = presenter._is_checkpoint_worth_dialog

    # 1. plan_checkpoint → always True
    cp_plan = DecisionCheckpoint(
        checkpoint_id="cp-plan",
        checkpoint_type="plan_checkpoint",
        prompt="章节方案确认",
        options=[DecisionOption(option_id="accept", label="采用方案")],
    )
    assert _filter(cp_plan) is True

    # 2. guard_checkpoint with repair option → True
    cp_guard_repair = DecisionCheckpoint(
        checkpoint_id="cp-guard-repair",
        checkpoint_type="guard_checkpoint",
        prompt="连贯性检查",
        options=[
            DecisionOption(option_id="accept", label="采用方案", is_recommended=True),
            DecisionOption(option_id="repair_continuity", label="修复连贯性"),
        ],
    )
    assert _filter(cp_guard_repair) is True

    # 3. guard_checkpoint with plot_guard keywords in prompt → True
    cp_guard_keywords = DecisionCheckpoint(
        checkpoint_id="cp-guard-kw",
        checkpoint_type="guard_checkpoint",
        prompt="plot_guard 检测到严重偏离",
        options=[DecisionOption(option_id="accept", label="采用方案")],
    )
    assert _filter(cp_guard_keywords) is True

    # 4. guard_checkpoint without repair options and no keywords → False (inline only)
    cp_routine = DecisionCheckpoint(
        checkpoint_id="cp-routine",
        checkpoint_type="guard_checkpoint",
        prompt="常规归档检查",
        options=[
            DecisionOption(option_id="accept", label="采用方案", is_recommended=True),
            DecisionOption(option_id="skip", label="跳过"),
        ],
    )
    assert _filter(cp_routine) is False
