# 03 小说模块 — 检查修复族（48 个模板）

> **分析基准**：以运行时目录 `packs/zh/templates/` 为准；本族 22/48 模板与旧副本分叉（G-13），均为小幅增强（如 check_chapter +9 行、continuity_eval +9 行、humanize_scan +16 行），不推翻「通过/无」结论
> 链路：`CHECK_CHAPTER → CHECK_ALIGNMENT → CHECK_CONTINUITY/REPAIR_CONTINUITY → VALIDATE_CAUSAL/REPAIR_CAUSAL → EVALUATE_READING_POWER/REPAIR_READING_POWER → HUMANIZE_SCAN/REPAIR → 提取与审计`
> 契约模式：JSON（FULL_OBJECT 为主，repair 类 TEXT_ONLY）；温度：检查类 0.0-0.2（`quality_checks_runner.py:1147` 0.1、`editorial_check_step.py:66` 0.2、`humanize_scan_step.py:642` 0.2、`repair_strategy_advisor.py:137` 0.2）
> 本族 48 个模板（46 个注册任务 + 2 个共享宏 `_causal_core`/`_continuity_core`）按 7 个子族分析；同构模板合并条目

## 子族 A：章节检查（check_chapter / check_editorial / check_alignment）

### A1. checking/check_chapter.j2（CHECK_CHAPTER）
- **元信息**：FULL_OBJECT 8 键（risk_level/summary/prompt_leaks/factual_errors/continuity_errors/expression_errors/repair_actions/forbidden_element_findings）；check_mode 支持 full/delta 双模式
- **诊断**：
  - 【质量】8 个检查域划分清晰，且显式声明「跨章边界/因果链/护栏由对应模块负责，本项不重复审查」——职责边界声明是防重复修复的关键设计；「创意合同落地（P2）」把 emotional_beat/sensory_focus/dialogue_subtext 作为可感知验收项，直接支撑质量目标
  - 【稳定】delta 模式「上一次报告默认仍然成立，未修复问题继续保留」+「保守保留」规则——防修复后误删旧问题，稳定性设计优秀 ✓
  - 【格式】`forbidden_element_findings[].verdict` 枚举 6 值（violation/mechanical_reuse/acceptable_callback/story_anchor/benign/uncertain）与下游裁决链对齐；`factual_errors` 等数组项要求完整对象（issue_type/severity/summary/evidence/fix_suggestion）✓
- **建议**：无（核对通过，标杆模板）
- **优先级**：无

### A2. checking/check_editorial.j2（CHECK_EDITORIAL）
- **元信息**：FULL_OBJECT 4 键（summary/findings/revision_plan/metrics）；json_schema `_EDITORIAL_AUDIT_SCHEMA`；温度 0.2
- **诊断**：
  - 【质量】「本地预检只是候选信号，必须结合正文判断」——防机械命中即判错；「不建议全章重写，优先局部删改」——创造力保护内建 ✓
  - 【稳定】6 条编号规则聚焦可执行修订建议（「不要只写加强/优化」）✓
- **建议**：无
- **优先级**：无

### A3. checking/check_alignment.j2（CHECK_ALIGNMENT）
- **元信息**：FULL_OBJECT（alignment_score/risk_level/missing_main_points/supportive_subplot_points/weak_subplot_points/repair_actions）；risk_level 枚举 low/medium/high
- **诊断**：
  - 【质量】判定原则 9 条 + 修复建议风格 3 条：「语义覆盖即覆盖」「不因大纲之外的人物视角扣分」「优先微调修复而非推翻重写」「禁止删除所有支线的僵硬建议」——这是全仓库创造力保护最明确的模板 ✓
  - 【格式】`missing_main_points` 与 summary/risk_level 的一致性约束（「summary 说缺失数组必须列出」）防自相矛盾输出 ✓
- **建议**：无
- **优先级**：无

## 子族 B：连贯性与因果（continuity_eval/repair、causal_validate/repair、_core 宏）

### B1. checking/continuity_eval.j2（CHECK_CONTINUITY）
- **元信息**：FULL_OBJECT 2 键（continuity_score/issues）；温度 0.1
- **诊断**：
  - 【稳定】6 条编号规则全为「修复后复检」语义（逐条核实/回归检查/不扩大范围/禁止自创新问题/严重程度一致/身份保持）——这是「复检模式」的完整规范，与 check_chapter 的 delta 模式呼应；「禁止自创新问题」防复检发散 ✓
  - 【质量】「不扩大范围」说明模板明确区分「复检」与「全量评估」职责 ✓
- **建议**：无
- **优先级**：无

### B2. checking/continuity_repair.j2（REPAIR_CONTINUITY，TEXT_ONLY）
- **诊断**：
  - 【格式】TEXT_ONLY 输出整章正文；模板已限定只处理 `repair_surface=chapter_text/manual` 的 ticket，并明确“只修 ticket 与文本窗口，不自由重写整章”，禁止借契约或宏观规划扩写剧情 ✓
- **建议**：无（核对通过）
- **优先级**：无

### B3. checking/_continuity_core.j2（共享宏）
- **诊断**：213 行核心宏（被 continuity_eval/repair 引用）；无编号条目（宏定义）；是 continuity 判断标准的单一事实源——宏化设计正确 ✓
- **建议**：无
- **优先级**：无

### B4. checking/causal_validate.j2（VALIDATE_CAUSAL）
- **元信息**：FULL_OBJECT 2 键（causal_score/issues）
- **诊断**：
  - 【稳定】与 continuity_eval 同构的 6 条复检规则（逐条核实/回归/不扩大/禁止自创新/严重度一致/身份保持）——复检规范族统一 ✓；「修复引入直接相关新问题仅报告 critical/high」有明确严重度门槛
- **建议**：无
- **优先级**：无

### B5. checking/causal_repair_typed.j2（REPAIR_CAUSAL，TEXT_ONLY）
- **元信息**：类型化修复（opening_causal_gap/missing_causal_transition/event_without_cause/unmotivated_decision/question_resolved_too_early/question_ignored 六类）
- **诊断**：
  - 【质量】按缺陷类型给修复策略（每类「只改必要段落/补 1-3 句/不改变决策本身」）——类型化修复是稳定性最优形态；第 8 条字数区间 + 第 9 条禁令（禁止新增计划外 POV/后续章节揭秘）边界完整 ✓
- **建议**：无
- **优先级**：无

### B6. checking/_causal_core.j2（共享宏）
- **诊断**：147 行因果判定宏；无编号异常
- **建议**：无
- **优先级**：无

## 子族 C：阅读力与追读力（evaluate_reading_power / repair_reading_power）

### C1. checking/evaluate_reading_power.j2（EVALUATE_READING_POWER）
- **元信息**：FULL_OBJECT 11 个 required 键（hook_type/hook_strength/hook_description/prev_hook_fulfilled/micro_payoffs/is_transition/next_chapter_reason/information_pacing/main_plot_depth/tension_match/character_drive）；其余诊断字段可选，json_schema 完整
- **诊断**：
  - 【质量】阅读力维度拆解（钩子/微兑现/信息节奏/主线深度/张力匹配/角色驱动）是追读力度量的正确模型；字段全部可机检（枚举/数值）
  - 【格式】11 个 required 键与 schema 对齐；`HookType` 和模板枚举均为 crisis/mystery/emotion/choice/desire/none，阅读力修复按这些类别消费 ✓
- **建议**：无（核对通过）
- **优先级**：无

### C2. checking/repair_reading_power.j2（REPAIR_READING_POWER，TEXT_ONLY）
- **诊断**：
  - 【质量】12 条编号规则按缺陷类型（prev_hook_unfulfilled/payoff_missing/hook_missing/hook_too_weak/outline_mismatch/information_pacing_slow/stagnant/main_plot_surface/stalled/tension_depressed/character_drive_weak/revelation_over_budget）给定向修复策略——类型化修复，与 C1 枚举映射已核对 ✓
  - 【稳定】第 11 条禁令（禁止新增计划外 POV/禁出角色/后续章节答案/注水）+ 字数区间——边界完整
- **建议**：无
- **优先级**：无

## 子族 D：拟人化与护栏（humanize_scan / humanize_paragraph_rewrite / guardrail_repair / repair_semantic_verify / macro_guard_audit / plot_guard_judge / guard_constraint_check / repair_strategy_diagnose）

### D1. checking/humanize_scan.j2（HUMANIZE_SCAN）
- **元信息**：FULL_OBJECT 5 键（total_hits/hits_by_category/critical_hits/pattern_hits/summary）；**require_native_structured_output=True**
- **诊断**：
  - 【格式】原生结构化输出硬依赖：无 provider 支持时任务不可用（G-04）；每条 `pattern_hits` 已要求唯一的 `evidence_quote` 及原样复制的 0-based `paragraph_index` / `span_start` / `span_end`，可安全定位给 rewrite 消费 ✓
  - 【质量】AI 味扫描是反 AI 味体系的前端，critical_hits 分级正确
- **建议**：保持 native 结构化路由要求；若需本地模型支持，作为独立架构项设计降级语义
- **优先级**：中（架构决策）

### D2. checking/humanize_paragraph_rewrite.j2（HUMANIZE_PARAGRAPH_REWRITE，TEXT_ONLY）
- **诊断**：
  - 【稳定】段落级重写（TEXT_ONLY 输出重写后段落）；模板规定按指定段落顺序输出、段落间一个空行，禁止段落编号、标题和解释性文字，输出边界明确 ✓
- **建议**：无（核对通过）
- **优先级**：无

### D3. checking/guardrail_repair.j2（REPAIR_GUARDRAIL，TEXT_ONLY）
- **诊断**：
  - 【质量】4 条规则（完整正文输出/保持段落结构/主线推进点原样保留/修复后自检）——「完整正文」语义明确，防碎片输出 ✓
- **建议**：无
- **优先级**：无

### D4. checking/repair_semantic_verify.j2（REPAIR_SEMANTIC_VERIFY）
- **元信息**：FULL_OBJECT 3 键（issue_resolved/confidence/reasoning）
- **诊断**：3 条核验问题（矛盾消除/新问题引入/上下文一致）——修复后验证的最小完备集 ✓
- **建议**：无
- **优先级**：无

### D5. checking/macro_guard_audit.j2（MACRO_GUARD_AUDIT）
- **元信息**：FULL_OBJECT 7 键（dimensions/recommended_action/drift_score/findings/adjustment_plan/confidence/reasoning）
- **诊断**：
  - 【质量】5 个宏观维度（outline_alignment/character_arc_consistency/pacing_curve/foreshadowing_recovery/thematic_cohesion）+ 3 档动作（pass/warning_hint/alert_plan）——宏观守护的完整模型；编号 1-5 与 1-3 分区重复（G-06 类，轻微）
  - 【稳定】「连续多章偏离同一节点才 alert_plan」——阈值防误报 ✓
- **建议**：编号分区改 6a/6b 式（低）
- **优先级**：低

### D6. checking/plot_guard_judge.j2（PLOT_GUARD_JUDGE）
- **元信息**：FULL_OBJECT 8 键（decision/risk_level/outline_action/entity_actions/next_chapter_constraints/reasoning_brief/targeted_repairs）
- **诊断**：
  - 【质量】4 个决策问题（是否继续/是否调大纲/新增元素去留/下章约束）+ 第 9 条「milestone_window.future_guardrails 命中时只判断本章是否提前兑现」——防越权裁决 ✓；编号 1-7 后跳 9（条件分支，轻微）
  - 【稳定】第 5 条「因果分 <6.0 不得给宽松结论」与第 7 条「可修复问题用 targeted_repairs 而非回滚」——「修复优先于回滚」原则 ✓
- **建议**：编号补 8（低）
- **优先级**：低

### D7. checking/guard_constraint_check.j2（GUARD_CONSTRAINT_CHECK）
- **元信息**：FULL_OBJECT 4 键（status/confidence/evidence/notes）；status 枚举 4 值
- **诊断**：
  - 【格式】evidence 必须「正文中连续出现的精确原文子串，不得改写、概括或拼接」——机检友好约束 ✓；「找不到精确违规子串时使用 unknown」防误判 ✓
  - 【稳定】「只评估这一条约束，不追加隐含要求」——单约束评估模式防扩散 ✓
- **建议**：无
- **优先级**：无

### D8. checking/repair_strategy_diagnose.j2（REPAIR_STRATEGY_DIAGNOSE）
- **元信息**：FULL_OBJECT 6 键（preferred_strategy/confidence/reason/root_causes/risk_flags/diagnostic_summary）；温度 0.2
- **诊断**：
  - 【稳定】patch/fulltext/rewrite 三策略选择规则（局部→patch、跨段→fulltext、结构问题→rewrite）——修复策略路由是 Repair Orchestration v2 的决策入口，规则明确 ✓
- **建议**：无
- **优先级**：无

### D9. checking/repair_adjudicated_issue.j2（REPAIR_ADJUDICATED_ISSUE，TEXT_ONLY）
- **元信息**：TEXT_ONLY；LLM 裁判驱动修复（adjudicated_issue 的 evidence_quotes/repair_instruction 为第一依据）
- **诊断**：
  - 【质量】「🔒 禁止修改的区域」三清单（未被指出的段落/未质疑的硬事实/已通过审核的内容）+「✅ 允许修改的范围」三清单（证据锚定段落/repair_instruction 要求/过渡段 ≤20%）——**修复边界的双清单模式是全仓库最严格的防扩散设计** ✓；「只输出被修改窗口中需要替换的段落，不输出说明或 JSON」——输出协议明确
  - 【格式】`repair_kind=mapping/路径修正/target_id 重组/状态槽位` 类问题「原样返回相关原文窗口，不改写正文」——职责分离（正文修复 vs 状态修复）✓；下游按窗口替换（与 patch 引擎定位协议兼容）
  - 【稳定】「若无法定位，返回最相关原文窗口而不是扩写」——防修复扩散的兜底 ✓
- **建议**：无（标杆模板）
- **优先级**：无
- **创造力影响**：中性（修复范围锁定，不影响创作授权）

### D10. checking/element_progress_arbiter.j2（ELEMENT_PROGRESS_ARBITER）
- **元信息**：FULL_OBJECT 3 键（status/confidence/reason）；status 枚举 hit/weak/miss
- **诊断**：
  - 【质量】「低频仲裁步骤，只用于规则灰区结果复核」——仲裁定位准确（不替代主评估）；「必须在 hit/weak/miss 三选一」+「reason ≤80 字可审计」——输出紧凑可机检 ✓；输入为计划/正文/质量反馈三摘录的对照仲裁——证据齐备 ✓
  - 【格式】契约 3 键与 schema 对齐；0.0-1.0 confidence 可机检 ✓
- **建议**：无
- **优先级**：无
- **创造力影响**：中性

## 子族 E：编辑契约派生（derive_editorial_* 5 个）

| 模板 | 契约 | 诊断摘要 | 优先级 |
|------|------|----------|--------|
| derive_editorial_contract.j2 | FULL_OBJECT 10 键 | 规则 1-11 后跳 13（编号卫生，G-06）；`signature_moves/taboo_patterns/sample_lines ≤5 条` 配额合理；覆盖角色/高潮/余波/主题/象征/阻力/表达预算/揭示阶梯/时间桥/标题策略——全书编辑契约的完整形态 | 中（编号） |
| derive_editorial_character_voices.j2 | FRAGMENT `character_voices` | 6 条规则：`character` 必须一字不差匹配角色设定 name——强引用约束防虚构角色 ✓；「样例台词禁止诱导正文复用」是创造力保护 ✓ | 无 |
| derive_editorial_structure.j2 | FRAGMENT 5 键 | climax_markers ≤5 且必含 main、revelation_ladder ≤8 级且每级带行动后果、time_bridge ≤7——数量上限明确 | 无 |
| derive_editorial_style_constraints.j2 | FRAGMENT 8 键 | 「expression_channel_profiles 只提取本项目最可能被机械复现的画像，不得泛化常见禁忌」——防通用模板化 ✓；forbidden_confirmation_phrases 防圆满确认重复 | 无 |
| derive_editorial_element_directives.j2 | FRAGMENT `editorial_element_directives` | ≤8 条、必须引用真实 element_id、不得发明新 ID——强引用约束 ✓ | 无 |

## 子族 F：Critic 与观察提取（critic_* 4 个 + extract_expression_observations + extract_knowledge_deltas + knowledge 相关 3 个）

### F1. checking/critic_continuity.j2（CRITIC_CONTINUITY）
- **诊断**：4 个检查点（开场承接/上章未决状态/人物失忆式跳变/规划术语混入）——批判性审查的最小集；FULL_OBJECT issues 输出
- **建议**：无 | **优先级**：无

### F2. checking/critic_character.j2（CRITIC_CHARACTER）
- **诊断**：prose 规则（无编号条目）；角色一致性批判——与 check_chapter 的 factual_errors 存在职责重叠（身份称谓两处查），建议确认边界声明
- **建议**：确认与 check_chapter 的边界（低） | **优先级**：低

### F3. checking/critic_causal.j2（CRITIC_CAUSAL）
- **诊断**：4 个因果断点检查（承接/凭空事件/决策动机/未决问题忽略）——与 causal_validate 的复检模式互补（critic 全量、validate 复检）✓
- **建议**：无 | **优先级**：无

### F4. checking/critic_strengths.j2（CRITIC_STRENGTHS）
- **诊断**：正向批判（strengths 输出）——「发现优势」任务在批评体系中罕见但重要（防修复损坏优质段落）；与 repair 的 preserve 清单联动 ✓
- **建议**：无 | **优先级**：无

### F5. checking/extract_expression_observations.j2（EXTRACT_EXPRESSION_OBSERVATIONS）
- **元信息**：FRAGMENT 3 键（observations/skipped_reason/source_text_hash）；温度 0.15
- **诊断**：表达通道观察提取（供表达通道冷却用）；`source_text_hash` 是防陈旧缓存的正确设计 ✓；skipped_reason 防空跑 ✓
- **建议**：无 | **优先级**：无

### F6. checking/extract_knowledge_deltas.j2（EXTRACT_KNOWLEDGE_DELTAS）
- **元信息**：FULL_OBJECT `knowledge_deltas`；json_schema
- **诊断**：6 条规则（只提取新增/避免重复/精确归属/区分 known-suspected-misbelief-secret_kept/合理判定可见性/宁缺毋滥）——知识边界提取的高质量约束；「宁缺毋滥」防知识污染 ✓
- **建议**：无 | **优先级**：无

### F7-F8. knowledge_boundary_audit / repair_knowledge_boundary / init_knowledge_boundaries
- **诊断**：知识边界审计（FULL_OBJECT verdict/issues）+ 修复（PARTIAL_OBJECT + json_schema）+ 初始化（FULL_OBJECT characters）三件套完整；`format_repair.py` 已有 PARTIAL 分支，明确要求只输出本轮新增/修改字段，宽松重试语义完整 ✓
- **建议**：无 | **优先级**：无

## 子族 G：书籍级审计（book_consistency* 9 个 + book_editorial_audit* 6 个 + summary_drift_check + volume_audit）

### G1. checking/book_consistency.j2（BOOK_CONSISTENCY + 7 个子任务共用）
- **元信息**：FULL_OBJECT 4 键（issues/repair_plan/summary/consistency_score）；8 个 TaskType 复用同一模板（维度注入）
- **诊断**：
  - 【质量】单模板多维度（naming/timeline/world_rule/character_state/plot_thread/narrative_drift）通过维度参数注入——复用设计节省维护成本 ✓；调用点始终设置 `active_audit_dimension`，模板也明确本次调用仅审该维度 ✓
  - 【稳定】编号 1–8 为“审计维度”目录，后续 1–7 为独立“定位与修复输出要求”清单，分节重新计数，非条件分支冲突 ✓
- **建议**：无（核对通过）
- **优先级**：无

### G2. checking/book_consistency_verify.j2（BOOK_CONSISTENCY_VERIFY）
- **元信息**：FULL_OBJECT `verified_issues`
- **诊断**：修复后复核协议（verified_issues 仅列仍存在的问题）——「复核只报残留」语义正确 ✓
- **建议**：无 | **优先级**：无

### G3-G8. checking/book_editorial_audit.j2（6 个 BOOK_EDITORIAL_*_AUDIT 共用）
- **元信息**：FULL_OBJECT 4 键（summary/findings/revision_plan/metrics）；json_schema `_EDITORIAL_AUDIT_SCHEMA`
- **诊断**：结构/声纹/语言/主题象征/要素 5 个维度 + 主审计共用一模板（维度注入）；7 条检查域（结构节奏/角色声纹/语言重复/主题象征/标题时间桥/支线收束/要素执行）质量高；与 check_editorial 存在 schema 同构（同一 `_EDITORIAL_AUDIT_SCHEMA`）——确认无职责冲突（check_editorial 单章、book_editorial 全书）
- **建议**：确认单章/全书边界声明（低） | **优先级**：低

### G9. checking/summary_drift_check.j2（SUMMARY_DRIFT_CHECK）
- **元信息**：FULL_OBJECT 3 键（issues/facts_checked/facts_missing/facts_contradicted）
- **诊断**：摘要漂移检测（facts_checked/missing/contradicted 三分）——摘要可信度维护 ✓；无标题（no headings 扫描标记）但短模板 57 行，影响小
- **建议**：无 | **优先级**：无

### G10. summary/volume_audit.j2（VOLUME_AUDIT）
- **元信息**：FULL_OBJECT 12 键（volume_number/volume_summary/milestone_status/carry_over_* 系列/next_volume 等）
- **诊断**：卷级交接协议（carry_over_characters/items/world_fact_keys/foreshadowing_ids 全量携带）——防卷间断裂的正确设计 ✓
- **建议**：无 | **优先级**：无

---

## 检查修复族优先级汇总表

| 模板 | 优先级 | 核心问题 | 建议动作 |
|------|--------|----------|----------|
| check_chapter.j2 | 无 | - | 通过 |
| check_editorial.j2 | 无 | - | 通过 |
| check_alignment.j2 | 无 | - | 通过 |
| continuity_eval.j2 | 无 | - | 通过 |
| continuity_repair.j2 | 无 | 核对通过（ticket/window 定向修复） | - |
| causal_validate.j2 | 无 | - | 通过 |
| causal_repair_typed.j2 | 无 | - | 通过 |
| evaluate_reading_power.j2 | 无 | 核对通过（11 required 键；HookType 一致） | - |
| repair_reading_power.j2 | 无 | 核对通过（类型化修复映射一致） | - |
| humanize_scan.j2 | 中 | 原生输出硬依赖 + hit 证据 | 确认降级 + 证据约束 |
| humanize_paragraph_rewrite.j2 | 无 | 核对通过（仅输出指定段落纯文本） | - |
| guardrail_repair.j2 | 无 | - | 通过 |
| repair_semantic_verify.j2 | 无 | - | 通过 |
| macro_guard_audit.j2 | 无 | 核对通过（审计维度与行动规则独立编号） | - |
| plot_guard_judge.j2 | 无 | 核对通过（裁决规则连续 1–9） | - |
| guard_constraint_check.j2 | 无 | - | 通过 |
| repair_strategy_diagnose.j2 | 无 | - | 通过 |
| repair_adjudicated_issue.j2 | 无 | - | 通过（修复边界双清单标杆） |
| element_progress_arbiter.j2 | 无 | - | 通过 |
| derive_editorial_contract.j2 | 无 | 核对通过（生成要求连续 1–13） | - |
| derive_editorial_*（4） | 无 | - | 通过 |
| critic_*（4） | 低 | critic_character 职责重叠 | 确认边界 |
| extract_expression_observations.j2 | 无 | - | 通过 |
| extract_knowledge_deltas.j2 | 无 | - | 通过 |
| knowledge_*（3） | 无 | 核对通过（PARTIAL 解析分支完整） | - |
| book_consistency.j2 | 无 | 核对通过（维度必注入；独立清单编号） | - |
| book_consistency_verify.j2 | 无 | - | 通过 |
| book_editorial_audit.j2 | 无 | 核对通过（单章与全书审计职责分离） | - |
| summary_drift_check.j2 | 无 | - | 通过 |
| volume_audit.j2 | 无 | - | 通过 |
