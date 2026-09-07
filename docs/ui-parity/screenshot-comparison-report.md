# 新老 UI 截图对比分析报告

生成日期：2026-07-22 · 分支：`codex/ui-parity-phase-1` · 视口：1440×900

## 1. 方法论

为绕过 macOS 屏幕录制权限对 Tauri 原生截图的阻塞，本报告采用可复现的双端离屏渲染对照：

- **源端黄金图（PySide6）**：`QT_QPA_PLATFORM=offscreen` 下用 `tools/ui-parity/run_pyside_golden_capture.py` 渲染 `NovelForgeDesktopWindow`，绑定 `tests/desktop/ui_parity_fixtures.py` 的固定 workspace 快照，冻结动画，输出 33 张全帧 PNG（`/tmp/nimo-parity-A/pyside/`）。
- **新端截图（React/Tauri）**：用 Playwright 无头 Chromium 渲染 `clients/nimo-desktop`，经同一套 `?__nimo_ui_parity=1` 受限夹具 URL（`tests/parity-capture.spec.ts`），输出 33 张同名 PNG（`/tmp/nimo-parity-A/react/`）。
- **像素对比**：`tools/ui-parity/compare_screenshots.py`，阈值 `max_changed_pixel_ratio=0.0075`、`max_mean_channel_delta=0.0075`（与项目既有标准一致）。
- **并排对照图**：`tools/ui-parity/compose_screenshot_pair.py`，归档于 `docs/ui-parity/comparison-pairs/`，仅作人工审阅辅助，不作门槛指标。

> 采集要点（已踩坑修正）：章台/声腔/火候为懒加载组件（`Lazy*Page` + `<Suspense fallback={LoadingSurface}>`），且预取在 workspace 加载后 1200ms 才触发。固定等待会截到骨架屏（顶栏已渲染、主体为 3 条 shimmer），造成「假性高差异」。修正后的采集脚本对每页等待其根容器类（`.studio-page`/`.voice-page`/`.settings-page` 等，仅懒组件挂载后才存在）可见后再截屏。

## 2. 全帧像素指标（33 对，按 changed_pixel_ratio 升序）

| 表面 / 状态 | changed_pixel_ratio | mean_channel_delta | 全帧门槛通过 |
| --- | ---: | ---: | :--: |
| projects / reader | 0.5055 | 0.0413 | 否 |
| settings / creative-temperature | 0.5273 | 0.0576 | 否 |
| workflow / twilight_ink | 0.7190 | 0.0342 | 否 |
| settings / twilight_ink | 0.7465 | 0.0350 | 否 |
| workflow / ink_amethyst | 0.7557 | 0.0655 | 否 |
| workflow / ink_jade | 0.7650 | 0.0670 | 否 |
| workflow / stillwater | 0.7667 | 0.0648 | 否 |
| workflow / narrative_ember | 0.7726 | 0.0672 | 否 |
| workflow / running | 0.7735 | 0.0675 | 否 |
| dashboard / twilight_ink | 0.7896 | 0.0290 | 否 |
| settings / narrative_ember | 0.8003 | 0.0709 | 否 |
| settings / stillwater | 0.8029 | 0.0688 | 否 |
| settings / ink_amethyst | 0.8067 | 0.0698 | 否 |
| settings / ink_jade | 0.8118 | 0.0722 | 否 |
| voice_studio / twilight_ink | 0.8151 | 0.0262 | 否 |
| chapter_studio / checkpoint-dialog | 0.8156 | 0.0730 | 否 |
| chapter_studio / twilight_ink | 0.8239 | 0.0388 | 否 |
| dashboard / stillwater | 0.8256 | 0.0592 | 否 |
| dashboard / narrative_ember | 0.8389 | 0.0626 | 否 |
| dashboard / ink_amethyst | 0.8418 | 0.0603 | 否 |
| dashboard / ink_jade | 0.8496 | 0.0620 | 否 |
| voice_studio / stillwater | 0.8500 | 0.0568 | 否 |
| voice_studio / narrative_ember | 0.8566 | 0.0596 | 否 |
| chapter_studio / stillwater | 0.8580 | 0.0688 | 否 |
| voice_studio / ink_jade | 0.8598 | 0.0598 | 否 |
| settings / model-routing | 0.8609 | 0.0675 | 否 |
| voice_studio / configured-team | 0.8610 | 0.0748 | 否 |
| voice_studio / ink_amethyst | 0.8616 | 0.0576 | 否 |
| chapter_studio / narrative_ember | 0.8649 | 0.0691 | 否 |
| chapter_studio / ink_amethyst | 0.8663 | 0.0707 | 否 |
| chapter_studio / running | 0.8669 | 0.0758 | 否 |
| chapter_studio / checkpoint | 0.8673 | 0.0758 | 否 |
| chapter_studio / ink_jade | 0.8677 | 0.0712 | 否 |

**全帧 0.75% 门槛：0/33 通过。** 但此结果**不能**解读为「复刻失败」——见第 3 节根因分解。

## 3. 差异根因分解（关键结论）

逐对审阅并排图后，高像素差由四类来源叠加，其中前三类属「已批准偏差 / 固有差异」，**不应**通过回退设计来「修复」：

1. **已批准的紧凑密度例外（主因）**。React 端采用 `4/8/12/16/20/24px` 间距与 `10–24px` 字阶的紧凑控制层，顶栏/hero/卡片几何普遍小于 PySide6。一旦 hero 或顶栏高度不同，其下所有元素发生**垂直偏移级联**，即使内容逐字相同，逐像素比较也会把整列判为「改变」。此例外已记录于 `fidelity-ledger.md`，且明确「不对紧凑主页面使用全帧严格像素门槛」。
2. **已批准的案头搜索框上移**。React 把检索框从「卷库总览」移入 hero（`verification-matrix.md` 记为 compact dashboard search relocation）。
3. **跨渲染引擎字体光栅化差异（固有）**。Qt（offscreen）与 Chromium 对中文 glyph 的抗锯齿/度量不同，所有含中文文本的区域都会贡献差异，无法在保持各自原生渲染的前提下消除。
4. **夹具数据分歧（唯一真实内容缺口）**。案头 hero：PySide6 视觉夹具 `build_visual_workspace_snapshot` 设 `featured_project=None`（即便存在 2 个项目，hero 显示空态「案头未陈一卷」）；React mock 以 `workspace.projects[0]` 作为 featured（hero 显示「案头当看：测试短篇」）。这是两个采集数据源未对齐所致，并非 React 组件逻辑错误——React 的 hero 逻辑与生产语义一致（有项目即显示 featured）。

> 结构/语义保真度：在排除上述偏移后，并排图显示六页面的**区块、组件、状态机、内容层级逐一对应**。以章台 running 为例（`comparison-pairs/chapter_studio--narrative_ember--chapter-running.png`）：顶栏、工作台选择器（写作模式/裁决模式/重写策略）、三栏主体（章节轨道 / 执行面板 / 记忆上下文）、章节列表状态、记忆七标签、承接要点/后续提示/评分卡，两端完全同构。

## 4. 结论与门槛判定

- **视觉复刻**：结构与语义层面**已达成高保真复刻**；全帧像素层面**按设计不追求** 0.75% 门槛（紧凑密度 + 搜索上移为已批准产品方向，字体光栅化为固有差异）。组件级阈值与语义/交互证据才是这些紧凑主页面的验收依据（与 `verification-matrix.md` 政策一致）。
- **功能交互复刻**：见第 5 节交互验证；现有 Playwright E2E 与 `interaction-parity-audit.md` 覆盖六页面的核心交互边界，Mock 路径下行为与源端同构。
- **唯一待决内容缺口**：案头 hero 的夹具数据分歧。建议后续将 React 的 parity 夹具 workspace 与 PySide6 `ui_parity_fixtures` 的 `featured_project` 字段对齐（或在 React mock 增加「有项目但无 featured」的可表达状态），使空态 hero 也可被对照捕获；此项不影响生产逻辑。

## 5. 交互验证（Phase D）

- 静态一致性：`pnpm check:interaction`（`tools/ui-parity/check-interaction-consistency.js`）。
- 行为回归：`pnpm visual`（11 个 Playwright spec，含章台/卷帙/短篇/声腔/工作流/火候连通性/项目操作）。
- 交互边界对照：`docs/ui-parity/interaction-parity-audit.md` 逐页记录「按钮可见」与「行为可用」的分离验证。

## 6. 产物清单

- 全帧指标：`comparison-pairs/comparison-summary.csv`
- 并排对照图：`comparison-pairs/*.png`（6 张关键表面）
- 逐对 diff 图与 JSON：`/tmp/nimo-parity-A/diff/`
- 采集脚本：`clients/nimo-desktop/tests/parity-capture.spec.ts`
