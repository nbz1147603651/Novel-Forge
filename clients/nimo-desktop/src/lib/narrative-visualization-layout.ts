import type { CharacterProfileView } from "@nimo/engine-contracts";

export interface VisualizationPoint {
  readonly x: number;
  readonly y: number;
}

export interface PositionedCharacter extends VisualizationPoint {
  readonly characterId: string;
  /** Source widget derives its hit target and visual circle from this value. */
  readonly radius: number;
}

/**
 * A deterministic radial graph layout matching CharacterGraphWidget.
 *
 * Lead roles occupy the core; remaining roles begin at 12 o'clock and orbit
 * clockwise.  The size-dependent radii are intentionally returned with the
 * positions: the source uses the same values for painting and hit testing,
 * rather than a fixed CSS circle size.
 */
export function layoutRelationshipNodes(
  characters: readonly CharacterProfileView[],
  size: Readonly<{ fontSize?: number; height: number; width: number }> = { width: 920, height: 540 },
): readonly PositionedCharacter[] {
  if (characters.length === 0) return [];
  const graphCenter: VisualizationPoint = { x: size.width / 2, y: size.height / 2 };
  const minEdge = Math.min(size.width, size.height);
  const core = characters.filter((character) => character.role.includes("主视角") || character.role.includes("主角") || character.role.includes("第二主角"));
  const centerCharacters = core.length > 0 ? core : [characters[0]!];
  // Preserve roster order. It is the PySide6 source's stable orbit order and
  // avoids an unnecessary force-layout dependency or layout jitter.
  const outer = characters.filter((character) => !centerCharacters.some((candidate) => candidate.id === character.id));
  // The source widget scales from its canvas. The web reader also has a live
  // typography scale, so node geometry follows the current body font instead
  // of making circles visually dominate a compact type ramp.
  const fontSize = Math.max(9, size.fontSize ?? 12);
  const baseRadius = Math.max(18, fontSize * 2, minEdge * 0.05);
  const centerRadius = baseRadius * (centerCharacters.length <= 1 ? 1.35 : 1.18);
  const orbitRadius = Math.min(minEdge * 0.34, Math.max(0, minEdge / 2 - baseRadius * 1.55));
  const startAngle = -Math.PI / 2;
  const positions: PositionedCharacter[] = [];

  if (centerCharacters.length === 1) {
    positions.push({ characterId: centerCharacters[0]!.id, ...graphCenter, radius: centerRadius });
  } else if (centerCharacters.length === 2) {
    const offset = minEdge * 0.105;
    positions.push(
      { characterId: centerCharacters[0]!.id, x: graphCenter.x - offset, y: graphCenter.y, radius: centerRadius },
      { characterId: centerCharacters[1]!.id, x: graphCenter.x + offset, y: graphCenter.y, radius: centerRadius },
    );
  } else {
    const coreDistance = minEdge * 0.12;
    centerCharacters.forEach((character, index) => {
      const angle = startAngle + (2 * Math.PI * index) / centerCharacters.length;
      positions.push({
        characterId: character.id,
        x: Math.round(graphCenter.x + Math.cos(angle) * coreDistance),
        y: Math.round(graphCenter.y + Math.sin(angle) * coreDistance),
        radius: centerRadius,
      });
    });
  }

  outer.forEach((character, index) => {
    const angle = startAngle + (2 * Math.PI * index) / outer.length;
    positions.push({
      characterId: character.id,
      x: Math.round(graphCenter.x + Math.cos(angle) * orbitRadius),
      y: Math.round(graphCenter.y + Math.sin(angle) * orbitRadius),
      radius: baseRadius,
    });
  });
  return positions;
}

/** Convert a one-based chapter number into a stable timeline x coordinate. */
export const timelineHorizontalLayout = {
  labelX: 174,
  left: 188,
  right: 1320,
  sectionLabelX: 24,
} as const;

export function timelineChapterWidth(
  totalChapters: number,
  left = timelineHorizontalLayout.left,
  right = timelineHorizontalLayout.right,
): number {
  const safeTotal = Math.max(2, totalChapters);
  return (right - left) / safeTotal;
}

/**
 * Match the source QWidget's chapter grid: each one-based chapter maps to
 * the centre of its equal-width cell, leaving a half-cell of air at both
 * extremes of the narrative track.
 */
export function chapterToTimelineX(
  chapter: number,
  totalChapters: number,
  left = timelineHorizontalLayout.left,
  right = timelineHorizontalLayout.right,
): number {
  const safeTotal = Math.max(2, totalChapters);
  const clamped = Math.min(safeTotal, Math.max(1, chapter));
  return left + (clamped - 0.5) * timelineChapterWidth(safeTotal, left, right);
}

export function timelineTickChapters(totalChapters: number): readonly number[] {
  const safeTotal = Math.max(1, totalChapters);
  const step = safeTotal <= 12 ? 1 : safeTotal <= 30 ? 3 : 5;
  return Array.from({ length: safeTotal }, (_, index) => index + 1)
    .filter((chapter) => chapter === 1 || chapter === safeTotal || chapter % step === 0);
}

/** Compact timeline geometry shared by renderer + tests. */
export const timelineVerticalLayout = {
  feedbackBusY: 244,
  gridTopY: 34,
  mainlineLabelY: 146,
  mainlineSectionLabelY: 128,
  milestoneIndexOffset: 32,
  milestoneY: 168,
  phaseHeight: 54,
  phaseSummaryY: 72,
  phaseTextY: 52,
  phaseTopY: 34,
  subplotSectionLabelY: 220,
  tickLabelY: 100,
} as const;

/** Small canvas-local metadata, so the reader does not need a separate header. */
export const timelineChapterBadgeLayout = {
  height: 22,
  topY: 5,
  width: 96,
} as const;

export const timelineLaneStartY = 278;
export const timelineLaneGap = 46;
/** Character-arc lanes sit below the subplot lanes (PySide6 角色弧光 section). */
export const timelineArcGap = 38;
const timelineArcSectionGap = 30;
const timelineMinHeight = 440;
const timelineBottomPadding = 30;

/** First character-arc lane's centre Y, given the subplot lane count. */
export function timelineArcLaneStartY(subplotLaneCount: number): number {
  return timelineLaneStartY + Math.max(0, subplotLaneCount) * timelineLaneGap + timelineArcSectionGap;
}

/**
 * Compute the timeline's natural SVG height from its subplot + arc lane counts,
 * mirroring PySide6 `NarrativeBlueprintWidget.setMinimumHeight(y_cursor + 20)`:
 * the canvas grows with the number of lanes instead of clipping them against
 * a fixed viewBox.
 */
export function timelineContentHeight(subplotLaneCount: number, arcLaneCount = 0): number {
  const safeLanes = Math.max(0, subplotLaneCount);
  const safeArcs = Math.max(0, arcLaneCount);
  const subplotBottom = safeLanes > 0
    ? timelineLaneStartY + (safeLanes - 1) * timelineLaneGap
    : timelineVerticalLayout.feedbackBusY;
  const contentBottom = safeArcs > 0
    ? timelineArcLaneStartY(safeLanes) + (safeArcs - 1) * timelineArcGap
    : subplotBottom;
  return Math.max(timelineMinHeight, contentBottom + timelineBottomPadding);
}
