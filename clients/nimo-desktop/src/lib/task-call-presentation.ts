import type { TaskModelCallStatus, TaskModelCallView } from "@nimo/engine-contracts";

import type { TaskStreamState } from "./task-stream";

export interface TaskCallGroup {
  readonly id: string;
  readonly label: string;
  readonly calls: readonly TaskModelCallView[];
  readonly providerModels: readonly string[];
  readonly status: TaskModelCallStatus;
  readonly promptTokens: number;
  readonly completionTokens: number;
  readonly totalTokens: number;
  readonly latencyMs: number;
  readonly costUsd: number;
}

export interface TaskCallPresentation {
  readonly calls: readonly TaskModelCallView[];
  readonly groups: readonly TaskCallGroup[];
  readonly promptTokens: number;
  readonly completionTokens: number;
  readonly totalTokens: number;
  readonly latencyMs: number;
  readonly costUsd: number;
  readonly runningCount: number;
  readonly retryCount: number;
  readonly failedCount: number;
  readonly maxGroupTokens: number;
  readonly hasTokenData: boolean;
  readonly hasLatencyData: boolean;
  readonly hasCostData: boolean;
}

interface MutableTaskCallGroup {
  id: string;
  label: string;
  calls: TaskModelCallView[];
  providerModels: Set<string>;
  status: TaskModelCallStatus;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  latencyMs: number;
  costUsd: number;
}

const STATUS_PRIORITY: Readonly<Record<TaskModelCallStatus, number>> = {
  success: 0,
  running: 1,
  retrying: 2,
  error: 3,
};

function callTotalTokens(call: TaskModelCallView): number {
  return call.totalTokens ?? (call.promptTokens ?? 0) + (call.completionTokens ?? 0);
}

function summaryFallback(stream: TaskStreamState): TaskModelCallView | null {
  const summary = stream.summary;
  if (summary === undefined) return null;
  const status: TaskModelCallStatus = stream.status === "completed"
    ? "success"
    : stream.status === "failed"
      ? "error"
      : "running";
  return {
    callId: `${stream.taskId}:summary`,
    task: stream.stepId,
    taskLabel: stream.stepLabel || stream.stepId || "当前模型调用",
    status,
    event: "summary",
    ...(summary.provider === undefined ? {} : { provider: summary.provider }),
    ...(summary.model === undefined ? {} : { model: summary.model }),
    ...(summary.attempt === undefined ? {} : { attempt: summary.attempt }),
    ...(summary.promptTokens === undefined ? {} : { promptTokens: summary.promptTokens }),
    ...(summary.completionTokens === undefined ? {} : { completionTokens: summary.completionTokens }),
    ...(summary.totalTokens === undefined ? {} : { totalTokens: summary.totalTokens }),
    ...(summary.elapsedMs === undefined ? {} : { latencyMs: summary.elapsedMs }),
    ...(summary.costUsd === undefined ? {} : { costUsd: summary.costUsd }),
  };
}

/** Build the call ledger once per stream snapshot; renderers consume this projection. */
export function deriveTaskCallPresentation(stream: TaskStreamState | null): TaskCallPresentation {
  const projectedCalls = stream?.calls ?? [];
  const fallback = stream === null || projectedCalls.length > 0 ? null : summaryFallback(stream);
  const calls = fallback === null ? projectedCalls : [fallback];
  const groupsByTask = new Map<string, MutableTaskCallGroup>();
  let promptTokens = 0;
  let completionTokens = 0;
  let totalTokens = 0;
  let latencyMs = 0;
  let costUsd = 0;
  let runningCount = 0;
  let retryCount = 0;
  let failedCount = 0;
  let hasTokenData = false;
  let hasLatencyData = false;
  let hasCostData = false;

  for (const call of calls) {
    const callPromptTokens = call.promptTokens ?? 0;
    const callCompletionTokens = call.completionTokens ?? 0;
    const callTokens = callTotalTokens(call);
    const callLatency = call.latencyMs ?? 0;
    const callCost = call.costUsd ?? 0;
    hasTokenData = hasTokenData
      || call.promptTokens !== undefined
      || call.completionTokens !== undefined
      || (call.totalTokens ?? 0) > 0;
    hasLatencyData = hasLatencyData || (call.latencyMs ?? 0) > 0;
    hasCostData = hasCostData || call.costUsd !== undefined;
    promptTokens += callPromptTokens;
    completionTokens += callCompletionTokens;
    totalTokens += callTokens;
    latencyMs += callLatency;
    costUsd += callCost;
    if (call.status === "running") runningCount += 1;
    if (call.status === "retrying" || call.willRetry === true || (call.attempt ?? 1) > 1) {
      retryCount += 1;
    }
    if (call.status === "error" && call.willRetry !== true) failedCount += 1;

    const id = call.task || call.taskLabel || call.callId;
    let group = groupsByTask.get(id);
    if (group === undefined) {
      group = {
        id,
        label: call.taskLabel || call.task || "模型调用",
        calls: [],
        providerModels: new Set<string>(),
        status: "success",
        promptTokens: 0,
        completionTokens: 0,
        totalTokens: 0,
        latencyMs: 0,
        costUsd: 0,
      };
      groupsByTask.set(id, group);
    }
    group.calls.push(call);
    const providerModel = [call.provider, call.model].filter(Boolean).join(" / ");
    if (providerModel.length > 0) group.providerModels.add(providerModel);
    if (STATUS_PRIORITY[call.status] > STATUS_PRIORITY[group.status]) group.status = call.status;
    group.promptTokens += callPromptTokens;
    group.completionTokens += callCompletionTokens;
    group.totalTokens += callTokens;
    group.latencyMs += callLatency;
    group.costUsd += callCost;
  }

  const groups = [...groupsByTask.values()].map((group): TaskCallGroup => ({
    ...group,
    calls: group.calls,
    providerModels: [...group.providerModels],
  }));
  let maxGroupTokens = 0;
  for (const group of groups) maxGroupTokens = Math.max(maxGroupTokens, group.totalTokens);

  return {
    calls,
    groups,
    promptTokens,
    completionTokens,
    totalTokens,
    latencyMs,
    costUsd,
    runningCount,
    retryCount,
    failedCount,
    maxGroupTokens,
    hasTokenData,
    hasLatencyData,
    hasCostData,
  };
}
