import { describe, expect, it } from "vitest";

import type { CharacterProfileView } from "@nimo/engine-contracts";

import { chapterToTimelineX, layoutRelationshipNodes, timelineChapterWidth, timelineContentHeight, timelineLaneGap, timelineLaneStartY, timelineTickChapters } from "./narrative-visualization-layout";

const characters: readonly CharacterProfileView[] = [
  { id: "protagonist", name: "主角", role: "主视角", statusLabel: "活跃", summary: "", arc: "" },
  { id: "ally", name: "同伴", role: "配角", statusLabel: "活跃", summary: "", arc: "" },
  { id: "opponent", name: "对手", role: "对手", statusLabel: "活跃", summary: "", arc: "" },
];

describe("narrative visualization layout", () => {
  it("keeps the protagonist centered with the source-sized lead radius", () => {
    const first = layoutRelationshipNodes(characters);
    expect(first).toEqual(layoutRelationshipNodes(characters));
    expect(first.find((node) => node.characterId === "protagonist")).toMatchObject({ x: 460, y: 270 });
    expect(first.find((node) => node.characterId === "protagonist")?.radius).toBeCloseTo(36.45);
    expect(first.find((node) => node.characterId === "ally")).toMatchObject({ x: 460, y: 86 });
    expect(first.find((node) => node.characterId === "ally")?.radius).toBeCloseTo(27);
    expect(first.find((node) => node.characterId === "opponent")).toMatchObject({ x: 460, y: 454 });
    expect(first.find((node) => node.characterId === "opponent")?.radius).toBeCloseTo(27);
  });

  it("uses the source widget's paired-lead core geometry", () => {
    const paired = layoutRelationshipNodes([
      characters[0]!,
      { ...characters[1]!, id: "second-lead", role: "第二主角" },
      characters[2]!,
    ]);

    expect(paired.find((node) => node.characterId === "protagonist")).toMatchObject({ x: 403.3, y: 270 });
    expect(paired.find((node) => node.characterId === "protagonist")?.radius).toBeCloseTo(31.86);
    expect(paired.find((node) => node.characterId === "second-lead")).toMatchObject({ x: 516.7, y: 270 });
    expect(paired.find((node) => node.characterId === "second-lead")?.radius).toBeCloseTo(31.86);
    expect(paired.find((node) => node.characterId === "opponent")).toMatchObject({ x: 460, y: 86 });
    expect(paired.find((node) => node.characterId === "opponent")?.radius).toBeCloseTo(27);
  });

  it("keeps every node core inside a compact graph viewport", () => {
    const compact = layoutRelationshipNodes(characters, { width: 340, height: 220 });

    for (const node of compact) {
      expect(node.x - node.radius).toBeGreaterThanOrEqual(0);
      expect(node.x + node.radius).toBeLessThanOrEqual(340);
      expect(node.y - node.radius).toBeGreaterThanOrEqual(0);
      expect(node.y + node.radius).toBeLessThanOrEqual(220);
    }
  });

  it("scales compact graph nodes with the active body font", () => {
    const smallType = layoutRelationshipNodes(characters, { width: 340, height: 220, fontSize: 10 });
    const largeType = layoutRelationshipNodes(characters, { width: 340, height: 220, fontSize: 16 });

    expect(largeType[0]!.radius).toBeGreaterThan(smallType[0]!.radius);
    expect(largeType.find((node) => node.characterId === "ally")!.radius).toBeGreaterThan(
      smallType.find((node) => node.characterId === "ally")!.radius,
    );
  });

  it("maps chapter centres into the compact canvas safe area", () => {
    expect(timelineChapterWidth(24)).toBeCloseTo(47.1667);
    expect(chapterToTimelineX(1, 24)).toBeCloseTo(211.5833);
    expect(chapterToTimelineX(24, 24)).toBeCloseTo(1296.4167);
    expect(timelineTickChapters(24)).toEqual([1, 3, 6, 9, 12, 15, 18, 21, 24]);
  });

  it("keeps compact lanes visible without reserving an unused final gap", () => {
    // Few lanes keep a compact, readable canvas floor.
    expect(timelineContentHeight(0)).toBe(440);
    expect(timelineContentHeight(3)).toBe(440);
    // Many lanes expand the canvas so every lane stays visible.
    expect(timelineContentHeight(6)).toBe(timelineLaneStartY + 5 * timelineLaneGap + 30);
    expect(timelineContentHeight(6)).toBeGreaterThan(440);
    // Character arcs share the same compact lane rhythm and retain bottom air.
    expect(timelineContentHeight(2, 6)).toBe(620);
    // Negative counts are clamped to the empty-lane floor.
    expect(timelineContentHeight(-2)).toBe(440);
  });
});
