"""Supporting widgets and workers extracted from the settings page."""

from __future__ import annotations

import importlib
import inspect
import json
import time
from collections.abc import Callable
from typing import Literal, Protocol, cast

from PySide6.QtCore import (
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QCloseEvent, QColor, QDropEvent, QMouseEvent, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from novel_forge.common.constants import TaskType
from novel_forge.desktop.components.dialogs import show_warning_message
from novel_forge.desktop.widgets import ActionButton, Badge, Surface
from novel_forge.desktop.workers.base import BaseJobWorker, BaseJobWorkerSignals
from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.gateway.profiles import (
    KNOWN_PROVIDERS,
    TONGYI_CODING_PLAN_BASE_URL,
    TONGYI_TOKEN_PLAN_BASE_URL,
    ModelProfile,
    ModelThinkingCapability,
    TaskRouteEntry,
    get_model_capabilities,
    get_model_thinking_capability,
)


class _HealthCheckAdapter(Protocol):
    last_health_error: str | None

    async def health_check(self) -> bool: ...


class _ModelStatusCard(Surface):
    """Compact card showing model connection status with capability indicators."""

    # Task 20 — Surface("card") has no QGraphicsDropShadowEffect, so the D10
    # single-effect constraint does not apply and we can install
    # QGraphicsOpacityEffect directly (no swap-and-restore needed).
    _STATUS_FLASH_DURATION_MS: int = 200

    def __init__(self, profile: ModelProfile, parent: QWidget | None = None) -> None:
        super().__init__("card", parent)
        self.profile_id = profile.profile_id
        self.setMinimumWidth(220)
        self._status_flash_anim: QPropertyAnimation | None = None
        self._status_flash_effect: QGraphicsOpacityEffect | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)
        self._dot = QLabel()
        self._dot.setFixedSize(10, 10)
        self._dot.setObjectName("statusDotYellow")
        top.addWidget(self._dot)

        name = QLabel(profile.display_name)
        name.setObjectName("modelNameLabel")
        name.setToolTip(f"{profile.provider}:{profile.model_id}")
        top.addWidget(name, 1)
        if is_embedding_model(profile.provider, profile.model_id):
            top.addWidget(Badge("\U0001f9e0 嵌入", tone="muted"))
        elif not profile.is_key_configured:
            top.addWidget(Badge("\U0001f512 不可调用", tone="muted"))
        layout.addLayout(top)

        prov_label = KNOWN_PROVIDERS.get(profile.provider, {}).get("label", profile.provider)
        self._prov_text = prov_label
        self._status_label = QLabel(f"{prov_label} · 待检测")
        self._status_label.setObjectName("statusMeta")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        cap_row = QHBoxLayout()
        cap_row.setSpacing(6)
        cap_row.setContentsMargins(0, 2, 0, 0)

        self._think_dot = QLabel()
        self._think_dot.setFixedSize(8, 8)
        self._think_dot.setObjectName("capDotPending")
        cap_row.addWidget(self._think_dot)
        think_label = QLabel("思考")
        think_label.setObjectName("capLabel")
        cap_row.addWidget(think_label)

        cap_row.addSpacing(8)

        self._multi_dot = QLabel()
        self._multi_dot.setFixedSize(8, 8)
        self._multi_dot.setObjectName("capDotPending")
        cap_row.addWidget(self._multi_dot)
        multi_label = QLabel("多轮")
        multi_label.setObjectName("capLabel")
        cap_row.addWidget(multi_label)

        cap_row.addStretch()
        layout.addLayout(cap_row)

    def set_result(self, success: bool, detail: str) -> None:
        """Update card with connection test result."""
        self._set_result(success, detail, flash=True)

    def set_background_result(self, success: bool, detail: str) -> None:
        """Update card from an automatic background test without animation."""
        self._set_result(success, detail, flash=False)

    def set_cached_result(self, success: bool, detail: str) -> None:
        """Update card from a cached result without playing a transition."""
        self._set_result(success, detail, flash=False)

    def set_stale_cached_result(self, success: bool, detail: str) -> None:
        """Keep the previous result visible while a background refresh runs."""
        self._set_result(success, f"上次{detail}，后台复测中", flash=False)

    def _set_result(self, success: bool, detail: str, *, flash: bool) -> None:
        if success:
            self._dot.setObjectName("statusDotGreen")
            self._status_label.setText(f"{self._prov_text} · {detail}")
            self._status_label.setToolTip("")
        else:
            self._dot.setObjectName("statusDotRed")
            short = detail if len(detail) <= 50 else detail[:47] + "..."
            self._status_label.setText(f"{self._prov_text} · {short}")
            self._status_label.setToolTip(detail)
        self._dot.style().unpolish(self._dot)
        self._dot.style().polish(self._dot)
        if flash:
            self._play_status_flash()

    def set_pending(self, detail: str = "检测中…", *, flash: bool = True) -> None:
        """Reset card visuals before a fresh connection test starts."""
        self._apply_pending_state(detail)
        if flash:
            self._play_status_flash()

    def set_queued(self, detail: str = "等待检测…") -> None:
        """Show that the card is queued without starting a costly flash animation."""
        self._apply_pending_state(detail)

    def _apply_pending_state(self, detail: str) -> None:
        self._dot.setObjectName("statusDotYellow")
        self._status_label.setText(f"{self._prov_text} · {detail}")
        self._status_label.setToolTip("")
        self._dot.style().unpolish(self._dot)
        self._dot.style().polish(self._dot)
        for dot in (self._think_dot, self._multi_dot):
            dot.setObjectName("capDotPending")
            dot.style().unpolish(dot)
            dot.style().polish(dot)

    def _play_status_flash(self) -> None:
        """Play a 200ms opacity flash to highlight the status transition.

        Animation shape: 1.0 → 0.4 → 1.0 (single QPropertyAnimation with three
        keyframes).  Reads as a soft fade-down + fade-up so the eye notices the
        colour change of the status dot without losing its context.

        Idempotent: cancels any in-flight flash before starting a new one.
        No-op when ANIMATIONS_ENABLED is False or when platform guards disable
        opacity animations (D1).
        """
        from novel_forge.desktop import constants
        from novel_forge.desktop.motion import animations_supported

        if not constants.ANIMATIONS_ENABLED:
            return
        if not animations_supported("opacity"):
            return

        if self._status_flash_anim is not None:
            self._status_flash_anim.stop()
            self._status_flash_anim = None

        if self._status_flash_effect is None:
            self._status_flash_effect = QGraphicsOpacityEffect(self)
            self._status_flash_effect.setOpacity(1.0)
            self.setGraphicsEffect(self._status_flash_effect)

        anim = QPropertyAnimation(self._status_flash_effect, b"opacity")
        anim.setDuration(self._STATUS_FLASH_DURATION_MS)
        anim.setKeyValueAt(0.0, 1.0)
        anim.setKeyValueAt(0.5, 0.4)
        anim.setKeyValueAt(1.0, 1.0)
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._status_flash_anim = anim

        def _on_finished() -> None:
            if self._status_flash_anim is anim:
                self._status_flash_anim = None

        anim.finished.connect(_on_finished)

    def set_capabilities(self, can_thinking: bool, can_multi_turn: bool) -> None:
        """Update capability indicator dots after detection."""
        self._think_dot.setObjectName("capDotGreen" if can_thinking else "capDotRed")
        self._think_dot.style().unpolish(self._think_dot)
        self._think_dot.style().polish(self._think_dot)
        self._multi_dot.setObjectName("capDotGreen" if can_multi_turn else "capDotRed")
        self._multi_dot.style().unpolish(self._multi_dot)
        self._multi_dot.style().polish(self._multi_dot)


class _ModelManageCard(Surface):
    """Card for a single model profile in the management section."""

    edit_clicked = Signal(str)
    delete_clicked = Signal(str)
    test_clicked = Signal(str)

    def __init__(self, profile: ModelProfile, parent: QWidget | None = None) -> None:
        super().__init__("card", parent)
        self._profile_id = profile.profile_id
        is_embedding = is_embedding_model(profile.provider, profile.model_id)

        # Get model capabilities from profiles.py
        from novel_forge.gateway.profiles import get_model_capabilities

        can_thinking, can_multi_turn = get_model_capabilities(profile.provider, profile.model_id)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(6)

        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setObjectName("statusDotGreen" if profile.is_key_configured else "statusDotRed")
        top.addWidget(dot)

        title = QLabel(profile.display_name)
        title.setObjectName("cardTitle")
        top.addWidget(title, 1)

        prov_badge = Badge(
            KNOWN_PROVIDERS.get(profile.provider, {}).get("label", profile.provider),
            tone="default",
        )
        layout.addLayout(top)

        meta = QLabel(
            f"模型: {profile.model_id}  ·  密钥: {profile.masked_key}"
            + (f"  ·  接口: {profile.base_url}" if profile.base_url else "")
        )
        meta.setObjectName("cardMeta")
        meta.setWordWrap(True)
        layout.addWidget(meta)

        # Capability indicators row
        cap_row = QHBoxLayout()
        cap_row.setSpacing(6)
        cap_row.setContentsMargins(0, 2, 0, 0)

        # Thinking capability
        think_dot = QLabel()
        think_dot.setFixedSize(8, 8)
        think_dot.setObjectName("capDotGreen" if can_thinking else "capDotRed")
        cap_row.addWidget(think_dot)
        think_label = QLabel("思考")
        think_label.setObjectName("capLabel")
        cap_row.addWidget(think_label)

        cap_row.addSpacing(8)

        # Multi-turn capability
        multi_dot = QLabel()
        multi_dot.setFixedSize(8, 8)
        multi_dot.setObjectName("capDotGreen" if can_multi_turn else "capDotRed")
        cap_row.addWidget(multi_dot)
        multi_label = QLabel("多轮")
        multi_label.setObjectName("capLabel")
        cap_row.addWidget(multi_label)

        cap_row.addStretch()
        footer_row = QHBoxLayout()
        footer_row.setSpacing(10)
        footer_row.addLayout(cap_row, 1)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        action_row.addWidget(prov_badge)
        action_row.addWidget(
            Badge(
                "嵌入模型" if is_embedding else "生成模型",
                tone="muted" if is_embedding else "default",
            )
        )
        if not profile.is_key_configured:
            action_row.addWidget(Badge("🔒 不可调用", tone="muted"))

        test_btn = ActionButton("测试连接", variant="secondary")
        test_btn.setProperty("compact", True)
        test_btn.clicked.connect(lambda: self.test_clicked.emit(self._profile_id))
        action_row.addWidget(test_btn)

        edit_btn = ActionButton("编辑", variant="secondary")
        edit_btn.setProperty("compact", True)
        edit_btn.clicked.connect(lambda: self.edit_clicked.emit(self._profile_id))
        action_row.addWidget(edit_btn)

        del_btn = ActionButton("删除", variant="danger")
        del_btn.setProperty("compact", True)
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(self._profile_id))
        action_row.addWidget(del_btn)

        footer_row.addLayout(action_row)
        layout.addLayout(footer_row)


class _ModelDialog(QDialog):
    """Dialog for adding or editing a model profile."""

    def __init__(
        self,
        profile: ModelProfile | None = None,
        existing_ids: list[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self._existing_ids = set(existing_ids or [])
        self._editing = profile is not None
        self._initial_signature = ""
        self.setWindowTitle("编辑模型" if self._editing else "添加模型")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 18, 20, 18)

        prov_row = QHBoxLayout()
        prov_row.setSpacing(10)
        prov_label = QLabel("供应商")
        prov_label.setObjectName("settingLabel")
        prov_label.setFixedWidth(80)
        prov_row.addWidget(prov_label)
        self._provider_combo = QComboBox()
        for prov_id, info in KNOWN_PROVIDERS.items():
            self._provider_combo.addItem(info["label"], prov_id)
        self._provider_combo.addItem("自定义 OpenAI 兼容", "custom")
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        prov_row.addWidget(self._provider_combo, 1)
        layout.addLayout(prov_row)

        model_row = QHBoxLayout()
        model_row.setSpacing(10)
        model_lbl = QLabel("模型")
        model_lbl.setObjectName("settingLabel")
        model_lbl.setFixedWidth(80)
        model_row.addWidget(model_lbl)
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        model_row.addWidget(self._model_combo, 1)
        layout.addLayout(model_row)

        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        name_lbl = QLabel("显示名称")
        name_lbl.setObjectName("settingLabel")
        name_lbl.setFixedWidth(80)
        name_row.addWidget(name_lbl)
        self._display_name = QLineEdit()
        self._display_name.setPlaceholderText("可选，留空自动生成")
        name_row.addWidget(self._display_name, 1)
        layout.addLayout(name_row)

        key_row = QHBoxLayout()
        key_row.setSpacing(10)
        key_lbl = QLabel("API Key")
        key_lbl.setObjectName("settingLabel")
        key_lbl.setFixedWidth(80)
        key_row.addWidget(key_lbl)
        self._api_key = QLineEdit()
        self._api_key.setPlaceholderText("sk-...")
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        key_row.addWidget(self._api_key, 1)

        self._show_key_btn = ActionButton("显示", variant="secondary")
        self._show_key_btn.setProperty("compact", True)
        self._show_key_btn.setMinimumWidth(88)
        self._show_key_btn.clicked.connect(self._toggle_key_visibility)
        key_row.addWidget(self._show_key_btn)
        layout.addLayout(key_row)

        url_row = QHBoxLayout()
        url_row.setSpacing(10)
        url_lbl = QLabel("接口地址")
        url_lbl.setObjectName("settingLabel")
        url_lbl.setFixedWidth(80)
        url_row.addWidget(url_lbl)
        self._base_url = QLineEdit()
        self._base_url.setPlaceholderText("（可选）自定义接口地址，默认留空")
        url_row.addWidget(self._base_url, 1)
        layout.addLayout(url_row)

        hint = QLabel(
            "同一供应商的不同模型可共用相同的 API Key。"
            "添加后可在「流程路由」中为各个步骤指定使用此模型。"
        )
        hint.setObjectName("formFieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addStretch()

        cancel_btn = ActionButton("取消", variant="secondary")
        cancel_btn.setProperty("compact", True)
        cancel_btn.setMinimumWidth(96)
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)

        submit_text = "保存修改" if self._editing else "添加模型"
        submit_btn = ActionButton(submit_text, variant="primary")
        submit_btn.setProperty("compact", True)
        submit_btn.setMinimumWidth(120)
        submit_btn.setDefault(True)
        submit_btn.setAutoDefault(True)
        submit_btn.clicked.connect(self._on_accept)
        button_row.addWidget(submit_btn)
        layout.addLayout(button_row)

        self._sync_show_key_btn_width()
        self._on_provider_changed()
        if profile is not None:
            self._fill_from_profile(profile)
        self._initial_signature = self._form_signature()

    def _fill_from_profile(self, profile: ModelProfile) -> None:
        idx = self._provider_combo.findData(profile.provider)
        if idx >= 0:
            self._provider_combo.setCurrentIndex(idx)
        else:
            self._provider_combo.setCurrentIndex(self._provider_combo.count() - 1)
        self._display_name.setText(profile.display_name)
        self._api_key.setText(profile.api_key)
        self._base_url.setText(profile.base_url)
        self._on_provider_changed()
        idx_model = self._model_combo.findText(profile.model_id)
        if idx_model >= 0:
            self._model_combo.setCurrentIndex(idx_model)
        else:
            self._model_combo.setEditText(profile.model_id)

    def _on_provider_changed(self) -> None:
        provider = self._provider_combo.currentData() or "custom"
        models = KNOWN_PROVIDERS.get(provider, {}).get("models", [])
        self._model_combo.clear()
        if models:
            self._model_combo.addItems(models)
        else:
            self._model_combo.setEditText("")

        # Adjust API Key field based on provider
        if provider == "ollama":
            self._api_key.setPlaceholderText("本地服务，无需 API Key")
            self._api_key.setEnabled(False)
            self._show_key_btn.setVisible(False)
        else:
            self._api_key.setPlaceholderText("sk-...")
            self._api_key.setEnabled(True)
            self._show_key_btn.setVisible(True)

    def _on_model_changed(self, model_name: str) -> None:
        if not model_name:
            return
        provider = self._provider_combo.currentData() or "custom"
        prov_label = KNOWN_PROVIDERS.get(provider, {}).get("label", provider)
        self._display_name.setPlaceholderText(f"{prov_label} {model_name}")

    def _toggle_key_visibility(self) -> None:
        if self._api_key.echoMode() == QLineEdit.EchoMode.Password:
            self._api_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self._show_key_btn.setText("隐藏")
        else:
            self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
            self._show_key_btn.setText("显示")
        self._sync_show_key_btn_width()

    def _sync_show_key_btn_width(self) -> None:
        """Keep a stable width so '显示/隐藏' never clips in compact mode."""
        fm = self._show_key_btn.fontMetrics()
        text_w = max(fm.horizontalAdvance("显示"), fm.horizontalAdvance("隐藏"))
        self._show_key_btn.setMinimumWidth(text_w + 36)

    def _on_accept(self) -> None:
        provider = self._provider_combo.currentData() or "custom"
        model_id = self._model_combo.currentText().strip()
        display_name = self._display_name.text().strip()
        api_key = self._api_key.text().strip()
        base_url = self._base_url.text().strip()

        if not model_id:
            show_warning_message(self, "缺少必填项", "请填写或选择模型。")
            return
        if provider == "custom" and not base_url:
            show_warning_message(self, "缺少必填项", "自定义供应商需要填写接口地址。")
            return
        if not display_name:
            prov_label = KNOWN_PROVIDERS.get(provider, {}).get("label", provider)
            display_name = f"{prov_label} {model_id}"

        profile_id = f"{provider}:{model_id}"
        if not self._editing and profile_id in self._existing_ids:
            show_warning_message(
                self,
                "ID 冲突",
                f"已存在模型 {profile_id}，请修改供应商或模型 ID。",
            )
            return

        self._result_profile = ModelProfile(
            profile_id=profile_id,
            display_name=display_name,
            provider=provider,
            model_id=model_id,
            api_key=api_key,
            base_url=base_url,
        )
        self.accept()

    def get_profile(self) -> ModelProfile | None:
        return getattr(self, "_result_profile", None)

    def _form_signature(self) -> str:
        provider = self._provider_combo.currentData() or "custom"
        payload = {
            "provider": provider,
            "model": self._model_combo.currentText().strip(),
            "display_name": self._display_name.text().strip(),
            "api_key": self._api_key.text().strip(),
            "base_url": self._base_url.text().strip(),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.result() == QDialog.DialogCode.Accepted:
            event.accept()
            return
        if self._form_signature() == self._initial_signature:
            event.accept()
            return
        box = QMessageBox(self)
        box.setWindowTitle("尚未保存")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("模型配置尚有未落笔的更改。")
        box.setInformativeText("要先保存这次编辑，还是直接放弃更改？")
        save_btn = box.addButton("保存并关闭", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton("继续编辑", QMessageBox.ButtonRole.RejectRole)
        save_btn.setObjectName("actionButton")
        save_btn.setProperty("variant", "primary")
        save_btn.setProperty("compact", True)
        discard_btn.setObjectName("actionButton")
        discard_btn.setProperty("variant", "danger")
        discard_btn.setProperty("compact", True)
        cancel_btn.setObjectName("actionButton")
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setProperty("compact", True)
        for btn in (save_btn, discard_btn, cancel_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        box.setDefaultButton(save_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_btn:
            self._on_accept()
            event.setAccepted(self.result() == QDialog.DialogCode.Accepted)
            return
        if clicked == discard_btn:
            event.accept()
            return
        if clicked == cancel_btn:
            event.ignore()
            return
        event.ignore()


MAX_FALLBACK_ROUTES = 3
FALLBACK_TRIGGER_MIN_WIDTH = 220
FALLBACK_TRIGGER_MAX_WIDTH = 440
ROUTE_MODEL_MIN_WIDTH = 220
ROUTE_MODEL_MAX_WIDTH = 360
ROUTE_ROW_SPACING = 4
ROUTE_TEXT_WIDTH = 138
ROUTE_HINT_ELIDE_WIDTH = ROUTE_TEXT_WIDTH - 8
ROUTE_TOGGLES_WIDTH = 178
ROUTE_TEMPERATURE_WIDTH = 100


_THINKING_MODE_LABELS = {
    "off": "思考：关闭",
    "on": "思考：开启",
    "adaptive": "思考：自适应",
    "minimal": "思考：最小",
    "low": "思考：低",
    "medium": "思考：中",
    "high": "思考：高",
    "xhigh": "思考：超高",
    "max": "思考：最高",
    "forced": "思考：固定",
}


class _ThinkingModeCombo(QComboBox):
    """Reasoning selector with QCheckBox-compatible migration helpers."""

    def __init__(self, mode: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        initial_mode = str(mode or "")
        self._requested_mode = initial_mode
        self._capability = ModelThinkingCapability("unsupported", ("off",), "off")
        self.setMinimumWidth(112)
        self.setMaximumWidth(124)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_capability(self._capability)
        self._requested_mode = initial_mode

    def set_capability(self, capability: ModelThinkingCapability) -> None:
        previous = str(self._requested_mode or self.currentData() or "")
        self._capability = capability
        target = capability.normalize(previous) if previous else capability.default_mode
        self.blockSignals(True)
        self.clear()
        for mode in capability.modes or ("off",):
            self.addItem(_THINKING_MODE_LABELS.get(mode, f"思考：{mode}"), mode)
        index = self.findData(target)
        self.setCurrentIndex(index if index >= 0 else 0)
        self.blockSignals(False)
        self._requested_mode = ""
        self.setEnabled(capability.user_selectable)
        self.setToolTip(self._tooltip_for(capability))

    def thinking_mode(self) -> str:
        return str(self.currentData() or self._capability.default_mode or "off")

    def isChecked(self) -> bool:  # noqa: N802 - QCheckBox compatibility
        return self.thinking_mode() not in {"off", "none", "disabled", "unsupported"}

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - migration compatibility
        target = self._capability.normalize("on" if checked else "off")
        index = self.findData(target)
        if index >= 0:
            self.setCurrentIndex(index)

    @staticmethod
    def _tooltip_for(capability: ModelThinkingCapability) -> str:
        default_label = _THINKING_MODE_LABELS.get(
            capability.default_mode,
            capability.default_mode,
        ).removeprefix("思考：")
        if capability.control == "forced":
            return "该模型固定使用思考模式，供应商不提供关闭参数"
        if capability.control == "unsupported":
            return "该模型或当前 API 端点不支持思考控制"
        if capability.control == "effort":
            return f"选择供应商官方支持的推理强度；默认：{default_label}"
        if capability.control == "adaptive":
            return f"模型按任务复杂度自适应思考；默认：{default_label}"
        return f"切换供应商思考模式；默认：{default_label}"


class _ElidedDescriptionLabel(QLabel):
    """Single-line label that keeps the full text available as a tooltip."""

    def __init__(
        self,
        text: str,
        *,
        tooltip_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("", parent)
        self._display_text = _normalize_route_hint(text)
        self._tooltip_text = _normalize_route_hint(
            tooltip_text if tooltip_text is not None else text
        )
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(self._tooltip_text)
        self._apply_elide()

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = self.width()
        if width <= 0:
            width = self.maximumWidth() if self.maximumWidth() < 16777215 else 320
        width = max(24, width - 2)
        QLabel.setText(
            self,
            self.fontMetrics().elidedText(
                self._display_text,
                Qt.TextElideMode.ElideRight,
                width,
            ),
        )


def _normalize_route_hint(text: str) -> str:
    return " ".join(str(text or "").split())


def _compact_route_hint(text: str) -> str:
    hint = _normalize_route_hint(text)
    for prefix in ("StoryBible 分片：", "CharacterBible 分片："):
        if hint.startswith(prefix):
            hint = hint.removeprefix(prefix).strip()
            break
    if "；" in hint:
        head = hint.split("；", 1)[0].strip()
        if head:
            hint = head
    for open_char, close_char in (("（", "）"), ("(", ")")):
        if hint.endswith(close_char):
            start = hint.rfind(open_char)
            if start >= 0:
                hint = hint[:start].rstrip()
    if len(hint) > 24:
        hint = hint[:23].rstrip() + "…"
    return hint


def _make_route_temperature_spin(value: float) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, 2.0)
    spin.setDecimals(2)
    spin.setSingleStep(0.1)
    spin.setAccelerated(True)
    spin.setPrefix("温 ")
    spin.setValue(float(value))
    spin.setFixedWidth(ROUTE_TEMPERATURE_WIDTH)
    spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
    spin.setToolTip("该步骤调用模型时使用的温度")
    return spin


def _make_route_temperature_placeholder() -> QWidget:
    placeholder = QWidget()
    placeholder.setFixedWidth(ROUTE_TEMPERATURE_WIDTH)
    placeholder.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return placeholder


class _FallbackDropdownButton(QFrame):
    """Button-like widget: left-aligned label + right-pinned ▼ arrow.

    Replaces a plain QPushButton for the fallback trigger so the arrow
    character is always flush to the right edge regardless of how long
    the route-chain text is.
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("fallbackDropdownBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 10, 0)
        layout.setSpacing(4)

        self._label = QLabel("备用路由")
        self._label.setObjectName("fallbackDropdownLabel")
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._label, 1)

        self._arrow = QLabel("▼")
        self._arrow.setObjectName("fallbackDropdownArrow")
        self._arrow.setFixedWidth(14)
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._arrow, 0)

    def setText(self, text: str) -> None:
        self._label.setText(text)

    def text(self) -> str:
        return self._label.text()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mousePressEvent(event)


class _FallbackRouteEditorItem(QWidget):
    """Single row inside fallback-route popup."""

    changed = Signal()

    def __init__(
        self,
        profile_id: str,
        label: str,
        *,
        can_route: bool,
        thinking: bool,
        multi_turn: bool,
        show_multi_turn: bool,
        thinking_mode: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.profile_id = profile_id
        self._show_multi_turn = show_multi_turn
        self._can_route = can_route
        self._model_can_think = True
        self._model_can_multi = True
        self._thinking_capability = ModelThinkingCapability("toggle", ("off", "on"), "off")
        self._base_can_think = True
        self._base_can_multi = show_multi_turn
        self._is_active_rank = True

        self.setObjectName("fallbackPopupRow")
        self.setProperty("activeRank", True)
        self.setProperty("routeEnabled", can_route)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)

        self._drag_label = QLabel("⋮⋮")
        self._drag_label.setObjectName("fallbackPopupHandle")
        self._drag_label.setToolTip("拖拽调整优先级")
        self._drag_label.setFixedWidth(14)
        layout.addWidget(self._drag_label, 0)

        self._rank_badge = QLabel("1")
        self._rank_badge.setObjectName("fallbackPopupRank")
        self._rank_badge.setFixedWidth(18)
        self._rank_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._rank_badge, 0)

        self._name_label = _ElidedDescriptionLabel(label, tooltip_text=label)
        self._name_label.setObjectName("fallbackPopupName")
        self._name_label.setToolTip(label)
        # The list item must shrink with the popup.  A shortened label keeps
        # the full model name available through its tooltip in narrow windows.
        self._name_label.setMinimumWidth(0)
        self._name_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self._name_label, 1)

        self._thinking_cb = _ThinkingModeCombo(thinking_mode or ("on" if thinking else "off"))
        self._thinking_cb.setObjectName("fallbackMiniToggle")
        self._thinking_cb.setToolTip("备用路由：思考模式")
        self._thinking_cb.currentIndexChanged.connect(lambda _index: self.changed.emit())

        self._multi_turn_cb = QCheckBox("多")
        self._multi_turn_cb.setObjectName("fallbackMiniToggle")
        self._multi_turn_cb.setChecked(bool(multi_turn))
        self._multi_turn_cb.setToolTip("备用路由：多轮模式")
        self._multi_turn_cb.stateChanged.connect(lambda _state: self.changed.emit())

        # Fixed-width container so all rows share the same toggle column width,
        # keeping 思/多 right-aligned regardless of name label length.
        _toggle_w = 172 if show_multi_turn else 124
        toggles_wrap = QWidget()
        toggles_wrap.setFixedWidth(_toggle_w)
        toggles_layout = QHBoxLayout(toggles_wrap)
        toggles_layout.setContentsMargins(0, 0, 0, 0)
        toggles_layout.setSpacing(6)
        toggles_layout.addWidget(self._thinking_cb)
        if show_multi_turn:
            toggles_layout.addWidget(self._multi_turn_cb)
        else:
            self._multi_turn_cb.setChecked(False)
            self._multi_turn_cb.hide()
        layout.addWidget(
            toggles_wrap, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.set_rank(0, MAX_FALLBACK_ROUTES)
        self.set_capability_masks(
            base_can_think=True,
            base_can_multi=show_multi_turn,
            model_can_think=True,
            model_can_multi=True,
        )

    def set_rank(self, index: int, max_active: int) -> None:
        self._is_active_rank = index < max_active
        self._rank_badge.setText(str(index + 1))
        if index == 0:
            rank_tier = "top1"
        elif index == 1:
            rank_tier = "top2"
        elif index == 2:
            rank_tier = "top3"
        else:
            rank_tier = "other"
        self.setProperty("activeRank", self._is_active_rank)
        self.setProperty("rankTier", rank_tier)
        self._name_label.setProperty("activeRank", self._is_active_rank)
        self._name_label.setProperty("rankTier", rank_tier)
        self._rank_badge.setProperty("activeRank", self._is_active_rank)
        self._rank_badge.setProperty("rankTier", rank_tier)
        self._refresh_style_state()
        if self._is_active_rank:
            self.setToolTip(f"优先级 #{index + 1}（生效）")
        else:
            self.setToolTip(f"优先级 #{index + 1}（超过 {max_active}，暂不生效）")
        self._apply_toggle_capability()

    def set_capability_masks(
        self,
        *,
        base_can_think: bool,
        base_can_multi: bool,
        model_can_think: bool,
        model_can_multi: bool,
    ) -> None:
        self._base_can_think = base_can_think
        self._base_can_multi = base_can_multi
        self._model_can_think = model_can_think
        self._model_can_multi = model_can_multi
        self._apply_toggle_capability()

    def set_thinking_capability(self, capability: ModelThinkingCapability) -> None:
        self._thinking_capability = capability
        self._thinking_cb.set_capability(capability)
        self._model_can_think = capability.supported
        self._apply_toggle_capability()

    def is_route_enabled(self) -> bool:
        return self._can_route

    def set_route_enabled(self, enabled: bool) -> None:
        self._can_route = enabled
        self.setProperty("routeEnabled", enabled)
        self._refresh_style_state()
        self._apply_toggle_capability()

    def current_entry(self) -> TaskRouteEntry:
        return TaskRouteEntry(
            profile_id=self.profile_id,
            thinking=self._thinking_cb.isChecked(),
            thinking_mode=self._thinking_cb.thinking_mode(),
            multi_turn=(self._multi_turn_cb.isChecked() if self._show_multi_turn else False),
        )

    def _refresh_style_state(self) -> None:
        for widget in (self, self._name_label, self._rank_badge):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _apply_toggle_capability(self) -> None:
        # Each model's capability is determined solely by its own detected abilities.
        # The primary route model's capability no longer gates fallback checkboxes.
        can_think = self._can_route and self._is_active_rank and self._model_can_think
        self._thinking_cb.setEnabled(can_think and self._thinking_capability.user_selectable)
        if not can_think:
            if not self._can_route:
                self._thinking_cb.setToolTip("该模型当前不可调用")
            elif not self._model_can_think:
                self._thinking_cb.setToolTip("该模型不支持思考/推理模式")
            else:
                self._thinking_cb.setToolTip("仅前 3 个备用路由生效")
        else:
            self._thinking_cb.setToolTip(_ThinkingModeCombo._tooltip_for(self._thinking_capability))

        if not self._show_multi_turn:
            self._multi_turn_cb.setChecked(False)
            self._multi_turn_cb.setEnabled(False)
            self._multi_turn_cb.hide()
            return

        can_multi = self._can_route and self._is_active_rank and self._model_can_multi
        self._multi_turn_cb.setEnabled(can_multi)
        if not can_multi:
            self._multi_turn_cb.setChecked(False)
            if not self._can_route:
                self._multi_turn_cb.setToolTip("该模型当前不可调用")
            elif not self._model_can_multi:
                self._multi_turn_cb.setToolTip("该模型不支持多轮对话")
            else:
                self._multi_turn_cb.setToolTip("仅前 3 个备用路由生效")
        else:
            self._multi_turn_cb.setToolTip("备用路由：多轮模式")


class _RankableListWidget(QListWidget):
    """QListWidget subclass that preserves item-widget bindings after drag-drop reorders.

    Qt's InternalMove uses a mime-data serialization path that does not reliably
    preserve Qt.ItemDataRole.UserRole on recreated items, causing widgets to be
    swallowed when dragging from active rows (0-2) outward.  This subclass
    bypasses Qt's mime path entirely: it uses takeItem/insertItem to move the
    item in-place (UserRole is never serialized/deserialized), then calls
    *rebuild_callback* to re-attach all item widgets.
    """

    def __init__(
        self,
        rebuild_callback: Callable[[dict[str, tuple[str, bool]]], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setUniformItemSizes(True)
        self._rebuild_callback = rebuild_callback
        self._drag_source_row: int = -1

    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        self._drag_source_row = self.currentRow()
        super().startDrag(supported_actions)

    def dropEvent(self, event: QDropEvent) -> None:
        # 1. Snapshot widget state keyed by profile_id BEFORE any manipulation.
        state: dict[str, tuple[str, bool]] = {}
        for i in range(self.count()):
            item = self.item(i)
            if item is None:
                continue
            pid = str(item.data(Qt.ItemDataRole.UserRole) or "")
            widget = self.itemWidget(item)
            if isinstance(widget, _FallbackRouteEditorItem):
                entry = widget.current_entry()
                state[pid] = (entry.thinking_mode, entry.multi_turn)
            elif pid:
                state.setdefault(pid, ("off", False))

        src_row = self._drag_source_row
        if src_row < 0 or src_row >= self.count():
            # Source not tracked; fall back to Qt default and hope for the best.
            super().dropEvent(event)
            return

        # 2. Determine drop target row from cursor position.
        pos = event.position().toPoint()
        target_item = self.itemAt(pos)
        if target_item is None:
            dst_row = self.count()  # dropped beyond last item -> append
        else:
            dst_row = self.row(target_item)
            item_rect = self.visualItemRect(target_item)
            if pos.y() > item_rect.center().y():
                dst_row += 1  # cursor in lower half -> insert after this item

        # 3. Manually move via takeItem/insertItem -- UserRole survives intact.
        #    After takeItem(src_row), rows above src_row are unaffected;
        #    dst_row needs -1 only when the target was originally below the source.
        item = self.takeItem(src_row)
        insert_at = dst_row if dst_row <= src_row else dst_row - 1
        self.insertItem(insert_at, item)

        # Report IgnoreAction so Qt's startDrag() sees a non-MoveAction return
        # from drag->exec() and does NOT call clearOrRemove() on the source row.
        # We already performed the reorder above; letting Qt also "clean up" the
        # source would delete whichever item now sits at the original src_row
        # (a different item after our takeItem/insertItem), causing visible loss.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()

        # 4. Re-attach widgets on the now-reordered items.
        self._rebuild_callback(state)


class _FallbackRoutesPopup(QFrame):
    """Dropdown-style popup to rank fallback routes."""

    changed = Signal()

    def __init__(
        self,
        *,
        profiles: list[tuple[str, str, bool]],
        fallback_routes: list[TaskRouteEntry],
        show_multi_turn: bool,
        capability_resolver: Callable[[str], tuple[bool, bool]],
        thinking_capability_resolver: Callable[[str], ModelThinkingCapability] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Popup, True)
        self.setObjectName("fallbackRoutesPopup")
        self._show_multi_turn = show_multi_turn
        self._capability_resolver = capability_resolver
        self._thinking_capability_resolver = thinking_capability_resolver or (
            lambda profile_id: (
                ModelThinkingCapability("toggle", ("off", "on"), "off")
                if capability_resolver(profile_id)[0]
                else ModelThinkingCapability("unsupported", ("off",), "off")
            )
        )
        self._profile_labels: dict[str, str] = {}
        self._profile_can_route: dict[str, bool] = {}
        self._primary_profile_id = ""
        self._base_can_think = True
        self._base_can_multi = show_multi_turn

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self._title_label = QLabel("备用路由排序")
        self._title_label.setObjectName("fallbackPopupTitle")
        layout.addWidget(self._title_label)

        hint = QLabel("拖拽调整优先级；仅前 3 个备用路由生效。右侧可选思考档位/多轮。")
        hint.setObjectName("fallbackPopupHint")
        layout.addWidget(hint)

        legend_row = QHBoxLayout()
        legend_row.setContentsMargins(0, 0, 0, 0)
        legend_row.setSpacing(6)
        active_badge = Badge("前 3 生效", tone="default")
        legend_row.addWidget(active_badge)
        standby_badge = Badge("其余候补", tone="muted")
        legend_row.addWidget(standby_badge)
        blocked_badge = Badge("灰色不可用", tone="warning")
        legend_row.addWidget(blocked_badge)
        legend_row.addStretch()
        layout.addLayout(legend_row)

        self._status_label = QLabel("")
        self._status_label.setObjectName("fallbackPopupStatus")
        layout.addWidget(self._status_label)

        self._list = _RankableListWidget(self._rebuild_widgets_from_items)
        self._list.setObjectName("fallbackRoutesList")
        self._list.setDragEnabled(True)
        self._list.setAcceptDrops(True)
        self._list.setDropIndicatorShown(True)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setMinimumHeight(220)
        self._list.setMaximumHeight(320)
        # Reserve right space so the scrollbar never overlaps the toggle column.
        self._list.setViewportMargins(0, 0, 4, 0)
        layout.addWidget(self._list, 1)

        # Rank changes are handled inside _RankableListWidget.dropEvent;
        # no separate rowsMoved connection is needed.

        self.set_profiles(profiles, fallback_routes)

    def set_primary_capabilities(self, can_think: bool, can_multi: bool) -> None:
        self._base_can_think = can_think
        self._base_can_multi = can_multi and self._show_multi_turn
        self._refresh_row_states()
        self._update_status_meta()

    def set_profiles(
        self,
        profiles: list[tuple[str, str, bool]],
        fallback_routes: list[TaskRouteEntry],
    ) -> None:
        existing_order, existing_state = self._snapshot_state()
        preferred_order = [entry.profile_id for entry in fallback_routes if entry.profile_id]
        for entry in fallback_routes:
            existing_state[entry.profile_id] = (entry.thinking_mode, entry.multi_turn)

        profile_map: dict[str, tuple[str, bool]] = {}
        for profile_id, label, can_route in profiles:
            if not profile_id:
                continue
            profile_map[profile_id] = (label, can_route)
        self._profile_labels = {
            profile_id: label for profile_id, (label, _can_route) in profile_map.items()
        }
        self._profile_can_route = {
            profile_id: can_route for profile_id, (_label, can_route) in profile_map.items()
        }

        ordered_ids: list[str] = []
        seen: set[str] = set()
        for profile_id in preferred_order:
            if profile_id in profile_map and profile_id not in seen:
                ordered_ids.append(profile_id)
                seen.add(profile_id)
        for profile_id in existing_order:
            if profile_id in profile_map and profile_id not in seen:
                ordered_ids.append(profile_id)
                seen.add(profile_id)
        for profile_id, _label, _can_route in profiles:
            if profile_id and profile_id in profile_map and profile_id not in seen:
                ordered_ids.append(profile_id)
                seen.add(profile_id)

        self._list.clear()
        for profile_id in ordered_ids:
            label, can_route = profile_map[profile_id]
            thinking_mode, multi_turn = existing_state.get(profile_id, ("off", False))
            row = _FallbackRouteEditorItem(
                profile_id,
                label,
                can_route=can_route,
                thinking=thinking_mode != "off",
                thinking_mode=thinking_mode,
                multi_turn=multi_turn,
                show_multi_turn=self._show_multi_turn,
            )
            row.changed.connect(self.changed.emit)
            item = QListWidgetItem()
            # Store profile_id as UserRole so _RankableListWidget.dropEvent can
            # identify each item after Qt recreates the QListWidgetItem objects.
            item.setData(Qt.ItemDataRole.UserRole, profile_id)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsDragEnabled
                | Qt.ItemFlag.ItemIsDropEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
            )
            # A content-width hint forces QListWidget to preserve the widest
            # model label and clips the right-hand controls in smaller windows.
            # Keep only the row height fixed; the list owns the row width.
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._list.addItem(item)
            self._list.setItemWidget(item, row)

        self._refresh_row_states()
        self._update_status_meta()
        self.changed.emit()

    def routeable_count(self) -> int:
        return self._list.count()

    def profile_label(self, profile_id: str) -> str:
        return self._profile_labels.get(profile_id, profile_id)

    def set_primary_profile_id(self, profile_id: str) -> None:
        self._primary_profile_id = profile_id
        self._refresh_row_states()
        self._update_status_meta()

    def fallback_routes(self) -> list[TaskRouteEntry]:
        routes: list[TaskRouteEntry] = []
        seen: set[str] = set()
        for index, row in enumerate(self._iter_rows()):
            if index >= MAX_FALLBACK_ROUTES:
                break
            if not row.is_route_enabled():
                continue
            entry = row.current_entry()
            if not entry.profile_id or entry.profile_id in seen:
                continue
            seen.add(entry.profile_id)
            routes.append(entry)
        return routes

    def _iter_rows(self) -> list[_FallbackRouteEditorItem]:
        rows: list[_FallbackRouteEditorItem] = []
        for index in range(self._list.count()):
            item = self._list.item(index)
            widget = self._list.itemWidget(item)
            if isinstance(widget, _FallbackRouteEditorItem):
                rows.append(widget)
        return rows

    def _snapshot_state(self) -> tuple[list[str], dict[str, tuple[str, bool]]]:
        order: list[str] = []
        states: dict[str, tuple[str, bool]] = {}
        for row in self._iter_rows():
            order.append(row.profile_id)
            entry = row.current_entry()
            states[row.profile_id] = (entry.thinking_mode, entry.multi_turn)
        return order, states

    def _on_rank_changed(self, *_args: object) -> None:
        self._refresh_row_states()
        self._update_status_meta()
        self.changed.emit()

    def _refresh_row_states(self) -> None:
        for index, row in enumerate(self._iter_rows()):
            row.set_rank(index, MAX_FALLBACK_ROUTES)
            base_enabled = self._profile_can_route.get(row.profile_id, True)
            row.set_route_enabled(base_enabled and row.profile_id != self._primary_profile_id)
            can_think, can_multi = self._capability_resolver(row.profile_id)
            row.set_thinking_capability(self._thinking_capability_resolver(row.profile_id))
            row.set_capability_masks(
                base_can_think=self._base_can_think,
                base_can_multi=self._base_can_multi,
                model_can_think=can_think,
                model_can_multi=can_multi,
            )

    def _update_status_meta(self) -> None:
        rows = self._iter_rows()
        enabled_rows = [row for row in rows if row.is_route_enabled()]
        active_enabled = min(MAX_FALLBACK_ROUTES, len(enabled_rows))
        self._status_label.setText(
            f"当前可调用 {len(enabled_rows)} / {len(rows)} 个；前 {active_enabled} 个参与自动切换。"
        )

    def _rebuild_widgets_from_items(self, state: dict[str, tuple[str, bool]]) -> None:
        """Rebuild item widgets after a drag-drop reorder.

        Qt's InternalMove drops destroy setItemWidget bindings by replacing
        the underlying QListWidgetItems.  This method reads the freshly-ordered
        item list (each item carries its profile_id via UserRole), recreates a
        _FallbackRouteEditorItem for every row using the pre-drop *state*
        snapshot, and re-attaches it via setItemWidget.
        """
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None:
                continue
            profile_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if not profile_id:
                continue
            label = self._profile_labels.get(profile_id, profile_id)
            can_route = self._profile_can_route.get(profile_id, True)
            thinking_mode, multi_turn = state.get(profile_id, ("off", False))
            row = _FallbackRouteEditorItem(
                profile_id,
                label,
                can_route=can_route,
                thinking=thinking_mode != "off",
                thinking_mode=thinking_mode,
                multi_turn=multi_turn,
                show_multi_turn=self._show_multi_turn,
            )
            row.changed.connect(self.changed.emit)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._list.setItemWidget(item, row)
        self._on_rank_changed()


class _TaskRouteRow(QWidget):
    """Single pipeline task row with primary + dropdown fallback routing."""

    def __init__(
        self,
        task_key: str,
        task_label: str,
        task_hint: str,
        profiles: list[tuple[str, str, bool]],
        route: TaskRouteEntry | None,
        fallback_routes: list[TaskRouteEntry] | None = None,
        *,
        show_multi_turn: bool = True,
        temperature: float | None = None,
        profile_configs: list[ModelProfile] | None = None,
        detected_capabilities: dict[str, tuple[bool, bool]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_key = task_key
        self._show_multi_turn = show_multi_turn
        self._profile_configs = profile_configs or []
        self._detected_capabilities = detected_capabilities or {}
        self._profiles = list(profiles)

        # Task 20 — object name enables the ``QWidget#taskRouteRow:hover`` rule.
        self.setObjectName("taskRouteRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(ROUTE_ROW_SPACING)

        text_block = QVBoxLayout()
        text_block.setContentsMargins(0, 0, 0, 0)
        text_block.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        lbl = QLabel(task_label)
        lbl.setObjectName("routeTaskLabel")
        title_row.addWidget(lbl)
        self._meta_badge: Badge | None = None
        if task_key == TaskType.ELEMENT_PROGRESS_ARBITER.value:
            self._meta_badge = Badge("低频触发", tone="warning")
            self._meta_badge.setToolTip("仅在火候页开启灰区仲裁且命中灰区时触发")
            title_row.addWidget(self._meta_badge)
        title_row.addStretch()
        text_block.addLayout(title_row)
        full_hint = _normalize_route_hint(task_hint)
        hint_lbl = _ElidedDescriptionLabel(
            _compact_route_hint(task_hint),
            tooltip_text=full_hint,
        )
        hint_lbl.setObjectName("routeTaskHint")
        hint_lbl.setFixedWidth(ROUTE_TEXT_WIDTH)
        self._hint_label = hint_lbl
        text_block.addWidget(hint_lbl)
        text_container = QWidget()
        text_container.setFixedWidth(ROUTE_TEXT_WIDTH)
        text_container.setLayout(text_block)
        layout.addWidget(text_container)

        self.model_combo = QComboBox()
        self.model_combo.setObjectName("routeModelCombo")
        self.model_combo.setMinimumWidth(ROUTE_MODEL_MIN_WIDTH)
        self.model_combo.setMaximumWidth(ROUTE_MODEL_MAX_WIDTH)
        self.model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._populate_combo(self.model_combo, profiles, "")
        if route and route.profile_id:
            idx = self.model_combo.findData(route.profile_id)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        layout.addWidget(self.model_combo, 1)

        self._toggles_wrap = QWidget()
        self._toggles_wrap.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        toggles_layout = QHBoxLayout(self._toggles_wrap)
        toggles_layout.setContentsMargins(0, 0, 0, 0)
        toggles_layout.setSpacing(6)

        self.thinking_cb = _ThinkingModeCombo(route.thinking_mode if route else "")
        self.thinking_cb.setObjectName("routeThinkingMode")
        toggles_layout.addWidget(self.thinking_cb)

        self.multi_turn_cb = QCheckBox("多轮")
        self.multi_turn_cb.setObjectName("routeMultiTurnToggle")
        self.multi_turn_cb.setToolTip("启用多轮对话上下文注入")
        if route:
            self.multi_turn_cb.setChecked(route.multi_turn)
        if show_multi_turn:
            self.multi_turn_cb.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
            toggles_layout.addWidget(self.multi_turn_cb)
        else:
            self.multi_turn_cb.setChecked(False)
            self.multi_turn_cb.hide()

        self._toggles_wrap.setFixedWidth(ROUTE_TOGGLES_WIDTH)
        layout.addWidget(self._toggles_wrap, 0, Qt.AlignmentFlag.AlignLeft)

        self._fallback_btn = _FallbackDropdownButton(parent=self)
        self._fallback_btn.setMinimumWidth(FALLBACK_TRIGGER_MIN_WIDTH)
        self._fallback_btn.setMaximumWidth(FALLBACK_TRIGGER_MAX_WIDTH)
        self._fallback_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._fallback_btn.setMinimumHeight(26)
        self._fallback_btn.clicked.connect(self._toggle_fallback_popup)
        layout.addWidget(self._fallback_btn, 2)

        self.temperature_spin: QDoubleSpinBox | None = None
        if temperature is not None:
            self.temperature_spin = _make_route_temperature_spin(temperature)
            layout.addWidget(self.temperature_spin, 0)
        else:
            layout.addWidget(_make_route_temperature_placeholder(), 0)

        self._fallback_popup = _FallbackRoutesPopup(
            profiles=self._profiles,
            fallback_routes=(fallback_routes or [])[:MAX_FALLBACK_ROUTES],
            show_multi_turn=self._show_multi_turn,
            capability_resolver=self._capabilities_for_profile,
            thinking_capability_resolver=self._thinking_capability_for_profile,
            parent=self,
        )
        self._fallback_popup.changed.connect(self._sync_fallback_button_text)

        self._on_model_changed()
        self._sync_fallback_button_text()

    def _toggle_fallback_popup(self) -> None:
        if self._fallback_popup.isVisible():
            self._fallback_popup.hide()
            return
        self._sync_fallback_capability_masks()
        popup_width = max(420, min(720, self._fallback_btn.width() + 72))
        self._fallback_popup.resize(popup_width, self._fallback_popup.sizeHint().height())
        anchor = self._fallback_popup_anchor()
        self._fallback_popup.move(anchor)
        self._fallback_popup.show()
        self._fallback_popup.raise_()

    def _fallback_popup_anchor(self) -> QPoint:
        """Place popup near trigger while avoiding off-screen clipping."""
        popup_size = self._fallback_popup.size()
        margin = 10
        below = self._fallback_btn.mapToGlobal(QPoint(0, self._fallback_btn.height() + 6))
        button_top = self._fallback_btn.mapToGlobal(QPoint(0, 0))

        screen = self._fallback_btn.screen() or QApplication.primaryScreen()
        if screen is None:
            return below
        area = screen.availableGeometry()
        left = area.x() + margin
        right = area.x() + area.width() - margin
        top = area.y() + margin
        bottom = area.y() + area.height() - margin

        x = below.x()
        y = below.y()
        if x + popup_size.width() > right:
            x = max(left, right - popup_size.width())
        if x < left:
            x = left

        if y + popup_size.height() > bottom:
            above_y = button_top.y() - popup_size.height() - 6
            if above_y >= top:
                y = above_y
            else:
                y = max(top, bottom - popup_size.height())
        if y < top:
            y = top
        return QPoint(x, y)

    def _on_model_changed(self) -> None:
        """Grey out primary/fallback toggles based on model capabilities."""
        profile_id = str(self.model_combo.currentData() or "")
        if not profile_id:
            self.thinking_cb.set_capability(ModelThinkingCapability("toggle", ("off", "on"), "off"))
            self.multi_turn_cb.setEnabled(True)
            self._sync_fallback_capability_masks()
            return

        can_think, can_multi = self._capabilities_for_profile(profile_id)
        thinking_capability = self._thinking_capability_for_profile(profile_id)
        self.thinking_cb.set_capability(thinking_capability)
        self.thinking_cb.setEnabled(can_think and thinking_capability.user_selectable)
        self.multi_turn_cb.setEnabled(can_multi)
        if not can_multi:
            self.multi_turn_cb.setChecked(False)
            self.multi_turn_cb.setToolTip("该模型不支持多轮对话")
        else:
            self.multi_turn_cb.setToolTip("启用多轮对话上下文注入")
        self._sync_fallback_capability_masks()

    def _sync_fallback_capability_masks(self) -> None:
        primary_id = str(self.model_combo.currentData() or "")
        if not primary_id:
            base_think, base_multi = True, self._show_multi_turn
        else:
            can_think, can_multi = self._capabilities_for_profile(primary_id)
            base_think = can_think
            base_multi = can_multi and self._show_multi_turn
        self._fallback_popup.set_primary_profile_id(primary_id)
        self._fallback_popup.set_primary_capabilities(base_think, base_multi)
        self._sync_fallback_button_text()

    def _sync_fallback_button_text(self) -> None:
        total = self._fallback_popup.routeable_count()
        active_routes = self._fallback_popup.fallback_routes()
        max_show = min(MAX_FALLBACK_ROUTES, total)
        self._fallback_btn.setProperty("hasFallback", bool(active_routes))
        self._fallback_btn.style().unpolish(self._fallback_btn)
        self._fallback_btn.style().polish(self._fallback_btn)
        self._fallback_btn.setEnabled(total > 0)
        if total <= 0:
            self._fallback_btn.setText("无可用备用模型")
            self._fallback_btn.setToolTip("当前没有可排序的备用模型")
            return
        if active_routes:
            names = [
                self._fallback_popup.profile_label(entry.profile_id) for entry in active_routes
            ]
            chain = " → ".join(self._short_profile_label(name) for name in names)
            self._fallback_btn.setText(f"已配置：{chain}")
        else:
            self._fallback_btn.setText(f"备用路由 0/{max_show}")
        if active_routes:
            names = [
                self._fallback_popup.profile_label(entry.profile_id) for entry in active_routes
            ]
            self._fallback_btn.setToolTip("优先级：" + " → ".join(names))
        else:
            self._fallback_btn.setToolTip("拖拽排序后，前 3 个备用路由将参与自动切换")

    @staticmethod
    def _short_profile_label(label: str) -> str:
        text = str(label or "").strip()
        if len(text) <= 9:
            return text
        return text[:8] + "…"

    def _capabilities_for_profile(self, profile_id: str) -> tuple[bool, bool]:
        if not profile_id:
            return True, True
        provider = ""
        model_id = ""
        for profile in self._profile_configs:
            if profile.profile_id == profile_id:
                provider = profile.provider
                model_id = profile.model_id
                break
        if not provider:
            return True, True
        # Thinking / multi-turn capability is a static attribute of the model API.
        # Connection test results (detected_capabilities) reflect reachability only —
        # a failed test must NOT downgrade a model's known capabilities, because that
        # would prevent users from selecting thinking mode for models that do support it.
        return get_model_capabilities(provider, model_id)

    def _thinking_capability_for_profile(
        self,
        profile_id: str,
    ) -> ModelThinkingCapability:
        for profile in self._profile_configs:
            if profile.profile_id == profile_id:
                return get_model_thinking_capability(profile.provider, profile.model_id)
        return ModelThinkingCapability("unsupported", ("off",), "off")

    def _populate_combo(
        self,
        combo: QComboBox,
        profiles: list[tuple[str, str, bool]],
        current_id: str,
    ) -> None:
        combo.clear()
        combo.addItem("（未指定）", "")
        model = combo.model()
        for profile_id, name, can_route in profiles:
            combo.addItem(name, profile_id)
            if isinstance(model, QStandardItemModel):
                item = model.item(combo.count() - 1)
                if item is None:
                    continue
                item.setEnabled(can_route)
                if not can_route:
                    item.setForeground(QColor("#a29689"))
                    item.setToolTip(f"{name}\n未配置 API Key，当前不可用于流程调用")
                else:
                    item.setToolTip(str(name))
        idx = combo.findData(current_id)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def get_route(self) -> TaskRouteEntry | None:
        """Collect current UI state into a TaskRouteEntry."""
        profile_id = self.model_combo.currentData() or ""
        if not profile_id:
            return None
        return TaskRouteEntry(
            profile_id=profile_id,
            thinking=self.thinking_cb.isChecked(),
            thinking_mode=self.thinking_cb.thinking_mode(),
            multi_turn=self.multi_turn_cb.isChecked(),
        )

    def get_fallback_routes(self) -> list[TaskRouteEntry]:
        """Collect fallback routes in current drag order (max 3)."""
        return self._fallback_popup.fallback_routes()

    def set_temperature_value(self, value: float) -> None:
        if self.temperature_spin is not None:
            self.temperature_spin.setValue(float(value))

    def refresh_profiles(
        self,
        profiles: list[tuple[str, str, bool]],
        current_id: str = "",
        *,
        profile_configs: list[ModelProfile] | None = None,
        detected_capabilities: dict[str, tuple[bool, bool]] | None = None,
    ) -> None:
        """Update the model dropdown while preserving selection."""
        if profile_configs is not None:
            self._profile_configs = profile_configs
        if detected_capabilities is not None:
            self._detected_capabilities = detected_capabilities
        current_fallbacks = self.get_fallback_routes()
        self._profiles = list(profiles)
        if not current_id:
            current_id = self.model_combo.currentData() or ""
        self.model_combo.blockSignals(True)
        self._populate_combo(self.model_combo, profiles, current_id)
        self.model_combo.blockSignals(False)
        self._fallback_popup.set_profiles(self._profiles, current_fallbacks)
        self._on_model_changed()

    def set_model_and_flags(
        self,
        profile_id: str,
        thinking: bool,
        multi_turn: bool,
        fallback_routes: list[TaskRouteEntry],
        thinking_mode: str = "",
    ) -> None:
        if not profile_id:
            return
        idx = self.model_combo.findData(profile_id)
        if idx < 0:
            return
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(idx)
        self.model_combo.blockSignals(False)
        self.thinking_cb.set_capability(self._thinking_capability_for_profile(profile_id))
        mode_index = self.thinking_cb.findData(
            self._thinking_capability_for_profile(profile_id).normalize(
                thinking_mode,
                legacy_thinking=thinking,
            )
        )
        if mode_index >= 0:
            self.thinking_cb.setCurrentIndex(mode_index)
        self.multi_turn_cb.setChecked(multi_turn)
        self._fallback_popup.set_profiles(self._profiles, fallback_routes)
        self._on_model_changed()


class _GroupBulkRow(QWidget):
    applied = Signal()

    def __init__(
        self,
        group_key: str,
        profiles: list[tuple[str, str, bool]],
        *,
        route: TaskRouteEntry | None = None,
        fallback_routes: list[TaskRouteEntry] | None = None,
        scope: Literal["group", "subgroup"] = "group",
        show_multi_turn: bool = True,
        profile_configs: list[ModelProfile] | None = None,
        detected_capabilities: dict[str, tuple[bool, bool]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.group_key = group_key
        self.scope = scope
        self._show_multi_turn = show_multi_turn
        self._profile_configs = profile_configs or []
        self._detected_capabilities = detected_capabilities or {}
        self._profiles = list(profiles)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 6)
        layout.setSpacing(ROUTE_ROW_SPACING)

        is_subgroup = scope == "subgroup"
        self._scope_label = QLabel("本阶段统一设定" if is_subgroup else "全组统一设定")
        self._scope_label.setObjectName("settingHint")
        self._scope_label.setFixedWidth(ROUTE_TEXT_WIDTH)
        self._scope_label.setToolTip(
            "仅统一当前阶段内的任务路由，不影响其他阶段"
            if is_subgroup
            else "统一当前分组下全部阶段的任务路由"
        )
        layout.addWidget(self._scope_label)

        self.model_combo = QComboBox()
        self.model_combo.setObjectName("routeModelCombo")
        self.model_combo.setMinimumWidth(ROUTE_MODEL_MIN_WIDTH)
        self.model_combo.setMaximumWidth(ROUTE_MODEL_MAX_WIDTH)
        self.model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._populate_combo(self.model_combo, profiles, route.profile_id if route else "")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        layout.addWidget(self.model_combo, 1)

        self._toggles_wrap = QWidget()
        self._toggles_wrap.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        toggles_layout = QHBoxLayout(self._toggles_wrap)
        toggles_layout.setContentsMargins(0, 0, 0, 0)
        toggles_layout.setSpacing(6)

        self.thinking_cb = _ThinkingModeCombo(route.thinking_mode if route else "")
        self.thinking_cb.setObjectName("routeThinkingMode")
        toggles_layout.addWidget(self.thinking_cb)

        self.multi_turn_cb = QCheckBox("多轮")
        self.multi_turn_cb.setObjectName("routeMultiTurnToggle")
        self.multi_turn_cb.setToolTip("启用多轮对话上下文注入")
        if route:
            self.multi_turn_cb.setChecked(route.multi_turn)
        if show_multi_turn:
            self.multi_turn_cb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            toggles_layout.addWidget(self.multi_turn_cb)
        else:
            self.multi_turn_cb.setChecked(False)
            self.multi_turn_cb.hide()

        self._toggles_wrap.setFixedWidth(ROUTE_TOGGLES_WIDTH)
        layout.addWidget(self._toggles_wrap, 0, Qt.AlignmentFlag.AlignLeft)

        self._fallback_btn = _FallbackDropdownButton(parent=self)
        self._fallback_btn.setMinimumWidth(FALLBACK_TRIGGER_MIN_WIDTH)
        self._fallback_btn.setMaximumWidth(FALLBACK_TRIGGER_MAX_WIDTH)
        self._fallback_btn.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._fallback_btn.setMinimumHeight(26)
        self._fallback_btn.clicked.connect(self._toggle_fallback_popup)
        layout.addWidget(self._fallback_btn, 2)

        self._fallback_popup = _FallbackRoutesPopup(
            profiles=self._profiles,
            fallback_routes=fallback_routes or [],
            show_multi_turn=self._show_multi_turn,
            capability_resolver=self._capabilities_for_profile,
            thinking_capability_resolver=self._thinking_capability_for_profile,
            parent=self,
        )
        self._fallback_popup.changed.connect(self._sync_fallback_button_text)

        self._apply_btn = ActionButton(
            "应用本阶段" if is_subgroup else "应用到全组",
            variant="primary",
        )
        self._apply_btn.setProperty("compact", True)
        self._apply_btn.setFixedWidth(ROUTE_TEMPERATURE_WIDTH)
        self._apply_btn.setToolTip(
            "仅应用到当前阶段的所有任务" if is_subgroup else "应用到当前分组的所有阶段和任务"
        )
        self._apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(self._apply_btn, 0)

        self._on_model_changed()
        self._sync_fallback_button_text()

    def _toggle_fallback_popup(self) -> None:
        if self._fallback_popup.isVisible():
            self._fallback_popup.hide()
            return
        popup_width = max(420, min(720, self._fallback_btn.width() + 72))
        self._fallback_popup.resize(popup_width, self._fallback_popup.sizeHint().height())
        anchor = self._fallback_btn.mapToGlobal(QPoint(0, self._fallback_btn.height() + 6))
        self._fallback_popup.move(anchor)
        self._fallback_popup.show()
        self._fallback_popup.raise_()

    def _on_model_changed(self) -> None:
        profile_id = str(self.model_combo.currentData() or "")
        if not profile_id:
            self.thinking_cb.set_capability(ModelThinkingCapability("toggle", ("off", "on"), "off"))
            self.multi_turn_cb.setEnabled(True)
            self._sync_fallback_capability_masks()
            return
        can_think, can_multi = self._capabilities_for_profile(profile_id)
        thinking_capability = self._thinking_capability_for_profile(profile_id)
        self.thinking_cb.set_capability(thinking_capability)
        self.thinking_cb.setEnabled(can_think and thinking_capability.user_selectable)
        self.multi_turn_cb.setEnabled(can_multi)
        if not can_multi:
            self.multi_turn_cb.setChecked(False)
            self.multi_turn_cb.setToolTip("该模型不支持多轮对话")
        else:
            self.multi_turn_cb.setToolTip("启用多轮对话上下文注入")
        self._sync_fallback_capability_masks()

    def _sync_fallback_capability_masks(self) -> None:
        primary_id = str(self.model_combo.currentData() or "")
        if not primary_id:
            base_think, base_multi = True, self._show_multi_turn
        else:
            can_think, can_multi = self._capabilities_for_profile(primary_id)
            base_think = can_think
            base_multi = can_multi and self._show_multi_turn
        self._fallback_popup.set_primary_profile_id(primary_id)
        self._fallback_popup.set_primary_capabilities(base_think, base_multi)
        self._sync_fallback_button_text()

    def _capabilities_for_profile(self, profile_id: str) -> tuple[bool, bool]:
        if not profile_id:
            return True, True
        provider = ""
        model_id = ""
        for profile in self._profile_configs:
            if profile.profile_id == profile_id:
                provider = profile.provider
                model_id = profile.model_id
                break
        if not provider:
            return True, True
        return get_model_capabilities(provider, model_id)

    def _thinking_capability_for_profile(
        self,
        profile_id: str,
    ) -> ModelThinkingCapability:
        for profile in self._profile_configs:
            if profile.profile_id == profile_id:
                return get_model_thinking_capability(profile.provider, profile.model_id)
        return ModelThinkingCapability("unsupported", ("off",), "off")

    @staticmethod
    def _populate_combo(
        combo: QComboBox,
        profiles: list[tuple[str, str, bool]],
        current_id: str,
    ) -> None:
        combo.clear()
        combo.addItem("（未指定）", "")
        model = combo.model()
        for profile_id, name, can_route in profiles:
            combo.addItem(name, profile_id)
            if isinstance(model, QStandardItemModel):
                item = model.item(combo.count() - 1)
                if item is None:
                    continue
                item.setEnabled(can_route)
                if not can_route:
                    item.setForeground(QColor("#a29689"))
                    item.setToolTip(f"{name}\n未配置 API Key，当前不可用于流程调用")
                else:
                    item.setToolTip(str(name))
        idx = combo.findData(current_id)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _sync_fallback_button_text(self) -> None:
        total = self._fallback_popup.routeable_count()
        active_routes = self._fallback_popup.fallback_routes()
        max_show = min(MAX_FALLBACK_ROUTES, total)
        self._fallback_btn.setProperty("hasFallback", bool(active_routes))
        self._fallback_btn.style().unpolish(self._fallback_btn)
        self._fallback_btn.style().polish(self._fallback_btn)
        self._fallback_btn.setEnabled(total > 0)
        if total <= 0:
            self._fallback_btn.setText("无可用备用模型")
            self._fallback_btn.setToolTip("当前没有可排序的备用模型")
            return
        if active_routes:
            names = [
                self._fallback_popup.profile_label(entry.profile_id) for entry in active_routes
            ]
            chain = " → ".join(_TaskRouteRow._short_profile_label(name) for name in names)
            self._fallback_btn.setText(f"已配置：{chain}")
        else:
            self._fallback_btn.setText(f"备用路由 0/{max_show}")
        if active_routes:
            names = [
                self._fallback_popup.profile_label(entry.profile_id) for entry in active_routes
            ]
            self._fallback_btn.setToolTip("优先级：" + " → ".join(names))
        else:
            self._fallback_btn.setToolTip("拖拽排序后，前 3 个备用路由将参与自动切换")

    def _on_apply(self) -> None:
        self.applied.emit()

    def get_bulk_route(self) -> TaskRouteEntry | None:
        profile_id = self.model_combo.currentData() or ""
        if not profile_id:
            return None
        return TaskRouteEntry(
            profile_id=profile_id,
            thinking=self.thinking_cb.isChecked(),
            thinking_mode=self.thinking_cb.thinking_mode(),
            multi_turn=self.multi_turn_cb.isChecked(),
        )

    def get_bulk_fallback_routes(self) -> list[TaskRouteEntry]:
        return self._fallback_popup.fallback_routes()

    def refresh_profiles(
        self,
        profiles: list[tuple[str, str, bool]],
        current_id: str = "",
        *,
        profile_configs: list[ModelProfile] | None = None,
        detected_capabilities: dict[str, tuple[bool, bool]] | None = None,
    ) -> None:
        if profile_configs is not None:
            self._profile_configs = profile_configs
        if detected_capabilities is not None:
            self._detected_capabilities = detected_capabilities
        current_fallbacks = self.get_bulk_fallback_routes()
        self._profiles = list(profiles)
        if not current_id:
            current_id = self.model_combo.currentData() or ""
        self.model_combo.blockSignals(True)
        self._populate_combo(self.model_combo, profiles, current_id)
        self.model_combo.blockSignals(False)
        self._fallback_popup.set_profiles(self._profiles, current_fallbacks)
        self._on_model_changed()


class _TestSignals(BaseJobWorkerSignals):
    probe_started = Signal(str)
    finished = Signal(str, bool, str, bool, bool)


class _OllamaEngineViewSignals(BaseJobWorkerSignals):
    loaded = Signal(bool, str, object)


class _ResearchConnectionSignals(BaseJobWorkerSignals):
    finished = Signal(bool, str)


class _ResearchConnectionTestWorker(BaseJobWorker):
    """Background worker that probes the selected web-research provider."""

    _TIMEOUT_SECONDS = 30.0
    pool = "aux"

    def __init__(self, settings_values: dict[str, object], provider_name: str) -> None:
        super().__init__()
        self.signals = _ResearchConnectionSignals()
        self._settings_values = dict(settings_values)
        self._provider_name = str(provider_name or "auto").strip() or "auto"
        self._running = False
        self._finished = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._finished = False
        self.submit()

    def isRunning(self) -> bool:  # noqa: N802 - keep QThread-like API for callers
        return self._running and not self._finished

    async def _run_async(self) -> None:
        import asyncio
        from types import SimpleNamespace

        from novel_forge.research.contracts import ResearchQuery
        from novel_forge.research.providers import NoopResearchProvider, provider_from_settings

        signals = self.signals
        try:
            settings = SimpleNamespace(**self._settings_values)
            provider = await asyncio.to_thread(
                provider_from_settings, settings, self._provider_name
            )
            if isinstance(provider, NoopResearchProvider):
                self._emit_finished(signals, False, "未配置可测试的检索 provider。")
                return

            async def _probe() -> tuple[bool, str]:
                result = await provider.search(
                    [
                        ResearchQuery(
                            query="Novel Forge search connectivity test", rationale="连接测试"
                        )
                    ]
                )
                if result.sources:
                    return True, f"连通，返回 {len(result.sources)} 条来源。"
                if result.warnings:
                    return False, self._redact("; ".join(result.warnings[:3]))
                return True, "已调用 provider，但测试查询未返回来源。"

            start = time.monotonic()
            ok, detail = await asyncio.wait_for(_probe(), timeout=self._TIMEOUT_SECONDS)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            suffix = f" · {elapsed_ms}ms"
            self._emit_finished(signals, ok, f"{detail}{suffix}")
        except Exception as exc:
            self._emit_finished(
                signals, False, self._redact(str(exc).strip() or type(exc).__name__)
            )
        finally:
            self._finished = True
            self._running = False

    @staticmethod
    def _emit_finished(signals: _ResearchConnectionSignals, success: bool, detail: str) -> None:
        try:
            signals.finished.emit(success, detail)
        except RuntimeError:
            pass

    def _redact(self, text: str) -> str:
        redacted = str(text or "")
        for key in ("research_api_key",):
            value = str(self._settings_values.get(key) or "").strip()
            if value:
                redacted = redacted.replace(value, "***")
        if len(redacted) > 260:
            redacted = redacted[:257] + "..."
        return redacted


class _OllamaEngineViewWorker(BaseJobWorker):
    """Read the Engine Ollama projection without talking to native Ollama."""

    pool = "aux"

    def __init__(self, job_service: object | None) -> None:
        super().__init__()
        self.signals = _OllamaEngineViewSignals()
        self._job_service = job_service
        self._running = False
        self._finished = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._finished = False
        self.submit()

    def isRunning(self) -> bool:  # noqa: N802 - keep QThread-like API for callers
        return self._running and not self._finished

    async def _run_async(self) -> None:
        import asyncio

        try:
            await asyncio.to_thread(self._read_engine_view)
        except asyncio.CancelledError:
            raise
        finally:
            self._finished = True
            self._running = False

    def _read_engine_view(self) -> None:
        try:
            from novel_forge.app_service.ollama_management import OllamaEngineCommandService
            from novel_forge.core.config import get_settings

            view = OllamaEngineCommandService(self._job_service).view(get_settings())
            self.signals.loaded.emit(True, "", view)
        except Exception as exc:
            self.signals.loaded.emit(False, str(exc).strip() or type(exc).__name__, None)


class _ConnectionTestWorker(BaseJobWorker):
    """Background worker that tests model adapter connectivity with latency."""

    _TIMEOUT_SECONDS = 18.0
    # Cache: profile_id → (timestamp, ok, detail, can_thinking, can_multi_turn)
    _result_cache: dict[str, tuple[float, bool, str, bool, bool]] = {}
    _CACHE_TTL = 300.0  # seconds
    _STALE_CACHE_TTL = 3600.0  # keep old UI state while a background refresh runs
    pool = "aux"

    def __init__(
        self,
        profile: ModelProfile,
        *,
        force_refresh: bool = False,
    ) -> None:
        super().__init__()
        self.signals = _TestSignals()
        self._profile = profile
        self._force_refresh = force_refresh
        self._running = False
        self._finished = False
        self._status_test_auto = False
        self._status_test_generation: int | None = None
        self._status_test_token: int | None = None

    @classmethod
    def clear_cached_result(cls, profile_id: str) -> None:
        cls._result_cache.pop(profile_id, None)

    @classmethod
    def clear_all_cached_results(cls) -> None:
        cls._result_cache.clear()

    @classmethod
    def cached_result(cls, profile_id: str) -> tuple[bool, str, bool, bool] | None:
        cached = cls._result_cache.get(profile_id)
        if cached is None:
            return None
        ts, ok, detail, can_thinking, can_multi_turn = cached
        age_s = time.monotonic() - ts
        if age_s >= cls._STALE_CACHE_TTL:
            cls._result_cache.pop(profile_id, None)
            return None
        if age_s >= cls._CACHE_TTL:
            return None
        return ok, detail, can_thinking, can_multi_turn

    @classmethod
    def stale_cached_result(cls, profile_id: str) -> tuple[bool, str, bool, bool] | None:
        cached = cls._result_cache.get(profile_id)
        if cached is None:
            return None
        ts, ok, detail, can_thinking, can_multi_turn = cached
        age_s = time.monotonic() - ts
        if age_s < cls._CACHE_TTL:
            return None
        if age_s >= cls._STALE_CACHE_TTL:
            cls._result_cache.pop(profile_id, None)
            return None
        return ok, detail, can_thinking, can_multi_turn

    def start(self) -> None:
        """Queue the worker on the desktop auxiliary pool."""
        if self._running:
            return
        self._running = True
        self._finished = False
        self.submit()

    def isRunning(self) -> bool:  # noqa: N802 - keep QThread-like API for callers
        return self._running and not self._finished

    @staticmethod
    def _emit_probe_started(signals: _TestSignals, profile_id: str) -> None:
        try:
            signals.probe_started.emit(profile_id)
        except RuntimeError:
            pass

    @staticmethod
    def _emit_finished(
        signals: _TestSignals,
        profile_id: str,
        success: bool,
        detail: str,
        can_thinking: bool,
        can_multi_turn: bool,
    ) -> None:
        try:
            signals.finished.emit(profile_id, success, detail, can_thinking, can_multi_turn)
        except RuntimeError:
            pass

    async def _run_async(self) -> None:
        import asyncio

        # Keep a strong local reference to the signals object for the entire
        # duration of _run_async(). The caller may drop its reference early,
        # so holding `signals` as a local variable prevents the _TestSignals
        # QObject from being garbage-collected while this coroutine is running.
        signals = self.signals
        profile = self._profile
        can_thinking, can_multi_turn = get_model_capabilities(
            profile.provider,
            profile.model_id,
        )
        # Return cached result if recent enough (avoids rate-limit on coding endpoints)
        cached = None if self._force_refresh else self.cached_result(profile.profile_id)
        if cached is not None:
            c_ok, c_detail, c_think, c_multi = cached
            self._emit_finished(signals, profile.profile_id, c_ok, c_detail, c_think, c_multi)
            self._finished = True
            self._running = False
            return
        try:
            if is_embedding_model(profile.provider, profile.model_id):
                self._emit_probe_started(signals, profile.profile_id)
                start = time.monotonic()
                ok, detail = await asyncio.wait_for(
                    self._embedding_health_check(profile),
                    timeout=self._TIMEOUT_SECONDS,
                )
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if not ok:
                    self._result_cache[profile.profile_id] = (
                        time.monotonic(),
                        False,
                        detail,
                        False,
                        False,
                    )
                    self._emit_finished(
                        signals,
                        profile.profile_id,
                        False,
                        detail,
                        False,
                        False,
                    )
                    return
                result_detail = f"嵌入连通 {elapsed_ms}ms"
                self._result_cache[profile.profile_id] = (
                    time.monotonic(),
                    True,
                    result_detail,
                    can_thinking,
                    can_multi_turn,
                )
                self._emit_finished(
                    signals,
                    profile.profile_id,
                    True,
                    result_detail,
                    can_thinking,
                    can_multi_turn,
                )
                return

            adapter = self._make_adapter(profile)
            try:
                self._emit_probe_started(signals, profile.profile_id)
                start = time.monotonic()
                ok = await asyncio.wait_for(
                    adapter.health_check(),
                    timeout=self._TIMEOUT_SECONDS,
                )
            finally:
                try:
                    await self._shutdown_adapter(adapter)
                except Exception:
                    pass
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if not ok:
                detail = self._format_connection_detail(
                    profile,
                    adapter.last_health_error or "连通性检测失败",
                )
                self._result_cache[profile.profile_id] = (
                    time.monotonic(),
                    False,
                    detail,
                    False,
                    False,
                )
                self._emit_finished(signals, profile.profile_id, False, detail, False, False)
                return
        except Exception as exc:
            raw_msg = str(exc).strip() or type(exc).__name__
            if self._is_benign_model_chatter(raw_msg):
                detail = "连通（模型对探活返回了无关内容，已忽略）"
                self._result_cache[profile.profile_id] = (
                    time.monotonic(),
                    True,
                    detail,
                    can_thinking,
                    can_multi_turn,
                )
                self._emit_finished(
                    signals,
                    profile.profile_id,
                    True,
                    detail,
                    can_thinking,
                    can_multi_turn,
                )
            else:
                msg = self._format_connection_detail(profile, raw_msg, exc=exc)
                if self._is_persistent_failure(msg, exc):
                    self._result_cache[profile.profile_id] = (
                        time.monotonic(),
                        False,
                        msg,
                        False,
                        False,
                    )
                self._emit_finished(signals, profile.profile_id, False, msg, False, False)
            return
        result_detail = f"连通 {elapsed_ms}ms"
        self._result_cache[profile.profile_id] = (
            time.monotonic(),
            True,
            result_detail,
            can_thinking,
            can_multi_turn,
        )
        self._emit_finished(
            signals,
            profile.profile_id,
            True,
            result_detail,
            can_thinking,
            can_multi_turn,
        )
        self._finished = True
        self._running = False

    @staticmethod
    def _format_connection_detail(
        profile: ModelProfile,
        detail: str,
        *,
        exc: Exception | None = None,
    ) -> str:
        msg = str(detail or "").strip() or (type(exc).__name__ if exc is not None else "")
        lower = msg.lower()
        if "openai package not installed" in lower or "no module named 'openai'" in lower:
            msg = (
                "缺少 openai 依赖：当前虚拟环境未安装 OpenAI SDK。请在项目根目录运行 "
                ".venv/bin/python -m pip install -e '.[openai]'，或安装完整依赖 '.[all]'。"
            )
            return msg
        if profile.provider == "deepseek":
            if "service is too busy" in lower or "service_unavailable" in lower or "503" in lower:
                msg = "供应商繁忙：DeepSeek API 返回 503 Service is too busy，请稍后重试或临时切换备用模型。"
            elif "timeout" in lower or (exc is not None and type(exc).__name__ == "TimeoutError"):
                msg = (
                    f"连接超时：DeepSeek API 未在 {int(_ConnectionTestWorker._TIMEOUT_SECONDS)}s "
                    "内返回，通常是供应商排队/拥塞或网络代理阻塞。"
                )
        if len(msg) > 200:
            msg = msg[:197] + "..."
        return msg

    @staticmethod
    def _is_benign_model_chatter(raw_msg: str) -> bool:
        lower = (raw_msg or "").lower()
        markers = (
            "image.png",
            "does not support image input",
            'cannot read "image',
            "cannot read 'image",
            "inform the user",
        )
        return any(marker in lower for marker in markers)

    @staticmethod
    def _is_persistent_failure(msg: str, exc: Exception | None) -> bool:
        lower = (msg or "").lower()
        persistent_markers = (
            "401",
            "403",
            "unauthorized",
            "invalid api key",
            "鉴权失败",
            "api key",
            "model not found",
            "model_not_found",
            "模型不存在",
            "no default model configured",
            "未配置",
            "no such model",
            "no model_id configured",
        )
        return any(marker in lower for marker in persistent_markers)

    @staticmethod
    async def _embedding_health_check(profile: ModelProfile) -> tuple[bool, str]:
        from novel_forge.gateway.embedding import EmbeddingService

        service = EmbeddingService(
            provider=profile.provider,
            api_key=profile.api_key or None,
            model=profile.model_id,
            base_url=profile.base_url or None,
        )
        try:
            result = await service.generate_embedding("ping")
        except Exception as exc:
            msg = str(exc).strip() or type(exc).__name__
            if (
                "模型不存在" in msg
                or "model not found" in msg.lower()
                or "model_not_found" in msg.lower()
                or "code': '1006" in msg
            ):
                msg = "模型不存在或未开通：请确认模型 ID、账号权限，且使用 /v1/embeddings 端点。"
            elif "401" in msg or "unauthorized" in msg.lower() or "invalid api key" in msg.lower():
                msg = "鉴权失败：请检查 API Key 是否正确且有权限调用 embedding。"
            elif "timeout" in msg.lower() or "timed out" in msg.lower():
                msg = "连接超时：请检查网络/代理，或本地服务是否启动。"
            elif "connection refused" in msg.lower() or "cannot connect" in msg.lower():
                msg = "无法连接服务：请检查 base_url 是否正确、服务是否运行。"
            if len(msg) > 200:
                msg = msg[:197] + "..."
            return False, msg
        finally:
            try:
                await service.aclose()
            except Exception:
                pass
        dim = len(result.embedding) if isinstance(result.embedding, list) else 0
        if dim <= 0:
            return False, "嵌入返回为空向量"
        return True, f"OK ({dim}维)"

    @staticmethod
    async def _shutdown_adapter(adapter: object) -> None:
        close_hook = getattr(adapter, "shutdown", None)
        if not callable(close_hook):
            close_hook = getattr(adapter, "aclose", None)
        if not callable(close_hook):
            return
        result = close_hook()
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _make_adapter(profile: ModelProfile) -> _HealthCheckAdapter:
        """Instantiate the provider adapter for health-check."""
        if profile.provider == "custom":
            if not profile.base_url.strip():
                raise ValueError("自定义供应商需要填写接口地址。")
            from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter

            return cast(
                _HealthCheckAdapter,
                OpenAICompatibleAdapter(
                    profile.api_key,
                    profile.base_url,
                    "custom",
                    profile.model_id,
                ),
            )
        adapter_map = {
            "tongyi": ("novel_forge.gateway.adapters.tongyi", "TongyiAdapter"),
            "tongyi_coding": ("novel_forge.gateway.adapters.tongyi", "TongyiAdapter"),
            "tongyi_token_plan": ("novel_forge.gateway.adapters.tongyi", "TongyiAdapter"),
            "deepseek": ("novel_forge.gateway.adapters.deepseek", "DeepSeekAdapter"),
            "openai": ("novel_forge.gateway.adapters.openai", "OpenAIAdapter"),
            "anthropic": ("novel_forge.gateway.adapters.anthropic", "AnthropicAdapter"),
            "kimi": ("novel_forge.gateway.adapters.kimi", "KimiAdapter"),
            "mimo": ("novel_forge.gateway.adapters.mimo", "MiMoAdapter"),
            "tencent": ("novel_forge.gateway.adapters.tencent_hunyuan", "TencentHunyuanAdapter"),
            "ollama": ("novel_forge.gateway.adapters.ollama", "OllamaAdapter"),
            "minimax": ("novel_forge.gateway.adapters.minimax", "MiniMaxAdapter"),
            "siliconflow": ("novel_forge.gateway.adapters.siliconflow", "SiliconFlowAdapter"),
            "volcengine_ark": (
                "novel_forge.gateway.adapters.volcengine_ark",
                "VolcengineArkAdapter",
            ),
            "opencode": ("novel_forge.gateway.adapters.opencode", "OpenCodeAdapter"),
        }
        entry = adapter_map.get(profile.provider)
        if entry is None:
            raise ValueError(f"未知供应商: {profile.provider}")
        module_path, class_name = entry
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        kwargs: dict[str, str] = {}
        tongyi_plan_default_urls = {
            "tongyi_coding": TONGYI_CODING_PLAN_BASE_URL,
            "tongyi_token_plan": TONGYI_TOKEN_PLAN_BASE_URL,
        }
        if profile.provider in tongyi_plan_default_urls:
            kwargs["base_url"] = profile.base_url or tongyi_plan_default_urls[profile.provider]
        elif profile.base_url:
            kwargs["base_url"] = profile.base_url
        if profile.model_id:
            kwargs["default_model"] = profile.model_id

        # Ollama uses keyword-only arguments and doesn't need API key
        if profile.provider == "ollama":
            return cast(_HealthCheckAdapter, cls(**kwargs))
        return cast(_HealthCheckAdapter, cls(profile.api_key, **kwargs))


class _RuntimeCard(Surface):
    """Small read-only summary card."""

    def __init__(self, title: str, *, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__("card", parent)
        self.setProperty("compact", compact)
        self.setMinimumHeight(76 if compact else 0)
        layout = QVBoxLayout(self)
        if compact:
            layout.setContentsMargins(12, 10, 12, 10)
        else:
            layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4 if compact else 6)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        self.title_label.setProperty("compact", compact)
        layout.addWidget(self.title_label)

        self.value_label = QLabel("")
        self.value_label.setObjectName("cardBody")
        self.value_label.setProperty("compact", compact)
        self.value_label.setWordWrap(True)
        layout.addWidget(self.value_label)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("cardHint")
        self.meta_label.setProperty("compact", compact)
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

    def bind(self, value: str, meta: str) -> None:
        self.value_label.setText(value)
        self.meta_label.setText(meta)
