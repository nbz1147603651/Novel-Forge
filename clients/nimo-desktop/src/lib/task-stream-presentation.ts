import type { TaskStreamState } from "./task-stream";

export function taskStreamStatusLabel(status: TaskStreamState["status"], jobState?: TaskStreamState["jobState"]): string {
  if (status === "completed" && (jobState === "running" || jobState === "queued")) return "任务仍在进行";
  return { streaming: "输出中", paused: "已暂停", completed: "本批完成", failed: "输出失败" }[status];
}

export function taskStreamEventLabel(segment: "content" | "reasoning" | "system"): string {
  return { content: "正文", reasoning: "思考", system: "系统" }[segment];
}

/** Init-coherence stage labels (mirrors pipeline/progress.py INIT_COHERENCE_STAGE_LABELS). */
export const INIT_COHERENCE_STAGE_LABELS: Readonly<Record<string, string>> = {
  blueprint_coherence: "叙事蓝图",
  outline_inheritance: "章节大纲",
  contract_coherence: "章节契约",
};

/** Init-coherence verdict labels (mirrors pipeline/progress.py INIT_COHERENCE_VERDICT_LABELS). */
export const INIT_COHERENCE_VERDICT_LABELS: Readonly<Record<string, string>> = {
  accept: "通过",
  defer: "延后",
  ambiguous: "需确认",
  needs_repair: "需修复",
  reject: "未通过",
};

/** Init-coherence artifact labels (mirrors pipeline/progress.py INIT_COHERENCE_ARTIFACT_LABELS). */
export const INIT_COHERENCE_ARTIFACT_LABELS: Readonly<Record<string, string>> = {
  spec: "故事规格",
  story_bible: "世界观设定",
  character_bible: "角色设定",
  character_system: "角色系统",
  entity_registry: "实体注册表",
  entity_graph: "实体图谱",
  creative_director_packet: "创作导演包",
  blueprint: "叙事蓝图",
  outline: "章节大纲",
  narrative_contract: "叙事契约",
  chapter_contracts: "章节契约",
};

/** Frontend fallback base labels for init-coherence steps (legacy engines). */
const INIT_COHERENCE_BASE_LABELS: Readonly<Record<string, string>> = {
  extract_init_coherence_claims: "一致性 Claims 抽取",
  retrieve_init_conflict_candidates: "冲突候选检索",
  adjudicate_init_conflict_candidates: "冲突候选裁判",
  init_coherence_recheck_chunked_start: "一致性复查（分块）",
  init_coherence_recheck_chunk_start: "一致性复查",
  init_coherence_recheck_chunk_done: "一致性复查完成",
  init_coherence_recheck_scope_stopped: "一致性复查收敛",
  init_coherence_recheck_chunks: "一致性复查汇总",
};

function isRawStepKey(label: string): boolean {
  return /^[a-z0-9]+(?:_[a-z0-9]+)+$/i.test(label.trim());
}

function initCoherenceBaseLabel(step: string): string | null {
  for (const [prefix, label] of Object.entries(INIT_COHERENCE_BASE_LABELS)) {
    if (step === prefix || step.startsWith(`${prefix}_`)) return label;
  }
  return null;
}

/**
 * Frontend fallback formatter mirroring pipeline/progress.py format_step_label.
 * Used when the engine still sends a raw step key (mock mode / legacy engine).
 */
export function formatInitLongStepLabel(
  step: string,
  payload?: Readonly<Record<string, unknown>> | null,
): string {
  const data = payload ?? {};
  const base = initCoherenceBaseLabel(step);
  if (base === null) return step;
  const parts: string[] = [];
  const stage = INIT_COHERENCE_STAGE_LABELS[String(data.stage ?? "")];
  if (stage !== undefined) parts.push(`当前层：${stage}`);
  if (step.startsWith("extract_init_coherence_claims")) {
    const batch = Number(data.batch ?? data.batches_done);
    const batchTotal = Number(data.batch_total ?? 0);
    if (Number.isFinite(batch) && Number.isFinite(batchTotal) && batchTotal > 0) {
      parts.push(`${batch} / ${batchTotal}`);
    }
    const artifact = String(data.artifact ?? "").trim();
    if (artifact.length > 0) {
      parts.push(`当前检查：${INIT_COHERENCE_ARTIFACT_LABELS[artifact] ?? artifact}`);
    }
    const claims = Number(data.claims);
    if (Number.isFinite(claims)) parts.push(`已抽取 ${claims} 条`);
  } else if (step.startsWith("retrieve_init_conflict_candidates")) {
    const claims = Number(data.active_claims ?? data.claims);
    if (Number.isFinite(claims)) parts.push(`一致性 Claims ${claims}`);
    const candidates = Number(data.candidates);
    if (Number.isFinite(candidates)) parts.push(`候选 ${candidates}`);
    if (data.degraded_memory === true) parts.push("语义召回降级");
  } else if (step.startsWith("adjudicate_init_conflict_candidates")) {
    const batch = Number(data.batch);
    const batchTotal = Number(data.batch_total ?? 0);
    if (Number.isFinite(batch) && Number.isFinite(batchTotal) && batchTotal > 0) {
      parts.push(`批次 ${batch} / ${batchTotal}`);
    }
    const issues = Number(data.issues ?? data.issue_count);
    if (Number.isFinite(issues)) parts.push(`问题 ${issues}`);
    const verdict = INIT_COHERENCE_VERDICT_LABELS[String(data.verdict ?? "").toLowerCase()];
    if (verdict !== undefined) parts.push(`判定：${verdict}`);
  } else {
    const chunkIndex = Number(data.chunk_index);
    const chunkCount = Number(data.chunk_count ?? 0);
    if (Number.isFinite(chunkIndex) && Number.isFinite(chunkCount) && chunkCount > 0) {
      parts.push(`分块 ${chunkIndex} / ${chunkCount}`);
    }
    const focus = Array.isArray(data.focus_chapters)
      ? data.focus_chapters.slice(0, 6).join("、")
      : "";
    if (focus.length > 0) parts.push(`聚焦第 ${focus} 章`);
  }
  return parts.length > 0 ? `${base}  ·  ${parts.join("  ·  ")}` : base;
}

/**
 * Unified display label for a stream: engine-formatted Chinese labels are used
 * as-is; raw step keys fall back to the local init-coherence formatter.
 */
export function displayStepLabel(stream: TaskStreamState | null, fallback?: string): string {
  const label = stream?.stepLabel ?? fallback ?? "";
  if (label.length === 0) return "";
  if (!isRawStepKey(label)) return label;
  return formatInitLongStepLabel(label);
}

/**
 * Contextual status notice for the stream (mirrors PySide6 presentation.py::stream_notice).
 * Displayed below the runtime summary to inform the user about data safety.
 */
export function taskStreamNotice(status: TaskStreamState["status"]): string {
  switch (status) {
    case "streaming":
      return "预览中，未校验，未写入磁盘。";
    case "completed":
      return "模型输出完成，等待质量校验与正式落盘。";
    case "failed":
      return "流式输出出错；失败片段不会写入磁盘。";
    case "paused":
      return "已暂停。";
  }
}

/**
 * Derive the 6-phase chapter generation index from a step label.
 * Mirrors PySide6 phase_progress.py::_chapter_phase_index.
 * Returns 0-5 corresponding to: 规划/生成/审查/润色/人性化/收尾.
 */
export function phaseIndexFromStepLabel(stepLabel: string): number {
  const step = stepLabel.toLowerCase();
  if (step.includes("humanize")) return 4;
  if (step.includes("polish") || step.includes("word_count") || step.includes("restructure")) return 3;
  if (
    step.includes("repair") || step.includes("check") || step.includes("continuity") ||
    step.includes("causal") || step.includes("alignment") || step.includes("guard") ||
    step.includes("dedup") || step.includes("pronoun") || step.includes("reading_power")
  ) return 2;
  if (step.includes("draft") || step.includes("wave") || step.includes("generate")) return 1;
  if (
    step.includes("persist") || step.includes("canon") || step.includes("memory") ||
    step.includes("finalize") || step.includes("evaluate") || step.includes("extract") ||
    step.includes("macro_guard")
  ) return 5;
  // plan, bridge, context, compress, etc.
  return 0;
}
