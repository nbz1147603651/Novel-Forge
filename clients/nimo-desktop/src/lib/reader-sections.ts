/**
 * Shared top-level navigation for the long-form project reader and task
 * artifact dialogs. Keep these labels aligned with the PySide6 projects page:
 * a task artifact should open in the same conceptual shelf as its source
 * document, rather than as an undifferentiated JSON blob.
 */

export type ReaderSectionId =
  | "foundation"
  | "research"
  | "blueprint"
  | "subplots"
  | "chapter_design"
  | "outline"
  | "chapters"
  | "governance"
  | "tracking";

export type ReaderSectionKind = "chapters" | "direct" | "governance" | "nested" | "subplots" | "tracking";

export interface ReaderSectionDefinition {
  readonly id: ReaderSectionId;
  readonly label: string;
  readonly kind: ReaderSectionKind;
}

export const LONG_READER_SECTIONS: readonly ReaderSectionDefinition[] = [
  { id: "foundation", label: "基础设定", kind: "nested" },
  { id: "research", label: "资料检索", kind: "nested" },
  { id: "blueprint", label: "叙事蓝图", kind: "direct" },
  { id: "subplots", label: "支线管理", kind: "subplots" },
  { id: "chapter_design", label: "章节设计矩阵", kind: "direct" },
  { id: "outline", label: "章节大纲", kind: "direct" },
  { id: "chapters", label: "章节", kind: "chapters" },
  { id: "governance", label: "治理", kind: "governance" },
  { id: "tracking", label: "追踪", kind: "tracking" },
];

const SECTION_BY_ID = new Map(LONG_READER_SECTIONS.map((section) => [section.id, section]));

function section(id: ReaderSectionId): ReaderSectionDefinition {
  return SECTION_BY_ID.get(id)!;
}

function artifactKey(artifact: { readonly label: string; readonly path: string }): string {
  return `${artifact.path} ${artifact.label}`.toLocaleLowerCase();
}

function matches(key: string, pattern: RegExp): boolean {
  return pattern.test(key);
}

/**
 * Match a task artifact to the same long-form reader shelf used by its final
 * project document. Exact file names take priority, then the bounded path and
 * label fallbacks cover intermediate task products such as chapter plans.
 */
export function readerSectionForTaskArtifact(
  artifact: { readonly label: string; readonly path: string },
): ReaderSectionDefinition | null {
  const key = artifactKey(artifact);

  if (matches(key, /(?:^|[\s/])memory\//u)) {
    return section("tracking");
  }
  if (matches(key, /(?:^|[\s/])(?:chapters?|drafts?)\/|chapter[_-]?\d+|第\s*\d+\s*章|正文|草稿/u)) {
    return section("chapters");
  }
  if (matches(key, /(?:chapter_design_matrix|chapter_contracts?|chapter_plan|scene_(?:plan|intent)|章节设计|章节契约|场景计划|章节桥接|bridge)/u)) {
    return section("chapter_design");
  }
  if (matches(key, /(?:^|[\s/])outline(?:\.json|[\s/_-])|chapter_outline|章节大纲|节拍结构|beats\.json/u)) {
    return section("outline");
  }
  if (matches(key, /(?:subplot|支线)/u)) {
    return section("subplots");
  }
  if (matches(key, /(?:blueprint_elements_selection|spec\.json|story_bible|character_(?:bible|system)|style_profile|entity_(?:graph|registry)|故事规格|规格确认|世界观|角色与实体|要素与风格|风格规范)/u)) {
    return section("foundation");
  }
  if (matches(key, /(?:research|资料检索|资料分析|参考资料|检索报告)/u)) {
    return section("research");
  }
  if (matches(key, /(?:narrative_blueprint|short_blueprint|blueprint_fragments|creative_director|叙事蓝图|创意总监)/u)) {
    return section("blueprint");
  }
  if (matches(key, /(?:readiness|coherence|consistency|governance|guard|audit|contract|契约|准入|一致性|审计|护栏|治理)/u)) {
    return section("governance");
  }
  if (matches(key, /(?:reports?\/chapter|chapter[_-]?\d+.*(?:eval|creative|alignment|continuity|causal|reading_power|quality)|评估|创作总结|对齐报告|连贯性|因果链|追读力|质量门禁)/u)) {
    return section("chapters");
  }
  if (matches(key, /(?:report|memory|state|canon|relationship|token|eval|analysis|追踪|记忆|状态|关系|评估|报告)/u)) {
    return section("tracking");
  }
  return null;
}
