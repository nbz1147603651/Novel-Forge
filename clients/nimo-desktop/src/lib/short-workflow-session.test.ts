import { describe, expect, it } from "vitest";

import {
  applyShortWorkflowPreview,
  createShortWorkflowSession,
  deleteShortWorkflowPreset,
  emptyShortWorkflowPayload,
  findShortWorkflowPresetByName,
  historyForShortWorkflowPreset,
  loadShortWorkflowPreset,
  normalizeShortWorkflowPayload,
  parseShortWorkflowJson,
  resetShortWorkflowSession,
  saveShortWorkflowPreset,
  serializeShortWorkflowPayload,
  shortWorkflowFixturePayload,
  shortWorkflowPayloadForLocale,
  shortWorkflowLaunchPreview,
  toRunShortWorkflowInput,
  updateShortWorkflowPayload,
  validateShortWorkflowPayload,
} from "./short-workflow-session";

describe("short workflow local session", () => {
  it("keeps PySide ShortForm bounds and source-shaped defaults at the pure boundary", () => {
    expect(normalizeShortWorkflowPayload({
      length_target: 99_999,
      max_edit_rounds: -2,
      segment_trigger_words: 12,
      writing_mode: "unknown",
      tone: "unknown",
      language: "jp",
    })).toMatchObject({
      lengthTarget: 50_000,
      maxEditRounds: 0,
      segmentTriggerWords: 1_500,
      writingMode: "auto",
      tone: "neutral",
      language: "zh",
    });
  });

  it("uses English as the workflow and prompt language when the desktop locale is English", () => {
    expect(shortWorkflowPayloadForLocale("en").language).toBe("en");
    expect(resetShortWorkflowSession(createShortWorkflowSession(), "en").payload.language).toBe("en");
  });

  it("saves, loads, updates and deletes presets only inside the active front-end session", () => {
    const initial = createShortWorkflowSession();
    const firstSave = saveShortWorkflowPreset(initial, "雨夜录音");
    expect(firstSave.error).toBeUndefined();
    expect(firstSave.session.presets).toHaveLength(1);

    const edited = updateShortWorkflowPayload(firstSave.session, { theme: "新的主题" });
    const updated = saveShortWorkflowPreset(edited, "雨夜录音");
    const preset = findShortWorkflowPresetByName(updated.session, "雨夜录音");
    expect(preset?.payload.theme).toBe("新的主题");

    const loaded = loadShortWorkflowPreset(updated.session, preset!.id);
    expect(loaded.payload.theme).toBe("新的主题");
    expect(deleteShortWorkflowPreset(loaded, preset!.id)).toMatchObject({ activePresetId: null, presets: [] });
  });

  it("merges partial raw and run_short JSON without accepting unrelated objects", () => {
    const imported = parseShortWorkflowJson(JSON.stringify({ run_short: { theme: "旧钟", length_target: 1800 } })).payload;
    expect(imported).toEqual({
      theme: "旧钟",
      lengthTarget: 1800,
    });
    expect(updateShortWorkflowPayload(createShortWorkflowSession(), imported!).payload).toMatchObject({
      theme: "旧钟",
      lengthTarget: 1800,
      genre: "悬疑",
      worldHint: shortWorkflowFixturePayload.worldHint,
    });
    expect(parseShortWorkflowJson(JSON.stringify({ something: "else" })).error).toContain("短篇表单");
    expect(parseShortWorkflowJson("{").error).toContain("无法解析 JSON");
  });

  it("serializes source field names, validates the only required ShortForm field and creates a safe launch preview", () => {
    expect(validateShortWorkflowPayload(emptyShortWorkflowPayload)).toEqual(["请填写「故事主题」。"]);
    const serialized = serializeShortWorkflowPayload(shortWorkflowFixturePayload);
    expect(JSON.parse(serialized)).toMatchObject({
      theme: shortWorkflowFixturePayload.theme,
      length_target: 3200,
      max_edit_rounds: 2,
      characters_hint: shortWorkflowFixturePayload.charactersHint,
      research_enabled: false,
      research_provider: "auto",
      research_query_hint: "",
    });
    expect(toRunShortWorkflowInput({
      ...shortWorkflowFixturePayload,
      researchEnabled: true,
      researchProvider: "mcp_search",
      researchQueryHint: "急诊分诊流程",
    })).toMatchObject({
      researchEnabled: true,
      researchProvider: "mcp_search",
      researchQueryHint: "急诊分诊流程",
    });
    expect(shortWorkflowLaunchPreview({ ...shortWorkflowFixturePayload, lengthTarget: 8000, segmentTriggerWords: 6000 })).toMatchObject({
      "segmented_mode": "开启",
      "项目标识": "（由引擎生成）",
    });
  });

  it("binds accepted previews to a preset and keeps histories isolated by preset", () => {
    const preview = applyShortWorkflowPreview(createShortWorkflowSession(), "AI 生成并预览", {
      endingStyle: "让录音在拆迁现场被真正播放。",
      extraInstructions: "保留物件线索。",
    });
    const session = preview.session;
    expect(session.payload.endingStyle).toContain("拆迁现场");
    expect(preview.autoSavedPresetName).toBeTruthy();
    expect(session.activePresetId).toBeTruthy();
    expect(session.presets).toHaveLength(1);
    expect(session.history).toHaveLength(1);
    const entry = session.history[0]!;
    expect(entry.id).toBe("short-history:1");
    expect(entry.label).toBe("AI 生成并预览");
    expect(entry.presetId).toBe(session.activePresetId);
    expect(entry.changedKeys).toEqual(["endingStyle", "extraInstructions"]);
    expect(entry.snapshot.endingStyle).toContain("拆迁现场");

    const second = saveShortWorkflowPreset(session, "另一份预设").session;
    const secondPreview = applyShortWorkflowPreview(second, "AI 定向润色", { title: "新标题" }).session;
    expect(historyForShortWorkflowPreset(secondPreview)).toHaveLength(1);
    const restoredFirst = loadShortWorkflowPreset(secondPreview, entry.presetId);
    expect(historyForShortWorkflowPreset(restoredFirst)).toEqual([entry]);
    expect(resetShortWorkflowSession(restoredFirst).payload).toEqual(emptyShortWorkflowPayload);
  });

  it("retains applied AI creative-note metadata with its preset-bound history", () => {
    const preview = applyShortWorkflowPreview(
      createShortWorkflowSession(),
      "AI 定向润色",
      { openingStyle: "从雨声与停摆的怀表切入。" },
      {
        timeLabel: "2026-07-29 22:30",
        userHint: "加强悬念与人物动机",
        selectedSuggestions: ["压缩解释性旁白"],
        creativeProfile: { style: "plot", novelty: "fresh" },
        creativeNote: { core_pitch: "让录音成为追问真相的行动代价。" },
      },
    );

    expect(preview.session.history[0]).toMatchObject({
      timeLabel: "2026-07-29 22:30",
      userHint: "加强悬念与人物动机",
      selectedSuggestions: ["压缩解释性旁白"],
      creativeNote: { core_pitch: "让录音成为追问真相的行动代价。" },
    });
  });

  it("sanitizes imported blueprint preferences to the same library-backed shape as PySide6", () => {
    const payload = normalizeShortWorkflowPayload({
      blueprint_element_preferences: {
        preset_id: "mystery",
        items: [
          { element_id: "mystery_clue_ledger", enabled: true, locked: false, weight: 74 },
          { element_id: "missing_from_library", enabled: true, locked: true, weight: 99 },
          { element_id: "mystery_clue_ledger", enabled: false, locked: true, weight: 111 },
        ],
      },
    });
    expect(payload.blueprintElementPreferences).toEqual({
      presetId: "mystery",
      manualOverride: false,
      items: [{ elementId: "mystery_clue_ledger", enabled: false, locked: true, weight: 100 }],
    });
  });
});
