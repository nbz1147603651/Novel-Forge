import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";

const sessionStorageKey = "nimo.ui-session.v1";
const themeStorageKey = "nimo-ui-parity.theme";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "settings", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
    localStorage.setItem("nimo:settings:routingSectionOpen", "1");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.locator("#nimo-settings-import-file").waitFor({ state: "attached" });
});

test("设置页顶栏导出完整配置而非模型占位提示", async ({ page }) => {
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出配置文件", exact: true }).click();
  const download = await downloadPromise;

  expect(download.suggestedFilename()).toBe("nimo_settings_export.json");
  const downloadPath = await download.path();
  expect(downloadPath).not.toBeNull();
  const payload = JSON.parse(await readFile(downloadPath!, "utf8")) as Record<string, unknown>;
  expect(payload.schemaVersion).toBe("nimo.settings.v1");
  expect(typeof payload.routes).toBe("object");
  expect(typeof payload.creationParameters).toBe("object");
  await expect(page.getByRole("status").filter({ hasText: "已导出模型、路由、创作参数、主题与字体设置" })).toBeVisible();
});

test("设置页顶栏导入完整配置到待保存草案", async ({ page }) => {
  const chooserPromise = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "导入配置文件", exact: true }).click();
  const chooser = await chooserPromise;
  await chooser.setFiles({
    name: "nimo-settings.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify({
      schemaVersion: "nimo.settings.v1",
      creationParameters: { "short-max-edit": "3" },
    })),
  });

  await expect(page.getByRole("status").filter({ hasText: "配置已导入当前草案" })).toBeVisible();
  await expect(page.getByRole("button", { name: "保存设置 · 有变更", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "短篇生成参数", exact: false }).click();
  await expect(page.getByRole("spinbutton", { name: "最大编辑轮次", exact: true })).toHaveValue("3");
});
