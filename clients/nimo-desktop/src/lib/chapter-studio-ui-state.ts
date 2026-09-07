/**
 * Chapter studio selector persistence.
 *
 * Mirrors the PySide6 chapter_studio export_ui_state/restore_project_preferences
 * semantics: only user choices are restored, never in-flight execution state,
 * so reopening the client never silently resumes work.
 *
 * Author: Novel Forge Team
 */

export type RewriteStrategyValue =
  | "auto"
  | "sequential"
  | "compatible"
  | "reconstruct"
  | "surgical";

export type StudioWritingMode = "manual" | "suggest" | "auto" | "book_auto";

export interface ChapterStudioUiPreferences {
  readonly compositionMode?: "whole" | "scene";
  readonly writingMode?: StudioWritingMode;
  readonly rewriteStrategy?: RewriteStrategyValue;
  readonly selectedChapterNumber?: number;
}

export interface ChapterStudioUiState {
  readonly version: number;
  readonly projects: Record<string, ChapterStudioUiPreferences>;
}

const STORAGE_KEY = "nimo:chapter-studio-ui-state";

const defaultUiState: ChapterStudioUiState = { version: 1, projects: {} };

const COMPOSITION_MODES: readonly ("whole" | "scene")[] = ["whole", "scene"];
const WRITING_MODES: readonly StudioWritingMode[] = [
  "manual",
  "suggest",
  "auto",
  "book_auto",
];
const REWRITE_STRATEGIES: readonly RewriteStrategyValue[] = [
  "auto",
  "sequential",
  "compatible",
  "reconstruct",
  "surgical",
];

export function loadChapterStudioUiState(): ChapterStudioUiState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultUiState;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (parsed.version !== 1 || typeof parsed.projects !== "object" || parsed.projects === null) {
      return defaultUiState;
    }
    return { version: 1, projects: parsed.projects as Record<string, ChapterStudioUiPreferences> };
  } catch {
    return defaultUiState;
  }
}

export function saveChapterStudioUiState(state: ChapterStudioUiState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage full or unavailable — non-fatal.
  }
}

/** Validate and normalize one project's saved preferences. */
function sanitizePreferences(raw: unknown): ChapterStudioUiPreferences {
  if (typeof raw !== "object" || raw === null) return {};
  const candidate = raw as Record<string, unknown>;
  const preferences: ChapterStudioUiPreferences = {};
  if (
    typeof candidate.compositionMode === "string" &&
    (COMPOSITION_MODES as readonly string[]).includes(candidate.compositionMode)
  ) {
    (preferences as { compositionMode?: "whole" | "scene" }).compositionMode =
      candidate.compositionMode as "whole" | "scene";
  }
  if (
    typeof candidate.writingMode === "string" &&
    (WRITING_MODES as readonly string[]).includes(candidate.writingMode)
  ) {
    (preferences as { writingMode?: StudioWritingMode }).writingMode =
      candidate.writingMode as StudioWritingMode;
  }
  if (
    typeof candidate.rewriteStrategy === "string" &&
    (REWRITE_STRATEGIES as readonly string[]).includes(candidate.rewriteStrategy)
  ) {
    (preferences as { rewriteStrategy?: RewriteStrategyValue }).rewriteStrategy =
      candidate.rewriteStrategy as RewriteStrategyValue;
  }
  if (
    typeof candidate.selectedChapterNumber === "number" &&
    Number.isInteger(candidate.selectedChapterNumber) &&
    candidate.selectedChapterNumber >= 1
  ) {
    (preferences as { selectedChapterNumber?: number }).selectedChapterNumber =
      candidate.selectedChapterNumber;
  }
  return preferences;
}

export function loadChapterStudioPreferences(
  projectId: string,
): ChapterStudioUiPreferences {
  if (!projectId) return {};
  return sanitizePreferences(loadChapterStudioUiState().projects[projectId]);
}

export function saveChapterStudioPreferences(
  projectId: string,
  preferences: ChapterStudioUiPreferences,
): void {
  const normalized = projectId.trim();
  if (!normalized) return;
  const state = loadChapterStudioUiState();
  saveChapterStudioUiState({
    version: 1,
    projects: { ...state.projects, [normalized]: sanitizePreferences(preferences) },
  });
}

/**
 * Derive the effective rewrite strategy shown to the user.
 *
 * Mirrors workspace/rewrite_strategy.py::_effective_strategy so the UI can
 * preview the engine verdict; the backend remains the source of truth.
 */
export function deriveEffectiveRewriteStrategy(
  strategy: RewriteStrategyValue,
  options: { readonly force: boolean; readonly hasDownstream: boolean },
): RewriteStrategyValue {
  if (!options.force && strategy === "auto") return "sequential";
  if (strategy === "auto") return options.hasDownstream ? "compatible" : "sequential";
  if (strategy === "compatible" && !options.hasDownstream) return "sequential";
  return strategy;
}
