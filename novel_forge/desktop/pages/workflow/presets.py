"""Preset toolbar and AI-generation dialogs for workflow forms."""

from __future__ import annotations

import difflib
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.ai_generate import (
    AI_CREATIVE_NOTE_FIELD,
    AI_POLISH_SUGGESTIONS_FIELD,
    creative_axis_options,
    generate_config,
    generation_mode_description,
    generation_mode_options,
    get_ai_field_label,
    get_default_polish_suggestions,
    polish_config,
    polishable_fields_for_mode,
)
from novel_forge.desktop.components.sizing import smart_dialog_size
from novel_forge.desktop.pages.document_renderer.incremental import IncrementalDocumentRenderer
from novel_forge.desktop.preset_manager import (
    backup_preset_before_save,
    delete_ai_input_draft,
    delete_polish_history,
    delete_preset,
    export_preset_json,
    import_from_cli_json,
    list_presets,
    load_ai_input_draft,
    load_polish_history,
    load_polish_suggestions,
    load_preset,
    save_ai_input_draft,
    save_polish_history,
    save_polish_suggestions,
    save_preset,
)
from novel_forge.desktop.thread_pools import desktop_thread_pools
from novel_forge.desktop.widgets import (
    MessageBoxAction,
    ask_confirmation,
    show_info_message,
    show_message_box,
    show_text_input_dialog,
    show_warning_message,
)
from novel_forge.desktop.workers import BaseJobWorker, BaseJobWorkerSignals


def _normalize_suggestion_list(value: Any) -> list[str]:
    """Normalize suggestion-like payload to a short unique string list."""
    if value is None:
        return []
    if isinstance(value, str):
        raw = [line.strip() for line in value.splitlines() if line.strip()]
    elif isinstance(value, list):
        raw = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw = [str(value).strip()]
    result: list[str] = []
    for item in raw:
        normalized = " ".join(item.split())
        if normalized and normalized not in result:
            result.append(normalized)
        if len(result) >= 8:
            break
    return result


def _make_selection_tool_button(text: str, handler: Callable[[], None]) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("subModeBtn")
    button.setProperty("compact", True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.clicked.connect(lambda _checked=False: handler())
    return button


def _dialog_param_block(label: str, widget: QWidget) -> QVBoxLayout:
    block = QVBoxLayout()
    block.setSpacing(5)
    label_widget = QLabel(label)
    label_widget.setObjectName("settingLabel")
    label_widget.setWordWrap(True)
    widget.setMinimumWidth(max(widget.minimumWidth(), 148))
    block.addWidget(label_widget)
    block.addWidget(widget)
    return block


def _compact_auto_preset_name(value: Any, *, limit: int = 32) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text


def _preset_storage_stem(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _ai_autosave_name_from_payload(mode: str, payload: dict[str, Any]) -> str:
    main_key = "premise" if mode == "long" else "theme"
    for key in ("title", main_key):
        candidate = _compact_auto_preset_name(payload.get(key))
        if candidate:
            return candidate
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return f"AI创意生成_{timestamp}"


def _unique_ai_autosave_name(mode: str, payload: dict[str, Any]) -> str:
    base_name = _ai_autosave_name_from_payload(mode, payload)
    existing_stems = {_preset_storage_stem(name) for name in list_presets(mode)}
    if _preset_storage_stem(base_name) not in existing_stems:
        return base_name

    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    candidate = f"{base_name}_{timestamp}"
    counter = 2
    while _preset_storage_stem(candidate) in existing_stems:
        candidate = f"{base_name}_{timestamp}_{counter}"
        counter += 1
    return candidate


def _merged_ai_payload(current: Any, data: dict[str, Any]) -> dict[str, Any]:
    return {**current, **data} if isinstance(current, dict) else dict(data)


AI_GENERATE_DRAFT_KEY = "ai_generate"
AI_POLISH_DRAFT_KEY = "ai_polish"


def _set_combo_data(combo: QComboBox, value: Any) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


class PresetToolbar(QWidget):
    """Toolbar for preset management: save / load / delete / import / AI generate / AI polish."""

    fill_requested = Signal(dict)

    def __init__(
        self,
        mode: str,
        *,
        payload_provider: Callable[[], dict[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._mode = mode
        self._payload_provider = payload_provider
        self._pending_constraints: dict[str, Any] = {}
        self._pending_ai_context: dict[str, Any] = {}
        self._latest_polish_suggestions: list[str] = []
        self._active_preset_name: str = ""
        self._progress_dialog: _AiGenerateDialog | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        ai_row = QHBoxLayout()
        ai_row.setContentsMargins(0, 0, 0, 0)
        ai_row.setSpacing(6)

        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(140)
        self._preset_combo.setMaximumWidth(220)
        self._preset_combo.setPlaceholderText("选择已保存的配置…")
        top_row.addWidget(self._preset_combo, 1)
        self._refresh_presets()

        def _make_btn(
            text: str,
            handler: Callable[[], None],
            *,
            tool_role: str = "",
        ) -> QPushButton:
            button = QPushButton(text)
            button.setObjectName("subModeBtn")
            if tool_role:
                button.setProperty("toolRole", tool_role)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(30)
            button.clicked.connect(handler)
            return button

        top_row.addWidget(_make_btn("载入", self._on_load))
        top_row.addWidget(_make_btn("更新", self._on_update))
        top_row.addWidget(_make_btn("删除", self._on_delete))
        top_row.addWidget(_make_btn("导入 JSON", self._on_import))
        top_row.addWidget(_make_btn("导出 JSON", self._on_export))
        top_row.addStretch()
        ai_row.addWidget(_make_btn("AI 生成并预览", self._on_ai_generate, tool_role="ai"))
        ai_row.addWidget(_make_btn("AI 定向润色", self._on_ai_polish, tool_role="ai"))
        ai_row.addWidget(_make_btn("创作札记", self._on_history))
        ai_row.addStretch()

        layout.addLayout(top_row)
        layout.addLayout(ai_row)

    def set_payload_provider(self, provider: Callable[[], dict[str, Any]]) -> None:
        self._payload_provider = provider

    def do_save(self, data: dict[str, Any]) -> None:
        default_name = str(data.get("title", "")).strip()
        name, ok = show_text_input_dialog(
            self.window(),
            "存为预设",
            "为此预设命名：",
            initial_text=default_name,
            placeholder_text="例如：都市悬疑长篇 v1",
            confirm_text="保存",
            cancel_text="取消",
        )
        if not ok or not name.strip():
            return
        saved = self._persist_named_preset(name, data, confirm_overwrite=True)
        if saved is None:
            return
        saved_name, path = saved
        show_info_message(
            self.window(),
            "已保存",
            f"预设「{saved_name}」已保存。",
            informative_text=f"下次可直接从列表载入。\n保存位置：{path.as_posix()}",
        )

    def _persist_named_preset(
        self,
        name: str,
        data: dict[str, Any],
        *,
        confirm_overwrite: bool,
    ) -> tuple[str, Path] | None:
        display_name = name.strip()
        storage_name = _preset_storage_stem(display_name)
        if not storage_name:
            show_warning_message(
                self.window(),
                "名称无效",
                "预设名称至少需要包含一个文字、字母或数字。",
            )
            return None

        exists = storage_name in list_presets(self._mode)
        if exists and confirm_overwrite:
            confirmed = ask_confirmation(
                self.window(),
                "覆盖预设",
                f"预设「{storage_name}」已存在，是否用当前表单更新？",
                informative_text="覆盖前会自动保留一份带时间戳的备份。",
                confirm_text="更新预设",
                cancel_text="取消",
            )
            if not confirmed:
                return None

        try:
            if exists:
                backup_preset_before_save(self._mode, storage_name)
            path = save_preset(self._mode, storage_name, data)
        except (OSError, ValueError) as exc:
            show_warning_message(
                self.window(),
                "保存失败",
                f"无法保存预设「{storage_name}」：{exc}",
            )
            return None

        saved_name = path.stem
        self._set_active_preset_name(saved_name)
        self._refresh_presets()
        self._select_preset_combo_name(saved_name)
        return saved_name, path

    def _set_active_preset_name(self, name: str) -> None:
        """Bind polish history and suggestions to a specific preset name."""
        if not name or name == self._active_preset_name:
            return
        self._active_preset_name = name
        self._latest_polish_suggestions = load_polish_suggestions(self._mode, name)

    def unbind_preset(self) -> None:
        """Detach the form from a preset so later AI work cannot overwrite it."""
        self._active_preset_name = ""
        self._latest_polish_suggestions = []
        self._preset_combo.setCurrentIndex(-1)

    def _select_preset_combo_name(self, name: str) -> None:
        index = self._preset_combo.findText(name)
        if index >= 0:
            self._preset_combo.setCurrentIndex(index)

    def _auto_save_ai_preset(self, payload: dict[str, Any]) -> str:
        pending_suggestions = list(self._latest_polish_suggestions)
        preset_name = self._active_preset_name.strip()
        if not preset_name:
            preset_name = _unique_ai_autosave_name(self._mode, payload)
        backup_preset_before_save(self._mode, preset_name)
        saved_path = save_preset(self._mode, preset_name, payload)
        saved_name = saved_path.stem
        self._set_active_preset_name(saved_name)
        self._refresh_presets()
        self._select_preset_combo_name(saved_name)
        if pending_suggestions:
            self._latest_polish_suggestions = pending_suggestions
            save_polish_suggestions(self._mode, saved_name, pending_suggestions)
        return saved_name

    def _show_ai_autosave_warning(self, error_text: str, *, applied: bool = True) -> None:
        message = (
            "AI 内容已写入当前表单，但未能自动保存为预设。"
            if applied
            else "AI 内容未写入当前表单，也未能自动保存为预设或创作札记。"
        )
        show_warning_message(
            self.window(),
            "自动保存失败",
            message,
            informative_text=f"请手动保存一次，避免创意丢失。\n\n错误：{error_text}",
        )

    def _clear_ai_input_draft(self, draft_key: str) -> None:
        try:
            delete_ai_input_draft(self._mode, draft_key)
        except OSError:
            pass

    def _on_history(self) -> None:
        if not self._active_preset_name:
            show_info_message(
                self.window(),
                "未绑定预设",
                "请先载入预设、导入并保存，或使用 AI 生成来建立预设关联。"
                "\n\n创作札记按预设名称独立保存。",
            )
            return
        dialog = _HistoryDialog(
            self._mode,
            self._active_preset_name,
            parent=self.window(),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_data is not None:
            self.fill_requested.emit(dialog.selected_data)

    def export_template(self) -> None:
        self._export_payload(template_only=True)

    def export_current_payload(self) -> None:
        self._export_payload(template_only=False)

    def _refresh_presets(self) -> None:
        current = self._preset_combo.currentText()
        self._preset_combo.clear()
        names = list_presets(self._mode)
        self._preset_combo.addItems(names)
        if current in names:
            self._preset_combo.setCurrentText(current)
        else:
            self._preset_combo.setCurrentIndex(-1)

    def _on_load(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name:
            show_info_message(self.window(), "提示", "请先选择要载入的配置。")
            return
        try:
            data = load_preset(self._mode, name)
        except FileNotFoundError:
            show_warning_message(self.window(), "未找到", f"配置「{name}」不存在。")
            return
        except (OSError, ValueError) as exc:
            show_warning_message(
                self.window(),
                "载入失败",
                f"预设「{name}」无法读取：{exc}",
            )
            return
        self._set_active_preset_name(name)
        self.fill_requested.emit(data)

    def _on_update(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name:
            show_info_message(
                self.window(),
                "提示",
                "请先选择要更新的预设；新配置请使用「存为预设」。",
            )
            return
        if self._payload_provider is None:
            show_warning_message(self.window(), "无法更新", "当前表单尚未准备好读取数据。")
            return
        data = self._payload_provider()
        if not isinstance(data, dict):
            show_warning_message(self.window(), "无法更新", "当前表单数据格式异常。")
            return
        saved = self._persist_named_preset(name, data, confirm_overwrite=True)
        if saved is not None:
            show_info_message(
                self.window(),
                "已更新",
                f"预设「{saved[0]}」已更新，旧版已自动备份。",
            )

    def _on_delete(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name:
            show_info_message(self.window(), "提示", "请先选择要删除的配置。")
            return
        confirmed = ask_confirmation(
            self.window(),
            "确认删除",
            f"确定要删除配置「{name}」？",
            informative_text="关联的创作札记会保留；以后重建同名预设可继续查看。",
            confirm_text="删除配置",
            cancel_text="保留",
            confirm_variant="danger",
        )
        if confirmed:
            try:
                delete_preset(self._mode, name)
            except OSError as exc:
                show_warning_message(self.window(), "删除失败", f"无法删除预设：{exc}")
                return
            if self._active_preset_name == name:
                self.unbind_preset()
            self._refresh_presets()

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self.window(),
            "导入配置文件",
            "",
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not path:
            return
        try:
            presets = import_from_cli_json(path, preferred_mode=self._mode)
        except Exception as exc:
            show_warning_message(self.window(), "导入失败", f"无法解析 JSON：{exc}")
            return

        data = presets.get(self._mode)
        if data is None:
            show_info_message(
                self.window(),
                "提示",
                f"该 JSON 不包含可用于当前{'短篇' if self._mode == 'short' else '长篇'}表单的预设数据。",
            )
            return

        choice = show_message_box(
            self.window(),
            "导入方式",
            "建议将导入内容保存到本地预设并立即填入表单。",
            informative_text="长篇预设会一并保留联网开关、检索后端和检索方向。",
            actions=(
                MessageBoxAction(
                    "save_and_fill",
                    "保存并填入",
                    QMessageBox.ButtonRole.AcceptRole,
                    "primary",
                    True,
                ),
                MessageBoxAction(
                    "fill_only", "仅填入", QMessageBox.ButtonRole.ActionRole, "secondary"
                ),
                MessageBoxAction("cancel", "取消", QMessageBox.ButtonRole.RejectRole, "secondary"),
            ),
            escape_key="cancel",
        )
        if choice == "fill_only":
            self.unbind_preset()
            self.fill_requested.emit(data)
        elif choice == "save_and_fill":
            saved = self._persist_named_preset(
                Path(path).stem,
                data,
                confirm_overwrite=True,
            )
            if saved is not None:
                self.fill_requested.emit(data)
                show_info_message(
                    self.window(), "已导入", f"预设已保存为「{saved[0]}」并填入表单。"
                )

    def _on_export(self) -> None:
        choice = show_message_box(
            self.window(),
            "导出 JSON",
            "选择要导出的内容。",
            informative_text="模板导出仅包含当前模式所需的 JSON 字段，不带 run_short / init_long 外层包装。",
            actions=(
                MessageBoxAction(
                    "current", "导出当前配置", QMessageBox.ButtonRole.AcceptRole, "primary", True
                ),
                MessageBoxAction(
                    "template", "导出模板", QMessageBox.ButtonRole.ActionRole, "secondary"
                ),
                MessageBoxAction("cancel", "取消", QMessageBox.ButtonRole.RejectRole, "secondary"),
            ),
            escape_key="cancel",
        )
        if choice == "current":
            self.export_current_payload()
        elif choice == "template":
            self.export_template()

    def _export_payload(self, *, template_only: bool) -> None:
        payload_provider = self._payload_provider
        if not template_only and payload_provider is None:
            show_warning_message(self.window(), "无法导出", "当前表单尚未准备好导出数据。")
            return

        default_name = (
            f"{self._mode}_template.json" if template_only else f"{self._mode}_preset.json"
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self.window(),
            "导出 JSON",
            default_name,
            "JSON 文件 (*.json)",
        )
        if not file_path:
            return
        payload: dict[str, Any] | None = None if template_only else payload_provider()  # type: ignore[misc]
        saved_path = export_preset_json(file_path, self._mode, payload)
        kind_label = "模板" if template_only else "当前配置"
        show_info_message(
            self.window(),
            "导出成功",
            f"{kind_label}已导出到：{saved_path.name}",
            informative_text="导出文件只包含当前模式的 JSON 字段，可直接再次导入。",
        )

    def _on_ai_generate(self) -> None:
        current_payload = self._payload_provider() if self._payload_provider else {}
        if current_payload is None:
            current_payload = {}
        if not isinstance(current_payload, dict):
            show_warning_message(self.window(), "无法生成", "当前配置格式异常，请稍后重试。")
            return
        dialog = _AiHintDialog(self._mode, self.window())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        user_hint = dialog.get_hint()
        self._pending_constraints = dialog.get_constraints()
        generation_mode = dialog.get_generation_mode()
        creative_profile = dialog.get_creative_profile()
        self._pending_ai_context = {
            "operation": "generate",
            "hint": user_hint,
            "generation_mode": generation_mode,
            "creative_profile": creative_profile,
            "constraints": dict(self._pending_constraints),
        }

        self._show_progress_dialog(
            title="AI 正在构思…",
            message="AI 正在生成创作配置，完成后会先展示变更预览，应用后自动保存。",
        )

        worker = _AiGenerateWorker(
            self._mode,
            user_hint,
            current_payload=current_payload,
            generation_mode=generation_mode,
            creative_profile=creative_profile,
            hard_constraints=self._pending_constraints,
        )
        worker.signals.finished.connect(self._on_ai_result)
        worker.signals.failed.connect(self._on_ai_error)
        desktop_thread_pools().aux_pool.start(worker)

    def _on_ai_polish(self) -> None:
        payload_provider = self._payload_provider
        if payload_provider is None:
            show_warning_message(self.window(), "无法润色", "当前表单尚未准备好读取配置。")
            return
        current_payload = payload_provider() or {}
        if not isinstance(current_payload, dict):
            show_warning_message(self.window(), "无法润色", "当前配置格式异常，请稍后重试。")
            return
        key = "premise" if self._mode == "long" else "theme"
        if not str(current_payload.get(key, "")).strip():
            show_info_message(
                self.window(),
                "先补充基础内容",
                "请先填写核心前提（或先使用「AI 随机生成」），再进行 AI 润色。",
            )
            return

        suggestions = self._latest_polish_suggestions
        if not suggestions:
            suggestions = get_default_polish_suggestions(self._mode)
        dialog = _AiPolishDialog(
            self._mode,
            suggestions=suggestions,
            parent=self.window(),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        user_hint = dialog.get_hint()
        selected_suggestions = dialog.get_selected_suggestions()
        focus_fields = dialog.get_focus_fields()
        if not user_hint and not selected_suggestions and not focus_fields:
            show_info_message(
                self.window(), "提示", "请至少输入润色方向、勾选灵感或选择重点润色字段。"
            )
            return
        self._pending_ai_context = {
            "operation": "polish",
            "hint": user_hint,
            "selected_suggestions": selected_suggestions,
            "focus_fields": focus_fields,
        }

        self._show_progress_dialog(
            title="AI 正在润色…",
            message="AI 正在按你的方向润色当前创作配置，应用后会自动保存。",
        )

        worker = _AiPolishWorker(
            self._mode,
            current_payload=current_payload,
            user_hint=user_hint,
            selected_suggestions=selected_suggestions,
            focus_fields=focus_fields,
        )
        worker.signals.finished.connect(self._on_ai_polish_result)
        worker.signals.failed.connect(self._on_ai_error)
        desktop_thread_pools().aux_pool.start(worker)

    def _show_progress_dialog(self, *, title: str, message: str) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
        self._progress_dialog = _AiGenerateDialog(self.window(), title=title, message=message)
        self._progress_dialog.show()

    def _close_progress_dialog(self) -> None:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None

    def _consume_polish_suggestions(self, data: dict[str, Any]) -> None:
        suggestions = _normalize_suggestion_list(data.pop(AI_POLISH_SUGGESTIONS_FIELD, None))
        if suggestions:
            self._latest_polish_suggestions = suggestions
            if self._active_preset_name:
                try:
                    save_polish_suggestions(self._mode, self._active_preset_name, suggestions)
                except OSError:
                    pass

    def _consume_creative_note(self, data: dict[str, Any]) -> dict[str, Any]:
        note = data.pop(AI_CREATIVE_NOTE_FIELD, None)
        return note if isinstance(note, dict) else {}

    def _build_ai_history_metadata(
        self,
        *,
        creative_note: dict[str, Any],
        accepted_fields: list[str],
        rejected_fields: list[str],
    ) -> dict[str, Any]:
        metadata = dict(self._pending_ai_context)
        if creative_note:
            metadata["creative_note"] = creative_note
        metadata["accepted_fields"] = list(accepted_fields)
        metadata["rejected_fields"] = list(rejected_fields)
        return metadata

    def _on_ai_result(self, data: dict[str, Any]) -> None:
        self._close_progress_dialog()
        constraints = getattr(self, "_pending_constraints", {})
        if constraints:
            data.update(constraints)
            self._pending_constraints = {}
        creative_note = self._consume_creative_note(data)
        current = self._payload_provider() if self._payload_provider else {}
        current_payload = current if isinstance(current, dict) else {}
        diffs = _compute_field_diffs(current_payload, data)
        accepted_fields: list[str] = []
        rejected_fields: list[str] = []

        if diffs:
            diff_dialog = _DiffDialog(
                diffs,
                window_title="AI 生成变更预览",
                title_text=f"AI 生成了 {len(diffs)} 个字段变更",
                subtitle="先预览再应用；未勾选字段会保留当前值。应用后会自动保存为预设。",
                parent=self.window(),
            )
            if diff_dialog.exec() == QDialog.DialogCode.Rejected:
                self._consume_polish_suggestions(data)
                rejected_fields = [str(diff["key"]) for diff in diffs]
                history_data = _merged_ai_payload(current_payload, data)
                autosave_error = ""
                if not self._active_preset_name:
                    try:
                        self._auto_save_ai_preset(history_data)
                    except OSError as exc:
                        autosave_error = str(exc)
                if self._active_preset_name:
                    try:
                        save_polish_history(
                            self._mode,
                            self._active_preset_name,
                            operation="generate_rejected",
                            data=history_data,
                            hint=str(self._pending_ai_context.get("hint", "")),
                            metadata=self._build_ai_history_metadata(
                                creative_note=creative_note,
                                accepted_fields=[],
                                rejected_fields=rejected_fields,
                            ),
                        )
                    except OSError:
                        pass
                self._pending_ai_context = {}
                if autosave_error:
                    self._show_ai_autosave_warning(autosave_error, applied=False)
                else:
                    self._clear_ai_input_draft(AI_GENERATE_DRAFT_KEY)
                return
            accepted_fields = list(diff_dialog.accepted_fields)
            rejected_fields = list(diff_dialog.rejected_fields)
            if rejected_fields:
                for field in rejected_fields:
                    data.pop(field, None)
        else:
            show_info_message(self.window(), "没有可应用变更", "AI 返回结果与当前表单基本一致。")
            self._consume_polish_suggestions(data)
            history_data = _merged_ai_payload(current_payload, data)
            autosave_error = ""
            if not self._active_preset_name:
                try:
                    self._auto_save_ai_preset(history_data)
                except OSError as exc:
                    autosave_error = str(exc)
            if self._active_preset_name and creative_note:
                try:
                    save_polish_history(
                        self._mode,
                        self._active_preset_name,
                        operation="generate_noop",
                        data=history_data,
                        hint=str(self._pending_ai_context.get("hint", "")),
                        metadata=self._build_ai_history_metadata(
                            creative_note=creative_note,
                            accepted_fields=[],
                            rejected_fields=[],
                        ),
                    )
                except OSError:
                    pass
            self._pending_ai_context = {}
            if autosave_error:
                self._show_ai_autosave_warning(autosave_error, applied=False)
            else:
                self._clear_ai_input_draft(AI_GENERATE_DRAFT_KEY)
            return

        self._consume_polish_suggestions(data)
        history_data = _merged_ai_payload(current_payload, data)
        autosave_error = ""
        try:
            self._auto_save_ai_preset(history_data)
        except OSError as exc:
            autosave_error = str(exc)
        if self._active_preset_name:
            try:
                save_polish_history(
                    self._mode,
                    self._active_preset_name,
                    operation="generate",
                    data=history_data,
                    hint=str(self._pending_ai_context.get("hint", "")),
                    metadata=self._build_ai_history_metadata(
                        creative_note=creative_note,
                        accepted_fields=accepted_fields,
                        rejected_fields=rejected_fields,
                    ),
                )
            except OSError:
                pass
        self._pending_ai_context = {}
        self.fill_requested.emit(data)
        if autosave_error:
            self._show_ai_autosave_warning(autosave_error)
        else:
            self._clear_ai_input_draft(AI_GENERATE_DRAFT_KEY)

    def _on_ai_polish_result(self, data: dict[str, Any]) -> None:
        self._close_progress_dialog()
        creative_note = self._consume_creative_note(data)
        current = self._payload_provider() if self._payload_provider else {}
        current_payload = current if isinstance(current, dict) else {}
        diffs = _compute_field_diffs(current_payload, data)
        accepted_fields: list[str] = []
        rejected_fields: list[str] = []

        if diffs:
            diff_dialog = _DiffDialog(
                diffs,
                subtitle="勾选左侧字段后应用；未勾选字段会保留原值。应用后会自动保存为预设。",
                parent=self.window(),
            )
            if diff_dialog.exec() == QDialog.DialogCode.Rejected:
                self._consume_polish_suggestions(data)
                rejected_fields = [str(diff["key"]) for diff in diffs]
                history_data = _merged_ai_payload(current_payload, data)
                autosave_error = ""
                if not self._active_preset_name:
                    try:
                        self._auto_save_ai_preset(history_data)
                    except OSError as exc:
                        autosave_error = str(exc)
                if self._active_preset_name:
                    try:
                        save_polish_history(
                            self._mode,
                            self._active_preset_name,
                            operation="polish_rejected",
                            data=history_data,
                            hint=str(self._pending_ai_context.get("hint", "")),
                            suggestions=list(
                                self._pending_ai_context.get("selected_suggestions", [])
                            ),
                            focus_fields=list(self._pending_ai_context.get("focus_fields", [])),
                            metadata=self._build_ai_history_metadata(
                                creative_note=creative_note,
                                accepted_fields=[],
                                rejected_fields=rejected_fields,
                            ),
                        )
                    except OSError:
                        pass
                self._pending_ai_context = {}
                if autosave_error:
                    self._show_ai_autosave_warning(autosave_error, applied=False)
                else:
                    self._clear_ai_input_draft(AI_POLISH_DRAFT_KEY)
                return
            accepted_fields = list(diff_dialog.accepted_fields)
            rejected_fields = list(diff_dialog.rejected_fields)
            if rejected_fields:
                for field in rejected_fields:
                    data.pop(field, None)
        else:
            show_info_message(self.window(), "没有可应用变更", "AI 返回结果与当前表单基本一致。")
            self._consume_polish_suggestions(data)
            history_data = _merged_ai_payload(current_payload, data)
            autosave_error = ""
            if not self._active_preset_name:
                try:
                    self._auto_save_ai_preset(history_data)
                except OSError as exc:
                    autosave_error = str(exc)
            if self._active_preset_name and creative_note:
                try:
                    save_polish_history(
                        self._mode,
                        self._active_preset_name,
                        operation="polish_noop",
                        data=history_data,
                        hint=str(self._pending_ai_context.get("hint", "")),
                        suggestions=list(self._pending_ai_context.get("selected_suggestions", [])),
                        focus_fields=list(self._pending_ai_context.get("focus_fields", [])),
                        metadata=self._build_ai_history_metadata(
                            creative_note=creative_note,
                            accepted_fields=[],
                            rejected_fields=[],
                        ),
                    )
                except OSError:
                    pass
            self._pending_ai_context = {}
            if autosave_error:
                self._show_ai_autosave_warning(autosave_error, applied=False)
            else:
                self._clear_ai_input_draft(AI_POLISH_DRAFT_KEY)
            return

        self._consume_polish_suggestions(data)
        history_data = _merged_ai_payload(current_payload, data)
        autosave_error = ""
        try:
            self._auto_save_ai_preset(history_data)
        except OSError as exc:
            autosave_error = str(exc)
        if self._active_preset_name:
            try:
                save_polish_history(
                    self._mode,
                    self._active_preset_name,
                    operation="polish_applied",
                    data=history_data,
                    hint=str(self._pending_ai_context.get("hint", "")),
                    suggestions=list(self._pending_ai_context.get("selected_suggestions", [])),
                    focus_fields=list(self._pending_ai_context.get("focus_fields", [])),
                    metadata=self._build_ai_history_metadata(
                        creative_note=creative_note,
                        accepted_fields=accepted_fields,
                        rejected_fields=rejected_fields,
                    ),
                )
            except OSError:
                pass
        self._pending_ai_context = {}
        self.fill_requested.emit(data)
        if autosave_error:
            self._show_ai_autosave_warning(autosave_error)
        else:
            self._clear_ai_input_draft(AI_POLISH_DRAFT_KEY)

    def _on_ai_error(self, error_text: str) -> None:
        self._close_progress_dialog()
        self._pending_ai_context = {}
        show_warning_message(
            self.window(),
            "AI 处理失败",
            f"调用模型时出错：\n\n{error_text}\n\n"
            "请确认模型配置正确（在「火候」设置页配置 API Key 和模型路由）。",
        )

    def shutdown(self) -> None:
        """Close progress dialog, persist suggestions, and clear pending state.

        Thread-pool draining is handled globally by
        ``shutdown_desktop_thread_pools()`` in ``_pre_close_cleanup()``.
        """
        self._close_progress_dialog()
        if self._active_preset_name and self._latest_polish_suggestions:
            try:
                save_polish_suggestions(
                    self._mode,
                    self._active_preset_name,
                    self._latest_polish_suggestions,
                )
            except OSError:
                pass
        self._pending_constraints = {}
        self._pending_ai_context = {}
        self._latest_polish_suggestions = []


class _AiGenSignals(BaseJobWorkerSignals):
    finished = Signal(dict)
    failed = Signal(str)


class _AiGenerateWorker(BaseJobWorker):
    """Background worker that calls the LLM to generate a creative config."""

    signals_cls = _AiGenSignals
    pool = "aux"

    def __init__(
        self,
        mode: str,
        user_hint: str,
        *,
        current_payload: dict[str, Any],
        generation_mode: str,
        creative_profile: dict[str, Any],
        hard_constraints: dict[str, Any],
    ) -> None:
        super().__init__()
        self.mode = mode
        self.user_hint = user_hint
        self.current_payload = dict(current_payload)
        self.generation_mode = generation_mode
        self.creative_profile = dict(creative_profile)
        self.hard_constraints = dict(hard_constraints)

    async def _run_async(self) -> None:
        try:
            result = await generate_config(
                self.mode,
                self.user_hint,
                current_config=self.current_payload,
                generation_mode=self.generation_mode,
                creative_profile=self.creative_profile,
                hard_constraints=self.hard_constraints,
            )
            self.signals.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - surfaced to UI
            self.signals.failed.emit(str(exc))


class _AiPolishWorker(BaseJobWorker):
    """Background worker that calls the LLM to polish current config."""

    signals_cls = _AiGenSignals
    pool = "aux"

    def __init__(
        self,
        mode: str,
        *,
        current_payload: dict[str, Any],
        user_hint: str,
        selected_suggestions: list[str],
        focus_fields: list[str],
    ) -> None:
        super().__init__()
        self.mode = mode
        self.current_payload = dict(current_payload)
        self.user_hint = user_hint
        self.selected_suggestions = list(selected_suggestions)
        self.focus_fields = list(focus_fields)

    async def _run_async(self) -> None:
        try:
            result = await polish_config(
                self.mode,
                self.current_payload,
                self.user_hint,
                selected_suggestions=self.selected_suggestions,
                focus_fields=self.focus_fields,
            )
            self.signals.finished.emit(result)
        except Exception as exc:  # noqa: BLE001 - surfaced to UI
            self.signals.failed.emit(str(exc))


class _AiGenerateDialog(QDialog):
    """Progress dialog shown while AI generates config."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "AI 正在构思…",
        message: str = "AI 正在为你生成创作灵感，请稍候。",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("aiDialog")
        self.setWindowTitle(title)
        self.setFixedSize(400, 130)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        label = QLabel(message)
        label.setObjectName("aiProgressLabel")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setFixedHeight(6)
        layout.addWidget(bar)


class _AiHintDialog(QDialog):
    """Dialog for entering an AI-generation hint."""

    def __init__(self, mode: str = "short", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode
        self.setObjectName("aiDialog")
        self.setWindowTitle("AI 创意生成")
        self.setMinimumSize(760, 720 if mode == "long" else 680)
        self.resize(*smart_dialog_size(self, 840, 760 if mode == "long" else 720))
        self.setSizeGripEnabled(True)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(12)

        title = QLabel(
            "输入简要提示（可以是几个关键词、一句话灵感，也可以留空让 AI 完全自由发挥）："
        )
        title.setObjectName("settingLabel")
        title.setWordWrap(True)
        layout.addWidget(title)

        example = QLabel("例如：失控的委托 / 架空王国的权力裂痕 / 当代都市中的关系谜题")
        example.setObjectName("formFieldHint")
        example.setWordWrap(True)
        layout.addWidget(example)

        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText("在此输入你的创作灵感……")
        self._text_edit.setMinimumHeight(112)
        self._text_edit.setMaximumHeight(150)
        layout.addWidget(self._text_edit)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 4, 0, 0)
        mode_row.setSpacing(16)
        self._generation_mode = QComboBox()
        for label, value, prompt in generation_mode_options():
            self._generation_mode.addItem(label, value)
            self._generation_mode.setItemData(
                self._generation_mode.count() - 1,
                prompt,
                Qt.ItemDataRole.ToolTipRole,
            )
        self._generation_mode.setToolTip(
            "重写新方案会丢弃当前配置从零构思；\n只补空白会保留已填字段仅补充空白项；\n生成变体会保留题材与核心设定换表达角度。"
        )
        mode_row.addLayout(_dialog_param_block("生成方式", self._generation_mode))

        self._creative_style = QComboBox()
        for label, value, prompt in creative_axis_options("style"):
            self._creative_style.addItem(label, value)
            self._creative_style.setItemData(
                self._creative_style.count() - 1,
                prompt,
                Qt.ItemDataRole.ToolTipRole,
            )
        mode_row.addLayout(_dialog_param_block("创意取向", self._creative_style))
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # 动态模式描述标签：随下拉选择切换
        self._mode_description_label = QLabel()
        self._mode_description_label.setObjectName("aiHintModeDesc")
        self._mode_description_label.setWordWrap(True)
        layout.addWidget(self._mode_description_label)
        self._generation_mode.currentIndexChanged.connect(self._update_mode_description)
        self._update_mode_description()

        # 变体模式 + 硬参数冲突提示
        self._variant_conflict_hint = QLabel(
            "⚠ 生成变体模式会保留当前题材与核心设定；若要彻底更换题材，请切换到「重写新方案」。"
        )
        self._variant_conflict_hint.setObjectName("aiHintConflict")
        self._variant_conflict_hint.setWordWrap(True)
        self._variant_conflict_hint.setVisible(False)
        layout.addWidget(self._variant_conflict_hint)
        self._generation_mode.currentIndexChanged.connect(self._update_variant_conflict_hint)

        creative_row = QHBoxLayout()
        creative_row.setContentsMargins(0, 2, 0, 0)
        creative_row.setSpacing(16)
        self._novelty = QComboBox()
        for label, value, prompt in creative_axis_options("novelty"):
            self._novelty.addItem(label, value)
            self._novelty.setItemData(
                self._novelty.count() - 1,
                prompt,
                Qt.ItemDataRole.ToolTipRole,
            )
        fresh_index = self._novelty.findData("fresh")
        self._novelty.setCurrentIndex(fresh_index if fresh_index >= 0 else 0)
        creative_row.addLayout(_dialog_param_block("新奇度", self._novelty))

        self._conflict_density = QComboBox()
        for label, value, prompt in creative_axis_options("conflict"):
            self._conflict_density.addItem(label, value)
            self._conflict_density.setItemData(
                self._conflict_density.count() - 1,
                prompt,
                Qt.ItemDataRole.ToolTipRole,
            )
        layered_index = self._conflict_density.findData("layered")
        self._conflict_density.setCurrentIndex(layered_index if layered_index >= 0 else 0)
        creative_row.addLayout(_dialog_param_block("冲突密度", self._conflict_density))

        self._emotion_density = QComboBox()
        for label, value, prompt in creative_axis_options("emotion"):
            self._emotion_density.addItem(label, value)
            self._emotion_density.setItemData(
                self._emotion_density.count() - 1,
                prompt,
                Qt.ItemDataRole.ToolTipRole,
            )
        textured_index = self._emotion_density.findData("textured")
        self._emotion_density.setCurrentIndex(textured_index if textured_index >= 0 else 0)
        creative_row.addLayout(_dialog_param_block("情感浓度", self._emotion_density))
        creative_row.addStretch()
        layout.addLayout(creative_row)

        self._creative_brief = QLineEdit()
        self._creative_brief.setPlaceholderText(
            "可选：输入你希望模型自由发挥的创意方向，例：更民国海派 / 更宿命感 / 避免霸总套路"
        )
        self._creative_brief.setMinimumWidth(360)
        layout.addLayout(_dialog_param_block("自由创意侧重点", self._creative_brief))

        param_label = QLabel("可选硬参数（留“由 AI 决定”则全部交给 AI）：")
        param_label.setObjectName("settingLabel")
        layout.addWidget(param_label)

        row1 = QHBoxLayout()
        row1.setSpacing(12)

        self._genre = QLineEdit()
        self._genre.setPlaceholderText("留空由 AI 决定，例：言情、悬疑言情、科幻")
        self._genre.setMinimumWidth(230)
        genre_block = QVBoxLayout()
        genre_block.setSpacing(2)
        genre_label = QLabel("题材")
        genre_label.setObjectName("settingLabel")
        genre_block.addWidget(genre_label)
        genre_block.addWidget(self._genre)
        row1.addLayout(genre_block)

        self._tone = QLineEdit()
        self._tone.setPlaceholderText("留空由 AI 决定，例：温暖、悬疑、幽默、阴郁")
        self._tone.setMinimumWidth(230)
        tone_block = QVBoxLayout()
        tone_block.setSpacing(2)
        tone_label = QLabel("基调")
        tone_label.setObjectName("settingLabel")
        tone_block.addWidget(tone_label)
        tone_block.addWidget(self._tone)
        row1.addLayout(tone_block)

        row1.addStretch()
        layout.addLayout(row1)

        if mode == "long":
            row2 = QHBoxLayout()
            row2.setSpacing(12)

            self._total_chapters = QSpinBox()
            self._total_chapters.setRange(0, 1000)
            self._total_chapters.setSpecialValueText("由 AI 决定")
            self._total_chapters.setSuffix(" 章")
            self._total_chapters.setValue(0)
            self._total_chapters.setMinimumWidth(150)
            chapter_block = QVBoxLayout()
            chapter_block.setSpacing(2)
            chapter_label = QLabel("总章节数")
            chapter_label.setObjectName("settingLabel")
            chapter_block.addWidget(chapter_label)
            chapter_block.addWidget(self._total_chapters)
            row2.addLayout(chapter_block)

            self._words_per_chapter = QSpinBox()
            self._words_per_chapter.setRange(0, 20000)
            self._words_per_chapter.setSpecialValueText("由 AI 决定")
            self._words_per_chapter.setSingleStep(500)
            self._words_per_chapter.setSuffix(" 字")
            self._words_per_chapter.setValue(0)
            self._words_per_chapter.setMinimumWidth(150)
            words_block = QVBoxLayout()
            words_block.setSpacing(2)
            words_label = QLabel("每章字数")
            words_label.setObjectName("settingLabel")
            words_block.addWidget(words_label)
            words_block.addWidget(self._words_per_chapter)
            row2.addLayout(words_block)

            row2.addStretch()
            layout.addLayout(row2)

        note = QLabel(
            "AI 将基于提示和创意控制生成完整配置，包括主题、人物、世界观、冲突等所有要素。\n"
            "同时会生成一组可复用的润色灵感，供「AI 润色」一键调用。\n"
            "生成完成后会先显示字段变更预览，确认应用后会自动保存为预设并写入表单。"
        )
        note.setObjectName("formFieldHint")
        note.setWordWrap(True)
        layout.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        # 左侧：随机新题材快捷按钮
        random_btn = QPushButton("🎲 随机新题材")
        random_btn.setObjectName("actionButton")
        random_btn.setProperty("variant", "secondary")
        random_btn.setProperty("compact", True)
        random_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        random_btn.setToolTip(
            "清空所有输入与参数，切换到「重写新方案」模式，完全交给 AI 自由发挥。"
        )
        random_btn.clicked.connect(self._on_random_new_theme)
        btn_row.addWidget(random_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("actionButton")
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setProperty("compact", True)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("生成并预览")
        ok_btn.setObjectName("actionButton")
        ok_btn.setProperty("variant", "primary")
        ok_btn.setProperty("compact", True)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)
        self._restore_draft()
        self._connect_draft_autosave()
        # 题材/基调变化时也更新变体冲突提示
        self._genre.textChanged.connect(self._update_variant_conflict_hint)
        self._tone.textChanged.connect(self._update_variant_conflict_hint)
        self._update_variant_conflict_hint()

    def get_hint(self) -> str:
        return self._text_edit.toPlainText().strip()

    def get_constraints(self) -> dict[str, Any]:
        constraints: dict[str, Any] = {}
        genre = self._genre.text().strip()
        if genre:
            constraints["genre"] = genre
        tone = self._tone.text().strip()
        if tone:
            constraints["tone"] = tone
        if self._mode == "long":
            total_chapters = self._total_chapters.value()
            if total_chapters > 0:
                constraints["total_chapters"] = total_chapters
            words_per_chapter = self._words_per_chapter.value()
            if words_per_chapter > 0:
                constraints["words_per_chapter"] = words_per_chapter
        return constraints

    def get_generation_mode(self) -> str:
        return str(self._generation_mode.currentData() or "replace")

    def _update_mode_description(self) -> None:
        """Update the mode description label when the combobox selection changes."""
        mode_value = str(self._generation_mode.currentData() or "replace")
        desc = generation_mode_description(mode_value)
        label = str(self._generation_mode.currentText() or "")
        self._mode_description_label.setText(f"ℹ {label}：{desc}")

    def _update_variant_conflict_hint(self) -> None:
        """Show/hide the variant-mode conflict warning."""
        mode_value = str(self._generation_mode.currentData() or "replace")
        # 变体模式下若用户填写了题材/基调，提示冲突风险
        has_hard_params = bool(self._genre.text().strip() or self._tone.text().strip())
        self._variant_conflict_hint.setVisible(mode_value == "variant" and has_hard_params)

    def _on_random_new_theme(self) -> None:
        """Clear all inputs and select replace mode for full AI freedom."""
        self._text_edit.clear()
        # 切换到「重写新方案」
        replace_index = self._generation_mode.findData("replace")
        if replace_index >= 0:
            self._generation_mode.setCurrentIndex(replace_index)
        # 清空硬参数
        self._genre.clear()
        self._tone.clear()
        if self._mode == "long":
            self._total_chapters.setValue(0)
            self._words_per_chapter.setValue(0)
        # 重置创意控制到默认值
        self._creative_brief.clear()
        fresh_index = self._novelty.findData("fresh")
        if fresh_index >= 0:
            self._novelty.setCurrentIndex(fresh_index)
        layered_index = self._conflict_density.findData("layered")
        if layered_index >= 0:
            self._conflict_density.setCurrentIndex(layered_index)
        textured_index = self._emotion_density.findData("textured")
        if textured_index >= 0:
            self._emotion_density.setCurrentIndex(textured_index)

    def get_creative_profile(self) -> dict[str, Any]:
        return {
            "style": self._creative_style.currentData() or "balanced",
            "novelty": self._novelty.currentData() or "fresh",
            "conflict": self._conflict_density.currentData() or "layered",
            "emotion": self._emotion_density.currentData() or "textured",
            "custom_brief": self._creative_brief.text().strip(),
        }

    def build_enhanced_hint(self) -> str:
        hint = self.get_hint()
        constraints = self.get_constraints()
        if not constraints:
            return hint
        labels = {
            "genre": "题材",
            "tone": "基调",
            "total_chapters": "总章节数",
            "words_per_chapter": "每章字数",
        }
        parts = []
        for key, val in constraints.items():
            label = labels.get(key, key)
            parts.append(f"{label}：{val}")
        constraint_text = "；".join(parts)
        if hint:
            return f"{hint}\n\n【用户指定参数】{constraint_text}"
        return f"【用户指定参数】{constraint_text}"

    def _draft_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "hint": self.get_hint(),
            "generation_mode": self.get_generation_mode(),
            "creative_profile": self.get_creative_profile(),
            "genre": self._genre.text(),
            "tone": self._tone.text(),
        }
        if self._mode == "long":
            payload["total_chapters"] = self._total_chapters.value()
            payload["words_per_chapter"] = self._words_per_chapter.value()
        return payload

    def _restore_draft(self) -> None:
        draft = load_ai_input_draft(self._mode, AI_GENERATE_DRAFT_KEY)
        if not draft:
            return
        self._text_edit.setPlainText(str(draft.get("hint", "")))
        _set_combo_data(self._generation_mode, draft.get("generation_mode"))
        creative_profile = draft.get("creative_profile", {})
        if isinstance(creative_profile, dict):
            _set_combo_data(self._creative_style, creative_profile.get("style"))
            _set_combo_data(self._novelty, creative_profile.get("novelty"))
            _set_combo_data(self._conflict_density, creative_profile.get("conflict"))
            _set_combo_data(self._emotion_density, creative_profile.get("emotion"))
            self._creative_brief.setText(str(creative_profile.get("custom_brief", "")))
        if "genre" in draft:
            self._genre.setText(str(draft.get("genre", "")))
        if "tone" in draft:
            self._tone.setText(str(draft.get("tone", "")))
        if self._mode == "long":
            try:
                if "total_chapters" in draft:
                    self._total_chapters.setValue(int(draft.get("total_chapters") or 0))
                if "words_per_chapter" in draft:
                    self._words_per_chapter.setValue(int(draft.get("words_per_chapter") or 0))
            except (TypeError, ValueError):
                pass

    def _connect_draft_autosave(self) -> None:
        def save(*_args: object) -> None:
            self._save_draft()

        self._draft_save_callback = save
        self._text_edit.textChanged.connect(save)
        self._generation_mode.currentIndexChanged.connect(save)
        self._creative_style.currentIndexChanged.connect(save)
        self._novelty.currentIndexChanged.connect(save)
        self._conflict_density.currentIndexChanged.connect(save)
        self._emotion_density.currentIndexChanged.connect(save)
        self._creative_brief.textChanged.connect(save)
        self._genre.textChanged.connect(save)
        self._tone.textChanged.connect(save)
        if self._mode == "long":
            self._total_chapters.valueChanged.connect(save)
            self._words_per_chapter.valueChanged.connect(save)

    def _save_draft(self) -> None:
        try:
            save_ai_input_draft(self._mode, AI_GENERATE_DRAFT_KEY, self._draft_payload())
        except OSError:
            pass


class _AiPolishDialog(QDialog):
    """Dialog for directional AI polishing."""

    def __init__(
        self,
        mode: str = "short",
        *,
        suggestions: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._suggestions = _normalize_suggestion_list(suggestions or [])
        self._suggestion_checks: list[QCheckBox] = []
        self._focus_checks: list[tuple[str, QCheckBox]] = []

        self.setObjectName("aiDialog")
        self.setWindowTitle("AI 润色")
        self.setMinimumSize(620, 700 if mode == "long" else 680)
        self.resize(*smart_dialog_size(self, 620, 700 if mode == "long" else 680))
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)

        title = QLabel("输入你想强化的方向（例如：加强暧昧拉扯、提升悬疑密度、压缩解释性旁白）。")
        title.setObjectName("settingLabel")
        title.setWordWrap(True)
        layout.addWidget(title)

        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText(
            "例：让设定表达更清晰，人物动机更立体，冲突推进更有压迫感。"
        )
        self._text_edit.setMinimumHeight(90)
        self._text_edit.setMaximumHeight(130)
        layout.addWidget(self._text_edit)

        suggestion_header = QHBoxLayout()
        suggestion_header.setSpacing(8)
        suggestion_title = QLabel("润色灵感（可多选）")
        suggestion_title.setObjectName("settingLabel")
        suggestion_header.addWidget(suggestion_title)
        suggestion_header.addStretch()
        if self._suggestions:
            suggestion_header.addWidget(
                _make_selection_tool_button("全选灵感", lambda: self._set_suggestions_checked(True))
            )
            suggestion_header.addWidget(
                _make_selection_tool_button(
                    "清空灵感", lambda: self._set_suggestions_checked(False)
                )
            )
        layout.addLayout(suggestion_header)

        if self._suggestions:
            hint = QLabel("以下灵感来自最近一次「AI 随机生成」，可直接勾选后用于本轮润色。")
            hint.setObjectName("formFieldHint")
            hint.setWordWrap(True)
            layout.addWidget(hint)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMinimumHeight(150)
            scroll.setMaximumHeight(210)
            scroll_content = QWidget()
            scroll_content.setObjectName("aiPolishScrollContent")
            grid = QGridLayout(scroll_content)
            grid.setContentsMargins(6, 2, 6, 2)
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(8)
            for idx, option in enumerate(self._suggestions):
                checkbox = QCheckBox(option)
                checkbox.setObjectName("blueprintPanelToggle")
                checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
                self._suggestion_checks.append(checkbox)
                grid.addWidget(checkbox, idx // 2, idx % 2)
            grid.setRowStretch((len(self._suggestions) + 1) // 2, 1)
            scroll.setWidget(scroll_content)
            layout.addWidget(scroll)
        else:
            empty = QLabel(
                "暂无灵感选项。你可以直接输入润色指令，或先执行一次「AI 随机生成」后再回来润色。"
            )
            empty.setObjectName("formFieldHint")
            empty.setWordWrap(True)
            layout.addWidget(empty)

        focus_header = QHBoxLayout()
        focus_header.setSpacing(8)
        focus_title = QLabel("重点润色字段（可多选）")
        focus_title.setObjectName("settingLabel")
        focus_header.addWidget(focus_title)
        focus_header.addStretch()
        focus_header.addWidget(
            _make_selection_tool_button("全选字段", lambda: self._set_focus_checked(True))
        )
        focus_header.addWidget(
            _make_selection_tool_button("清空字段", lambda: self._set_focus_checked(False))
        )
        layout.addLayout(focus_header)
        focus_hint = QLabel("默认已勾选标题、核心梗概、人物、世界观、冲突与叙事常用字段。")
        focus_hint.setObjectName("formFieldHint")
        focus_hint.setWordWrap(True)
        layout.addWidget(focus_hint)

        focus_widget = QWidget()
        focus_grid = QGridLayout(focus_widget)
        focus_grid.setContentsMargins(6, 2, 6, 2)
        focus_grid.setHorizontalSpacing(10)
        focus_grid.setVerticalSpacing(8)
        focus_options = [
            (field_name, get_ai_field_label(field_name, mode))
            for field_name in polishable_fields_for_mode(mode)
        ]
        for idx, (field_name, label_text) in enumerate(focus_options):
            checkbox = QCheckBox(label_text)
            checkbox.setObjectName("blueprintPanelToggle")
            checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            checkbox.setChecked(True)
            self._focus_checks.append((field_name, checkbox))
            focus_grid.addWidget(checkbox, idx // 2, idx % 2)
        layout.addWidget(focus_widget)

        note = QLabel(
            "AI 可同步优化标题、故事前提/主题，并整体增强角色、世界观、冲突与叙事等要素，"
            "并更新下一轮可用的润色灵感。确认应用后会自动保存为预设，避免创意丢失。"
        )
        note.setObjectName("formFieldHint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("actionButton")
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setProperty("compact", True)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("开始润色")
        ok_btn.setObjectName("actionButton")
        ok_btn.setProperty("variant", "primary")
        ok_btn.setProperty("compact", True)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)
        self._restore_draft()
        self._connect_draft_autosave()

    def get_hint(self) -> str:
        return self._text_edit.toPlainText().strip()

    def get_selected_suggestions(self) -> list[str]:
        return [
            cb.text().strip()
            for cb in self._suggestion_checks
            if cb.isChecked() and cb.text().strip()
        ]

    def get_focus_fields(self) -> list[str]:
        return [field_name for field_name, cb in self._focus_checks if cb.isChecked()]

    def _set_suggestions_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for checkbox in self._suggestion_checks:
            checkbox.setCheckState(state)

    def _set_focus_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for _, checkbox in self._focus_checks:
            checkbox.setCheckState(state)

    def _draft_payload(self) -> dict[str, Any]:
        return {
            "hint": self.get_hint(),
            "selected_suggestions": self.get_selected_suggestions(),
            "focus_fields": self.get_focus_fields(),
        }

    def _restore_draft(self) -> None:
        draft = load_ai_input_draft(self._mode, AI_POLISH_DRAFT_KEY)
        if not draft:
            return
        self._text_edit.setPlainText(str(draft.get("hint", "")))

        selected_suggestions = draft.get("selected_suggestions")
        if isinstance(selected_suggestions, list):
            selected = {str(item) for item in selected_suggestions}
            for checkbox in self._suggestion_checks:
                checkbox.setChecked(checkbox.text().strip() in selected)

        focus_fields = draft.get("focus_fields")
        if isinstance(focus_fields, list):
            selected_fields = {str(item) for item in focus_fields}
            for field_name, checkbox in self._focus_checks:
                checkbox.setChecked(field_name in selected_fields)

    def _connect_draft_autosave(self) -> None:
        def save(*_args: object) -> None:
            self._save_draft()

        self._draft_save_callback = save
        self._text_edit.textChanged.connect(save)
        for checkbox in self._suggestion_checks:
            checkbox.stateChanged.connect(save)
        for _, checkbox in self._focus_checks:
            checkbox.stateChanged.connect(save)

    def _save_draft(self) -> None:
        try:
            save_ai_input_draft(self._mode, AI_POLISH_DRAFT_KEY, self._draft_payload())
        except OSError:
            pass


_PROTECTED_DIFF_FIELDS = {
    AI_POLISH_SUGGESTIONS_FIELD,
    "blueprint_element_preferences",
    "segment_trigger_words",
}


def _is_hidden_diff_field(key: str) -> bool:
    return key.startswith("_") or key in _PROTECTED_DIFF_FIELDS


def _diff_value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _compute_field_diffs(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compute per-field diffs between two config dicts, returning changed fields only."""
    diffs: list[dict[str, Any]] = []
    all_keys = set(before.keys()) | set(after.keys())
    for key in sorted(all_keys):
        if _is_hidden_diff_field(key):
            continue
        if key in before and key not in after:
            continue
        old_val = _diff_value_to_text(before.get(key, "")).strip()
        new_val = _diff_value_to_text(after.get(key, "")).strip()
        if old_val == new_val:
            continue
        if not old_val and not new_val:
            continue
        label = get_ai_field_label(key)
        diffs.append({"key": key, "label": label, "old": old_val, "new": new_val})
    return diffs


def _history_text(value: Any, *, limit: int = 96) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _history_items(value: Any, *, limit: int = 2) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, dict):
        raw_items = value.values()
    elif value:
        raw_items = [value]
    else:
        raw_items = []
    result: list[str] = []
    for item in raw_items:
        text = _history_text(item)
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _history_field_labels(value: Any, *, limit: int = 6) -> str:
    if not isinstance(value, list):
        return ""
    labels = [get_ai_field_label(str(field)) for field in value[:limit]]
    suffix = "…" if len(value) > limit else ""
    return "、".join(labels) + suffix


class _HistoryDialog(QDialog):
    """Dialog showing AI generate/polish history for a preset, with restore capability."""

    selected_data: dict[str, Any] | None = None

    def __init__(
        self,
        mode: str,
        preset_name: str,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.selected_data = None
        self._mode = mode
        self._preset_name = preset_name
        self.setObjectName("aiDialog")
        self.setWindowTitle(f"AI 创作札记 — {preset_name}")
        self.setMinimumSize(640, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        header = QLabel(f"「{preset_name}」的 AI 构思与润色札记")
        header.setObjectName("settingLabel")
        header.setWordWrap(True)
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(300)
        scroll_content = QWidget()
        self._entry_layout = QVBoxLayout(scroll_content)
        self._entry_layout.setContentsMargins(0, 0, 0, 0)
        self._entry_layout.setSpacing(6)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        self._entries = load_polish_history(mode, preset_name)
        if not self._entries:
            empty = QLabel("暂无创作札记。执行 AI 生成或 AI 润色后会自动创建记录。")
            empty.setObjectName("formFieldHint")
            empty.setWordWrap(True)
            self._entry_layout.addWidget(empty)
        else:
            self._populate_entries()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()
        clear_btn = QPushButton("清空札记")
        clear_btn.setObjectName("actionButton")
        clear_btn.setProperty("variant", "secondary")
        clear_btn.setProperty("compact", True)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(clear_btn)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("actionButton")
        close_btn.setProperty("variant", "primary")
        close_btn.setProperty("compact", True)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _populate_entries(self) -> None:
        for entry in self._entries:
            group = QGroupBox()
            group_layout = QVBoxLayout(group)
            group_layout.setContentsMargins(8, 6, 8, 6)
            group_layout.setSpacing(4)

            ts = entry.get("timestamp", "")
            op = entry.get("operation", "unknown")
            op_label = (
                "🎲 AI 随机生成"
                if op == "generate"
                else ("✨ AI 润色" if "polish" in op else f"📝 {op}")
            )
            ts_display = ts
            for fmt in ("%Y%m%dT%H%M%S%fZ", "%Y%m%dT%H%M%SZ"):
                try:
                    dt = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
                    ts_display = dt.astimezone().strftime("%Y-%m-%d %H:%M")
                    break
                except ValueError:
                    continue

            summary = f"{ts_display}  {op_label}"
            hint = entry.get("hint", "")
            if hint:
                summary += f"\n润色方向: {hint[:80]}"
            suggestions = entry.get("selected_suggestions", [])
            if suggestions:
                summary += f"\n灵感: {'、'.join(suggestions[:3])}"
            metadata = entry.get("metadata", {})
            if isinstance(metadata, dict):
                generation_mode = str(metadata.get("generation_mode", "") or "").strip()
                creative_profile = metadata.get("creative_profile")
                if generation_mode:
                    mode_labels = {
                        value: label for label, value, _prompt in generation_mode_options()
                    }
                    summary += f"\n生成方式: {mode_labels.get(generation_mode, generation_mode)}"
                if isinstance(creative_profile, dict) and creative_profile.get("style"):
                    style_labels = {
                        value: label for label, value, _prompt in creative_axis_options("style")
                    }
                    style = str(creative_profile.get("style") or "")
                    summary += f"\n创意取向: {style_labels.get(style, style)}"
                    custom_brief = _history_text(creative_profile.get("custom_brief"), limit=120)
                    if custom_brief:
                        summary += f"\n自由侧重点: {custom_brief}"
                accepted = _history_field_labels(metadata.get("accepted_fields"))
                rejected = _history_field_labels(metadata.get("rejected_fields"))
                if accepted:
                    summary += f"\n已采纳字段: {accepted}"
                if rejected:
                    summary += f"\n未采纳字段: {rejected}"
                creative_note = metadata.get("creative_note")
                if isinstance(creative_note, dict):
                    core_pitch = _history_text(creative_note.get("core_pitch"), limit=120)
                    design_intent = _history_text(
                        creative_note.get("design_intent"),
                        limit=120,
                    )
                    next_moves = _history_items(creative_note.get("next_moves"))
                    risks = _history_items(creative_note.get("risks"), limit=1)
                    anti_drift = _history_items(creative_note.get("anti_drift_check"), limit=1)
                    if core_pitch:
                        summary += f"\n核心卖点: {core_pitch}"
                    if design_intent:
                        summary += f"\n设计意图: {design_intent}"
                    if next_moves:
                        summary += f"\n后续方向: {'；'.join(next_moves)}"
                    if risks:
                        summary += f"\n注意事项: {'；'.join(risks)}"
                    if anti_drift:
                        summary += f"\n边界检查: {'；'.join(anti_drift)}"

            summary_label = QLabel(summary)
            summary_label.setObjectName("formFieldHint")
            summary_label.setWordWrap(True)
            group_layout.addWidget(summary_label)

            btn_row = QHBoxLayout()
            btn_row.addStretch()
            restore_btn = QPushButton("恢复此版本")
            restore_btn.setObjectName("actionButton")
            restore_btn.setProperty("variant", "primary")
            restore_btn.setProperty("compact", True)
            restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            restore_btn.clicked.connect(lambda _checked, e=entry: self._on_restore(e))
            btn_row.addWidget(restore_btn)

            del_btn = QPushButton("删除")
            del_btn.setObjectName("actionButton")
            del_btn.setProperty("variant", "secondary")
            del_btn.setProperty("compact", True)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            filename = entry.get("_file", "")
            del_btn.clicked.connect(lambda _checked, fn=filename: self._on_delete(fn))
            btn_row.addWidget(del_btn)
            group_layout.addLayout(btn_row)

            self._entry_layout.addWidget(group)

        self._entry_layout.addStretch()

    def _on_restore(self, entry: dict[str, Any]) -> None:
        data = entry.get("data", {})
        if data:
            self.selected_data = data
            self.accept()

    def _on_delete(self, filename: str) -> None:
        if not filename:
            return
        confirmed = ask_confirmation(
            self,
            "删除记录",
            "确定要删除这条历史记录吗？\n\n此操作不可撤销。",
        )
        if not confirmed:
            return
        try:
            delete_polish_history(self._mode, self._preset_name, filename)
        except OSError:
            show_warning_message(self, "删除失败", "无法删除历史记录文件。")
            return
        self._entries = [e for e in self._entries if e.get("_file") != filename]
        self.reject()
        dialog = _HistoryDialog(self._mode, self._preset_name, parent=self.parentWidget())
        dialog.exec()

    def _on_clear(self) -> None:
        confirmed = ask_confirmation(
            self,
            "清空札记",
            f"确定要清空「{self._preset_name}」的所有 AI 创作札记吗？\n\n此操作不可撤销。",
        )
        if not confirmed:
            return
        from novel_forge.desktop.preset_manager import clear_history_for_preset

        try:
            count = clear_history_for_preset(self._mode, self._preset_name)
        except OSError:
            show_warning_message(self, "清空失败", "无法清空历史记录文件。")
            return
        show_info_message(
            self,
            "已清空",
            f"已删除 {count} 条创作札记。",
        )
        self.reject()


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _html_text_block(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return '<span style="color:#9a8878;">（空）</span>'
    return _esc(value).replace("\n", "<br>")


def _inline_diff_html(old: str, new: str) -> str:
    sm = difflib.SequenceMatcher(None, str(old), str(new))
    parts: list[str] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            parts.append(_esc(str(old)[i1:i2]))
        elif tag == "delete":
            parts.append(
                '<span style="background:#fff0eb;color:#8f392a;'
                'text-decoration:line-through;">'
                f"{_esc(str(old)[i1:i2])}</span>"
            )
        elif tag == "insert":
            parts.append(
                '<span style="background:#eef8f0;'
                'color:#2f6d4c;font-weight:600;">'
                f"{_esc(str(new)[j1:j2])}</span>"
            )
        elif tag == "replace":
            parts.append(
                '<span style="background:#fff0eb;color:#8f392a;'
                'text-decoration:line-through;">'
                f"{_esc(str(old)[i1:i2])}</span>"
            )
            parts.append(
                '<span style="background:#eef8f0;'
                'color:#2f6d4c;font-weight:600;">'
                f"{_esc(str(new)[j1:j2])}</span>"
            )
    return "".join(parts)


class _DiffFieldItemWidget(QWidget):
    toggled = Signal(bool)

    def __init__(self, label: str, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("diffFieldItem")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 7, 8, 7)
        layout.setSpacing(10)

        self._check_btn = QPushButton("✓")
        self._check_btn.setObjectName("diffCheckButton")
        self._check_btn.setCheckable(True)
        self._check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_btn.setAccessibleName(f"选择变更：{label}")
        self._check_btn.toggled.connect(self._on_button_toggled)
        layout.addWidget(self._check_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        label_widget = QLabel(label)
        label_widget.setObjectName("diffFieldLabel")
        label_widget.setToolTip(f"{label}（{key}）")
        key_widget = QLabel(key)
        key_widget.setObjectName("diffFieldKey")
        key_widget.setToolTip(key)
        text_col.addWidget(label_widget)
        text_col.addWidget(key_widget)
        layout.addLayout(text_col, 1)

    def set_checked(self, checked: bool) -> None:
        self._check_btn.blockSignals(True)
        self._check_btn.setChecked(checked)
        self._check_btn.setText("✓" if checked else "")
        self._check_btn.blockSignals(False)

    def _on_button_toggled(self, checked: bool) -> None:
        self._check_btn.setText("✓" if checked else "")
        self.toggled.emit(checked)


class _DiffDialog(QDialog):
    rejected_fields: list[str]
    accepted_fields: list[str]

    def __init__(
        self,
        diffs: list[dict[str, Any]],
        *,
        window_title: str = "AI 润色变更预览",
        title_text: str | None = None,
        subtitle: str = "勾选左侧字段后应用；未勾选字段会保留原值。",
        accept_verb: str = "应用",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.rejected_fields = []
        self.accepted_fields = []
        self._diffs = diffs
        self._diff_items: list[QListWidgetItem] = []
        self._accept_btn: QPushButton | None = None
        self._selection_summary: QLabel | None = None
        self._accept_verb = accept_verb

        self.setObjectName("aiDialog")
        self.setProperty("dialogRole", "diffPreview")
        self.setWindowTitle(window_title)
        self.setMinimumSize(960, 620)
        self.resize(*smart_dialog_size(self, 1080, 700))
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        title = QLabel(title_text or f"AI 润色修改了 {len(diffs)} 个字段")
        title.setObjectName("diffDialogTitle")
        title.setWordWrap(True)
        header_layout.addWidget(title)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("diffDialogSubtitle")
        subtitle_label.setWordWrap(True)
        header_layout.addWidget(subtitle_label)
        layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("diffSplitter")
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)

        left = QFrame()
        left.setObjectName("diffPanel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        selection_row = QHBoxLayout()
        selection_row.setSpacing(8)
        self._selection_summary = QLabel("")
        self._selection_summary.setObjectName("diffSelectionBadge")
        selection_row.addWidget(self._selection_summary)
        selection_row.addStretch()
        selection_row.addWidget(
            _make_selection_tool_button("全选变更", lambda: self._set_all_diff_items(True))
        )
        selection_row.addWidget(
            _make_selection_tool_button("清空变更", lambda: self._set_all_diff_items(False))
        )
        selection_row.addWidget(_make_selection_tool_button("反选", self._invert_diff_items))
        left_layout.addLayout(selection_row)

        self._field_list = QListWidget()
        self._field_list.setObjectName("diffFieldList")
        self._field_list.setUniformItemSizes(True)
        self._field_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._field_list.setAlternatingRowColors(False)
        for diff in diffs:
            item = QListWidgetItem("")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, diff)
            item.setSizeHint(QSize(0, 58))
            item.setToolTip(f"{diff['label']}（{diff['key']}）")
            self._field_list.addItem(item)
            field_widget = _DiffFieldItemWidget(str(diff["label"]), str(diff["key"]))
            field_widget.set_checked(True)
            field_widget.toggled.connect(
                lambda checked, item=item: self._set_diff_item_checked(item, checked)
            )
            self._field_list.setItemWidget(item, field_widget)
            self._diff_items.append(item)
        self._field_list.itemChanged.connect(self._on_diff_item_changed)
        left_layout.addWidget(self._field_list, 1)

        splitter.addWidget(left)

        right = QFrame()
        right.setObjectName("diffPanel")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 14, 16, 16)
        right_layout.setSpacing(10)

        self._diff_title = QLabel("")
        self._diff_title.setObjectName("diffDialogTitle")
        right_layout.addWidget(self._diff_title)

        self._diff_detail = QTextBrowser()
        self._diff_detail.setObjectName("diffDetail")
        self._diff_detail.setOpenExternalLinks(False)
        self._diff_detail_renderer = IncrementalDocumentRenderer(self._diff_detail)
        right_layout.addWidget(self._diff_detail, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([330, 730])
        layout.addWidget(splitter, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()
        reject_btn = QPushButton("全部拒绝")
        reject_btn.setObjectName("actionButton")
        reject_btn.setProperty("variant", "secondary")
        reject_btn.setProperty("compact", True)
        reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reject_btn.clicked.connect(self.reject)
        btn_row.addWidget(reject_btn)
        self._accept_btn = QPushButton("应用选中变更")
        self._accept_btn.setObjectName("actionButton")
        self._accept_btn.setProperty("variant", "primary")
        self._accept_btn.setProperty("compact", True)
        self._accept_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._accept_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(self._accept_btn)
        layout.addLayout(btn_row)

        self._field_list.currentRowChanged.connect(self._show_current_diff)
        if self._field_list.count() > 0:
            self._field_list.setCurrentRow(0)
        self._update_selection_summary()

    def _show_current_diff(self, row: int) -> None:
        if row < 0 or row >= len(self._diffs):
            self._diff_detail_renderer.update_content("")
            self._diff_title.setText("")
            return
        diff = self._diffs[row]
        self._diff_title.setText(f"{diff['label']}（{diff['key']}）")
        old_text = str(diff.get("old", ""))
        new_text = str(diff.get("new", ""))
        inline = _inline_diff_html(old_text, new_text).replace("\n", "<br>")
        detail_html = f"""
        <div style="font-family:'PingFang SC','Microsoft YaHei',system-ui,sans-serif;
                    color:#30231b;line-height:1.65;">
          <div style="font-size:12px;color:#8b7460;margin-bottom:12px;">
            当前预览仅展示右侧字段差异，最终应用范围以左侧勾选为准。
          </div>
          <div style="font-size:12px;font-weight:700;color:#5a4837;margin:4px 0 6px;">
            原内容
          </div>
          <div style="background:#fff8f1;border:1px solid #eaded2;border-radius:6px;
                      padding:11px 12px;margin-bottom:14px;">
            {_html_text_block(old_text)}
          </div>
          <div style="font-size:12px;font-weight:700;color:#5a4837;margin:4px 0 6px;">
            润色后
          </div>
          <div style="background:#f3fbf4;border:1px solid #d7eadb;border-radius:6px;
                      padding:11px 12px;margin-bottom:14px;">
            {_html_text_block(new_text)}
          </div>
          <div style="font-size:12px;font-weight:700;color:#5a4837;margin:4px 0 6px;">
            行内对比
          </div>
          <div style="background:#ffffff;border:1px solid #e8e0d8;border-radius:6px;
                      padding:11px 12px;line-height:1.75;">
            {inline or _html_text_block(new_text)}
          </div>
        </div>
        """
        self._diff_detail_renderer.update_content(detail_html)

    def _set_all_diff_items(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in self._diff_items:
            item.setCheckState(state)
        self._update_selection_summary()

    def _invert_diff_items(self) -> None:
        for item in self._diff_items:
            next_state = (
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
            item.setCheckState(next_state)
        self._update_selection_summary()

    def _set_diff_item_checked(self, item: QListWidgetItem, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        if item.checkState() != state:
            item.setCheckState(state)
        else:
            self._on_diff_item_changed(item)
        self._update_selection_summary()

    def _on_diff_item_changed(self, item: QListWidgetItem) -> None:
        widget = self._field_list.itemWidget(item)
        if isinstance(widget, _DiffFieldItemWidget):
            widget.set_checked(item.checkState() == Qt.CheckState.Checked)
        self._update_selection_summary()

    def _selected_count(self) -> int:
        return sum(1 for item in self._diff_items if item.checkState() == Qt.CheckState.Checked)

    def _update_selection_summary(self) -> None:
        selected = self._selected_count()
        total = len(self._diff_items)
        if self._selection_summary is not None:
            self._selection_summary.setText(f"已选择 {selected}/{total} 项")
        if self._accept_btn is not None:
            self._accept_btn.setText(f"{self._accept_verb} {selected} 项变更")
            self._accept_btn.setEnabled(selected > 0)

    def _on_accept(self) -> None:
        self.rejected_fields = []
        self.accepted_fields = []
        for item in self._diff_items:
            diff = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(diff, dict):
                continue
            if item.checkState() != Qt.CheckState.Checked:
                self.rejected_fields.append(str(diff["key"]))
            else:
                self.accepted_fields.append(str(diff["key"]))
        self.accept()
