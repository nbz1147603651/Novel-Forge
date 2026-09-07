"""Sound-asset settings and local Stable Audio management for Voice Studio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop.components.dialogs import (
    ask_confirmation,
    show_info_message,
    show_warning_message,
)
from novel_forge.desktop.widgets import (
    ActionButton,
    Badge,
    CollapsibleSection,
    Surface,
    add_setting_group_description,
    add_setting_group_header,
    clear_layout,
    make_combo_setting,
    make_line_setting,
    make_spin_setting,
)
from novel_forge.tts.sound_generation.model_manager import (
    ManagedSoundModelStatus,
    StableAudioModelManager,
    StableAudioRuntimeStatus,
)
from novel_forge.tts.sound_generation.models.catalog import SOUND_MODEL_CATALOG


class SoundGenerationSettingsMixin:
    """Host methods for Voice Studio's BGM, ambience, and SFX settings section."""

    _PHASE_ONE_MODEL_IDS: tuple[str, ...] = ("small-sfx",)

    def _build_sound_generation_section(self: Any) -> CollapsibleSection:
        """Build the settings section; all fields are saved by Voice Studio itself."""
        section = CollapsibleSection(
            "声音资产生成 — 环境声、SFX 与 BGM",
            expanded=False,
            persist_key="voice_studio/settings/sound_asset_generation",
        )
        layout = section.body_layout
        layout.setSpacing(8)
        widgets: dict[str, Any] = {}
        add_setting_group_description(
            layout,
            "声音资产属于配音工作流：第一阶段以本地 Stable Audio Small-SFX 补齐环境声和短音效，"
            "无歌词 BGM 使用 MiniMax Music。",
        )

        add_setting_group_header(layout, "工作流", "控制配音脚本如何补齐声音资产。")
        row, widgets["automation_mode"] = make_combo_setting(
            "默认推进模式",
            "桌面项目可临时覆盖；API、后台任务及新项目默认使用这里的模式。",
            ["manual", "assisted", "autonomous"],
            current=self._settings.tts_automation_mode,
            item_labels=["全人工", "AI 伴随（推荐）", "全 AI 自主"],
        )
        layout.addWidget(row)
        complete_mix_preset = ActionButton("切换为全 AI 自主成片", variant="secondary")
        complete_mix_preset.setProperty("compact", True)
        complete_mix_preset.setToolTip(
            "同时启用缺失声音自动生成与自动进入混音；实际生成前仍会执行模型、密钥和运行时预检。"
        )
        complete_mix_preset.clicked.connect(self._apply_complete_mix_preset)
        layout.addWidget(complete_mix_preset)
        row, widgets["enabled"] = make_combo_setting(
            "启用生成式声音",
            "开启后，配音流程会处理 BGM、环境声与 SFX 提示；自动生成另行控制。",
            ["true", "false"],
            current=str(self._settings.sound_generation_enabled).lower(),
        )
        layout.addWidget(row)
        row, widgets["auto_generate"] = make_combo_setting(
            "自动生成缺失素材",
            "素材库未匹配时自动生成；关闭时只报告缺失声音。",
            ["true", "false"],
            current=str(self._settings.sound_generation_auto_generate).lower(),
        )
        layout.addWidget(row)
        row, widgets["auto_approve"] = make_combo_setting(
            "自动进入混音",
            "关闭后，生成资产会保留为待审核，不会自动进入章节混音。",
            ["true", "false"],
            current=str(self._settings.sound_generation_auto_approve).lower(),
        )
        layout.addWidget(row)

        add_setting_group_header(
            layout,
            "声音设计提取",
            "脚本定稿后独立运行，提取 SFX/BGM/环境声/转场设计。",
        )
        row, widgets["sound_design_enabled"] = make_combo_setting(
            "启用声音设计提取",
            "开启后，配音脚本定稿后会自动提取声音设计（SFX、BGM 需求、环境声、转场）。",
            ["true", "false"],
            current=str(self._settings.tts_sound_design_enabled).lower(),
        )
        layout.addWidget(row)
        row, widgets["bgm_palette_minimum_count"] = make_spin_setting(
            "BGM 调色板最低数量",
            "项目声音库中已批准 BGM 不足此数时，冷启动自动生成基础情绪调色板。",
            self._settings.tts_bgm_palette_minimum_count,
            0,
            10,
        )
        layout.addWidget(row)
        row, widgets["default_provider"] = make_combo_setting(
            "默认路由",
            "推荐 auto：BGM 使用 MiniMax，环境声 / SFX 使用本地 Stable Audio。",
            ["auto", "minimax_music", "stable_audio"],
            current=self._settings.sound_generation_default_provider,
            item_labels=["auto（阶段一推荐）", "MiniMax Music", "Stable Audio（扩展）"],
        )
        layout.addWidget(row)
        row, widgets["max_duration_s"] = make_spin_setting(
            "单项最长时长（秒）",
            "单个声音资产的生成时长上限；Small-SFX 最长支持 120 秒。",
            self._settings.sound_generation_max_duration_s,
            1,
            600,
        )
        layout.addWidget(row)
        row, widgets["output_format"] = make_combo_setting(
            "输出格式",
            "推荐 WAV，便于后续时间轴混音；也可选择 MP3 或 FLAC。",
            ["wav", "mp3", "flac"],
            current=self._settings.sound_generation_output_format,
        )
        layout.addWidget(row)
        row, widgets["timeout_s"] = make_spin_setting(
            "生成超时（秒）",
            "本地模型首次加载通常更慢；超时后资产会保留失败原因。",
            self._settings.sound_generation_timeout_s,
            10,
            1800,
        )
        layout.addWidget(row)

        add_setting_group_header(layout, "MiniMax 无歌词 BGM", "第一阶段的伴奏使用云端 API。")
        row, widgets["minimax_api_key"] = make_line_setting(
            "MiniMax Music API Key",
            "留空时回退复用已配置的 TTS / LLM MiniMax Key。",
            self._settings.sound_generation_minimax_api_key,
            secret=True,
        )
        layout.addWidget(row)
        # BGM 模型下拉框：选项来自统一模型目录，展示模型名称、ID、能力标签和适用场景
        minimax_music_models = [
            ("music-3.0", "Music 3.0（推荐）— 商业唱片级音质"),
            ("music-2.6", "Music 2.6 — 上一代稳定模型"),
            ("music-3.0-free", "Music 3.0 Free — 限免版 (RPM 3)"),
            ("music-2.6-free", "Music 2.6 Free — 限免版 (RPM 3)"),
        ]
        row, widgets["minimax_music_model"] = make_combo_setting(
            "BGM 模型",
            "推荐 music-3.0：语义理解升级、音质全面跃升、支持指定乐器与真实技法。",
            [model_id for model_id, _ in minimax_music_models],
            current=self._settings.sound_generation_minimax_music_model,
            item_labels=[label for _, label in minimax_music_models],
        )
        layout.addWidget(row)
        row, widgets["minimax_music_endpoint"] = make_line_setting(
            "BGM API 端点",
            "默认使用 MiniMax 官方全球端点；仅在账号区域或企业网关不同的时候修改。",
            self._settings.sound_generation_minimax_music_endpoint,
        )
        layout.addWidget(row)

        add_setting_group_header(
            layout,
            "Stable Audio 本地环境声 / SFX",
            "应用管理模型缓存；Stable Audio 3 运行时需按官方说明单独安装。",
        )
        row, widgets["stable_audio_command"] = make_line_setting(
            "CLI 命令或路径",
            "默认 stable-audio；也可指定独立虚拟环境中的可执行文件。",
            self._settings.sound_generation_stable_audio_command,
        )
        layout.addWidget(row)
        row, widgets["stable_audio_models_dir"] = make_line_setting(
            "本机共享模型库",
            "模型权重由应用统一管理，所有项目复用；项目只保存生成资产和模型引用，不会随项目切换。",
            self._settings.sound_generation_stable_audio_models_dir,
        )
        widgets["stable_audio_models_dir"].setToolTip(
            "默认位于 Novel Forge 的可写应用数据目录；可迁移到更大的本地磁盘。"
        )
        models_dir_browse = ActionButton("选择位置", variant="secondary")
        models_dir_browse.setProperty("compact", True)
        row._input_slot.setSpacing(8)
        row._input_slot.addWidget(models_dir_browse)
        models_dir_browse.clicked.connect(self._browse_stable_audio_models_dir)
        layout.addWidget(row)
        row, widgets["stable_audio_sfx_model"] = make_line_setting(
            "环境声 / SFX 模型",
            "第一阶段使用 small-sfx；请优先通过下方模型管理选择已下载模型。",
            self._settings.sound_generation_stable_audio_sfx_model,
        )
        layout.addWidget(row)
        row, widgets["huggingface_token"] = make_line_setting(
            "Hugging Face Token",
            "仅用于下载已接受条款的模型，请使用具备访问权限的只读 Token。",
            self._settings.sound_generation_huggingface_token,
            secret=True,
        )
        layout.addWidget(row)
        add_setting_group_description(
            layout,
            "Small-Music 已登记给后续本地 BGM 阶段；当前阶段不会自动下载或调用它。",
        )

        self._sound_generation_widgets = widgets
        widgets["automation_mode"].currentIndexChanged.connect(
            self._sync_sound_generation_mode_widgets
        )
        self._sync_sound_generation_mode_widgets()
        layout.addWidget(self._build_stable_audio_model_panel())
        self._init_stable_audio_runtime_panel()
        return section

    def _apply_complete_mix_preset(self: Any) -> None:
        """Reduce the complete-audio workflow to one explicit, reviewable preset."""

        mode = self._sound_widget("automation_mode")
        if isinstance(mode, QComboBox):
            index = mode.findData("autonomous")
            if index >= 0:
                mode.setCurrentIndex(index)
        self._sync_sound_generation_mode_widgets()
        provider = self._sound_widget("default_provider")
        if isinstance(provider, QComboBox):
            provider_index = provider.findData("auto")
            if provider_index >= 0:
                provider.setCurrentIndex(provider_index)
        if hasattr(self, "_status_badge"):
            self._status_badge.setText(
                "已套用完整成片推荐：人声、BGM、环境声与音效将自动补齐。请保存设置后运行“全流程”。"
            )
            self._status_badge.set_tone("warning")

    def _sync_sound_generation_mode_widgets(self: Any) -> None:
        """Project one authority mode onto the legacy low-level switches."""
        mode_widget = self._sound_widget("automation_mode")
        if not isinstance(mode_widget, QComboBox):
            return
        mode = self._sound_combo_value(mode_widget)
        values = {
            "manual": ("false", "false", "false"),
            "assisted": ("true", "true", "false"),
            "autonomous": ("true", "true", "true"),
        }.get(mode, ("true", "true", "false"))
        for key, value in zip(
            ("enabled", "auto_generate", "auto_approve"),
            values,
            strict=True,
        ):
            widget = self._sound_widget(key)
            if not isinstance(widget, QComboBox):
                continue
            widget.setCurrentText(value)
            widget.setEnabled(False)
            widget.setToolTip("由“默认推进模式”统一管理，避免平台设置与实际执行语义冲突。")

    def _sound_widget(self: Any, key: str) -> Any:
        return self._sound_generation_widgets.get(key)

    def _stable_audio_models_dir_value(self: Any) -> str:
        widget = self._sound_widget("stable_audio_models_dir")
        return str(widget.text()).strip() if widget is not None else ""

    def _browse_stable_audio_models_dir(self: Any) -> None:
        current = self._stable_audio_models_dir_value()
        initial = Path(current).expanduser() if current else Path.home()
        if initial.is_file():
            initial = initial.parent
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 Novel Forge 本机模型库位置",
            str(initial),
        )
        if not selected:
            return
        self._sound_widget("stable_audio_models_dir").setText(
            str(Path(selected).expanduser().resolve())
        )
        self._refresh_stable_audio_runtime_summary()

    def _stable_audio_command_value(self: Any) -> str:
        widget = self._sound_widget("stable_audio_command")
        return str(widget.text()).strip() if widget is not None else "stable-audio"

    def _stable_audio_hf_token_value(self: Any) -> str:
        widget = self._sound_widget("huggingface_token")
        return str(widget.text()).strip() if widget is not None else ""

    def _stable_audio_manager(self: Any) -> StableAudioModelManager:
        return StableAudioModelManager(
            models_dir=self._stable_audio_models_dir_value(),
            command=self._stable_audio_command_value(),
        )

    def _build_stable_audio_model_panel(self: Any) -> QWidget:
        panel = Surface("card")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        top = QHBoxLayout()
        title = QLabel("本地声音模型")
        title.setObjectName("settingGroupTitle")
        top.addWidget(title)
        self._stable_audio_status_badge = Badge("未检测", tone="muted")
        top.addWidget(self._stable_audio_status_badge)
        self._stable_audio_runtime_badge = Badge("运行未定", tone="muted")
        top.addWidget(self._stable_audio_runtime_badge)
        top.addStretch()
        refresh_btn = ActionButton("刷新", variant="secondary")
        refresh_btn.setProperty("compact", True)
        refresh_btn.clicked.connect(self._refresh_stable_audio_models)
        top.addWidget(refresh_btn)
        model_page_btn = ActionButton("模型页", variant="quiet")
        model_page_btn.setProperty("compact", True)
        model_page_btn.clicked.connect(self._open_stable_audio_model_page)
        top.addWidget(model_page_btn)
        layout.addLayout(top)
        self._stable_audio_status_label = QLabel("尚未检测受管的 Stable Audio 模型。")
        self._stable_audio_status_label.setObjectName("panelDescription")
        self._stable_audio_status_label.setWordWrap(True)
        layout.addWidget(self._stable_audio_status_label)
        self._stable_audio_runtime_label = QLabel("")
        self._stable_audio_runtime_label.setObjectName("panelDescription")
        self._stable_audio_runtime_label.setWordWrap(True)
        layout.addWidget(self._stable_audio_runtime_label)
        self._stable_audio_storage_label = QLabel("")
        self._stable_audio_storage_label.setObjectName("panelDescription")
        self._stable_audio_storage_label.setWordWrap(True)
        layout.addWidget(self._stable_audio_storage_label)
        self._stable_audio_legacy_label = QLabel("")
        self._stable_audio_legacy_label.setObjectName("panelDescription")
        self._stable_audio_legacy_label.setWordWrap(True)
        self._stable_audio_legacy_label.setVisible(False)
        layout.addWidget(self._stable_audio_legacy_label)
        actions = QHBoxLayout()
        open_btn = ActionButton("打开目录", variant="secondary")
        open_btn.setProperty("compact", True)
        open_btn.clicked.connect(self._open_stable_audio_storage_dir)
        actions.addWidget(open_btn)
        self._stable_audio_migrate_legacy_btn = ActionButton("迁移旧项目缓存", variant="quiet")
        self._stable_audio_migrate_legacy_btn.setProperty("compact", True)
        self._stable_audio_migrate_legacy_btn.setEnabled(False)
        self._stable_audio_migrate_legacy_btn.clicked.connect(
            self._migrate_legacy_stable_audio_cache
        )
        actions.addWidget(self._stable_audio_migrate_legacy_btn)
        self._stable_audio_install_cli_btn = ActionButton("安装 CLI 运行时", variant="secondary")
        self._stable_audio_install_cli_btn.setProperty("compact", True)
        self._stable_audio_install_cli_btn.setToolTip(
            "Stable Audio CLI 是 SFX 生成的运行时；BGM/声场走 MiniMax 不受影响。"
        )
        self._stable_audio_install_cli_btn.clicked.connect(self._install_stable_audio_cli)
        self._stable_audio_install_cli_btn.setVisible(False)
        actions.addWidget(self._stable_audio_install_cli_btn)
        actions.addStretch()
        layout.addLayout(actions)
        self._stable_audio_models_layout = QVBoxLayout()
        self._stable_audio_models_layout.setContentsMargins(0, 0, 0, 0)
        self._stable_audio_models_layout.setSpacing(8)
        empty = QLabel("点击“刷新”检查 Small-SFX 的本地缓存状态。")
        empty.setObjectName("emptyMessage")
        self._stable_audio_models_layout.addWidget(empty)
        layout.addLayout(self._stable_audio_models_layout)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setObjectName("routingGroupSep")
        layout.addWidget(separator)
        download_row = QHBoxLayout()
        self._stable_audio_download_model = QComboBox()
        for model_id in self._PHASE_ONE_MODEL_IDS:
            descriptor = SOUND_MODEL_CATALOG[model_id]
            self._stable_audio_download_model.addItem(descriptor.display_name, model_id)
        download_row.addWidget(self._stable_audio_download_model, 1)
        self._stable_audio_download_btn = ActionButton("下载", variant="primary")
        self._stable_audio_download_btn.setProperty("compact", True)
        self._stable_audio_download_btn.clicked.connect(self._download_stable_audio_model)
        download_row.addWidget(self._stable_audio_download_btn)
        layout.addLayout(download_row)
        self._stable_audio_download_progress = QProgressBar()
        self._stable_audio_download_progress.setTextVisible(False)
        self._stable_audio_download_progress.setVisible(False)
        layout.addWidget(self._stable_audio_download_progress)
        self._stable_audio_download_status = QLabel("")
        self._stable_audio_download_status.setObjectName("panelDescription")
        self._stable_audio_download_status.setWordWrap(True)
        self._stable_audio_download_status.setVisible(False)
        layout.addWidget(self._stable_audio_download_status)
        return panel

    def _init_stable_audio_runtime_panel(self: Any) -> None:
        self._sound_widget("stable_audio_command").textChanged.connect(
            self._refresh_stable_audio_runtime_summary
        )
        self._refresh_stable_audio_runtime_summary()

    def _open_stable_audio_storage_dir(self: Any) -> None:
        try:
            directory = self._stable_audio_manager().ensure_models_dir()
        except OSError as exc:
            show_warning_message(self, "无法打开目录", f"创建模型目录失败：{exc}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))):
            show_warning_message(self, "无法打开目录", f"Finder 未能打开：{directory}")

    def _open_stable_audio_model_page(self: Any) -> None:
        model_id = str(self._stable_audio_download_model.currentData() or "small-sfx")
        QDesktopServices.openUrl(QUrl(SOUND_MODEL_CATALOG[model_id].model_page_url))

    def _apply_stable_audio_runtime_status(self: Any, status: StableAudioRuntimeStatus) -> None:
        install_btn = getattr(self, "_stable_audio_install_cli_btn", None)
        if not status.huggingface_hub_available:
            self._stable_audio_runtime_badge.setText("下载组件缺失")
            self._stable_audio_runtime_badge.set_tone("danger")
            if install_btn is not None:
                install_btn.setVisible(False)
        elif status.cli_available:
            self._stable_audio_runtime_badge.setText("运行时就绪")
            self._stable_audio_runtime_badge.set_tone("success")
            if install_btn is not None:
                install_btn.setVisible(False)
        elif status.python_package_available:
            self._stable_audio_runtime_badge.setText("需配置 CLI 路径")
            self._stable_audio_runtime_badge.set_tone("warning")
            if install_btn is not None:
                install_btn.setVisible(False)
        else:
            self._stable_audio_runtime_badge.setText("需安装运行时")
            self._stable_audio_runtime_badge.set_tone("warning")
            if install_btn is not None:
                install_btn.setVisible(True)
        self._stable_audio_runtime_label.setText(status.detail)
        self._stable_audio_storage_label.setText(
            f"应用模型库：{self._stable_audio_manager().models_dir.expanduser()} · 所有项目共享"
        )
        legacy_dir = self._legacy_stable_audio_models_dir()
        if legacy_dir is not None:
            self._stable_audio_legacy_label.setText(
                f"检测到旧项目缓存：{legacy_dir}。可复制到应用模型库；旧目录不会自动删除。"
            )
            self._stable_audio_legacy_label.setVisible(True)
        else:
            self._stable_audio_legacy_label.setVisible(False)
        if hasattr(self, "_stable_audio_migrate_legacy_btn"):
            self._stable_audio_migrate_legacy_btn.setEnabled(legacy_dir is not None)

    def _refresh_stable_audio_runtime_summary(self: Any, *_args: object) -> None:
        if hasattr(self, "_stable_audio_runtime_badge"):
            self._apply_stable_audio_runtime_status(self._stable_audio_manager().runtime_status())

    def _legacy_stable_audio_models_dir(self: Any) -> Path | None:
        layout = getattr(self, "_layout", None)
        if layout is None:
            return None
        candidate = layout.tts_dir / "models"
        try:
            return candidate if candidate.is_dir() and any(candidate.iterdir()) else None
        except OSError:
            return None

    def _migrate_legacy_stable_audio_cache(self: Any) -> None:
        legacy_dir = self._legacy_stable_audio_models_dir()
        if legacy_dir is None:
            return
        if not ask_confirmation(
            self,
            "迁移旧项目模型缓存",
            "将复制旧项目的 Stable Audio 缓存到应用模型库。",
            informative_text="为保护已有项目，迁移完成后旧目录仍会保留；确认新库可用后可自行删除。",
            confirm_text="复制并迁移",
            cancel_text="取消",
        ):
            return
        self._stable_audio_migrate_legacy_btn.setEnabled(False)
        self._set_stable_audio_status("迁移中", "warning", "正在复制旧项目模型缓存…")
        worker = self._StableAudioModelWorker(
            models_dir=self._stable_audio_models_dir_value(),
            command=self._stable_audio_command_value(),
            operation="migrate",
            source_dir=str(legacy_dir),
        )
        worker.signals.migration_finished.connect(self._on_stable_audio_migration_finished)
        self._stable_audio_workers.append(worker)
        worker.start()

    def _on_stable_audio_migration_finished(self: Any, success: bool, message: str) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._prune_stable_audio_workers()
        self._set_stable_audio_status(
            "已迁移" if success else "迁移失败", "success" if success else "danger", message
        )
        self._refresh_stable_audio_runtime_summary()
        if success:
            self._refresh_stable_audio_models()

    def _install_stable_audio_cli(self: Any) -> None:
        """Guide the user through installing the Stable Audio 3 CLI runtime.

        Only SFX generation depends on this CLI; BGM and soundscape generation
        route through MiniMax Music 2.6 and are unaffected.  We cannot ship the
        CLI inside the app (it has a separate uv environment with heavyweight
        model dependencies), so we surface the official isolated install flow.
        """
        show_info_message(
            self,
            "安装 Stable Audio CLI 运行时",
            "Stable Audio CLI 是 SFX（音效）生成的本地运行时。",
            informative_text=(
                "BGM 和环境声走 MiniMax Music 2.6，无需安装本组件；仅当需要生成 SFX 时才需要。\n\n"
                "安装方式（使用独立虚拟环境）：\n"
                "  git clone https://github.com/Stability-AI/stable-audio-3.git\n"
                "  cd stable-audio-3 && uv sync\n\n"
                "安装后在下方「Stable Audio 命令」填写"
                "<stable-audio-3>/.venv/bin/stable-audio，然后点击「刷新」重新检测。\n\n"
                "官方文档：https://github.com/Stability-AI/stable-audio-3"
            ),
        )
        QDesktopServices.openUrl(QUrl("https://github.com/Stability-AI/stable-audio-3"))

    def _set_stable_audio_status(self: Any, text: str, tone: str, detail: str) -> None:
        self._stable_audio_status_badge.setText(text)
        self._stable_audio_status_badge.set_tone(tone)
        self._stable_audio_status_label.setText(detail)

    def _prune_stable_audio_workers(self: Any) -> None:
        self._stable_audio_workers = [
            worker for worker in self._stable_audio_workers if worker.isRunning()
        ]

    def _refresh_stable_audio_models(self: Any) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._prune_stable_audio_workers()
        self._refresh_stable_audio_runtime_summary()
        self._set_stable_audio_status("检测中", "warning", "正在检查受管模型缓存。")
        worker = self._StableAudioModelWorker(
            models_dir=self._stable_audio_models_dir_value(),
            command=self._stable_audio_command_value(),
            operation="list",
        )
        worker.signals.listed.connect(self._on_stable_audio_models_listed)
        self._stable_audio_workers.append(worker)
        worker.start()

    def _on_stable_audio_models_listed(
        self: Any, success: bool, message: str, statuses: object, runtime: object
    ) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._prune_stable_audio_workers()
        if isinstance(runtime, StableAudioRuntimeStatus):
            self._apply_stable_audio_runtime_status(runtime)
        clear_layout(self._stable_audio_models_layout)
        if not success:
            self._set_stable_audio_status("检测失败", "danger", message)
            return
        managed = (
            [
                item
                for item in statuses
                if isinstance(item, ManagedSoundModelStatus)
                and item.descriptor.model_id in self._PHASE_ONE_MODEL_IDS
            ]
            if isinstance(statuses, list)
            else []
        )
        installed = sum(item.installed for item in managed)
        self._set_stable_audio_status(
            f"{installed}/{len(self._PHASE_ONE_MODEL_IDS)} 已安装",
            "success" if installed else "muted",
            message,
        )
        for status in managed:
            self._stable_audio_models_layout.addWidget(self._build_stable_audio_model_card(status))

    def _build_stable_audio_model_card(self: Any, status: ManagedSoundModelStatus) -> QWidget:
        descriptor = status.descriptor
        card = Surface("inset")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        title = QLabel(descriptor.display_name)
        title.setObjectName("modelNameLabel")
        top.addWidget(title, 1)
        top.addWidget(Badge("环境声 / SFX", tone="default"))
        top.addWidget(
            Badge(
                "已安装" if status.installed else "未下载",
                tone="success" if status.installed else "muted",
            )
        )
        layout.addLayout(top)
        size = self._format_stable_audio_size(status.installed_size_bytes)
        estimate = self._format_stable_audio_size(descriptor.estimated_download_bytes)
        meta = QLabel(
            f"本地占用：{size if status.installed else '—'} · 预计下载：{estimate} · 最长 {descriptor.default_max_duration_s} 秒"
        )
        meta.setObjectName("cardMeta")
        layout.addWidget(meta)
        actions = QHBoxLayout()
        actions.addStretch()
        use_btn = ActionButton("设为环境声模型", variant="secondary")
        use_btn.setProperty("compact", True)
        use_btn.setEnabled(status.installed)
        use_btn.clicked.connect(
            lambda _=False, model_id=descriptor.model_id: self._set_stable_audio_sfx_model(model_id)
        )
        actions.addWidget(use_btn)
        delete_btn = ActionButton("删减本地", variant="danger")
        delete_btn.setProperty("compact", True)
        delete_btn.setEnabled(status.installed)
        delete_btn.clicked.connect(
            lambda _=False, model_id=descriptor.model_id: self._delete_stable_audio_model(model_id)
        )
        actions.addWidget(delete_btn)
        layout.addLayout(actions)
        return card

    @staticmethod
    def _format_stable_audio_size(value: int) -> str:
        units = ("B", "KB", "MB", "GB", "TB")
        size = float(max(value, 0))
        index = 0
        while size >= 1024 and index < len(units) - 1:
            size /= 1024
            index += 1
        return f"{size:.1f} {units[index]}" if index else f"{int(size)} {units[index]}"

    def _download_stable_audio_model(self: Any) -> None:
        if self._stable_audio_download_running:
            return
        model_id = str(self._stable_audio_download_model.currentData() or "")
        descriptor = SOUND_MODEL_CATALOG.get(model_id)
        if descriptor is None:
            return
        if not ask_confirmation(
            self,
            "确认下载本地声音模型",
            f"确定下载「{descriptor.display_name}」？预计需要约 {self._format_stable_audio_size(descriptor.estimated_download_bytes)} 磁盘空间。",
            informative_text="请先在 Hugging Face 模型页接受 Stable Audio 与 Gemma 条款。",
            confirm_text="下载",
            cancel_text="取消",
        ):
            return
        self._stable_audio_download_running = True
        self._stable_audio_download_btn.setEnabled(False)
        self._stable_audio_download_progress.setRange(0, 0)
        self._stable_audio_download_progress.setVisible(True)
        self._stable_audio_download_status.setVisible(True)
        worker = self._StableAudioModelWorker(
            models_dir=self._stable_audio_models_dir_value(),
            command=self._stable_audio_command_value(),
            operation="download",
            model_id=model_id,
            token=self._stable_audio_hf_token_value(),
        )
        worker.signals.download_progress.connect(self._on_stable_audio_download_progress)
        worker.signals.download_finished.connect(self._on_stable_audio_download_finished)
        self._stable_audio_workers.append(worker)
        worker.start()

    def _on_stable_audio_download_progress(self: Any, message: str, percent: int) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._stable_audio_download_status.setText(
            message if percent < 0 else f"{message} · {percent}%"
        )
        if percent >= 0:
            self._stable_audio_download_progress.setRange(0, 100)
            self._stable_audio_download_progress.setValue(percent)

    def _on_stable_audio_download_finished(
        self: Any, success: bool, message: str, _status: object
    ) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._stable_audio_download_running = False
        self._stable_audio_download_btn.setEnabled(True)
        self._stable_audio_download_progress.setRange(0, 100)
        self._stable_audio_download_progress.setValue(100 if success else 0)
        self._stable_audio_download_status.setText(message if success else f"下载失败：{message}")
        self._prune_stable_audio_workers()
        if success:
            self._refresh_stable_audio_models()

    def _delete_stable_audio_model(self: Any, model_id: str) -> None:
        descriptor = SOUND_MODEL_CATALOG.get(model_id)
        if descriptor is None or not ask_confirmation(
            self,
            "确认删除本地声音模型",
            f"确定从当前受管目录删除「{descriptor.display_name}」？",
            informative_text="只会删除模型缓存，不会影响已生成的章节声音资产。",
            confirm_text="确定",
            cancel_text="取消",
            confirm_variant="danger",
        ):
            return
        worker = self._StableAudioModelWorker(
            models_dir=self._stable_audio_models_dir_value(),
            command=self._stable_audio_command_value(),
            operation="delete",
            model_id=model_id,
        )
        worker.signals.delete_finished.connect(self._on_stable_audio_delete_finished)
        self._stable_audio_workers.append(worker)
        worker.start()

    def _on_stable_audio_delete_finished(self: Any, success: bool, message: str) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._prune_stable_audio_workers()
        self._set_stable_audio_status(
            "已更新" if success else "删除失败", "success" if success else "danger", message
        )
        if success:
            self._refresh_stable_audio_models()

    def _set_stable_audio_sfx_model(self: Any, model_id: str) -> None:
        self._sound_widget("stable_audio_sfx_model").setText(model_id)
        self._sync_sound_generation_mode_widgets()
        self._stable_audio_download_status.setText(
            f"已设为环境声 / SFX 模型：{model_id}。请点击页面底部保存设置。"
        )
        self._stable_audio_download_status.setVisible(True)

    def _sound_generation_env_updates(self: Any) -> dict[str, str]:
        """Return all sound-asset config values for Voice Studio's atomic save."""
        widget = self._sound_widget
        updates: dict[str, str] = {
            "NOVEL_FORGE_TTS_AUTOMATION_MODE": self._sound_combo_value(widget("automation_mode")),
            "NOVEL_FORGE_SOUND_GENERATION_ENABLED": self._sound_combo_value(widget("enabled")),
            "NOVEL_FORGE_SOUND_GENERATION_AUTO_GENERATE": self._sound_combo_value(
                widget("auto_generate")
            ),
            "NOVEL_FORGE_SOUND_GENERATION_AUTO_APPROVE": self._sound_combo_value(
                widget("auto_approve")
            ),
            "NOVEL_FORGE_TTS_SOUND_DESIGN_ENABLED": self._sound_combo_value(
                widget("sound_design_enabled")
            ),
            "NOVEL_FORGE_TTS_BGM_PALETTE_MINIMUM_COUNT": str(
                widget("bgm_palette_minimum_count").value()
            ),
            "NOVEL_FORGE_SOUND_GENERATION_DEFAULT_PROVIDER": self._sound_combo_value(
                widget("default_provider")
            ),
            "NOVEL_FORGE_SOUND_GENERATION_MAX_DURATION_S": str(widget("max_duration_s").value()),
            "NOVEL_FORGE_SOUND_GENERATION_OUTPUT_FORMAT": self._sound_combo_value(
                widget("output_format")
            ),
            "NOVEL_FORGE_SOUND_GENERATION_TIMEOUT_S": str(widget("timeout_s").value()),
            "NOVEL_FORGE_SOUND_GENERATION_MINIMAX_API_KEY": widget("minimax_api_key")
            .text()
            .strip(),
            "NOVEL_FORGE_SOUND_GENERATION_MINIMAX_MUSIC_ENDPOINT": widget("minimax_music_endpoint")
            .text()
            .strip(),
            "NOVEL_FORGE_SOUND_GENERATION_MINIMAX_MUSIC_MODEL": self._sound_combo_value(
                widget("minimax_music_model")
            ),
            "NOVEL_FORGE_SOUND_GENERATION_STABLE_AUDIO_COMMAND": widget("stable_audio_command")
            .text()
            .strip(),
            "NOVEL_FORGE_SOUND_GENERATION_STABLE_AUDIO_MODELS_DIR": widget(
                "stable_audio_models_dir"
            )
            .text()
            .strip(),
            "NOVEL_FORGE_SOUND_GENERATION_HUGGINGFACE_TOKEN": widget("huggingface_token")
            .text()
            .strip(),
            "NOVEL_FORGE_SOUND_GENERATION_STABLE_AUDIO_SFX_MODEL": widget("stable_audio_sfx_model")
            .text()
            .strip(),
        }
        return updates

    @staticmethod
    def _sound_combo_value(widget: QComboBox) -> str:
        data = widget.currentData()
        return str(data if data is not None else widget.currentText())


__all__ = ["SoundGenerationSettingsMixin"]
