/**
 * Embedded current-task observation panel.
 *
 * Mirrors PySide6 `TaskFocusPanel` (focus_panel.py, 733 lines) as a
 * React component with three zones:
 *   1. Status header (label + progress bar + state badge)
 *   2. Stream preview (latest content snippet with fade mask)
 *   3. Action area (decision buttons / diagnostics fold)
 *
 * Supports `scope` (global/workflow/chapter), `compact`, and `prominent`
 * display modes. Data flows exclusively through EngineClient contracts.
 */
import { useMemo, useState, type KeyboardEvent } from "react";

import type { JobView } from "@nimo/engine-contracts";

import { deriveTaskFocusPresentation } from "../lib/task-focus-presentation";
import type { TaskStreamState } from "../lib/task-stream";
import { useStreamFollow } from "../lib/use-stream-follow";
import { StreamContent } from "./StreamContent";

export type TaskFocusScope = "global" | "workflow" | "chapter";

/** 6-phase chapter generation labels (mirrors phase_progress.py _PHASE_LABELS). */
const PHASE_LABELS = ["规划", "生成", "审查", "润色", "人性化", "收尾"] as const;

interface TaskFocusPanelProps {
  readonly scope?: TaskFocusScope;
  readonly compact?: boolean;
  readonly prominent?: boolean;
  readonly title?: string;
  readonly job: JobView | null;
  readonly stream: TaskStreamState | null;
  readonly onDecision?: (jobId: string, decisionId: string, choice: string, customText: string, approvalVersion?: string) => void;
  readonly onExpand?: () => void;
  /** Repair diagnostics lines (mirrors focus_panel.py _diagnostics_text). */
  readonly diagnostics?: readonly string[];
  /** Key event lines (mirrors focus_panel.py _events_text). */
  readonly events?: readonly string[];
  /** Active phase index 0-5 for chapter jobs (mirrors PhaseProgressBar). */
  readonly phaseIndex?: number;
  /** The owning dialog can provide task identity and status once in its header. */
  readonly showIdentity?: boolean;
}

const STATE_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  paused: "已暂停",
  succeeded: "已完成",
  failed: "已失败",
};

function stateTone(state: string): string {
  if (state === "running") return "is-running";
  if (state === "succeeded") return "is-succeeded";
  if (state === "failed") return "is-failed";
  if (state === "paused") return "is-paused";
  return "is-queued";
}

function FocusArrowIcon() {
  return (
    <svg aria-hidden="true" fill="none" height="14" viewBox="0 0 16 16" width="14">
      <path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
    </svg>
  );
}

function QuietFocusState({ scope }: { readonly scope: TaskFocusScope }) {
  const place = scope === "chapter" ? "章台" : scope === "workflow" ? "机杼" : "书案";
  return (
    <div className="task-focus-empty-state">
      <span aria-hidden="true" className="task-focus-empty-mark" />
      <div>
        <strong>{place}暂静</strong>
        <p>新任务开始后，这里会显示当前进度和需要确认的动作。</p>
      </div>
    </div>
  );
}

export function TaskFocusPanel({
  scope = "global",
  compact = false,
  prominent = false,
  showIdentity = true,
  title = "当前关注",
  job,
  stream,
  onDecision,
  onExpand,
  diagnostics,
  events,
  phaseIndex,
}: TaskFocusPanelProps) {
  const [customDecisionText, setCustomDecisionText] = useState("");
  const presentation = useMemo(
    () => job === null ? null : deriveTaskFocusPresentation(job, stream),
    [job, stream],
  );
  const followLive = prominent && presentation?.contentLive === true;
  const { containerRef, sentinelRef, followingLatest, pauseFollow, scrollToLatest } = useStreamFollow(
    followLive ? presentation?.contentText : undefined,
    followLive ? job?.id : undefined,
  );

  if (job === null || presentation === null) {
    return (
      <section
        aria-label={title}
        className={`task-focus-panel${compact ? " is-compact" : ""}${prominent ? " is-prominent" : ""}`}
        data-scope={scope}
      >
        <QuietFocusState scope={scope} />
      </section>
    );
  }

  const tone = stateTone(job.state);
  const isThinking = presentation.activityLabel === "思考中";
  const isStructuredPreview = presentation.renderKindLabel === "JSON"
    || presentation.renderKindLabel === "JSON 片段"
    || presentation.renderKindLabel === "结构化报告";
  const decisions = job.decisions ?? [];
  const hasDecision = decisions.length > 0 && job.state === "paused";
  const activatePreview = (event: KeyboardEvent<HTMLDivElement>) => {
    if (onExpand !== undefined && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      onExpand();
    }
  };

  return (
    <section
      aria-label={title}
      className={`task-focus-panel${compact ? " is-compact" : ""}${prominent ? " is-prominent" : ""}`}
      data-scope={scope}
    >
      {showIdentity && <div className="task-focus-header">
        <div className="task-focus-header-text">
          <h3 className="task-focus-title">
            {presentation.title}
          </h3>
          {presentation.stepLabel !== null && <p className="task-focus-step">{presentation.stepLabel}</p>}
        </div>
        <span className={`task-focus-badge ${tone}`}>
          {STATE_LABEL[job.state] ?? job.state}
        </span>
      </div>}

      <div className="task-focus-progress">
        <strong className="task-focus-progress-value">{presentation.progress}%</strong>
        <div className="task-focus-progress-main">
          <div className="task-focus-progress-track">
            <div
              className={`task-focus-progress-fill ${tone}`}
              style={{ width: `${presentation.progress}%` }}
            />
          </div>
          <span className="task-focus-progress-caption">
            {presentation.progressCaption}
          </span>
        </div>
      </div>

      {/* Phase progress bar (A4: 6-phase chapter generation) */}
      {phaseIndex !== undefined && phaseIndex >= 0 && (
        <div className="task-focus-phases" aria-label="章节生成阶段">
          {PHASE_LABELS.map((label, index) => (
            <span
              className={index < phaseIndex ? "is-complete" : index === phaseIndex ? "is-active" : ""}
              key={label}
            >
              {label}
            </span>
          ))}
        </div>
      )}

      {compact && (
        <div className="task-focus-activity">
          <div className="task-focus-activity-copy">
            <div className="task-focus-activity-label">
              <span className={isThinking ? "task-focus-live is-thinking" : "task-focus-live"}>
                {presentation.activityLabel}
              </span>
              {presentation.renderKindLabel !== null && <span className="task-focus-render-badge">{presentation.renderKindLabel}</span>}
            </div>
            <p className="task-focus-ticker-text">{presentation.tickerText}</p>
          </div>
          {onExpand !== undefined && (
            <button aria-label="查看任务详情" className="task-focus-expand-btn" onClick={onExpand} type="button">
              <span>查看任务详情</span>
              <FocusArrowIcon />
            </button>
          )}
        </div>
      )}

      {!compact && (
        <>
          <div className="task-focus-stream-header">
            <span className="task-focus-stream-title">{presentation.contentHeading}</span>
            {presentation.renderKindLabel !== null && <span className="task-focus-render-badge">{presentation.renderKindLabel}</span>}
            {prominent && presentation.contentLive && (
              <button
                aria-label={followingLatest ? "暂停自动跟随" : "继续跟随最新输出"}
                aria-pressed={followingLatest}
                className={`task-trace-follow-toggle${followingLatest ? " is-following" : ""}`}
                onClick={followingLatest ? pauseFollow : scrollToLatest}
                type="button"
              >{followingLatest ? "自动跟随" : "继续跟随"}</button>
            )}
          </div>
          {isThinking && presentation.reasoningPreview.length > 0 && (
            <details
              className="task-focus-preview-fold"
              data-scope={scope}
            >
              <summary>
                <span aria-hidden="true" className="task-focus-preview-fold-caret" />
                思考中 · {presentation.reasoningCharacterCount} 字
              </summary>
              <pre aria-label="最新推理片段" className="task-focus-preview-fold-body">
                {presentation.reasoningPreview}
              </pre>
            </details>
          )}
          <div
            aria-label={onExpand !== undefined ? "点击查看任务详情" : undefined}
            className={`task-focus-preview${isStructuredPreview ? " is-structured" : ""}${onExpand !== undefined ? " is-clickable" : ""}`}
            onClick={onExpand}
            onKeyDown={activatePreview}
            role={onExpand !== undefined ? "button" : undefined}
            tabIndex={onExpand !== undefined ? 0 : undefined}
            aria-live="off"
            ref={followLive ? containerRef : undefined}
          >
            {presentation.contentPreview.length > 0 ? (
              <StreamContent
                density={prominent ? "reader" : "preview"}
                live={presentation.contentLive}
                {...(presentation.outputKind === undefined ? {} : { outputKind: presentation.outputKind })}
                revealInvalidSource={prominent}
                settled={presentation.contentSettled}
                text={prominent ? presentation.contentText : presentation.contentPreview}
                textLength={presentation.contentTextLength}
                textTruncated={presentation.contentTextTruncated}
                {...(presentation.validationStatus === undefined
                  ? {}
                  : { validationStatus: presentation.validationStatus })}
              />
            ) : (
              <p className="task-focus-preview-empty">{presentation.previewEmptyText}</p>
            )}
            {prominent && <div aria-hidden="true" className="task-stream-sentinel" ref={sentinelRef} />}
          </div>
        </>
      )}

      {/* Zone 3: Decision area */}
      {hasDecision && onDecision !== undefined && (
        <div className="task-focus-decision">
          <p className="task-focus-decision-title">等待你的确认</p>
          {decisions[0]?.description !== undefined && decisions[0].description.length > 0 && (
            <p className="task-focus-decision-desc">{decisions[0].description}</p>
          )}
          <div className="task-focus-decision-options">
            {decisions.map((decision) => (
              <button
                className="button button-secondary"
                key={decision.id}
                disabled={decision.requiresExplicitApproval && !decision.approvalVersion}
                onClick={() => onDecision(job.id, decision.decisionId ?? decision.id, decision.decisionId ? decision.id : decision.label, decision.id === "apply_edits" ? customDecisionText.trim() : "", decision.approvalVersion)}
                type="button"
              >
                {decision.label}
              </button>
            ))}
          </div>
          <div className="task-focus-decision-custom">
            <input
              aria-label="自定义回复"
              onChange={(event) => setCustomDecisionText(event.target.value)}
              placeholder="或输入自定义回复…"
              type="text"
              value={customDecisionText}
            />
            <button
              className="button button-primary"
              disabled={customDecisionText.trim().length === 0 || decisions[0]?.requiresExplicitApproval}
              onClick={() => {
                if (decisions[0] !== undefined) {
                  onDecision(job.id, decisions[0].id, "custom", customDecisionText.trim());
                }
                setCustomDecisionText("");
              }}
              type="button"
            >
              提交
            </button>
          </div>
        </div>
      )}

      {/* Diagnostics fold (A3) */}
      {!compact && diagnostics !== undefined && diagnostics.length > 0 && (
        <details className="task-focus-diagnostics">
          <summary>诊断摘要（{diagnostics.length}）</summary>
          <ul>{diagnostics.map((line, index) => <li key={index}>{line}</li>)}</ul>
        </details>
      )}

      {/* Key events fold (A3) */}
      {!compact && events !== undefined && events.length > 0 && (
        <details className="task-focus-diagnostics">
          <summary>关键事件（{events.length}）</summary>
          <ul>{events.map((line, index) => <li key={index}>{line}</li>)}</ul>
        </details>
      )}

      {!compact && onExpand !== undefined && (
        <button aria-label="查看任务详情" className="task-focus-expand-btn is-full" onClick={onExpand} type="button">
          <span>查看任务详情</span>
          <FocusArrowIcon />
        </button>
      )}
    </section>
  );
}
