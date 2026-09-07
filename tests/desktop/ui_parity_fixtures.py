"""Deterministic desktop fixtures shared by UI-parity capture tools.

These helpers deliberately live in the test layer.  They freeze the visible
workspace without changing the Python writing pipeline or filesystem format.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication


def visual_profiles_config() -> object:
    """Return the single deterministic provider profile used by screenshots."""

    from novel_forge.gateway.profiles import ModelProfile, ProfilesConfig

    profile = ModelProfile(
        profile_id="openai:gpt-4o-mini",
        display_name="OpenAI Mini",
        provider="openai",
        model_id="gpt-4o-mini",
        api_key="sk-test",
    )
    return ProfilesConfig(profiles=[profile], default_profile_id=profile.profile_id)


def visual_character_bible_payload() -> dict[str, object]:
    """Return the canonical three-character fixture for parity captures.

    The full-window Projects reader and the focused character-profile capture
    must exercise the same source-owned roster and relationship topology.
    Keeping it in the shared visual-fixture module prevents two screenshots
    from drifting into separate made-up character graphs.
    """

    return {
        "characters": [
            {
                "name": "林逐",
                "role": "protagonist",
                "age": "28",
                "gender": "女",
                "status": "active",
                "time_layer": "default",
                "social_status": "持证记忆回收师，习惯以流程隔离情感。",
                "abilities": "记忆采集、证据链核验、授权记录比对。",
                "appearance": "沉稳寡言，随身保留纸质笔记。",
                "personality": "恪守规范，核心欲望是查清姐姐失踪真相。",
                "backstory": "十年前姐姐参与神经同步实验后失踪。",
                "arc": "从保留证据到主动决定是否公开记忆。",
                "relationships": {"周砚": "同盟 · 不互信", "林澈": "亲属 · 未完成告别"},
            },
            {
                "name": "周砚",
                "role": "supporting",
                "age": "32",
                "gender": "男",
                "status": "active",
                "time_layer": "default",
                "social_status": "调查官，掌握授权链漏洞的旧案材料。",
                "abilities": "调查取证、授权链追踪、跨部门协调。",
                "personality": "克制、警觉，习惯先验证再表态。",
                "backstory": "早期公开证据曾让证人遭遇风险。",
                "arc": "从审视林逐到共同承担公开的代价。",
                "relationships": {"林澈": "隐秘 · 调查线"},
            },
            {
                "name": "林澈",
                "role": "supporting",
                "age": "30",
                "gender": "女",
                "status": "retired",
                "time_layer": "past",
                "social_status": "神经同步实验研究员。",
                "abilities": "神经同步研究、实验数据解读。",
                "personality": "理性而有保护欲。",
                "backstory": "失踪前留下未经登记的记忆残片与语音线索。",
                "arc": "她留下的选择持续影响当下。",
                "relationships": {},
            },
        ]
    }


def build_visual_workspace_snapshot(storage_root: Path) -> object:
    """Build the same populated workspace used by existing desktop visuals."""

    from novel_forge.desktop.workspace import (
        DesktopProjectItem,
        DesktopWorkspaceMetrics,
        DesktopWorkspaceSnapshot,
        ProviderStatus,
    )
    from novel_forge.workspace.projects import ProjectDetail, WorkspaceOverview

    short_detail = ProjectDetail(
        project_id="test-short",
        mode="short",
        title="测试短篇",
        genre="mystery",
        tone="suspenseful",
        premise="一个关于遗物整理的短篇故事",
        preview="",
        chapters=[],
        artifact_counts={"chapters": 1, "spec": 1},
        recent_files=["chapters/short_story.md", "spec.json"],
        outline_generated_count=0,
        total_chapters=0,
    )
    long_detail = ProjectDetail(
        project_id="test-long",
        mode="long",
        title="测试长篇",
        genre="scifi",
        tone="dark",
        premise="记忆回收师的长篇故事",
        preview="",
        chapters=[],
        artifact_counts={"outline": 1, "canon": 1},
        recent_files=["outline.json", "canon/canon_current.json"],
        outline_generated_count=1,
        total_chapters=24,
    )
    return DesktopWorkspaceSnapshot(
        storage_root=storage_root,
        default_provider="openai",
        overview=WorkspaceOverview(
            storage_root=str(storage_root),
            total_projects=2,
            short_projects=1,
            long_projects=1,
            total_generated_chapters=3,
            providers=["openai"],
            default_provider="openai",
        ),
        metrics=DesktopWorkspaceMetrics(
            total_projects=2,
            total_chapters=3,
            total_words=15000,
            configured_providers=1,
        ),
        providers=[
            ProviderStatus(
                provider_id="openai",
                label="OpenAI",
                detail="UI parity fixture",
                is_default=True,
                ready=True,
                configured=True,
            ),
        ],
        projects=[
            DesktopProjectItem(
                project_id="test-short",
                title="测试短篇",
                mode="short",
                mode_label="短篇",
                status="completed",
                status_label="已完稿",
                progress_label="短篇完稿",
                progress_percent=100,
                next_chapter=None,
                last_updated_label="2026-04-30",
                headline="一个关于遗物整理的短篇故事",
                next_action="查看作品",
                genre="mystery",
                tone="suspenseful",
                completed_chapters=1,
                total_chapters=None,
                has_outline=False,
                has_canon=False,
            ),
            DesktopProjectItem(
                project_id="test-long",
                title="测试长篇",
                mode="long",
                mode_label="长篇",
                status="writing",
                status_label="连载中",
                progress_label="第 4 章完成",
                progress_percent=45,
                next_chapter=5,
                last_updated_label="2026-04-29",
                headline="记忆回收师的长篇故事",
                next_action="续写第 5 章",
                genre="scifi",
                tone="dark",
                completed_chapters=4,
                total_chapters=24,
                has_outline=True,
                has_canon=True,
            ),
        ],
        featured_project=None,
        details={"test-short": short_detail, "test-long": long_detail},
    )


def build_visual_chapter_workspace_snapshot() -> object:
    """Build the populated Chapter Studio state used by source goldens.

    The desktop shell fixture deliberately stops at workspace metadata.  The
    chapter page has a second, read-only projection with its own chapter rail,
    carry-forward context and next-step suggestion.  Keeping it here makes
    that visible state explicit without touching the writing runtime.
    """

    from novel_forge.workspace.contracts import ChapterWorkspaceChapter, ChapterWorkspaceSnapshot

    return ChapterWorkspaceSnapshot(
        project_id="test-long",
        project_title="测试长篇",
        chapter_number=5,
        total_chapters=24,
        genre="科幻悬疑",
        tone="冷峻克制",
        project_summary="记忆回收师循着被篡改的时间戳，追查一桩被掩埋的失踪案。",
        chapters=[
            ChapterWorkspaceChapter(
                chapter_number=1,
                title="雨夜来客",
                status="done",
                status_label="已归档",
                word_count=3526,
                overall_score=8.7,
            ),
            ChapterWorkspaceChapter(
                chapter_number=2,
                title="旧桥回声",
                status="done",
                status_label="已归档",
                word_count=3712,
                overall_score=8.6,
            ),
            ChapterWorkspaceChapter(
                chapter_number=3,
                title="失真的录音",
                status="done",
                status_label="已归档",
                word_count=3589,
                overall_score=8.8,
            ),
            ChapterWorkspaceChapter(
                chapter_number=4,
                title="高架桥下",
                status="done",
                status_label="已归档",
                word_count=3921,
                overall_score=8.5,
            ),
            ChapterWorkspaceChapter(
                chapter_number=5,
                title="档案室",
                status="current",
                status_label="待创作",
            ),
            ChapterWorkspaceChapter(
                chapter_number=6,
                title="错误的授权",
                status="pending",
                status_label="待排队",
            ),
        ],
        previous_title="高架桥下",
        previous_summary="沈岸从废弃高架桥下取回一枚被擦除编号的存储芯片，发现其中的时间戳与苏晚失踪当天完全重合。",
        previous_exit_summary="芯片上的授权链指向旧城区档案室，门禁记录却显示沈岸从未拥有访问权限。",
        current_title="档案室",
        current_goal="让沈岸在档案室取得可验证的授权链证据，同时暴露一个足以改变调查方向的代价。",
        current_outline_summary="沈岸借用临时权限进入档案室，发现记录被分层加密；他必须在保安到达前决定是否调用苏晚留下的非法记忆片段。",
        next_title="错误的授权",
        next_goal="追查伪造授权的来源，并让主角承担动用记忆片段的后果。",
        carry_forward=[
            "存储芯片的时间戳与苏晚失踪日重合，不能将其解释为偶然。",
            "沈岸尚未说明自己为何熟悉档案室的旧门禁协议。",
            "林小满承诺在外部监控保安动向，不能在本章中失联。",
        ],
        suggestions_for_next_chapter="让证据先改变人物的行动选择，再揭露其解释；避免把授权链写成无代价的线索投放。",
        alignment_score=8.7,
        overall_score=8.6,
        continuity_score=9.0,
        causal_score=8.4,
        reading_power_score=8.5,
        warnings=["档案室场景需保留门禁失效的因果链。"],
    )


def build_visual_chapter_running_workspace_snapshot() -> object:
    """Return the same chapter while a fixed preparation job is in progress.

    The runtime job itself stays in the capture tool because it is a desktop
    presentation concern.  This projection only freezes the source data that
    changes while generation is active, keeping it free of pipeline calls and
    project files.
    """

    snapshot = build_visual_chapter_workspace_snapshot()
    return snapshot.model_copy(
        update={
            "alignment_score": None,
            "overall_score": None,
            "continuity_score": None,
            "causal_score": None,
            "reading_power_score": None,
            "has_review_progress": True,
            "review_progress_stage": "draft",
        }
    )


def build_visual_chapter_checkpoint_workspace_snapshot() -> object:
    """Return a deterministic plan checkpoint without starting the pipeline."""

    from novel_forge.workspace.contracts import DecisionCheckpoint, DecisionOption

    snapshot = build_visual_chapter_workspace_snapshot()
    chapters = [
        chapter.model_copy(update={"status": "needs_decision", "status_label": "方案待确认"})
        if chapter.chapter_number == snapshot.chapter_number
        else chapter
        for chapter in snapshot.chapters
    ]
    checkpoint = DecisionCheckpoint(
        checkpoint_id="fixture-plan-checkpoint",
        checkpoint_type="plan_checkpoint",
        summary="本章的开场证据、人物边界与章节落点已经汇总。请选择继续路径。",
        prompt="需要人工确认后才能继续执行本章草稿。",
        options=[
            DecisionOption(
                option_id="adopt",
                label="采用当前方案",
                description="保留时间戳、授权链和林小满在场的既定边界。",
                is_recommended=True,
                semantic_tag="accept_and_archive",
            ),
            DecisionOption(
                option_id="regenerate",
                label="带备注重新规划",
                description="基于人工补充约束重新生成章节计划。",
                semantic_tag="regenerate_plan",
            ),
        ],
        related_artifacts=["chapter_plan", "bridge"],
    )
    return snapshot.model_copy(update={"chapters": chapters, "pending_checkpoint": checkpoint})


@contextmanager
def deterministic_desktop_visual_runtime() -> Iterator[QApplication]:
    """Freeze non-deterministic desktop behavior while a source image is made."""

    import novel_forge.desktop.pages.settings.page as settings_page
    import novel_forge.desktop.pages.standalone.dashboard_page as dashboard_page
    from novel_forge.desktop import constants as desktop_constants
    from novel_forge.desktop.motion import Motion

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    original_stylesheet = app.styleSheet()
    original_theme_id = app.property("_novel_forge_desktop_theme")

    class _InstantSignal:
        def connect(self, callback):  # noqa: ANN001, ANN202
            callback()

    class _InstantAnimation:
        finished = _InstantSignal()

        def stop(self) -> None:
            return

    def _instant_fade(widget, **_kwargs):  # noqa: ANN001, ANN202
        effect = widget.graphicsEffect()
        if effect is not None and hasattr(effect, "setOpacity"):
            effect.setOpacity(1.0)
        return _InstantAnimation()

    def _instant_scale(widget, *, end_value: float = 1.0, **_kwargs):  # noqa: ANN001, ANN202
        if hasattr(widget, "_scale"):
            widget._scale = end_value
        widget.update()
        return _InstantAnimation()

    def _load_profiles(_settings: object | None = None) -> object:
        return visual_profiles_config()

    with ExitStack() as stack:
        stack.enter_context(patch.object(dashboard_page, "load_or_import_profiles", _load_profiles))
        stack.enter_context(patch.object(settings_page, "load_or_import_profiles", _load_profiles))
        stack.enter_context(patch.object(desktop_constants, "ANIMATIONS_ENABLED", False))
        stack.enter_context(patch.object(Motion, "fade_in", staticmethod(_instant_fade)))
        stack.enter_context(patch.object(Motion, "scale", staticmethod(_instant_scale)))
        try:
            yield app
        finally:
            # ``apply_desktop_theme`` fast-paths an unchanged application
            # property.  Restore that property together with QSS so repeated
            # source captures cannot inherit a theme id whose stylesheet was
            # intentionally cleared by the previous fixture.
            app.setProperty("_novel_forge_desktop_theme", original_theme_id)
            app.setStyleSheet(original_stylesheet)
