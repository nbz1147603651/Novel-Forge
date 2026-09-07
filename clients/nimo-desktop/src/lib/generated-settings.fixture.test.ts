import { afterAll, describe, expect, it, vi } from "vitest";

import { mockEngineClient } from "./mock-engine";

vi.stubGlobal("window", globalThis);

describe("generated settings parity fixture", () => {
  afterAll(() => vi.unstubAllGlobals());

  it("exposes every canonical route group including voice and protected temperature rules", async () => {
    const settings = await mockEngineClient.getSettings();
    const labels = settings.routingGroups.map((group) => group.label);

    expect(labels).toEqual([
      "短篇流程",
      "长篇初始化",
      "长篇章节创作",
      "工具与维护",
      "记忆增强",
      "配音",
    ]);
    const protectedRoute = settings.routingGroups
      .flatMap((group) => group.routes)
      .find((route) => route.temperatureJitterProtected && route.temperatureKind !== null);
    expect(protectedRoute?.temperatureKind).toBe("fixed");
    expect(settings.creativeTemperature).toEqual({
      enabled: false,
      scope: "recommended",
      downDelta: 0.3,
      upDelta: 0.1,
      customTaskKeys: [],
    });
  });
});
