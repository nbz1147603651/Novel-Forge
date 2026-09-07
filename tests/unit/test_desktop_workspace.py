"""Tests for desktop workspace snapshot aggregation."""

from __future__ import annotations

from novel_forge.core.config import Settings, get_settings
from novel_forge.core.project_state import ProjectOperation, ProjectStateMachine
from novel_forge.core.schemas.bible import StoryBible
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceService,
    _project_headline,
    _project_headline_full,
    _truncate_at_clause,
)
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.runtime import RuntimeServices


def test_desktop_workspace_snapshot_builds_ui_friendly_models(
    tmp_storage,
    router,
    builder,
) -> None:
    short_layout = ProjectLayout(tmp_storage.project_dir("short_demo"))
    short_layout.ensure_dirs()
    tmp_storage.save_json(
        short_layout.spec_path,
        StorySpec(
            title="短篇示例",
            theme="一枚旧印章牵出前朝密约",
            genre="historical",
            tone="mysterious",
            length_target=2600,
        ).model_dump(mode="json"),
    )
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
    tmp_storage.save_text(
        long_layout.chapter_path(1),
        "怀表盖弹开的那一刻，沈砚听见了屋里最轻的一声叹息。",
    )
    tmp_storage.save_json(
        long_layout.chapter_meta_path(1),
        {"title": "旧怀表", "word_count": 3210},
    )
    tmp_storage.save_json(
        long_layout.eval_report_path(1),
        EvalReport(overall_score=8.4, passed=True).model_dump(mode="json"),
    )

    runtime = RuntimeServices(
        settings=get_settings(),
        router=router,
        builder=builder,
        storage=tmp_storage,
    )
    snapshot = DesktopWorkspaceService(runtime).build_snapshot()

    assert snapshot.metrics.total_projects == 2
    assert snapshot.metrics.total_words > 0
    assert snapshot.metrics.configured_providers == sum(1 for provider in snapshot.providers if provider.ready)
    assert snapshot.featured_project is not None
    assert any(
        item.project_id == "short_demo" and item.status == "completed"
        for item in snapshot.projects
    )
    assert any(
        item.project_id == "long_demo" and item.next_chapter == 2
        for item in snapshot.projects
    )
    assert any(provider.provider_id == "mock" and provider.ready for provider in snapshot.providers)


def test_desktop_workspace_snapshot_exposes_resumable_init_long_projects(
    tmp_storage,
    router,
    builder,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("resume_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="遗物人生",
            theme="整理遗物的人渐渐拼出逝者的另一生",
            genre="romance",
            tone="suspenseful",
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

    runtime = RuntimeServices(
        settings=get_settings(),
        router=router,
        builder=builder,
        storage=tmp_storage,
    )
    snapshot = DesktopWorkspaceService(runtime).build_snapshot()

    project = next(item for item in snapshot.projects if item.project_id == "resume_demo")
    assert project.status == "planning"
    assert project.status_label == "待续立项"
    assert project.progress_percent > 74
    assert project.init_resume_available is True


def test_desktop_workspace_snapshot_reports_failed_init_readiness_resume(
    tmp_storage,
    router,
    builder,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("readiness_failed_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="准入失败示例",
            theme="契约检查停在最后准入。",
            genre="fantasy",
            tone="suspenseful",
            length_target=20000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=2,
            synopsis="测试准入失败恢复。",
            volume_mode=False,
            chapters=[
                ChapterOutline(
                    chapter_number=num,
                    title=f"第{num}章",
                    goal=f"推进第{num}章剧情",
                    beats_summary=[f"第{num}章节拍"],
                )
                for num in range(1, 3)
            ],
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.reports_dir / "init_readiness.json",
        {
            "allowed": False,
            "summary": "初始化准入未通过，需修复大纲继承问题。",
            "stages": {
                "outline_inheritance": {
                    "verdict": "needs_repair",
                    "blocked": True,
                    "issue_count": 1,
                }
            },
        },
    )
    runtime = RuntimeServices(
        settings=get_settings(),
        router=router,
        builder=builder,
        storage=tmp_storage,
    )

    snapshot = DesktopWorkspaceService(runtime).build_snapshot()

    project = next(item for item in snapshot.projects if item.project_id == "readiness_failed_demo")
    detail = snapshot.details["readiness_failed_demo"]
    assert project.init_resume_available is True
    assert project.progress_percent == 98
    assert detail.init_resume_step == "init_readiness"
    assert detail.init_resume_progress_label == "初始化准入未通过，需修复大纲继承问题。"


def test_desktop_workspace_snapshot_reports_source_artifacts_resume(
    tmp_storage,
    router,
    builder,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("source_artifacts_failed_demo"))
    layout.ensure_dirs()
    tmp_storage.save_json(
        layout.spec_path,
        StorySpec(
            title="源头准入示例",
            theme="合理新增群体需要登记",
            genre="xuanhuan",
            tone="serious",
            length_target=6000,
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.bible_path,
        StoryBible(title="源头准入示例", premise="合理新增群体需要登记").model_dump(
            mode="json"
        ),
    )
    tmp_storage.save_json(
        layout.outline_path,
        StoryOutline(
            total_chapters=2,
            synopsis="源头准入示例",
            volume_mode=False,
            chapters=[
                ChapterOutline(
                    chapter_number=num,
                    title=f"第{num}章",
                    goal=f"推进第{num}章剧情",
                    beats_summary=[f"第{num}章节拍"],
                )
                for num in range(1, 3)
            ],
        ).model_dump(mode="json"),
    )
    tmp_storage.save_json(
        layout.reports_dir / "init_readiness.json",
        {
            "allowed": False,
            "summary": "source_artifacts 准入未通过。",
            "stages": {
                "contract_coherence": {"verdict": "accept", "blocked": False},
                "claim_contract_coverage": {"verdict": "accept", "blocked": False},
                "source_artifacts": {"verdict": "needs_repair", "blocked": True},
            },
            "remaining_issues": [{"stage": "source_artifacts"}],
        },
    )
    runtime = RuntimeServices(
        settings=get_settings(),
        router=router,
        builder=builder,
        storage=tmp_storage,
    )

    snapshot = DesktopWorkspaceService(runtime).build_snapshot()

    detail = snapshot.details["source_artifacts_failed_demo"]
    assert detail.init_resume_available is True
    assert detail.init_resume_step == "init_source_artifacts"
    assert detail.init_resume_step_label == "源头 artifact 准入"
    assert detail.init_resume_progress_label == "source_artifacts 准入未通过。"


def test_desktop_workspace_snapshot_tolerates_profile_alias_adapters(
    tmp_storage,
    builder,
) -> None:
    class _FakeAdapter:
        provider_name = "openai"
        default_model = "gpt-4o-mini"

    router = ModelRouter(
        adapters={
            "openai": _FakeAdapter(),
            "openai:writer": _FakeAdapter(),
        },
        default_provider="openai",
    )
    runtime = RuntimeServices(
        settings=get_settings(),
        router=router,
        builder=builder,
        storage=tmp_storage,
    )

    snapshot = DesktopWorkspaceService(runtime).build_snapshot()

    openai_status = next(provider for provider in snapshot.providers if provider.provider_id == "openai")
    assert openai_status.ready is True
    assert snapshot.metrics.configured_providers == 1


def test_desktop_workspace_snapshot_marks_tencent_as_configured(
    tmp_storage,
    router,
    builder,
) -> None:
    runtime = RuntimeServices(
        settings=Settings(_env_file=None, tencent_api_key="sk-test"),
        router=router,
        builder=builder,
        storage=tmp_storage,
    )

    snapshot = DesktopWorkspaceService(runtime).build_snapshot()

    tencent_status = next(provider for provider in snapshot.providers if provider.provider_id == "tencent")
    assert tencent_status.configured is True
    assert tencent_status.label == "腾讯混元"


def test_desktop_workspace_snapshot_uses_project_state_for_paused_projects(
    tmp_storage,
    router,
    builder,
) -> None:
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
        {"project_id": "paused_demo", "current_chapter": 1, "schema_version": "2.0"},
    )
    tmp_storage.save_text(layout.chapter_path(1), "第一章正文")

    machine = ProjectStateMachine("paused_demo", project_dir=layout.root)
    machine.execute(ProjectOperation.INIT)
    machine.execute(ProjectOperation.COMPLETE_INIT)
    machine.execute(ProjectOperation.START_WRITING)
    machine.execute(ProjectOperation.PAUSE)

    runtime = RuntimeServices(
        settings=get_settings(),
        router=router,
        builder=builder,
        storage=tmp_storage,
    )

    snapshot = DesktopWorkspaceService(runtime).build_snapshot()

    project = next(item for item in snapshot.projects if item.project_id == "paused_demo")
    assert project.status == "paused"
    assert project.status_label == "已暂停"
    assert project.project_state == "paused"
    assert project.allowed_operations == ("archive", "resume")
    assert project.next_action == "恢复并继续第 2 章"


# ─────────────────────────────────────────────────────────────────────────
# 在库卷册卡片智能截断 (Task: 优化卷帙在库卷册硬截断)
# ─────────────────────────────────────────────────────────────────────────


def test_truncate_at_clause_breaks_at_chinese_punctuation() -> None:
    """超过 limit 时，优先在最近的中文标点处截断并追加 …。"""
    text = (
        "沈岸是南方临海老城醒梦事务所的老板，靠已故恋人苏晚留下的神经校准坏表，"
        "以神经同步技术进入访客的三层流速递增梦境心结，一旦坏表校准失效便会沦"
        "为脑死亡的梦傻。"
    )
    out = _truncate_at_clause(text, limit=40)
    # 截断必须在 limit 字符以内（包含 …）
    assert len(out) <= 41  # 40 字符 + … 自身的 1 字节长度偏差可接受
    assert out.endswith("…")
    # 截断点应落在中文标点之后：原句 limit=40 范围末段最近的标点是 “，”，
    # 所以输出必须以「…递增梦境心结，…」之前的标点结尾（这里验证末尾是 …）。
    assert "。" not in out or out.rfind("。") < out.rfind("…")
    # 验证截断后内容包含「…」前的整段语义，而不是被切掉一半的子句。
    assert "," in out or "，" in out


def test_truncate_at_clause_short_text_returns_as_is() -> None:
    """长度 <= limit 时原样返回，不追加 …。"""
    text = "青瓦梦魇。"
    assert _truncate_at_clause(text, limit=80) == text
    assert _truncate_at_clause("", limit=80) == ""
    assert _truncate_at_clause(None, limit=80) == ""  # type: ignore[arg-type]


def test_truncate_at_clause_falls_back_to_char_cut_without_punctuation() -> None:
    """范围内无中文标点时退化为字符级硬截断，保留 …。"""
    text = "abcdefghijklmnopqrstuvwxyz" * 5  # 130 字符，无中文标点
    out = _truncate_at_clause(text, limit=40)
    assert out.endswith("…")
    assert len(out) <= 41
    # 截断部分应来自原文开头
    assert out.rstrip("…") in text


def test_project_headline_caps_at_limit_and_headline_full_keeps_full_text() -> None:
    """_project_headline 返回限长版；_project_headline_full 返回原文。"""
    long_preview = (
        "沈岸是南方临海老城醒梦事务所的老板，靠已故恋人苏晚留下的神经校准坏表，"
        "以神经同步技术进入访客的三层流速递增梦境心结，一旦坏表校准失效便会沦"
        "为脑死亡的梦傻。"
        "另一些补充文本补充上去，让总长度明显超过 80 字符限制以确保函数被执行到。"
        "再多一些背景：他在接连上门的客户梦境深处不断发现当年害死苏晚的墓后势力"
        "残存的神经数据碎片。"
    )

    class _StubDetail:
        def __init__(self, preview: str) -> None:
            self.preview = preview
            self.premise = ""
            self.genre = ""
            self.tone = ""

    detail = _StubDetail(long_preview)
    headline = _project_headline(detail)
    headline_full = _project_headline_full(detail)

    assert len(headline) <= 81  # 80 + 省略号占位符
    assert headline.endswith("…")
    # 完整版必须保留原文，不做任何截断
    assert headline_full == long_preview


def test_desktop_project_item_exposes_headline_full_field() -> None:
    """DesktopProjectItem 必填项之外多了 headline_full 字段，默认值是空串。"""
    item = DesktopProjectItem(
        project_id="demo",
        title="Demo",
        mode="long",
        mode_label="长篇",
        status="writing",
        status_label="创作中",
        progress_label="3 / 12 章",
        progress_percent=25,
        last_updated_label="2026-08-04",
        headline="节选片段…",
        next_action="继续推进第 4 章",
        genre="mystery",
        tone="suspenseful",
        completed_chapters=3,
        total_chapters=12,
        next_chapter=4,
        has_outline=True,
        has_canon=False,
        headline_full="完整 preview 在此保留，未截断。",
    )
    assert item.headline_full == "完整 preview 在此保留，未截断。"

    # dataclass 默认值仍能正确实例化（向后兼容）。
    minimal = DesktopProjectItem(
        project_id="demo",
        title="Demo",
        mode="long",
        mode_label="长篇",
        status="writing",
        status_label="创作中",
        progress_label="3 / 12 章",
        progress_percent=25,
        last_updated_label="2026-08-04",
        headline="节选片段…",
        next_action="继续推进第 4 章",
        genre="mystery",
        tone="suspenseful",
        completed_chapters=3,
        total_chapters=12,
        next_chapter=4,
        has_outline=True,
        has_canon=False,
    )
    assert minimal.headline_full == ""
