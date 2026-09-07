import { expect, test, type Page } from "@playwright/test";

const themeStorageKey = "nimo-ui-parity.theme";
const sessionStorageKey = "nimo.ui-session.v1";

async function openFreshWorkspace(page: Page): Promise<void> {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "先定手头所重" })).toBeVisible();
  await expect(page.locator(".side-rail")).toHaveCSS(
    "background-image",
    "linear-gradient(270deg, rgb(45, 30, 24) 0%, rgb(39, 27, 22) 45%, rgb(28, 19, 16) 100%)",
  );
  // At the standard desktop viewport the rail uses the normal design-token
  // track; compact density is applied only below the window threshold.
  await expect(page.locator(".rail-navigation")).toHaveCSS("width", "254px");
  await expect(page.locator(".rail-nav-item").first()).toHaveCSS("font-size", "19px");
  await expect(page.locator(".rail-footer")).toHaveCSS("text-align", "center");
}

async function navigate(page: Page, label: string, heading: string): Promise<void> {
  await page.getByRole("button", { name: label }).click();
  await expect(page.getByRole("heading", { name: heading })).toBeVisible();
}

async function selectDropdownOption(page: Page, label: string, option: string): Promise<void> {
  await page.getByLabel(label).click();
  await page.getByRole("listbox", { name: label }).getByRole("option", { name: option, exact: true }).click();
}

async function selectReaderProject(page: Page): Promise<void> {
  await selectDropdownOption(page, "选择阅卷项目", "测试长篇");
}

async function openChapterStudioParityState(page: Page, state: "prepared" | "running" | "checkpoint"): Promise<void> {
  await page.goto(`/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=${state}`);
  await expect(page.getByRole("heading", { name: "章台工作台" })).toBeVisible();
}

test.describe("NIMO primary-surface visual regression", () => {
  test("dashboard — narrative ember", async ({ page }) => {
    await openFreshWorkspace(page);
    await expect(page).toHaveScreenshot("dashboard-narrative-ember.png");
  });

  test("dashboard — collapsed navigation rail", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=dashboard&theme=narrative_ember&rail=collapsed");
    await expect(page.getByRole("heading", { name: "先定手头所重" })).toBeVisible();
    await expect(page.locator(".rail-nav-icon")).toHaveCount(7);
    await expect(page).toHaveScreenshot("dashboard-rail-collapsed-narrative-ember.png");
  });

  test("projects — empty reader", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await expect(page.getByText("请在案头选择一个项目，然后点击「阅卷」按钮来到此处。")).toBeVisible();
    await expect(page).toHaveScreenshot("projects-empty-narrative-ember.png");
  });

  test("projects — selected long reader", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await selectReaderProject(page);
    await expect(page.getByText("📖　测试长篇")).toBeVisible();
    await expect(page.getByRole("tab", { name: "基础设定" })).toBeVisible();
    await expect(page).toHaveScreenshot("projects-reader-narrative-ember.png");
  });

  test("projects — relationship graph", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await selectReaderProject(page);
    await page.getByRole("tab", { name: "角色与实体" }).click();
    const workspace = page.getByRole("region", { name: "角色与实体工作台" });
    await expect(workspace).toBeVisible();
    await workspace.getByRole("tab", { name: "图谱" }).click();
    await expect(workspace.getByRole("region", { name: "角色关系图谱" })).toBeVisible();
    await expect(page).toHaveScreenshot("projects-relationship-graph-narrative-ember.png");
  });

  test("projects — relationship graph character tooltip", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await selectReaderProject(page);
    await page.getByRole("tab", { name: "角色与实体" }).click();
    const workspace = page.getByRole("region", { name: "角色与实体工作台" });
    await workspace.getByRole("tab", { name: "图谱" }).click();
    const graph = workspace.getByRole("region", { name: "角色关系图谱" });
    await graph.getByRole("button", { name: "聚焦 林逐", exact: true }).hover({ force: true });
    const tooltip = page.locator(".relationship-graph-tooltip.is-character");
    await expect(tooltip).toBeVisible();
    await expect(page).toHaveScreenshot("projects-relationship-graph-character-tooltip-narrative-ember.png", { animations: "disabled" });
  });

  test("projects — source character relationship editor", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await selectReaderProject(page);
    await page.getByRole("tab", { name: "角色与实体" }).click();
    const workspace = page.getByRole("region", { name: "角色与实体工作台" });
    await workspace.getByRole("tab", { name: "关系" }).click();
    await expect(workspace.getByRole("table", { name: "角色关系表格" })).toBeVisible();
    await expect(page).toHaveScreenshot("projects-character-relationships-narrative-ember.png");
  });

  test("projects — narrative timeline", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await selectReaderProject(page);
    await page.getByRole("tab", { name: "叙事蓝图", exact: true }).click();
    const workspace = page.getByRole("region", { name: "叙事蓝图工作台" });
    await expect(workspace.getByRole("region", { name: "叙事时间线" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "支线管理", exact: true })).toBeVisible();
    await expect(page).toHaveScreenshot("projects-narrative-timeline-narrative-ember.png");
  });

  test("projects — top-level subplot management", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await selectReaderProject(page);
    await page.getByRole("tab", { name: "支线管理", exact: true }).click();
    const management = page.getByRole("region", { name: "支线管理工作台" });
    await expect(management.getByRole("button", { name: "应用到章节大纲" })).toBeVisible();
    await expect(page.getByLabel("支线对章节大纲的影响")).toBeVisible();
    await expect(management).toHaveScreenshot("projects-subplot-management-narrative-ember.png");
  });

  test("projects — source chapter report reader", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await selectReaderProject(page);
    await page.getByRole("tab", { name: "章节", exact: true }).click();
    const reader = page.getByRole("region", { name: "章节阅读器" });
    await reader.getByRole("tab", { name: "报告", exact: true }).click();
    await expect(page.getByRole("heading", { name: "质量评估" })).toBeVisible();
    await expect(page).toHaveScreenshot("projects-chapter-report-reader-narrative-ember.png");
  });

  test("projects — source rich document viewer", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await selectReaderProject(page);
    await page.getByRole("tab", { name: "章节", exact: true }).click();
    const reader = page.getByRole("region", { name: "章节阅读器" });
    await reader.getByRole("tab", { name: "报告", exact: true }).click();
    await page.getByRole("button", { name: "查看原始数据" }).click();
    const dialog = page.getByRole("dialog", { name: "质量评估 · 原始数据" });
    await expect(dialog.locator(".app-dialog > header h2")).toHaveCSS("font-size", "19px");
    await expect(dialog.getByRole("region", { name: "质量评估 · 文档查看器" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "复制全文" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "在文件夹中显示" })).toBeDisabled();
    await expect(dialog.getByText("3 个顶层字段")).toBeVisible();
    await expect(page).toHaveScreenshot("projects-rich-document-viewer-narrative-ember.png");
  });

  test("projects — source relationship tracking", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 卷帙 ·", "卷帙总览");
    await selectReaderProject(page);
    await page.getByRole("tab", { name: "追踪", exact: true }).click();
    const tracking = page.getByRole("region", { name: "关系追踪工作台" });
    await selectDropdownOption(page, "角色筛选", "周砚");
    await tracking.getByRole("button", { name: "刷新" }).click();
    await expect(page).toHaveScreenshot("projects-relationship-tracking-narrative-ember.png");
  });

  test("workflow — running task", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 机杼 ·", "任务流");
    await expect(page).toHaveScreenshot("workflow-running-narrative-ember.png");
  });

  test("workflow — stage artifact", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 机杼 ·", "任务流");
    // 步骤指示器：已完成步骤可点击查看真实产物（getStepArtifacts 链路）。
    await page.getByRole("button", { name: "叙事蓝图: 已完成" }).click();
    const dialog = page.getByRole("dialog", { name: "叙事蓝图 · 产出文件" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "产出文件" })).toBeVisible();
    await expect(dialog.getByText("叙事蓝图 · 共 1 个产出文件")).toBeVisible();
    // fixture 产物经共享 ContentRenderer 渲染（JSON 结构视图，含 blueprint/status 键）。
    await expect(dialog.locator(".document-json-key").filter({ hasText: "blueprint" })).toBeVisible();
    await expect(dialog.locator(".document-json-key").filter({ hasText: "status" })).toBeVisible();
    const bounds = await dialog.locator(".workflow-artifact-dialog").boundingBox();
    const viewport = page.viewportSize();
    expect(bounds).not.toBeNull();
    expect(viewport).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.y).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport!.width);
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(viewport!.height);
    await expect(page).toHaveScreenshot("workflow-stage-artifact-narrative-ember.png");
    await dialog.getByRole("button", { name: "原始 JSON" }).click();
    await expect(dialog.locator(".document-json-raw")).toContainText("\"status\": \"completed\"");
    await dialog.getByRole("button", { name: "结构视图" }).click();
    await expect(dialog.locator(".document-json-key").filter({ hasText: "blueprint" })).toBeVisible();
    await dialog.getByRole("button", { name: "关闭" }).click();
    await expect(dialog).toBeHidden();
  });

  test("workflow — chapter prose artifact uses the available reading pane", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 机杼 ·", "任务流");
    await page.getByRole("button", { name: "规格确认: 已完成" }).click();

    const dialog = page.getByRole("dialog", { name: "规格确认 · 产出文件" });
    const documentPane = dialog.locator(".artifact-reader-document");
    const paragraph = dialog.locator(".rendered-markdown p").first();
    await expect(paragraph).toBeVisible();

    const [paneBounds, paragraphBounds, typography] = await Promise.all([
      documentPane.boundingBox(),
      paragraph.boundingBox(),
      paragraph.evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          fontSize: Number.parseFloat(style.fontSize),
          lineHeight: Number.parseFloat(style.lineHeight),
        };
      }),
    ]);
    expect(paneBounds).not.toBeNull();
    expect(paragraphBounds).not.toBeNull();
    expect(paragraphBounds!.width / paneBounds!.width).toBeGreaterThan(0.72);
    expect(typography.fontSize).toBeGreaterThanOrEqual(17);
    expect(typography.lineHeight / typography.fontSize).toBeGreaterThanOrEqual(1.9);
  });

  test("workflow — source floating stream panel", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember&workflow_dialog=floating-stream");
    const panel = page.locator(".floating-stream-overlay");
    await expect(panel).toBeVisible();
    await expect(panel).toHaveCSS(
      "background-image",
      "linear-gradient(rgb(255, 250, 243), rgb(255, 250, 244))",
    );
    await expect(panel).toHaveScreenshot("task-focus-floating-stream-narrative-ember.png");
  });

  test("workflow — source error-log dialog", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember&workflow_dialog=error-log");
    const dialog = page.locator(".workflow-error-log-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveScreenshot("workflow-error-log-narrative-ember.png");
  });

  test("workflow — source stop-confirmation dialog", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember&workflow_dialog=cancel");
    const dialog = page.locator(".workflow-cancel-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveScreenshot("workflow-cancel-narrative-ember.png");
  });

  test("chapter studio — prepared chapter", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 章台 ·", "章台工作台");
    await expect(page).toHaveScreenshot("chapter-studio-prepared-narrative-ember.png");
  });

  test("chapter studio — decision checkpoint", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 章台 ·", "章台工作台");
    await page.getByRole("button", { name: "准备章节方案 →" }).click();
    await expect(page.getByRole("dialog", { name: "方案需确认" })).toBeVisible();
    await expect(page).toHaveScreenshot("chapter-studio-checkpoint-narrative-ember.png");
  });

  test("chapter studio — source running state", async ({ page }) => {
    await openChapterStudioParityState(page, "running");
    // 标题省略号对标 action_panel.py:262「正在执行…」（U+2026，非三个 ASCII 点）。
    await expect(page.getByRole("heading", { name: "正在执行…" })).toBeVisible();
    await expect(page).toHaveScreenshot("chapter-studio-running-narrative-ember.png");
  });

  test("chapter studio — source checkpoint state", async ({ page }) => {
    await openChapterStudioParityState(page, "checkpoint");
    await expect(page.getByRole("heading", { name: "章节方案待确认" })).toBeVisible();
    await expect(page).toHaveScreenshot("chapter-studio-source-checkpoint-narrative-ember.png");
  });

  test("chapter studio — source checkpoint floating panel", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=checkpoint&chapter_dialog=checkpoint");
    await expect(page.getByRole("dialog", { name: "方案需确认" })).toBeVisible();
    await expect(page).toHaveScreenshot("chapter-studio-source-checkpoint-dialog-narrative-ember.png");
  });

  test("voice studio — script ready", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 声腔 ·", "AI 配音工作室");
    await page.getByRole("tab", { name: "2 配音脚本" }).click();
    await expect(page.getByRole("heading", { name: "配音脚本" })).toBeVisible();
    await expect(page).toHaveScreenshot("voice-script-narrative-ember.png");
  });

  test("voice studio — source configured team", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&theme=narrative_ember");
    await expect(page.locator(".topbar-voice-summary")).toContainText("4");
    await expect(page.getByRole("heading", { name: "配音角色" })).toBeVisible();
    // 对标 PySide6 `_on_custom_tab_changed`：保存设置仅在最后一个「平台设置」 tab 显示。
    await expect(page.getByRole("button", { name: "保存设置" })).toHaveCount(0);
    await expect(page).toHaveScreenshot("voice-configured-narrative-ember.png");
    await page.getByRole("tab", { name: "平台设置" }).click();
    await expect(page.getByRole("button", { name: "保存设置" })).toBeVisible();
  });

  test("voice studio — source configured platform settings", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&voice_tab=settings&theme=narrative_ember");
    await expect(page.getByRole("tab", { name: "平台设置" })).toHaveAttribute("aria-selected", "true");
    // PySide6 fresh-page defaults expand the local model centre, quality plan,
    // and current provider; the remaining settings stay collapsed.
    await expect(page.getByRole("button", { name: "本机音频模型中心" })).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("button", { name: "MiniMax — 连接与模型" })).toHaveAttribute("aria-expanded", "true");
    await expect(page).toHaveScreenshot("voice-configured-settings-narrative-ember.png");
  });

  test("voice studio — source provider File ID dialog", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=voice_studio&theme=narrative_ember&voice_dialog=clone-provider-file-id");
    const dialog = page.locator(".voice-clone-source-dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByRole("textbox", { name: "供应商参考文件 ID" })).toBeFocused();
    await expect(page.getByRole("button", { name: "继续克隆" })).toBeDisabled();
    await expect(dialog).toHaveScreenshot("voice-clone-provider-id-narrative-ember.png");
  });

  test("voice studio — provider File ID enables the native-confirmation transition", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=voice_studio&theme=narrative_ember&voice_dialog=clone-provider-file-id");
    await page.getByRole("textbox", { name: "供应商参考文件 ID" }).fill("file_01H-parity");
    await expect(page.getByRole("button", { name: "继续克隆" })).toBeEnabled();
    await page.getByRole("button", { name: "继续克隆" }).click();
    await expect(page.getByRole("heading", { name: "正在准备克隆请求" })).toBeVisible();
  });

  test("settings — creative temperature expanded", async ({ page }) => {
    await openFreshWorkspace(page);
    await navigate(page, "· 火候 ·", "炼鼎通路，调鹽火候，令山河流载不穷。");
    await page.getByRole("button", { name: "创作火候 — 浮动与适用范围" }).click();
    const section = page.locator(".creative-temperature-section");
    await section.evaluate((element) => element.scrollIntoView({ block: "start" }));
    await expect(page.getByRole("combobox", { name: "启用创作火候浮动" })).toBeVisible();
    await expect(page).toHaveScreenshot("settings-creative-temperature-narrative-ember.png");
  });

  test("settings — source-shaped task routing", async ({ page }) => {
    await page.goto("/?__nimo_ui_parity=1&page=settings&theme=narrative_ember&settings_section=model-routing");
    const routing = page.locator(".settings-routing-card");
    await expect(routing.getByRole("tab", { name: "短篇流程" })).toBeVisible();
    await expect(routing.getByRole("tab", { name: "构思与节拍" })).toBeVisible();
    const groupBulk = routing.locator('[data-bulk-key="group:短篇流程"]');
    await groupBulk.getByRole("combobox", { name: "全组统一设定 统一主路由" }).selectOption("openai:gpt-4o-mini");
    await expect(groupBulk.locator("summary")).toContainText("已配置：阿里百炼 · Qwen Max → DeepSeek · Reasoner");
    await expect(page).toHaveScreenshot("settings-model-routing-narrative-ember.png");
  });

  for (const [themeId, label] of [
    ["narrative_ember", "玄炉"],
    ["ink_jade", "青墨"],
    ["ink_amethyst", "墨紫"],
    ["stillwater", "素蓝"],
    ["twilight_ink", "暮夜"],
  ] as const) {
    test(`settings — ${label}`, async ({ page }) => {
      await openFreshWorkspace(page);
      await navigate(page, "· 火候 ·", "炼鼎通路，调鹽火候，令山河流载不穷。");
      // Theme picker is now a 3×4 radiogroup grid (phase 1), not a <select>
      // Theme order in desktopThemes: narrative_ember(0), cinnabar_seal(1), autumn_apricot(2),
      // bamboo_mist(3), ink_jade(4), stillwater(5), ink_amethyst(6), snow_inkstone(7),
      // moonlit_paper(8), twilight_ink(9), indigo_night(10), pine_soot(11)
      const themeIndex: Record<string, number> = {
        narrative_ember: 0, ink_jade: 4, ink_amethyst: 6, stillwater: 5, twilight_ink: 9,
      };
      const themeGroup = page.getByRole("radiogroup", { name: "桌面主题" });
      const themeItem = themeGroup.getByRole("radio").nth(themeIndex[themeId]);
      await themeItem.click();
      // 点击后把主题网格滚到 .page-canvas 顶部的固定位置：click() 的
      // scroll-into-view 落点在并行负载下不确定（connection 卡片 content-visibility
      // 影响几何，青墨/暮夜基线曾因此漂移），显式滚到确定位置保证截图可复现。
      await page.locator(".page-canvas").evaluate((canvas) => {
        const group = canvas.querySelector('[role="radiogroup"]');
        if (group === null) return;
        const top = group.getBoundingClientRect().top - canvas.getBoundingClientRect().top + canvas.scrollTop;
        canvas.scrollTo({ top, behavior: "instant" });
      });
      // Wait for theme CSS variables to be applied
      await page.waitForTimeout(100);
      await expect(page).toHaveScreenshot(`settings-${themeId}.png`);
    });
  }

  for (const [fixture, label] of [
    ["model-profile-add", "add model profile"],
    ["model-profile-edit", "edit model profile"],
  ] as const) {
    test(`settings — source ${label} dialog`, async ({ page }) => {
      await page.goto(`/?__nimo_ui_parity=1&page=settings&theme=narrative_ember&settings_dialog=${fixture}`);
      const dialog = page.locator(".model-profile-source-dialog");
      await expect(dialog).toBeVisible();
      await expect(dialog).toHaveScreenshot(`settings-${fixture}-narrative-ember.png`);
    });
  }
});
