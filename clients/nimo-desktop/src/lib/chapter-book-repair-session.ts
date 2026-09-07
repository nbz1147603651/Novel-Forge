import type { ExecuteGlobalRepairQueueCommand } from "@nimo/engine-contracts";

export interface ChapterBookRepairParameters {
  readonly maxItems: number;
  readonly verifyBeforeApply: boolean;
  readonly rollbackOnFailure: boolean;
  readonly concurrency: number;
}

export const defaultChapterBookRepairParameters: ChapterBookRepairParameters = {
  maxItems: 20,
  verifyBeforeApply: true,
  rollbackOnFailure: true,
  concurrency: 1,
};

function clampInteger(value: number, minimum: number, maximum: number): number {
  const finiteValue = Number.isFinite(value) ? Math.trunc(value) : minimum;
  return Math.min(maximum, Math.max(minimum, finiteValue));
}

export function normalizeChapterBookRepairParameters(
  parameters: ChapterBookRepairParameters,
): ChapterBookRepairParameters {
  return {
    ...parameters,
    maxItems: clampInteger(parameters.maxItems, 1, 500),
    concurrency: clampInteger(parameters.concurrency, 1, 8),
  };
}

export function buildExecuteGlobalRepairQueueCommand(
  projectId: string,
  parameters: ChapterBookRepairParameters,
): ExecuteGlobalRepairQueueCommand {
  const normalized = normalizeChapterBookRepairParameters(parameters);
  return {
    kind: "execute_global_repair_queue",
    projectId,
    statuses: ["ready"],
    maxItems: normalized.maxItems,
    verifyBeforeApply: normalized.verifyBeforeApply,
    rollbackOnFailure: normalized.rollbackOnFailure,
    concurrency: normalized.concurrency,
  };
}
