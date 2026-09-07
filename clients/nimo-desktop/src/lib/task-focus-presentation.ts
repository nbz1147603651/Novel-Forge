import type { JobView, TaskStreamValidationStatus } from "@nimo/engine-contracts";

import { detectStreamRenderKind, STREAM_RENDER_KIND_LABEL } from "./stream-render-kind";
import type { TaskStreamState } from "./task-stream";
import { taskStreamTranscriptFromState, type TaskStreamTranscriptItem } from "./task-stream-transcript";
import { deriveTaskStreamTrace, type TaskStreamTraceStatus } from "./task-stream-trace";

export interface TaskFocusPresentation {
  readonly activityLabel: string;
  readonly contentHeading: string;
  readonly contentPreview: string;
  /** Retained current-node source; full readers must not parse a clipped card preview. */
  readonly contentText: string;
  /** Whether this exact stream is still receiving/generating output. */
  readonly contentLive: boolean;
  /** Whether this exact stream attempt has stopped changing. */
  readonly contentSettled: boolean;
  /** Full backend-reported content length, which may exceed `contentText`. */
  readonly contentTextLength: number;
  /** True when `contentText` is a bounded observation snapshot. */
  readonly contentTextTruncated: boolean;
  /** Durable backend verdict for this exact stream attempt. */
  readonly validationStatus: TaskStreamValidationStatus | undefined;
  /** Reader-facing trace status shared with the run-trace tab. */
  readonly contentStatus: TaskStreamTraceStatus | null;
  readonly outputCharacterCount: number;
  readonly previewEmptyText: string;
  readonly progress: number;
  readonly progressCaption: string;
  readonly reasoningCharacterCount: number;
  readonly reasoningPreview: string;
  readonly renderKindLabel: string | null;
  readonly outputKind: string | undefined;
  readonly stepLabel: string | null;
  readonly tickerText: string;
  readonly title: string;
}

const DISPLAY_PUNCTUATION = /[\s·:：\-—_/()[\]（）【】]/g;

function normalizedDisplayText(value: string): string {
  return value.trim().toLocaleLowerCase().replace(DISPLAY_PUNCTUATION, "");
}

export function isRedundantDisplayText(primary: string, secondary: string): boolean {
  const normalizedPrimary = normalizedDisplayText(primary);
  const normalizedSecondary = normalizedDisplayText(secondary);
  return normalizedPrimary.length > 0 && normalizedPrimary === normalizedSecondary;
}

function clipTail(text: string, limit: number): string {
  const characters = [...text.trim()];
  if (characters.length <= limit) return characters.join("");
  return `…${characters.slice(-limit).join("")}`;
}

function latestItem(
  items: readonly TaskStreamTranscriptItem[],
  kind: "content" | "reasoning",
): TaskStreamTranscriptItem | undefined {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item?.kind === kind) return item;
  }
  return undefined;
}

function progressCaption(
  job: JobView,
  stream: TaskStreamState | null,
  outputCharacterCount: number,
  structured: boolean,
  validationStatus: TaskStreamValidationStatus | undefined,
  contentStatus: TaskStreamTraceStatus | null,
): string {
  if (job.state === "paused" && (job.decisions?.length ?? 0) > 0) return "等待你的确认";
  if (job.state === "failed") return "查看失败原因";
  if (job.state === "queued") return "等待开始";
  if (structured && validationStatus === "validated") return "结构化结果已校验";
  if (structured && contentStatus === "attention") return "结构化结果需处理";
  if (structured && contentStatus === "unverified") return "结构化结果待核验";
  if (structured && stream?.status === "completed") return "结构化结果已生成";
  if (outputCharacterCount > 0) {
    return `${new Intl.NumberFormat("zh-CN").format(outputCharacterCount)} ${structured ? "字符已接收" : "字输出"}`;
  }
  if (stream?.status === "streaming") return "等待首段输出";
  if (job.state === "succeeded") return "任务已完成";
  return "等待任务流";
}

/**
 * Reader-oriented projection for every embedded focus surface.
 *
 * The Engine event log is intentionally verbose for replay. Focus surfaces use
 * the merged transcript instead, so token-sized deltas, raw event counts and
 * duplicate task/step labels never leak into the primary UI.
 */
export function deriveTaskFocusPresentation(
  job: JobView,
  stream: TaskStreamState | null,
): TaskFocusPresentation {
  const transcript = taskStreamTranscriptFromState(stream);
  const trace = deriveTaskStreamTrace(stream);
  const latestContent = latestItem(transcript.items, "content");
  const contentNode = latestContent === undefined
    ? undefined
    : [...trace.nodes].reverse().find((node) => node.streamId === latestContent.streamId);
  const latestReasoning = latestItem(transcript.items, "reasoning");
  const finalItem = transcript.items.at(-1);
  const title = job.label || job.currentStep || "后台任务";
  const rawStepLabel = job.stepLabel ?? (job.currentStep || job.detail || "");
  const stepLabel = rawStepLabel.length > 0 && !isRedundantDisplayText(title, rawStepLabel)
    ? rawStepLabel
    : null;
  const isThinking = stream?.status === "streaming" && finalItem?.kind === "reasoning";
  const contentStatus = contentNode?.status ?? null;
  const validationStatus = contentNode?.validationStatus;
  const contentLive = stream?.status === "streaming" && contentStatus === "running";
  const contentSettled = contentStatus === null
    ? stream?.status === "completed" || stream?.status === "paused" || stream?.status === "failed" || job.state === "succeeded"
    : contentStatus !== "running";
  const contentTextLength = Math.max(
    contentNode?.characterCount ?? 0,
    latestContent?.characterCount ?? 0,
  );
  const contentTextTruncated = contentNode?.textTruncated === true;
  const outputCharacterCount = Math.max(
    transcript.contentCharacterCount,
    contentTextLength,
    stream?.summary?.outputCharacters ?? 0,
  );
  const contentPreview = clipTail(latestContent?.text ?? "", 1_200);
  const reasoningPreview = clipTail(latestReasoning?.text ?? "", 280);
  const outputKind = latestContent?.outputKind ?? stream?.summary?.outputKind;
  const normalizedOutputKind = outputKind?.trim().toLocaleLowerCase();
  const detectedKind = normalizedOutputKind === "json" || normalizedOutputKind === "json_partial"
    ? "json"
    : normalizedOutputKind === "report"
      ? "report"
      : latestContent === undefined
        ? null
        : detectStreamRenderKind(latestContent.text);
  const detectedRenderKind = detectedKind === null ? null : STREAM_RENDER_KIND_LABEL[detectedKind];
  const structured = detectedKind === "json" || detectedKind === "json_partial" || detectedKind === "report";
  const progressText = progressCaption(
    job,
    stream,
    outputCharacterCount,
    structured,
    validationStatus,
    contentStatus,
  );

  const structuredActivity = validationStatus === "validated"
    ? "结果已校验"
    : validationStatus === "validating"
      ? "校验中"
      : validationStatus === "repairing"
        ? "修复中"
        : validationStatus === "retrying"
          ? "重试中"
          : contentStatus === "attention"
            ? "需处理"
            : contentStatus === "unverified"
              ? "待核验"
              : contentLive
                ? "组装中"
                : "最近输出";

  return {
    activityLabel: isThinking
      ? "思考中"
      : structured
        ? structuredActivity
          : stream?.status === "streaming"
            ? "输出中"
            : "最近输出",
    contentHeading: detectedKind === "json" || detectedKind === "json_partial"
      ? "当前节点结果"
      : detectedKind === "report"
        ? "评估报告"
        : "实时正文",
    contentPreview,
    contentText: latestContent?.text ?? "",
    contentLive,
    contentSettled,
    contentTextLength,
    contentTextTruncated,
    validationStatus,
    contentStatus,
    outputCharacterCount,
    previewEmptyText: isThinking
      ? "正在梳理当前节点，首段结果随后出现。"
      : structured
        ? "等待结构化结果。"
        : "等待当前节点输出。",
    progress: job.state === "succeeded" ? 100 : Math.max(0, Math.min(100, job.progressPercent)),
    progressCaption: progressText,
    reasoningCharacterCount: latestReasoning?.characterCount ?? 0,
    reasoningPreview,
    renderKindLabel: detectedRenderKind === "文本" ? null : detectedRenderKind,
    outputKind,
    stepLabel,
    tickerText: structured && validationStatus === "validated"
      ? contentTextTruncated
        ? `结构化结果已校验 · 当前为 ${new Intl.NumberFormat("zh-CN").format(latestContent?.characterCount ?? 0)} / ${new Intl.NumberFormat("zh-CN").format(contentTextLength)} 字符快照`
        : "结构化结果已校验，可打开查看字段。"
      : structured && contentLive
      ? `正在组装结构化结果 · ${new Intl.NumberFormat("zh-CN").format(outputCharacterCount)} 字符`
      : structured && contentStatus === "attention"
        ? "结构化输出需处理，当前片段不作为正式结果。"
      : structured && contentStatus === "unverified"
        ? "结构化输出已停止更新，正等待后端校验结论。"
        : contentPreview.length > 0
      ? clipTail(contentPreview, 120)
      : isThinking
        ? "正在梳理当前节点…"
        : progressText,
    title,
  };
}
