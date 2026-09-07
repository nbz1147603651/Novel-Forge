import { test, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

/**
 * Standalone parity capture: renders the React UI through the same restricted
 * `?__nimo_ui_parity=1` fixtures the PySide6 golden capture uses, then writes
 * full-frame 1440×900 screenshots to /tmp/nimo-parity-A/react/ with names that
 * mirror the PySide6 golden manifest (<page>--<theme>--<state>--1440x900.png).
 *
 * IMPORTANT: chapter_studio / voice_studio / settings are lazy-loaded behind
 * <Suspense fallback={LoadingSurface}>. A fixed timeout races the lazy chunk
 * and captures the skeleton instead of real content. We therefore wait for a
 * page-content selector that only exists AFTER the lazy component mounts.
 *
 * Run with: pnpm exec playwright test parity-capture.spec.ts
 */

const OUT_DIR = "/tmp/nimo-parity-A/react";
const THEMES = ["narrative_ember", "ink_jade", "ink_amethyst", "stillwater", "twilight_ink"] as const;
const PRIMARY_PAGES = ["dashboard", "workflow", "settings", "chapter_studio", "voice_studio"] as const;

test.describe.configure({ mode: "serial" });

/** Content-ready selector per page: the lazy component's root container class,
 *  which only exists AFTER the Suspense fallback is replaced by the real page. */
function readySelector(pg: string): string {
  switch (pg) {
    case "chapter_studio":
      return ".studio-page";
    case "voice_studio":
      return ".voice-page";
    case "settings":
      return ".settings-page";
    case "workflow":
      return ".workflow-page";
    case "projects":
      return ".reader-page";
    default:
      return ".dashboard-page";
  }
}

async function capture(page: Page, name: string, url: string, pg: string): Promise<void> {
  await page.goto(url);
  // Wait for the lazy page content to mount (defeats the Suspense skeleton),
  // then let theme tokens / fonts settle before the frame grab.
  await expect(page.locator(readySelector(pg)).first()).toBeVisible({ timeout: 15_000 });
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(OUT_DIR, `${name}.png`), animations: "disabled", caret: "hide" });
}

test.describe("React parity capture matrix", () => {
  test.beforeAll(() => {
    mkdirSync(OUT_DIR, { recursive: true });
  });

  test("primary pages × 5 themes (loaded state)", async ({ page }) => {
    for (const theme of THEMES) {
      for (const pg of PRIMARY_PAGES) {
        const name = `${pg}--${theme}--loaded--1440x900`;
        await capture(page, name, `/?__nimo_ui_parity=1&page=${pg}&theme=${theme}`, pg);
      }
    }
  });

  test("projects reader (narrative_ember)", async ({ page }) => {
    await capture(page, "projects--narrative_ember--loaded--1440x900", "/?__nimo_ui_parity=1&page=projects&theme=narrative_ember&reader=long", "projects");
  });

  test("chapter studio key states (narrative_ember)", async ({ page }) => {
    await capture(page, "chapter_studio--narrative_ember--chapter-running--1440x900", "/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=running", "chapter_studio");
    await capture(page, "chapter_studio--narrative_ember--chapter-checkpoint--1440x900", "/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=checkpoint", "chapter_studio");
    await capture(page, "chapter_studio--narrative_ember--chapter-checkpoint-dialog--1440x900", "/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=checkpoint&chapter_dialog=checkpoint", "chapter_studio");
  });

  test("workflow running (narrative_ember)", async ({ page }) => {
    await capture(page, "workflow--narrative_ember--workflow-running--1440x900", "/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember", "workflow");
  });

  test("voice studio configured team (narrative_ember)", async ({ page }) => {
    await capture(page, "voice_studio--narrative_ember--voice-configured-team--1440x900", "/?__nimo_ui_parity=1&page=voice_studio&theme=narrative_ember&voice_state=configured&voice_tab=team", "voice_studio");
  });

  test("settings expanded sections (narrative_ember)", async ({ page }) => {
    await capture(page, "settings--narrative_ember--section-creative-temperature--1440x900", "/?__nimo_ui_parity=1&page=settings&theme=narrative_ember&settings_section=creative-temperature", "settings");
    await capture(page, "settings--narrative_ember--section-model-routing--1440x900", "/?__nimo_ui_parity=1&page=settings&theme=narrative_ember&settings_section=model-routing", "settings");
  });
});
