# 02 小说模块 — 规划族（22 个模板）

> **分析基准**：以运行时目录 `packs/zh/templates/` 为准；本族 10/22 模板与旧副本 `prompts/prompts/` 分叉（G-13）：plan_chapter（+29 行：认知约束宏/世界规则强化）、plan_chapter_scenes（-4 行）、plan_chapter_contracts、subplot_polish（+13 行）、init_coherence_extraction_guide（+7 行）、adjudicate_*（小幅）——差异不推翻本族结论，标注见各条目
> 链路：`BLUEPRINT_ELEMENT_SELECT → PLAN_OUTLINE(→BATCH/CONTINUE) → PLAN_CHAPTER_CONTRACTS → PLAN_CHAPTER → PLAN_CHAPTER_SCENES → VALIDATE_SCENE_PLAN`；短篇：`SHORT_BLUEPRINT → BEATS`；大纲维护：`POLISH_OUTLINE / ADJUST_OUTLINE`；初始化研究：`PLAN_INIT_RESEARCH_QUERIES / GROUND_OUTLINE_RESEARCH / SYNTHESIZE_*`
> 契约模式：全部 JSON（FULL_OBJECT / FRAGMENT_OBJECT / PATCH_PLAN）；温度：规划类 0.0-0.2 区间（`planning.py:373` bridge 0.0、`scene_writing.py:685` VALIDATE_SCENE_PLAN 0.0、init_repair_execution 0.15）

## 1. planning/plan_chapter.j2（PLAN_CHAPTER）

- **元信息**：FULL_OBJECT JSON；required 16 顶层键（scene_intents/world_rule_applications/opening_contract/closing_contract/required_state_transitions/required_literals/chapter_type/emotional_arc/relationship_evolution/forbidden_elements/forbidden_elements_soft/forbidden_elements_quota/intentional_callbacks/foreshadowing_plan/key_revelations/cross_scene_intent）
- **诊断**：
  - 【质量】「规划规则」14 条 + 1a/3a 子条是全仓库质量最高的约束集之一：规则 12/13 明确「summary/required_outcome 必须是过程描述而非结果陈述」「dramatic_question 必须用疑问句」——直接服务于防剧透规划；字段级对比示例（✗/✓，5 组）是引导质量的标杆
  - 【稳定】规则 14「required_state_transitions 最多 3 条，超过拆场景」——主动防枚举式并列（与 draft 的枚举限制呼应），系统性强 ✓；规则 6「传统时辰一至四刻，严禁五刻以上」是可机检约束（下游 outline_tracker 可校验）
  - 【格式】`cross_scene_intent` 段（2 字段必出，ref_type 枚举 5 值、pacing_curve 长度=scene_intents）与 WAVE 消费协议完全一致 ✓；契约 required 16 键与模板输出段对齐 ✓
  - 【创造】规则 5「forbidden_elements 只放修辞意象，不放人物/地点/道具」防止误伤剧情锚点——边界正确
- **建议**：规则 1 的 26 个必填场景字段未在模板中分组展示（一行列出过长），建议按「身份/事件/表演/感官」四组重排（低）；其余无需改动
- **优先级**：低（已是标杆模板，仅格式排版优化）
- **创造力影响**：中性

## 2. planning/plan_chapter_scenes.j2（PLAN_CHAPTER_SCENES）

- **元信息**：FRAGMENT_OBJECT JSON；required `scene_plan`；`scene_plan` 细化为每场 26 字段
- **诊断**：
  - 【格式】FRAGMENT 模式与 `_PlanChapterScenesResponse` 对齐 ✓（**已核对**：`_PlanChapterScenesPayloadResponse` 与 `_PlanChapterResponse` 的 16 顶层字段集完全一致，scene_plan 内嵌 scene_intents——两任务是同一协议的不同批次组织，无字段漂移风险）
  - 【稳定】VALIDATE_SCENE_PLAN（0.0 温度）对 scene_plan 做并行组/串行边校验——下游有独立校验器 ✓
- **建议**：无（核对通过）
- **优先级**：无
- **创造力影响**：中性

## 3. planning/plan_chapter_contracts.j2（PLAN_CHAPTER_CONTRACTS）

- **元信息**：FULL_OBJECT JSON；required `chapter_contracts`；json_schema `_chapter_contracts_schema`
- **诊断**：
  - 【格式】schema 契约完整（含 cognitive_constraints/hard_facts/exit_state_targets 等 30+ 字段投影），与 DRAFT 消费字段对齐 ✓
  - 【质量】单章契约是「Source Artifact 投影」的上游，其字段密度决定 draft 卡质量；模板未发现编号或字段异常
- **建议**：无（核对通过）
- **优先级**：无
- **创造力影响**：中性

## 4. planning/blueprint_element_select.j2（BLUEPRINT_ELEMENT_SELECT）

- **元信息**：FULL_OBJECT JSON；温度 0.0-0.2 区间
- **诊断**：
  - 【质量】要素选择是全书结构性决策，低温度正确；`extension_selection.reason` 必须点名本作具体机制或风险，`prompt_hint` 可直接注入后续规划 ✓
  - 【创造】模板采用「最多 8（长篇）/6（短篇）」的上限，而非必须填满的配额；明确要求按真实叙事任务分配并避免同类堆叠，未发现机械填充压力 ✓
- **建议**：无（核对通过）
- **优先级**：无
- **创造力影响**：中性

## 5. planning/plan_outline.j2（PLAN_OUTLINE）

- **元信息**：FULL_OBJECT JSON；required 14 键（synopsis/volume_mode/volumes/narrative_phases/key_turning_points/character_arcs/subplot_plan/suspense_schedule/ending_strategy/emotional_arcs/causal_chains/subplot_collisions 等）
- **诊断**：
  - 【稳定】原先的条件分支「4.」重复已改为具名字段约束，单次渲染不再出现多个第 4 条；其余字段约束保持不变（G-06 已修正）
  - 【格式】规则 13「related_subplot 必须精确匹配主线或某条 subplot_plan[].id」+ 规则 11「depends_on 必须是 JSON 数组」——强枚举/类型约束正确；契约 required 与 schema 对齐 ✓
  - 【质量】规则 8/9/10 支线密度与交织结构约束（chapter_events ≥3、依赖闭环检测）是防「支线贴上去」的高质量约束
- **建议**：保持具名字段约束；新增条件分支时不要复用正文编号（低）
- **修改片段示例**（编号卫生）：
  ```
  （条件分支前）
  4. 骨架中的每条支线必须写明主线触发、支线反哺和最终收束三类功能…
  （三个条件分支现改为）
  - 骨架约束：`weave_links` 必须是对象数组；禁止空字符串占位…
  - 角色弧字段约束：`character_arcs[].milestones[]` 每个对象只允许 chapter_start / chapter_end / …
  - 结局字段约束：`ending_strategy` 的值必须是字符串；禁止对象、数组、嵌套字段和额外字段…
  ```
- **优先级**：低
- **创造力影响**：中性

## 6. planning/plan_outline_batch.j2（PLAN_OUTLINE_BATCH）

- **元信息**：FRAGMENT_OBJECT JSON；required `chapters`；json_schema 含每章 goal/main_plot_points（main_min-main_max）/beats_summary/subplot_points（subplot_max）/element_focus
- **诊断**：
  - 【稳定】批处理模式（batch_start-batch_end + 蓝图阶段顺序硬约束）把长大纲切分为有界批次——稳定性设计正确；「本批最后一章要留出下一批承接点」约束防批次间断裂 ✓
  - 【质量】「若蓝图提供关键转折点，必须让对应章节承载转折本身，而不是只做预告」——防预告式大纲 ✓
  - 【格式】每章 `element_focus` 必填与要素系统对齐 ✓；契约 required `chapters` 单键 ✓
- **建议**：无（核对通过）
- **优先级**：无
- **创造力影响**：中性

## 7. planning/plan_outline_continue.j2（PLAN_OUTLINE_CONTINUE）

- **元信息**：FRAGMENT_OBJECT JSON；required `chapters`（同 batch）
- **诊断**：
  - 【稳定】续写模式与 batch 模式同构；明确规定“只生成当前批次，不改写或覆盖已完成章节”，首章必须直接承接前一批结尾，已过揭示只能作为既定承接且不得重写为新发现 ✓
- **建议**：无（核对通过）
- **优先级**：无
- **创造力影响**：中性

## 8. planning/polish_outline.j2（POLISH_OUTLINE）

- **元信息**：PATCH_PLAN JSON；required `adjusted_chapters/polish_suggestions`
- **诊断**：
  - 【格式】PATCH_PLAN 模式要求 `adjusted_chapters` 定位到原大纲条目；模板已限定每项包含整数 `chapter_number`，仅输出被修改章节且保留原字段集，不支持整体重排 ✓
  - 【创造】title_repair 模式（`_is_polish_outline_title_repair_context`）说明模板按 context 分模式——上下文条件化是正确设计 ✓
- **建议**：无（核对通过）
- **优先级**：无
- **创造力影响**：中性

## 9. planning/short_blueprint.j2（SHORT_BLUEPRINT）

- **元信息**：FULL_OBJECT JSON；required `synopsis/anchor_elements/narrative_phases/turning_points/character_arcs/emotional_arc/ending_strategy`
- **诊断**：
  - 【质量】短篇蓝图锚定要素（anchor_elements）是短篇 DRAFT 的锚点源；契约 7 键与 schema `_ShortBlueprintResponse` 对齐 ✓
- **建议**：无
- **优先级**：无
- **创造力影响**：中性

## 10. planning/short_creative_summary.j2（SHORT_CREATIVE_SUMMARY）

- **元信息**：FULL_OBJECT JSON；required `characters/narrative_analysis/thematic_analysis/creative_highlights/improvement_suggestions/beat_fulfillment`
- **诊断**：
  - 【质量】创作总结任务：6 键全为分析型字段；它在短篇流程的最终评估后生成并持久化为创作报告，不回灌已完成的 EDIT 轮次。这一时序避免“分析结论伪装成自动改写指令”，建议由用户在下一轮显式采纳 ✓
- **建议**：无（运行时语义已确认）
- **优先级**：无
- **创造力影响**：中性

## 11. planning/validate_scene_plan.j2（VALIDATE_SCENE_PLAN）

- **元信息**：FULL_OBJECT JSON；required `valid/issues/parallel_groups/serial_edges/summary`；温度 0.0（`scene_writing.py:685`）
- **诊断**：
  - 【格式】并行组/串行边结构是场景级 DAG 校验协议；契约 5 键与模板对齐 ✓
  - 【稳定】0.0 温度 + 结构化输出 = 高稳定 ✓
- **建议**：无
- **优先级**：无
- **创造力影响**：中性

## 12. planning/subplot_polish.j2（POLISH_SUBPLOT）

- **元信息**：FRAGMENT_OBJECT JSON；required `subplots`；`_LEGACY_TEMPLATE_ALLOWLIST` 之一
- **诊断**：
  - 【稳定】`TaskType.POLISH_SUBPLOT` 已在 `PromptRegistry` 映射到 `planning/subplot_polish.j2`，并有对应格式契约；“未注册”的历史判断不成立 ✓
- **建议**：无（核对通过）
- **优先级**：无
- **创造力影响**：中性

## 13-16. 初始化一致性族（derive/init_coherence/refine/ground 等 4 个）

- **元信息**：`derive_init_coherence_profile.j2`（FULL_OBJECT 6 键）/ `refine_init_coherence_profile.j2`（FULL_OBJECT 6 键）/ `init_coherence_ontology.j2`（FRAGMENT 3 键）/ `init_coherence_extraction_guide.j2`（FRAGMENT 1 键）/ `init_coherence_conflict_rules.j2`（FRAGMENT 1 键）/ `init_coherence_payoff_rules.j2`（FRAGMENT 2 键）/ `ground_outline_research.j2`（FULL_OBJECT 7 键）/ `plan_init_research_queries.j2`（FULL_OBJECT 2 键）/ `synthesize_model_prior_research.j2`（FULL_OBJECT 3 键）/ `refine_init_artifacts_from_synopsis.j2`（PATCH_PLAN 6 键）/ `repair_init_artifact_patch.j2`（PATCH_PLAN 8 键）
- **诊断**：
  - 【格式】11 个模板契约均与 schema 对齐（07 章矩阵逐项核对）；`repair_init_artifact_patch` 的 PATCH_PLAN 8 键（artifact/artifact_payload/repair_targets/coherence_report/repair_scope/preserve/change_intent/max_ops）是初始化修复的完整协议
  - 【质量】research 族（plan_init_research_queries → synthesize_model_prior_research）把「模型先验知识显性化」作为独立任务——防「模型用训练集知识污染世界观」的正确设计
  - 【创造】`repair_init_artifact_patch` 带 `preserve`（必须保留）与 `change_intent`（改动意图）双清单——修复边界清晰
- **建议**：无系统性改动；`ground_outline_research` 的 `fact_risks` 字段建议确认模板有「风险分级」指令（低）
- **优先级**：低
- **创造力影响**：中性

## 17-22. 剩余规划模板（6 个）

| 模板 | TaskType | 契约 | 诊断摘要 | 优先级 |
|------|----------|------|----------|--------|
| `checking/adjudicate_blueprint_coherence.j2` | ADJUDICATE_BLUEPRINT_COHERENCE | FULL_OBJECT 12 键 | 裁决协议（verdict/score/issues/source_refs/repair_scope/preserve/change_intent/blocked）规范；`blocked` 字段与「自动修复 vs 人工」决策链对齐 | 无 |
| `checking/adjudicate_outline_inheritance.j2` | ADJUDICATE_OUTLINE_INHERITANCE | FULL_OBJECT 12 键 | 同族同构；「继承」语义正确（大纲延续而非重建） | 无 |
| `checking/adjudicate_contract_coherence.j2` | ADJUDICATE_CONTRACT_COHERENCE | FULL_OBJECT 8 键 | 简化版裁决（无 score 字段）与 schema 对齐 | 无 |
| `checking/adjudicate_init_conflict_candidates.j2` | ADJUDICATE_INIT_CONFLICT_CANDIDATES | FULL_OBJECT 12 键 | 同 blueprint 结构；输入含 repair_policy——「冲突候选→裁决→修复策略」闭环正确 | 无 |
| `checking/extract_blueprint_holistic_claims.j2` | EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS | FRAGMENT `claims` | 分批抽取协议（chunk/claim_batch_index/claim_limit 注入）——批量上下文窗口的正确形态 | 无 |
| `checking/extract_init_coherence_claims.j2` | EXTRACT_INIT_COHERENCE_CLAIMS | FRAGMENT `claims` | 同上；`summary` 键出现在 JSON 键扫描中，需确认与契约（仅 claims）是否冲突——07 章矩阵核对显示 required 仅 claims ✓ | 无 |

---

## 规划族优先级汇总表

| 模板 | 优先级 | 核心问题 | 建议动作 |
|------|--------|----------|----------|
| plan_chapter.j2 | 低 | 26 字段单行过长 | 按 4 组重排字段说明 |
| plan_chapter_scenes.j2 | 无 | 核对通过（16 字段协议一致） | - |
| plan_chapter_contracts.j2 | 无 | - | 通过 |
| blueprint_element_select.j2 | 无 | 核对通过（上限而非填满配额） | - |
| plan_outline.j2 | 低 | 条件分支编号已改为具名字段约束 | 保持结构 |
| plan_outline_batch.j2 | 无 | - | 通过 |
| plan_outline_continue.j2 | 无 | 核对通过（当前批次续写、既有事实承接） | - |
| polish_outline.j2 | 无 | 核对通过（按 chapter_number 局部 patch） | - |
| short_blueprint.j2 | 无 | - | 通过 |
| short_creative_summary.j2 | 无 | 最终创作报告，不回灌已完成 edit | - |
| validate_scene_plan.j2 | 无 | - | 通过 |
| subplot_polish.j2 | 无 | 核对通过（已注册并有契约） | - |
| init_coherence 族（11） | 低 | 系统性核对通过 | 可选 fact_risks 分级 |
