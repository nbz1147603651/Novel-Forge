# 提示词模板索引

> 本文件由 `scripts/generate_prompt_index.py` 根据 `registry.py`、`format_contracts.py` 与模板备注自动生成。请不要手工维护模板清单。

## 模板概览

- 注册任务模板：113
- 共享宏 / 局部模板：17
- 未注册独立模板：0
- JSON 输出任务模板：100
- TEXT 输出任务模板：13

| 类别 | 中文名 | 文件数 |
|------|--------|--------|
| `beats/` | 节拍 | 2 |
| `checking/` | 检查 | 43 |
| `compression/` | 压缩 | 5 |
| `initialization/` | 初始化 | 18 |
| `kernel/` | kernel | 11 |
| `planning/` | 规划 | 20 |
| `summary/` | 总结 | 5 |
| `writing/` | 写作 | 9 |

## 注册任务模板

### beats/ - 节拍

| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |
|---------|----------|------|----------|------|
| [beats_to_draft.j2](beats/beats_to_draft.j2) | `draft` | TEXT | - | 根据节拍列表（Beats）、叙事蓝图（Blueprint）和执行约束（ExecutionPlan） 撰写短篇小说正文，支持分段（SegmentPlan）模式。 |
| [spec_to_beats.j2](beats/spec_to_beats.j2) | `beats` | JSON | `beats`（强制） | 根据故事规格（Spec）和叙事蓝图（Blueprint）生成结构化的故事节拍 （Beats）列表，每个节拍包含 sequence, beat_type, summary, tension_level, characters_involved, setting, emotional_note。 |

### checking/ - 检查

| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |
|---------|----------|------|----------|------|
| [adjudicate_blueprint_coherence.j2](checking/adjudicate_blueprint_coherence.j2) | `adjudicate_blueprint_coherence` | JSON | `verdict`、`issues`、`source_refs`、`repair_scope`、`preserve`、`change_intent`、`blocked`、`summary`（强制） | 检查叙事蓝图内部是否存在语义顺序、兑现、高潮消费或状态互斥冲突。 |
| [adjudicate_contract_coherence.j2](checking/adjudicate_contract_coherence.j2) | `adjudicate_contract_coherence` | JSON | `verdict`、`issues`、`summary`（强制） | 检查初始化叙事契约是否存在角色弧线、伏笔、世界规则或章节任务冲突。 |
| [adjudicate_contract_completion.j2](checking/adjudicate_contract_completion.j2) | `adjudicate_contract_completion` | JSON | `verdict`、`severity`、`rationale`、`repair_or_replan_decision`、`missing_required_progressions`、`missing_knowledge_ops`、`forbidden_progression_hits`、`future_leak_hits`、`evidence_quotes`、`should_block_archive`、`contract_completion_score`（强制） | 判断章节正文是否按 ChapterContract 执行，并识别禁止推进/未来泄露。 |
| [adjudicate_fact_conflict.j2](checking/adjudicate_fact_conflict.j2) | `adjudicate_fact_conflict` | JSON | `verdict`、`severity`、`rationale`（强制） | 在当前状态与证据窗口之间裁定候选事实冲突是否成立。 |
| [adjudicate_init_conflict_candidates.j2](checking/adjudicate_init_conflict_candidates.j2) | `adjudicate_init_conflict_candidates` | JSON | `verdict`、`issues`、`source_refs`、`repair_scope`、`preserve`、`change_intent`、`blocked`、`summary`（强制） | 对结构化检索与记忆召回得到的候选 claims 组做语义裁判，并给出局部修复 scope。 |
| [adjudicate_outline_inheritance.j2](checking/adjudicate_outline_inheritance.j2) | `adjudicate_outline_inheritance` | JSON | `verdict`、`issues`、`source_refs`、`repair_scope`、`preserve`、`change_intent`、`blocked`、`summary`（强制） | 检查章节大纲是否忠实继承蓝图，且没有新增蓝图之外的语义冲突。 |
| [adjudicate_state_delta.j2](checking/adjudicate_state_delta.j2) | `adjudicate_state_delta` | JSON | `candidate_id`、`verdict`、`severity`、`rationale`（强制） | 对单个候选状态变化进行受限叙事裁判，决定 accept/reject/ambiguous/needs_repair/defer。 |
| [book_consistency.j2](checking/book_consistency.j2) | `book_consistency`<br>`book_consistency_naming`<br>`book_consistency_timeline`<br>`book_consistency_world_rule`<br>`book_consistency_character_state`<br>`book_consistency_plot_thread`<br>`book_consistency_narrative_drift` | JSON | `issues`、`repair_plan`、`summary`、`consistency_score`（强制） | 对长篇小说已完成章节进行全局诊断，输出证据定位、全局发现与修复队列候选。 本阶段不直接执行修复；修复队列执行是独立入口。 |
| [book_consistency_verify.j2](checking/book_consistency_verify.j2) | `book_consistency_verify` | JSON | `verified_issues`（强制） | 对全书审计中关于某章的问题清单进行逐条验证，判定问题是否真实存在。 |
| [book_editorial_audit.j2](checking/book_editorial_audit.j2) | `book_editorial_audit`<br>`book_editorial_structure_audit`<br>`book_editorial_voice_audit`<br>`book_editorial_language_audit`<br>`book_editorial_theme_symbol_audit`<br>`book_editorial_element_audit` | JSON | `summary`、`findings`、`revision_plan`、`metrics`（强制） | 依据 EditorialContract 审计全书结构、声纹、语言、主题、象征、标题 和时间桥，输出修订计划，不执行一致性修复。 |
| [causal_repair_typed.j2](checking/causal_repair_typed.j2) | `repair_causal` | TEXT | - | 只修复 causal validation 指出的因果问题，按 issue 类型定位窗口， 不重新规划章节、不引入新主线。 |
| [causal_validate.j2](checking/causal_validate.j2) | `validate_causal` | JSON | `causal_score`、`issues`（强制） | 对章节正文进行因果链深度审核，输出因果验证报告（支持全量/点验两种模式）。 |
| [check_alignment.j2](checking/check_alignment.j2) | `check_alignment` | JSON | `alignment_score`、`summary`（强制） | 检查章节正文与章节大纲的对齐程度，输出对齐分、可执行修正建议。 |
| [check_chapter.j2](checking/check_chapter.j2) | `check_chapter` | JSON | `risk_level`、`summary`、`prompt_leaks`、`factual_errors`、`continuity_errors`、`expression_errors`、`repair_actions`、`forbidden_element_findings`（强制） | 对章节正文进行本章内部质量与提示误读风险检查，输出风险等级 与可执行的修复动作列表。 |
| [check_editorial.j2](checking/check_editorial.j2) | `check_editorial` | JSON | `summary`、`findings`、`revision_plan`、`metrics`（强制） | 依据 EditorialContract 检查单章的出版级编辑问题，输出软报告。 |
| [continuity_eval.j2](checking/continuity_eval.j2) | `check_continuity` | JSON | `continuity_score`、`issues`（强制） | 评估章节间的跨章连贯性，输出 ContinuityReport（支持全量/点验两种模式）。 |
| [continuity_repair.j2](checking/continuity_repair.j2) | `repair_continuity` | TEXT | - | 只根据 continuity_report 和 repair_plan 修复正文中被定位的问题。 本阶段不得重写结构化产物，不得重新规划整章。 |
| [critic_causal.j2](checking/critic_causal.j2) | `critic_causal` | JSON | `causal_breaks`（强制） | 章内因果链审查器，检查本章是否存在"关键因果断裂"。 |
| [critic_character.j2](checking/critic_character.j2) | `critic_character` | JSON | `issues`（强制） | 角色一致性审查器，检查角色在本章是否与正史冲突（支持批量/单角色两种模式）。 |
| [critic_continuity.j2](checking/critic_continuity.j2) | `critic_continuity` | JSON | `issues`（强制） | 跨章连贯性审查器，检查"上一章 → 本章"之间的连贯性问题。 |
| [critic_strengths.j2](checking/critic_strengths.j2) | `critic_strengths` | JSON | `strengths`（强制） | 章节亮点评审器，识别"有证据的优势"，不输出空泛夸赞。 |
| [derive_editorial_character_voices.j2](checking/derive_editorial_character_voices.j2) | `derive_editorial_character_voices` | JSON | `character_voices`（强制） | 只生成 EditorialContract.character_voices，避免全契约单次生成过载。 |
| [derive_editorial_contract.j2](checking/derive_editorial_contract.j2) | `derive_editorial_contract` | JSON | `character_voices`、`climax_markers`、`denouement_budget`、`theme_policies`、`symbol_policies`、`scene_resistance_rules`、`revelation_ladder`、`editorial_element_directives`、`time_bridge_policies`、`title_policy`（强制） | 基于初始化产物生成全书唯一编辑质量契约，约束声纹、高潮余波、主题、 象征物、揭示阶梯、叙述要素指令、表达通道和场景阻力。 |
| [derive_editorial_element_directives.j2](checking/derive_editorial_element_directives.j2) | `derive_editorial_element_directives` | JSON | `editorial_element_directives`（强制） | 只把已选择叙述要素转译为项目级编辑指令。 |
| [derive_editorial_structure.j2](checking/derive_editorial_structure.j2) | `derive_editorial_structure` | JSON | `climax_markers`、`denouement_budget`、`revelation_ladder`、`time_bridge_policies`、`title_policy`（强制） | 只生成高潮余波、揭示阶梯、时间桥和标题复用策略。 |
| [derive_editorial_style_constraints.j2](checking/derive_editorial_style_constraints.j2) | `derive_editorial_style_constraints` | JSON | `theme_policies`、`symbol_policies`、`scene_resistance_rules`、`expression_channel_budget`、`expression_channel_profiles`、`body_signal_budget_per_high_emotion_scene`、`forbidden_confirmation_phrases`、`revision_priorities`（强制） | 只生成主题呈现、象征物解释、场景阻力和表达通道预算。 |
| [element_progress_arbiter.j2](checking/element_progress_arbiter.j2) | `element_progress_arbiter` | JSON | `status`、`confidence`、`reason`（强制） | 对规则命中的灰区结果进行低频复判，输出统一的 hit/weak/miss 判定。 |
| [evaluate_reading_power.j2](checking/evaluate_reading_power.j2) | `evaluate_reading_power` | JSON | `hook_type`、`hook_strength`、`hook_description`、`prev_hook_fulfilled`、`micro_payoffs`、`is_transition`、`next_chapter_reason`、`information_pacing`、`main_plot_depth`、`tension_match`、`character_drive`（强制） | 评估章节的追读力，包括章尾钩子、上章钩子兑现、微兑现等维度。 |
| [extract_blueprint_holistic_claims.j2](checking/extract_blueprint_holistic_claims.j2) | `extract_blueprint_holistic_claims` | JSON | `claims`（强制） | 完整阅读叙事蓝图，抽取跨字段依赖、承诺、状态轴、伏笔/payoff 与不可逆事件 claims。 |
| [extract_expression_observations.j2](checking/extract_expression_observations.j2) | `extract_expression_observations` | JSON | `observations`、`skipped_reason`、`source_text_hash`（强制） | - |
| [extract_init_coherence_claims.j2](checking/extract_init_coherence_claims.j2) | `extract_init_coherence_claims` | JSON | `claims`（强制） | 从蓝图、大纲或章节契约分块中抽取可检索、可裁判的通用叙事事实。 |
| [extract_knowledge_deltas.j2](checking/extract_knowledge_deltas.j2) | `extract_knowledge_deltas` | JSON | `knowledge_deltas`（强制） | 从章节正文中提取角色新获得或揭示的知识，输出结构化 knowledge_deltas。 |
| [guardrail_repair.j2](checking/guardrail_repair.j2) | `repair_guardrail` | TEXT | - | 根据护栏违规报告进行定向修复，严格保护主线推进点和大纲对齐关系。 |
| [humanize_paragraph_rewrite.j2](checking/humanize_paragraph_rewrite.j2) | `humanize_paragraph_rewrite` | TEXT | - | 对含不可精确替换 AI 痕迹的段落执行定向改写，仅去除 AI 生成痕迹， 保留原文叙事内容、因果关系和角色状态。 |
| [humanize_scan.j2](checking/humanize_scan.j2) | `humanize_scan` | JSON | `total_hits`、`hits_by_category`、`critical_hits`、`pattern_hits`、`summary`（强制）；`humanize_score` 由本地确定性公式计算 | 对本地预扫描/拟人化库候选进行 AI 痕迹裁判，确认真实命中、 严重性和安全局部修复边界，为下游拟人化修复提供有界输入。 |
| [knowledge_boundary_audit.j2](checking/knowledge_boundary_audit.j2) | `knowledge_boundary_audit` | JSON | `verdict`、`issues`（强制） | 判断章节正文是否泄露当前 POV/当前章节不应可见的隐藏知识。 |
| [macro_guard_audit.j2](checking/macro_guard_audit.j2) | `macro_guard_audit` | JSON | `dimensions`、`recommended_action`、`drift_score`、`findings`、`adjustment_plan`、`confidence`、`reasoning`（强制） | 从宏观角度审计最近N章的创作轨迹，判断是否存在累积性大纲偏离。 |
| [repair_adjudicated_issue.j2](checking/repair_adjudicated_issue.j2) | `repair_adjudicated_issue` | TEXT | - | 根据 LLM 裁判给出的修复要求，对章节正文做局部修复并返回可合并的正文段落。 |
| [repair_knowledge_boundary.j2](checking/repair_knowledge_boundary.j2) | `repair_knowledge_boundary` | JSON | - | 定向改写窗口文本中 POV 角色不应知道的越界认知，保持戏剧意图但 移除全知信息。仅改写违规段落 ± 2 段窗口。 |
| [repair_reading_power.j2](checking/repair_reading_power.j2) | `repair_reading_power` | TEXT | - | 只修复阅读驱动力问题：上章钩子回应、本章微兑现、章尾钩子。 不重新规划主事件，不引入新 POV 或后续章节揭秘。 |
| [repair_semantic_verify.j2](checking/repair_semantic_verify.j2) | `repair_semantic_verify` | JSON | `issue_resolved`、`confidence`、`reasoning`（强制） | 修复完成后，调用 LLM 语义验证原始问题是否真正被解决，而非仅做字符串匹配。 |
| [structure_profile_derive.j2](checking/structure_profile_derive.j2) | `profile_structure` | JSON | `hook_config`、`strand_config`、`micro_payoff_config`、`cool_point_config`（强制） | 专注推导叙事结构相关的四个核心配置（钩子/情节线/微兑现/爽点）。 |
| [style_profile_derive.j2](checking/style_profile_derive.j2) | `profile_style` | JSON | `modules`、`source_elements`、`summary`、`global_style`（强制） | 根据项目设定要素推导本作品的写作风格规范模块和全局参数。 |

### compression/ - 压缩

| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |
|---------|----------|------|----------|------|
| [adaptive_compress.j2](compression/adaptive_compress.j2) | `adaptive_compress` | JSON | `items`（强制） | 根据上下文类型自适应调整压缩策略，在保持关键事实的前提下智能压缩 |
| [context_compress.j2](compression/context_compress.j2) | `context_compress` | JSON | `items`（强制） | 在不改变剧情方向和事实约束的前提下，压缩写作上下文片段 |
| [guard_constraint_check.j2](compression/guard_constraint_check.j2) | `guard_constraint_check` | JSON | `status`、`confidence`、`evidence`、`notes`（强制） | 评估本章正文是否遵守了上一章 AI 护栏裁决器给出的约束 |
| [plot_guard_judge.j2](compression/plot_guard_judge.j2) | `plot_guard_judge` | JSON | `decision`、`risk_level`、`outline_action`、`entity_actions`、`next_chapter_constraints`、`reasoning_brief`、`targeted_repairs`（强制） | 基于当前章节质量信号，裁决自动续写策略（继续/约束/调整大纲/暂停） |
| [verify_compression.j2](compression/verify_compression.j2) | `verify_compression` | JSON | `quality_score`、`recommendation`（强制） | 对比原文和压缩后文本，评估压缩质量并判定是否通过 |

### initialization/ - 初始化

| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |
|---------|----------|------|----------|------|
| [adjudicate_character_introduction.j2](initialization/adjudicate_character_introduction.j2) | `adjudicate_character_introduction` | JSON | `decisions`、`summary`（强制） | 在自动建档前，判断结构化候选是否真是值得建档的新角色、已有角色别名或临时叙事标签 |
| [enrich_character.j2](initialization/enrich_character.j2) | `enrich_character` | JSON | `appearance`、`personality`、`backstory`、`arc`（强制） | 根据角色在章节中的实际表现，补全角色的详细设定（外貌、性格、背景、弧线） |
| [enrich_spec.j2](initialization/enrich_spec.j2) | `spec_enrich` | JSON | `title`、`genre`、`theme`、`tone`、`length_target`、`language`、`characters_hint`、`world_hint`、`conflict_hint`、`pov_hint`、`opening_style`、`ending_style`、`extra_instructions`（强制） | 根据用户提供的简短构想，补全关键创作要素（标题、冲突、角色、开篇、结尾） |
| [generate_config.j2](initialization/generate_config.j2) | `generate_config`<br>`polish_config` | JSON | `characters_hint`、`conflict_hint`、`ending_style`、`extra_instructions`、`genre`、`language`、`length_target`、`max_edit_rounds`、`opening_style`、`pov_hint`、`project_id`、`theme`、`title`、`tone`、`world_hint`、`writing_mode`、`polish_suggestions`、`creative_note`（强制） | 根据用户简要描述生成完整创作配置方案，或对已有配置进行定向润色 |
| [init_character_arc_plan.j2](initialization/init_character_arc_plan.j2) | `init_character_arc_plan` | JSON | `character_arcs`（强制） | 基于同一 roster 生成关键角色起点到终点的变化弧线。 |
| [init_character_bible.j2](initialization/init_character_bible.j2) | `init_character_bible` | JSON | `character_bible`（强制） | 根据世界观设定和故事前提，生成完整的角色档案（CharacterBible）， 为后续规划与写作提供角色约束。 |
| [init_character_profile_batch.j2](initialization/init_character_profile_batch.j2) | `init_character_profile_batch` | JSON | `character_profiles`（强制） | 根据稳定 roster 为每个角色补全 CharacterProfile 字段，避免一次性输出全角色大 JSON。 |
| [init_character_relationship_matrix.j2](initialization/init_character_relationship_matrix.j2) | `init_character_relationship_matrix` | JSON | `relationship_matrix`（强制） | 基于同一 roster 生成角色间关系矩阵，避免档案分片彼此割裂。 |
| [init_character_roster.j2](initialization/init_character_roster.j2) | `init_character_roster` | JSON | `character_roster`（强制） | 先确定全书核心角色名单与功能，作为后续档案/关系/弧线的共同锚点。 |
| [init_entity_registry.j2](initialization/init_entity_registry.j2) | `init_entity_registry` | JSON | `entities`（强制） | 从项目初始化资料识别角色、地点、物品、组织和概念实体，建立稳定实体注册表。 |
| [init_knowledge_boundaries.j2](initialization/init_knowledge_boundaries.j2) | `init_knowledge_boundaries` | JSON | `characters`（强制） | 根据角色设定和世界观设定，为每个角色生成故事开始时的知识边界， 包括已知事实、怀疑、误解、隐瞒信息和感知通道限制。 用于限知视角下的 POV 信息约束和知识账本初始化。 |
| [init_narrative_contract.j2](initialization/init_narrative_contract.j2) | `init_narrative_contract` | JSON | `world_rules`、`character_arcs`、`plot_threads`、`promise_plan`、`notes`（强制） | 生成全书可裁判叙事契约，为后续章节规划、裁判和修复提供硬约束。 |
| [init_story_bible.j2](initialization/init_story_bible.j2) | `init_story_bible` | JSON | `story_bible`（强制） | 根据用户提供的故事前提与创作意图，生成完整且自洽的世界观设定 （StoryBible），为后续所有规划与写作提供基础约束。 |
| [init_story_continuity_rules.j2](initialization/init_story_continuity_rules.j2) | `init_story_continuity_rules` | JSON | `continuity_rules`（强制） | 生成时间、称谓、自称、礼制、术语、错位词等可执行连续性约束。 |
| [init_story_core_premise.j2](initialization/init_story_core_premise.j2) | `init_story_core_premise` | JSON | `story_core`（强制） | 只生成 StoryBible 的 title/premise/tone 核心骨架，减轻完整世界观 JSON 压力。 |
| [init_story_themes_and_symbols.j2](initialization/init_story_themes_and_symbols.j2) | `init_story_themes_and_symbols` | JSON | `themes_and_symbols`（强制） | 生成 StoryBible 的 themes、banned_intent_rules 与必要 notes，避免完整 JSON 过载。 |
| [init_story_world_rules.j2](initialization/init_story_world_rules.j2) | `init_story_world_rules` | JSON | `world_rules`（强制） | 生成 StoryBible 的时代、地理、文化、技术/超自然体系与硬规则。 |
| [introduce_character.j2](initialization/introduce_character.j2) | `introduce_character` | JSON | `name`、`role`、`gender`、`social_status`、`abilities`、`appearance`、`personality`、`backstory`、`arc`、`relationships`、`voice`、`notes`（强制） | 根据大纲和世界观信息，为即将首次登场的新角色构建初始骨架档案 |

### kernel/ - kernel

| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |
|---------|----------|------|----------|------|
| [adjudicate_final_state.j2](kernel/adjudicate_final_state.j2) | `adjudicate_final_state` | JSON | `verdict`、`accepted_candidate_ids`、`pending_candidate_ids`、`repair_candidate_ids`、`should_block_archive`、`summary`（强制） | 汇总候选状态变化和逐项裁判结果，决定本章可写入、挂起或需修复的状态变更。 |
| [adjust_outline.j2](kernel/adjust_outline.j2) | `adjust_outline` | JSON | `adjusted_chapters`、`adjustment_summary`（强制） | 根据已完成章节的实际情节，重新规划后续章节大纲，使其与实际内容自然衔接 |
| [extract_candidate_state_deltas.j2](kernel/extract_candidate_state_deltas.j2) | `extract_candidate_state_deltas` | JSON | `candidates`（强制） | 从章节正文抽取可裁判的候选状态变化，只做证据定位，不做最终事实写入判断。 |
| [extract_canon_delta.j2](kernel/extract_canon_delta.j2) | `extract_canon` | JSON | `canon_delta`、`creative_report`、`chapter_exit_state`、`character_state_deltas`、`relationship_deltas`、`plot_thread_deltas`、`structured_summary`（强制） | 从章节正文提取结构化 Canon 增量，供 canon/state/memory 持久化使用 |
| [extract_canon_delta_fragment.j2](kernel/extract_canon_delta_fragment.j2) | `extract_canon_delta` | JSON | `canon_delta`（强制） | 只提取 canon_delta，使用 summary/exit 作为共同事实锚点避免分片割裂。 |
| [extract_chapter_summary_exit.j2](kernel/extract_chapter_summary_exit.j2) | `extract_chapter_summary_exit` | JSON | `chapter_exit_state`、`structured_summary`（强制） | 先提取章节事实摘要和出口状态，作为其他 Canon 分片的共同锚点。 |
| [extract_character_state_deltas.j2](kernel/extract_character_state_deltas.j2) | `extract_character_state_deltas` | JSON | `character_state_deltas`（强制） | 只提取本章明确角色状态变化，避免与关系/情节线分片互相污染。 |
| [extract_creative_report.j2](kernel/extract_creative_report.j2) | `extract_creative_report` | JSON | `creative_report`（强制） | 只提取新增角色、地点、物件、亮点和下一章建议，降低 Canon 主 JSON 压力。 |
| [extract_motifs.j2](kernel/extract_motifs.j2) | `extract_motifs` | JSON | `motifs`（强制） | 从章节正文提取可追踪母题（意象/动作/感官/颜色/声音/主题/符号） |
| [extract_plot_thread_deltas.j2](kernel/extract_plot_thread_deltas.j2) | `extract_plot_thread_deltas` | JSON | `plot_thread_deltas`（强制） | 只提取本章推进、揭示、休眠或解决的情节线变化，优先复用已有 thread_id。 |
| [extract_relationship_deltas.j2](kernel/extract_relationship_deltas.j2) | `extract_relationship_deltas` | JSON | `relationship_deltas`（强制） | 只提取本章明确的人物关系变化，并以前态关系和章节摘要为锚点。 |

### planning/ - 规划

| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |
|---------|----------|------|----------|------|
| [blueprint_element_select.j2](planning/blueprint_element_select.j2) | `blueprint_element_select` | JSON | `genre_inference`、`required_ids`、`extension_ids`、`extension_selection`、`focus_constraints`、`selector_summary`（强制） | 根据题材（Genre）、冲突类型（ConflictHint）和用户偏好，从叙事要素库 中筛选必要项和扩展项，生成 ElementSelection 输出供后续规划使用。 |
| [derive_init_coherence_profile.j2](planning/derive_init_coherence_profile.j2) | `derive_init_coherence_profile` | JSON | `genre_tags`、`narrative_modes`、`project_ontology`、`conflict_lens`、`extraction_guidance`、`summary`（强制） | 从项目设定、风格规范和叙事要素中推导题材画像与项目本体，供 claims 抽取和冲突裁判使用。 |
| [init_coherence_conflict_rules.j2](planning/init_coherence_conflict_rules.j2) | `init_coherence_conflict_rules` | JSON | `conflict_lens`（强制） | 只生成初始化一致性裁判需要关注的冲突类型和误判边界。 |
| [init_coherence_extraction_guide.j2](planning/init_coherence_extraction_guide.j2) | `init_coherence_extraction_guide` | JSON | `extraction_guidance`（强制） | 只生成后续 claims 抽取应关注的规则，避免画像精炼输出过大。 |
| [init_coherence_ontology.j2](planning/init_coherence_ontology.j2) | `init_coherence_ontology` | JSON | `genre_tags`、`narrative_modes`、`project_ontology`（强制） | 基于当前画像与蓝图，只精炼项目本体、状态轴、关系轴与术语。 |
| [init_coherence_payoff_rules.j2](planning/init_coherence_payoff_rules.j2) | `init_coherence_payoff_rules` | JSON | `payoff_types`、`summary`（强制） | 只精炼 payoff 类型、不可逆事件标记、时间标记与摘要。 |
| [plan_chapter.j2](planning/plan_chapter.j2) | `plan_chapter` | JSON | `scene_intents`、`opening_contract`、`closing_contract`、`required_state_transitions`、`chapter_type`、`emotional_arc`、`relationship_evolution`、`forbidden_elements`、`forbidden_elements_soft`、`forbidden_elements_quota`、`intentional_callbacks`、`foreshadowing_plan`、`key_revelations`、`cross_scene_intent`（强制） | 把本章大纲、桥接契约、章节执行契约路由成 scene_intents。 本阶段只做结构规划，不写正文，不做修辞展开。 |
| [plan_chapter_contracts.j2](planning/plan_chapter_contracts.j2) | `plan_chapter_contracts` | JSON | `chapter_contracts`（强制） | 将全书叙事契约拆分为当前输入章节的逐章可执行契约。 |
| [plan_chapter_scenes.j2](planning/plan_chapter_scenes.j2) | `plan_chapter_scenes` | JSON | `scene_plan`（强制） | 把单章计划拆成边界清楚、依赖明确、可验证/可分组草稿的 scene_plan。 |
| [plan_outline.j2](planning/plan_outline.j2) | `plan_outline` | JSON | `synopsis`、`volume_mode`、`volumes`、`narrative_phases`、`key_turning_points`、`character_arcs`、`subplot_plan`、`suspense_schedule`、`ending_strategy`、`emotional_arcs`、`causal_chains`、`subplot_collisions`、`subversion_points`（强制） | 根据故事规格（Spec）和叙事要素选择（ElementSelection）生成完整 叙事蓝图（Narrative Blueprint），为后续批量章节大纲生成提供结构约束。 |
| [plan_outline_batch.j2](planning/plan_outline_batch.j2) | `plan_outline_batch` | JSON | `chapters`（强制） | 根据叙事蓝图（Blueprint）批量生成指定章节范围（batch_start-batch_end） 的章节大纲（ChapterOutline），每章包含 goal、beats_summary、main_plot_points、 element_focus 等结构化信息。 |
| [plan_outline_continue.j2](planning/plan_outline_continue.j2) | `plan_outline_continue` | JSON | `chapters`（强制） | 在已生成的大纲基础上，续写指定章节范围（batch_start-batch_end） 的章节大纲，保持与前批次的连贯性。 |
| [polish_outline.j2](planning/polish_outline.j2) | `polish_outline` | JSON | `adjusted_chapters`、`polish_suggestions`（强制） | 根据用户调整方向精调章节大纲，只修改指定章节/字段 |
| [refine_init_artifacts_from_synopsis.j2](planning/refine_init_artifacts_from_synopsis.j2) | `refine_init_artifacts_from_synopsis` | JSON | `suggestions`、`repair_scope`、`patches`、`preserve`、`risks`、`summary`（强制） | 在蓝图一致性审查前，基于既有梗概与设定提出低风险、证据驱动的蓝图增强 patch。 |
| [refine_init_coherence_profile.j2](planning/refine_init_coherence_profile.j2) | `refine_init_coherence_profile` | JSON | `genre_tags`、`narrative_modes`、`project_ontology`、`conflict_lens`、`extraction_guidance`、`summary`（强制） | 在叙事蓝图生成后，反哺并精炼项目一致性画像，补全蓝图中新增的状态轴、payoff、不可逆事件和题材术语。 |
| [repair_init_artifact_patch.j2](planning/repair_init_artifact_patch.j2) | `repair_init_artifact_patch` | JSON | `patches`、`summary`（强制） | 根据裁判报告输出受限 JSON Patch，精准修复蓝图/大纲/章节契约字段。 |
| [short_blueprint.j2](planning/short_blueprint.j2) | `short_blueprint` | JSON | `synopsis`、`anchor_elements`、`narrative_phases`、`turning_points`、`character_arcs`、`emotional_arc`、`ending_strategy`（强制） | 为短篇小说生成轻量叙事蓝图（Synopsis + AnchorElements + NarrativePhases + TurningPoints + CharacterArcs + EmotionalArc + EndingStrategy），指导后续 节拍生成和草稿创作。 |
| [short_creative_summary.j2](planning/short_creative_summary.j2) | `short_creative_summary` | JSON | `characters`、`narrative_analysis`、`thematic_analysis`、`creative_highlights`、`improvement_suggestions`、`beat_fulfillment`（强制） | 对短篇小说终稿进行创作分析，从结构、人物、主题三个维度提取深层洞察， 评估蓝图/节拍落地情况，输出创作报告。 |
| [subplot_polish.j2](planning/subplot_polish.j2) | `polish_subplot` | JSON | `subplots`（强制） | 对已有支线规划进行润色和优化，增强交织关系、优化节点事件、 完善收束路径，确保支线与蓝图一致性。 |
| [validate_scene_plan.j2](planning/validate_scene_plan.j2) | `validate_scene_plan` | JSON | `valid`、`issues`、`parallel_groups`、`serial_edges`、`summary`（强制） | 检查 scene_plan 的场景边界、依赖、交接与可并行分组，给出串行边和修复意见。 |

### summary/ - 总结

| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |
|---------|----------|------|----------|------|
| [summarize_arc.j2](summary/summarize_arc.j2) | `summarize_arc` | JSON | `summary`（强制） | 将故事弧内容压缩为长期上下文注入用的高密度摘要 |
| [summarize_chapter.j2](summary/summarize_chapter.j2) | `summarize_chapter` | JSON | `summary`（强制） | 将章节正文压缩为高密度摘要，供后续创作检索和上下文注入使用 |
| [summarize_scene.j2](summary/summarize_scene.j2) | `summarize_scene` | JSON | `summary`（强制） | 将场景内容压缩为高密度摘要，供续写时快速定位上下文 |
| [summarize_volume.j2](summary/summarize_volume.j2) | `summarize_volume` | JSON | `summary`（强制） | 将多章内容压缩为卷级摘要，供后续卷创作与记忆检索使用 |
| [volume_audit.j2](summary/volume_audit.j2) | `volume_audit` | JSON | `volume_number`、`volume_summary`、`milestone_status`、`carry_over_characters`、`carry_over_items`、`carry_over_world_fact_keys`、`carry_over_foreshadowing_ids`、`next_volume_focus`（强制） | 对当前卷进行卷末审计，评估目标完成度与一致性，输出下一卷衔接方案 |

### tts/ - 配音

| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |
|---------|----------|------|----------|------|
| [tts/build_narrator_profile.j2](tts/build_narrator_profile.j2) | `tts_build_narrator_profile` | JSON | `voice_type`、`base_speed`、`emotional_range`、`narration_distance`、`style_keywords`（强制） | 根据全书大纲、体裁、基调与风格档案，调用 LLM 分析生成旁白的声音特征（音色类型、语速范围、情感表达风格、叙述距离感等），供后续合成阶段的动态语速/音量适配使用。 |
| [tts/generate_dubbing_script.j2](tts/generate_dubbing_script.j2) | `tts_generate_dubbing_script` | JSON | `segments`（强制） | 将章节正文转换为结构化配音脚本，包含角色对白、旁白、内心独白、情感标注、副语言标记、场景转换、BGM/SFX 建议等。 |

### writing/ - 写作

| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |
|---------|----------|------|----------|------|
| [bridge_chapter.j2](writing/bridge_chapter.j2) | `bridge_chapter` | JSON | `opening_time`、`opening_location`、`opening_pov`、`transition_mode`、`emotional_carryover`、`action_handoff`、`causal_link`、`pending_questions`、`forbidden_repetition`、`opening_acceptance_criteria`（强制） | 只生成“上一章出口 → 本章开场”的桥接契约，确定时间、地点、 POV、动作接力、情绪余波和因果链，不承担完整规划与正文写作。 |
| [draft_chapter.j2](writing/draft_chapter.j2) | `draft_chapter` | TEXT | - | 按已批准的 PlanCard + ContractCard 写正文。 本阶段不重新规划，不扩展契约，不消费全书级初始化源。 |
| [draft_scene.j2](writing/draft_scene.j2) | `draft_scene` | TEXT | - | 按当前 scene contract 写单个场景正文，只兑现本场独占事件/揭示/状态变化。 |
| [edit_chapter.j2](writing/edit_chapter.j2) | `edit_chapter` | TEXT | - | 基于阶段卡片对草稿做局部编辑。编辑阶段负责清理语言、修正已知问题、 调整字数与节奏，但不得重新规划章节。 |
| [edit_draft.j2](writing/edit_draft.j2) | `edit` | TEXT | - | 对短篇小说草稿进行多轮编辑，根据节拍和蓝图提升文字质量。 |
| [evaluate_draft.j2](writing/evaluate_draft.j2) | `evaluate` | JSON | `scores`、`overall_score`、`passed`、`threshold`、`summary`、`repair_suggestions`（强制） | 对短篇小说正文进行七维度质量评估（一致性、连贯性、实质推进、 人物、风格、吸引力、节奏），输出评估分数与修复建议。 |
| [patch_chapter.j2](writing/patch_chapter.j2) | `patch_chapter` | JSON | `patches`（强制） | 精准修复章节中的特定问题，使用文本匹配策略进行外科式修复， 只看到问题所在段落的上下文窗口。 |
| [polish_chapter.j2](writing/polish_chapter.j2) | `polish_chapter` | TEXT | - | 对章节进行精修润色，使其在文学品质上达到出版级标准。 |
| [wave_chapter.j2](writing/wave_chapter.j2) | `wave_chapter` | TEXT | - | 本步骤在 DRAFT 写完各场景后，**单次**将多场景草稿编织为完整长篇章节。 核心职责： 1. 在场景之间补入过渡段或过渡句 2. 兑现 plan.cross_scene_intent.cross_scene_references（callback/foreshadow/parallel/contrast/echo） 3. 按 plan.cross_scene_intent.pacing_curve 调整局部节奏 4. 统一跨场景 POV、声纹与风格 5. 在需要时删去跨场景重复或补全锚点 严格禁止： - 整段重写任何场景的 POV 主体 - 整段删除场景 - 新增场景 - 改动 plan.scene_intents 中已锁定的 POV 字符 - 字数相对 target 超出 +/-30% 区间 |

## 共享宏与局部模板

| 模板文件 | 类别 | 用途 |
|---------|------|------|
| [_base/_artifact_source_contract.j2](_base/_artifact_source_contract.j2) | `shared` | 渲染 ChapterSourceSlice 的轻量源头契约，统一长篇章节阶段的 source artifact 消费边界。该宏只描述输入消费规则，不定义输出格式。 |
| [_base/_calculate_word_constraints.j2](_base/_calculate_word_constraints.j2) | `shared` | - |
| [_base/_default_style.j2](_base/_default_style.j2) | `shared` | - |
| [_base/_narrative_contract.j2](_base/_narrative_contract.j2) | `shared` | - |
| [_base/_narrative_state.j2](_base/_narrative_state.j2) | `shared` | - |
| [_base/_quality_standards.j2](_base/_quality_standards.j2) | `shared` | 定义通用写作质量标准宏，包括系统痕迹禁止、代词一致性、 感官多样性、对话格式、说明腔禁止、意象复用禁止等约束。 这些宏被写作模板和检查模板共享，确保质量标准统一。 定义宏： - forbidden_system_markers(...): 禁止系统标记和规划术语混入正文 - pronoun_consistency(canonical_characters): 强制角色代词一致性 - self_reference_context_awareness(...): 角色自称语境意识 - sensory_diversity(weak_senses=[], ...): 感官多样性要求 - dialogue_density(min_pct, max_pct): 对话密度控制 - dialogue_format(): 中文引号对话格式规范 - anti_exposition_rules(...): 说明腔禁止 - anti_meta_narrative_rules(...): 章尾元叙事禁止 - creative_specificity_rules(...): 创造性具体化与反机械句式 - webnovel_pacing_rules(...): 网文节奏约束 - imagery_repetition_ban(...): 意象复用禁止 - phrase_blacklist(...): 模板化短语黑名单 - paragraph_uniqueness(...): 段落唯一性约束 使用方式： {%- from "_quality_standards.j2" import forbidden_system_markers, pronoun_consistency -%} 被调用方：writing/*.j2, checking/*.j2 |
| [_base/_render_canon_characters.j2](_base/_render_canon_characters.j2) | `shared` | - |
| [_base/_render_element_focus.j2](_base/_render_element_focus.j2) | `shared` | - |
| [_base/_render_style_profile_global.j2](_base/_render_style_profile_global.j2) | `shared` | - |
| [_base/_repair_attempt_guidance.j2](_base/_repair_attempt_guidance.j2) | `shared` | - |
| [_base/_role_definitions.j2](_base/_role_definitions.j2) | `shared` | - |
| [_base/_shared_evidence_anchor.j2](_base/_shared_evidence_anchor.j2) | `shared` | 统一渲染初始化/分片任务的共享证据锚点，避免各模板维护重复文案。 定义宏： - render_shared_evidence_anchor(shared_evidence_anchor, task_label="本次分片") |
| [_quality_standards.j2](_quality_standards.j2) | `shared` | - |
| [_role_definitions.j2](_role_definitions.j2) | `shared` | - |
| [_styles/_render_style_profile.j2](_styles/_render_style_profile.j2) | `shared` | - |
| [checking/_causal_core.j2](checking/_causal_core.j2) | `shared` | - |
| [checking/_continuity_core.j2](checking/_continuity_core.j2) | `shared` | - |

## 维护命令

```bash
python scripts/lint_prompt_layers.py
python scripts/audit_prompt_format_layers.py --all
python scripts/scaffold_prompt.py <task_type> <category> --output-kind json
python scripts/generate_prompt_index.py --check
python scripts/verify_templates.py
python scripts/verify_format_contracts.py
```
