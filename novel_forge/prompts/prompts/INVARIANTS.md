# Prompt Invariants — Novel Forge Phase 2

> **本文档定义提示词模板系统中不可变更的契约与规则。**
> 任何模板优化、压缩或重构都不得违反以下不变量。
> 版本：v1.0.1 | 创建日期：2026-04-21 | 更新日期：2026-05-23

---

## 目录

1. [输出格式契约单源规则](#1-输出格式契约单源规则)
2. [不可变更的阶段交接字段](#2-不可变更的阶段交接字段)
3. [单一输出格式块规则](#3-单一输出格式块规则)
4. [硬约束优先规则](#4-硬约束优先规则)
5. [Artifact Source 消费规则](#5-artifact-source-消费规则)
6. [NON-COMPRESSIBLE CANON WHITELIST](#6-non-compressible-canon-whitelist)
7. [全链路风格约束宏](#7-全链路风格约束宏)

---

## 1. 输出格式契约单源规则

输出格式、必填顶层字段、允许字段、JSON Schema 与 TEXT 守卫策略的唯一权威来源是
`core/format_contracts.py` 中的 `TaskFormatContract`。本文档只记录不变量原则，不复制
逐 TaskType 字段清单，避免文档在任务扩展后滞后于运行时契约。

### 1.1 权威来源顺序

| 层级 | 文件/命令 | 作用 |
|------|-----------|------|
| 契约定义 | `core/format_contracts.py` | 定义每个 TaskType 的 OutputKind、必填字段、允许字段、Schema 与 strict 模式 |
| 模板注入 | `prompts/builder.py` | 渲染模板后追加唯一的 `统一格式契约（系统注入）` 块 |
| 运行时验证 | `validate_json_output_contract()` / `validate_text_output_contract()` | 用同一份契约验证模型输出，并触发格式重试 |
| 人类索引 | `prompts/prompts/INDEX.md` | 由脚本生成的当前模板/契约快照，只读，不手工维护 |

### 1.2 维护规则

- 新增或调整 TaskType 输出字段时，只修改 `core/format_contracts.py` 与必要的解析/归一化代码。
- 不在任务模板中手写 JSON/TEXT 输出协议、字段清单、示例 JSON 或 `standard_json_output()`。
- 不在本文档复制字段全集；需要审计当前字段时运行下面的维护命令。
- 若 `INDEX.md`、模板备注或本文档与 `TaskFormatContract` 冲突，以 `TaskFormatContract` 为准。

### 1.3 必跑验证

```bash
.venv/bin/python scripts/lint_prompt_layers.py
.venv/bin/python scripts/audit_prompt_format_layers.py --all
.venv/bin/python scripts/generate_prompt_index.py --check
.venv/bin/python scripts/verify_templates.py
.venv/bin/python scripts/verify_format_contracts.py
```

### 1.4 TEXT 输出不变量

TEXT 输出类型由 `OutputKind.TEXT` 与 `validate_text_output_contract()` 统一约束：

- 输出必须直接从正文第一句话开始
- 禁止在正文末尾追加修改说明、修改摘要、更改清单或元注释
- 禁止返回 JSON 或使用 Markdown 代码块包裹
- 禁止泄露规划/系统字段，例如 `scene_intent`、`opening_contract`、`opening_bridge`、`required_outcome`

---

## 2. 不可变更的阶段交接字段

Pipeline 各阶段之间通过以下字段名传递上下文。**字段名不可变更**，否则会导致下游步骤无法正确解析上游输出。

### 2.1 Bridge → Plan（桥接 → 规划）

| 字段 | 来源 Schema | 用途 |
|------|-------------|------|
| `transition_mode` | `ChapterBridge` | 过渡方式（direct_continue / time_skip / scene_cut 等） |
| `action_handoff` | `ChapterBridge` | 动作交接描述 |
| `pending_questions` | `ChapterBridge` | 待解决问题列表 |
| `emotional_carryover` | `ChapterBridge` | 情绪延续 |
| `opening_time` | `ChapterBridge` | 开场时间 |
| `opening_location` | `ChapterBridge` | 开场场景 |
| `opening_pov` | `ChapterBridge` | 开场视角 |
| `causal_link` | `ChapterBridge` | 因果链接（含 previous_event, causal_mechanism, unresolved_question） |

Bridge 字段只在 Bridge / Planning 阶段直接消费。Planning 必须把可执行开场锚吸收到 `ChapterPlan.opening_bridge`；写作阶段不得再把 `ChapterBridge` 当作独立执行源。

### 2.2 Plan → Draft（规划 → 草稿）

| 字段 | 来源 Schema | 用途 |
|------|-------------|------|
| `scene_intents` | `ChapterPlan` | 场景意图列表 |
| `opening_bridge` | `ChapterPlan` | Bridge 派生的开场时间/地点/POV/动作接力/情绪余波/因果链执行锚 |
| `opening_contract` | `ChapterPlan` | 开场契约 |
| `closing_contract` | `ChapterPlan` | 收场契约 |
| `required_state_transitions` | `ChapterPlan` | 必需状态转换 |
| `chapter_type` | `ChapterPlan` | 章节功能类型 |
| `forbidden_elements` | `ChapterPlan` | 禁用元素列表 |
| `forbidden_elements_soft` | `ChapterPlan` | 软约束禁用元素 |
| `intentional_callbacks` | `ChapterPlan` | 有意回环意象 |
| `emotional_arc` | `ChapterPlan` | 情绪弧线 |
| `foreshadowing_plan` | `ChapterPlan` | 伏笔计划 |

### 2.3 DRAFT → WAVE（原稿 → 初稿成章）

| 字段 | 用途 |
|------|------|
| `text` | DRAFT 原稿正文文本（`v0_draft.md`） |
| `meta` | 章节元数据（chapter_number, title, word_count 等） |

### 2.4 WAVE → Review / Extract（初稿成章 → 审阅 / 典据提取）

| 字段 | 用途 |
|------|------|
| `text` | DRAFT+WAVE 后的可审初稿（`v1_wave.md`），后续修复可继续更新 |
| `chapter_number` | 章节号 |

### 2.5 Extract → Canon（提取 → 典据持久化）

| 字段 | 来源 Schema | 用途 |
|------|-------------|------|
| `canon_delta` | `CanonDelta` | 典据增量 |
| `creative_report` | `CreativeReport` | 创作报告 |
| `chapter_exit_state` | `ChapterExitState` | 章节退出状态 |
| `character_state_deltas` | `list[CharacterStateDelta]` | 角色状态变化 |
| `relationship_deltas` | `list[RelationshipStateDelta]` | 关系变化 |
| `plot_thread_deltas` | `list[PlotThreadDelta]` | 情节线变化 |
| `structured_summary` | `str` | 结构化摘要 |

### 2.6 ChapterOutline 跨阶段不变字段

以下 `ChapterOutline` 字段在整个 Pipeline 生命周期中保持不变：

| 字段 | 用途 |
|------|------|
| `chapter_number` | 章节号（1-based） |
| `pov_character` | POV 角色 |
| `pov_switch` | 是否视角切换（bool） |
| `time_anchor` | 时间锚点 |
| `time_gap_from_prev` | 与上章时间差 |
| `is_flashback` | 是否闪回 |
| `element_focus` | 叙事要素聚焦（最多 3 个） |

---

## 3. 单一输出契约块规则

### 3.1 规则定义

**每个任务最终 prompt 中，有且仅有一个由 Builder 注入的统一输出契约块。**

### 3.2 实现机制

`PromptBuilder.render()` 按以下逻辑注入格式契约：

```python
# 伪代码逻辑
contract_block = render_prompt_contract_block(task_type, context)
if contract_block:
    user_prompt = f"{user_prompt.rstrip()}\n\n{contract_block}\n"
```

### 3.3 不变量

1. **契约单源**：输出格式、必填字段、允许字段、JSON schema 与 TEXT 策略只来自 `TaskFormatContract`
2. **Builder 注入**：任务模板不得包含 `standard_json_output()`、`standard_text_output()`、`## 输出格式` 或手写 JSON 协议段
3. **单一实例**：最终 prompt 中只能出现一个 `统一格式契约（系统注入）` 块
4. **运行时强验证**：JSON 输出必须通过契约/schema 校验；TEXT 输出必须通过统一文本守卫

### 3.4 模板编写指南

- 任务模板只描述业务意图、上下文字段和领域约束
- 示例 JSON 必须可被 `json.loads` 解析，且不得包含范围表达或注释
- 新任务必须先在 `core/format_contracts.py` 建立契约，再注册模板

---

## 4. 硬约束优先规则

### 4.1 规则定义

**当模板中存在多重约束时，硬约束始终优先于风格/建议性指导。**

### 4.2 系统级保障

系统 preamble（`prompts/builder.py:20-36`）已内置此规则：

```
4. 遇到多重约束时，优先遵守当前任务中的硬约束，再考虑风格润色。
```

### 4.3 硬约束清单（P0 级）

以下约束属于硬约束，任何风格指导不得与之冲突：

| 约束类别 | 具体内容 | 违反后果 |
|----------|----------|----------|
| **JSON Schema** | 输出必须包含所有 enforce_required_keys 字段 | 解析失败 → 自动重试 |
| **字数约束** | expected_word_count / target_words | 质量评分降低 |
| **Immutable Facts** | `immutable_facts` 中的【禁用意向】和【禁用表达】 | 语义漂移检测 |
| **Forbidden Elements** | `forbidden_elements` 列表中的意象/句式 | 禁用元素检测 → 触发修复 |
| **POV 一致性** | `pov_character` 和 `pov_switch` 规则 | 连续性评估失败 |
| **时间锚点** | `time_anchor`, `time_gap_from_prev` | 时间一致性校验失败 |
| **因果链** | `causal_link` 中的 previous_event → causal_mechanism | 因果验证失败 |

### 4.4 软约束/风格指导（P1-P2 级）

以下内容在硬约束满足后才考虑：

- 风格规范（`style_profile` 中的模块和规则）
- `forbidden_elements_soft`（软约束禁用元素）
- 情绪弧线建议（`emotional_arc`）
- 感官锚点建议（`sensory_anchors`）
- 关系演进建议（`relationship_evolution`）

### 4.5 冲突解决策略

当硬约束与风格指导冲突时：
1. **遵守硬约束**，忽略冲突的风格指导
2. **不报告冲突**，静默优先
3. **不修改硬约束** 来适配风格

---

## 5. Artifact Source 消费规则

### 5.1 长篇全链路 source 顺序

长篇提示词侧的权威输入顺序为：

`Init Source Artifacts → ChapterSourceSlice → StageArtifact → FinalArtifact → Canon/Memory`

- 初始化模板可以读取完整初始化输入，因为它们负责生成源头 artifacts。
- `run-chapter` 后续阶段必须通过 `stage_cards.source` 消费 `ChapterSourceSlice` 的白名单投影。
- 每个阶段只消费上一步 artifact 与本步骤声明的 source 投影；不得临时读取全书级创作大包来补剧情。
- 角色、地点、物件、组织和概念只能通过 canonical entity refs / registered aliases 进入提示词；正文渲染前才解析显示名。

### 5.2 初始化源头 artifact 的 stage 投影边界

| Raw init artifact | 不直接进入 runtime stage 的理由 | 允许的投影 |
|-------------------|----------------------------------|------------|
| `project_spec` | 原始用户意图只负责初始化；章节期重读会绕过已裁定的 bible / contract | `story_foundation`、`chapter_contract` |
| `character_system` | 全角色和全关系太宽，容易引入非本章人物/关系 | 相关角色卡、声纹卡、StoryKernel 角色知识 |
| `entity_graph` | 原始图谱用于 canonical id / alias 校验，不是写作上下文 | `relevant_entities`、entity reference graph |
| `blueprint` | 全书蓝图含未来 payoff，直读会泄露后续确认 | chapter contract、outline slice、milestone window、creative direction |

### 5.3 阶段边界

| 阶段 | 允许消费 | 禁止消费 |
|------|----------|----------|
| Bridge | `ChapterSourceSlice` + 上章 Final/exit + bridge local card | 全书级规划大包、未发生未来 payoff |
| Plan | BridgeArtifact + `source.chapter_contract` + 章节薄片 | 通过完整蓝图推断新剧情 |
| Draft | 单场/章节 plan、`ChapterPlan.opening_bridge`、局部世界规则、局部声纹/风格 | 独立 BridgeCard、cross-scene intent、人物大包、全书级创作规划 |
| Wave | DraftArtifact 文本、PlanArtifact 的 cross_scene_intent、禁揭示边界 | 新增场景、事实、确认式揭示 |
| Review/Repair | ticket、evidence quote、文本窗口、验收条件、禁揭示边界 | 宏观创作规划、自由重写整章 |
| Polish | 已修正文、表达问题、风格投影 | 新剧情事实、角色认知、事件 ledger |
| Canon/Memory | FinalArtifact / 最终正文证据 | 计划、章节契约、中间稿、source 投影本身 |

### 5.4 模板维护规则

- 长篇 Bridge/Plan/Draft/Wave/Edit/Repair/Polish 模板必须导入或等价执行 `_base/_artifact_source_contract.j2` 的 source 边界。
- Repair 类模板必须显式包含“只修 ticket/window，不自由重写整章”的硬约束。
- Canon/Memory 抽取模板必须显式包含“只从最终正文证据写事实”的硬约束。
- 若新增 source 字段必须同步 schema、投影服务和阶段 contract；缺字段应由上游 gate 失败，不让模型猜。

---

## 6. NON-COMPRESSIBLE CANON WHITELIST

以下典据字段在上下文压缩（Context Compression / Adaptive Compression）过程中 **不可被压缩、省略或简化**。它们是 Pipeline 正确运行的最低限度典据。

### 6.1 `immutable_facts`

- **来源**：`CanonRetriever._extract_immutable_facts()`
- **内容**：
  - `【禁用意向】` 规则（来自 `StoryBible.banned_intent_rules`，最多 15 条）
  - `【禁用表达】` 短语（来自 `CanonState.banned_phrases`，最多 20 条）
- **用途**：在 `draft_chapter.j2` 中作为 ★ P0 硬约束渲染
- **不可压缩原因**：缺失将导致禁用元素检测失效，产生语义漂移

### 6.2 `opening_bridge.transition_mode`

- **来源**：`ChapterPlan.opening_bridge.transition_mode`（由 `ChapterBridge.transition_mode` 吸收而来）
- **内容**：过渡方式字符串（`direct_continue` / `time_skip` / `scene_cut` 等）
- **用途**：决定草稿开头的叙事衔接方式
- **不可压缩原因**：缺失将导致章节间过渡断裂

### 6.3 `forbidden_elements`

- **来源**：`ChapterPlan.forbidden_elements`
- **内容**：本章必须回避的意象/句式/感官通道列表
- **用途**：写作时避开已禁用元素，修复时检测违规使用
- **不可压缩原因**：与 `intentional_callbacks` 配合使用，缺失将导致误判

### 6.4 `intentional_callbacks`

- **来源**：`ChapterPlan.intentional_callbacks`
- **内容**：有意回环意象列表——作者刻意复用以实现母题呼应的表达
- **用途**：从 `forbidden_elements` 检测中排除这些刻意重复的意象
- **不可压缩原因**：缺失将导致合法的母题呼应被误判为禁用元素违规

### 6.5 `pov_switch_rules`

- **来源**：`ChapterOutline.pov_switch`（bool）+ `ChapterPlan.opening_bridge.opening_pov`
- **内容**：
  - `pov_switch`: 标记本章是否 deliberate 切换视角
  - `pov_character`: 当前章 POV 角色
  - `opening_pov`: Plan 吸收后的开场视角
- **用途**：连续性评估时，`pov_switch=True` 会抑制 `pov_jump` 警告
- **不可压缩原因**：缺失将导致合法的视角切换被误报为连续性错误

### 6.6 压缩安全检查

在执行 `CONTEXT_COMPRESS` 或 `ADAPTIVE_COMPRESS` 任务时，必须验证：

```
压缩后上下文 ⊇ NON-COMPRESSIBLE CANON WHITELIST
```

即：以上 5 类字段在压缩后的上下文中必须完整保留，不得丢失任何一项。

---

## 7. 全链路风格约束宏

`creative_specificity_rules` 是一个共享质量宏，对所有 TEXT 输出阶段实施"反机械自证句式"的跨阶段约束。

### 7.1 定义位置

| 文件 | 用途 |
|------|------|
| `_base/_quality_standards.j2` (line 148) | 宏定义：`creative_specificity_rules(title=...)` |
| `_quality_standards.j2` (line 15) | 重导出：`{%- set creative_specificity_rules = _base_qs.creative_specificity_rules -%}` |
| `core/format_contracts.py` | TEXT 输出守卫阻断 Markdown 结构、规划术语和 prompt leak |

### 7.2 必须包含此宏的模板

所有正文相关的 TEXT 输出模板，以及生成正文替换片段的 `PATCH_CHAPTER` JSON 模板，必须通过 `{%- from "_quality_standards.j2" import creative_specificity_rules -%}` 导入并在执行层渲染：

| 类别 | 模板 |
|------|------|
| 写作 | `draft_chapter.j2`, `edit_chapter.j2`, `polish_chapter.j2`, `edit_draft.j2` |
| 补丁 | `patch_chapter.j2`（JSON 补丁计划，`replacement` 字段仍承载正文片段） |
| 节拍 | `beats_to_draft.j2` |
| 修复 | `continuity_repair.j2`, `causal_repair_typed.j2`, `repair_reading_power.j2`, `guardrail_repair.j2`, `repair_adjudicated_issue.j2` |
| 检查 | `check_chapter.j2`（判为 `expression_errors`） |

### 7.3 核心不变量

以下约束不可删除或弱化，只能扩展替代方向：

1. 定义式否定转折只能在角色真实辩驳、事实/时间纠偏、关键情感悬置等少数位置保留，不得用来自证描写品质
2. 具体化必须落到角色选择、动作后果、对白潜台词、环境阻力或可感知的场面调度
3. 同一章内不得反复使用同构判断句包装目光、沉默、生理反应、心动/恐惧等感受；修复应由模型判断叙事功能后改写，不做本地机械替换

### 7.4 反诱导保护

以下模板的规则/示例不得诱导生成此类句式：

- `style_profile_derive.j2`：禁止生成鼓励机械对照句的正例/规则
- `plan_chapter.j2`：summary / required_outcome 不得设计定义式对照
- `polish_chapter.j2`：必须让模型进行全章修辞指纹整理，保留有叙事功能的少数处，改写其余同构辨析句

---

## 附录 A：相关文件索引

| 文件 | 用途 |
|------|------|
| `core/format_contracts.py` | TaskFormatContract 定义 |
| `core/schemas/continuity.py` | ChapterPlan, ChapterBridge, ChapterStatePacket |
| `core/schemas/outline.py` | ChapterOutline, StoryOutline, NarrativeBlueprint |
| `core/schemas/canon.py` | CanonDelta, CreativeReport, ExtractedCanon |
| `core/schemas/chapter.py` | ChapterResult, ChapterOutcome |
| `prompts/builder.py` | PromptBuilder — 输出格式块注入逻辑 |
| `core/format_contracts.py` | 统一输出契约渲染与运行时校验 |
| `canon/retriever.py` | CanonRetriever — immutable_facts 提取 |

## 附录 B：变更历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.1 | 2026-05-23 | 同步 `format_contracts.py`：修正 `PATCH_CHAPTER` 为 JSON、补齐强制字段、移除非强制 required_keys 例外 |
| v1.0.0 | 2026-04-21 | 初始版本 — Phase 2 Wave 1 |
