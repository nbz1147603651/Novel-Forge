import { expect, test } from "@playwright/test";

test("document-start native fixture takes precedence over the previous stored desktop route", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.clear();
    localStorage.setItem("nimo.ui-session.v1", JSON.stringify({ activePage: "chapter_studio", sidebarCollapsed: false }));
    Object.defineProperty(window, "__NIMO_UI_PARITY_QUERY__", {
      configurable: false,
      value: "?__nimo_ui_parity=1&page=dashboard&theme=narrative_ember&rail=collapsed",
    });
  });

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "先定手头所重" })).toBeVisible();
  await expect(page.locator(".nimo-app")).toHaveClass(/is-rail-collapsed/);
  await expect(page.getByRole("heading", { name: "章台工作台" })).toHaveCount(0);

  // The compact rail remains a usable navigation surface: icons stay visible,
  // the active target is marked, and the page can still change routes.
  await expect(page.locator(".rail-nav-icon")).toHaveCount(6);
  await expect(page.locator(".rail-nav-icon").first()).toBeVisible();
  await expect(page.locator(".rail-nav-item.is-active")).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".rail-nav-item.is-active")).toHaveCSS("width", "44px");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  await expect(page.getByRole("heading", { name: "读懂当前运行环境" })).toBeVisible();
  await expect(page.getByRole("button", { name: "· 火候 ·" })).toHaveAttribute("aria-current", "page");
});
