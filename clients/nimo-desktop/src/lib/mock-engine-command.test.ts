import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockEngineCommandClient } from "./mock-engine";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

async function resolveCommand<T>(command: Promise<T>): Promise<T> {
  await vi.advanceTimersByTimeAsync(600);
  return command;
}

describe("mock EngineCommandClient", () => {
  it("labels settings acknowledgement as non-persistent", async () => {
    const result = await resolveCommand(mockEngineCommandClient.saveSettings({
      kind: "save_settings",
      routes: {},
      themeId: "narrative_ember",
    }));

    expect(result.status).toBe("saved");
    expect(result.persistence).toBe("accepted_only");
    expect(result.message).toContain("前端演练");
    expect(result.message).toContain("未写入");
  });

  it("keeps chapter and whole-book delivery requests distinct", async () => {
    const chapterResult = await resolveCommand(mockEngineCommandClient.exportAudio({
      kind: "export_audio",
      projectId: "test-long",
      scope: "chapter",
      chapterNumber: 4,
      format: "mp3",
      targetLufs: -14,
    }));
    const bookResult = await resolveCommand(mockEngineCommandClient.exportAudio({
      kind: "export_audio",
      projectId: "test-long",
      scope: "book",
      format: "zip",
      includeSubtitles: true,
    }));

    expect(chapterResult.message).toContain("第 4 章 MP3 音频（-14 LUFS）");
    expect(bookResult.message).toContain("全书音频打包（含字幕）");
  });
});
