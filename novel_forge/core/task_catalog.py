"""Shared task metadata used by routing, settings UI, and task defaults."""

from __future__ import annotations

from dataclasses import dataclass

from novel_forge.core.constants import ModelTier, TaskType


@dataclass(frozen=True)
class RoutingTask:
    """Visible task entry in the settings routing UI."""

    task_type: TaskType
    label: str
    hint: str

    @property
    def key(self) -> str:
        return self.task_type.value


@dataclass(frozen=True)
class RoutingSubgroup:
    """Bulk-routing subsection inside a visible routing group."""

    start_task_type: TaskType
    title: str
    description: str

    @property
    def start_key(self) -> str:
        return self.start_task_type.value


@dataclass(frozen=True)
class RoutingGroup:
    """Grouped routing section in the settings UI."""

    name: str
    description: str
    icon: str
    tasks: tuple[RoutingTask, ...]
    subgroups: tuple[RoutingSubgroup, ...] = ()


def routing_subgroup_task_keys(
    group: RoutingGroup,
    subgroup: RoutingSubgroup,
) -> tuple[str, ...]:
    """Return the contiguous task slice owned by one routing subgroup.

    A subgroup deliberately stores only its first task so inserting a task in
    the catalog keeps existing persisted group-bulk routes valid.  Keeping the
    range calculation here gives PySide, the API projection and profile
    migration one canonical interpretation of that boundary.
    """

    keys = tuple(task.key for task in group.tasks)
    try:
        start_index = keys.index(subgroup.start_key)
    except ValueError:
        return ()
    start_keys = {item.start_key for item in group.subgroups}
    end_index = len(keys)
    for index in range(start_index + 1, len(keys)):
        if keys[index] in start_keys:
            end_index = index
            break
    return keys[start_index:end_index]


@dataclass(frozen=True)
class TemperatureTask:
    """Temperature setting binding for a task."""

    task_type: TaskType
    label: str
    setting_attr: str

    @property
    def key(self) -> str:
        return self.task_type.value


DEFAULT_TASK_TIERS: dict[TaskType, ModelTier] = {
    TaskType.SPEC_ENRICH: ModelTier.STANDARD,
    TaskType.BEATS: ModelTier.STANDARD,
    TaskType.DRAFT: ModelTier.PREMIUM,
    TaskType.EDIT: ModelTier.PREMIUM,
    TaskType.EVALUATE: ModelTier.STANDARD,
    TaskType.TTS_GENERATE_DUBBING_SCRIPT: ModelTier.STANDARD,
    TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS: ModelTier.STANDARD,
    TaskType.TTS_REVIEW_DUBBING_SCRIPT: ModelTier.STANDARD,
    TaskType.TTS_ANALYZE_DUBBING_STYLE: ModelTier.STANDARD,
    TaskType.TTS_BUILD_NARRATOR_PROFILE: ModelTier.STANDARD,
    TaskType.TTS_REWRITE_SPOKEN_TEXT: ModelTier.STANDARD,
    TaskType.TTS_EMOTION_LABEL: ModelTier.STANDARD,
    TaskType.TTS_ADJUDICATE_VOICE_MATCH: ModelTier.STANDARD,
    TaskType.TTS_SOUND_DESIGN: ModelTier.STANDARD,
    TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES: ModelTier.PREMIUM,
    TaskType.INIT_CREATIVE_DIRECTION_SELECT: ModelTier.STANDARD,
    TaskType.INIT_STORY_BIBLE: ModelTier.PREMIUM,
    TaskType.INIT_STORY_CORE_PREMISE: ModelTier.STANDARD,
    TaskType.INIT_STORY_WORLD_RULES: ModelTier.STANDARD,
    TaskType.INIT_STORY_CONTINUITY_RULES: ModelTier.STANDARD,
    TaskType.INIT_STORY_THEMES_AND_SYMBOLS: ModelTier.STANDARD,
    TaskType.LOCATIONS_FIELD_BACKFILL: ModelTier.STANDARD,
    TaskType.INIT_CHARACTER_BIBLE: ModelTier.PREMIUM,
    TaskType.INIT_CHARACTER_ROSTER: ModelTier.STANDARD,
    TaskType.INIT_CHARACTER_PROFILE_BATCH: ModelTier.STANDARD,
    TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX: ModelTier.STANDARD,
    TaskType.INIT_CHARACTER_ARC_PLAN: ModelTier.STANDARD,
    TaskType.BLUEPRINT_ELEMENT_SELECT: ModelTier.STANDARD,
    TaskType.PLAN_OUTLINE: ModelTier.PREMIUM,
    TaskType.PLAN_OUTLINE_BATCH: ModelTier.PREMIUM,
    TaskType.PLAN_OUTLINE_CONTINUE: ModelTier.PREMIUM,
    TaskType.PLAN_CHAPTER: ModelTier.PREMIUM,
    TaskType.PLAN_CHAPTER_SCENES: ModelTier.PREMIUM,
    TaskType.VALIDATE_SCENE_PLAN: ModelTier.STANDARD,
    TaskType.DRAFT_CHAPTER: ModelTier.PREMIUM,
    TaskType.DRAFT_SCENE: ModelTier.PREMIUM,
    TaskType.EDIT_CHAPTER: ModelTier.STANDARD,
    TaskType.WAVE_CHAPTER: ModelTier.PREMIUM,
    TaskType.EXTRACT_EXPRESSION_OBSERVATIONS: ModelTier.BUDGET,
    TaskType.EXTRACT_CANON: ModelTier.STANDARD,
    TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT: ModelTier.BUDGET,
    TaskType.EXTRACT_CANON_DELTA: ModelTier.STANDARD,
    TaskType.EXTRACT_CREATIVE_REPORT: ModelTier.BUDGET,
    TaskType.EXTRACT_CHARACTER_STATE_DELTAS: ModelTier.STANDARD,
    TaskType.EXTRACT_RELATIONSHIP_DELTAS: ModelTier.STANDARD,
    TaskType.EXTRACT_PLOT_THREAD_DELTAS: ModelTier.STANDARD,
    TaskType.CHECK_ALIGNMENT: ModelTier.STANDARD,
    TaskType.ELEMENT_PROGRESS_ARBITER: ModelTier.STANDARD,
    TaskType.CHECK_CHAPTER: ModelTier.STANDARD,
    TaskType.BRIDGE_CHAPTER: ModelTier.STANDARD,
    TaskType.CHECK_CONTINUITY: ModelTier.STANDARD,
    TaskType.REPAIR_CONTINUITY: ModelTier.PREMIUM,
    TaskType.REPAIR_CAUSAL: ModelTier.PREMIUM,
    TaskType.REPAIR_STRATEGY_DIAGNOSE: ModelTier.BUDGET,
    TaskType.REPAIR_READING_POWER: ModelTier.PREMIUM,
    TaskType.REPAIR_GUARDRAIL: ModelTier.PREMIUM,
    TaskType.REPAIR_SEMANTIC_VERIFY: ModelTier.STANDARD,
    TaskType.VOLUME_AUDIT: ModelTier.STANDARD,
    TaskType.ADJUST_OUTLINE: ModelTier.STANDARD,
    TaskType.ENRICH_CHARACTER: ModelTier.STANDARD,
    TaskType.ADJUDICATE_CHARACTER_INTRODUCTION: ModelTier.STANDARD,
    TaskType.INTRODUCE_CHARACTER: ModelTier.STANDARD,
    TaskType.CONTEXT_COMPRESS: ModelTier.BUDGET,
    TaskType.PLOT_GUARD_JUDGE: ModelTier.STANDARD,
    TaskType.GUARD_CONSTRAINT_CHECK: ModelTier.STANDARD,
    TaskType.GENERATE_CONFIG: ModelTier.STANDARD,
    TaskType.POLISH_CONFIG: ModelTier.STANDARD,
    TaskType.POLISH_OUTLINE: ModelTier.STANDARD,
    TaskType.POLISH_CHAPTER: ModelTier.PREMIUM,
    TaskType.POLISH_SUBPLOT: ModelTier.PREMIUM,
    TaskType.BOOK_CONSISTENCY: ModelTier.STANDARD,
    TaskType.BOOK_CONSISTENCY_NAMING: ModelTier.BUDGET,
    TaskType.BOOK_CONSISTENCY_TIMELINE: ModelTier.STANDARD,
    TaskType.BOOK_CONSISTENCY_WORLD_RULE: ModelTier.STANDARD,
    TaskType.BOOK_CONSISTENCY_CHARACTER_STATE: ModelTier.STANDARD,
    TaskType.BOOK_CONSISTENCY_PLOT_THREAD: ModelTier.STANDARD,
    TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT: ModelTier.STANDARD,
    TaskType.BOOK_CONSISTENCY_VERIFY: ModelTier.STANDARD,
    TaskType.SHORT_BLUEPRINT: ModelTier.STANDARD,
    TaskType.SHORT_CREATIVE_SUMMARY: ModelTier.STANDARD,
    TaskType.VALIDATE_CAUSAL: ModelTier.STANDARD,
    TaskType.PATCH_CHAPTER: ModelTier.STANDARD,
    TaskType.ADAPTIVE_COMPRESS: ModelTier.BUDGET,
    TaskType.VERIFY_COMPRESSION: ModelTier.STANDARD,
    TaskType.EXTRACT_MOTIFS: ModelTier.STANDARD,
    TaskType.CRITIC_CONTINUITY: ModelTier.STANDARD,
    TaskType.CRITIC_CHARACTER: ModelTier.STANDARD,
    TaskType.CRITIC_CAUSAL: ModelTier.STANDARD,
    TaskType.CRITIC_STRENGTHS: ModelTier.STANDARD,
    TaskType.SUMMARIZE_CHAPTER: ModelTier.STANDARD,
    TaskType.SUMMARIZE_VOLUME: ModelTier.STANDARD,
    TaskType.SUMMARIZE_ARC: ModelTier.STANDARD,
    TaskType.SUMMARIZE_SCENE: ModelTier.BUDGET,
    TaskType.PROFILE_STYLE: ModelTier.STANDARD,
    TaskType.PROFILE_STRUCTURE: ModelTier.STANDARD,
    TaskType.DERIVE_EDITORIAL_CONTRACT: ModelTier.STANDARD,
    TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES: ModelTier.STANDARD,
    TaskType.DERIVE_EDITORIAL_STRUCTURE: ModelTier.STANDARD,
    TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS: ModelTier.STANDARD,
    TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES: ModelTier.STANDARD,
    TaskType.EVALUATE_READING_POWER: ModelTier.STANDARD,
    TaskType.MACRO_GUARD_AUDIT: ModelTier.STANDARD,
    TaskType.SUMMARY_DRIFT_CHECK: ModelTier.STANDARD,
    TaskType.KNOWLEDGE_BOUNDARY_AUDIT: ModelTier.STANDARD,
    TaskType.AUDIT_POV_DRIFT: ModelTier.STANDARD,
    TaskType.INIT_KNOWLEDGE_BOUNDARIES: ModelTier.STANDARD,
    TaskType.EXTRACT_KNOWLEDGE_DELTAS: ModelTier.STANDARD,
    TaskType.REPAIR_KNOWLEDGE_BOUNDARY: ModelTier.PREMIUM,
    TaskType.CHECK_EDITORIAL: ModelTier.STANDARD,
    TaskType.BOOK_EDITORIAL_AUDIT: ModelTier.STANDARD,
    TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT: ModelTier.STANDARD,
    TaskType.BOOK_EDITORIAL_VOICE_AUDIT: ModelTier.STANDARD,
    TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT: ModelTier.BUDGET,
    TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT: ModelTier.STANDARD,
    TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT: ModelTier.STANDARD,
    TaskType.INIT_ENTITY_REGISTRY: ModelTier.STANDARD,
    TaskType.INIT_NARRATIVE_CONTRACT: ModelTier.PREMIUM,
    TaskType.PLAN_CHAPTER_CONTRACTS: ModelTier.PREMIUM,
    TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER: ModelTier.STANDARD,
    TaskType.GROUND_OUTLINE_RESEARCH: ModelTier.STANDARD,
    TaskType.PLAN_INIT_RESEARCH_QUERIES: ModelTier.STANDARD,
    TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH: ModelTier.STANDARD,
    TaskType.DERIVE_INIT_COHERENCE_PROFILE: ModelTier.STANDARD,
    TaskType.REFINE_INIT_COHERENCE_PROFILE: ModelTier.STANDARD,
    TaskType.INIT_COHERENCE_ONTOLOGY: ModelTier.STANDARD,
    TaskType.INIT_COHERENCE_EXTRACTION_GUIDE: ModelTier.BUDGET,
    TaskType.INIT_COHERENCE_CONFLICT_RULES: ModelTier.STANDARD,
    TaskType.INIT_COHERENCE_PAYOFF_RULES: ModelTier.STANDARD,
    TaskType.EXTRACT_INIT_COHERENCE_CLAIMS: ModelTier.STANDARD,
    TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS: ModelTier.STANDARD,
    TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES: ModelTier.STANDARD,
    TaskType.ADJUDICATE_BLUEPRINT_COHERENCE: ModelTier.STANDARD,
    TaskType.ADJUDICATE_OUTLINE_INHERITANCE: ModelTier.STANDARD,
    TaskType.ADJUDICATE_CONTRACT_COHERENCE: ModelTier.STANDARD,
    TaskType.REPAIR_INIT_ARTIFACT_PATCH: ModelTier.PREMIUM,
    TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS: ModelTier.PREMIUM,
    TaskType.EXTRACT_CANDIDATE_STATE_DELTAS: ModelTier.STANDARD,
    TaskType.ADJUDICATE_ENTITY_REFERENCES: ModelTier.STANDARD,
    TaskType.ADJUDICATE_STATE_DELTA: ModelTier.STANDARD,
    TaskType.ADJUDICATE_CONTRACT_COMPLETION: ModelTier.STANDARD,
    TaskType.ADJUDICATE_FACT_CONFLICT: ModelTier.STANDARD,
    TaskType.ADJUDICATE_FINAL_STATE: ModelTier.STANDARD,
    TaskType.REPAIR_ADJUDICATED_ISSUE: ModelTier.PREMIUM,
    TaskType.RECONCILE_ENTITIES: ModelTier.STANDARD,
    TaskType.HUMANIZE_SCAN: ModelTier.STANDARD,
    TaskType.HUMANIZE_PARAGRAPH_REWRITE: ModelTier.STANDARD,
    # ── Film / Adaptation ─────────────────────────────────────────────
    TaskType.ADAPT_SCREENPLAY: ModelTier.PREMIUM,
    TaskType.COMPLIANCE_CHECK: ModelTier.STANDARD,
    TaskType.VISION_QC_SCORE: ModelTier.STANDARD,
    TaskType.DRAMA_SERIES_PLAN: ModelTier.STANDARD,
    TaskType.EPISODE_OUTLINE: ModelTier.STANDARD,
    TaskType.EPISODE_SCREENPLAY: ModelTier.PREMIUM,
    TaskType.FILM_SHOT_LAYOUT: ModelTier.STANDARD,
    # ── Comic / Adaptation ────────────────────────────────────────────
    TaskType.COMIC_PANEL_LAYOUT: ModelTier.STANDARD,
}


ROUTING_GROUPS: tuple[RoutingGroup, ...] = (
    RoutingGroup(
        name="短篇流程",
        description="一键短篇：规格确认 → 节拍 → 初稿 → 编辑 → 评估",
        icon="✦",
        tasks=(
            RoutingTask(
                TaskType.SPEC_ENRICH,
                "规格丰富",
                "短篇与长篇共用入口：充实用户输入为可执行规格",
            ),
            RoutingTask(TaskType.BEATS, "节拍生成", "产出结构化大纲骨架"),
            RoutingTask(TaskType.DRAFT, "初稿写作", "写短篇正文初稿"),
            RoutingTask(TaskType.EDIT, "润色编辑", "降漂移、提文笔、补逻辑"),
            RoutingTask(TaskType.EVALUATE, "质量评估", "打分与改进建议"),
        ),
        subgroups=(
            RoutingSubgroup(TaskType.SPEC_ENRICH, "构思与节拍", "规格丰富 → 节拍生成"),
            RoutingSubgroup(TaskType.DRAFT, "写作闭环", "初稿 → 编辑 → 评估"),
        ),
    ),
    RoutingGroup(
        name="长篇初始化",
        description=(
            "v3 立项主链：世界/角色分片 → 风格/实体 → 蓝图 → 画像 → 大纲/编辑契约 "
            "→ 章节契约 → 一致性硬门"
        ),
        icon="✧",
        tasks=(
            RoutingTask(
                TaskType.PLAN_INIT_RESEARCH_QUERIES,
                "研究查询规划",
                "用 LLM 分析 spec 生成精准研究查询，规则拼接作为回退",
            ),
            RoutingTask(
                TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER,
                "资料分析",
                "把联网检索报告压缩为 StoryBible 可参考的紧凑资料包",
            ),
            RoutingTask(
                TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH,
                "模型先验知识补充",
                "用模型内置知识合成研究资料，独立标记为 model_prior",
            ),
            RoutingTask(
                TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES,
                "创意方向候选",
                "在用户硬约束内生成自适应 2+1 创意方向",
            ),
            RoutingTask(
                TaskType.INIT_CREATIVE_DIRECTION_SELECT,
                "创意方向裁决",
                "以独立低温度调用选择方向并识别是否需要第三候选",
            ),
            RoutingTask(TaskType.INIT_STORY_BIBLE, "世界观设定", "StoryBible 单任务兜底入口"),
            RoutingTask(
                TaskType.INIT_STORY_CORE_PREMISE,
                "故事核心前提",
                "StoryBible 分片：确立核心冲突、题名、基调与主承诺；温度沿用世界观设定",
            ),
            RoutingTask(
                TaskType.INIT_STORY_WORLD_RULES,
                "世界规则",
                "StoryBible 分片：生成世界运行规则、限制与外部秩序；温度沿用世界观设定",
            ),
            RoutingTask(
                TaskType.INIT_STORY_THEMES_AND_SYMBOLS,
                "主题与符号",
                "StoryBible 分片：生成主题母题、象征系统与反复意象；温度沿用世界观设定",
            ),
            RoutingTask(
                TaskType.INIT_STORY_CONTINUITY_RULES,
                "连贯性规则",
                "StoryBible 分片：生成时间、称谓、地点转场与连续性约束；温度沿用世界观设定",
            ),
            RoutingTask(TaskType.INIT_CHARACTER_BIBLE, "角色设定", "CharacterBible 单任务兜底入口"),
            RoutingTask(
                TaskType.INIT_CHARACTER_ROSTER,
                "角色清单",
                "CharacterBible 分片：确定主要角色、功能位与出场优先级；温度沿用角色设定",
            ),
            RoutingTask(
                TaskType.INIT_CHARACTER_PROFILE_BATCH,
                "角色档案批次",
                "CharacterBible 分片：扩写角色画像、欲望、弱点与表达习惯；温度沿用角色设定",
            ),
            RoutingTask(
                TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
                "角色关系矩阵",
                "CharacterBible 分片：生成可追踪的人物关系和冲突张力；温度沿用角色设定",
            ),
            RoutingTask(
                TaskType.INIT_CHARACTER_ARC_PLAN,
                "角色弧光计划",
                "CharacterBible 分片：规划关键角色跨卷变化与不可逆节点；温度沿用角色设定",
            ),
            RoutingTask(
                TaskType.PROFILE_STYLE,
                "风格规范生成",
                "根据项目要素推导专属写作风格规范（含题材参数）",
            ),
            RoutingTask(
                TaskType.PROFILE_STRUCTURE,
                "风格结构参数",
                "生成钩子/爽点/微兑现/三线节奏等结构参数（并行分支）",
            ),
            RoutingTask(
                TaskType.BLUEPRINT_ELEMENT_SELECT, "叙事要素选择", "根据题材选择必要项与扩展项"
            ),
            RoutingTask(
                TaskType.INIT_ENTITY_REGISTRY,
                "实体注册表补充",
                "确定性角色实体不足时，仅补充地点、组织、物件、概念实体",
            ),
            RoutingTask(
                TaskType.PLAN_OUTLINE, "叙事蓝图", "规划全书叙事蓝图（全局结构、支线、弧光）"
            ),
            RoutingTask(
                TaskType.REFINE_INIT_COHERENCE_PROFILE,
                "画像精炼",
                "用叙事蓝图反哺一致性画像，补全状态轴、payoff 与不可逆事件",
            ),
            RoutingTask(
                TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS,
                "创意增强",
                "可选：基于既有设定提出低风险蓝图增强 patch，默认不启用",
            ),
            RoutingTask(
                TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
                "编辑契约：角色声纹",
                "并行派生角色声纹、对白边界与表达禁区；温度沿用叙事蓝图并上限 0.10",
            ),
            RoutingTask(
                TaskType.DERIVE_EDITORIAL_STRUCTURE,
                "编辑契约：结构",
                "并行派生高潮标记、揭示阶梯、标题策略与收束预算；温度沿用叙事蓝图并上限 0.10",
            ),
            RoutingTask(
                TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
                "编辑契约：风格约束",
                "并行派生主题、符号、场景阻力与表达通道约束；温度沿用叙事蓝图并上限 0.10",
            ),
            RoutingTask(
                TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES,
                "编辑契约：要素指令",
                "并行派生题材要素在章节中的使用边界；温度沿用叙事蓝图并上限 0.10",
            ),
            RoutingTask(TaskType.PLAN_OUTLINE_BATCH, "章节大纲（首批）", "分批生成章节细纲首批"),
            RoutingTask(
                TaskType.PLAN_OUTLINE_CONTINUE,
                "章节大纲（续写）",
                "分批续写章节细纲，保留多轮历史和恢复语义",
            ),
            RoutingTask(
                TaskType.POLISH_OUTLINE,
                "大纲润色",
                "当用户提供 polish_hint 时，在初始化大纲后定向精修章节大纲",
            ),
            RoutingTask(
                TaskType.GROUND_OUTLINE_RESEARCH,
                "大纲资料校准",
                "把资料包与最终大纲对齐，生成章节级提醒和事实风险",
            ),
            RoutingTask(TaskType.PLAN_CHAPTER_CONTRACTS, "章节契约", "拆分每章入口/出口与状态任务"),
            RoutingTask(
                TaskType.INIT_NARRATIVE_CONTRACT,
                "叙事契约",
                "生成全书级叙事契约，作为章节契约裁判的全局基线",
            ),
            RoutingTask(
                TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                "一致性 Claims 抽取",
                "从蓝图、大纲、章节契约分批抽取结构化叙事事实",
            ),
            RoutingTask(
                TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
                "蓝图整体一致性 Claims",
                "完整阅读叙事蓝图，补充跨字段依赖、承诺、弧线与 payoff 一致性 claims",
            ),
            RoutingTask(
                TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
                "冲突候选裁判",
                "对结构化检索和记忆召回出的候选冲突做 LLM 裁判",
            ),
            RoutingTask(
                TaskType.REPAIR_INIT_ARTIFACT_PATCH,
                "初始化局部修复",
                "按裁判 scope 输出受限 JSON Patch，精准修复蓝图/大纲/契约",
            ),
            RoutingTask(
                TaskType.ADJUDICATE_CONTRACT_COHERENCE,
                "契约裁判",
                "分批裁判章节契约是否覆盖全局叙事契约与状态迁移要求",
            ),
        ),
        subgroups=(
            RoutingSubgroup(
                TaskType.PLAN_INIT_RESEARCH_QUERIES,
                "资料准备与世界规则",
                "查询规划、资料分析、故事核心、世界规则、主题符号与连续性规则",
            ),
            RoutingSubgroup(
                TaskType.INIT_CHARACTER_BIBLE,
                "角色与关系",
                "角色清单、档案、关系矩阵与跨卷弧光",
            ),
            RoutingSubgroup(
                TaskType.PROFILE_STYLE,
                "风格与实体",
                "风格画像、结构参数、叙事要素与实体注册",
            ),
            RoutingSubgroup(
                TaskType.PLAN_OUTLINE,
                "蓝图与画像",
                "叙事蓝图、项目画像精炼与可选创意增强",
            ),
            RoutingSubgroup(
                TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
                "编辑契约",
                "角色声纹、结构、风格约束与要素指令",
            ),
            RoutingSubgroup(
                TaskType.PLAN_OUTLINE_BATCH,
                "章节规划",
                "章节大纲批次、续写、润色与章节契约",
            ),
            RoutingSubgroup(
                TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
                "初始化一致性",
                "一致性 Claims 抽取、整体蓝图 claims、冲突裁判与局部修复",
            ),
        ),
    ),
    RoutingGroup(
        name="长篇章节创作",
        description="逐章推进：桥接 → 规划 → DRAFT → WAVE → 审计 → 归档",
        icon="✺",
        tasks=(
            RoutingTask(TaskType.PLAN_CHAPTER, "章节规划", "单章 beats / 情绪弧线"),
            RoutingTask(
                TaskType.PLAN_CHAPTER_SCENES, "场景级规划", "高级模式：规划可独立草稿的场景契约"
            ),
            RoutingTask(
                TaskType.VALIDATE_SCENE_PLAN, "场景计划验证", "验证场景边界、依赖与可并行组"
            ),
            RoutingTask(TaskType.BRIDGE_CHAPTER, "章节桥接", "桥接上一章末尾与当前章"),
            RoutingTask(TaskType.DRAFT_CHAPTER, "DRAFT 原稿", "写单章正文原始草稿"),
            RoutingTask(TaskType.DRAFT_SCENE, "场景草稿", "高级模式：按场景契约写局部正文"),
            RoutingTask(
                TaskType.WAVE_CHAPTER,
                "初稿成章",
                "把 DRAFT 原稿按完整场景意图、角色声纹与跨场景引用编织成可审初稿",
            ),
            RoutingTask(TaskType.EDIT_CHAPTER, "章节编辑", "遗留编辑任务：润色单章/降漂移/补逻辑"),
            RoutingTask(
                TaskType.POLISH_CHAPTER,
                "终稿选段精修",
                "卷帙终稿右键选段时，生成火候建议与局部候选文本",
            ),
            RoutingTask(TaskType.CHECK_CHAPTER, "章节校验", "质量检查与自动修复"),
            RoutingTask(TaskType.CHECK_ALIGNMENT, "对齐审计", "检查是否按大纲推进"),
            RoutingTask(
                TaskType.CHECK_CONTINUITY,
                "连贯性检查",
                "跨章连续性检查，核验上一章出口、本章开场与状态承接",
            ),
            RoutingTask(
                TaskType.VALIDATE_CAUSAL,
                "因果链验证",
                "章内因果链深度审核，核验事件触发、动机与结果兑现",
            ),
            RoutingTask(
                TaskType.HUMANIZE_SCAN,
                "AI 去痕扫描",
                "检测并修复 AI 写作痕迹（默认关闭，开启后每次章节末尾自动运行）",
            ),
            RoutingTask(
                TaskType.HUMANIZE_PARAGRAPH_REWRITE,
                "AI 去痕段落改写",
                "对不可精确替换的 AI 痕迹执行段落级定向改写",
            ),
            RoutingTask(
                TaskType.KNOWLEDGE_BOUNDARY_AUDIT,
                "知识边界审计",
                "专用审计路由，判断隐藏知识是否被提前泄露",
            ),
            RoutingTask(
                TaskType.INIT_KNOWLEDGE_BOUNDARIES,
                "知识边界初始化",
                "为每个角色建立知识边界：已知事实、怀疑、误解、保密与感官访问规则",
            ),
            RoutingTask(
                TaskType.EVALUATE_READING_POWER,
                "追读力评估",
                "评估章节钩子强度与悬念兑现，为下章提供约束",
            ),
            RoutingTask(
                TaskType.MACRO_GUARD_AUDIT,
                "宏观护栏审计",
                "跨章节轨迹宏观审计，检测多章累积偏离",
            ),
            RoutingTask(
                TaskType.ELEMENT_PROGRESS_ARBITER,
                "要素灰区仲裁",
                "仅在开启灰区仲裁时触发，对要素规则灰区结果进行低频复判",
            ),
            RoutingTask(TaskType.REPAIR_CONTINUITY, "连贯性修复", "修复检测到的连贯性问题"),
            RoutingTask(TaskType.REPAIR_CAUSAL, "因果链全文修复", "按问题类型进行定向全文修复"),
            RoutingTask(
                TaskType.REPAIR_STRATEGY_DIAGNOSE,
                "修复策略诊断",
                "诊断重复修复失败并推荐 patch/fulltext/rewrite 倾向",
            ),
            RoutingTask(TaskType.REPAIR_READING_POWER, "追读力修复", "强化章尾钩子和微兑现"),
            RoutingTask(
                TaskType.REPAIR_KNOWLEDGE_BOUNDARY,
                "知识边界修复",
                "修复 POV 角色知识边界违规：泄露、提前揭示与无依据知识获取",
            ),
            RoutingTask(
                TaskType.EXTRACT_KNOWLEDGE_DELTAS,
                "知识增量提取",
                "从正文提取角色知识状态变化增量",
            ),
            RoutingTask(TaskType.PATCH_CHAPTER, "补丁修复", "外科手术式补丁修复（局部窗口）"),
            RoutingTask(TaskType.EXTRACT_CANON, "Canon 提取", "从正文提取剧情增量"),
            RoutingTask(
                TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
                "候选状态抽取",
                "从正文抽取待裁判状态变化",
            ),
            RoutingTask(
                TaskType.ADJUDICATE_STATE_DELTA,
                "状态变化裁判",
                "基于证据窗口裁判单个候选状态变化",
            ),
            RoutingTask(
                TaskType.ADJUDICATE_CONTRACT_COMPLETION,
                "契约完成度裁判",
                "归档前判断本章是否完成当前章节契约并识别未来泄露",
            ),
            RoutingTask(
                TaskType.ADJUDICATE_FINAL_STATE,
                "最终状态裁判",
                "归并 LLM 裁判结果并决定写入/挂起/修复",
            ),
            RoutingTask(
                TaskType.REPAIR_ADJUDICATED_ISSUE,
                "裁判问题修复",
                "修复 LLM 裁定需要修改的正文问题",
            ),
        ),
        subgroups=(
            RoutingSubgroup(
                TaskType.PLAN_CHAPTER,
                "生成链路",
                "规划 → 桥接 → DRAFT → WAVE → 精修",
            ),
            RoutingSubgroup(
                TaskType.CHECK_CHAPTER,
                "审计与评估",
                "质量、对齐、连续性、因果、知识边界、追读力与宏观护栏",
            ),
            RoutingSubgroup(
                TaskType.REPAIR_CONTINUITY,
                "修复与补丁",
                "连贯性、因果链、追读力与局部补丁",
            ),
            RoutingSubgroup(
                TaskType.EXTRACT_CANON,
                "归档与状态",
                "Canon 增量、候选状态抽取与 LLM 裁判",
            ),
        ),
    ),
    RoutingGroup(
        name="工具与维护",
        description="辅助调度：护栏 / 角色 / 全书审计 / 调整 / 配置生成",
        icon="⚙",
        tasks=(
            RoutingTask(TaskType.PLOT_GUARD_JUDGE, "护栏决策", "自动判断续写策略"),
            RoutingTask(TaskType.ENRICH_CHARACTER, "角色丰富", "补全/扩写角色设定"),
            RoutingTask(
                TaskType.ADJUDICATE_CHARACTER_INTRODUCTION,
                "新角色裁决",
                "判断候选名字是否真是重要新角色、别名或临时标签",
            ),
            RoutingTask(
                TaskType.INTRODUCE_CHARACTER, "新角色建档", "根据大纲预先生成新出场角色的完整档案"
            ),
            RoutingTask(TaskType.ADJUST_OUTLINE, "大纲调整", "根据偏离智能调整"),
            RoutingTask(
                TaskType.BOOK_CONSISTENCY, "全书一致性审计", "跨章节深度审计并输出可修复定位"
            ),
            RoutingTask(TaskType.VOLUME_AUDIT, "卷末审计", "卷摘要与一致性检查"),
            RoutingTask(
                TaskType.BOOK_CONSISTENCY_VERIFY, "全书审计验证", "逐章验证审计问题并精确定位段落"
            ),
            RoutingTask(TaskType.GENERATE_CONFIG, "AI 创意生成", "根据用户提示生成完整创作配置"),
            RoutingTask(
                TaskType.POLISH_CONFIG, "AI 定向润色", "基于当前配置按提示定向润色并保留核心设定"
            ),
        ),
        subgroups=(
            RoutingSubgroup(
                TaskType.PLOT_GUARD_JUDGE,
                "护栏与角色工具",
                "续写护栏、角色丰富、新角色裁决/建档与大纲调整",
            ),
            RoutingSubgroup(
                TaskType.BOOK_CONSISTENCY,
                "全书审计",
                "全书一致性、卷末审计与问题验证",
            ),
            RoutingSubgroup(
                TaskType.GENERATE_CONFIG,
                "配置生成",
                "AI 创意生成与定向润色",
            ),
        ),
    ),
    RoutingGroup(
        name="记忆增强",
        description="记忆增强：上下文压缩 / 母题追踪 / Critic 独立评审 / 多粒度摘要",
        icon="✿",
        tasks=(
            RoutingTask(TaskType.CONTEXT_COMPRESS, "上下文压缩", "带质量验证的自适应压缩"),
            RoutingTask(TaskType.VERIFY_COMPRESSION, "压缩质量验证", "验证压缩结果的质量"),
            RoutingTask(TaskType.EXTRACT_MOTIFS, "母题提取", "从章节中提取母题和意象"),
            RoutingTask(TaskType.CRITIC_CONTINUITY, "连续性评审", "CriticAgent 跨章连续性检查"),
            RoutingTask(TaskType.CRITIC_CHARACTER, "角色一致性检查", "CriticAgent 角色一致性验证"),
            RoutingTask(TaskType.CRITIC_CAUSAL, "因果链评审", "CriticAgent 因果链检查"),
            RoutingTask(TaskType.CRITIC_STRENGTHS, "优点识别", "CriticAgent 识别章节优点"),
            RoutingTask(TaskType.SUMMARIZE_CHAPTER, "章节摘要", "生成章节级摘要"),
            RoutingTask(TaskType.SUMMARIZE_VOLUME, "卷摘要", "生成卷级摘要"),
            RoutingTask(TaskType.SUMMARIZE_ARC, "故事弧摘要", "生成故事弧级摘要"),
            RoutingTask(TaskType.SUMMARIZE_SCENE, "场景摘要", "生成场景级高密度摘要"),
        ),
        subgroups=(
            RoutingSubgroup(
                TaskType.CONTEXT_COMPRESS,
                "压缩与验证",
                "上下文压缩与压缩质量验证",
            ),
            RoutingSubgroup(
                TaskType.EXTRACT_MOTIFS,
                "母题与 Critic",
                "母题提取与 CriticAgent 独立评审",
            ),
            RoutingSubgroup(
                TaskType.SUMMARIZE_CHAPTER,
                "多粒度摘要",
                "章节、卷、故事弧与场景摘要",
            ),
        ),
    ),
    RoutingGroup(
        name="配音",
        description="配音流程：脚本生成 / 片段裁决 / 专业审校 / 音色匹配 / 声音设计",
        icon="♫",
        tasks=(
            RoutingTask(
                TaskType.TTS_GENERATE_DUBBING_SCRIPT,
                "配音脚本生成",
                "将章节正文转化为带情绪、语气标注的表演脚本",
            ),
            RoutingTask(
                TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS,
                "片段裁决",
                "对脚本分段进行说话人与情绪裁决",
            ),
            RoutingTask(
                TaskType.TTS_REVIEW_DUBBING_SCRIPT,
                "脚本专业审校",
                "LLM 审校配音脚本的情绪、语气与副语言标注",
            ),
            RoutingTask(
                TaskType.TTS_ADJUDICATE_VOICE_MATCH,
                "音色匹配裁决",
                "低置信度音色匹配的 LLM 受约束裁决",
            ),
            RoutingTask(
                TaskType.TTS_BUILD_NARRATOR_PROFILE,
                "旁白声纹建档",
                "为旁白生成结构化声纹特征档案",
            ),
            RoutingTask(
                TaskType.TTS_SOUND_DESIGN,
                "声音设计",
                "提取音效、BGM 与环境声需求",
            ),
        ),
        subgroups=(
            RoutingSubgroup(
                TaskType.TTS_GENERATE_DUBBING_SCRIPT,
                "脚本与审校",
                "配音脚本生成、片段裁决与专业审校",
            ),
            RoutingSubgroup(
                TaskType.TTS_ADJUDICATE_VOICE_MATCH,
                "音色与声音设计",
                "音色匹配裁决、旁白建档与声音设计",
            ),
        ),
    ),
)


MULTI_TURN_TASK_KEYS: frozenset[str] = frozenset(
    {
        TaskType.DRAFT.value,
        TaskType.PLAN_OUTLINE_BATCH.value,
        TaskType.PLAN_OUTLINE_CONTINUE.value,
        TaskType.PLAN_CHAPTER_CONTRACTS.value,
        TaskType.EDIT_CHAPTER.value,
    }
)


SHORT_TEMPERATURE_TASKS: tuple[TemperatureTask, ...] = (
    TemperatureTask(TaskType.SPEC_ENRICH, "规格丰富", "temp_spec_enrich"),
    TemperatureTask(TaskType.BLUEPRINT_ELEMENT_SELECT, "要素选择", "temp_blueprint_element_select"),
    TemperatureTask(TaskType.BEATS, "节拍", "temp_beats"),
    TemperatureTask(TaskType.DRAFT, "初稿", "temp_draft"),
    TemperatureTask(TaskType.EDIT, "编辑", "temp_edit"),
    TemperatureTask(TaskType.EVALUATE, "评估", "temp_evaluate"),
)


LONG_TEMPERATURE_TASKS: tuple[TemperatureTask, ...] = (
    TemperatureTask(
        TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES,
        "初始化创意候选",
        "temp_init_creative_direction_candidates",
    ),
    TemperatureTask(
        TaskType.INIT_CREATIVE_DIRECTION_SELECT,
        "初始化创意选择",
        "temp_init_creative_direction_select",
    ),
    TemperatureTask(
        TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER,
        "资料分析",
        "temp_synthesize_init_research_dossier",
    ),
    TemperatureTask(TaskType.INIT_STORY_BIBLE, "世界观设定", "temp_init_story_bible"),
    TemperatureTask(TaskType.INIT_STORY_CORE_PREMISE, "故事核心前提", "temp_init_story_bible"),
    TemperatureTask(TaskType.INIT_STORY_WORLD_RULES, "世界规则", "temp_init_story_bible"),
    TemperatureTask(TaskType.INIT_STORY_THEMES_AND_SYMBOLS, "主题与符号", "temp_init_story_bible"),
    TemperatureTask(TaskType.INIT_STORY_CONTINUITY_RULES, "连贯性规则", "temp_init_story_bible"),
    TemperatureTask(TaskType.INIT_CHARACTER_BIBLE, "角色设定", "temp_init_character_bible"),
    TemperatureTask(TaskType.INIT_CHARACTER_ROSTER, "角色清单", "temp_init_character_bible"),
    TemperatureTask(
        TaskType.INIT_CHARACTER_PROFILE_BATCH,
        "角色档案批次",
        "temp_init_character_bible",
    ),
    TemperatureTask(
        TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX,
        "角色关系矩阵",
        "temp_init_character_bible",
    ),
    TemperatureTask(TaskType.INIT_CHARACTER_ARC_PLAN, "角色弧光计划", "temp_init_character_bible"),
    TemperatureTask(TaskType.PROFILE_STYLE, "风格规范", "temp_profile_style"),
    TemperatureTask(TaskType.PROFILE_STRUCTURE, "风格结构参数", "temp_profile_structure"),
    TemperatureTask(
        TaskType.BLUEPRINT_ELEMENT_SELECT, "叙事要素选择", "temp_blueprint_element_select"
    ),
    TemperatureTask(TaskType.INIT_ENTITY_REGISTRY, "实体注册表", "temp_init_entity_registry"),
    TemperatureTask(TaskType.PLAN_OUTLINE, "叙事蓝图", "temp_plan_outline"),
    TemperatureTask(
        TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES,
        "编辑契约：角色声纹",
        "temp_plan_outline",
    ),
    TemperatureTask(
        TaskType.DERIVE_EDITORIAL_STRUCTURE,
        "编辑契约：结构",
        "temp_plan_outline",
    ),
    TemperatureTask(
        TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
        "编辑契约：风格约束",
        "temp_plan_outline",
    ),
    TemperatureTask(
        TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES,
        "编辑契约：要素指令",
        "temp_plan_outline",
    ),
    TemperatureTask(
        TaskType.REFINE_INIT_COHERENCE_PROFILE,
        "画像精炼",
        "temp_refine_init_coherence_profile",
    ),
    TemperatureTask(
        TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS,
        "创意增强",
        "temp_refine_init_artifacts_from_synopsis",
    ),
    TemperatureTask(
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        "一致性 Claims 抽取",
        "temp_extract_init_coherence_claims",
    ),
    TemperatureTask(
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
        "蓝图整体一致性 Claims",
        "temp_extract_blueprint_holistic_claims",
    ),
    TemperatureTask(
        TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
        "冲突候选裁判",
        "temp_adjudicate_init_conflict_candidates",
    ),
    TemperatureTask(
        TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        "初始化局部修复",
        "temp_repair_init_artifact_patch",
    ),
    TemperatureTask(
        TaskType.ADJUDICATE_CONTRACT_COHERENCE,
        "契约裁判",
        "temp_adjudicate_contract_coherence",
    ),
    TemperatureTask(
        TaskType.GROUND_OUTLINE_RESEARCH,
        "大纲资料校准",
        "temp_ground_outline_research",
    ),
    TemperatureTask(
        TaskType.PLAN_INIT_RESEARCH_QUERIES,
        "研究查询规划",
        "temp_plan_init_research_queries",
    ),
    TemperatureTask(
        TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH,
        "模型先验知识补充",
        "temp_synthesize_model_prior_research",
    ),
    TemperatureTask(TaskType.PLAN_CHAPTER_CONTRACTS, "章节契约", "temp_plan_chapter_contracts"),
    TemperatureTask(TaskType.INIT_NARRATIVE_CONTRACT, "叙事契约", "temp_init_narrative_contract"),
    TemperatureTask(TaskType.PLAN_OUTLINE_BATCH, "章节大纲（首批）", "temp_plan_outline_batch"),
    TemperatureTask(
        TaskType.PLAN_OUTLINE_CONTINUE, "章节大纲（续写）", "temp_plan_outline_continue"
    ),
    TemperatureTask(TaskType.PLAN_CHAPTER, "章节规划", "temp_plan_chapter"),
    TemperatureTask(TaskType.PLAN_CHAPTER_SCENES, "场景级规划", "temp_plan_chapter"),
    TemperatureTask(TaskType.VALIDATE_SCENE_PLAN, "场景计划验证", "temp_check_alignment"),
    TemperatureTask(TaskType.BRIDGE_CHAPTER, "章节桥接", "temp_bridge_chapter"),
    TemperatureTask(TaskType.DRAFT_CHAPTER, "章节写作", "temp_draft_chapter"),
    TemperatureTask(TaskType.DRAFT_SCENE, "场景草稿", "temp_draft_chapter"),
    TemperatureTask(TaskType.WAVE_CHAPTER, "初稿成章", "temp_wave_chapter"),
    TemperatureTask(TaskType.EDIT_CHAPTER, "章节编辑", "temp_edit_chapter"),
    TemperatureTask(TaskType.POLISH_CHAPTER, "终稿选段精修", "temp_polish_chapter"),
    TemperatureTask(TaskType.CHECK_CHAPTER, "章节校验", "temp_check_chapter"),
    TemperatureTask(TaskType.CHECK_ALIGNMENT, "对齐检查", "temp_check_alignment"),
    TemperatureTask(TaskType.HUMANIZE_SCAN, "AI 去痕扫描", "temp_humanize_scan"),
    TemperatureTask(
        TaskType.HUMANIZE_PARAGRAPH_REWRITE,
        "AI 去痕段落改写",
        "temp_humanize_paragraph_rewrite",
    ),
    TemperatureTask(
        TaskType.KNOWLEDGE_BOUNDARY_AUDIT,
        "知识边界审计",
        "temp_knowledge_boundary_audit",
    ),
    TemperatureTask(
        TaskType.INIT_KNOWLEDGE_BOUNDARIES,
        "知识边界初始化",
        "temp_init_knowledge_boundaries",
    ),
    TemperatureTask(TaskType.EVALUATE_READING_POWER, "追读力评估", "temp_evaluate_reading_power"),
    TemperatureTask(TaskType.MACRO_GUARD_AUDIT, "宏观护栏审计", "temp_macro_guard"),
    TemperatureTask(TaskType.CHECK_CONTINUITY, "连贯性评估", "temp_check_continuity"),
    TemperatureTask(TaskType.VALIDATE_CAUSAL, "因果链验证", "temp_validate_causal"),
    TemperatureTask(TaskType.REPAIR_CONTINUITY, "连贯性修复", "temp_repair_continuity"),
    TemperatureTask(TaskType.REPAIR_CAUSAL, "因果链全文修复", "temp_repair_causal"),
    TemperatureTask(TaskType.REPAIR_STRATEGY_DIAGNOSE, "修复策略诊断", "temp_check_alignment"),
    TemperatureTask(TaskType.REPAIR_READING_POWER, "追读力修复", "temp_repair_reading_power"),
    TemperatureTask(
        TaskType.REPAIR_KNOWLEDGE_BOUNDARY,
        "知识边界修复",
        "temp_repair_knowledge_boundary",
    ),
    TemperatureTask(
        TaskType.EXTRACT_KNOWLEDGE_DELTAS,
        "知识增量提取",
        "temp_extract_knowledge_deltas",
    ),
    TemperatureTask(TaskType.PATCH_CHAPTER, "补丁修复", "temp_patch_chapter"),
    TemperatureTask(TaskType.EXTRACT_CANON, "Canon 提取", "temp_extract_canon"),
    TemperatureTask(
        TaskType.EXTRACT_CANDIDATE_STATE_DELTAS,
        "候选状态抽取",
        "temp_extract_candidate_state_deltas",
    ),
    TemperatureTask(
        TaskType.ADJUDICATE_STATE_DELTA,
        "状态变化裁判",
        "temp_adjudicate_state_delta",
    ),
    TemperatureTask(
        TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        "契约完成度裁判",
        "temp_adjudicate_contract_completion",
    ),
    TemperatureTask(
        TaskType.ADJUDICATE_FINAL_STATE,
        "最终状态裁判",
        "temp_adjudicate_final_state",
    ),
    TemperatureTask(
        TaskType.REPAIR_ADJUDICATED_ISSUE,
        "裁判问题修复",
        "temp_repair_adjudicated_issue",
    ),
    TemperatureTask(TaskType.VOLUME_AUDIT, "卷末审计", "temp_volume_audit"),
    TemperatureTask(TaskType.CONTEXT_COMPRESS, "上下文压缩", "temp_context_compress"),
    TemperatureTask(TaskType.PLOT_GUARD_JUDGE, "护栏决策", "temp_plot_guard_judge"),
    TemperatureTask(TaskType.BOOK_CONSISTENCY, "全书一致性审计", "temp_book_consistency"),
    TemperatureTask(TaskType.BOOK_CONSISTENCY_VERIFY, "全书审计验证", "temp_book_consistency"),
    TemperatureTask(TaskType.ENRICH_CHARACTER, "角色丰富", "temp_enrich_character"),
    TemperatureTask(
        TaskType.ADJUDICATE_CHARACTER_INTRODUCTION,
        "新角色裁决",
        "temp_adjudicate_character_introduction",
    ),
    TemperatureTask(TaskType.INTRODUCE_CHARACTER, "新角色建档", "temp_introduce_character"),
    TemperatureTask(TaskType.GENERATE_CONFIG, "AI 创意生成", "temp_generate_config"),
    TemperatureTask(TaskType.POLISH_CONFIG, "AI 定向润色", "temp_polish_config"),
    TemperatureTask(TaskType.ADJUST_OUTLINE, "大纲调整", "temp_adjust_outline"),
    TemperatureTask(TaskType.POLISH_OUTLINE, "大纲润色", "temp_polish_outline"),
    # Memory module tasks
    TemperatureTask(TaskType.VERIFY_COMPRESSION, "压缩质量验证", "temp_verify_compression"),
    TemperatureTask(TaskType.EXTRACT_MOTIFS, "母题提取", "temp_extract_motifs"),
    TemperatureTask(TaskType.CRITIC_CONTINUITY, "连续性评审", "temp_critic_continuity"),
    TemperatureTask(TaskType.CRITIC_CHARACTER, "角色一致性检查", "temp_critic_character"),
    TemperatureTask(TaskType.CRITIC_CAUSAL, "因果链评审", "temp_critic_causal"),
    TemperatureTask(TaskType.CRITIC_STRENGTHS, "优点识别", "temp_critic_strengths"),
    TemperatureTask(TaskType.SUMMARIZE_CHAPTER, "章节摘要", "temp_summarize_chapter"),
    TemperatureTask(TaskType.SUMMARIZE_VOLUME, "卷摘要", "temp_summarize_volume"),
    TemperatureTask(TaskType.SUMMARIZE_ARC, "故事弧摘要", "temp_summarize_arc"),
    TemperatureTask(TaskType.SUMMARIZE_SCENE, "场景摘要", "temp_summarize_scene"),
)
