import { expect, test } from "@playwright/test";

const sessionStorageKey = "nimo.ui-session.v1";
const themeStorageKey = "nimo-ui-parity.theme";

async function openReader(page: import("@playwright/test").Page): Promise<void> {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });

  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await expect(page.getByRole("heading", { name: "卷帙总览" })).toBeVisible();
  await page.getByLabel("选择阅卷项目").click();
  await page.getByRole("option", { name: "测试长篇", exact: true }).click();
  await expect(page.getByText("📖　测试长篇")).toBeVisible();
}

test("卷帙叙事蓝图与支线管理切换各自渲染对应工作台，不残留上一标签内容", async ({ page }) => {
  await openReader(page);

  const blueprintTab = page.getByRole("tab", { name: "叙事蓝图", exact: true });
  const subplotsTab = page.getByRole("tab", { name: "支线管理", exact: true });

  // 叙事蓝图 → 支线管理：两区块都复用 NarrativeToolsWorkbench，必须各自重新挂载，
  // 否则 tab 状态残留会让支线管理仍显示叙事时间线。
  await blueprintTab.click();
  await expect(blueprintTab).toHaveAttribute("aria-selected", "true");
  const blueprintWorkspace = page.getByRole("region", { name: "叙事蓝图工作台" });
  await expect(blueprintWorkspace.getByRole("region", { name: "叙事时间线" })).toBeVisible();

  await subplotsTab.click();
  await expect(subplotsTab).toHaveAttribute("aria-selected", "true");
  const subplotWorkspace = page.getByRole("region", { name: "支线管理工作台" });
  await expect(subplotWorkspace.getByRole("button", { name: "应用到章节大纲" })).toBeVisible();
  await expect(subplotWorkspace.getByRole("button", { name: "AI 生成支线" })).toBeVisible();
  await expect(subplotWorkspace.getByRole("region", { name: "叙事时间线" })).toHaveCount(0);

  // 反向：支线管理 → 叙事蓝图，同样不能残留支线工作台内容。
  await blueprintTab.click();
  await expect(blueprintTab).toHaveAttribute("aria-selected", "true");
  await expect(blueprintWorkspace.getByRole("region", { name: "叙事时间线" })).toBeVisible();
  await expect(page.getByRole("region", { name: "支线管理工作台" })).toHaveCount(0);

  // 往返一次后仍正确，确认非偶发路径问题。
  await subplotsTab.click();
  await expect(subplotWorkspace.getByRole("button", { name: "应用到章节大纲" })).toBeVisible();
});
