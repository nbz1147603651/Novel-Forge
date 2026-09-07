"""Performance-oriented regression tests for workflow job panel rendering."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QSignalSpy  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QTextEdit, QWidget  # noqa: E402

from novel_forge.desktop.components.workflow import StepIndicatorRow  # noqa: E402
from novel_forge.desktop.jobs import (  # noqa: E402
    DesktopJobEvent,
    DesktopJobRecord,
    DesktopJobState,
)
from novel_forge.desktop.pages.standalone.init_manual_repair_dialog import (  # noqa: E402
    InitManualRepairDialog,
    build_init_manual_repair_location_cards,
    build_init_manual_repair_summary,
    init_manual_repair_available,
)
from novel_forge.desktop.pages.workflow.jobs import (  # noqa: E402
    _CARD_FIXED_HEIGHT,
    JobCard,
    _active_display_step_name_for_job,
    _auxiliary_step_summary,
    _compute_card_progress,
    _display_job_label,
    _init_long_chapter_design_matrix_summary,
    _init_long_outline_claim_prefetch_summary,
    _init_long_outline_generation_summary,
    _visible_steps_for_kind,
)
from novel_forge.desktop.pages.workflow.widgets import (  # noqa: E402
    _TASK_FLOW_MAX_HEIGHT,
    _TASK_FLOW_MIN_VISIBLE_CARDS,
    JobsPanel,
    TaskFlowErrorLogDialog,
    _format_task_flow_error_entries,
)
from novel_forge.pipeline.progress import summary_steps  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _job(job_id: str, *, step_count: int = 0) -> DesktopJobRecord:
    record = DesktopJobRecord(job_id=job_id, kind="run_short", label=f"任务 {job_id}")
    record.current_step = f"step_{step_count}" if step_count else ""
    record.updated_at = f"2026-03-30T12:00:{step_count:02d}+00:00"
    record.events = [
        DesktopJobEvent(at=record.updated_at, step=f"event_{idx}", payload={})
        for idx in range(step_count)
    ]
    return record


def _indicator_states_by_label(card: JobCard) -> dict[str, str]:
    indicator = card.findChildren(StepIndicatorRow)[0]
    labels = [
        label.toolTip()
        for label in indicator.findChildren(QLabel)
        if label.objectName() == "stepLabel"
    ]
    states = [str(dot.property("state")) for dot in indicator._dot_labels]
    return dict(zip(labels, states, strict=True))


def _write_init_repair_project(root: Path, project_id: str = "demo") -> None:
    project_dir = root / project_id
    (project_dir / "reports").mkdir(parents=True, exist_ok=True)
    (project_dir / "reports" / "init_readiness.json").write_text(
        json.dumps(
            {
                "allowed": False,
                "summary": "初始化准入未通过，需修复大纲继承问题。",
                "remaining_issues": [
                    {
                        "stage": "outline_inheritance",
                        "id": "issue_1",
                        "severity": "high",
                        "description": "第 2 章承诺未继承。",
                        "repair_scope": [
                            {"artifact": "outline", "chapters": [2], "fields": ["goal"]}
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_dir / "outline.json").write_text(
        json.dumps(
            {
                "total_chapters": 2,
                "volume_mode": False,
                "volumes": [],
                "synopsis": "测试大纲。",
                "chapters": [
                    {
                        "chapter_number": 1,
                        "title": "起",
                        "goal": "提出承诺。",
                        "beats_summary": ["提出承诺。"],
                    },
                    {
                        "chapter_number": 2,
                        "title": "承",
                        "goal": "承接承诺。",
                        "beats_summary": ["承接承诺。"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_init_blueprint_repair_project(root: Path, project_id: str = "demo") -> None:
    project_dir = root / project_id
    (project_dir / "reports").mkdir(parents=True, exist_ok=True)
    (project_dir / "plans").mkdir(parents=True, exist_ok=True)
    (project_dir / "reports" / "init_readiness.json").write_text(
        json.dumps(
            {
                "allowed": False,
                "summary": "初始化准入未通过，需修复叙事蓝图。",
                "remaining_issues": [
                    {
                        "stage": "blueprint_coherence",
                        "id": "issue_chapter",
                        "severity": "high",
                        "description": "同一不可逆事件出现在第 6、9、14 章。",
                        "repair_scope": [
                            {
                                "artifact": "blueprint",
                                "chapters": [6, 9, 14],
                                "fields": ["character_arcs", "narrative_phases"],
                            }
                        ],
                    }
                ],
                "recovery_actions": [
                    {
                        "kind": "manual_repair",
                        "artifacts": ["blueprint"],
                        "artifact_paths": ["plans/narrative_blueprint.json"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_dir / "plans" / "narrative_blueprint.json").write_text(
        json.dumps(
            {
                "synopsis": "测试蓝图。",
                "key_turning_points": [],
                "narrative_phases": [
                    {
                        "phase_name": "引入期",
                        "chapter_start": 1,
                        "chapter_end": 13,
                        "description": "初入小镇并建立风声母题。",
                    },
                    {
                        "phase_name": "转折期",
                        "chapter_start": 14,
                        "chapter_end": 26,
                        "description": "不可逆事件后的关系调整。",
                    },
                ],
                "character_arcs": [
                    {
                        "character": "林正",
                        "arc_summary": "从守成到愿意让老宅重新进入生活。",
                        "milestones": [
                            {
                                "chapter_start": 6,
                                "chapter_end": 9,
                                "description": "草图交给鹿鸣并被误读。",
                            }
                        ],
                    }
                ],
                "volumes": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_jobs_panel_reuses_cards_and_handles_empty_state(qapp: QApplication) -> None:
    panel = JobsPanel()
    first = _job("A", step_count=1)
    second = _job("B", step_count=0)

    panel.render([first, second])
    qapp.processEvents()

    first_card = panel._job_cards["A"]
    second_card = panel._job_cards["B"]

    first.events.append(DesktopJobEvent(at=first.updated_at, step="event_1", payload={}))
    first.current_step = "event_1"
    first.updated_at = "2026-03-30T12:00:59+00:00"
    panel.render([first, second])
    qapp.processEvents()

    assert panel._job_cards["A"] is first_card
    assert panel._job_cards["B"] is second_card

    panel.render([])
    qapp.processEvents()

    assert panel._job_cards == {}
    assert panel._empty_state is not None


def test_jobs_panel_keeps_two_and_half_card_container_height(qapp: QApplication) -> None:
    panel = JobsPanel()
    margins = panel._jobs_layout.contentsMargins()
    spacing = panel._jobs_layout.spacing()
    expected_floor = int(
        margins.top()
        + margins.bottom()
        + _CARD_FIXED_HEIGHT * _TASK_FLOW_MIN_VISIBLE_CARDS
        + spacing * (_TASK_FLOW_MIN_VISIBLE_CARDS - 1.0)
    )

    panel.render([_job("A", step_count=1)])
    qapp.processEvents()

    assert panel._jobs_scroll.minimumHeight() >= min(expected_floor, _TASK_FLOW_MAX_HEIGHT)

    panel.render([_job("A", step_count=1), _job("B", step_count=2), _job("C", step_count=3)])
    qapp.processEvents()

    assert panel._jobs_scroll.minimumHeight() >= min(expected_floor, _TASK_FLOW_MAX_HEIGHT)


def test_jobs_panel_clear_button_emits_terminal_job_ids(qapp: QApplication) -> None:
    panel = JobsPanel()
    running = _job("RUN", step_count=1)
    running.status = DesktopJobState.RUNNING
    done = _job("DONE", step_count=2)
    done.status = DesktopJobState.SUCCEEDED
    failed = _job("FAIL", step_count=3)
    failed.status = DesktopJobState.FAILED

    panel.render([running, done, failed])
    qapp.processEvents()

    assert panel._clear_task_flow_btn.isEnabled() is True
    spy = QSignalSpy(panel.clear_task_flow_requested)
    panel._clear_task_flow_btn.click()
    qapp.processEvents()

    assert spy.count() == 1
    assert spy.at(0)[0] == ["DONE", "FAIL"]


def test_jobs_panel_error_log_button_tracks_format_retry_events(qapp: QApplication) -> None:
    panel = JobsPanel()
    running = _job("RUN", step_count=1)
    running.status = DesktopJobState.RUNNING

    panel.render([running])
    qapp.processEvents()

    assert panel._error_logs_btn.property("errorState") == "clean"
    assert panel._error_logs_btn.text() == "错误日志 0"
    assert panel._task_flow_error_entries == []

    running.events.append(
        DesktopJobEvent(
            at="2026-03-30T12:01:00+00:00",
            step="format_retry",
            payload={
                "task": "plan_chapter_contracts",
                "attempt": 1,
                "max_attempts": 2,
                "error": "Expecting ',' delimiter",
                "raw_excerpt": '{"entry_state_requirements":["A" "B"]}',
                "log_file": "/tmp/demo/format_errors/001.json",
            },
        )
    )

    panel.render([running])
    qapp.processEvents()

    assert panel._error_logs_btn.property("errorState") == "error"
    assert panel._error_logs_btn.text() == "错误日志 1"
    assert panel._task_flow_error_entries[0]["task"] == "plan_chapter_contracts"
    assert panel._task_flow_error_entries[0]["attempt"] == "1/2"
    assert panel._task_flow_error_entries[0]["error"] == "Expecting ',' delimiter"


def test_cancelled_job_is_muted_and_does_not_create_error_log(qapp: QApplication) -> None:
    panel = JobsPanel()
    cancelled = DesktopJobRecord(
        job_id="CANCELLED",
        kind="init_long",
        label="长篇立项 · demo",
        project_id="demo",
        status=DesktopJobState.FAILED,
        current_step="cancelled",
        error="用户已取消",
        events=[
            DesktopJobEvent(
                at="2026-07-13T12:00:00+00:00",
                step="plan_outline",
                payload={},
            )
        ],
    )

    panel.render([cancelled])
    qapp.processEvents()

    card = panel._job_cards["CANCELLED"]
    badges = [label.text() for label in card.findChildren(QLabel, "badge")]
    danger_lines = card.findChildren(QLabel, "dangerText")
    indicator = card.findChildren(StepIndicatorRow)[0]

    assert "已取消" in badges
    assert danger_lines == []
    assert "skipped" in [dot.property("state") for dot in indicator._dot_labels]
    assert panel._task_flow_error_entries == []
    assert panel._error_logs_btn.text() == "错误日志 0"


def test_jobs_panel_auto_resolves_format_retry_after_same_task_success(
    qapp: QApplication,
) -> None:
    panel = JobsPanel()
    running = _job("RUN", step_count=1)
    running.status = DesktopJobState.RUNNING
    running.events.extend(
        [
            DesktopJobEvent(
                at="2026-03-30T12:01:00+00:00",
                step="format_retry",
                payload={
                    "task": "plan_chapter_contracts",
                    "attempt": 1,
                    "max_attempts": 3,
                    "error": "Local JSON repair lost structural content",
                },
            ),
            DesktopJobEvent(
                at="2026-03-30T12:02:00+00:00",
                step="plan_chapter_contracts_repaired",
                payload={"coverage": {"complete": True}},
            ),
        ]
    )

    panel.render([running])
    qapp.processEvents()

    assert panel._error_logs_btn.property("errorState") == "clean"
    assert panel._error_logs_btn.text() == "错误日志 1 · 已处理"
    assert panel._task_flow_error_entries[0]["auto_resolved"] == "true"
    rendered = _format_task_flow_error_entries(panel._task_flow_error_entries)
    assert "状态：已自动恢复" in rendered


def test_jobs_panel_auto_resolves_format_retry_after_validation_success(
    qapp: QApplication,
) -> None:
    panel = JobsPanel()
    running = _job("RUN", step_count=1)
    running.status = DesktopJobState.RUNNING
    running.events.extend(
        [
            DesktopJobEvent(
                at="2026-03-30T12:01:00+00:00",
                step="format_retry",
                payload={
                    "task": "plan_chapter_contracts",
                    "attempt": 1,
                    "max_attempts": 3,
                    "error": "Local JSON repair lost structural content",
                },
            ),
            DesktopJobEvent(
                at="2026-03-30T12:02:00+00:00",
                step="format_validation_success",
                payload={"task": "plan_chapter_contracts", "parse_source": "json"},
            ),
        ]
    )

    panel.render([running])
    qapp.processEvents()

    assert panel._error_logs_btn.property("errorState") == "clean"
    assert panel._error_logs_btn.text() == "错误日志 1 · 已处理"
    assert panel._task_flow_error_entries[0]["auto_resolved"] == "true"


def test_jobs_panel_does_not_treat_successful_format_repair_as_error(
    qapp: QApplication,
) -> None:
    panel = JobsPanel()
    running = _job("RUN", step_count=1)
    running.status = DesktopJobState.RUNNING
    running.current_step = "extract_init_coherence_claims"
    running.events = [
        DesktopJobEvent(
            at="2026-03-30T12:01:00+00:00",
            step="format_repaired",
            payload={
                "task": "plan_outline",
                "attempt": 1,
                "max_attempts": 2,
                "error": "JSONDecodeError: Expecting ',' delimiter",
                "repair_action": "local_parse_repair",
            },
        ),
        DesktopJobEvent(
            at="2026-03-30T12:01:01+00:00",
            step="extract_init_coherence_claims",
            payload={"stage": "outline_inheritance"},
        ),
    ]

    panel.render([running])
    qapp.processEvents()

    assert panel._error_logs_btn.property("errorState") == "clean"
    assert panel._error_logs_btn.text() == "错误日志 0"
    assert panel._task_flow_error_entries == []

    card = panel._job_cards["RUN"]
    labels = [(label.text(), label.objectName()) for label in card.findChildren(QLabel)]
    assert ("格式已本地修复 · 叙事蓝图", "cardMeta") in labels
    assert all("JSONDecodeError" not in text for text, _object_name in labels)


def test_jobs_panel_error_logs_can_be_marked_resolved(qapp: QApplication) -> None:
    panel = JobsPanel()
    running = _job("RUN", step_count=1)
    running.status = DesktopJobState.RUNNING
    running.events.append(
        DesktopJobEvent(
            at="2026-03-30T12:01:00+00:00",
            step="format_retry",
            payload={
                "task": "plan_chapter_contracts",
                "attempt": 1,
                "max_attempts": 2,
                "error": "Expecting ',' delimiter",
            },
        )
    )

    panel.render([running])
    qapp.processEvents()
    assert panel._error_logs_btn.property("errorState") == "error"

    spy = QSignalSpy(panel.task_flow_error_logs_resolved)
    panel._mark_current_error_logs_resolved()
    qapp.processEvents()

    assert len(panel._task_flow_error_entries) == 1
    entry_id = panel._task_flow_error_entries[0]["id"]
    assert spy.count() == 1
    assert spy.at(0)[0] == {"RUN": [entry_id]}
    assert panel._error_logs_btn.property("errorState") == "clean"
    assert panel._error_logs_btn.text() == "错误日志 1 · 已处理"

    running.events.append(
        DesktopJobEvent(
            at="2026-03-30T12:02:00+00:00",
            step="format_retry",
            payload={
                "task": "adjudicate_contract_coherence",
                "attempt": 1,
                "max_attempts": 2,
                "error": "Missing required response key: summary",
            },
        )
    )
    panel.render([running])
    qapp.processEvents()

    assert panel._error_logs_btn.property("errorState") == "error"
    assert panel._error_logs_btn.text() == "错误日志 2 · 待处理 1"


def test_task_flow_error_dialog_disambiguates_plan_outline_task(
    qapp: QApplication,
) -> None:
    panel = JobsPanel()
    running = _job("RUN", step_count=1)
    running.status = DesktopJobState.RUNNING
    running.events.append(
        DesktopJobEvent(
            at="2026-03-30T12:01:00+00:00",
            step="format_retry",
            payload={
                "task": "plan_outline",
                "attempt": 1,
                "max_attempts": 2,
                "error": "Unexpected response key(s): created_at, schema_version",
            },
        )
    )

    panel.render([running])
    qapp.processEvents()

    entry = panel._task_flow_error_entries[0]
    assert entry["task"] == "plan_outline"
    assert entry["task_label"] == "叙事蓝图"
    rendered = _format_task_flow_error_entries(panel._task_flow_error_entries)
    assert "任务：叙事蓝图（plan_outline）" in rendered


def test_jobs_panel_restores_resolved_error_log_state_from_job_record(
    qapp: QApplication,
) -> None:
    panel = JobsPanel()
    failed = _job("FAIL", step_count=1)
    failed.status = DesktopJobState.FAILED
    failed.current_step = "init_readiness"
    failed.error = "blueprint_coherence needs_repair"

    panel.render([failed])
    qapp.processEvents()

    entry_id = panel._task_flow_error_entries[0]["id"]
    failed.resolved_error_entry_ids = {entry_id}

    restored_panel = JobsPanel()
    restored_panel.render([failed])
    qapp.processEvents()

    assert restored_panel._error_logs_btn.property("errorState") == "clean"
    assert restored_panel._error_logs_btn.text() == "错误日志 1 · 已处理"


def test_task_flow_error_dialog_marks_entries_without_deleting(
    qapp: QApplication,
) -> None:
    entries = [
        {
            "id": "entry-1",
            "kind": "格式错误",
            "time": "2026-03-30T12:01:00+00:00",
            "job": "长篇立项 · demo",
            "task": "plan_chapter_contracts",
            "attempt": "1/2",
            "error": "Expecting ',' delimiter",
        }
    ]
    dialog = TaskFlowErrorLogDialog(entries)
    spy = QSignalSpy(dialog.resolved_requested)

    assert "状态：待确认" in dialog._text.toPlainText()
    dialog._mark_resolved_btn.click()
    qapp.processEvents()

    assert spy.count() == 1
    assert "状态：已标记修复" in dialog._text.toPlainText()
    assert dialog._mark_resolved_btn.isEnabled() is False
    assert len(dialog._entries) == 1


def test_jobs_panel_uses_top_alignment_to_keep_cards_compact(qapp: QApplication) -> None:
    panel = JobsPanel()
    panel.render([_job("A", step_count=2)])
    qapp.processEvents()

    assert bool(panel._jobs_layout.alignment() & Qt.AlignmentFlag.AlignTop)


def test_job_card_expands_details_without_internal_scroll(qapp: QApplication) -> None:
    record = _job("LONG", step_count=3)
    record.label = "任务流卡片标题非常长，需要保持单行省略但不再让步骤标签换行"
    record.project_id = "与君共赴-一个用于验证详情常显与卡片自适应高度的超长项目名"
    record.status = DesktopJobState.SUCCEEDED
    record.result = {
        "word_count": 6200,
        "overall_score": 8.1,
        "continuity_score": 7.5,
        "warnings": [
            "这是一条非常长的提醒文本，用来验证卡片不再通过内部滚动区折叠详情，而是直接把文本展示在卡片主体中。"
        ],
    }

    card = JobCard(record)
    card.resize(520, _CARD_FIXED_HEIGHT)
    card.show()
    qapp.processEvents()

    title_label = next(
        label for label in card.findChildren(QLabel) if label.objectName() == "cardTitle"
    )
    warning_label = next(
        label for label in card.findChildren(QLabel) if label.objectName() == "memoryWarningMarker"
    )
    title_label.setFixedWidth(40)
    qapp.processEvents()

    assert card.minimumHeight() == _CARD_FIXED_HEIGHT
    assert card.maximumHeight() > _CARD_FIXED_HEIGHT
    assert card.sizeHint().height() >= _CARD_FIXED_HEIGHT
    assert title_label.toolTip() == record.label
    assert title_label.text() != record.label
    assert warning_label.text() == f"提醒：{record.result['warnings'][0]}"
    assert not card.findChildren(QWidget, "jobCardDetailScroll")


def test_running_init_task_flow_does_not_expand_panel_width(qapp: QApplication) -> None:
    record = DesktopJobRecord(
        job_id="init-running",
        kind="init_long",
        label="长篇立项 · 一个很长很长的初始化项目名称",
        project_id="一个很长很长的初始化项目名称",
        status=DesktopJobState.RUNNING,
        current_step="adjudicate_contract_coherence",
    )
    record.created_at = "2026-06-21T10:00:00+00:00"
    record.updated_at = "2026-06-21T10:00:01+00:00"
    record.events = [
        DesktopJobEvent(
            at=record.updated_at,
            step="adjudicate_contract_coherence",
            payload={"status": "running"},
        )
    ]
    panel = JobsPanel()
    panel.resize(400, 360)
    panel.render([record])
    panel.show()
    qapp.processEvents()

    card = panel._job_cards["init-running"]
    indicator = card.findChildren(StepIndicatorRow)[0]

    assert panel.width() == 400
    assert panel.minimumSizeHint().width() <= 400
    assert card.width() <= panel._jobs_scroll.viewport().width() + 1
    assert indicator.minimumSizeHint().width() <= card.width()
    assert not card.findChildren(QWidget, "jobCardDetailScroll")


def test_failed_init_card_exposes_retry_and_manual_repair_actions(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _write_init_repair_project(tmp_path, "魂玉")
    record = DesktopJobRecord(
        job_id="INIT-FAILED",
        kind="init_long",
        label="长篇立项 · 魂玉",
        project_id="魂玉",
        status=DesktopJobState.FAILED,
        current_step="init_readiness",
        error="章节大纲继承裁判未通过：outline_inheritance needs_repair",
    )

    card = JobCard(record, storage_root=tmp_path, enable_init_repair_actions=True)
    card.show()
    qapp.processEvents()

    buttons = {
        button.text(): button for button in card.findChildren(QWidget) if hasattr(button, "text")
    }
    assert "AI修复" in buttons
    assert "人工修复" in buttons

    retry_spy = QSignalSpy(card.init_retry_requested)
    manual_spy = QSignalSpy(card.init_manual_repair_requested)
    buttons["AI修复"].click()
    buttons["人工修复"].click()
    qapp.processEvents()

    assert retry_spy.count() == 1
    assert manual_spy.count() == 1


def test_init_manual_repair_available_uses_blueprint_scope(tmp_path: Path) -> None:
    _write_init_blueprint_repair_project(tmp_path, "山风与归人")
    project_dir = tmp_path / "山风与归人"

    assert init_manual_repair_available(project_dir) is True
    summary = build_init_manual_repair_summary(project_dir)

    assert "当前修复产物：叙事蓝图（plans/narrative_blueprint.json）" in summary
    assert "同一不可逆事件出现在第 6、9、14 章" in summary
    assert "字段 character_arcs, narrative_phases" in summary


def test_init_manual_repair_locations_resolve_blueprint_pointers(tmp_path: Path) -> None:
    _write_init_blueprint_repair_project(tmp_path, "山风与归人")
    project_dir = tmp_path / "山风与归人"

    cards = build_init_manual_repair_location_cards(project_dir)

    assert len(cards) == 1
    locations = cards[0]["locations"]
    pointers = {location["pointer"] for location in locations}
    assert "/character_arcs/0" in pointers
    assert "/narrative_phases/0" in pointers
    assert "/narrative_phases/1" in pointers
    assert all(location["confidence"] == "exact" for location in locations)
    assert any("林正" in location["excerpt"] for location in locations)


def test_init_manual_repair_dialog_can_jump_to_location(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _write_init_blueprint_repair_project(tmp_path, "山风与归人")
    project_dir = tmp_path / "山风与归人"

    dialog = InitManualRepairDialog(project_dir)
    dialog.show()
    qapp.processEvents()

    selectors = dialog.findChildren(QComboBox)
    assert len(selectors) >= 2
    assert "issue_chapter" in selectors[0].currentText()
    assert selectors[1].currentText().startswith("/")
    buttons = {
        button.text(): button for button in dialog.findChildren(QWidget) if hasattr(button, "text")
    }
    buttons["定位到修改点"].click()
    qapp.processEvents()

    editor = dialog.findChild(QTextEdit, "workflowFieldDialogText")
    assert editor is not None
    assert editor.textCursor().hasSelection()
    dialog.close()


def test_failed_init_card_hides_repair_actions_when_same_project_is_active(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    _write_init_repair_project(tmp_path, "魂玉")
    running = DesktopJobRecord(
        job_id="INIT-RUNNING",
        kind="init_long",
        label="长篇立项 · 魂玉",
        project_id="魂玉",
        status=DesktopJobState.RUNNING,
        current_step="derive_narrative_contract",
    )
    failed = DesktopJobRecord(
        job_id="INIT-FAILED",
        kind="init_long",
        label="长篇立项 · 魂玉",
        project_id="魂玉",
        status=DesktopJobState.FAILED,
        current_step="init_readiness",
        error="章节大纲继承裁判未通过：outline_inheritance needs_repair",
    )

    panel = JobsPanel()
    panel.render_jobs([running, failed], tmp_path, {"魂玉"})
    panel.show()
    qapp.processEvents()

    failed_card = panel._job_cards["INIT-FAILED"]
    button_texts = {
        button.text() for button in failed_card.findChildren(QWidget) if hasattr(button, "text")
    }
    assert "AI修复" not in button_texts
    assert "人工修复" not in button_texts


def test_job_card_keeps_primary_steps_and_summarizes_auxiliary_steps(
    qapp: QApplication,
) -> None:
    record = DesktopJobRecord(
        job_id="INIT",
        kind="init_long",
        label="长篇立项 · 与君共赴",
        project_id="与君共赴",
        status=DesktopJobState.SUCCEEDED,
        current_step="canon_state",
        events=[
            DesktopJobEvent(
                at="2026-05-13T23:38:10+08:00",
                step="spec",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:38:11+08:00",
                step="init_entity_registry_failed",
                payload={"fallback": "character_system_entity_graph"},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:38:12+08:00",
                step="init_entity_graph",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:38:13+08:00",
                step="plan_blueprint_fragments",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:38:14+08:00",
                step="plan_blueprint_validated",
                payload={},
            ),
        ],
    )

    card = JobCard(record)
    card.resize(520, _CARD_FIXED_HEIGHT)
    card.show()
    qapp.processEvents()

    indicators = card.findChildren(StepIndicatorRow)
    assert len(indicators) == 1
    step_tooltips = [
        label.toolTip()
        for label in indicators[0].findChildren(QLabel)
        if label.objectName() == "stepLabel"
    ]
    assert "章节契约" in step_tooltips
    assert "规范初始化" in step_tooltips
    assert "章节设计矩阵" in step_tooltips
    assert "角色审计" not in step_tooltips
    assert "实体图谱" not in step_tooltips
    assert "蓝图分块" not in step_tooltips
    assert "蓝图验证" not in step_tooltips
    dot_states = [dot.property("state") for dot in indicators[0]._dot_labels]
    assert dot_states[step_tooltips.index("叙事蓝图")] == "done"

    detail_texts = [
        label.text()
        for label in card.findChildren(QLabel)
        if label.objectName() in {"progressDetail", "cardMeta"}
    ]
    assert any("步骤：规范初始化 · 15/15" in text for text in detail_texts)
    assert any("后台步骤：" in text and "已兜底 实体注册表" in text for text in detail_texts)
    assert any("后台步骤：" in text and "实体图谱" in text for text in detail_texts)
    assert any("蓝图分块" in text and "蓝图验证" in text for text in detail_texts)


def test_job_card_counts_full_init_steps_while_hiding_auxiliary_nodes(
    qapp: QApplication,
) -> None:
    record = DesktopJobRecord(
        job_id="INIT-RUNNING",
        kind="init_long",
        label="长篇立项 · 与君共赴",
        project_id="与君共赴",
        status=DesktopJobState.RUNNING,
        current_step="init_character_system",
        events=[
            DesktopJobEvent(
                at="2026-05-13T23:38:10+08:00",
                step="spec",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:38:11+08:00",
                step="init_story_bible",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:38:12+08:00",
                step="plan_blueprint_elements",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:38:13+08:00",
                step="init_character_bible",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:38:14+08:00",
                step="init_character_system",
                payload={},
            ),
        ],
    )

    card = JobCard(record)
    card.resize(520, _CARD_FIXED_HEIGHT)
    card.show()
    qapp.processEvents()

    indicator = card.findChildren(StepIndicatorRow)[0]
    step_tooltips = [
        label.toolTip()
        for label in indicator.findChildren(QLabel)
        if label.objectName() == "stepLabel"
    ]
    assert "角色审计" not in step_tooltips

    detail_texts = [
        label.text()
        for label in card.findChildren(QLabel)
        if label.objectName() in {"progressDetail", "cardMeta"}
    ]
    assert any("步骤：角色设定 · 5/15" in text for text in detail_texts)
    assert any("后台步骤：" in text and "当前 角色审计" in text for text in detail_texts)


def test_init_outline_claim_prefetch_displays_claims_as_active_step(
    qapp: QApplication,
) -> None:
    record = DesktopJobRecord(
        job_id="INIT-CLAIMS",
        kind="init_long",
        label="长篇立项 · 与君共赴",
        project_id="与君共赴",
        status=DesktopJobState.RUNNING,
        current_step="extract_init_coherence_claims",
        events=[
            DesktopJobEvent(
                at="2026-05-13T23:38:10+08:00",
                step="plan_chapter_design_matrix",
                payload={"chapters": 70, "entities": 18, "total_chapters": 70},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:38:20+08:00",
                step="plan_outline_starting",
                payload={"chapters_done": 0, "total_chapters": 70},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:39:10+08:00",
                step="plan_outline_batch_1_3",
                payload={"chapters_done": 3, "chapters_total": 70},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:39:20+08:00",
                step="extract_init_coherence_claims",
                payload={
                    "stage": "outline_inheritance",
                    "artifact": "outline",
                    "extraction_mode": "stream",
                    "claims": 0,
                    "batch": 1,
                    "batch_total": 1,
                    "max_parallel": 4,
                },
            ),
            DesktopJobEvent(
                at="2026-05-13T23:40:10+08:00",
                step="plan_outline_batch_4_6",
                payload={"chapters_done": 6, "chapters_total": 70},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:40:20+08:00",
                step="extract_init_coherence_claims",
                payload={
                    "stage": "outline_inheritance",
                    "artifact": "outline",
                    "extraction_mode": "stream",
                    "claims": 3,
                    "batch": 1,
                    "batch_total": 1,
                    "fallback_claims": 3,
                    "max_parallel": 4,
                },
            ),
        ],
    )

    assert _active_display_step_name_for_job(record).startswith(
        "初始化一致性 Claims 抽取  ·  1 / 1  ·  当前检查：章节大纲"
    )
    assert _init_long_outline_generation_summary(record) == (
        "大纲生成：章节大纲分批生成（4-6章）  ·  已安全保存到第6章（6/70）"
    )
    assert _init_long_chapter_design_matrix_summary(record) == (
        "章节设计矩阵：已覆盖 70章 · 结构预计算，非大纲正文 · 实体 18"
    )
    assert _init_long_outline_claim_prefetch_summary(record) == (
        "并行预取：一致性 Claims（大纲） · 2 批 · 累计 3 条 · 最近 3 条 · 空批 1 · "
        "本地兜底 3 条 · 并发 4"
    )
    assert "当前 Claims 抽取" not in _auxiliary_step_summary(record)

    card = JobCard(record)
    card.resize(640, _CARD_FIXED_HEIGHT)
    card.show()
    qapp.processEvents()

    detail_texts = [
        label.text()
        for label in card.findChildren(QLabel)
        if label.objectName() in {"progressDetail", "cardMeta"}
    ]
    assert any(text.startswith("正在执行：初始化一致性 Claims 抽取") for text in detail_texts)
    assert any(text.startswith("章节设计矩阵：已覆盖 70章") for text in detail_texts)
    assert any(text.startswith("大纲生成：章节大纲分批生成") for text in detail_texts)
    assert any(text.startswith("并行预取：一致性 Claims") for text in detail_texts)
    assert all("正在执行：章节大纲分批生成" not in text for text in detail_texts)


def test_init_blueprint_coherence_keeps_trimmed_prior_milestones_done(
    qapp: QApplication,
) -> None:
    record = DesktopJobRecord(
        job_id="INIT-BLUEPRINT-CHECK",
        kind="init_long",
        label="长篇立项 · 与君共赴",
        project_id="与君共赴",
        status=DesktopJobState.RUNNING,
        current_step="adjudicate_init_conflict_candidates",
        events=[
            DesktopJobEvent(
                at="2026-05-13T23:55:10+08:00",
                step="profile_style",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:56:10+08:00",
                step="plan_blueprint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:57:20+08:00",
                step="adjudicate_init_conflict_candidates",
                payload={
                    "stage": "blueprint_coherence",
                    "artifact": "blueprint",
                    "candidates": 74,
                    "issues": 0,
                    "verdict": "accept",
                },
            ),
        ],
    )

    card = JobCard(record)
    card.resize(640, _CARD_FIXED_HEIGHT)
    card.show()
    qapp.processEvents()
    states = _indicator_states_by_label(card)

    assert states["规格确认"] == "done"
    assert states["世界观设定"] == "done"
    assert states["要素选择"] == "done"
    assert states["角色设定"] == "done"
    assert states["风格规范"] == "done"
    assert states["叙事蓝图"] == "done"
    assert states["编辑契约"] == "pending"


def test_init_job_card_marks_prior_milestones_done_when_early_events_are_trimmed(
    qapp: QApplication,
) -> None:
    record = DesktopJobRecord(
        job_id="INIT-TRIMMED",
        kind="init_long",
        label="长篇立项 · 与君共赴",
        project_id="与君共赴",
        status=DesktopJobState.RUNNING,
        current_step="extract_init_coherence_claims",
        events=[
            DesktopJobEvent(
                at="2026-05-13T23:55:10+08:00",
                step="profile_style",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:55:30+08:00",
                step="profile_structure",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:56:10+08:00",
                step="plan_blueprint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:57:10+08:00",
                step="plan_outline_batch_19_21",
                payload={"chapters_done": 21, "chapters_total": 70},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:57:20+08:00",
                step="extract_init_coherence_claims",
                payload={
                    "stage": "outline_inheritance",
                    "artifact": "outline",
                    "extraction_mode": "stream",
                    "claims": 2,
                    "batch": 1,
                    "batch_total": 1,
                },
            ),
        ],
    )

    card = JobCard(record)
    card.resize(640, _CARD_FIXED_HEIGHT)
    card.show()
    qapp.processEvents()

    indicator = card.findChildren(StepIndicatorRow)[0]
    step_tooltips = [
        label.toolTip()
        for label in indicator.findChildren(QLabel)
        if label.objectName() == "stepLabel"
    ]
    dot_states = [dot.property("state") for dot in indicator._dot_labels]

    assert dot_states[step_tooltips.index("规格确认")] == "done"
    assert dot_states[step_tooltips.index("世界观设定")] == "done"
    assert dot_states[step_tooltips.index("要素选择")] == "done"
    assert dot_states[step_tooltips.index("角色设定")] == "done"
    assert dot_states[step_tooltips.index("章节大纲")] == "active"
    assert dot_states[step_tooltips.index("章节设计矩阵")] == "done"


def test_run_chapter_late_diagnostics_do_not_rewind_completed_milestones(
    qapp: QApplication,
) -> None:
    record = DesktopJobRecord(
        job_id="CHAPTER-DIAGNOSTICS",
        kind="run_chapter",
        label="章节生成 · demo / 第 2 章",
        project_id="demo",
        status=DesktopJobState.RUNNING,
        current_step="memory_planning_context",
        events=[
            DesktopJobEvent(
                at="2026-05-14T10:00:00+08:00",
                step="bridge",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-14T10:01:00+08:00",
                step="alignment",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-14T10:02:00+08:00",
                step="input_integrity_check",
                payload={"phase": "extract_input"},
            ),
            DesktopJobEvent(
                at="2026-05-14T10:03:00+08:00",
                step="memory_planning_context",
                payload={"status": "ready"},
            ),
        ],
    )

    card = JobCard(record)
    card.resize(640, _CARD_FIXED_HEIGHT)
    card.show()
    qapp.processEvents()
    states = _indicator_states_by_label(card)

    assert states["准备上下文"] == "done"
    assert states["初稿成章"] == "done"
    assert states["质量检查"] == "active"
    assert states["正文归档"] == "pending"


def test_consistency_replan_explicitly_resets_later_checkpoint_milestones(
    qapp: QApplication,
) -> None:
    record = DesktopJobRecord(
        job_id="CHAPTER-REPLAN",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 2 章",
        project_id="demo",
        status=DesktopJobState.RUNNING,
        current_step="consistency_replan",
        events=[
            DesktopJobEvent(
                at="2026-05-14T10:00:00+08:00",
                step="draft",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-14T10:01:00+08:00",
                step="alignment",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-14T10:02:00+08:00",
                step="consistency_replan",
                payload={"action": "replan"},
            ),
        ],
    )

    card = JobCard(record)
    card.resize(640, _CARD_FIXED_HEIGHT)
    card.show()
    qapp.processEvents()
    states = _indicator_states_by_label(card)

    assert states["方案确认"] == "active"
    assert states["初稿成章"] == "pending"
    assert states["质量检查"] == "pending"
    assert states["连续性修复"] == "pending"
    assert states["对齐修复"] == "pending"


def test_init_resume_rollback_resets_later_init_milestones(
    qapp: QApplication,
) -> None:
    record = DesktopJobRecord(
        job_id="INIT-ROLLBACK",
        kind="init_long",
        label="长篇立项 · 与君共赴",
        project_id="与君共赴",
        status=DesktopJobState.RUNNING,
        current_step="init_character_bible_starting",
        events=[
            DesktopJobEvent(
                at="2026-05-13T23:55:10+08:00",
                step="plan_blueprint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:56:10+08:00",
                step="plan_outline_batch_1_5",
                payload={"chapters_done": 5, "chapters_total": 70},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:57:10+08:00",
                step="init_resume_rollback",
                payload={"action": "rollback"},
            ),
            DesktopJobEvent(
                at="2026-05-13T23:58:10+08:00",
                step="init_character_bible_starting",
                payload={},
            ),
        ],
    )

    card = JobCard(record)
    card.resize(640, _CARD_FIXED_HEIGHT)
    card.show()
    qapp.processEvents()
    states = _indicator_states_by_label(card)

    assert states["规格确认"] == "done"
    assert states["世界观设定"] == "done"
    assert states["要素选择"] == "done"
    assert states["角色设定"] == "active"
    assert states["叙事蓝图"] == "pending"
    assert states["章节大纲"] == "pending"


def test_jobs_panel_single_card_does_not_expand_to_fill_viewport(qapp: QApplication) -> None:
    panel = JobsPanel()
    panel.resize(900, _TASK_FLOW_MAX_HEIGHT)
    panel.render([_job("A", step_count=1)])
    panel.show()
    qapp.processEvents()

    card = panel._job_cards["A"]
    assert card.height() < panel._jobs_scroll.viewport().height()


def test_job_card_progress_is_remapped_to_summary_step_positions() -> None:
    job = DesktopJobRecord(
        job_id="job-progress-visual",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 79 章",
        status=DesktopJobState.RUNNING,
        current_step="draft",
        events=[
            DesktopJobEvent(
                at="2026-05-02T10:00:00+00:00",
                step="plan_checkpoint",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-02T10:01:00+00:00",
                step="draft",
                payload={},
            ),
        ],
    )

    assert _compute_card_progress(job) == 21


def test_split_chapter_job_progress_matches_visible_step_positions() -> None:
    for kind in ("resolve_chapter_checkpoint", "resolve_chapter_checkpoint_finalize"):
        steps = summary_steps(kind)
        expected_positions = [
            round(((index + 0.5) / len(steps)) * 100) for index in range(len(steps))
        ]
        actual_positions = []
        for step in steps:
            job = DesktopJobRecord(
                job_id=f"job-{kind}-{step.key}",
                kind=kind,
                label="章节任务 · demo / 第 79 章",
                status=DesktopJobState.RUNNING,
                current_step=step.key,
                events=[
                    DesktopJobEvent(
                        at="2026-05-02T10:00:00+00:00",
                        step=step.key,
                        payload={},
                    )
                ],
            )
            actual_positions.append(_compute_card_progress(job))

        assert actual_positions == expected_positions


def test_init_long_job_progress_matches_visible_step_positions() -> None:
    steps = _visible_steps_for_kind("init_long")
    expected_positions = [round(((index + 0.5) / len(steps)) * 100) for index in range(len(steps))]
    actual_positions = []
    for step in steps:
        job = DesktopJobRecord(
            job_id=f"job-init-long-{step.key}",
            kind="init_long",
            label="长篇立项 · demo",
            status=DesktopJobState.RUNNING,
            current_step=step.key,
            events=[
                DesktopJobEvent(
                    at="2026-05-02T10:00:00+00:00",
                    step=step.key,
                    payload={},
                )
            ],
        )
        actual_positions.append(_compute_card_progress(job))

    assert actual_positions == expected_positions


def test_split_chapter_jobs_show_explicit_phase_titles() -> None:
    write_job = DesktopJobRecord(
        job_id="write",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 79 章",
    )
    finalize_job = DesktopJobRecord(
        job_id="finalize",
        kind="resolve_chapter_checkpoint_finalize",
        label="归档执行 · demo / 第 79 章",
    )

    assert _display_job_label(write_job) == "方案执行（阶段 1/2） · demo / 第 79 章"
    assert _display_job_label(finalize_job) == "归档执行（阶段 2/2） · demo / 第 79 章"


def test_needs_decision_success_card_keeps_checkpoint_semantics(
    qapp: QApplication,
) -> None:
    job = DesktopJobRecord(
        job_id="needs-decision",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 2 章",
        status=DesktopJobState.SUCCEEDED,
        current_step="completed",
        result={
            "chapter_number": 2,
            "status": "needs_decision",
            "word_count": 5918,
            "overall_score": 6.8,
            "checkpoint": {
                "checkpoint_id": "plan-002",
                "checkpoint_type": "plan_checkpoint",
            },
        },
        events=[
            DesktopJobEvent(at="2026-05-14T13:30:00+00:00", step="draft", payload={}),
            DesktopJobEvent(at="2026-05-14T13:35:00+00:00", step="alignment", payload={}),
            DesktopJobEvent(
                at="2026-05-14T13:40:00+00:00",
                step="consistency_replan",
                payload={},
            ),
            DesktopJobEvent(
                at="2026-05-14T13:40:56+00:00",
                step="plan_checkpoint",
                payload={},
            ),
        ],
    )

    card = JobCard(job)
    card.show()
    qapp.processEvents()

    badge_texts = [
        label.text() for label in card.findChildren(QLabel) if label.objectName() == "badge"
    ]
    assert "已重新规划" in badge_texts
    assert "已完成" not in badge_texts
    assert _compute_card_progress(job) == 7

    indicator = card.findChildren(StepIndicatorRow)[0]
    dot_states = [dot.property("state") for dot in indicator._dot_labels]
    assert dot_states[0] == "done"
    assert set(dot_states[1:]) == {"pending"}

    detail_texts = [
        label.text()
        for label in card.findChildren(QLabel)
        if label.objectName() in {"cardHint", "cardMeta", "progressDetail"}
    ]
    assert any("已生成重规划方案，等待确认后再写正文" in text for text in detail_texts)
    assert any("草稿指标" in text and "5,918 字" in text for text in detail_texts)


def test_plain_plan_checkpoint_card_does_not_claim_replan(
    qapp: QApplication,
) -> None:
    job = DesktopJobRecord(
        job_id="plain-plan-checkpoint",
        kind="prepare_chapter",
        label="章节方案 · demo / 第 9 章",
        status=DesktopJobState.SUCCEEDED,
        current_step="completed",
        result={
            "chapter_number": 9,
            "status": "needs_decision",
            "checkpoint": {
                "checkpoint_id": "plan-009",
                "checkpoint_type": "plan_checkpoint",
                "prompt": "章节方案已备好。先确认方案，再决定是否继续写作。",
            },
        },
        events=[
            DesktopJobEvent(at="2026-05-18T03:27:30+00:00", step="state_packet", payload={}),
            DesktopJobEvent(at="2026-05-18T03:30:25+00:00", step="plan_checkpoint", payload={}),
        ],
    )

    card = JobCard(job)
    card.show()
    qapp.processEvents()

    badge_texts = [
        label.text() for label in card.findChildren(QLabel) if label.objectName() == "badge"
    ]
    assert "方案已生成" in badge_texts
    assert "已重新规划" not in badge_texts

    detail_texts = [
        label.text()
        for label in card.findChildren(QLabel)
        if label.objectName() in {"cardHint", "cardMeta", "progressDetail"}
    ]
    assert any("已生成章节方案，等待确认后再写正文" in text for text in detail_texts)


def test_replanned_card_shows_compact_reason_tag(
    qapp: QApplication,
) -> None:
    reason = "章节 1 字数 1500/4500 处于 hard_reject 区间，结构性重整未生成可接受候选稿。"
    job = DesktopJobRecord(
        job_id="needs-decision-word-count",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 1 章",
        status=DesktopJobState.SUCCEEDED,
        current_step="completed",
        result={
            "chapter_number": 1,
            "status": "needs_decision",
            "checkpoint": {
                "checkpoint_id": "plan-001",
                "checkpoint_type": "plan_checkpoint",
            },
        },
        events=[
            DesktopJobEvent(
                at="2026-05-14T13:40:00+00:00",
                step="consistency_replan",
                payload={"violations": [reason]},
            ),
            DesktopJobEvent(
                at="2026-05-14T13:40:56+00:00",
                step="plan_checkpoint",
                payload={},
            ),
        ],
    )

    card = JobCard(job)
    card.show()
    qapp.processEvents()

    badges = [label for label in card.findChildren(QLabel) if label.objectName() == "badge"]
    badge_texts = [label.text() for label in badges]

    assert "字数不足" in badge_texts
    assert "已重新规划" in badge_texts
    assert reason in next(label.toolTip() for label in badges if label.text() == "字数不足")


def test_replanned_card_infers_reason_tag_from_word_count_gate_event(
    qapp: QApplication,
) -> None:
    job = DesktopJobRecord(
        job_id="needs-decision-legacy-word-count",
        kind="resolve_chapter_checkpoint",
        label="方案执行 · demo / 第 1 章",
        status=DesktopJobState.SUCCEEDED,
        current_step="completed",
        result={
            "chapter_number": 1,
            "status": "needs_decision",
            "checkpoint": {
                "checkpoint_id": "plan-001",
                "checkpoint_type": "plan_checkpoint",
            },
        },
        events=[
            DesktopJobEvent(
                at="2026-05-14T13:39:59+00:00",
                step="word_count_archive_gate",
                payload={"chapter": 1, "reason": "candidate_still_outside_buffer"},
            ),
            DesktopJobEvent(
                at="2026-05-14T13:40:00+00:00",
                step="consistency_replan",
                payload={"chapter": 1},
            ),
            DesktopJobEvent(
                at="2026-05-14T13:40:56+00:00",
                step="plan_checkpoint",
                payload={},
            ),
        ],
    )

    card = JobCard(job)
    card.show()
    qapp.processEvents()

    badge_texts = [
        label.text() for label in card.findChildren(QLabel) if label.objectName() == "badge"
    ]

    assert "字数闸门" in badge_texts
    assert "已重新规划" in badge_texts
