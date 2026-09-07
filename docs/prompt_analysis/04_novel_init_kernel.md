# 04 小说模块 — 初始化 / Kernel 提取 / 摘要 / 压缩 / Beats（44 个模板）

> **分析基准**：以运行时目录 `packs/zh/templates/` 为准；本族 10/44 模板与旧副本分叉（G-13）：init_story_bible（-2 行）、init_story_world_rules（+3 行）、init_character_profile_batch（-1 行）、init_character_relationship_matrix（+2 行）、reconcile_entities（+4 行）、kernel 2 个（+1~2 行）、summary 4 个（+3 行）——均为小幅调整，不推翻结论
> 链路：`SPEC_ENRICH → INIT_STORY_* → INIT_CHARACTER_* → INIT_NARRATIVE_CONTRACT → INIT_ENTITY_REGISTRY → PLAN_CHAPTER_CONTRACTS → Canon 提取（EXTRACT_*）→ 摘要（SUMMARIZE_*）→ 压缩（COMPRESS）→ 短篇（BEATS/DRAFT/EDIT/EVALUATE）`
> 契约模式：JSON 为主（FRAGMENT/FULL_OBJECT/PATCH_PLAN）；温度：init 类 0.15-0.2、压缩 0.2、提取类走标准路由

## 子族 A：初始化（initialization/ 21 个）

### A1. initialization/enrich_spec.j2（SPEC_ENRICH）
- **元信息**：FULL_OBJECT JSON；required 键动态（spec_enrich schema，`_enrich_task_format_contract` 上下文化）
- **诊断**：
  - 【质量】5 条强化规则（核心卖点/情感引擎/冲突推导/类型自觉/下游可用性）+ 4 条视角规则（人称枚举 4 值 + 「非对话正文禁止我/我们/咱们」硬禁令）——开篇质量的第一道闸；「人物欲望要能推动外部事件，不只是情绪标签」是反空洞的高质量指令
  - 【稳定】编号 1-5 与 1-4 分区重复（G-06 类，轻微）
- **建议**：编号分区化（低）
- **优先级**：低

### A2. initialization/generate_config.j2（GENERATE_CONFIG / POLISH_CONFIG 共用）
- **元信息**：FULL_OBJECT（GENERATE_CONFIG，strict）/ PARTIAL_OBJECT（POLISH_CONFIG，非 strict）；动态契约 `_enrich_task_format_contract` 上下文化（strict_mode 由 polish 上下文切换）
- **诊断**：
  - 【稳定】原先的 9/10 重复来自润色、长短篇与初始生成条件分支；运行时模板已改为具名追加小节，编号冲突关闭；「先思考后落字段」「信息守恒」「边界优先」三条元规则质量高
  - 【格式】POLISH_CONFIG 的 PARTIAL_OBJECT + enforce_required_keys=False：宽松模式防 polish 时误杀字段，但「信息守恒」规则（每个被润色字段必须保留事实点/硬约束/数字）承担了保真职责——模板约束替代契约约束，正确但依赖模型执行
- **建议**：保持具名条件小节；新增规则时不要恢复跨分支连续编号（低）
- **优先级**：低

### A3-A4. init_story_bible.j2（INIT_STORY_BIBLE）/ init_story_core_premise.j2（INIT_STORY_CORE_PREMISE）
- **诊断**：
  - A3【格式】FULL_OBJECT required `story_bible` 单键；故事圣经是全书源 artifact，模板需含「世界观规则可执行化（供章节 guard 消费）」约束——需读原文确认（122 行）
  - A4【格式】FRAGMENT `story_core`；核心前提碎片化输出（与 bible 分离维护）✓
- **建议**：A3 确认规则可执行化约束（低）；A4 无 | **优先级**：低

### A5-A7. init_story_world_rules / init_story_continuity_rules / init_story_themes_and_symbols（各 FRAGMENT 单键）
- **诊断**：三个碎片化任务把 bible 拆分为可独立重试/独立消费的产物——架构正确；theme/symbol 输出会被 editorial 的 symbol_policies 二次消费，模板需确认「主题句可被后续象征物策略复用」的结构（低）
- **建议**：无系统性改动 | **优先级**：低

### A8-A10. init_character_roster / init_character_profile_batch / init_character_bible
- **诊断**：
  - roster（FRAGMENT `character_roster`）：先列表后建档的分阶段设计（roster→profile_batch）防一次生成超载 ✓
  - profile_batch（FRAGMENT `character_profiles` + json_schema）：批量建档带 schema 校验 ✓
  - bible（FULL_OBJECT `character_bible` + json_schema）：最终合并产物 ✓
- **建议**：无 | **优先级**：无

### A11-A12. init_character_relationship_matrix / init_character_arc_plan
- **诊断**：关系矩阵（FRAGMENT `relationship_matrix`）+ 弧光计划（FRAGMENT `character_arcs`）与 editorial 的 relationship_duties/arc_milestones 联动——「角色弧里程碑」被 plan_chapter 消费，模板需确认字段名与消费点一致（07 章矩阵核对 `character_arcs` 字段）
- **建议**：核对 arc 字段消费链（低） | **优先级**：低

### A13-A14. init_narrative_contract.j2 / init_entity_registry.j2
- **诊断**：
  - narrative_contract（FULL_OBJECT 6 键：world_rules/character_arcs/plot_threads/promise_plan/notes）：全书级叙事契约，`promise_plan`（承诺/伏笔计划）被 foreshadow_due 消费——模板需确认「承诺必须可回收（带目标章）」约束
  - entity_registry（FULL_OBJECT `entities`）：实体注册表（人物/地点/物品/组织/概念）——entity graph 的上游
- **建议**：确认 promise_plan 可回收约束（低） | **优先级**：低

### A15-A16. introduce_character.j2 / enrich_character.j2
- **诊断**：
  - introduce（FULL_OBJECT 12 键：name/role/gender/social_status/abilities/appearance/personality/backstory/arc/relationships/voice/notes + json_schema）：角色建档的完整字段集 ✓
  - enrich（FRAGMENT 4 键：appearance/personality/backstory/arc + json_schema）：按层增强（欲望层/声纹层/关系张力层/弧光种子层）——「证据弱时用倾向于/可能源于」的稳妥措辞防过度断言 ✓
- **建议**：无 | **优先级**：无

### A17. adjudicate_character_introduction.j2（ADJUDICATE_CHARACTER_INTRODUCTion）
- **元信息**：FULL_OBJECT `decisions/summary`
- **诊断**：7 条建档裁决规则（create_profile/merge_existing/skip 三分；「职业/功能/群体/组织/道具标签应 skip」防档案污染；「只有可追踪规范姓名才建档」）——角色库质量控制的关键闸门 ✓
- **建议**：无 | **优先级**：无

### A18. reconcile_entities.j2（RECONCILE_ENTITIES）
- **元信息**：FULL_OBJECT `resolutions` + json_schema
- **诊断**：实体消解（同实异名/异实同名）——entity graph 一致性维护 ✓
- **建议**：无 | **优先级**：无

### A19-A21. synthesize_init_research_dossier / synthesize_model_prior_research / ground_outline_research / plan_init_research_queries / refine_init_artifacts_from_synopsis
- **诊断**：研究族 5 个（详见 02 章 13-16 条）；dossier（FULL_OBJECT 7 键含 source_refs/uncertainty_notes）——「不确定性显性化」是高质量研究输出；refine_from_synopsis（PATCH_PLAN 6 键含 auto_apply_low_risk）——低风险自动应用机制 ✓
- **建议**：无 | **优先级**：无

## 子族 B：Kernel 提取（kernel/ 11 个）

### B1. kernel/extract_canon_delta.j2（EXTRACT_CANON_DELTA）
- **元信息**：FRAGMENT `canon_delta`；EXTRACT_CANON（全量版）require_native_structured_output=True
- **诊断**：
  - 【格式】约束 2「顶层只保留这 7 个字段并平铺输出，不要互相嵌套；不要在字符串里再嵌裸 ASCII 双引号」——7 字段与 EXTRACT_CANON 契约 required 一致 ✓；「字符串不嵌裸双引号」是 JSON 逃逸风险的实用约束 ✓
  - 【格式】约束 7「gender 原样复用，取值限定男/女」现由 `CharacterNormalizer` 与 StoryKernel 合并边界共同保证；`male/female` 规范化为男/女，未知值降为空，不能污染 canon（G-03 已关闭）
  - 【质量】约束 1「只提取正文可证实的信息；不确定就返回空」——防编造 ✓
- **建议**：无（白名单规范化与回归测试已补）
- **优先级**：无

### B2. kernel/extract_canon_delta_fragment.j2（EXTRACT_CANON_DELTA 分片路径）
- **诊断**：70 行短模板，分片提取（与全量版互补）；无编号异常
- **建议**：无 | **优先级**：无

### B3. kernel/extract_chapter_summary_exit.j2（EXTRACT_CHAPTER_SUMMARY_EXIT）
- **元信息**：FRAGMENT 2 键（chapter_exit_state/structured_summary）
- **诊断**：出口状态 + 结构化摘要合并输出——`chapter_exit_state` 是下一章 opening_bridge 的事实源，模板需确认「出口状态必须可被下一章直接消费（字段对齐 bridge 输入）」约束
- **建议**：确认 exit_state 与 bridge 输入字段对齐（低） | **优先级**：低

### B4-B7. extract_creative_report / extract_character_state_deltas / extract_relationship_deltas / extract_plot_thread_deltas
- **诊断**：四个碎片化提取任务（各 FRAGMENT 单键）——把 EXTRACT_CANON 全量拆分为可独立重试的碎片（架构设计，配合 require_native 约束）✓；character/relationship/plot_thread 三键与 story_kernel 的 state_writer 消费点对齐（07 章矩阵核对）
- **建议**：无 | **优先级**：无

### B8. kernel/extract_candidate_state_deltas.j2（EXTRACT_CANDIDATE_STATE_DELTAS）
- **元信息**：FRAGMENT `candidates` + json_schema；**require_native_structured_output=True**
- **诊断**：
  - 【格式】18 条编号规则（部分跳号：1-5,7,9,11-14,16-18）——条件分支编号问题；「evidence 项只允许 quote 和可选 paragraph_index；禁止 context 字段」是强 schema 约束 ✓
  - 【质量】「不做最终裁判，只列候选」「没有 quote 不输出候选」「宁缺毋滥」——候选提取的防污染约束 ✓；候选上限/证据上限注入（candidate_limit/evidence_limit）防超载 ✓
- **建议**：编号整理（低）；降级路径确认（随 G-04） | **优先级**：低

### B9. kernel/adjudicate_final_state.j2（ADJUDICATE_FINAL_STATE）
- **元信息**：FULL_OBJECT 6 键 + json_schema；**require_native_structured_output=True**
- **诊断**：
  - 【稳定】运行时模板当前编号连续为 1–22；原报告的“大跨度跳号”结论已过期。「顶层 verdict 只允许 5 个字符串」+「severity 只允许 low/medium/high/critical」是强枚举约束 ✓
  - 【质量】「不要把 rejected/ambiguous/defer 候选扩散成正文修复任务」——防修复扩散 ✓
- **建议**：保持连续编号；后续只在实际条件分支造成重复时调整（低）
- **优先级**：中

### B10. kernel/adjust_outline.j2（ADJUST_OUTLINE）
- **元信息**：PATCH_PLAN `adjusted_chapters/adjustment_summary`
- **诊断**：5 条调整原则（自然衔接/整合新元素/保持方向/连贯性/具体化）——「总体故事走向不变，只调整达成方式」是稳定的正确表述 ✓
- **建议**：无 | **优先级**：无

### B11. kernel/extract_motifs.j2（EXTRACT_MOTIFS）
- **元信息**：FRAGMENT `motifs` + json_schema
- **诊断**：7+5 条规则（部分跳号）；「能复用已有 motif_id 就复用」「人名/称谓/普通地点不做修辞母题」「category_confidence 不确定时降低」——母题库质量控制的完整约束 ✓
- **建议**：编号整理（低） | **优先级**：低

## 子族 C：摘要（summary/ 5 个）

| 模板 | 契约 | 诊断摘要 | 优先级 |
|------|------|----------|--------|
| summarize_chapter.j2 | FRAGMENT `summary` | 4 条规则（只留主事件/删除描写重复/时间可追溯/无解释标题代码块）——「删除描写性细节」与「保时间顺序」是摘要可用性的关键 | 无 |
| summarize_scene.j2 | FRAGMENT `summary` | 「下一场景能从摘要直接推断承接状态」——场景摘要的可消费性约束 ✓ | 无 |
| summarize_volume.j2 | FRAGMENT `summary` | 「避免逐章流水账」「明确本卷结尾状态与下一卷承接点」✓ | 无 |
| summarize_arc.j2 | FRAGMENT `summary` | 「不输出章节级细节列表，不输出创作建议」——弧线摘要聚焦 ✓ | 无 |
| volume_audit.j2 | FULL_OBJECT 12 键 | 已在 03 章 G10 分析（carry_over 全量携带） | 无 |

## 子族 D：压缩（compression/ 5 个）

### D1. compression/context_compress.j2（CONTEXT_COMPRESS）
- **元信息**：FRAGMENT `items` + json_schema
- **诊断**：4 条规则（id 必须来自输入/不新增事实/压缩后是自然语言短文本/贴近 max_chars）——压缩任务的保真约束完整；「不新增输入中不存在的事实，不改变事件先后和因果关系」是压缩安全的底线 ✓
- **建议**：无 | **优先级**：无

### D2. compression/adaptive_compress.j2（ADAPTIVE_COMPRESS）
- **诊断**：在 context_compress 基础上增加 4 条保留规则（主线推进信息/硬事实/删除冗余/保持可读性）+「priority_facts 必须在 retained_facts 列出保留情况」——自适应压缩的审计闭环 ✓；编号 1-5 与 1-4 分区重复（轻微）
- **建议**：编号分区化（低） | **优先级**：低

### D3. compression/verify_compression.j2（VERIFY_COMPRESSION）
- **元信息**：FULL_OBJECT `quality_score/recommendation`
- **诊断**：压缩质量复核（与 llm_verify 调用点 `verify_temperature=0.2` 对齐）——压缩环节的独立质检 ✓
- **建议**：无 | **优先级**：无

### D4. compression/guard_constraint_check.j2 / plot_guard_judge.j2
- **诊断**：已在 03 章 D7/D6 分析；编号 4 分区重复（guard_constraint_check 的 status 枚举四值约束编号 1-4 与规则编号 1-4 重复）——轻微
- **建议**：低 | **优先级**：低

## 子族 E：短篇 Beats（beats/ 2 个）

### E1. beats/spec_to_beats.j2（BEATS）
- **元信息**：FULL_OBJECT `beats` + json_schema
- **诊断**：
  - 【质量】5 条规则：节拍数公式（目标字数/1000×2，4-16 弹性）、张力曲线递增、每拍推动情节、「最后 beat 必须具体写收束，不能只写留下悬念」——短篇结构质量的源头约束 ✓
- **建议**：无 | **优先级**：无

### E2. beats/beats_to_draft.j2（DRAFT 短篇）
- **元信息**：TEXT_ONLY；短篇 DRAFT 主模板
- **诊断**：
  - 【稳定】原先的跨分支编号混乱（G-02）已修复：分段、整篇与共同表达规则已拆为具名小节，避免把不同模式下的规则误读为同一序号；「禁止待续式断尾」与「保留后续空间但不悬空」分别只在末段与非末段生效，并不矛盾
  - 【质量】规则内容本身高质量：按节拍写实/写出来代替说出来/对话个性化/节奏控制/锚点稳定/转折落地/感官多样性/对话密度——与 evaluate_draft 的七维度评分完全对应（质量闭环）✓
- **建议**：已采用独立小节标题；保持该结构（低）
- **修改片段示例**（编号整理原则）：
  ```
  ## 分段写作规则（分段模式启用）
  1. 按当前节拍顺序写实，不跳过本段必须覆盖的 beat。
  2. 用"写出来"代替"说出来"…
  3. 对话个性化…
  4. 节奏控制…
  5. 锚点稳定：不新增蓝图之外的主场景/主角色/关键事件…
  6. 转折落地：execution_plan 标明 turning_point_hint 时正文必须真正发生转折…
  7. 篇幅分配：严格参考字数分配…
  8. 系统洁净：禁止任何提示词/结构标签/元说明腔…
  9. 只完成当前分段职责：不把后续段落一次写完，不用"下文再说"…
  10. 桥接自然：直接承接上一段尾部，不做完整背景介绍…
  11. 分段内部也要成文：本段有完整小节奏，不像提纲或半截草稿…
  12. 首段必须开稳：第一段锚定人物/情境/异常变化并启动冲突…

  ## 整篇写作规则（整篇模式启用）
  13. 末段必须收稳：最后一段完成 resolution，至少兑现两项（…）
  14. 禁止待续式断尾：不停在动作起手式/突发异象/半句对话/单纯悬念…
  15. 保留后续空间但不悬空：可留推动力，不能像突然截断…
  16. 完整短篇而非连载片段：完整 beginning-middle-ending；开放式结尾允许…
  17. 最后一段必须落地：完成以下至少两项（…）
  18. 禁止没头没尾：开头缺人物/情境锚定、结尾只剩异象/起手式均违规…

  ## 通用规则（两种模式均生效）
  19. 感官多样性：至少覆盖 3 种感官…
  20. 对话密度：对话内容占全文 {{ _dlg_min }}%-{{ _dlg_max }}%…
  ```
- **优先级**：低（已修正）
- **创造力影响**：中性（仅编号整理，规则内容不变）

---

## 本族优先级汇总表

| 模板 | 优先级 | 核心问题 | 建议动作 |
|------|--------|----------|----------|
| enrich_spec.j2 | 低 | 编号分区重复 | 编号整理 |
| generate_config.j2 | 低 | 条件分支编号已改为具名小节 | 保持结构 |
| init_story_bible.j2 | 低 | 规则可执行化约束待确认 | 核对 |
| init_story_*（3） | 低 | - | 核对联动 |
| init_character_*（4） | 无 | - | 通过 |
| init_narrative_contract.j2 | 低 | promise_plan 可回收约束 | 核对 |
| init_entity_registry.j2 | 无 | - | 通过 |
| introduce/enrich_character.j2 | 无 | - | 通过 |
| adjudicate_character_introduction.j2 | 无 | - | 通过 |
| reconcile_entities.j2 | 无 | - | 通过 |
| research 族（5） | 无 | - | 通过 |
| extract_canon_delta.j2 | 无 | 核对通过（性别白名单规范化） | - |
| extract_canon_delta_fragment.j2 | 无 | - | 通过 |
| extract_chapter_summary_exit.j2 | 低 | exit_state 与 bridge 对齐 | 核对 |
| extract_*_deltas（4） | 无 | - | 通过 |
| extract_candidate_state_deltas.j2 | 低 | 编号 + 降级路径 | 编号整理 |
| adjudicate_final_state.j2 | 无 | 当前编号连续 | 通过 |
| adjust_outline.j2 | 无 | - | 通过 |
| extract_motifs.j2 | 低 | 编号跳号 | 整理 |
| summary 族（4） | 无 | - | 通过 |
| context_compress.j2 | 无 | - | 通过 |
| adaptive_compress.j2 | 低 | 编号分区重复 | 整理 |
| verify_compression.j2 | 无 | - | 通过 |
| spec_to_beats.j2 | 无 | - | 通过 |
| beats_to_draft.j2 | 低 | 双模式规则已分离为具名小节 | 保持结构 |
