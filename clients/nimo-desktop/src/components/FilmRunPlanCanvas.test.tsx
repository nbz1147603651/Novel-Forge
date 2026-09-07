import { describe, expect, it } from "vitest";

import { createMockFilmGraph } from "../lib/film-graph-fixture";
import {
  autoLayoutFilmGraph,
  canConnectFilmPorts,
  filmExpertWorkbenchClassName,
  filmGraphNodeHeight,
  magnetizeFilmNodePosition,
} from "./film-workflow-utils";
import {
  FILM_CANVAS_PAN_MOUSE_BUTTONS,
  layoutRunPlan,
  type RunPlanNodeView,
} from "./FilmRunPlanCanvas";

function node(partial: Partial<RunPlanNodeView> & { readonly nodeId: string }): RunPlanNodeView {
  return {
    nodeId: partial.nodeId,
    label: partial.label ?? `节点 ${partial.nodeId}`,
    stage: partial.stage ?? "planning",
    dependsOn: partial.dependsOn ?? [],
    artifactInputs: partial.artifactInputs ?? [],
    artifactOutputs: partial.artifactOutputs ?? [],
    providerId: partial.providerId ?? "",
    modelId: partial.modelId ?? "",
    status: partial.status ?? "pending",
    humanCheckpoint: partial.humanCheckpoint ?? false,
    retryLimit: partial.retryLimit ?? 2,
    estimatedCostUsd: partial.estimatedCostUsd ?? 0,
    notes: partial.notes ?? "",
  };
}

describe("layoutRunPlan", () => {
  it("allows the primary pointer button to pan both film canvases", () => {
    expect(FILM_CANVAS_PAN_MOUSE_BUTTONS).toEqual([0, 1, 2]);
  });

  it("returns empty nodes and edges for an empty run plan", () => {
    const graph = layoutRunPlan([]);

    expect(graph.nodes).toEqual([]);
    expect(graph.edges).toEqual([]);
  });

  it("places a planning node at the first lane origin", () => {
    const graph = layoutRunPlan([node({ nodeId: "a", stage: "planning" })]);

    expect(graph.nodes).toHaveLength(1);
    expect(graph.nodes[0]?.position).toEqual({ x: 0, y: 0 });
    expect(graph.nodes[0]?.type).toBe("runPlan");
  });

  it("stacks same-stage nodes vertically within one lane", () => {
    const graph = layoutRunPlan([
      node({ nodeId: "s1", stage: "shot_production" }),
      node({ nodeId: "s2", stage: "shot_production" }),
    ]);

    const [first, second] = graph.nodes;
    expect(first?.position.x).toBe(second?.position.x);
    expect(second?.position.y).toBeGreaterThan(first?.position.y ?? 0);
  });

  it("assigns later stages to later lanes (left-to-right production flow)", () => {
    const graph = layoutRunPlan([
      node({ nodeId: "asset", stage: "visual_development" }),
      node({ nodeId: "shot", stage: "shot_production" }),
    ]);

    const asset = graph.nodes.find((item) => item.id === "asset");
    const shot = graph.nodes.find((item) => item.id === "shot");
    expect(shot?.position.x).toBeGreaterThan(asset?.position.x ?? 0);
  });

  it("puts unknown stages into a trailing lane instead of crashing", () => {
    const graph = layoutRunPlan([
      node({ nodeId: "x", stage: "mystery_stage" as RunPlanNodeView["stage"] }),
    ]);

    expect(graph.nodes[0]?.position.x).toBeGreaterThan(0);
  });

  it("builds cross-stage edges from dependsOn and skips missing deps", () => {
    const graph = layoutRunPlan([
      node({ nodeId: "generate_asset:a1", stage: "visual_development" }),
      node({
        nodeId: "generate:sh1",
        stage: "shot_production",
        dependsOn: ["generate_asset:a1", "generate_asset:ghost"],
      }),
    ]);

    expect(graph.edges).toHaveLength(1);
    expect(graph.edges[0]).toMatchObject({
      id: "generate_asset:a1->generate:sh1",
      source: "generate_asset:a1",
      target: "generate:sh1",
    });
  });

  it("animates incoming edges of active nodes only", () => {
    const graph = layoutRunPlan([
      node({ nodeId: "a", stage: "visual_development", status: "completed" }),
      node({ nodeId: "b", stage: "shot_production", dependsOn: ["a"], status: "active" }),
      node({ nodeId: "c", stage: "shot_production", dependsOn: ["a"], status: "pending" }),
    ]);

    const active = graph.edges.find((edge) => edge.target === "b");
    const pending = graph.edges.find((edge) => edge.target === "c");
    expect(active?.animated).toBe(true);
    expect(pending?.animated).toBe(false);
    expect(active?.className).toBe("is-active");
    expect(pending?.className).toBe("is-pending");
  });
});

describe("canConnectFilmPorts", () => {
  const graph = createMockFilmGraph("test-long");

  it("accepts matching registered artifact types", () => {
    expect(canConnectFilmPorts({ ...graph, edges: graph.edges.filter((edge) => edge.targetNodeId !== "minimax-h3") }, {
      source: "h3-context-ir",
      sourceHandle: "prompt",
      target: "minimax-h3",
      targetHandle: "prompt",
    })).toMatchObject({ valid: true, code: "ok", message: "" });
  });

  it("rejects incompatible ports and explains the artifact mismatch", () => {
    const result = canConnectFilmPorts(graph, {
      source: "creative-brief",
      sourceHandle: "brief",
      target: "minimax-h3",
      targetHandle: "prompt",
    });

    expect(result.valid).toBe(false);
    expect(result.code).toBe("typeMismatch");
    expect(result.message).toContain("CreativeBrief");
    expect(result.message).toContain("H3PromptIR");
  });

  it("rejects duplicate links and occupied single-input ports", () => {
    const duplicate = canConnectFilmPorts(graph, {
      source: "creative-brief",
      sourceHandle: "brief",
      target: "style-lock",
      targetHandle: "brief",
    });
    const extraSource = {
      ...graph.nodes.find((item) => item.nodeId === "creative-brief")!,
      nodeId: "creative-brief-copy",
    };
    const occupied = canConnectFilmPorts({ ...graph, nodes: [...graph.nodes, extraSource] }, {
      source: extraSource.nodeId,
      sourceHandle: "brief",
      target: "style-lock",
      targetHandle: "brief",
    });

    expect(duplicate.code).toBe("duplicate");
    expect(occupied.code).toBe("inputOccupied");
  });

  it("rejects backward production-stage links even when port types match", () => {
    const creative = graph.nodes.find((item) => item.nodeId === "creative-brief")!;
    const style = graph.nodes.find((item) => item.nodeId === "style-lock")!;
    const lateSource = { ...creative, nodeId: "late-brief", stage: "delivery" as const };
    const emptyTarget = { ...style, nodeId: "early-style" };
    const result = canConnectFilmPorts(
      { ...graph, nodes: [lateSource, emptyTarget], edges: [] },
      {
        source: lateSource.nodeId,
        sourceHandle: "brief",
        target: emptyTarget.nodeId,
        targetHandle: "brief",
      },
    );

    expect(result.code).toBe("stageRegression");
  });
});

describe("expert graph arrangement", () => {
  const graph = createMockFilmGraph("test-long");

  it("uses topological columns and measured port heights without overlap", () => {
    const arranged = autoLayoutFilmGraph(graph);
    const columns = new Map<number, typeof arranged.nodes>();
    for (const graphNode of arranged.nodes) {
      columns.set(graphNode.position.x, [...(columns.get(graphNode.position.x) ?? []), graphNode]);
    }
    for (const column of columns.values()) {
      const ordered = [...column].sort((left, right) => left.position.y - right.position.y);
      for (let index = 1; index < ordered.length; index += 1) {
        const previous = ordered[index - 1]!;
        const current = ordered[index]!;
        expect(current.position.y).toBeGreaterThanOrEqual(
          previous.position.y + filmGraphNodeHeight(previous),
        );
      }
    }
    for (const edge of arranged.edges) {
      const source = arranged.nodes.find((item) => item.nodeId === edge.sourceNodeId)!;
      const target = arranged.nodes.find((item) => item.nodeId === edge.targetNodeId)!;
      expect(target.position.x).toBeGreaterThan(source.position.x);
    }
  });

  it("snaps to the grid and nearby node alignment guides", () => {
    const anchor = graph.nodes[0]!;
    const snapped = magnetizeFilmNodePosition(graph, graph.nodes[1]!.nodeId, {
      x: anchor.position.x + 7,
      y: 137,
    });

    expect(snapped.x).toBe(anchor.position.x);
    expect(snapped.y % 20).toBe(0);
  });

  it("collapses all auxiliary panes while focus mode is active", () => {
    const className = filmExpertWorkbenchClassName({
      catalogOpen: true,
      focusMode: true,
      inspectorOpen: true,
      queueOpen: true,
    });

    expect(className).toContain("is-catalog-collapsed");
    expect(className).toContain("is-inspector-collapsed");
    expect(className).toContain("is-focus-mode");
    expect(className).not.toContain("has-queue");
  });
});
