import { expect, test } from "@playwright/test";

test("机杼原生捕获夹具把并发输出折叠为可诊断的运行轨迹", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember&workflow_dialog=floating-stream");

  const dialog = page.getByRole("dialog", { name: "流式详情" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("tab", { name: "运行轨迹" })).toHaveAttribute("aria-selected", "true");
  await expect(dialog.locator(".task-stream-runtime-summary")).toHaveCount(0);
  const trace = dialog.getByRole("region", { name: "批次运行轨迹" });
  await expect(trace).toBeVisible();
  await expect(trace.locator(".task-trace-health-counts .is-completed")).toContainText("已完成 1");
  await expect(trace.locator(".task-trace-health-counts .is-running")).toContainText("运行中 1");
  await expect(trace.locator(".task-trace-health-counts .is-attention")).toContainText("需处理 1");
  await expect(trace.getByRole("region", { name: "结构化输出诊断" })).toBeVisible();
  await expect(trace.getByText("最后可识别字段", { exact: true })).toBeVisible();
  await expect(trace.getByText("cognitive_level", { exact: true })).toBeVisible();

  await trace.getByRole("button", { name: "选择子流 01 · 已完成" }).click();
  await expect(dialog.getByText("visible_consequence", { exact: true })).toBeVisible();
  await expect(dialog.getByText("主题兑现落在可见行动与后果中", { exact: true })).toBeVisible();

  await trace.getByRole("button", { name: "运行中 1" }).click();
  await expect(dialog.getByText("结构化结果生成中", { exact: true })).toBeVisible();
  await expect(dialog.getByText("完成校验后展示字段。", { exact: false })).toBeVisible();
  await expect(trace.getByRole("button", { name: "选择子流 01 · 已完成" })).toHaveCount(0);
  await expect(dialog.getByText("开始接收 一致性画像 输出")).toHaveCount(0);
});

test("长篇立项进度百分比保持在恢复面板内", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember");
  await page.getByRole("tab", { name: "长篇初始化 分章推进 · 立项后去章台逐章续写" }).click();

  const resumePanel = page.locator(".long-init-resume");
  const percent = page.locator(".long-init-progress-value");
  await expect(resumePanel).toBeVisible();
  await expect(percent).toBeVisible();

  const [panelBounds, percentBounds] = await Promise.all([
    resumePanel.boundingBox(),
    percent.boundingBox(),
  ]);
  expect(panelBounds).not.toBeNull();
  expect(percentBounds).not.toBeNull();
  expect(percentBounds!.x).toBeGreaterThanOrEqual(panelBounds!.x);
  expect(percentBounds!.x + percentBounds!.width).toBeLessThanOrEqual(
    panelBounds!.x + panelBounds!.width,
  );
});

test("机杼原生捕获夹具可冻结错误日志与停止确认，并保留源端操作语义", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember&workflow_dialog=error-log");
  const errorLog = page.getByRole("dialog", { name: "任务流错误日志" });
  await expect(errorLog).toBeVisible();
  await expect(errorLog.getByText("格式合同", { exact: true })).toBeVisible();
  await expect(errorLog.getByText("待确认 1")).toBeVisible();
  await errorLog.getByRole("button", { name: "选择第 1 条格式合同错误" }).click();
  await expect(errorLog.getByText("已选择 1 条待确认错误。")).toBeVisible();
  await errorLog.getByRole("button", { name: "确认已处理 (1)" }).click();
  await errorLog.getByRole("tab", { name: "已处理 (2)" }).click();
  await expect(errorLog.getByText("已处理 1")).toBeVisible();
  await expect(errorLog.getByText("自动恢复 1")).toBeVisible();
  await expect(errorLog.getByText("确认处理；这不会自动修复模型或重新运行任务。", { exact: false })).toBeVisible();
  await errorLog.getByRole("button", { name: "关闭" }).click();
  await expect(page.getByRole("button", { name: "错误日志 · 已处理" })).toBeVisible();
  await page.getByRole("button", { name: "错误日志 · 已处理" }).click();
  await expect(errorLog.getByText("当前筛选下没有条目")).toBeVisible();
  await errorLog.getByRole("tab", { name: "已处理 (2)" }).click();
  await errorLog.getByRole("button", { name: "重新打开" }).click();
  await errorLog.getByRole("tab", { name: "待确认 (1)" }).click();
  await expect(errorLog.getByText("待确认 1")).toBeVisible();
  await expect(errorLog.getByRole("button", { name: "确认这 1 条已处理" })).toBeVisible();

  await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember&workflow_dialog=cancel");
  const cancelDialog = page.getByRole("dialog", { name: "确认停止任务" });
  await expect(cancelDialog).toBeVisible();
  await expect(cancelDialog.getByText("当前步骤：叙事蓝图 · 7/15")).toBeVisible();
  await cancelDialog.getByRole("button", { name: "确认停止" }).click();
  await expect(cancelDialog).toBeHidden();
  // 对标 PySide6：取消 = FAILED + cancelled 标记 → 「已取消」徽章（muted），
  // init_long 非章节类任务无断点续写按钮（_build_action_bar 返回 None）。
  await expect(page.getByText("已取消", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "↻ 从断点恢复" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "⏹ 停止" })).toHaveCount(0);
});


test("机杼关注打开同一任务的流式详情工作区", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember");

  const focus = page.getByRole("region", { name: "机杼关注" });
  const focusTitle = focus.locator(".task-focus-title");
  await expect(focusTitle).toBeVisible();
  const expectedTitle = (await focusTitle.textContent())?.trim();
  expect(expectedTitle).toBeTruthy();

  await focus.getByRole("button", { name: "查看任务详情" }).click();
  const dialog = page.getByRole("dialog", { name: "任务详情" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".task-observation-header h2")).toHaveText(expectedTitle!);
  await expect(dialog.getByRole("tab", { name: "当前节点" })).toHaveAttribute("aria-selected", "true");
  await dialog.getByRole("tab", { name: "运行轨迹" }).click();
  await expect(dialog.getByRole("complementary", { name: "任务事件时间线" })).toHaveCount(0);
  await expect(dialog.getByRole("complementary", { name: "调用观察" })).toHaveCount(0);
  await expect(dialog.getByRole("region", { name: "实时输出" })).toBeVisible();
  await dialog.getByRole("tab", { name: "调用详情" }).click();
  await expect(dialog.getByText("模型调用全景")).toBeVisible();
  const callSteps = dialog.getByRole("region", { name: "步骤调用列表" });
  await expect(callSteps).toBeVisible();
  await expect(callSteps.getByRole("button", { name: /章节计划/ })).toBeVisible();
  await expect(callSteps.getByRole("button", { name: /章节织波/ })).toBeVisible();
});
