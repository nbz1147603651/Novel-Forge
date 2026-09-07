import { useRef, useState, type PointerEvent } from "react";

export interface PanelOffset {
  readonly x: number;
  readonly y: number;
}

interface ActivePointer {
  readonly pointerId: number;
  readonly originOffset: PanelOffset;
  readonly startX: number;
  readonly startY: number;
}

const zeroOffset: PanelOffset = { x: 0, y: 0 };

export function panelOffsetAfterPointer(origin: PanelOffset, startX: number, startY: number, currentX: number, currentY: number): PanelOffset {
  return { x: origin.x + currentX - startX, y: origin.y + currentY - startY };
}

function ignoresDrag(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && target.closest("button, input, select, textarea, a, [data-no-panel-drag]") !== null;
}

/**
 * Small pointer-only drag primitive for source-shaped floating panels.
 * It deliberately owns presentation offset only; opening, focus trapping and
 * persistence remain the concern of OverlaySurface and its parent page.
 */
export function usePanelDrag() {
  const [offset, setOffset] = useState<PanelOffset>(zeroOffset);
  const activePointer = useRef<ActivePointer | null>(null);

  const onPointerDown = (event: PointerEvent<HTMLElement>) => {
    if (event.button !== 0 || ignoresDrag(event.target)) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    activePointer.current = {
      pointerId: event.pointerId,
      originOffset: offset,
      startX: event.clientX,
      startY: event.clientY,
    };
  };
  const onPointerMove = (event: PointerEvent<HTMLElement>) => {
    const active = activePointer.current;
    if (active === null || active.pointerId !== event.pointerId) return;
    setOffset(panelOffsetAfterPointer(active.originOffset, active.startX, active.startY, event.clientX, event.clientY));
  };
  const finishDrag = (event: PointerEvent<HTMLElement>) => {
    if (activePointer.current?.pointerId !== event.pointerId) return;
    activePointer.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  return {
    headerProps: { onPointerCancel: finishDrag, onPointerDown, onPointerMove, onPointerUp: finishDrag },
    offset,
  } as const;
}
