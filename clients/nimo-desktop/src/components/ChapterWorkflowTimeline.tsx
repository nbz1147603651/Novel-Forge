/**
 * Detailed chapter workflow timeline.
 *
 * The action panel owns commands and checkpoints; this surface owns the
 * observable six-phase production flow and its step artifacts.  Keeping the
 * two concerns separate gives the chapter studio the same inspectable task
 * experience as the workflow workbench without crowding the primary action.
 */
import { useCallback, useEffect, useState } from "react";

import type {
  ChapterStudioActivityView,
  EngineClient,
  StepArtifactFile,
} from "@nimo/engine-contracts";

import {
  CHAPTER_WORKFLOW_PHASES,
  chapterWorkflowPhaseLabel,
  chapterWorkflowPhaseSteps,
} from "./chapter-workflow-model";
import { RunInsightStrip } from "./RunInsightStrip";
import { DropdownSelect } from "./DropdownSelect";
import { WorkflowArtifactDialog } from "./WorkflowArtifactDialog";
import {
  pipelineStepLabel,
  taskProgressProjection,
  type PipelineStep,
  type StepState,
  workflowStepVisualState,
} from "./StepIndicatorRow";
import { useLocale, type Locale } from "../lib/i18n";

type ArtifactDialogState = {
  readonly artifacts: readonly StepArtifactFile[];
  readonly candidatePaths?: readonly { readonly label: string; readonly path: string }[];
  readonly emptyHint?: string;
  readonly stageLabel: string;
};

export interface ChapterWorkflowTimelineProps {
  readonly activity: ChapterStudioActivityView;
  readonly chapterNumber: number;
  readonly engineClient: EngineClient;
  readonly onNotice: (message: string) => void;
  readonly projectId: string;
}

const NODE_STATE_LABELS: Record<StepState, Record<Locale, string>> = {
  done: { zh: "查看产物", en: "View artifacts" },
  active: { zh: "执行中", en: "In progress" },
  failed: { zh: "已失败", en: "Failed" },
  skipped: { zh: "已跳过", en: "Skipped" },
  blocked: { zh: "已阻断", en: "Blocked" },
  rolled_back: { zh: "已回滚", en: "Rolled back" },
  pending: { zh: "待执行", en: "Pending" },
};

function nodeStatusLabel(state: StepState, locale: Locale): string {
  return NODE_STATE_LABELS[state][locale];
}

function nodeStateGlyph(state: StepState): string {
  switch (state) {
    case "done": return "✓";
    case "active": return "●";
    case "failed": return "×";
    case "skipped": return "–";
    case "blocked": return "!";
    case "rolled_back": return "↶";
    default: return "·";
  }
}

/**
 * A full-width, six-phase task surface immediately above chapter artifacts.
 * Completed node buttons reuse the workflow workbench's Engine artifact API.
 */
export function ChapterWorkflowTimeline({
  activity,
  chapterNumber,
  engineClient,
  onNotice,
  projectId,
}: ChapterWorkflowTimelineProps) {
  const locale = useLocale();
  const [artifactDialog, setArtifactDialog] = useState<ArtifactDialogState | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const history = activity.history ?? [];
  const chapterFlow = activity.chapterFlow ?? null;
  useEffect(() => {
    if (
      (selectedTaskId === "__current__" && activity.state === "idle")
      || (
        selectedTaskId !== null
        && selectedTaskId !== "__current__"
        && !history.some((run) => run.taskId === selectedTaskId)
      )
    ) {
      setSelectedTaskId(null);
    }
  }, [activity.state, history, selectedTaskId]);
  const selectedHistory = selectedTaskId !== null && selectedTaskId !== "__current__"
    ? history.find((run) => run.taskId === selectedTaskId)
    : chapterFlow === null && activity.state === "idle"
      ? history[0]
      : undefined;
  const showingChapterFlow = selectedTaskId === null && chapterFlow !== null;
  const viewedActivity: ChapterStudioActivityView = showingChapterFlow
    ? {
        kind: chapterFlow.kind,
        taskId: chapterFlow.taskId,
        state: chapterFlow.status === "running" || chapterFlow.status === "queued"
          ? "running"
          : chapterFlow.status === "paused"
            ? "checkpoint"
            : "idle",
        taskLabel: chapterFlow.taskLabel,
        currentStepLabel: chapterFlow.currentStepLabel,
        ...(activity.operationDetail === undefined
          ? {}
          : { operationDetail: activity.operationDetail }),
        progressPercent: chapterFlow.progressPercent,
        checkpoint: chapterFlow.status === "paused" ? activity.checkpoint : null,
        stages: chapterFlow.stages,
      }
    : selectedHistory === undefined
      ? activity
      : {
        kind: selectedHistory.kind,
        taskId: selectedHistory.taskId,
        state: "idle",
        taskLabel: selectedHistory.taskLabel,
        currentStepLabel: selectedHistory.currentStepLabel,
        progressPercent: selectedHistory.progressPercent,
        checkpoint: null,
        stages: selectedHistory.stages,
      };
  const showingHistory = selectedHistory !== undefined;
  const taskProgress = taskProgressProjection({
    currentStepLabel: viewedActivity.currentStepLabel,
    isRunning: viewedActivity.state === "running",
    kind: viewedActivity.kind ?? "run_chapter",
    locale,
    reportedProgress: viewedActivity.progressPercent,
    stages: viewedActivity.stages ?? [],
  });
  // The Engine sequence is authoritative, including interactive checkpoint
  // and finalization jobs. Never discard nodes by intersecting with 18 keys.
  const steps = taskProgress.steps;
  const terminalStates = new Set<StepState>(["done", "skipped", "rolled_back"]);
  const completedCount = steps.filter((step) => (
    terminalStates.has(workflowStepVisualState(step, taskProgress.stepState, steps))
  )).length;
  const currentLabel = taskProgress.currentStepLabel || (
    viewedActivity.state === "running"
      ? (locale === "zh" ? "正在同步引擎阶段" : "Syncing Engine stages")
      : (locale === "zh" ? "等待启动章节任务" : "Waiting to start chapter task")
  );
  const operationDetail = showingHistory ? "" : viewedActivity.operationDetail?.trim() ?? "";

  const openArtifact = useCallback(async (step: PipelineStep) => {
    try {
      const result = await engineClient.getStepArtifacts(
        projectId,
        viewedActivity.kind ?? "run_chapter",
        step.key,
        chapterNumber,
      );
      setArtifactDialog({
        artifacts: result.artifacts,
        ...(result.candidatePaths !== undefined ? { candidatePaths: result.candidatePaths } : {}),
        ...(result.emptyHint !== undefined ? { emptyHint: result.emptyHint } : {}),
        stageLabel: pipelineStepLabel(step, locale),
      });
    } catch (error) {
      onNotice(error instanceof Error
        ? error.message
        : (locale === "zh"
          ? "获取步骤产物失败。请稍后重试。"
          : "Could not load step artifacts. Please try again."));
    }
  }, [chapterNumber, engineClient, locale, onNotice, projectId, viewedActivity.kind]);

  const taskOptions = [
    ...(chapterFlow === null ? [] : [{
      value: "__flow__",
      label: `${locale === "zh" ? "本章全流程" : "Full chapter flow"} · ${chapterFlow.updatedAt.slice(5, 16).replace("T", " ")}`,
    }]),
    ...(activity.state === "idle" ? [] : [{
      value: "__current__",
      label: `${locale === "zh" ? "当前尝试" : "Current attempt"} · ${activity.taskLabel}`,
    }]),
    ...history.map((run) => ({
      value: run.taskId,
      label: `${locale === "zh" ? "历史" : "History"} · ${run.taskLabel} · ${run.updatedAt.slice(5, 16).replace("T", " ")}`,
    })),
  ];
  const taskOptionValue = showingChapterFlow
    ? "__flow__"
    : showingHistory
      ? selectedHistory.taskId
      : "__current__";

  return (
    <section aria-labelledby="chapter-workflow-title" className="studio-workflow-timeline">
      <header className="studio-workflow-heading">
        <div>
          <span className="section-kicker">CHAPTER FLOW</span>
          <h2 id="chapter-workflow-title">{locale === "zh" ? "章台任务流" : "Chapter workflow"}</h2>
          <p>{locale === "zh"
            ? `六个阶段、${steps.length} 个可追踪节点；${showingChapterFlow ? "当前查看本章累计流程，可直接打开前置产物。" : showingHistory ? "当前查看某次任务的持久记录。" : "按当前任务显示执行、跳过和等待确认状态。"}`
            : `Six phases and ${steps.length} traceable milestones; ${showingChapterFlow ? "showing the cumulative chapter flow with earlier artifacts." : showingHistory ? "showing one persisted task attempt." : "states follow the current Engine task."}`}</p>
        </div>
        <div className="studio-workflow-heading-actions">
          {taskOptions.length > 1 && <DropdownSelect
            ariaLabel={locale === "zh" ? "查看章节任务记录" : "View chapter task run"}
            className="studio-workflow-run-select"
            onChange={(value) => setSelectedTaskId(value === "__flow__" ? null : value)}
            options={taskOptions}
            value={taskOptionValue}
          />}
          <div className="studio-workflow-summary">
            <strong>{taskProgress.progressPercent}%</strong>
            <span>{completedCount}/{steps.length} {locale === "zh" ? "已收束" : "settled"}</span>
          </div>
        </div>
      </header>

      <div
        className="studio-workflow-progress"
        aria-label={`${locale === "zh" ? "章节任务进度" : "Chapter task progress"} ${taskProgress.progressPercent}%`}
      >
        <span style={{ width: `${taskProgress.progressPercent}%` }} />
      </div>
      <p aria-live="polite" className="studio-workflow-current">
        {locale === "zh"
          ? (showingChapterFlow ? "本章流程：" : showingHistory ? "历史尝试：" : "当前步骤：")
          : (showingChapterFlow ? "Chapter flow: " : showingHistory ? "Historical attempt: " : "Current step: ")}<strong>{currentLabel}</strong>
        {operationDetail ? <span> · {operationDetail}</span> : null}
      </p>
      <RunInsightStrip
        {...(!showingHistory && activity.efficiency !== undefined ? { efficiency: activity.efficiency } : {})}
        insights={showingHistory ? [] : activity.runInsights ?? []}
        onOpenArtifact={(stepKey) => {
          const step = steps.find((item) => item.key === stepKey);
          if (step !== undefined) void openArtifact(step);
        }}
      />

      <div className="studio-workflow-phases">
        {CHAPTER_WORKFLOW_PHASES.map((phase) => {
          const phaseSteps = chapterWorkflowPhaseSteps(phase, steps);
          const phaseDoneCount = phaseSteps.filter((step) => (
            terminalStates.has(workflowStepVisualState(step, taskProgress.stepState, steps))
          )).length;

          return (
            <article className="studio-workflow-phase" key={phase.id}>
              <header>
                <span>{chapterWorkflowPhaseLabel(phase, locale)}</span>
                <small>{phaseSteps.length === 0
                  ? (locale === "zh" ? "本次未涉及" : "Not in this attempt")
                  : `${phaseDoneCount}/${phaseSteps.length}`}</small>
              </header>
              <ol>
                {phaseSteps.map((step) => {
                  const state = workflowStepVisualState(step, taskProgress.stepState, steps);
                  const canOpen = state === "done" || state === "rolled_back";
                  const label = pipelineStepLabel(step, locale);
                  return (
                    <li className={`is-${state}`} key={step.key}>
                      <button
                        aria-label={`${label}${locale === "zh" ? "：" : ": "}${nodeStatusLabel(state, locale)}`}
                        disabled={!canOpen}
                        onClick={() => { void openArtifact(step); }}
                        title={canOpen
                          ? `${label} · ${locale === "zh" ? "点击查看产物" : "View artifacts"}`
                          : label}
                        type="button"
                      >
                        <span aria-hidden="true" className="studio-workflow-node-mark">
                          {nodeStateGlyph(state)}
                        </span>
                        <span className="studio-workflow-node-copy">
                          <strong>{label}</strong>
                          <small>{state === "active" && operationDetail
                            ? operationDetail
                            : nodeStatusLabel(state, locale)}</small>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </article>
          );
        })}
      </div>

      {artifactDialog && (
        <WorkflowArtifactDialog
          artifacts={artifactDialog.artifacts}
          {...(artifactDialog.candidatePaths !== undefined
            ? { candidatePaths: artifactDialog.candidatePaths }
            : {})}
          {...(artifactDialog.emptyHint !== undefined ? { emptyHint: artifactDialog.emptyHint } : {})}
          onClose={() => setArtifactDialog(null)}
          stageLabel={artifactDialog.stageLabel}
        />
      )}
    </section>
  );
}
