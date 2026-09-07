import { renderToStaticMarkup } from "react-dom/server";

import { afterEach, describe, expect, it, vi } from "vitest";

import { sideRailPageMeta } from "../App";
import { LocaleProvider } from "../lib/i18n";
import { mockEngineClient, mockEngineCommandClient } from "../lib/mock-engine";
import { filmStageClassName } from "./film-workflow-utils";
import { defaultFilmTimelineMode, FilmStudioPage } from "./FilmStudioPage";

// React Flow 依赖 DOM 测量，node SSR 测试环境无法真实渲染，替换为桩组件。
vi.mock("./FilmRunPlanCanvas", () => ({
  FilmRunPlanCanvas: ({ nodes }: { readonly nodes: readonly unknown[] }) => (
    <div data-stub="film-run-plan-canvas">{nodes.length}</div>
  ),
}));

describe("FilmStudioPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("places 映界 directly below 声腔 and before 火候", () => {
    const ids = sideRailPageMeta.map((item) => item.id);

    expect(ids.indexOf("film_studio")).toBe(ids.indexOf("voice_studio") + 1);
    expect(ids.indexOf("film_studio")).toBeLessThan(ids.indexOf("settings"));
  });

  it("keeps a direct canvas entry available in every production stage", async () => {
    vi.stubGlobal("window", { setTimeout });
    const [studio, catalog, workspace] = await Promise.all([
      mockEngineClient.getFilmStudio("test-long"),
      mockEngineClient.getFilmProviderCatalog(),
      mockEngineClient.getWorkspace(),
    ]);
    for (const currentStage of ["planning", "screenplay", "visual_development", "storyboard", "shot_production", "sound_picture", "edit", "compliance", "delivery"] as const) {
      const markup = renderToStaticMarkup(<FilmStudioPage
        catalog={catalog} commandClient={mockEngineCommandClient} engineClient={mockEngineClient}
        format="feature" onFormatChange={() => undefined} onProjectChange={() => undefined}
        onStudioChange={() => undefined} projects={workspace.projects} studio={{ ...studio, currentStage }}
      />);
      expect(markup).toContain('class="film-canvas-entry"');
      expect(markup).toContain('>专家画布</button>');
      expect(markup).toContain(`${currentStage} 工作区`);
    }
  });

  it("separates the selected stage highlight from the runtime stage marker", () => {
    const planning = filmStageClassName(false, "active");
    const visual = filmStageClassName(true, "ready");

    expect(planning).toContain("is-runtime-active");
    expect(planning).not.toContain("is-selected");
    expect(visual).toContain("is-selected");
    expect(visual).toContain("is-runtime-ready");
  });

  it("renders the commercial workflow, storyboard inspector, and inherited timeline", async () => {
    vi.stubGlobal("window", { setTimeout });
    const [studio, catalog, workspace] = await Promise.all([
      mockEngineClient.getFilmStudio("test-long"),
      mockEngineClient.getFilmProviderCatalog(),
      mockEngineClient.getWorkspace(),
    ]);

    const markup = renderToStaticMarkup(
      <FilmStudioPage
        catalog={catalog}
        commandClient={mockEngineCommandClient}
        engineClient={mockEngineClient}
        format="feature"
        onFormatChange={() => undefined}
        onProjectChange={() => undefined}
        onStudioChange={() => undefined}
        projects={workspace.projects}
        studio={studio}
      />,
    );

    expect(markup).toContain("策划");
    expect(markup).toContain("剧本");
    expect(markup).toContain("视觉");
    expect(markup).toContain("分镜");
    expect(markup).toContain("声画");
    expect(markup).toContain("交付");
    expect(markup).toContain("SC-001-SH-03");
    expect(markup).toContain("UPSTREAM LOCKS");
    expect(markup).toContain("这串时间戳，不该出现在这里。");
    expect(markup).toContain("废弃记忆档案库");
    expect(markup).toContain("时间线");
    expect(markup).toContain('aria-expanded="false"');
    expect(markup).toContain('film-timeline-launcher');
    expect(markup).not.toContain('film-master-timeline-tracks');
    // 分镜阶段是镜头设计稿：呈现叙事设计字段与进入生产线的入口，不承载生成/检查器。
    expect(markup).toContain("STORYBOARD");
    expect(markup).toContain("进入镜头生产");
    expect(markup).not.toContain("三平台生成通路");
    expect(markup).not.toContain("镜头检查器");

    // 镜头阶段是生产线：生成路由、检查器与媒体管线都在此收敛。
    const shotMarkup = renderToStaticMarkup(
      <FilmStudioPage
        catalog={catalog}
        commandClient={mockEngineCommandClient}
        engineClient={mockEngineClient}
        format="feature"
        onFormatChange={() => undefined}
        onProjectChange={() => undefined}
        onStudioChange={() => undefined}
        projects={workspace.projects}
        studio={{ ...studio, currentStage: "shot_production" }}
      />,
    );
    expect(shotMarkup).toContain("SHOT PRODUCTION");
    expect(shotMarkup).toContain("Wan 2.7 Reference-to-Video");
    expect(shotMarkup).toContain("三平台生成通路");
    expect(shotMarkup).toContain('role="tablist"');
    expect(shotMarkup).toContain("百炼原生参数");
    expect(shotMarkup).toContain("提示优化");
    expect(shotMarkup).toContain("负面提示词");
    expect(shotMarkup).toContain("镜头检查器");
    expect(shotMarkup).toContain("展开轨道");
    expect(shotMarkup).toContain('film-master-timeline-tracks');
    expect(catalog.volcengine_ark?.label).toBe("火山方舟");

    const visualMarkup = renderToStaticMarkup(
      <FilmStudioPage
        catalog={catalog}
        commandClient={mockEngineCommandClient}
        engineClient={mockEngineClient}
        format="feature"
        onFormatChange={() => undefined}
        onProjectChange={() => undefined}
        onStudioChange={() => undefined}
        projects={workspace.projects}
        studio={{ ...studio, currentStage: "visual_development" }}
      />,
    );
    expect(visualMarkup).toContain("进入资产页");
  });

  it("switches Film Studio chrome to English while preserving project content", async () => {
    vi.stubGlobal("window", { setTimeout });
    const [studio, catalog, workspace] = await Promise.all([
      mockEngineClient.getFilmStudio("test-long"),
      mockEngineClient.getFilmProviderCatalog(),
      mockEngineClient.getWorkspace(),
    ]);

    const markup = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <FilmStudioPage
          catalog={catalog}
          commandClient={mockEngineCommandClient}
          engineClient={mockEngineClient}
          format="feature"
          onFormatChange={() => undefined}
          onProjectChange={() => undefined}
          onStudioChange={() => undefined}
          projects={workspace.projects}
          studio={studio}
        />
      </LocaleProvider>,
    );

    expect(markup).toContain("Active Project");
    expect(markup).toContain("Feature Series");
    expect(markup).toContain("Storyboard");
    expect(markup).toContain("Upstream Locks");
    expect(markup).toContain("Enter Shot Production");
    expect(markup).toContain("Timeline");
    expect(markup).not.toContain(">在制项目<");
    expect(markup).not.toContain("进入镜头生产 →");
    // Story data follows the project's writing language, not the interface locale.
    expect(markup).toContain("废弃记忆档案库");
  });

  it("uses stage-aware progressive disclosure for the master timeline", () => {
    expect(defaultFilmTimelineMode("planning")).toBe("hidden");
    expect(defaultFilmTimelineMode("storyboard")).toBe("hidden");
    expect(defaultFilmTimelineMode("shot_production")).toBe("peek");
    expect(defaultFilmTimelineMode("edit")).toBe("peek");
    expect(defaultFilmTimelineMode("delivery")).toBe("peek");
  });

  it("shows one edit ledger at a time and keeps the timeline in peek mode", async () => {
    vi.stubGlobal("window", { setTimeout });
    const [studio, catalog, workspace] = await Promise.all([
      mockEngineClient.getFilmStudio("test-long"),
      mockEngineClient.getFilmProviderCatalog(),
      mockEngineClient.getWorkspace(),
    ]);

    const markup = renderToStaticMarkup(
      <FilmStudioPage
        catalog={catalog}
        commandClient={mockEngineCommandClient}
        engineClient={mockEngineClient}
        format="feature"
        onFormatChange={() => undefined}
        onProjectChange={() => undefined}
        onStudioChange={() => undefined}
        projects={workspace.projects}
        studio={{ ...studio, currentStage: "edit" }}
      />,
    );

    expect(markup).toContain("film-edit-view-switch");
    expect(markup).toContain("媒体库台账");
    expect(markup).not.toContain("film-readiness-row");
    expect(markup).toContain("film-timeline is-collapsed");
  });

  it("renders MiniMax H3 native duration and multimodal guidance", async () => {
    vi.stubGlobal("window", { setTimeout });
    const [studio, catalog, workspace] = await Promise.all([
      mockEngineClient.getFilmStudio("test-long"),
      mockEngineClient.getFilmProviderCatalog(),
      mockEngineClient.getWorkspace(),
    ]);
    const h3Studio = {
      ...studio,
      currentStage: "shot_production" as const,
      shots: studio.shots.map((shot) => ({
        ...shot,
        providerId: "minimax",
        modelId: "MiniMax-H3",
        durationS: 14,
        generationParams: { ...shot.generationParams, resolution: "2K" },
      })),
    };

    const markup = renderToStaticMarkup(
      <FilmStudioPage
        catalog={catalog}
        commandClient={mockEngineCommandClient}
        engineClient={mockEngineClient}
        format="feature"
        onFormatChange={() => undefined}
        onProjectChange={() => undefined}
        onStudioChange={() => undefined}
        projects={workspace.projects}
        studio={h3Studio}
      />,
    );

    expect(markup).toContain("H3 多模态参考");
    expect(markup).toContain("4–15 秒整数");
    expect(markup).toContain("2K 直出");
    expect(markup).toContain("当前接口不提供可复现种子");
  });

  it("renders the drama and comic production lines as 映界 formats", async () => {
    vi.stubGlobal("window", { setTimeout });
    const [studio, catalog, workspace] = await Promise.all([
      mockEngineClient.getFilmStudio("test-long"),
      mockEngineClient.getFilmProviderCatalog(),
      mockEngineClient.getWorkspace(),
    ]);

    const dramaMarkup = renderToStaticMarkup(
      <FilmStudioPage
        catalog={catalog}
        commandClient={mockEngineCommandClient}
        engineClient={mockEngineClient}
        format="drama"
        onFormatChange={() => undefined}
        onProjectChange={() => undefined}
        onStudioChange={() => undefined}
        projects={workspace.projects}
        studio={studio}
      />,
    );
    expect(dramaMarkup).toContain("短剧线");
    expect(dramaMarkup).toContain("is-format-mode");
    expect(dramaMarkup).toContain("UPSTREAM LOCKS");
    expect(dramaMarkup).toContain("同步上游");

    const comicMarkup = renderToStaticMarkup(
      <FilmStudioPage
        catalog={catalog}
        commandClient={mockEngineCommandClient}
        engineClient={mockEngineClient}
        format="comic"
        onFormatChange={() => undefined}
        onProjectChange={() => undefined}
        onStudioChange={() => undefined}
        projects={workspace.projects}
        studio={studio}
      />,
    );
    expect(comicMarkup).toContain("漫画线");
    expect(comicMarkup).toContain("剧本来自成稿回流投影");
  });

  it("lets autonomous creation advance to the paid-generation authorization gate", async () => {
    const studio = await mockEngineCommandClient.advanceFilm({
      kind: "advance_film",
      projectId: "test-long",
      mode: "autonomous",
      useAi: true,
      runUntil: "delivery",
    });

    expect(studio.currentStage).toBe("shot_production");
    expect(studio.stages.find((item) => item.stage === "shot_production")?.status).toBe("blocked");
  });
});
