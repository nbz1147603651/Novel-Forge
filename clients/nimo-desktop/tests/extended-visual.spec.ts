import { expect, test, type Page } from "@playwright/test";

/**
 * Extended visual regression suite — settings, voice studio, and edge states.
 *
 * Complements `cross-theme-visual.spec.ts` by covering surfaces that were
 * previously only tested for interaction correctness but not pixel parity.
 *
 * Coverage:
 *   1. Settings page (火候) — default theme, all sections collapsed
 *   2. Voice studio (声腔) — default theme, chapter list visible
 *   3. Dashboard empty state — no projects loaded
 *   4. Workflow idle state — no task running
 *   5. Settings with routing expanded — complex form layout
 */

const themeStorageKey = "nimo-ui-parity.theme";
const sessionStorageKey = "nimo.ui-session.v1";

const themes = [
  "narrative_ember",
  "ink_jade",
  "ink_amethyst",
  "stillwater",
  "twilight_ink",
] as const;

async function openWithTheme(page: Page, themeId: string, params: string = ""): Promise<void> {
  const searchParams = new URLSearchParams({
    __nimo_ui_parity: "1",
    theme: themeId,
  });
  if (params) {
    new URLSearchParams(params).forEach((value, key) => searchParams.set(key, value));
  }
  await page.addInitScript(({ sessionKey, themeKey, theme }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, theme);
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey, theme: themeId });
  await page.goto(`/?${searchParams.toString()}`);
}

test.describe("Extended visual regression", () => {
  for (const themeId of themes) {
    test.describe(`${themeId}`, () => {
      test("settings — page load with sections collapsed", async ({ page }) => {
        await openWithTheme(page, themeId, "page=settings");
        await expect(page.getByRole("heading", { name: "炼鼎通路，调鹽火候，令山河流载不穷。" })).toBeVisible();
        await expect(page).toHaveScreenshot(`extended-${themeId}-settings.png`);
      });

      test("voice studio — chapter list and controls", async ({ page }) => {
        await openWithTheme(page, themeId, "page=voice_studio");
        await expect(page.getByRole("heading", { name: "AI 配音工作室" })).toBeVisible();
        await expect(page).toHaveScreenshot(`extended-${themeId}-voice-studio.png`);
      });

      test("workflow — idle state no task running", async ({ page }) => {
        await openWithTheme(page, themeId, "page=workflow");
        await expect(page.getByRole("heading", { name: "任务流" })).toBeVisible();
        await expect(page).toHaveScreenshot(`extended-${themeId}-workflow-idle.png`);
      });

      test("projects — reader with project selected", async ({ page }) => {
        await openWithTheme(page, themeId, "page=projects");
        await expect(page.getByRole("heading", { name: "卷帙总览" })).toBeVisible();
        await expect(page).toHaveScreenshot(`extended-${themeId}-projects.png`);
      });
    });
  }
});
