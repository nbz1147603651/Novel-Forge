import type { TaskStreamEvent, TaskStreamValidationStatus } from "@nimo/engine-contracts";

import { detectStreamRenderKind, extractJsonBody } from "./stream-render-kind";
import type { TaskStreamState } from "./task-stream";
import {
  buildTaskStreamTranscript,
  type TaskStreamTranscriptItem,
} from "./task-stream-transcript";

export type TaskStreamTraceStatus = "completed" | "running" | "attention" | "unverified" | "superseded";

export interface TaskStreamTraceNode {
  readonly streamId: string;
  readonly index: number;
  readonly status: TaskStreamTraceStatus;
  readonly outputKind?: string;
  readonly structured: boolean;
  readonly contentText: string;
  readonly contentItems: readonly TaskStreamTranscriptItem[];
  readonly reasoningItems: readonly TaskStreamTranscriptItem[];
  readonly errorText?: string;
  readonly characterCount: number;
  readonly eventCount: number;
  readonly attempt?: number;
  readonly startedAt?: string;
  readonly endedAt?: string;
  readonly durationMs?: number;
  readonly operationId?: string;
  readonly validationStatus?: TaskStreamValidationStatus;
  readonly repairSource?: string;
  readonly maxAttempts?: number;
  readonly modelId?: string;
  readonly modelTaskId?: string;
  readonly finishReason?: string;
  readonly textTruncated: boolean;
  readonly rawContentText: string;
}

export interface TaskStreamTracePresentation {
  readonly nodes: readonly TaskStreamTraceNode[];
  readonly completedCount: number;
  readonly runningCount: number;
  readonly attentionCount: number;
  readonly unverifiedCount: number;
  readonly supersededCount: number;
  readonly totalCharacters: number;
}

export interface StructuredOutputDiagnostic {
  readonly expectedClosers: string;
  readonly lastField?: string;
}

interface MutableTraceNode {
  readonly streamId: string;
  readonly events: TaskStreamEvent[];
  readonly items: TaskStreamTranscriptItem[];
}

function dateValue(value: string | undefined): number | null {
  if (value === undefined) return null;
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? null : parsed;
}

function declaredOutputKind(
  events: readonly TaskStreamEvent[],
  items: readonly TaskStreamTranscriptItem[],
  fallback: string | undefined,
): string | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const value = events[index]?.outputKind;
    if (value !== undefined && value.length > 0) return value;
  }
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const value = items[index]?.outputKind;
    if (value !== undefined && value.length > 0) return value;
  }
  return fallback;
}

function isStructuredOutput(outputKind: string | undefined, text: string): boolean {
  const normalized = outputKind?.trim().toLowerCase() ?? "";
  if (normalized.includes("json") || normalized === "report" || normalized.includes("结构化")) {
    return true;
  }
  if (text.trim().length === 0) return false;
  const detected = detectStreamRenderKind(text);
  return detected === "json" || detected === "json_partial" || detected === "report";
}

function traceStatus(
  stream: TaskStreamState,
  events: readonly TaskStreamEvent[],
  structured: boolean,
  validation: TaskStreamValidationStatus | undefined,
): TaskStreamTraceStatus {
  if (validation === "validated") return "completed";
  if (validation === "failed") return "attention";
  if (validation !== undefined) {
    return stream.jobState === "running" || stream.jobState === "queued" ? "running" : "attention";
  }
  const errored = events.some((event) => event.kind === "stream_error");
  const settled = events.some((event) => event.kind === "stream_end" || event.kind === "stream_error");
  if (errored) return "attention";
  if (structured && (settled || stream.jobState !== "running")) return "unverified";
  if (!settled && stream.jobState === "failed") return "attention";
  return settled ? "completed" : "running";
}

/**
 * Project append-only stream events into a trace browser model. The projection
 * is deliberately pure so frequent token updates reconcile the same row rather
 * than appending another visual card.
 */
export function deriveTaskStreamTrace(
  stream: TaskStreamState | null,
): TaskStreamTracePresentation {
  if (stream === null) {
    return {
      nodes: [],
      completedCount: 0,
      runningCount: 0,
      attentionCount: 0,
      unverifiedCount: 0,
      supersededCount: 0,
      totalCharacters: 0,
    };
  }

  const grouped = new Map<string, MutableTraceNode>();
  const ensure = (streamId: string): MutableTraceNode => {
    const current = grouped.get(streamId);
    if (current !== undefined) return current;
    const created: MutableTraceNode = { streamId, events: [], items: [] };
    grouped.set(streamId, created);
    return created;
  };

  for (const event of stream.events) ensure(event.streamId).events.push(event);
  for (const item of buildTaskStreamTranscript(stream.events).items) {
    ensure(item.streamId).items.push(item);
  }

  const nodes = [...grouped.values()].map((group, index): TaskStreamTraceNode => {
    const contentItems = group.items.filter((item) => item.kind === "content");
    const reasoningItems = group.items.filter((item) => item.kind === "reasoning");
    const errorItem = [...group.items].reverse().find((item) => item.kind === "error");
    const contentText = contentItems.map((item) => item.text).join("");
    const outputKind = declaredOutputKind(group.events, group.items, stream.summary?.outputKind);
    const structured = isStructuredOutput(outputKind, contentText);
    const timestamps = group.events
      .map((event) => ({ raw: event.at, value: dateValue(event.at) }))
      .filter((item): item is { raw: string; value: number } => item.raw !== undefined && item.value !== null);
    const started = timestamps.at(0);
    const ended = group.events.some((event) => event.kind === "stream_end" || event.kind === "stream_error")
      ? timestamps.at(-1)
      : undefined;
    const attempt = [...group.events].reverse().find((event) => event.attempt !== undefined)?.attempt;
    const latest = [...group.events].reverse();
    const validation = latest.find((event) => event.validationStatus !== undefined);
    const operationId = latest.find((event) => event.operationId)?.operationId;
    const maxAttempts = latest.find((event) => event.maxAttempts !== undefined)?.maxAttempts;
    const modelId = latest.find((event) => event.modelId)?.modelId;
    const modelTaskId = latest.find((event) => event.modelTaskId)?.modelTaskId;
    const finishReason = latest.find((event) => event.finishReason)?.finishReason;
    const snapshot = latest.find((event) => event.segment === "content" && event.textMode === "snapshot");
    const rawContentText = buildTaskStreamTranscript(group.events.filter((event) => event.kind !== "validation"))
      .items.filter((item) => item.kind === "content").map((item) => item.text).join("");
    const errorText = validation?.message ?? errorItem?.text;

    return {
      streamId: group.streamId,
      index: index + 1,
      status: traceStatus(stream, group.events, structured, validation?.validationStatus),
      ...(outputKind === undefined ? {} : { outputKind }),
      structured,
      contentText,
      contentItems,
      reasoningItems,
      ...(errorText === undefined ? {} : { errorText }),
      characterCount: snapshot?.textLength ?? [...contentText].length,
      eventCount: group.events.length,
      ...(attempt === undefined ? {} : { attempt }),
      ...(operationId === undefined ? {} : { operationId }),
      ...(validation?.validationStatus === undefined ? {} : { validationStatus: validation.validationStatus }),
      ...(validation?.repairSource === undefined ? {} : { repairSource: validation.repairSource }),
      ...(maxAttempts === undefined ? {} : { maxAttempts }),
      ...(modelId === undefined ? {} : { modelId }),
      ...(modelTaskId === undefined ? {} : { modelTaskId }),
      ...(finishReason === undefined ? {} : { finishReason }),
      textTruncated: snapshot?.textTruncated === true,
      rawContentText,
      ...(started === undefined ? {} : { startedAt: started.raw }),
      ...(ended === undefined ? {} : { endedAt: ended.raw }),
      ...(started === undefined || ended === undefined
        ? {}
        : { durationMs: Math.max(0, ended.value - started.value) }),
    };
  });

  // Never resolve by modelTaskId: concurrent batches often use the SAME task.
  const latestAttempt = new Map<string, number>();
  for (const node of nodes) {
    if (node.operationId !== undefined) {
      latestAttempt.set(node.operationId, Math.max(latestAttempt.get(node.operationId) ?? 0, node.attempt ?? 1));
    }
  }
  const resolvedNodes = nodes.map((node): TaskStreamTraceNode => node.operationId !== undefined
    && (node.attempt ?? 1) < (latestAttempt.get(node.operationId) ?? 1)
    ? { ...node, status: "superseded" } : node);
  return {
    nodes: resolvedNodes,
    completedCount: resolvedNodes.filter((node) => node.status === "completed").length,
    runningCount: resolvedNodes.filter((node) => node.status === "running").length,
    attentionCount: resolvedNodes.filter((node) => node.status === "attention").length,
    unverifiedCount: resolvedNodes.filter((node) => node.status === "unverified").length,
    supersededCount: resolvedNodes.filter((node) => node.status === "superseded").length,
    totalCharacters: nodes.reduce((total, node) => total + node.characterCount, 0),
  };
}

/** Lightweight diagnostics for an ended JSON fragment; never mutates or repairs it. */
export function diagnoseStructuredFragment(text: string): StructuredOutputDiagnostic {
  const stack: string[] = [];
  let inString = false;
  let escaped = false;
  for (const character of extractJsonBody(text)) {
    if (inString) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') inString = true;
    else if (character === "{") stack.push("}");
    else if (character === "[") stack.push("]");
    else if ((character === "}" || character === "]") && stack.at(-1) === character) stack.pop();
  }

  const keyPattern = /"((?:\\.|[^"\\])*)"\s*:/g;
  let latest: RegExpExecArray | null = null;
  for (let match = keyPattern.exec(text); match !== null; match = keyPattern.exec(text)) latest = match;
  let lastField: string | undefined;
  if (latest?.[1] !== undefined) {
    try {
      lastField = JSON.parse(`"${latest[1]}"`) as string;
    } catch {
      lastField = latest[1];
    }
  }

  return {
    expectedClosers: stack.reverse().join(""),
    ...(lastField === undefined ? {} : { lastField }),
  };
}
