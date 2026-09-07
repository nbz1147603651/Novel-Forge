"""TTS / 配音 parameters for the desktop settings page."""

from __future__ import annotations

import json
from typing import Any

from novel_forge.desktop.widgets import (
    CollapsibleSection,
    add_setting_group_description,
    add_setting_group_header,
    make_combo_setting,
    make_float_setting,
    make_line_setting,
    make_nested_setting_section,
    make_spin_setting,
)
from novel_forge.tts.platform.config import registry_from_settings
from novel_forge.tts.platform.schemas import AudioCapability, AudioExecutionStage


def _make_settings_subsection(
    parent: CollapsibleSection,
    title: str,
    hint: str = "",
) -> Any:
    section = make_nested_setting_section(title, hint, expanded=False)
    parent.body_layout.addWidget(section)
    return section


_TTS_PROVIDER_CHOICES = [
    "minimax",
    "bailian",
    "dashscope",
    "tencent",
    "volcengine_ark",
    "mimo",
    "local",
    "qwen3",
    "cosyvoice",
    "openvoice",
    "mock",
]

_TTS_PROVIDER_LABELS = [
    "MiniMax（推荐）",
    "百炼 (DashScope)",
    "DashScope",
    "腾讯云",
    "火山方舟",
    "小米 MiMo",
    "本地 OpenAI 兼容",
    "Qwen3-TTS",
    "CosyVoice",
    "OpenVoice",
    "Mock（调试）",
]

_AUTOMATION_MODES = ["manual", "assisted", "autonomous"]
_AUTOMATION_LABELS = ["全人工", "AI 伴随（推荐）", "全 AI 自主"]

_OUTPUT_FORMATS = ["mp3", "wav", "flac", "pcm"]

# MiniMax TTS 模型目录（来自官方文档 2026-07）
_MINIMAX_TTS_MODELS = [
    "speech-2.8-hd",
    "speech-2.8-turbo",
    "speech-2.6-hd",
    "speech-2.6-turbo",
    "speech-02-hd",
    "speech-02-turbo",
    "speech-01-hd",
    "speech-01-turbo",
]
_MINIMAX_TTS_MODEL_LABELS = [
    "Speech 2.8 HD — 情绪渲染融合语气词",
    "Speech 2.8 Turbo — 极致生成速度",
    "Speech 2.6 HD — 超低延时、归一化升级",
    "Speech 2.6 Turbo — 极速版、语音聊天/数字人",
    "Speech 02 HD — 出色韵律、稳定性、复刻相似度",
    "Speech 02 Turbo — 小语种加强、性能出色",
    "Speech 01 HD — 高音质（兼容）",
    "Speech 01 Turbo — 快速生成（兼容）",
]

_DASHSCOPE_SYNTHESIS_MODELS = [
    "qwen-audio-3.0-tts-plus",
    "qwen-audio-3.0-tts-flash",
    "cosyvoice-v3.5-plus",
    "cosyvoice-v3.5-flash",
    "qwen3-tts-flash",
    "qwen3-tts-instruct-flash",
    "qwen3-tts-vc-2026-01-22",
    "qwen3-tts-vd-2026-01-26",
]
_DASHSCOPE_SYNTHESIS_LABELS = [
    "Qwen Audio 3.0 Plus — 正式多角色／指令／复刻（推荐）",
    "Qwen Audio 3.0 Flash — 快速试听／复刻",
    "CosyVoice 3.5 Plus — 设计音色／复刻／指令",
    "CosyVoice 3.5 Flash — 快速设计音色／复刻",
    "Qwen3 TTS Flash — 多语种系统音色",
    "Qwen3 TTS Instruct Flash — 旁白表演指令",
    "Qwen3 TTS VC 2026-01-22 — Qwen3 复刻音色专用",
    "Qwen3 TTS VD 2026-01-26 — Qwen3 设计音色专用",
]
_DASHSCOPE_CLONE_MODELS = [
    "qwen-audio-3.0-tts-plus",
    "qwen-audio-3.0-tts-flash",
    "cosyvoice-v3.5-plus",
    "cosyvoice-v3.5-flash",
    "qwen3-tts-vc-2026-01-22",
]
_DASHSCOPE_DESIGN_MODELS = [
    "cosyvoice-v3.5-plus",
    "cosyvoice-v3.5-flash",
    "qwen3-tts-vd-2026-01-26",
]
_LOCAL_AUDIO_ROUTE_CAPABILITIES = {
    AudioExecutionStage.ASR: ("转写核验", AudioCapability.ASR),
    AudioExecutionStage.ALIGN: ("强制对齐／真实时间线", AudioCapability.FORCED_ALIGNMENT),
    AudioExecutionStage.ALIGNMENT_VALIDATOR: (
        "独立对齐复核",
        AudioCapability.FORCED_ALIGNMENT,
    ),
    AudioExecutionStage.VAD: ("停顿与语音活动检测", AudioCapability.VAD),
    AudioExecutionStage.SFX: ("短音效生成", AudioCapability.SFX_GENERATION),
    AudioExecutionStage.MUSIC: ("背景音乐生成", AudioCapability.MUSIC_GENERATION),
    AudioExecutionStage.SOUNDSCAPE: ("长环境声生成", AudioCapability.SOUNDSCAPE_GENERATION),
    AudioExecutionStage.RENDERER: ("多轨混音与渲染", AudioCapability.AUDIO_RENDER),
    AudioExecutionStage.QUALITY: ("成片质检", AudioCapability.QUALITY_EVALUATION),
}


def _add_bailian_and_audio_routes(
    sec: CollapsibleSection,
    s: Any,
    widgets: dict[str, Any],
) -> None:
    """Expose Bailian model-bound routes and local audio post-production routes."""

    layout = _make_settings_subsection(
        sec,
        "阿里百炼平台与模型路由",
        ("正式、试听、复刻、设计四条路由分开管理。自定义音色始终优先使用创建时绑定的模型。"),
    ).body_layout

    row, widgets["_tts_dashscope_api_key"] = make_line_setting(
        "百炼 API Key",
        "必须与 Workspace 专属域名位于同一地域；保存时写入 .env。",
        str(getattr(s, "tts_dashscope_api_key", "")),
        secret=True,
    )
    layout.addWidget(row)
    row, widgets["_tts_dashscope_base_url"] = make_line_setting(
        "Workspace API 地址",
        "生产环境建议使用 Workspace 专属 /api/v1 地址。",
        str(getattr(s, "tts_dashscope_base_url", "https://dashscope.aliyuncs.com/api/v1")),
    )
    layout.addWidget(row)
    row, widgets["_tts_dashscope_formal_model"] = make_combo_setting(
        "正式人声路由",
        "用于整章合成；多角色小说默认推荐 Qwen Audio 3.0 Plus。",
        _DASHSCOPE_SYNTHESIS_MODELS,
        current=str(getattr(s, "tts_dashscope_model", "qwen-audio-3.0-tts-plus")),
        item_labels=_DASHSCOPE_SYNTHESIS_LABELS,
    )
    layout.addWidget(row)
    row, widgets["_tts_dashscope_preview_model"] = make_combo_setting(
        "人声试听路由",
        "随正式模型最安全；已绑定音色不会跨模型试听。",
        ["", *_DASHSCOPE_SYNTHESIS_MODELS],
        current=str(getattr(s, "tts_dashscope_preview_model", "")),
        item_labels=["随正式模型／音色绑定（推荐）", *_DASHSCOPE_SYNTHESIS_LABELS],
    )
    layout.addWidget(row)
    row, widgets["_tts_dashscope_clone_model"] = make_combo_setting(
        "声音复刻路由",
        "Qwen3 VC 可读取本地 WAV/MP3/M4A；其他百炼模型需公网音频 URL。",
        _DASHSCOPE_CLONE_MODELS,
        current=str(getattr(s, "tts_dashscope_voice_clone_model", "qwen-audio-3.0-tts-plus")),
    )
    layout.addWidget(row)
    row, widgets["_tts_dashscope_design_model"] = make_combo_setting(
        "声音设计路由",
        "从自然语言角色描述创建并试听专属音色。",
        _DASHSCOPE_DESIGN_MODELS,
        current=str(getattr(s, "tts_dashscope_voice_design_model", "cosyvoice-v3.5-plus")),
    )
    layout.addWidget(row)
    row, widgets["_tts_dashscope_optimize_instructions"] = make_combo_setting(
        "Qwen3 Instruct 指令优化",
        "成品建议开启；严格 A/B 测试时可关闭。",
        ["true", "false"],
        current=str(getattr(s, "tts_dashscope_optimize_instructions", True)).lower(),
        item_labels=["开启（推荐）", "关闭"],
    )
    layout.addWidget(row)
    row, widgets["_tts_dashscope_clone_preprocess"] = make_combo_setting(
        "复刻参考音频预处理",
        "仅含明显噪声时开启；安静录音关闭可更好保留声纹。",
        ["false", "true"],
        current=str(getattr(s, "tts_dashscope_voice_clone_enable_preprocess", False)).lower(),
        item_labels=["关闭（安静录音推荐）", "开启降噪与归一"],
    )
    layout.addWidget(row)
    row, widgets["_tts_dashscope_cny_per_usd"] = make_float_setting(
        "预算折算汇率",
        "将百炼人民币字符计费折算为项目 USD 预算。",
        float(getattr(s, "tts_dashscope_cny_per_usd", 7.2)),
        1.0,
        20.0,
    )
    layout.addWidget(row)

    layout = _make_settings_subsection(
        sec,
        "云端人声与本地后处理路由",
        "百炼负责人声，本地 ASR、对齐、VAD、混音和质检形成闭环。",
    ).body_layout
    row, widgets["_audio_quality_preset"] = make_combo_setting(
        "音频质量目标",
        "质量目标与执行位置独立。",
        ["quick_preview", "production", "master", "low_resource", "custom"],
        current=str(getattr(s, "audio_quality_preset", "production")),
        item_labels=["快速试听", "正式成片（推荐）", "极致精校", "低配置离线", "自定义"],
    )
    layout.addWidget(row)
    row, widgets["_audio_location_policy"] = make_combo_setting(
        "执行位置策略",
        "混合生产让百炼专注人声，本地负责对齐、混音与质检。",
        ["cloud_only", "local_only", "prefer_cloud", "prefer_local", "hybrid"],
        current=str(getattr(s, "audio_location_policy", "hybrid")),
        item_labels=["全部云端", "全部本地", "优先云端", "优先本地", "混合生产（推荐）"],
    )
    layout.addWidget(row)

    try:
        raw_overrides = json.loads(str(getattr(s, "audio_plugin_overrides", "{}") or "{}"))
    except (TypeError, ValueError):
        raw_overrides = {}
    current_overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
    registry = registry_from_settings(s)
    route_combos: dict[AudioExecutionStage, Any] = {}
    for stage, (label, capability) in _LOCAL_AUDIO_ROUTE_CAPABILITIES.items():
        candidates = registry.candidates(capability)
        values = ["", *(item.plugin_id for item in candidates)]
        labels = [
            "自动推荐",
            *(
                f"{item.display_name} · {'本地' if item.capabilities.offline else '云端'}"
                for item in candidates
            ),
        ]
        row, combo = make_combo_setting(
            label,
            "固定后仍会检查语言、能力、硬件与位置策略。",
            values,
            current=str(current_overrides.get(stage.value, "")),
            item_labels=labels,
        )
        layout.addWidget(row)
        route_combos[stage] = combo
    widgets["_audio_plugin_route_combos"] = route_combos


def _build_tts_params(s: Any) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Build TTS / 配音 routing and parameter controls."""

    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("配音 — TTS 路由与参数", expanded=False)

    add_setting_group_description(
        sec.body_layout,
        "控制配音模块的默认 TTS 平台、推进模式、并发与速率、音色管理以及预算。"
        "百炼模型绑定路由和本地后处理插件路由均在本区高级分组中设置。",
    )

    # ── 基本路由 ──────────────────────────────────────────
    add_setting_group_header(
        sec.body_layout,
        "基本路由",
        "选择默认 TTS 平台与模型；角色级覆盖在配音工作室中设置。",
    )

    row, widgets["_tts_enabled"] = make_combo_setting(
        "启用配音模块",
        "关闭后章节生成不会触发 TTS 流程",
        ["true", "false"],
        current=str(getattr(s, "tts_enabled", False)).lower(),
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_subtitle_word_level"] = make_combo_setting(
        "字词级字幕",
        "开启后导出字词级 SRT；MiniMax 会请求其原生 word subtitle，其他平台仍使用 ASR 对齐。",
        ["false", "true"],
        current=str(getattr(s, "tts_subtitle_word_level", False)).lower(),
        item_labels=["片段级（默认）", "字词级"],
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_default_provider"] = make_combo_setting(
        "默认 TTS 平台",
        "人声合成的默认供应商；配音工作室中可逐项目切换",
        _TTS_PROVIDER_CHOICES,
        current=str(getattr(s, "tts_default_provider", "minimax")),
        item_labels=_TTS_PROVIDER_LABELS,
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_default_model"] = make_combo_setting(
        "默认 TTS 模型",
        "MiniMax 平台推荐 speech-2.8-hd；其他平台可手动输入模型 ID",
        _MINIMAX_TTS_MODELS,
        current=str(getattr(s, "tts_default_model", "speech-2.8-hd")),
        item_labels=_MINIMAX_TTS_MODEL_LABELS,
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_default_speed"] = make_float_setting(
        "默认语速",
        "项目级语速倍率；角色与片段参数在此基础上叠加",
        float(getattr(s, "tts_default_speed", 1.0)),
        0.5,
        2.0,
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_automation_mode"] = make_combo_setting(
        "推进模式",
        "manual=全人工、assisted=AI 伴随、autonomous=全 AI 自主",
        _AUTOMATION_MODES,
        current=str(getattr(s, "tts_automation_mode", "assisted")),
        item_labels=_AUTOMATION_LABELS,
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_audio_quality_tier"] = make_combo_setting(
        "音频质量档位",
        "audition=单遍母带+跳过跨章校准（最快）；standard=两遍响度母带；commercial=两遍母带+强制跨章一致性",
        ["audition", "standard", "commercial"],
        current=str(getattr(s, "tts_audio_quality_tier", "standard")),
        item_labels=["试听档（audition）", "标准档（standard）", "商业档（commercial）"],
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_auto_trigger"] = make_combo_setting(
        "章节完成后自动触发",
        "章节生成完成后自动开始配音流程",
        ["true", "false"],
        current=str(getattr(s, "tts_auto_trigger_after_chapter", False)).lower(),
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_voice_library_scope"] = make_combo_setting(
        "音色库匹配范围",
        "全局音色库复用策略：仅本项目 / 跨项目同名复用 / 全部",
        ["project_only", "global_with_names", "global_all"],
        current=str(getattr(s, "tts_voice_library_scope", "project_only") or "project_only"),
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_post_archive_retry_enabled"] = make_combo_setting(
        "失败后延迟重试",
        "自动配音失败后 60s 自动重试一次（仅针对待确认/过期）",
        ["true", "false"],
        current=str(getattr(s, "tts_post_archive_retry_enabled", True)).lower(),
    )
    sec.body_layout.addWidget(row)

    row, widgets["_tts_post_archive_retry_delay_s"] = make_spin_setting(
        "重试延迟秒数",
        "自动配音失败后的延迟重试等待秒数",
        int(getattr(s, "tts_post_archive_retry_delay_s", 60)),
        10,
        600,
        step=10,
    )
    sec.body_layout.addWidget(row)

    # ── 并发与速率 ──────────────────────────────────────────
    layout = _make_settings_subsection(
        sec,
        "并发与速率",
        "控制远程 TTS 合成的并发数、速率限制与重试策略。",
    ).body_layout

    row, widgets["_tts_max_concurrent"] = make_spin_setting(
        "最大并发合成数",
        "同时进行的 TTS 合成请求数（推荐 2-6）",
        int(getattr(s, "tts_max_concurrent_synthesis", 4)),
        1,
        20,
    )
    layout.addWidget(row)

    row, widgets["_tts_rate_limit"] = make_spin_setting(
        "每分钟请求上限",
        "远程 TTS 的最大请求速率；所有重试也计入",
        int(getattr(s, "tts_synthesis_requests_per_minute", 45)),
        1,
        600,
    )
    layout.addWidget(row)

    row, widgets["_tts_rate_cooldown"] = make_spin_setting(
        "限流冷却",
        "触发限流后整条合成队列暂停的秒数",
        int(getattr(s, "tts_synthesis_rate_limit_cooldown_s", 60)),
        5,
        600,
    )
    layout.addWidget(row)

    row, widgets["_tts_retry_limit"] = make_spin_setting(
        "单段重试次数",
        "单段合成失败后的最大重试次数",
        int(getattr(s, "tts_synthesis_retry_limit", 3)),
        0,
        10,
    )
    layout.addWidget(row)

    row, widgets["_tts_background_concurrency"] = make_spin_setting(
        "后台流水线并发",
        "后台整章配音的并发数；默认 1 让多章排队",
        int(getattr(s, "tts_background_pipeline_concurrency", 1)),
        1,
        3,
    )
    layout.addWidget(row)

    row, widgets["_tts_output_format"] = make_combo_setting(
        "输出格式",
        "默认音频输出格式",
        _OUTPUT_FORMATS,
        current=str(getattr(s, "tts_output_format", "mp3")),
    )
    layout.addWidget(row)

    # ── 音色管理 ──────────────────────────────────────────
    layout = _make_settings_subsection(
        sec,
        "音色管理",
        "控制音色设计、语义匹配、LLM 裁决与克隆策略。",
    ).body_layout

    row, widgets["_tts_voice_design"] = make_combo_setting(
        "音色设计",
        "最后兜底手段（按次计费）：仅当音色库与系统目录均无合格音色时才调用",
        ["true", "false"],
        current=str(getattr(s, "tts_voice_design_enabled", False)).lower(),
    )
    layout.addWidget(row)

    row, widgets["_tts_voice_semantic"] = make_combo_setting(
        "语义匹配",
        "对全局音色库启用嵌入向量召回",
        ["true", "false"],
        current=str(getattr(s, "tts_voice_semantic_matching_enabled", True)).lower(),
    )
    layout.addWidget(row)

    row, widgets["_tts_voice_llm_adjudication"] = make_combo_setting(
        "LLM 音色裁决",
        "仅对低置信度匹配启用 LLM 受约束裁决",
        ["true", "false"],
        current=str(getattr(s, "tts_voice_llm_adjudication_enabled", False)).lower(),
    )
    layout.addWidget(row)

    row, widgets["_tts_parallel_clone"] = make_combo_setting(
        "并行音色克隆",
        "Init 阶段并行执行音色克隆",
        ["true", "false"],
        current=str(getattr(s, "tts_parallel_voice_clone", True)).lower(),
    )
    layout.addWidget(row)

    row, widgets["_tts_clone_ttl"] = make_spin_setting(
        "克隆有效期（天）",
        "克隆音色的有效期；过期后需重新克隆",
        int(getattr(s, "tts_voice_clone_ttl_days", 7)),
        1,
        30,
    )
    layout.addWidget(row)

    # ── 脚本审校 ──────────────────────────────────────────
    layout = _make_settings_subsection(
        sec,
        "脚本审校与声音设计",
        "控制配音脚本 LLM 审校和声音设计提取。",
    ).body_layout

    row, widgets["_tts_script_review"] = make_combo_setting(
        "LLM 脚本审校",
        "在确定性检查后运行配音专用 LLM 审校",
        ["true", "false"],
        current=str(getattr(s, "tts_script_llm_review_enabled", True)).lower(),
    )
    layout.addWidget(row)

    row, widgets["_tts_sound_design"] = make_combo_setting(
        "声音设计提取",
        "表演脚本定稿后提取音效、BGM 与环境声需求",
        ["true", "false"],
        current=str(getattr(s, "tts_sound_design_enabled", True)).lower(),
    )
    layout.addWidget(row)

    row, widgets["_tts_sound_gen_enabled"] = make_combo_setting(
        "生成式声音资产",
        "启用 BGM、环境声和短音效的生成能力",
        ["true", "false"],
        current=str(getattr(s, "sound_generation_enabled", False)).lower(),
    )
    layout.addWidget(row)

    # ── LLM 温度与采样参数 ──
    row, widgets["_tts_script_generation_temperature"] = make_float_setting(
        "脚本生成 Temperature",
        "配音脚本生成的 LLM 温度；越低越稳定，越高越有创造性（默认 0.2）",
        float(getattr(s, "tts_script_generation_temperature", 0.2)),
        0.0,
        1.0,
    )
    layout.addWidget(row)

    row, widgets["_tts_script_generation_top_p"] = make_float_setting(
        "脚本生成 Top P",
        "配音脚本生成的 nucleus sampling 阈值（默认 0.95）",
        float(getattr(s, "tts_script_generation_top_p", 0.95)),
        0.0,
        1.0,
    )
    layout.addWidget(row)

    row, widgets["_tts_review_adjudication_temperature"] = make_float_setting(
        "审校/裁决 Temperature",
        "脚本审校与角色裁决的 LLM 温度；建议低温确保判断稳定（默认 0.0）",
        float(getattr(s, "tts_review_adjudication_temperature", 0.0)),
        0.0,
        1.0,
    )
    layout.addWidget(row)

    row, widgets["_tts_narrator_profile_temperature"] = make_float_setting(
        "旁白画像 Temperature",
        "旁白声音画像生成的 LLM 温度（默认 0.2）",
        float(getattr(s, "tts_narrator_profile_temperature", 0.2)),
        0.0,
        1.0,
    )
    layout.addWidget(row)

    row, widgets["_tts_sound_design_temperature"] = make_float_setting(
        "声音设计 Temperature",
        "声音设计提取的 LLM 温度；适度提高增加创意多样性（默认 0.5）",
        float(getattr(s, "tts_sound_design_temperature", 0.5)),
        0.0,
        1.0,
    )
    layout.addWidget(row)

    row, widgets["_tts_sound_design_top_p"] = make_float_setting(
        "声音设计 Top P",
        "声音设计提取的 nucleus sampling 阈值（默认 0.95）",
        float(getattr(s, "tts_sound_design_top_p", 0.95)),
        0.0,
        1.0,
    )
    layout.addWidget(row)

    # ── 预算 ──────────────────────────────────────────
    layout = _make_settings_subsection(
        sec,
        "预算与容量",
        "控制 TTS 费用预算与项目存储容量上限。",
    ).body_layout

    row, widgets["_tts_monthly_budget"] = make_float_setting(
        "月度预算（USD）",
        "0 表示不限制；跨项目共享预算池，每月 1 号重置",
        float(getattr(s, "tts_monthly_cost_budget_usd", 0.0)),
        0.0,
        10000.0,
    )
    layout.addWidget(row)

    row, widgets["_tts_book_budget"] = make_float_setting(
        "单书预算（USD）",
        "0 表示不限制；按 voice_team_hash 维度累计",
        float(getattr(s, "tts_book_cost_budget_usd", 0.0)),
        0.0,
        10000.0,
    )
    layout.addWidget(row)

    row, widgets["_tts_chapter_budget"] = make_float_setting(
        "单章云端预算（USD）",
        "0 表示不设硬上限但仍记录实际费用",
        float(getattr(s, "audio_budget_limit_usd", 0.0)),
        0.0,
        1000.0,
    )
    layout.addWidget(row)

    row, widgets["_tts_project_max_storage"] = make_spin_setting(
        "项目存储上限（MB）",
        "单个项目 tts/ 目录的总容量上限",
        int(getattr(s, "tts_project_max_storage_mb", 1024)),
        64,
        20480,
    )
    layout.addWidget(row)

    # ── MiniMax 配置 ──────────────────────────────────────────
    _add_bailian_and_audio_routes(sec, s, widgets)

    layout = _make_settings_subsection(
        sec,
        "MiniMax 平台",
        "MiniMax TTS v2 的 API 地址、密钥与输出参数。",
    ).body_layout

    row, widgets["_tts_minimax_api_key"] = make_line_setting(
        "MiniMax API Key",
        "可与 LLM Key 不同；为空时回退使用 LLM 的 MiniMax Key",
        str(getattr(s, "tts_minimax_api_key", "")),
        secret=True,
    )
    layout.addWidget(row)

    row, widgets["_tts_minimax_base_url"] = make_line_setting(
        "MiniMax API 地址",
        "默认使用官方全球端点；可按区域改为兼容端点",
        str(getattr(s, "tts_minimax_base_url", "https://api.minimax.io/v1")),
    )
    layout.addWidget(row)

    row, widgets["_tts_minimax_bitrate"] = make_combo_setting(
        "MP3 比特率",
        "输出 MP3 的比特率",
        ["32000", "64000", "128000", "256000"],
        current=str(getattr(s, "tts_minimax_bitrate", 128000)),
        item_labels=["32 kbps", "64 kbps", "128 kbps（推荐）", "256 kbps"],
    )
    layout.addWidget(row)

    row, widgets["_tts_minimax_force_cbr"] = make_combo_setting(
        "恒定比特率",
        "正式多段 MP3 默认开启，稳定段落时长和无损拼接；仅在排查编码差异时关闭。",
        ["true", "false"],
        current=str(getattr(s, "tts_minimax_force_cbr", True)).lower(),
        item_labels=["开启（推荐）", "关闭"],
    )
    layout.addWidget(row)

    return sec, widgets
