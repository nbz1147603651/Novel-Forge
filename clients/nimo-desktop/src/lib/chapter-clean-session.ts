/**
 * Request boundary shared by the chapter cleanup dialog and Engine command.
 *
 * The source QSpinBox never exposes an out-of-range cutoff.  React receives
 * browser text input, so normalize here before the destructive Engine command
 * consumes it.  The backend remains responsible for worker/authority checks,
 * artifact deletion, Canon rollback, and durable task cleanup.
 */

import type { ChapterCommandResult, EngineCommandClient } from "@nimo/engine-contracts";

export interface ChapterCleanRequest {
  readonly cutoff: number;
}

export function createChapterCleanRequest(input: {
  readonly cutoff: number;
  readonly maxChapter: number;
}): ChapterCleanRequest {
  const maximum = Math.max(1, Math.trunc(input.maxChapter));
  const proposed = Number.isFinite(input.cutoff) ? Math.trunc(input.cutoff) : 1;
  return { cutoff: Math.min(maximum, Math.max(1, proposed)) };
}

export async function executeChapterClean(input: {
  readonly client: EngineCommandClient;
  readonly projectId: string;
  readonly cutoff: number;
  readonly onRefreshRequired: (cutoff: number) => void;
}): Promise<ChapterCommandResult> {
  const result = await input.client.cleanChapters({
    kind: "clean_chapters",
    projectId: input.projectId,
    fromChapter: input.cutoff,
  });
  const refreshRequired = result.data?.refreshRequired === true;
  if (result.status === "accepted" || refreshRequired) input.onRefreshRequired(input.cutoff);
  return result;
}

export function describeChapterCleanFailure(error: unknown): string {
  const detail = error instanceof Error ? error.message.trim() : "";
  return [
    `未能确认章节清理结果${detail ? `：${detail}` : "。"}`,
    "请先刷新页面核对正文、Canon 水位与任务流，不要立即重复清理。",
  ].join("");
}
