import { useEffect, useRef, useState } from "react";

type ParameterField = {
  readonly hint: string;
  readonly id: string;
  readonly initial: string;
  readonly label: string;
  readonly max?: number;
  readonly min?: number;
  readonly options?: readonly string[];
  readonly step?: number;
  readonly type: "number" | "select" | "text";
};

type ParameterSection = {
  readonly description?: string;
  readonly fields: readonly ParameterField[];
  readonly title: string;
};

type ParameterBlock =
  | { readonly kind: "heading"; readonly description: string; readonly title: string }
  | { readonly kind: "section"; readonly section: ParameterSection };

const blocks: readonly ParameterBlock[] = [
  // ═══════════════════════════════════════════════════════════════════════════
  // 创作参数
  // ═══════════════════════════════════════════════════════════════════════════
  { kind: "section", section: { title: "短篇生成参数", description: "短篇编辑 · 控制短篇生成后的编辑迭代。", fields: [
    { id: "short-max-edit", label: "最多修订轮次", hint: "自适应模式按问题执行 0–2 轮；该值仅作为上限，0 表示禁止自动改文", initial: "2", min: 0, max: 10, type: "number" },
  ] } },
  { kind: "section", section: { title: "长篇生成 — 初始化与蓝图", description: "大纲批次与密度 · 小批次可降低长项目的 JSON 失败概率。", fields: [
    { id: "outline-batch", label: "大纲批次章数", hint: "章节大纲首批/续写每批生成的章数上限；0=自动", initial: "0", min: 0, max: 50, type: "number" },
    { id: "outline-beats-min", label: "章纲节拍下限", hint: "每章 beats_summary 最少条数，推荐 4", initial: "4", min: 1, max: 20, type: "number" },
    { id: "outline-beats-max", label: "章纲节拍上限", hint: "每章 beats_summary 最多条数，推荐 8；过高会变成分场大纲", initial: "8", min: 1, max: 30, type: "number" },
    { id: "init-outline-main-points-min", label: "主线点下限", hint: "每章 main_plot_points 最少条数，推荐 2", initial: "2", min: 1, max: 12, type: "number" },
    { id: "init-outline-main-points-max", label: "主线点上限", hint: "每章 main_plot_points 最多条数，推荐 4", initial: "4", min: 1, max: 20, type: "number" },
    { id: "init-outline-subplot-points-max", label: "支线点上限", hint: "每章 subplot_points 最多条数，推荐 3；0=不主动规划", initial: "3", min: 0, max: 12, type: "number" },
    { id: "init-outline-element-focus-max", label: "要素聚焦上限", hint: "每章 element_focus 最多数量，schema 上限为 3", initial: "3", min: 0, max: 3, type: "number" },
    { id: "init-outline-payoffs-min", label: "微兑现下限", hint: "每章 expected_payoffs 最少条数，推荐 1", initial: "1", min: 0, max: 8, type: "number" },
    { id: "init-outline-payoffs-max", label: "微兑现上限", hint: "每章 expected_payoffs 最多条数，推荐 3", initial: "3", min: 0, max: 12, type: "number" },
    { id: "chapter-contract-batch-size", label: "契约批次章数", hint: "章节契约每批生成的章数；0=自动", initial: "0", min: 0, max: 50, type: "number" },
    { id: "init-fragment-parallel", label: "初始化分片并发", hint: "2-4 稳定，5-8 更快但更吃限流", initial: "4", min: 1, max: 8, type: "number" },
    { id: "init-kb-phase-c-parallel", label: "知识边界并行", hint: "知识边界生成与风格/实体图谱并行；关闭则恢复 KB 先行串行结构", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-entity-reference-max-parallel", label: "实体指称并发", hint: "实体指称裁决的最大并发批次数；2-3 最稳", initial: "3", min: 1, max: 8, type: "number" },
    { id: "split-tasks-enabled", label: "巨型任务分片", hint: "开启后章节抽取、全书审计等大 JSON 会拆分执行", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-character-profile-parallel-min-roster", label: "角色档案并行阈值", hint: "角色清单达到该人数后才分批并行；推荐 11", initial: "11", min: 1, max: 50, type: "number" },
    { id: "init-character-profile-batch-size", label: "角色档案批次大小", hint: "并行生成时每批补全的角色数；建议 4-6", initial: "5", min: 1, max: 12, type: "number" },
    { id: "outline-thinking", label: "大纲思考默认", hint: "未在路由中显式覆盖时，蓝图与章节大纲是否允许 thinking", initial: "false", options: ["true", "false"], type: "select" },
    { id: "outline-thinking-providers", label: "思考 Provider allowlist", hint: "逗号分隔；仅未设置路由覆盖时生效", initial: "tongyi,deepseek", type: "text" },
    { id: "outline-thinking-models", label: "思考模型 allowlist", hint: "逗号分隔，空=不按模型限制", initial: "", type: "text" },
    { id: "outline-multi-turn", label: "大纲续写多轮默认", hint: "PLAN_OUTLINE_CONTINUE 是否带入前批对话历史", initial: "true", options: ["true", "false"], type: "select" },
    { id: "outline-multi-turn-providers", label: "大纲多轮 Provider allowlist", hint: "逗号分隔；仅未设置路由覆盖时生效", initial: "tongyi,deepseek", type: "text" },
    { id: "outline-multi-turn-models", label: "大纲多轮模型 allowlist", hint: "逗号分隔，空=不按模型限制", initial: "", type: "text" },
    { id: "chapter-contract-context-window", label: "契约上下文窗口", hint: "章节契约分批生成时注入前后多少章大纲", initial: "2", min: 0, max: 12, type: "number" },
    { id: "chapter-contract-dynamic-budget", label: "契约动态预算", hint: "按目标字数限制 required/allowed/出口状态条数", initial: "true", options: ["true", "false"], type: "select" },
    { id: "chapter-contract-hard-words-per-item", label: "硬约束字数/条", hint: "目标字数每达到多少字允许 1 条硬性推进", initial: "1000", min: 200, max: 5000, type: "number" },
    { id: "chapter-contract-hard-min-items", label: "硬约束下限", hint: "每章 required_progressions 最少保留条数", initial: "3", min: 1, max: 12, type: "number" },
    { id: "chapter-contract-hard-max-items", label: "硬约束上限", hint: "每章 required_progressions 最多保留条数；建议 5-6", initial: "6", min: 1, max: 12, type: "number" },
    { id: "chapter-contract-soft-words-per-item", label: "软铺垫字数/条", hint: "目标字数每达到多少字允许 1 条 allowed_* 铺垫", initial: "900", min: 200, max: 5000, type: "number" },
    { id: "chapter-contract-soft-max-items", label: "软铺垫上限", hint: "每章 allowed_changes 最多保留条数", initial: "5", min: 1, max: 12, type: "number" },
    { id: "chapter-contract-state-words-per-item", label: "出口状态字数/条", hint: "目标字数每达到多少字允许 1 条 exit_state", initial: "1200", min: 200, max: 5000, type: "number" },
    { id: "chapter-contract-state-max-items", label: "出口状态上限", hint: "每章 exit_state_targets 最多保留条数", initial: "4", min: 1, max: 12, type: "number" },
    { id: "chapter-contract-multi-turn", label: "章节契约多轮默认", hint: "PLAN_CHAPTER_CONTRACTS 是否带入前批对话历史", initial: "true", options: ["true", "false"], type: "select" },
    { id: "chapter-contract-multi-turn-providers", label: "契约多轮 Provider allowlist", hint: "逗号分隔；仅未设置路由覆盖时生效", initial: "tongyi,deepseek,minimax", type: "text" },
    { id: "chapter-contract-multi-turn-models", label: "契约多轮模型 allowlist", hint: "逗号分隔，空=不按模型限制", initial: "", type: "text" },
    { id: "contract-coherence-batch-size", label: "契约裁判批大小", hint: "每批纳入多少章契约做一致性裁判", initial: "12", min: 1, max: 30, type: "number" },
    { id: "contract-coherence-context-window", label: "契约裁判上下文窗口", hint: "裁判时注入前后多少章契约作为邻近上下文", initial: "2", min: 0, max: 12, type: "number" },
    { id: "contract-coherence-max-parallel", label: "契约裁判并发", hint: "契约裁判分批检查的最大并发批次数；2 最稳", initial: "2", min: 1, max: 8, type: "number" },
    { id: "world-rule-count-min", label: "世界规则数下限", hint: "world_rule_book 最少规则数；推荐 10", initial: "10", min: 4, max: 20, type: "number" },
    { id: "world-rule-count-max", label: "世界规则数上限", hint: "world_rule_book 最多规则数；推荐 14", initial: "14", min: 6, max: 24, type: "number" },
    { id: "world-rule-hard-min", label: "硬规则下限", hint: "至少需要 N 条 hard 规则；推荐 3", initial: "3", min: 1, max: 10, type: "number" },
    { id: "world-rule-always-on-cap", label: "始终生效硬规则上限", hint: "always_on=true 的硬规则上限；推荐 4", initial: "4", min: 1, max: 10, type: "number" },
    { id: "world-rule-category-min", label: "类别覆盖下限", hint: "至少覆盖 N 个规则类别；推荐 4", initial: "4", min: 1, max: 6, type: "number" },
    { id: "world-rule-ability-required", label: "强制 ability_tech 类别", hint: "世界包含魔法/异能/科技体系时必须覆盖", initial: "true", options: ["true", "false"], type: "select" },
    { id: "world-rule-block-on-violation", label: "治理违反阻断初始化", hint: "默认 false（仅警告）；true 时违反会中止初始化", initial: "false", options: ["true", "false"], type: "select" },
    { id: "long-vol-auto-ch", label: "自动分卷章节阈值", hint: "总章数超过此值时自动启用分卷；推荐 30-80", initial: "60", min: 1, max: 500, type: "number" },
    { id: "long-vol-auto-word", label: "自动分卷字数阈值", hint: "总字数超过此值时自动启用分卷；推荐 100000-400000", initial: "250000", min: 10000, max: 1000000, type: "number" },
    { id: "long-default-cpv", label: "每卷默认章节数", hint: "推荐 12-30", initial: "20", min: 1, max: 200, type: "number" },
    { id: "style-profile-enabled", label: "风格规范", hint: "初始化时自动生成专属写作风格规范", initial: "true", options: ["true", "false"], type: "select" },
    { id: "style-profile-required", label: "风格规范必需", hint: "生成失败时阻塞流程而非静默降级", initial: "false", options: ["true", "false"], type: "select" },
    { id: "temp-init-story-bible", label: "世界观温度", hint: "StoryBible 分片生成温度；高更发散，低更稳定（0-2）", initial: "0.7", min: 0, max: 2, step: 0.05, type: "number" },
    { id: "temp-init-character-bible", label: "角色圣经温度", hint: "角色清单/档案/关系生成温度（0-2）", initial: "0.7", min: 0, max: 2, step: 0.05, type: "number" },
    { id: "temp-plan-outline-batch", label: "大纲批次温度", hint: "章纲分批生成温度（0-2）", initial: "0.7", min: 0, max: 2, step: 0.05, type: "number" },
    { id: "temp-plan-chapter-contracts", label: "章节契约温度", hint: "契约分批生成温度；低更稳（0-2）", initial: "0.25", min: 0, max: 2, step: 0.05, type: "number" },
    { id: "temp-init-entity-registry", label: "实体注册表温度", hint: "实体补充裁决温度（0-2）", initial: "0.2", min: 0, max: 2, step: 0.05, type: "number" },
    { id: "temp-init-knowledge-boundaries", label: "知识边界温度", hint: "角色知识边界生成温度（0-2）", initial: "0.3", min: 0, max: 2, step: 0.05, type: "number" },
  ] } },
  { kind: "section", section: { title: "长篇生成 — 章节写作与节拍", description: "章节节拍 · 控制章节规划的节拍数量与场景颗粒度。", fields: [
    { id: "long-beats-min", label: "最少节拍数", hint: "推荐 3-6", initial: "4", min: 1, max: 20, type: "number" },
    { id: "long-beats-max", label: "最多节拍数", hint: "推荐 6-10", initial: "8", min: 1, max: 30, type: "number" },
    { id: "scene-switches", label: "场景切换上限", hint: "章节计划 scene_intents 的有效上限；推荐 8", initial: "8", min: 2, max: 12, type: "number" },
    { id: "long-sensory-anchor-limit", label: "每场感官候选上限", hint: "Plan 可构思多候选，Draft/Edit 只接收前 N 个；1=更克制", initial: "2", min: 1, max: 2, type: "number" },
    { id: "long-beat-chars", label: "节拍描述字符上限", hint: "推荐 200-320", initial: "260", min: 50, max: 1000, type: "number" },
    { id: "auto-introduce", label: "自动角色出场", hint: "章节生成前自动检测并生成新出场角色档案", initial: "true", options: ["true", "false"], type: "select" },
    { id: "auto-introduce-char-limit", label: "每章新增角色上限", hint: "限制自动写入人物设定的新角色数量；0=不新增", initial: "2", min: 0, max: 10, type: "number" },
    { id: "polish-enabled", label: "精修润色", hint: "审核后、归档前对正文做文学性打磨（增加 token 消耗）", initial: "false", options: ["true", "false"], type: "select" },
    { id: "polish-threshold", label: "精修自动触发阈值", hint: "审核评分低于此值自动触发精修润色（推荐 6.5-7.5）", initial: "7", min: 0, max: 10, step: 0.5, type: "number" },
    { id: "humanize-enabled", label: "启用 AI 去痕", hint: "章节生成后自动检测并修复 AI 写作痕迹", initial: "false", options: ["true", "false"], type: "select" },
    { id: "humanize-change-ratio-cap", label: "去痕变更率上限", hint: "AI 去痕允许的最大文本变更比例（推荐 0.03-0.08）", initial: "0.05", min: 0.01, max: 0.30, step: 0.01, type: "number" },
    { id: "humanize-min-text-length", label: "去痕最短触发字数", hint: "低于此字数的章节不触发 AI 去痕（推荐 300-1000）", initial: "500", min: 100, max: 10000, type: "number" },
    { id: "humanize-patch-confidence-floor", label: "精确补丁置信门槛", hint: "命中置信度低于此值时只记录报告（推荐 0.75-0.90）", initial: "0.80", min: 0, max: 1, step: 0.01, type: "number" },
    { id: "humanize-paragraph-confidence-floor", label: "段落改写置信门槛", hint: "段落级定向改写只收集高于此值的命中（推荐 0.65-0.85）", initial: "0.70", min: 0, max: 1, step: 0.01, type: "number" },
    { id: "humanize-library-sim-threshold", label: "拟人化库相似阈值", hint: "示例库检索的最低匹配度（推荐 0.45-0.70）", initial: "0.60", min: 0, max: 1, step: 0.01, type: "number" },
  ] } },
  { kind: "section", section: { title: "长篇状态 — 上下文、投喂与归档", description: "状态档案 · 控制角色资料和关系字段进入提示词的规模。", fields: [
    { id: "max-profiles", label: "最多投喂角色数", hint: "推荐 8-16", initial: "8", min: 1, max: 50, type: "number" },
    { id: "max-profile-chars", label: "保留字段字符数", hint: "推荐 160-800", initial: "240", min: 50, max: 2000, type: "number" },
    { id: "max-relations", label: "每角色最大关系数", hint: "推荐 4-10", initial: "6", min: 1, max: 30, type: "number" },
    { id: "canon-events", label: "近期事件", hint: "Canon 注入时的事件上限，推荐 20-60", initial: "40", min: 1, max: 200, type: "number" },
    { id: "canon-chars", label: "角色条目", hint: "推荐 15-40", initial: "15", min: 1, max: 100, type: "number" },
    { id: "canon-foreshadow", label: "伏笔条目", hint: "推荐 10-40", initial: "30", min: 1, max: 100, type: "number" },
    { id: "plan-foreshadow", label: "规划步骤伏笔上限", hint: "只注入最近 N 条，节省 token（推荐 5-15）", initial: "10", min: 1, max: 50, type: "number" },
    { id: "canon-facts", label: "世界事实", hint: "推荐 40-120", initial: "80", min: 1, max: 500, type: "number" },
    { id: "long-compact-interval", label: "压缩间隔", hint: "每隔 N 章触发一次 Canon 压缩。推荐 5", initial: "5", min: 1, max: 20, type: "number" },
    { id: "long-compact-start", label: "压缩起始章节", hint: "从第 N 章开始启用压缩。推荐 10", initial: "10", min: 1, max: 50, type: "number" },
    { id: "long-compact-stale", label: "陈旧判定章数", hint: "超过 N 章未活跃的角色/物品标记为陈旧。推荐 8", initial: "8", min: 1, max: 30, type: "number" },
    { id: "long-compact-lookahead", label: "大纲前瞻章数", hint: "压缩时向前看 N 章大纲，保护未来出场角色。推荐 12", initial: "12", min: 1, max: 30, type: "number" },
    { id: "long-compact-min-chars", label: "最少保留角色数", hint: "压缩后至少保留的活跃角色数。推荐 8", initial: "8", min: 1, max: 30, type: "number" },
    { id: "long-compact-target-facts", label: "世界事实目标数", hint: "压缩后世界事实的目标数量。推荐 120", initial: "120", min: 10, max: 500, type: "number" },
    { id: "long-compact-keep-recent-facts", label: "保留近期世界事实", hint: "强制保留最近 N 章内的世界事实。推荐 40", initial: "40", min: 5, max: 100, type: "number" },
    { id: "long-compact-archive-foreshadowing", label: "伏笔归档延迟", hint: "伏笔回收后 N 章归档。推荐 6", initial: "6", min: 1, max: 20, type: "number" },
    { id: "narrative-evidence-candidates", label: "动态证据候选池", hint: "Zvec 先按截止时间过滤，再从候选池中按排名装入；推荐 16-48", initial: "24", min: 1, max: 128, type: "number" },
    { id: "narrative-evidence-token-budget", label: "动态证据预算", hint: "规划/复审的历史证据 token 预算；推荐 1600-4000", initial: "2400", min: 256, max: 16000, type: "number" },
    { id: "boundary-prev-tail-paragraphs", label: "上章末尾段数", hint: "Bridge/Edit-Polish/Repair 共用的上一章末尾段落数（推荐 3-5）", initial: "5", min: 1, max: 12, type: "number" },
    { id: "boundary-opening-paragraphs", label: "本章开头段数", hint: "开场衔接检查与修复的目标窗口段落数（推荐 1-3）", initial: "3", min: 1, max: 8, type: "number" },
    { id: "draft-prompt-diag-enabled", label: "Draft Prompt 诊断", hint: "记录估算 token 和最大上下文字段", initial: "true", options: ["true", "false"], type: "select" },
    { id: "draft-prompt-warn-tokens", label: "Draft 警告阈值", hint: "估算 prompt token 达到该值写 warning；0=只记录 info", initial: "32000", min: 0, max: 200000, type: "number" },
    { id: "prompt-diag-enabled", label: "生成链路 Prompt 诊断", hint: "记录 Bridge/Plan/Edit 等阶段的估算 token", initial: "true", options: ["true", "false"], type: "select" },
    { id: "prompt-warn-tokens", label: "生成链路警告阈值", hint: "非 Draft prompt 估算 token 达到该值写 warning", initial: "32000", min: 0, max: 200000, type: "number" },
    { id: "narrative-state-enabled", label: "启用裁判状态", hint: "启用 LLM 裁判驱动的 narrative ledger/projection", initial: "true", options: ["true", "false"], type: "select" },
    { id: "arc-liveness-window", label: "弧光沉睡窗口", hint: "副线/角色弧光超过多少章未推进时给 Plan 轻触提示", initial: "6", min: 0, type: "number" },
    { id: "compress-enabled", label: "启用压缩", hint: "plan_chapter 前语义压缩", initial: "true", options: ["true", "false"], type: "select" },
    { id: "compress-min", label: "最小触发字符数", hint: "推荐 200-500", initial: "220", min: 0, type: "number" },
    { id: "compress-max-tokens", label: "压缩后上限", hint: "推荐 1024-4096", initial: "2048", min: 0, type: "number" },
    { id: "contract-audit-enabled", label: "契约执行审计", hint: "归档前核对章节契约、里程碑窗口和推进账本", initial: "true", options: ["true", "false"], type: "select" },
    { id: "contract-audit-strictness", label: "契约审计强度", hint: "warn=只报告；block=阻断未来泄露/禁用推进；strict=同时阻断必达缺失", initial: "block", options: ["block", "warn", "strict"], type: "select" },
    { id: "expression-channel-detection", label: "表达通道检测", hint: "把近义生理反应、动作标签、感官锚点等合并为语义通道治理", initial: "true", options: ["true", "false"], type: "select" },
    { id: "expression-cooldown-window", label: "表达冷却章数", hint: "通道进入冷却后多少章内提醒 Draft 改换表达机制", initial: "3", min: 0, type: "number" },
    { id: "future-leak-guard-enabled", label: "未来泄露护栏", hint: "检查当前章是否提前兑现未来里程碑或高光触发方式", initial: "true", options: ["true", "false"], type: "select" },
    { id: "plot-progression-strictness", label: "剧情推进强度", hint: "warn=只报告；block=阻断未来泄露/禁用推进；strict=同时阻断必达缺失", initial: "block", options: ["block", "warn", "strict"], type: "select" },
    { id: "stage-visibility-debug", label: "阶段可见性诊断", hint: "记录 Bridge/Plan/Draft/Judge 实际收到的里程碑摘要", initial: "true", options: ["true", "false"], type: "select" },
    { id: "draft-char-history-lookback", label: "角色历史回溯窗口(章)", hint: "草稿阶段从记忆系统获取角色状态历史与关系变化的前溯章数", initial: "10", min: 0, type: "number" },
    { id: "extract-abort-on-severe-damage", label: "重度损坏即停", hint: "解析后若检测到重度结构损坏，则终止流程而不是继续污染 canon", initial: "true", options: ["true", "false"], type: "select" },
    { id: "extract-existing-thread-ids", label: "已有线索 ID 上限", hint: "推荐 20-40；0=不注入已有 thread_id 列表", initial: "30", min: 0, type: "number" },
    { id: "extract-max-character-state-deltas", label: "角色状态 delta 上限", hint: "只保留变化最明确的角色状态项；推荐 4-10", initial: "8", min: 0, type: "number" },
    { id: "extract-max-exit-state-characters", label: "章末角色状态上限", hint: "chapter_exit_state 中最多保留的关键角色数；推荐 4-8", initial: "6", min: 0, type: "number" },
    { id: "extract-max-plot-thread-deltas", label: "线索 delta 上限", hint: "只保留本章真正推进的线索；推荐 4-10", initial: "8", min: 0, type: "number" },
    { id: "extract-max-relationship-deltas", label: "关系 delta 上限", hint: "只保留变化最大的关系项；推荐 4-10", initial: "8", min: 0, type: "number" },
    { id: "extract-output-base-tokens", label: "输出基础 token", hint: "基础输出预算；推荐 3072-4096", initial: "4096", min: 0, type: "number" },
    { id: "extract-output-max-tokens", label: "输出 token 上限", hint: "Canon 提取单次调用的 max_tokens；推荐 8192-16384", initial: "12288", min: 0, type: "number" },
    { id: "extract-output-per-2500-chars", label: "每2500字追加 token", hint: "正文越长，按块追加输出预算；推荐 512-1024", initial: "768", min: 0, type: "number" },
    { id: "extract-output-per-character-tokens", label: "每角色追加 token", hint: "每个已知角色额外分配的输出预算；推荐 300-600", initial: "450", min: 0, type: "number" },
    { id: "extract-prior-characters", label: "前态角色快照上限", hint: "推荐 8-16；0=不注入角色快照", initial: "12", min: 0, type: "number" },
    { id: "extract-prior-plot-thread-summary-chars", label: "线索摘要字符上限", hint: "推荐 40-80；0=只传标题和状态", initial: "60", min: 0, type: "number" },
    { id: "extract-prior-plot-threads", label: "前态线索上限", hint: "推荐 6-12；0=不注入活跃线索基线", initial: "10", min: 0, type: "number" },
    { id: "extract-prior-relationships", label: "前态关系上限", hint: "推荐 8-16；0=不注入人物关系基线", initial: "12", min: 0, type: "number" },
    { id: "extract-recent-character-window", label: "前态角色回看章数", hint: "推荐 3-8；0=不过滤最近章节窗口", initial: "5", min: 0, type: "number" },
    { id: "extract-severe-damage-threshold", label: "缺段停机阈值", hint: "原始响应里出现但解析后丢失的顶层区块达到 N 个时停止；推荐 2", initial: "2", min: 0, type: "number" },
    { id: "narrative-state-candidate-evidence-limit", label: "每候选证据数", hint: "每个状态候选保留的正文证据条数。建议 2；超过 3 通常只会增加输入重复", initial: "2", min: 0, type: "number" },
    { id: "narrative-state-candidate-max", label: "候选变化上限", hint: "每章常规进入单候选裁判的最大数。必达契约证据会额外保留；建议 6-10", initial: "8", min: 0, type: "number" },
    { id: "narrative-state-final-context-max-chars", label: "最终裁决上下文预算(字符)", hint: "仅影响有残留模糊/修复问题时的最终 LLM 合并。建议 18000", initial: "18000", min: 0, type: "number" },
    { id: "narrative-state-final-target-max-chars", label: "最终契约目标字数", hint: "最终合并中每条契约目标的说明长度。建议 96；ID、状态路径和类型不会被截断", initial: "96", min: 0, type: "number" },
    { id: "narrative-state-pending-tail", label: "待定项注入条数", hint: "注入最近多少条 ambiguous/defer 待定事项，提醒模型不要提前定论", initial: "6", min: 0, type: "number" },
    { id: "narrative-state-repair-rounds", label: "裁判修复轮数", hint: "LLM 最终裁判要求修复时最多重试几轮；0=只裁判不自动修", initial: "1", min: 0, type: "number" },
    { id: "narrative-state-required", label: "失败阻断流程", hint: "初始化或章节裁判失败时阻断，而不是降级跳过（推荐开启）", initial: "true", options: ["true", "false"], type: "select" },
  ] } },
  { kind: "heading", title: "质量与记忆", description: "控制章节审计、归档门控、自动修复、追读力与记忆增强。" },
  { kind: "section", section: { title: "质量、门控与自动修复", description: "质量阈值与裁判策略 · 控制基础评分线和本章审查器的预算。", fields: [
    { id: "alignment-threshold", label: "对齐阈值", hint: "低于此值视为需要修复（推荐 6.5-7.5）", initial: "7", min: 0, max: 10, step: 0.5, type: "number" },
    { id: "guard-mode", label: "主线护栏模式", hint: "free / balanced / strict / ai_judge", initial: "balanced", options: ["free", "balanced", "strict", "ai_judge"], type: "select" },
    { id: "max-context-chapters", label: "最大上下文章节", hint: "推荐 12-36", initial: "24", min: 1, max: 100, type: "number" },
    { id: "judge-max-tokens", label: "响应 token 上限", hint: "推荐 1024-2048", initial: "1536", min: 256, max: 16384, type: "number" },
    { id: "judge-entity-actions", label: "回写实体动作", hint: "是否把裁决回写到创作报告", initial: "true", options: ["true", "false"], type: "select" },
    { id: "guardrails-trust", label: "本地护栏信任级别", hint: "balanced=本地检测提示+LLM判定（推荐）", initial: "balanced", options: ["strict", "balanced", "llm_first"], type: "select" },
    { id: "local-confidence-threshold", label: "本地检查置信度阈值", hint: "仅传递 >= 此置信度的结果（推荐 0.5-0.8）", initial: "0.7", min: 0, type: "number" },
    { id: "plan-max-key-revelations", label: "单章重大揭示上限", hint: "规划阶段允许的 key_revelations 最大数量（推荐 1-3）", initial: "2", min: 0, type: "number" },
    { id: "pronoun-autofix-mode", label: "代词自动修复", hint: "默认建议关闭自动改写，仅做检查告警；需要时再开启", initial: "off", options: ["off", "pov_only", "pov_or_many"], type: "select" },
    { id: "quality-trend-tracker-enabled", label: "长期质量趋势监控", hint: "每章归档后记录总评、追读力、连贯性、伏笔老化和召回质量", initial: "false", options: ["true", "false"], type: "select" },
    { id: "summary-drift-check-enabled", label: "卷末摘要漂移检查", hint: "在每卷结束后比对卷摘要、章节摘要与 StoryKernel 关键事实", initial: "false", options: ["true", "false"], type: "select" },
  ] } },
  { kind: "section", section: { title: "归档门控", description: "最终归档裁决：低于硬线会阻断归档并触发重新规划或失败。", fields: [
    { id: "min-accept-score", label: "总评硬线", hint: "最终综合评分低于此值会阻断归档（推荐 5.0）", initial: "5", min: 0, max: 10, step: 0.5, type: "number" },
    { id: "continuity-hard-block", label: "连贯性硬线", hint: "最终连贯性评分低于此值会阻断归档（推荐 4.0）", initial: "4", min: 0, max: 10, step: 0.5, type: "number" },
    { id: "causal-hard-block", label: "因果硬线", hint: "最终因果评分低于此值会阻断归档（推荐 4.0）", initial: "4", min: 0, max: 10, step: 0.5, type: "number" },
    { id: "word-count-archive-gate", label: "归档前字数闸门", hint: "开启时按目标字数重整或阻断；关闭后仅保留极短正文保护", initial: "true", options: ["true", "false"], type: "select" },
    { id: "wave-word-count-policy", label: "WAVE 字数后置检查", hint: "inherit=跟随归档字数闸门；enforce=始终执行；warn=只警告", initial: "inherit", options: ["inherit", "enforce", "warn"], type: "select" },
    { id: "word-count-max-rejections", label: "字数拒绝上限", hint: "同一章连续因字数不达标被拒绝的最大次数；推荐 2", initial: "2", min: 0, max: 10, type: "number" },
    { id: "rp-archive-policy", label: "追读力归档策略", hint: "off=仅报告；floor_only=极低分阻断；floor_or_core_high=+核心高危", initial: "floor_only", options: ["off", "floor_only", "floor_or_core_high"], type: "select" },
    { id: "rp-hard-block-threshold", label: "追读力硬线", hint: "仅在追读力归档策略启用分数硬线时生效（推荐 3.0）", initial: "3", min: 0, max: 10, step: 0.5, type: "number" },
    { id: "guard-archive-policy", label: "AI 护栏归档策略", hint: "warn=只记录；block_actionable=高置信可修复违约阻断", initial: "warn", options: ["warn", "block_actionable"], type: "select" },
    { id: "guard-archive-confidence", label: "护栏阻断置信度", hint: "AI 护栏策略为可行动违约阻断时生效（推荐 0.8）", initial: "0.8", min: 0, max: 1, step: 0.05, type: "number" },
  ] } },
  { kind: "section", section: { title: "宏观护栏", description: "控制多章节轨迹审计、调纲冷却和偏离阈值。", fields: [
    { id: "macro-guard-enabled", label: "宏观护栏", hint: "启用多章节轨迹宏观审计（推荐开启）", initial: "true", options: ["true", "false"], type: "select" },
    { id: "macro-guard-interval", label: "宏观审计间隔", hint: "每N章触发一次全量宏观审计（推荐 3-5）", initial: "5", min: 1, max: 20, type: "number" },
    { id: "macro-guard-max-adjustments", label: "最大调纲次数", hint: "整本书最多允许调整大纲几次（推荐 3）", initial: "3", min: 0, max: 10, type: "number" },
    { id: "macro-guard-cooldown", label: "调纲冷却期", hint: "大纲调整后跳过几章不审计（推荐 5）", initial: "5", min: 0, max: 20, type: "number" },
    { id: "macro-guard-drift-warning", label: "宏观偏离警告阈值", hint: "drift_score超过此值输出规划提示（推荐 0.3）", initial: "0.3", min: 0, max: 1, step: 0.05, type: "number" },
    { id: "macro-guard-drift-alert", label: "宏观偏离告警阈值", hint: "drift_score超过此值触发调纲确认（推荐 0.5）", initial: "0.5", min: 0, max: 1, step: 0.05, type: "number" },
    { id: "macro-guard-drift-critical", label: "宏观偏离严重阈值", hint: "drift_score超过此值建议回滚锚点（推荐 0.7）", initial: "0.7", min: 0, max: 1, step: 0.05, type: "number" },
    { id: "macro-guard-auto-apply", label: "自动应用宏观提示", hint: "连跑模式下自动将warning_hint约束应用到下一章规划", initial: "true", options: ["true", "false"], type: "select" },
  ] } },
  { kind: "section", section: { title: "全书审计", description: "控制深度分析、智能漏斗和审计对话框的全局默认值。", fields: [
    { id: "book-audit-mode", label: "默认分析模式", hint: "full_text=注入章节全文深审；summary=仅摘要", initial: "full_text", options: ["full_text", "summary"], type: "select" },
    { id: "book-audit-chapter-max-chars", label: "单章最大字符数", hint: "仅 full_text 模式生效；推荐 8000-20000", initial: "20000", min: 1000, max: 100000, type: "number" },
    { id: "book-audit-prompt-char-budget", label: "单次输入预算", hint: "全书审计按该字符预算自动拆分全文；推荐 32000-80000", initial: "48000", min: 12000, max: 500000, type: "number" },
    { id: "book-audit-max-chapters-per-batch", label: "每批审查章节", hint: "full_text 模式单次请求最多审查的章节数；推荐 6-16", initial: "12", min: 1, max: 500, type: "number" },
    { id: "book-audit-max-tokens", label: "审计输出上限", hint: "全书审计返回 JSON 的 max_tokens；推荐 4096-16384", initial: "8192", min: 1024, max: 65536, type: "number" },
    { id: "book-audit-max-issues-per-chunk", label: "每块最多问题", hint: "单次模型调用最多返回的问题数；建议 8-12", initial: "12", min: 1, max: 50, type: "number" },
    { id: "book-audit-issue-pool-max-items", label: "问题池条目上限", hint: "注入全书审计提示词的问题面板条目上限；推荐 80-200", initial: "160", min: 0, max: 1000, type: "number" },
    { id: "book-audit-location-strictness", label: "定位严格度", hint: "strict=精确落段；balanced=平衡；loose=覆盖优先", initial: "balanced", options: ["strict", "balanced", "loose"], type: "select" },
    { id: "book-audit-prompt-hint", label: "审计附加提示词", hint: "可留空；用于强调本项目的重点审计规则", initial: "", type: "text" },
    { id: "book-audit-two-phase", label: "启用智能漏斗", hint: "先摘要筛查，再对目标章节全文深审", initial: "true", options: ["true", "false"], type: "select" },
    { id: "book-audit-two-phase-threshold", label: "漏斗标记阈值", hint: "摘要标记章节占比超过该值时截断目标章节", initial: "0.7", min: 0, max: 1, step: 0.05, type: "number" },
    { id: "book-audit-two-phase-max-target", label: "漏斗目标章节上限", hint: "摘要筛查后进入全文深审的最多章节数；推荐 12-24", initial: "24", min: 1, max: 500, type: "number" },
  ] } },
  { kind: "section", section: { title: "全书修复", description: "控制全书审计后的自动修复、问题池锚定和防污染闸门。", fields: [
    { id: "book-audit-repair-min-severity", label: "自动修复最低级别", hint: "仅修复达到该严重度的审计问题", initial: "warning", options: ["critical", "warning", "info"], type: "select" },
    { id: "book-audit-repair-max-chapters", label: "自动修复最多章节", hint: "防止一次全书审计触发过多修复任务；推荐 6-20", initial: "12", min: 1, max: 500, type: "number" },
    { id: "book-audit-repair-concurrency", label: "自动修复并发上限", hint: "1=串行最稳；>1 使用有界并发调度", initial: "1", min: 1, max: 8, type: "number" },
    { id: "book-audit-generate-repair-report", label: "生成卷帙修复报告", hint: "审计完成后在 reports/ 生成全书修复报告", initial: "true", options: ["true", "false"], type: "select" },
    { id: "book-audit-repair-guard-enabled", label: "修复后防污染闸门", hint: "疑似提示词/JSON/重复段落写入正文时自动回滚", initial: "true", options: ["true", "false"], type: "select" },
    { id: "book-audit-repair-guard-max-delta-ratio", label: "闸门改动比例上限", hint: "单章自动修复改动比例超过此值会回滚（推荐 0.08-0.15）", initial: "0.12", min: 0.01, max: 1, step: 0.01, type: "number" },
    { id: "book-audit-repair-guard-max-added-chars", label: "闸门新增字数上限", hint: "单章自动修复新增字数超过此值会回滚（推荐 400-800）", initial: "600", min: 100, max: 10000, type: "number" },
    { id: "book-audit-max-continue-batches", label: "自动续修最大批次数", hint: "达到此批次数后停止自动续修；推荐 10", initial: "10", min: 1, max: 50, type: "number" },
  ] } },
  { kind: "section", section: { title: "连续性与开场修复", description: "控制跨章连贯性、禁用元素和开场承接修复。", fields: [
    { id: "continuity-repair-threshold", label: "触发阈值", hint: "分数低于此值才触发修复（推荐 8.5-9.5，0=始终修复）", initial: "9", min: 0, max: 10, step: 0.5, type: "number" },
    { id: "continuity-max-repair-rounds", label: "最大修复轮数", hint: "0=禁用连贯性修复；推荐 2-3", initial: "2", min: 0, max: 5, type: "number" },
    { id: "forbidden-cross-chapter-window", label: "跨章节禁用窗口", hint: "0=仅上章硬禁；N=最近N章作为软约束提示（推荐 3-6）", initial: "4", min: 0, max: 30, type: "number" },
    { id: "forbidden-hard-max-items", label: "硬禁提示上限", hint: "单章进入生产提示的硬禁修辞项数量", initial: "8", min: 0, max: 50, type: "number" },
    { id: "forbidden-soft-max-items", label: "软禁提示上限", hint: "单章进入生产提示的软禁修辞项数量；默认 12", initial: "12", min: 0, max: 80, type: "number" },
    { id: "forbidden-quota-max-items", label: "限额回环上限", hint: "单章有限额复用/回环项数量上限", initial: "6", min: 0, max: 50, type: "number" },
    { id: "forbidden-sources-max-items", label: "禁用来源记录上限", hint: "每章保留的禁用元素来源追踪记录数量", initial: "24", min: 0, max: 160, type: "number" },
    { id: "forbidden-rank-by-relevance", label: "禁用项相关性排序", hint: "开启后先按来源置信度和本章语境相关性排序", initial: "true", options: ["true", "false"], type: "select" },
    { id: "opening-guard-enabled", label: "开场硬门禁", hint: "质量检查前先做开场承接预筛并尝试窄窗口修复", initial: "true", options: ["true", "false"], type: "select" },
    { id: "opening-guard-max-issues", label: "门禁问题上限", hint: "开场硬门禁单次最多处理的问题数", initial: "2", min: 1, max: 6, type: "number" },
  ] } },
  { kind: "section", section: { title: "因果修复", description: "控制因果逻辑校验和修复退出策略。", fields: [
    { id: "causal-repair-enabled", label: "启用因果修复", hint: "是否对章节进行因果逻辑修复", initial: "true", options: ["true", "false"], type: "select" },
    { id: "causal-threshold", label: "退出阈值", hint: "修复过程中分数达到此值即提前停止（推荐 4.0-6.0）", initial: "5", min: 0, max: 10, step: 0.5, type: "number" },
    { id: "causal-max-repair-rounds", label: "最大修复轮数", hint: "0=禁用，推荐 2-3", initial: "2", min: 0, max: 5, type: "number" },
    { id: "causal-fail-mode", label: "因果失败策略", hint: "warn_unknown=标记未知并告警；fail_open=失败时放行", initial: "warn_unknown", options: ["warn_unknown", "fail_open"], type: "select" },
  ] } },
  { kind: "section", section: { title: "修复流程与本地检查", description: "控制自动修复策略、章内流程检查和本地护栏预筛。", fields: [
    { id: "repair-control-mode", label: "修复控制模式", hint: "manual=只给建议；ai_assisted=低风险自动修；ai_auto=全自动", initial: "ai_assisted", options: ["manual", "ai_assisted", "ai_auto"], type: "select" },
    { id: "max-auto-repair-attempts", label: "最大自动修复次数", hint: "普通问题的自动重试上限（推荐 2-4）", initial: "2", min: 1, max: 10, type: "number" },
    { id: "repair-must-fix-severity", label: "必修级别", hint: "达到此严重程度时强制继续修复", initial: "critical", options: ["critical", "high", "medium", "off"], type: "select" },
    { id: "recheck-strategy", label: "复检策略", hint: "targeted_with_global_guard=目标点验+全局高危守卫", initial: "targeted_with_global_guard", options: ["targeted_with_global_guard", "strict_targeted"], type: "select" },
    { id: "change-budget-threshold", label: "单轮改动预算", hint: "单轮修复改动占比上限（0.15=15%）", initial: "0.15", min: 0, max: 1, step: 0.05, type: "number" },
    { id: "repair-display-min-severity", label: "面板显示最低严重程度", hint: "低于此级别的问题不在问题面板中显示", initial: "low", options: ["low", "medium", "high", "critical"], type: "select" },
    { id: "repair-always-reaudit", label: "修复后始终重审核", hint: "开启后不论 AI 是否改动文本都会重审核", initial: "false", options: ["true", "false"], type: "select" },
    { id: "check-chapter-enabled", label: "章内质量检查", hint: "章节生成后运行章内质量检查", initial: "true", options: ["true", "false"], type: "select" },
    { id: "max-consistency-replans", label: "一致性重试上限", hint: "质量校验未通过时自动重新规划的最大次数；推荐 2", initial: "2", min: 0, max: 5, type: "number" },
    { id: "local-check-mode", label: "本地检查模式", hint: "prescreen=快速预筛选（推荐）；off=仅 LLM", initial: "prescreen", options: ["prescreen", "off"], type: "select" },
  ] } },
  { kind: "section", section: { title: "元素进度裁判", description: "默认仅使用规则判定。开启后仅对灰区结果触发低频 LLM 复判。", fields: [
    { id: "element-progress-arbiter-enabled", label: "启用灰区仲裁", hint: "开启后按灰区规则触发低频复判", initial: "false", options: ["true", "false"], type: "select" },
    { id: "element-progress-arbiter-max-items", label: "每章仲裁上限", hint: "每章最多仲裁的要素数量（0=禁用）", initial: "1", min: 0, max: 5, type: "number" },
    { id: "element-progress-gray-low", label: "灰区分数下界", hint: "规则分数落在灰区才会触发仲裁（含边界）", initial: "0.8", min: 0, max: 10, step: 0.1, type: "number" },
    { id: "element-progress-gray-high", label: "灰区分数上界", hint: "建议高于下界；若反填将由运行时自动纠正", initial: "1.4", min: 0, max: 10, step: 0.1, type: "number" },
    { id: "element-progress-arbiter-max-tokens", label: "仲裁输出上限", hint: "单次仲裁输出 token 上限（建议 128-384）", initial: "256", min: 64, max: 1024, type: "number" },
    { id: "element-progress-arbiter-temp", label: "仲裁温度", hint: "建议低温（0.0-0.2）以保持判定稳定", initial: "0", min: 0, max: 1, step: 0.05, type: "number" },
  ] } },
  { kind: "section", section: { title: "追读力 — 评估修复与下章提示", description: "评估修复 · 处理钩子缺失、兑现断裂与张力失衡。", fields: [
    { id: "reading-repair", label: "启用追读力修复", hint: "低于修复线或出现核心追读问题时，尝试定向修复本章", initial: "true", options: ["true", "false"], type: "select" },
    { id: "reading-threshold", label: "修复触发线", hint: "追读力评分低于此值时进入修复循环（推荐 6.0）", initial: "6", min: 0, max: 10, step: 0.5, type: "number" },
    { id: "reading-rounds", label: "最大修复轮数", hint: "0=不自动修复；推荐 1-2", initial: "2", min: 0, max: 5, type: "number" },
    { id: "reading-repair-change-ratio", label: "单轮改动上限", hint: "追读力修复允许的最大文本改动比例（0.20=20%）", initial: "0.20", min: 0, max: 1, step: 0.05, type: "number" },
    { id: "reading-power-enabled", label: "启用窗口提示", hint: "开启后在规划/桥接阶段注入跨章追读力提示", initial: "true", options: ["true", "false"], type: "select" },
    { id: "reading-power-window-size", label: "窗口大小", hint: "覆盖章节数（推荐 3-10）", initial: "5", min: 3, max: 10, type: "number" },
    { id: "reading-power-left-offset", label: "窗口左偏移", hint: "向左扩展的历史章节数（负值表示向前扩展）", initial: "0", min: -5, max: 0, type: "number" },
    { id: "reading-power-right-offset", label: "窗口右偏移", hint: "向右扩展的预览章节数", initial: "0", min: 0, max: 5, type: "number" },
    { id: "reading-power-suspense-delay", label: "悬念延迟阈值", hint: "超过此章数未兑现则触发预警", initial: "3", min: 1, max: 7, type: "number" },
    { id: "reading-power-force-resolve", label: "强制兑现阈值", hint: "超过此章数必须在下章兑现悬念", initial: "5", min: 3, max: 10, type: "number" },
    { id: "reading-power-hook-alternation", label: "钩子交替阈值", hint: "连续相同类型钩子的容忍度（推荐 1-4）", initial: "2", min: 1, max: 4, type: "number" },
    { id: "reading-power-tension-deviation", label: "张力偏差容忍", hint: "实际张力与预期张力的最大偏差", initial: "1.5", min: 0.5, max: 3, step: 0.1, type: "number" },
  ] } },
  { kind: "section", section: { title: "记忆模块 — 语义检索与质量增强", description: "记忆索引 · 控制语义检索、摘要与母题追踪。", fields: [
    { id: "episodic-memory", label: "启用情节记忆", hint: "归档后写入章节语义记忆，供后续章节检索", initial: "true", options: ["true", "false"], type: "select" },
    { id: "semantic-search", label: "启用语义检索", hint: "规划和生成阶段使用关联片段补充上下文", initial: "true", options: ["true", "false"], type: "select" },
    { id: "chapter-summary-words", label: "章节摘要目标词数", hint: "每章摘要的目标长度", initial: "200", min: 50, max: 1200, type: "number" },
    { id: "memory-embedding-profile", label: "嵌入模型", hint: "选择用于向量检索的嵌入模型；auto=自动选择", initial: "auto", type: "text" },
    { id: "memory-vector-store-backend", label: "向量后端", hint: "zvec=正式后端；in_memory=测试/mock", initial: "zvec", options: ["zvec", "in_memory"], type: "select" },
    { id: "memory-zvec-index-type", label: "zvec 索引类型", hint: "HNSW=默认；DiskANN=大规模低内存场景", initial: "hnsw", options: ["hnsw", "ivf", "flat", "hnsw_rabitq", "diskann"], type: "select" },
    { id: "memory-summary-enabled", label: "启用摘要服务", hint: "生成场景/章节/卷/弧线级别的多粒度摘要", initial: "true", options: ["true", "false"], type: "select" },
    { id: "memory-volume-summary-words", label: "卷级摘要目标字数", hint: "跨卷承接与全局记忆的目标长度；推荐 800-1600", initial: "1000", min: 300, max: 5000, type: "number" },
    { id: "memory-summary-input-token-budget", label: "摘要单次输入预算", hint: "超过预算自动做完整覆盖的 LLM 分块归纳；推荐 16000-48000", initial: "24000", min: 2048, max: 120000, type: "number" },
    { id: "memory-summary-recent-chapters", label: "近章摘要窗口", hint: "规划/生成直接注入的最近章数；推荐 3-8", initial: "5", min: 1, max: 20, type: "number" },
    { id: "memory-volume-summary-threshold", label: "不分卷时卷摘要注入阈值", hint: "当前章节超过此值时注入卷级摘要", initial: "10", min: 1, max: 200, type: "number" },
    { id: "memory-compression-enabled", label: "启用自适应压缩", hint: "带质量验证的上下文压缩", initial: "true", options: ["true", "false"], type: "select" },
    { id: "memory-concurrent-indexing", label: "并发索引", hint: "章节归档后母题提取、摘要生成、片段索引是否并发执行；关闭则依次顺序执行", initial: "true", options: ["true", "false"], type: "select" },
    { id: "memory-critic-enabled", label: "启用评审代理", hint: "独立的章节质量评审（通用模型）", initial: "true", options: ["true", "false"], type: "select" },
    { id: "memory-critic-async", label: "异步运行", hint: "在后台异步运行评审（不阻塞主流程）", initial: "false", options: ["true", "false"], type: "select" },
    { id: "memory-critic-cache-enabled", label: "评审缓存", hint: "缓存相同输入的评审结果；路由/模型变化会自动失效", initial: "true", options: ["true", "false"], type: "select" },
    { id: "memory-critic-cache-max", label: "缓存条目上限", hint: "Critic 结果缓存最大条目数（建议 16-64）", initial: "24", min: 0, type: "number" },
    { id: "memory-critic-timeout", label: "评审软超时(秒)", hint: "并发评审任务超时后返回可用结果；母题提取较慢，建议设为 90 秒以上", initial: "90", min: 0, type: "number" },
    { id: "memory-critic-timeout-extend-attempts", label: "超时自动延长次数", hint: "首轮超时后自动追加等待轮次（0=不延长，建议 1-2）", initial: "1", min: 0, type: "number" },
    { id: "memory-critic-timeout-extend-multiplier", label: "延长倍率", hint: "每次追加等待 = 上一轮超时 × 倍率（例如 1.5）", initial: "1.5", min: 0, type: "number" },
    { id: "memory-motif-enabled", label: "启用母题追踪", hint: "追踪主题/母题的回调与重复（通用模型）", initial: "true", options: ["true", "false"], type: "select" },
    { id: "memory-motif-repetition", label: "重复检查", hint: "检测非刻意的母题重复", initial: "true", options: ["true", "false"], type: "select" },
    { id: "memory-motif-re-extract-concurrency", label: "母题重新提取并发数", hint: "修补母题历史→强制重新提取时，同时处理的章节数（默认 3，范围 1-10）", initial: "3", min: 0, type: "number" },
    { id: "memory-motif-related-lookback", label: "母题关联窗口(章)", hint: "控制当前章关联母题范围：0=仅本章，2=前两章+本章（默认）", initial: "2", min: 0, type: "number" },
    { id: "motif-prompt-max-items", label: "预算裁剪上限(项)", hint: "预算超限时列表裁剪的单类上限；默认 8。越高越保留信息，但更占 prompt", initial: "8", min: 0, type: "number" },
    { id: "motif-prompt-token-budget", label: "母题提示预算(token)", hint: "控制母题上下文进入提示词的估算 token 上限；500 偏保守，母题密集可调到 800-1000", initial: "500", min: 0, type: "number" },
    { id: "motif-forbidden-max-items", label: "避重复提示上限", hint: "每次写作提示允许进入 prompt 的近期避重复母题数量；默认 3", initial: "3", min: 0, type: "number" },
    { id: "motif-repetition-lookback", label: "重复检查回看(章)", hint: "控制母题无意识重复检测回看多少章；默认 5", initial: "5", min: 0, type: "number" },
    { id: "motif-repetition-recent-gap", label: "近距重复阈值(章)", hint: "章距小于该值时才判为过近复用；默认 2 表示只拦截紧邻上一章", initial: "2", min: 0, type: "number" },
    { id: "motif-suggestion-min-chapters", label: "建议最小章数间隔", hint: "母题在此章数内曾使用则不提示（默认 5）", initial: "5", min: 0, type: "number" },
    { id: "motif-suggestion-min-occurrences", label: "建议最小出现次数", hint: "母题至少出现过此次数才会生成建议（默认 3）", initial: "3", min: 0, type: "number" },
    { id: "motif-auto-forget-ephemeral", label: "自动遗忘非重要母题", hint: "自动退役低重要度、低出现次数、长期未复现的母题；保留历史但不再进入提示词", initial: "true", options: ["true", "false"], type: "select" },
    { id: "motif-ephemeral-forget-after", label: "非重要母题遗忘间隔(章)", hint: "低重要度母题在多少章未复现后自动退役；默认 12", initial: "12", min: 0, type: "number" },
    { id: "motif-ephemeral-importance-threshold", label: "遗忘重要度阈值(%)", hint: "importance_score 不高于该百分比才可自动退役；默认 35", initial: "35", min: 0, type: "number" },
    { id: "motif-ephemeral-max-occurrences", label: "遗忘出现次数上限", hint: "只自动遗忘出现次数不超过该值的母题；默认 1", initial: "1", min: 0, type: "number" },
    { id: "motif-dormant-callback-min", label: "沉睡回调间隔(章)", hint: "母题至少沉睡多少章后进入长线回调建议；默认 20", initial: "20", min: 0, type: "number" },
  ] } },
  { kind: "heading", title: "运行环境", description: "管理桌面提醒、本地模型、日志、格式修复、存储与调试开关。" },
  { kind: "section", section: { title: "桌面提醒 — 任务提示音", description: "任务完成、等待人工决策和任务失败时可播放提示音。", fields: [
    { id: "notify-sound", label: "启用提示音", hint: "关闭后所有任务状态变化只保留界面提示", initial: "true", options: ["true", "false"], type: "select" },
    { id: "notify-success-sound", label: "任务完成", hint: "短篇、立项、章节写作、修复、导出等任务成功结束时播放", initial: "chime", options: ["chime", "bell", "alert"], type: "select" },
    { id: "notify-decision-sound", label: "等待决策", hint: "章节检查点或守卫流程需要人工处理时播放", initial: "bell", options: ["chime", "bell", "alert"], type: "select" },
    { id: "notify-failure-sound", label: "任务失败", hint: "非用户取消的任务失败时播放", initial: "alert", options: ["chime", "bell", "alert"], type: "select" },
  ] } },
  { kind: "section", section: { title: "资料检索 — 路由与检索参数", description: "只控制长篇立项的资料检索阶段；创作模型仍走任务路由。", fields: [
    { id: "research-enabled", label: "默认启用长篇立项资料检索", hint: "单次立项仍可在表单中临时关闭", initial: "false", options: ["true", "false"], type: "select" },
    { id: "research-default-provider", label: "默认检索后端", hint: "noop=显式不联网，auto=按环境变量解析", initial: "auto", options: ["auto", "noop", "Tavily", "Brave", "SearXNG", "HTTP JSON", "MCP Search"], type: "select" },
    { id: "research-http-endpoint", label: "HTTP Endpoint", hint: "SearXNG/http_json/百炼需要填写；官方后端可留空", initial: "", type: "text" },
    { id: "outline-research-grounding-notes-per-chapter", label: "每章提醒数上限", hint: "大纲资料校准为每章保留的提醒/风险条数上限；0=仅保留全局提醒", initial: "3", min: 0, type: "number" },
    { id: "research-timeout-s", label: "请求超时秒数", hint: "每次 provider 请求的超时时间；网络慢时可调高", initial: "10", min: 0, type: "number" },
    { id: "research-max-queries", label: "查询数上限", hint: "从规格中规划出的最大查询条数；推荐 2-4", initial: "3", min: 0, type: "number" },
    { id: "research-query-max-parallel", label: "查询并发数", hint: "同时发起的检索查询上限；结果按计划查询序合并，去重/截断与串行一致；推荐 2-3", initial: "2", min: 1, max: 8, type: "number" },
    { id: "research-use-llm-planning", label: "LLM 查询规划", hint: "先用当前模型路由分析规格并规划 queries；失败时自动回退到规则查询", initial: "true", options: ["true", "false"], type: "select" },
    { id: "research-model-prior-enabled", label: "模型先验知识", hint: "在联网检索之外合成模型先验笔记，补充来源缺失的主题背景", initial: "false", options: ["true", "false"], type: "select" },
    { id: "research-results-per-query", label: "单查询结果数", hint: "每条 query 向 provider 请求的 top_k；推荐 3-8", initial: "5", min: 0, type: "number" },
    { id: "research-max-results", label: "报告来源上限", hint: "去重和过滤后保留到报告与 prompt 摘要的来源数", initial: "5", min: 0, type: "number" },
    { id: "research-retry-attempts", label: "失败重试次数", hint: "每条 query 失败后的额外重试次数；0=不重试", initial: "1", min: 0, type: "number" },
    { id: "research-search-depth", label: "搜索深度", hint: "Tavily 等 provider 支持时生效；其他 provider 会作为兼容字段传递", initial: "basic", type: "text" },
    { id: "research-api-key", label: "API Key", hint: "Tavily/Brave/HTTP/MCP 共用密钥；MiniMax 预设也填这里，再按下方 Key Env 注入", initial: "", type: "text" },
    { id: "research-locale", label: "语言/地区", hint: "可选，例如 zh-CN、en-US；SearXNG/Brave 等 provider 支持时生效", initial: "", type: "text" },
    { id: "research-include-domains", label: "域名 allowlist", hint: "逗号分隔；填写后仅保留匹配域名及其子域名结果", initial: "", type: "text" },
    { id: "research-exclude-domains", label: "域名 blocklist", hint: "逗号分隔；用于屏蔽低质量站点或不想引用的来源", initial: "", type: "text" },
    { id: "research-dossier-max-sources", label: "资料包来源上限", hint: "送入资料分析模型的最大来源数；越高越完整但更慢更贵", initial: "8", min: 0, type: "number" },
    { id: "research-mcp-command", label: "MCP Command", hint: "启动 MCP Server 的命令，例如 uvx 或 npx", initial: "", type: "text" },
    { id: "research-mcp-args-json", label: "MCP Args JSON", hint: "tools/call 前的启动参数 JSON；支持 {query} 占位符", initial: "", type: "text" },
    { id: "research-mcp-env-json", label: "MCP Env JSON", hint: "MCP Server 需要注入的环境变量 JSON", initial: "", type: "text" },
    { id: "research-mcp-api-key-env", label: "API Key Env", hint: "MCP Server 接收 API Key 的环境变量名；MiniMax=MINIMAX_API_KEY，留空则不自动注入", initial: "MINIMAX_API_KEY", type: "text" },
    { id: "research-mcp-protocol-version", label: "MCP Protocol", hint: "initialize 使用的协议版本；默认 2024-11-05", initial: "2024-11-05", type: "text" },
    { id: "research-mcp-stdio-framing", label: "stdio Framing", hint: "官方 MCP stdio 使用 newline；content_length 仅用于旧本地工具兼容", initial: "newline", options: ["newline", "content_length"], type: "select" },
    { id: "research-mcp-tool-name", label: "Search Tool", hint: "可选：精确指定 MCP 工具名；留空时自动选择 web_search/search", initial: "", type: "text" },
    { id: "research-mcp-query-argument", label: "Query Argument", hint: "未设置参数模板时用于传入查询词的字段名；默认 query", initial: "query", type: "text" },
    { id: "research-mcp-tool-arguments-json", label: "Tool Args JSON", hint: "可选：tools/call 参数模板，支持 {query} 和 {results_per_query} 占位符", initial: "", type: "text" },
  ] } },
  { kind: "section", section: { title: "存储、日志与格式修复", description: "控制运行日志、超时和格式修复的基础边界。", fields: [
    { id: "log-level", label: "日志级别", hint: "保存后对后续任务生效", initial: "INFO", options: ["DEBUG", "INFO", "WARNING", "ERROR"], type: "select" },
    { id: "log-keep-runs", label: "保留运行记录", hint: "按项目保留最近的运行记录数量", initial: "20", min: 1, max: 1000, type: "number" },
    { id: "api-timeout", label: "API 超时秒数", hint: "慢模型或复杂步骤可适当提高", initial: "900", min: 30, max: 3600, type: "number" },
    { id: "storage-format-repair-enabled", label: "格式修复", hint: "开启后自动修复章节格式问题", initial: "true", options: ["true", "false"], type: "select" },
    { id: "storage-format-repair-max-attempts", label: "格式修复最大尝试", hint: "单章格式修复的最大重试次数", initial: "2", min: 1, max: 5, type: "number" },
    { id: "llm-format-repair-max-tokens", label: "格式修复 Token", hint: "专用修复 LLM 的最大输出 token（推荐 4096-8192）", initial: "4096", min: 0, type: "number" },
    { id: "llm-format-repair-raw-char-limit", label: "修复原文字符上限", hint: "发送给专用修复 LLM 的错误原文上限；过低可能丢失尾部字段", initial: "60000", min: 0, type: "number" },
    { id: "llm-format-retry-raw-char-limit", label: "重试错误原文上限", hint: "普通格式重试时注入给原任务模型的上一轮错误输出字符预算", initial: "6000", min: 0, type: "number" },
    { id: "llm-format-retry-temperature", label: "格式重试温度", hint: "格式重试时使用的低温度；越低越稳定（推荐 0.0）", initial: "0", min: 0, type: "number" },
    { id: "story-kernel-db-path", label: "数据库文件位置", hint: "留空=自动使用项目目录；也可填普通 .db 文件路径", initial: "", type: "text" },
    { id: "story-kernel-wal-mode", label: "SQLite WAL 模式", hint: "开启后提升读写并发稳定性；建议保持 true", initial: "true", options: ["true", "false"], type: "select" },
    { id: "story-kernel-zvec-enabled", label: "ZVec 语义增强", hint: "为故事内核字段启用可选语义检索层；仅在安装 zvec 并需要语义召回时开启", initial: "false", options: ["false", "true"], type: "select" },
  ] } },
  { kind: "section", section: { title: "拟人化库 — 仪表板与维护", description: "拟人化库用于去除 AI 味、增强文本自然度。", fields: [
    { id: "humanize-library-enabled", label: "启用拟人化库", hint: "基于优秀段落示例库的改写能力", initial: "true", options: ["true", "false"], type: "select" },
    { id: "humanize-library-top-k", label: "相似段落检索数", hint: "检索时返回的最相似段落数量", initial: "30", min: 1, max: 100, type: "number" },
    { id: "humanize-library-path", label: "示例库路径", hint: "留空使用默认 {storage_root}/_global/humanize_library", initial: "", type: "text" },
    { id: "humanize-library-seed-builtin", label: "内置示例种子", hint: "是否用内置示例段落初始化示例库", initial: "true", options: ["true", "false"], type: "select" },
    { id: "humanize-model", label: "拟人化模型覆盖", hint: "provider:model 覆盖；留空复用标准模型路由", initial: "", type: "text" },
  ] } },
  { kind: "section", section: { title: "任务错误档案 — 保留与清空", description: "死信队列保留失败任务的诊断信息，便于回溯。", fields: [
    { id: "dlq-max-entries", label: "最大保留条数", hint: "超出后按时间淘汰最旧记录", initial: "100", min: 10, max: 5000, type: "number" },
  ] } },
  { kind: "heading", title: "初始化一致性裁判", description: "控制立项阶段的一致性 Claims 抽取、裁判、修复与准入。" },
  { kind: "section", section: { title: "一致性 Claims 抽取", description: "抽取蓝图、章纲和契约中的可裁判叙事声明。", fields: [
    { id: "init-coh-use-memory", label: "使用记忆召回", hint: "在结构化索引之外启用语义向量召回相似叙事事实", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-coh-claim-batch-size", label: "Claims 批大小", hint: "每批抽取 claims 的初始最大章节数", initial: "8", min: 1, max: 50, type: "number" },
    { id: "init-coh-claim-payload-budget", label: "Claims 载荷预算", hint: "单块输入 payload 字符预算；超出后自动缩小章节范围", initial: "18000", min: 1000, max: 200000, type: "number" },
    { id: "init-coh-claim-max-parallel", label: "Claims 并发", hint: "同时抽取 claims 的最大分块数；2 最稳", initial: "2", min: 1, max: 8, type: "number" },
    { id: "init-coh-overlap-chapters", label: "重叠章节", hint: "分批抽取时注入的前后章节上下文窗口", initial: "2", min: 0, max: 12, type: "number" },
    { id: "init-coh-semantic-top-k", label: "语义召回 TopK", hint: "每条 claim 从记忆中召回的相似事实数量", initial: "12", min: 0, max: 50, type: "number" },
    { id: "init-blueprint-holistic-claims", label: "蓝图整体一致性 Claims", hint: "完整阅读叙事蓝图补充跨字段一致性 claims", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-blueprint-holistic-claim-tokens", label: "蓝图整体一致性 Claims Token", hint: "完整蓝图一致性 Claims 抽取的基础输出 token 上限；越高越稳但更慢", initial: "4096", min: 0, type: "number" },
    { id: "init-claim-coverage-enabled", label: "Claims 覆盖审计", hint: "章节契约通过后，检查高价值 Claims 是否已被最终契约吸收；不进入章节 prompt", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-claim-coverage-block-p0", label: "P0 未覆盖阻断", hint: "P0 Claim 未出现在章节契约中时阻断初始化准入", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-claim-coverage-block-p1", label: "P1 未覆盖阻断", hint: "P1 Claim 未覆盖时默认只告警；开发调试可开启阻断", initial: "false", options: ["true", "false"], type: "select" },
    { id: "init-claim-coverage-block-degraded", label: "Claims 账本缺失阻断", hint: "Claims 账本缺失或损坏时默认阻断初始化准入，避免契约覆盖假通过", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-stream-claim-prefetch", label: "流式预取", hint: "章节大纲每批完成后立即预取一致性 Claims", initial: "true", options: ["true", "false"], type: "select" },
  ] } },
  { kind: "section", section: { title: "一致性裁判与修复", description: "控制候选冲突裁判、局部修复、复查窗口和初始化阻断阈值。", fields: [
    { id: "init-coh-candidate-max", label: "候选上限", hint: "每个初始化阶段最多送入 LLM 裁判的候选冲突数", initial: "80", min: 1, max: 500, type: "number" },
    { id: "init-coh-llm-batch", label: "裁判批大小", hint: "每次 LLM 候选冲突裁判的候选组数量", initial: "8", min: 1, max: 50, type: "number" },
    { id: "init-coh-llm-max-parallel", label: "裁判并发", hint: "同时进行候选冲突裁判的最大批次数", initial: "2", min: 1, max: 8, type: "number" },
    { id: "init-coh-confidence", label: "置信阈值", hint: "记忆语义召回采用的默认相似度阈值", initial: "0.75", min: 0, max: 1, step: 0.05, type: "number" },
    { id: "init-coh-auto-repair", label: "LLM 局部修复", hint: "裁判给出可定位 scope 时自动生成受限 JSON Patch", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-coh-repair-rounds", label: "修复轮次", hint: "每层 artifact 自动修复最大轮次（推荐 2）", initial: "2", min: 0, max: 3, type: "number" },
    { id: "init-coh-block-severity", label: "阻断严重度", hint: "达到该严重度的裁判问题会阻断或触发修复", initial: "high", options: ["high", "critical", "medium", "low"], type: "select" },
    { id: "init-coh-deterministic-repair", label: "确定性元数据修复", hint: "先用本地规则修复章节归属、伏笔时序、重复 payoff_id 等结构化问题", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-coh-patch-max-ops", label: "Patch 上限", hint: "一次初始化局部修复最多允许的 JSON Patch 操作数", initial: "40", min: 0, type: "number" },
    { id: "init-coh-recheck-window", label: "复查窗口", hint: "局部修复后重查受影响章节前后的窗口大小", initial: "2", min: 0, type: "number" },
    { id: "init-coh-target-patch-batch-size", label: "Target 批次", hint: "一次初始化精准修复最多下发给模型的定位目标数", initial: "12", min: 0, type: "number" },
  ] } },
  { kind: "section", section: { title: "初始化协议 — 连续性基线", description: "控制 bridge 回声窗口、转场/POV/时间制式等初始化连续性协议。", fields: [
    { id: "init-cont-protocol-enabled", label: "启用初始化协议", hint: "是否在初始化阶段注入连贯性协议到 bridge/plan/draft", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-cont-bridge-min", label: "桥接窗口下限（字）", hint: "动态窗口下限（推荐 400-700）", initial: "450", min: 0, type: "number" },
    { id: "init-cont-bridge-max", label: "桥接窗口上限（字）", hint: "动态窗口上限（推荐 1000-1800）", initial: "1400", min: 0, type: "number" },
    { id: "init-cont-bridge-ratio", label: "桥接窗口比例", hint: "bridge 回声窗口 = 每章字数 × 比例（推荐 0.20-0.35）", initial: "0.28", min: 0, type: "number" },
    { id: "init-cont-loc-required", label: "地点转场强制", hint: "地点变化时是否强制要求开场交代位移动作链", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-cont-loc-window", label: "转场句窗（句）", hint: "地点变化时，开场前 N 句需出现位移动作链（推荐 2-4）", initial: "3", min: 0, type: "number" },
    { id: "init-cont-pov-rule", label: "POV 可见性规则", hint: "注入 bridge/plan/draft 的 POV 限制文本", initial: "限知视角仅描写可观察事实，禁止直接写非POV角色内心。", type: "text" },
    { id: "init-cont-time-profile", label: "时间制式", hint: "auto=按语言自动；traditional_cn=传统时辰刻度；locale_default=本地常规时间", initial: "auto", options: ["auto", "traditional_cn", "locale_default"], type: "select" },
    { id: "init-cont-time-ke-range", label: "传统时辰刻度范围", hint: "仅 traditional_cn 生效，默认一至四刻", initial: "一至四刻", type: "text" },
    { id: "init-cont-max-reveals", label: "单章揭示上限", hint: "初始化协议中的单章重大揭示预算（推荐 1-3）", initial: "2", min: 0, type: "number" },
    { id: "init-cont-min-unresolved", label: "最少未决线索", hint: "初始化协议中的悬念留存预算（0=不强制）", initial: "1", min: 0, type: "number" },
    { id: "init-creative-refinement-enabled", label: "保守创意增强", hint: "蓝图审查前可选低风险增强；默认关闭", initial: "false", options: ["true", "false"], type: "select" },
    { id: "init-creative-refinement-auto-apply", label: "自动应用低风险增强", hint: "仅应用带证据和 scope 的 low risk patch", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-disable-local-story-fallbacks", label: "禁用本地剧情兜底", hint: "只保留通用安全校验，题材/剧情判断交给 LLM 裁判", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-readiness-required", label: "准入报告必需", hint: "章节生成前必须检查 init_readiness.json 是否允许继续", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-source-artifact-auto-repair", label: "源头准入自愈", hint: "source_artifacts 准入发现可定位问题时，自动尝试受限修复并复验", initial: "true", options: ["true", "false"], type: "select" },
    { id: "init-source-artifact-repair-rounds", label: "源头自愈轮次", hint: "source_artifacts 准入自动修复最大轮次；0=只报告不修复", initial: "2", min: 0, type: "number" },
  ] } },
];

const creationParameterDefaults = Object.fromEntries(
  blocks.flatMap((block) =>
    block.kind === "section"
      ? block.section.fields.map((field) => [field.id, field.initial] as const)
      : [],
  ),
);

/**
 * Stable field-id -> default-value map for the fire-tune parameter forms.
 * Exported so tests can assert that every field id is echoed back by the
 * engine settings projection (see generated-settings.fixture.ts).
 */
export const creationParameterDefaultsById: Readonly<Record<string, string>> = creationParameterDefaults;

function visibleCreationParameters(
  values: Readonly<Record<string, string>> | undefined,
): Record<string, string> {
  if (!values) return {};
  return Object.fromEntries(
    Object.entries(values).filter(([fieldId]) => fieldId in creationParameterDefaults),
  );
}

export interface CreationParameterSectionsProps {
  readonly initialValues?: Readonly<Record<string, string>> | undefined;
  readonly onSettingsChange: () => void;
  readonly valuesRef?: React.MutableRefObject<Record<string, string>> | undefined;
}

/**
 * Source-backed continuation of SettingsPage's long parameter stack.  The
 * forms keep their own draft values and expose them via valuesRef for the
 * page-level save control to include in SaveSettingsCommand.creationParameters.
 */
export function CreationParameterSections({ initialValues, onSettingsChange, valuesRef }: CreationParameterSectionsProps) {
  const [expandedTitles, setExpandedTitles] = useState<ReadonlySet<string>>(() => new Set());
  const [values, setValues] = useState<Record<string, string>>(() => {
    return { ...creationParameterDefaults, ...visibleCreationParameters(initialValues) };
  });

  // Keep valuesRef in sync so parent can read current values at save time
  useEffect(() => {
    if (valuesRef) valuesRef.current = values;
  }, [values, valuesRef]);

  // Re-seed when initialValues arrive asynchronously (e.g. backend fetch)
  const prevInitialRef = useRef(initialValues);
  useEffect(() => {
    if (initialValues && initialValues !== prevInitialRef.current) {
      prevInitialRef.current = initialValues;
      // The Engine projection is authoritative after a save/reload. Replacing
      // the complete form state also removes write-only secrets from the DOM
      // and restores defaults for overrides that the user explicitly cleared.
      setValues({ ...creationParameterDefaults, ...visibleCreationParameters(initialValues) });
    }
  }, [initialValues]);

  const updateValue = (fieldId: string, value: string) => {
    setValues((current) => ({ ...current, [fieldId]: value }));
    onSettingsChange();
  };

  return <div className="creation-parameter-sections">
    {blocks.map((block) => block.kind === "heading"
      ? <section className="settings-section-heading creation-parameter-heading" key={block.title}><h2>{block.title}</h2><p>{block.description}</p></section>
      : <ParameterAccordion expanded={expandedTitles.has(block.section.title)} key={block.section.title} onToggle={() => setExpandedTitles((current) => {
        const next = new Set(current);
        if (next.has(block.section.title)) next.delete(block.section.title);
        else next.add(block.section.title);
        return next;
      })} section={block.section} values={values} onValueChange={updateValue} />)}
  </div>;
}

function ParameterAccordion({ expanded, onToggle, onValueChange, section, values }: { readonly expanded: boolean; readonly onToggle: () => void; readonly onValueChange: (fieldId: string, value: string) => void; readonly section: ParameterSection; readonly values: Readonly<Record<string, string>> }) {
  return <section className={expanded ? "parameter-accordion is-expanded" : "parameter-accordion"}>
    <button aria-expanded={expanded} className="settings-accordion-toggle parameter-accordion-toggle" onClick={onToggle} type="button"><span>{expanded ? "⌄" : "›"}</span>{section.title}</button>
    {expanded && <div className="parameter-accordion-body">
      {section.description && <p className="parameter-accordion-description">{section.description}</p>}
      {section.fields.map((field) => <label className="parameter-field" key={field.id}>
        <span><strong>{field.label}</strong><small>{field.hint}</small></span>
        {field.type === "select"
          ? <select aria-label={field.label} onChange={(event) => onValueChange(field.id, event.target.value)} value={values[field.id] ?? field.initial}>{field.options?.map((option) => <option key={option} value={option}>{option}</option>)}</select>
          : <input aria-label={field.label} max={field.max} min={field.min} onChange={(event) => onValueChange(field.id, event.target.value)} step={field.step} type={field.type} value={values[field.id] ?? field.initial} />}
      </label>)}
    </div>}
  </section>;
}
