import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type OnNodeDrag,
  type NodeProps,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { memo, useCallback, useEffect, useMemo, useState } from "react";

import type {
  FilmGraphDefinitionView,
  FilmGraphNodeView,
  FilmGraphRunView,
  FilmNodeAttemptView,
  FilmNodeDefinitionView,
  FilmStageId,
  FilmStageStatus,
} from "@nimo/engine-contracts";

import { useT } from "../lib/i18n";
import {
  canConnectFilmPorts,
  FILM_GRAPH_GRID_SIZE,
  FILM_GRAPH_NODE_WIDTH,
  filmGraphNodeHeight,
  magnetizeFilmNodePosition,
} from "./film-workflow-utils";

/** run-plan 节点契约视图（FilmStudioView.runPlan 元素，结构对齐 engine-contracts）。 */
export interface RunPlanNodeView {
  readonly nodeId: string;
  readonly label: string;
  readonly stage: FilmStageId;
  readonly dependsOn: readonly string[];
  readonly artifactInputs: readonly string[];
  readonly artifactOutputs: readonly string[];
  readonly providerId: string;
  readonly modelId: string;
  readonly status: FilmStageStatus;
  readonly humanCheckpoint: boolean;
  readonly retryLimit: number;
  readonly estimatedCostUsd: number;
  readonly notes: string;
}

/** 泳道列序：与 FilmStudioPage stageMeta 一致；未知 stage 落入末列之后。 */
const STAGE_LANES: readonly FilmStageId[] = [
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

const NODE_WIDTH = 176;
const NODE_HEIGHT = 88;
const LANE_GAP_X = 64;
const ROW_GAP_Y = 24;

/** Left / middle / right pointer buttons may all pan from the empty canvas. */
export const FILM_CANVAS_PAN_MOUSE_BUTTONS: number[] = [0, 1, 2];

export interface RunPlanGraph {
  readonly nodes: Node[];
  readonly edges: Edge[];
}

/**
 * 泳道布局纯函数：x = stage 列序，y = 该 stage 内节点序号；edges 由 dependsOn
 * 生成（缺失依赖自动跳过，对应后端 topological_order 的容错语义）。不依赖 DOM，
 * 可在 node 环境直接单测。
 */
export function layoutRunPlan(runPlan: readonly RunPlanNodeView[]): RunPlanGraph {
  const laneOf = (stage: string): number => {
    const index = STAGE_LANES.indexOf(stage as FilmStageId);
    return index >= 0 ? index : STAGE_LANES.length;
  };
  const laneCounters = new Map<number, number>();
  const nodes: Node[] = runPlan.map((item) => {
    const lane = laneOf(item.stage);
    const row = laneCounters.get(lane) ?? 0;
    laneCounters.set(lane, row + 1);
    return {
      id: item.nodeId,
      type: "runPlan",
      position: {
        x: lane * (NODE_WIDTH + LANE_GAP_X),
        y: row * (NODE_HEIGHT + ROW_GAP_Y),
      },
      data: { node: item },
    };
  });
  const known = new Set(runPlan.map((item) => item.nodeId));
  const edges: Edge[] = [];
  for (const item of runPlan) {
    for (const dep of item.dependsOn) {
      if (!known.has(dep)) continue;
      edges.push({
        id: `${dep}->${item.nodeId}`,
        source: dep,
        target: item.nodeId,
        animated: item.status === "active",
        className: `is-${item.status}`,
      });
    }
  }
  return { nodes, edges };
}

function statusLabel(status: string, t: ReturnType<typeof useT>): string {
  const translated = t(`film.status.${status}`);
  return translated === `film.status.${status}` ? status : translated;
}

interface RunPlanNodeData extends Record<string, unknown> {
  readonly node: RunPlanNodeView;
  readonly busy?: boolean;
  readonly onRerun?: (nodeId: string) => void;
}

/** 画布节点卡片：status 左边框着色 + 状态点 + 重跑按钮（stopPropagation 防误触拖拽）。 */
const RunPlanNodeCard = memo(function RunPlanNodeCard({ data }: NodeProps) {
  const t = useT();
  const { node, busy = false, onRerun } = data as RunPlanNodeData;
  const rerunable = node.status !== "completed" && node.status !== "blocked";
  return (
    <div className={`film-run-node is-${node.status}`}>
      <header>
        <b>{statusLabel(node.status, t)}</b>
        <i className={`is-${node.status}`} />
        <small>{node.stage}</small>
      </header>
      <strong>{node.label}</strong>
      <footer>
        <span>{t("film.canvas.retryCost", { count: node.retryLimit, cost: node.estimatedCostUsd.toFixed(3) })}</span>
        {rerunable && onRerun ? (
          <button
            disabled={busy}
            onClick={(event) => {
              event.stopPropagation();
              onRerun(node.nodeId);
            }}
            onMouseDown={(event) => event.stopPropagation()}
            type="button"
          >
            {t(busy ? "film.canvas.rerunning" : "film.canvas.rerun")}
          </button>
        ) : null}
      </footer>
    </div>
  );
});

const NODE_TYPES = { runPlan: RunPlanNodeCard } as const;

const LEGEND: readonly FilmStageStatus[] = ["completed", "active", "ready", "review", "blocked", "pending"];

/** fitView / 「重置视图」按钮需要位于 ReactFlowProvider 内部才能访问实例。 */
function CanvasBody({ edges, nodes }: { readonly edges: Edge[]; readonly nodes: Node[] }) {
  const t = useT();
  const flow = useReactFlow();
  const [interactiveNodes, setInteractiveNodes] = useState(nodes);
  useEffect(() => {
    setInteractiveNodes((current) => nodes.map((next) => {
      const existing = current.find((item) => item.id === next.id);
      return existing === undefined ? next : { ...next, position: existing.position };
    }));
  }, [nodes]);
  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    setInteractiveNodes((current) => applyNodeChanges(changes, current));
  }, []);
  const fit = useCallback(() => {
    void flow.fitView({ duration: 350, maxZoom: 1, padding: 0.18 });
  }, [flow]);
  return (
    <>
      <ReactFlow
        defaultEdgeOptions={{ type: "smoothstep" }}
        edges={edges}
        fitView
        fitViewOptions={{ duration: 350, maxZoom: 1, padding: 0.18 }}
        maxZoom={1.6}
        minZoom={0.35}
        nodeTypes={NODE_TYPES}
        nodes={interactiveNodes}
        nodesDraggable
        onNodesChange={handleNodesChange}
        onlyRenderVisibleElements={nodes.length > 40}
        panActivationKeyCode="Space"
        panOnDrag={FILM_CANVAS_PAN_MOUSE_BUTTONS}
        proOptions={{ hideAttribution: false }}
        selectionOnDrag={false}
        zoomOnDoubleClick={false}
      >
        <Background color="var(--film-line-strong)" gap={18} size={1} />
        <Controls position="bottom-right" showInteractive={false} />
      </ReactFlow>
      <button className="film-canvas-reset" onClick={fit} type="button">
        {t("film.canvas.fitView")}
      </button>
    </>
  );
}

/**
 * ComfyUI 式执行画布：run-plan 节点以泳道布局呈现，支持缩放/平移/拖拽与单点重跑。
 * 节点位置不持久化——每次数据更新按布局函数复位并重新取景。
 */
export function FilmRunPlanCanvas({
  nodes: runPlan,
  onRerunNode,
}: {
  readonly nodes: readonly RunPlanNodeView[];
  readonly onRerunNode: (nodeId: string) => Promise<void>;
}) {
  const t = useT();
  const [busyNodeId, setBusyNodeId] = useState("");

  const handleRerun = useCallback(
    (nodeId: string) => {
      if (busyNodeId) return;
      setBusyNodeId(nodeId);
      void onRerunNode(nodeId).finally(() => setBusyNodeId(""));
    },
    [busyNodeId, onRerunNode],
  );

  const { nodes, edges } = useMemo(() => layoutRunPlan(runPlan), [runPlan]);
  const enrichedNodes = useMemo(
    () => nodes.map((node) => ({
      ...node,
      data: {
        ...(node.data as RunPlanNodeData),
        busy: node.id === busyNodeId,
        onRerun: handleRerun,
      },
    })),
    [nodes, busyNodeId, handleRerun],
  );
  const completed = runPlan.filter((item) => item.status === "completed").length;
  const ratio = runPlan.length === 0 ? 0 : Math.round((completed / runPlan.length) * 100);

  if (runPlan.length === 0) {
    return (
      <div className="film-run-canvas is-empty">
        <p className="film-empty-hint">{t("film.canvas.empty")}</p>
      </div>
    );
  }

  return (
    <div className="film-run-canvas">
      <header className="film-canvas-bar">
        <span>EXECUTION GRAPH</span>
        <b>
          {t("film.canvas.summary", { count: runPlan.length, ratio })}
        </b>
        <div className="film-canvas-legend">
          {LEGEND.map((status) => (
            <span key={status} title={statusLabel(status, t)}>
              <i className={`is-${status}`} />
              {statusLabel(status, t)}
            </span>
          ))}
        </div>
        <small className="film-canvas-pan-hint">{t("film.canvas.panHintWithSpace")}</small>
      </header>
      <div className="film-canvas-viewport">
        <ReactFlowProvider>
          <CanvasBody edges={edges} nodes={enrichedNodes} />
        </ReactFlowProvider>
      </div>
    </div>
  );
}

const ARTIFACT_COLORS: Readonly<Record<string, string>> = {
  CreativeBrief: "#c98b58",
  StyleLock: "#bf8cd8",
  CharacterAssetPack: "#77a8d5",
  SceneAssetPack: "#63ad91",
  PropAssetPack: "#d4a45f",
  ShotList: "#79a9c4",
  Storyboard: "#7894d2",
  H3PromptIR: "#de8d5d",
  VideoClip: "#e6a348",
  QCReport: "#8eb476",
  DeliveryPackage: "#b8b9b1",
};

function artifactColor(artifactType: string): string {
  return ARTIFACT_COLORS[artifactType] ?? "#8b9693";
}

function attemptLabel(attempt: FilmNodeAttemptView | undefined, t: ReturnType<typeof useT>): string {
  if (attempt === undefined) return t("film.canvas.notRun");
  const key = `film.status.${attempt.status}`;
  const translated = t(key);
  return translated === key ? attempt.status : translated;
}

function graphNodeLabel(
  node: FilmGraphNodeView,
  definition: FilmNodeDefinitionView | undefined,
  t: ReturnType<typeof useT>,
): string {
  const key = `film.node.${node.typeId}`;
  const translated = t(key);
  if (translated === key || (definition !== undefined && node.label !== definition.label)) return node.label;
  return translated;
}

function graphProviderLabel(providerId: string, t: ReturnType<typeof useT>): string {
  const key = `film.provider.${providerId}.short`;
  const translated = t(key);
  return translated === key ? providerId : translated;
}

interface GraphNodeData extends Record<string, unknown> {
  readonly node: FilmGraphNodeView;
  readonly definition?: FilmNodeDefinitionView;
  readonly attempt?: FilmNodeAttemptView;
}

const FilmGraphNodeCard = memo(function FilmGraphNodeCard({ data, selected }: NodeProps) {
  const t = useT();
  const { node, definition, attempt } = data as GraphNodeData;
  const paid = definition?.paid ?? node.estimatedCostUsd > 0;
  return (
    <article
      className={`film-graph-node is-${attempt?.status ?? "idle"} ${selected ? "is-selected" : ""} ${node.bypassed ? "is-bypassed" : ""}`}
      data-node-type={node.typeId}
    >
      <header>
        <span>{node.stage.replaceAll("_", " ")}</span>
        <div>{node.humanCheckpoint ? <i title={t("film.canvas.humanCheckpoint")}>{t("film.canvas.human")}</i> : null}{paid ? <i className="is-paid" title={t("film.canvas.paidNode")}>{t("film.canvas.paid")}</i> : null}</div>
      </header>
      <strong>{graphNodeLabel(node, definition, t)}</strong>
      {node.providerId ? <span className="film-graph-node-route"><b>{graphProviderLabel(node.providerId, t)}</b><em>{node.modelId || t("film.workbench.modelPending")}</em></span> : null}
      <small>{attemptLabel(attempt, t)}{node.bypassed ? ` · ${t("film.canvas.bypassed")}` : ""}</small>
      <div className="film-graph-port-grid">
        <div className="film-graph-ports is-input">
          {node.inputPorts.map((port) => (
            <div key={port.portId} title={`${port.label} · ${port.artifactType}`}>
              <Handle
                id={port.portId}
                position={Position.Left}
                style={{ background: artifactColor(port.artifactType) }}
                type="target"
              />
              <span>{port.label}</span>
            </div>
          ))}
        </div>
        <div className="film-graph-ports is-output">
          {node.outputPorts.map((port) => (
            <div key={port.portId} title={`${port.label} · ${port.artifactType}`}>
              <span>{port.label}</span>
              <Handle
                id={port.portId}
                position={Position.Right}
                style={{ background: artifactColor(port.artifactType) }}
                type="source"
              />
            </div>
          ))}
        </div>
      </div>
    </article>
  );
});

const GRAPH_NODE_TYPES = { filmGraph: FilmGraphNodeCard } as const;

function graphAttemptMap(run: FilmGraphRunView | null | undefined): ReadonlyMap<string, FilmNodeAttemptView> {
  const result = new Map<string, FilmNodeAttemptView>();
  for (const attempt of run?.attempts ?? []) result.set(attempt.nodeId, attempt);
  return result;
}

function toFlowGraph(
  graph: FilmGraphDefinitionView,
  catalog: readonly FilmNodeDefinitionView[],
  run: FilmGraphRunView | null | undefined,
  selectedNodeId: string,
): { readonly nodes: Node[]; readonly edges: Edge[] } {
  const definitions = new Map(catalog.map((item) => [item.typeId, item]));
  const attempts = graphAttemptMap(run);
  return {
    nodes: graph.nodes.map((node) => ({
      id: node.nodeId,
      type: "filmGraph",
      initialHeight: filmGraphNodeHeight(node),
      initialWidth: FILM_GRAPH_NODE_WIDTH,
      position: { ...node.position },
      selected: node.nodeId === selectedNodeId,
      data: { node, definition: definitions.get(node.typeId), attempt: attempts.get(node.nodeId) },
    })),
    edges: graph.edges.map((edge) => {
      const attempt = attempts.get(edge.targetNodeId);
      return {
        id: edge.edgeId,
        source: edge.sourceNodeId,
        sourceHandle: edge.sourcePortId,
        target: edge.targetNodeId,
        targetHandle: edge.targetPortId,
        type: "smoothstep",
        animated: attempt?.status === "running",
        className: `is-${attempt?.status ?? "idle"}`,
      };
    }),
  };
}

interface FilmGraphCanvasBodyProps {
  readonly catalog: readonly FilmNodeDefinitionView[];
  readonly graph: FilmGraphDefinitionView;
  readonly latestRun?: FilmGraphRunView | null;
  readonly selectedNodeId: string;
  readonly onGraphChange: (next: FilmGraphDefinitionView) => void;
  readonly onConnectionError: (message: string) => void;
  readonly onSelectedNodeChange: (nodeId: string) => void;
  readonly fitRequestId?: number;
  readonly snapEnabled?: boolean;
}

function FilmGraphCanvasBody({
  catalog,
  graph,
  latestRun,
  selectedNodeId,
  onGraphChange,
  onConnectionError,
  onSelectedNodeChange,
  fitRequestId = 0,
  snapEnabled = true,
}: FilmGraphCanvasBodyProps) {
  const t = useT();
  const flow = useReactFlow();
  const flowGraph = useMemo(
    () => toFlowGraph(graph, catalog, latestRun, selectedNodeId),
    [catalog, graph, latestRun, selectedNodeId],
  );
  const [interactiveNodes, setInteractiveNodes] = useState(flowGraph.nodes);
  useEffect(() => {
    setInteractiveNodes((current) => flowGraph.nodes.map((next) => {
      const existing = current.find((item) => item.id === next.id);
      if (existing === undefined) return next;
      return {
        ...existing,
        ...next,
        ...(existing.dragging === undefined ? {} : { dragging: existing.dragging }),
        ...(existing.height === undefined ? {} : { height: existing.height }),
        ...(existing.measured === undefined ? {} : { measured: existing.measured }),
        ...(existing.width === undefined ? {} : { width: existing.width }),
      };
    }));
  }, [flowGraph.nodes]);
  const fitGraph = useCallback(() => {
    if (graph.nodes.length === 0) return;
    const x = Math.min(...graph.nodes.map((node) => node.position.x));
    const y = Math.min(...graph.nodes.map((node) => node.position.y));
    const right = Math.max(...graph.nodes.map((node) => node.position.x + FILM_GRAPH_NODE_WIDTH));
    const bottom = Math.max(...graph.nodes.map((node) => node.position.y + filmGraphNodeHeight(node)));
    void flow.fitBounds({ x, y, width: right - x, height: bottom - y }, { duration: 280, padding: .12 });
  }, [flow, graph.nodes]);

  useEffect(() => {
    if (fitRequestId === 0) return;
    const frame = window.requestAnimationFrame(fitGraph);
    return () => window.cancelAnimationFrame(frame);
  }, [fitGraph, fitRequestId]);

  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    setInteractiveNodes((current) => applyNodeChanges(changes, current));
    const removed = new Set(
      changes.filter((change) => change.type === "remove").map((change) => change.id),
    );
    if (removed.size === 0) return;
    onGraphChange({
      ...graph,
      nodes: graph.nodes.filter((node) => !removed.has(node.nodeId)),
      edges: graph.edges.filter(
        (edge) => !removed.has(edge.sourceNodeId) && !removed.has(edge.targetNodeId),
      ),
    });
  }, [graph, onGraphChange]);

  const handleNodeDragStop = useCallback<OnNodeDrag>((_event, flowNode) => {
    const position = snapEnabled
      ? magnetizeFilmNodePosition(graph, flowNode.id, flowNode.position)
      : flowNode.position;
    setInteractiveNodes((current) => current.map((node) => (
      node.id === flowNode.id ? { ...node, position } : node
    )));
    const currentNode = graph.nodes.find((node) => node.nodeId === flowNode.id);
    if (
      currentNode === undefined
      || (currentNode.position.x === position.x && currentNode.position.y === position.y)
    ) return;
    onGraphChange({
      ...graph,
      nodes: graph.nodes.map((node) => (
        node.nodeId === flowNode.id ? { ...node, position } : node
      )),
    });
  }, [graph, onGraphChange, snapEnabled]);

  const handleEdgesChange = useCallback((changes: EdgeChange[]) => {
    const nextEdges = applyEdgeChanges(changes, flowGraph.edges);
    const remaining = new Set(nextEdges.map((edge) => edge.id));
    onGraphChange({ ...graph, edges: graph.edges.filter((edge) => remaining.has(edge.edgeId)) });
  }, [flowGraph.edges, graph, onGraphChange]);

  const handleConnect = useCallback((connection: Connection) => {
    const result = canConnectFilmPorts(graph, connection);
    if (!result.valid) {
      const key = `film.canvas.connection.${result.code}`;
      const translated = t(key, result.params);
      onConnectionError(translated === key ? result.message : translated);
      return;
    }
    const edgeId = `${connection.source}:${connection.sourceHandle}->${connection.target}:${connection.targetHandle}`;
    if (graph.edges.some((edge) => edge.edgeId === edgeId)) return;
    onGraphChange({
      ...graph,
      edges: [...graph.edges, {
        edgeId,
        sourceNodeId: connection.source ?? "",
        sourcePortId: connection.sourceHandle ?? "",
        targetNodeId: connection.target ?? "",
        targetPortId: connection.targetHandle ?? "",
      }],
    });
  }, [graph, onConnectionError, onGraphChange, t]);

  const handleMoveEnd = useCallback((_event: MouseEvent | TouchEvent | null, viewport: Viewport) => {
    if (
      viewport.x === graph.viewport.x
      && viewport.y === graph.viewport.y
      && viewport.zoom === graph.viewport.zoom
    ) return;
    onGraphChange({ ...graph, viewport: { x: viewport.x, y: viewport.y, zoom: viewport.zoom } });
  }, [graph, onGraphChange]);

  return (
    <ReactFlow
      defaultEdgeOptions={{ type: "smoothstep" }}
      defaultViewport={{ ...graph.viewport }}
      deleteKeyCode={["Backspace", "Delete"]}
      edges={flowGraph.edges}
      isValidConnection={(connection) => canConnectFilmPorts(graph, connection).valid}
      maxZoom={2}
      minZoom={0.2}
      multiSelectionKeyCode={["Meta", "Control"]}
      nodeTypes={GRAPH_NODE_TYPES}
      nodes={interactiveNodes}
      nodesConnectable
      nodesDraggable
      onConnect={handleConnect}
      onEdgesChange={handleEdgesChange}
      onMoveEnd={handleMoveEnd}
      onNodeClick={(_event, node) => onSelectedNodeChange(node.id)}
      onNodeDragStop={handleNodeDragStop}
      onNodesChange={handleNodesChange}
      onlyRenderVisibleElements={graph.nodes.length > 80}
      panActivationKeyCode="Space"
      panOnDrag={FILM_CANVAS_PAN_MOUSE_BUTTONS}
      selectionOnDrag={false}
      snapGrid={[FILM_GRAPH_GRID_SIZE, FILM_GRAPH_GRID_SIZE]}
      snapToGrid={snapEnabled}
      zoomOnDoubleClick={false}
    >
      <Background color="var(--film-line-strong)" gap={FILM_GRAPH_GRID_SIZE} size={1} />
      <Controls position="bottom-right" showFitView={false} showInteractive={false} />
      <MiniMap
        maskColor="rgba(8, 12, 13, .76)"
        nodeColor={(node) => {
          const typed = node.data as GraphNodeData;
          return typed.attempt?.status === "running" ? "#e0915e" : "#435052";
        }}
        pannable
        position="bottom-left"
        zoomable
      />
      <Panel position="top-left">
        <span className="film-graph-pan-hint">{t("film.canvas.panHint")}</span>
      </Panel>
      <Panel position="top-right">
        <button
          className="film-graph-fit"
          onClick={fitGraph}
          title={t("film.canvas.fitViewTitle")}
          type="button"
        >
          {t("film.canvas.fitView")}
        </button>
      </Panel>
    </ReactFlow>
  );
}

export function FilmGraphCanvas(props: FilmGraphCanvasBodyProps) {
  return (
    <div className="film-graph-canvas" data-testid="film-graph-canvas">
      <ReactFlowProvider>
        <FilmGraphCanvasBody {...props} />
      </ReactFlowProvider>
    </div>
  );
}
