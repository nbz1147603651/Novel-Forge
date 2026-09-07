import { lazy } from "react";

const loadChapterStudioPage = () =>
  import("./ChapterStudioPage").then((m) => ({ default: m.ChapterStudioPage }));

/**
 * Keep the dense chapter studio surface out of the application-shell chunk.
 *
 * The chapter studio includes the memory panel, version diff, book audit,
 * and multiple operation dialogs — all heavy enough to warrant splitting.
 */
export const LazyChapterStudioPage = lazy(loadChapterStudioPage);

export function preloadChapterStudioPage(): Promise<unknown> {
  return loadChapterStudioPage();
}
