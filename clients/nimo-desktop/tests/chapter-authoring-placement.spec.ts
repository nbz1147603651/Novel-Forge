import { expect, test } from "@playwright/test";

test("章台将 AI 共创入口归入标题动作组并与导航动作分组", async ({ page }) => {
  await page.goto("/tests/browser/chapter-authoring-placement.html");
  await expect(page.getByRole("heading", { name: "章台工作台" })).toBeVisible();

  const coauthor = page.getByRole("button", { name: "AI 共创" });
  const reader = page.getByRole("button", { name: "阅卷" });
  const workflow = page.getByRole("button", { name: "回机杼 →" });
  await expect(coauthor).toBeVisible();
  await expect(page.locator(".authoring-toggle")).toHaveCount(0);

  const [coauthorBox, readerBox, workflowBox] = await Promise.all([
    coauthor.boundingBox(),
    reader.boundingBox(),
    workflow.boundingBox(),
  ]);
  expect(coauthorBox).not.toBeNull();
  expect(readerBox).not.toBeNull();
  expect(workflowBox).not.toBeNull();
  expect(coauthorBox!.x + coauthorBox!.width).toBeLessThan(readerBox!.x);
  expect(readerBox!.x + readerBox!.width).toBeLessThan(workflowBox!.x);

  await coauthor.click();
  await expect(page.getByRole("complementary", { name: "AI 共创侧栏" })).toBeVisible();
  await expect(page.getByRole("button", { name: "收起共创", exact: true })).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("button", { name: "收起共创", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "AI 共创侧栏" })).toHaveCount(0);
});

test("章台共创入口在窄窗口换行但不发生页面级横向溢出", async ({ page }) => {
  await page.setViewportSize({ width: 480, height: 820 });
  await page.goto("/tests/browser/chapter-authoring-placement.html");
  await expect(page.getByRole("button", { name: "AI 共创" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
