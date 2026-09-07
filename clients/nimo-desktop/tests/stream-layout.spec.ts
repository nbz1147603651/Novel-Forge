import { expect, test } from "@playwright/test";

test("专注任务详情把当前节点与流式正文分层展示", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember");

  const focus = page.getByRole("region", { name: "机杼关注" });
  const focusTitle = focus.locator(".task-focus-title");
  await expect(focusTitle).toBeVisible();
  const expectedTitle = (await focusTitle.textContent())?.trim();
  expect(expectedTitle).toBeTruthy();

  await focus.getByRole("button", { name: "查看任务详情" }).click();
  const dialog = page.getByRole("dialog", { name: "专注任务详情", exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".task-observation-header h2")).toHaveText(expectedTitle!);
  await expect(dialog.getByRole("tab", { name: "当前节点" })).toHaveAttribute("aria-selected", "true");
  const currentBounds = await dialog.boundingBox();
  expect(currentBounds).not.toBeNull();
  const currentBody = await dialog.locator(".task-current-workbench").boundingBox();
  const currentContent = await dialog.locator(".task-focus-preview").boundingBox();
  expect(currentContent!.width).toBeGreaterThan(currentBody!.width * 0.7);
  expect(currentContent!.height).toBeGreaterThan(currentBody!.height * 0.7);
  await dialog.getByRole("tab", { name: "运行轨迹" }).click();
  const traceBounds = await dialog.boundingBox();
  expect(traceBounds).toEqual(currentBounds);
  await expect(dialog.getByRole("complementary", { name: "任务事件时间线" })).toHaveCount(0);
  await expect(dialog.getByRole("complementary", { name: "调用观察" })).toHaveCount(0);
  const reader = dialog.getByRole("region", { name: "实时输出" });
  await expect(reader).toBeVisible();
  await expect(reader.locator(".task-stream-runtime-summary")).toHaveCount(0);
  await expect(reader.locator(".task-stream-trace-browser, .task-stream-list")).toBeVisible();
  const readerBounds = await reader.boundingBox();
  const footerBounds = await dialog.locator(".task-observation-footer").boundingBox();
  expect(Math.abs(readerBounds!.y + readerBounds!.height - footerBounds!.y)).toBeLessThan(2);

  const follow = dialog.getByRole("button", { name: "暂停自动跟随" });
  await expect(follow).toHaveAttribute("aria-pressed", "true");
  await follow.click();
  const resume = dialog.getByRole("button", { name: "继续跟随最新输出" });
  await expect(resume).toHaveAttribute("aria-pressed", "false");
  await resume.click();
  await expect(follow).toHaveAttribute("aria-pressed", "true");

  await dialog.getByRole("tab", { name: "调用详情" }).click();
  const callsBounds = await dialog.boundingBox();
  expect(callsBounds).toEqual(currentBounds);
  const [bodyBounds, workbenchBounds] = await Promise.all([
    dialog.locator(".task-observation-body").boundingBox(),
    dialog.locator(".task-call-workbench").boundingBox(),
  ]);
  expect(bodyBounds).not.toBeNull();
  expect(workbenchBounds).not.toBeNull();
  expect(workbenchBounds!.height).toBeGreaterThan(bodyBounds!.height * 0.7);
});

test("运行轨迹浮动窗口保留标题与轨迹概览", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember&workflow_dialog=floating-stream");

  const dialog = page.getByRole("dialog", { name: "流式详情" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("tab", { name: "运行轨迹" })).toHaveAttribute("aria-selected", "true");
  await expect(dialog.locator(".floating-stream-heading strong")).toBeVisible();
  await expect(dialog.getByRole("region", { name: "批次运行轨迹" })).toBeVisible();
});
