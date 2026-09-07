/**
 * Task bubble card shown next to the pet companion.
 *
 * Mirrors PySide6 `_TaskProgressBubbleCard` — a compact card with title,
 * detail line, 3px progress bar, and a custom-painted status icon.
 * Up to 2 cards are shown (enforced by the parent PetCompanion).
 */
import type { JobView } from "@nimo/engine-contracts";

type BubbleStatus = "running" | "success" | "failed" | "decision" | "paused" | "queued" | "cancelled";

const TONE_BY_STATUS: Record<BubbleStatus, string> = {
  running: "active",
  success: "success",
  failed: "danger",
  decision: "warning",
  paused: "muted",
  queued: "muted",
  cancelled: "muted",
};

function deriveBubbleStatus(job: JobView): BubbleStatus {
  if (job.decisions !== undefined && job.decisions.length > 0 && job.state === "paused") return "decision";
  if (job.state === "succeeded") return "success";
  if (job.state === "failed") return "failed";
  if (job.state === "paused") return "paused";
  if (job.state === "queued") return "queued";
  return "running";
}

function bubbleDetail(job: JobView, status: BubbleStatus): string {
  let detail: string;
  if (status === "success") {
    detail = "任务已完成";
  } else if (status === "failed") {
    detail = job.detail || "任务执行失败";
  } else if (status === "cancelled") {
    detail = "任务已取消";
  } else if (status === "decision") {
    detail = "等待你的确认";
  } else {
    detail = job.currentStep || job.label;
  }
  detail = detail.replaceAll(/\s+/g, " ").trim();
  if ((status === "running" || status === "paused" || status === "decision") && job.progressPercent > 0) {
    detail = `${detail} · ${job.progressPercent}%`;
  }
  return detail.length > 72 ? `${detail.slice(0, 72)}…` : detail;
}

/** SVG status icons matching PySide6 _TaskBubbleStatusIcon paint logic. */
function StatusIcon({ status }: { readonly status: BubbleStatus }) {
  switch (status) {
    case "running":
      return (
        <svg className="pet-bubble-icon is-running" height="24" viewBox="0 0 24 24" width="24">
          <circle cx="12" cy="12" fill="none" opacity="0.3" r="9" stroke="currentColor" strokeWidth="2" />
          <path d="M12 3a9 9 0 0 1 9 9" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2.4" />
        </svg>
      );
    case "success":
      return (
        <svg className="pet-bubble-icon is-success" height="24" viewBox="0 0 24 24" width="24">
          <circle cx="12" cy="12" fill="var(--nf-status-success)" r="9" />
          <path d="M7 12l3.3 3.2L17.2 8.2" fill="none" stroke="var(--nf-bg-surface)" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
        </svg>
      );
    case "failed":
      return (
        <svg className="pet-bubble-icon is-failed" height="24" viewBox="0 0 24 24" width="24">
          <circle cx="12" cy="12" fill="var(--nf-status-danger)" r="9" />
          <path d="M8 8l8 8M16 8l-8 8" stroke="var(--nf-bg-surface)" strokeLinecap="round" strokeWidth="2" />
        </svg>
      );
    case "decision":
      return (
        <svg className="pet-bubble-icon is-decision" height="24" viewBox="0 0 24 24" width="24">
          <circle cx="12" cy="12" fill="var(--nf-status-warning)" r="9" />
          <path d="M12 7v6" stroke="var(--nf-bg-surface)" strokeLinecap="round" strokeWidth="2" />
          <circle cx="12" cy="17" fill="var(--nf-bg-surface)" r="1.2" />
        </svg>
      );
    case "paused":
      return (
        <svg className="pet-bubble-icon is-paused" height="24" viewBox="0 0 24 24" width="24">
          <circle cx="12" cy="12" fill="none" r="9" stroke="currentColor" strokeWidth="2" />
          <path d="M9 8v8M15 8v8" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
        </svg>
      );
    default:
      return (
        <svg className="pet-bubble-icon is-queued" height="24" viewBox="0 0 24 24" width="24">
          <circle cx="12" cy="12" fill="none" r="9" stroke="currentColor" strokeWidth="2" />
          <path d="M8 12h8" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
        </svg>
      );
  }
}

interface TaskBubbleCardProps {
  readonly job: JobView;
  readonly compact?: boolean;
  readonly onActivated?: () => void;
  /**
   * Explicitly mark the card as having an active stream.  Defaults to
   * `state === "running"` which is a good proxy; callers that know the
   * underlying stream status (e.g. have the TaskStreamState) can pass a
   * more precise value to suppress the indicator once the stream completes
   * but the job is still running.
   */
  readonly streaming?: boolean;
}

export function TaskBubbleCard({ compact = false, job, onActivated, streaming }: TaskBubbleCardProps) {
  const status = deriveBubbleStatus(job);
  const tone = TONE_BY_STATUS[status];
  const title = (job.label || job.currentStep || "后台任务").trim();
  const truncatedTitle = title.length > 42 ? `${title.slice(0, 42)}…` : title;
  const detail = bubbleDetail(job, status);
  const progress = status === "success" ? 100 : Math.max(0, Math.min(100, job.progressPercent));
  // Default: any running job is treated as streaming.  Parents that know the
  // stream status can override.
  const showStreaming = streaming ?? (status === "running");

  return (
    <button
      className={`pet-bubble-card is-${tone}${compact ? " is-compact" : ""}${showStreaming ? " is-streaming" : ""}`}
      onClick={onActivated}
      title={`${title}\n${detail}`}
      type="button"
    >
      <div className="pet-bubble-text">
        <span className="pet-bubble-title">{truncatedTitle}</span>
        <span className="pet-bubble-detail">{detail}</span>
        {showStreaming && (
          <span aria-label="流式输出中" className="pet-bubble-streaming-tag">流式输出中</span>
        )}
        <span className="pet-bubble-progress">
          <span className={`pet-bubble-progress-fill is-${tone}`} style={{ width: `${progress}%` }} />
        </span>
      </div>
      <StatusIcon status={status} />
    </button>
  );
}
