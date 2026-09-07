import { useEffect, useRef, useState } from "react";

import type { WorkflowRunView } from "@nimo/engine-contracts";

import {
  StepIndicatorRow,
  taskProgressProjection,
  type PipelineStep,
} from "./StepIndicatorRow";
import { RunInsightStrip } from "./RunInsightStrip";
import { formatElapsedClock } from "../hooks/useElapsedClock";
import { CHAPTER_CHECKPOINT_KINDS } from "../lib/workflow-run-session";
import { workflowRunState } from "../lib/workflow-run-state";
import { useLocale } from "../lib/i18n";

/**
 * Job card action bar mirroring `JobCard._build_action_bar`
 * (desktop/pages/workflow/jobs.py):
 * - RUNNING/QUEUED → 「⏹ 停止」(secondary, compact)
 * - PAUSED → 「▶ 恢复」(primary, compact)
 * - FAILED + initRepairAvailable → 「AI修复」(primary) + 「人工修复」(secondary)
 * - FAILED + chapter kind + hasCheckpoint → 「🔄 断点续写」(primary)
 * - otherwise no action bar (returns None in PySide6)
 *
 * Stage line reuses the shared `StepIndicatorRow` + `stepIndicatorStateFromStages`
 * derivation, so card steps and form-level step indicators stay in sync;
 * clicking a completed step opens its artifacts via `onOpenArtifact`.
 *
 * Elapsed time uses the page-level shared clock (`tick` from
 * `useElapsedClock`) instead of a per-card setInterval.
 */
export function WorkflowRunCard({
  onInitRepair,
  onManualRepair,
  onOpenArtifact,
  onRunAction,
  run,
  tick,
}: {
  readonly run: WorkflowRunView;
  readonly onRunAction: () => void;
  readonly onInitRepair: () => void;
  readonly onManualRepair: () => void;
  readonly onOpenArtifact: (step: PipelineStep) => void;
  /** Shared elapsed clock tick from the page; 0 when no card is running. */
  readonly tick: number;
}) {
  const locale = useLocale();
  const state = workflowRunState(run);
  const isRunning = state === "running" || state === "queued";
  const isPaused = state === "paused";
  const isCancelled = run.isCancelled ?? (
    run.stateLabel === "已取消" || run.stateLabel.toLocaleLowerCase() === "cancelled"
  );
  const isFailed = state === "failed";
  const qualityBlocked = run.qualityStatus === "blocked";
  const stateLabels = locale === "zh"
    ? { queued: "排队中", running: "执行中", paused: "已暂停", succeeded: "已完成", failed: "失败", unknown: run.stateLabel }
    : { queued: "Queued", running: "Running", paused: "Paused", succeeded: "Completed", failed: "Failed", unknown: run.stateLabel };
  const stateLabel = isCancelled
    ? (locale === "zh" ? "已取消" : "Cancelled")
    : qualityBlocked && state === "succeeded"
      ? (locale === "zh" ? "执行完成 · 质量待处理" : "Finished · quality needs attention")
      : stateLabels[state];
  const engineActions = run.recoveryActions ?? [];
  const enabledActionKinds = new Set(
    engineActions.filter((action) => action.enabled).map((action) => action.kind),
  );
  const showInitRepair = isFailed && (
    enabledActionKinds.has("retry_init_repair") || (
      engineActions.length === 0 && run.initRepairAvailable
    )
  );
  const showManualRepair = isFailed && (
    enabledActionKinds.has("manual_init_repair") || (
      engineActions.length === 0 && run.initRepairAvailable
    )
  );
  const showCheckpointResume =
    isFailed && (
      enabledActionKinds.has("resume_checkpoint") || (
        engineActions.length === 0 &&
        CHAPTER_CHECKPOINT_KINDS.has(run.kind) &&
        run.hasCheckpoint
      )
    );
  const showInitResume = isPaused && run.kind === "init_long";
  const hasActions =
    isRunning || isPaused || showInitResume || showInitRepair || showManualRepair || showCheckpointResume;
  const isHistorical = run.historical ?? false;

  // ── Step indicator: single shared contract with the forms ──────────
  const progress = taskProgressProjection({
    currentStepLabel: run.currentStageLabel,
    isRunning,
    kind: run.kind,
    locale,
    reportedProgress: run.progressPercent,
    stages: run.stages ?? [],
  });
  const { currentStepLabel: currentStageLabel, progressPercent, stepState, steps } = progress;

  // ── Elapsed display via shared clock (mirrors PySide6 _tick_elapsed) ─
  const createdAtRef = useRef(run.createdAt);
  useEffect(() => {
    createdAtRef.current = run.createdAt;
  }, [run.createdAt]);
  const elapsedText =
    isRunning && run.createdAt && tick > 0
      ? formatElapsedClock(new Date(createdAtRef.current!).getTime(), tick)
      : run.elapsedLabel;
  const [hadRunning, setHadRunning] = useState(isRunning);
  useEffect(() => {
    if (isRunning) setHadRunning(true);
  }, [isRunning]);
  const visibleElapsed = isRunning || hadRunning ? elapsedText : run.elapsedLabel;
  const displayTitle = run.projectLabel.trim() || run.title;
  const projectSubtitle = run.projectLabel.trim() === displayTitle ? "" : `${run.projectLabel} · `;
  const visibleElapsedLabel = visibleElapsed.startsWith("已运行")
    ? visibleElapsed
    : `已运行 ${visibleElapsed}`;

  // ── Detail lines (mirrors PySide6 detail_lines) ────────────────────
  const detailLines = run.detailLines ?? [];
  const fallbackDetails: { text: string; tone: string }[] = [];
  if (detailLines.length === 0) {
    // Engine-formatted Chinese step label (batch x/y, verdict…) is the
    // most granular feedback surface; the stage label remains the fallback.
    const detailSource = run.stepLabel ?? currentStageLabel;
    if (detailSource) {
      fallbackDetails.push({
        text: `${locale === "zh" ? "步骤" : "Step"}: ${detailSource}`,
        tone: "default",
      });
    }
    if (run.validationLabel) fallbackDetails.push({ text: run.validationLabel, tone: "muted" });
    if (run.activityLabel) fallbackDetails.push({ text: run.activityLabel, tone: "default" });
    if (run.degradationReason) {
      fallbackDetails.push({ text: `质量说明：${run.degradationReason}`, tone: "warning" });
    }
    if ((run.staleDependencies?.length ?? 0) > 0) {
      fallbackDetails.push({
        text: `待处理依赖：${run.staleDependencies!.join("；")}`,
        tone: "danger",
      });
    }
    if (run.checkpoint?.exists) {
      fallbackDetails.push({
        text: `引擎断点：${run.checkpoint.completedStage || "已保存"}`,
        tone: "success",
      });
    }
    const lineageParts = [
      run.workflowVersion && run.workflowVersion !== "legacy_unknown" ? run.workflowVersion : "",
      (run.outputVersion ?? 0) > 0 ? `产物 v${run.outputVersion}` : "",
      run.inputSignature && run.inputSignature !== "legacy_unknown"
        ? `签名 ${run.inputSignature.slice(0, 10)}`
        : "",
      run.templateVersion && run.templateVersion !== "legacy_unknown"
        ? `模板 ${run.templateVersion}`
        : "",
      run.configFingerprint && run.configFingerprint !== "legacy_unknown"
        ? `配置 ${run.configFingerprint.slice(0, 10)}`
        : "",
      run.modelFingerprint && run.modelFingerprint !== "legacy_unknown"
        ? `模型 ${run.modelFingerprint.slice(0, 10)}`
        : "",
      (run.cumulativeTokens ?? 0) > 0 ? `${run.cumulativeTokens!.toLocaleString()} tokens` : "",
      (run.cumulativeCostUsd ?? 0) > 0 ? `$${run.cumulativeCostUsd!.toFixed(4)}` : "",
    ].filter(Boolean);
    if (lineageParts.length > 0) {
      fallbackDetails.push({ text: lineageParts.join(" · "), tone: "hint" });
    }
  }
  const visibleDetails = detailLines.length > 0
    ? (isHistorical ? detailLines.slice(0, 2) : detailLines)
    : fallbackDetails;

  // ── Badges (mirrors PySide6 _job_badge_spec + _replan_reason_badge_specs) ──
  const badges = run.badges ?? [];
  const qualityLabels: Readonly<Record<string, string>> = locale === "zh"
    ? { blocked: "未通过", degraded: "已降级", fallback: "使用兜底" }
    : { blocked: "Blocked", degraded: "Degraded", fallback: "Fallback" };
  const lineageBadges = [
    ...(run.qualityStatus && run.qualityStatus !== "actual" && run.qualityStatus !== "legacy_unknown"
      ? [{ label: `质量：${qualityLabels[run.qualityStatus] ?? run.qualityStatus}`, tone: qualityBlocked ? "danger" : "warning" } as const]
      : []),
    ...(run.derivationStatus && run.derivationStatus !== "fresh" && run.derivationStatus !== "legacy_unknown"
      ? [{ label: `血缘：${run.derivationStatus}`, tone: "danger" } as const]
      : []),
  ];

  return (
    <article className={`workflow-run-card ${isHistorical ? "is-historical" : ""} ${isCancelled ? "is-cancelled" : ""}`}>
      <div className="workflow-run-heading">
        <div>
          <h3>{displayTitle}</h3>
          <p>
            {projectSubtitle}{isRunning ? visibleElapsedLabel : visibleElapsed}
          </p>
        </div>
        <div className="workflow-run-badges">
          {badges.map((badge, i) => (
            <span className={`workflow-badge is-${badge.tone}`} key={i} title={badge.tooltip}>
              {badge.label}
            </span>
          ))}
          {lineageBadges.map((badge) => (
            <span className={`workflow-badge is-${badge.tone}`} key={badge.label}>
              {badge.label}
            </span>
          ))}
          <span
            className={`workflow-run-state is-${
              isRunning
                ? "running"
                : isFailed
                  ? isCancelled
                    ? "cancelled"
                    : "failed"
                  : isPaused || qualityBlocked
                    ? "paused"
                    : "completed"
            }`}
          >
            {stateLabel}
          </span>
        </div>
      </div>
      <div className="workflow-progress-row">
        <div aria-label={`任务进度 ${progressPercent}%`} className="workflow-progress-line">
          <span style={{ width: `${progressPercent}%` }} />
        </div>
        <strong className="workflow-progress-percent">{progressPercent}%</strong>
      </div>
      {!isHistorical && (run.runInsights?.length ?? 0) > 0 ? (
        <RunInsightStrip
          {...(run.efficiency !== undefined ? { efficiency: run.efficiency } : {})}
          insights={run.runInsights ?? []}
          onOpenArtifact={(stepKey) => {
            const artifactStep = steps.find((step) => step.key === stepKey);
            if (artifactStep !== undefined) onOpenArtifact(artifactStep);
          }}
        />
      ) : null}
      {!isHistorical && steps.length > 0 && (
        <div className="workflow-stage-steps">
          <StepIndicatorRow
            {...stepState}
            blockedStepKey={stepState.blockedStepKey}
            completedSteps={stepState.completedSteps}
            currentStepKey={stepState.currentStepKey}
            failedStepKey={stepState.failedStepKey}
            isComplete={stepState.isComplete}
            onStepClick={(_, step) => onOpenArtifact(step)}
            rolledBackSteps={stepState.rolledBackSteps}
            skippedSteps={stepState.skippedSteps}
            steps={steps}
          />
        </div>
      )}
      <div className="workflow-run-footer">
        <div className="workflow-run-details">
          {visibleDetails.map((line, i) => (
            <p className={`workflow-detail-line is-${line.tone}`} key={i}>{line.text}</p>
          ))}
        </div>
        {hasActions && (
          <div className="workflow-run-actions">
            {isRunning && (
              <button
                className="button button-secondary is-compact"
                onClick={onRunAction}
                type="button"
              >
                ⏹ 停止
              </button>
            )}
            {isPaused && (
              <button
                className="button button-primary is-compact"
                onClick={onRunAction}
                type="button"
              >
                {showInitResume ? "继续立项 →" : "▶ 恢复"}
              </button>
            )}
            {(showInitRepair || showManualRepair) && (
              <>
                {showInitRepair && (
                  <button
                    className="button button-primary is-compact"
                    onClick={onInitRepair}
                    title="复用可验证的已落盘产物，重新提交自动修复与初始化复审。"
                    type="button"
                  >
                    AI修复
                  </button>
                )}
                {showManualRepair && (
                  <button
                    className="button button-secondary is-compact"
                    onClick={onManualRepair}
                    title="打开矛盾点与修复建议，手动编辑关联产物后再复审。"
                    type="button"
                  >
                    人工修复
                  </button>
                )}
              </>
            )}
            {showCheckpointResume && (
              <button
                className="button button-primary is-compact"
                onClick={onRunAction}
                type="button"
              >
                🔄 断点续写
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
