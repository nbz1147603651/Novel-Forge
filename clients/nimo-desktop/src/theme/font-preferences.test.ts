import { describe, expect, it } from "vitest";

import {
  defaultFontPreferences,
  fontSizeRamp,
  fontScaleChoices,
  normalizeFontPreferences,
  readingFontChoices,
  uiFontChoices,
} from "./font-preferences";

describe("desktop font preference contract", () => {
  it("keeps the settings choices and persisted IDs in one module", () => {
    expect(uiFontChoices.map((choice) => choice.id)).toEqual([
      "source_sans",
      "system_sans",
      "literary_serif",
    ]);
    expect(readingFontChoices.map((choice) => choice.id)).toEqual([
      "source_serif",
      "ui_sans",
      "calligraphy",
    ]);
    expect(fontScaleChoices.map((choice) => choice.value)).toEqual([0.9, 1, 1.1, 1.2]);
  });

  it("uses the PySide-aligned theme UI / reading serif default and clamps a saved scale", () => {
    expect(defaultFontPreferences).toEqual({ uiFamily: "source_sans", readingFamily: "source_serif", scale: 1 });
    expect(normalizeFontPreferences({ uiFamily: "source_sans", readingFamily: "source_serif", scale: 4 })).toEqual({
      uiFamily: "source_sans",
      readingFamily: "source_serif",
      scale: 1.2,
    });
  });

  it("keeps ordinary workbench typography compact and evenly graded", () => {
    expect(fontSizeRamp).toMatchObject({
      meta: 12,
      ui: 13,
      body: 14,
      subsection: 15,
      "section-sm": 16,
      section: 17,
      page: 19,
      display: 21,
    });
    expect(fontSizeRamp.section - fontSizeRamp.body).toBe(3);
    expect(fontSizeRamp.display - fontSizeRamp.body).toBeLessThanOrEqual(7);
  });

});
