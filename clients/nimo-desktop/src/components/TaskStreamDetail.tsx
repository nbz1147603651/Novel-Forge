import { useMemo, useState } from "react";

import type { TaskModelCallStatus, TaskModelCallView } from "@nimo/engine-contracts";

import { deriveTaskCallPresentation, type TaskCallGroup } from "../lib/task-call-presentation";
import type { TaskStreamState } from "../lib/task-stream";
import { buildTaskStreamTranscript } from "../lib/task-stream-transcript";
import { deriveTaskStreamTrace } from "../lib/task-stream-trace";
import { displayStepLabel as resolveDisplayStepLabel, taskStreamNotice } from "../lib/task-stream-presentation";
import { useStreamFollow } from "../lib/use-stream-follow";
import { useThinkingFoldState, type ThinkingFoldApi } from "../lib/use-thinking-fold-state";
import { StreamContent } from "./StreamContent";
import { TaskStreamTraceBrowser } from "./TaskStreamTraceBrowser";

interface TaskStreamDetailProps {
  /** User-facing stage label projected by the owning task surface. */
  readonly displayStepLabel?: string;
  readonly showToolbar?: boolean;
  /** Hide the metadata strip when the owning dialog header already presents it. */
  readonly showSummary?: boolean;
  readonly stream: TaskStreamState | null;
  /**
   * Optional externally-owned fold/expand controller.  When provided the
   * detail body still renders each segment but the toolbar buttons call
   * the supplied callbacks so the focus module and the floating companion
   * can mount the same controls in their own header rows.
   */
  readonly foldState?: ThinkingFoldApi;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatElapsed(elapsedMs: number): string {
  if (elapsedMs < 60_000) return `${(elapsedMs / 1_000).toFixed(1)}s`;
  const minutes = Math.floor(elapsedMs / 60_000);
  const seconds = Math.round((elapsedMs % 60_000) / 1_000);
  return `${minutes}m ${seconds}s`;
}

function formatCost(costUsd: number): string {
  return `$${costUsd.toFixed(4)}`;
}

const CALL_STATUS_LABEL: Readonly<Record<TaskModelCallStatus, string>> = {
  running: "调用中",
  success: "成功",
  retrying: "重试中",
  error: "失败",
};

function formatCallTime(value: string | undefined): string {
  if (value === undefined || value.length === 0) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function StreamRuntimeSummary({
  displayStepLabel,
  stream,
}: {
  readonly displayStepLabel?: string;
  readonly stream: TaskStreamState;
}) {
  const summary = stream.summary;
  const details = [
    summary?.outputKind,
    summary?.attempt === undefined ? undefined : `第 ${summary.attempt} 轮`,
    summary?.outputCharacters === undefined ? undefined : `${formatNumber(summary.outputCharacters)} 字`,
    summary?.elapsedMs === undefined ? undefined : formatElapsed(summary.elapsedMs),
    summary?.model,
    summary?.totalTokens === undefined ? undefined : `Token ${formatNumber(summary.totalTokens)}`,
    summary?.promptTokens === undefined || summary?.completionTokens === undefined
      ? undefined
      : `输入 ${formatNumber(summary.promptTokens)} / 输出 ${formatNumber(summary.completionTokens)}`,
    summary?.costUsd === undefined ? undefined : formatCost(summary.costUsd),
  ].filter((value): value is string => value !== undefined);

  return (
    <section aria-label="本次输出概况" className="task-stream-runtime-summary">
      <div className="task-stream-runtime-primary">
        <span>实时输出</span>
        <strong title={displayStepLabel ?? stream.stepLabel}>{displayStepLabel ?? stream.stepLabel}</strong>
      </div>
      {details.length > 0 && (
        <div className="task-stream-runtime-facts">
          {details.map((detail) => <span key={detail}>{detail}</span>)}
        </div>
      )}
    </section>
  );
}

/**
 * Shared, ordered stream renderer. Both the embedded task observer and the
 * detached workbench consume this component so a replay cannot render a
 * different event order or thought-folding policy in each surface.
 *
 * Enhancements over Phase-1 baseline:
 * - Content type detection (TEXT/JSON/JSON_PARTIAL/REPORT) with rich rendering
 * - Smart auto-scroll via IntersectionObserver (mirrors StreamFollowController)
 * - Reasoning blocks with max-height + internal scroll + overscroll containment
 * - CSS content-visibility for native virtualization of off-screen events
 * - "Back to bottom" floating button when the reader scrolls up
 */
export function TaskStreamDetail({ displayStepLabel, foldState, showSummary = true, showToolbar = false, stream }: TaskStreamDetailProps) {
  // Engine labels are already Chinese; when the owning surface does not pass
  // one, resolve a display label from the stream (raw keys fall back to the
  // init-coherence formatter).
  const effectiveStepLabel = displayStepLabel
    ?? (stream === null ? undefined : resolveDisplayStepLabel(stream));
  const renderWindowSize = 500;
  const visibleEvents = useMemo(
    () => (stream?.events ?? []).filter((event) => event.segment !== "system" || event.kind === "stream_error"),
    [stream?.events],
  );
  const hiddenEventCount = Math.max(0, visibleEvents.length - renderWindowSize);
  const renderedEvents = useMemo(
    () => hiddenEventCount === 0 ? visibleEvents : visibleEvents.slice(hiddenEventCount),
    [hiddenEventCount, visibleEvents],
  );
  const transcript = useMemo(() => buildTaskStreamTranscript(renderedEvents), [renderedEvents]);
  const tracePresentation = useMemo(() => deriveTaskStreamTrace(stream), [stream]);
  const usesTraceBrowser = tracePresentation.nodes.length > 1
    || tracePresentation.nodes.some((node) => node.structured);
  const settledStreamIds = useMemo(() => {
    const ids = new Set<string>();
    // Completion markers are system events and intentionally absent from the
    // reader transcript. Inspect the source event list so a finished JSON
    // sibling can render immediately while the overall concurrent task runs.
    for (const event of stream?.events ?? []) {
      if (event.kind === "stream_end" || event.kind === "stream_error") ids.add(event.streamId);
    }
    return ids;
  }, [stream?.events]);
  const reasoningKeys = useMemo(
    () => transcript.items.filter((item) => item.kind === "reasoning").map((item) => item.key),
    [transcript.items],
  );
  // Each reader surface mounts its own fold controller; the parent dialog
  // (focus / floating) can pass a pre-built controller in `foldState` to keep
  // its header toolbar and the body in lockstep.
  const internalFold = useThinkingFoldState(stream?.taskId, reasoningKeys);
  const fold = foldState ?? internalFold;
  const setExpanded = (identity: string, open: boolean) => fold.toggle(identity, open);

  const latestItem = transcript.items.at(-1);
  const followKey = latestItem === undefined
    ? `${stream?.taskId ?? "connecting"}:empty`
    : `${latestItem.key}:${latestItem.characterCount}:${latestItem.eventCount}`;
  const {
    containerRef,
    sentinelRef,
    followingLatest,
    pauseFollow,
    scrollToLatest,
  } = useStreamFollow(followKey, `${stream?.taskId ?? "connecting"}:reader`);

  if (stream === null) {
    return <p className="task-stream-empty">正在连接任务流。</p>;
  }

  const attention = stream.status === "failed" || stream.status === "paused"
    ? taskStreamNotice(stream.status)
    : null;
  const showReaderHeader = showSummary || (showToolbar && reasoningKeys.length > 0 && !usesTraceBrowser) || attention !== null;

  return (
    <div className={`task-stream-detail${showReaderHeader ? " has-reader-header" : ""}`}>
      {showReaderHeader && <header className="task-stream-reader-header">
        {showSummary && <StreamRuntimeSummary
          {...(effectiveStepLabel === undefined ? {} : { displayStepLabel: effectiveStepLabel })}
          stream={stream}
        />}
        {showToolbar && reasoningKeys.length > 0 && !usesTraceBrowser && (
          <div className="task-stream-toolbar">
            <button
              aria-pressed={fold.preference === "collapsed"}
              className="button button-secondary"
              onClick={() => fold.foldAll()}
              type="button"
            >
              全部折叠思考
            </button>
            <button
              aria-pressed={fold.preference === "expanded"}
              className="button button-secondary"
              onClick={() => fold.expandAll()}
              type="button"
            >
              全部展开思考
            </button>
          </div>
        )}
        {attention !== null && <p className="task-stream-attention">{attention}</p>}
      </header>}
      {usesTraceBrowser ? (
        <TaskStreamTraceBrowser
          presentation={tracePresentation}
          stepLabel={effectiveStepLabel ?? stream.stepLabel}
          stream={stream}
        />
      ) : <div aria-live="off" className="task-stream-list" ref={containerRef}>
        {hiddenEventCount > 0 && <p className="task-stream-window-notice">为保持流畅，已折叠前 {hiddenEventCount} 条原始事件；完整记录可在“原始事件”查看。</p>}
        {transcript.items.length === 0 ? (
          <p className="task-stream-empty">正在等待第一段输出。</p>
        ) : transcript.items.map((item) => {
          if (item.kind === "reasoning") {
            const isOpen = fold.isOpen(item.key);
            return (
              <details
                aria-expanded={isOpen}
                className="task-stream-reasoning"
                key={item.key}
                onToggle={(toggleEvent) => setExpanded(item.key, toggleEvent.currentTarget.open)}
                open={isOpen}
              >
                <summary>思考 · {item.characterCount} 字 · {item.eventCount} 个片段</summary>
                <div className="task-stream-reasoning-body"><p>{item.text}</p></div>
              </details>
            );
          }
          if (item.kind === "error") {
            return <p className="task-stream-error" key={item.key} role="alert">{item.text}</p>;
          }
          return (
            <article aria-label="正文输出" className="task-stream-event is-content" key={item.key}>
              <StreamContent
                {...(item.outputKind === undefined && stream.summary?.outputKind === undefined
                  ? {}
                  : { outputKind: item.outputKind ?? stream.summary?.outputKind })}
                live={stream.status === "streaming" && item.key === latestItem?.key && !settledStreamIds.has(item.streamId)}
                settled={settledStreamIds.has(item.streamId) || stream.status === "completed"}
                text={item.text}
              />
            </article>
          );
        })}
        <div aria-hidden="true" ref={sentinelRef} className="task-stream-sentinel" />
      </div>}
      {!usesTraceBrowser && stream.status === "streaming" && (
        <button
          aria-label={followingLatest ? "暂停自动跟随" : "继续跟随最新输出"}
          aria-pressed={followingLatest}
          className={`task-stream-follow-toggle${followingLatest ? " is-following" : ""}`}
          onClick={followingLatest ? pauseFollow : scrollToLatest}
          type="button"
        >
          <span aria-hidden="true" />
          {followingLatest ? "自动跟随" : "继续跟随"}
        </button>
      )}
    </div>
  );
}

function CallStatus({ status }: { readonly status: TaskModelCallStatus }) {
  return <span className={`task-call-status is-${status}`}>{CALL_STATUS_LABEL[status]}</span>;
}

function TaskCallStepList({
  activeGroup,
  groups,
  maxTokens,
  onSelect,
}: {
  readonly activeGroup: TaskCallGroup;
  readonly groups: readonly TaskCallGroup[];
  readonly maxTokens: number;
  readonly onSelect: (id: string) => void;
}) {
  return (
    <section aria-label="步骤调用列表" className="task-call-step-list">
      <header><strong>步骤调用</strong><span>{groups.length} 个节点</span></header>
      <div className="task-call-step-rows">
        {groups.map((group, index) => {
          const ratio = maxTokens <= 0 ? 0 : Math.max(0.025, group.totalTokens / maxTokens);
          return (
            <button
              aria-pressed={activeGroup.id === group.id}
              className={activeGroup.id === group.id ? "is-active" : ""}
              key={group.id}
              onClick={() => onSelect(group.id)}
              type="button"
            >
              <span className="task-call-step-index">{String(index + 1).padStart(2, "0")}</span>
              <span className="task-call-step-copy">
                <strong>{group.label}</strong>
                <small>{group.providerModels.join("、") || "等待路由"} · {group.calls.length} 次</small>
                <span className="task-call-step-bar"><i style={{ width: `${ratio * 100}%` }} /></span>
              </span>
              <span className="task-call-step-value">
                <strong>{group.totalTokens > 0 ? formatNumber(group.totalTokens) : "—"}</strong>
                <small>Token</small>
              </span>
              <CallStatus status={group.status} />
            </button>
          );
        })}
      </div>
    </section>
  );
}

function TaskCallInspector({ group }: { readonly group: TaskCallGroup }) {
  const tokenBase = group.promptTokens + group.completionTokens;
  const promptRatio = tokenBase <= 0 ? 0 : group.promptTokens / tokenBase;
  const completionRatio = tokenBase <= 0 ? 0 : group.completionTokens / tokenBase;

  return (
    <section aria-label={`${group.label}调用明细`} className="task-call-inspector">
      <header className="task-call-inspector-header">
        <div><span>节点明细</span><h3>{group.label}</h3></div>
        <CallStatus status={group.status} />
      </header>

      <section aria-label="Token 构成" className="task-call-token-mix">
        <header><strong>Token 构成</strong><span>{group.totalTokens > 0 ? `${formatNumber(group.totalTokens)} 总计` : "等待用量回填"}</span></header>
        <div className="task-call-token-track">
          {tokenBase > 0 ? (
            <>
              <i className="is-prompt" style={{ width: `${promptRatio * 100}%` }} />
              <i className="is-completion" style={{ width: `${completionRatio * 100}%` }} />
            </>
          ) : group.totalTokens > 0
            ? <i className="is-unclassified" />
            : <i className="is-pending" />}
        </div>
        <div className="task-call-token-legend">
          <span><i className="is-prompt" />输入 <strong>{group.promptTokens > 0 ? formatNumber(group.promptTokens) : "—"}</strong></span>
          <span><i className="is-completion" />输出 <strong>{group.completionTokens > 0 ? formatNumber(group.completionTokens) : "—"}</strong></span>
        </div>
      </section>

      <dl className="task-call-node-metrics">
        <div><dt>调用</dt><dd>{group.calls.length} 次</dd></div>
        <div><dt>累计耗时</dt><dd>{group.latencyMs > 0 ? formatElapsed(group.latencyMs) : "统计中"}</dd></div>
        <div><dt>累计费用</dt><dd>{group.costUsd > 0 ? formatCost(group.costUsd) : "待回填"}</dd></div>
      </dl>

      <div className="task-call-timeline">
        {group.calls.map((call, index) => <TaskCallTimelineRow call={call} index={index} key={call.callId} />)}
      </div>
    </section>
  );
}

function TaskCallTimelineRow({ call, index }: { readonly call: TaskModelCallView; readonly index: number }) {
  const attempt = call.attempt === undefined
    ? ""
    : `第 ${call.attempt}${call.maxAttempts === undefined ? "" : ` / ${call.maxAttempts}`} 次`;
  const metrics = [
    call.totalTokens === undefined || call.totalTokens <= 0 ? "" : `${formatNumber(call.totalTokens)} Token`,
    call.latencyMs === undefined || call.latencyMs <= 0 ? "" : formatElapsed(call.latencyMs),
    call.costUsd === undefined || call.costUsd <= 0 ? "" : formatCost(call.costUsd),
  ].filter(Boolean);
  return (
    <article className={`task-call-timeline-row is-${call.status}`}>
      <span className="task-call-timeline-dot">{index + 1}</span>
      <div className="task-call-timeline-copy">
        <header>
          <strong>{[call.provider, call.model].filter(Boolean).join(" / ") || "等待模型响应"}</strong>
          <CallStatus status={call.status} />
        </header>
        <p>{[call.route, attempt, formatCallTime(call.finishedAt ?? call.startedAt)].filter(Boolean).join(" · ") || "调用信息正在写入"}</p>
        {metrics.length > 0 && <small>{metrics.join(" · ")}</small>}
      </div>
    </article>
  );
}

/** Per-node model-call ledger inspired by the PySide TaskModelCallPanel. */
export function TaskCallDetails({
  compact = false,
  displayStepLabel,
  showContext = true,
  stream,
}: {
  readonly compact?: boolean;
  readonly displayStepLabel?: string;
  readonly showContext?: boolean;
  readonly stream: TaskStreamState | null;
}) {
  const presentation = useMemo(
    () => deriveTaskCallPresentation(
      stream === null || displayStepLabel === undefined
        ? stream
        : { ...stream, stepLabel: displayStepLabel },
    ),
    [displayStepLabel, stream],
  );
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const activeGroup = presentation.groups.find((group) => group.id === selectedGroupId)
    ?? presentation.groups.at(-1);
  const attention = stream !== null && (stream.status === "failed" || stream.status === "paused")
    ? taskStreamNotice(stream.status)
    : null;

  return (
    <div className={`task-call-detail${compact ? " is-compact" : ""}${attention !== null ? " has-notice" : ""}`}>
      <header className="task-call-overview">
        <div>
          <span>模型调用全景</span>
          <strong>{presentation.calls.length > 0 ? `${presentation.calls.length} 次调用` : "等待首个调用"}</strong>
          {showContext && <p>{displayStepLabel ?? stream?.stepLabel ?? "正在连接任务"}</p>}
        </div>
        <dl>
          <div><dt>总 Token</dt><dd>{presentation.hasTokenData ? formatNumber(presentation.totalTokens) : "统计中"}</dd></div>
          <div><dt>累计耗时</dt><dd>{presentation.hasLatencyData ? formatElapsed(presentation.latencyMs) : "统计中"}</dd></div>
          <div><dt>费用</dt><dd>{presentation.hasCostData ? formatCost(presentation.costUsd) : "待回填"}</dd></div>
          <div><dt>重试 / 失败</dt><dd>{presentation.retryCount} / {presentation.failedCount}</dd></div>
        </dl>
      </header>
      {attention !== null && <p className="task-call-notice">{attention}</p>}
      {activeGroup === undefined ? <p className="task-call-empty">模型调用开始后，将按工作节点显示 Token、耗时、费用与重试情况。</p> : (
        <div className="task-call-workbench">
          <TaskCallStepList
            activeGroup={activeGroup}
            groups={presentation.groups}
            maxTokens={presentation.maxGroupTokens}
            onSelect={setSelectedGroupId}
          />
          <TaskCallInspector group={activeGroup} />
        </div>
      )}
    </div>
  );
}
