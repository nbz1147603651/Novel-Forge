/**
 * Smart auto-scroll hook for live streaming output.
 *
 * Mirrors PySide6 `StreamFollowController` behavior:
 * - Auto-follows newest content when the reader is at the bottom
 * - Pauses following when the user scrolls up to inspect earlier output
 * - Resumes following when the reader returns to the bottom
 *
 * Uses IntersectionObserver on a sentinel element (lighter than PySide6's
 * scrollbar signal approach) and exposes `followingLatest` for a
 * "back to bottom" floating button.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export interface StreamFollowApi {
  /** Ref to attach to the scrollable container. */
  readonly containerRef: React.RefObject<HTMLDivElement | null>;
  /** Ref to attach to a sentinel element at the bottom of the list. */
  readonly sentinelRef: React.RefObject<HTMLDivElement | null>;
  /** Whether the viewport is currently following the latest output. */
  readonly followingLatest: boolean;
  /** Programmatically scroll to the latest content and resume following. */
  readonly scrollToLatest: () => void;
  /** Pause following without changing the user's current scroll position. */
  readonly pauseFollow: () => void;
  /** Reset following state (use when switching streams). */
  readonly resetFollow: () => void;
}

export function useStreamFollow(
  contentDependency?: unknown,
  observerDependency?: unknown,
): StreamFollowApi {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const [followingLatest, setFollowingLatest] = useState(true);
  const programmaticScrollRef = useRef(false);
  const releaseTimerRef = useRef<number | null>(null);

  // Observe the sentinel to detect when the user is at/near the bottom.
  useEffect(() => {
    const sentinel = sentinelRef.current;
    const container = containerRef.current;
    if (sentinel === null || container === null) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // Ignore intersections triggered by our own scrollTo calls.
        if (programmaticScrollRef.current) return;
        for (const entry of entries) {
          setFollowingLatest((current) => current === entry.isIntersecting
            ? current
            : entry.isIntersecting);
        }
      },
      {
        root: container,
        // Trigger slightly before the sentinel is fully visible so the
        // reader does not need to pixel-perfect scroll to the very bottom.
        rootMargin: "0px 0px 24px 0px",
        threshold: 0,
      },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
    // Re-observe only when the scroll surface changes. Content updates have a
    // separate dependency below so a token chunk never rebuilds the observer.
  }, [observerDependency]);

  const scrollToLatest = useCallback(() => {
    const container = containerRef.current;
    if (container === null) return;
    if (releaseTimerRef.current !== null) window.clearTimeout(releaseTimerRef.current);
    programmaticScrollRef.current = true;
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    setFollowingLatest(true);
    // Release the programmatic guard after the smooth scroll settles.
    requestAnimationFrame(() => {
      releaseTimerRef.current = window.setTimeout(() => {
        programmaticScrollRef.current = false;
        releaseTimerRef.current = null;
      }, 300);
    });
  }, []);

  const pauseFollow = useCallback(() => {
    if (releaseTimerRef.current !== null) {
      window.clearTimeout(releaseTimerRef.current);
      releaseTimerRef.current = null;
    }
    programmaticScrollRef.current = false;
    setFollowingLatest(false);
  }, []);

  const resetFollow = useCallback(() => {
    setFollowingLatest(true);
    programmaticScrollRef.current = false;
  }, []);

  // Auto-scroll when following and new content arrives.
  useEffect(() => {
    if (!followingLatest) return;
    const container = containerRef.current;
    if (container === null) return;
    programmaticScrollRef.current = true;
    container.scrollTop = container.scrollHeight;
    requestAnimationFrame(() => {
      programmaticScrollRef.current = false;
    });
  }, [contentDependency, followingLatest]);

  useEffect(() => () => {
    if (releaseTimerRef.current !== null) window.clearTimeout(releaseTimerRef.current);
  }, []);

  return {
    containerRef,
    sentinelRef,
    followingLatest,
    pauseFollow,
    resetFollow,
    scrollToLatest,
  };
}
