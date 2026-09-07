"""Unit tests for document report renderers."""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTextBrowser

from novel_forge.desktop.pages.document_renderer_reports import (
    generic_key_label,
    render_creative_report,
    render_eval_report,
    render_eval_report_body_html,
    render_humanize_report,
    render_knowledge_boundary_report,
    render_state_adjudication_index,
    render_state_adjudication_report,
    render_state_evidence_snapshot,
    render_state_ledger,
    render_state_pending_queue,
    render_story_bible,
)
from novel_forge.desktop.pages.document_renderers import smart_render_document


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_generic_key_label_names_reveal_guard_manifest_fields() -> None:
    assert generic_key_label("artifact_manifest") == "产物清单"
    assert generic_key_label("input_hashes") == "输入指纹"
    assert generic_key_label("output_hashes") == "输出指纹"
    assert generic_key_label("reveal_guard_version") == "揭示边界版本"
    assert generic_key_label("editorial_revelation_ladder") == "编辑揭示阶梯"


def test_render_eval_report_accepts_numeric_strings(qapp: QApplication) -> None:
    widget = render_eval_report(
        {
            "overall_score": "9.7",
            "passed": True,
            "threshold": "6.0",
            "scores": [
                {
                    "dimension": "causal_chain",
                    "score": "10",
                    "comment": "A-E均满足，因果链完整。",
                }
            ],
            "summary": "整体完成度高。",
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "质量评估报告" in text
    assert "9.7" in text
    assert "阈值 6.0" in text
    assert "因果链" in text
    assert "10.0" in text
    assert "A-E均满足，因果链完整。" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_eval_report_body_html_omits_document_wrapper() -> None:
    body = render_eval_report_body_html(
        {
            "overall_score": 8.4,
            "passed": True,
            "scores": [{"dimension": "continuity", "score": 8, "comment": "衔接稳定。"}],
            "summary": "整体可用。",
        }
    )

    assert "<html" not in body
    assert "质量评估报告" not in body
    assert "各维度评分" in body
    assert "衔接稳定" in body


def test_render_eval_report_handles_mixed_score_items(qapp: QApplication) -> None:
    widget = render_eval_report(
        {
            "overall_score": 8.2,
            "passed": True,
            "scores": [
                "原始提示文本",
                {
                    "dimension": "scene_transition",
                    "score": 12,
                    "comment": "转场描述充足。",
                },
            ],
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "质量评估报告" in text
    assert "原始提示文本" in text
    assert "scene transition" in text
    # score should be clamped to [0, 10]
    assert "10.0" in text
    # default threshold fallback
    assert "阈值 6.0" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_creative_report_labels_new_characters_as_candidates(
    qapp: QApplication,
) -> None:
    widget = render_creative_report(
        {
            "new_characters": [
                {
                    "name": "黄门侍郎",
                    "canonical_name": "黄门侍郎",
                    "role_in_story": "supporting",
                    "importance": "supporting",
                    "confidence": 0.82,
                    "should_add_to_bible": True,
                    "evidence": ["传递礼部消息"],
                },
                {
                    "name": "周芷若（狱中）",
                    "canonical_name": "周芷若",
                    "matched_existing_name": "周芷若",
                    "importance": "major",
                    "should_add_to_bible": False,
                },
            ]
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "新增角色候选" in text
    assert "建档候选" in text
    assert "已有角色别名" in text
    assert "匹配已有角色：周芷若" in text
    assert "证据：传递礼部消息" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_humanize_report_shows_score_hits_and_hides_metadata(
    qapp: QApplication,
) -> None:
    widget = render_humanize_report(
        {
            "schema_version": "2.0",
            "created_at": "2026-06-02T00:00:00Z",
            "source_text_hash": "abc123",
            "chapter_number": 3,
            "total_hits": 2,
            "hits_by_category": {"元语言": 1, "聊天残留": 1},
            "critical_hits": 1,
            "humanize_score": 6.8,
            "summary": "检测到两处 AI 痕迹。",
            "pattern_hits": [
                {
                    "pattern_id": "filler_phrases",
                    "pattern_name": "填充短语",
                    "category": "元语言",
                    "severity": "high",
                    "evidence_quote": "值得注意的是",
                    "paragraph_index": 1,
                    "suggestion": "删除该提示词",
                    "confidence": 0.95,
                    "actionable": True,
                    "source": "llm",
                },
                {
                    "pattern_id": "collaborative_artifact",
                    "pattern_name": "协作对话残留",
                    "category": "聊天残留",
                    "severity": "critical",
                    "evidence_quote": "如果你想让我",
                    "paragraph_index": 0,
                    "suggestion": "",
                    "confidence": 0.97,
                    "actionable": False,
                    "source": "llm",
                },
            ],
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "拟人化扫描" in text
    assert "6.8" in text
    assert "总命中" in text
    assert "严重命中" in text
    assert "元语言" in text
    assert "协作对话残留" in text
    assert "填充短语" in text
    assert "值得注意的是" in text
    assert "删除该提示词" in text
    assert "schema_version" not in text
    assert "created_at" not in text
    assert "abc123" not in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_humanize_report_empty_hits_has_clear_empty_state(
    qapp: QApplication,
) -> None:
    widget = render_humanize_report(
        {
            "chapter_number": 1,
            "total_hits": 0,
            "hits_by_category": {},
            "critical_hits": 0,
            "humanize_score": 9.5,
            "pattern_hits": [],
            "summary": "文本自然。",
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "未发现明显 AI 痕迹" in text
    assert "文本自然" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_state_adjudication_report_shows_decision_brief(qapp: QApplication) -> None:
    widget = render_state_adjudication_report(
        {
            "chapter_number": 3,
            "candidates": [{"candidate_id": "c1", "delta_type": "event"}],
            "decisions": [
                {
                    "candidate_id": "c1",
                    "verdict": "accept",
                    "severity": "low",
                    "confidence": 0.95,
                    "rationale": "证据充分。",
                    "evidence_quotes": ["你听——"],
                    "affected_state_paths": ["contract.required_events.3.01"],
                }
            ],
            "final_adjudication": {
                "verdict": "accept",
                "severity": "low",
                "confidence": 0.95,
                "accepted_candidate_ids": ["c1"],
                "rejected_candidate_ids": [],
                "pending_candidate_ids": [],
                "repair_candidate_ids": [],
                "should_block_archive": False,
                "summary": "归档裁定通过。",
                "state_updates": [
                    {
                        "scope": "contract_progression",
                        "state_path": "contract.required_events.3.01",
                        "summary": "林正引出穿堂风层次",
                        "value": "你听——",
                    }
                ],
            },
            "contract_coverage": {
                "total_required_targets": 2,
                "covered_count": 1,
                "all_required_covered": False,
                "uncovered_targets": [{"target_id": "required_events.3.02"}],
            },
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "状态裁判报告" in text
    assert "归档裁定通过" in text
    assert "写入状态" in text
    assert "候选裁决" in text
    assert "契约覆盖" in text
    assert "你听——" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_state_artifacts_are_rich_views(qapp: QApplication) -> None:
    index_widget = render_state_adjudication_index(
        {
            "reports": [
                {
                    "chapter_number": 3,
                    "final_verdict": "needs_repair",
                    "final_severity": "high",
                    "final_confidence": 0.9,
                    "accepted_count": 7,
                    "pending_count": 1,
                    "repair_count": 2,
                    "should_block_archive": True,
                    "contract_covered_count": 11,
                    "contract_total_required_targets": 14,
                }
            ]
        }
    )
    evidence_widget = render_state_evidence_snapshot(
        {
            "chapter_number": 3,
            "contract_coverage": {"total_required_targets": 14, "covered_count": 11},
            "evidence": [
                {
                    "candidate_id": "c3_001",
                    "delta_type": "event",
                    "summary": "林正告知风季更盛。",
                    "adjudicated": True,
                    "evidence": [
                        {
                            "quote": "风季比往年长",
                            "chapter_number": 3,
                            "paragraph_index": 64,
                            "found": True,
                            "context": "他把烟从嘴里拿下来。",
                        }
                    ],
                }
            ],
        }
    )
    pending_widget = render_state_pending_queue(
        {
            "pending_items": [
                {
                    "pending_id": "p1",
                    "chapter_number": 3,
                    "severity": "medium",
                    "pending_reason": "证据窗口被截断。",
                    "repair_instruction": "补充完整风道动作。",
                }
            ]
        }
    )
    ledger_widget = render_state_ledger(
        [
            {
                "entry_id": "l1",
                "chapter_number": 3,
                "delta_type": "event",
                "summary": "林正告知风季更盛",
                "state_update": {
                    "state_path": "contract.required_events.3.03",
                    "value": "风季将更盛",
                },
                "decision": {"verdict": "accept", "rationale": "证据逐字支持。"},
                "evidence": [{"quote": "还会更盛", "found": True}],
            }
        ]
    )

    for widget in (index_widget, evidence_widget, pending_widget, ledger_widget):
        assert isinstance(widget, QTextBrowser)

    assert "章节裁判索引" in index_widget.toPlainText()
    assert "需修复" in index_widget.toPlainText()
    assert "章节证据快照" in evidence_widget.toPlainText()
    assert "风季比往年长" in evidence_widget.toPlainText()
    assert "待定叙事队列" in pending_widget.toPlainText()
    assert "补充完整风道动作" in pending_widget.toPlainText()
    assert "叙事状态账本" in ledger_widget.toPlainText()
    assert "风季将更盛" in ledger_widget.toPlainText()

    for widget in (index_widget, evidence_widget, pending_widget, ledger_widget):
        widget.deleteLater()
    qapp.processEvents()


def test_smart_render_document_routes_state_ledger_jsonl(tmp_path, qapp: QApplication) -> None:
    path = tmp_path / "state_ledger.jsonl"
    path.write_text(
        json.dumps(
            {
                "entry_id": "l1",
                "chapter_number": 3,
                "delta_type": "event",
                "summary": "林正告知风季更盛",
                "state_update": {"state_path": "contract.required_events.3.03"},
                "decision": {"verdict": "accept"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    widget = smart_render_document(path)

    assert isinstance(widget, QTextBrowser)
    assert "叙事状态账本" in widget.toPlainText()
    assert "林正告知风季更盛" in widget.toPlainText()

    widget.deleteLater()
    qapp.processEvents()


def test_render_knowledge_boundary_report_pass_no_candidates(qapp: QApplication) -> None:
    widget = render_knowledge_boundary_report(
        {
            "chapter": 2,
            "stage": "review_finalize",
            "verdict": "pass",
            "hidden_candidate_count": 0,
            "prescreen_hit_count": 0,
            "prescreen_hits": [],
            "findings": [],
            "issues": [],
            "repair_tickets": [],
            "fallback_used": False,
            "source_text_hash": "a" * 64,
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "通过" in text
    assert "隐藏候选 0" in text
    assert "预筛命中 0" in text
    assert "审计发现 0" in text
    assert "本次审计未发现异常" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_knowledge_boundary_report_pass_with_candidates(qapp: QApplication) -> None:
    widget = render_knowledge_boundary_report(
        {
            "chapter": 3,
            "stage": "review_finalize",
            "verdict": "pass",
            "hidden_candidate_count": 203,
            "prescreen_hit_count": 0,
            "prescreen_hits": [],
            "findings": [],
            "issues": [],
            "repair_tickets": [],
            "fallback_used": False,
            "source_text_hash": "abc123" + "0" * 58,
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "通过" in text
    assert "203" in text
    assert "本次审计未发现异常" not in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_knowledge_boundary_report_fail_with_findings(qapp: QApplication) -> None:
    widget = render_knowledge_boundary_report(
        {
            "chapter": 4,
            "stage": "review_finalize",
            "verdict": "leak",
            "hidden_candidate_count": 50,
            "prescreen_hit_count": 1,
            "prescreen_hits": [],
            "findings": [
                {
                    "issue_type": "leak",
                    "severity": "high",
                    "confidence": 0.92,
                    "summary": "X 提前获知了 Y",
                    "evidence_quote": "他想起了那个秘密",
                    "paragraph_start": 3,
                    "paragraph_end": 3,
                    "repair_goal": "补足可见获知证据",
                    "metadata": {
                        "entry_id": "kb_001",
                        "decision": "leak",
                        "fact_fingerprint": "fp_abc",
                    },
                }
            ],
            "issues": [],
            "repair_tickets": [],
            "fallback_used": False,
            "source_text_hash": "b" * 64,
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "知识泄露" in text
    assert "高" in text
    assert "X 提前获知了 Y" in text
    assert "他想起了那个秘密" in text
    assert "第 3 段" in text
    assert "补足可见获知证据" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_knowledge_boundary_report_fallback_used(qapp: QApplication) -> None:
    widget = render_knowledge_boundary_report(
        {
            "chapter": 5,
            "stage": "review_finalize",
            "verdict": "pass",
            "hidden_candidate_count": 10,
            "prescreen_hit_count": 0,
            "prescreen_hits": [],
            "findings": [],
            "issues": [],
            "repair_tickets": [],
            "fallback_used": True,
            "source_text_hash": "c" * 64,
        }
    )

    assert isinstance(widget, QTextBrowser)
    text = widget.toPlainText()
    assert "回退方案" in text

    widget.deleteLater()
    qapp.processEvents()


# ---------------------------------------------------------------------------
# render_story_bible: structured world_rule_book rendering
#
# commit ec88814c introduced a structured ``world_rule_book`` (10-14 rules
# with category/severity/trigger_conditions/forbidden_behavior/etc.) as the
# authoritative source of truth. The legacy ``rules`` string list is only a
# display summary. The UI must render the structured rule book so authors can
# review the executable rules, not just the summary excerpts.
# ---------------------------------------------------------------------------


def _sample_world_rule_book() -> dict[str, object]:
    """Mirror the structure produced by INIT_STORY_WORLD_RULES."""
    return {
        "description": "《测试》世界规则体系涵盖梦境神经机制与偏门职业伦理。",
        "rules": [
            {
                "rule_id": "NEURO_001",
                "content": "神经同步失效即沦为梦傀：怀表倒转三圈触发校准失效。",
                "category": "ability_tech",
                "severity": "hard",
                "always_on": True,
                "applicability_tags": ["梦境入侵", "神经同步"],
                "trigger_conditions": "怀表倒转三圈；计时指针停止超过180秒。",
                "allowed_behavior": "保持怀表运转；感知倒转信号后立即撤离。",
                "forbidden_behavior": "在怀表倒转后继续深入梦境；用手工计时替代校准。",
                "cost_or_consequence": "意识永久困于潜意识；肉体脑死亡。",
                "exceptions": "备用神经锚点可在三十秒内提供临时校准。",
            },
            {
                "rule_id": "POSTAL_002",
                "content": "阴邮差递送记忆碎片必须在凌晨三点到四点间进行。",
                "category": "time_space",
                "severity": "soft",
                "always_on": False,
                "applicability_tags": ["阴邮差", "记忆递送"],
                "trigger_conditions": "递送跨时间记忆碎片。",
                "allowed_behavior": "在凌晨三点至四点间递送。",
                "forbidden_behavior": "在窗口期外递送；递送时开口说话。",
                "cost_or_consequence": "记忆碎片扩散到周边人海马体。",
                "exceptions": "",
            },
        ],
    }


def test_render_story_bible_shows_structured_world_rule_book(
    qapp: QApplication,
) -> None:
    """The structured world_rule_book must render its rules with full detail.

    Before this fix, ``render_story_bible`` only rendered the legacy ``rules``
    string-summary list and silently dropped the structured
    ``world_rule_book`` (category, severity, trigger_conditions,
    forbidden_behavior, cost_or_consequence, exceptions).
    """
    data = {
        "title": "测试世界",
        "premise": "测试前提。",
        "era": "现代南方临海老城。",
        "world_rule_book": _sample_world_rule_book(),
        "rules": ["神经同步失效即沦为梦傀"],  # legacy summary
    }

    widget = render_story_bible(data)
    text = widget.toPlainText()

    # The structured rule book section must appear.
    assert "世界规则账本" in text or "结构化世界规则" in text
    # Each rule's content must be visible.
    assert "神经同步失效即沦为梦傀" in text
    assert "阴邮差递送记忆碎片" in text
    # Structured fields must be visible (not just the content string).
    assert "ability_tech" in text or "能力/技术" in text
    assert "hard" in text or "硬约束" in text
    assert "soft" in text or "软约束" in text
    assert "怀表倒转三圈" in text  # trigger_conditions
    assert "脑死亡" in text  # cost_or_consequence
    # The rule book description should appear.
    assert "梦境神经机制" in text

    widget.deleteLater()
    qapp.processEvents()


def test_render_story_bible_without_world_rule_book_still_works(
    qapp: QApplication,
) -> None:
    """Projects initialized before ec88814c have no world_rule_book; must not crash."""
    data = {
        "title": "旧项目",
        "premise": "旧前提。",
        "rules": ["旧规则一", "旧规则二"],
    }

    widget = render_story_bible(data)
    text = widget.toPlainText()

    assert "旧前提" in text
    assert "旧规则一" in text
    assert "旧规则二" in text

    widget.deleteLater()
    qapp.processEvents()
