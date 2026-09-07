/**
 * Read-only export configuration mirroring PySide6 ExportDialog.
 *
 * Phase 1 keeps the request inside the front-end session: it captures the
 * exact choices a future EngineClient command will receive, but never writes
 * an export file or starts a Python workflow.
 */

export type ChapterExportFormat = "markdown" | "txt" | "epub";
export type ChapterExportRange = "all" | "custom";

export interface ChapterExportRequest {
  readonly format: ChapterExportFormat;
  /** Empty means "all completed chapters", matching ExportDialog.get_chapter_range(). */
  readonly selectedChapters: readonly number[];
  readonly outputDirectory: string;
  readonly bookTitle: string;
}

export const chapterExportFormats: readonly { readonly id: ChapterExportFormat; readonly label: string }[] = [
  { id: "markdown", label: "Markdown (.md)" },
  { id: "txt", label: "纯文本 (.txt)" },
  { id: "epub", label: "EPUB (.epub)" },
];

export function createChapterExportRequest(input: {
  readonly format: ChapterExportFormat;
  readonly range: ChapterExportRange;
  readonly selectedChapters: ReadonlySet<number>;
  readonly outputDirectory: string;
  readonly bookTitle: string;
  readonly defaultBookTitle: string;
}): ChapterExportRequest {
  return {
    format: input.format,
    selectedChapters: input.range === "all" ? [] : [...input.selectedChapters].sort((left, right) => left - right),
    outputDirectory: input.outputDirectory,
    bookTitle: input.bookTitle.trim() || input.defaultBookTitle,
  };
}

export function chapterExportExtension(format: ChapterExportFormat): string {
  return format === "markdown" ? "md" : format;
}
