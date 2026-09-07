import { describe, expect, it } from "vitest";

import {
  buildRelationshipMatrix,
  computeWaterfall,
  convertCurrency,
  convertArcToSubplot,
  filterEdgesByType,
  filterStepsByKind,
  formatCurrency,
  sortSubplotsByChapter,
  type RelationshipEdge,
  type StepCost,
  type Subplot,
} from "./standalone-pages-session";

// ── Relationship Network ─────────────────────────────────────────────────

const EDGES: readonly RelationshipEdge[] = [
  { source: "c1", target: "c2", type: "family", description: "姐弟" },
  { source: "c1", target: "c3", type: "mentor", description: "师徒" },
  { source: "c1", target: "c4", type: "antagonist", description: "对抗" },
  { source: "c2", target: "c3", type: "ally", description: "同盟" },
];

describe("filterEdgesByType", () => {
  it("returns all edges for 'all' filter", () => {
    expect(filterEdgesByType(EDGES, "all")).toHaveLength(4);
  });

  it("filters by family type", () => {
    const result = filterEdgesByType(EDGES, "family");
    expect(result).toHaveLength(1);
    expect(result[0]!.type).toBe("family");
  });

  it("filters by adversary (antagonist + relationship)", () => {
    const result = filterEdgesByType(EDGES, "adversary");
    expect(result).toHaveLength(1);
    expect(result[0]!.type).toBe("antagonist");
  });

  it("returns all edges for unknown filter", () => {
    expect(filterEdgesByType(EDGES, "unknown")).toHaveLength(4);
  });
});

// ── Subplot Manager ──────────────────────────────────────────────────────

const SUBPLOTS: readonly Subplot[] = [
  { id: "sp2", name: "B", priority: "normal", startChapter: 5, endChapter: 18 },
  { id: "sp1", name: "A", priority: "primary", startChapter: 1, endChapter: 20 },
  { id: "sp3", name: "C", priority: "background", startChapter: 3, endChapter: 12 },
];

describe("sortSubplotsByChapter", () => {
  it("sorts by start chapter ascending", () => {
    const sorted = sortSubplotsByChapter(SUBPLOTS);
    expect(sorted.map((s) => s.id)).toEqual(["sp1", "sp3", "sp2"]);
  });

  it("does not mutate the original array", () => {
    sortSubplotsByChapter(SUBPLOTS);
    expect(SUBPLOTS[0]!.id).toBe("sp2");
  });
});

describe("convertArcToSubplot", () => {
  it("creates a subplot from arc milestones", () => {
    const result = convertArcToSubplot("陈半仙", "救赎弧光", [
      { chapter: 2, description: "登场" },
      { chapter: 16, description: "牺牲" },
    ]);
    expect(result.name).toBe("陈半仙的救赎弧光");
    expect(result.startChapter).toBe(2);
    expect(result.endChapter).toBe(16);
    expect(result.priority).toBe("normal");
  });

  it("defaults to 1-10 for empty milestones", () => {
    const result = convertArcToSubplot("X", "Y", []);
    expect(result.startChapter).toBe(1);
    expect(result.endChapter).toBe(10);
  });
});

// ── Token Analytics ──────────────────────────────────────────────────────

describe("convertCurrency", () => {
  it("returns same amount for CNY", () => {
    expect(convertCurrency(100, "CNY")).toBe(100);
  });

  it("converts to USD", () => {
    expect(convertCurrency(100, "USD")).toBeCloseTo(14);
  });

  it("defaults to rate 1 for unknown currency", () => {
    expect(convertCurrency(50, "XYZ")).toBe(50);
  });
});

describe("formatCurrency", () => {
  it("formats CNY with ¥ symbol", () => {
    expect(formatCurrency(136, "CNY")).toBe("¥136.00");
  });

  it("formats USD with $ symbol", () => {
    expect(formatCurrency(100, "USD")).toBe("$14.00");
  });
});

const STEPS: readonly StepCost[] = [
  { step: "DRAFT", kind: "chapter", costCny: 36 },
  { step: "INIT", kind: "init", costCny: 10 },
  { step: "REPAIR", kind: "repair", costCny: 9.6 },
];

describe("computeWaterfall", () => {
  it("sorts by cost and computes percentages", () => {
    const result = computeWaterfall(STEPS);
    expect(result).toHaveLength(3);
    // Sorted ascending: REPAIR(9.6) < INIT(10) < DRAFT(36)
    expect(result[0]!.step).toBe("REPAIR");
    expect(result[2]!.step).toBe("DRAFT");
    // Last entry cumulative should be 100%
    expect(result[2]!.cumulativePercentage).toBeCloseTo(100);
  });

  it("returns empty for zero total", () => {
    expect(computeWaterfall([])).toEqual([]);
  });
});

describe("filterStepsByKind", () => {
  it("returns all for 'all'", () => {
    expect(filterStepsByKind(STEPS, "all")).toHaveLength(3);
  });

  it("filters by kind", () => {
    expect(filterStepsByKind(STEPS, "chapter")).toHaveLength(1);
    expect(filterStepsByKind(STEPS, "init")).toHaveLength(1);
  });
});

// ── Character Bible ──────────────────────────────────────────────────────

describe("buildRelationshipMatrix", () => {
  it("builds a matrix from character ids and relationships", () => {
    const matrix = buildRelationshipMatrix(
      ["c1", "c2", "c3"],
      [
        { source: "c1", target: "c2", type: "family" },
        { source: "c1", target: "c3", type: "mentor" },
      ],
    );
    expect(matrix["c1"]!["c2"]).toBe("family");
    expect(matrix["c1"]!["c3"]).toBe("mentor");
    expect(matrix["c2"]!["c1"]).toBeUndefined();
  });

  it("ignores relationships with unknown source", () => {
    const matrix = buildRelationshipMatrix(
      ["c1"],
      [{ source: "c99", target: "c1", type: "ally" }],
    );
    expect(matrix["c1"]).toEqual({});
  });
});
