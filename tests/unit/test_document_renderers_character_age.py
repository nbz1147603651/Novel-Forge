"""Regression tests for character age rendering in document cards."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from novel_forge.desktop.pages.document_renderers import (  # noqa: E402
    _build_character_card,
    _role_label_for_display,
    render_chapter_prose,
    render_character_relationship_matrix,
    render_character_system,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.mark.parametrize(
    ("raw_age", "expected"),
    [
        ("19", "19岁"),
        ("19岁", "19岁"),
        ("19 歲", "19岁"),
        ("十九岁", "十九岁"),
    ],
)
def test_character_card_age_suffix_is_not_duplicated(
    qapp: QApplication, raw_age: str, expected: str,
) -> None:
    card = _build_character_card(
        {"name": "风伏京", "role": "protagonist", "age": raw_age, "gender": "女"},
    )
    qapp.processEvents()

    meta_texts = [
        label.text()
        for label in card.findChildren(QLabel)
        if label.objectName() == "charCardMeta"
    ]

    assert expected in meta_texts
    assert not any("岁岁" in text for text in meta_texts)


def test_deuteragonist_label_respects_gender_in_character_card(qapp: QApplication) -> None:
    card = _build_character_card(
        {"name": "沈知微", "role": "deuteragonist", "age": "29岁", "gender": "女"},
    )
    qapp.processEvents()

    badge_texts = [
        label.text()
        for label in card.findChildren(QLabel)
        if label.objectName() == "charCardRoleBadge"
    ]

    assert "女主" in badge_texts
    assert "男主" not in badge_texts


def test_protagonist_label_respects_gender() -> None:
    assert _role_label_for_display("protagonist", "女") == "女主"
    assert _role_label_for_display("protagonist", "男") == "男主"
    assert _role_label_for_display("protagonist", "") == "主角"


def test_character_card_shows_summary_without_arc(qapp: QApplication) -> None:
    card = _build_character_card(
        {
            "name": "陆奶奶",
            "role": "supporting",
            "age": "68岁",
            "gender": "女",
            "appearance": "身材矮小但精神矍铄，常年穿素色棉麻唐装。",
            "personality": "传统但敏锐细腻，最在意孙儿身体健康。",
            "backstory": "年轻时曾受益于沈家老爷子的针灸。",
        },
    )
    qapp.processEvents()

    summaries = [
        label.text()
        for label in card.findChildren(QLabel)
        if label.objectName() == "charCardSummary"
    ]

    assert summaries
    assert "外貌：" in summaries[0]
    assert "性格：" in summaries[0]


def test_character_card_click_expands_details(qapp: QApplication) -> None:
    card = _build_character_card(
        {
            "name": "陈默",
            "role": "supporting",
            "appearance": "常穿藏青色棉麻长衫。",
            "personality": "温润稳重的长兄型人格。",
            "backstory": "中医世家旁支传人。",
            "relationships": {"沈知微": "同门师兄与合伙人"},
        },
    )
    detail = card.findChild(QWidget, "charCardDetails")
    assert detail is not None
    assert detail.isHidden()

    card.resize(360, 120)
    card.show()
    qapp.processEvents()

    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    qapp.processEvents()

    assert not detail.isHidden()
    assert any(
        label.objectName() == "charCardRelationLine" and "沈知微" in label.text()
        for label in card.findChildren(QLabel)
    )

    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    qapp.processEvents()

    assert detail.isHidden()
    card.close()


def test_deuteragonist_label_falls_back_to_structural_role_when_gender_unknown() -> None:
    assert _role_label_for_display("deuteragonist", "") == "第二主角"
    assert _role_label_for_display("deuteragonist", "其他") == "第二主角"
    assert _role_label_for_display("deuteragonist", "男") == "男主"
    assert _role_label_for_display("deuteragonist", "女") == "女主"


def test_character_system_summary_uses_gender_aware_role_labels(qapp: QApplication) -> None:
    browser = render_character_system(
        {
            "profiles": [
                {"name": "陆砚深", "role": "protagonist", "gender": "男"},
                {"name": "沈知微", "role": "deuteragonist", "gender": "女"},
            ],
            "roster": [],
            "relationship_edges": [],
            "identity_links": [],
            "audit": [],
        }
    )
    qapp.processEvents()

    rendered = browser.toPlainText()

    assert "女主" in rendered
    assert "男主" in rendered


def test_relationship_matrix_renderer_shows_health_and_unknown_name_audit(
    qapp: QApplication,
) -> None:
    browser = render_character_relationship_matrix(
        {
            "relationship_generation_phase": "final",
            "relationship_matrix": [
                {
                    "character_a": "沈知微",
                    "character_b": "陈默",
                    "relation_type": "alliance",
                    "description": "共同追查旧案。",
                    "confidence": 0.9,
                }
            ],
        },
        character_system={
            "roster": [
                {"name": "沈知微", "role": "protagonist", "status": "active"},
                {"name": "陈默", "role": "supporting", "status": "active"},
                {"name": "林晚", "role": "antagonist", "status": "active"},
            ],
            "audit": [
                {
                    "code": "isolated_active_character",
                    "severity": "warning",
                    "character_name": "林晚",
                    "message": "活跃关键人物没有任何人物关系。",
                    "suggested_action": "检查关系矩阵。",
                }
            ],
        },
    )
    qapp.processEvents()

    rendered = browser.toPlainText()

    assert "最终人物关系矩阵" in rendered
    assert "沈知微" in rendered
    assert "陈默" in rendered
    assert "孤立关键人物" in rendered
    assert "林晚" in rendered


def test_render_chapter_prose_scrubs_prompt_leak_labels(qapp: QApplication) -> None:
    browser = render_chapter_prose(
        "她将这卷书放在火边。\n\n"
        "**情感障碍落地场景——第一阶段：对规则的盲从**\n\n"
        "她没有抬头。\n\n"
        "**暴露风险信号：她说话时握紧了袖口。**\n\n"
        "火星在她指尖下炸开。",
        chapter_num=1,
        title="焚《女诫》",
    )
    qapp.processEvents()

    rendered = browser.toPlainText()

    assert "情感障碍落地场景" not in rendered
    assert "暴露风险信号" not in rendered
    assert "她将这卷书放在火边" in rendered
    assert "火星在她指尖下炸开" in rendered
