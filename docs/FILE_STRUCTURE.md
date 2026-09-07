# Novel Forge 文件结构

**最后更新**: 2026-05-25

本文档描述 Novel Forge 项目的完整目录结构和各模块职责。

## 1. 根目录

```text
.
├── README.md                     # 项目概览
├── MANUAL.md                     # 使用手册
├── FILE_STRUCTURE.md             # 文件结构说明（本文件）
├── .env.example                  # 环境变量模板
├── .env                          # 环境变量配置（不提交到 Git）
├── novel_forge.cli.json          # CLI 配置文件
├── model_profiles.json           # 模型档案配置（可选）
├── pyproject.toml                # 项目配置与依赖
├── novel_forge_desktop.spec      # Desktop 打包配置
├── .gitignore                    # Git 忽略规则
├── docs/                         # 文档目录
├── logo/                         # Logo 文件
├── novel_forge/                  # 源码主目录
├── scripts/                      # 辅助脚本
└── tests/                        # 测试目录
```

## 2. 源码主目录

```text
novel_forge/
├── api/              # FastAPI 应用、路由、依赖注入
├── canon/            # Canon 典据系统（状态追踪、一致性规则、检索）
├── cli/              # 命令行入口、命令实现、错误处理
├── common/           # 公共常量、接口定义
├── core/             # 核心模块（配置、Schema、工具、补丁引擎）
├── desktop/          # NIMO 受管启动器 + 冻结的 PySide6 后备端
├── eval/             # 评估器与 A/B 测试
├── gateway/          # AI 网关（适配器、路由、缓存）
├── memory/           # Memory 记忆系统
├── obs/              # 观测系统（日志、追踪、报告）
├── persistence/      # 持久化存储
├── pipeline/         # Pipeline 执行器与步骤
├── prompts/          # Prompt 模板系统
└── workspace/        # 工作空间（执行逻辑、契约定义）
```

## 3. 核心模块详解

### `novel_forge/api`

```text
api/
├── app.py                      # FastAPI 应用工厂
├── deps.py                     # 依赖注入
├── performance_router.py        # 性能路由
└── routes/
    ├── admin.py                # 管理接口
    ├── chapter.py              # 章节接口
    ├── chapter_tools.py        # 章节工具（精修/全书审计/导出）
    ├── memory.py               # 记忆系统接口
    ├── outline_tracker.py       # 大纲追踪接口
    ├── short.py                # 短篇接口
    ├── status.py               # 状态接口
    └── works.py                # 作品接口
```

### `novel_forge/canon`

```text
canon/
├── __init__.py                 # 模块导出
├── store.py                    # CanonStore 典据持久化存储
├── retriever.py                # CanonRetriever 上下文检索
├── merger.py                   # CanonMerger 状态合并
├── rules.py                    # ConsistencyRules 一致性规则
├── state_tracker.py             # StateTracker 状态追踪
├── outline_tracker.py          # OutlineRelationshipTracker 大纲追踪
├── relationship_tracker.py     # RelationshipTracker 关系追踪
└── continuity_rules.py         # ContinuityRules 连贯性规则
```

### `novel_forge/cli`

```text
cli/
├── main.py                     # Typer 应用入口
├── chapter_runner.py           # CLI 章节运行器
├── chapter_helpers.py          # 章节辅助函数
├── display.py                  # Rich 显示辅助
├── error_handling.py           # 错误处理
├── plot_guard.py               # Plot Guard CLI
├── progress_helpers.py          # 进度辅助
├── runtime_config.py           # CLI 配置解析
├── runtime_helpers.py          # 运行辅助函数
└── commands/
    ├── init_long.py            # 长篇立项命令
    ├── run_short.py            # 短篇创作命令
    ├── run_chapter.py          # 章节续写命令
    ├── maintenance.py          # 维护命令
    └── system.py               # 系统命令
```

### `novel_forge/common`

```text
common/
├── __init__.py
├── constants.py                 # TaskType (119个), ModelTier, 严重程度常量
├── interfaces.py               # 共享接口定义
└── plot_guard.py               # Plot Guard 常量与类型
```

### `novel_forge/core`

```text
core/
├── __init__.py
├── config.py                  # Settings 配置管理
├── settings_loader.py         # 设置加载器（配置解析与验证） ✨
├── constants.py                # Genre, BeatType, PipelineConstants
├── format_contracts.py         # 输出格式契约
├── context.py                  # 运行上下文
├── exceptions.py               # 异常定义
├── exceptions_framework.py     # 统一异常框架（增强：分级异常处理） ✨
├── guardrails.py              # AI 护栏
├── normalizers.py              # 数据标准化
├── parse_utils.py             # 解析工具
├── text_utils.py              # 文本处理工具（含中文引号规范化）
├── task_catalog.py             # 任务路由分组与温度配置
├── performance_monitor.py      # 性能监控
├── performance.py             # 性能指标
├── advanced_cache.py           # 高级缓存
├── async_task_processor.py     # 异步任务处理器
├── response_schemas.py         # 响应 Schema 定义
├── patch_engine/               # 补丁引擎（PatchExecutorV2）
│   ├── __init__.py
│   ├── executor.py             # 补丁执行器
│   ├── matchers.py             # 文本匹配策略（精确/修剪/规范化/锚点）
│   └── models.py               # 补丁操作数据模型
├── schemas/                    # Pydantic 数据模型 (27个)
│   ├── __init__.py
│   ├── base.py               # VersionedSchema 基类
│   ├── beats.py              # 节拍结构
│   ├── bible.py              # 故事/角色圣经
│   ├── blueprint_elements.py  # 叙事要素
│   ├── canon.py              # 典据模型
│   ├── chapter.py            # 章节模型（含因果验证）
│   ├── continuity.py         # 连贯性模型
│   ├── draft.py              # 草稿模型
│   ├── eval_schema.py        # 评估模型
│   ├── init_coherence.py     # 初始化连贯性 ✨
│   ├── outline.py           # 大纲模型（含 element_focus 字段）
│   ├── short_blueprint.py    # 短篇蓝图
│   ├── short_creative.py     # 短篇创作
│   ├── spec.py              # 故事规格
│   ├── story_state.py        # 故事状态
│   ├── style_profile.py      # 风格规范
│   ├── volume.py             # 分卷模型
│   ├── init_v2.py            # 初始化 V2 模型 ✨
│   ├── review.py             # 评审模型 ✨
│   ├── audit.py              # 审计模型 ✨
│   ├── reading_power.py      # 阅读力模型 ✨
│   ├── reading_power_repair.py # 阅读力修复模型 ✨
│   ├── reading_power_window_config.py # 阅读力窗口配置 ✨
│   ├── reading_power_timeline_window_report.py # 窗口报告 ✨
│   ├── plot_progression_report.py # 剧情进展报告 ✨
│   └── suspense_timeline_entry.py # 悬疑时间线索引 ✨
└── utils/                     # 工具函数 (11个)
    ├── __init__.py
    ├── coerce.py             # 类型强制转换
    ├── collections.py        # 集合操作
    ├── edit_tracker.py       # 编辑追踪
    ├── issue_ledger.py       # 问题追踪系统
    ├── json.py               # JSON 工具
    ├── patch_utils.py        # 补丁工具
    ├── pipeline_helpers.py    # Pipeline 辅助
    ├── semantic_drift.py     # 语义漂移检测
    ├── string.py             # 字符串工具
    ├── validation.py         # 校验工具
    └── version_diff.py       # 版本差异
```

### `novel_forge/desktop`

```text
desktop/
├── __init__.py
├── main.py                    # 应用入口
├── window.py                  # 主窗口
├── theme.py                   # 主题管理
├── widgets.py                 # 基础组件
├── workspace.py               # 工作区快照
├── jobs.py                    # 任务记录、后台任务管理
├── preset_manager.py          # 预设管理
├── config_store.py            # 配置存储
├── ai_generate.py             # AI 生成辅助
├── document_presenter.py      # 文档展示
├── files.py                   # 文件操作
├── progress.py                # 进度追踪
├── errors.py                  # 错误处理
├── workflow_requests.py        # 工作流请求
├── constants.py               # Desktop 常量
├── sleep_inhibitor.py         # 睡眠抑制器（macOS/Windows/Linux）
├── components/                # UI 组件
│   ├── __init__.py
│   ├── primitives.py         # 基础组件 (Surface, Badge等)
│   ├── containers.py         # 容器组件 (ScrollPage, MetricCard等)
│   ├── toast.py              # Toast 浮动通知
│   ├── dialogs.py            # 对话框组件
│   ├── forms.py              # 表单组件
│   ├── workflow.py           # 工作流组件
│   └── memory_components.py  # 记忆组件
├── pages/                     # 页面组件 (52个)
│   ├── __init__.py
│   ├── dashboard_page.py     # 案头页面
│   ├── projects_page.py       # 卷帙页面
│   ├── settings_page.py       # 火候页面
│   ├── settings_page_components.py
│   ├── settings_page_save.py  # 保存逻辑 ✨
│   ├── settings_page_parameters.py # 参数配置 ✨
│   ├── settings_page_model_mgmt.py # 模型管理 ✨
│   ├── settings_sections.py
│   ├── workflow_page.py       # 机杼页面
│   ├── workflow_components.py
│   ├── workflow_form_utils.py
│   ├── workflow_forms.py
│   ├── workflow_jobs.py
│   ├── workflow_presets.py
│   ├── workflow_widgets.py
│   ├── workflow_artifacts.py
│   ├── workflow_chapter_launcher.py
│   ├── workflow_workers.py
│   ├── chapter_studio_page.py        # 章节工作室主页
│   ├── chapter_studio_state.py       # 状态管理
│   ├── chapter_studio_actions.py     # 用户动作处理
│   ├── chapter_studio_workers.py     # 后台工作线程
│   ├── chapter_studio_autorun.py     # 自动运行模式
│   ├── chapter_studio_renderers.py   # UI 渲染逻辑
│   ├── chapter_studio_widgets.py     # UI 组件
│   ├── chapter_studio_dialogs.py     # 对话框
│   ├── chapter_studio_inspector.py   # 状态检查器
│   ├── chapter_studio_artifacts.py   # 产物查看器
│   ├── chapter_studio_action_panel.py # 动作面板
│   ├── chapter_studio_memory.py      # 记忆面板集成
│   ├── chapter_studio_coord.py       # 协调器 ✨
│   ├── chapter_studio_data.py        # 数据管理 ✨
│   ├── chapter_studio_contract.py    # 契约管理 ✨
│   ├── chapter_studio_jobs.py        # 任务管理 ✨
│   ├── memory_panel.py               # 记忆面板
│   ├── document_renderers.py          # 文档渲染器
│   ├── document_renderer_reports.py
│   ├── document_renderer_relationships.py
│   ├── document_renderer_story_artifacts.py
│   ├── document_renderer_incremental.py # 增量渲染 ✨
│   ├── document_viewer.py
│   ├── renderer_html.py
│   ├── token_analytics.py            # Token 分析
│   ├── outline_editor.py             # 大纲编辑器 ✨
│   ├── subplot_manager.py            # 子情节管理 ✨
│   ├── final_revision.py             # 终版修订 ✨
│   └── init_artifact_catalog.py      # 初始化产物目录 ✨
├── resources/                  # 资源文件
└── state/                     # 状态管理
```

### `novel_forge/eval`

```text
eval/
├── __init__.py
├── evaluator.py               # 评估器
├── metrics.py                # 评估指标
└── ab_test.py               # A/B 测试
```

### `novel_forge/gateway`

```text
gateway/
├── __init__.py
├── base.py                   # ProviderAdapter 基类
├── router.py                 # ModelRouter 任务路由
├── factory.py                # 工厂函数
├── rate_limiter.py          # 速率限制
├── pricing.py               # 成本计算
├── profiles.py              # 配置档管理
├── cache.py                 # 持久缓存
├── types.py                 # 类型定义
├── secure_keys.py          # 密钥安全管理
├── embedding.py            # Embedding 服务
├── embedding_config.py     # Embedding 配置
├── client_lifecycle.py      # 客户端生命周期管理
├── EMBEDDING_CONFIG.md     # Embedding 配置说明
└── adapters/                # Provider 适配器 (11个)
    ├── __init__.py
    ├── openai.py           # OpenAI 官方
    ├── openai_compat.py    # OpenAI 兼容基类（增强：流式响应/工具调用） ✨
    ├── anthropic.py        # Anthropic Claude
    ├── deepseek.py         # DeepSeek
    ├── tongyi.py           # 通义千问
    ├── kimi.py             # Kimi
    ├── tencent_hunyuan.py  # 腾讯混元
    ├── minimax.py          # MiniMax
    ├── ollama.py           # Ollama 本地
    └── mock.py             # 本地模拟
```

### `novel_forge/memory`

```text
memory/
├── __init__.py
├── base.py                  # 记忆基类
├── episodic.py              # EpisodicMemory 情景记忆
├── motif.py                # MotifTracker 母题追踪
├── critic.py               # CriticAgent 批评代理
├── summary.py              # MultiGranularitySummaryService 多粒度摘要
├── compression.py          # AdaptiveCompressionService 自适应压缩
├── compression_enricher.py # 压缩增强器 ✨
├── integration.py          # MemoryContext 记忆集成
├── audit_coordinator.py   # AuditCoordinator 审计协调
├── retrieval.py            # 检索服务 ✨
├── vector_store.py         # 向量存储 ✨
├── zvec_store.py           # ZVec 存储 ✨
├── vector_serialization.py # 向量序列化 ✨
├── embedding_profiles.py   # Embedding 配置 ✨
└── migration.py            # 数据迁移 ✨
```

### `novel_forge/narrative_state` ✨

```text
narrative_state/
├── __init__.py
├── schemas.py               # EntityRegistry, StateDelta, AdjudicationRecord
├── store.py                 # NarrativeStateStore 向量索引存储
└── evidence.py              # EvidenceChain 证据链追踪
```

### `novel_forge/obs`

```text
obs/
├── __init__.py
├── logger.py               # 日志工具
├── tracer.py              # PipelineTrace 追踪
├── reporter.py            # 报告生成
└── project_logger.py      # 项目日志 (logs/<run_id>/)
```

### `novel_forge/persistence`

```text
persistence/
├── __init__.py
├── base.py                 # 存储基类
├── factory.py              # 存储工厂
├── filesystem.py          # FileSystemStorage 文件存储
├── models.py              # ProjectLayout 项目布局
└── project_staleness.py   # 项目陈旧度检测
```

### `novel_forge/pipeline`

```text
pipeline/
├── __init__.py
├── short_runner.py         # 短篇执行器
├── chapter_runner.py       # 章节执行器
├── quality_gate.py        # 质量守护
├── progress.py            # 进度追踪
├── style_profile_helpers.py # 风格规范辅助 ✨
├── long/                   # 长篇流程
│   ├── __init__.py
│   ├── chapter_flow.py     # 章节流程控制
│   ├── chapter_flow_facade.py # 延迟导入门面 ✨
│   ├── chapter_flow_finalize.py # 章节收束 ✨
│   ├── chapter_flow_generate.py # 章节生成 ✨
│   ├── chapter_flow_orchestrate.py # 章节编排 ✨
│   ├── chapter_flow_review.py # 章节审查 ✨
│   ├── context.py          # 执行上下文
│   ├── decisions.py        # 决策引擎（修复/质量/回退决策）
│   ├── execution_models.py # 执行模型
│   ├── preflight.py        # 预检查
│   ├── repair.py           # 修复逻辑
│   ├── repair_causal.py    # 因果修复编排
│   ├── canon_ops.py        # Canon 操作
│   ├── entity_reference.py # 实体引用 ✨
│   ├── loop.py             # 主循环
│   ├── helpers.py          # 辅助函数
│   ├── repair_dimensions.py # 修复维度协调策略 ✨
│   ├── repair_safety.py    # 修复安全策略 ✨
│   ├── services/           # 领域服务 (50个)
│   │   ├── __init__.py
│   │   ├── anchor_terms.py       # 锚点术语 ✨
│   │   ├── arc_liveness.py       # 故事弧活性 ✨
│   │   ├── blueprint_chapter_refs.py # 蓝图章节引用 ✨
│   │   ├── blueprint_validation.py   # 蓝图验证 ✨
│   │   ├── bridge_service.py
│   │   ├── compaction_service.py
│   │   ├── constraint_router.py     # 约束路由 ✨
│   │   ├── context_helpers.py
│   │   ├── context_projection.py    # 上下文投影 ✨
│   │   ├── contract_execution_repair.py # 契约执行修复 ✨
│   │   ├── element_progress.py   # 要素执行追踪
│   │   ├── guidance_contract_audit.py # 引导契约审计 ✨
│   │   ├── init_cache.py         # 初始化缓存管理 ✨
│   │   ├── init_coherence.py     # 初始化连贯性 ✨
│   │   ├── init_coherence_v2.py  # 初始化连贯性 V2 ✨
│   │   ├── init_context.py       # 初始化上下文构建 ✨
│   │   ├── init_contract.py      # 初始化契约验证 ✨
│   │   ├── init_outline_batch.py # 初始化大纲批量处理 ✨
│   │   ├── init_outline_helpers.py # 初始化大纲辅助 ✨
│   │   ├── init_service.py       # 初始化服务（核心编排）
│   │   ├── init_split_artifacts.py # 初始化产物拆分 ✨
│   │   ├── init_v2.py            # 初始化 V2 ✨
│   │   ├── llm_helpers.py
│   │   ├── llm_service.py
│   │   ├── motif_prompt_format.py # 母题提示词格式 ✨
│   │   ├── outline_helpers.py
│   │   ├── plot_milestones.py    # 剧情里程碑 ✨
│   │   ├── reading_power_timeline_window_manager.py # 阅读力时间线管理 ✨
│   │   ├── scene_writing.py      # 场景写作 ✨
│   │   ├── stage_memory_builder.py # 阶段记忆构建 ✨
│   │   ├── strand_weave.py       # 支线交织 ✨
│   │   ├── task_output_adapters.py # 任务输出适配 ✨
│   │   ├── time_validation.py    # 时间验证 ✨
│   │   ├── upstream_compass.py   # 上游指南针 ✨
│   │   ├── validation_service.py
│   │   └── weave_validation.py   # 交织验证 ✨
│   └── stages/             # 阶段处理相关模块 (11个)
│       ├── __init__.py
│       ├── character_intro.py
│       ├── draft.py
│       ├── finalize.py
│       ├── planning.py
│       ├── quality_checks.py
│       ├── continuity_repair.py
│       ├── causal_repair.py
│       ├── prompt_leak_repair.py
│       ├── dedup_pronoun.py
│       └── word_count.py
└── steps/                  # Pipeline 步骤相关模块 (68个)
    ├── __init__.py
    ├── alignment_step.py
    ├── base.py             # PipelineStep 基类
    ├── beats_step.py
    ├── blueprint_element_select/ # 蓝图元素选择子模块
    │   ├── __init__.py
    │   ├── core.py
    │   ├── element_defs.py
    │   ├── elements.py
    │   ├── presets.py
    │   ├── selection.py
    │   └── validation.py
    ├── blueprint_element_select_step.py
    ├── book_audit_runner.py          # 全书审计运行器 ✨
    ├── book_consistency_step.py
    ├── book_consistency_verify_step.py # 全书一致性验证 ✨
    ├── book_editorial_audit_step.py    # 出版级编辑审计 ✨
    ├── bridge_step.py
    ├── causal_repair_step.py
    ├── causal_validation_step.py
    ├── check_chapter_step.py
    ├── continuity_artifact_repair.py # 连贯性工件修复 ✨
    ├── continuity_eval/              # 连贯性评估子模块
    │   ├── __init__.py
    │   ├── classifier.py
    │   ├── context.py
    │   ├── core.py
    │   ├── evaluator.py
    │   ├── local_checks.py
    │   ├── normalizer.py
    │   ├── scoring.py
    │   ├── semantics.py
    │   └── validators.py
    ├── continuity_eval_step.py
    ├── continuity_repair_step.py
    ├── contract_execution_audit_step.py # 契约执行审计 ✨
    ├── draft_step.py
    ├── edit_step.py
    ├── editorial_check_step.py    # 编辑检查 ✨
    ├── editorial_contract_step.py # 编辑契约 ✨
    ├── evaluate_step.py
    ├── extract_step.py
    ├── forbidden_sources.py   # 禁忌来源检查 ✨
    ├── macro_guard_step.py    # 宏观护栏 ✨
    ├── patch_step.py
    ├── plan_step.py
    ├── planning/               # 规划子模块
    │   ├── __init__.py
    │   ├── context.py
    │   ├── core.py
    │   └── hints.py
    ├── polish_outline_step.py  # 大纲精修 ✨
    ├── polish_step.py
    ├── profile_style_step.py  # 风格规范生成
    ├── prompt_diagnostics.py   # 提示词诊断 ✨
    ├── pronoun_check_step.py  # 代词检查
    ├── reading_power_eval_step.py  # 阅读力评估 ✨
    ├── reading_power_repair_step.py # 阅读力修复 ✨
    ├── repair/               # 修复子模块
    │   ├── __init__.py
    │   ├── base.py
    │   ├── forbidden_checker.py
    │   ├── patch_bridge.py
    │   ├── text_utils.py
    │   └── word_guard.py
    ├── short_blueprint_step.py
    ├── short_creative_step.py
    ├── spec_step.py
    ├── split_artifact_runner.py # 产物拆分运行器 ✨
    ├── state_adjudication_step.py # 状态裁决 ✨
    ├── step_registry.py    # 步骤注册表
    └── volume_step.py
```

### `novel_forge/prompts`

```text
prompts/
├── __init__.py
├── builder.py              # PromptBuilder 提示词构建
├── registry.py             # TaskType → Template 映射
├── styles.py              # 动态风格发现
├── context_helpers.py      # 上下文辅助
├── compliance.py           # 合规检查
├── version.py              # 模板版本管理
└── prompts/               # Jinja2 模板目录
    ├── INDEX.md            # 模板索引
    ├── VERSION.md          # 版本信息
    ├── CHANGELOG.md       # 变更日志
    ├── _output_formats.j2
    ├── _quality_standards.j2
    ├── _role_definitions.j2
    ├── webnovel_style_guide.j2    # 网文风格兼容别名（转发）
    ├── _base/             # 基础模板
    │   ├── _default_style.j2
    │   ├── _output_formats.j2
    │   ├── _quality_standards.j2
    │   └── _role_definitions.j2
    ├── _styles/            # 风格模板 (动态发现)
    │   ├── README.md
    │   ├── _registry.j2
    │   ├── _render_style_profile.j2
    │   ├── _default_style.j2
    │   ├── _literary_style.j2
    │   └── _webnovel_style.j2
    ├── beats/             # 节拍生成 (2)
    ├── canon/             # Canon 操作 (11) ✨
    ├── checking/          # 检查评估 (39个) ✨
    │   ├── _causal_core.j2
    │   ├── _continuity_core.j2
    │   ├── adjudicate_blueprint_coherence.j2
    │   ├── adjudicate_contract_coherence.j2
    │   ├── adjudicate_contract_completion.j2
    │   ├── adjudicate_fact_conflict.j2
    │   ├── adjudicate_init_conflict_candidates.j2
    │   ├── adjudicate_outline_inheritance.j2
    │   ├── adjudicate_state_delta.j2
    │   ├── book_consistency.j2
    │   ├── book_consistency_verify.j2
    │   ├── book_editorial_audit.j2
    │   ├── causal_repair_typed.j2
    │   ├── causal_validate.j2
    │   ├── check_alignment.j2
    │   ├── check_chapter.j2
    │   ├── check_editorial.j2
    │   ├── continuity_eval.j2
    │   ├── continuity_repair.j2
    │   ├── critic_causal.j2
    │   ├── critic_character.j2
    │   ├── critic_continuity.j2
    │   ├── critic_strengths.j2
    │   ├── critic_world_building.j2
    │   ├── derive_editorial_character_voices.j2
    │   ├── derive_editorial_contract.j2
    │   ├── derive_editorial_element_directives.j2
    │   ├── derive_editorial_structure.j2
    │   ├── derive_editorial_style_constraints.j2
    │   ├── element_progress_arbiter.j2
    │   ├── evaluate_reading_power.j2
    │   ├── extract_blueprint_holistic_claims.j2
    │   ├── extract_init_coherence_claims.j2
    │   ├── guardrail_repair.j2
    │   ├── macro_guard_audit.j2
    │   ├── profile_style.j2
    │   ├── repair_adjudicated_issue.j2
    │   ├── repair_reading_power.j2
    │   ├── repair_semantic_verify.j2
    │   ├── structure_profile_derive.j2
    │   └── style_profile_derive.j2
    ├── compression/       # 压缩处理 (5)
    ├── initialization/    # 项目初始化 (17) ✨
    ├── planning/          # 规划模板 (20) ✨
    ├── summary/           # 摘要模板 (5)
    └── writing/           # 写作模板 (8) ✨
```

### `novel_forge/workspace`

```text
workspace/
├── __init__.py
├── contracts.py            # 请求/响应契约
├── execution.py           # 三入口共享执行逻辑（薄入口，向后兼容）
├── execution_runners.py    # 执行运行器（短篇/长篇/章节入口分发） ✨
├── execution_state.py      # 执行状态管理（StatePacket 构建与流转） ✨
├── execution_io.py         # 执行 I/O（文件读写、持久化、加载） ✨
├── execution_helpers.py    # 执行辅助函数（通用工具） ✨
├── execution_polish.py     # 精修执行逻辑 ✨
├── execution_repair.py     # 修复执行逻辑（薄入口，向后兼容）
├── execution_repair_common.py      # 修复模块共享工具（ExecutionResult, 签名匹配等） ✨
├── execution_repair_continuity.py  # 连贯性修复执行逻辑 ✨
├── execution_repair_causal.py      # 因果修复执行逻辑 ✨
├── execution_export.py     # 导出执行逻辑 ✨
├── execution_book_consistency.py   # 全书一致性执行（薄入口，向后兼容）
├── execution_book_common.py        # 全书一致性共享工具 ✨
├── execution_book_verify.py        # 全书一致性验证逻辑 ✨
├── execution_book_repair.py        # 全书一致性修复逻辑 ✨
├── execution_book_reevaluate.py    # 章节重评估逻辑 ✨
├── execution_book_continue.py      # 修复继续逻辑 ✨
├── execution_book_entry.py         # 全书一致性主入口 ✨
├── runtime.py             # RuntimeServices 服务聚合
├── projects.py            # 项目视图与概览
├── chapter_sessions.py    # 章节会话主入口
├── chapter_session_handlers.py  # 会话处理器
├── chapter_session_state.py    # 会话状态管理
├── chapter_session_results.py  # 会话结果处理
├── memory_contracts.py     # 记忆系统契约
└── result_payloads.py     # 结果载荷定义
```

## 4. 测试目录

**测试文件数**: 462 个 `.py` 文件

```text
tests/
├── __init__.py              # 测试包
├── conftest.py              # pytest 配置和 fixtures
├── _check_crash.py          # 崩溃检查
├── _verify_intro.py         # 引入验证
├── manual_qa_test.py        # 手动 QA 测试
├── test_task11_persistence.py # 持久化测试
├── unit/                    # 单元测试 (404个)
│   └── ...
├── integration/             # 集成测试 (32个)
│   ├── __init__.py
│   ├── test_chapter_pipeline.py
│   ├── test_chapter_session_flow.py
│   ├── test_previous_chapter_exit_to_next_opening.py
│   └── test_short_pipeline.py
├── desktop/                 # 桌面应用测试 (17个) ✨
│   ├── __init__.py
│   ├── conftest.py
│   └── test_*.py
└── regression/              # 回归测试 (3个)
    ├── __init__.py
    ├── test_fixed_cases.py
    └── test_smoke.py
```

## 5. 文档目录

```text
docs/
├── MACOS_DEV.md                    # macOS 开发笔记
├── WINDOWS_DEV.md                  # Windows 开发笔记
├── audit_responsibilities.md       # 审计职责
├── book_audit_architecture.md      # 全书审计架构
├── chapter-generation-memory-usage-analysis.md # 章节生成内存分析
├── deep-research-report on prompt.md           # Prompt 深度研究报告 I
├── deep-research-report on prompt II.md        # Prompt 深度研究报告 II
├── desktop_platform_notes.md       # Desktop 平台笔记
├── element_library_extension.md    # 要素库扩展
├── forbidden_element_mapping.md    # 禁忌要素映射
├── forbidden_element_optimization_plan.md # 禁忌要素优化计划
├── memory-module-usage-report.md   # 记忆模块使用报告
├── memory-ui-diagnostic-report.md  # 记忆 UI 诊断报告
├── review_repair_unified_architecture.md # 审查修复统一架构
├── visual_regression_testing.md    # 视觉回归测试
├── 分析报告/                      # 分析报告
│   ├── webnovel-writer对比优化报告.md
│   └── zvec-integration.md
├── 支线交织机制实现计划.md
└── 软著/                         # 软件著作权材料
    ├── 01-软著材料总说明.md
    ├── 02-软件基本信息.md
    ├── 03-软件功能说明书.md
    ├── 04-软件技术说明.md
    ├── 05-申请表填写参考.md
    ├── 06-源代码提交说明.md
    ├── 07-源代码节选.txt
    ├── 08-材料检查清单.md
    ├── 程序创作报告.md
    └── 软件说明书.md
```

## 6. 脚本目录

```text
scripts/
├── analyze_repair_rate.py              # 修复率分析
├── audit_contract_schema_depth.py      # 契约 Schema 深度审计
├── audit_prompt_format_layers.py       # Prompt 格式层次审计
├── benchmark_continuity_latency.py     # 连贯性延迟基准测试
├── benchmark_guardrail_repair.py       # 护栏修复基准测试
├── benchmark_ui_perf.py                # UI 性能基准测试
├── build_desktop.py                    # 默认 React/Tauri NIMO 打包脚本
├── build_pyside_desktop.py             # PySide6 兼容后备端打包脚本
├── check_carry_forward.py              # 检查携带状态
├── cleanup_plot_threads.py             # 清理剧情线脚本
├── fix_pronoun_consistency.py          # 修复代词一致性
├── generate_prompt_index.py            # 生成 Prompt 索引
├── lint_prompt_layers.py               # Prompt 层次检查
├── repair_motifs.py                    # 修复母题
├── scaffold_prompt.py                  # Prompt 模板脚手架
├── test_prompt.py                      # Prompt 测试
├── verify_blueprint_elements.py        # 验证蓝图要素
├── verify_format_contracts.py          # 验证格式契约
├── verify_motif_parity.py              # 验证母题一致性
├── verify_task_13.py                   # 验证 Task 13
└── verify_templates.py                 # 验证模板
```

## 7. 运行产物目录

所有项目都落在 `NOVEL_FORGE_STORAGE_ROOT`，默认 `./data`。

```text
data/
├── <project_id>/
│   ├── spec.json                    # 故事规格
│   ├── story_bible.json             # 故事圣经
│   ├── character_bible.json         # 角色圣经
│   ├── style_profile.json           # 风格规范
│   ├── outline.json                 # 大纲
│   ├── narrative_blueprint.json     # 叙事蓝图
│   ├── canon/
│   │   ├── canon_current.json       # 当前典据
│   │   ├── canon_history.jsonl      # 典据历史
│   │   └── canon_outcomes.jsonl    # 典据结果
│   ├── chapters/                    # 章节正文
│   │   └── chapter_<N>.md
│   ├── drafts/                      # 草稿版本
│   ├── plans/                       # 章节计划
│   │   └── element_progress.json    # 要素执行追踪
│   ├── reports/                     # 创作报告
│   │   ├── creative_report_<N>.json   # 创作报告
│   │   ├── eval_report_<N>.json       # 评估报告
│   │   ├── continuity_report_<N>.json # 连贯性报告
│   │   ├── alignment_report_<N>.json   # 对齐报告
│   │   ├── causal_report_<N>.json      # 因果报告
│   │   └── guard_report_<N>.json       # 护栏报告
│   ├── states/                      # 状态包
│   │   └── state_packet_<N>.json
│   ├── exports/                     # 导出文件
│   └── logs/<run_id>/              # 运行日志
│       ├── summary.json             # 运行摘要
│       ├── events.jsonl             # 关联 ID + 序号的事件流
│       ├── application.jsonl        # INFO+ Python 结构化诊断日志（脱敏）
│       ├── python.log               # WARNING+ 人类可读诊断日志
│       └── model_calls/             # 模型调用记录
├── presets/                         # 预设文件
│   ├── short_<name>.json
│   ├── long_<name>.json
│   └── chapter_<name>.json
└── logs/                            # 全局日志
```

## 8. 配置文件

### `novel_forge.cli.json`

CLI 配置文件，包含常用命令的默认参数。

### `model_profiles.json`

模型档案配置，包含 Provider 配置和任务路由规则。

### `.env`

环境变量配置，包含 API Key 和全局设置。

## 9. 新增模块标记

以下标记用于标识最近新增的功能模块：

| 标记 | 含义 |
|------|------|
| ✨ | 2026年4月新增的功能模块 |

最近新增的模块包括：

- **Core Patch Engine**: `patch_engine/` — 补丁引擎（PatchExecutorV2，含匹配器链） ✨
- **Core**: `response_schemas.py` — 响应 Schema 定义 ✨
- **Core**: `settings_loader.py` — 设置加载器（配置解析与验证） ✨
- **Core**: `exceptions_framework.py` — 统一异常框架（增强：分级异常处理） ✨
- **Pipeline Long**: `decisions.py` — 决策引擎（修复/质量/回退决策） ✨
- **Pipeline Long Services**: `init_cache.py` — 初始化缓存管理 ✨
- **Pipeline Long Services**: `init_context.py` — 初始化上下文构建 ✨
- **Pipeline Long Services**: `init_contract.py` — 初始化契约验证 ✨
- **Pipeline Long Services**: `init_outline_helpers.py` — 初始化大纲辅助 ✨
- **Pipeline Long Services**: `init_outline_batch.py` — 初始化大纲批量处理 ✨
- **Desktop**: `sleep_inhibitor.py` — 睡眠抑制器（防止长时间任务中系统休眠） ✨
- **Pipeline Services**: `validation_service.py` — 验证服务 ✨
- **Prompts Checking**: `critic_world_building.j2` — 世界构建一致性检查 ✨
- **Prompts Checking**: `book_consistency_verify.j2` — 全书一致性验证 ✨
- **Prompts Canon**: `extract_canon_delta.j2` — 增量典据提取 ✨
- **Scripts**: `cleanup_plot_threads.py` — 剧情线清理脚本 ✨
- **Workspace**: `execution_runners.py` — 执行运行器（入口分发） ✨
- **Workspace**: `execution_state.py` — 执行状态管理 ✨
- **Workspace**: `execution_io.py` — 执行 I/O（文件读写） ✨
- **Workspace**: `execution_helpers.py` — 执行辅助函数 ✨
- **Workspace**: `execution_polish.py` — 精修执行逻辑 ✨
- **Workspace**: `execution_repair.py` — 修复执行逻辑 ✨
- **Workspace**: `execution_export.py` — 导出执行逻辑 ✨
- **Workspace**: `execution_book_consistency.py` — 全书一致性执行 ✨
- **Gateway**: `adapters/openai_compat.py` — OpenAI 兼容基类（增强：流式响应/工具调用） ✨

## 10. 文档优先级

如果需要判断"哪个文档更可信"，优先顺序是：

1. **源码** - 最权威的事实来源
2. **README.md** - 项目概览和快速开始
3. **MANUAL.md** - 用户使用流程与操作指南
4. **FILE_STRUCTURE.md** - 目录职责说明（本文档）
5. **docs/软著/程序创作报告.md** - 程序创作报告
6. **docs/分析报告/UI架构性能分析报告.md** - UI 架构性能分析
7. **novel_forge/prompts/prompts/INDEX.md** - Prompt 模板索引
8. **novel_forge/gateway/EMBEDDING_CONFIG.md** - Embedding 配置说明
