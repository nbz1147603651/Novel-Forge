import { afterEach, describe, expect, it, vi } from "vitest";

import { LegacyLocalEngineClient } from "./legacy-engine-client";

describe("LegacyLocalEngineClient film boundary", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("maps film state to camelCase and serializes shot patches to snake_case", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          project_id: "demo",
          project_title: "演示",
          current_stage: "storyboard",
          production_bible: { style: { aspect_ratio: "16:9" } },
          screenplay: { scenes: [] },
          visual_assets: [],
          shots: [],
          run_plan: [],
          timeline: { tracks: [], markers: [] },
          stages: [],
          decisions: [],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ project_id: "demo", project_title: "演示" }),
      });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LegacyLocalEngineClient({ baseUrl: "http://127.0.0.1:8900" });

    const studio = await client.getFilmStudio("demo");
    await client.updateFilmShot({
      kind: "update_film_shot",
      projectId: "demo",
      shotId: "sc-001-sh-01",
      patch: { providerId: "bailian", selectedAssetUrl: "https://asset.test/shot.mp4" },
    });

    expect(studio.currentStage).toBe("storyboard");
    expect(studio.productionBible.style.aspectRatio).toBe("16:9");
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:8900/api/v1/film/projects/demo/shots/sc-001-sh-01",
    );
    const init = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({
      patch: {
        provider_id: "bailian",
        selected_asset_url: "https://asset.test/shot.mp4",
      },
    });
  });
});

