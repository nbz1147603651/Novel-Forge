/**
 * Chinese label maps for rich document rendering — a faithful port of
 * novel_forge/desktop/pages/document_renderer/reports/_common.py.
 *
 * These maps turn raw JSON keys / enum values into the same Chinese wording
 * the PySide6 renderers use, so the React rich documents read identically.
 *
 * NOTE: Genre and tone labels are now derived from the i18n DICT for consistency.
 * The standalone maps below are kept for backward compatibility (default locale = zh).
 */

import { DICT, type Locale } from "./i18n";

/** spec.genre -> Chinese (PySide6 `_GENRE_LABELS`). Derived from DICT. */
export const GENRE_LABELS: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(DICT)
    .filter(([k]) => k.startsWith("genre."))
    .map(([k, v]) => [k.slice(6), v.zh]),
);

/** spec.tone -> Chinese (PySide6 `_TONE_LABELS`). Derived from DICT. */
export const TONE_LABELS: Readonly<Record<string, string>> = Object.fromEntries(
  Object.entries(DICT)
    .filter(([k]) => k.startsWith("tone."))
    .map(([k, v]) => [k.slice(5), v.zh]),
);

/** story_bible top-level fields -> Chinese (PySide6 `_BIBLE_FIELD_LABELS`). */
export const BIBLE_FIELD_LABELS: Readonly<Record<string, string>> = {
  premise: "故事前提",
  era: "时代背景",
  geography: "地理场景",
  culture: "社会文化",
  magic_or_tech: "核心设定 / 魔法·技术",
  tone: "叙事基调",
};

/** eval report dimension -> Chinese (PySide6 `_EVAL_DIM_LABELS`). */
export const EVAL_DIM_LABELS: Readonly<Record<string, string>> = {
  consistency: "设定一致",
  continuity: "场景连贯",
  character: "人物塑造",
  style: "文笔风格",
  engagement: "吸引力",
  pacing: "节奏控制",
  causal_chain: "因果链",
  tension: "张力控制",
  coherence: "连贯性",
  tech_density: "术语密度",
  plot: "情节逻辑",
  originality: "独创性",
  theme: "主题表达",
  world_building: "世界构建",
  dialogue: "对话质量",
  rewrite_compliance: "重写落地",
};

/**
 * Generic JSON key -> Chinese label (PySide6 `_GENERIC_KEY_LABELS`, ~235 entries).
 * Used by the generic structured report renderer for the long tail of reports.
 */
export const GENERIC_KEY_LABELS: Readonly<Record<string, string>> = {
  age: "年龄",
  gender: "性别",
  social_status: "身份与地位",
  abilities: "能力与资源",
  personality: "性格",
  backstory: "背景经历",
  appearance: "外貌",
  voice: "语言风格",
  query: "检索问题",
  rationale: "检索依据",
  intent: "目的",
  locale: "语言地区",
  source_preferences: "来源偏好",
  recency_required: "需要时效性",
  risk_if_missing: "缺失风险",
  arc: "人物弧线",
  visual_identity: "视觉特征",
  shot_language_seed: "镜头表现",
  knowledge_boundaries: "认知边界",
  tts_voice_hints: "声音设定",
  silhouette: "体态轮廓",
  facial_anchors: "面部特征",
  body_language: "肢体语言",
  costume_palette: "服饰配色",
  signature_props: "标志物件",
  continuity_rules: "一致性规则",
  forbidden_drift: "禁止偏移",
  known_facts: "已知事实",
  suspected: "怀疑与推测",
  misbeliefs: "错误认知",
  secrets_kept: "保守的秘密",
  sensory_access_rules: "感官获取边界",
  timbre: "音色",
  register: "语域",
  cadence: "语速与节奏",
  accent: "口音",
  emotion_range: "情绪表现",
  pronunciation_notes: "发音注意事项",
  from_chapter: "衔接自章",
  to_chapter: "过渡至章",
  opening_time: "开场时间",
  opening_location: "开场地点",
  opening_pov: "开场视角",
  transition_mode: "过渡方式",
  emotional_carryover: "情感承接",
  action_handoff: "行动交接",
  pending_questions: "悬而未决的问题",
  forbidden_repetition: "下一章避免重复",
  bridge_summary: "桥接概述",
  sensory_anchors: "感官锚点",
  relationship_beat: "关系节拍",
  current_trust_level: "信任度",
  unspoken_tension: "潜在张力",
  power_dynamic: "权力动态",
  scene_intents: "场景编排",
  opening_contract: "开篇契约",
  closing_contract: "收束契约",
  emotional_arc: "情感弧线",
  required_state_transitions: "状态转换",
  foreshadowing_plan: "伏笔计划",
  key_revelations: "关键揭露",
  scene_id: "场景 ID",
  summary: "摘要",
  purpose: "目的",
  conflict: "冲突",
  required_characters: "涉及角色",
  character_motivations: "角色动机",
  entry_state_refs: "进入状态",
  required_outcome: "必要结果",
  exit_target_state: "退出状态",
  location: "地点",
  time_marker: "时间标记",
  character: "角色",
  motivation: "动机",
  stake: "风险",
  alignment_score: "对齐分数",
  risk_level: "风险级别",
  missing_main_points: "缺失主线要点",
  supportive_subplot_points: "支线正面贡献",
  weak_subplot_points: "薄弱支线",
  repair_actions: "修复建议",
  continuity_score: "连贯性分数",
  issues: "问题列表",
  recommendations: "建议",
  violations: "违规项",
  analysis: "分析",
  severity: "严重性",
  description: "描述",
  chapter_number: "章节编号",
  chapter_outline: "章节大纲",
  artifact: "产物",
  artifact_manifest: "产物清单",
  artifacts: "产物清单",
  workflow: "工作流",
  step: "步骤",
  input_hashes: "输入指纹",
  output_hashes: "输出指纹",
  paths: "文件路径",
  metadata: "元数据",
  reusable_failure: "可复用失败",
  repair_rounds: "修复轮次",
  updated_at: "更新时间",
  schema_version: "结构版本",
  reveal_guard_version: "揭示边界版本",
  reveal_guard_input_hashes: "揭示边界输入指纹",
  outline_reveal_guard: "大纲揭示边界",
  outline_output: "大纲输出指纹",
  editorial_revelation_ladder: "编辑揭示阶梯",
  narrative_contract: "叙事契约",
  canon_context: "Canon 上下文",
  previous_exit_state: "上章退出状态",
  previous_chapter_ending: "上章结尾",
  previous_creative_report: "上章创作报告",
  character_profiles: "角色资料",
  active_relationships: "活跃关系",
  active_plot_threads: "活跃情节线",
  must_carry_forward: "必须承接",
  known_characters: "已知角色",
  bridge: "桥接",
  overall_score: "总评分",
  scores: "各维度评分",
  passed: "是否通过",
  threshold: "阈值",
  dimension: "维度",
  score: "分数",
  comment: "评语",
  genre: "题材",
  tone: "基调",
  title: "标题",
  name: "名称",
  role: "角色定位",
  status: "状态",
  word_count: "字数",
  guard_report: "护栏报告",
  roster: "角色清单",
  profiles: "角色资料",
  relationship_edges: "人物关系边",
  identity_links: "身份/指代链接",
  audit: "审计记录",
  entities: "实体",
  entity_links: "实体链接",
  entity_id: "实体 ID",
  entity_type: "实体类型",
  source_id: "源 ID",
  target_id: "目标 ID",
  source_name: "源名称",
  target_name: "目标名称",
  link_type: "链接类型",
  time_layer: "时间层",
  confidence: "置信度",
  world_rules: "世界规则",
  character_arcs: "角色弧光",
  plot_threads: "情节线",
  promise_plan: "承诺规划",
  notes: "备注",
  chapter_contracts: "章节契约",
  entry_state_requirements: "入口状态要求",
  required_events: "必要事件",
  allowed_changes: "允许变化",
  forbidden_changes: "禁止变化",
  promise_ops: "承诺操作",
  relationship_ops: "关系操作",
  item_ops: "物件操作",
  knowledge_ops: "认知操作",
  cognitive_constraints: "认知揭示约束",
  exit_state_targets: "出口状态目标",
  required_progressions: "必须推进",
  allowed_progressions: "允许推进",
  forbidden_progressions: "禁止推进",
  completion_criteria: "完成标准",
  future_leak_risks: "未来泄露风险",
  milestone_window: "里程碑窗口",
  progression_ledger_tail: "推进账本尾部",
  contract_execution: "契约执行",
  missing_required_progressions: "缺失必达推进",
  unexpected_progressions: "意外推进",
  forbidden_progression_hits: "禁用推进命中",
  future_leak_hits: "未来泄露命中",
  cognitive_constraint_hits: "认知越界命中",
  contract_completion_score: "契约完成分",
  repair_or_replan_decision: "修复/重规划决定",
  should_block_archive: "是否阻断归档",
  expression_repetition: "表达通道重复",
  records: "记录",
  hits: "命中",
  repair_guidance: "修复指引",
  channel: "表达通道",
  channel_id: "通道 ID",
  cooldown_chapters: "冷却章数",
  actor_scope: "角色范围",
  examples: "例句",
  arc_liveness: "角色弧光活跃度",
  dormant_arcs: "沉睡弧光",
  last_progress_chapter: "上次推进章节",
  expected_next_touch: "预计下次触碰",
  planning_hint: "规划提示",
  stage_visibility: "阶段可见性",
  stage_visibility_diagnostics: "阶段可见性诊断",
  visible_current_milestones: "可见当前里程碑",
  visible_future_guardrails: "可见未来护栏",
  withheld_future_count: "隐藏未来节点数",
  policy: "策略",
  emotional_engine: "情感引擎",
  thematic_promises: "主题承诺",
  signature_motifs: "核心意象",
  relationship_tensions: "关系张力",
  anti_cliche_rules: "反套路清单",
  scene_potential: "关键场面潜力",
  synopsis: "故事概要",
  volumes: "分卷",
  narrative_phases: "叙事阶段",
  key_turning_points: "关键转折",
  subplot_plan: "支线规划",
  suspense_schedule: "悬念规划",
  ending_strategy: "收束策略",
  assembly_mode: "组装模式",
  last_chapter: "最近章节",
  recent_summaries: "近期摘要",
  accepted_updates: "已接受更新",
  facts_by_path: "状态路径事实",
  pending_items: "待定事项",
  memory_index_path: "记忆索引路径",
  allowed: "是否允许",
  required: "是否必需",
  block_min_severity: "阻断级别",
  stages: "阶段结果",
  repairs: "修复记录",
  repair_effectiveness: "修复效果",
  remaining_issues: "剩余问题",
  claims_count: "一致性 Claims 总数",
  extracted_claims_count: "抽取一致性 Claims",
  active_claims_count: "活跃一致性 Claims",
  candidate_count: "候选数",
  degraded_memory: "记忆降级",
  claim_ledger_path: "一致性 Claims 账本",
  efficiency: "效率统计",
  model_call_count_by_task: "模型调用",
  stage_durations: "阶段耗时",
  effective_batch_sizes: "有效批量",
  verdict: "裁决",
  blocked: "是否阻断",
  issue_count: "问题数",
  blocking_issue_count: "阻断问题",
  findings: "发现项",
  metrics: "指标",
  critical_count: "严重问题",
  high_count: "高风险问题",
  finding_count: "发现数",
  character_voice_count: "角色声纹",
  climax_marker_count: "高潮标记",
  theme_policy_count: "主题规则",
  symbol_policy_count: "象征策略",
  scene_resistance_rule_count: "场景阻力",
  revelation_step_count: "揭示节点",
  element_directive_count: "要素指令",
  state_update: "状态更新",
  evidence_quotes: "证据摘录",
  candidate_id: "候选 ID",
  delta_type: "状态类型",
  scope: "范围",
  state_path: "状态路径",
  value: "状态值",
  next_impact: "后续影响",
  entity_ids: "实体 ID",
  by_delta_type: "按类型归档",
  entries: "条目",
  project_id: "项目",
  project_mode: "项目模式",
  current_chapter: "当前章节",
  active_volume: "当前卷",
  created_at: "创建时间",
  source_chapter: "来源章节",
  relationships: "关系",
  timeline: "时间线",
};

/** Meta keys skipped by the generic report renderer (PySide6 `_GENERIC_META_KEYS`). */
export const GENERIC_META_KEYS: ReadonlySet<string> = new Set([
  "schema_version",
  "created_at",
  "updated_at",
  "report_type",
  "chapter_number",
  "project_id",
]);

export function genreLabel(value: string, locale?: Locale): string {
  if (locale && locale !== "zh") {
    const key = `genre.${value}`;
    return DICT[key]?.[locale] ?? value;
  }
  return GENRE_LABELS[value] ?? value;
}

export function toneLabel(value: string, locale?: Locale): string {
  if (locale && locale !== "zh") {
    const key = `tone.${value}`;
    return DICT[key]?.[locale] ?? value;
  }
  return TONE_LABELS[value] ?? value;
}

export function evalDimLabel(value: string): string {
  const mapped = EVAL_DIM_LABELS[value];
  if (mapped !== undefined) return mapped;
  if (value.includes("_")) return value.replaceAll("_", " ");
  return value;
}

/** Generic JSON key -> Chinese label, falling back to the raw key. */
export function genericKeyLabel(key: string): string {
  return GENERIC_KEY_LABELS[key] ?? key;
}

const LANGUAGE_LABELS: Readonly<Record<string, string>> = { zh: "中文", en: "English" };

export function languageLabel(value: string): string {
  return LANGUAGE_LABELS[value] ?? value;
}

/** 300000 -> "300,000 字" (PySide6 `{length:,} 字`). */
export function formatLength(length: number): string {
  return `${length.toLocaleString("en-US")} 字`;
}

/** Format a number with thousands separators. */
export function formatNumber(value: number): string {
  return value.toLocaleString("en-US");
}
