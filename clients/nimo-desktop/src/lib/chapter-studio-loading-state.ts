/**
 * Pure helper that builds the props the chapter-studio branch in
 * ``App.tsx`` passes to ``<LoadingSurface>``.
 *
 * Exported separately so unit tests can assert the error / retry contract
 * without standing up the full React tree (the Tauri / jsdom / browser
 * bridge dependencies are not wired into this repo's vitest config).
 *
 * Priority: when both errors are present, ``chapterStudioError`` wins so
 * the user always sees the snapshot failure (the more impactful problem)
 * first, and the narrative-tools error stays on the recovery path.
 */
export function buildChapterStudioLoadingState(input: {
  readonly chapterStudioError: string | null;
  readonly narrativeToolsError: string | null;
  readonly onRetry: () => void;
}): { readonly error?: string; readonly onRetry: () => void } {
  if (input.chapterStudioError !== null) {
    return {
      error: `章台快照加载失败：${input.chapterStudioError}`,
      onRetry: input.onRetry,
    };
  }
  if (input.narrativeToolsError !== null) {
    return {
      error: `叙事工具加载失败：${input.narrativeToolsError}`,
      onRetry: input.onRetry,
    };
  }
  return { onRetry: input.onRetry };
}
