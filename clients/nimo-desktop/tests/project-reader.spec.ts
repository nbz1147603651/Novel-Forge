import { expect, test } from "@playwright/test";

const sessionStorageKey = "nimo.ui-session.v1";
const themeStorageKey = "nimo-ui-parity.theme";

async function selectDropdownOption(page: import("@playwright/test").Page, label: string, option: string): Promise<void> {
  await page.getByLabel(label).click();
  await page.getByRole("listbox", { name: label }).getByRole("option", { name: option, exact: true }).click();
}

async function selectReaderProject(page: import("@playwright/test").Page, name: string): Promise<void> {
  await selectDropdownOption(page, "选择阅卷项目", name);
}

async function openSourceCharacterGraph(page: import("@playwright/test").Page): Promise<import("@playwright/test").Locator> {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  await page.getByRole("tab", { name: "角色与实体", exact: true }).click();
  const workspace = page.getByRole("region", { name: "角色与实体工作台" });
  await workspace.getByRole("tab", { name: "图谱", exact: true }).click();
  return workspace.getByRole("region", { name: "角色关系图谱" });
}

test("卷帙可按源端章节阅读器切换报告并查看只读原始 JSON", async ({ page }) => {
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  await expect(page.getByRole("tab", { name: "章节", exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "章节", exact: true }).click();
  const reader = page.getByRole("region", { name: "章节阅读器" });
  await expect(reader).toBeVisible();
  const chapterRows = reader.locator(".reader-chapter-list button");
  const finalChapter = chapterRows.filter({ hasText: "第 4 章 · 高架桥下" });
  const draftChapter = chapterRows.filter({ hasText: "第 5 章 · 档案室对照" });
  const pendingChapter = chapterRows.filter({ hasText: "第 6 章 · 权限追踪" });
  await expect(finalChapter).toContainText("终稿");
  await expect(draftChapter).toContainText("草稿");
  await expect(pendingChapter).toContainText("待续写");
  await draftChapter.click();
  await expect(reader.getByRole("heading", { name: "章节草稿（v_humanize_candidate）" })).toBeVisible();
  await expect(reader.getByText("档案室的雨声比桥下更轻。")).toBeVisible();
  await finalChapter.click();
  await reader.getByRole("tab", { name: "报告", exact: true }).click();
  await expect(page.getByRole("heading", { name: "质量评估" })).toBeVisible();
  await expect(page.getByText("章节已保持主线推进：时间戳出现、林逐选择隐瞒、与周砚的信任裂缝被明确。")).toBeVisible();

  await page.getByRole("button", { name: "查看原始数据" }).click();
  const dialog = page.getByRole("dialog", { name: "质量评估 · 原始数据" });
  await expect(dialog).toBeVisible();
  const viewer = dialog.getByRole("region", { name: "质量评估 · 文档查看器" });
  await expect(viewer).toBeVisible();
  await expect(viewer.getByRole("button", { name: "在文件夹中显示" })).toBeDisabled();
  await expect(dialog.locator(".document-json-key").filter({ hasText: "source" })).toBeVisible();
  await expect(dialog.locator(".document-json-key").filter({ hasText: "facts" })).toBeVisible();
  await expect(dialog.locator(".document-json-key").filter({ hasText: "paragraphs" })).toBeVisible();
  await expect(dialog.locator(".document-json-value").filter({ hasText: "reports/chapter_004_eval.json" }).first()).toBeVisible();
  await viewer.getByRole("button", { name: "复制全文" }).click();
  await expect(viewer.getByRole("status")).toHaveText("已复制全文");
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toContain("reports/chapter_004_eval.json");
  await viewer.getByRole("button", { name: "切换原始 JSON" }).click();
  await expect(dialog.locator(".document-json-raw")).toContainText("\"source\":\"reports/chapter_004_eval.json\"");
  await viewer.getByRole("button", { name: "返回富视图" }).click();
  await expect(dialog.locator(".document-json-value").filter({ hasText: "reports/chapter_004_eval.json" }).first()).toBeVisible();
  await page.getByRole("button", { name: "关闭对话框" }).click();
  await expect(dialog).toHaveCount(0);
});

test("卷帙的角色与实体页复用档案、关系与图谱工作台", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");

  const outerCategory = page.getByLabel("卷帙分类").getByRole("tab", { name: "基础设定", exact: true });
  const innerCategory = page.getByLabel("基础设定分类").getByRole("tab", { name: "故事规格", exact: true });
  const outerFontSize = await outerCategory.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize));
  const innerFontSize = await innerCategory.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize));
  expect(outerFontSize).toBeGreaterThan(innerFontSize);

  await page.getByRole("tab", { name: "角色与实体", exact: true }).click();
  const workspace = page.getByRole("region", { name: "角色与实体工作台" });
  await expect(workspace).toBeVisible();
  await expect(workspace.getByRole("tab", { name: "档案" })).toHaveAttribute("aria-selected", "true");
  await workspace.getByRole("tab", { name: "图谱" }).click();
  await expect(workspace.getByRole("region", { name: "角色关系图谱" })).toBeVisible();
  const graph = workspace.getByRole("region", { name: "角色关系图谱" });
  const zhouNode = graph.getByRole("button", { name: "聚焦 周砚", exact: true });
  await zhouNode.click();
  await expect(zhouNode).toHaveClass(/is-selected/);
  expect(await graph.locator(".relationship-edge.is-active animateMotion").count()).toBeGreaterThan(0);
  await expect(workspace.getByRole("button", { name: "周砚 · 配角", exact: true })).toHaveClass(/is-active/);
  // The source halo is intentionally in motion while a node is focused;
  // force keeps the pointer-state assertion independent from that animation.
  await zhouNode.hover({ force: true });
  const characterTooltip = page.locator(".relationship-graph-tooltip.is-character");
  await expect(characterTooltip).toBeVisible();
  await expect(characterTooltip).toContainText("周砚");
  await expect(characterTooltip).toContainText("性格");
  const tooltipBounds = await characterTooltip.boundingBox();
  expect(tooltipBounds).not.toBeNull();
  // The source tooltip must remain a usable compact overlay, regardless of
  // the active desktop density scale or its responsive max-width.
  expect(tooltipBounds!.width).toBeGreaterThanOrEqual(200);
  expect(tooltipBounds!.width).toBeLessThanOrEqual(282);
  expect(tooltipBounds!.x).toBeGreaterThanOrEqual(8);
  expect(tooltipBounds!.y).toBeGreaterThanOrEqual(8);
  expect(tooltipBounds!.x + tooltipBounds!.width).toBeLessThanOrEqual(1432);
  expect(tooltipBounds!.y + tooltipBounds!.height).toBeLessThanOrEqual(892);
  const firstEdge = graph.locator(".relationship-edge").first();
  await firstEdge.hover({ force: true });
  const relationshipTooltip = page.locator(".relationship-graph-tooltip.is-relationship");
  await expect(relationshipTooltip).toBeVisible();
  await expect(relationshipTooltip).toContainText("图层：relationship");
  const relationshipTooltipBounds = await relationshipTooltip.boundingBox();
  expect(relationshipTooltipBounds).not.toBeNull();
  expect(relationshipTooltipBounds!.width).toBeGreaterThanOrEqual(180);
  expect(relationshipTooltipBounds!.width).toBeLessThanOrEqual(248);
  await graph.getByLabel("可聚焦角色节点的关系网络").click({ position: { x: 16, y: 160 } });
  await expect(zhouNode).not.toHaveClass(/is-selected/);
  await expect(graph.locator(".relationship-edge.is-active animateMotion")).toHaveCount(0);
  await expect(page.locator(".relationship-graph-tooltip")).toHaveCount(0);
  await expect(workspace.getByRole("button", { name: "周砚 · 配角", exact: true })).toHaveClass(/is-active/);
  await workspace.getByRole("button", { name: "编辑角色", exact: true }).click();
  await expect(workspace.getByRole("tab", { name: "档案", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(workspace.getByRole("heading", { name: "周砚", exact: true })).toBeVisible();
});

test("卷帙角色图谱复刻源端的右键编辑、关系编辑与移除", async ({ page }) => {
  const graph = await openSourceCharacterGraph(page);
  const linNode = graph.getByRole("button", { name: "聚焦 林逐", exact: true });
  // The source graph intentionally pulses a focused node, so force avoids
  // treating that visual feedback animation as a stale click target.
  await linNode.click({ button: "right", force: true });
  const nodeMenu = page.getByRole("menu", { name: "角色图谱操作菜单" });
  await expect(nodeMenu.getByRole("menuitem")).toHaveText(["编辑角色", "标记退场", "新增角色"]);
  await nodeMenu.getByRole("menuitem", { name: "编辑角色", exact: true }).click();
  const workspace = page.getByRole("region", { name: "角色与实体工作台" });
  await expect(workspace.getByRole("tab", { name: "档案", exact: true })).toHaveAttribute("aria-selected", "true");

  await workspace.getByRole("tab", { name: "图谱", exact: true }).click();
  const relationEdge = graph.getByRole("button", { name: "查看 同盟 · 不互信：林逐 到 周砚", exact: true });
  const edgePoint = await relationEdge.evaluate((element) => {
    const svg = element.closest("svg");
    const line = element.querySelector("line");
    if (svg === null || line === null) throw new Error("角色图谱连线未准备好");
    const viewBox = (svg.getAttribute("viewBox") ?? "").trim().split(/\s+/).map(Number);
    const rect = svg.getBoundingClientRect();
    if (viewBox.length !== 4 || viewBox[2] === 0 || viewBox[3] === 0) throw new Error("角色图谱坐标无效");
    const x1 = Number(line.getAttribute("x1"));
    const x2 = Number(line.getAttribute("x2"));
    const y1 = Number(line.getAttribute("y1"));
    const y2 = Number(line.getAttribute("y2"));
    return {
      x: rect.left + ((((x1 + x2) / 2) - viewBox[0]!) / viewBox[2]!) * rect.width,
      y: rect.top + ((((y1 + y2) / 2) - viewBox[1]!) / viewBox[3]!) * rect.height,
    };
  });
  await page.mouse.click(edgePoint.x, edgePoint.y, { button: "right" });
  const edgeMenu = page.getByRole("menu", { name: "角色图谱操作菜单" });
  await expect(edgeMenu.getByRole("menuitem")).toHaveText(["编辑关系", "移除关系"]);
  await edgeMenu.getByRole("menuitem", { name: "编辑关系", exact: true }).click();
  const relationshipDialog = page.getByRole("dialog", { name: "编辑关系" });
  await expect(relationshipDialog).toContainText("林逐 的关系");
  await expect(relationshipDialog.getByLabel("关系类型").locator("option")).toHaveCount(10);
  await expect(relationshipDialog.getByLabel("关系类型")).toHaveValue("同盟");
  await expect(relationshipDialog.getByLabel("关系描述")).toHaveAttribute("placeholder", "关系描述，例如：彼此试探但在关键行动中互相掩护。");
  const sourceDialogMetrics = await relationshipDialog.locator(".source-relationship-dialog").evaluate((dialog) => {
    const dialogRect = dialog.getBoundingClientRect();
    const controlRect = dialog.querySelector("select")?.getBoundingClientRect();
    return {
      controlLeft: controlRect === null ? 0 : controlRect.left - dialogRect.left,
      dialogHeight: dialogRect.height,
      dialogWidth: dialogRect.width,
    };
  });
  // The source editor remains compact and two-column at the active desktop
  // density; exact pixels are deliberately covered by the visual suite.
  expect(sourceDialogMetrics.dialogWidth).toBeGreaterThanOrEqual(300);
  expect(sourceDialogMetrics.dialogHeight).toBeGreaterThanOrEqual(260);
  expect(sourceDialogMetrics.controlLeft).toBeGreaterThan(0);
  expect(sourceDialogMetrics.controlLeft).toBeLessThan(sourceDialogMetrics.dialogWidth / 2);
  await relationshipDialog.getByRole("button", { name: "取消", exact: true }).click();

  await page.mouse.click(edgePoint.x, edgePoint.y, { button: "right" });
  await page.getByRole("menu", { name: "角色图谱操作菜单" }).getByRole("menuitem", { name: "移除关系", exact: true }).click();
  await page.getByRole("dialog", { name: "移除关系" }).getByRole("button", { name: "移除", exact: true }).click();
  await expect(graph.getByRole("button", { name: "查看 同盟 · 不互信：林逐 到 周砚", exact: true })).toHaveCount(0);
});

test("卷帙角色图谱从节点边缘拖拽后打开源端关系表单", async ({ page }) => {
  const graph = await openSourceCharacterGraph(page);
  const source = graph.getByRole("button", { name: "聚焦 林逐", exact: true });
  const target = graph.getByRole("button", { name: "聚焦 周砚", exact: true });
  const sourceGeometry = await source.evaluate((element) => {
    const svg = element.closest("svg");
    const circle = element.querySelector("circle:not(.relationship-node-halo)");
    if (svg === null || circle === null) throw new Error("角色图谱节点未准备好");
    const transform = element.getAttribute("transform") ?? "";
    const values = transform.match(/[-+]?\d*\.?\d+/g)?.map(Number) ?? [];
    const viewBox = (svg.getAttribute("viewBox") ?? "").trim().split(/\s+/).map(Number);
    const rect = svg.getBoundingClientRect();
    if (viewBox.length !== 4 || viewBox[2] === 0 || viewBox[3] === 0) throw new Error("角色图谱坐标无效");
    const center = { x: rect.left + (((values[0] ?? 0) - viewBox[0]!) / viewBox[2]!) * rect.width, y: rect.top + (((values[1] ?? 0) - viewBox[1]!) / viewBox[3]!) * rect.height };
    return { center, radius: (Number(circle.getAttribute("r") ?? "0") / viewBox[2]!) * rect.width };
  });
  const targetCenter = await target.evaluate((element) => {
    const svg = element.closest("svg");
    if (svg === null) throw new Error("角色图谱画布未准备好");
    const transform = element.getAttribute("transform") ?? "";
    const values = transform.match(/[-+]?\d*\.?\d+/g)?.map(Number) ?? [];
    const viewBox = (svg.getAttribute("viewBox") ?? "").trim().split(/\s+/).map(Number);
    const rect = svg.getBoundingClientRect();
    if (viewBox.length !== 4 || viewBox[2] === 0 || viewBox[3] === 0) throw new Error("角色图谱坐标无效");
    return { x: rect.left + (((values[0] ?? 0) - viewBox[0]!) / viewBox[2]!) * rect.width, y: rect.top + (((values[1] ?? 0) - viewBox[1]!) / viewBox[3]!) * rect.height };
  });
  await page.mouse.move(sourceGeometry.center.x + sourceGeometry.radius, sourceGeometry.center.y);
  await page.mouse.down();
  await page.mouse.move(targetCenter.x, targetCenter.y, { steps: 4 });
  await page.mouse.up();

  const dialog = page.getByRole("dialog", { name: "编辑关系" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("关系对象")).toHaveValue("zhou-yan");
  await expect(dialog.getByLabel("关系类型")).toHaveValue("一般关系");
  await expect(dialog.getByLabel("关系描述")).toHaveValue("第 4 章：林逐隐瞒夜间时间戳。");
});

test("卷帙的叙事蓝图页以源端直接文档方式复用可交互时间线", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");

  await page.getByRole("tab", { name: "叙事蓝图", exact: true }).click();
  const workspace = page.getByRole("region", { name: "叙事蓝图工作台" });
  await expect(workspace).toBeVisible();
  const timeline = workspace.getByRole("region", { name: "叙事时间线" });
  await expect(timeline).toBeVisible();
  // The source timeline renders phase blocks, milestone diamonds, subplot
  // lanes and the 角色弧光 section; assert each track is present.
  await expect(timeline.locator(".timeline-phase").first()).toBeVisible();
  await expect(timeline.locator(".timeline-milestone").first()).toBeVisible();
  await expect(timeline.locator(".timeline-lane").first()).toBeVisible();
  await expect(timeline.locator(".timeline-arc")).toHaveCount(2);
  await expect(timeline.locator(".timeline-arc-milestone").first()).toBeVisible();
});

test("卷帙覆盖资料、直接文档、治理与追踪的源端分类和操作", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");

  const outerTabs = page.getByLabel("卷帙分类");
  await expect(outerTabs.getByRole("tab")).toHaveCount(9);

  await outerTabs.getByRole("tab", { name: "资料检索", exact: true }).click();
  await page.getByLabel("资料检索分类").getByRole("tab", { name: "资料分析报告", exact: true }).click();
  await expect(page.getByRole("heading", { name: "资料分析报告" })).toBeVisible();

  await outerTabs.getByRole("tab", { name: "章节设计矩阵", exact: true }).click();
  await expect(page.getByRole("heading", { name: "章节设计矩阵" })).toBeVisible();

  await outerTabs.getByRole("tab", { name: "章节大纲", exact: true }).click();
  const outline = page.getByRole("region", { name: "章节大纲工作台" });
  await expect(outline).toBeVisible();
  await outline.getByRole("button", { name: "延长全书" }).click();
  await outline.getByRole("button", { name: "确认延长全书" }).click();
  await expect(page.getByText("延长全书任务已提交；新章节生成后会自动同步章节契约。")).toBeVisible();

  await outerTabs.getByRole("tab", { name: "治理", exact: true }).click();
  await expect(page.getByRole("heading", { name: "一致性审计" })).toBeVisible();
  await page.getByLabel("治理分类").getByRole("tab", { name: "初始化准入", exact: true }).click();
  await expect(page.getByRole("heading", { name: "初始化准入" })).toBeVisible();

  await outerTabs.getByRole("tab", { name: "追踪", exact: true }).click();
  const tracking = page.getByRole("region", { name: "关系追踪工作台" });
  await expect(tracking).toBeVisible();
  await selectDropdownOption(page, "角色筛选", "周砚");
  await tracking.getByRole("button", { name: "刷新" }).click();
  await expect(page.getByText("已按当前筛选刷新关系追踪。")).toBeVisible();
  await page.getByLabel("追踪分类").getByRole("tab", { name: "Token 追踪", exact: true }).click();
  const token = page.getByRole("region", { name: "Token 追踪工作台" });
  await expect(token).toBeVisible();
  await token.getByRole("tab", { name: "价格", exact: true }).click();
  await token.getByLabel("显示货币").selectOption("CNY");
  await expect(token.getByRole("button", { name: "保存设置 *" })).toBeEnabled();
});

test("卷帙的终稿砚修经 Engine 生成候选并保留分组报告", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  await page.getByRole("tab", { name: "章节", exact: true }).click();

  const reader = page.getByRole("region", { name: "章节阅读器" });
  const revision = reader.getByRole("region", { name: "终稿修订" });
  await revision.getByRole("button", { name: "入砚修" }).click();
  const editor = revision.getByLabel("第 4 章终稿编辑器");
  await editor.focus();
  await page.keyboard.press("Meta+A");
  await expect(revision.getByText("已选择文本")).toBeVisible();
  await revision.getByRole("button", { name: "选段精修" }).click();
  const dialog = page.getByRole("dialog", { name: /选中约 .* 字/ });
  await expect(dialog.getByRole("button", { name: "更凝练" })).toBeVisible();
  await dialog.getByRole("button", { name: "更凝练" }).click();
  await dialog.getByRole("button", { name: "生成候选" }).click();
  await expect(revision.getByText("Engine 精修候选 · 尚未纳入正文")).toBeVisible();
  await revision.getByRole("button", { name: "纳入正文" }).click();
  await expect(revision.getByRole("button", { name: "保存定稿" })).toBeEnabled();
  await revision.getByRole("button", { name: "保存定稿" }).click();
  const preview = page.getByRole("dialog", { name: "保存终稿修订" });
  await expect(preview.getByText("保存后约")).toBeVisible();
  await expect(preview.locator(".reader-revision-diff")).toContainText("--- 保存前");
  expect(await preview.locator("footer .button").allTextContents()).toEqual(["确认保存", "继续修订"]);
  await expect(preview.locator(".reader-revision-dialog")).toHaveScreenshot("project-reader-final-revision-save-preview.png", { animations: "disabled" });
  await preview.getByRole("button", { name: "确认保存" }).click();
  const scope = page.getByRole("dialog", { name: "保存影响范围" });
  await scope.getByRole("radio", { name: "仅下一章" }).check();
  await scope.getByRole("checkbox", { name: "保存后后台重评本章" }).check();
  await scope.getByRole("button", { name: "按此范围保存" }).click();
  await expect(page.getByText("演练引擎已持久化终稿修订。")).toBeVisible();

  await reader.getByRole("tab", { name: "报告", exact: true }).click();
  const reports = page.getByRole("region", { name: "章节报告阅读器" });
  await reports.getByRole("tab", { name: "表达", exact: true }).click();
  await reports.getByRole("tab", { name: "润色对比", exact: true }).click();
  await expect(reports.getByRole("heading", { name: "润色对比" })).toBeVisible();
});

test("卷帙终稿砚修在返回阅稿前保护未保存草稿", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  await page.getByRole("tab", { name: "章节", exact: true }).click();

  const revision = page.getByRole("region", { name: "章节阅读器" }).getByRole("region", { name: "终稿修订" });
  await revision.getByRole("button", { name: "入砚修" }).click();
  const editor = revision.getByLabel("第 4 章终稿编辑器");
  await editor.fill("雨声压在高架桥底。\n\n林逐并无立刻拨给周砚。");
  await revision.getByRole("button", { name: "回到阅稿" }).click();
  const confirm = page.getByRole("dialog", { name: "终稿修订尚未保存" });
  await expect(confirm).toBeVisible();
  expect(await confirm.locator(".source-message-dialog-actions .button").allTextContents()).toEqual(["保存定稿", "放弃修改", "继续修订"]);
  await confirm.getByRole("button", { name: "放弃修改" }).click();
  await expect(revision.getByRole("button", { name: "入砚修" })).toBeVisible();
  await expect(page.getByText("已放弃未保存的砚修草稿并回到阅稿")).toBeVisible();
});

test("卷帙在切换项目时依次保护终稿砚修草稿", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  await page.getByRole("tab", { name: "章节", exact: true }).click();

  const revision = page.getByRole("region", { name: "章节阅读器" }).getByRole("region", { name: "终稿修订" });
  await revision.getByRole("button", { name: "入砚修" }).click();
  await revision.getByLabel("第 4 章终稿编辑器").fill("雨声压在高架桥底。\n\n林逐并无立刻拨给周砚。");
  await selectReaderProject(page, "测试短篇");

  const guard = page.getByRole("dialog", { name: "终稿修订尚未保存" });
  await expect(guard).toBeVisible();
  await expect(guard.locator(".source-message-dialog")).toHaveScreenshot("project-reader-final-revision-project-switch-guard.png", { animations: "disabled" });
  await guard.getByRole("button", { name: "继续修订" }).click();
  await expect(page.getByLabel("选择阅卷项目")).toContainText("测试长篇");
  await expect(revision.getByLabel("第 4 章终稿编辑器")).toHaveValue("雨声压在高架桥底。\n\n林逐并无立刻拨给周砚。");

  await selectReaderProject(page, "测试短篇");
  await page.getByRole("dialog", { name: "终稿修订尚未保存" }).getByRole("button", { name: "放弃修改" }).click();
  await expect(page.getByText("📖　测试短篇")).toBeVisible();
});

test("卷帙在切换项目时静默保存终稿后继续切换", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  await page.getByRole("tab", { name: "章节", exact: true }).click();

  const revision = page.getByRole("region", { name: "章节阅读器" }).getByRole("region", { name: "终稿修订" });
  await revision.getByRole("button", { name: "入砚修" }).click();
  await revision.getByLabel("第 4 章终稿编辑器").fill("雨声压在高架桥底。\n\n林逐决定先保留那段录音。");
  await selectReaderProject(page, "测试短篇");

  const guard = page.getByRole("dialog", { name: "终稿修订尚未保存" });
  await expect(guard).toBeVisible();
  await guard.getByRole("button", { name: "保存定稿" }).click();
  await expect(page.getByText("📖　测试短篇")).toBeVisible();
});

test("卷帙将大纲润色提交给 Engine，并继续保护角色编辑", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  await page.getByRole("tab", { name: "章节大纲", exact: true }).click();

  const outline = page.getByRole("region", { name: "章节大纲工作台" });
  await outline.getByRole("tab", { name: "润色", exact: true }).click();
  await outline.getByRole("button", { name: "提交润色任务" }).click();
  await expect(page.getByText("大纲润色任务已提交；任务完成后会刷新受影响章节契约。可在机杼查看实时进度。")).toBeVisible();
  await selectReaderProject(page, "测试短篇");
  await expect(page.getByText("📖　测试短篇")).toBeVisible();
  await expect(page.getByRole("dialog", { name: "大纲润色尚未应用" })).toHaveCount(0);

  await selectReaderProject(page, "测试长篇");
  await expect(page.getByText("📖　测试长篇")).toBeVisible();
  await page.getByRole("tab", { name: "角色与实体", exact: true }).click();
  const characterWorkbench = page.getByRole("region", { name: "角色与实体工作台" });
  await characterWorkbench.getByRole("button", { name: "编辑" }).click();
  const age = characterWorkbench.getByLabel("年龄");
  await expect(age).toBeVisible();
  await age.fill("29");
  await selectReaderProject(page, "测试短篇");
  const characterGuard = page.getByRole("dialog", { name: "角色设定尚未保存" });
  await expect(characterGuard).toBeVisible();
  await characterGuard.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("📖　测试短篇")).toBeVisible();
});

test("卷帙跨外层标签保留终稿砚修草稿并继续项目切换保护", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  const outerTabs = page.getByLabel("卷帙分类");
  await outerTabs.getByRole("tab", { name: "章节", exact: true }).click();

  const revision = page.getByRole("region", { name: "章节阅读器" }).getByRole("region", { name: "终稿修订" });
  await revision.getByRole("button", { name: "入砚修" }).click();
  const editor = revision.getByLabel("第 4 章终稿编辑器");
  await editor.fill("雨声压在高架桥底。\n\n林逐决定先保留那段录音。");

  await outerTabs.getByRole("tab", { name: "章节大纲", exact: true }).click();
  await expect(page.getByRole("region", { name: "章节大纲工作台" })).toBeVisible();
  await selectReaderProject(page, "测试短篇");
  const guard = page.getByRole("dialog", { name: "终稿修订尚未保存" });
  await expect(guard).toBeVisible();
  await guard.getByRole("button", { name: "继续修订" }).click();

  await outerTabs.getByRole("tab", { name: "章节", exact: true }).click();
  await expect(page.getByRole("region", { name: "章节阅读器" }).getByLabel("第 4 章终稿编辑器")).toHaveValue("雨声压在高架桥底。\n\n林逐决定先保留那段录音。");
});

test("卷帙跨外层标签保留角色编辑会话，并让大纲任务由 Engine 接管", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  const outerTabs = page.getByLabel("卷帙分类");

  await outerTabs.getByRole("tab", { name: "章节大纲", exact: true }).click();
  const outline = page.getByRole("region", { name: "章节大纲工作台" });
  await outline.getByRole("tab", { name: "润色", exact: true }).click();
  await outline.getByRole("button", { name: "提交润色任务" }).click();
  await expect(page.getByText("大纲润色任务已提交；任务完成后会刷新受影响章节契约。可在机杼查看实时进度。")).toBeVisible();
  await outerTabs.getByRole("tab", { name: "章节", exact: true }).click();
  await selectReaderProject(page, "测试短篇");
  await expect(page.getByText("📖　测试短篇")).toBeVisible();
  await expect(page.getByRole("dialog", { name: "大纲润色尚未应用" })).toHaveCount(0);

  await selectReaderProject(page, "测试长篇");
  await outerTabs.getByRole("tab", { name: "基础设定", exact: true }).click();
  await page.getByLabel("基础设定分类").getByRole("tab", { name: "角色与实体", exact: true }).click();
  const characters = page.getByRole("region", { name: "角色与实体工作台" });
  await characters.getByRole("button", { name: "编辑" }).click();
  await characters.getByLabel("年龄").fill("29");
  await outerTabs.getByRole("tab", { name: "资料检索", exact: true }).click();
  await outerTabs.getByRole("tab", { name: "基础设定", exact: true }).click();
  await expect(page.getByLabel("基础设定分类").getByRole("tab", { name: "角色与实体", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "角色与实体工作台" }).getByLabel("年龄")).toHaveValue("29");
  await selectReaderProject(page, "测试短篇");
  await expect(page.getByRole("dialog", { name: "角色设定尚未保存" })).toBeVisible();
});

test("卷帙角色与关系操作遵循源端角色编辑器的档案、关系、图谱分栏", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await selectReaderProject(page, "测试长篇");
  await page.getByRole("tab", { name: "角色与实体", exact: true }).click();

  const characterWorkbench = page.getByRole("region", { name: "角色与实体工作台" });
  await characterWorkbench.getByRole("button", { name: "新增角色" }).click();
  const characterDialog = page.getByRole("dialog", { name: "新增角色" });
  await characterDialog.getByLabel("角色姓名").fill("程野");
  await characterDialog.getByRole("button", { name: "创建" }).click();
  await expect(page.getByText("演练引擎已新增角色「程野」。")).toBeVisible();
  await expect(characterWorkbench.getByRole("button", { name: /程野/ })).toBeVisible();

  await characterWorkbench.getByRole("button", { name: "标记退场" }).click();
  await page.getByRole("dialog", { name: "标记角色退场" }).getByRole("button", { name: "标记退场" }).click({ force: true });
  await expect(page.getByText("演练引擎已标记角色退场。")).toBeVisible();
  await expect(characterWorkbench.locator(".character-status-pill", { hasText: "退场" })).toBeVisible();

  await characterWorkbench.getByRole("tab", { name: "关系" }).click();
  const relationshipTable = characterWorkbench.getByRole("table", { name: "角色关系表格" });
  await expect(relationshipTable).toBeVisible();
  await characterWorkbench.getByRole("button", { name: "林逐 · 主角", exact: true }).click();
  await characterWorkbench.getByRole("button", { name: "新增关系" }).click();
  const relationshipDialog = page.getByRole("dialog", { name: "新增关系" });
  await expect(relationshipDialog.getByLabel("关系来源")).toHaveCount(0);
  await relationshipDialog.getByLabel("关系对象").selectOption({ label: "程野" });
  await relationshipDialog.getByLabel("关系类型").selectOption("同盟");
  await relationshipDialog.getByLabel("关系描述").fill("关键行动中互相掩护。");
  await relationshipDialog.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("演练引擎已保存角色关系。")).toBeVisible();
  await characterWorkbench.getByRole("tab", { name: "关系" }).click();
  await expect(characterWorkbench.getByRole("table", { name: "角色关系表格" })).toContainText("程野");
  await expect(characterWorkbench.getByRole("button", { name: "移除关系" })).toBeVisible();
  await characterWorkbench.getByRole("button", { name: "移除关系" }).click();
  await page.getByRole("dialog", { name: "移除关系" }).getByRole("button", { name: "移除" }).click();
  await expect(page.getByText("演练引擎已移除角色关系。")).toBeVisible();
});

test("卷帙将终稿选段经 Engine 持久化到拟人化库", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");
  await page.getByRole("button", { name: "· 卷帙 ·" }).click();
  await page.getByLabel("选择阅卷项目").click();
  await page.getByRole("listbox", { name: "选择阅卷项目" }).getByRole("option", { name: "测试长篇" }).click();
  await page.getByRole("tab", { name: "章节", exact: true }).click();

  const revision = page.getByRole("region", { name: "章节阅读器" }).getByRole("region", { name: "终稿修订" });
  await revision.getByRole("button", { name: "入砚修" }).click();
  const editor = revision.getByLabel("第 4 章终稿编辑器");
  await editor.focus();
  await page.keyboard.press("Meta+A");
  await expect(revision.getByText("已选择文本")).toBeVisible();
  await editor.dispatchEvent("contextmenu", { bubbles: true, cancelable: true, clientX: 120, clientY: 120 });
  await page.getByRole("button", { name: "加入拟人化库…" }).click();

  const dialog = page.getByRole("dialog", { name: "加入拟人化库" });
  await expect(dialog.getByLabel("模式名称")).toHaveValue(/第 4 章选段/);
  await dialog.getByLabel("模式名称").fill("终稿收束模板");
  await dialog.getByLabel("模式关键词").fill("模板句式，收束");
  await dialog.getByRole("button", { name: "加入拟人化库" }).click();

  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("演练引擎已保存拟人化模式「终稿收束模板」。")).toBeVisible();
});
