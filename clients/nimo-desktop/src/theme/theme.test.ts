import { afterEach, describe, expect, it, vi } from "vitest";

import chapterDialogCss from "../styles/chapter-dialog.css?raw";
import dashboardBookshelfCss from "../styles/dashboard-bookshelf.css?raw";
import engineRuntimeCss from "../styles/engine-runtime.css?raw";
import readerFolioCss from "../styles/reader-folio.css?raw";
import themeSystemCss from "../styles/theme-system.css?raw";

import {
  applyTheme,
  desktopThemes,
  themeAppearance,
  themeIds,
  type SourceToken,
} from "./theme";

function relativeLuminance(hex: string): number {
  const channels = hex.slice(1).match(/.{2}/g)?.map((channel) => Number.parseInt(channel, 16) / 255) ?? [];
  return channels
    .map((channel) => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
    .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index]!, 0);
}

function contrastRatio(foreground: string, background: string): number {
  const lighter = Math.max(relativeLuminance(foreground), relativeLuminance(background));
  const darker = Math.min(relativeLuminance(foreground), relativeLuminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}

describe("desktop theme projection", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("provides every semantic status colour used by connection cards", () => {
    for (const theme of desktopThemes) {
      expect(theme.tokens["status.success"]).toMatch(/^#[0-9a-f]{6}$/i);
      expect(theme.tokens["status.warning"]).toMatch(/^#[0-9a-f]{6}$/i);
      expect(theme.tokens["status.danger"]).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("projects the source warm-border token for every desktop theme", () => {
    for (const theme of desktopThemes) {
      expect(theme.tokens["border.warm"]).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("keeps relationship workbench semantics in every theme palette", () => {
    for (const theme of desktopThemes) {
      expect(theme.tokens["relation.ally"]).toMatch(/^#[0-9a-f]{6}$/i);
      expect(theme.tokens["relation.tension"]).toMatch(/^#[0-9a-f]{6}$/i);
      expect(theme.tokens["relation.neutral"]).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("classifies only the three night palettes as dark browser appearances", () => {
    const darkThemes = themeIds.filter((themeId) => themeAppearance(themeId) === "dark");

    expect(darkThemes).toEqual(["twilight_ink", "indigo_night", "pine_soot"]);
    expect(themeAppearance("stillwater")).toBe("light");
    expect(themeAppearance("ink_amethyst")).toBe("light");
  });

  it("keeps core reading text AA-readable across every theme surface", () => {
    const readingPairs: readonly (readonly [SourceToken, SourceToken])[] = [
      ["text.primary", "bg.surface"],
      ["text.body", "bg.surface"],
      ["text.artifact", "bg.surface"],
      ["text.heading", "bg.dialog"],
    ];

    for (const theme of desktopThemes) {
      for (const [foreground, background] of readingPairs) {
        expect(
          contrastRatio(theme.tokens[foreground], theme.tokens[background]),
          `${theme.id}: ${foreground} on ${background}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    }
  });

  it("keeps late-loading workbench surfaces free of fixed pigments", () => {
    const managedStyles: readonly (readonly [string, string])[] = [
      ["chapter-dialog.css", chapterDialogCss],
      ["dashboard-bookshelf.css", dashboardBookshelfCss],
      ["engine-runtime.css", engineRuntimeCss],
      ["reader-folio.css", readerFolioCss],
      ["theme-system.css", themeSystemCss],
    ];

    for (const [name, source] of managedStyles) {
      const css = source.replace(/\/\*[\s\S]*?\*\//g, "");
      expect(css, name).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
      expect(css, name).not.toMatch(
        /\b(?:background|border(?:-color)?|box-shadow|color|fill|outline)\s*:[^;]*(?:black|white)(?=\s*(?:[;,)\n]|!important))/i,
      );
    }
  });

  it("updates colour tokens without enabling a global transition class", () => {
    const setProperty = vi.fn();
    const addTransitionClass = vi.fn();
    const setItem = vi.fn();
    const root = {
      classList: { add: addTransitionClass },
      dataset: {} as { colorScheme?: string; contrast?: string; theme?: string },
      style: { colorScheme: "", setProperty },
    };
    vi.stubGlobal("document", { documentElement: root });
    vi.stubGlobal("window", { localStorage: { setItem } });

    applyTheme("ink_jade");

    expect(root.dataset.theme).toBe("ink_jade");
    expect(root.dataset.colorScheme).toBe("light");
    expect(root.style.colorScheme).toBe("light");
    expect(setProperty).toHaveBeenCalled();
    expect(addTransitionClass).not.toHaveBeenCalled();

    const firstProjectionWrites = setProperty.mock.calls.length;
    applyTheme("ink_jade", { contrast: "high" });

    expect(setProperty).toHaveBeenCalledTimes(firstProjectionWrites);
    expect(root.dataset.contrast).toBe("high");

    applyTheme("twilight_ink");
    expect(setProperty.mock.calls.length).toBeGreaterThan(firstProjectionWrites);
    expect(root.dataset.colorScheme).toBe("dark");
  });
});
