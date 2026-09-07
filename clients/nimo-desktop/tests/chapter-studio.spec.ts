import { expect, test } from "@playwright/test";

const sessionStorageKey = "nimo.ui-session.v1";
const themeStorageKey = "nimo-ui-parity.theme";

async function openChapterStudioParityState(page: import("@playwright/test").Page, state: "prepared" | "running" | "checkpoint"): Promise<void> {
  await page.goto(`/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=${state}`);
  await expect(page.getByRole("heading", { name: "章台工作台" })).toBeVisible();
}

test("章台的紧凑控制带保留写作与裁决模式语义", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });

  await page.goto("/");
  await page.getByRole("button", { name: "· 章台 ·" }).click();
  await expect(page.getByRole("heading", { name: "章台工作台" })).toBeVisible();

  const writingModes = page.getByRole("group", { name: "章节写作模式" });
  const wholeChapter = writingModes.getByRole("button", { name: "整章写作" });
  const sceneLevel = writingModes.getByRole("button", { name: "场景级写作" });
  await expect(wholeChapter).toHaveClass(/is-active/);
  await sceneLevel.click();
  await expect(sceneLevel).toHaveClass(/is-active/);
  await expect(wholeChapter).not.toHaveClass(/is-active/);

  const decisionModes = page.getByRole("group", { name: "章节裁决模式" });
  await decisionModes.getByRole("button", { name: "AI 建议" }).click();
  await expect(decisionModes.getByRole("button", { name: "AI 建议" })).toHaveClass(/is-active/);

  await decisionModes.getByRole("button", { name: "本章自动" }).click();
  await expect(page.getByRole("button", { name: "启动本章自动" })).toBeVisible();

  await decisionModes.getByRole("button", { name: "章节连跑" }).click();
  await expect(page.getByRole("button", { name: "启动章节连跑" })).toBeVisible();

  const memory = page.getByRole("region", { name: "记忆上下文" });
  await expect(memory.getByRole("tab", { name: "概览" })).toHaveAttribute("aria-selected", "true");
  await memory.getByRole("tab", { name: "母题" }).click();
  await expect(memory.getByRole("tabpanel")).toContainText("时间戳");
  await memory.getByRole("tab", { name: "控制" }).click();
  await expect(memory.getByRole("tabpanel")).toContainText("剧情控制");
});

test("章台运行态来自引擎活动视图，并保留上下文主信息", async ({ page }) => {
  await openChapterStudioParityState(page, "running");

  await expect(page.getByRole("heading", { name: "正在执行…" })).toBeVisible();
  await expect(page.getByText("最近任务：第 5 章 · 档案室 · 当前步骤：章节生成")).toBeVisible();
  await expect(page.getByRole("button", { name: "⏹ 取消任务" })).toBeVisible();
  await expect(page.getByText("上一章实际结果", { exact: true })).toBeVisible();
  await expect(page.getByText("本章目标", { exact: true })).toBeVisible();
  await expect(page.getByRole("tabpanel")).toContainText("等待初稿生成");
});

test("章台关注在工作台产物之后纵向呈现，不挤占中间工作列", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");

  const focus = page.locator("section[aria-label='章台关注'].studio-focus-section-bottom");
  await expect(focus).toBeVisible();
  await expect(page.locator(".studio-artifacts + .studio-focus-section-bottom")).toHaveCount(1);
});

test("章台上下文详情使用共享阅读宽度，正文不在右侧留下宽版空白", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");

  await page.getByRole("button", { name: "查看下一章预埋详情" }).click();
  const dialog = page.getByRole("dialog", { name: "下一章预埋" });
  const panel = dialog.locator(".studio-context-dialog");
  const document = dialog.locator(".studio-context-detail-document");
  const prose = document.locator(".rendered-markdown > p");

  await expect(panel).toHaveCSS("width", "1080px");
  await expect(prose).toBeVisible();

  const [documentBounds, proseBounds] = await Promise.all([
    document.boundingBox(),
    prose.boundingBox(),
  ]);
  expect(documentBounds).not.toBeNull();
  expect(proseBounds).not.toBeNull();
  expect(proseBounds!.width / documentBounds!.width).toBeGreaterThan(0.94);
});

test("章台项目与重写策略在页面内展开为可选择的列表", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");

  const projectSelect = page.getByRole("button", { name: /章台项目：/ });
  await projectSelect.click();
  const projectOptions = page.getByRole("listbox", { name: "章台项目选项" });
  await expect(projectOptions).toBeVisible();
  await expect(projectOptions.getByRole("option")).toHaveCount(1);
  await expect(projectOptions.getByRole("option", { selected: true })).toContainText("测试长篇");

  const rewriteSelect = page.getByRole("button", { name: /重写策略：/ });
  await rewriteSelect.click();
  const rewriteOptions = page.getByRole("listbox", { name: "重写策略选项" });
  await expect(rewriteOptions).toBeVisible();
  await expect(rewriteOptions.getByRole("option")).toHaveCount(5);
  await rewriteOptions.getByRole("option", { name: "定点重写（修复指定问题）" }).click();
  await expect(rewriteSelect).toHaveAccessibleName("重写策略：定点重写（修复指定问题）");
  await expect(rewriteOptions).toHaveCount(0);
});

test("章台运行时仍可打开项目列表，便于切换查看并行项目", async ({ page }) => {
  await openChapterStudioParityState(page, "running");

  const projectSelect = page.getByRole("button", { name: /章台项目：/ });
  await expect(projectSelect).toBeEnabled();
  await projectSelect.click();
  await expect(page.getByRole("listbox", { name: "章台项目选项" })).toBeVisible();
});

test("章台运行透明度抽屉支持 Esc 关闭与焦点返回", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");

  await page.getByRole("button", { name: "本章自动" }).click();
  await page.getByRole("button", { name: "启动本章自动" }).click();
  const trigger = page.getByRole("button", { name: /意图保护 执行中/ });
  await expect(trigger).toBeVisible();

  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "意图保护" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".run-insight-drawer")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("章台窄屏不产生横向溢出，减弱动态时取消无限脉冲", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 900 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openChapterStudioParityState(page, "prepared");
  await page.getByRole("button", { name: "本章自动" }).click();
  await page.getByRole("button", { name: "启动本章自动" }).click();

  await expect.poll(() => page.evaluate(() => (
    document.documentElement.scrollWidth - document.documentElement.clientWidth
  ))).toBe(0);
  await expect.poll(() => page.locator(".studio-workflow-phase").evaluateAll((cards) => (
    new Set(cards.map((card) => card.getBoundingClientRect().left)).size
  ))).toBe(1);
  await expect(page.locator(".pet-companion")).toBeHidden();
  await expect.poll(() => page.evaluate(() => [...document.querySelectorAll("body *")].filter(
    (element) => {
      const style = getComputedStyle(element);
      return style.animationName !== "none" && style.animationIterationCount === "infinite";
    },
  ).length)).toBe(0);
});

test("章台检查点以非阻塞建议浮窗进入，而不替换主工作台", async ({ page }) => {
  await openChapterStudioParityState(page, "checkpoint");

  await expect(page.getByRole("heading", { name: "章节方案待确认" })).toBeVisible();
  await expect(page.getByRole("button", { name: "定位 AI 建议浮窗 →" })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "方案需确认" })).toHaveCount(0);
  await page.getByRole("button", { name: "定位 AI 建议浮窗 →" }).click();
  await expect(page.getByRole("dialog", { name: "方案需确认" })).toBeVisible();
  await expect(page.getByRole("button", { name: "确认", exact: true })).toBeVisible();

  const header = page.locator(".checkpoint-dialog-header");
  const floatingSurface = page.locator(".checkpoint-overlay .overlay-surface");
  const initialTransform = await floatingSurface.evaluate((element) => element.getAttribute("style"));
  const bounds = await header.boundingBox();
  if (bounds === null) throw new Error("checkpoint header should have a visible bounding box");
  await page.mouse.move(bounds.x + 40, bounds.y + 18);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 82, bounds.y + 45);
  await page.mouse.up();
  await expect(floatingSurface).not.toHaveAttribute("style", initialTransform ?? "");
});

test("章台原生捕获夹具只在检查点主窗上打开非阻塞建议浮窗", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=checkpoint&chapter_dialog=checkpoint");

  await expect(page.getByRole("heading", { name: "章节方案待确认" })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "方案需确认" })).toBeVisible();
  await expect(page.getByRole("button", { name: "确认", exact: true })).toBeVisible();
});

test("章台原生捕获夹具仅在准备态打开导出、审计、清理与错误日志", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=prepared&chapter_dialog=export");
  await expect(page.getByRole("dialog", { name: "导出设置" })).toBeVisible();

  await page.goto("/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=prepared&chapter_dialog=book-audit");
  await expect(page.getByRole("dialog", { name: "全书一致性审计" })).toBeVisible();

  await page.goto("/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=prepared&chapter_dialog=clean");
  await expect(page.getByRole("dialog", { name: "清理失效章节" })).toBeVisible();

  await page.goto("/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=prepared&chapter_dialog=error-log");
  await expect(page.getByRole("dialog", { name: "任务流错误日志" })).toBeVisible();
});

test("章台原生捕获夹具仅在运行态打开切换项目确认", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=running&chapter_dialog=project-switch");

  await expect(page.getByRole("dialog", { name: "切换项目" })).toBeVisible();
  await expect(page.getByText("当前项目「测试长篇」正在生成第 5 章")).toBeVisible();
});

test("章台版本对比从 Engine 阅读模型加载真实版本链路", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");

  await page.getByRole("button", { name: "版本对比" }).click();
  const unavailable = page.getByRole("dialog", { name: "版本不足" });
  await expect(unavailable).toContainText("草稿版本不足 2 个");
  await unavailable.getByRole("button", { name: "知道了" }).click();

  await page.getByRole("button", { name: /第 4 章 · 高架桥下/ }).click();
  await page.getByRole("button", { name: "版本对比" }).click();
  const dialog = page.getByRole("dialog", { name: "第 4 章 · 版本对比" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".chapter-dialog")).toHaveScreenshot("chapter-version-diff-selector-narrative-ember.png", { animations: "disabled" });

  await dialog.getByRole("radio", { name: /初稿成章.*4,216 字/ }).first().check();
  await dialog.getByRole("radio", { name: /初稿成章.*4,216 字/ }).nth(1).check();
  await dialog.getByRole("button", { name: "开始对比" }).click();
  await expect(dialog).toBeVisible();

  await dialog.getByRole("radio", { name: /DRAFT 原稿.*3,840 字/ }).first().check();
  await dialog.getByRole("button", { name: "开始对比" }).click();
  await expect(dialog).toHaveCount(0);
  const artifact = page.getByRole("article", { name: "版本对比结果" });
  await expect(artifact).toContainText("50.0%");
  await expect(artifact).toContainText("她没有");
  await expect(artifact).toContainText("她并无");
  await expect(artifact).toHaveScreenshot("chapter-version-diff-artifact-narrative-ember.png", { animations: "disabled" });
});

test("章台导出保留源端的配置范围与只读提交边界", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");

  await page.getByRole("button", { name: "导出" }).click();
  const dialog = page.getByRole("dialog", { name: "导出设置" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".chapter-dialog")).toHaveScreenshot("chapter-export-selector-narrative-ember.png", { animations: "disabled" });

  await dialog.getByRole("radio", { name: "选择特定章节" }).check();
  const exportChapters = dialog.getByRole("group", { name: "导出章节" });
  await exportChapters.getByRole("checkbox", { name: "第2章" }).uncheck();
  await dialog.getByRole("radio", { name: "EPUB (.epub)" }).check();
  await dialog.getByRole("button", { name: "选择路径…" }).click();
  await expect(dialog.getByLabel("导出目录")).toHaveText("/tmp/nimo-export-selected");

  await dialog.getByRole("button", { name: "开始导出" }).click();
  await expect(dialog).toHaveCount(0);
  const notice = page.locator(".studio-operation-notice");
  await expect(notice).toContainText("已在当前前端会话准备导出「测试长篇.epub」");
  await expect(notice).toContainText("范围为第 1、3、4 章，未写入文件。");
});

test("章台全书审计保留范围校验、高级参数与只读提交边界", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");

  await page.getByRole("button", { name: "全书审计" }).click();
  const dialog = page.getByRole("dialog", { name: "全书一致性审计" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".chapter-dialog")).toHaveScreenshot("chapter-book-audit-selector-narrative-ember.png", { animations: "disabled" });

  await dialog.getByRole("radio", { name: "选择特定章节（至少 2 章）" }).check();
  const auditChapters = dialog.getByRole("group", { name: "审计章节" });
  await auditChapters.getByRole("checkbox", { name: "第2章" }).uncheck();
  await auditChapters.getByRole("checkbox", { name: "第3章" }).uncheck();
  await auditChapters.getByRole("checkbox", { name: "第4章" }).uncheck();
  await dialog.getByRole("button", { name: "开始审计" }).click();
  const warning = page.getByRole("dialog", { name: "章节不足" });
  await expect(warning).toContainText("至少需要选择 2 个章节才能进行一致性审计。");
  await warning.getByRole("button", { name: "确定" }).click();
  await expect(dialog).toBeVisible();

  await auditChapters.getByRole("checkbox", { name: "第2章" }).check();
  await dialog.getByRole("button", { name: "高级模式" }).click();
  await dialog.getByLabel("审计分析模式").selectOption("summary");
  await dialog.getByLabel("审计定位严格度").selectOption("strict");
  await dialog.getByLabel("审计提示词").fill("优先检查人物称谓变体。");
  await dialog.getByRole("button", { name: "开始审计" }).click();
  await expect(dialog).toHaveCount(0);
  const notice = page.locator(".studio-operation-notice");
  await expect(notice).toContainText("已在当前前端会话准备全书一致性审计；范围为第 1、2 章，未启动模型或写入项目。");
});

test("章台清理章节保留源端配置关闭与受限 cutoff 边界", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");

  await page.getByRole("button", { name: "清理章节" }).click();
  const dialog = page.getByRole("dialog", { name: "清理失效章节" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".chapter-operation-dialog")).toHaveScreenshot("chapter-clean-selector-narrative-ember.png", { animations: "disabled" });

  await dialog.getByLabel("清理起始章节").fill("99");
  await dialog.getByRole("button", { name: "确认清理" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".studio-operation-notice")).toContainText("已在当前前端会话标记：从第 24 章起清理并从断点重新开始。");
});

test("章台切换项目保留源端的活动任务确认边界", async ({ page }) => {
  await openChapterStudioParityState(page, "running");

  await page.getByRole("button", { name: "切换项目" }).click();
  const dialog = page.getByRole("dialog", { name: "切换项目" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".chapter-operation-dialog")).toHaveScreenshot("chapter-project-switch-selector-narrative-ember.png", { animations: "disabled" });

  await dialog.getByRole("button", { name: "确定" }).click();
  await expect(dialog).toHaveCount(0);
  const notice = page.locator(".studio-operation-notice");
  await expect(notice).toContainText("项目切换请求已在当前前端会话确认；运行中的任务和检查点均已保留。");

  await page.getByRole("button", { name: "切换项目" }).click();
  await page.getByRole("dialog", { name: "切换项目" }).getByRole("button", { name: "取消" }).click();
  await expect(page.getByRole("dialog", { name: "切换项目" })).toHaveCount(0);
  await expect(notice).toContainText("项目切换请求已在当前前端会话确认；运行中的任务和检查点均已保留。");
});

test("章台任务流错误日志保留源端审计内容与已处理状态", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");

  await page.getByRole("button", { name: "错误日志 1" }).click();
  const dialog = page.getByRole("dialog", { name: "任务流错误日志" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".workflow-error-log-dialog")).toHaveScreenshot("chapter-error-log-selector-narrative-ember.png", { animations: "disabled" });
  await expect(dialog.getByLabel("任务流错误详情")).toContainText("因果验证等待上游计划检查点");

  await dialog.getByRole("button", { name: "标记已修复" }).click();
  await expect(dialog.getByRole("button", { name: "已标记修复" })).toBeDisabled();
  await dialog.getByRole("button", { name: "关闭" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "错误日志 1 · 已处理" })).toBeVisible();

  await page.getByRole("button", { name: "错误日志 1 · 已处理" }).click();
  await expect(page.getByRole("dialog", { name: "任务流错误日志" }).getByRole("button", { name: "已标记修复" })).toBeDisabled();
});

test("章台叙事工具中的支线管理以本地会话完成新增、确认编辑、刷新与删除", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");
  await page.getByRole("button", { name: "叙事工具" }).click();
  const toolsDialog = page.getByRole("dialog", { name: "叙事工具" });
  await toolsDialog.getByRole("tab", { name: "支线管理" }).click();
  const subplots = toolsDialog.getByRole("region", { name: "支线管理" });

  await subplots.getByRole("button", { name: "+ 添加支线" }).click();
  const addDialog = page.getByRole("dialog", { name: "添加支线" });
  await addDialog.getByLabel("支线名称").fill("旧案回声");
  await addDialog.getByLabel("支线描述").fill("被封存的旧案记录逐步影响当前调查。");
  await addDialog.getByLabel("支线起始章节").fill("3");
  await addDialog.getByLabel("支线结束章节").fill("8");
  await addDialog.getByLabel("支线收束章节").fill("8");
  await addDialog.getByLabel("支线收束类型").selectOption("悬念揭示");
  await addDialog.getByLabel("支线收束目标").fill("确认记录来源");
  await addDialog.getByRole("button", { name: "保存", exact: true }).click();
  const localSubplot = subplots.locator("details").filter({ hasText: "旧案回声" });
  await expect(localSubplot).toBeVisible();

  await localSubplot.locator("summary").click();
  await localSubplot.getByRole("button", { name: "编辑支线" }).click();
  const editDialog = page.getByRole("dialog", { name: "编辑支线" });
  await editDialog.getByLabel("支线收束目标").fill("确认记录来源并推动公开选择");
  await editDialog.getByRole("button", { name: "保存", exact: true }).click();
  await page.getByRole("dialog", { name: "确认保存修改" }).getByRole("button", { name: "保存修改" }).click();
  await expect(localSubplot).toContainText("推动公开选择");

  await subplots.getByRole("button", { name: "刷新" }).click();
  await expect(subplots.getByText("旧案回声", { exact: true })).toHaveCount(0);

  await subplots.getByRole("button", { name: "+ 添加支线" }).click();
  const addAgainDialog = page.getByRole("dialog", { name: "添加支线" });
  await addAgainDialog.getByLabel("支线名称").fill("旧案回声");
  await addAgainDialog.getByRole("button", { name: "保存", exact: true }).click();
  const deletableSubplot = subplots.locator("details").filter({ hasText: "旧案回声" });
  await deletableSubplot.locator("summary").click();
  await deletableSubplot.getByRole("button", { name: "删除" }).click();
  await page.getByRole("dialog", { name: "确认删除" }).getByRole("button", { name: "删除", exact: true }).click();
  await expect(subplots.getByText("旧案回声", { exact: true })).toHaveCount(0);
});

test("章台叙事工具中的拟人化库复刻本地新建、查重、编辑、导入与删除链路", async ({ page }) => {
  await openChapterStudioParityState(page, "prepared");
  await page.getByRole("button", { name: "叙事工具" }).click();
  const toolsDialog = page.getByRole("dialog", { name: "叙事工具" });
  await toolsDialog.getByRole("tab", { name: "拟人化库" }).click();
  const library = toolsDialog.getByRole("table", { name: "拟人化模式库" });

  await toolsDialog.getByRole("button", { name: "+ 新建" }).click();
  const createDialog = page.getByRole("dialog", { name: "新建模式" });
  await createDialog.getByLabel("模式名称").fill("万能结语");
  await createDialog.getByLabel("模式分类").fill("模板结尾");
  await createDialog.getByLabel("模式关键词").fill("结语，收束");
  await createDialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(library.getByRole("button", { name: "选择 万能结语" })).toHaveCount(2);

  await toolsDialog.getByRole("button", { name: "找重复" }).click();
  const duplicatesDialog = page.getByRole("dialog", { name: "重复模式对" });
  await expect(duplicatesDialog).toContainText("万能结语");
  await duplicatesDialog.getByRole("button", { name: "关闭", exact: true }).click();

  await library.getByRole("button", { name: "选择 万能结语" }).last().click();
  const actions = toolsDialog.getByLabel("拟人化模式操作");
  await actions.getByRole("button", { name: "编辑" }).click();
  const editDialog = page.getByRole("dialog", { name: "编辑模式" });
  await editDialog.getByLabel("模式备注").fill("只在当前前端会话中编辑。");
  await editDialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(actions).toContainText("万能结语");

  await toolsDialog.getByRole("button", { name: "导入…" }).click();
  const importDialog = page.getByRole("dialog", { name: "导入拟人化库" });
  await importDialog.getByLabel("导入拟人化库 JSON").fill(JSON.stringify({ patterns: [{ id: "archive-pattern", name: "导入模式", category: "词汇", severity: "低", keywords: ["导入"], enabled: true }] }));
  await importDialog.getByRole("button", { name: "导入到前端会话" }).click();
  await expect(library).toContainText("导入模式");

  await library.getByRole("button", { name: "选择 万能结语" }).last().click();
  await actions.getByRole("button", { name: "删除" }).click();
  await page.getByRole("dialog", { name: "确认删除" }).getByRole("button", { name: "删除", exact: true }).click();
  await expect(library.getByRole("button", { name: "选择 万能结语" })).toHaveCount(1);
});

test("章台源大纲冲突转向修订而不重做 Plan", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=source-conflict");

  await expect(page.getByRole("heading", { name: "上游大纲需修订" })).toBeVisible();
  const sourceAction = page.getByRole("button", { name: "请先修订上游大纲" });
  await expect(sourceAction).toBeVisible();
  await expect(sourceAction).toBeDisabled();
  await expect(page.getByRole("button", { name: "重新准备方案 →" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "继续章节连跑 ▶" })).toHaveCount(0);
  await expect(page.getByText("连跑将按章节轨道自动推进")).toHaveCount(0);
});
