export type OperationState = "configuration" | "running" | "completed" | "failed";

export interface OperationProgressStep {
  readonly id: string;
  readonly label: string;
  /** Elapsed time label for tooltip (e.g. "3.2s"). */
  readonly elapsedLabel?: string;
}

/**
 * A pair of step ids that execute concurrently.
 * Rendered with a fork/merge visual in the timeline.
 */
export interface OperationParallelGroup {
  readonly stepIds: readonly [string, string];
}

interface OperationProgressProps {
  readonly state: OperationState;
  readonly title: string;
  readonly runningLabel: string;
  readonly completedLabel: string;
  readonly steps: readonly OperationProgressStep[];
  /**
   * The id of the currently active step, driven by backend `stepLabel`.
   * When provided, replaces the former `Math.ceil(steps.length / 2)` heuristic.
   */
  readonly activeStepId?: string | null;
  /** Concurrent step pairs rendered with fork/merge indicators. */
  readonly parallelGroups?: readonly OperationParallelGroup[];
}

/**
 * A visual-only operation timeline shared by export, audit, and future
 * EngineCommandClient commands. The command boundary remains outside this component.
 *
 * Enhancements over Phase-1 baseline:
 * - Real-time step driving via `activeStepId` (replaces heuristic estimation)
 * - Parallel step group indicators (fork/merge visual)
 * - Failed step state styling
 * - Per-step elapsed tooltip
 */
export function OperationProgress({ activeStepId, completedLabel, parallelGroups, runningLabel, state, steps, title }: OperationProgressProps) {
  // Determine completed count from activeStepId when available.
  const activeIndex = activeStepId !== undefined && activeStepId !== null
    ? steps.findIndex((step) => step.id === activeStepId)
    : -1;
  const completedSteps = state === "completed"
    ? steps.length
    : activeIndex > 0
      ? activeIndex
      : state === "running"
        ? Math.max(1, Math.ceil(steps.length / 2))
        : 0;
  const stateLabel = state === "configuration" ? "等待配置" : state === "running" ? runningLabel : state === "completed" ? completedLabel : "执行失败";

  // Build a set of step ids that are part of a parallel group.
  const parallelStepIds = new Set<string>();
  if (parallelGroups !== undefined) {
    for (const group of parallelGroups) {
      for (const id of group.stepIds) {
        parallelStepIds.add(id);
      }
    }
  }

  const stepClassName = (step: OperationProgressStep, index: number): string => {
    const classes: string[] = [];
    if (index < completedSteps) classes.push("is-complete");
    else if (index === completedSteps && state === "running") classes.push("is-active");
    if (state === "failed" && index === completedSteps) classes.push("is-failed");
    if (parallelStepIds.has(step.id)) classes.push("is-parallel");
    return classes.join(" ");
  };

  return (
    <section aria-live="polite" className={`operation-progress is-${state}`}>
      <header><span className="section-kicker">任务状态</span><strong>{title}</strong><p>{stateLabel}</p></header>
      <ol aria-label={`${title}步骤`}>
        {steps.map((step, index) => (
          <li
            className={stepClassName(step, index)}
            key={step.id}
            title={step.elapsedLabel !== undefined ? `${step.label} · ${step.elapsedLabel}` : step.label}
          >
            <span>{index < completedSteps ? "✓" : state === "failed" && index === completedSteps ? "✗" : index + 1}</span>
            <small>{step.label}</small>
            {parallelStepIds.has(step.id) && <em aria-hidden="true" className="operation-parallel-mark">‖</em>}
          </li>
        ))}
      </ol>
      {state === "completed" && <p className="operation-result">前端会话已完成该流程展示；真实结果将由受保护的 EngineCommandClient 适配器写入指定位置。</p>}
      {state === "failed" && <p className="operation-result is-failed">本次操作未完成。保留当前配置，可检查条件后重试。</p>}
    </section>
  );
}
