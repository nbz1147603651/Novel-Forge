"""Regression coverage for the React project reader's PySide catalog parity."""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.api.routes.ui_views import _build_narrative_visualization, get_project_reader_view
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.projects import ChapterSummary, ProjectDetail


class _ProjectInspector:
    def __init__(self, detail: ProjectDetail) -> None:
        self._detail = detail

    def get_project_detail(self, project_id: str) -> ProjectDetail:
        assert project_id == self._detail.project_id
        return self._detail


async def test_outline_read_model_preserves_beats_and_constraints(tmp_path: Path) -> None:
    from novel_forge.api.routes.ui_views import get_narrative_tools_view

    storage = FileSystemStorage(tmp_path)
    project_id = "outline-reading"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    chapter = {
        "chapter_number": 1,
        "title": "梅雨门槛",
        "goal": "将委托转化为行动。",
        "beats_summary": ["核验门槛。", "保留疑点。"],
        "main_plot_points": ["接下委托。"],
        "subplot_points": ["留存纸鹤。"],
        "scene_design_goals": ["不提前揭示身份。"],
        "notes": "让物证推动选择。",
        "pov_character_name": "沈岸",
        "setting": "事务所",
        "time_anchor": "梅雨清晨",
        "involved_character_names": ["沈岸", "林小满"],
    }
    payload = {"total_chapters": 60, "chapters": [chapter]}
    storage.save_json(layout.outline_path, payload)
    detail = ProjectDetail(project_id=project_id, mode="long", title="眠咒")
    view = await get_narrative_tools_view(project_id, _ProjectInspector(detail), storage)

    node = view["outline"][0]
    assert node["summary"] == "核验门槛。\n\n保留疑点。"
    for key in (
        "goal",
        "beats_summary",
        "main_plot_points",
        "subplot_points",
        "scene_design_goals",
        "notes",
    ):
        assert node[key] == chapter[key]
    assert {"label": "参与角色", "value": "沈岸、林小满"} in node["facts"]
    assert view["visualization"]["milestones"][0]["description"] == node["summary"]
    assert storage.load_json(layout.outline_path) == payload


def test_outline_read_model_tolerates_legacy_prose_and_ignores_unrenderable_values() -> None:
    from novel_forge.api.routes.ui_views import _outline_chapter_sources, _outline_text_items

    assert _outline_text_items("  旧版单段正文  ") == ["旧版单段正文"]
    assert _outline_text_items(["一", {"description": "二"}, None, 3, {}]) == ["一", "二"]
    assert _outline_chapter_sources(
        {"chapters": [{"chapter_number": 2, "title": "雨", "summary": "旧版摘要"}]}
    ) == [(2, "雨", "旧版摘要")]


async def test_long_reader_exposes_the_full_pyside_catalog(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_id = "reader-book"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    storage.save_json(layout.spec_path, {"title": "梦侦探", "premise": "追查被改写的记忆"})
    chapter_text = ("第一段正文推动人物选择。" * 12) + "\n\n第二段正文。"
    storage.save_text(layout.chapter_path(1), chapter_text)
    storage.save_json(
        layout.reports_dir / "chapter_001_eval.json",
        {"summary": "节奏稳定", "score": 8.6},
    )
    storage.save_json(
        layout.reports_dir / "init_coherence_profile.json",
        {"status": "ready"},
    )
    storage.save_json(
        layout.reports_dir / "book_editorial_audit.json",
        {"summary": "出版编辑审查完成", "revision_queue": []},
    )
    storage.save_json(
        layout.root / "logs" / "reader-run" / "summary.json",
        {
            "run_id": "reader-run",
            "metadata": {"kind": "run_chapter", "chapter_number": 1},
            "trace_summary": {
                "total_tokens": 200,
                "total_prompt_tokens": 120,
                "total_completion_tokens": 80,
                "total_cost_usd": 0.02,
                "steps": [
                    {
                        "name": "draft",
                        "tokens": 200,
                        "prompt_tokens": 120,
                        "completion_tokens": 80,
                        "model_call_count": 1,
                        "cost": 0.02,
                    }
                ],
            },
        },
    )
    detail = ProjectDetail(
        project_id=project_id,
        mode="long",
        title="梦侦探",
        chapters=[
            ChapterSummary(chapter_number=1, title="怀表", word_count=1200),
            ChapterSummary(chapter_number=21, title="回声", word_count=1600),
        ],
    )

    view = await get_project_reader_view(project_id, _ProjectInspector(detail), storage)

    assert view["mode_label"] == "长篇项目"
    assert [tab["id"] for tab in view["tabs"]] == [
        "foundation",
        "research",
        "blueprint",
        "chapter_design",
        "outline",
        "chapters",
        "governance",
        "tracking",
    ]
    foundation = view["tabs"][0]
    assert [artifact["id"] for artifact in foundation["artifacts"]] == [
        "spec",
        "world",
        "characters",
        "elements",
    ]
    assert foundation["artifacts"][0]["paragraphs"][0] == "title：梦侦探"

    chapters = next(tab for tab in view["tabs"] if tab["id"] == "chapters")
    assert {artifact["id"] for artifact in chapters["artifacts"]} >= {
        "chapter-1",
        "chapter-1-report-8d28-91cf-8bc4-4f30",
    }
    assert chapters["chapters"] == [
        {
            "number": 1,
            "title": "怀表",
            "state": "final",
            "word_count": count_chapter_words(chapter_text),
        },
        {"number": 21, "title": "回声", "state": "pending"},
    ]
    assert "chapter-21" not in {artifact["id"] for artifact in chapters["artifacts"]}
    governance = next(tab for tab in view["tabs"] if tab["id"] == "governance")
    assert [artifact["label"] for artifact in governance["artifacts"]] == [
        "出版编辑审查",
        "一致性画像",
    ]
    tracking = next(tab for tab in view["tabs"] if tab["id"] == "tracking")
    assert [artifact["id"] for artifact in tracking["artifacts"]] == ["token-analytics"]
    token_analytics = tracking["artifacts"][0]
    assert token_analytics["format"] == "json"
    assert json.loads(token_analytics["content"])["total_tokens"] == 200


async def test_long_reader_lists_outline_chapters_and_drafts_before_archive(
    tmp_path: Path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    project_id = "reader-draft-book"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    storage.save_json(
        layout.outline_path,
        {
            "total_chapters": 2,
            "chapters": [
                {"chapter_number": 1, "title": "晨钟嗅香"},
                {"chapter_number": 2, "title": "铜匙问诊"},
            ],
        },
    )
    draft_text = "晨钟前两刻，黎明未至。"
    storage.save_text(
        layout.chapter_draft_dir(1) / "v_humanize_candidate.md",
        draft_text,
    )
    detail = ProjectDetail(project_id=project_id, mode="long", title="药证")

    view = await get_project_reader_view(project_id, _ProjectInspector(detail), storage)

    chapters = next(tab for tab in view["tabs"] if tab["id"] == "chapters")
    assert chapters["chapters"] == [
        {
            "number": 1,
            "title": "晨钟嗅香",
            "state": "draft",
            "word_count": count_chapter_words(draft_text),
        },
        {"number": 2, "title": "铜匙问诊", "state": "pending"},
    ]
    assert [artifact["id"] for artifact in chapters["artifacts"]] == [
        "chapter-1-draft-v_humanize_candidate"
    ]
    assert chapters["artifacts"][0]["source_label"] == (
        "drafts/chapter_001/v_humanize_candidate.md"
    )
    assert all(artifact["id"] != "chapter-1" for artifact in chapters["artifacts"])


def test_narrative_visualization_preserves_full_node_descriptions(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("narrative-details"))
    turning_point = "许清禾上门求助，沈岸核验她连续七天的噩梦与现实幻觉。"
    subplot_event = "沈岸取走她一段无关记忆，存进旧玻璃罐，作为接案报酬。"
    storage.save_json(
        layout.blueprint_path,
        {
            "total_chapters": 12,
            "key_turning_points": [{"chapter_number": 1, "description": turning_point}],
            "subplot_plan": [
                {
                    "name": "梦境委托线",
                    "chapter_events": [{"chapter_number": 1, "event": subplot_event}],
                }
            ],
        },
    )
    detail = ProjectDetail(project_id="narrative-details", mode="long", title="梦侦探")

    visualization, subplots = _build_narrative_visualization(storage, layout, detail)

    milestone = visualization["milestones"][0]
    event = visualization["subplot_lanes"][0]["events"][0]
    assert milestone["label"] == turning_point[:12]
    assert milestone["description"] == turning_point
    assert event["label"] == subplot_event[:12]
    assert event["description"] == subplot_event
    assert subplots[0]["plan"] == {
        "name": "梦境委托线",
        "description": "",
        "involved_chapters": [],
        "chapter_events": [
            {
                "chapter_number": 1,
                "event": subplot_event,
                "weave_notes": "",
                "depends_on": [],
            }
        ],
        "weave_links": [],
        "priority": "normal",
        "resolution_chapter": 0,
        "resolution_target": "",
        "resolution_type": "",
    }


def test_narrative_visualization_uses_declared_total_and_outline_nodes_when_blueprint_is_sparse(
    tmp_path: Path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("sparse-blueprint"))
    storage.save_json(
        layout.blueprint_path,
        {
            "total_chapters": 12,
            "narrative_phases": [],
            "key_turning_points": [],
        },
    )
    storage.save_json(
        layout.outline_path,
        {
            "total_chapters": 12,
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "坠梦",
                    "beats_summary": "沈岸第一次接到会污染现实的梦境委托。",
                },
                {
                    "chapter_number": 12,
                    "title": "醒来",
                    "beats_summary": "沈岸决定公开梦境污染的证据。",
                },
            ],
        },
    )
    detail = ProjectDetail(project_id="sparse-blueprint", mode="long", title="梦侦探")

    visualization, _ = _build_narrative_visualization(storage, layout, detail)

    assert visualization["total_chapters"] == 12
    assert visualization["milestones"] == [
        {
            "id": "outline-milestone-1",
            "chapter": 1,
            "label": "坠梦",
            "description": "沈岸第一次接到会污染现实的梦境委托。",
        },
        {
            "id": "outline-milestone-12",
            "chapter": 12,
            "label": "醒来",
            "description": "沈岸决定公开梦境污染的证据。",
        },
    ]
