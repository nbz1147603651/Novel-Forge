import type { AuditBookCommand, AuditBookEditorialCommand } from "@nimo/engine-contracts";

/**
 * Typed, local-only configuration for the PySide6 BookAuditDialog surface.
 *
 * It deliberately models only a request envelope. Phase 1 may validate and
 * display it, but cannot start an audit, alter a project, or imply that a
 * model call happened.
 */

export type ChapterAuditRange = "all" | "custom";
export type ChapterAuditAnalysisMode = "full_text" | "summary";
export type ChapterAuditStrictness = "strict" | "balanced" | "loose";

export interface ChapterAuditParameters {
  readonly analysisMode: ChapterAuditAnalysisMode;
  readonly useTwoPhase: boolean;
  readonly targetChapterLimit: number;
  readonly locationStrictness: ChapterAuditStrictness;
  readonly maxTokens: number;
  readonly temperature: number;
  readonly chaptersPerBatch: number;
  readonly issuesPerChunk: number;
  readonly issuePoolLimit: number;
  readonly twoPhaseThreshold: number;
  readonly chapterMaxChars: number;
  readonly parallelChunks: boolean;
  readonly parallelDimensions: boolean;
  readonly promptHint: string;
}

export interface ChapterBookAuditRequest {
  /** Empty means all completed chapters, matching BookAuditDialog.get_chapter_range(). */
  readonly selectedChapters: readonly number[];
  readonly parameters: ChapterAuditParameters;
}

export function buildAuditBookCommand(
  projectId: string,
  request: ChapterBookAuditRequest,
): AuditBookCommand {
  const parameters = request.parameters;
  return {
    kind: "audit_book",
    projectId,
    chapterRange: request.selectedChapters,
    analysisMode: parameters.analysisMode,
    twoPhaseEnabled: parameters.useTwoPhase,
    twoPhaseMaxTargetChapters: parameters.targetChapterLimit,
    locationStrictness: parameters.locationStrictness,
    maxTokens: parameters.maxTokens,
    temperature: parameters.temperature,
    auditMaxChaptersPerBatch: parameters.chaptersPerBatch,
    auditMaxIssuesPerChunk: parameters.issuesPerChunk,
    auditIssuePoolMaxItems: parameters.issuePoolLimit,
    twoPhaseThreshold: parameters.twoPhaseThreshold,
    chapterMaxChars: parameters.chapterMaxChars,
    parallelChunks: parameters.parallelChunks,
    parallelDimensions: parameters.parallelDimensions,
    ...(parameters.promptHint ? { promptHint: parameters.promptHint } : {}),
  };
}

export function buildAuditBookEditorialCommand(
  projectId: string,
  request: ChapterBookAuditRequest,
): AuditBookEditorialCommand {
  const parameters = request.parameters;
  return {
    kind: "audit_book_editorial",
    projectId,
    chapterRange: request.selectedChapters,
    maxTokens: parameters.maxTokens,
    temperature: parameters.temperature,
    batchSize: parameters.chaptersPerBatch,
    ...(parameters.promptHint ? { promptHint: parameters.promptHint } : {}),
  };
}

export const defaultChapterAuditParameters: ChapterAuditParameters = {
  analysisMode: "full_text",
  useTwoPhase: true,
  targetChapterLimit: 4,
  locationStrictness: "balanced",
  maxTokens: 8192,
  temperature: 0.2,
  chaptersPerBatch: 4,
  issuesPerChunk: 12,
  issuePoolLimit: 160,
  twoPhaseThreshold: 0.7,
  chapterMaxChars: 12000,
  parallelChunks: false,
  parallelDimensions: true,
  promptHint: "",
};

export type ChapterBookAuditValidation =
  | { readonly kind: "valid"; readonly request: ChapterBookAuditRequest }
  | { readonly kind: "minimum-chapter-count" };

function clampInteger(value: number, minimum: number, maximum: number): number {
  const finiteValue = Number.isFinite(value) ? Math.trunc(value) : minimum;
  return Math.min(maximum, Math.max(minimum, finiteValue));
}

function clampDecimal(value: number, minimum: number, maximum: number): number {
  const finiteValue = Number.isFinite(value) ? value : minimum;
  return Math.min(maximum, Math.max(minimum, finiteValue));
}

/**
 * Mirrors the ranges enforced by the source QSpinBox/QDoubleSpinBox controls.
 * Keeping it in the request boundary means keyboard input cannot leak an
 * invalid browser-only value into a later Engine command.
 */
export function normalizeChapterAuditParameters(
  parameters: ChapterAuditParameters,
  completedChapterCount: number,
): ChapterAuditParameters {
  const maxCompletedChapters = Math.max(1, completedChapterCount);
  return {
    ...parameters,
    targetChapterLimit: clampInteger(parameters.targetChapterLimit, 1, maxCompletedChapters),
    maxTokens: clampInteger(parameters.maxTokens, 512, 65536),
    temperature: clampDecimal(parameters.temperature, 0, 2),
    chaptersPerBatch: clampInteger(parameters.chaptersPerBatch, 1, maxCompletedChapters),
    issuesPerChunk: clampInteger(parameters.issuesPerChunk, 1, 50),
    issuePoolLimit: clampInteger(parameters.issuePoolLimit, 0, 1000),
    twoPhaseThreshold: clampDecimal(parameters.twoPhaseThreshold, 0, 1),
    chapterMaxChars: clampInteger(parameters.chapterMaxChars, 1000, 100000),
    promptHint: parameters.promptHint.trim(),
  };
}

export function validateChapterBookAuditRequest(input: {
  readonly range: ChapterAuditRange;
  readonly selectedChapters: ReadonlySet<number>;
  readonly parameters: ChapterAuditParameters;
  readonly completedChapterCount: number;
}): ChapterBookAuditValidation {
  const selectedChapters = input.range === "all"
    ? []
    : [...input.selectedChapters].sort((left, right) => left - right);
  if (input.range === "custom" && selectedChapters.length < 2) {
    return { kind: "minimum-chapter-count" };
  }
  return {
    kind: "valid",
    request: {
      selectedChapters,
      parameters: normalizeChapterAuditParameters(input.parameters, input.completedChapterCount),
    },
  };
}
