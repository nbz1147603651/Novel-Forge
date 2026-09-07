"""Capability planning, plugin routing, and diagnostics UI for Voice Studio."""

from __future__ import annotations

import html
import json
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QSpinBox,
    QTextBrowser,
)

from novel_forge.core.local_model_resources import configure_local_model_resources
from novel_forge.desktop.pages.document_renderer.incremental import update_browser_html
from novel_forge.desktop.widgets import (
    ActionButton,
    Badge,
    CollapsibleSection,
    SettingRow,
    add_setting_group_description,
    add_setting_group_header,
    make_combo_setting,
    make_line_setting,
)
from novel_forge.tts.platform.config import build_audio_execution_plan, registry_from_settings
from novel_forge.tts.platform.field_mapping import (
    FieldSupport,
    VocalDirectionField,
    provider_field_profile,
)
from novel_forge.tts.platform.schemas import (
    AudioExecutionStage,
    AudioLocationPolicy,
    AudioQualityPreset,
)

_PRESET_LABELS = {
    AudioQualityPreset.QUICK_PREVIEW.value: "快速试听",
    AudioQualityPreset.PRODUCTION.value: "本地正式成片",
    AudioQualityPreset.MASTER.value: "极致精校",
    AudioQualityPreset.LOW_RESOURCE.value: "低配置离线",
    AudioQualityPreset.CUSTOM.value: "自定义",
}

_LOCATION_LABELS = {
    AudioLocationPolicy.CLOUD_ONLY.value: "全部线上",
    AudioLocationPolicy.LOCAL_ONLY.value: "全部本地",
    AudioLocationPolicy.PREFER_CLOUD.value: "优先线上",
    AudioLocationPolicy.PREFER_LOCAL.value: "优先本地",
    AudioLocationPolicy.HYBRID.value: "混合生产（推荐）",
}

_STAGE_LABELS = {
    AudioExecutionStage.VOICE_DESIGN: "人物音色设计",
    AudioExecutionStage.VOICE_CLONE: "授权音色克隆",
    AudioExecutionStage.TTS_PREVIEW: "人声试听",
    AudioExecutionStage.TTS_FORMAL: "正式人声",
    AudioExecutionStage.ASR: "转写核验",
    AudioExecutionStage.ALIGN: "真实时间线",
    AudioExecutionStage.ALIGNMENT_VALIDATOR: "对齐复核",
    AudioExecutionStage.VAD: "停顿检测",
    AudioExecutionStage.SFX: "短音效",
    AudioExecutionStage.MUSIC: "背景音乐",
    AudioExecutionStage.SOUNDSCAPE: "长环境声",
    AudioExecutionStage.RENDERER: "多轨渲染",
    AudioExecutionStage.QUALITY: "成片质检",
}


class AudioPlatformSettingsMixin:
    """Host methods mixed into ``VoiceStudioPage``."""

    def _build_audio_planning_section(self: Any) -> CollapsibleSection:
        section = CollapsibleSection(
            "质量目标与模型计划",
            expanded=True,
            persist_key="voice_studio/settings/audio_planning",
        )
        layout = section.body_layout
        layout.setSpacing(8)
        add_setting_group_description(
            layout,
            "先选择成片目标，再由能力计划器组合当前 TTS 平台、ASR、强制对齐、VAD、"
            "声音生成、渲染与质检插件。高级覆盖只固定一个阶段，不会破坏其他回退链路。",
        )

        preset_values = list(_PRESET_LABELS)
        row, self._audio_preset_combo = make_combo_setting(
            "质量预设",
            "普通用户只需选择目标；自定义模式可在下方逐阶段固定插件。",
            preset_values,
            current=self._settings.audio_quality_preset,
            item_labels=[_PRESET_LABELS[item] for item in preset_values],
        )
        layout.addWidget(row)
        location_values = list(_LOCATION_LABELS)
        row, self._audio_location_combo = make_combo_setting(
            "运行位置策略",
            "严格控制每个阶段可使用的位置；全部本地绝不会回退云端。",
            location_values,
            current=self._settings.audio_location_policy,
            item_labels=[_LOCATION_LABELS[item] for item in location_values],
        )
        layout.addWidget(row)
        row, self._audio_accelerator_combo = make_combo_setting(
            "加速器偏好",
            "只影响本地模型排序；不满足时会给出回退，而不是静默改变质量目标。",
            ["auto", "mps", "cuda", "cpu"],
            current=self._settings.audio_accelerator_preference,
            item_labels=["自动检测", "Apple MPS", "NVIDIA CUDA", "CPU"],
        )
        layout.addWidget(row)
        row, self._local_resource_budget_combo = make_combo_setting(
            "全局本机资源预算",
            (
                "同时约束 Ollama、本地 TTS、ASR/对齐、Stable Audio 和本地后处理；"
                "使用 MiniMax 等云端人声时，后续本地阶段仍受此约束。"
            ),
            ["light", "medium", "high"],
            current=self._settings.local_model_resource_budget,
            item_labels=["轻量（单重任务）", "均衡（默认串行大模型）", "宽裕（允许轻模型并行）"],
        )
        layout.addWidget(row)
        resource_wait_row = SettingRow(
            "本机任务最长排队",
            "超时会明确报告正在等待的本地工作负载，不会无限卡住界面。",
        )
        self._local_resource_wait_spin = QSpinBox()
        self._local_resource_wait_spin.setRange(30, 7200)
        self._local_resource_wait_spin.setSuffix(" 秒")
        self._local_resource_wait_spin.setValue(
            round(self._settings.local_model_resource_wait_timeout_s)
        )
        resource_wait_row.set_input(self._local_resource_wait_spin)
        layout.addWidget(resource_wait_row)
        row, self._audio_memory_combo = make_combo_setting(
            "音频选型内存上限",
            "用于能力计划器筛选音频模型；实际运行并发另受全局本机资源预算限制。",
            ["light", "medium", "high"],
            current=self._settings.audio_memory_budget,
            item_labels=["轻量（约 8–12 GB 设备）", "均衡（16 GB）", "宽裕（24 GB 及以上）"],
        )
        layout.addWidget(row)

        validate_row = SettingRow(
            "双对齐器复核",
            "正式时间线由不同实现独立复核；极致精校预设会自动开启。",
        )
        self._audio_dual_alignment_check = QCheckBox()
        self._audio_dual_alignment_check.setChecked(
            self._settings.audio_dual_alignment_validation
        )
        validate_row.set_input(self._audio_dual_alignment_check)
        layout.addWidget(validate_row)

        add_setting_group_header(
            layout,
            "闭环质量门",
            "人声先通过片段客观检查和转写/对齐，再进入声音解析与多轨混音。",
        )
        repair_row = SettingRow(
            "人声自动修复轮数",
            "只重新生成有转写失配或对齐低覆盖证据的片段，旧的可用 take 作为回滚锚点。",
        )
        self._audio_repair_rounds_spin = QSpinBox()
        self._audio_repair_rounds_spin.setRange(0, 3)
        self._audio_repair_rounds_spin.setValue(self._settings.tts_alignment_repair_rounds)
        repair_row.set_input(self._audio_repair_rounds_spin)
        layout.addWidget(repair_row)
        error_row = SettingRow(
            "最大转写错误率",
            "超过阈值的片段进入有界修复；0.12 表示 12%。",
        )
        self._audio_text_error_spin = QDoubleSpinBox()
        self._audio_text_error_spin.setRange(0.0, 1.0)
        self._audio_text_error_spin.setDecimals(2)
        self._audio_text_error_spin.setSingleStep(0.01)
        self._audio_text_error_spin.setValue(self._settings.tts_max_text_error_rate)
        error_row.set_input(self._audio_text_error_spin)
        layout.addWidget(error_row)
        master_row = SettingRow(
            "极致精校硬门",
            "开启后，削波、对齐覆盖或转写质量未达标时保留产物供检查，但不标记为正式成片。",
        )
        self._audio_master_gate_check = QCheckBox()
        self._audio_master_gate_check.setChecked(self._settings.tts_master_quality_gate_blocking)
        master_row.set_input(self._audio_master_gate_check)
        layout.addWidget(master_row)
        row, self._audio_delivery_tier_combo = make_combo_setting(
            "交付质量档",
            "试听只做单遍母带；标准做两遍响度；商业档额外强制跨章节响度、峰值与混音风险门。",
            ["audition", "standard", "commercial"],
            current=self._settings.tts_audio_quality_tier,
            item_labels=["试听（最快）", "标准成片（推荐）", "商业交付（最严）"],
        )
        layout.addWidget(row)
        word_subtitle_row = SettingRow(
            "MiniMax 原生词级字幕",
            "仅在导出词级 SRT 时请求 MiniMax word subtitle；关闭时不为未使用的字幕支付额外响应开销。",
        )
        self._audio_word_subtitle_check = QCheckBox()
        self._audio_word_subtitle_check.setChecked(self._settings.tts_subtitle_word_level)
        word_subtitle_row.set_input(self._audio_word_subtitle_check)
        layout.addWidget(word_subtitle_row)
        cbr_row = SettingRow(
            "MiniMax 恒定比特率",
            "正式多段 MP3 默认开启，稳定段落时长和无损拼接；仅在排查编码差异时关闭。",
        )
        self._audio_minimax_force_cbr_check = QCheckBox()
        self._audio_minimax_force_cbr_check.setChecked(self._settings.tts_minimax_force_cbr)
        cbr_row.set_input(self._audio_minimax_force_cbr_check)
        layout.addWidget(cbr_row)

        add_setting_group_header(
            layout,
            "阶段覆盖",
            "保留“自动推荐”时按能力、语言、硬件与本机基准分数动态选择。",
        )
        self._audio_plugin_override_combos: dict[AudioExecutionStage, QComboBox] = {}
        registry = registry_from_settings(self._settings)
        current_overrides = self._audio_override_payload(self._settings.audio_plugin_overrides)
        for stage in AudioExecutionStage:
            capability = self._stage_capability(stage)
            if capability is None:
                continue
            candidates = registry.candidates(capability)
            row = SettingRow(
                _STAGE_LABELS[stage],
                "固定后仍会校验能力和语言覆盖；不满足时在计划报告中明确提示。",
            )
            combo = QComboBox()
            combo.setAccessibleName(f"{_STAGE_LABELS[stage]}插件")
            combo.addItem("自动推荐", "")
            for manifest in candidates:
                location = "本地" if manifest.capabilities.offline else "云端"
                combo.addItem(f"{manifest.display_name} · {location}", manifest.plugin_id)
            selected = current_overrides.get(stage.value, "")
            index = combo.findData(selected)
            combo.setCurrentIndex(max(index, 0))
            combo.currentIndexChanged.connect(self._refresh_audio_execution_plan)
            row.set_input(combo)
            layout.addWidget(row)
            self._audio_plugin_override_combos[stage] = combo

        add_setting_group_header(
            layout,
            "独立运行时",
            "模型框架留在各自 sidecar；主程序只使用统一健康、能力、转写与对齐接口。",
        )
        row, self._audio_qwen_asr_url_input = make_line_setting(
            "Qwen3-ASR / ForcedAligner",
            "正式转写与 11 语种已知文本对齐服务。",
            self._settings.audio_qwen3_asr_base_url,
        )
        layout.addWidget(row)
        row, self._audio_qwen_asr_key_input = make_line_setting(
            "Qwen3-ASR Sidecar Token",
            "只绑定本机回环地址时可留空。",
            self._settings.audio_qwen3_asr_api_key,
            secret=True,
        )
        layout.addWidget(row)
        row, self._audio_whisperx_url_input = make_line_setting(
            "WhisperX 服务",
            "精校复核、语言扩展与 Qwen 对齐器的独立验证。",
            self._settings.audio_whisperx_base_url,
        )
        layout.addWidget(row)
        row, self._audio_sherpa_url_input = make_line_setting(
            "Sherpa ONNX 服务",
            "快速试听、CPU、VAD 和轻量多语言模型包。",
            self._settings.audio_sherpa_base_url,
        )
        layout.addWidget(row)
        row, self._audio_mfa_command_input = make_line_setting(
            "MFA 命令",
            "专业词典精校时使用；普通章节不会自动启动。",
            self._settings.audio_mfa_command,
        )
        layout.addWidget(row)
        row, self._audio_manifest_dirs_input = make_line_setting(
            "第三方插件清单目录",
            "逗号分隔；只加载 *.audio-plugin.json，不执行清单目录中的 Python 代码。",
            self._settings.audio_plugin_manifest_dirs,
        )
        layout.addWidget(row)

        report_top = QHBoxLayout()
        self._audio_hardware_badge = Badge("正在评估", tone="warning")
        report_top.addWidget(self._audio_hardware_badge)
        report_top.addStretch()
        refresh_btn = ActionButton("重新推荐", variant="secondary")
        refresh_btn.clicked.connect(self._refresh_audio_execution_plan)
        report_top.addWidget(refresh_btn)
        benchmark_btn = ActionButton("用当前章节校准", variant="primary")
        benchmark_btn.setToolTip(
            "使用当前章节已生成的 10–30 段真实人声，对已配置的 ASR/对齐 sidecar 建立本机分数卡"
        )
        benchmark_btn.clicked.connect(self._run_audio_model_benchmark)
        report_top.addWidget(benchmark_btn)
        self._audio_benchmark_btn = benchmark_btn
        layout.addLayout(report_top)
        self._audio_plan_report = QTextBrowser()
        self._audio_plan_report.setObjectName("audioModelPlanReport")
        self._audio_plan_report.setMinimumHeight(210)
        self._audio_plan_report.setOpenExternalLinks(False)
        layout.addWidget(self._audio_plan_report)

        for widget in (
            self._audio_preset_combo,
            self._audio_location_combo,
            self._audio_accelerator_combo,
            self._local_resource_budget_combo,
            self._audio_memory_combo,
        ):
            widget.currentIndexChanged.connect(self._refresh_audio_execution_plan)
        self._audio_dual_alignment_check.toggled.connect(self._refresh_audio_execution_plan)
        self._refresh_audio_execution_plan()
        return section

    @staticmethod
    def _stage_capability(stage: AudioExecutionStage) -> Any:
        from novel_forge.tts.platform.planner import _STAGE_CAPABILITY

        return _STAGE_CAPABILITY.get(stage)

    @staticmethod
    def _audio_override_payload(raw: str) -> dict[str, str]:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in payload.items()
            if str(key) and str(value)
        }

    def _audio_languages_for_plan(self: Any) -> list[str]:
        languages: list[str] = []
        script = getattr(self, "_current_script", None)
        if script is not None:
            for segment in script.segments:
                languages.extend(run.language for run in segment.language_runs)
                if not segment.language_runs and segment.language_code != "auto":
                    languages.append(segment.language_code)
        return list(dict.fromkeys(languages)) or ["zh"]

    def _audio_preview_settings(self: Any) -> Any:
        overrides = {
            stage.value: str(combo.currentData() or "")
            for stage, combo in self._audio_plugin_override_combos.items()
            if combo.currentData()
        }
        preset = str(self._audio_preset_combo.currentData() or self._audio_preset_combo.currentText())
        provider = self._get_current_provider()
        return self._settings.model_copy(
            update={
                "tts_default_provider": provider,
                "audio_quality_preset": preset,
                "audio_location_policy": str(
                    self._audio_location_combo.currentData()
                    or self._audio_location_combo.currentText()
                ),
                "audio_accelerator_preference": str(
                    self._audio_accelerator_combo.currentData()
                    or self._audio_accelerator_combo.currentText()
                ),
                "local_model_resource_budget": str(
                    self._local_resource_budget_combo.currentData()
                    or self._local_resource_budget_combo.currentText()
                ),
                "local_model_resource_wait_timeout_s": float(
                    self._local_resource_wait_spin.value()
                ),
                "audio_memory_budget": str(
                    self._audio_memory_combo.currentData()
                    or self._audio_memory_combo.currentText()
                ),
                "audio_dual_alignment_validation": self._audio_dual_alignment_check.isChecked(),
                "audio_plugin_overrides": json.dumps(overrides, ensure_ascii=False),
                "audio_plugin_manifest_dirs": self._audio_manifest_dirs_input.text().strip(),
                "audio_qwen3_asr_base_url": self._audio_qwen_asr_url_input.text().strip(),
                "audio_qwen3_asr_api_key": self._audio_qwen_asr_key_input.text().strip(),
                "audio_whisperx_base_url": self._audio_whisperx_url_input.text().strip(),
                "audio_sherpa_base_url": self._audio_sherpa_url_input.text().strip(),
                "audio_mfa_command": self._audio_mfa_command_input.text().strip(),
            }
        )

    def _refresh_audio_execution_plan(self: Any, *_args: object) -> None:
        if not hasattr(self, "_audio_plan_report"):
            return
        try:
            settings = self._audio_preview_settings()
            plan = build_audio_execution_plan(
                settings,
                languages=self._audio_languages_for_plan(),
                scorecard_path=(
                    self._layout.tts_model_scorecards_path
                    if getattr(self, "_layout", None) is not None
                    else None
                ),
            )
        except Exception as exc:
            self._audio_plan_report.setPlainText(f"无法生成模型计划：{exc}")
            self._audio_hardware_badge.setText("计划无效")
            self._audio_hardware_badge.set_tone("danger")
            return
        resource_snapshot = configure_local_model_resources(settings).snapshot()
        memory = f"{plan.hardware.memory_gb:.0f} GB" if plan.hardware.memory_gb else "内存未知"
        self._audio_hardware_badge.setText(
            f"{plan.hardware.device_label} · {memory} · {plan.hardware.memory_class.value}"
        )
        self._audio_hardware_badge.set_tone("success")
        registry = registry_from_settings(settings)
        rows: list[str] = []
        for assignment in plan.routes:
            plugin_id = assignment.primary.plugin_id if assignment.primary else ""
            manifest = registry.get(plugin_id)
            label = manifest.display_name if manifest else plugin_id or "暂无候选"
            fallback_names = [
                registry.require(route.plugin_id).display_name
                for route in assignment.fallbacks
                if registry.get(route.plugin_id) is not None
            ]
            fallback = f"<br><small>回退：{' → '.join(map(html.escape, fallback_names))}</small>" if fallback_names else ""
            lock = " · 已固定" if assignment.overridden else ""
            rows.append(
                "<tr>"
                f"<td><b>{html.escape(_STAGE_LABELS[assignment.stage])}</b></td>"
                f"<td>{html.escape(label)}{html.escape(lock)}{fallback}</td>"
                f"<td>{html.escape(assignment.reason or '等待可用插件')}</td>"
                "</tr>"
            )
        routes = "".join(
            f"<li><b>{html.escape(route.language)}</b>：ASR {html.escape(route.asr_plugin_id or '—')} "
            f"→ 对齐 {html.escape(route.aligner_plugin_id or '片段级降级')}</li>"
            for route in plan.language_routes
        )
        warning_html = "".join(f"<li>{html.escape(item)}</li>" for item in plan.warnings)
        resource_html = (
            "<p><b>全局本机调度</b>："
            f"{html.escape(resource_snapshot.budget)} · "
            f"加速器 {resource_snapshot.accelerator_used}/"
            f"{resource_snapshot.accelerator_capacity} · "
            f"CPU {resource_snapshot.cpu_used}/{resource_snapshot.cpu_capacity} · "
            f"排队 {len(resource_snapshot.waiting)}</p>"
        )
        update_browser_html(
            self._audio_plan_report,
            "<h3 style='margin:0 0 8px 0'>当前推荐计划</h3>"
            + resource_html
            + "<table cellspacing='0' cellpadding='5' width='100%'>"
            "<tr><th align='left'>阶段</th><th align='left'>主插件 / 回退</th>"
            "<th align='left'>选择依据</th></tr>"
            + "".join(rows)
            + "</table>"
            + (f"<h4>按语言路由</h4><ul>{routes}</ul>" if routes else "")
            + (f"<h4>需要注意</h4><ul>{warning_html}</ul>" if warning_html else "")
        )

    def _audio_platform_env_updates(self: Any) -> dict[str, str]:
        overrides = {
            stage.value: str(combo.currentData())
            for stage, combo in self._audio_plugin_override_combos.items()
            if combo.currentData()
        }
        return {
            "NOVEL_FORGE_AUDIO_QUALITY_PRESET": str(
                self._audio_preset_combo.currentData() or self._audio_preset_combo.currentText()
            ),
            "NOVEL_FORGE_AUDIO_LOCATION_POLICY": str(
                self._audio_location_combo.currentData()
                or self._audio_location_combo.currentText()
            ),
            "NOVEL_FORGE_AUDIO_ACCELERATOR_PREFERENCE": str(
                self._audio_accelerator_combo.currentData()
                or self._audio_accelerator_combo.currentText()
            ),
            "NOVEL_FORGE_LOCAL_MODEL_RESOURCE_BUDGET": str(
                self._local_resource_budget_combo.currentData()
                or self._local_resource_budget_combo.currentText()
            ),
            "NOVEL_FORGE_LOCAL_MODEL_RESOURCE_WAIT_TIMEOUT_S": str(
                self._local_resource_wait_spin.value()
            ),
            "NOVEL_FORGE_AUDIO_MEMORY_BUDGET": str(
                self._audio_memory_combo.currentData() or self._audio_memory_combo.currentText()
            ),
            "NOVEL_FORGE_AUDIO_PLUGIN_OVERRIDES": json.dumps(overrides, ensure_ascii=False),
            "NOVEL_FORGE_AUDIO_DUAL_ALIGNMENT_VALIDATION": str(
                self._audio_dual_alignment_check.isChecked()
            ).lower(),
            "NOVEL_FORGE_AUDIO_QWEN3_ASR_BASE_URL": self._audio_qwen_asr_url_input.text().strip(),
            "NOVEL_FORGE_AUDIO_QWEN3_ASR_API_KEY": self._audio_qwen_asr_key_input.text().strip(),
            "NOVEL_FORGE_AUDIO_WHISPERX_BASE_URL": self._audio_whisperx_url_input.text().strip(),
            "NOVEL_FORGE_AUDIO_SHERPA_BASE_URL": self._audio_sherpa_url_input.text().strip(),
            "NOVEL_FORGE_AUDIO_MFA_COMMAND": self._audio_mfa_command_input.text().strip(),
            "NOVEL_FORGE_AUDIO_PLUGIN_MANIFEST_DIRS": self._audio_manifest_dirs_input.text().strip(),
            "NOVEL_FORGE_TTS_ALIGNMENT_REPAIR_ROUNDS": str(
                self._audio_repair_rounds_spin.value()
            ),
            "NOVEL_FORGE_TTS_MAX_TEXT_ERROR_RATE": str(self._audio_text_error_spin.value()),
            "NOVEL_FORGE_TTS_MASTER_QUALITY_GATE_BLOCKING": str(
                self._audio_master_gate_check.isChecked()
            ).lower(),
            "NOVEL_FORGE_TTS_AUDIO_QUALITY_TIER": str(
                self._audio_delivery_tier_combo.currentData()
                or self._audio_delivery_tier_combo.currentText()
            ),
            "NOVEL_FORGE_TTS_SUBTITLE_WORD_LEVEL": str(
                self._audio_word_subtitle_check.isChecked()
            ).lower(),
            "NOVEL_FORGE_TTS_MINIMAX_FORCE_CBR": str(
                self._audio_minimax_force_cbr_check.isChecked()
            ).lower(),
        }

    def _run_audio_model_benchmark(self: Any) -> None:
        if getattr(self, "_layout", None) is None:
            self._status_badge.setText("请先选择项目，再使用真实章节人声校准模型")
            self._status_badge.set_tone("warning")
            return
        chapter_number = int(getattr(self, "_active_chapter_number", 0) or 0)
        if chapter_number <= 0 and hasattr(self, "_audio_chapter_combo"):
            chapter_number = int(self._audio_chapter_combo.currentData() or 0)
        if chapter_number <= 0:
            self._status_badge.setText("请选择已有正式人声的章节")
            self._status_badge.set_tone("warning")
            return
        worker = self._AudioBenchmarkWorker(
            settings=self._audio_preview_settings(),
            layout=self._layout,
            chapter_number=chapter_number,
        )
        worker.signals.audio_benchmark_completed.connect(self._on_audio_benchmark_completed)
        worker.signals.worker_failed.connect(self._on_audio_benchmark_failed)
        self._audio_benchmark_workers.append(worker)
        self._audio_benchmark_btn.setEnabled(False)
        self._status_badge.setText("正在用当前章节真实人声校准 ASR 与对齐插件…")
        self._status_badge.set_tone("warning")
        worker.submit()

    def _on_audio_benchmark_completed(self: Any, payload: dict[str, Any]) -> None:
        self._audio_benchmark_btn.setEnabled(True)
        scorecards = payload.get("scorecards", [])
        failures = payload.get("failures", {})
        self._status_badge.setText(
            f"模型校准完成：{len(scorecards)} 个分数卡，{len(failures)} 个运行时不可用"
        )
        self._status_badge.set_tone("success" if scorecards else "warning")
        self._refresh_audio_execution_plan()

    def _on_audio_benchmark_failed(self: Any, _worker_id: str, payload: object) -> None:
        self._audio_benchmark_btn.setEnabled(True)
        detail = payload.get("message", "基准任务失败") if isinstance(payload, dict) else str(payload)
        self._status_badge.setText(f"模型校准失败：{detail}")
        self._status_badge.set_tone("danger")

    @staticmethod
    def _provider_capability_summary(provider: str) -> str:
        profile = provider_field_profile(provider)
        native = [field.value for field, item in profile.mappings.items() if item.support == FieldSupport.NATIVE]
        portable = [field.value for field, item in profile.mappings.items() if item.support == FieldSupport.PORTABLE]
        director = [
            field.value
            for field, item in profile.mappings.items()
            if item.support == FieldSupport.DIRECTOR_ONLY
        ]
        labels = {
            VocalDirectionField.EMOTION.value: "情绪",
            VocalDirectionField.TONE.value: "语气",
            VocalDirectionField.SPEED.value: "语速",
            VocalDirectionField.VOLUME.value: "音量",
            VocalDirectionField.PITCH.value: "音高",
            VocalDirectionField.STRESS.value: "重音",
            VocalDirectionField.PARALINGUISTIC.value: "副语言",
            VocalDirectionField.PRONUNCIATION.value: "发音",
            VocalDirectionField.LANGUAGE.value: "语言",
            VocalDirectionField.DELIVERY_STYLE.value: "表达风格",
            VocalDirectionField.ENERGY.value: "能量",
            VocalDirectionField.ARTICULATION.value: "吐字",
            VocalDirectionField.BREATHINESS.value: "气声",
            VocalDirectionField.RESONANCE.value: "共鸣",
            VocalDirectionField.TENSION.value: "张力",
            VocalDirectionField.INTIMACY.value: "亲密度",
            VocalDirectionField.SPATIAL_EFFECT.value: "空间效果",
        }

        def render(values: list[str]) -> str:
            return "、".join(labels.get(value, value) for value in values) or "无"

        return (
            f"原生能力：{render(native)}\n"
            f"本地后处理：{render(portable)}\n"
            f"仅保留为导演提示：{render(director)}"
        )


__all__ = ["AudioPlatformSettingsMixin"]
