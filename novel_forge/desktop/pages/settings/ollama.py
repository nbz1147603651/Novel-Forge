"""Engine-observed Ollama management mixin for the Settings page."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from novel_forge.app_service.contracts import JobKind
from novel_forge.app_service.engine_views import OllamaManagerView, OllamaModelView
from novel_forge.app_service.ollama_management import (
    OllamaConfigurationError,
    OllamaEngineCommandService,
)
from novel_forge.core.config import get_settings
from novel_forge.desktop.components.dialogs import (
    ask_confirmation,
    show_info_message,
    show_warning_message,
)
from novel_forge.desktop.widgets import ActionButton, Badge, Surface, clear_layout
from novel_forge.gateway.profiles import load_or_import_profiles

from .contract import SettingsPageMixinBase


class OllamaManagementMixin(SettingsPageMixinBase):
    """Qt presentation and intent submission over the Engine Ollama contract."""

    _OLLAMA_RECOMMENDED_MODELS: tuple[tuple[str, str], ...] = (
        ("轻量生成", "llama3.2"),
        ("中文通用", "qwen2.5:7b"),
        ("长文更稳", "qwen2.5:14b"),
        ("代码辅助", "qwen2.5-coder:7b"),
        ("推理实验", "deepseek-r1:7b"),
        ("经典通用", "mistral"),
        ("语义嵌入", "nomic-embed-text"),
        ("高质量嵌入", "mxbai-embed-large"),
        ("多语嵌入", "bge-m3"),
    )

    def _build_ollama_model_panel(self: Any) -> QWidget:
        panel = Surface("card")
        panel.setMinimumWidth(430)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("Engine 主机模型管理")
        title.setObjectName("settingGroupTitle")
        top.addWidget(title)
        self._ollama_status_badge = Badge("未检测", tone="muted")
        top.addWidget(self._ollama_status_badge)
        self._ollama_runtime_badge = Badge("运行未定", tone="muted")
        top.addWidget(self._ollama_runtime_badge)
        top.addStretch()
        refresh_btn = ActionButton("刷新", variant="secondary")
        refresh_btn.setProperty("compact", True)
        refresh_btn.clicked.connect(self._refresh_ollama_models)
        top.addWidget(refresh_btn)
        library_btn = ActionButton("模型库", variant="quiet")
        library_btn.setProperty("compact", True)
        library_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://ollama.com/library"))
        )
        top.addWidget(library_btn)
        layout.addLayout(top)

        self._ollama_status_label = QLabel("尚未读取 Engine 主机上的 Ollama 状态。")
        self._ollama_status_label.setObjectName("panelDescription")
        self._ollama_status_label.setWordWrap(True)
        layout.addWidget(self._ollama_status_label)
        self._ollama_runtime_label = QLabel("")
        self._ollama_runtime_label.setObjectName("panelDescription")
        self._ollama_runtime_label.setWordWrap(True)
        layout.addWidget(self._ollama_runtime_label)
        self._ollama_storage_label = QLabel("")
        self._ollama_storage_label.setObjectName("panelDescription")
        self._ollama_storage_label.setWordWrap(True)
        layout.addWidget(self._ollama_storage_label)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._ollama_apply_runtime_btn = ActionButton("应用运行设置", variant="secondary")
        self._ollama_apply_runtime_btn.setProperty("compact", True)
        self._ollama_apply_runtime_btn.clicked.connect(self._configure_ollama_runtime)
        controls.addWidget(self._ollama_apply_runtime_btn)
        self._ollama_ensure_btn = ActionButton("检查/启动", variant="secondary")
        self._ollama_ensure_btn.setProperty("compact", True)
        self._ollama_ensure_btn.clicked.connect(lambda: self._submit_ollama_runtime("ensure"))
        controls.addWidget(self._ollama_ensure_btn)
        self._ollama_restart_btn = ActionButton("重启", variant="secondary")
        self._ollama_restart_btn.setProperty("compact", True)
        self._ollama_restart_btn.clicked.connect(lambda: self._submit_ollama_runtime("restart"))
        controls.addWidget(self._ollama_restart_btn)
        self._ollama_stop_btn = ActionButton("停止托管服务", variant="danger")
        self._ollama_stop_btn.setProperty("compact", True)
        self._ollama_stop_btn.clicked.connect(lambda: self._submit_ollama_runtime("stop"))
        controls.addWidget(self._ollama_stop_btn)
        controls.addStretch()
        layout.addLayout(controls)

        self._ollama_operations_host = QWidget()
        self._ollama_operations_layout = QVBoxLayout(self._ollama_operations_host)
        self._ollama_operations_layout.setContentsMargins(0, 0, 0, 0)
        self._ollama_operations_layout.setSpacing(5)
        layout.addWidget(self._ollama_operations_host)

        self._ollama_models_host = QWidget()
        self._ollama_models_layout = QVBoxLayout(self._ollama_models_host)
        self._ollama_models_layout.setContentsMargins(0, 0, 0, 0)
        self._ollama_models_layout.setSpacing(8)
        self._ollama_models_layout.addWidget(QLabel("Engine 主机模型会显示在这里。"))
        layout.addWidget(self._ollama_models_host)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setObjectName("routingGroupSep")
        layout.addWidget(sep)
        pull_title = QLabel("新增模型到 Engine 主机")
        pull_title.setObjectName("settingGroupTitle")
        layout.addWidget(pull_title)
        pull_hint = QLabel("下载由 Engine 系统任务执行；两套桌面 UI 会观察同一任务和恢复结果。")
        pull_hint.setObjectName("panelDescription")
        pull_hint.setWordWrap(True)
        layout.addWidget(pull_hint)
        pull_row = QHBoxLayout()
        pull_row.setSpacing(8)
        self._ollama_pull_input = QLineEdit()
        self._ollama_pull_input.setPlaceholderText("输入模型名，如 llama3.2、qwen2.5:7b")
        pull_row.addWidget(self._ollama_pull_input, 1)
        self._ollama_pick_menu = QMenu(self)
        for label, model in self._OLLAMA_RECOMMENDED_MODELS:
            self._ollama_pick_menu.addAction(
                f"{label} ({model})", lambda m=model: self._ollama_pull_input.setText(m)
            )
        self._ollama_pull_pick_btn = ActionButton("常用", variant="quiet")
        self._ollama_pull_pick_btn.setProperty("compact", True)
        self._ollama_pull_pick_btn.setMenu(self._ollama_pick_menu)
        pull_row.addWidget(self._ollama_pull_pick_btn)
        self._ollama_pull_btn = ActionButton("新增", variant="primary")
        self._ollama_pull_btn.setProperty("compact", True)
        self._ollama_pull_btn.clicked.connect(self._pull_ollama_model)
        pull_row.addWidget(self._ollama_pull_btn)
        layout.addLayout(pull_row)
        self._ollama_pull_progress = QProgressBar()
        self._ollama_pull_progress.setRange(0, 100)
        self._ollama_pull_progress.setValue(0)
        self._ollama_pull_progress.setTextVisible(False)
        self._ollama_pull_progress.setVisible(False)
        layout.addWidget(self._ollama_pull_progress)
        self._ollama_pull_status = QLabel("")
        self._ollama_pull_status.setObjectName("panelDescription")
        self._ollama_pull_status.setWordWrap(True)
        self._ollama_pull_status.setVisible(False)
        layout.addWidget(self._ollama_pull_status)
        return panel

    def bind_engine_job_service(self: Any, job_service: object) -> None:
        """Bind the window's shared Engine job service; no Qt worker owns Ollama."""

        if getattr(self, "_ollama_job_service", None) is job_service:
            return
        self._ollama_job_service = job_service
        self._ollama_engine = OllamaEngineCommandService(job_service)
        self._refresh_ollama_models()

    def supports_job_binding(self: Any) -> bool:
        return True

    def on_jobs_changed(self: Any, jobs: list[object]) -> None:
        seen = getattr(self, "_ollama_seen_job_updates", {})
        changed = False
        for job in jobs:
            kind = str(getattr(job, "kind", "") or "")
            if kind not in {
                JobKind.OLLAMA_RUNTIME_CONTROL.value,
                JobKind.OLLAMA_PULL_MODEL.value,
                JobKind.OLLAMA_DELETE_MODEL.value,
            }:
                continue
            task_id = str(getattr(job, "job_id", "") or "")
            revision = ":".join(
                (
                    str(getattr(job, "updated_at", "") or ""),
                    str(getattr(job, "status", "") or ""),
                    str(getattr(job, "current_step", "") or ""),
                )
            )
            if seen.get(task_id) != revision:
                seen[task_id] = revision
                changed = True
            payload = getattr(job, "current_step_payload", {})
            if kind == JobKind.OLLAMA_PULL_MODEL.value and isinstance(payload, dict):
                percent = int(payload.get("percent", -1) or -1)
                status = str(payload.get("status") or "下载中")
                self._on_ollama_pull_progress(status, percent)
        self._ollama_seen_job_updates = seen
        if changed:
            QTimer.singleShot(0, self._refresh_ollama_models)

    def _engine(self: Any) -> OllamaEngineCommandService:
        return getattr(self, "_ollama_engine", OllamaEngineCommandService(None))

    def _ollama_base_url_value(self: Any) -> str:
        widget = self._param_widgets.get("_ollama_base_url")
        return str(widget.text()).strip() if widget is not None else ""

    def _ollama_sidecar_bool_value(self: Any, key: str, default: bool) -> bool:
        widget = self._param_widgets.get(key)
        try:
            return str(widget.currentText()).strip().lower() == "true" if widget is not None else default
        except AttributeError:
            return default

    def _ollama_sidecar_text_value(self: Any, key: str) -> str:
        widget = self._param_widgets.get(key)
        try:
            return str(widget.text()).strip() if widget is not None else ""
        except AttributeError:
            return ""

    @staticmethod
    def _dialog_start_dir(raw_value: str) -> str:
        candidate = Path(raw_value).expanduser() if raw_value else Path.home()
        return str(candidate.parent if candidate.is_file() else candidate)

    def _browse_ollama_sidecar_binary_dir(self: Any) -> None:
        self._browse_ollama_path("_ollama_sidecar_binary_path", "选择 Ollama 可执行目录")

    def _browse_ollama_sidecar_models_dir(self: Any) -> None:
        self._browse_ollama_path("_ollama_sidecar_models_dir", "选择 Ollama 模型目录")

    def _browse_ollama_path(self: Any, key: str, title: str) -> None:
        current = self._ollama_sidecar_text_value(key)
        directory = QFileDialog.getExistingDirectory(self, title, self._dialog_start_dir(current))
        if directory and self._param_widgets.get(key) is not None:
            self._param_widgets[key].setText(str(Path(directory).expanduser().resolve()))

    def _init_ollama_runtime_panel(self: Any) -> None:
        if getattr(self, "_ollama_runtime_controls_initialized", False):
            return
        for key, handler in (
            ("_ollama_sidecar_binary_browse_btn", self._browse_ollama_sidecar_binary_dir),
            ("_ollama_sidecar_models_browse_btn", self._browse_ollama_sidecar_models_dir),
        ):
            button = self._param_widgets.get(key)
            if button is not None:
                button.clicked.connect(handler)
        self._ollama_runtime_controls_initialized = True

    def _set_ollama_status(self: Any, text: str, *, tone: str = "muted", detail: str = "") -> None:
        self._ollama_status_badge.setText(text)
        self._ollama_status_badge.set_tone(tone)
        if detail:
            self._ollama_status_label.setText(detail)

    def _prune_ollama_workers(self: Any) -> None:
        self._ollama_workers = [worker for worker in self._ollama_workers if worker.isRunning()]

    def _refresh_ollama_models(self: Any) -> None:
        self._prune_ollama_workers()
        self._set_ollama_status("检测中", tone="warning", detail="正在读取 Engine Ollama 状态。")
        worker = self._OllamaEngineViewWorker(getattr(self, "_ollama_job_service", None))
        worker.signals.loaded.connect(self._on_ollama_view_loaded)
        self._ollama_workers.append(worker)
        worker.start()

    def _on_ollama_view_loaded(self: Any, success: bool, message: str, payload: object) -> None:
        self._prune_ollama_workers()
        if not success or not isinstance(payload, OllamaManagerView):
            self._set_ollama_status("需要升级 Engine", tone="danger", detail=message or "无法读取 Engine Ollama 契约。")
            return
        self._ollama_view = payload
        view = payload
        tones = {
            "healthy": "success",
            "external": "success",
            "starting": "warning",
            "degraded": "warning",
        }
        tone = tones.get(view.runtime.status, "danger")
        self._set_ollama_status(
            f"{len(view.models)} 个模型" if tone == "success" else "运行未就绪",
            tone=tone,
            detail=view.runtime.detail,
        )
        self._ollama_runtime_badge.setText(view.runtime.status)
        self._ollama_runtime_badge.set_tone(tone)
        self._ollama_runtime_label.setText(
            f"Engine 主机 · {'托管 sidecar' if view.ownership == 'engine_owned' else '外部服务' if view.ownership == 'external' else '不可用'}"
            + (f" · v{view.runtime.version}" if view.runtime.version else "")
        )
        self._ollama_storage_label.setText(view.storage.display_label)
        self._ollama_ensure_btn.setEnabled(view.capabilities.can_ensure)
        self._ollama_restart_btn.setEnabled(view.capabilities.can_restart)
        self._ollama_stop_btn.setEnabled(view.capabilities.can_stop)
        self._ollama_pull_btn.setEnabled(view.capabilities.can_pull)
        self._render_ollama_operations(view)
        clear_layout(self._ollama_models_layout)
        if not view.models:
            empty = QLabel("Engine 主机上尚无模型。可以在下方下载。")
            empty.setObjectName("emptyMessage")
            self._ollama_models_layout.addWidget(empty)
        for model in view.models:
            self._ollama_models_layout.addWidget(self._build_ollama_model_card(model))

    def _render_ollama_operations(self: Any, view: OllamaManagerView) -> None:
        clear_layout(self._ollama_operations_layout)
        for operation in view.active_operations:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{operation.model or '运行时'} · {operation.operation or operation.kind} · {operation.state}"), 1)
            if operation.state in {"queued", "running"}:
                button = ActionButton("取消", variant="quiet")
                button.setProperty("compact", True)
                button.clicked.connect(lambda _checked=False, task_id=operation.task_id: self._cancel_ollama_job(task_id))
                row.addWidget(button)
            elif operation.state == "failed":
                button = ActionButton("重试", variant="quiet")
                button.setProperty("compact", True)
                button.clicked.connect(lambda _checked=False, task_id=operation.task_id: self._resume_ollama_job(task_id))
                row.addWidget(button)
            self._ollama_operations_layout.addLayout(row)

    def _build_ollama_model_card(self: Any, model: OllamaModelView) -> QWidget:
        details = model.details if isinstance(model.details, dict) else {}
        meta_parts = [
            str(details.get("family") or ""),
            str(details.get("parameter_size") or details.get("parameterSize") or ""),
            str(details.get("quantization_level") or details.get("quantizationLevel") or ""),
            self._format_ollama_size(model.size),
            model.modified_at.split("T", 1)[0],
        ]
        card = Surface("inset")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(7)
        top = QHBoxLayout()
        title = QLabel(model.name or "(未命名模型)")
        title.setObjectName("modelNameLabel")
        top.addWidget(title, 1)
        top.addWidget(Badge(self._role_label(model), tone="default"))
        card_layout.addLayout(top)
        meta = QLabel(" · ".join(part for part in meta_parts if part) or "由 Engine 管理")
        meta.setObjectName("cardMeta")
        meta.setWordWrap(True)
        card_layout.addWidget(meta)
        row = QHBoxLayout()
        row.addStretch()
        roles = set(model.roles)
        for label, patch in (
            ("取消生成" if "generation" in roles else "设为生成", {"generation": "generation" not in roles}),
            ("取消嵌入" if "embedding" in roles else "设为嵌入", {"embedding": "embedding" not in roles}),
            ("移出路由" if "managed" in roles else "纳入路由", {"managed": "managed" not in roles}),
        ):
            button = ActionButton(label, variant="secondary")
            button.setProperty("compact", True)
            button.clicked.connect(lambda _checked=False, value=model, changes=patch: self._set_ollama_roles(value, changes))
            row.addWidget(button)
        delete = ActionButton("删除", variant="danger")
        delete.setProperty("compact", True)
        current_view = getattr(self, "_ollama_view", None)
        delete.setEnabled(isinstance(current_view, OllamaManagerView) and current_view.capabilities.can_delete)
        delete.clicked.connect(lambda _checked=False, value=model: self._delete_ollama_model(value))
        row.addWidget(delete)
        card_layout.addLayout(row)
        return card

    @staticmethod
    def _role_label(model: OllamaModelView) -> str:
        roles = set(model.roles)
        if {"generation", "embedding"} <= roles:
            return "生成 + 嵌入"
        if "generation" in roles:
            return "生成"
        if "embedding" in roles:
            return "嵌入"
        return "已纳入路由" if "managed" in roles else "未配置"

    @staticmethod
    def _format_ollama_size(value: object) -> str:
        if not isinstance(value, (int, float)) or value <= 0:
            return ""
        units = ("B", "KB", "MB", "GB", "TB")
        size = float(value)
        index = 0
        while size >= 1024 and index < len(units) - 1:
            size /= 1024
            index += 1
        return f"{size:.1f} {units[index]}" if index else f"{int(size)} {units[index]}"

    def _configure_ollama_runtime(self: Any) -> None:
        view = getattr(self, "_ollama_view", None)
        if not isinstance(view, OllamaManagerView):
            self._refresh_ollama_models()
            return
        try:
            self._engine().configure_runtime(
                get_settings(),
                expected_revision=view.revision,
                base_url=self._ollama_base_url_value(),
                enabled=self._ollama_sidecar_bool_value("_ollama_sidecar_enabled", True),
                auto_start=self._ollama_sidecar_bool_value("_ollama_sidecar_auto_start", True),
                prefer_local=self._ollama_sidecar_bool_value("_ollama_sidecar_prefer_local", True),
                binary_path=self._ollama_sidecar_text_value("_ollama_sidecar_binary_path"),
                models_dir=self._ollama_sidecar_text_value("_ollama_sidecar_models_dir"),
                allow_path_configuration=True,
                allow_endpoint_configuration=True,
            )
        except OllamaConfigurationError as exc:
            show_warning_message(self, "无法应用运行设置", str(exc))
            return
        self._settings = get_settings()
        self._config = load_or_import_profiles(self._settings)
        self._refresh_ollama_models()

    def _submit_ollama_runtime(self: Any, operation: str) -> None:
        view = getattr(self, "_ollama_view", None)
        if not isinstance(view, OllamaManagerView):
            self._refresh_ollama_models()
            return
        try:
            self._engine().submit_runtime(
                get_settings(), expected_revision=view.revision, idempotency_key=uuid4().hex, operation=operation
            )
        except OllamaConfigurationError as exc:
            show_warning_message(self, "无法执行运行时操作", str(exc))
            return
        self._set_ollama_status("已提交", tone="warning", detail="运行时操作已交由 Engine 系统任务执行。")
        self._refresh_ollama_models()

    def _set_ollama_roles(self: Any, model: OllamaModelView, patch: dict[str, bool]) -> None:
        view = getattr(self, "_ollama_view", None)
        if not isinstance(view, OllamaManagerView):
            return
        roles = {
            "managed": "managed" in model.roles,
            "generation": "generation" in model.roles,
            "embedding": "embedding" in model.roles,
            **patch,
        }
        if roles["generation"] or roles["embedding"]:
            roles["managed"] = True
        try:
            self._engine().set_model_roles(
                get_settings(), expected_revision=view.revision, model=model.name, **roles
            )
        except OllamaConfigurationError as exc:
            show_warning_message(self, "无法更新模型角色", str(exc))
            return
        self._settings = get_settings()
        self._config = load_or_import_profiles(self._settings)
        self._refresh_models_list()
        self._refresh_model_status_grid()
        self._refresh_route_combos()
        self._refresh_embedding_combo()
        self._refresh_ollama_models()

    def _delete_ollama_model(self: Any, model: OllamaModelView) -> None:
        view = getattr(self, "_ollama_view", None)
        if not isinstance(view, OllamaManagerView):
            return
        impact = view.routing_impact_by_model.get(model.name)
        if impact is None:
            self._refresh_ollama_models()
            return
        parts = []
        if impact.generation_selected:
            parts.append("当前生成模型")
        if impact.embedding_selected:
            parts.append("当前嵌入模型")
        if impact.profile_ids:
            parts.append(f"{len(impact.profile_ids)} 个模型配置")
        if impact.primary_route_ids:
            parts.append(f"{len(impact.primary_route_ids)} 条主路由")
        if impact.fallback_route_ids:
            parts.append(f"{len(impact.fallback_route_ids)} 条备用路由")
        impact_text = "、".join(parts)
        if not ask_confirmation(
            self,
            "确认删除 Engine 模型",
            f"确定删除 Engine 主机上的 Ollama 模型「{model.name}」？",
            informative_text=(f"将同时清理：{impact_text}。" if impact_text else "模型未被当前配置引用。"),
            confirm_text="确定删除",
            cancel_text="取消",
            confirm_variant="danger",
        ):
            return
        try:
            self._engine().submit_delete(
                get_settings(),
                expected_revision=view.revision,
                idempotency_key=uuid4().hex,
                model=model.name,
                confirmation_token=impact.confirmation_token,
                cascade_configuration=bool(impact_text),
            )
        except OllamaConfigurationError as exc:
            show_warning_message(self, "无法删除模型", str(exc))
            return
        self._set_ollama_status("已提交", tone="warning", detail=f"{model.name} 的删除 Saga 已交由 Engine 执行。")
        self._refresh_ollama_models()

    def _pull_ollama_model(self: Any) -> None:
        model = self._ollama_pull_input.text().strip()
        view = getattr(self, "_ollama_view", None)
        if not model:
            show_warning_message(self, "无法下载", "请先填写要下载的模型名。")
            return
        if not isinstance(view, OllamaManagerView):
            self._refresh_ollama_models()
            return
        try:
            self._engine().submit_pull(
                get_settings(), expected_revision=view.revision, idempotency_key=uuid4().hex, model=model
            )
        except OllamaConfigurationError as exc:
            show_warning_message(self, "无法下载", str(exc))
            return
        self._ollama_pull_progress.setVisible(True)
        self._ollama_pull_progress.setRange(0, 0)
        self._ollama_pull_status.setText(f"{model} 已提交给 Engine 下载任务。")
        self._ollama_pull_status.setVisible(True)
        self._refresh_ollama_models()

    def _on_ollama_pull_progress(self: Any, status: str, percent: int) -> None:
        self._ollama_pull_progress.setVisible(True)
        if percent >= 0:
            self._ollama_pull_progress.setRange(0, 100)
            self._ollama_pull_progress.setValue(percent)
            self._ollama_pull_status.setText(f"{status} · {percent}%")
        else:
            self._ollama_pull_progress.setRange(0, 0)
            self._ollama_pull_status.setText(status)
        self._ollama_pull_status.setVisible(True)

    def _cancel_ollama_job(self: Any, task_id: str) -> None:
        service = getattr(self, "_ollama_job_service", None)
        if service is None:
            return
        service.cancel(task_id, reason="用户在 PySide 取消 Ollama 管理任务")
        self._refresh_ollama_models()

    def _resume_ollama_job(self: Any, task_id: str) -> None:
        service = getattr(self, "_ollama_job_service", None)
        if service is None:
            return
        try:
            service.resume(task_id)
        except KeyError:
            show_info_message(self, "无法重试", "此任务的恢复意图已不可用，请重新提交操作。")
        self._refresh_ollama_models()
__all__ = ["OllamaManagementMixin"]
