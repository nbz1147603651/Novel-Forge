import { useCallback, useRef, useState } from "react";

/**
 * Lightweight virtual scroll hook for large lists.
 *
 * Renders only the visible window of items plus a small overscan buffer,
 * keeping DOM node count constant regardless of list length. Designed for
 * uniform-height rows (chapter lists, job cards, route rows).
 *
 * Usage:
 * ```tsx
 * const { containerProps, visibleItems, totalHeight, offsetY } = useVirtualList({
 *   itemCount: chapters.length,
 *   itemHeight: 72,
 *   overscan: 4,
 * });
 * ```
 */
export interface VirtualListConfig {
  /** Total number of items in the list. */
  readonly itemCount: number;
  /** Fixed height of each row in pixels. */
  readonly itemHeight: number;
  /** Number of extra rows to render above/below the viewport. */
  readonly overscan?: number;
  /** Viewport height in pixels (defaults to container measurement). */
  readonly viewportHeight?: number;
}

export interface VirtualListResult {
  /** Props to spread on the scrollable container element. */
  readonly containerProps: {
    readonly onScroll: (event: React.UIEvent<HTMLElement>) => void;
    readonly ref: (node: HTMLElement | null) => void;
    readonly style: React.CSSProperties;
  };
  /** Indices of items that should be rendered. */
  readonly visibleRange: { readonly start: number; readonly end: number };
  /** Total scrollable height for the spacer element. */
  readonly totalHeight: number;
  /** Y offset for the first visible item (translateY on the inner wrapper). */
  readonly offsetY: number;
}

export function useVirtualList(config: VirtualListConfig): VirtualListResult {
  const { itemCount, itemHeight, overscan = 4 } = config;
  const [scrollTop, setScrollTop] = useState(0);
  const [measuredHeight, setMeasuredHeight] = useState(config.viewportHeight ?? 600);
  const nodeRef = useRef<HTMLElement | null>(null);

  const totalHeight = itemCount * itemHeight;
  const startIndex = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan);
  const visibleCount = Math.ceil(measuredHeight / itemHeight) + overscan * 2;
  const endIndex = Math.min(itemCount, startIndex + visibleCount);
  const offsetY = startIndex * itemHeight;

  const handleScroll = useCallback((event: React.UIEvent<HTMLElement>) => {
    setScrollTop(event.currentTarget.scrollTop);
  }, []);

  const handleRef = useCallback((node: HTMLElement | null) => {
    nodeRef.current = node;
    if (node !== null && config.viewportHeight === undefined) {
      setMeasuredHeight(node.clientHeight);
      const observer = new ResizeObserver((entries) => {
        const entry = entries[0];
        if (entry !== undefined) {
          setMeasuredHeight(entry.contentRect.height);
        }
      });
      observer.observe(node);
    }
  }, [config.viewportHeight]);

  return {
    containerProps: {
      onScroll: handleScroll,
      ref: handleRef,
      style: { overflow: "auto", position: "relative" },
    },
    visibleRange: { start: startIndex, end: endIndex },
    totalHeight,
    offsetY,
  };
}
