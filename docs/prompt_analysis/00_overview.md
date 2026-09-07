# 提示词系统性分析报告（总览）

> **分析范围**：全部 154 个运行时 Jinja2 模板（129 个注册任务模板 + 25 个支持模板），**分析基准为运行时目录 `novel_forge/prompts/packs/zh/templates/`**（registry 实际加载路径），并与旧副本 `novel_forge/prompts/prompts/` 逐文件比对。支持模板为 `_base/` 20、根级兼容宏 2、`_styles/` 1、`checking/` 未注册共享宏 2。
> **⚠️ 重大前提（G-13）**：两个目录存在 53 个文件内容分叉 + 3 个宏仅存在于运行时目录 + 1 个遗留模板仅存在于旧副本——本报告所有结论均以运行时目录为准，旧副本差异已在 G-13 中专项列出
> **分析目标**：在不压制 AI 创造力的前提下，同时提升 ① 创作质量 ② 生成稳定性 ③ 格式正确性
> **分析方法**：模板正文 × 格式契约（`core/format_contracts.py`，142 个 TaskType 契约；验证脚本因动态配置分支渲染 144 个变体）× 响应 Schema（`core/parsing/response_schemas.py`）× 解析/校验代码 × 调用点参数（temperature / max_tokens / retry）五方交叉核对；结论均附证据（文件:行号）。2026-08-01 的复核与执行记录见 [08_validation_and_execution.md](08_validation_and_execution.md)。
> **基线校验**（全部通过，说明元数据/语法层无缺失）：
> - `scripts/verify_templates.py`：154 个模板语法全部正常
> - `scripts/lint_prompt_layers.py`：129 个注册任务模板分层规范 PASS（checking=46, planning=22, initialization=21, kernel=11, writing=9, tts=8, compression=5, summary=5, beats=2；注：checking 目录另有 2 个未注册共享宏 `_causal_core`/`_continuity_core`，实际 48 个 .j2）
> - `scripts/audit_prompt_format_layers.py --all`：strict-json=115 / text=13 / freeform-json=1，lint issues=0
> - `scripts/verify_format_contracts.py`：131 个 JSON 契约 + 13 个 TEXT 契约全部可渲染 PASS
> - 注：上述脚本只验证「元数据↔契约」一致性，不验证「模板正文声明字段 ↔ 契约 ↔ 解析器」三方一致性——本报告第 07 章矩阵补上这一层

---

## 1. 报告文件索引

| 文件 | 内容 | 模板数 |
|------|------|--------|
| `00_overview.md`（本文件） | 方法论、全局问题清单、优先级落地路线图 | - |
| `01_novel_writing.md` | 写作族：bridge / draft / draft_scene / wave / edit / edit_draft / polish / patch / evaluate；另说明 1 个仅存在于旧副本的 legacy 模板 | 9（运行时） |
| `02_novel_planning.md` | 规划族：plan_chapter / plan_chapter_scenes / plan_chapter_contracts / plan_outline* / blueprint / short_blueprint 等 | 22 |
| `03_novel_checking.md` | 检查修复族：check_* / continuity / causal / reading_power / guard / humanize / critic / editorial / book_consistency 等 | 48 |
| `04_novel_init_kernel.md` | 初始化（21）+ kernel 提取（11）+ 摘要（5）+ 压缩（5）+ beats（2） | 44 |
| `05_tts_module.md` | 配音模块全部 8 个注册任务模板 | 8 |
| `06_base_macros.md` | `_base/` 共享宏（20）+ 根级兼容层（2）+ `_styles/`（1） | 23 |
| `07_format_matrix.md` | 模板 ↔ 契约 ↔ Schema ↔ 解析器对照矩阵（142 个 TaskType 契约；动态渲染为 144 个变体） | 142 |

## 2. 方法论

每个模板条目统一按六段式产出：
1. **元信息**：TaskType / 契约模式（ContractMode）/ 输出类型（OutputKind）/ 温度与重试（调用点证据）/ 所属链路
2. **问题诊断**：按四维度标注——【质量】创作质量、【稳定】生成稳定性、【格式】格式正确性、【创造】创造力压制风险；每条带证据
3. **改进建议**：可操作、与架构约束兼容（frozen dataclass、契约不可运行时修改、PipelineStep 框架）
4. **修改片段示例**：仅对高优先级问题给出（不整文件重写，落地时按示例局部替换）
5. **优先级**：高（解析失败/字段缺失/质量硬伤）> 中（稳定性或质量显著影响）> 低（优化项）
6. **创造力影响评估**：松绑 / 收紧 / 中性

**创造力保护总原则**（贯穿全部建议）：
- 约束指向「边界」而非「风格」：规定不做什么、保留什么，不规定必须怎么写
- 硬约束必须可验证（下游有校验器）；纯风格偏好一律标 P2 软约束
- 建议中凡涉及收紧的条目，均同时给出对应的创作自由度补偿项

## 3. 全局问题清单（按严重度排序）

### P0 级（高优先级，落地首选）

| # | 问题 | 位置 | 类型 | 影响 |
|---|------|------|------|------|
| G-13 | **双目录漂移（P0 工程问题）**：运行时实际加载 `packs/zh/templates/`（registry `_PROMPTS_DIR`，含 `INDEX.md` 生成物），但 `prompts/prompts/` 存在 53 个文件内容分叉（34 个旧版更旧、13 个旧版更长但更旧、部分重构方向相反）、3 个宏（`_cognitive_constraints`/`_retrieval_evidence`/`_summary_pass`）仅存在于运行时目录、`repair_literary_quality.j2` 仅存在于旧副本。若开发者编辑旧副本（如本报告分析初期的误判来源），运行时模板引用缺失宏将直接渲染失败 | `packs/zh/templates/` vs `prompts/prompts/` | 稳定/工程 | 双目录同时被 git 跟踪（164/166 文件）且有独立提交历史；模板修改可能不生效或直接破坏运行时；必须确立单一事实源并同步/废弃旧副本 |
| G-01 | ~~`generate_dubbing_script.j2` 编号断裂~~ → **已修正（运行时版）**：`packs/zh/templates/tts/generate_dubbing_script.j2` 任务列表已重写为连续 1-14（含「平台原生导演指令/口语适配改写/引号不等于对白/按听感分句/事件音效要精准」），编号问题仅存在于旧副本 `prompts/prompts/`（G-13 的一部分） | `tts/generate_dubbing_script.j2`（旧副本） | 稳定 | 落地时以运行时目录为基准；无需修复编号，但需同步旧副本 |
| G-02 | ~~`beats_to_draft.j2` 条件分支编号混乱~~ → **已修正（运行时版）**：分段、整篇与共同表达规则已分为具名小节，约束内容不变 | `beats/beats_to_draft.j2` | 稳定 | 关闭：不再依赖跨条件分支的连续编号 |
| G-03 | `extract_canon_delta.j2` 约束第 2 条要求「顶层只保留 7 个字段」与契约/解析器实际字段数（`canon_delta`/`creative_report`/`chapter_exit_state`/`character_state_deltas`/`relationship_deltas`/`plot_thread_deltas`/`structured_summary` = 7，核对一致）。性别值现由 `CharacterNormalizer` 与 StoryKernel 合并边界统一白名单化：常见英文别名映射为男/女，未知值降为空，不能写入 canon。 | `kernel/extract_canon_delta.j2`、`normalizers.py`、`common/utils.py` | 格式 | 已关闭（回归测试覆盖） |
| G-04 | `EXTRACT_CANON` / `HUMANIZE_SCAN` / `ADJUDICATE_STATE_DELTA` / `ADJUDICATE_CONTRACT_COMPLETION` / `ADJUDICATE_FINAL_STATE` / `EXTRACT_CANDIDATE_STATE_DELTAS` 六个任务 `require_native_structured_output=True`：**已确认硬依赖**——`router.py:1519-1554` 对不支持原生 Schema 的 route 整体拒绝，全部不满足时抛 `ModelGatewayError`（无 prompt-only 降级） | `format_contracts.py:3416` 等、`router.py:1519-1554` | 稳定/格式 | 冷启动或本地模型场景下这六个任务直接失败；建议文档化或增加降级开关 |

### P1 级（中优先级）

| # | 问题 | 位置 | 类型 | 影响 |
|---|------|------|------|------|
| G-05 | TTS 生成/改写任务温度偏高与文本保真硬校验冲突：`tts_script_generation_temperature=0.5`（+top_p 0.95）、`tts_spoken_rewrite_temperature=0.45`，而 `_validate_script_text_fidelity` 对 `segments[].text` 做逐字拼接校验、`validate_spoken_rewrite` 做长度比例+序列相似度校验——温度越高，改写/增删概率越大，重试成本越高 | `config.py:1327-1342`、`generate_script_step.py:942-946`、`spoken_text_rewrite.py:236-262` | 稳定 | 高温度 × FULL_OBJECT 契约 × 逐字校验 = 重试率上升；建议生成 0.5→0.3、改写 0.45→0.3 区间做 A/B |
| G-06 | ~~`plan_outline.j2` 与 `generate_config.j2` 的条件分支编号重复~~ → **已修正（运行时版）**：专属条件改为具名约束小节。`derive_editorial_contract.j2` 当前为连续 1–13、`adjudicate_final_state.j2` 当前为连续 1–22，原报告对后二者的跳号判断已过期。 | 各模板 | 稳定 | 关闭已验证的编号卫生问题；其余模板须按实际单次渲染结果核对 |
| G-07 | `draft_chapter.j2` 规则密度过高：17 条写作规则 + 4 条开场硬约束 + 8 个质量宏 + 字数自检 + 交付核验，总提示词估计 8-12k token；P0/P1/P2 分级依赖模型自觉遵守 | `writing/draft_chapter.j2` | 稳定/质量 | 注意力稀释是合理假设，但须用渲染 token、重试率与质量样本验证；模板已调用 `render_pre_delivery_checklist`，后续应去重而非再次迁移 |
| G-08 | `wave_chapter.j2` 的后置条件（**6 项**：cross-ref ≥80%、pacing_curve、scene anchors、字数 ±30%、POV 一致、required_outcome 覆盖）在 `wave_step.py` 中 **never raise**（失败仅记 warning） | `wave_step.py` | 稳定 | 这是流水线策略，而非仅提示词问题；是否升级为硬门需单独设计恢复与回归测试 |

### P2 级（低优先级）

| # | 问题 | 位置 | 类型 |
|---|------|------|------|
| G-09 | `evaluate_draft.j2`（运行时版）七维度均为 5×2 分子项 → 维度分被限制在整数/半档粒度，`overall_score` 粒度粗糙，`passed=6.0` 判定对 6.0-6.9 区间区分度低；且运行时版已删除「文学质量专项扣分」4 条（身体反应模板化/角色语言同质化/意象缺乏新鲜感/场景结构单一——仅存于旧副本） | `writing/evaluate_draft.j2`（运行时版 254 行） | 质量 | 反 AI 味扣分细则在运行时缺失，style 维度降分依据不足；建议恢复并补充至运行时版 |
| G-10 | ~~`emotion_label.j2` 漏列 `determined`/`playful`~~ → **已修正**：模板枚举现与 15 值 `EmotionTag` 对齐 | `tts/emotion_label.j2`、`tts/schemas.py` | 格式 | 关闭 |
| G-11 | ~~旁白档案示例缺少防照抄说明~~ → **已修正**：示例前已声明必须按作品重新推导。原“解析器缺范围校验”结论不成立：解析器会钳制速度与 modifier 范围。 | `tts/build_narrator_profile.j2`、`build_narrator_profile_step.py` | 格式/创造 | 关闭提示词问题；无需重复添加解析校验 |
| G-12 | `_base/_quality_standards.j2` 的 `forbidden_system_markers` / `anti_exposition_rules` 等宏在 20+ 模板中重复展开，相同规则文本多次出现于同一提示词（如 draft 同时引入 `anti_exposition_rules` 与 `creative_specificity_rules` 且各带不同 title） | `_base/_quality_standards.j2` | 质量/成本 | 提示词长度膨胀、token 成本上升；建议按 stage 裁剪宏调用参数（已有 `title=""` 机制，可进一步压缩重复内容） |

## 4. 按模块的全局观察

### 4.1 小说模块（146 个模板）

**优势（应保持，勿因优化破坏）**：
- 卡片化架构：`stage_cards` 分层（source/contract/plan/characters/style/quality…）+ P0/P1/P2 消费优先级，是全仓库最成熟的提示词工程范式
- 反 AI 味体系完备：`_quality_standards.j2` 15 个宏覆盖机械句式、意象复用、说明腔、元叙事、感官多样性、情感工艺
- 创造力保护已内建：`check_alignment.j2` 专设「修复建议风格（保证创作自主性）」章节；`plan_chapter.j2` 的「先思考后落字段」和字段级对比示例（✗/✓）是高质量引导
- 一致性锚点密度高：opening_bridge 硬锁定、required_literals 字面合同、cognitive_constraints 认知分级、POV 知识边界——这些是「不压制创造力但约束边界」的正确实现

**共性问题**：
1. **规则总量超载**：写作/修复类模板普遍 10+ 条编号规则 + 多个宏展开，长尾规则执行率递减（证据：draft_chapter 17 条、wave 11 条、edit 10 条）
2. **编号卫生**：6 个模板存在条件分支编号冲突（见 G-02/G-06）
3. **验收脱节**：模板内「自检清单」与运行时硬门（quality_gate）边界不清，模型自检通过≠系统验收通过，且系统不把验收失败回灌提示词
4. **负面示例缺失**：多数规则只有正向指令，缺少「✗ 错误示范」；仅 plan_chapter / check_* 少量模板有 ✗/✓ 对比

### 4.2 配音模块（8 个模板）

**优势**：
- 文本保真契约（`segments[].text` 逐字一致 + 拼接校验）是商业级配音脚本的正确基线
- 平台能力档案注入（`rewrite_profile`）实现「通用字段 + 平台私有扩展」分离，避免模板与平台耦合
- 确定性回退完备：情绪标注的枚举回退、speaker 裁决的先验保留规则、审校的 manual_review 通道

**共性问题**：
1. 运行时编号已连续；旧兼容副本仍可能与运行时内容分叉（G-01/G-13）
2. 温度与保真校验冲突（G-05）
3. 指令密度同样偏高：generate_dubbing_script 任务 14 条 + 约束 15 条 + 改写细则 3 段，模型注意力分配风险
4. 模板与 Schema 字段对齐总体良好；`emotion_label` 的合法枚举遗漏已修正（G-10）

## 5. 优先级落地路线图

### 批次 P0（先做，低风险高收益）
0. **G-13 双目录同步（最高优先）**：确立 `packs/zh/templates/` 为单一事实源；将 `prompts/prompts/` 标记为废弃副本或改为软链接/构建产物；补齐 CI 校验（如新增 `check_prompt_dir_sync.py` 对比两目录）
1. 编号卫生的已验证问题已在运行时模板修复；兼容副本是否同步需先决定维护策略
2. 为六个 `require_native_structured_output=True` 任务确认/补齐降级路径（或明确文档化不可用场景）
3. `wave_chapter.j2` 字数/POV 自检升级为运行时硬门（改 `wave_step.py` 校验强度，模板同步删去「自检」表述改为「系统将核验」）

### 批次 P1（中期）
4. TTS 生成/改写温度下调至 0.3 区间并做 A/B 质量对比（遵守「质量不可降」原则：对比 eval 指标与重试率）
5. 对其他模板按“单次实际渲染”复核编号；不要按旧文档对已连续编号的模板做机械改动
6. 用真实渲染 token、重试率与质量样本评估 `draft_chapter`，仅去除已测得的重复约束

### 批次 P2（持续优化）
7. evaluate_draft 引入 0.5 分步长与负向示例
8. emotion_label 枚举已经与 EmotionTag 统一；持续以 Schema 为唯一枚举源
9. 旁白档案示例已补防照抄说明；保留解析器钳制作为兜底
10. 宏按 stage 参数化裁剪，压缩重复规则文本

## 6. 创造力保护自检（本报告建议的净效应）

| 建议类别 | 数量 | 松绑 | 收紧 | 中性 |
|----------|------|------|------|------|
| 编号/格式修复（G-01/02/06/08/11，其中 G-01 运行时已修复） | 6 | 0 | 0 | 6 |
| 双目录同步（G-13） | 1 | 0 | 0 | 1（工程治理） |
| 温度/参数调整（G-05） | 1 | 0 | 1（参数，非提示词） | 0 |
| 规则瘦身/恢复（G-07/12 + evaluate 扣分恢复） | 3 | 2 | 0 | 1 |
| 验收对齐（G-04/08） | 2 | 0 | 1 | 1 |
| 质量增强（G-09/10） | 2 | 1 | 0 | 1 |

净效应：**无一条建议新增「必须怎么写」的指令**；收紧项均为运行时参数或验收强度，不改变提示词对创作空间的授权；瘦身项释放模型注意力给 P0 硬约束，间接扩大有效创作空间。
