import { expect, test } from "@playwright/test";

const sessionStorageKey = "nimo.ui-session.v1";
const themeStorageKey = "nimo-ui-parity.theme";

test("案头将卷库检索收进欢迎卡的空白区", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");

  const hero = page.locator(".dashboard-hero");
  const heroSearch = page.locator(".dashboard-hero-search");
  await expect(heroSearch).toBeVisible();
  const [heroBox, searchBox] = await Promise.all([hero.boundingBox(), heroSearch.boundingBox()]);
  expect(heroBox).not.toBeNull();
  expect(searchBox).not.toBeNull();
  // The lookup belongs immediately beneath 起笔 / 去机杼, occupying the
  // otherwise empty upper-left band rather than settling at the hero bottom.
  expect(searchBox!.y).toBeGreaterThan(heroBox!.y + heroBox!.height * 0.3);
  expect(searchBox!.y).toBeLessThan(heroBox!.y + heroBox!.height * 0.62);
  await expect(page.locator(".filter-surface input")).toHaveCount(0);

  await heroSearch.getByRole("textbox", { name: "检索卷帙" }).fill("测试短篇");
  await expect(page.locator(".project-detail h2", { hasText: "测试短篇" })).toBeVisible();
});

test("案头卷册卡的标题与状态徽标不重叠", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");

  const cards = page.locator(".project-grid .project-card");
  await expect(cards).toHaveCount(2);
  for (let index = 0; index < await cards.count(); index += 1) {
    const card = cards.nth(index);
    const heading = card.locator(".project-card-heading");
    const title = heading.locator("h3");
    const badge = heading.locator(".status-badge");
    await expect(heading).toBeVisible();
    const [titleBox, badgeBox, cardBox] = await Promise.all([
      title.boundingBox(),
      badge.boundingBox(),
      card.boundingBox(),
    ]);
    expect(titleBox).not.toBeNull();
    expect(badgeBox).not.toBeNull();
    expect(cardBox).not.toBeNull();
    expect(titleBox!.x + titleBox!.width).toBeLessThanOrEqual(badgeBox!.x);
    expect(badgeBox!.x + badgeBox!.width).toBeLessThanOrEqual(cardBox!.x + cardBox!.width);
  }
});

test("案头卷册卡的进度条位于下一步内容之后", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");

  const cards = page.locator(".project-grid .project-card");
  await expect(cards).toHaveCount(2);
  for (let index = 0; index < await cards.count(); index += 1) {
    const card = cards.nth(index);
    const nextAction = card.locator(".card-next-action");
    const progress = card.locator(".project-progress");
    const [nextActionBox, progressBox, cardBox, trackPosition] = await Promise.all([
      nextAction.boundingBox(),
      progress.boundingBox(),
      card.boundingBox(),
      progress.evaluate((element) => getComputedStyle(element, "::before").position),
    ]);
    expect(nextActionBox).not.toBeNull();
    expect(progressBox).not.toBeNull();
    expect(cardBox).not.toBeNull();
    expect(nextActionBox!.y + nextActionBox!.height).toBeLessThanOrEqual(progressBox!.y);
    expect(progressBox!.y + progressBox!.height).toBeLessThanOrEqual(cardBox!.y + cardBox!.height);
    expect(trackPosition).toBe("static");
  }
});

test("案头重建向量保留源端确认边界并在本地会话排队", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");

  await page.getByRole("button", { name: "重建向量" }).click();
  await expect(page.getByRole("dialog", { name: "重建向量索引？" })).toBeVisible();
  await expect(page.getByText("该操作只重建 zvec 记忆索引和表达通道语义索引，不会改动正文、章节大纲或初始化产物。 ")).toBeVisible();
  await page.getByRole("button", { name: "开始重建" }).click();
  await expect(page.getByRole("dialog", { name: "向量索引重建任务已提交" })).toBeVisible();
  await expect(page.locator(".vector-rebuild-result")).toContainText("zvec 记忆索引");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("案头压缩欢迎区，并支持批量管理与持久化卷册排序", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    if (sessionStorage.getItem("project-operations-initialized") === "true") return;
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
    sessionStorage.setItem("project-operations-initialized", "true");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");

  const hero = page.locator(".dashboard-hero");
  const heroBox = await hero.boundingBox();
  expect(heroBox).not.toBeNull();
  expect(heroBox!.height).toBeLessThan(250);

  await page.getByRole("button", { name: "批量管理" }).click();
  const toolbar = page.getByRole("region", { name: "卷册批量管理" });
  await expect(toolbar).toBeVisible();
  const longProjectCheckbox = page.getByRole("checkbox", { name: "勾选卷册：测试长篇" });
  await longProjectCheckbox.click();
  await expect(longProjectCheckbox).toHaveAttribute("aria-checked", "true");
  await expect(toolbar).toContainText("已选 1 卷");
  await toolbar.getByRole("button", { name: "置于最前" }).click();
  await expect(page.locator(".project-grid .project-card h3").first()).toHaveText("测试长篇");
  await page.waitForFunction(() => {
    const raw = localStorage.getItem("nimo.ui-session.v1");
    if (raw === null) return false;
    const session = JSON.parse(raw) as { dashboardProjectOrder?: string[] };
    return session.dashboardProjectOrder?.[0] === "test-long";
  });

  await page.reload();
  await expect(page.locator(".project-grid .project-card h3").first()).toHaveText("测试长篇");
  await page.getByRole("button", { name: "前移" }).click();
  await expect(page.locator(".project-grid .project-card h3").first()).toHaveText("测试短篇");
});

test("案头批量删除跨过 Engine 确认边界", async ({ page }) => {
  await page.addInitScript(({ sessionKey, themeKey }) => {
    localStorage.clear();
    localStorage.setItem(sessionKey, JSON.stringify({ activePage: "dashboard", sidebarCollapsed: false }));
    localStorage.setItem(themeKey, "narrative_ember");
  }, { sessionKey: sessionStorageKey, themeKey: themeStorageKey });
  await page.goto("/");

  await page.getByRole("button", { name: "批量管理" }).click();
  const shortProjectCheckbox = page.getByRole("checkbox", { name: "勾选卷册：测试短篇" });
  await shortProjectCheckbox.click();
  await expect(shortProjectCheckbox).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "永久删除" }).click();
  const removeDialog = page.getByRole("dialog", { name: "永久删除 1 卷？" });
  await expect(removeDialog).toBeVisible();
  await expect(removeDialog).toContainText("无法撤销");
  await removeDialog.getByRole("button", { name: "永久删除 1 卷", exact: true }).click();
  await expect(page.locator(".project-grid .project-card")).toHaveCount(1);
  await expect(page.getByText("不会删除磁盘上的任何项目文件。", { exact: false })).toHaveCount(0);
});
