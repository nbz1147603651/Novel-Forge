import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ChapterCommandResult, ClearClosedTaskErrorsResult, EngineClient, EngineCommandClient, JobView, PageId, StepArtifactFile, TaskErrorResolutionResult, WorkflowErrorLogEntryView, WorkflowRunView, WorkflowView, WorkspaceView } from "@nimo/engine-contracts";

import { FloatingStreamDialog } from "./FloatingStreamDialog";
import {
  pipelineStepLabel,
  taskProgressProjection,
  type PipelineStep,
} from "./StepIndicatorRow";
import { TaskFocusPanel } from "./TaskFocusPanel";
import { TaskObservationDialog } from "./TaskObservationDialog";
import { WorkflowArtifactDialog } from "./WorkflowArtifactDialog";
import { WorkflowCancelDialog } from "./WorkflowCancelDialog";
import { WorkflowComposer, type WorkflowMode } from "./WorkflowComposer";
import { WorkflowErrorLogDialog } from "./WorkflowErrorLogDialog";
import { InitManualRepairDialog } from "./InitManualRepairDialog";
import { WorkflowRunCard } from "./WorkflowRunCard";
import { createTaskStreamState } from "../lib/task-stream";
import { useTaskStream } from "../lib/use-task-stream";
import { useElapsedClock } from "../hooks/useElapsedClock";
import { shouldShowLongInitResumeAction } from "../lib/long-init-action-state";
import { phaseIndexFromStepLabel } from "../lib/task-stream-presentation";
import { cancelWorkflowRun, workflowRunProjectId } from "../lib/workflow-run-session";
import { filterVisibleRuns, limitRenderedRuns } from "../lib/workflow-job-filter";
import { isActiveWorkflowRun, isClearableWorkflowRun, isFailedWorkflowRun, workflowRunState } from "../lib/workflow-run-state";
import { useWorkflowUiState } from "../lib/workflow-ui-state";
import type { WorkflowDialogFixture } from "../lib/parity-fixture";
import { useEngineRuntime } from "../lib/engine-runtime-context";
import { useLocale } from "../lib/i18n";

interface WorkflowPageProps {
  readonly commandClient: EngineCommandClient;
  readonly frozenStream: ReturnType<typeof createTaskStreamState> | null;
  readonly initialWorkflowDialog: WorkflowDialogFixture | null;
  readonly onNavigate: (page: PageId) => void;
  /** Refreshes the shared task projections used by cards and the pet. */
  readonly onTaskDataChanged: () => Promise<WorkflowView>;
  readonly shortTemplateExportRequest: number;
  readonly streamClient: EngineClient;
  readonly workflow: WorkflowView;
  readonly workflowMode: WorkflowMode;
  readonly onWorkflowModeChange: (mode: WorkflowMode) => void;
  readonly workspace: WorkspaceView;
}

/** 步骤产物弹窗状态：由点击步骤指示器/阶段点触发，走 engine getStepArtifacts 链路。 */
interface ArtifactDialogState {
  readonly runId: string;
  readonly stepLabel: string;
  readonly stepKey: string;
  readonly artifacts: readonly StepArtifactFile[];
  readonly loading?: boolean;
  readonly candidatePaths?: readonly { readonly label: string; readonly path: string }[];
  readonly emptyHint?: string;
}

export function WorkflowPage({
  commandClient,
  frozenStream,
  initialWorkflowDialog,
  onNavigate,
  onTaskDataChanged,
  shortTemplateExportRequest,
  streamClient,
  workflow,
  workflowMode,
  onWorkflowModeChange,
  workspace,
}: WorkflowPageProps) {
  const { isCommandAvailable } = useEngineRuntime();
  const locale = useLocale();
  const [runs, setRuns] = useState(workflow.runs);
  // 共享秒级时钟：所有运行卡共用单一定时器，避免每卡一个 setInterval。
  const anyRunning = runs.some((run) => {
    const state = workflowRunState(run);
    return state === "running" || state === "queued";
  });
  const clockTick = useElapsedClock(anyRunning);
  const [operationNotice, setOperationNotice] = useState("");
  const [taskFocusOpen, setTaskFocusOpen] = useState(false);
  const [floatingStreamOpen, setFloatingStreamOpen] = useState(
    initialWorkflowDialog === "floating-stream",
  );
  const [errorLogOpen, setErrorLogOpen] = useState(
    initialWorkflowDialog === "error-log",
  );
  const [artifactDialog, setArtifactDialog] = useState<ArtifactDialogState | null>(null);
  const artifactRequestId = useRef(0);
  const [manualRepairRun, setManualRepairRun] = useState<WorkflowRunView | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [cancelRunId, setCancelRunId] = useState<string | null>(
    () =>
      initialWorkflowDialog === "cancel"
        ? workflow.runs.find(isActiveWorkflowRun)?.id ?? null
        : null,
  );
  const fallbackObservedTaskId = runs.find(isActiveWorkflowRun)?.id ?? runs[0]?.id ?? null;
  // A frozen stream can be opened from the chapter studio while the workflow
  // projection still points at another active job. Keep the focus card and
  // its detail view anchored to the same task instead of mixing two runs.
  const frozenObservedTaskId = frozenStream !== null
    && runs.some((run) => run.id === frozenStream.taskId)
    ? frozenStream.taskId
    : null;
  const observedTaskId = selectedTaskId !== null && runs.some((run) => run.id === selectedTaskId)
    ? selectedTaskId
    : frozenObservedTaskId ?? fallbackObservedTaskId;
  const observedStream = useTaskStream(streamClient, observedTaskId);
  const stream = frozenStream !== null && frozenStream.taskId === observedTaskId
    ? frozenStream
    : observedStream;

  // Construct a JobView from the observed run for TaskFocusPanel consumption.
  const focusJob: JobView | null = useMemo(() => {
    const run = runs.find((r) => r.id === observedTaskId);
    if (run === undefined) return null;
    const frozenJob = frozenStream?.taskId === run.id ? frozenStream : null;
    const state = frozenJob?.jobState ?? workflowRunState(run);
    const taskProgress = taskProgressProjection({
      currentStepLabel: run.currentStageLabel,
      isRunning: state === "running" || state === "queued",
      kind: run.kind,
      locale,
      reportedProgress: run.progressPercent,
      stages: run.stages ?? [],
    });
    // Engine-formatted Chinese step label (batch x/y, verdict…) beats the
    // stage-sequence label and the raw step key on every focus surface.
    const stepLabel = frozenJob?.stepLabel ?? run.stepLabel ?? taskProgress.currentStepLabel;
    return {
      id: run.id,
      label: frozenJob?.title ?? run.title,
      projectId: "",
      state: state === "running" || state === "queued" ? "running"
        : state === "paused" ? "paused"
        : state === "failed" ? "failed"
        : "succeeded",
      currentStep: stepLabel,
      stepLabel,
      progressPercent: frozenJob?.progressPercent ?? taskProgress.progressPercent,
      detail: stepLabel || run.activityLabel || run.validationLabel || "",
    };
  }, [frozenStream, locale, runs, observedTaskId]);
  // The six-stage ribbon describes chapter generation only. Blueprint work in
  // long-form initialization has its own Engine-projected milestone tracker and should not be
  // relabelled as a chapter phase.
  const focusPhaseIndex = stream !== null && runs.find((run) => run.id === observedTaskId)?.kind === "run_chapter"
    ? phaseIndexFromStepLabel(stream.stepId ?? stream.stepLabel)
    : undefined;
  const unresolvedErrorCount = workflow.errorLog.filter(
    (entry) => !entry.autoResolved && !entry.acknowledgedAt,
  ).length;

  // UI state persistence (mirrors PySide6 export_ui_state/restore_ui_state)
  useWorkflowUiState({ mode: workflowMode });

  useEffect(() => setRuns(workflow.runs), [workflow.runs]);

  const refreshRuns = useCallback(async () => {
    const latest = await onTaskDataChanged();
    setRuns(latest.runs);
  }, [onTaskDataChanged]);

  useEffect(() => {
    if (!runs.some(isActiveWorkflowRun)) {
      return;
    }
    const refreshVisibleRuns = () => {
      if (document.visibilityState === "hidden") {
        return;
      }
      void refreshRuns().catch(() => undefined);
    };
    const timer = window.setInterval(refreshVisibleRuns, 1_500);
    document.addEventListener("visibilitychange", refreshVisibleRuns);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisibleRuns);
    };
  }, [refreshRuns, runs]);

  const requestRunAction = async (run: WorkflowRunView) => {
    if (isActiveWorkflowRun(run)) {
      setCancelRunId(run.id);
      return;
    }
    try {
      const result = await commandClient.resumeJob({
        kind: "resume_job",
        taskId: run.id,
      });
      setOperationNotice(result.message);
      if (result.status === "accepted") await refreshRuns();
    } catch (error) {
      setOperationNotice(error instanceof Error ? error.message : "恢复任务失败，任务卡保持原状态。");
    }
  };
  const confirmStop = async () => {
    if (cancelRunId === null) return;
    try {
      const result = await commandClient.cancelJob({
        kind: "cancel_job",
        taskId: cancelRunId,
        reason: "用户在机杼任务流中请求停止",
      });
      setOperationNotice(result.message);
      if (result.status === "accepted") {
        setRuns((current) => cancelWorkflowRun(current, cancelRunId));
        await refreshRuns();
      }
    } catch (error) {
      setOperationNotice(error instanceof Error ? error.message : "停止任务失败，任务仍保持原状态。");
    } finally {
      setCancelRunId(null);
    }
  };
  const retryInitRepair = async (run: WorkflowRunView): Promise<ChapterCommandResult> => {
    try {
      const result = await commandClient.retryInitRepair({
        kind: "retry_init_repair",
        projectId: run.projectId ?? run.projectLabel,
      });
      setOperationNotice(result.message);
      if (result.status === "accepted") {
        await refreshRuns();
      }
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : "立项修复提交失败，任务卡保持原状态。";
      setOperationNotice(message);
      throw new Error(message);
    }
  };
  const cancelRun = runs.find((run) => run.id === cancelRunId) ?? null;
  // Mirrors LongInitForm: _stop_button visible while an init_long job runs;
  // resume affordance appears when a long project is still in planning.
  const initRunning = runs.some(
    (run) =>
      run.kind === "init_long" &&
      isActiveWorkflowRun(run),
  );
  // Engine projections are newest-first. Only the latest init attempt may own
  // resume/restart semantics; an older failed card must not shadow a newer
  // successful initialization.
  const latestInitRun = runs.find((run) => run.kind === "init_long") ?? null;
  const activeInitRun = latestInitRun !== null && workflowRunState(latestInitRun) !== "succeeded"
    ? latestInitRun
    : null;
  // Latest run_short run for StepIndicator binding in ShortWorkflowComposer.
  const activeShortRun = [...runs]
    .reverse()
    .find((run) => run.kind === "run_short") ?? null;
  const resumableLongProject = workspace.projects.find(
    (project) => project.mode === "long" && project.initResumeAvailable,
  );
  const resumable = resumableLongProject !== undefined;
  const showHeaderInitResume = shouldShowLongInitResumeAction(resumable, initRunning);
  const focusLongInit = useCallback(() => {
    onWorkflowModeChange("long");
    window.requestAnimationFrame(() => {
      document.getElementById("long-init-form")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [onWorkflowModeChange]);
  // 对标 PySide6 restart_task_flow_cleanup_requested：失败立项可一键清理并重新发起。
  const hasFailedInit = activeInitRun !== null && isFailedWorkflowRun(activeInitRun);
  const clearHistory = async () => {
    try {
      const result = await commandClient.clearJobHistory({ kind: "clear_job_history" });
      setOperationNotice(result.message);
      if (result.status === "cleared") {
        const cleared = new Set(result.clearedTaskIds);
        setRuns((current) => current.filter((run) => !cleared.has(run.id)));
        await refreshRuns();
      }
    } catch (error) {
      setOperationNotice(error instanceof Error ? error.message : "清理任务历史失败。");
    }
  };
  const acknowledgeErrorEntries = useCallback(async (
    entries: readonly WorkflowErrorLogEntryView[],
  ): Promise<TaskErrorResolutionResult> => {
    const result = await commandClient.acknowledgeTaskErrors({
      kind: "acknowledge_task_errors",
      errorEntryIds: entries.map((entry) => entry.id),
    });
    if (result.status !== "acknowledged") throw new Error(result.message);
    setOperationNotice(result.message);
    await refreshRuns();
    return result;
  }, [commandClient, refreshRuns]);
  const reopenErrorEntries = useCallback(async (
    entries: readonly WorkflowErrorLogEntryView[],
  ): Promise<TaskErrorResolutionResult> => {
    const result = await commandClient.reopenTaskErrors({
      kind: "reopen_task_errors",
      errorEntryIds: entries.map((entry) => entry.id),
    });
    if (result.status !== "reopened") throw new Error(result.message);
    setOperationNotice(result.message);
    await refreshRuns();
    return result;
  }, [commandClient, refreshRuns]);
  const clearClosedErrorEntries = useCallback(async (
    entries: readonly WorkflowErrorLogEntryView[],
  ): Promise<ClearClosedTaskErrorsResult> => {
    const result = await commandClient.clearClosedTaskErrors({
      kind: "clear_closed_task_errors",
      errorEntryIds: entries.map((entry) => entry.id),
    });
    if (result.status !== "cleared") throw new Error(result.message);
    const clearedTasks = new Set(result.clearedTaskIds);
    setRuns((current) => current.filter((run) => !clearedTasks.has(run.id)));
    setOperationNotice(result.message);
    await refreshRuns();
    return result;
  }, [commandClient, refreshRuns]);
  const restartInitFlow = async () => {
    const terminalRunProjectId = activeInitRun !== null
      && ["failed", "paused"].includes(workflowRunState(activeInitRun))
      ? workflowRunProjectId(activeInitRun)
      : "";
    const projectId = terminalRunProjectId || resumableLongProject?.id;
    if (!projectId) {
      throw new Error("未找到可重新立项的项目，请刷新任务状态后重试。");
    }
    const result = await commandClient.restartLongInit({
      kind: "restart_long_init",
      projectId,
    });
    if (result.status !== "reset") throw new Error(result.message);
    const cleared = new Set(result.clearedTaskIds);
    setRuns((current) => current.filter((run) => !cleared.has(run.id)));
    setOperationNotice(result.message);
    await refreshRuns();
  };

  // ── Step artifact viewing (mirrors PySide6 _on_step_clicked) ──────
  // Clicking a completed step in a run card opens its real artifacts via
  // the EngineClient getStepArtifacts contract; the shared ArtifactViewer
  // renders them with format-aware Markdown/JSON output.
  const handleStepClick = useCallback(async (run: WorkflowRunView, step: PipelineStep) => {
    const projectId = workflowRunProjectId(run);
    const stepLabel = pipelineStepLabel(step, locale);
    if (!projectId) {
      setArtifactDialog({
        runId: run.id,
        stepKey: step.key,
        stepLabel,
        artifacts: [],
        emptyHint: "此任务尚未关联项目目录，因此暂时无法读取该步骤的产物。",
      });
      return;
    }

    const requestId = artifactRequestId.current + 1;
    artifactRequestId.current = requestId;
    // Open immediately so a slow local Engine never looks like a dead click.
    setArtifactDialog({
      runId: run.id,
      stepKey: step.key,
      stepLabel,
      artifacts: [],
      loading: true,
    });
    try {
      const result = await streamClient.getStepArtifacts(
        projectId,
        run.kind,
        step.key,
        run.chapterNumber,
      );
      if (artifactRequestId.current !== requestId) return;
      setArtifactDialog({
        runId: run.id,
        stepKey: step.key,
        stepLabel,
        artifacts: result.artifacts,
        ...(result.candidatePaths !== undefined ? { candidatePaths: result.candidatePaths } : {}),
        ...(result.emptyHint !== undefined ? { emptyHint: result.emptyHint } : {}),
      });
    } catch (error) {
      if (artifactRequestId.current !== requestId) return;
      const message = error instanceof Error ? error.message : "获取步骤产物失败。";
      setOperationNotice(message);
      setArtifactDialog({
        runId: run.id,
        stepKey: step.key,
        stepLabel,
        artifacts: [],
        emptyHint: `无法读取该步骤的产物：${message}`,
      });
    }
  }, [locale, streamClient]);
  const closeArtifactDialog = useCallback(() => {
    // Ignore a late response after the author has closed the loading window.
    artifactRequestId.current += 1;
    setArtifactDialog(null);
  }, []);

  return (
    <div className="workflow-page">
      <WorkflowComposer
        activeInitRun={activeInitRun}
        activeShortRun={activeShortRun}
        commandClient={commandClient}
        engineClient={streamClient}
        hasFailedInit={hasFailedInit}
        initRunning={initRunning}
        mode={workflowMode}
        onCancelInit={(runId) => setRuns((current) => cancelWorkflowRun(current, runId))}
        onFocusLongInit={focusLongInit}
        onModeChange={onWorkflowModeChange}
        onNavigate={onNavigate}
        onRestartInit={restartInitFlow}
        onWorkflowStarted={refreshRuns}
        projects={workspace.projects}
        resumable={resumable}
        shortTemplateExportRequest={shortTemplateExportRequest}
      />
      <section className="workflow-stream">
        <div className="workflow-stream-header">
          <div>
            <h2>任务流</h2>
            <p>后台任务的实时进度、执行步骤、Token 摘要与失败原因。</p>
          </div>
          <div className="workflow-actions">
            {runs.length > 1 && <label className="workflow-observed-task">观察任务<select aria-label="观察任务" onChange={(event) => setSelectedTaskId(event.target.value || null)} value={observedTaskId ?? ""}>{runs.map((run) => <option key={run.id} value={run.id}>{run.title} · {run.stateLabel}</option>)}</select></label>}
            <button
              className={`button button-secondary workflow-error-button${unresolvedErrorCount > 0 ? " is-unresolved" : " is-resolved"}`}
              onClick={() => setErrorLogOpen(true)}
              title="查看任务流中的格式错误、重试次数与失败摘要。"
              type="button"
            >
              {unresolvedErrorCount > 0 ? `错误日志 ${unresolvedErrorCount}` : "错误日志 · 已处理"}
            </button>
            <button
              className="button button-secondary workflow-clear-button"
              disabled={!runs.some(isClearableWorkflowRun)}
              onClick={() => void clearHistory()}
              title={"清空机杼任务流中的已完成、失败和已暂停记录。\n运行中或排队中的任务不会被中断。"}
              type="button"
            >
              🧹 清理
            </button>
            {showHeaderInitResume && (
              <button
                className="button button-primary"
                onClick={focusLongInit}
                title="回到长篇立项表单，从上次已落盘的节点继续。"
                type="button"
              >
                继续立项 →
              </button>
            )}
            {hasFailedInit && (
              <button
                className="button button-secondary workflow-clear-button"
                disabled={!isCommandAvailable("restart_long_init")}
                onClick={() => void restartInitFlow()}
                title={isCommandAvailable("restart_long_init")
                  ? "清理失败的立项任务流，可重新发起立项初始化。"
                  : "请先重启本地 Engine 以加载重新立项能力。"}
                type="button"
              >
                🔄 重启立项
              </button>
            )}
          </div>
        </div>
        {runs.length === 0 ? (
          <div className="workflow-empty">
            <span className="section-kicker">任务流已清理</span>
            <h3>机杼尚静，无任务在运</h3>
            <p>
              置卷后此处自现进度。正在执行的任务不会被中断；新的创作任务会在此显示进度。
            </p>
          </div>
        ) : (
          <div className="workflow-run-list">
            {limitRenderedRuns(filterVisibleRuns(runs)).map((run) => (
              <WorkflowRunCard
                key={run.id}
                onInitRepair={() => void retryInitRepair(run)}
                onManualRepair={() => setManualRepairRun(run)}
                onOpenArtifact={(step) => void handleStepClick(run, step)}
                onRunAction={() => void requestRunAction(run)}
                run={run}
                tick={clockTick}
              />
            ))}
          </div>
        )}
        {operationNotice && (
          <p aria-live="polite" className="studio-operation-notice">
            {operationNotice}
          </p>
        )}
      </section>

      <section className="workflow-focus">
        <div className="workflow-focus-header">
          <div>
            <h2>机杼关注</h2>
            <p>当前节点输出、诊断与需要确认的动作。</p>
          </div>
        </div>
        <TaskFocusPanel
          compact
          job={focusJob}
          onExpand={() => setTaskFocusOpen(true)}
          {...(focusPhaseIndex !== undefined ? { phaseIndex: focusPhaseIndex } : {})}
          scope="workflow"
          stream={stream}
          title="机杼关注"
        />
      </section>

      {errorLogOpen && (
        <WorkflowErrorLogDialog
          entries={workflow.errorLog}
          onAcknowledge={acknowledgeErrorEntries}
          onClearClosed={clearClosedErrorEntries}
          onClose={() => setErrorLogOpen(false)}
          onReopen={reopenErrorEntries}
        />
      )}
      {manualRepairRun !== null && (
        <InitManualRepairDialog
          commandClient={commandClient}
          engineClient={streamClient}
          onClose={() => setManualRepairRun(null)}
          onRetry={() => retryInitRepair(manualRepairRun)}
          projectId={workflowRunProjectId(manualRepairRun)}
        />
      )}
      {cancelRun !== null && (
        <WorkflowCancelDialog
          currentStep={taskProgressProjection({
            currentStepLabel: cancelRun.currentStageLabel,
            isRunning: true,
            kind: cancelRun.kind,
            locale,
            reportedProgress: cancelRun.progressPercent,
            stages: cancelRun.stages ?? [],
          }).currentStepLabel}
          jobLabel={cancelRun.title}
          onClose={() => setCancelRunId(null)}
          onConfirm={confirmStop}
        />
      )}
      {artifactDialog !== null && (
        <WorkflowArtifactDialog
          artifacts={artifactDialog.artifacts}
          {...(artifactDialog.loading === true ? { loading: true } : {})}
          {...(artifactDialog.candidatePaths !== undefined ? { candidatePaths: artifactDialog.candidatePaths } : {})}
          {...(artifactDialog.emptyHint !== undefined ? { emptyHint: artifactDialog.emptyHint } : {})}
          onClose={closeArtifactDialog}
          stageLabel={artifactDialog.stepLabel}
        />
      )}
      {taskFocusOpen && (
        <TaskObservationDialog
          job={focusJob}
          onClose={() => setTaskFocusOpen(false)}
          onOpenFloating={() => {
            setTaskFocusOpen(false);
            setFloatingStreamOpen(true);
          }}
          {...(focusPhaseIndex !== undefined ? { phaseIndex: focusPhaseIndex } : {})}
          stream={stream}
        />
      )}
      {floatingStreamOpen && (
        <FloatingStreamDialog
          onClose={() => setFloatingStreamOpen(false)}
          stream={stream}
        />
      )}
    </div>
  );
}
