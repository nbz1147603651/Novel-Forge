/**
 * CSS-driven sprite animation renderer for the desktop pet.
 *
 * Replaces PySide6's QTimer + QPixmap.copy() approach with pure CSS
 * `@keyframes` + `steps()` for GPU-accelerated frame-by-frame animation.
 *
 * The atlas spritesheet is 8 columns x 9 rows (192x208px cells).
 * Each state maps to a row; CSS background-position steps through frames.
 */
import { useEffect, useRef, useState } from "react";

import {
  NON_LOOPING_STATES,
  PET_ATLAS_ROW_SPECS,
  type PetVisualState,
} from "../../lib/pet-state";

/** Atlas geometry constants (mirrors PySide6 CODEX_ATLAS_*). */
const ATLAS_COLUMNS = 8;
const ATLAS_CELL_WIDTH = 192;
const ATLAS_CELL_HEIGHT = 208;

interface PetSpriteProps {
  readonly state: PetVisualState;
  readonly size: number;
  readonly atlasUrl: string | null;
  readonly spriteUrl: string | null;
  readonly displayName: string;
  /** Called when a non-looping animation finishes (auto-return to idle). */
  readonly onAnimationEnd?: () => void;
}

export function PetSprite({
  atlasUrl,
  displayName,
  onAnimationEnd,
  size,
  spriteUrl,
  state,
}: PetSpriteProps) {
  const elementRef = useRef<HTMLDivElement | null>(null);
  const prevStateRef = useRef<PetVisualState>(state);
  const [crossfade, setCrossfade] = useState(false);
  const [atlasUnavailable, setAtlasUnavailable] = useState(false);
  const spec = PET_ATLAS_ROW_SPECS[state];
  const isNonLooping = NON_LOOPING_STATES.has(state);
  const frameDuration = 1000 / spec.fps;
  const totalDuration = (frameDuration * spec.frames) / 1000;
  const scale = size / ATLAS_CELL_WIDTH;

  // B1: Cross-fade on state change — dip opacity to 0.4 then recover.
  useEffect(() => {
    if (prevStateRef.current === state) return;
    prevStateRef.current = state;
    setCrossfade(true);
    const raf = requestAnimationFrame(() => {
      requestAnimationFrame(() => setCrossfade(false));
    });
    return () => cancelAnimationFrame(raf);
  }, [state]);

  // Listen for animationend on non-looping states to trigger auto-return.
  useEffect(() => {
    const element = elementRef.current;
    if (element === null || !isNonLooping || onAnimationEnd === undefined) return;
    const handler = () => onAnimationEnd();
    element.addEventListener("animationend", handler, { once: true });
    return () => element.removeEventListener("animationend", handler);
  }, [state, isNonLooping, onAnimationEnd]);

  // CSS backgrounds do not expose an error event.  Preload the atlas once so
  // a packaged-resource miss still produces the supplied static pet instead
  // of an invisible companion.
  useEffect(() => {
    if (atlasUrl === null || atlasUrl.length === 0 || typeof Image === "undefined") {
      setAtlasUnavailable(false);
      return;
    }
    let disposed = false;
    const atlas = new Image();
    atlas.onload = () => {
      if (!disposed) setAtlasUnavailable(false);
    };
    atlas.onerror = () => {
      if (!disposed) setAtlasUnavailable(true);
    };
    atlas.src = atlasUrl;
    return () => {
      disposed = true;
    };
  }, [atlasUrl]);

  // When no atlas is available, render a fallback breathing sprite.
  if (atlasUrl === null || atlasUrl.length === 0 || atlasUnavailable) {
    return (
      <div
        className="pet-sprite pet-sprite-fallback"
        ref={elementRef}
        style={{ width: size, height: size, opacity: crossfade ? 0.4 : 1 }}
      >
        {spriteUrl !== null && spriteUrl.length > 0 ? (
          <img
            alt={displayName}
            className="pet-sprite-img"
            draggable={false}
            height={size}
            src={spriteUrl}
            width={size}
          />
        ) : (
          <span className="pet-sprite-placeholder">{displayName.slice(0, 2)}</span>
        )}
      </div>
    );
  }

  // Atlas-based animation: use CSS background-position stepping.
  const rowOffset = spec.row * ATLAS_CELL_HEIGHT * scale;
  // The animation shifts background-position-x from 0 to -(frames * cellWidth).
  const animationName = `pet-atlas-${state}`;

  return (
    <div
      className={`pet-sprite pet-sprite-atlas is-${state}`}
      ref={elementRef}
      style={{
        width: size,
        height: size,
        opacity: crossfade ? 0.4 : 1,
        backgroundImage: `url(${atlasUrl})`,
        backgroundPositionY: `-${rowOffset}px`,
        // Background geometry must scale with the viewport sprite.  Using the
        // atlas's native 192px cell in a 54px container clips a tiny, often
        // transparent corner of each frame.
        backgroundSize: `${ATLAS_COLUMNS * ATLAS_CELL_WIDTH * scale}px auto`,
        // CSS custom properties consumed by the keyframes in pet-companion.css
        ["--pet-frames" as string]: spec.frames,
        ["--pet-cell-w" as string]: `${ATLAS_CELL_WIDTH * scale}px`,
        ["--pet-duration" as string]: `${totalDuration}s`,
        ["--pet-iteration" as string]: isNonLooping ? "1" : "infinite",
        animationName,
        animationDuration: `${totalDuration}s`,
        animationTimingFunction: `steps(${spec.frames})`,
        animationIterationCount: isNonLooping ? "1" : "infinite",
      }}
    />
  );
}
