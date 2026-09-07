/**
 * Pure presentation helpers for FinalRevisionWidget parity.
 *
 * Revision persistence and selection-candidate generation cross the Engine
 * boundary. This module keeps only display counts, validation and diff-preview
 * calculations that do not own durable project state.
 */

export type RevisionInvalidationScope = "none" | "next" | "volume" | "downstream";

export interface TextSelectionRange {
  readonly start: number;
  readonly end: number;
}

export interface RevisionGuardReport {
  readonly errors: readonly string[];
  readonly warnings: readonly string[];
  readonly wordCount: number;
  readonly expectedWordCount: number;
  readonly minAcceptable: number;
  readonly ok: boolean;
}

export const defaultRevisionDirections = ["更凝练", "更有画面", "增强张力", "更顺畅", "保留事实微调"] as const;

const promptLeakMatchers = [/提示词/u, /系统(?:指令|消息)/u, /ignore\s+(?:all\s+)?previous/iu, /you\s+are\s+chatgpt/iu];

/** Mirrors the source's display intent without importing the Python text core. */
export function countRevisionWords(text: string): number {
  const cjk = text.match(/[\u3400-\u9fff]/gu)?.length ?? 0;
  const nonCjk = text
    .replace(/[\u3400-\u9fff]/gu, " ")
    .match(/[\p{L}\p{N}]+/gu)?.length ?? 0;
  return cjk + nonCjk;
}

export function selectionWordCount(text: string, selection: TextSelectionRange): number {
  return countRevisionWords(text.slice(selection.start, selection.end));
}

export function validateRevisionDraft({ draft, expectedWordCount = 0, saved }: { readonly draft: string; readonly expectedWordCount?: number; readonly saved: string }): RevisionGuardReport {
  const wordCount = countRevisionWords(draft);
  const savedWordCount = countRevisionWords(saved);
  const minAcceptable = savedWordCount >= 500 ? Math.max(120, Math.floor(savedWordCount * 0.3)) : 0;
  const errors: string[] = [];
  const warnings: string[] = [];

  if (promptLeakMatchers.some((matcher) => matcher.test(draft))) {
    errors.push("检测到疑似提示词或系统指令泄露，请清理后再保存。");
  }
  if (minAcceptable > 0 && wordCount < minAcceptable) {
    errors.push(`正文过短：当前约 ${wordCount.toLocaleString()} 字，最低需要 ${minAcceptable.toLocaleString()} 字。`);
  }
  if (expectedWordCount > 0 && Math.abs(wordCount - expectedWordCount) / expectedWordCount >= 0.35) {
    warnings.push(`字数与章节目标偏离较大：当前约 ${wordCount.toLocaleString()} 字，目标 ${expectedWordCount.toLocaleString()} 字。`);
  }
  if (savedWordCount > 0 && wordCount > 0 && Math.abs(wordCount - savedWordCount) / savedWordCount >= 0.25) {
    warnings.push(`字数变化较大：保存前约 ${savedWordCount.toLocaleString()} 字，保存后约 ${wordCount.toLocaleString()} 字。`);
  }

  return { errors, warnings, wordCount, expectedWordCount, minAcceptable, ok: errors.length === 0 };
}

/** A bounded, inspectable unified-diff preview for the source save-review step. */
export function buildRevisionDiff(before: string, after: string, maxLines = 260): string {
  if (before === after) return "文本内容无可显示差异。";
  const beforeLines = before.split("\n");
  const afterLines = after.split("\n");
  let prefix = 0;
  while (prefix < beforeLines.length && prefix < afterLines.length && beforeLines[prefix] === afterLines[prefix]) prefix += 1;
  let beforeSuffix = beforeLines.length - 1;
  let afterSuffix = afterLines.length - 1;
  while (beforeSuffix >= prefix && afterSuffix >= prefix && beforeLines[beforeSuffix] === afterLines[afterSuffix]) {
    beforeSuffix -= 1;
    afterSuffix -= 1;
  }
  const contextLines = 3;
  const hunkStart = Math.max(0, prefix - contextLines);
  const hunkEndBefore = Math.min(beforeLines.length, beforeSuffix + 1 + contextLines);
  const hunkEndAfter = Math.min(afterLines.length, afterSuffix + 1 + contextLines);
  const contextBefore = beforeLines.slice(hunkStart, prefix);
  const removed = beforeLines.slice(prefix, beforeSuffix + 1);
  const added = afterLines.slice(prefix, afterSuffix + 1);
  const contextAfter = beforeLines.slice(beforeSuffix + 1, hunkEndBefore);
  const beforeHunkSize = hunkEndBefore - hunkStart;
  const afterHunkSize = hunkEndAfter - hunkStart;
  const body = [
    "--- 保存前",
    "+++ 保存后",
    `@@ -${hunkStart + 1},${beforeHunkSize} +${hunkStart + 1},${afterHunkSize} @@`,
    ...contextBefore.map((line) => ` ${line}`),
    ...removed.map((line) => `-${line}`),
    ...added.map((line) => `+${line}`),
    ...contextAfter.map((line) => ` ${line}`),
  ];
  if (body.length <= maxLines) return body.join("\n");
  return [...body.slice(0, maxLines), `… 省略 ${body.length - maxLines} 行 diff …`].join("\n");
}

export function scopeLabel(scope: RevisionInvalidationScope): string {
  return ({ none: "不级联", next: "仅下一章", volume: "仅本卷", downstream: "全下游" })[scope];
}
