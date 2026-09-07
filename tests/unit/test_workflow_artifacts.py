"""Tests for workflow artifact resolution."""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget, QTextBrowser  # noqa: E402

from novel_forge.desktop.pages.document_renderers import smart_render_document  # noqa: E402
from novel_forge.desktop.pages.workflow.artifacts import (
    StepArtifactDialog,
    _artifact_candidates,
    _build_empty_artifact_hint,
    _resolve_artifacts,
)  # noqa: E402
from novel_forge.pipeline.progress import summary_steps  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_smart_render_document_renders_chapter_repair_plan(
    tmp_path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "chapter_026_repair_plan.json"
    path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "issue_type": "continuity_gap",
                        "severity": "medium",
                        "summary": "承接关系需要补足",
                        "evidence": "上一章留下供应链威胁，本章应继续落点。",
                        "fix_actions": ["补一处人物反应", "保留商务冲突"],
                    }
                ],
                "target_sections": [
                    {
                        "section_type": "opening",
                        "start_paragraph": 1,
                        "end_paragraph": 3,
                        "reason": "开场需要承接上一章。",
                    }
                ],
                "must_keep": ["沈念卿与陆云峥的职场对手格局"],
                "must_change": ["补强上一章威胁的落点"],
                "expected_outcome": "修复后不改变主线，只补足承接。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "修复方案" in text
    assert "待修复问题" in text
    assert "承接关系需要补足" in text
    assert "必须修改" in text

    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_renders_expression_repetition_report(
    tmp_path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "chapter_026_expression_repetition.json"
    path.write_text(
        json.dumps(
            {
                "report_type": "expression_repetition",
                "chapter_number": 26,
                "records": [
                    {
                        "text": "逆光意象连续出现不变换角度",
                        "channel": "sensory_anchor",
                        "channel_id": "light_shadow",
                        "source": "plan",
                        "level": "soft",
                        "cooldown_chapters": 3,
                        "actor_scope": "global",
                        "reason": "plan_chapter forbidden_elements_soft",
                        "examples": [],
                    }
                ],
                "hits": [
                    {
                        "channel": "定义式转折",
                        "summary": "定义式否定转折句过多，造成审美疲劳。",
                        "examples": ["不是窒息，而是涌动。"],
                    }
                ],
                "repair_guidance": [
                    {
                        "issue": "同构句式过密",
                        "action": "减少同构句式，改用动作和场面推进。",
                        "priority": "medium",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "表达通道报告" in text
    assert "命中问题" in text
    assert "定义式转折" in text
    assert "通道限制" in text
    assert "感官锚点" in text
    assert "逆光意象连续出现不变换角度" in text
    assert "减少同构句式" in text
    assert "schema_version" not in text

    widget.deleteLater()
    qapp.processEvents()


def test_expression_repetition_renderer_parses_legacy_guidance_string(
    tmp_path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "chapter_001_expression_repetition.json"
    path.write_text(
        json.dumps(
            {
                "report_type": "expression_repetition",
                "chapter_number": 1,
                "records": [
                    {
                        "text": "心跳漏拍/心跳加速（限额出现三次，本章出现两次）",
                        "channel": "somatic_reaction",
                        "channel_id": "heart_stutter",
                        "level": "quota",
                    }
                ],
                "hits": [],
                "repair_guidance": [
                    "{'issue': '光影意象高频重复', 'action': '改用环境细节或动作链替代', 'priority': 'medium'}"
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "身体反应" in text
    assert "心跳漏拍" in text
    assert "光影意象高频重复" in text
    assert "改用环境细节或动作链替代" in text
    assert "{'issue'" not in text

    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_renders_humanize_report(
    tmp_path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "chapter_001_humanize.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "created_at": "2026-06-02T00:00:00Z",
                "source_text_hash": "abc",
                "chapter_number": 1,
                "total_hits": 1,
                "hits_by_category": {"模板句式": 1},
                "critical_hits": 0,
                "humanize_score": 7.2,
                "summary": "检测到一处模板句式。",
                "pattern_hits": [
                    {
                        "pattern_id": "negative_parallelism",
                        "pattern_name": "否定式并列",
                        "category": "模板句式",
                        "severity": "high",
                        "evidence_quote": "不是因为害怕，而是为了等待",
                        "paragraph_index": 0,
                        "suggestion": "他后退一步，等对方先动。",
                        "confidence": 0.91,
                        "actionable": True,
                        "source": "llm",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "拟人化扫描" in text
    assert "7.2" in text
    assert "否定式并列" in text
    assert "模板句式" in text
    assert "不是因为害怕" in text
    assert "schema_version" not in text

    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_does_not_misclassify_generic_humanize_score(
    tmp_path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "ordinary_report.json"
    path.write_text(
        json.dumps(
            {
                "humanize_score": 8.0,
                "summary": "普通报告里偶然带了同名字段。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "Ordinary Report" in text
    assert "普通报告里偶然带了同名字段" in text
    assert "拟人化扫描" not in text

    widget.deleteLater()
    qapp.processEvents()


def test_reading_power_renderer_flags_zero_dimension_scores(
    tmp_path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "chapter_001_reading_power.json"
    path.write_text(
        json.dumps(
            {
                "chapter": 1,
                "overall_score": 4.3,
                "evaluation_status": "ok",
                "hook_type": "mystery",
                "hook_strength": "medium",
                "prev_hook_fulfilled": True,
                "micro_payoffs": [],
                "information_pacing": "balanced",
                "information_pacing_score": 0.0,
                "main_plot_depth": "moderate",
                "tension_match": "matched",
                "tension_match_score": 0.0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "分数需要复核" in text
    assert "评分维度" in text
    assert "信息节奏" in text
    assert "张力匹配" in text

    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_renders_stage_visibility_report(
    tmp_path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "chapter_057_stage_visibility.json"
    path.write_text(
        json.dumps(
            {
                "report_type": "stage_visibility_diagnostics",
                "chapter_number": 57,
                "stage_visibility_diagnostics": {
                    "bridge": {
                        "stage": "bridge",
                        "visible_current_milestones": 2,
                        "visible_future_guardrails": 1,
                        "withheld_future_count": 0,
                        "policy": "Stage receives bounded milestone window, not full outline.",
                    },
                    "draft": {
                        "stage": "draft",
                        "visible_current_milestones": 2,
                        "visible_future_guardrails": 0,
                        "withheld_future_count": 3,
                        "policy": (
                            "Draft only receives current execution cards; "
                            "future guardrails are withheld."
                        ),
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
    assert "阶段控制" in text
    assert "阶段可见性诊断" in text
    assert "桥接" in text
    assert "起草" in text
    assert "未来隐藏" in text
    assert "草稿阶段只接收当前执行卡" in text
    assert "report_type" not in text

    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_renders_story_bible_core_checkpoint(
    tmp_path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "story_core.checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_name": "story_bible_core",
                "signature": "abc",
                "fragments": {
                    "core": {
                        "story_core": {
                            "title": "山风与归人",
                            "premise": "沈鹿溪回到风季小镇，重新面对被听见这件事。",
                            "tone": "温暖但克制",
                        }
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "山风与归人" in text
    assert "故事前提" in text
    assert "重新面对被听见" in text
    assert "schema_version" not in text
    assert "fragments" not in text

    widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_renders_story_bible_world_checkpoint(
    tmp_path,
    qapp: QApplication,
) -> None:
    path = tmp_path / "story_bible_independent_fragments.checkpoint.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_name": "story_bible_independent_fragments",
                "signature": "abc",
                "fragments": {
                    "world": {
                        "world_rules": {
                            "era": "2020年代中期",
                            "geography": "云南山区小镇",
                            "culture": "熟人社会与短视频流量并存",
                            "magic_or_tech": "手机信号不稳定",
                            "rules": ["风声会放大未说出口的真相。"],
                        }
                    },
                    "themes": {
                        "themes_and_symbols": {
                            "themes": ["被看见与重新发声"],
                            "banned_intent_rules": ["不把沉默写成软弱。"],
                        }
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
    assert "时代背景" in text
    assert "2020年代中期" in text
    assert "世界规则" in text
    assert "风声会放大未说出口的真相" in text
    assert "核心主题" in text
    assert "被看见与重新发声" in text
    assert "schema_version" not in text

    widget.deleteLater()
    qapp.processEvents()


def test_step_artifact_dialog_fits_archive_tabs_by_default(
    tmp_path,
    qapp: QApplication,
) -> None:
    labels = [
        "大纲对齐报告",
        "连贯性报告",
        "因果链报告",
        "追读力报告",
        "质量评估",
        "护栏报告",
        "阶段控制",
        "表达通道",
        "修复方案",
    ]
    artifacts = []
    for index, label in enumerate(labels, 1):
        path = tmp_path / f"artifact_{index:02d}.json"
        path.write_text(json.dumps({"summary": label}, ensure_ascii=False), encoding="utf-8")
        artifacts.append((label, path))

    dialog = StepArtifactDialog("归档前修复", artifacts, project_dir=tmp_path)
    tabs = dialog.findChild(QTabWidget, "artifactTabs")

    assert dialog.width() >= 1200
    assert tabs is not None
    assert tabs.tabBar().usesScrollButtons() is False
    assert tabs.tabBar().elideMode() == tabs.elideMode()

    dialog.deleteLater()
    qapp.processEvents()


def test_quality_check_artifacts_include_eval_report(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    for filename in (
        "chapter_001_alignment.json",
        "chapter_001_continuity.json",
        "chapter_001_eval.json",
    ):
        (reports_dir / filename).write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts(
        "resolve_chapter_checkpoint",
        "alignment",
        tmp_path,
        chapter_number=1,
    )

    assert [label for label, _ in artifacts] == [
        "大纲对齐报告",
        "连贯性报告",
        "质量评估",
    ]


def test_story_bible_step_falls_back_to_split_checkpoints(tmp_path) -> None:
    fragment_dir = tmp_path / "initialization" / "fragments" / "story_bible"
    fragment_dir.mkdir(parents=True)
    for filename in (
        "story_core.checkpoint.json",
        "story_bible_independent_fragments.checkpoint.json",
        "story_bible_continuity.checkpoint.json",
    ):
        (fragment_dir / filename).write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts("init_long", "init_story_bible", tmp_path)

    assert [(label, path.name) for label, path in artifacts] == [
        ("世界观分片：核心前提", "story_core.checkpoint.json"),
        ("世界观分片：世界规则与主题", "story_bible_independent_fragments.checkpoint.json"),
        ("世界观分片：连续性规则", "story_bible_continuity.checkpoint.json"),
    ]


def test_story_bible_step_prefers_final_artifact_over_split_checkpoints(tmp_path) -> None:
    fragment_dir = tmp_path / "initialization" / "fragments" / "story_bible"
    fragment_dir.mkdir(parents=True)
    (fragment_dir / "story_core.checkpoint.json").write_text("{}", encoding="utf-8")
    final_path = tmp_path / "story_bible.json"
    final_path.write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts("init_long", "init_story_bible", tmp_path)

    assert artifacts == [("世界观设定", final_path)]


def test_init_outline_artifacts_include_reveal_guard_manifest(tmp_path) -> None:
    states_dir = tmp_path / "states"
    states_dir.mkdir()
    outline_path = tmp_path / "outline.json"
    manifest_path = states_dir / "artifact_manifest.json"
    session_path = states_dir / "outline_session.json"
    for path in (outline_path, manifest_path, session_path):
        path.write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts("init_long", "plan_outline", tmp_path)

    assert artifacts == [
        ("章节大纲", outline_path),
        ("揭示边界产物清单", manifest_path),
        ("大纲断点", session_path),
    ]


def test_init_chapter_design_matrix_has_separate_artifact_node(tmp_path) -> None:
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()
    matrix_path = plans_dir / "chapter_design_matrix.json"
    matrix_path.write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts("init_long", "plan_chapter_design_matrix", tmp_path)

    assert artifacts == [("章节设计矩阵（结构约束）", matrix_path)]


def test_init_chapter_contract_artifacts_include_reveal_guard_manifest(tmp_path) -> None:
    plans_dir = tmp_path / "plans"
    states_dir = tmp_path / "states"
    plans_dir.mkdir()
    states_dir.mkdir()
    contracts_path = plans_dir / "chapter_contracts.json"
    manifest_path = states_dir / "artifact_manifest.json"
    contracts_path.write_text("{}", encoding="utf-8")
    manifest_path.write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts("init_long", "plan_chapter_contracts", tmp_path)

    assert artifacts == [
        ("章节契约", contracts_path),
        ("揭示边界产物清单", manifest_path),
    ]


def test_init_web_research_artifacts_include_dossier_and_grounding(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    web_path = reports_dir / "init_web_research.json"
    dossier_path = reports_dir / "init_research_dossier.json"
    grounding_path = reports_dir / "outline_research_grounding.json"
    for path in (web_path, dossier_path, grounding_path):
        path.write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts("init_long", "init_web_research", tmp_path)

    assert artifacts == [
        ("资料检索报告", web_path),
        ("资料分析报告", dossier_path),
        ("大纲资料校准", grounding_path),
    ]


def test_resolve_checkpoint_draft_artifacts_prefer_latest_versions(tmp_path) -> None:
    draft_dir = tmp_path / "drafts" / "chapter_001"
    draft_dir.mkdir(parents=True)
    for filename in (
        "v0_draft.md",
        "v1_wave.md",
        "v1_edited.md",
        "v2_edited.md",
        "v_final_review.md",
    ):
        (draft_dir / filename).write_text(filename, encoding="utf-8")

    artifacts = _resolve_artifacts(
        "resolve_chapter_checkpoint",
        "draft",
        tmp_path,
        chapter_number=1,
    )

    assert [(label, path.name) for label, path in artifacts] == [
        ("章节草稿（评审定稿）", "v_final_review.md"),
        ("章节草稿（第2轮润色）", "v2_edited.md"),
        ("章节草稿（第1轮润色）", "v1_edited.md"),
        ("章节草稿（初稿成章）", "v1_wave.md"),
        ("章节草稿（DRAFT 原稿）", "v0_draft.md"),
    ]


def test_guard_checkpoint_artifacts_show_review_draft_before_decision_reports(tmp_path) -> None:
    draft_dir = tmp_path / "drafts" / "chapter_001"
    reports_dir = tmp_path / "reports"
    draft_dir.mkdir(parents=True)
    reports_dir.mkdir()
    for filename in ("v0_draft.md", "v2_edited.md", "v_final_review.md"):
        (draft_dir / filename).write_text(filename, encoding="utf-8")
    for filename in (
        "chapter_001_eval.json",
        "chapter_001_alignment.json",
        "chapter_001_knowledge_boundary_verification.json",
    ):
        (reports_dir / filename).write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts(
        "resolve_chapter_checkpoint",
        "guard_checkpoint",
        tmp_path,
        chapter_number=1,
    )

    assert [(label, path.name) for label, path in artifacts[:3]] == [
        ("章节草稿（评审定稿）", "v_final_review.md"),
        ("章节草稿（第2轮润色）", "v2_edited.md"),
        ("章节草稿（DRAFT 原稿）", "v0_draft.md"),
    ]
    assert [label for label, _ in artifacts[3:]] == [
        "质量评估",
        "大纲对齐报告",
        "知识边界审计",
    ]


def test_post_guard_repair_artifacts_focus_on_repair_targets(tmp_path) -> None:
    draft_dir = tmp_path / "drafts" / "chapter_001"
    reports_dir = tmp_path / "reports"
    draft_dir.mkdir(parents=True)
    reports_dir.mkdir()
    (draft_dir / "v_final_review.md").write_text("post repair", encoding="utf-8")
    for filename in (
        "chapter_001_repair_plan.json",
        "chapter_001_continuity.json",
        "chapter_001_causal.json",
        "chapter_001_reading_power.json",
        "chapter_001_guard.json",
        "chapter_001_stage_visibility.json",
        "chapter_001_expression_repetition.json",
    ):
        (reports_dir / filename).write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts(
        "resolve_chapter_checkpoint_finalize",
        "post_guard_repair",
        tmp_path,
        chapter_number=1,
    )

    assert [label for label, _ in artifacts] == [
        "修复后稿件",
        "修复方案",
        "连贯性报告",
        "因果链报告",
        "追读力报告",
        "护栏报告",
        "阶段控制",
        "表达通道",
    ]


def test_post_guard_repair_artifacts_omit_repaired_repair_plan_dynamically(tmp_path) -> None:
    """When repair_plan.json does not exist (no repair needed), 修复方案 is simply absent."""
    draft_dir = tmp_path / "drafts" / "chapter_001"
    draft_dir.mkdir(parents=True)
    (draft_dir / "v_final_review.md").write_text("post repair", encoding="utf-8")

    artifacts = _resolve_artifacts(
        "resolve_chapter_checkpoint_finalize",
        "post_guard_repair",
        tmp_path,
        chapter_number=1,
    )

    assert [label for label, _ in artifacts] == ["修复后稿件"]


def test_polish_reextract_canon_artifacts_expose_state_outputs_only(tmp_path) -> None:
    chapters_dir = tmp_path / "chapters"
    reports_dir = tmp_path / "reports"
    narrative_dir = tmp_path / "narrative_state"
    evidence_dir = narrative_dir / "evidence"
    memory_dir = tmp_path / "memory"
    for directory in (chapters_dir, reports_dir, narrative_dir, evidence_dir, memory_dir):
        directory.mkdir(parents=True)
    for filename in (
        "chapter_001.md",
        "chapter_001_creative.json",
        "chapter_001_state_adjudication.json",
    ):
        (reports_dir / filename).write_text("{}", encoding="utf-8") if filename.endswith(
            ".json"
        ) else (chapters_dir / filename).write_text("# chapter", encoding="utf-8")
    (narrative_dir / "chapter_001_state_adjudication.json").write_text("{}", encoding="utf-8")
    (narrative_dir / "adjudication_report_index.json").write_text("{}", encoding="utf-8")
    (evidence_dir / "chapter_001_evidence.json").write_text("{}", encoding="utf-8")
    (narrative_dir / "state_ledger.jsonl").write_text("{}", encoding="utf-8")
    (narrative_dir / "story_state_projection.json").write_text("{}", encoding="utf-8")
    (narrative_dir / "pending_queue.json").write_text("{}", encoding="utf-8")
    (memory_dir / "narrative_state_index.json").write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts(
        "resolve_chapter_checkpoint_finalize",
        "polish_reextract_canon",
        tmp_path,
        chapter_number=1,
    )

    assert [label for label, _ in artifacts] == [
        "最终章节",
        "创作报告",
        "状态裁判报告",
        "状态裁判副本",
        "状态裁判索引",
        "章节证据快照",
        "叙事状态账本",
        "叙事状态投影",
        "待定叙事队列",
        "叙事记忆索引",
    ]


def test_persist_artifacts_are_distinct_from_state_extraction(tmp_path) -> None:
    chapters_dir = tmp_path / "chapters"
    reports_dir = tmp_path / "reports"
    canon_dir = tmp_path / "canon"
    for directory in (chapters_dir, reports_dir, canon_dir):
        directory.mkdir(parents=True)
    (chapters_dir / "chapter_001.md").write_text("# chapter", encoding="utf-8")
    (reports_dir / "chapter_001_creative.json").write_text("{}", encoding="utf-8")
    (canon_dir / "canon_current.json").write_text("{}", encoding="utf-8")
    (reports_dir / "chapter_001_state_adjudication.json").write_text("{}", encoding="utf-8")
    (reports_dir / "chapter_001_alignment.json").write_text("{}", encoding="utf-8")
    (reports_dir / "chapter_001_continuity.json").write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts(
        "resolve_chapter_checkpoint_finalize",
        "persist",
        tmp_path,
        chapter_number=1,
    )

    labels = [label for label, _ in artifacts]
    assert labels == ["最终章节", "创作报告", "规范状态"]
    for forbidden in (
        "状态裁判报告",
        "状态裁判副本",
        "状态裁判索引",
        "章节证据快照",
        "叙事状态账本",
        "叙事状态投影",
        "待定叙事队列",
        "叙事记忆索引",
        "大纲对齐报告",
        "连贯性报告",
        "因果链报告",
        "追读力报告",
        "知识边界审计",
        "护栏报告",
        "阶段控制",
        "表达通道",
        "质量评估",
    ):
        assert forbidden not in labels, f"persist 不应再展示 {forbidden}"


def test_persist_artifacts_omit_missing_canon_state(tmp_path) -> None:
    chapters_dir = tmp_path / "chapters"
    reports_dir = tmp_path / "reports"
    chapters_dir.mkdir(parents=True)
    reports_dir.mkdir(parents=True)
    (chapters_dir / "chapter_001.md").write_text("# chapter", encoding="utf-8")
    (reports_dir / "chapter_001_creative.json").write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts(
        "resolve_chapter_checkpoint_finalize",
        "persist",
        tmp_path,
        chapter_number=1,
    )

    assert [label for label, _ in artifacts] == ["最终章节", "创作报告"]


def test_book_consistency_summary_nodes_resolve_artifacts(tmp_path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    audit_path = reports_dir / "book_consistency_audit.json"
    repair_path = reports_dir / "book_consistency_repair_report.json"
    audit_path.write_text("{}", encoding="utf-8")
    repair_path.write_text("{}", encoding="utf-8")

    verify_artifacts = _resolve_artifacts(
        "book_consistency",
        "book_consistency_verify_",
        tmp_path,
    )
    review_artifacts = _resolve_artifacts(
        "book_consistency",
        "book_consistency_repair_review",
        tmp_path,
    )

    assert verify_artifacts == [("全书一致性审计", audit_path)]
    assert review_artifacts == [
        ("全书修复报告", repair_path),
        ("全书一致性审计", audit_path),
    ]


def test_prepare_chapter_plan_resolves_the_same_artifacts_as_nimo(tmp_path) -> None:
    plans_dir = tmp_path / "plans"
    reports_dir = tmp_path / "reports"
    plans_dir.mkdir()
    reports_dir.mkdir()
    plan_path = plans_dir / "chapter_007_plan.json"
    scene_plan_path = plans_dir / "chapter_007_scene_plan.json"
    validation_path = reports_dir / "chapter_007_scene_plan_validation.json"
    for path in (plan_path, scene_plan_path, validation_path):
        path.write_text("{}", encoding="utf-8")

    assert _resolve_artifacts("prepare_chapter", "plan", tmp_path, chapter_number=7) == [
        ("章节计划", plan_path),
        ("场景计划", scene_plan_path),
        ("场景计划验证", validation_path),
    ]


def test_all_visible_workflow_steps_have_artifact_candidates(tmp_path) -> None:
    missing: list[str] = []
    for kind in (
        "run_short",
        "init_long",
        "run_chapter",
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "polish_chapter",
        "repair_continuity",
        "repair_causal",
        "repair_issues",
        "reevaluate_chapter",
        "book_consistency",
        "export_book",
        "reextract_relationships",
        "repair_motif_history",
    ):
        for step in summary_steps(kind):
            candidates = _artifact_candidates(kind, step.key, tmp_path, chapter_number=1)
            if not candidates:
                missing.append(f"{kind}:{step.key}")

    assert missing == []


def test_finalize_memory_step_resolves_memory_artifacts(tmp_path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    project_memory = memory_dir / "project_memory.json"
    project_memory.write_text("{}", encoding="utf-8")

    artifacts = _resolve_artifacts(
        "resolve_chapter_checkpoint_finalize",
        "memory_updated",
        tmp_path,
        chapter_number=1,
    )

    assert artifacts == [("项目记忆索引", project_memory)]


def test_export_step_resolves_exported_files(tmp_path) -> None:
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    exported = export_dir / "demo.md"
    exported.write_text("# demo", encoding="utf-8")

    artifacts = _resolve_artifacts("export_book", "export", tmp_path)

    assert artifacts == [("导出文件", exported)]


def test_profile_style_empty_hint_uses_history_failure_reason(tmp_path) -> None:
    states_dir = tmp_path / "states"
    states_dir.mkdir(parents=True)
    history_payload = [
        {
            "kind": "init_long",
            "events": [
                {
                    "step": "profile_style_failed",
                    "payload": {"error": "gateway timeout"},
                }
            ],
        }
    ]
    (states_dir / "task_flow_history.json").write_text(
        json.dumps(history_payload, ensure_ascii=False),
        encoding="utf-8",
    )

    hint = _build_empty_artifact_hint("init_long", "profile_style", tmp_path)

    assert hint is not None
    assert "style_profile.json" in hint
    assert "gateway timeout" in hint


def test_profile_style_empty_hint_uses_latest_log_api_error(tmp_path) -> None:
    log_dir = tmp_path / "logs" / "20260421-000000_desktop-init-long_demo"
    log_dir.mkdir(parents=True)
    errors_line = {
        "event": "api_call_error",
        "data": {
            "task": "profile_style",
            "error": {
                "provider_error_message": "your current token plan not support model",
            },
        },
    }
    (log_dir / "errors.jsonl").write_text(
        json.dumps(errors_line, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    hint = _build_empty_artifact_hint("init_long", "profile_style", tmp_path)

    assert hint is not None
    assert "模型调用失败" in hint
    assert "not support model" in hint


def test_profile_style_empty_hint_reads_structure_branch_error(tmp_path) -> None:
    log_dir = tmp_path / "logs" / "20260421-000000_desktop-init-long_demo"
    log_dir.mkdir(parents=True)
    lines = [
        {
            "event": "api_call_error",
            "data": {
                "task": "profile_structure",
                "error": {
                    "provider_error_message": "structure route denied",
                },
            },
        }
    ]
    (log_dir / "errors.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in lines) + "\n",
        encoding="utf-8",
    )

    hint = _build_empty_artifact_hint("init_long", "profile_style", tmp_path)

    assert hint is not None
    assert "结构分支" in hint
    assert "structure route denied" in hint


def test_non_profile_style_empty_hint_returns_none(tmp_path) -> None:
    assert _build_empty_artifact_hint("init_long", "plan_blueprint", tmp_path) is None
