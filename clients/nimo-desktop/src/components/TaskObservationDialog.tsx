import { useMemo, useState } from "react";

import type { JobView } from "@nimo/engine-contracts";

import type { TaskStreamState } from "../lib/task-stream";
import { deriveTaskFocusPresentation, type TaskFocusPresentation } from "../lib/task-focus-presentation";
import { taskStreamTranscriptFromState } from "../lib/task-stream-transcript";
import { deriveTaskStreamTrace } from "../lib/task-stream-trace";
import { taskStreamStatusLabel } from "../lib/task-stream-presentation";
import {
  isRawPipelineStepLabel,
  localizePipelineStepAcrossKinds,
} from "./StepIndicatorRow";
import { OverlaySurface } from "./OverlaySurface";
import { OBSERVATION_TABS, STREAM_TAB_LABELS, type ObservationTab } from "./stream-tabs";
import { TaskCallDetails, TaskStreamDetail } from "./TaskStreamDetail";
import { TaskFocusPanel } from "./TaskFocusPanel";
import { formatInitLongStepLabel } from "../lib/task-stream-presentation";
import { useLocale } from "../lib/i18n";
import { useThinkingFoldState } from "../lib/use-thinking-fold-state";
import { computeAnchorOffset, type FloatingStreamAnchor } from "./FloatingStreamDialog";

interface TaskObservationDialogProps {
  /** The reader may be entered from the focus surface or from NIMO's task tray. */
  readonly source?: "focus" | "companion";
  readonly onClose: () => void;
  readonly onOpenFloating?: () => void;
  readonly stream: TaskStreamState | null;
  readonly job?: JobView | null;
  readonly diagnostics?: readonly string[];
  readonly events?: readonly string[];
  readonly phaseIndex?: number;
  readonly onDecision?: (jobId: string, decisionId: string, choice: string, customText: string, approvalVersion?: string) => void;
  /**
   * Optional pet-anchor rect.  When set, the dialog opens adjacent to the
   * pet companion so the user can see both the dialog and the bubble
   * that opened it (mirrors the FloatingStreamDialog anchor contract).
   */
  readonly anchor?: FloatingStreamAnchor | null;
}

function displayObservationStep(rawStep: string, locale: "zh" | "en"): string {
  // Engine-formatted Chinese labels (with batch detail) pass through as-is.
  if (!isRawPipelineStepLabel(rawStep)) return rawStep;
  const localized = localizePipelineStepAcrossKinds(rawStep, locale);
  if (!isRawPipelineStepLabel(localized)) return localized;
  // Init-coherence family (claims / retrieval / adjudication / recheck)
  // shares the engine formatter so every surface shows the same Chinese text.
  const formatted = formatInitLongStepLabel(rawStep);
  if (formatted !== rawStep) return formatted;

  // These can be the newest engine event before the parent workflow snapshot
  // has refreshed.  Keep the diagnostic semantics, but never make a reader
  // parse the implementation key to understand the active operation.
  const internalEventLabels: Record<string, { readonly zh: string; readonly en: string }> = {
    init_claim_entity_adjudication_cache_hit: {
      zh: "实体声明判定 · 已命中缓存",
      en: "Entity Claim Adjudication · Cache Hit",
    },
    init_claim_entity_adjudication_degraded: {
      zh: "实体声明判定 · 降级处理",
      en: "Entity Claim Adjudication · Degraded",
    },
  };
  const internal = internalEventLabels[rawStep];
  if (internal !== undefined) return internal[locale];
  return locale === "zh" ? "任务执行中" : "Task in progress";
}

/**
 * Header toolbar for the focus dialog.  Lives outside the stream body so the
 * fold controls stay at the top edge of the dialog (per `Tab栏操作按钮位置规范`)
 * and never collide with the runtime summary or attention callout.
 */
function TaskObservationHeaderToolbar({
  foldState,
}: {
  readonly foldState: import("../lib/use-thinking-fold-state").ThinkingFoldApi;
}) {
  if (!foldState.hasReasoning) return null;
  return (
    <div aria-label="思考折叠工具" className="task-observation-toolbar" role="group">
      <button
        aria-pressed={foldState.preference === "collapsed"}
        className="button button-secondary"
        onClick={() => foldState.foldAll()}
        type="button"
      >
        全部折叠思考
      </button>
      <button
        aria-pressed={foldState.preference === "expanded"}
        className="button button-secondary"
        onClick={() => foldState.expandAll()}
        type="button"
      >
        全部展开思考
      </button>
    </div>
  );
}

interface TaskObservationDialogBodyProps {
  readonly diagnostics?: readonly string[];
  readonly events?: readonly string[];
  readonly foldState: import("../lib/use-thinking-fold-state").ThinkingFoldApi;
  readonly job: JobView | null;
  readonly onDecision?: (jobId: string, decisionId: string, choice: string, customText: string, approvalVersion?: string) => void;
  readonly phaseIndex?: number;
  readonly step: string;
  readonly stream: TaskStreamState | null;
  readonly tab: ObservationTab;
}

const CURRENT_FACT_NUMBER = new Intl.NumberFormat("zh-CN");

function currentOutputKindLabel(kind: string | undefined): string {
  const normalized = kind?.trim().toLocaleLowerCase();
  if (normalized === "json") return "JSON";
  if (normalized === "json_partial") return "JSON 草稿";
  if (normalized === "report") return "结构化报告";
  if (normalized === "text") return "正文";
  return "待识别";
}

function currentElapsedLabel(elapsedMs: number | undefined): string {
  if (elapsedMs === undefined) return "统计中";
  if (elapsedMs < 1_000) return `${Math.round(elapsedMs)}ms`;
  return `${(elapsedMs / 1_000).toFixed(1)}s`;
}

function currentSnapshotStatusLabel(
  job: JobView | null,
  stream: TaskStreamState | null,
  presentation: TaskFocusPresentation | null,
): string {
  if (presentation?.validationStatus === "validated") {
    if (job?.state === "paused" && (job.decisions?.length ?? 0) > 0) return "已校验 · 待确认";
    return presentation.contentTextTruncated ? "已校验快照" : "已校验";
  }
  if (presentation?.validationStatus === "validating") return "正在校验";
  if (presentation?.validationStatus === "repairing") return "正在修复";
  if (presentation?.validationStatus === "retrying") return "正在重试";
  if (presentation?.contentStatus === "attention") return "需处理";
  if (presentation?.contentStatus === "unverified") {
    return stream?.status === "paused" ? "已暂停 · 待核验" : "待核验";
  }
  if (stream?.status === "completed") return "结果快照";
  if (stream?.status === "paused") return "已暂停";
  if (stream?.status === "failed") return "需处理";
  return "实时更新";
}

function CurrentNodeFacts({ job, step, stream }: Pick<TaskObservationDialogBodyProps, "job" | "step" | "stream">) {
  const presentation = useMemo(
    () => job === null ? null : deriveTaskFocusPresentation(job, stream),
    [job, stream],
  );
  const summary = stream?.summary;
  const latestCall = stream?.calls?.at(-1);
  const provider = summary?.provider ?? latestCall?.provider;
  const model = summary?.model ?? latestCall?.model;
  const route = [provider, model].filter((value) => value !== undefined && value.length > 0).join(" / ");
  const outputCharacters = presentation?.outputCharacterCount ?? summary?.outputCharacters;
  const retainedCharacters = presentation === null ? undefined : [...presentation.contentText].length;
  const totalTokens = summary?.totalTokens;
  const attempt = summary?.attempt ?? latestCall?.attempt;
  const facts = [
    ["当前节点", job?.stepLabel ?? job?.currentStep ?? step],
    ["输出类型", currentOutputKindLabel(presentation?.outputKind ?? summary?.outputKind)],
    ["模型 / 配置", route || "等待路由"],
    ["输出长度", outputCharacters === undefined
      ? "等待首段"
      : presentation?.contentTextTruncated === true && retainedCharacters !== undefined
        ? `${CURRENT_FACT_NUMBER.format(outputCharacters)} 字符（预览 ${CURRENT_FACT_NUMBER.format(retainedCharacters)}）`
        : `${CURRENT_FACT_NUMBER.format(outputCharacters)} 字符`],
    ["本次 Token", totalTokens === undefined ? "统计中" : CURRENT_FACT_NUMBER.format(totalTokens)],
    ["本次耗时", currentElapsedLabel(summary?.elapsedMs)],
    ["当前尝试", attempt === undefined ? "等待调用" : `第 ${attempt} 次`],
  ] as const;

  return (
    <aside aria-label="当前节点信息" className="task-current-facts">
      <header>
        <span>节点信息</span>
        <strong>{currentSnapshotStatusLabel(job, stream, presentation)}</strong>
      </header>
      <dl>
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd title={value}>{value}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

/** One owner per information layer: focus, transcript, or call diagnostics. */
export function TaskObservationDialogBody({ diagnostics, events, foldState, job, onDecision, phaseIndex, step, stream, tab }: TaskObservationDialogBodyProps) {
  if (tab === "stream") {
    return (
      <section className="task-observation-body is-stream-workbench">
        <section aria-label="实时输出" className="task-observation-reader">
          <TaskStreamDetail displayStepLabel={step} foldState={foldState} showSummary={false} stream={stream} />
        </section>
      </section>
    );
  }
  if (tab === "calls") {
    return (
      <section className="task-observation-body is-call-detail">
        <TaskCallDetails displayStepLabel={step} showContext={false} stream={stream} />
      </section>
    );
  }
  return (
    <section className="task-observation-body is-current-overview">
      <div className="task-current-workbench">
        <TaskFocusPanel
          {...(diagnostics === undefined ? {} : { diagnostics })}
          {...(events === undefined ? {} : { events })}
          job={job}
          {...(onDecision === undefined ? {} : { onDecision })}
          {...(phaseIndex === undefined ? {} : { phaseIndex })}
          prominent
          scope="global"
          showIdentity={false}
          stream={stream}
          title="任务当前节点"
        />
        <CurrentNodeFacts job={job} step={step} stream={stream} />
      </div>
    </section>
  );
}

/**
 * Full task observation workspace. Focus entry points open on the current
 * node; companion entry points open on the transcript. Each tab owns one
 * information layer so task identity, prose, and call diagnostics do not
 * compete on the same screen.
 */
export function TaskObservationDialog({ anchor = null, diagnostics, events, job = null, onClose, onDecision, onOpenFloating, phaseIndex, source = "focus", stream }: TaskObservationDialogProps) {
  const [tab, setTab] = useState<ObservationTab>(source === "focus" ? "current" : "stream");
  const locale = useLocale();
  const title = job?.label || stream?.title || "正在连接任务";
  const rawStep = job?.stepLabel || stream?.stepLabel || job?.currentStep || "";
  const step = rawStep.length > 0
    ? displayObservationStep(rawStep, locale)
    : locale === "zh" ? "正在读取流式事件" : "Reading task events";
  const transcript = useMemo(() => taskStreamTranscriptFromState(stream), [stream]);
  // Reasoning keys are projected from the same transcript the body uses so
  // the header toolbar and the per-segment details stay in lockstep.
  const reasoningKeys = useMemo(
    () => transcript.items.filter((item) => item.kind === "reasoning")
      .map((item) => item.key),
    [transcript.items],
  );
  const foldState = useThinkingFoldState(stream?.taskId, reasoningKeys);
  const batchedTrace = useMemo(() => deriveTaskStreamTrace(stream).nodes.length > 1, [stream]);
  const isCompanion = source === "companion";
  // Anchor offset is computed once on mount so the dialog appears next to
  // the pet even if the user has never dragged it.  Without an anchor the
  // dialog centers itself via the OverlaySurface defaults.
  const anchorOffset = useMemo(
    () => (anchor === null ? { x: 0, y: 0 } : computeAnchorOffset(anchor, 0, 0)),
    [anchor],
  );

  return (
    <OverlaySurface
      ariaLabel={isCompanion ? "NIMO 任务流" : "专注任务详情"}
      className={`task-observation-overlay is-${source}-stream is-${tab}-tab${anchor !== null ? " is-anchored" : ""}`}
      onClose={onClose}
      {...(anchor === null
        ? {}
        : { style: { transform: `translate(${anchorOffset.x}px, ${anchorOffset.y}px)` } })}
    >
      <header className="task-observation-header">
        <div className="task-observation-title-block">
          <h2>{title}</h2>
          <p>{step}</p>
        </div>
        {tab === "stream" && !batchedTrace && <TaskObservationHeaderToolbar foldState={foldState} />}
        <span
          aria-live="polite"
          className={`task-observation-status is-${stream?.status ?? "streaming"}`}
          role="status"
        >{stream === null ? "连接中" : taskStreamStatusLabel(stream.status, stream.jobState)}</span>
        <button aria-label="关闭任务详情" className="app-dialog-close" onClick={onClose} type="button">×</button>
      </header>
      <div className="task-observation-tabs" role="tablist" aria-label="任务详情分类">
        {OBSERVATION_TABS.map((item) => <button aria-selected={tab === item} className={tab === item ? "is-active" : ""} key={item} onClick={() => setTab(item)} role="tab" type="button">{STREAM_TAB_LABELS[item]}</button>)}
      </div>
      <TaskObservationDialogBody
        {...(diagnostics === undefined ? {} : { diagnostics })}
        {...(events === undefined ? {} : { events })}
        foldState={foldState}
        job={job}
        {...(onDecision === undefined ? {} : { onDecision })}
        {...(phaseIndex === undefined ? {} : { phaseIndex })}
        step={step}
        stream={stream}
        tab={tab}
      />
      <footer className="task-observation-footer">
        {onOpenFloating !== undefined && <button className="button button-secondary" onClick={onOpenFloating} type="button">在浮动窗口打开</button>}
        <button className="button button-secondary" onClick={onClose} type="button">关闭</button>
      </footer>
    </OverlaySurface>
  );
}
