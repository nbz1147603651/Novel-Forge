"""Regression tests for unified memory panel widgets."""

from __future__ import annotations

import os

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from novel_forge.desktop.components.memory_components import (
    MemoryMotifCard,
    MemoryRepetitionWarning,
    MemorySuggestionCard,
    UnifiedMemoryPanel,
)
from novel_forge.desktop.components.primitives import ActionButton, Surface
from novel_forge.desktop.pages.chapter_studio.renderers import (
    _format_context_blob,
    _format_context_items,
)
from novel_forge.desktop.theme import get_stylesheet

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_update_motifs_renders_suggestions_and_warnings(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()

    panel.update_motifs(
        motifs=[
            {
                "motif_id": "m1",
                "category": "意象",
                "description": "怀表反复出现",
                "occurrence_count": 3,
                "last_chapter": 4,
            }
        ],
        suggestions=[
            {
                "motif_id": "m1",
                "suggestion": "在章末回调怀表声响",
                "priority": "high",
                "motif_name": "旧怀表",
            }
        ],
        warnings=[
            {
                "motif_id": "m1",
                "message": "怀表描写频率偏高",
                "severity": "medium",
            }
        ],
    )

    assert len(panel.findChildren(MemorySuggestionCard)) == 1
    assert len(panel.findChildren(MemoryRepetitionWarning)) == 1


def test_chapter_context_items_hide_internal_payload_fields() -> None:
    formatted = _format_context_items(
        [
            "{'schema_version': '2.0', 'created_at': '2026-06-27T10:47:04Z', "
            "'text': '沈鹿溪每日清晨录入分钟的风声剔已形成隐性约定', "
            "'status': 'open', 'deferred_to_chapter': 0}",
            "{'text': '被陈叔公拒绝拍摄祭祀的事实', 'status': 'open'}",
        ],
        empty="暂无承接要点。",
    )

    assert "• 沈鹿溪每日清晨录入分钟的风声剔已形成隐性约定" in formatted
    assert "• 被陈叔公拒绝拍摄祭祀的事实" in formatted
    assert "schema_version" not in formatted
    assert "created_at" not in formatted


def test_chapter_context_blob_preserves_summary_and_formats_payloads() -> None:
    formatted = _format_context_blob(
        "风先抵达了这里。\n"
        "{'schema_version': '2.0', 'text': '镇上两个外来者存在', 'status': 'open'}",
        empty="暂无。",
    )

    assert formatted.startswith("风先抵达了这里。")
    assert "• 镇上两个外来者存在" in formatted
    assert "schema_version" not in formatted


def test_chapter_context_blob_keeps_non_payload_braces() -> None:
    assert _format_context_blob("保留{普通提示}文本", empty="暂无。") == "保留{普通提示}文本"


def _surface_containing_text(panel: UnifiedMemoryPanel, text: str) -> Surface:
    for card in panel.findChildren(Surface):
        if any(text in label.text() for label in card.findChildren(QLabel)):
            return card
    raise AssertionError(f"Surface containing {text!r} was not rendered")


def test_memory_inset_hover_style_is_opt_in() -> None:
    stylesheet = get_stylesheet()

    assert 'QFrame#surface[tone="inset"]:hover' not in stylesheet
    assert 'QFrame#surface[tone="inset"][memoryHover="true"]:hover' in stylesheet
    assert "QLabel#memoryMotifName {\n    color:" in stylesheet
    assert "QLabel#memoryPriorityName {\n    color:" in stylesheet


def test_interactive_memory_cards_opt_into_hover(qapp: QApplication) -> None:
    motif_card = MemoryMotifCard(
        motif_id="m1",
        category="意象",
        description="怀表反复出现",
        occurrence_count=3,
        last_chapter=4,
    )
    suggestion_card = MemorySuggestionCard(
        motif_id="m1",
        suggestion="在章末回调怀表声响",
        priority="high",
        motif_name="旧怀表",
    )

    assert motif_card.property("memoryHover") is True
    assert suggestion_card.property("memoryHover") is True
    assert motif_card.property("memoryPanelSurface") is True
    assert suggestion_card.property("memoryPanelSurface") is True
    assert motif_card.graphicsEffect() is None
    assert suggestion_card.graphicsEffect() is None


def test_issue_panel_cards_do_not_opt_into_memory_hover(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()
    panel.update_chapter_warnings(["提醒 1：评估报告未绑定正文版本，建议重新评估。"])
    panel.update_continuity_issues(
        [
            {
                "issue_type": "carry_forward_missing",
                "severity": "high",
                "summary": "需要承接上一章留下的金锡线索。",
            }
        ]
    )
    panel.update_causal_issues(
        [
            {
                "issue_type": "event_without_cause",
                "severity": "high",
                "summary": "角色突然知道暗号，但前文没有信息来源。",
            }
        ]
    )

    for text in ("提醒 1", "金锡线索", "突然知道暗号"):
        card = _surface_containing_text(panel, text)
        assert card.property("memoryHover") is not True
        assert card.property("memoryPanelSurface") is True
        assert card.graphicsEffect() is None


def test_all_memory_panel_surface_cards_are_effect_free(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()
    panel.update_checkpoint("等待用户决定是否归档。")
    panel.update_carry_forward("玄豆需要保留金锡线索。")
    panel.update_suggestions("下一章继续推进身份误认。")
    panel.update_scores("评估 8.1 / 10。")
    panel.update_motifs(
        motifs=[
            {
                "motif_id": "m1",
                "category": "动作",
                "description": "衣摆被扯",
                "occurrence_count": 5,
                "last_chapter": 2,
                "name": "衣摆被扯",
            }
        ],
        suggestions=[
            {
                "motif_id": "m1",
                "suggestion": "下一章可轻触一次衣摆动作。",
                "priority": "medium",
                "motif_name": "衣摆被扯",
            }
        ],
        warnings=[
            {
                "motif_id": "m1",
                "message": "衣摆动作出现频率偏高。",
                "severity": "medium",
            }
        ],
    )
    panel.update_chapter_warnings(["提醒：报告未绑定当前正文。"])
    panel.update_continuity_issues(
        [{"summary": "需要承接上一章留下的金锡线索。", "severity": "high"}]
    )
    panel.update_causal_issues(
        [{"summary": "角色突然知道暗号，但前文没有信息来源。", "severity": "high"}]
    )

    surfaces = panel.findChildren(Surface)

    assert surfaces
    assert all(card.property("memoryPanelSurface") is True for card in surfaces)
    assert all(card.graphicsEffect() is None for card in surfaces)


def test_memory_tabs_fill_panel_width_adaptively(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()
    panel.resize(340, 600)
    panel.show()
    qapp.processEvents()

    tab_bar = panel._tab_widget.tabBar()  # noqa: SLF001
    total_tab_width = sum(tab_bar.tabSizeHint(index).width() for index in range(tab_bar.count()))

    assert tab_bar.expanding() is True
    assert tab_bar.usesScrollButtons() is False
    assert "font-size: 10px" in tab_bar.styleSheet()
    assert total_tab_width <= panel.width()


def test_memory_tab_fade_animation_does_not_keep_deleted_wrapper(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()
    panel.show()
    qapp.processEvents()

    panel._tab_widget.setCurrentIndex(1)  # noqa: SLF001
    qapp.processEvents()
    assert panel._tab_fade_anim is not None  # noqa: SLF001

    QTest.qWait(panel.TAB_SWITCH_FADE_DURATION_MS + 50)
    qapp.processEvents()
    assert panel._tab_fade_anim is None  # noqa: SLF001

    panel._tab_widget.setCurrentIndex(2)  # noqa: SLF001
    qapp.processEvents()
    assert panel._tab_fade_anim is not None  # noqa: SLF001


def test_set_empty_continuity_resets_titles(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()

    panel.update_continuity_issues([{"summary": "连贯性问题"}])
    panel.update_causal_issues([{"summary": "因果问题"}], status_hint="旧版提示")
    panel.set_empty_continuity()

    assert panel._continuity_title.text() == "连贯性问题"  # noqa: SLF001
    assert panel._causal_title.text() == "因果链问题"  # noqa: SLF001
    assert panel._causal_status_hint.isVisible() is False  # noqa: SLF001


def test_update_causal_issues_renders_status_hint(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()

    panel.update_causal_issues(
        [{"summary": "因果问题", "severity": "low"}],
        status_hint="当前剩余因果问题均为中低优先级，不阻断归档。可按需手动修复。",
    )

    assert panel._causal_status_hint.text()  # noqa: SLF001
    assert panel._causal_status_hint.isHidden() is False  # noqa: SLF001
    assert "中低优先级" in panel._causal_status_hint.text()  # noqa: SLF001


def test_continuity_issue_card_uses_repair_directive_fallback(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()

    panel.update_continuity_issues(
        [
            {
                "issue_type": "carry_forward_missing",
                "severity": "high",
                "repair_directive": {
                    "summary": "需要承接上一章留下的金锡线索。",
                },
            }
        ]
    )

    labels = [label.text() for label in panel.findChildren(QLabel)]
    assert any("金锡线索" in text for text in labels)


def test_causal_issue_card_uses_description_fallback(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()

    panel.update_causal_issues(
        [
            {
                "issue_type": "event_without_cause",
                "severity": "high",
                "description": "角色突然知道暗号，但前文没有信息来源。",
            }
        ]
    )

    labels = [label.text() for label in panel.findChildren(QLabel)]
    assert any("突然知道暗号" in text for text in labels)


def test_issues_tab_exposes_reevaluate_button(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()
    reevaluate_btn = panel.get_reevaluate_button()

    assert isinstance(reevaluate_btn, ActionButton)
    assert reevaluate_btn.text() == "重新评估"


def test_reevaluate_button_stays_visible_when_repair_section_hidden(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()

    panel.set_repair_section_visible(False)

    assert panel.get_reevaluate_button().isHidden() is False
    assert panel.get_repair_button().isHidden() is True


def test_chapter_warning_clear_button_emits_signal(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()
    panel.update_chapter_warnings(["提醒：基于旧版正文，建议重新评估。"])
    clear_btn = panel.get_chapter_warnings_clear_button()

    captured: list[str] = []
    panel.chapter_warnings_clear_requested.connect(lambda: captured.append("clear"))
    clear_btn.click()

    assert captured == ["clear"]


def test_update_chapter_warnings_renders_warning_cards(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()
    panel.update_chapter_warnings(
        [
            "提醒 1：评估报告未绑定正文版本，建议重新评估。",
            "提醒 2：当前报告基于旧版正文。",
            "提醒 3：可先重新评估再决定是否修复。",
        ]
    )

    assert panel._chapter_warning_title.text() == "章节提醒（3 条）"  # noqa: SLF001
    warning_cards = [
        panel._chapter_warning_layout.itemAt(index).widget()  # noqa: SLF001
        for index in range(panel._chapter_warning_layout.count())  # noqa: SLF001
        if panel._chapter_warning_layout.itemAt(index).widget() is not None  # noqa: SLF001
    ]
    assert len(warning_cards) == 3

    warning_texts: list[str] = []
    for card in warning_cards:
        for label in card.findChildren(QLabel):
            text = label.text()
            if "提醒" in text:
                warning_texts.append(text)
                break
    assert any("提醒 1" in text for text in warning_texts)


def test_chapter_warning_clear_button_disabled_without_warnings(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()
    panel.update_chapter_warnings([])

    assert panel.get_chapter_warnings_clear_button().isEnabled() is False
    assert "当前无章节提醒" in panel._chapter_warning_audit_hint.text()  # noqa: SLF001


def test_chapter_warning_audit_hint_for_stale_report(qapp: QApplication) -> None:
    panel = UnifiedMemoryPanel()
    panel.update_chapter_warnings(["质量评估基于旧版正文，建议重新评估或重新生成。"])

    assert "重新评估" in panel._chapter_warning_audit_hint.text()  # noqa: SLF001
