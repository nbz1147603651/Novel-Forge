"""Layout regression tests for workflow form widgets."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import novel_forge.desktop.pages.workflow.forms as workflow_forms  # noqa: E402
import novel_forge.desktop.pages.workflow.presets as workflow_presets  # noqa: E402
import novel_forge.desktop.preset_manager as preset_manager  # noqa: E402
from novel_forge.desktop.ai_generate import (  # noqa: E402
    AI_CREATIVE_NOTE_FIELD,
    AI_POLISH_SUGGESTIONS_FIELD,
)
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState  # noqa: E402
from novel_forge.desktop.pages.workflow.forms import (  # noqa: E402
    BlueprintElementPreferencePanel,
    LongInitForm,
    ShortForm,
)
from novel_forge.desktop.pages.workflow.page import WorkflowPage  # noqa: E402
from novel_forge.desktop.pages.workflow.presets import (  # noqa: E402
    PresetToolbar,
    _AiHintDialog,
    _AiPolishDialog,
    _compute_field_diffs,
    _DiffDialog,
)
from novel_forge.desktop.widgets import CollapsibleSection, ScrollPage  # noqa: E402
from novel_forge.persistence.models import ProjectLayout  # noqa: E402
from novel_forge.workspace.contracts import InitLongRequest, RunShortRequest  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _build_panel(width: int, qapp: QApplication) -> BlueprintElementPreferencePanel:
    panel = BlueprintElementPreferencePanel("long")
    panel.resize(width, 760)
    panel.show()
    qapp.processEvents()
    panel._update_top_controls_layout(force=True)
    panel._update_row_width_constraints(force=True)
    panel._update_density_profile(force=True)
    return panel


def _dispose(widget: BlueprintElementPreferencePanel, qapp: QApplication) -> None:
    widget.hide()
    widget.deleteLater()
    qapp.processEvents()


def _buttons_by_text(widget: QWidget) -> dict[str, QPushButton]:
    return {button.text(): button for button in widget.findChildren(QPushButton)}


def test_blueprint_panel_top_controls_stack_on_very_narrow_width(qapp: QApplication) -> None:
    panel = _build_panel(720, qapp)
    try:
        assert panel._top_controls_layout_mode == "stack"
        assert panel._top_grid.itemAtPosition(0, 0).widget() is panel._preset_combo
        assert panel._top_grid.itemAtPosition(1, 0).widget() is panel._apply_btn
        assert panel._top_grid.itemAtPosition(2, 0).widget() is panel._apply_suggested_btn
        assert panel._top_grid.itemAtPosition(3, 0).widget() is panel._reset_btn
    finally:
        _dispose(panel, qapp)


def test_blueprint_panel_top_controls_wrap_on_medium_width(qapp: QApplication) -> None:
    panel = _build_panel(980, qapp)
    try:
        assert panel._top_controls_layout_mode == "wrap"
        assert panel._top_grid.itemAtPosition(0, 0).widget() is panel._preset_combo
        assert panel._top_grid.itemAtPosition(1, 0).widget() is panel._apply_btn
        assert panel._top_grid.itemAtPosition(1, 1).widget() is panel._apply_suggested_btn
        assert panel._top_grid.itemAtPosition(2, 0).widget() is panel._reset_btn
    finally:
        _dispose(panel, qapp)


def test_blueprint_panel_rows_do_not_force_horizontal_scroll(qapp: QApplication) -> None:
    panel = _build_panel(700, qapp)
    try:
        first_row = next(iter(panel._rows.values()))
        assert first_row["widget"].minimumWidth() == 0
        assert first_row["weight"].minimumWidth() == 112
        assert first_row["weight"].__class__.__name__ == "_NoWheelSlider"
        assert panel._scroll_content is not None
        assert panel._scroll_content.minimumWidth() == 0
        assert panel._density_profile == "compact"
        scroll = panel._scroll_content.parentWidget().parentWidget()
        assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    finally:
        _dispose(panel, qapp)


def test_blueprint_panel_slider_min_width_scales_down_for_smaller_width(qapp: QApplication) -> None:
    panel = _build_panel(620, qapp)
    try:
        first_row = next(iter(panel._rows.values()))
        assert first_row["widget"].minimumWidth() == 0
        assert first_row["weight"].minimumWidth() == 99
    finally:
        _dispose(panel, qapp)


def test_workflow_forms_keep_compact_minimum_width(qapp: QApplication) -> None:
    short_form = ShortForm()
    long_form = LongInitForm()
    try:
        short_form.show()
        long_form.show()
        qapp.processEvents()

        assert short_form.minimumSizeHint().width() <= 720
        assert long_form.minimumSizeHint().width() <= 720
    finally:
        short_form.hide()
        short_form.deleteLater()
        long_form.hide()
        long_form.deleteLater()
        qapp.processEvents()


def test_short_form_round_trips_chapter_research_fields(qapp: QApplication) -> None:
    form = ShortForm()
    captured: list[RunShortRequest] = []
    try:
        form._theme.setPlainText("雨夜急诊室")
        form._short_research_enabled.setChecked(True)
        form._short_research_provider.setCurrentIndex(
            form._short_research_provider.findData("mcp_search")
        )
        form._short_research_query_hint.setPlainText("当代急诊分诊流程")
        form.submitted.connect(captured.append)

        collected = form._collect()
        form._short_research_enabled.setChecked(False)
        form._fill(collected)
        form._submit()

        assert captured
        request = captured[0]
        assert request.research_enabled is True
        assert request.research_provider == "mcp_search"
        assert request.research_query_hint == "当代急诊分诊流程"
    finally:
        form.deleteLater()
        qapp.processEvents()


def test_long_init_form_uses_fixed_blueprint_architecture(qapp: QApplication) -> None:
    form = LongInitForm()
    captured: list[InitLongRequest] = []
    try:
        form._premise.setPlainText("一座城的记忆开始倒流")
        form.submitted.connect(captured.append)

        form._submit()
        qapp.processEvents()

        assert captured
        assert not hasattr(form, "_blueprint_mode")
        assert not hasattr(captured[0], "init_blueprint_mode")
    finally:
        form.hide()
        form.deleteLater()
        qapp.processEvents()


def test_workflow_page_places_long_form_above_task_flow(qapp: QApplication) -> None:
    page = WorkflowPage()
    try:
        widgets = [
            page.body_layout.itemAt(index).widget()
            for index in range(page.body_layout.count())
            if page.body_layout.itemAt(index).widget() is not None
        ]

        assert widgets.index(page._mode_stack) < widgets.index(page._jobs_panel)
        assert page._mode_stack.indexOf(page._short_form) >= 0
        assert page._mode_stack.indexOf(page._long_panel) >= 0
        assert not hasattr(page._long_panel._init_form, "_blueprint_mode")
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_workflow_page_mode_switch_uses_stacked_forms(qapp: QApplication) -> None:
    page = WorkflowPage()
    try:
        page._mode_bar.select("long")
        qapp.processEvents()
        assert page.current_mode() == "long"
        assert page._mode_stack.currentWidget() is page._long_panel

        page._mode_bar.select("short")
        qapp.processEvents()
        assert page.current_mode() == "short"
        assert page._mode_stack.currentWidget() is page._short_form
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_workflow_task_flow_preferred_width_tracks_current_form(
    qapp: QApplication,
) -> None:
    page = WorkflowPage()
    try:
        page._mode_bar.select("short")
        qapp.processEvents()
        short_width = page._jobs_panel.sizeHint().width()
        assert 700 <= short_width <= page._short_form.sizeHint().width()

        page._mode_bar.select("long")
        qapp.processEvents()
        long_width = page._jobs_panel.sizeHint().width()
        assert short_width <= long_width <= page._long_panel.sizeHint().width()
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_workflow_long_init_toolbar_does_not_stretch_vertically(qapp: QApplication) -> None:
    page = WorkflowPage()
    try:
        page.resize(1600, 900)
        page.show()
        page._mode_bar.select("long")
        qapp.processEvents()

        toolbar = page._long_panel._init_form._toolbar
        assert toolbar.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
        assert page._mode_stack.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
        assert page._long_panel._stack.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
        assert toolbar.height() <= toolbar.sizeHint().height() + 8
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_workflow_mode_stack_shrinks_after_long_form_section_collapse(
    qapp: QApplication,
) -> None:
    page = WorkflowPage()
    try:
        page.resize(1600, 900)
        page.show()
        page._mode_bar.select("long")
        qapp.processEvents()

        form = page._long_panel._init_form
        section = form._blueprint_preferences_section
        section.set_expanded(True)
        qapp.processEvents()
        QTest.qWait(260)
        qapp.processEvents()
        expanded_height = page._mode_stack.sizeHint().height()

        section.set_expanded(False)
        qapp.processEvents()
        QTest.qWait(260)
        qapp.processEvents()

        collapsed_height = page._mode_stack.sizeHint().height()
        assert collapsed_height < expanded_height
        assert collapsed_height == form.sizeHint().height()
        assert page._long_panel._stack.sizeHint().height() == form.sizeHint().height()
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_workflow_task_flow_stays_close_after_dynamic_height_shrink(
    qapp: QApplication,
) -> None:
    page = WorkflowPage()
    try:
        page.resize(1600, 900)
        page.show()
        page._mode_bar.select("long")
        qapp.processEvents()
        page.sync_body_height()
        qapp.processEvents()

        form = page._long_panel._init_form
        for section in (
            form._resume_panel,
            form._core_field_section,
            form._advanced_field_section,
            form._blueprint_preferences_section,
        ):
            section.hide()

        page.bind_jobs(
            [
                DesktopJobRecord(
                    job_id="init-running",
                    kind="init_long",
                    label="长篇立项 · 魂玉",
                    project_id="soul_jade",
                    status=DesktopJobState.RUNNING,
                    current_step="init_story_bible",
                )
            ]
        )
        qapp.processEvents()

        mode_stack_bottom = page._mode_stack.geometry().bottom()
        task_flow_top = page._jobs_panel.geometry().top()

        assert page.widget().height() <= page.widget().maximumHeight()
        assert task_flow_top - mode_stack_bottom <= 120
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_workflow_task_flow_reflows_after_blueprint_section_collapse(
    qapp: QApplication,
) -> None:
    page = WorkflowPage()
    try:
        page.resize(1600, 1000)
        page.show()
        page._mode_bar.select("long")
        page.bind_jobs(
            [
                DesktopJobRecord(
                    job_id="init-failed",
                    kind="init_long",
                    label="长篇立项 · 魂玉",
                    project_id="soul_jade",
                    status=DesktopJobState.FAILED,
                    current_step="init_readiness",
                )
            ]
        )
        qapp.processEvents()
        page.sync_body_height()
        qapp.processEvents()

        section = page._long_panel._init_form._blueprint_preferences_section
        section.set_expanded(True)
        qapp.processEvents()
        QTest.qWait(260)
        section.set_expanded(False)
        qapp.processEvents()
        QTest.qWait(260)
        qapp.processEvents()

        mode_stack_bottom = page._mode_stack.geometry().bottom()
        task_flow_top = page._jobs_panel.geometry().top()

        assert page.widget().height() <= page.widget().maximumHeight()
        assert abs(page._mode_stack.height() - page._mode_stack.sizeHint().height()) <= 24
        assert task_flow_top - mode_stack_bottom <= 80
    finally:
        page.shutdown()
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_blueprint_preference_supports_whole_panel_collapse(qapp: QApplication) -> None:
    short_form = ShortForm()
    long_form = LongInitForm()
    try:
        short_form.show()
        long_form.show()
        qapp.processEvents()

        assert not short_form._blueprint_preferences.is_built
        assert not long_form._blueprint_preferences.is_built

        short_form._blueprint_preferences_section.set_expanded(False)
        long_form._blueprint_preferences_section.set_expanded(False)
        qapp.processEvents()
        QTest.qWait(260)
        qapp.processEvents()
        assert not short_form._blueprint_preferences_section._body_frame.isVisible()
        assert not long_form._blueprint_preferences_section._body_frame.isVisible()

        short_form._blueprint_preferences_section.set_expanded(True)
        long_form._blueprint_preferences_section.set_expanded(True)
        qapp.processEvents()
        QTest.qWait(260)
        qapp.processEvents()
        assert short_form._blueprint_preferences_section._body_frame.isVisible()
        assert long_form._blueprint_preferences_section._body_frame.isVisible()
        assert short_form._blueprint_preferences.is_built
        assert long_form._blueprint_preferences.is_built
    finally:
        short_form.hide()
        short_form.deleteLater()
        long_form.hide()
        long_form.deleteLater()
        qapp.processEvents()


def test_deferred_blueprint_preferences_preserve_payload_before_expansion(
    qapp: QApplication,
) -> None:
    form = ShortForm()
    payload = {
        "preset_id": "",
        "manual_override": True,
        "items": [],
    }
    try:
        form._fill({"blueprint_element_preferences": payload})

        assert not form._blueprint_preferences.is_built
        assert form._collect()["blueprint_element_preferences"] == payload

        form._blueprint_preferences_section.set_expanded(True)
        qapp.processEvents()

        assert form._blueprint_preferences.is_built
        assert form._collect()["blueprint_element_preferences"] == payload
    finally:
        form.deleteLater()
        qapp.processEvents()


def test_blueprint_preference_animation_locks_height_during_toggle(qapp: QApplication) -> None:
    form = LongInitForm()
    try:
        form.show()
        qapp.processEvents()

        section = form._blueprint_preferences_section
        section.set_expanded(False)
        qapp.processEvents()

        if section._animations_enabled:
            assert section._height_animation is not None
            assert section._body_frame.minimumHeight() == section._body_frame.maximumHeight()
            QTest.qWait(260)
            qapp.processEvents()
        else:
            assert section._height_animation is None
            assert section._body_frame.maximumHeight() == 0
        assert not section._body_frame.isVisible()

        section.set_expanded(True)
        qapp.processEvents()

        if section._animations_enabled:
            assert section._height_animation is not None
            assert section._body_frame.minimumHeight() == section._body_frame.maximumHeight()
            QTest.qWait(260)
            qapp.processEvents()
        else:
            assert section._height_animation is None
        assert section._body_frame.isVisible()
        assert section._body_frame.minimumHeight() == 0
        assert section._body_frame.maximumHeight() == 16777215
    finally:
        form.hide()
        form.deleteLater()
        qapp.processEvents()


def test_collapsible_section_title_stays_stable_while_icon_changes(qapp: QApplication) -> None:
    section = CollapsibleSection("叙事要素偏好", expanded=False)
    try:
        section.show()
        qapp.processEvents()

        collapsed_icon_key = section._toggle.icon().cacheKey()
        collapsed_width = section._toggle.sizeHint().width()
        assert section._toggle.text() == "叙事要素偏好"
        assert collapsed_icon_key != 0

        section.set_expanded(True)
        qapp.processEvents()

        expanded_icon_key = section._toggle.icon().cacheKey()
        expanded_width = section._toggle.sizeHint().width()
        assert section._toggle.text() == "叙事要素偏好"
        assert expanded_icon_key != 0
        assert expanded_icon_key != collapsed_icon_key
        assert expanded_width == collapsed_width

        section.set_expanded(False)
        qapp.processEvents()
        assert section._toggle.text() == "叙事要素偏好"
    finally:
        section.hide()
        section.deleteLater()
        qapp.processEvents()


def test_collapsible_section_preserves_outer_scroll_anchor_during_toggle(
    qapp: QApplication,
) -> None:
    page = ScrollPage()
    page.resize(920, 600)
    try:
        for idx in range(8):
            filler = QLabel(f"top {idx}")
            filler.setFixedHeight(84)
            page.body_layout.addWidget(filler)

        section = CollapsibleSection("叙事要素偏好", expanded=False)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        for idx in range(20):
            row = QLabel(f"row {idx}")
            row.setFixedHeight(40)
            content_layout.addWidget(row)
        section.body_layout.addWidget(content)
        page.body_layout.addWidget(section)

        for idx in range(10):
            filler = QLabel(f"bottom {idx}")
            filler.setFixedHeight(100)
            page.body_layout.addWidget(filler)

        page.show()
        qapp.processEvents()
        bar = page.verticalScrollBar()
        bar.setValue(320)
        qapp.processEvents()
        anchor = bar.value()

        section.set_expanded(True)
        qapp.processEvents()
        QTest.qWait(80)
        qapp.processEvents()
        assert bar.value() == anchor

        QTest.qWait(220)
        qapp.processEvents()
        assert bar.value() == anchor
    finally:
        page.hide()
        page.deleteLater()
        qapp.processEvents()


def test_story_and_advanced_fields_render_as_field_cards(qapp: QApplication) -> None:
    short_form = ShortForm()
    long_form = LongInitForm()
    try:
        short_form.show()
        long_form.show()
        qapp.processEvents()

        assert set(short_form._core_field_section.cards) == {
            "theme",
            "characters_hint",
            "world_hint",
            "conflict_hint",
        }
        assert set(long_form._core_field_section.cards) == {
            "premise",
            "characters_hint",
            "world_hint",
            "conflict_hint",
        }
        assert set(short_form._advanced_field_section.cards) == {
            "title_language",
            "pov_hint",
            "opening_style",
            "ending_style",
            "extra_instructions",
            "project_id",
        }
        assert set(long_form._advanced_field_section.cards) == {
            "title_language",
            "pov_hint",
            "opening_style",
            "ending_style",
            "extra_instructions",
            "project_id",
        }

        assert "故事主题 *" in short_form._core_field_section.cards["theme"].text()
        assert "故事前提 *" in long_form._core_field_section.cards["premise"].text()
        assert "开篇方式" in short_form._advanced_field_section.cards["opening_style"].text()
        assert "结尾方式" in long_form._advanced_field_section.cards["ending_style"].text()
    finally:
        short_form.hide()
        short_form.deleteLater()
        long_form.hide()
        long_form.deleteLater()
        qapp.processEvents()


def test_long_init_research_uses_settings_default_and_submit(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
    tmp_path,
) -> None:
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")
    monkeypatch.setattr(
        workflow_forms,
        "get_settings",
        lambda: SimpleNamespace(research_enabled=True),
    )
    form = LongInitForm()
    try:
        form._premise.setPlainText("一名记者调查旧城档案")
        assert form._planning_commitment.currentData() == "full"
        assert form._research_enabled.isChecked() is True
        assert form._research_provider.currentData() == "auto"
        assert form._research_provider.parentWidget() is not None

        form._research_provider.setCurrentIndex(form._research_provider.findData("brave"))
        form._research_query_hint.setPlainText("城市更新 档案制度")

        preset_manager.save_preset("long", "旧城档案", form._collect())
        saved = preset_manager.load_preset("long", "旧城档案")
        assert saved["research_enabled"] is True
        assert saved["research_provider"] == "brave"
        assert saved["research_query_hint"] == "城市更新 档案制度"

        form._research_enabled.setChecked(False)
        form._research_provider.setCurrentIndex(form._research_provider.findData("auto"))
        form._research_query_hint.clear()
        form._fill(saved)

        request = form._build_submit_request()

        assert request.research_enabled is True
        assert request.research_provider == "brave"
        assert request.research_query_hint == "城市更新 档案制度"
        assert request.planning_commitment == "full"
        form._fill({**saved, "planning_commitment": "progressive"})
        assert form._build_submit_request().planning_commitment == "progressive"
    finally:
        form.deleteLater()
        qapp.processEvents()


def test_long_init_research_default_refreshes_before_submit(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    state = SimpleNamespace(research_enabled=False)
    monkeypatch.setattr(workflow_forms, "get_settings", lambda: state)

    form = LongInitForm()
    try:
        assert form._research_enabled.isChecked() is False

        state.research_enabled = True
        form._premise.setPlainText("一名声音治疗师回到山谷小镇")
        request = form._build_submit_request()

        assert form._research_enabled.isChecked() is True
        assert request.research_enabled is True
    finally:
        form.deleteLater()
        qapp.processEvents()


def test_long_init_research_default_refresh_preserves_user_override(
    monkeypatch: pytest.MonkeyPatch,
    qapp: QApplication,
) -> None:
    state = SimpleNamespace(research_enabled=True)
    monkeypatch.setattr(workflow_forms, "get_settings", lambda: state)

    form = LongInitForm()
    try:
        assert form._research_enabled.isChecked() is True

        form._research_enabled.setChecked(False)
        form._premise.setPlainText("一名记者调查旧城档案")
        request = form._build_submit_request()

        assert form._research_enabled.isChecked() is False
        assert request.research_enabled is False
    finally:
        form.deleteLater()
        qapp.processEvents()


def test_blueprint_preferences_sit_below_advanced_field_cards(qapp: QApplication) -> None:
    short_form = ShortForm()
    long_form = LongInitForm()
    try:
        short_layout = short_form.layout()
        long_layout = long_form.layout()
        assert short_layout is not None
        assert long_layout is not None

        assert short_layout.indexOf(short_form._core_field_section) < short_layout.indexOf(
            short_form._advanced_field_section
        )
        assert short_layout.indexOf(short_form._advanced_field_section) < short_layout.indexOf(
            short_form._blueprint_preferences_section
        )
        assert long_layout.indexOf(long_form._core_field_section) < long_layout.indexOf(
            long_form._advanced_field_section
        )
        assert long_layout.indexOf(long_form._advanced_field_section) < long_layout.indexOf(
            long_form._blueprint_preferences_section
        )
    finally:
        short_form.hide()
        short_form.deleteLater()
        long_form.hide()
        long_form.deleteLater()
        qapp.processEvents()


def test_field_dialog_applies_and_cancels_text_changes(qapp: QApplication) -> None:
    form = LongInitForm()
    try:
        form._premise.setPlainText("旧前提")
        dialog = form._make_field_editor_dialog(form._core_fields, "premise", "核心梗概")
        editor = dialog._editors["premise"]
        assert isinstance(editor, QTextEdit)
        editor.setPlainText("新前提")
        dialog.reject()
        assert form._premise.toPlainText() == "旧前提"

        dialog = form._make_field_editor_dialog(form._core_fields, "premise", "核心梗概")
        editor = dialog._editors["premise"]
        assert isinstance(editor, QTextEdit)
        editor.setPlainText("新前提")
        dialog._apply_changes()
        qapp.processEvents()

        assert form._premise.toPlainText() == "新前提"
        assert "新前提" in form._core_field_section.cards["premise"].text()
    finally:
        form.hide()
        form.deleteLater()
        qapp.processEvents()


def test_advanced_field_dialog_applies_title_language_and_project_id(
    qapp: QApplication,
) -> None:
    form = LongInitForm()
    try:
        dialog = form._make_field_editor_dialog(
            form._advanced_fields,
            "title_language",
            "高级设定",
        )
        title_language_editor = dialog._editors["title_language"]
        project_editor = dialog._editors["project_id"]
        assert isinstance(title_language_editor, tuple)
        assert isinstance(project_editor, QLineEdit)
        title_input, language_input = title_language_editor
        title_input.setText("新书名")
        language_input.setCurrentIndex(language_input.findData("en"))
        project_editor.setText("demo_project")

        dialog._apply_changes()
        qapp.processEvents()

        assert form._title.text() == "新书名"
        assert form._language.currentData() == "en"
        assert form._project_id.text() == "demo_project"
        assert "新书名" in form._advanced_field_section.cards["title_language"].text()
        assert "demo_project" in form._advanced_field_section.cards["project_id"].text()
    finally:
        form.hide()
        form.deleteLater()
        qapp.processEvents()


def test_long_init_autorun_uses_stored_request_when_resuming_failed_job(
    tmp_path,
    qapp: QApplication,
) -> None:
    layout = ProjectLayout(tmp_path / "long_demo")
    layout.ensure_dirs()
    layout.init_request_meta_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_fingerprint": "stored",
                "request": {
                    "schema_version": 1,
                    "init_input": {
                        "premise": "原始前提",
                        "genre": "romance",
                        "tone": "warm",
                        "title": "旧标题",
                        "language": "zh",
                        "characters_hint": "原始人物",
                        "world_hint": "原始世界",
                        "conflict_hint": "原始冲突",
                        "pov_hint": "原始视角",
                        "opening_style": "原始开篇",
                        "ending_style": "原始结尾",
                        "extra_instructions": "原始额外指令",
                    },
                    "generation_options": {
                        "total_chapters": 52,
                        "words_per_chapter": 4500,
                        "volume_mode_setting": "off",
                        "chapters_per_volume_setting": 0,
                        "effective_volume_mode": False,
                        "effective_chapters_per_volume": 20,
                        "blueprint_element_preferences": {
                            "preset_id": "romance",
                            "manual_override": False,
                            "items": [],
                        },
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    form = LongInitForm()
    captured: list[InitLongRequest] = []
    try:
        form.set_storage_root(tmp_path)
        form._project_id.setText("long_demo")
        form._premise.setPlainText("失败后由项目详情回填的新前提")
        form._genre.setText("sci-fi")
        form.update_progress(
            DesktopJobRecord(
                job_id="failed-init",
                kind="init_long",
                label="长篇立项 · long_demo",
                project_id="long_demo",
                status=DesktopJobState.FAILED,
                current_step="plan_chapter_contracts",
            )
        )
        form.init_long_autorun_requested.connect(captured.append)

        form._submit_autorun()
        qapp.processEvents()

        assert len(captured) == 1
        request = captured[0]
        assert request.project_id == "long_demo"
        assert request.premise == "原始前提"
        assert request.genre == "romance"
        assert request.total_chapters == 52
        assert request.words_per_chapter == 4500
        # 长篇 WAVE 单次连贯起稿：max_edit_rounds 字段已下线，仅用于向后兼容残留
        assert "max_edit_rounds" not in type(request).model_fields
        assert not hasattr(request, "init_blueprint_mode")
        assert request.blueprint_element_preferences["preset_id"] == "romance"
    finally:
        form.hide()
        form.deleteLater()
        qapp.processEvents()


def test_restart_init_clears_full_init_artifact_tree(
    tmp_path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import novel_forge.desktop.pages.workflow.forms as workflow_forms

    project_id = "long_demo"
    layout = ProjectLayout(tmp_path / project_id)
    layout.ensure_dirs()
    for path in (
        layout.spec_path,
        layout.bible_path,
        layout.characters_path,
        layout.outline_path,
        layout.style_profile_path,
        layout.narrative_contract_path,
        layout.states_dir / "init_v2" / "character_system.json",
        layout.states_dir / "task_flow_history.json",
        layout.narrative_state_dir / "entity_registry.json",
        layout.memory_dir / "project_memory.json",
        layout.memory_dir / "init_coherence_claim_ledger.json",
        layout.memory_dir / "init_coherence_claim_batches" / "stale.json",
        layout.reports_dir / "init_readiness.json",
        layout.root
        / "initialization"
        / "fragments"
        / "coherence_profile"
        / "payoff.checkpoint.json",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    log_path = layout.logs_dir / "previous_run" / "summary.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("{}", encoding="utf-8")

    form = LongInitForm()
    cleanup_requests: list[str] = []
    try:
        form.set_storage_root(tmp_path)
        form.restart_task_flow_cleanup_requested.connect(cleanup_requests.append)
        monkeypatch.setattr(
            form,
            "_active_resume_detail",
            lambda: SimpleNamespace(project_id=project_id, title="旧项目"),
        )
        monkeypatch.setattr(
            workflow_forms,
            "ask_confirmation_with_checkbox",
            lambda *args, **kwargs: SimpleNamespace(confirmed=True, checkbox_checked=False),
        )
        monkeypatch.setattr(form, "_refresh_resume_feedback", lambda: None)

        form._on_force_restart_init()
        qapp.processEvents()

        assert not layout.spec_path.exists()
        assert not layout.style_profile_path.exists()
        assert not layout.narrative_contract_path.exists()
        assert not (layout.states_dir / "init_v2" / "character_system.json").exists()
        assert not (layout.states_dir / "task_flow_history.json").exists()
        assert not (layout.narrative_state_dir / "entity_registry.json").exists()
        assert not (layout.memory_dir / "project_memory.json").exists()
        assert not (layout.memory_dir / "init_coherence_claim_ledger.json").exists()
        assert not (layout.memory_dir / "init_coherence_claim_batches" / "stale.json").exists()
        assert not (layout.reports_dir / "init_readiness.json").exists()
        assert not (
            layout.root
            / "initialization"
            / "fragments"
            / "coherence_profile"
            / "payoff.checkpoint.json"
        ).exists()
        assert layout.logs_dir.exists()
        assert not log_path.exists()
        assert cleanup_requests == [project_id]
    finally:
        form.hide()
        form.deleteLater()
        qapp.processEvents()


def test_restart_init_uses_failed_job_when_snapshot_has_no_resume_detail(
    tmp_path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale/misclassified snapshot must not turn restart into a no-op."""
    import novel_forge.desktop.pages.workflow.forms as workflow_forms

    project_id = "early_init_demo"
    layout = ProjectLayout(tmp_path / project_id)
    layout.ensure_dirs()
    layout.spec_path.write_text("{}", encoding="utf-8")

    form = LongInitForm()
    cleanup_requests: list[str] = []
    try:
        form.set_storage_root(tmp_path)
        form._project_id.setText(project_id)
        form._premise.setPlainText("当前表单前提")
        form.update_progress(
            DesktopJobRecord(
                job_id="failed-init",
                kind="init_long",
                label="长篇立项 · early_init_demo",
                project_id=project_id,
                status=DesktopJobState.FAILED,
                current_step="init_story_bible",
            )
        )
        form.restart_task_flow_cleanup_requested.connect(cleanup_requests.append)
        monkeypatch.setattr(form, "_active_resume_detail", lambda: None)
        monkeypatch.setattr(
            workflow_forms,
            "ask_confirmation_with_checkbox",
            lambda *args, **kwargs: SimpleNamespace(confirmed=True, checkbox_checked=False),
        )
        monkeypatch.setattr(form, "_refresh_resume_feedback", lambda: None)

        form._restart_btn.click()
        qapp.processEvents()

        assert cleanup_requests == [project_id]
        assert not layout.spec_path.exists()
        assert form._current_job is None
    finally:
        form.hide()
        form.deleteLater()
        qapp.processEvents()


def test_ai_polish_dialog_exposes_core_story_focus_fields(
    qapp: QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")
    dialog = _AiPolishDialog("long", suggestions=[], parent=None)
    try:
        dialog.show()
        qapp.processEvents()

        labels = {checkbox.text() for checkbox in dialog.findChildren(QCheckBox)}
        buttons = _buttons_by_text(dialog)
        expected = {
            "故事前提",
            "标题",
            "主角群提示",
            "世界观 / 时代背景",
            "主冲突提示",
            "叙事视角",
            "开篇方式",
            "结尾方式",
            "额外创作指令",
            "大纲润色提示",
        }
        assert expected <= labels
        assert {"全选字段", "清空字段"} <= set(buttons)
        assert dialog.get_focus_fields() == [
            "premise",
            "title",
            "characters_hint",
            "world_hint",
            "conflict_hint",
            "pov_hint",
            "opening_style",
            "ending_style",
            "extra_instructions",
            "polish_hint",
        ]
        buttons["清空字段"].click()
        assert dialog.get_focus_fields() == []
        buttons["全选字段"].click()
        assert dialog.get_focus_fields()[0] == "premise"
    finally:
        dialog.hide()
        dialog.deleteLater()
        qapp.processEvents()


def test_ai_generate_dialog_exposes_preview_modes_and_creative_controls(
    qapp: QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")
    dialog = _AiHintDialog("long", parent=None)
    try:
        dialog.show()
        qapp.processEvents()

        buttons = _buttons_by_text(dialog)
        combo_values = {
            combo.itemData(index)
            for combo in dialog.findChildren(QComboBox)
            for index in range(combo.count())
        }

        assert "生成并预览" in buttons
        assert {"replace", "fill_blanks", "variant"} <= combo_values
        assert {"balanced", "high_concept", "commercial", "literary", "emotional"} <= combo_values
        assert dialog.get_generation_mode() == "replace"

        dialog._generation_mode.setCurrentIndex(dialog._generation_mode.findData("fill_blanks"))
        dialog._creative_style.setCurrentIndex(dialog._creative_style.findData("high_concept"))
        dialog._novelty.setCurrentIndex(dialog._novelty.findData("bold"))
        dialog._creative_brief.setText("更海派但不换主冲突")
        assert dialog.get_generation_mode() == "fill_blanks"
        profile = dialog.get_creative_profile()
        assert profile["style"] == "high_concept"
        assert profile["novelty"] == "bold"
        assert profile["custom_brief"] == "更海派但不换主冲突"
    finally:
        dialog.hide()
        dialog.deleteLater()
        qapp.processEvents()


def test_ai_generate_dialog_autosaves_and_restores_input_draft(
    qapp: QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")
    dialog = _AiHintDialog("long", parent=None)
    try:
        dialog._text_edit.setPlainText("当代都市中的关系谜题")
        dialog._generation_mode.setCurrentIndex(dialog._generation_mode.findData("variant"))
        dialog._creative_style.setCurrentIndex(dialog._creative_style.findData("emotional"))
        dialog._creative_brief.setText("更宿命感")
        dialog._genre.setText("悬疑言情")
        dialog._total_chapters.setValue(36)
        qapp.processEvents()

        draft = preset_manager.load_ai_input_draft(
            "long",
            workflow_presets.AI_GENERATE_DRAFT_KEY,
        )

        assert draft["hint"] == "当代都市中的关系谜题"
        assert draft["generation_mode"] == "variant"
        assert draft["creative_profile"]["style"] == "emotional"
        assert draft["total_chapters"] == 36
    finally:
        dialog.hide()
        dialog.deleteLater()
        qapp.processEvents()

    restored = _AiHintDialog("long", parent=None)
    try:
        assert restored.get_hint() == "当代都市中的关系谜题"
        assert restored.get_generation_mode() == "variant"
        assert restored.get_creative_profile()["style"] == "emotional"
        assert restored.get_creative_profile()["custom_brief"] == "更宿命感"
        assert restored.get_constraints()["genre"] == "悬疑言情"
        assert restored.get_constraints()["total_chapters"] == 36
    finally:
        restored.hide()
        restored.deleteLater()
        qapp.processEvents()


def test_ai_polish_dialog_suggestion_bulk_buttons(
    qapp: QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")
    dialog = _AiPolishDialog("short", suggestions=["增强悬念", "压缩旁白"], parent=None)
    try:
        dialog.show()
        qapp.processEvents()

        buttons = _buttons_by_text(dialog)
        assert {"全选灵感", "清空灵感"} <= set(buttons)
        assert dialog.get_selected_suggestions() == []
        buttons["全选灵感"].click()
        assert dialog.get_selected_suggestions() == ["增强悬念", "压缩旁白"]
        buttons["清空灵感"].click()
        assert dialog.get_selected_suggestions() == []
    finally:
        dialog.hide()
        dialog.deleteLater()
        qapp.processEvents()


def test_ai_polish_dialog_autosaves_and_restores_input_draft(
    qapp: QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")
    suggestions = ["增强悬念", "压缩旁白"]
    dialog = _AiPolishDialog("short", suggestions=suggestions, parent=None)
    try:
        dialog._text_edit.setPlainText("加强误会后的情绪回响")
        dialog._suggestion_checks[1].setChecked(True)
        dialog._set_focus_checked(False)
        dialog._focus_checks[0][1].setChecked(True)
        qapp.processEvents()

        draft = preset_manager.load_ai_input_draft(
            "short",
            workflow_presets.AI_POLISH_DRAFT_KEY,
        )

        assert draft["hint"] == "加强误会后的情绪回响"
        assert draft["selected_suggestions"] == ["压缩旁白"]
        assert draft["focus_fields"] == ["theme"]
    finally:
        dialog.hide()
        dialog.deleteLater()
        qapp.processEvents()

    restored = _AiPolishDialog("short", suggestions=suggestions, parent=None)
    try:
        assert restored.get_hint() == "加强误会后的情绪回响"
        assert restored.get_selected_suggestions() == ["压缩旁白"]
        assert restored.get_focus_fields() == ["theme"]
    finally:
        restored.hide()
        restored.deleteLater()
        qapp.processEvents()


def test_ai_polish_diff_dialog_bulk_selection_and_counts(qapp: QApplication) -> None:
    dialog = _DiffDialog(
        [
            {"key": "premise", "label": "故事前提", "old": "旧前提", "new": "新前提"},
            {"key": "world_hint", "label": "世界观提示", "old": "旧世界", "new": "新世界"},
        ],
        parent=None,
    )
    try:
        dialog.show()
        qapp.processEvents()

        buttons = _buttons_by_text(dialog)
        assert {"全选变更", "清空变更", "反选"} <= set(buttons)
        assert dialog._selected_count() == 2
        assert dialog._accept_btn is not None
        assert dialog._accept_btn.text() == "应用 2 项变更"

        buttons["清空变更"].click()
        assert dialog._selected_count() == 0
        assert not dialog._accept_btn.isEnabled()

        buttons["反选"].click()
        assert dialog._selected_count() == 2
        assert dialog._accept_btn.isEnabled()

        first = dialog._field_list.item(0)
        first.setCheckState(Qt.CheckState.Unchecked)
        dialog._on_accept()
        assert dialog.accepted_fields == ["world_hint"]
        assert dialog.rejected_fields == ["premise"]
    finally:
        dialog.hide()
        dialog.deleteLater()
        qapp.processEvents()


def test_ai_polish_diff_ignores_internal_and_protected_fields() -> None:
    diffs = _compute_field_diffs(
        {
            "premise": "旧前提",
            "characters_hint": "模型未返回时不应误报删除",
            "blueprint_element_preferences": {
                "preset_id": "romance",
                "manual_override": True,
                "items": [{"element_id": "meet_cute", "enabled": True}],
            },
            "segment_trigger_words": 5500,
        },
        {
            "premise": "新前提",
            AI_CREATIVE_NOTE_FIELD: {"core_pitch": "强化关系钩子"},
            AI_POLISH_SUGGESTIONS_FIELD: ["强化人物", "优化冲突"],
        },
    )

    assert [diff["key"] for diff in diffs] == ["premise"]


def test_ai_generate_acceptance_auto_saves_preset(
    qapp: QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")

    class AcceptAllDiff:
        accepted_fields: list[str]
        rejected_fields: list[str]

        def __init__(self, diffs, **_kwargs) -> None:
            self.accepted_fields = [str(diff["key"]) for diff in diffs]
            self.rejected_fields = []

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(workflow_presets, "_DiffDialog", AcceptAllDiff)
    current = {
        "theme": "旧主题",
        "genre": "悬疑",
        "tone": "冷峻",
        "length_target": 2800,
    }
    toolbar = PresetToolbar("short", payload_provider=lambda: dict(current))
    filled: list[dict[str, object]] = []
    toolbar.fill_requested.connect(lambda data: filled.append(dict(data)))
    try:
        toolbar._pending_ai_context = {"operation": "generate", "hint": "雾中委托"}
        toolbar._on_ai_result(
            {
                "title": "雾中委托",
                "theme": "新主题",
                "genre": "悬疑",
                AI_POLISH_SUGGESTIONS_FIELD: ["强化线索"],
            }
        )
        qapp.processEvents()

        saved = preset_manager.load_preset("short", "雾中委托")
        entries = [
            entry
            for entry in preset_manager.load_polish_history("short", "雾中委托")
            if entry.get("operation")
        ]

        assert filled and filled[0]["theme"] == "新主题"
        assert saved["theme"] == "新主题"
        assert saved["length_target"] == 2800
        assert preset_manager.load_polish_suggestions("short", "雾中委托") == ["强化线索"]
        assert entries[0]["operation"] == "generate"
    finally:
        toolbar.hide()
        toolbar.deleteLater()
        qapp.processEvents()


def test_manual_preset_save_binds_and_update_creates_backup(
    qapp: QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")
    monkeypatch.setattr(
        workflow_presets,
        "show_text_input_dialog",
        lambda *_args, **_kwargs: ("联网资料预设", True),
    )
    monkeypatch.setattr(workflow_presets, "show_info_message", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_presets, "ask_confirmation", lambda *_args, **_kwargs: True)
    current = {
        "premise": "记者调查旧城档案",
        "research_enabled": True,
        "research_provider": "searxng",
        "research_query_hint": "旧城改造 档案制度",
    }
    toolbar = PresetToolbar("long", payload_provider=lambda: dict(current))
    try:
        toolbar.do_save(dict(current))

        assert toolbar._active_preset_name == "联网资料预设"
        assert toolbar._preset_combo.currentText() == "联网资料预设"
        assert (
            preset_manager.load_preset("long", "联网资料预设")["research_query_hint"]
            == "旧城改造 档案制度"
        )

        current["research_query_hint"] = "旧城改造 档案制度 历史建筑保护"
        toolbar._on_update()

        assert (
            preset_manager.load_preset("long", "联网资料预设")["research_query_hint"]
            == "旧城改造 档案制度 历史建筑保护"
        )
        backup_dir = tmp_path / ".presets" / "long"
        assert list(backup_dir.glob("联网资料预设.json.bak.*"))

        toolbar.unbind_preset()
        assert toolbar._active_preset_name == ""
        assert toolbar._preset_combo.currentIndex() == -1
    finally:
        toolbar.hide()
        toolbar.deleteLater()
        qapp.processEvents()


def test_ai_polish_acceptance_auto_saves_preset(
    qapp: QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")

    class AcceptAllDiff:
        accepted_fields: list[str]
        rejected_fields: list[str]

        def __init__(self, diffs, **_kwargs) -> None:
            self.accepted_fields = [str(diff["key"]) for diff in diffs]
            self.rejected_fields = []

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(workflow_presets, "_DiffDialog", AcceptAllDiff)
    current = {
        "theme": "旧主题",
        "title": "旧标题",
        "genre": "悬疑",
        "tone": "冷峻",
        "length_target": 2600,
    }
    toolbar = PresetToolbar("short", payload_provider=lambda: dict(current))
    filled: list[dict[str, object]] = []
    toolbar.fill_requested.connect(lambda data: filled.append(dict(data)))
    try:
        toolbar._pending_ai_context = {
            "operation": "polish",
            "hint": "更有压迫感",
            "selected_suggestions": ["强化反转"],
            "focus_fields": ["theme"],
        }
        toolbar._on_ai_polish_result(
            {
                "title": "雾中委托修订版",
                "theme": "更锋利的新主题",
                AI_POLISH_SUGGESTIONS_FIELD: ["加深人物代价"],
            }
        )
        qapp.processEvents()

        saved = preset_manager.load_preset("short", "雾中委托修订版")
        entries = [
            entry
            for entry in preset_manager.load_polish_history("short", "雾中委托修订版")
            if entry.get("operation")
        ]

        assert filled and filled[0]["theme"] == "更锋利的新主题"
        assert saved["theme"] == "更锋利的新主题"
        assert saved["length_target"] == 2600
        assert preset_manager.load_polish_suggestions("short", "雾中委托修订版") == ["加深人物代价"]
        assert entries[0]["operation"] == "polish_applied"
    finally:
        toolbar.hide()
        toolbar.deleteLater()
        qapp.processEvents()


def test_workflow_forms_disable_shadow_effect_for_stable_repaint(qapp: QApplication) -> None:
    short_form = ShortForm()
    long_form = LongInitForm()
    try:
        short_form.show()
        long_form.show()
        qapp.processEvents()
        assert short_form.graphicsEffect() is None
        assert long_form.graphicsEffect() is None
    finally:
        short_form.hide()
        short_form.deleteLater()
        long_form.hide()
        long_form.deleteLater()
        qapp.processEvents()


def test_short_form_restore_draft_uses_qtextedit_without_name_error(
    qapp: QApplication,
    tmp_path,
) -> None:
    form = ShortForm()
    try:
        form.show()
        qapp.processEvents()
        draft_path = tmp_path / "short_draft.json"
        draft_path.write_text(
            json.dumps({"theme": "测试主题", "genre": "悬疑"}, ensure_ascii=False),
            encoding="utf-8",
        )
        assert form.restore_draft(draft_path) is True
        assert all(not edit.document().isModified() for edit in form.findChildren(QTextEdit))
    finally:
        form.hide()
        form.deleteLater()
        qapp.processEvents()


def test_long_form_restore_draft_uses_qtextedit_without_name_error(
    qapp: QApplication,
    tmp_path,
) -> None:
    form = LongInitForm()
    try:
        form.show()
        qapp.processEvents()
        draft_path = tmp_path / "long_draft.json"
        draft_path.write_text(
            json.dumps({"premise": "测试前提", "genre": "仙侠"}, ensure_ascii=False),
            encoding="utf-8",
        )
        assert form.restore_draft(draft_path) is True
        assert all(not edit.document().isModified() for edit in form.findChildren(QTextEdit))
    finally:
        form.hide()
        form.deleteLater()
        qapp.processEvents()
