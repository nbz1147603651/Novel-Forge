# 01 小说模块 — 写作族（9 个运行时模板 + 1 个 legacy 模板）

> **分析基准**：本报告以运行时目录 `novel_forge/prompts/packs/zh/templates/` 为准（registry 实际加载路径）；运行时写作族有 9 个模板。`repair_literary_quality.j2` 仅在旧副本中存在，因此只作为 legacy 项记录，不能作为运行时结论的依据。
> 链路：`BRIDGE_CHAPTER → DRAFT_CHAPTER/DRAFT_SCENE → WAVE_CHAPTER → EDIT_CHAPTER → POLISH_CHAPTER`；短篇链路：`beats_to_draft → evaluate_draft`（evaluate 属检查族但产出在写作链路，此处一并分析）
> 契约模式：全部 TEXT_ONLY（bridge 为 FULL_OBJECT JSON）；温度：写作类默认 0.7（`llm_service.py:1182`），bridge 0.0（`planning.py:373`）

## 1. writing/bridge_chapter.j2（BRIDGE_CHAPTER）

- **元信息**：FULL_OBJECT JSON；required 10 键（opening_time/opening_location/opening_pov/transition_mode/emotional_carryover/action_handoff/causal_link/pending_questions/forbidden_repetition/opening_acceptance_criteria）；温度 0.0（`stages/planning.py:373`，retry 0.0）
- **诊断**：
  - 【稳定】模板声明「只输出以下 10 个英文 snake_case 顶层字段；不要新增非契约字段」→ 与契约 required 10 键一致 ✓；但模板正文未列出 10 个字段名（仅注释提及），依赖 builder 注入契约。若 builder 未注入（legacy 调用路径），模型将无字段名可依 → 建议在格式层显式列出字段清单（成本极低）
  - 【质量】任务定义仅「桥接只回答一个问题：上一章最后的状态，为什么会自然导致本章第一幕…」——单一问题定义是良好聚焦，但未给出负面示例（如「禁止用天气描写搪塞因果」）
  - 【创造】约束仅 10 字段边界，自由度高 ✓
- **建议**：格式层补 10 字段清单 + 1 个 ✗/✓ 对比示例（中等）
- **优先级**：中
- **创造力影响**：中性

## 2. writing/draft_chapter.j2（DRAFT_CHAPTER）

- **元信息**：TEXT_ONLY；温度 0.7（`llm_service.py:1182` 默认，`stages/draft.py:862` 经 DraftStep 调用）；最长链路上游卡（source/contract/plan/characters/style/quality/repair 等 12+ 张卡）；**运行时版 422 行（旧版 414 行）**
- **诊断（运行时版）**：
  - 【质量】运行时版新增：世界规则卡（`source.world_rule_card`，只执行本场 `world_rule_ids` 绑定规则）、场景级必达清单（`owned_events`/`owned_revelations`/`owned_state_changes` 逐项写入）、认知约束宏（`render_cognitive_constraints`，含 `character_knowledge_coverage` 逐角色知识覆盖）——**「必达事件」从章级细化为场景级可观察项，且硬结果逐项核对（「用并列关系连接的硬结果必须各自落地，具体道具/动作不得用功能近似替代」）**，是防「事件虚晃」的关键升级 ✓
  - 【稳定】规则密度仍为全仓库最高：17 条「写作规则」+ 4 条「开场承接硬约束」+ 8 个质量宏展开 + 字数自检 + 交付核验清单（G-07 在运行时版仍成立）；规则 14-17 与宏内容重叠（重复段落 vs `paragraph_uniqueness`、感官侧重 vs `sensory_diversity`）——同一语义两处出现，长尾规则执行率递减
  - 【创造】第 10 条「同一表达通道最多完整展开一次」与宏叠加后可能让模型过度保守（写一段动作就换通道，段落碎片化）——建议改为「相邻 3 段内不重复展开」
  - 【格式】模板输出纯正文；下游 `DraftStep` 直接取 text ✓ 无格式风险
- **建议**：
  1. 删除与宏重复的规则 14/16/17（保留宏版本），规则总数 17→13（高）
  2. 第 10 条「同一表达通道最多完整展开一次」改为「相邻 3 段内不重复展开同一通道」（中）
  3. 开场承接 4 条硬约束后补一句「若 opening_acceptance_criteria 为空，只执行第 1-3 条」（低）
- **修改片段示例**（规则瘦身，高优先级）：
  ```
  ## 写作规则
  1. 按 scene 顺序推进，不改变主事件结果和因果顺序；scene 内的动作、对白、细节、情绪层次可自由创作。
  2. 必达事件必须在正文中真实发生，禁止只用旁白概括。
  3. 禁止变化中的正面出场、主动联系、直接揭示、状态变化不得发生；未来 payoff 只能铺垫不能兑现。
  4. 非 POV 角色内心只能通过表情、动作、台词和可观察反应呈现。
  5. 角色称谓、能力、动机服从角色卡和硬事实。
  6. 禁用元素只针对修辞意象/句式；剧情锚点保留功能但换表达通道。
  7. 不输出 scene_id、opening_contract、required_outcome 等规划术语。
  8. 章尾必须形成 closing_contract 指向的可承接状态。
  9. `sensory_notes` 是候选锚点：每场择 1-2 个服务冲突的细节，不逐条兑现。
  10. 相邻 3 段内同一表达通道（身体信号/环境映射/对白标签）不重复展开；同类情绪反复出现时，改用动作选择、对白延迟、环境阻力或决策过程。
  11. 写完自检：没有新增行动、信息、冲突或人物选择的语言，应删减或改成契约允许的行动、对白、决策或场面调度。
  12. **只执行显式字面合同**：仅上方 `required_literals` 中由 Planning LLM 显式裁定的文字必须完全相同地出现；其他引号文本按语义要求完成，禁止自行升级为字面合同。
  13. **开场框架不可替换**：严格服从上方「开场承接硬约束」，创意空间在场景内部。
  ```
  （删除原 14/15/16/17 与宏重复项，保留 13 条；情绪节拍/感官侧重/潜台词由对应宏与场景卡字段承载，避免双写）
- **优先级**：高（规则瘦身）
- **创造力影响**：松绑（注意力释放 + 表达通道约束放宽到 3 段窗口）

## 3. writing/draft_scene.j2（DRAFT_SCENE）

- **元信息**：TEXT_ONLY；场景级写作模式（`writing_mode=scene_level`）；温度 0.7
- **诊断**：
  - 【质量】模板带 `current.scene_id` + 章级共享锚点（Cast Plan/Emotional Plan 为 P0）——比 draft_chapter 更聚焦单场景，设计良好；「只写当前场景，不输出场景标题、scene_id」是干净的输出边界
  - 【稳定】依赖变量极多（chapter_*/contract_* 平铺变量约 20 个，无 stage_cards 包装），一旦调用点漏传某变量，Jinja2 `StrictUndefined` 直接渲染失败（registry 用 `_SafeUndefined` 但模板渲染走 builder 的严格模式？需核对 builder）——调用点 `_draft_scene_level_text` 在 `stages/draft.py:865`，字段装配在 bundle 构造处，属高风险面
  - 【创造】单场景自由度好，且运行时规则 1 已明确禁止提前写其他场景的 `owned_events` / `owned_revelations` / `owned_state_changes`；原报告关于缺少负向约束的结论不成立
- **建议**：无新增约束；保持现有场景边界，后续以跨场景提前兑现的真实样本决定是否调整（低）
- **优先级**：中
- **创造力影响**：中性

## 4. writing/wave_chapter.j2（WAVE_CHAPTER）

- **元信息**：TEXT_ONLY；温度 0.7；WAVE 后置 6 条件均记录 warning、不抛出异常（见 00 章 G-08）
- **诊断**：
  - 【稳定】「编织规则」11 条 + 自检 6 条 + 交付核验 6 条——但系统侧对 6 个后置条件全部只记 warning。模板要求「必须命中」「必须保持」的强度与运行时验收不符；模型永远无法从失败中学习（无回灌）
  - 【质量】第 11 条「文学编织（P1）」是全模板质量最高的一组指令（过渡段情感功能、接缝文学性、章末 3 段记忆锚点、句法骨架轮换、呼吸段）——P1 定位准确，与 P0 编织职责分层清晰
  - 【创造】禁令多但均为「不做什么」；「允许 1-2 句衍接性微调」给出了明确的自由度边界 ✓
- **建议**：
  1. 将可机检条件（字数 ±30%、POV 守恒）从「自检」改为「系统将核验，不通过将触发修复」，模板表述同步调整（高）
  2. 不可机检条件（cross-ref 80%）保持软约束，但增加「未命中时在输出末尾以 `<!--wave:miss:ref_id-->` 形式自报」的协议（供系统统计回灌）——该协议目前不存在，需同步改 wave_step 解析（中）
- **修改片段示例**（验收对齐，高优先级）：
  ```
  ## 交付前核验
  - 字数与 POV 由系统自动核验：字数超出 target ±30% 或 POV 偏离 `{{ pov_name }}` 将触发修复流程，无需自行说明。
  - 跨场景引用命中率由系统统计：若你判断某条引用无法在不重写 POV 主体的前提下命中，在正文末尾（不与正文混排）输出一行：
    `<!--wave:miss:{ref_id}-->`；系统将记录为 warning 并回灌下一章。
  ```
- **优先级**：高
- **创造力影响**：中性（验收归系统，模型自由度不变）

## 5. writing/edit_chapter.j2（EDIT_CHAPTER）

- **元信息**：TEXT_ONLY；温度 0.7；复用 `_repair_attempt_guidance`、`render_quality_calibration`
- **诊断**：
  - 【质量】模板结构（字数重整事务/代词定向修复/对齐定向修复/标准修复 tickets 分 P0/P1 区块）是修复链路的正确形态；「编辑契约卡优先于泛风格偏好」是清晰的优先级声明
  - 【稳定】ticket 驱动模式（`chapter_quality_repair_tickets` 按 ticket_id 一对一处理）把修复范围锁定到段级，稳定性设计优秀；但模板同时接受 `chapter_repair_report`（旧格式）与 tickets（新格式），两者并存时模型可能双写 → 建议调用点收敛到 tickets 单通道
  - 【创造】第 9 条「不因计划里有多个感官提示就新增细节」防止注水 ✓
- **建议**：调用点收敛 tickets 单通道；模板加一句「若 tickets 与 chapter_repair_report 同时存在，以 tickets 为准」（中）
- **优先级**：中
- **创造力影响**：中性

## 6. writing/edit_draft.j2（EDIT，短篇）

- **元信息**：TEXT_ONLY；短篇 EditStep（`max_edit_rounds`）
- **诊断**：
  - 【质量】未读全文，编号条目未提取到（模板以 prose 段落为主而非编号规则）；短篇编辑与长篇 edit_chapter 职责重叠但无 tickets 机制——短篇修复稳定性依赖 edit_draft 自身规则
- **建议**：短篇链路补 min 级 ticket 结构（可选，若短篇质量门足够则不动）
- **优先级**：低
- **创造力影响**：中性

## 7. writing/polish_chapter.j2（POLISH_CHAPTER）

- **元信息**：TEXT_ONLY；温度 0.7；Polish 后触发 re-extract/re-eval/re-audit（AGENTS.md）
- **诊断**：
  - 【质量】10 条精修规则按 P0/P1/P2 分层（P0 事实不变 / P1 表达密度 / P2 风格节奏），分层是全仓库最规范的模板之一；第 5 条「允许用全新的表达手段重写该段落」是反模板化的强指令 ✓
  - 【稳定】「若语言优化会改变事件结果或信息等级，保留原事实表达，只微调句法和节奏」——定义清晰；「字数保持原文 ±15%」硬约束可机检 ✓
  - 【创造】P2 第 9 条「连续 3 个段落不得长度相近（±20%）」是机械风格约束——在对话密集段落（天然长度接近）会与对话密度要求冲突，建议加「对话轮次段除外」
- **建议**：第 9 条补「对话轮次段与短句独白段除外」（低）
- **优先级**：低
- **创造力影响**：松绑（修正机械约束）

## 8. writing/patch_chapter.j2（PATCH_CHAPTER）

- **元信息**：PATCH_PLAN JSON；required `patches`；`patch_engine/` 精确文本匹配下游
- **诊断**：
  - 【格式】契约 required 仅 `patches`；模板已声明 `original` 为含标点、空白与换行的精确原文子串，并明确要求补丁按原文顺序返回、不得重叠或嵌套。可编辑范围外与上一章结尾均为只读 ✓
  - 【稳定】模板已带 must_fix_summaries（阻断归档硬清单）+ 逐处修复合同（postconditions/repair_directive）——修复范围锁定到段落级，是外科式修复的正确形态；「不会看到完整章节」的定位声明很好
  - 【创造】每处修复给出「修复方向」但保留实现自由；「必须回扣的语义锚点」只约束落点不约束表达 ✓
- **建议**：无（已补精确匹配、顺序与不重叠约束）
- **优先级**：无
- **创造力影响**：中性

## 9. writing/evaluate_draft.j2（EVALUATE，短篇评估）

- **元信息**：FULL_OBJECT JSON；required `scores/overall_score/passed/threshold/summary/repair_suggestions`；`stages/short/` 链路；**运行时版 254 行（旧版 259 行）**
- **诊断（运行时版）**：
  - 【质量】七维度评分体系（consistency/continuity/plot_progression/character/style/engagement/pacing）子项分值明确（2 分制/1.5 分制）；**注意：运行时版已删除「文学质量专项扣分」4 条**（身体反应模板化/角色语言同质化/意象缺乏新鲜感/场景结构单一——仅存于旧副本，G-09）——反 AI 味扣分细则在运行时缺失，style 维度降分仅剩「泛化动词占比/句式多样/系统标记/公式化起笔」四条，建议从旧版恢复或重构后补充至运行时版
  - 【稳定】「对同一正文多次评估结果应高度一致，不要随机波动超过 ±0.5 分」——评估稳定性要求明确；维度分 5×2 整数制导致 `overall_score` 粒度粗糙（G-09）
  - 【格式】`scores[].dimension` 固定枚举 7+1 个（rewrite_compliance 条件性出现）与契约 schema 对齐 ✓（`_EvaluateResponse`）
- **建议**：① 恢复「文学质量专项扣分」4 条至运行时版（高）；② consistency 等四维度引入 0.5 分步长（中）
- **优先级**：高（扣分细则恢复）/ 中（分步长）
- **创造力影响**：中性

## 10. writing/repair_literary_quality.j2（REPAIR_GUARDRAIL 侧，遗留）

- **元信息**：TEXT_ONLY；`_LEGACY_TEMPLATE_ALLOWLIST` 之一（未注册 TaskType，由 guardrail_repair 等调用）
- **诊断**：
  - 【稳定】legacy 模板不在 `_TASK_TEMPLATE_MAP` 中（`test_prompt_template_registry_coverage.py` 白名单），但仍在被调用——属于双轨运行风险；`low_quality_segments` 驱动修复的模式与 tickets 模式并存
- **建议**：确认调用点是否仍活跃；若活跃，迁移到 `checking/guardrail_repair.j2` 统一通道，随后从 allowlist 移除（中）
- **优先级**：中
- **创造力影响**：中性

---

## 写作族优先级汇总表

| 模板 | 优先级 | 核心问题 | 建议动作 |
|------|--------|----------|----------|
| bridge_chapter.j2 | 中 | 字段清单依赖 builder 注入 | 格式层显式列 10 字段 |
| draft_chapter.j2 | 高 | 规则超载/与宏重复 | 17→13 条瘦身，通道约束放宽 |
| draft_scene.j2 | 低 | 运行时已有跨场景负向约束 | 保持并以真实样本复核 |
| wave_chapter.j2 | 高 | 自检与运行时验收脱节 | 机检条件移交系统，自报 miss 协议 |
| edit_chapter.j2 | 中 | tickets 与旧报告双轨 | 调用点收敛单通道 |
| edit_draft.j2 | 低 | 无 tickets 机制 | 可选补 min ticket |
| polish_chapter.j2 | 低 | 段落长度规则过机械 | 对话段除外 |
| patch_chapter.j2 | 无 | 核对通过（精确匹配、顺序与不重叠） | - |
| evaluate_draft.j2 | 高 | 运行时版缺失文学质量专项扣分 4 条 + 维度分粒度粗 | 恢复扣分细则 + 0.5 分步长 |
| repair_literary_quality.j2 | 中 | legacy 双轨 | 迁移后移除 allowlist |
