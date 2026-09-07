import type {
  FilmGraphDefinitionView,
  FilmGraphNodeView,
  FilmNodePromptView,
  FilmPromptSectionView,
  FilmStageId,
  FilmStageStatus,
} from "@nimo/engine-contracts";

export const H3_DURATION_OPTIONS: readonly number[] = Array.from(
  { length: 12 },
  (_, index) => index + 4,
);

export const FILM_GRAPH_STAGE_ORDER: readonly FilmStageId[] = [
  "planning",
  "screenplay",
  "visual_development",
  "storyboard",
  "shot_production",
  "sound_picture",
  "edit",
  "compliance",
  "delivery",
];

export const FILM_GRAPH_NODE_WIDTH = 232;
export const FILM_GRAPH_GRID_SIZE = 20;
const FILM_GRAPH_COLUMN_GAP = 108;
const FILM_GRAPH_ROW_GAP = 44;
const FILM_GRAPH_ORIGIN = 60;

export function filmGraphNodeHeight(node: FilmGraphNodeView): number {
  const portRows = Math.max(1, node.inputPorts.length, node.outputPorts.length);
  return 88 + portRows * 18 + (node.providerId ? 20 : 0);
}

function stageRank(stage: FilmStageId): number {
  const index = FILM_GRAPH_STAGE_ORDER.indexOf(stage);
  return index < 0 ? FILM_GRAPH_STAGE_ORDER.length : index;
}

/**
 * ComfyUI 风格的有向无环图整理：拓扑深度决定主列，制作阶段作为最小列约束，
 * 同列按真实端口高度垂直堆叠并居中，保证卡片之间不会互相覆盖。
 */
export function autoLayoutFilmGraph(graph: FilmGraphDefinitionView): FilmGraphDefinitionView {
  const byId = new Map(graph.nodes.map((node) => [node.nodeId, node]));
  const incoming = new Map(graph.nodes.map((node) => [node.nodeId, 0]));
  const outgoing = new Map(graph.nodes.map((node) => [node.nodeId, [] as string[]]));
  const parents = new Map(graph.nodes.map((node) => [node.nodeId, [] as string[]]));

  for (const edge of graph.edges) {
    if (!byId.has(edge.sourceNodeId) || !byId.has(edge.targetNodeId)) continue;
    outgoing.get(edge.sourceNodeId)?.push(edge.targetNodeId);
    parents.get(edge.targetNodeId)?.push(edge.sourceNodeId);
    incoming.set(edge.targetNodeId, (incoming.get(edge.targetNodeId) ?? 0) + 1);
  }

  const queue = graph.nodes
    .filter((node) => (incoming.get(node.nodeId) ?? 0) === 0)
    .sort((left, right) => stageRank(left.stage) - stageRank(right.stage));
  const rank = new Map<string, number>();
  const visited = new Set<string>();

  while (queue.length > 0) {
    const node = queue.shift();
    if (node === undefined) break;
    visited.add(node.nodeId);
    const parentRank = (parents.get(node.nodeId) ?? []).reduce(
      (current, parentId) => Math.max(current, (rank.get(parentId) ?? -1) + 1),
      0,
    );
    rank.set(node.nodeId, Math.max(stageRank(node.stage), parentRank));
    for (const childId of outgoing.get(node.nodeId) ?? []) {
      const remaining = (incoming.get(childId) ?? 1) - 1;
      incoming.set(childId, remaining);
      if (remaining === 0) {
        const child = byId.get(childId);
        if (child !== undefined) queue.push(child);
      }
    }
    queue.sort((left, right) => stageRank(left.stage) - stageRank(right.stage));
  }

  // 已损坏的循环图仍然可整理：把循环成员放回其制作阶段列，等待校验器提示修复。
  for (const node of graph.nodes) {
    if (!visited.has(node.nodeId)) rank.set(node.nodeId, stageRank(node.stage));
  }

  const columns = new Map<number, FilmGraphNodeView[]>();
  for (const node of graph.nodes) {
    const column = rank.get(node.nodeId) ?? stageRank(node.stage);
    columns.set(column, [...(columns.get(column) ?? []), node]);
  }
  for (const nodes of columns.values()) {
    nodes.sort((left, right) => {
      const stageDelta = stageRank(left.stage) - stageRank(right.stage);
      return stageDelta !== 0 ? stageDelta : left.label.localeCompare(right.label, "zh-CN");
    });
  }

  const columnHeight = (nodes: readonly FilmGraphNodeView[]) => nodes.reduce(
    (total, node, index) => total + filmGraphNodeHeight(node) + (index === 0 ? 0 : FILM_GRAPH_ROW_GAP),
    0,
  );
  const tallestColumn = Math.max(0, ...[...columns.values()].map(columnHeight));
  const positions = new Map<string, { readonly x: number; readonly y: number }>();

  for (const [column, nodes] of columns) {
    let y = FILM_GRAPH_ORIGIN + (tallestColumn - columnHeight(nodes)) / 2;
    for (const node of nodes) {
      positions.set(node.nodeId, {
        x: Math.round((FILM_GRAPH_ORIGIN + column * (FILM_GRAPH_NODE_WIDTH + FILM_GRAPH_COLUMN_GAP)) / FILM_GRAPH_GRID_SIZE) * FILM_GRAPH_GRID_SIZE,
        y: Math.round(y / FILM_GRAPH_GRID_SIZE) * FILM_GRAPH_GRID_SIZE,
      });
      y += filmGraphNodeHeight(node) + FILM_GRAPH_ROW_GAP;
    }
  }

  return {
    ...graph,
    nodes: graph.nodes.map((node) => ({
      ...node,
      position: positions.get(node.nodeId) ?? node.position,
    })),
  };
}

/** Grid snap plus nearby-node alignment. The nearest guide within the threshold wins. */
export function magnetizeFilmNodePosition(
  graph: FilmGraphDefinitionView,
  nodeId: string,
  position: { readonly x: number; readonly y: number },
  threshold = 12,
): { readonly x: number; readonly y: number } {
  let x = Math.round(position.x / FILM_GRAPH_GRID_SIZE) * FILM_GRAPH_GRID_SIZE;
  let y = Math.round(position.y / FILM_GRAPH_GRID_SIZE) * FILM_GRAPH_GRID_SIZE;
  let xDistance = threshold + 1;
  let yDistance = threshold + 1;

  for (const node of graph.nodes) {
    if (node.nodeId === nodeId) continue;
    const nextXDistance = Math.abs(position.x - node.position.x);
    const nextYDistance = Math.abs(position.y - node.position.y);
    if (nextXDistance <= threshold && nextXDistance < xDistance) {
      x = node.position.x;
      xDistance = nextXDistance;
    }
    if (nextYDistance <= threshold && nextYDistance < yDistance) {
      y = node.position.y;
      yDistance = nextYDistance;
    }
  }
  return { x, y };
}

export function filmStageClassName(selected: boolean, runtimeStatus: FilmStageStatus): string {
  return `film-stage ${selected ? "is-selected" : ""} is-runtime-${runtimeStatus}`;
}

export function filmExpertWorkbenchClassName(options: {
  readonly catalogOpen: boolean;
  readonly focusMode: boolean;
  readonly inspectorOpen: boolean;
  readonly queueOpen: boolean;
}): string {
  return [
    "film-expert-workbench",
    options.queueOpen && !options.focusMode ? "has-queue" : "",
    options.catalogOpen && !options.focusMode ? "" : "is-catalog-collapsed",
    options.inspectorOpen && !options.focusMode ? "" : "is-inspector-collapsed",
    options.focusMode ? "is-focus-mode" : "",
  ].filter(Boolean).join(" ");
}

export function renderFilmNodePrompt(prompt: FilmNodePromptView): string {
  const sections = prompt.sections
    .filter((section) => section.content.trim().length > 0)
    .map((section) => `【${section.label}】\n${section.content.trim()}`);
  if (prompt.userNotes.trim().length > 0) {
    sections.push(`【用户补充】\n${prompt.userNotes.trim()}`);
  }
  return sections.join("\n\n");
}

export function replaceFilmPromptSection(
  prompt: FilmNodePromptView,
  section: FilmPromptSectionView,
): FilmNodePromptView {
  return {
    ...prompt,
    sections: prompt.sections.map((item) => item.sectionId === section.sectionId ? section : item),
    updatedAt: new Date().toISOString(),
  };
}

export function canConnectFilmPorts(
  graph: FilmGraphDefinitionView,
  connection: {
    readonly source: string | null;
    readonly sourceHandle?: string | null;
    readonly target: string | null;
    readonly targetHandle?: string | null;
  },
): {
  readonly valid: boolean;
  readonly code: "ok" | "missingPort" | "self" | "duplicate" | "typeMismatch" | "inputOccupied" | "stageRegression" | "cycle";
  readonly message: string;
  readonly params?: Readonly<Record<string, string>>;
} {
  const source = graph.nodes.find((node) => node.nodeId === connection.source);
  const target = graph.nodes.find((node) => node.nodeId === connection.target);
  const output = source?.outputPorts.find((port) => port.portId === connection.sourceHandle);
  const input = target?.inputPorts.find((port) => port.portId === connection.targetHandle);
  if (source === undefined || target === undefined || output === undefined || input === undefined) {
    return { valid: false, code: "missingPort", message: "连接端口不存在，请刷新节点定义后重试。" };
  }
  if (source.nodeId === target.nodeId) return { valid: false, code: "self", message: "节点不能连接到自身。" };
  const duplicate = graph.edges.some((edge) => (
    edge.sourceNodeId === source.nodeId
    && edge.sourcePortId === output.portId
    && edge.targetNodeId === target.nodeId
    && edge.targetPortId === input.portId
  ));
  if (duplicate) return { valid: false, code: "duplicate", message: "该端口连接已经存在。" };
  if (output.artifactType !== input.artifactType) {
    return {
      valid: false,
      code: "typeMismatch",
      message: `端口类型不兼容：${output.artifactType} 不能连接到 ${input.artifactType}。`,
      params: { sourceType: output.artifactType, targetType: input.artifactType },
    };
  }
  if (!input.multiple && graph.edges.some((edge) => (
    edge.targetNodeId === target.nodeId && edge.targetPortId === input.portId
  ))) {
    return {
      valid: false,
      code: "inputOccupied",
      message: `输入端口只能连接一次：${input.label}。`,
      params: { port: input.label },
    };
  }
  if (stageRank(source.stage) > stageRank(target.stage)) {
    return {
      valid: false,
      code: "stageRegression",
      message: `制作流程不能从 ${source.stage} 回连到 ${target.stage}。`,
      params: { sourceStage: source.stage, targetStage: target.stage },
    };
  }

  const outgoing = new Map<string, string[]>();
  for (const edge of graph.edges) {
    outgoing.set(edge.sourceNodeId, [...(outgoing.get(edge.sourceNodeId) ?? []), edge.targetNodeId]);
  }
  const pending = [target.nodeId];
  const visited = new Set<string>();
  while (pending.length > 0) {
    const current = pending.pop();
    if (current === undefined || visited.has(current)) continue;
    if (current === source.nodeId) {
      return { valid: false, code: "cycle", message: "该连接会形成循环依赖。" };
    }
    visited.add(current);
    pending.push(...(outgoing.get(current) ?? []));
  }

  return { valid: true, code: "ok", message: "" };
}
