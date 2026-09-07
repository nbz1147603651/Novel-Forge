"""Regression tests for long-init artifacts in the projects page."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QWidget,
)

from novel_forge.desktop.pages.chapter_studio.artifacts import ChapterStudioArtifactPresenter
from novel_forge.desktop.pages.document_renderers import CharacterGraphWidget, smart_render_document
from novel_forge.desktop.pages.standalone.projects_page import (
    _BOOK_CONSISTENCY_REPORTS,
    _CHAPTER_REPORT_GROUPS,
    _CHAPTER_REPORT_TYPES,
    _LONG_GLOBAL_CATEGORIES,
    _LONG_GLOBAL_GROUPS,
    _LONG_STANDALONE_TABS,
    ProjectsPage,
)
from novel_forge.desktop.pages.workflow.artifacts import _artifact_mapping_for
from novel_forge.desktop.workspace import (
    DesktopProjectItem,
    DesktopWorkspaceMetrics,
    DesktopWorkspaceSnapshot,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.contracts import ChapterArtifactPreview
from novel_forge.workspace.projects import ChapterSummary, ProjectDetail


@pytest.fixture(autouse=True)
def _ensure_qapplication(qapp: object) -> None:
    """This module creates QWidget instances directly; ensure QApplication exists."""


def _delete_widget(widget: QWidget | None, qapp: QApplication) -> None:
    if widget is None:
        return
    widget.close()
    widget.deleteLater()


def _activate_widget_tree(widget: QWidget) -> None:
    widget.ensurePolished()
    layout = widget.layout()
    if layout is not None:
        layout.activate()
    for child in widget.findChildren(QWidget):
        child.ensurePolished()
        child_layout = child.layout()
        if child_layout is not None:
            child_layout.activate()


def test_projects_page_keeps_long_init_artifacts_grouped_under_existing_themes() -> None:
    categories = {rel_path: label for label, rel_path in _LONG_GLOBAL_CATEGORIES}
    labels = [label for label, _rel_path in _LONG_GLOBAL_CATEGORIES]
    groups = {
        group_label: [label for label, _rel_path in specs]
        for group_label, specs in _LONG_GLOBAL_GROUPS
    }
    standalone_labels = [label for label, _rel_path in _LONG_STANDALONE_TABS]

    assert labels == [
        "故事规格",
        "世界观",
        "角色与实体",
        "要素与风格",
        "叙事蓝图",
        "章节设计矩阵",
        "章节大纲",
    ]
    assert groups == {
        "基础设定": ["故事规格", "世界观", "角色与实体", "要素与风格"],
        "资料检索": ["资料检索报告", "资料分析报告", "大纲资料校准"],
    }
    assert standalone_labels == ["叙事蓝图", "章节设计矩阵", "章节大纲"]
    assert categories["character_bible.json"] == "角色与实体"
    assert categories["plans/blueprint_elements_selection.json"] == "要素与风格"
    assert categories["plans/narrative_blueprint.json"] == "叙事蓝图"
    assert categories["plans/chapter_design_matrix.json"] == "章节设计矩阵"
    assert dict(_BOOK_CONSISTENCY_REPORTS) == {
        "一致性审计": "reports/book_consistency_audit.json",
        "修复报告": "reports/book_consistency_repair_report.json",
    }
    assert "支线矩阵" not in labels
    assert "plans/subplot_execution_matrix.json" not in categories
    assert "style_profile.json" not in categories
    assert "plans/narrative_contract.json" not in categories
    assert "states/init_v2/character_system.json" not in categories
    assert "narrative_state/entity_graph.json" not in categories


def test_knowledge_boundary_report_is_exposed_with_chapter_reports() -> None:
    report_types = dict(_CHAPTER_REPORT_TYPES)
    assert report_types["知识边界"] == ("reports/chapter_{ch}_knowledge_boundary_verification.json")
    grouped_report_labels = {
        label for _group, specs in _CHAPTER_REPORT_GROUPS for label, _template in specs
    }
    assert "知识边界" in grouped_report_labels
    assert [group for group, _specs in _CHAPTER_REPORT_GROUPS] == [
        "质量",
        "结构",
        "表达",
        "创作",
    ]

    run_chapter_mapping = _artifact_mapping_for("run_chapter")
    alignment_artifacts = run_chapter_mapping["alignment"]
    extract_artifacts = run_chapter_mapping["extract_canon"]
    expected = ("知识边界审计", "reports/chapter_{ch}_knowledge_boundary_verification.json")
    assert expected in alignment_artifacts
    assert expected in extract_artifacts


def test_chapter_design_matrix_renders_as_matrix_not_outline(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    path = tmp_path / "chapter_design_matrix.json"
    path.write_text(
        json.dumps(
            {
                "total_chapters": 2,
                "entity_catalog": [{"entity_id": "char_1"}],
                "chapters": [
                    {
                        "chapter_number": 1,
                        "plot_duties": ["锁定开篇结构职责。"],
                        "cast_plan": {
                            "pov_entity_id": "char_1",
                            "required_character_ids": ["char_1"],
                        },
                        "emotional_plan": {"pressure_source": "入局压力。"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    plain = widget.toPlainText()
    assert "章节设计矩阵" in plain
    assert "不是可断点续跑的章节大纲正文" in plain
    assert "全书大纲" not in plain

    _delete_widget(widget, qapp)


def test_outline_with_cast_plan_renders_as_outline_not_matrix(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    """outline.json 的章节 schema 天然包含 cast_plan / emotional_plan 字段，
    必须通过文件名精确路由到 render_outline，而不是命中章节设计矩阵的
    内容检测 fallback。"""
    path = tmp_path / "outline.json"
    path.write_text(
        json.dumps(
            {
                "total_chapters": 2,
                "synopsis": "全书概要：风眼之下的世界观建立。",
                "chapters": [
                    {
                        "chapter_number": 1,
                        "title": "风眼初起",
                        "goal": "建立世界观与人物初始状态。",
                        "pov_character": "沈鹿溪",
                        "expected_word_count": 3000,
                        "beats_summary": [
                            "沈鹿溪抵达风眼小镇",
                            "正式接手有风小筑民宿",
                        ],
                        "cast_plan": {
                            "pov_entity_id": "char_729dba90439a",
                            "required_character_ids": ["char_729dba90439a"],
                            "support_character_ids": ["char_support_1", "char_support_2"],
                        },
                        "emotional_plan": {"pressure_source": "入局压力。"},
                    },
                    {
                        "chapter_number": 2,
                        "title": "风声如织",
                        "goal": "平缓铺陈每日录制仪式。",
                        "pov_character": "沈鹿溪",
                        "expected_word_count": 3000,
                        "beats_summary": ["清晨五点半录制细节落地"],
                        "cast_plan": {
                            "pov_entity_id": "char_729dba90439a",
                            "required_character_ids": ["char_729dba90439a"],
                            "support_character_ids": ["char_support_1", "char_support_2"],
                        },
                        "emotional_plan": {"pressure_source": "低张力。"},
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    plain = widget.toPlainText()
    assert "全书大纲" in plain
    assert "章节设计矩阵" not in plain
    assert "风眼初起" in plain
    assert "风声如织" in plain

    _delete_widget(widget, qapp)


def test_chapter_studio_artifact_fingerprint_tracks_file_updates(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    report_dir = project_dir / "reports"
    report_dir.mkdir(parents=True)
    report_path = report_dir / "chapter_001_eval.json"
    report_path.write_text('{"overall_score": 8.0}', encoding="utf-8")

    artifact = ChapterArtifactPreview(
        artifact_id="chapter_001_eval",
        label="质量评估",
        relative_path="reports/chapter_001_eval.json",
        kind="json",
    )
    workspace = SimpleNamespace(storage_root=tmp_path)
    studio = SimpleNamespace(project_id="demo", chapter_number=1, artifacts=[artifact])

    before, _ = ChapterStudioArtifactPresenter._build_fingerprint(
        workspace=workspace,
        studio=studio,
    )
    report_path.write_text('{"overall_score": 8.0, "summary": "updated"}', encoding="utf-8")
    after, _ = ChapterStudioArtifactPresenter._build_fingerprint(
        workspace=workspace,
        studio=studio,
    )

    assert before != after


def test_projects_page_groups_book_consistency_reports_under_one_outer_tab(
    qapp,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "long_project"
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True)
    (reports_dir / "book_consistency_audit.json").write_text(
        json.dumps({"consistency_score": 8.2, "issues": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (reports_dir / "book_consistency_repair_report.json").write_text(
        json.dumps(
            {"report_type": "book_consistency_repair_report", "task_flow": {}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    page = ProjectsPage()
    page._build_long_viewer(
        project_dir,
        ProjectDetail(project_id="long_project", mode="long", title="测试长篇"),
        ProjectLayout(project_dir),
    )

    outer_tabs = page.findChild(QTabWidget, "projectDocumentTabs")
    assert outer_tabs is not None
    outer_labels = [outer_tabs.tabText(i) for i in range(outer_tabs.count())]
    assert outer_labels == [
        "基础设定",
        "资料检索",
        "叙事蓝图",
        "章节设计矩阵",
        "章节大纲",
        "章节",
        "治理",
        "追踪",
    ]
    assert "全书审修" not in outer_labels
    assert "全书一致性审计" not in outer_labels
    assert "全书修复报告" not in outer_labels

    governance = outer_tabs.widget(outer_labels.index("治理"))
    assert isinstance(governance, QTabWidget)
    governance_labels = [governance.tabText(i) for i in range(governance.count())]
    assert "全书审修" in governance_labels

    grouped = governance.widget(governance_labels.index("全书审修"))
    assert isinstance(grouped, QTabWidget)
    assert [grouped.tabText(i) for i in range(grouped.count())] == [
        "一致性审计",
        "修复报告",
    ]

    page.shutdown()
    _delete_widget(page, qapp)


def test_projects_page_groups_chapter_reports_by_theme(
    qapp,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "long_project"
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True)
    (project_dir / "outline.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {"chapter_number": 1, "title": "入局"},
                    {"chapter_number": 2, "title": "未竟"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for rel_path in [
        "reports/chapter_001_eval.json",
        "reports/chapter_001_alignment.json",
        "reports/chapter_001_expression_repetition.json",
        "reports/chapter_001_humanize.json",
        "reports/chapter_001_creative.json",
    ]:
        path = project_dir / rel_path
        payload = {"summary": rel_path}
        if rel_path.endswith("_humanize.json"):
            payload = {
                "chapter_number": 1,
                "humanize_score": 96,
                "total_hits": 0,
                "critical_hits": 0,
                "pattern_hits": [],
                "hits_by_category": {},
                "warnings": [],
            }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    page = ProjectsPage()
    page._build_long_viewer(
        project_dir,
        ProjectDetail(
            project_id="long_project",
            mode="long",
            title="测试长篇",
            chapters=[ChapterSummary(chapter_number=1, title="入局")],
        ),
        ProjectLayout(project_dir),
    )

    outer_tabs = page.findChild(QTabWidget, "projectDocumentTabs")
    assert outer_tabs is not None
    chapter_list = page.findChild(QListWidget, "chapterList")
    assert chapter_list is not None
    assert chapter_list.count() == 2
    assert "◇" in chapter_list.item(1).text()
    outer_labels = [outer_tabs.tabText(i) for i in range(outer_tabs.count())]
    chapter_tabs = outer_tabs.widget(outer_labels.index("章节"))
    assert isinstance(chapter_tabs, QTabWidget)
    chapter_labels = [chapter_tabs.tabText(i) for i in range(chapter_tabs.count())]
    chapter_tabs.setCurrentIndex(chapter_labels.index("报告"))

    report_groups = page.findChild(QTabWidget, "chapterReportGroupTabs")
    assert report_groups is not None
    assert [report_groups.tabText(i) for i in range(report_groups.count())] == [
        "质量",
        "结构",
        "表达",
        "创作",
    ]

    page.shutdown()
    _delete_widget(page, qapp)


def test_projects_page_falls_back_when_report_renderer_fails(
    qapp,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "long_project"
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True)
    (project_dir / "outline.json").write_text(
        json.dumps(
            {"chapters": [{"chapter_number": 1, "title": "入局"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (reports_dir / "chapter_001_eval.json").write_text(
        json.dumps({"summary": "质量评估"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (reports_dir / "chapter_001_humanize.json").write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "humanize_score": 96,
                "total_hits": 0,
                "critical_hits": 0,
                "pattern_hits": [],
                "hits_by_category": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def _raise_render_error(_path: Path) -> QWidget:
        raise RuntimeError("broken renderer")

    monkeypatch.setattr(
        "novel_forge.desktop.pages.standalone.projects_page.smart_render_document",
        _raise_render_error,
    )

    page = ProjectsPage()
    page._build_long_viewer(
        project_dir,
        ProjectDetail(
            project_id="long_project",
            mode="long",
            title="测试长篇",
            chapters=[ChapterSummary(chapter_number=1, title="入局")],
        ),
        ProjectLayout(project_dir),
    )

    outer_tabs = page.findChild(QTabWidget, "projectDocumentTabs")
    assert outer_tabs is not None
    outer_labels = [outer_tabs.tabText(i) for i in range(outer_tabs.count())]
    chapter_tabs = outer_tabs.widget(outer_labels.index("章节"))
    assert isinstance(chapter_tabs, QTabWidget)
    chapter_labels = [chapter_tabs.tabText(i) for i in range(chapter_tabs.count())]
    chapter_tabs.setCurrentIndex(chapter_labels.index("报告"))

    report_groups = page.findChild(QTabWidget, "chapterReportGroupTabs")
    assert report_groups is not None
    assert [report_groups.tabText(i) for i in range(report_groups.count())] == [
        "质量",
        "表达",
    ]

    page.shutdown()
    _delete_widget(page, qapp)


def test_long_artifact_bundle_primary_tabs_are_named_overview(
    qapp,
    tmp_path: Path,
) -> None:
    character_path = tmp_path / "character_bible.json"
    character_path.write_text(
        json.dumps(
            {"characters": [{"name": "沈念卿", "role": "protagonist"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    editorial_path = tmp_path / "plans" / "editorial_contract.json"
    editorial_path.parent.mkdir(parents=True)
    editorial_path.write_text(json.dumps({"voices": []}, ensure_ascii=False), encoding="utf-8")

    character_widget = smart_render_document(character_path)
    assert isinstance(character_widget, QTabWidget)
    assert character_widget.tabText(0) == "总览"
    assert "角色设定" not in [
        character_widget.tabText(i) for i in range(character_widget.count())
    ]

    element_path = tmp_path / "plans" / "blueprint_elements_selection.json"
    element_path.write_text(
        json.dumps({"required_elements": [], "extension_elements": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    style_path = tmp_path / "style_profile.json"
    style_path.write_text(
        json.dumps({"summary": "清冷克制"}, ensure_ascii=False),
        encoding="utf-8",
    )

    element_widget = smart_render_document(element_path)
    assert isinstance(element_widget, QTabWidget)
    assert element_widget.tabText(0) == "总览"
    assert "要素选择" not in [element_widget.tabText(i) for i in range(element_widget.count())]

    blueprint_path = tmp_path / "plans" / "narrative_blueprint.json"
    blueprint_path.write_text(
        json.dumps({"narrative_phases": [{"phase_name": "入局"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    contract_path = tmp_path / "plans" / "narrative_contract.json"
    contract_path.write_text(json.dumps({"title": "契约"}, ensure_ascii=False), encoding="utf-8")

    blueprint_widget = smart_render_document(blueprint_path)
    assert isinstance(blueprint_widget, QTabWidget)
    blueprint_labels = [blueprint_widget.tabText(i) for i in range(blueprint_widget.count())]
    assert blueprint_labels[0] == "总览"
    assert "蓝图总览" not in blueprint_labels
    assert "蓝图附录" not in blueprint_labels

    _delete_widget(character_widget, qapp)
    _delete_widget(element_widget, qapp)
    _delete_widget(blueprint_widget, qapp)


def test_projects_page_omits_subplot_matrix_from_outer_long_tabs(
    qapp,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "long_project"
    plans_dir = project_dir / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "narrative_blueprint.json").write_text(
        json.dumps(
            {
                "synopsis": "主线概要",
                "narrative_phases": [{"phase_name": "入局", "chapter_start": 1, "chapter_end": 3}],
                "subplot_plan": [
                    {
                        "name": "前世情债",
                        "description": "作为蓝图内的支线管理内容展示",
                        "involved_chapters": [1, 2, 3],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (plans_dir / "subplot_execution_matrix.json").write_text(
        json.dumps({"rows": [{"name": "前世情债"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    page = ProjectsPage()
    page._build_long_viewer(
        project_dir,
        ProjectDetail(project_id="long_project", mode="long", title="测试长篇"),
        ProjectLayout(project_dir),
    )

    outer_tabs = page.findChild(QTabWidget, "projectDocumentTabs")
    assert outer_tabs is not None
    outer_labels = [outer_tabs.tabText(i) for i in range(outer_tabs.count())]
    assert "支线矩阵" not in outer_labels
    assert "蓝图契约" not in outer_labels
    assert "叙事蓝图" in outer_labels
    assert "章节大纲" in outer_labels

    blueprint_widget = outer_tabs.widget(outer_labels.index("叙事蓝图"))
    blueprint_tabs = blueprint_widget
    if not isinstance(blueprint_tabs, QTabWidget):
        blueprint_tabs = blueprint_widget.findChild(QTabWidget, "narrativeBlueprintTabs")
    assert isinstance(blueprint_tabs, QTabWidget)
    inner_labels = [blueprint_tabs.tabText(i) for i in range(blueprint_tabs.count())]
    assert "支线矩阵" in inner_labels

    page.shutdown()
    _delete_widget(page, qapp)


def test_projects_page_restores_final_draft_scroll_after_refresh(
    qapp,
    tmp_path: Path,
) -> None:
    project_id = "long_project"
    project_dir = tmp_path / project_id
    chapters_dir = project_dir / "chapters"
    chapters_dir.mkdir(parents=True)
    (project_dir / "outline.json").write_text(
        json.dumps(
            {
                "chapters": [
                    {
                        "chapter_number": 1,
                        "title": "逆光初遇",
                        "expected_word_count": 4500,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    chapter_text = "\n\n".join(
        f"这是第 {i} 段终稿正文，足够长以便阅读器产生滚动位置。" for i in range(1, 180)
    )
    (chapters_dir / "chapter_001.md").write_text(chapter_text, encoding="utf-8")

    def _snapshot(updated_at: str, word_count: int) -> DesktopWorkspaceSnapshot:
        item = DesktopProjectItem(
            project_id=project_id,
            title="测试长篇",
            mode="long",
            mode_label="长篇",
            status="active",
            status_label="进行中",
            progress_label="1/1",
            progress_percent=100,
            last_updated_label="刚刚",
            headline="测试",
            next_action="继续",
            genre="",
            tone="",
            completed_chapters=1,
            total_chapters=1,
            next_chapter=None,
            has_outline=True,
            has_canon=True,
        )
        detail = ProjectDetail(
            project_id=project_id,
            mode="long",
            title="测试长篇",
            completed_chapters=1,
            latest_chapter=1,
            total_chapters=1,
            has_outline=True,
            has_canon=True,
            updated_at=updated_at,
            chapters=[
                ChapterSummary(
                    chapter_number=1,
                    title="逆光初遇",
                    word_count=word_count,
                    updated_at=updated_at,
                    preview="这是第 1 段终稿正文",
                )
            ],
        )
        return DesktopWorkspaceSnapshot(
            storage_root=tmp_path,
            default_provider="mock",
            overview=None,  # type: ignore[arg-type]
            metrics=DesktopWorkspaceMetrics(
                total_projects=1,
                total_chapters=1,
                total_words=word_count,
                configured_providers=0,
            ),
            providers=[],
            projects=[item],
            featured_project=item,
            details={project_id: detail},
        )

    page = ProjectsPage()
    page.resize(900, 640)
    page.show()
    page.bind_workspace(_snapshot("2026-05-18T01:00:00+00:00", 4500))
    page.load_project(project_id)
    _activate_widget_tree(page)

    outer_tabs = page._outer_tabs
    assert outer_tabs is not None
    chapter_tab = next(
        index for index in range(outer_tabs.count()) if outer_tabs.tabText(index) == "章节"
    )
    outer_tabs.setCurrentIndex(chapter_tab)
    chapter_group = outer_tabs.widget(chapter_tab)
    assert isinstance(chapter_group, QTabWidget)
    chapter_group.setCurrentIndex(
        next(
            index
            for index in range(chapter_group.count())
            if chapter_group.tabText(index) == "正文"
        )
    )
    _activate_widget_tree(page)

    reader = page.findChild(QTextEdit, "chapterProseBrowser")
    assert reader is not None
    revision_widget = page._revision_widgets[0]
    scroll_bar = reader.verticalScrollBar()
    if scroll_bar.maximum() <= 0:
        scroll_bar.setRange(0, 1000)
    scroll_bar.setValue(max(1, scroll_bar.maximum() // 2))
    previous_scroll = scroll_bar.value()

    page.bind_workspace(_snapshot("2026-05-18T01:01:00+00:00", 4501))
    _activate_widget_tree(page)

    restored_reader = page.findChild(QTextEdit, "chapterProseBrowser")
    assert restored_reader is not None
    assert restored_reader is reader
    assert page._revision_widgets[0] is revision_widget
    assert restored_reader.verticalScrollBar().value() == previous_scroll

    (chapters_dir / "chapter_001.md").write_text(
        chapter_text + "\n\n这是新增的一段终稿正文，会改变章节正文指纹。",
        encoding="utf-8",
    )
    page.bind_workspace(_snapshot("2026-05-18T01:02:00+00:00", 4502))
    _activate_widget_tree(page)

    changed_reader = page.findChild(QTextEdit, "chapterProseBrowser")
    assert changed_reader is not None
    assert page._revision_widgets[0] is not revision_widget

    page.shutdown()
    page.deleteLater()


def test_smart_render_document_renders_long_init_v2_artifacts(
    qapp,
    tmp_path: Path,
) -> None:
    cases = {
        "states/init_v2/character_system.json": {
            "roster": [{"name": "沈念卿", "role": "protagonist"}],
            "profiles": [{"name": "沈念卿"}],
            "relationship_edges": [],
            "identity_links": [],
            "audit": [],
        },
        "states/init_v2/character_relationship_matrix.json": {
            "relationship_generation_phase": "final",
            "relationship_matrix": [
                {
                    "character_a": "沈念卿",
                    "character_b": "陆云峥",
                    "relation_type": "romantic_tension",
                    "description": "情感拉扯推动主线选择。",
                    "confidence": 0.92,
                }
            ],
            "relationship_candidate_evidence": [
                {
                    "source": "沈念卿",
                    "target": "陆云峥",
                    "field": "backstory",
                    "snippet": "沈念卿与陆云峥互相试探。",
                }
            ],
        },
        "narrative_state/entity_graph.json": {
            "entities": [{"name": "金镯", "entity_type": "item"}],
            "entity_links": [
                {
                    "source_name": "金镯",
                    "target_name": "前世记忆",
                    "link_type": "symbolic_association",
                }
            ],
        },
        "plans/narrative_contract.json": {
            "title": "契约",
            "world_rules": ["金镯只作为记忆触发物"],
            "plot_threads": [],
        },
        "plans/chapter_contracts.json": {
            "chapter_contracts": [{"chapter_number": 1, "required_events": ["建立主线钩子"]}]
        },
        "plans/creative_director_packet.json": {
            "emotional_engine": ["安全感与失去"],
            "signature_motifs": ["金镯"],
        },
        "plans/narrative_blueprint_fragments.json": {
            "synopsis": "分块蓝图",
            "narrative_phases": [{"phase_name": "入局"}],
            "assembly_mode": "fragmented_plan_outline",
        },
        "narrative_state/story_state_projection.json": {
            "last_chapter": 0,
            "recent_summaries": [],
            "facts_by_path": {},
        },
    }

    for rel_path, payload in cases.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        widget = smart_render_document(path)

        assert isinstance(widget, QTextBrowser), rel_path
        assert widget.toPlainText().strip(), rel_path
        _delete_widget(widget, qapp)


def test_smart_render_document_uses_scan_friendly_init_artifact_renderers(
    qapp,
    tmp_path: Path,
) -> None:
    cases = {
        "reports/init_readiness.json": (
            {
                "allowed": True,
                "required": True,
                "summary": "初始化准入通过，可以进入章节生成。",
                "stages": {
                    "contract_coherence": {
                        "verdict": "accept",
                        "blocked": False,
                        "summary": "契约一致性通过。",
                    }
                },
            },
            ["初始化准入", "阶段门禁", "契约一致性"],
        ),
        "reports/init_editorial_readiness.json": (
            {
                "status": "warning",
                "summary": "编辑契约校验通过但有提醒。",
                "character_voice_count": 2,
                "findings": [{"severity": "medium", "summary": "确认性场景偏多"}],
            },
            ["编辑契约准入", "角色声纹", "确认性场景偏多"],
        ),
        "plans/chapter_contracts.json": (
            {
                "chapter_contracts": [
                    {
                        "chapter_number": 1,
                        "title": "入局",
                        "entry_state_requirements": ["主角已抵达城门"],
                        "required_events": ["发现第一条线索"],
                    }
                ]
            },
            ["章节契约", "第 1 章", "入口状态", "必要事件"],
        ),
        "narrative_state/story_state_projection.json": (
            {
                "last_chapter": 1,
                "accepted_updates": [
                    {
                        "chapter_number": 1,
                        "delta_type": "event",
                        "summary": "主角发现第一条线索",
                        "state_update": {"state_path": "plot.main.1", "value": "线索成立"},
                    }
                ],
                "facts_by_path": {"plot.main.1": "线索成立"},
            },
            ["叙事状态投影", "最近状态更新", "状态事实"],
        ),
        "canon/canon_current.json": (
            {
                "project_id": "测试长篇",
                "current_chapter": 1,
                "active_volume": 1,
                "world_rules": [{"rule_id": "rule_1", "content": "铁律不可破"}],
                "entities": [{"name": "沈念卿", "entity_type": "character"}],
            },
            ["规范状态", "世界规则", "实体清单"],
        ),
    }

    for rel_path, (payload, expected_texts) in cases.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        widget = smart_render_document(path)

        assert isinstance(widget, QTextBrowser), rel_path
        plain_text = widget.toPlainText()
        for expected in expected_texts:
            assert expected in plain_text
        _delete_widget(widget, qapp)


def test_blueprint_fragments_artifact_uses_structured_renderer(qapp, tmp_path: Path) -> None:
    path = tmp_path / "plans" / "narrative_blueprint_fragments.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "synopsis": "以信任崩塌与重建为主线。",
                "volume_mode": True,
                "volumes": [
                    {
                        "volume_num": 1,
                        "title": "脉中玄机",
                        "start_chapter": 1,
                        "end_chapter": 20,
                        "arc_goal": "建立医治关系与双向试探。",
                    }
                ],
                "narrative_phases": [
                    {
                        "phase_name": "试探",
                        "chapter_start": 1,
                        "chapter_end": 12,
                        "description": "双方建立信任边界。",
                        "key_events": ["初诊", "数据质疑"],
                    }
                ],
                "key_turning_points": [
                    {
                        "chapter_number": 12,
                        "title": "日记线索",
                        "description": "偶遇设计露出破绽。",
                    }
                ],
                "character_arcs": [{"character": "沈知微", "arc_summary": "从怀疑到主动校准。"}],
                "subplot_plan": [
                    {
                        "name": "银杏叶 callback",
                        "description": "物证串联情感线。",
                        "involved_chapters": [3, 12, 20],
                        "chapter_events": [{"chapter_number": 12, "event": "物证回收"}],
                    }
                ],
                "suspense_schedule": [
                    {
                        "suspense_id": "算法偶遇",
                        "introduce_chapter": 1,
                        "resolve_chapter": 25,
                        "description": "偶遇是否被设计。",
                    }
                ],
                "ending_strategy": "终局以物证与主动选择并置。",
                "assembly_mode": "fragmented_plan_outline",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    plain_text = widget.toPlainText()
    assert "叙事蓝图分块" in plain_text
    assert "分卷规划" in plain_text
    assert "叙事阶段" in plain_text
    assert "关键转折" in plain_text
    assert "支线计划" in plain_text
    assert "volume_mode" not in plain_text

    _delete_widget(widget, qapp)


def test_narrative_contract_renderer_merges_duplicate_llm_contract(
    qapp,
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plans" / "narrative_contract.json"
    state_path = tmp_path / "narrative_state" / "narrative_contract.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    llm_contract = {
        "title": "契约",
        "world_rules": [
            {
                "rule_id": "WR001",
                "title": "轮回债务清算规则",
                "content": "LLM 细化规则",
                "enforcement": "逐步揭露",
                "forbidden_violations": "禁止提前摊牌",
                "binding_level": "hard",
            }
        ],
        "character_arcs": [
            {
                "char_name": "沈念卿",
                "role": "protagonist",
                "arc_stages": [
                    {
                        "stage_name": "破防",
                        "chapters": "1-10",
                        "narrative_requirement": "从防御到愿意合作",
                    }
                ],
                "forbidden_behavior": "禁止突兀表白",
                "completion_criteria": "接受并肩",
            }
        ],
        "plot_threads": [
            {
                "thread_name": "商战三路夹击与反击",
                "priority": "primary",
                "chapters": "10-42",
                "key_scenes": [{"chapter": 18, "title": "断供危机", "description": "危机爆发"}],
                "resolution": "三路同时瓦解",
            }
        ],
    }
    plan_path.write_text(
        json.dumps(
            {
                "world_rules": ["确定性规则"],
                "themes": ["来得及"],
                "llm_contract": llm_contract,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state_path.write_text(json.dumps(llm_contract, ensure_ascii=False), encoding="utf-8")

    plan_widget = smart_render_document(plan_path)
    llm_widget = smart_render_document(state_path)

    assert isinstance(plan_widget, QTextBrowser)
    plan_text = plan_widget.toPlainText()
    assert "确定性规则" in plan_text
    assert "LLM 细化规则" in plan_text
    assert "不会再单独显示重复的 LLM叙事契约文件" in plan_text

    assert isinstance(llm_widget, QTextBrowser)
    llm_text = llm_widget.toPlainText()
    assert "WR001｜轮回债务清算规则｜hard" in llm_text
    assert "rule_id" not in llm_text
    assert "断供危机" in llm_text

    init_artifacts = _artifact_mapping_for("init_long")["init_narrative_contract"]
    assert ("叙事契约", "plans/narrative_contract.json") in init_artifacts
    assert ("LLM叙事契约", "narrative_state/narrative_contract.json") not in init_artifacts

    _delete_widget(plan_widget, qapp)
    _delete_widget(llm_widget, qapp)


def test_style_profile_renderer_loads_project_plans_element_selection(
    qapp,
    tmp_path: Path,
) -> None:
    style_path = tmp_path / "style_profile.json"
    style_path.write_text(
        json.dumps(
            {
                "summary": "克制、紧张",
                "modules": {"narration": {"voice": "近景"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    element_path = tmp_path / "plans" / "blueprint_elements_selection.json"
    element_path.parent.mkdir(parents=True, exist_ok=True)
    element_path.write_text(
        json.dumps(
            {
                "library_version": "test",
                "required_elements": [{"element_id": "hook", "name": "章节钩子"}],
                "extension_elements": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(style_path)

    assert isinstance(widget, QTabWidget)
    assert widget.count() >= 2
    assert widget.tabText(1) == "要素选择"

    _delete_widget(widget, qapp)


def test_character_bible_renderer_groups_character_system_and_entity_graph(
    qapp,
    tmp_path: Path,
) -> None:
    character_path = tmp_path / "character_bible.json"
    character_path.write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "name": "沈念卿",
                        "role": "protagonist",
                        "relationships": {"顾锦绣": "前世映射"},
                    },
                    {"name": "顾锦绣", "role": "supporting", "relationships": {}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    system_path = tmp_path / "states" / "init_v2" / "character_system.json"
    system_path.parent.mkdir(parents=True, exist_ok=True)
    system_path.write_text(
        json.dumps({"roster": [{"name": "沈念卿"}], "audit": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    matrix_path = tmp_path / "states" / "init_v2" / "character_relationship_matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "relationship_matrix": [
                    {
                        "character_a": "沈念卿",
                        "character_b": "顾锦绣",
                        "relation_type": "identity_link",
                        "description": "前世映射影响当下选择。",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    graph_path = tmp_path / "narrative_state" / "entity_graph.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(
            {
                "entities": [
                    {"entity_id": "char_a", "name": "沈念卿", "entity_type": "character"},
                    {"entity_id": "char_b", "name": "顾锦绣", "entity_type": "character"},
                ],
                "entity_links": [
                    {
                        "source_name": "沈念卿",
                        "target_name": "顾锦绣",
                        "link_type": "reincarnation_of",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(character_path)

    assert isinstance(widget, QTabWidget)
    assert [widget.tabText(i) for i in range(widget.count())] == [
        "总览",
        "角色系统",
        "角色关系矩阵",
        "实体图谱",
    ]

    _delete_widget(widget, qapp)


def test_character_bible_bundle_renders_editorial_voice_contract(
    qapp,
    tmp_path: Path,
) -> None:
    character_path = tmp_path / "character_bible.json"
    character_path.write_text(
        json.dumps(
            {
                "characters": [
                    {"name": "沈念卿", "role": "protagonist", "relationships": {}},
                    {"name": "陆云峥", "role": "deuteragonist", "relationships": {}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    contract_path = tmp_path / "plans" / "editorial_contract.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "project_title": "测试长篇",
                "character_voices": [
                    {
                        "character": "沈念卿",
                        "sentence_profile": "短句多，先问后断。",
                        "explanation_bias": "少解释，用具体物证替代情绪。",
                        "emotion_syntax": "紧张时句子断开。",
                        "signature_moves": ["停顿后反问"],
                        "taboo_patterns": ["长篇自白"],
                        "sample_lines": ["账页不对。"],
                    }
                ],
                "climax_markers": [{"chapter_number": 12, "climax_type": "main"}],
                "theme_policies": ["用选择呈现主题"],
                "symbol_policies": [],
                "scene_resistance_rules": [],
                "editorial_element_directives": [],
                "revision_priorities": ["先分声纹"],
                "expression_channel_budget": {"dialogue_tag": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(character_path)

    assert isinstance(widget, QTabWidget)
    labels = [widget.tabText(i) for i in range(widget.count())]
    assert labels == ["总览", "角色声纹"]
    voice_widget = widget.widget(labels.index("角色声纹"))
    assert isinstance(voice_widget, QTextBrowser)
    plain_text = voice_widget.toPlainText()
    assert "编辑契约" in plain_text
    assert "角色声纹" in plain_text
    assert "沈念卿" in plain_text
    assert "长篇自白" in plain_text
    assert "character_voices" not in plain_text

    _delete_widget(widget, qapp)


def test_entity_graph_and_registry_render_distinct_views(qapp, tmp_path: Path) -> None:
    graph_path = tmp_path / "entity_graph.json"
    registry_path = tmp_path / "entity_registry.json"
    payload = {
        "entities": [
            {
                "entity_id": "char_a",
                "name": "沈念卿",
                "entity_type": "character",
                "source": "init",
            }
        ],
        "entity_links": [
            {
                "source_name": "沈念卿",
                "target_name": "金镯",
                "link_type": "keeper_of",
            }
        ],
    }
    graph_path.write_text(
        json.dumps({"entities": payload["entities"], "entity_links": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    registry_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    graph_widget = smart_render_document(graph_path)
    registry_widget = smart_render_document(registry_path)

    assert isinstance(graph_widget, QTextBrowser)
    assert isinstance(registry_widget, QTextBrowser)
    graph_text = graph_widget.toPlainText()
    registry_text = registry_widget.toPlainText()
    assert "图谱节点" in graph_text
    assert "尚无身份/语义关系边" in graph_text
    assert "实体索引" in registry_text
    assert "实体关系边" not in registry_text

    _delete_widget(graph_widget, qapp)
    _delete_widget(registry_widget, qapp)


def test_character_bible_renderer_coalesces_stage_aliases_and_role_prose(
    qapp,
    tmp_path: Path,
) -> None:
    character_path = tmp_path / "character_bible.json"
    character_path.write_text(
        json.dumps(
            {
                "characters": [
                    {"name": "周芷若", "role": "antagonist", "relationships": {}},
                    {
                        "name": "周芷若（狱中）",
                        "role": "major + 狱中反思阶段",
                        "relationships": {"沈念卿": "收到桂花后开始释然"},
                    },
                    {
                        "name": "前世女工转世",
                        "role": "minor + 面包店店主",
                        "relationships": {},
                    },
                    {
                        "name": "苏曼华（祖母）",
                        "role": "minor + 回忆阶段",
                        "relationships": {},
                    },
                    {"name": "沈念卿", "role": "protagonist", "relationships": {}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(character_path)
    graph = widget.findChild(CharacterGraphWidget)

    assert graph is not None
    node_by_name = {node.name: node for node in graph._nodes}
    assert "周芷若" in node_by_name
    assert "周芷若（狱中）" not in node_by_name
    assert "苏曼华" in node_by_name
    assert "苏曼华（祖母）" not in node_by_name
    assert node_by_name["周芷若"].role == "antagonist"
    assert node_by_name["前世女工转世"].role == "minor"
    graph.resize(600, 450)
    assert not graph.grab().isNull()

    _delete_widget(widget, qapp)


def test_character_bible_renderer_keeps_dual_leads_near_center_and_formats_legacy_roles(
    qapp,
    tmp_path: Path,
) -> None:
    character_path = tmp_path / "character_bible.json"
    character_path.write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "name": "陆砚深",
                        "role": "protagonist",
                        "relationships": {"沈知微": "互相吸引"},
                    },
                    {
                        "name": "沈知微",
                        "role": "protagonist",
                        "relationships": {"陆砚深": "互相吸引"},
                    },
                    {
                        "name": "沈父",
                        "role": "mentioned",
                        "status": "deceased",
                        "time_layer": "memory",
                        "relationships": {},
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(character_path)
    graph = widget.findChild(CharacterGraphWidget)

    assert graph is not None
    graph.resize(600, 450)
    assert not graph.grab().isNull()
    center_names = {node.name for node in graph._center_nodes}
    node_by_name = {node.name: node for node in graph._nodes}
    assert center_names == {"陆砚深", "沈知微"}
    assert node_by_name["沈父"].role == "minor"

    _delete_widget(widget, qapp)


def test_character_graph_focus_and_dynamic_relationship_tones(qapp, tmp_path: Path) -> None:
    character_path = tmp_path / "character_bible.json"
    character_path.write_text(
        json.dumps(
            {
                "characters": [
                    {
                        "name": "沈念卿",
                        "role": "protagonist",
                        "relationships": {
                            "陆云峥": "互相吸引但仍在试探",
                            "周芷若": "公开对立，存在冲突",
                            "顾锦绣": "同盟伙伴，彼此信任",
                        },
                    },
                    {"name": "陆云峥", "role": "deuteragonist", "relationships": {}},
                    {"name": "周芷若", "role": "antagonist", "relationships": {}},
                    {"name": "顾锦绣", "role": "supporting", "relationships": {}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(character_path)
    graph = widget.findChild(CharacterGraphWidget)
    focus_label = widget.findChild(QLabel, "charGraphFocus")

    assert graph is not None
    assert focus_label is not None
    tones = {(edge.src.name, edge.dst.name): edge.tone for edge in graph._edges}
    assert tones[("沈念卿", "陆云峥")] == "bond"
    assert tones[("沈念卿", "周芷若")] == "tension"
    assert tones[("沈念卿", "顾锦绣")] == "ally"

    graph.focus_character("沈念卿")
    assert graph._selected_node is not None
    assert graph._selected_node.name == "沈念卿"
    assert graph._animation_timer.isActive()
    assert "当前聚焦：沈念卿" in focus_label.text()

    graph.clear_focus()
    assert graph._selected_node is None
    assert graph._selected_edge is None
    assert not graph._animation_timer.isActive()

    _delete_widget(widget, qapp)


def test_character_graph_does_not_promote_description_mentions_to_edges(qapp) -> None:
    graph = CharacterGraphWidget(
        [
            {
                "name": "玄昱",
                "role": "protagonist",
                "relationships": {
                    "玄昱母妃": "玄昱母妃被宇文铎构陷而死，玄苍追忆时才揭示真相。"
                },
            },
            {"name": "玄昱母妃", "role": "minor", "relationships": {"玄昱": "生母"}},
            {"name": "宇文铎", "role": "antagonist", "relationships": {}},
            {"name": "玄苍", "role": "supporting", "relationships": {}},
        ]
    )

    edge_pairs = {
        frozenset((edge.src.name, edge.dst.name))
        for edge in graph._edges
        if edge.edge_type == "relationship"
    }

    assert frozenset(("玄昱", "玄昱母妃")) in edge_pairs
    assert frozenset(("玄昱母妃", "宇文铎")) not in edge_pairs
    assert frozenset(("玄昱母妃", "玄苍")) not in edge_pairs

    _delete_widget(graph, qapp)


def test_character_graph_renders_explicit_entity_links_between_visible_characters(qapp) -> None:
    graph = CharacterGraphWidget(
        [
            {"name": "玄昱母妃", "role": "minor", "relationships": {"玄昱": "生母"}},
            {"name": "玄昱", "role": "protagonist", "relationships": {}},
            {"name": "宇文铎", "role": "antagonist", "relationships": {}},
        ],
        entity_graph={
            "entity_links": [
                {
                    "source_name": "玄昱母妃",
                    "target_name": "宇文铎",
                    "link_type": "caused_by",
                    "description": "被宇文铎构陷而死。",
                }
            ]
        },
    )

    edge_types = {(edge.src.name, edge.dst.name, edge.edge_type) for edge in graph._edges}

    assert ("玄昱母妃", "宇文铎", "caused_by") in edge_types

    _delete_widget(graph, qapp)


def test_element_selection_renderer_groups_style_profile(
    qapp,
    tmp_path: Path,
) -> None:
    element_path = tmp_path / "plans" / "blueprint_elements_selection.json"
    element_path.parent.mkdir(parents=True, exist_ok=True)
    element_path.write_text(
        json.dumps(
            {
                "library_version": "test",
                "required_elements": [{"element_id": "hook", "name": "章节钩子"}],
                "extension_elements": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "style_profile.json").write_text(
        json.dumps(
            {
                "summary": "克制、紧张",
                "modules": {"narration": {"voice": "近景"}},
                "reading_power_window_config": {"opening_hook": {"enabled": True, "intensity": 60}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(element_path)

    assert isinstance(widget, QTabWidget)
    assert [widget.tabText(i) for i in range(widget.count())] == [
        "总览",
        "风格规范",
        "追读力窗口",
    ]

    _delete_widget(widget, qapp)


def test_narrative_blueprint_renderer_groups_init_artifacts(
    qapp,
    tmp_path: Path,
) -> None:
    blueprint_path = tmp_path / "plans" / "narrative_blueprint.json"
    blueprint_path.parent.mkdir(parents=True, exist_ok=True)
    blueprint_path.write_text(
        json.dumps(
            {
                "synopsis": "主线概要",
                "narrative_phases": [{"phase_name": "入局", "chapter_start": 1, "chapter_end": 5}],
                "key_turning_points": [{"chapter_number": 3, "description": "真相露出一角"}],
                "character_arcs": [],
                "subplot_plan": [],
                "suspense_schedule": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    extras = {
        "plans/narrative_contract.json": {"world_rules": ["前世记忆不可混作人物关系"]},
        "plans/chapter_contracts.json": {"chapter_contracts": [{"chapter_number": 1}]},
        "plans/creative_director_packet.json": {"emotional_engine": ["安全感与失去"]},
        "plans/narrative_blueprint_fragments.json": {
            "synopsis": "分块蓝图",
            "assembly_mode": "fragmented_plan_outline",
        },
        "narrative_state/story_state_projection.json": {"last_chapter": 0},
    }
    for rel_path, payload in extras.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    widget = smart_render_document(blueprint_path)

    assert isinstance(widget, QTabWidget)
    assert [widget.tabText(i) for i in range(widget.count())] == [
        "总览",
        "叙事契约",
        "章节契约",
        "创作导演包",
        "附录",
    ]
    appendix_tab = widget.widget(4)
    assert isinstance(appendix_tab, QTabWidget)
    assert [appendix_tab.tabText(i) for i in range(appendix_tab.count())] == [
        "蓝图分块",
        "叙事状态",
    ]
    fragment_tab = appendix_tab.widget(0)
    assert isinstance(fragment_tab, QTextBrowser)
    assert "叙事蓝图分块" in fragment_tab.toPlainText()

    _delete_widget(widget, qapp)


def test_smart_render_document_renders_init_coherence_claims_jsonl(
    qapp,
    tmp_path: Path,
) -> None:
    claims_path = tmp_path / "memory" / "init_coherence_claims.jsonl"
    claims_path.parent.mkdir(parents=True, exist_ok=True)
    claims = [
        {
            "claim_id": "claim_state_1",
            "artifact": "blueprint",
            "source_path": "/character_arcs/0",
            "source_field": "milestones",
            "chapter_numbers": [3],
            "subject_ids": ["沈知微"],
            "subject_text": "沈知微",
            "axis": "identity",
            "claim_type": "state",
            "claim_text": "沈知微在第三章已经知道陆深真实身份。",
            "state_after": "知晓陆深身份",
            "evidence": "第三章真相露出一角。",
            "confidence": 0.91,
        },
        {
            "claim_id": "claim_promise_1",
            "artifact": "outline",
            "source_path": "/chapters/5",
            "source_field": "goal",
            "chapter_numbers": [5],
            "subject_text": "前世承诺",
            "axis": "promise",
            "claim_type": "promise",
            "claim_text": "第五章必须兑现前世承诺。",
            "evidence": "大纲目标明确写出兑现。",
            "confidence": 0.82,
        },
    ]
    claims_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in claims) + "\n",
        encoding="utf-8",
    )

    widget = smart_render_document(claims_path)

    assert isinstance(widget, QTextBrowser)
    plain = widget.toPlainText()
    assert "初始化一致性 Claims" in plain
    assert "沈知微" in plain
    assert "前世承诺" in plain
    assert "状态" in plain
    assert "承诺" in plain

    _delete_widget(widget, qapp)


def test_narrative_blueprint_bundle_includes_init_coherence_gate_artifacts(
    qapp,
    tmp_path: Path,
) -> None:
    blueprint_path = tmp_path / "plans" / "narrative_blueprint.json"
    blueprint_path.parent.mkdir(parents=True, exist_ok=True)
    blueprint_path.write_text(
        json.dumps(
            {
                "synopsis": "主线概要",
                "narrative_phases": [{"phase_name": "入局", "chapter_start": 1}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payloads = {
        "reports/init_coherence_profile.json": {
            "genre_tags": ["romance"],
            "narrative_modes": ["dual_timeline"],
            "project_ontology": {
                "state_axes": ["identity"],
                "relationship_axes": ["trust"],
                "payoff_types": ["promise"],
            },
            "conflict_lens": ["身份认知前后不能倒置"],
            "extraction_guidance": ["抽取不可逆揭露"],
            "summary": "用于立项一致性门禁。",
        },
        "memory/init_coherence_claim_ledger.json": {
            "active_claim_ids": ["claim_state_1"],
            "claims_by_id": {"claim_state_1": {"status": "active"}},
            "stages": {"outline_inheritance": {"claim_count": 1, "active_claim_count": 1}},
            "latest_stage": "outline_inheritance",
        },
        "memory/init_coherence_index.json": {
            "structured": {
                "claims_by_id": {"claim_state_1": {}},
                "by_subject_axis": {"shen::identity": ["claim_state_1"]},
                "by_payoff": {},
            },
            "ledger": {"active_claim_count": 1, "latest_stage": "outline_inheritance"},
        },
        "reports/init_conflict_candidates.json": {
            "stages": {
                "outline_inheritance": {
                    "stage": "outline_inheritance",
                    "claims_count": 1,
                    "candidates": [],
                    "summary": "无候选。",
                }
            },
            "latest_stage": "outline_inheritance",
        },
        "reports/init_conflict_adjudication.json": {
            "stages": {
                "outline_inheritance": {
                    "stage": "outline_inheritance",
                    "verdict": "needs_repair",
                    "issues": [
                        {
                            "id": "issue_1",
                            "severity": "high",
                            "description": "身份揭露顺序冲突。",
                            "candidate_ids": ["cand_1"],
                            "repair_scope": [
                                {
                                    "artifact": "outline",
                                    "chapters": [3],
                                    "fields": ["goal"],
                                }
                            ],
                        }
                    ],
                    "repair_scope": [{"artifact": "outline", "chapters": [3], "fields": ["goal"]}],
                    "candidate_count": 1,
                    "summary": "发现 1 个高风险问题。",
                }
            },
            "latest_stage": "outline_inheritance",
        },
        "reports/init_claim_contract_coverage.json": {
            "verdict": "warn",
            "summary": "P1 Claim 未覆盖 1 条。",
            "total_claims": 2,
            "covered_claims": 1,
            "uncovered_claims": 1,
            "uncovered_p0_p1": 1,
            "items": [
                {
                    "claim_id": "claim_state_1",
                    "stage": "outline_inheritance",
                    "artifact": "blueprint",
                    "claim_type": "state",
                    "priority": "P1",
                    "chapter_scope": [3],
                    "coverage_status": "covered",
                    "claim_text": "第三章揭露身份。",
                    "matched_contract_refs": [
                        {
                            "chapter_number": 3,
                            "field": "required_events",
                            "text": "第三章揭露身份",
                        }
                    ],
                    "reason": "Claim 已被目标章节契约字段覆盖。",
                }
            ],
        },
    }
    for rel_path, payload in payloads.items():
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    claims_path = tmp_path / "memory" / "init_coherence_claims.jsonl"
    claims_path.write_text(
        json.dumps(
            {
                "claim_id": "claim_state_1",
                "artifact": "blueprint",
                "source_path": "/key_turning_points/0",
                "claim_type": "state",
                "claim_text": "第三章揭露身份。",
                "evidence": "关键转折写明揭露。",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    widget = smart_render_document(blueprint_path)

    assert isinstance(widget, QTabWidget)
    labels = [widget.tabText(i) for i in range(widget.count())]
    assert labels == [
        "总览",
        "一致性校验",
    ]
    coherence_tab = widget.widget(labels.index("一致性校验"))
    assert isinstance(coherence_tab, QTabWidget)
    coherence_labels = [coherence_tab.tabText(i) for i in range(coherence_tab.count())]
    assert coherence_labels == [
        "一致性画像",
        "一致性 Claims",
        "一致性 Claims 账本",
        "一致性索引",
        "冲突候选",
        "冲突裁决",
        "一致性 Claims 契约覆盖",
    ]
    adjudication_tab = coherence_tab.widget(coherence_labels.index("冲突裁决"))
    assert isinstance(adjudication_tab, QTextBrowser)
    assert "身份揭露顺序冲突" in adjudication_tab.toPlainText()
    coverage_tab = coherence_tab.widget(coherence_labels.index("一致性 Claims 契约覆盖"))
    assert isinstance(coverage_tab, QTextBrowser)
    assert "一致性 Claims 契约覆盖" in coverage_tab.toPlainText()

    _delete_widget(widget, qapp)
