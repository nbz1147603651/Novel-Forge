import { describe, expect, it } from "vitest";

import {
  createLongInitSession,
  emptyLongInitPayload,
  emptyLongInitPayloadForLocale,
  longInitLaunchPreview,
  normalizeLongInitPayload,
  normalizeVolumeFields,
  resetLongInitSession,
  saveLongInitPreset,
  loadLongInitPreset,
  deleteLongInitPreset,
  updateLongInitPayload,
  validateLongInitPayload,
  serializeLongInitPayload,
  parseLongInitJson,
} from "./long-init-session";

describe("long-init-session", () => {
  describe("normalizeLongInitPayload", () => {
    it("returns defaults for empty input", () => {
      const result = normalizeLongInitPayload({});
      expect(result.premise).toBe("");
      expect(result.totalChapters).toBe(24);
      expect(result.wordsPerChapter).toBe(4500);
      expect(result.volumeMode).toBe("auto");
      expect(result.tone).toBe("neutral");
      expect(result.planningCommitment).toBe("full");
    });

    it("preserves an explicitly chosen progressive plan in existing drafts", () => {
      expect(normalizeLongInitPayload({ planning_commitment: "progressive" }).planningCommitment).toBe("progressive");
      expect(normalizeLongInitPayload({ planningCommitment: "progressive" }).planningCommitment).toBe("progressive");
    });

    it("normalizes snake_case aliases", () => {
      const result = normalizeLongInitPayload({
        characters_hint: "角色提示",
        world_hint: "世界提示",
        total_chapters: 30,
        words_per_chapter: 5000,
        volume_mode: "on",
        chapters_per_volume: 10,
      });
      expect(result.charactersHint).toBe("角色提示");
      expect(result.worldHint).toBe("世界提示");
      expect(result.totalChapters).toBe(30);
      expect(result.wordsPerChapter).toBe(5000);
      expect(result.volumeMode).toBe("on");
      expect(result.chaptersPerVolume).toBe(10);
    });

    it("clamps numeric values to bounds", () => {
      const result = normalizeLongInitPayload({
        totalChapters: 9999,
        wordsPerChapter: 100,
      });
      expect(result.totalChapters).toBe(1000);
      expect(result.wordsPerChapter).toBe(500);
    });
  });

  describe("normalizeVolumeFields", () => {
    it("forces chaptersPerVolume to 0 when mode is auto", () => {
      const result = normalizeVolumeFields("auto", 15);
      expect(result.chaptersPerVolume).toBe(0);
      expect(result.hint).toContain("auto");
    });

    it("forces chaptersPerVolume to 0 when mode is off", () => {
      const result = normalizeVolumeFields("off", 15);
      expect(result.chaptersPerVolume).toBe(0);
      expect(result.hint).toContain("off");
    });

    it("preserves chaptersPerVolume when mode is on", () => {
      const result = normalizeVolumeFields("on", 15);
      expect(result.chaptersPerVolume).toBe(15);
      expect(result.hint).toContain("on");
    });
  });

  describe("session operations", () => {
    it("creates session with empty payload by default", () => {
      const session = createLongInitSession();
      expect(session.payload).toEqual(emptyLongInitPayload);
      expect(session.presets).toEqual([]);
      expect(session.activePresetId).toBeNull();
    });

    it("updates payload immutably", () => {
      const session = createLongInitSession();
      const updated = updateLongInitPayload(session, { premise: "新前提" });
      expect(updated.payload.premise).toBe("新前提");
      expect(session.payload.premise).toBe("");
    });

    it("resets session to empty payload", () => {
      const session = createLongInitSession();
      const updated = updateLongInitPayload(session, { premise: "新前提" });
      const reset = resetLongInitSession(updated);
      expect(reset.payload.premise).toBe("");
      expect(reset.activePresetId).toBeNull();
    });

    it("uses English as the workflow and prompt language when the desktop locale is English", () => {
      expect(emptyLongInitPayloadForLocale("en").language).toBe("en");
      expect(resetLongInitSession(createLongInitSession(), "en").payload.language).toBe("en");
    });
  });

  describe("preset CRUD", () => {
    it("saves a new preset", () => {
      const session = createLongInitSession();
      const { session: saved, error } = saveLongInitPreset(session, "测试预设");
      expect(error).toBeUndefined();
      expect(saved.presets).toHaveLength(1);
      expect(saved.presets[0]!.name).toBe("测试预设");
      expect(saved.activePresetId).toBe(saved.presets[0]!.id);
    });

    it("rejects empty preset name", () => {
      const session = createLongInitSession();
      const { error } = saveLongInitPreset(session, "   ");
      expect(error).toContain("预设名称");
    });

    it("loads a preset by id", () => {
      const session = createLongInitSession();
      const { session: saved } = saveLongInitPreset(session, "测试预设", {
        ...emptyLongInitPayload,
        premise: "预设前提",
      });
      const presetId = saved.presets[0]!.id;
      const loaded = loadLongInitPreset(saved, presetId);
      expect(loaded.payload.premise).toBe("预设前提");
    });

    it("deletes a preset", () => {
      const session = createLongInitSession();
      const { session: saved } = saveLongInitPreset(session, "测试预设");
      const presetId = saved.presets[0]!.id;
      const deleted = deleteLongInitPreset(saved, presetId);
      expect(deleted.presets).toHaveLength(0);
      expect(deleted.activePresetId).toBeNull();
    });
  });

  describe("validation", () => {
    it("requires premise", () => {
      const issues = validateLongInitPayload(emptyLongInitPayload);
      expect(issues).toContain("请填写「故事前提」。");
    });

    it("passes with premise", () => {
      const issues = validateLongInitPayload({ ...emptyLongInitPayload, premise: "有前提" });
      expect(issues).toHaveLength(0);
    });
  });

  describe("serialization", () => {
    it("serializes to snake_case JSON", () => {
      const json = serializeLongInitPayload({
        ...emptyLongInitPayload,
        premise: "测试前提",
        totalChapters: 30,
      });
      const parsed = JSON.parse(json);
      expect(parsed.premise).toBe("测试前提");
      expect(parsed.total_chapters).toBe(30);
    });

    it("parses JSON with snake_case keys", () => {
      const { payload, error } = parseLongInitJson(JSON.stringify({
        premise: "解析前提",
        total_chapters: 50,
      }));
      expect(error).toBeUndefined();
      expect(payload?.premise).toBe("解析前提");
      expect(payload?.totalChapters).toBe(50);
    });

    it("rejects invalid JSON", () => {
      const { error } = parseLongInitJson("not json");
      expect(error).toContain("无法解析");
    });
  });

  describe("launch preview", () => {
    it("generates preview with key fields", () => {
      const preview = longInitLaunchPreview({
        ...emptyLongInitPayload,
        premise: "这是一个很长的前提".repeat(10),
        genre: "悬疑",
        totalChapters: 24,
      });
      expect(preview["题材"]).toBe("悬疑");
      expect(preview["总章数"]).toBe(24);
      expect(String(preview["故事前提"])).toContain("…");
    });
  });
});
