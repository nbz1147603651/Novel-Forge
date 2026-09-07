import { expect, test } from "@playwright/test";

const sessionStorageKey = "nimo.ui-session.v1";
const themeStorageKey = "nimo-ui-parity.theme";

test("火候页只在用户明确请求后开始连接检测", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });

  await page.goto("/");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  await expect(page.getByRole("heading", { name: "炼鼎通路，调鹽火候，令山河流载不穷。" })).toBeVisible();
  await expect(page.getByText("OpenAI · 待检测")).toBeVisible();

  const runButton = page.getByRole("button", { name: "检测全部通路" });
  await runButton.click();
  await expect(page.getByRole("button", { name: "检测中…" })).toBeDisabled();
  await expect(page.getByText("OpenAI · 检测中…")).toBeVisible();
  await expect(page.getByText("OpenAI · 连通 38ms")).toBeVisible();
  await expect(page.getByRole("button", { name: "检测全部通路" })).toBeEnabled();
  // The parity baseline captures the page as the user first entered it.  A
  // locator assertion may scroll the independently scrolling canvas while it
  // verifies a status card; restore that explicit viewport before comparing
  // pixels so the test checks the interface, not Playwright's focus plumbing.
  await page.locator(".page-canvas").evaluate((canvas) => canvas.scrollTo({ top: 0 }));
  await expect(page).toHaveScreenshot("settings-connectivity-success-narrative-ember.png");
});

test("火候页在原位展开创作火候并保留校验与保存反馈", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });

  await page.goto("/");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  const temperatureSection = page.getByRole("button", { name: "创作火候 — 浮动与适用范围" });
  const settingsHero = page.locator(".settings-hero");
  await temperatureSection.click();
  await expect(page.getByRole("combobox", { name: "启用创作火候浮动" })).toHaveValue("false");
  await page.getByRole("combobox", { name: "创作火候适用范围" }).selectOption("custom");
  await expect(page.getByRole("region", { name: "高级自定义浮动任务" })).toBeVisible();

  const lowerDelta = page.getByRole("spinbutton", { name: "创作火候下浮幅度" });
  await lowerDelta.fill("2.5");
  await settingsHero.getByRole("button", { name: "保存设置 · 有变更" }).click();
  await expect(page.getByText("请输入 0.0–2.0 之间的数值。")).toBeVisible();
  await lowerDelta.fill("0.3");
  await settingsHero.getByRole("button", { name: "保存设置 · 有变更" }).click();
  await expect(settingsHero.getByRole("button", { name: "已提交" })).toBeVisible();
});

test("火候页的同级折叠标签复用统一的字体契约", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });

  await page.goto("/");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  await expect(page.getByRole("heading", { name: "界面外观" })).toBeVisible();

  for (const label of ["界面主题 — 配色与风格", "创作火候 — 浮动与适用范围", "短篇生成参数"]) {
    const toggle = page.getByRole("button", { name: label });
    await expect(toggle).toHaveClass(/settings-accordion-toggle/);
    await expect(toggle).toHaveCSS("font-family", 'Georgia, "Songti SC", serif');
    await expect(toggle).toHaveCSS("font-size", "17px");
    await expect(toggle).toHaveCSS("font-weight", "600");
    await expect(toggle).toHaveCSS("line-height", "22.95px");
    await expect(toggle).toHaveCSS("min-height", "48px");
  }
});

test("火候页的后续参数分区可并行展开并参与统一保存", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  await expect(page.getByRole("heading", { name: "炼鼎通路，调鹽火候，令山河流载不穷。" })).toBeVisible();

  await page.getByRole("button", { name: "创作火候 — 浮动与适用范围" }).click();
  await page.getByRole("button", { name: "短篇生成参数" }).click();
  await expect(page.getByRole("combobox", { name: "启用创作火候浮动" })).toBeVisible();
  const maxEdit = page.getByRole("spinbutton", { name: "最大编辑轮次" });
  await maxEdit.fill("3");
  const settingsHero = page.locator(".settings-hero");
  await expect(settingsHero.getByRole("button", { name: "保存设置 · 有变更" })).toBeVisible();
  await settingsHero.getByRole("button", { name: "保存设置 · 有变更" }).click();
  await expect(settingsHero.getByRole("button", { name: "已提交" })).toBeVisible();
});

test("离开火候后保留用户发起的诊断会话，返回时不重新检测", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  await page.getByRole("button", { name: "检测全部通路" }).click();
  await expect(page.getByText("OpenAI · 检测中…")).toBeVisible();

  await page.getByRole("button", { name: "· 案头 ·" }).click();
  await expect(page.getByRole("heading", { name: "先定手头所重" })).toBeVisible();
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  await expect(page.getByText("OpenAI · 连通 38ms")).toBeVisible();
  await expect(page.getByRole("button", { name: "检测全部通路" })).toBeEnabled();
});

test("Token Plan 模型档案会填入套餐专属端点，密钥不进入浏览器持久化状态", async ({ page }) => {
  const sessionKey = "nimo.ui-session.v1";
  const themeKey = "nimo-ui-parity.theme";
  const sessionOnlySecret = "sk-sp-session-only-fixture-secret";
  await page.addInitScript(({ currentSessionKey, currentThemeKey }) => {
    localStorage.clear();
    localStorage.setItem(currentSessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(currentThemeKey, "narrative_ember");
  }, { currentSessionKey: sessionKey, currentThemeKey: themeKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  const connectionStatus = page.locator(".model-connection-status");
  const manageModels = connectionStatus.getByRole("button", { name: "模型管理 — 添加、编辑和删除模型" });
  await expect(manageModels).toHaveClass(/model-connection-manage/);
  await expect(manageModels).toHaveCSS("font-size", "12px");
  await expect(connectionStatus.getByRole("heading", { name: "模型连接状态" })).toHaveCSS("font-size", "17px");
  await expect(page.locator(".settings-placeholder")).toHaveCount(0);
  await manageModels.click();
  const workbench = page.getByRole("dialog", { name: "模型管理" });
  await expect(workbench).toBeVisible();
  const addModel = workbench.getByRole("button", { name: "＋ 添加模型" });
  await expect(addModel).toHaveClass(/model-registry-add/);
  await expect(addModel).toHaveCSS("min-height", "42px");
  await addModel.click();
  await page.getByLabel("供应商").selectOption("tongyi_token_plan");
  await expect(page.getByLabel("模型", { exact: true })).toHaveValue("qwen3.8-max");
  await page.getByLabel("模型", { exact: true }).selectOption("qwen3.7-plus");
  await expect(page.getByLabel("接口地址")).toHaveValue(
    "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
  );
  await expect(workbench.getByText("仅使用以 sk-sp- 开头的 Token Plan 专属 API Key；不得与按量付费或 Coding Plan 的密钥、接口地址混用。")).toBeVisible();
  await page.getByLabel("API Key").fill(sessionOnlySecret);
  await page.getByRole("button", { name: "添加模型", exact: true }).click();
  await expect(workbench.getByRole("heading", { name: "阿里百炼 Token Plan qwen3.7-plus" })).toBeVisible();
  const persistedStorage = await page.evaluate(() => Object.values(localStorage).join("\n"));
  expect(persistedStorage).not.toContain(sessionOnlySecret);
});

test("流程路由保留旧端的分组、任务级火候与可排序备用链路", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });

  await page.goto("/");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  await page.getByRole("button", { name: "流程路由 — 为每个步骤选择模型与能力" }).click();
  const workbench = page.locator(".settings-routing-card");
  await expect(workbench).toBeVisible();

  await expect(workbench.getByRole("tab", { name: "短篇流程" })).toBeVisible();
  await expect(workbench.getByRole("tab", { name: "长篇初始化" })).toBeVisible();
  await workbench.getByRole("tab", { name: "构思与节拍" }).click();
  const temperature = workbench.getByRole("spinbutton", { name: "规格丰富 温度" });
  await temperature.fill("1.23");
  await expect(temperature).toHaveValue("1.23");

  const fallback = workbench.locator(".route-row").filter({ hasText: "规格丰富" }).locator("details.route-fallbacks");
  await expect(fallback).toHaveCount(1);
  await fallback.locator("summary").click();
  await expect(fallback.getByText("备用路由排序")).toBeVisible();
  await expect(fallback.getByText("前 3 生效")).toBeVisible();
  await fallback.getByRole("button", { name: "将 DeepSeek · Reasoner 上移" }).click();

  await workbench.getByRole("combobox", { name: "构思与节拍 批量设定 统一主路由" }).selectOption("deepseek:reasoner");
  const groupTemperature = workbench.getByRole("spinbutton", { name: "构思与节拍 批量设定 统一温度" });
  await groupTemperature.fill("0.66");
  await workbench.getByRole("button", { name: "应用本组" }).last().click();
  await expect(temperature).toHaveValue("0.66");
  await expect(workbench.getByText("已将「短篇流程」的 2 个步骤更新为统一主路由、备用链与能力草案；温度已同步到可设置的步骤，点击“保存设置”后生效。")).toBeVisible();

  await workbench.getByRole("combobox", { name: "规格丰富 主路由" }).selectOption("deepseek:reasoner");
  const topBar = page.locator(".top-bar");
  await expect(topBar.getByRole("button", { name: "保存设置 · 有变更" })).toBeVisible();
  await topBar.getByRole("button", { name: "保存设置 · 有变更" }).click();
  await expect(workbench.getByText("正在保存模型路由与运行参数…")).toBeVisible();
  await expect(workbench.getByText(/前端演练已接受/)).toBeVisible();
});

test("流程路由将批量备用链与折叠后的同一草案保持在一起", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });

  await page.goto("/");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  const routingToggle = page.getByRole("button", { name: "流程路由 — 为每个步骤选择模型与能力" });
  await routingToggle.click();
  const workbench = page.locator(".settings-routing-card");

  await workbench.getByRole("tab", { name: "短篇流程" }).click();
  await workbench.getByRole("tab", { name: "构思与节拍" }).click();
  await workbench.getByRole("combobox", { name: "规格丰富 主路由" }).selectOption("");
  const inheritedRoute = workbench.locator('.route-row:has(select[aria-label="规格丰富 主路由"])');
  await expect(inheritedRoute).toHaveClass(/is-inherited/);
  await expect(inheritedRoute.getByText("继承 · 高级")).toBeVisible();

  const groupBulk = workbench.locator('[data-bulk-key="group:短篇流程"]');
  await groupBulk.getByRole("combobox", { name: "全组统一设定 统一主路由" }).selectOption("openai:gpt-4o-mini");
  await groupBulk.locator("summary").click();
  await expect(groupBulk.getByText("备用路由排序")).toBeVisible();
  await groupBulk.getByRole("button", { name: "将 阿里百炼 · Qwen Max 下移" }).click();
  await groupBulk.getByRole("button", { name: "应用本组" }).click();
  await expect(workbench.getByText("已将「短篇流程」的 5 个步骤更新为统一主路由、备用链与能力草案；温度保持任务级设置，点击“保存设置”后生效。")).toBeVisible();

  await routingToggle.click();
  await routingToggle.click();
  await expect(workbench.locator('[data-bulk-key="group:短篇流程"]')).toContainText("已配置：DeepSeek · Reasoner → 阿里百炼 · Qwen Max");
});

test("长篇路由不使用嵌套滚动条，所有步骤完整随设置页展开", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });

  await page.goto("/");
  await page.getByRole("button", { name: "· 火候 ·" }).click();
  await page.getByRole("button", { name: "流程路由 — 为每个步骤选择模型与能力" }).click();
  const panel = page.locator(".routing-group-panel");
  await page.getByRole("tab", { name: "长篇初始化" }).click();
  const subgroupTabs = panel.getByRole("tablist", { name: "长篇初始化 二级阶段分类" });
  await expect(subgroupTabs).toBeVisible();

  await expect(page.locator(".routing-group-scrollbar")).toHaveCount(0);
  const layout = await panel.evaluate((element) => {
    const panelBounds = element.getBoundingClientRect();
    const routes = [...element.querySelectorAll<HTMLElement>(".route-row")];
    return {
      clippedRows: routes.flatMap((route) => {
        const bounds = route.getBoundingClientRect();
        return bounds.top >= panelBounds.top && bounds.bottom <= panelBounds.bottom + 1
          ? []
          : [{ top: bounds.top, bottom: bounds.bottom, panelTop: panelBounds.top, panelBottom: panelBounds.bottom }];
      }),
      hasRoutes: routes.length > 0,
      hasNestedOverflow: element.scrollHeight > element.clientHeight + 1,
    };
  });
  expect(layout.hasRoutes).toBe(true);
  expect(layout.clippedRows).toEqual([]);
  expect(layout.hasNestedOverflow).toBe(false);
});
