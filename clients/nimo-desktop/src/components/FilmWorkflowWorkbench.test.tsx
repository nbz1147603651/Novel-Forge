import type { FilmProviderCatalogView } from "@nimo/engine-contracts";
import { describe, expect, it } from "vitest";

import { createMockFilmGraph, mockFilmNodeCatalog } from "../lib/film-graph-fixture";
import { routeFilmGraphProvider } from "./FilmWorkflowWorkbench";
import {
  H3_DURATION_OPTIONS,
  renderFilmNodePrompt,
  replaceFilmPromptSection,
} from "./film-workflow-utils";

describe("FilmWorkflowWorkbench H3 contract projection", () => {
  it("offers every integer duration from 4 through 15 seconds", () => {
    expect(H3_DURATION_OPTIONS).toEqual([4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]);
  });

  it("keeps direct 2K generation separate from 768P to 2K regeneration", () => {
    const generation = mockFilmNodeCatalog.find((item) => item.typeId === "minimax_h3_video");
    const regeneration = mockFilmNodeCatalog.find((item) => item.typeId === "minimax_h3_regenerate_2k");

    expect(generation?.defaultConfig.resolution).toBe("768P");
    expect(regeneration?.inputPorts[0]?.artifactType).toBe("VideoClip");
    expect(regeneration?.providerCapabilities).toContain("minimax:regenerate_2k");
  });

  it("does not expose an undeclared H3 seed", () => {
    const generation = mockFilmNodeCatalog.find((item) => item.typeId === "minimax_h3_video");

    expect(generation?.defaultConfig).not.toHaveProperty("seed");
  });

  it("gives every graph node an editable prompt inherited from novel and voice contracts", () => {
    for (const definition of mockFilmNodeCatalog) {
      expect(definition.promptTemplate.templateId).toBe(`film.${definition.typeId}`);
      expect(definition.promptTemplate.sections.length).toBeGreaterThan(0);
      expect(definition.promptTemplate.sections.every((section) => section.editable)).toBe(true);
      expect(definition.promptTemplate.sections.flatMap((section) => section.sources)).toContain("novel");
      expect(definition.promptTemplate.sections.flatMap((section) => section.sources)).toContain("voice");
    }
  });

  it("renders and replaces prompt sections without flattening source lineage", () => {
    const definition = mockFilmNodeCatalog.find((item) => item.typeId === "minimax_h3_video")!;
    const core = definition.promptTemplate.sections.find((section) => section.sectionId === "core_creative")!;
    const edited = replaceFilmPromptSection(
      definition.promptTemplate,
      { ...core, content: "雨夜旧电台里，主持人发现一盘失落录音。" },
    );
    const rendered = renderFilmNodePrompt({ ...edited, userNotes: "保持人物声线身份。" });

    expect(edited.sections.find((section) => section.sectionId === "core_creative")?.content)
      .toBe("雨夜旧电台里，主持人发现一盘失落录音。");
    expect(edited.sections.find((section) => section.sectionId === "core_creative")?.sources)
      .toEqual(core.sources);
    expect(rendered).toContain("【核心创意】");
    expect(rendered).toContain("【用户补充】\n保持人物声线身份。");
  });

  it("routes video context and generation nodes together when the canvas platform changes", () => {
    const providerCatalog: FilmProviderCatalogView = {
      bailian: {
        label: "阿里百炼",
        imageModels: [],
        videoModels: [{
          id: "wan2.7-r2v",
          modes: ["reference_to_video"],
          durations: { min: 2, max: 15 },
          resolutions: ["720P", "1080P"],
          defaultDuration: 10,
          defaultResolution: "720P",
          features: ["prompt_optimizer", "seed"],
        }],
      },
      volcengine_ark: {
        label: "火山方舟",
        imageModels: [],
        videoModels: [{
          id: "doubao-seedance-2-0-260128",
          modes: ["reference_to_video"],
          durations: { min: 2, max: 15 },
          resolutions: ["720P", "1080P"],
          defaultDuration: 5,
          defaultResolution: "1080P",
          features: ["generate_audio", "return_last_frame", "seed"],
        }],
      },
    };
    const initial = createMockFilmGraph("provider-route");
    const bailian = routeFilmGraphProvider(
      initial,
      mockFilmNodeCatalog,
      providerCatalog,
      "bailian",
    );

    expect(bailian.changedNodeCount).toBe(2);
    expect(bailian.graph.nodes.find((node) => node.typeId === "h3_context_ir")).toMatchObject({
      providerId: "bailian",
      modelId: "wan2.7-r2v",
      config: { target_model: "wan2.7-r2v" },
    });
    expect(bailian.graph.nodes.find((node) => node.typeId === "minimax_h3_video")).toMatchObject({
      providerId: "bailian",
      modelId: "wan2.7-r2v",
      config: { mode: "reference_to_video", resolution: "720P" },
    });

    const ark = routeFilmGraphProvider(
      bailian.graph,
      mockFilmNodeCatalog,
      providerCatalog,
      "volcengine_ark",
    );
    expect(ark.changedNodeCount).toBe(2);
    expect(ark.graph.nodes.find((node) => node.typeId === "h3_context_ir")).toMatchObject({
      providerId: "volcengine_ark",
      modelId: "doubao-seedance-2-0-260128",
    });
    expect(ark.graph.nodes.find((node) => node.typeId === "minimax_h3_video")).toMatchObject({
      providerId: "volcengine_ark",
      modelId: "doubao-seedance-2-0-260128",
      config: { generate_audio: true, return_last_frame: true },
    });
  });
});
