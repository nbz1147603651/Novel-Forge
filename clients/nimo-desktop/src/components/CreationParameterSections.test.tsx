import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { generatedSettingsFixture } from "../lib/generated-settings.fixture";
import { CreationParameterSections, creationParameterDefaultsById } from "./CreationParameterSections";

describe("CreationParameterSections ownership", () => {
  it("keeps TTS-only parameters out of 火候 after they move to 声腔", () => {
    const markup = renderToStaticMarkup(
      <CreationParameterSections
        initialValues={{
          "short-max-edit": "3",
          "tts-provider": "minimax",
          "tts-model": "speech-2.8-hd",
        }}
        onSettingsChange={() => undefined}
      />,
    );

    expect(markup).not.toContain("TTS 基本路由");
    expect(markup).not.toContain("TTS 并发与速率");
    expect(markup).not.toContain("TTS 平台凭据");
    expect(markup).toContain("短篇生成参数");
  });

  it("defines every form field exactly once (no duplicate ids)", () => {
    const markup = renderToStaticMarkup(
      <CreationParameterSections
        initialValues={undefined}
        onSettingsChange={() => undefined}
      />,
    );
    // aria-label doubles as the stable field id for every control.
    const ariaLabels = [...markup.matchAll(/aria-label="([^"]+)"/g)].map((match) => match[1]);
    const duplicates = ariaLabels.filter((label, index) => ariaLabels.indexOf(label) !== index);
    expect(duplicates).toEqual([]);
  });

  it("exposes no legacy temp-jitter duplicates now that 创作火候 lives in CreativeTemperatureSection", () => {
    const markup = renderToStaticMarkup(
      <CreationParameterSections
        initialValues={undefined}
        onSettingsChange={() => undefined}
      />,
    );
    expect(markup).not.toContain("启用温度浮动");
    expect(markup).not.toContain("temp-jitter");
  });

  it("exposes no fields without a backend Settings attribute (dead controls)", () => {
    const markup = renderToStaticMarkup(
      <CreationParameterSections
        initialValues={undefined}
        onSettingsChange={() => undefined}
      />,
    );
    for (const deadLabel of ["拟人化强度", "库容量上限", "项目锁超时秒数", "启用 Mock 模式", "启用问题池锚定", "保留天数"]) {
      expect(markup).not.toContain(deadLabel);
    }
  });

  it("has unique field ids", () => {
    const ids = Object.keys(creationParameterDefaultsById);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("maps every field id to an echo-back entry in the engine settings projection", () => {
    const echoed = new Set(Object.keys(generatedSettingsFixture.creationParameters ?? {}));
    // research-api-key is write-only (persisted to .env, never echoed back).
    const missing = Object.keys(creationParameterDefaultsById).filter(
      (id) => !echoed.has(id) && id !== "research-api-key",
    );
    expect(missing).toEqual([]);
  });

  it("keeps fallback defaults aligned with the Engine Settings defaults", () => {
    const engineDefaults = generatedSettingsFixture.creationParameters ?? {};
    const mismatches = Object.entries(creationParameterDefaultsById).flatMap(([fieldId, value]) => {
      const engineValue = engineDefaults[fieldId];
      if (engineValue === undefined) return fieldId === "research-api-key" ? [] : [fieldId];
      if (fieldId === "memory-embedding-profile" && value === "auto" && engineValue === "") return [];
      const numericValue = Number(value);
      const numericEngineValue = Number(engineValue);
      if (value !== "" && engineValue !== "" && Number.isFinite(numericValue) && Number.isFinite(numericEngineValue)) {
        return numericValue === numericEngineValue ? [] : [fieldId];
      }
      return value === engineValue ? [] : [fieldId];
    });
    expect(mismatches).toEqual([]);
  });
});
