"""Application-level audio model center UI mixed into Voice Studio settings."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.dialogs import ask_confirmation, show_warning_message
from novel_forge.desktop.widgets import ActionButton, Badge, CollapsibleSection, Surface
from novel_forge.tts.model_center.schemas import (
    AudioModelInstallState,
    AudioModelStatus,
    AudioRuntimeDescriptor,
    AudioRuntimeState,
    RuntimeInstallState,
)


class AudioModelCenterMixin:
    _MAX_PARALLEL_AUDIO_DOWNLOADS = 2
    _DOWNLOAD_CANCEL_GUARD_MS = 900

    def _build_audio_model_center_section(self: Any) -> CollapsibleSection:
        section = CollapsibleSection(
            "本机音频模型中心",
            expanded=True,
            persist_key="voice_studio/settings/audio_model_center",
        )
        layout = section.body_layout
        layout.setSpacing(8)
        intro = QLabel(
            "模型权重与运行时属于应用，所有项目共享。下载完成后会校验文件，并通过 sidecar "
            "检查运行时；项目只保存模型 ID、版本与引用。"
        )
        intro.setObjectName("panelDescription")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self._audio_model_repository_summary = QLabel("正在读取模型库容量与回滚状态…")
        self._audio_model_repository_summary.setObjectName("panelDescription")
        self._audio_model_repository_summary.setWordWrap(True)
        layout.addWidget(self._audio_model_repository_summary)
        top = QHBoxLayout()
        self._audio_model_center_badge = Badge("等待检测", tone="muted")
        top.addWidget(self._audio_model_center_badge)
        top.addStretch()
        open_btn = ActionButton("打开模型库", variant="secondary")
        open_btn.clicked.connect(self._open_audio_model_center_dir)
        top.addWidget(open_btn)
        self._audio_model_migrate_btn = ActionButton("迁移模型库", variant="secondary")
        self._audio_model_migrate_btn.clicked.connect(self._choose_audio_model_repository_target)
        top.addWidget(self._audio_model_migrate_btn)
        self._audio_model_rollback_btn = ActionButton("回滚迁移", variant="secondary")
        self._audio_model_rollback_btn.setEnabled(False)
        self._audio_model_rollback_btn.clicked.connect(self._rollback_audio_model_repository)
        top.addWidget(self._audio_model_rollback_btn)
        self._audio_install_runtimes_btn = ActionButton("安装缺失运行时", variant="secondary")
        self._audio_install_runtimes_btn.setEnabled(False)
        self._audio_install_runtimes_btn.clicked.connect(self._install_missing_audio_runtimes)
        top.addWidget(self._audio_install_runtimes_btn)
        refresh_btn = ActionButton("刷新状态", variant="primary")
        refresh_btn.clicked.connect(self._refresh_audio_model_center)
        top.addWidget(refresh_btn)
        layout.addLayout(top)
        self._audio_model_center_progress = QProgressBar()
        self._audio_model_center_progress.setVisible(False)
        layout.addWidget(self._audio_model_center_progress)
        self._audio_model_center_message = QLabel("")
        self._audio_model_center_message.setObjectName("panelDescription")
        self._audio_model_center_message.setWordWrap(True)
        layout.addWidget(self._audio_model_center_message)
        runtime_title = QLabel("独立运行时")
        runtime_title.setObjectName("sectionTitle")
        layout.addWidget(runtime_title)
        self._audio_runtime_cards_layout = QVBoxLayout()
        self._audio_runtime_cards_layout.setSpacing(8)
        layout.addLayout(self._audio_runtime_cards_layout)
        model_title = QLabel("模型权重")
        model_title.setObjectName("sectionTitle")
        layout.addWidget(model_title)
        self._audio_model_cards_layout = QVBoxLayout()
        self._audio_model_cards_layout.setSpacing(8)
        layout.addLayout(self._audio_model_cards_layout)
        self._audio_model_statuses: dict[str, AudioModelStatus] = {}
        self._audio_runtime_descriptors: dict[str, AudioRuntimeDescriptor] = {}
        self._audio_runtime_states: dict[str, AudioRuntimeState] = {}
        self._audio_repository_status: dict[str, Any] = {}
        self._audio_repository_operation_active = False
        self._audio_model_center_workers: list[Any] = []
        self._audio_model_download_queue: list[tuple[str, bool, bool]] = []
        self._audio_model_download_active: set[str] = set()
        self._audio_model_download_workers: dict[str, Any] = {}
        self._audio_model_download_progress: dict[str, tuple[str, int]] = {}
        self._audio_model_download_cancelling: set[str] = set()
        self._audio_model_download_cancel_available_at: dict[str, float] = {}
        self._audio_runtime_operation_queue: list[tuple[str, str]] = []
        self._audio_runtime_operation_active: set[str] = set()
        self._audio_runtime_operation_progress: dict[str, tuple[str, int]] = {}
        self._audio_model_progress_widgets: dict[str, tuple[QLabel, QProgressBar]] = {}
        self._audio_runtime_progress_widgets: dict[str, tuple[QLabel, QProgressBar]] = {}
        self._refresh_audio_model_center()
        return section

    def _refresh_audio_model_center(self: Any) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._audio_model_center_badge.setText("检测中")
        self._audio_model_center_badge.set_tone("warning")
        worker = self._AudioModelCenterWorker(settings=self._settings, operation="list")
        worker.signals.models_listed.connect(self._on_audio_models_listed)
        worker.signals.runtimes_listed.connect(self._on_audio_runtimes_listed)
        worker.signals.repository_status.connect(self._on_audio_repository_status)
        worker.signals.worker_failed.connect(self._on_audio_model_center_worker_failed)
        self._audio_model_center_workers.append(worker)
        worker.submit()

    def _on_audio_models_listed(self: Any, payloads: list[dict[str, Any]]) -> None:
        statuses = [AudioModelStatus.model_validate(item) for item in payloads]
        self._audio_model_statuses = {
            item.descriptor.plugin_id: item for item in statuses
        }
        installed = 0
        for status in statuses:
            if status.state == AudioModelInstallState.INSTALLED:
                installed += 1
        self._render_audio_model_cards(statuses)
        self._audio_model_center_badge.setText(f"{installed}/{len(statuses)} 已安装")
        self._audio_model_center_badge.set_tone("success" if installed else "muted")
        result_message = str(
            getattr(self, "_audio_model_center_result_message", "") or ""
        )
        self._audio_model_center_message.setText(
            result_message
            or "已连接的 sidecar 会同步报告运行时和模型状态；未连接时仍可管理应用模型文件。"
        )
        self._audio_model_center_result_message = ""
        if hasattr(self, "_sync_provider_settings_ui"):
            self._sync_provider_settings_ui(self._get_current_provider())

    def _on_audio_runtimes_listed(self: Any, payloads: list[dict[str, Any]]) -> None:
        self._audio_runtime_descriptors = {}
        self._audio_runtime_states = {}
        for payload in payloads:
            descriptor = AudioRuntimeDescriptor.model_validate(payload.get("descriptor", {}))
            state = AudioRuntimeState.model_validate(payload.get("state", {}))
            self._audio_runtime_descriptors[descriptor.runtime_id] = descriptor
            self._audio_runtime_states[descriptor.runtime_id] = state
        self._render_audio_runtime_cards()
        self._sync_audio_model_center_action_state()

    def _on_audio_repository_status(self: Any, payload: dict[str, Any]) -> None:
        self._audio_repository_status = dict(payload)
        size = self._format_audio_model_size(int(payload.get("size_bytes") or 0))
        free = self._format_audio_model_size(int(payload.get("free_bytes") or 0))
        rollback = "存在可回滚迁移" if payload.get("rollback_available") else "无回滚锚点"
        root = str(payload.get("root") or "")
        self._audio_model_repository_summary.setText(
            f"已识别模型约 {size} · 所在磁盘可用 {free} · {rollback}\n{root}"
        )
        self._audio_model_repository_summary.setToolTip(root)
        self._sync_audio_model_center_action_state()

    def _render_audio_model_cards(self: Any, statuses: list[AudioModelStatus]) -> None:
        from novel_forge.desktop.widgets import clear_layout

        clear_layout(self._audio_model_cards_layout)
        self._audio_model_progress_widgets = {}
        for status in statuses:
            self._audio_model_cards_layout.addWidget(self._build_audio_model_card(status))

    def _render_audio_runtime_cards(self: Any) -> None:
        from novel_forge.desktop.widgets import clear_layout

        clear_layout(self._audio_runtime_cards_layout)
        self._audio_runtime_progress_widgets = {}
        for runtime_id, descriptor in self._audio_runtime_descriptors.items():
            self._audio_runtime_cards_layout.addWidget(
                self._build_audio_runtime_card(descriptor, self._audio_runtime_states[runtime_id])
            )

    def _build_audio_runtime_card(
        self: Any,
        descriptor: AudioRuntimeDescriptor,
        state: AudioRuntimeState,
    ) -> QWidget:
        card = Surface("inset")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        top.addWidget(QLabel(descriptor.display_name), 1)
        state_label = {
            RuntimeInstallState.NOT_INSTALLED: "未安装",
            RuntimeInstallState.INSTALLED: "已安装",
            RuntimeInstallState.RUNNING: "运行中",
            RuntimeInstallState.STOPPED: "已停止",
            RuntimeInstallState.FAILED: "异常",
            RuntimeInstallState.INCOMPATIBLE: "不兼容",
        }[state.state]
        state_tone = {
            RuntimeInstallState.RUNNING: "success",
            RuntimeInstallState.FAILED: "danger",
            RuntimeInstallState.INCOMPATIBLE: "danger",
            RuntimeInstallState.NOT_INSTALLED: "warning",
            RuntimeInstallState.INSTALLED: "muted",
            RuntimeInstallState.STOPPED: "muted",
        }[state.state]
        top.addWidget(
            Badge(
                state_label,
                tone=state_tone,
            )
        )
        layout.addLayout(top)
        if state.environment_path:
            version_detail = f"当前版本 {state.version or descriptor.version}"
            if state.version != descriptor.version:
                version_detail += f" → 可升级 {descriptor.version}"
        elif state.state == RuntimeInstallState.FAILED and state.version:
            version_detail = f"目标版本 {descriptor.version} · 上次尝试 {state.version}"
        else:
            version_detail = f"目标版本 {descriptor.version}"
        details = QLabel(self._audio_runtime_status_summary(state))
        technical_detail = (
            f"Python {descriptor.python_constraint} · 端口 {descriptor.port or '-'} · "
            f"{version_detail}"
        )
        if state.creator_python_version:
            source_label = {
                "uv_managed": "应用托管 Python",
                "project_venv": "项目虚拟环境 Python",
            }.get(state.creator_python_source, "独立环境 Python")
            technical_detail = (
                f"{technical_detail} · 创建器 {state.creator_python_version}（{source_label}）"
            )
            if state.creator_python:
                technical_detail = f"{technical_detail}\n{state.creator_python}"
        details.setToolTip(technical_detail)
        details.setObjectName("panelDescription")
        layout.addWidget(details)
        if state.last_error and state.state in {
            RuntimeInstallState.FAILED,
            RuntimeInstallState.INCOMPATIBLE,
        }:
            error = QLabel(f"原因：{self._audio_runtime_error_summary(state.last_error)}")
            error.setObjectName("panelDescription")
            error.setWordWrap(True)
            error.setToolTip("完整诊断请点击“查看日志”。")
            layout.addWidget(error)
        runtime_progress = self._audio_runtime_operation_progress.get(descriptor.runtime_id)
        if runtime_progress is not None:
            message, percent = runtime_progress
            progress_label = QLabel(message)
            progress_label.setObjectName("panelDescription")
            progress_bar = QProgressBar()
            self._set_audio_operation_progress(progress_label, progress_bar, message, percent)
            self._audio_runtime_progress_widgets[descriptor.runtime_id] = (
                progress_label,
                progress_bar,
            )
            layout.addWidget(progress_label)
            layout.addWidget(progress_bar)
        actions = QHBoxLayout()
        actions.addStretch()
        if runtime_progress is not None:
            actions.addWidget(QLabel("任务进行中…"))
            layout.addLayout(actions)
            return card
        can_install = state.state == RuntimeInstallState.NOT_INSTALLED or (
            state.state == RuntimeInstallState.FAILED and not state.environment_path
        )
        if can_install:
            button = ActionButton(
                "重试安装" if state.state == RuntimeInstallState.FAILED else "安装独立环境",
                variant="primary",
            )
            button.clicked.connect(
                lambda _=False, runtime_id=descriptor.runtime_id: self._run_audio_runtime_operation(
                    "install_runtime", runtime_id
                )
            )
            actions.addWidget(button)
        elif descriptor.managed_process and state.version != descriptor.version:
            update = ActionButton("升级运行时", variant="primary")
            update.clicked.connect(
                lambda _=False, runtime_id=descriptor.runtime_id: self._run_audio_runtime_operation(
                    "upgrade_runtime", runtime_id
                )
            )
            actions.addWidget(update)
        elif descriptor.managed_process:
            operation = (
                "stop_runtime"
                if state.state == RuntimeInstallState.RUNNING
                else "start_runtime"
            )
            button = ActionButton(
                "停止" if operation == "stop_runtime" else "启动",
                variant="secondary",
            )
            button.clicked.connect(
                lambda _=False, runtime_id=descriptor.runtime_id, op=operation: self._run_audio_runtime_operation(
                    op, runtime_id
                )
            )
            actions.addWidget(button)
            update = ActionButton("检查/更新", variant="secondary")
            update.clicked.connect(
                lambda _=False, runtime_id=descriptor.runtime_id: self._run_audio_runtime_operation(
                    "upgrade_runtime", runtime_id
                )
            )
            actions.addWidget(update)
            rollback = ActionButton("回滚运行时", variant="secondary")
            rollback.setEnabled(state.rollback_available)
            rollback.setToolTip(
                "切换到上一个已验证运行时版本。"
                if state.rollback_available
                else "尚无可回滚的运行时版本。"
            )
            rollback.clicked.connect(
                lambda _=False, runtime_id=descriptor.runtime_id: self._run_audio_runtime_operation(
                    "rollback_runtime", runtime_id
                )
            )
            actions.addWidget(rollback)
        log_path = self._audio_runtime_log_path(descriptor.runtime_id)
        if log_path.is_file():
            open_log = ActionButton("查看日志", variant="secondary")
            open_log.setToolTip(str(log_path))
            open_log.clicked.connect(
                lambda _=False, runtime_id=descriptor.runtime_id: self._open_audio_runtime_log(
                    runtime_id
                )
            )
            actions.addWidget(open_log)
        layout.addLayout(actions)
        return card

    def _build_audio_model_card(self: Any, status: AudioModelStatus) -> QWidget:
        descriptor = status.descriptor
        card = Surface("inset")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        title = QLabel(descriptor.display_name)
        title.setObjectName("modelNameLabel")
        top.addWidget(title, 1)
        top.addWidget(Badge(" / ".join(descriptor.roles), tone="default"))
        state_label = {
            AudioModelInstallState.INSTALLED: "已安装",
            AudioModelInstallState.INCOMPLETE: "不完整",
            AudioModelInstallState.EXTERNAL: "Sidecar 管理",
            AudioModelInstallState.NOT_INSTALLED: "未安装",
        }[status.state]
        top.addWidget(
            Badge(
                "不兼容" if not status.compatible else state_label,
                tone="success" if status.state == AudioModelInstallState.INSTALLED else "warning",
            )
        )
        layout.addLayout(top)
        size = self._format_audio_model_size(status.installed_size_bytes or descriptor.estimated_download_bytes)
        runtime = (
            f"运行时就绪 {status.runtime_version}".rstrip()
            if status.runtime_healthy is True
            else "运行时不可用"
            if status.runtime_healthy is False
            else "运行时未检测"
        )
        detail = QLabel(
            f"{descriptor.family} · {descriptor.recommended_for} · "
            f"{'占用' if status.installed_size_bytes else '预计'} {size} · {runtime}"
        )
        if status.installed_revision:
            detail.setText(f"{detail.text()} · 版本 {status.installed_revision[:12]}")
        detail.setObjectName("cardMeta")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        if status.compatibility_reason:
            compatibility = QLabel(status.compatibility_reason)
            compatibility.setObjectName("panelDescription")
            compatibility.setWordWrap(True)
            layout.addWidget(compatibility)
        if status.local_path:
            path_label = QLabel(f"本机路径：{status.local_path}")
            path_label.setObjectName("panelDescription")
            path_label.setWordWrap(True)
            layout.addWidget(path_label)
        if status.project_references:
            refs = QLabel("项目引用：" + "、".join(status.project_references))
            refs.setObjectName("panelDescription")
            refs.setWordWrap(True)
            layout.addWidget(refs)
        model_progress = self._audio_model_download_progress.get(descriptor.plugin_id)
        if model_progress is not None:
            message, percent = model_progress
            progress_label = QLabel(message)
            progress_label.setObjectName("panelDescription")
            progress_bar = QProgressBar()
            self._set_audio_operation_progress(progress_label, progress_bar, message, percent)
            self._audio_model_progress_widgets[descriptor.plugin_id] = (
                progress_label,
                progress_bar,
            )
            layout.addWidget(progress_label)
            layout.addWidget(progress_bar)
        license_check = QCheckBox(f"已阅读并接受 {descriptor.license_name}")
        license_check.setVisible(descriptor.requires_license_acceptance)
        license_check.setChecked(status.license_accepted)
        license_check.setEnabled(not status.license_accepted)
        if descriptor.requires_license_acceptance and not status.license_accepted:
            license_check.toggled.connect(
                lambda checked, plugin_id=descriptor.plugin_id: self._record_audio_model_license_acceptance(
                    plugin_id, checked
                )
            )
        layout.addWidget(license_check)
        actions = QHBoxLayout()
        actions.addStretch()
        if model_progress is not None:
            is_cancelling = descriptor.plugin_id in self._audio_model_download_cancelling
            actions.addWidget(
                QLabel(
                    "正在取消…"
                    if is_cancelling
                    else "正在下载…"
                    if descriptor.plugin_id in self._audio_model_download_active
                    else "等待下载…"
                )
            )
            cancel_download = ActionButton("取消下载", variant="secondary")
            cancel_available_at = self._audio_model_download_cancel_available_at.get(
                descriptor.plugin_id, 0.0
            )
            if time.monotonic() < cancel_available_at:
                cancel_download.setText("准备下载…")
                cancel_download.setEnabled(False)
            elif is_cancelling:
                cancel_download.setText("正在取消…")
                cancel_download.setEnabled(False)
            cancel_download.clicked.connect(
                lambda _=False, plugin_id=descriptor.plugin_id: self._cancel_audio_model_download(
                    plugin_id
                )
            )
            actions.addWidget(cancel_download)
        elif status.state != AudioModelInstallState.INSTALLED:
            install_btn = ActionButton("通过 Sidecar 安装" if status.state == AudioModelInstallState.EXTERNAL else "下载", variant="primary")
            install_btn.clicked.connect(
                lambda _=False, plugin_id=descriptor.plugin_id, check=license_check: self._run_audio_model_operation(
                    "install", plugin_id, accept_license=check.isChecked()
                )
            )
            actions.addWidget(install_btn)
        if status.state == AudioModelInstallState.INSTALLED:
            test_btn = ActionButton("自检", variant="secondary")
            test_btn.clicked.connect(
                lambda _=False, plugin_id=descriptor.plugin_id: self._run_audio_model_operation(
                    "self_test", plugin_id
                )
            )
            actions.addWidget(test_btn)
            delete_btn = ActionButton("删除", variant="danger")
            delete_btn.clicked.connect(
                lambda _=False, plugin_id=descriptor.plugin_id: self._delete_audio_model(plugin_id)
            )
            actions.addWidget(delete_btn)
        layout.addLayout(actions)
        return card

    def _run_audio_model_operation(
        self: Any,
        operation: str,
        plugin_id: str,
        *,
        accept_license: bool = False,
        force: bool = False,
    ) -> None:
        if operation == "install":
            self._enqueue_audio_model_download(
                plugin_id,
                accept_license=accept_license,
                force=force,
            )
            return
        self._audio_model_center_progress.setVisible(True)
        self._audio_model_center_progress.setRange(0, 0)
        worker = self._AudioModelCenterWorker(
            settings=self._settings,
            operation=operation,
            plugin_id=plugin_id,
            token=self._settings.sound_generation_huggingface_token,
            accept_license=accept_license,
            force=force,
        )
        worker.signals.operation_progress.connect(self._on_audio_model_progress)
        worker.signals.operation_finished.connect(self._on_audio_model_operation_finished)
        worker.signals.worker_failed.connect(self._on_audio_model_center_worker_failed)
        self._audio_model_center_workers.append(worker)
        worker.submit()

    def _record_audio_model_license_acceptance(self: Any, plugin_id: str, checked: bool) -> None:
        if checked:
            self._run_audio_model_operation("accept_license", plugin_id)

    def _run_audio_runtime_operation(self: Any, operation: str, runtime_id: str) -> None:
        if runtime_id in self._audio_runtime_operation_progress:
            return
        self._audio_runtime_operation_queue.append((operation, runtime_id))
        self._audio_runtime_operation_progress[runtime_id] = (
            f"等待运行时队列（最多并行 {self._MAX_PARALLEL_AUDIO_DOWNLOADS} 项）…",
            -1,
        )
        self._sync_audio_model_center_action_state()
        self._render_audio_runtime_cards()
        self._start_queued_audio_runtime_operations()

    def _install_missing_audio_runtimes(self: Any) -> None:
        for runtime_id, state in self._audio_runtime_states.items():
            if state.state == RuntimeInstallState.NOT_INSTALLED or (
                state.state == RuntimeInstallState.FAILED and not state.environment_path
            ):
                self._run_audio_runtime_operation("install_runtime", runtime_id)

    def _model_center_has_active_operations(self: Any) -> bool:
        return bool(
            self._audio_model_download_progress
            or self._audio_runtime_operation_progress
            or self._audio_repository_operation_active
        )

    def _sync_audio_model_center_action_state(self: Any) -> None:
        if not hasattr(self, "_audio_model_migrate_btn"):
            return
        busy = self._model_center_has_active_operations()
        rollback_available = bool(
            self._audio_repository_status.get("rollback_available")
        )
        missing_runtime = any(
            state.state == RuntimeInstallState.NOT_INSTALLED
            or (state.state == RuntimeInstallState.FAILED and not state.environment_path)
            for state in self._audio_runtime_states.values()
        )
        self._audio_model_migrate_btn.setEnabled(not busy)
        self._audio_model_migrate_btn.setToolTip(
            "请等待当前下载或运行时任务结束。" if busy else "迁移前会检查容量并安全停止受管 Sidecar。"
        )
        self._audio_model_rollback_btn.setEnabled(not busy and rollback_available)
        self._audio_model_rollback_btn.setToolTip(
            "请等待当前下载或运行时任务结束。"
            if busy
            else "恢复迁移前的模型库。"
            if rollback_available
            else "当前没有可回滚的模型库迁移。"
        )
        self._audio_install_runtimes_btn.setEnabled(not busy and missing_runtime)
        self._audio_install_runtimes_btn.setToolTip(
            "请等待当前任务结束。"
            if busy
            else "安装所有缺失或安装失败的独立运行时。"
            if missing_runtime
            else "所有独立运行时均已安装。"
        )

    def _start_queued_audio_runtime_operations(self: Any) -> None:
        while (
            self._audio_runtime_operation_queue
            and len(self._audio_runtime_operation_active) < self._MAX_PARALLEL_AUDIO_DOWNLOADS
        ):
            operation, runtime_id = self._audio_runtime_operation_queue.pop(0)
            self._audio_runtime_operation_active.add(runtime_id)
            self._audio_runtime_operation_progress[runtime_id] = ("正在准备任务…", 0)
            worker = self._AudioModelCenterWorker(
                settings=self._settings,
                operation=operation,
                runtime_id=runtime_id,
            )
            worker.signals.runtime_progress.connect(self._on_audio_runtime_progress)
            worker.signals.operation_finished.connect(
                lambda success, message, payload, rid=runtime_id: self._on_audio_runtime_operation_finished(
                    rid, success, message, payload
                )
            )
            worker.signals.worker_failed.connect(
                lambda _worker_id, payload, rid=runtime_id: self._on_audio_runtime_operation_failed(
                    rid, payload
                )
            )
            self._audio_model_center_workers.append(worker)
            worker.submit()

    def _on_audio_runtime_progress(self: Any, runtime_id: str, message: str, percent: int) -> None:
        self._audio_runtime_operation_progress[runtime_id] = (message, percent)
        widgets = self._audio_runtime_progress_widgets.get(runtime_id)
        if widgets is not None:
            self._set_audio_operation_progress(*widgets, message, percent)

    def _on_audio_runtime_operation_finished(
        self: Any,
        runtime_id: str,
        success: bool,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        self._audio_runtime_operation_active.discard(runtime_id)
        self._audio_runtime_operation_progress.pop(runtime_id, None)
        self._sync_audio_model_center_action_state()
        self._on_audio_model_operation_finished(success, message, payload)
        self._start_queued_audio_runtime_operations()

    def _on_audio_runtime_operation_failed(self: Any, runtime_id: str, payload: object) -> None:
        self._audio_runtime_operation_active.discard(runtime_id)
        self._audio_runtime_operation_progress.pop(runtime_id, None)
        self._sync_audio_model_center_action_state()
        self._on_audio_model_center_worker_failed("", payload)
        self._start_queued_audio_runtime_operations()

    def _enqueue_audio_model_download(
        self: Any,
        plugin_id: str,
        *,
        accept_license: bool,
        force: bool,
    ) -> None:
        if plugin_id in self._audio_model_download_progress:
            return
        self._audio_model_download_queue.append((plugin_id, accept_license, force))
        self._audio_model_download_progress[plugin_id] = (
            f"等待下载队列（最多并行 {self._MAX_PARALLEL_AUDIO_DOWNLOADS} 项）…",
            -1,
        )
        self._audio_model_download_cancel_available_at[plugin_id] = (
            time.monotonic() + self._DOWNLOAD_CANCEL_GUARD_MS / 1000
        )
        self._sync_audio_model_center_action_state()
        # ``clicked`` is emitted from QPushButton.mouseReleaseEvent.  Replacing
        # that button synchronously with a same-positioned “取消下载” button can
        # make the latter receive the tail of the same native mouse gesture on
        # macOS.  Defer both the re-render and task submission until this click
        # event has fully unwound, so only a deliberate second click cancels.
        QTimer.singleShot(0, self._render_queued_audio_model_downloads)
        QTimer.singleShot(
            self._DOWNLOAD_CANCEL_GUARD_MS,
            lambda pid=plugin_id: self._enable_audio_model_download_cancellation(pid),
        )

    def _render_queued_audio_model_downloads(self: Any) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._render_audio_model_cards(list(self._audio_model_statuses.values()))
        self._start_queued_audio_model_downloads()

    def _enable_audio_model_download_cancellation(self: Any, plugin_id: str) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        if plugin_id not in self._audio_model_download_progress:
            return
        self._render_audio_model_cards(list(self._audio_model_statuses.values()))

    def _start_queued_audio_model_downloads(self: Any) -> None:
        while (
            self._audio_model_download_queue
            and len(self._audio_model_download_active) < self._MAX_PARALLEL_AUDIO_DOWNLOADS
        ):
            plugin_id, accept_license, force = self._audio_model_download_queue.pop(0)
            self._audio_model_download_active.add(plugin_id)
            self._audio_model_download_progress[plugin_id] = ("正在准备下载…", 0)
            worker = self._AudioModelCenterWorker(
                settings=self._settings,
                operation="install",
                plugin_id=plugin_id,
                token=self._settings.sound_generation_huggingface_token,
                accept_license=accept_license,
                force=force,
            )
            worker.signals.operation_progress.connect(
                lambda message, percent, pid=plugin_id: self._on_audio_model_download_progress(
                    pid, message, percent
                )
            )
            worker.signals.operation_finished.connect(
                lambda success, message, payload, pid=plugin_id: self._on_audio_model_download_finished(
                    pid, success, message, payload
                )
            )
            worker.signals.worker_failed.connect(
                lambda _worker_id, payload, pid=plugin_id: self._on_audio_model_download_failed(
                    pid, payload
                )
            )
            self._audio_model_center_workers.append(worker)
            self._audio_model_download_workers[plugin_id] = worker
            worker.submit()

    def _on_audio_model_download_progress(
        self: Any,
        plugin_id: str,
        message: str,
        percent: int,
    ) -> None:
        if plugin_id in self._audio_model_download_cancelling:
            if message.startswith("正在终止"):
                self._audio_model_download_progress[plugin_id] = (message, -1)
                widgets = self._audio_model_progress_widgets.get(plugin_id)
                if widgets is not None:
                    self._set_audio_operation_progress(*widgets, message, -1)
            return
        self._audio_model_download_progress[plugin_id] = (message, percent)
        widgets = self._audio_model_progress_widgets.get(plugin_id)
        if widgets is not None:
            self._set_audio_operation_progress(*widgets, message, percent)

    def _on_audio_model_download_finished(
        self: Any,
        plugin_id: str,
        success: bool,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        self._audio_model_download_active.discard(plugin_id)
        self._audio_model_download_workers.pop(plugin_id, None)
        self._audio_model_download_progress.pop(plugin_id, None)
        self._audio_model_download_cancelling.discard(plugin_id)
        self._audio_model_download_cancel_available_at.pop(plugin_id, None)
        self._sync_audio_model_center_action_state()
        if payload.get("cancelled"):
            self._audio_model_center_result_message = message
            self._audio_model_center_message.setText(message)
            self._audio_model_center_badge.set_tone("warning")
            self._refresh_audio_model_center()
        else:
            self._on_audio_model_operation_finished(success, message, payload)
        self._start_queued_audio_model_downloads()

    def _on_audio_model_download_failed(self: Any, plugin_id: str, payload: object) -> None:
        self._audio_model_download_active.discard(plugin_id)
        self._audio_model_download_workers.pop(plugin_id, None)
        self._audio_model_download_progress.pop(plugin_id, None)
        self._audio_model_download_cancelling.discard(plugin_id)
        self._audio_model_download_cancel_available_at.pop(plugin_id, None)
        self._sync_audio_model_center_action_state()
        self._on_audio_model_center_worker_failed("", payload)
        self._start_queued_audio_model_downloads()

    def _cancel_audio_model_download(self: Any, plugin_id: str) -> None:
        if time.monotonic() < self._audio_model_download_cancel_available_at.get(plugin_id, 0.0):
            return
        if plugin_id in self._audio_model_download_cancelling:
            return
        if plugin_id in self._audio_model_download_active:
            self._audio_model_download_cancelling.add(plugin_id)
            self._audio_model_download_progress[plugin_id] = ("正在取消下载…", -1)
            worker = self._audio_model_download_workers.get(plugin_id)
            if worker is not None:
                worker.request_cancel()
            self._render_audio_model_cards(list(self._audio_model_statuses.values()))
            return
        self._audio_model_download_queue = [
            item for item in self._audio_model_download_queue if item[0] != plugin_id
        ]
        self._audio_model_download_progress.pop(plugin_id, None)
        self._audio_model_download_cancelling.discard(plugin_id)
        self._audio_model_download_cancel_available_at.pop(plugin_id, None)
        self._sync_audio_model_center_action_state()
        self._render_audio_model_cards(list(self._audio_model_statuses.values()))

    @staticmethod
    def _set_audio_operation_progress(
        label: QLabel,
        progress_bar: QProgressBar,
        message: str,
        percent: int,
    ) -> None:
        label.setText(message)
        if percent < 0:
            progress_bar.setRange(0, 0)
            return
        progress_bar.setRange(0, 100)
        progress_bar.setValue(max(0, min(100, percent)))

    def _choose_audio_model_repository_target(self: Any) -> None:
        if self._model_center_has_active_operations():
            show_warning_message(
                self,
                "暂不能迁移",
                "请等待模型下载和运行时任务全部结束后再迁移模型库。",
            )
            return
        current = str(self._settings.audio_models_root)
        target = QFileDialog.getExistingDirectory(self, "选择新的应用模型库", current)
        if not target:
            return
        if not ask_confirmation(
            self,
            "迁移应用模型库",
            "系统将复制全部权重、运行时和任务状态，并逐文件校验 SHA256。",
            informative_text="原目录会保留为回滚锚点，项目资料不受影响。",
            confirm_text="开始迁移",
            cancel_text="取消",
        ):
            return
        worker = self._AudioModelCenterWorker(
            settings=self._settings,
            operation="migrate_repository",
            target_root=target,
        )
        worker.signals.operation_finished.connect(self._on_audio_model_operation_finished)
        worker.signals.worker_failed.connect(self._on_audio_model_center_worker_failed)
        self._audio_model_center_workers.append(worker)
        self._audio_model_center_progress.setVisible(True)
        self._audio_model_center_progress.setRange(0, 0)
        self._audio_repository_operation_active = True
        self._sync_audio_model_center_action_state()
        worker.submit()

    def _rollback_audio_model_repository(self: Any) -> None:
        if self._model_center_has_active_operations():
            show_warning_message(
                self,
                "暂不能回滚",
                "请等待模型下载和运行时任务全部结束后再回滚。",
            )
            return
        if not self._audio_repository_status.get("rollback_available"):
            show_warning_message(self, "无法回滚", "当前模型库没有可回滚的迁移记录。")
            return
        if not ask_confirmation(
            self,
            "回滚模型库迁移",
            "只有新模型库自迁移后未发生改变时才会执行。",
            informative_text="系统将恢复原路径；如果新库已新增或修改文件，会拒绝自动删除。",
            confirm_text="回滚",
            cancel_text="取消",
        ):
            return
        worker = self._AudioModelCenterWorker(
            settings=self._settings,
            operation="rollback_repository",
        )
        worker.signals.operation_finished.connect(self._on_audio_model_operation_finished)
        worker.signals.worker_failed.connect(self._on_audio_model_center_worker_failed)
        self._audio_model_center_workers.append(worker)
        self._audio_model_center_progress.setVisible(True)
        self._audio_model_center_progress.setRange(0, 0)
        self._audio_repository_operation_active = True
        self._sync_audio_model_center_action_state()
        worker.submit()

    def _delete_audio_model(self: Any, plugin_id: str) -> None:
        status = self._audio_model_statuses.get(plugin_id)
        refs = status.project_references if status is not None else []
        if not ask_confirmation(
            self,
            "删除本机模型",
            "模型仍被项目引用，删除后再次使用需要重新下载。" if refs else "确定删除这个本机模型？",
            informative_text=("引用项目：" + "、".join(refs)) if refs else "项目资料与生成资产不会被删除。",
            confirm_text="仍然删除" if refs else "删除",
            cancel_text="取消",
            confirm_variant="danger",
        ):
            return
        self._run_audio_model_operation("delete", plugin_id, force=bool(refs))

    def _on_audio_model_progress(self: Any, message: str, percent: int) -> None:
        self._audio_model_center_message.setText(message)
        if percent >= 0:
            self._audio_model_center_progress.setRange(0, 100)
            self._audio_model_center_progress.setValue(percent)

    def _on_audio_model_operation_finished(
        self: Any, success: bool, message: str, _payload: dict[str, Any]
    ) -> None:
        repository_root = ""
        if success and _payload.get("state") == "committed":
            repository_root = str(_payload.get("target_root") or "")
        elif success and _payload.get("state") == "rolled_back":
            repository_root = str(_payload.get("source_root") or "")
        if repository_root:
            from novel_forge.core.config import get_settings, get_writable_env_path, reset_settings
            from novel_forge.desktop.config_store import DesktopSettingsStore

            DesktopSettingsStore.merge_env(
                get_writable_env_path(),
                {
                    "NOVEL_FORGE_AUDIO_MODELS_ROOT": repository_root,
                    "NOVEL_FORGE_SOUND_GENERATION_STABLE_AUDIO_MODELS_DIR": str(
                        Path(repository_root) / "stable-audio"
                    ),
                },
            )
            reset_settings()
            self._settings = get_settings()
        self._audio_repository_operation_active = False
        self._sync_audio_model_center_action_state()
        self._audio_model_center_progress.setVisible(False)
        display_message = message if success else f"操作失败：{message}"
        self._audio_model_center_result_message = display_message
        self._audio_model_center_message.setText(display_message)
        self._audio_model_center_badge.set_tone("success" if success else "danger")
        self._refresh_audio_model_center()

    def _on_audio_model_center_worker_failed(self: Any, _worker_id: str, payload: object) -> None:
        self._audio_repository_operation_active = False
        self._sync_audio_model_center_action_state()
        self._audio_model_center_progress.setVisible(False)
        message = payload.get("message", "模型中心任务失败") if isinstance(payload, dict) else str(payload)
        self._audio_model_center_message.setText(message)
        self._audio_model_center_badge.set_tone("danger")

    def _open_audio_model_center_dir(self: Any) -> None:
        from novel_forge.core.config import get_application_models_dir

        path = get_application_models_dir()
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _audio_runtime_log_path(self: Any, runtime_id: str) -> Path:
        root = Path(str(self._settings.audio_models_root)).expanduser().resolve()
        return root / "logs" / f"{runtime_id}.log"

    def _open_audio_runtime_log(self: Any, runtime_id: str) -> None:
        path = self._audio_runtime_log_path(runtime_id)
        if not path.is_file():
            show_warning_message(self, "暂无运行日志", "该运行时尚未写入诊断日志。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @staticmethod
    def _audio_runtime_status_summary(state: AudioRuntimeState) -> str:
        return {
            RuntimeInstallState.RUNNING: "运行正常，可执行本地音频任务。",
            RuntimeInstallState.INSTALLED: "已安装，当前未启动。执行配音时会自动启动。",
            RuntimeInstallState.STOPPED: "已停止。执行配音时会自动启动。",
            RuntimeInstallState.NOT_INSTALLED: "尚未安装，当前不可执行本地音频任务。",
            RuntimeInstallState.FAILED: "运行异常，当前不可执行；可重试启动或检查更新。",
            RuntimeInstallState.INCOMPATIBLE: "版本不兼容，当前不可执行；请检查更新。",
        }[state.state]

    @staticmethod
    def _audio_runtime_error_summary(value: str, *, limit: int = 180) -> str:
        raw = str(value or "").strip()
        if "连续崩溃" in raw:
            return "自动恢复多次失败；请查看日志后重试。"
        ignored_fragments = (
            '"GET /v1/health HTTP/1.1" 200',
            '"GET /v1/version HTTP/1.1" 200',
            '"GET /v1/models HTTP/1.1" 200',
        )
        lines = [
            line.strip()
            for line in raw.splitlines()
            if line.strip()
            and not line.lstrip().startswith("INFO:")
            and not any(fragment in line for fragment in ignored_fragments)
        ]
        if not lines:
            return "未发现有效错误；请刷新状态。"
        summary = lines[-1]
        if len(summary) > limit:
            summary = f"{summary[: limit - 1]}…"
        return summary

    def _available_provider_models(self: Any, provider: str, candidates: list[str]) -> list[str]:
        configured = self._configured_model_for_provider(provider)
        provider_candidates = list(candidates)
        # The generic local endpoint deliberately accepts arbitrary server-side
        # model IDs. Other providers are catalog-backed, so an old model from a
        # different platform must not leak into their selector.
        if provider == "local" and configured and configured not in provider_candidates:
            provider_candidates.append(configured)
        configured_belongs_to_provider = configured in provider_candidates
        if not getattr(self, "_audio_model_statuses", None):
            return provider_candidates
        installed_ids = {
            status.descriptor.model_id
            for status in self._audio_model_statuses.values()
            if status.state == AudioModelInstallState.INSTALLED
            and status.compatible
            and (
                not status.descriptor.endpoint_setting
                or status.runtime_healthy is True
            )
        }
        managed_ids = {status.descriptor.model_id for status in self._audio_model_statuses.values()}
        available = [
            item
            for item in provider_candidates
            if item not in managed_ids or item in installed_ids
        ]
        if configured_belongs_to_provider and configured not in available:
            available.append(configured)
        return available

    def _sync_platform_model_control_state(self: Any, model_id: str) -> None:
        status = next(
            (
                item
                for item in getattr(self, "_audio_model_statuses", {}).values()
                if item.descriptor.model_id == model_id
            ),
            None,
        )
        unavailable = status is not None and (
            status.state != AudioModelInstallState.INSTALLED
            or not status.compatible
            or (
                bool(status.descriptor.endpoint_setting)
                and status.runtime_healthy is not True
            )
        )
        # Keep the selector usable so an old, missing model can be replaced by
        # any installed alternative.  The stale ID remains visible for audit
        # instead of being silently rewritten.
        self._model_combo.setEnabled(True)
        self._model_combo.setToolTip(
            "当前配置的本地模型尚未安装或未被 sidecar 确认；"
            "可前往上方模型中心安装，或切换为已安装模型。"
            if unavailable
            else "仅显示已安装模型；云端或外部平台模型由服务端管理。"
        )

    @staticmethod
    def _format_audio_model_size(value: int) -> str:
        size = float(max(value, 0))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return "0 B"


__all__ = ["AudioModelCenterMixin"]
