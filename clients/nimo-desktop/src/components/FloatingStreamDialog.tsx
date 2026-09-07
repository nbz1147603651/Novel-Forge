import { useMemo, useState } from "react";

import type { TaskStreamState } from "../lib/task-stream";
import { buildTaskStreamTranscript } from "../lib/task-stream-transcript";
import { deriveTaskStreamTrace } from "../lib/task-stream-trace";
import { displayStepLabel as resolveDisplayStepLabel, taskStreamStatusLabel } from "../lib/task-stream-presentation";
import { usePanelDrag } from "../lib/use-panel-drag";
import { useThinkingFoldState, type ThinkingFoldApi } from "../lib/use-thinking-fold-state";
import { OverlaySurface } from "./OverlaySurface";
import { FLOATING_STREAM_TABS, RawStreamEvents, STREAM_TAB_LABELS, type FloatingStreamTab } from "./stream-tabs";
import { TaskCallDetails, TaskStreamDetail } from "./TaskStreamDetail";

/**
 * Body of the floating stream dialog.  Exported so each tab can be
 * exercised in isolation by tests: the parent owns the `tab` state but
 * the body can be rendered with a controlled `tab` prop to assert the
 * `流式详情` → TaskStreamDetail, `调用详情` → TaskCallDetails,
 * `原始事件` → RawStreamEvents projection without driving React state
 * during a server-rendered test.
 */
export interface FloatingStreamDialogBodyProps {
  readonly tab: FloatingStreamTab;
  readonly stream: TaskStreamState | null;
  readonly foldState: ThinkingFoldApi;
  readonly displayStepLabel: string | undefined;
}

export function FloatingStreamDialogBody({
  displayStepLabel,
  foldState,
  stream,
  tab,
}: FloatingStreamDialogBodyProps) {
  if (tab === "stream") {
    return (
      <TaskStreamDetail
        {...(displayStepLabel === undefined ? {} : { displayStepLabel })}
        foldState={foldState}
        showSummary={false}
        stream={stream}
      />
    );
  }
  if (tab === "calls") {
    return (
      <TaskCallDetails
        {...(displayStepLabel === undefined ? {} : { displayStepLabel })}
        showContext={false}
        stream={stream}
      />
    );
  }
  return <RawStreamEvents stream={stream} />;
}

/**
 * Anchor rect (in viewport pixels) for the pet companion.  When provided
 * the dialog opens adjacent to the anchor with viewport edge detection so
 * the reader never loses sight of which pet bubble it came from.  The pet
 * owns this measurement: the dialog must remain a passive consumer so the
 * pet can drag itself without re-anchoring the dialog.
 */
export interface FloatingStreamAnchor {
  /** Left edge of the anchor in viewport pixels. */
  readonly left: number;
  /** Top edge of the anchor in viewport pixels. */
  readonly top: number;
  /** Anchor width in viewport pixels. */
  readonly width: number;
  /** Anchor height in viewport pixels. */
  readonly height: number;
}

const ANCHOR_GAP = 12;
/** Width of the floating dialog so we can compute the anchor offset. */
const ANCHOR_DIALOG_WIDTH = 900;
const ANCHOR_DIALOG_HEIGHT = 620;
/** Backdrop padding (matches `.floating-stream-backdrop`). */
const ANCHOR_BACKDROP_PADDING = 28;

/**
 * Compute a `transform: translate(x, y)` offset that places the dialog
 * adjacent to the pet anchor.  The backdrop already parks the dialog at the
 * bottom-right corner (`align-items: end; justify-items: end;` plus 28px
 * padding), so the transform is an offset on top of that resting position.
 */
export function computeAnchorOffset(
  anchor: FloatingStreamAnchor,
  viewportWidth: number,
  viewportHeight: number,
): { x: number; y: number } {
  // The caller is expected to pass the viewport size (or 0 for "use window").
  // The fallback to window.innerWidth/innerHeight only kicks in when both
  // the caller and the global are absent, which never happens in the
  // browser but makes the helper safe for SSR and unit tests alike.
  const vw = viewportWidth > 0
    ? viewportWidth
    : typeof window === "undefined" ? 0 : window.innerWidth;
  const vh = viewportHeight > 0
    ? viewportHeight
    : typeof window === "undefined" ? 0 : window.innerHeight;
  if (vw === 0 || vh === 0) return { x: 0, y: 0 };
  // Natural bottom-right position before any anchor offset is applied.
  const naturalX = vw - ANCHOR_DIALOG_WIDTH - ANCHOR_BACKDROP_PADDING;
  const naturalY = vh - ANCHOR_DIALOG_HEIGHT - ANCHOR_BACKDROP_PADDING;
  // Default placement: dialog sits above the pet, right-aligned with the
  // pet's right edge.
  let x = anchor.left + anchor.width - ANCHOR_DIALOG_WIDTH - naturalX;
  let y = anchor.top - ANCHOR_DIALOG_HEIGHT - ANCHOR_GAP - naturalY;
  // Clamp horizontally so the dialog never goes off the left edge.
  const minX = ANCHOR_GAP - naturalX;
  const maxX = vw - ANCHOR_DIALOG_WIDTH - ANCHOR_GAP - naturalX;
  if (x < minX) x = minX;
  if (x > maxX) x = maxX;
  // If the dialog would clip the top edge (pet sits near the bottom), place
  // it below the pet instead.
  const minY = ANCHOR_GAP - naturalY;
  if (y < minY) {
    y = anchor.top + anchor.height + ANCHOR_GAP - naturalY;
  }
  return { x, y };
}

/**
 * Phase-1 browser analogue of the source's always-on-top stream window.
 * Tauri can later promote this exact content component into a native child
 * window without changing TaskStreamState or stream rendering behavior.
 * Tab definitions/labels and the raw-event view are shared with the
 * task observation dialog (stream-tabs.tsx).
 */
export interface FloatingStreamDialogProps {
  readonly onClose: () => void;
  readonly stream: TaskStreamState | null;
  /**
   * Optional pet-anchor rect.  When set, the dialog opens adjacent to the
   * pet companion so the user can see both the dialog and the bubble
   * that opened it.  Drag offsets are added on top of the anchor offset
   * so the user can still reposition the dialog after opening.
   */
  readonly anchor?: FloatingStreamAnchor | null;
}

export function FloatingStreamDialog({ anchor = null, onClose, stream }: FloatingStreamDialogProps) {
  const [tab, setTab] = useState<FloatingStreamTab>("stream");
  const drag = usePanelDrag();
  // Mirror the focus dialog: project reasoning keys from the same transcript
  // the body uses so the header fold buttons stay in lockstep with the
  // per-segment <details> elements.
  const reasoningKeys = useMemo(
    () => buildTaskStreamTranscript(stream?.events ?? []).items
      .filter((item) => item.kind === "reasoning")
      .map((item) => item.key),
    [stream?.events],
  );
  const foldState = useThinkingFoldState(stream?.taskId, reasoningKeys);
  const batchedTrace = useMemo(() => deriveTaskStreamTrace(stream).nodes.length > 1, [stream]);
  const showFoldToolbar = tab === "stream" && foldState.hasReasoning && !batchedTrace;
  // Engine labels are Chinese; legacy snapshots fall back to the local
  // formatter so the floating header never exposes a raw step key.
  const stepLabel = stream === null ? undefined : resolveDisplayStepLabel(stream);
  // Anchor offset is computed once on mount so the dialog appears next to
  // the pet even if the user has never dragged it.  User drag is layered on
  // top via the `drag.offset` transform.
  const anchorOffset = useMemo(
    () => (anchor === null ? { x: 0, y: 0 } : computeAnchorOffset(anchor, 0, 0)),
    [anchor],
  );
  const transformX = anchorOffset.x + drag.offset.x;
  const transformY = anchorOffset.y + drag.offset.y;

  return (
    <OverlaySurface
      ariaLabel="流式详情"
      backdropClassName="floating-stream-backdrop"
      className={`floating-stream-overlay${anchor !== null ? " is-anchored" : ""}`}
      modal={false}
      onClose={onClose}
      style={{ transform: `translate(${transformX}px, ${transformY}px)` }}
    >
      <header className="floating-stream-header" {...drag.headerProps}>
        <div className="floating-stream-heading">
          <strong>{stream?.title ?? "正在连接任务"}</strong>
          <small>{stepLabel ?? "等待任务流"}</small>
        </div>
        <span className="floating-stream-progress">{stream?.progressPercent ?? 0}%</span>
        {showFoldToolbar && (
          <div aria-label="思考折叠工具" className="floating-stream-toolbar" role="group">
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
        )}
        <span className={`task-observation-status is-${stream?.status ?? "streaming"}`}>{stream === null ? "连接中" : taskStreamStatusLabel(stream.status, stream.jobState)}</span>
        <button aria-label="关闭流式详情" className="app-dialog-close" onClick={onClose} type="button">×</button>
      </header>
      <div aria-label="流式详情分类" className="floating-stream-tabs" role="tablist">
        {FLOATING_STREAM_TABS.map((item) => (
          <button
            aria-selected={tab === item}
            className={tab === item ? "is-active" : ""}
            key={item}
            onClick={() => setTab(item)}
            role="tab"
            type="button"
          >
            {STREAM_TAB_LABELS[item]}
          </button>
        ))}
      </div>
      <section className="floating-stream-body">
        <FloatingStreamDialogBody
          displayStepLabel={stepLabel}
          foldState={foldState}
          stream={stream}
          tab={tab}
        />
      </section>
    </OverlaySurface>
  );
}
