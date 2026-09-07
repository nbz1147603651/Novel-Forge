import { expect, test, type Page } from "@playwright/test";

async function openVoiceStudio(page: Page): Promise<void> {
  await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&theme=narrative_ember");
  await expect(page.getByRole("heading", { name: "配音角色" })).toBeVisible();
  await page.getByRole("tab", { name: "3 配音室" }).click();
  await expect(page.getByRole("heading", { name: "配音室" })).toBeVisible();
}

test("声腔顶栏复刻项目选择与四项配音指标", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&theme=narrative_ember");

  const projectSelector = page.getByRole("button", { name: "当前配音项目" });
  await expect(projectSelector).toHaveText("测试长篇");
  await expect(page.getByLabel("配音工作室概览").getByText("待确认")).toBeVisible();

  await projectSelector.click();
  const projectOptions = page.getByRole("listbox", { name: "当前配音项目" });
  await expect(projectOptions.getByRole("option")).toHaveCount(2);
  await projectOptions.getByRole("option", { name: "测试短篇" }).click();
  await expect(projectSelector).toHaveText("测试短篇");
  await expect(page.getByRole("heading", { name: "配音角色" })).toBeVisible();
  await expect(page.getByRole("contentinfo")).toContainText("上次操作：切换配音项目：测试短篇");
});

test("声腔取消将活跃配音团队任务交给 Engine，而非只重置本地状态", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&theme=narrative_ember");

  await page.getByRole("button", { name: "重新组建并准备试听" }).click();
  const rebuildDialog = page.getByRole("dialog", { name: "重新构建配音团队" });
  await rebuildDialog.getByRole("button", { name: "全选" }).click();
  await rebuildDialog.getByRole("button", { name: /重建 \d+ 位角色/ }).click();
  const statusBar = page.locator(".voice-status");
  await expect(statusBar).toContainText("正在构建");
  await expect(statusBar.getByRole("button", { name: "取消" })).toBeEnabled();

  await statusBar.getByRole("button", { name: "取消" }).click();
  await expect(page.locator(".voice-action-feedback")).toContainText(
    "配音团队构建已取消；现有角色音色保持不变。",
  );
  await expect(statusBar).toContainText("已取消");
});

test("声腔右侧显示当前角色档案，并允许从当前平台目录持久化改音色", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&theme=narrative_ember");

  const dossier = page.getByLabel("当前角色声音档案");
  await expect(dossier).toContainText("声音档案");
  await expect(dossier).toContainText("工作进度");
  await expect(dossier).toContainText("配音团队已确认");
  await expect(page.getByRole("button", { name: "查看完整档案" })).toBeVisible();

  const voiceSelector = page.getByRole("combobox", { name: "当前角色音色" });
  await expect(voiceSelector).toBeEnabled();
  await expect(voiceSelector.locator("option")).toHaveCount(4);
  await voiceSelector.selectOption("Chinese (Mandarin)_Cool_Chen");
  await expect(voiceSelector).toHaveValue("Chinese (Mandarin)_Cool_Chen");
  await expect(page.locator(".voice-catalog-error")).toHaveText(
    "演练引擎已将 冷静陈 写入 narrator，请重新试听并确认团队。",
  );
});

test("声腔团队在短窗口内不横向截断，并为增长的角色列表保留滚动区", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&theme=narrative_ember");

  await expect(page.locator(".voice-cast-list")).toHaveCSS("overflow-y", "auto");

  const detailGeometry = await page.locator(".voice-detail").evaluate((detail) => ({
    clientWidth: detail.clientWidth,
    scrollWidth: detail.scrollWidth,
    hiddenContent: Array.from(detail.querySelectorAll<HTMLElement>("*")).some((element) =>
      element.textContent?.trim() && getComputedStyle(element).display === "none"
    ),
  }));

  expect(detailGeometry.scrollWidth).toBeLessThanOrEqual(detailGeometry.clientWidth);
  expect(detailGeometry.hiddenContent).toBe(false);
});

test("声腔团队在短窗口为进度栏保留独立空间，不与工作区重叠", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&theme=narrative_ember");

  const geometry = await page.locator(".voice-page").evaluate(() => {
    const bounds = (selector: string) => {
      const element = document.querySelector<HTMLElement>(selector);
      if (element === null) throw new Error(`Missing ${selector}`);
      const { bottom, top } = element.getBoundingClientRect();
      return { bottom, top };
    };

    return {
      frame: bounds(".voice-frame"),
      primaryActions: bounds(".voice-team-primary-actions"),
      status: bounds(".voice-status"),
      workspace: bounds(".voice-team"),
    };
  });

  expect(geometry.workspace.bottom).toBeLessThanOrEqual(geometry.frame.bottom + 1);
  expect(geometry.frame.bottom).toBeLessThanOrEqual(geometry.status.top);
  expect(geometry.primaryActions.bottom).toBeLessThanOrEqual(geometry.status.top);
});

test("声腔团队在紧凑窗口保持角色卡层级，且主详情可以滚动查看", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&theme=narrative_ember");
  await expect(page.getByRole("heading", { name: "配音角色" })).toBeVisible();

  const cardMetrics = await page.locator(".voice-cast-list > button").evaluateAll((cards) =>
    cards.map((card) => {
      const title = card.querySelector("strong")?.getBoundingClientRect();
      const summary = card.querySelector("small")?.getBoundingClientRect();
      const badge = card.querySelector(".voice-status-badge")?.getBoundingClientRect();
      const bounds = card.getBoundingClientRect();
      return {
        badgeBottom: badge?.bottom ?? 0,
        badgeTop: badge?.top ?? 0,
        cardBottom: bounds.bottom,
        cardHeight: bounds.height,
        cardTop: bounds.top,
        summaryBottom: summary?.bottom ?? 0,
        summaryTop: summary?.top ?? 0,
        titleBottom: title?.bottom ?? 0,
      };
    })
  );

  expect(cardMetrics.length).toBeGreaterThan(0);
  for (const card of cardMetrics) {
    expect(card.cardHeight).toBeGreaterThanOrEqual(62);
    expect(card.titleBottom).toBeLessThanOrEqual(card.summaryTop);
    expect(card.summaryBottom).toBeLessThanOrEqual(card.cardBottom);
    expect(card.badgeTop).toBeGreaterThanOrEqual(card.cardTop);
    expect(card.badgeBottom).toBeLessThanOrEqual(card.cardBottom);
  }

  const detail = page.locator(".voice-detail");
  const facts = page.getByLabel("音色摘要");
  const dossier = page.getByLabel("当前角色声音档案");
  await expect(detail).toHaveCSS("overflow-y", "auto");
  await expect(facts).toBeVisible();
  await expect(dossier).toContainText("工作进度");

  const detailMetrics = await detail.evaluate((element) => {
    const style = getComputedStyle(element);
    element.scrollTop = element.scrollHeight;
    return {
      maxBlockSize: style.maxBlockSize,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
      viewportHeight: element.clientHeight,
    };
  });
  const workbenchMetrics = await page.locator(".voice-detail-workbench").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));

  expect(detailMetrics.scrollHeight).toBeGreaterThan(detailMetrics.viewportHeight);
  expect(detailMetrics.scrollTop).toBeGreaterThan(0);
  expect(workbenchMetrics.scrollWidth).toBeLessThanOrEqual(workbenchMetrics.clientWidth);
});

test("声腔配音室保持指导草稿、候选试听、接受与舍弃的审听边界", async ({ page }) => {
  await openVoiceStudio(page);
  const accept = page.getByRole("button", { name: "接受此版" });
  await expect(accept).toBeDisabled();

  await page.getByRole("button", { name: "编辑指导" }).click();
  const editor = page.getByRole("dialog", { name: "编辑第 4 章配音脚本" });
  await editor.getByLabel("当前片段语气提示").fill("压低声线，句尾收住");
  await editor.getByRole("button", { name: "保存试听指导" }).click();
  await expect(page.getByText("已保存 1 段待审试听指导；已有候选试听已舍弃，正式脚本未变。")).toBeVisible();
  await expect(page.getByText("待审指导").first()).toBeVisible();
  await expect(accept).toBeDisabled();
  await page.getByRole("button", { name: "编辑指导" }).click();
  const restoredGuidanceEditor = page.getByRole("dialog", { name: "编辑第 4 章配音脚本" });
  await expect(restoredGuidanceEditor.getByLabel("当前片段语气提示")).toHaveValue("压低声线，句尾收住");
  await restoredGuidanceEditor.getByRole("button", { name: "取消" }).click();

  await page.getByRole("button", { name: "生成试听" }).click();
  await expect(page.getByRole("button", { name: "正在生成试听…" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "重新生成试听" })).toBeVisible();
  await expect(accept).toBeEnabled();

  await page.getByRole("button", { name: "舍弃试听" }).click();
  await expect(page.getByText("待审指导").first()).toBeVisible();
  await expect(accept).toBeDisabled();

  await page.getByRole("button", { name: "生成试听" }).click();
  await expect(page.getByRole("button", { name: "重新生成试听" })).toBeVisible();
  await accept.click();
  await expect(page.getByText("已接受 · 待装配").first()).toBeVisible();
  await expect(page.getByText("候选试听已接受；请到后处理装配章节主音轨。")).toBeVisible();
});

test("声腔指导取消不污染草稿，脚本保存会清空旧试听状态", async ({ page }) => {
  await openVoiceStudio(page);

  await page.getByRole("button", { name: "编辑指导" }).click();
  const editor = page.getByRole("dialog", { name: "编辑第 4 章配音脚本" });
  await editor.getByLabel("当前片段语气提示").fill("这次不保存");
  await editor.getByRole("button", { name: "取消" }).click();
  await expect(page.getByText("尚未保存本段的附加指导参数。")).toBeVisible();

  await page.getByRole("button", { name: "生成试听" }).click();
  await expect(page.getByRole("button", { name: "重新生成试听" })).toBeVisible();
  await page.getByRole("button", { name: "接受此版" }).click();
  await expect(page.getByText("已接受 · 待装配").first()).toBeVisible();

  await page.getByRole("tab", { name: "2 配音脚本" }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const scriptEditor = page.getByRole("dialog", { name: "编辑第 4 章配音脚本" });
  await scriptEditor.getByLabel("当前片段文本").fill("雨声压在高架桥底，像一层未干的墨。 ");
  await scriptEditor.getByRole("button", { name: "保存脚本" }).click();
  await expect(page.getByText("演练引擎已保存 4 个脚本片段。")).toBeVisible();

  await page.getByRole("tab", { name: "3 配音室" }).click();
  await expect(page.getByText("尚未保存本段的附加指导参数。")).toBeVisible();
  await expect(page.getByRole("button", { name: "接受此版" })).toBeDisabled();
});

test("声腔交付将章节音频、字幕与全书打包明确映射到命令范围", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&theme=narrative_ember");
  await page.getByRole("tab", { name: "4 后处理" }).click();
  const exportMenuButton = page.getByRole("button", { name: "导出 ▾" });
  await expect(exportMenuButton).toHaveAttribute("aria-expanded", "false");
  await exportMenuButton.click();
  await expect(exportMenuButton).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("menu", { name: "导出菜单" }).getByRole("menuitem", { name: "导出 MP3", exact: true }).click();

  const dialog = page.getByRole("dialog", { name: "导出音频" });
  await expect(dialog.getByRole("combobox", { name: "音频导出范围" })).toHaveValue("chapter");
  await expect(dialog.getByRole("combobox", { name: "音频导出格式" })).toHaveValue("mp3");
  await dialog.getByRole("checkbox", { name: "标准化到 -14 LUFS（适合成品交付）" }).check();
  await dialog.getByRole("button", { name: "创建 MP3 导出请求" }).click();
  await expect(dialog.getByText("第 4 章 MP3 音频（-14 LUFS）导出命令已在前端演练中接受")).toBeVisible();
  await dialog.getByRole("button", { name: "完成" }).click();

  await page.getByRole("button", { name: "导出 ▾" }).click();
  await page.getByRole("menu", { name: "导出菜单" }).getByRole("menuitem", { name: "全书打包导出（含字幕）" }).click();
  const bookDialog = page.getByRole("dialog", { name: "导出音频" });
  await expect(bookDialog.getByRole("combobox", { name: "音频导出范围" })).toHaveValue("book");
  await expect(bookDialog.getByRole("combobox", { name: "音频导出格式" })).toHaveCount(0);
  await bookDialog.getByRole("checkbox", { name: "ZIP 同时包含 SRT 字幕" }).check();
  await bookDialog.getByRole("button", { name: "创建 ZIP 全书导出请求" }).click();
  await expect(bookDialog.getByText("全书音频打包（含字幕）导出命令已在前端演练中接受")).toBeVisible();
});

test("声腔平台设置复刻源端的分类折叠流，并保留草稿保存边界", async ({ page }) => {
  await page.goto("/?__nimo_ui_parity=1&page=voice_studio&voice_state=configured&voice_tab=settings&theme=narrative_ember");

  const settingsTab = page.getByRole("tab", { name: "平台设置" });
  await expect(settingsTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "本机模型中心" })).toBeVisible();
  await expect(page.getByRole("button", { name: "MiniMax — 连接与模型" })).toHaveAttribute("aria-expanded", "true");

  const model = page.getByRole("combobox", { name: "当前平台模型" });
  await expect(model).toBeVisible();
  await model.selectOption("speech-02-turbo");
  await page.getByRole("button", { name: "保存设置" }).click();

  await expect(model).toHaveValue("speech-02-turbo");
  await expect(page.getByText(/前端演练已接受.*未写入/)).toBeVisible();
  await page.getByRole("button", { name: "检查本机运行时" }).click();
  await expect(page.getByText(/已识别模型约/)).toBeVisible();
});
