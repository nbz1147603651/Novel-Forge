/**
 * FloatingTaskCompanion — the desktop pet overlay.
 *
 * Mirrors PySide6 `FloatingTaskCompanion` (companion.py, 950 lines) as a
 * React component with:
 * - CSS-driven sprite animation (PetSprite)
 * - Task bubble cards (TaskBubbleCard, max 2)
 * - Drag-to-reposition via pointer events
 * - Double-click to reset position
 * - Context menu (reset / hide)
 * - Compact/normal display modes
 * - Token usage label
 * - Cross-fade on state change (CSS opacity transition)
 * - Glow pulse for non-idle states (CSS box-shadow animation)
 *
 * Position is persisted via the `onPositionChange` callback (ui-session).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { JobView } from "@nimo/engine-contracts";

import { derivePetState, type PetVisualState } from "../../lib/pet-state";
import { PetSprite } from "./PetSprite";
import { TaskBubbleCard } from "./TaskBubbleCard";

interface PetCompanionProps {
  readonly jobs: readonly JobView[];
  readonly visible: boolean;
  readonly compact?: boolean;
  readonly atlasUrl?: string | null;
  readonly spriteUrl?: string | null;
  readonly displayName?: string;
  readonly tokenUsageLabel?: string;
  /** Opens the companion-owned stream for one explicitly selected task. */
  readonly onTaskActivated?: (taskId: string) => void;
  /** Optional action for clicking the sprite itself; it never opens task output. */
  readonly onActivated?: () => void;
  readonly onHideRequested?: () => void;
  readonly onPositionChange?: (position: { x: number; y: number }) => void;
  readonly initialPosition?: { x: number; y: number };
  /**
   * Optional ref to the companion's container element.  Callers use this to
   * anchor secondary popups (e.g. the floating stream dialog) so they open
   * adjacent to the pet rather than at the center of the workspace.
   */
  readonly containerRef?: React.MutableRefObject<HTMLDivElement | null>;
}

const DRAG_THRESHOLD = 5;

export function PetCompanion({
  atlasUrl = null,
  compact = true,
  containerRef: externalContainerRef,
  displayName = "Nimo",
  initialPosition,
  jobs,
  onActivated,
  onHideRequested,
  onPositionChange,
  onTaskActivated,
  spriteUrl = null,
  tokenUsageLabel = "",
  visible,
}: PetCompanionProps) {
  const internalContainerRef = useRef<HTMLDivElement | null>(null);
  // Forward the local ref to the parent so anchor-aware popups can read the
  // pet's current viewport rect (see FloatingStreamDialog / TaskObservationDialog
  // anchor computation).  We use a callback ref pattern so the external ref
  // updates synchronously whenever the pet renders.
  const containerRef = externalContainerRef ?? internalContainerRef;
  const positionRef = useRef(initialPosition ?? { x: 0, y: 0 });
  const positionFrameRef = useRef<number | null>(null);
  const [position, setPosition] = useState(positionRef.current);
  // Attention bubbles are a secondary shortcut. Start collapsed so they do
  // not cover the task focus/reader surfaces the user is actively working in.
  const [bubblesExpanded, setBubblesExpanded] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const [idleTick, setIdleTick] = useState(0);
  const dragState = useRef<{ startX: number; startY: number; originX: number; originY: number; dragging: boolean } | null>(null);
  const spriteSize = compact ? 54 : 68;

  const commitPosition = useCallback((nextPosition: { x: number; y: number }) => {
    positionRef.current = nextPosition;
    if (positionFrameRef.current !== null) return;
    positionFrameRef.current = requestAnimationFrame(() => {
      positionFrameRef.current = null;
      setPosition(positionRef.current);
    });
  }, []);

  useEffect(
    () => () => {
      if (positionFrameRef.current !== null) {
        cancelAnimationFrame(positionFrameRef.current);
        positionFrameRef.current = null;
      }
    },
    [],
  );

  // Derive pet visual state from jobs.
  const petState = derivePetState(jobs, idleTick);

  // Idle animation tick timer (only when no active attention).
  useEffect(() => {
    if (!visible || petState.hasAttention) return;
    const timer = setInterval(() => {
      setIdleTick((tick) => tick + 1);
    }, 720); // ~6fps idle cycle
    return () => clearInterval(timer);
  }, [visible, petState.hasAttention]);

  // Default position: bottom-right corner.
  useEffect(() => {
    if (initialPosition !== undefined || containerRef.current === null) return;
    const parent = containerRef.current.parentElement;
    if (parent === null) return;
    const rect = parent.getBoundingClientRect();
    commitPosition({ x: rect.width - 100, y: rect.height - 120 });
  }, [commitPosition, initialPosition]);

  const handleNonLoopEnd = useCallback(() => {
    // Non-looping animation finished — reset to idle by bumping the tick.
    setIdleTick((tick) => tick + 1);
  }, []);

  // ── Pointer-based drag ──────────────────────────────────────────

  const handlePointerDown = (event: React.PointerEvent) => {
    if (event.button !== 0) return;
    event.preventDefault();
    dragState.current = {
      startX: event.clientX,
      startY: event.clientY,
      originX: positionRef.current.x,
      originY: positionRef.current.y,
      dragging: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handlePointerMove = (event: React.PointerEvent) => {
    const drag = dragState.current;
    if (drag === null) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.dragging && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) return;
    drag.dragging = true;
    commitPosition({ x: drag.originX + dx, y: drag.originY + dy });
  };

  const handlePointerUp = (event: React.PointerEvent) => {
    const drag = dragState.current;
    dragState.current = null;
    if (drag === null) return;
    if (drag.dragging) {
      onPositionChange?.(positionRef.current);
    } else {
      // The companion body only opens its own notification tray.  A task
      // bubble chooses a task explicitly before any stream reader is opened,
      // so it cannot accidentally borrow the focus panel's active stream.
      if (onActivated !== undefined) onActivated();
      else setBubblesExpanded((expanded) => !expanded);
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const handleDoubleClick = () => {
    const parent = containerRef.current?.parentElement;
    if (parent === undefined || parent === null) return;
    const rect = parent.getBoundingClientRect();
    const resetPos = { x: rect.width - 100, y: rect.height - 120 };
    commitPosition(resetPos);
    onPositionChange?.(resetPos);
  };

  const handleContextMenu = (event: React.MouseEvent) => {
    event.preventDefault();
    setContextMenu({ x: event.clientX, y: event.clientY });
  };

  const closeContextMenu = () => setContextMenu(null);

  // Close context menu on outside click.
  useEffect(() => {
    if (contextMenu === null) return;
    const handler = () => closeContextMenu();
    window.addEventListener("click", handler, { once: true });
    return () => window.removeEventListener("click", handler);
  }, [contextMenu]);

  if (!visible) return null;

  const hasBubbles = petState.bubbleJobs.length > 0;
  const showBubbles = bubblesExpanded && hasBubbles;

  // B2: Smart bubble positioning with viewport edge detection.
  // Default: above-right of pet. Fallback: left side, then below.
  const BUBBLE_W = 280;
  const BUBBLE_H = 180;
  const MARGIN = 8;
  const bubblePosition = (() => {
    const vw = typeof window !== "undefined" ? window.innerWidth : 1440;
    let x = position.x - BUBBLE_W + 40;
    let y = position.y - BUBBLE_H - 8;
    // If above overflows top, try below
    if (y < MARGIN) {
      y = position.y + (compact ? 72 : 92) + 8;
    }
    // If left overflows, clamp to margin
    if (x < MARGIN) {
      x = MARGIN;
    }
    // If right overflows, pull back
    if (x + BUBBLE_W > vw - MARGIN) {
      x = vw - BUBBLE_W - MARGIN;
    }
    return { x, y };
  })();

  return (
    <>
      <div
        className={`pet-companion${compact ? " is-compact" : ""}${petState.hasAttention ? " has-attention" : ""}`}
        onContextMenu={handleContextMenu}
        onDoubleClick={handleDoubleClick}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        ref={containerRef}
        role="button"
        style={{ transform: `translate(${position.x}px, ${position.y}px)` }}
        tabIndex={-1}
        title={petState.hasAttention ? `${displayName} · ${petState.stateLabel}` : displayName}
      >
        {/* Glow ring (CSS animation, only for non-idle states) */}
        <div className={`pet-glow is-${petState.visualState}`} />

        {/* Sprite */}
        <PetSprite
          atlasUrl={atlasUrl}
          displayName={displayName}
          onAnimationEnd={handleNonLoopEnd}
          size={spriteSize}
          spriteUrl={spriteUrl}
          state={petState.visualState}
        />

        {/* Labels */}
        {!compact && <span className="pet-name">{displayName}</span>}
        <span className="pet-meta">{petState.stateLabel}</span>
        {tokenUsageLabel.length > 0 && <span className="pet-usage">{tokenUsageLabel}</span>}

        {/* Bubble toggle */}
        {hasBubbles && (
          <button
            aria-label={bubblesExpanded ? "收起任务气泡" : "展开任务气泡"}
            className="pet-bubble-toggle"
            onClick={(event) => { event.stopPropagation(); setBubblesExpanded(!bubblesExpanded); }}
            onPointerDown={(event) => event.stopPropagation()}
            onPointerUp={(event) => event.stopPropagation()}
            type="button"
          >
            {bubblesExpanded ? "▾" : "▴"}
          </button>
        )}
      </div>

      {/* Task bubble panel (B2: viewport edge detection) */}
      {showBubbles && (
        <div
          className="pet-bubble-panel"
          style={{ left: bubblePosition.x, top: bubblePosition.y }}
        >
          {petState.bubbleJobs.map((job) => (
            <TaskBubbleCard
              compact={compact}
              job={job}
              key={job.id}
              {...(onTaskActivated === undefined ? {} : { onActivated: () => onTaskActivated(job.id) })}
            />
          ))}
        </div>
      )}

      {/* Context menu */}
      {contextMenu !== null && (
        <div className="pet-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }}>
          <button
            onClick={() => { handleDoubleClick(); closeContextMenu(); }}
            type="button"
          >
            回到右下角
          </button>
          <hr />
          <button
            onClick={() => { onHideRequested?.(); closeContextMenu(); }}
            type="button"
          >
            隐藏 Nimo
          </button>
        </div>
      )}
    </>
  );
}
