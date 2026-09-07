import { expect, test } from "@playwright/test";

async function openShortWorkflow(page: import("@playwright/test").Page): Promise<void> {
  await page.goto("/?__nimo_ui_parity=1&page=workflow&theme=narrative_ember");
  await expect(page.getByRole("heading", { name: "短篇创作" })).toBeVisible();
}

test("短篇 ShortForm 支持字段编辑、参数、预设和 Engine 启动前校验", async ({ page }) => {
  await openShortWorkflow(page);

  await page.getByRole("button", { name: /故事主题/ }).click();
  const core = page.getByRole("dialog", { name: "核心故事" });
  await expect(core.getByRole("button", { name: "故事主题 *" })).toBeVisible();
  await core.getByLabel("编辑故事主题").fill("父亲留下的录音要求女儿在拆迁前找回那块停摆的表。");
  await core.getByRole("button", { name: "应用" }).click();
  await expect(page.getByText("已更新「核心故事」字段。")).toBeVisible();

  await page.getByLabel("短篇目标字数").fill("7200");
  await page.getByLabel("短篇写作模式").selectOption("scene_level");
  await page.getByRole("button", { name: "存为预设" }).click();
  const save = page.getByRole("dialog", { name: "存为预设" });
  await save.getByLabel("预设名称").fill("雨夜录音");
  await save.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText("预设「雨夜录音」已保存在当前前端会话。")).toBeVisible();
  await expect(page.getByLabel("选择短篇预设")).toHaveValue("short-preset:雨夜录音");

  await page.getByRole("button", { name: "导出 JSON" }).click();
  const exportChoice = page.getByRole("dialog", { name: "导出 JSON" });
  await expect(exportChoice.getByText("选择要导出的内容")).toBeVisible();
  await exportChoice.getByRole("button", { name: "导出当前配置" }).click();
  const exportDialog = page.getByRole("dialog", { name: "当前短篇配置 JSON" });
  await expect(exportDialog.getByLabel("只读短篇 JSON")).toHaveValue(/"length_target": 7200/);
  await expect(exportDialog.getByLabel("只读短篇 JSON")).toHaveValue(/"writing_mode": "scene_level"/);
  await exportDialog.getByRole("button", { exact: true, name: "关闭" }).click();

  await page.getByRole("button", { name: "导入 JSON" }).click();
  const importer = page.getByRole("dialog", { name: "导入 JSON" });
  await importer.getByLabel("短篇 JSON 内容").fill('{"run_short":{"theme":"从港口失窃的怀表开始","length_target":1800,"max_edit_rounds":1}}');
  await importer.getByRole("button", { name: "解析配置" }).click();
  const choice = page.getByRole("dialog", { name: "导入方式" });
  await choice.getByRole("button", { name: "仅填入" }).click();
  await expect(page.getByText("导入内容已填入短篇表单，JSON 未提供的字段已保留；尚未保存为预设。")).toBeVisible();
  await expect(page.getByText("从港口失窃的怀表开始")).toBeVisible();
  await expect(page.getByLabel("短篇写作模式")).toHaveValue("scene_level");

  await page.getByRole("button", { name: "清空表单" }).click();
  await page.getByRole("button", { name: "发起短篇创作 →" }).click();
  const validation = page.getByRole("dialog", { name: "输入有误" });
  await expect(validation.getByText("请填写「故事主题」。")).toBeVisible();
  await validation.getByRole("button", { name: "返回填写" }).click();

  await page.getByRole("button", { name: /故事主题/ }).click();
  await page.getByLabel("编辑故事主题").fill("失窃怀表被送回港口时，女儿发现里面藏着父亲年轻时录下的证词。");
  await page.getByRole("dialog", { name: "核心故事" }).getByRole("button", { name: "应用" }).click();
  await page.getByRole("button", { name: "发起短篇创作 →" }).click();
  const launch = page.getByRole("dialog", { name: "短篇启动意向" });
  await expect(launch.getByText("segmented_mode")).toBeVisible();
  await expect(launch.getByText("短篇参数已提交给 Engine；任务流将持续显示真实进度。")).toBeVisible();
});

test("短篇 Engine 生成候选、创作札记及顶栏模板导出使用同一持久化预设", async ({ page }) => {
  await openShortWorkflow(page);

  // First, fill theme and save a preset so history gate passes
  await page.getByRole("button", { name: /故事主题/ }).click();
  const core = page.getByRole("dialog", { name: "核心故事" });
  await core.getByLabel("编辑故事主题").fill("测试札记恢复功能。");
  await core.getByRole("button", { name: "应用" }).click();
  await page.getByRole("button", { name: "存为预设" }).click();
  const save = page.getByRole("dialog", { name: "存为预设" });
  await save.getByLabel("预设名称").fill("札记测试预设");
  await save.getByRole("button", { name: "保存" }).click();

  // Without preset bound, history shows gate dialog
  await page.getByRole("button", { name: "清空表单" }).click();
  await page.getByRole("button", { name: "创作札记" }).click();
  await expect(page.getByRole("dialog", { name: "未绑定预设" }).getByText("请先载入预设")).toBeVisible();
  await page.getByRole("dialog", { name: "未绑定预设" }).getByRole("button", { name: "知道了" }).click();

  // Reload the preset, generate an Engine candidate, apply it, then access history.
  await page.getByLabel("选择短篇预设").selectOption("short-preset:札记测试预设");
  await page.getByRole("button", { name: "载入" }).click();
  await page.getByRole("button", { name: "AI 生成并预览" }).click();
  const generation = page.getByRole("dialog", { name: "AI 生成并预览" });
  await generation.getByRole("button", { name: "生成并预览" }).click();
  const preview = page.getByRole("dialog", { name: "AI 生成变更预览" });
  await expect(preview.getByRole("button", { name: /应用 \d+ 项变更/ })).toBeEnabled();
  await preview.getByRole("button", { name: /应用 \d+ 项变更/ }).click();
  await expect(page.getByText(/已应用 \d+ 项 AI 生成变更；草稿将自动保存。/)).toBeVisible();
  await page.getByRole("button", { name: "创作札记" }).click();
  const history = page.getByRole("dialog", { name: "创作札记" });
  await expect(history.getByText("AI 生成并预览")).toBeVisible();
  // Restore from history
  await history.getByRole("button", { name: "恢复此版本" }).click();
  const restore = page.getByRole("dialog", { name: "恢复创作札记" });
  await expect(restore.getByText("确定要将表单恢复为「AI 生成并预览」的状态吗")).toBeVisible();
  await restore.getByRole("button", { name: "恢复" }).click();
  await expect(page.getByText("已恢复到「AI 生成并预览」的状态。")).toBeVisible();

  await page.getByRole("button", { name: "导出短篇模板" }).click();
  const template = page.getByRole("dialog", { name: "短篇模板 JSON" });
  await expect(template.getByLabel("只读短篇 JSON")).toHaveValue(/"theme": ""/);
  await expect(template.getByLabel("只读短篇 JSON")).toHaveValue(/"segment_trigger_words": 6000/);
});

test("AI 生成与润色输入控件保持中等尺寸，且可完整填写", async ({ page }) => {
  await openShortWorkflow(page);

  await page.getByRole("button", { name: "AI 生成并预览" }).click();
  const generate = page.getByRole("dialog", { name: "AI 生成并预览" });
  const hint = generate.getByRole("textbox", { name: /输入简要提示/ });
  const genre = generate.getByLabel("题材硬参数");
  const tone = generate.getByLabel("基调硬参数");
  const lengthTarget = generate.getByLabel("目标字数硬参数");

  await expect(hint).toHaveCSS("font-size", "14px");
  await expect(hint).toHaveCSS("min-height", "132px");
  await expect(genre).toHaveCSS("font-size", "14px");

  await hint.pressSequentially("input-check");
  await expect(hint).toHaveValue("input-check");
  await expect(hint).toBeFocused();
  await generate.getByLabel("生成方式").selectOption("variant");
  await generate.getByLabel("创意取向").selectOption("character");
  await generate.getByLabel("自由创意侧重点").fill("用物件和停顿承载信息。");
  await genre.pressSequentially("mystery");
  await tone.fill("克制");
  await lengthTarget.fill("4800");
  await expect(genre).toHaveValue("mystery");
  await expect(lengthTarget).toHaveValue("4800");

  const [genreBox, toneBox, lengthBox] = await Promise.all([
    genre.boundingBox(),
    tone.boundingBox(),
    lengthTarget.boundingBox(),
  ]);
  expect(genreBox?.y).toBe(toneBox?.y);
  expect(toneBox?.y).toBe(lengthBox?.y);

  await generate.getByRole("button", { name: "取消" }).click();
  await page.getByRole("button", { name: "AI 定向润色" }).click();
  const polish = page.getByRole("dialog", { name: "AI 定向润色" });
  await polish.getByRole("textbox", { name: /输入你想强化的方向/ }).fill("压缩解释性旁白，强化人物停顿。");
  const firstFocus = polish.getByRole("checkbox").first();
  await expect(firstFocus).toBeChecked();
  await firstFocus.click();
  await expect(firstFocus).not.toBeChecked();
});
