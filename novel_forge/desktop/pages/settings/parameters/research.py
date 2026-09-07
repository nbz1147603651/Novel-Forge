"""Settings controls for initialization web research."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QWidget

from novel_forge.app_service.chapter_runtime_policy import (
    CHAPTER_RUNTIME_POLICY_PRESETS,
    project_chapter_runtime_policy,
)
from novel_forge.desktop.widgets import (
    ActionButton,
    CollapsibleSection,
    add_setting_group_description,
    add_setting_group_header,
    make_combo_setting,
    make_float_setting,
    make_line_setting,
    make_nested_setting_section,
    make_spin_setting,
)

_RESEARCH_PRESETS: dict[str, dict[str, str]] = {
    "minimax_cn": {
        "provider": "mcp_search",
        "api_key_env": "MINIMAX_API_KEY",
        "mcp_command": "uvx",
        "mcp_args_json": '["minimax-coding-plan-mcp"]',
        "mcp_env_json": '{"MINIMAX_API_HOST":"https://api.minimax.chat"}',
        "mcp_tool_name": "web_search",
        "mcp_query_argument": "query",
        "mcp_tool_arguments_json": "",
        "mcp_stdio_framing": "newline",
        "mcp_protocol_version": "2024-11-05",
        "http_endpoint": "",
    },
    "minimax_global": {
        "provider": "mcp_search",
        "api_key_env": "MINIMAX_API_KEY",
        "mcp_command": "uvx",
        "mcp_args_json": '["minimax-coding-plan-mcp"]',
        "mcp_env_json": '{"MINIMAX_API_HOST":"https://api.minimax.io"}',
        "mcp_tool_name": "web_search",
        "mcp_query_argument": "query",
        "mcp_tool_arguments_json": "",
        "mcp_stdio_framing": "newline",
        "mcp_protocol_version": "2024-11-05",
        "http_endpoint": "",
    },
    "searxng_local": {
        "provider": "searxng",
        "http_endpoint": "http://127.0.0.1:8080/search",
        "api_key_env": "",
        "mcp_command": "",
        "mcp_args_json": "",
        "mcp_env_json": "",
        "mcp_tool_name": "",
        "mcp_query_argument": "query",
        "mcp_tool_arguments_json": "",
        "mcp_stdio_framing": "newline",
        "mcp_protocol_version": "2024-11-05",
    },
    "mcp_local": {
        "provider": "mcp_search",
        "api_key_env": "",
        "mcp_command": "npx",
        "mcp_args_json": '["-y","your-local-search-mcp"]',
        "mcp_env_json": "{}",
        "mcp_tool_name": "web_search",
        "mcp_query_argument": "query",
        "mcp_tool_arguments_json": "",
        "mcp_stdio_framing": "newline",
        "mcp_protocol_version": "2024-11-05",
        "http_endpoint": "",
    },
}

_RESEARCH_PRESET_LABELS = [
    ("", "选择预设..."),
    ("minimax_cn", "MiniMax 中国区 MCP"),
    ("minimax_global", "MiniMax 国际区 MCP"),
    ("searxng_local", "SearXNG 本地服务"),
    ("mcp_local", "本地 MCP 搜索模板"),
]

_RESEARCH_PROVIDER_VALUES = [
    "auto",
    "noop",
    "tavily",
    "brave",
    "searxng",
    "http_json",
    "bailian_web_search",
    "mcp_search",
]

_RESEARCH_PROVIDER_LABELS = [
    "自动",
    "不检索",
    "Tavily",
    "Brave Search",
    "SearXNG",
    "HTTP JSON",
    "百炼 Web Search",
    "MCP Search",
]


def _build_research_params(s: Any) -> tuple[CollapsibleSection, dict[str, Any]]:
    """Build the search-provider routing and retrieval parameter section."""
    widgets: dict[str, Any] = {}
    sec = CollapsibleSection("资料检索 — 路由与检索参数", expanded=False)

    add_setting_group_description(
        sec.body_layout,
        "该配置控制长篇立项资料检索，并在项目显式开启 research 时为长短篇章节提供有界事实与低频灵感。"
        "创作模型仍走上方任务路由。",
    )
    add_setting_group_description(
        sec.body_layout,
        "是否真正联网由下方启用开关、provider、endpoint/API Key 共同决定；"
        "未启用或后端不可用时会生成 skipped 报告并继续流程。",
    )

    policy = project_chapter_runtime_policy(s)
    policy_section: Any = make_nested_setting_section(
        "章节质量与研究策略",
        "预设只在保存后作用于新任务；联网仍要求项目或短篇请求显式开启 research。",
        expanded=True,
    )
    sec.body_layout.addWidget(policy_section)
    policy_layout = policy_section.body_layout
    preset_ids = [*CHAPTER_RUNTIME_POLICY_PRESETS, "custom"]
    preset_labels = [
        str(CHAPTER_RUNTIME_POLICY_PRESETS[item]["label"])
        for item in CHAPTER_RUNTIME_POLICY_PRESETS
    ] + ["自定义"]
    row, preset_combo = make_combo_setting(
        "章节策略预设",
        "兼容当前不增加调用；稳健仅补必须事实；均衡/增强允许受控灵感刷新。",
        preset_ids,
        current=str(policy["preset"]),
        item_labels=preset_labels,
    )
    widgets["_chapter_runtime_policy_preset"] = preset_combo
    policy_layout.addWidget(row)
    row, intent_combo = make_combo_setting(
        "意图门禁",
        "block 会拒绝改变人物、关系、POV、结局和 locked 要素的自动修复。",
        ["off", "warn", "block"],
        current=str(policy["intent_guard_mode"]),
        item_labels=["关闭", "记录警告", "阻断冲突"],
    )
    widgets["_chapter_intent_guard_mode"] = intent_combo
    policy_layout.addWidget(row)
    fact_refresh = QCheckBox("章节存在 must 事实缺口时允许一次事实刷新")
    fact_refresh.setChecked(bool(policy["fact_refresh_enabled"]))
    widgets["_chapter_research_refresh_enabled"] = fact_refresh
    policy_layout.addWidget(fact_refresh)
    inspiration = QCheckBox("允许低频外部灵感（只投影抽象机制，不进入 StoryKernel）")
    inspiration.setChecked(bool(policy["inspiration_enabled"]))
    widgets["_chapter_research_inspiration_enabled"] = inspiration
    policy_layout.addWidget(inspiration)
    row, cooldown_spin = make_spin_setting(
        "灵感冷却章数",
        "相同方向的外部灵感刷新至少间隔 1–20 章。",
        int(policy["inspiration_cooldown"]),
        1,
        20,
    )
    widgets["_chapter_research_inspiration_cooldown"] = cooldown_spin
    policy_layout.addWidget(row)
    adaptive_short = QCheckBox("短篇按问题自适应修订 0–2 轮")
    adaptive_short.setChecked(bool(policy["short_adaptive_revision_enabled"]))
    widgets["_short_adaptive_revision_enabled"] = adaptive_short
    policy_layout.addWidget(adaptive_short)
    final_verify = QCheckBox("长篇所有语义改文收敛后执行单次最终验证")
    final_verify.setChecked(bool(policy["long_single_final_verify_enabled"]))
    widgets["_long_single_final_verify_enabled"] = final_verify
    policy_layout.addWidget(final_verify)

    applying_policy = {"active": False}

    def apply_policy_preset() -> None:
        preset_id = str(preset_combo.currentData() or "custom")
        definition = CHAPTER_RUNTIME_POLICY_PRESETS.get(preset_id)
        if definition is None:
            return
        applying_policy["active"] = True
        intent_combo.setCurrentIndex(intent_combo.findData(definition["intent_guard_mode"]))
        fact_refresh.setChecked(bool(definition["fact_refresh_enabled"]))
        inspiration.setChecked(bool(definition["inspiration_enabled"]))
        cooldown_spin.setValue(int(definition["inspiration_cooldown"]))
        adaptive_short.setChecked(bool(definition["short_adaptive_revision_enabled"]))
        final_verify.setChecked(bool(definition["long_single_final_verify_enabled"]))
        applying_policy["active"] = False

    def mark_policy_custom() -> None:
        if applying_policy["active"]:
            return
        custom_index = preset_combo.findData("custom")
        if custom_index >= 0:
            preset_combo.setCurrentIndex(custom_index)

    preset_combo.currentIndexChanged.connect(apply_policy_preset)
    intent_combo.currentIndexChanged.connect(mark_policy_custom)
    fact_refresh.toggled.connect(mark_policy_custom)
    inspiration.toggled.connect(mark_policy_custom)
    cooldown_spin.valueChanged.connect(mark_policy_custom)
    adaptive_short.toggled.connect(mark_policy_custom)
    final_verify.toggled.connect(mark_policy_custom)
    add_setting_group_description(
        sec.body_layout,
        "报告中的 queries 只是查询计划；只有 sources 非空且 status=succeeded/partial 时，才代表已从外部搜索拿到来源。",
    )

    routing_section: Any = make_nested_setting_section(
        "检索路由",
        "Provider 作为独立检索组件管理；可在 Tavily/Brave/SearXNG/HTTP JSON/百炼/MCP 之间切换。"
        "未配置 endpoint/API key 时会跳过并继续立项。",
        expanded=True,
    )
    sec.body_layout.addWidget(routing_section)
    routing_layout = routing_section.body_layout

    enabled_cb = QCheckBox("默认启用长篇立项资料检索")
    enabled_cb.setToolTip(
        "启用后，新建立项表单默认会勾选联网资料检索；单次立项仍可在表单中临时关闭。"
    )
    enabled_cb.setChecked(bool(getattr(s, "research_enabled", False)))
    widgets["_research_enabled"] = enabled_cb
    routing_layout.addWidget(enabled_cb)

    row, widgets["_research_default_provider"] = make_combo_setting(
        "默认检索后端",
        "长篇立项默认使用；noop=显式不联网，auto=按这里和环境变量解析",
        _RESEARCH_PROVIDER_VALUES,
        current=str(getattr(s, "research_default_provider", "auto") or "auto"),
        item_labels=_RESEARCH_PROVIDER_LABELS,
    )
    routing_layout.addWidget(row)
    row, widgets["_research_http_endpoint"] = make_line_setting(
        "HTTP Endpoint",
        "SearXNG/http_json/百炼 需要填写；Tavily/Brave 留空时使用内置官方 endpoint",
        str(getattr(s, "research_http_endpoint", "") or ""),
    )
    routing_layout.addWidget(row)
    row, widgets["_research_api_key"] = make_line_setting(
        "API Key",
        "Tavily/Brave/HTTP/MCP 共用密钥；MiniMax 预设也填这里，再按下方 Key Env 注入",
        str(getattr(s, "research_api_key", "") or ""),
        secret=True,
    )
    routing_layout.addWidget(row)

    preset_row = make_nested_setting_section(
        "常用预设",
        "选择模板后应用到当前表单；API Key 仍填上方通用字段，保存前可先测试连接。",
        expanded=True,
    )
    sec.body_layout.addWidget(preset_row)
    preset_layout = preset_row.body_layout
    row, widgets["_research_preset_combo"] = make_combo_setting(
        "预设方案",
        "常用搜索模块模板；自定义 MCP 可从本地模板开始改。",
        [value for value, _label in _RESEARCH_PRESET_LABELS],
        current="",
        item_labels=[label for _value, label in _RESEARCH_PRESET_LABELS],
    )
    preset_layout.addWidget(row)

    action_container = QWidget()
    action_layout = QHBoxLayout(action_container)
    action_layout.setContentsMargins(0, 0, 0, 0)
    action_layout.setSpacing(8)
    apply_btn = ActionButton("应用预设", variant="secondary")
    apply_btn.setProperty("density", "compact")
    widgets["_research_apply_preset_btn"] = apply_btn
    action_layout.addWidget(apply_btn)
    test_btn = ActionButton("测试联网", variant="primary")
    test_btn.setProperty("density", "compact")
    widgets["_research_test_btn"] = test_btn
    action_layout.addWidget(test_btn)
    action_layout.addStretch(1)
    preset_layout.addWidget(action_container)

    status_label = QLabel("测试状态：尚未测试")
    status_label.setObjectName("panelDescription")
    status_label.setWordWrap(True)
    status_label.setToolTip("点击“测试联网”后显示当前配置的连通结果。")
    widgets["_research_test_status"] = status_label
    preset_layout.addWidget(status_label)

    add_setting_group_description(
        routing_layout,
        "HTTP 类 provider：Tavily/Brave 可留空 endpoint 使用内置地址；SearXNG/HTTP JSON/百炼需填 Endpoint。"
        "MCP 类 provider：选择 MCP Search 后使用下方 MCP 配置启动任意兼容搜索 server。",
    )

    mcp_section: Any = make_nested_setting_section(
        "MCP 检索后端",
        "当 Provider 选择 MCP Search 时生效；通过 stdio 启动任意 MCP Server，并调用可配置的搜索工具。",
        expanded=False,
    )
    widgets["_research_mcp_section"] = mcp_section
    sec.body_layout.addWidget(mcp_section)
    mcp_layout = mcp_section.body_layout

    row, widgets["_research_mcp_command"] = make_line_setting(
        "MCP Command",
        "启动 MCP Server 的命令，例如 uvx 或 npx",
        str(getattr(s, "research_mcp_command", "") or ""),
    )
    mcp_layout.addWidget(row)
    row, widgets["_research_mcp_args_json"] = make_line_setting(
        "MCP Args JSON",
        '推荐格式：JSON 数组，例如 ["minimax-coding-plan-mcp"] 或 ["-y","some-search-mcp"]',
        str(getattr(s, "research_mcp_args_json", "") or ""),
    )
    mcp_layout.addWidget(row)
    row, widgets["_research_mcp_env_json"] = make_line_setting(
        "MCP Env JSON",
        '推荐格式：JSON 对象，例如 {"MINIMAX_API_HOST":"https://api.minimax.chat"}',
        str(getattr(s, "research_mcp_env_json", "") or ""),
    )
    mcp_layout.addWidget(row)
    row, widgets["_research_mcp_api_key_env"] = make_line_setting(
        "API Key Env",
        "MCP Server 接收 API Key 的环境变量名；MiniMax=MINIMAX_API_KEY，其他模块按其文档填写，留空则不自动注入",
        str(getattr(s, "research_mcp_api_key_env", "MINIMAX_API_KEY") or "MINIMAX_API_KEY"),
    )
    mcp_layout.addWidget(row)
    row, widgets["_research_mcp_protocol_version"] = make_line_setting(
        "MCP Protocol",
        "initialize 使用的协议版本；默认 2024-11-05，兼容多数现有 MCP Server",
        str(getattr(s, "research_mcp_protocol_version", "2024-11-05") or "2024-11-05"),
    )
    mcp_layout.addWidget(row)
    row, widgets["_research_mcp_stdio_framing"] = make_combo_setting(
        "stdio Framing",
        "官方 MCP stdio 使用 newline；content_length 仅用于旧本地工具兼容",
        ["newline", "content_length"],
        current=str(getattr(s, "research_mcp_stdio_framing", "newline") or "newline"),
        item_labels=["newline JSON-RPC", "Content-Length"],
    )
    mcp_layout.addWidget(row)
    inherit_env_cb = QCheckBox("MCP 子进程继承完整环境变量")
    inherit_env_cb.setToolTip(
        "默认关闭，仅传 PATH/HOME/代理/证书等最小环境和显式 MCP Env；"
        "只在可信本地 MCP Server 需要完整 shell 环境时开启。"
    )
    inherit_env_cb.setChecked(bool(getattr(s, "research_mcp_inherit_environment", False)))
    widgets["_research_mcp_inherit_environment"] = inherit_env_cb
    mcp_layout.addWidget(inherit_env_cb)

    row, widgets["_research_mcp_tool_name"] = make_line_setting(
        "Search Tool",
        "可选：精确指定 MCP 工具名；留空时自动选择 web_search/search/包含 search 的工具",
        str(getattr(s, "research_mcp_tool_name", "") or ""),
    )
    mcp_layout.addWidget(row)
    row, widgets["_research_mcp_query_argument"] = make_line_setting(
        "Query Argument",
        "未设置参数模板时用于传入查询词的字段名；默认 query",
        str(getattr(s, "research_mcp_query_argument", "query") or "query"),
    )
    mcp_layout.addWidget(row)
    row, widgets["_research_mcp_tool_arguments_json"] = make_line_setting(
        "Tool Args JSON",
        "可选：tools/call 参数模板，支持 {query} 和 {results_per_query} 占位符",
        str(getattr(s, "research_mcp_tool_arguments_json", "") or ""),
    )
    mcp_layout.addWidget(row)

    add_setting_group_description(
        mcp_layout,
        "常用切换示例：MiniMax 中国区 Env JSON 填 "
        '{"MINIMAX_API_HOST":"https://api.minimax.chat"}；国际区改为 '
        '{"MINIMAX_API_HOST":"https://api.minimax.io"}。其他 MCP 搜索模块通常只需要改 Command、Args JSON、'
        "API Key Env、Search Tool 和 Tool Args JSON。",
    )

    params_section: Any = make_nested_setting_section(
        "检索参数",
        "控制查询规划、top_k、过滤与失败重试；这些参数会进入初始化缓存指纹，变化后不会复用旧报告。",
        expanded=False,
    )
    sec.body_layout.addWidget(params_section)
    params_layout = params_section.body_layout

    llm_planning_cb = QCheckBox("启用 LLM 查询规划")
    llm_planning_cb.setToolTip(
        "启用后会先用当前模型路由分析规格并规划 queries；失败时自动回退到规则查询。"
        "生成 queries 不等于已经联网，仍需 provider 可用并返回 sources。"
    )
    llm_planning_cb.setChecked(bool(getattr(s, "research_use_llm_planning", True)))
    widgets["_research_use_llm_planning"] = llm_planning_cb
    params_layout.addWidget(llm_planning_cb)

    row, widgets["_research_max_queries"] = make_spin_setting(
        "查询数上限",
        "从规格中规划出的最大查询条数；推荐 2-4",
        int(getattr(s, "research_max_queries", 3)),
        1,
        8,
    )
    params_layout.addWidget(row)
    row, widgets["_research_query_max_parallel"] = make_spin_setting(
        "查询并发数",
        "同时发起的检索查询上限；结果按计划查询序合并，去重/截断与串行一致；推荐 2-3",
        int(getattr(s, "research_query_max_parallel", 2)),
        1,
        8,
    )
    params_layout.addWidget(row)
    row, widgets["_research_results_per_query"] = make_spin_setting(
        "单查询结果数",
        "每条 query 向 provider 请求的 top_k；推荐 3-8",
        int(getattr(s, "research_results_per_query", 5)),
        1,
        20,
    )
    params_layout.addWidget(row)
    row, widgets["_research_max_results"] = make_spin_setting(
        "报告来源上限",
        "去重和过滤后保留到报告与 prompt 摘要的来源数",
        int(getattr(s, "research_max_results", 5)),
        1,
        30,
    )
    params_layout.addWidget(row)
    row, widgets["_research_timeout_s"] = make_float_setting(
        "请求超时秒数",
        "每次 provider 请求的超时时间；网络慢时可调高",
        float(getattr(s, "research_timeout_s", 10.0)),
        1.0,
        120.0,
        decimals=1,
        step=1.0,
    )
    params_layout.addWidget(row)
    row, widgets["_research_retry_attempts"] = make_spin_setting(
        "失败重试次数",
        "每条 query 失败后的额外重试次数；0=不重试",
        int(getattr(s, "research_retry_attempts", 1)),
        0,
        3,
    )
    params_layout.addWidget(row)
    row, widgets["_research_search_depth"] = make_combo_setting(
        "搜索深度",
        "Tavily 等 provider 支持时生效；其他 provider 会作为兼容字段传递",
        ["basic", "advanced"],
        current=str(getattr(s, "research_search_depth", "basic") or "basic"),
        item_labels=["基础", "深入"],
    )
    params_layout.addWidget(row)
    row, widgets["_research_locale"] = make_line_setting(
        "语言/地区",
        "可选，例如 zh-CN、en-US；SearXNG/Brave 等 provider 支持时生效",
        str(getattr(s, "research_locale", "") or ""),
    )
    params_layout.addWidget(row)
    row, widgets["_research_include_domains"] = make_line_setting(
        "域名 allowlist",
        "逗号分隔；填写后仅保留匹配域名及其子域名结果",
        str(getattr(s, "research_include_domains", "") or ""),
    )
    params_layout.addWidget(row)
    row, widgets["_research_exclude_domains"] = make_line_setting(
        "域名 blocklist",
        "逗号分隔；用于屏蔽低质量站点或不想引用的来源",
        str(getattr(s, "research_exclude_domains", "") or ""),
    )
    params_layout.addWidget(row)

    analysis_section: Any = make_nested_setting_section(
        "资料分析与校准",
        "控制检索报告后的资料包压缩，以及最终大纲进入章节契约前的资料提醒。",
        expanded=False,
    )
    sec.body_layout.addWidget(analysis_section)
    analysis_layout = analysis_section.body_layout

    model_prior_cb = QCheckBox("启用模型先验补充")
    model_prior_cb.setToolTip(
        "启用后在外部来源之外追加 model_prior 资料；不会写入 sources，不能当作互联网搜索来源，默认关闭"
    )
    model_prior_cb.setChecked(bool(getattr(s, "research_model_prior_enabled", False)))
    widgets["_research_model_prior_enabled"] = model_prior_cb
    analysis_layout.addWidget(model_prior_cb)

    dossier_cb = QCheckBox("启用资料分析")
    dossier_cb.setToolTip(
        "启用后会把有 sources 的 init_web_research.json 压缩为 init_research_dossier.json，供世界观设定参考；"
        "检索 skipped/failed 时资料包也会跳过"
    )
    dossier_cb.setChecked(bool(getattr(s, "research_dossier_enabled", True)))
    widgets["_research_dossier_enabled"] = dossier_cb
    analysis_layout.addWidget(dossier_cb)
    row, widgets["_research_dossier_max_sources"] = make_spin_setting(
        "资料包来源上限",
        "送入资料分析模型的最大来源数；越高越完整但更慢更贵",
        int(getattr(s, "research_dossier_max_sources", 8)),
        1,
        30,
    )
    analysis_layout.addWidget(row)

    grounding_cb = QCheckBox("启用大纲资料校准")
    grounding_cb.setToolTip(
        "启用后会在资料包可用时生成 outline_research_grounding.json，并作为章节契约的写作提醒；"
        "不会替代大纲或故事规格"
    )
    grounding_cb.setChecked(bool(getattr(s, "outline_research_grounding_enabled", True)))
    widgets["_outline_research_grounding_enabled"] = grounding_cb
    analysis_layout.addWidget(grounding_cb)
    row, widgets["_outline_research_grounding_notes_per_chapter"] = make_spin_setting(
        "每章提醒数上限",
        "大纲资料校准为每章保留的提醒/风险条数上限；0=仅保留全局提醒",
        int(getattr(s, "outline_research_grounding_notes_per_chapter", 3)),
        0,
        12,
    )
    analysis_layout.addWidget(row)

    add_setting_group_header(
        sec.body_layout,
        "运行原则",
        "检索、资料分析或大纲校准失败都不会中断立项；报告会落盘到 reports/。",
    )

    return sec, widgets


def research_preset_payload(preset_id: str) -> dict[str, str]:
    """Return a copy of a built-in research preset."""
    return dict(_RESEARCH_PRESETS.get(str(preset_id or "").strip(), {}))
