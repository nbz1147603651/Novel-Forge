# 06 共享宏与质量规则层（20 个模板）

> **分析基准**：以运行时目录 `packs/zh/templates/` 为准；**注意：运行时目录额外包含 3 个旧副本缺失的宏**（G-13）：`_cognitive_constraints.j2`（认知约束+角色知识覆盖渲染）、`_retrieval_evidence.j2`（Zvec 召回证据，authority 分级/时间边界/证据预算）、`_summary_pass.j2`（分层摘要 evidence_chunk/evidence_merge 两阶段）——这三个宏是新能力，旧副本缺失；`_artifact_source_contract`（+24 行）与 `_quality_calibration`（+1 行）也有增强
> 本层为提示词体系的「公共底座」。运行时目录中有 `_base/` 20 个、根级兼容层 2 个（`_quality_standards.j2`、`_role_definitions.j2`）和 `_styles/` 1 个；另有 `checking/` 下 2 个未注册共享宏，合计 25 个支持模板。
> 关键事实：宏在渲染时**展开**进调用方提示词（Jinja2 import 无惰性），同一宏被 20+ 模板复用 → 规则文本重复出现在每个提示词中，是 token 成本与注意力稀释的最大来源

## 1. _base/_quality_standards.j2（15 个宏，核心质量规则库）

- **元信息**：15 个宏：forbidden_system_markers / traditional_time_marker_rules / pronoun_consistency / self_reference_context_awareness / sensory_diversity / dialogue_density / dialogue_format / anti_exposition_rules / anti_meta_narrative_rules / creative_specificity_rules / webnovel_pacing_rules / imagery_repetition_ban / phrase_blacklist / paragraph_uniqueness / emotional_craft_rules
- **诊断**：
  - 【质量】这是全仓库反 AI 味的核心资产：
    - `anti_exposition_rules` 的 dramatization_rule 带 ✗/✓ 对比（「她已识破酒中双重试探」✗ vs 「她端起杯，鼻尖微蹙」✓）——负面示例密度最高的宏
    - `creative_specificity_rules` 对「不是……而是……」定义式否定转折给出精确豁免（角色辩驳/事实纠偏/时间纠偏/关键悬置可用）——防止误伤合法用法 ✓
    - `phrase_blacklist` 默认 7 个模板化短语（深吸一口气/目光坚定/声音低沉/心中一震/微微一怔/嘴角微扬/眉头微皱）——「禁止超过 2 次」是配额式而非一刀切，创作自由度保留 ✓
    - `emotional_craft_rules`（反差出情感/留白即表达/情绪不叠加同构）——情感工艺是商业化写作的高阶引导
  - 【稳定】`imagery_repetition_ban` 的「自检规则：本书已出现 ≥2 次即换新」——可机检约束（依赖 memory 系统的意象计数）
  - 【成本/注意力】15 个宏全部展开进 draft/wave/edit 等大模板（G-12）：draft_chapter 同时引入 anti_exposition + creative_specificity + sensory_diversity + emotional_craft + dialogue_format + forbidden_system_markers + anti_meta_narrative + traditional_time_marker ≈ 8 个宏展开，估计 1500-2500 token 的规则文本，与模板自身 17 条规则叠加
- **建议**：
  1. 宏调用参数化裁剪：为 writing 阶段提供「精简版」宏（如 anti_exposition_rules 在 draft 只需 dramatization 子规则，标题/开场规则可省略）（中）
  2. 将 phrase_blacklist 与 imagery_repetition_ban 合并渲染（两者功能重叠）（低）
  3. 对 `_quality_standards.j2`（根级兼容层，20 行）做清理：内容已全部迁移到 `_base/`，保留仅为向后兼容——建议 6 个月后删除并改 import 路径（低）
- **优先级**：中（1）/ 低（2、3）
- **创造力影响**：中性

## 2. _base/_artifact_source_contract.j2（render_artifact_source_contract）

- **诊断**：渲染 source_artifact_id/source_hashes/权威边界/实体边界——「`cards.source` 是本章唯一初始化权威投影」的声明在 draft/wave/edit/polish/bridge/plan 六个模板中重复出现（每个模板都 import）；**这是提示词防「模型自行编造全书设定」的关键机制** ✓；source_hashes 哈希校验与 `tts_artifact_source_mismatch` 等运行时审计联动
- **建议**：无（设计正确）；可考虑对多模板重复的权威边界声明做压缩（低）
- **优先级**：无
- **创造力影响**：中性

## 3. _base/_calculate_word_constraints.j2（3 个宏）

- **诊断**：calculate_word_constraints（按 target 计算 min/max，±20% 浮动）/ render_word_constraint_hard / render_word_constraint_self_check——字数约束统一出口 ✓；「编辑完成后自检字数」的措辞与「硬约束」并存，hard 版本在 draft/wave 使用、self_check 在 edit/polish 使用，分层正确 ✓
- **建议**：无
- **优先级**：无

## 4. _base/_default_style.j2

- **诊断**：「执行提醒」段（对话是信息载体但不硬堆/转译 relationship_dynamics/每场 1-2 个细节/技术信息借对话带出）——被无 style_profile 的模板用作回退风格；「已有动作/对白足以承载信息时不额外补感官描写」防注水 ✓
- **建议**：无
- **优先级**：无

## 5. _base/_editorial_card.j2（render_editorial_card）

- **诊断**：编辑准入风险/角色声纹/象征物策略/揭示阶梯/要素指令/时间桥 六段渲染（mode 参数区分 draft/edit/polish 裁剪）——**mode 参数化是宏裁剪的正确范式**，可推广到其他宏（见 G-12 建议）✓
- **建议**：以本宏为模板，评估其他宏的 mode 裁剪（低）
- **优先级**：低

## 6. _base/_knowledge_card.j2（render_knowledge_card）

- **诊断**：「StoryKernel.knowledge_ledger 是角色知识唯一事实源；POV 内心/判断/对白/行动只能基于该角色已知信息」——认知边界机制（与 cognitive_constraints、POV 知识边界三件套）✓；「不得新增未由本章动作支撑的认知变化」——防知识幻觉 ✓
- **建议**：无
- **优先级**：无

## 7. _base/_narrative_contract.j2（render_narrative_contract）

- **诊断**：全局叙事契约渲染（时代语境/角色弧光/连贯性协议）——「★ 硬约束」标记；被初始化后章节消费 ✓
- **建议**：无
- **优先级**：无

## 8. _base/_narrative_state.j2（render_authoritative_state）

- **诊断**：「LLM 已裁定叙事状态（P0 最高事实源），只能执行不能被旧记忆覆盖」——状态权威层级声明 ✓；结构化状态路径渲染供 state_writer 消费
- **建议**：无
- **优先级**：无

## 9. _base/_pov_knowledge_constraints.j2

- **诊断**：按 scene 渲染「该角色不可能知道以下类别的信息」+「感知限制」——POV 知识边界（draft_scene 逐场渲染、draft_chapter 全章渲染）✓；「正文中绝不能出现这些知识的直接或间接表达」——强负向约束，格式为白名单式（无该宏的模板无此保护）
- **建议**：确认在 wave 模板中同样调用（wave_chapter.j2:168 有调用 ✓）
- **优先级**：无

## 10. _base/_quality_calibration.j2（2 个宏）

- **诊断**：render_quality_calibration（工作身份/创意推断路径/字段级质量示例/可操作路径）+ render_pre_delivery_checklist（交付前核验 4 问）——「字段级质量示例（平弱 vs 高质量）」是全仓库最有效的质量引导机制；被 draft/edit/polish/wave 等 8+ 模板复用
- **建议**：无（标杆宏）
- **优先级**：无

## 11. _base/_render_canon_characters.j2（render_canon_characters）

- **诊断**：角色状态表渲染（alive/位置/情绪）——供 check_chapter 等核对身份 ✓
- **建议**：无
- **优先级**：无

## 12. _base/_render_element_focus.j2（3 个宏）

- **诊断**：build_focus_cards / render_element_focus / render_element_focus_plan——叙事要素焦点卡渲染（plan 与 draft 两版）✓；「要素名称只用于幕后设计，正文必须转译成动作/对白/冲突/感官细节」的约束在 draft 模板中
- **建议**：无
- **优先级**：无

## 13. _base/_render_style_profile_global.j2（6 个宏）

- **诊断**：extract_global_style/gs_field/global_style_value/dialogue_ratio_min/max/render_style_profile_global——全局风格提取与对话比例参数化；`dialogue_ratio_min/max` 被 evaluate_draft 引用为 `_dlg_min/_dlg_max` ✓
- **建议**：无
- **优先级**：无

## 14. _base/_repair_attempt_guidance.j2

- **诊断**：修复轮次信息（round_number/max_rounds/avoid/失败原因/可借鉴策略/历史成功率）——修复策略回灌机制（Repair Orchestration v2 的「避免重复失败」通道）✓；「历史成功率」注入让模型选择高成功率策略——数据驱动提示词的正确形态
- **建议**：无
- **优先级**：无

## 15. _base/_repair_quality_calibration.j2

- **诊断**：修复版质量校准（「只改冲突句/缺口段，保留已验证的人物/事件/语气」）——与写作版 calibration 区分场景 ✓；「最小必要修复」原则防修复扩散
- **建议**：无
- **优先级**：无

## 16. _base/_role_definitions.j2

- **诊断**：check_chapter_role/check_continuity_role/validate_causal_role/check_alignment_role/responsibility_matrix——角色职责矩阵（「本章内部质量 vs 跨章连贯性」边界声明）——**职责边界体系是防多检查模块重复修复的关键** ✓
- **建议**：`responsibility_matrix` 未被运行时模板调用，保留为兼容宏接口；因其不渲染到模型上下文，无运行时 token 或行为影响。若未来执行兼容层清理，可一并移除。
- **优先级**：无

## 17. _base/_shared_evidence_anchor.j2（render_shared_evidence_anchor）

- **诊断**：共享证据锚点渲染（多维度评审的共同依据，维度特需信息只能补充不得覆盖）——多维检查一致性机制 ✓
- **建议**：无
- **优先级**：无

## 18. _base/_narrative_state.j2 / _base/_knowledge_card.j2 等（已并入上文）

## 19. 根级兼容层：_quality_standards.j2（20 行）/ _role_definitions.j2（5 行）

- **诊断**：两文件均为「重新导出 _base/」的兼容 shim（`{% import "_base/_quality_standards.j2" as _base_qs %}` 转发）——被 20+ 模板 import，保持兼容合理；但按 AGENTS.md「webnovel_style_guide.j2 shim 已删除」的先例，兼容 shim 有清理节奏
- **建议**：与 `_base/_quality_standards.j2` 的清理一同评估（低）
- **优先级**：低

## 20. _styles/_render_style_profile.j2（render_style_profile_for_stage）

- **诊断**：241 行风格档案渲染宏（stage 参数：edit/polish 等）——风格模块规则/禁用短语/对话比例的 stage 化输出；`include_banned_phrases` 开关控制禁用短语注入——宏参数化范式的又一实例 ✓
- **建议**：无
- **优先级**：无

---

## 共享宏层优先级汇总表

| 宏 | 优先级 | 核心问题 | 建议动作 |
|----|--------|----------|----------|
| _quality_standards.j2（15 宏） | 中 | 8 宏同时展开致 token 膨胀（G-12） | 按 stage 参数化精简版 |
| _artifact_source_contract.j2 | 无 | - | 通过 |
| _calculate_word_constraints.j2 | 无 | - | 通过 |
| _default_style.j2 | 无 | - | 通过 |
| _editorial_card.j2 | 低 | 可作宏裁剪范式推广 | 评估推广 |
| _knowledge_card.j2 | 无 | - | 通过 |
| _narrative_contract.j2 | 无 | - | 通过 |
| _narrative_state.j2 | 无 | - | 通过 |
| _pov_knowledge_constraints.j2 | 无 | - | 通过 |
| _quality_calibration.j2 | 无 | - | 通过（标杆） |
| _render_canon_characters.j2 | 无 | - | 通过 |
| _render_element_focus.j2 | 无 | - | 通过 |
| _render_style_profile_global.j2 | 无 | - | 通过 |
| _repair_attempt_guidance.j2 | 无 | - | 通过 |
| _repair_quality_calibration.j2 | 无 | - | 通过 |
| _role_definitions.j2 | 无 | 未调用的兼容宏不进入运行时 prompt | 随兼容层清理评估 |
| _shared_evidence_anchor.j2 | 无 | - | 通过 |
| 根级 shim（2） | 低 | 兼容层清理节奏 | 与 base 清理同批 |
| _styles/_render_style_profile.j2 | 无 | - | 通过 |
