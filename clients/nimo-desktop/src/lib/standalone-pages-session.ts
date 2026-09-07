/**
 * Standalone pages shared session logic (relationship network, subplot manager,
 * character bible editor, token analytics).
 *
 * Pure data-layer functions extracted from the page components so they can be
 * unit-tested without a DOM environment.
 */

// ── Relationship Network ─────────────────────────────────────────────────

export interface RelationshipEdge {
  readonly source: string;
  readonly target: string;
  readonly type: string;
  readonly description: string;
}

export interface FilterOption {
  readonly id: string;
  readonly label: string;
  readonly types: readonly string[];
}

export const RELATIONSHIP_FILTER_OPTIONS: readonly FilterOption[] = [
  { id: "all", label: "全部", types: [] },
  { id: "adversary", label: "对抗", types: ["antagonist", "relationship"] },
  { id: "ally", label: "同盟", types: ["ally", "supporting"] },
  { id: "family", label: "亲属", types: ["family"] },
  { id: "mentor", label: "师徒", types: ["mentor"] },
  { id: "secret", label: "隐秘", types: ["secret"] },
];

export function filterEdgesByType(
  edges: readonly RelationshipEdge[],
  filterId: string,
): readonly RelationshipEdge[] {
  if (filterId === "all") return edges;
  const filter = RELATIONSHIP_FILTER_OPTIONS.find((f) => f.id === filterId);
  if (!filter) return edges;
  return edges.filter((edge) => filter.types.includes(edge.type));
}

// ── Subplot Manager ──────────────────────────────────────────────────────

export interface Subplot {
  readonly id: string;
  readonly name: string;
  readonly priority: "primary" | "normal" | "background";
  readonly startChapter: number;
  readonly endChapter: number;
}

export function sortSubplotsByChapter(subplots: readonly Subplot[]): readonly Subplot[] {
  return [...subplots].sort((a, b) => a.startChapter - b.startChapter);
}

export function convertArcToSubplot(
  characterName: string,
  arcType: string,
  milestones: readonly { chapter: number; description: string }[],
): Omit<Subplot, "id"> {
  return {
    name: `${characterName}的${arcType}`,
    priority: "normal",
    startChapter: milestones[0]?.chapter ?? 1,
    endChapter: milestones[milestones.length - 1]?.chapter ?? 10,
  };
}

// ── Token Analytics ──────────────────────────────────────────────────────

export const CURRENCY_SYMBOLS: Record<string, string> = {
  CNY: "¥",
  USD: "$",
  EUR: "€",
  GBP: "£",
  JPY: "¥",
};

export const EXCHANGE_RATES: Record<string, number> = {
  CNY: 1.0,
  USD: 0.14,
  EUR: 0.13,
  GBP: 0.11,
  JPY: 21.0,
};

export function convertCurrency(cnyAmount: number, currency: string): number {
  const rate = EXCHANGE_RATES[currency] ?? 1;
  return cnyAmount * rate;
}

export function formatCurrency(cnyAmount: number, currency: string): string {
  const symbol = CURRENCY_SYMBOLS[currency] ?? "¥";
  const converted = convertCurrency(cnyAmount, currency);
  return `${symbol}${converted.toFixed(2)}`;
}

export interface StepCost {
  readonly step: string;
  readonly kind: string;
  readonly costCny: number;
}

export interface WaterfallEntry extends StepCost {
  readonly percentage: number;
  readonly cumulativePercentage: number;
}

export function computeWaterfall(steps: readonly StepCost[]): readonly WaterfallEntry[] {
  const sorted = [...steps].sort((a, b) => a.costCny - b.costCny);
  const total = sorted.reduce((sum, s) => sum + s.costCny, 0);
  if (total === 0) return [];
  let cumulative = 0;
  return sorted.map((step) => {
    cumulative += step.costCny;
    return {
      ...step,
      percentage: (step.costCny / total) * 100,
      cumulativePercentage: (cumulative / total) * 100,
    };
  });
}

export function filterStepsByKind(
  steps: readonly StepCost[],
  kindFilter: string,
): readonly StepCost[] {
  if (kindFilter === "all") return steps;
  return steps.filter((s) => s.kind === kindFilter);
}

// ── Character Bible ──────────────────────────────────────────────────────

export function buildRelationshipMatrix(
  characterIds: readonly string[],
  relationships: readonly { source: string; target: string; type: string }[],
): Record<string, Record<string, string>> {
  const matrix: Record<string, Record<string, string>> = {};
  for (const id of characterIds) {
    matrix[id] = {};
  }
  for (const rel of relationships) {
    const row = matrix[rel.source];
    if (row) {
      row[rel.target] = rel.type;
    }
  }
  return matrix;
}
