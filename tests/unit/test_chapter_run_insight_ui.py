from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QTextEdit, QWidget

from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.workflow.jobs import JobCard, RunInsightDialog
from novel_forge.desktop.task_flow_errors import task_flow_error_guidance_lines


def _qapp() -> QApplication:
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


def _chapter_job() -> DesktopJobRecord:
    return DesktopJobRecord(
        job_id="chapter-insights",
        kind="run_chapter",
        label="第 3 章",
        project_id="story",
        status=DesktopJobState.SUCCEEDED,
        events=[
            DesktopJobEvent(
                at="2026-08-02T10:00:00+00:00",
                step="chapter_research_cache_hit",
                payload={"queries": 1},
            ),
            DesktopJobEvent(
                at="2026-08-02T10:01:00+00:00",
                step="final_text_hash_verified",
                payload={"semantic_mutation_allowed_after": False},
            ),
        ],
    )


def test_job_card_renders_four_engine_owned_insight_chips() -> None:
    app = _qapp()
    card = JobCard(_chapter_job())
    strip = card.findChild(QWidget, "runInsightStrip")
    chips = strip.findChildren(QPushButton, "runInsightChip") if strip is not None else []

    assert strip is not None
    assert len(chips) == 4
    assert any("意图保护" in chip.text() for chip in chips)
    assert any("研究与证据" in chip.text() for chip in chips)

    card.deleteLater()
    app.processEvents()


def test_run_insight_dialog_shows_bounded_efficiency_without_hashes() -> None:
    app = _qapp()
    dialog = RunInsightDialog(
        {
            "label": "最终验证",
            "status": "success",
            "summary": "终稿文本指纹已验证",
            "detail": "验证后禁止继续发生语义修改。",
        },
        {
            "llm_calls": 4,
            "total_tokens": 1200,
            "cost_usd": 0.04,
            "research_queries": 1,
            "research_cache_hits": 1,
            "semantic_mutations": 2,
            "report_refreshes": 1,
            "rollbacks": 1,
            "final_hash_verifications": 1,
        },
    )
    text = dialog.findChild(QTextEdit, "runInsightDetail")

    assert text is not None
    assert "终稿文本指纹已验证" in text.toPlainText()
    assert "回滚：1" in text.toPlainText()
    assert "source_text_hash" not in text.toPlainText()

    dialog.deleteLater()
    app.processEvents()


def test_pyside_error_guidance_prefers_engine_classification() -> None:
    lines = task_flow_error_guidance_lines(
        {
            "error": "ValidationError",
            "cause_code": "intent_protection",
            "auto_repair_state": "blocked",
            "auto_repair_explanation": "修复会改变用户指定的 HE 结局。",
            "recommended_action": "保留安全版本并等待作者确认。",
            "recovery_action_kinds": ["manual_review"],
        }
    )

    assert lines == [
        "自动修复：为保护用户意图而停止",
        "为何停止：修复会改变用户指定的 HE 结局。",
        "建议动作：保留安全版本并等待作者确认。",
        "可用恢复：manual_review",
    ]
