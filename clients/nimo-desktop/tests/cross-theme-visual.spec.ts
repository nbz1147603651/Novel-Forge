import { expect, test, type Page } from "@playwright/test";

/**
 * Cross-theme visual regression suite.
 *
 * Captures key UI states across all 5 themes to detect unintended regressions
 * when design tokens or page-level styles change. Each theme gets its own
 * baseline snapshot so that theme-specific palettes are preserved.
 *
 * Key states:
 *   1. Dashboard (home page load)
 *   2. Chapter studio (page navigation / content-heavy)
 *   3. Workflow running (task execution in progress)
 *   4. Dialog open (modal overlay)
 *   5. Form validation error (invalid input state)
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

async function openDashboard(page: Page, themeId: string): Promise<void> {
  await openWithTheme(page, themeId);
  await expect(page.getByRole("heading", { name: "先定手头所重" })).toBeVisible();
}

test.describe("Cross-theme visual regression", () => {
  for (const themeId of themes) {
    test.describe(`${themeId}`, () => {
      test("dashboard — home page load", async ({ page }) => {
        await openDashboard(page, themeId);
        await expect(page).toHaveScreenshot(`cross-theme-${themeId}-dashboard.png`);
      });

      test("chapter studio — page navigation", async ({ page }) => {
        await openWithTheme(page, themeId, "page=chapter_studio");
        await expect(page.getByRole("heading", { name: "章台工作台" })).toBeVisible();
        await expect(page).toHaveScreenshot(`cross-theme-${themeId}-chapter-studio.png`);
      });

      test("workflow — task execution running", async ({ page }) => {
        await openWithTheme(page, themeId, "page=workflow");
        await expect(page.getByRole("heading", { name: "任务流" })).toBeVisible();
        await expect(page).toHaveScreenshot(`cross-theme-${themeId}-workflow-running.png`);
      });

      test("dialog — cancel confirmation open", async ({ page }) => {
        await openWithTheme(page, themeId, "page=workflow&workflow_dialog=cancel");
        const dialog = page.locator(".workflow-cancel-dialog");
        await expect(dialog).toBeVisible();
        await expect(dialog).toHaveScreenshot(`cross-theme-${themeId}-dialog-cancel.png`);
      });

      test("form validation — voice clone empty field", async ({ page }) => {
        await openWithTheme(page, themeId, "page=voice_studio&voice_dialog=clone-provider-file-id");
        const dialog = page.locator(".voice-clone-source-dialog");
        await expect(dialog).toBeVisible();
        await expect(page.getByRole("textbox", { name: "供应商参考文件 ID" })).toBeFocused();
        await expect(page.getByRole("button", { name: "继续克隆" })).toBeDisabled();
        await expect(dialog).toHaveScreenshot(`cross-theme-${themeId}-form-validation.png`);
      });
    });
  }
});
