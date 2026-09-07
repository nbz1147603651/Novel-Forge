> 历史审计记录，保留当时的观察，不代表当前交付状态。当前状态见[文档导航](../../README.md)。

# Dashboard search placement QA

Date: 2026-07-18

## Comparison target

- Source visual truth: `/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-194a662a-ce8c-4f46-9040-e3b9ce7bc660.png`
- Implementation capture: `/tmp/nimo-ui-parity-dashboard-search/web-dashboard-1440x900.png`
- Viewport and state: 1440×900, `narrative_ember`, populated dashboard. The
  source is a framed 82% annotation capture, so the assessment compares the
  dashboard content region rather than its surrounding Codex chrome.
- Focused comparison: the two red rectangles in the source identify the
  desired relocation: move the only volume search field from the lower
  catalogue filter surface into the unused lower-left area of the welcome
  panel.

## Findings

No P0, P1, or P2 visual differences remain for the requested relocation.

- Typography: the input keeps the shared compact UI type size and uses the
  established dashboard control treatment; the larger welcome title remains
  visually dominant.
- Spacing and layout rhythm: the search now occupies the lower-left band of
  the welcome panel, below the two creation actions. The catalogue surface
  keeps only its result summary and filter chips, eliminating the duplicated
  input and the annotated unused space.
- Colors and visual tokens: the search uses the existing surface, border,
  focus, and theme tokens; no new palette, gradient, or icon treatment was
  introduced.
- Image and asset fidelity: no asset was added or replaced; the existing
  source logo remains unchanged.
- Copy and behavior: placeholder and accessible name stay the same. Typing
  filters project cards immediately, and filter chips continue to combine
  with the query.

## Interaction evidence

- `tests/project-operations.spec.ts` asserts that the search belongs to
  `.dashboard-hero-search`, sits in the hero's lower band, no search input
  remains in `.filter-surface`, and `测试短篇` filters the catalogue.
- The focused test passed on 2026-07-18.

## Residual verification gap

The native fixture hand-off has been changed from a post-load URL replacement
to a restricted document-start seed, so React receives the dashboard fixture
before it can load a previous stored Chapter Studio session. Unit tests cover
that precedence. The current Codex host nevertheless received a macOS Screen
Recording denial when it attempted to capture the temporary native window, so
this screen cannot yet be marked as cross-application pixel accepted. The
browser-rendered capture remains the visual evidence for this relocation.

## Final result

blocked

---

# 叙事蓝图紧凑布局 QA — 2026-07-29

## Comparison target

- Source visual truth:
  `/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-6f8eedaa-ee46-470d-bfd1-4700dec30f0d.png`
  (3024×1750 px, 144 dpi).
- Implementation capture:
  `/private/tmp/nimo-reader-audit/blueprint-compact-after.png`
  (1280×720 px, browser CSS viewport 1280×720, device scale factor 1).
- Normalized full-view comparison:
  `/private/tmp/nimo-reader-audit/blueprint-comparison-clean.png`. The source was
  center-cropped and resampled to 1280×720, then stacked with the unchanged
  1280×720 implementation capture in one comparison image.
- State: populated long-form project, 卷帙 → 叙事蓝图. The source uses the
  real project and the browser capture uses the deterministic test fixture;
  geometry and density are compared, not title or lane-count parity.
- Focused comparison was not required: the affected surfaces—canvas bounds,
  left labels, right endpoint, and vertical lane rhythm—are clearly visible
  in the full-view composite.

## Findings and iteration history

- Earlier P1: the proportional canvas reserved 976 source units for two
  subplot lanes and six character arcs, creating excessive vertical travel.
  Fix: compacted the semantic bands and changed the same case to 620 units;
  each lane count still grows deterministically.
- Earlier P1: lane labels were anchored at x=82 and could extend beyond the
  left viewBox edge. Fix: introduced shared horizontal safe-area geometry,
  moving labels to x=174 and the plot to x=188…1320.
- Earlier P2: a 1160px SVG minimum width forced a nested horizontal viewport.
  Fix: the SVG now fills its host at 100% width with no minimum-width floor.
  Browser measurement after the fix is `clientWidth=990`,
  `scrollWidth=990`, with the SVG contained at x=254…1232 inside its
  x=247…1239 host.
- Post-fix evidence: the 1280×720 capture shows all fixture labels, endpoints,
  subplot lanes, character arcs, and the start of the detail footer without
  horizontal clipping. The real-data six-arc geometry is covered by the
  explicit `timelineContentHeight(2, 6) === 620` regression.

## Required fidelity surfaces

- Fonts and typography: the existing unified reading/UI families and type
  hierarchy are unchanged; no new local font override was introduced.
- Spacing and layout rhythm: vertical track gaps are reduced while preserving
  distinct phase, mainline, subplot, and character-arc bands.
- Colors and visual tokens: all existing parchment, ink, jade, blue, red, and
  violet tokens are preserved.
- Image quality and assets: no image, logo, icon, or illustration was added or
  replaced; the timeline remains the existing interactive data
  visualization.
- Copy and content: labels, summaries, accessible names, and detail content
  are unchanged.

## Interaction and runtime evidence

- Browser path: `http://127.0.0.1:1420/` → 卷帙 → 叙事蓝图 → select the
  第 4 章 milestone. The footer changed from the phase summary to `隐瞒`.
- Page identity, meaningful DOM, and framework-overlay checks passed.
- Browser console warnings/errors: none.
- `pnpm --filter @nimo/desktop test`: 47 files / 237 tests passed.
- `pnpm --filter @nimo/desktop build`: TypeScript and Vite production build
  passed.
- `git diff --check`: passed.

## Remaining risk

- The in-app Browser fixture contains three subplot lanes and two character
  arcs. Six-arc growth is covered structurally and by unit tests, but a
  screenshot using the user's private project data was not generated.

## Final result

passed

---

# 声腔 PySide 全流程复刻验收 — 2026-07-29

## 参考与实现

- 用户提供的 PySide 参考覆盖：配音团队、配音脚本、逐段编辑、配音室、
  后处理、声音资源库、导出菜单与更多菜单。
- React 终稿及并列比对：
  `<local-artifact>`
- 总览比对图：
  `comparison-contact-sheet.jpg`

## 验收结论

- 五段主导航、脚本纵向阅读流、逐段编辑器、配音室双栏、后处理播放器与
  实时脚本层级均与 PySide 工作流一致。
- 配音团队右栏已收束为“日常试听 + 音色选择 + 三项表演微调”的无滚动摘要；
  浏览器实测 `clientHeight === scrollHeight === 535`，`overflow-y: hidden`。
- 完整匹配依据、试听边界与角色事实已迁入“声纹编辑”，主工作台不再重复堆叠。
- 声音资源库不再开启嵌套弹窗，改为后处理内嵌子工作区；候选列表与详情各自
  使用内部阅读通道，整个页面保持单层工作台。
- 导出与更多操作恢复 PySide 弹出菜单结构；MP3/WAV/FLAC、-14 LUFS、
  字幕、全书打包，以及五类清理入口均可进入后续确认流程。
- 配音脚本的“声场设计”和后处理的“补齐声音素材”统一进入同一声音资源库，
  不再出现两套入口对应两套页面的重复逻辑。
- 逐段编辑器恢复 PySide 的全宽双栏、固定底部导航和删除入口，并补齐片段类型、
  情绪强度、说话人确认、平台能力边界；片段类型与情绪强度已贯通
  TypeScript 命令、FastAPI 严格模型和项目脚本持久化。
- 视觉比对确认主要差异来自验收数据量（React fixture 为 4 角色/4 片段，
  PySide 截图为 12 角色/201 片段），布局和交互密度保持一致。

## 自动验证

- `pnpm --dir clients/nimo-desktop check`
- `pnpm --dir clients/nimo-desktop test`：51 files / 253 tests passed
- `pnpm --dir clients/nimo-desktop build`
- `.venv/bin/python -m pytest tests/unit/test_engine_voice_script_commands.py
  tests/unit/test_api_ui_views_contract.py -q`：12 passed
- `ruff check`（本次变更涉及的 API、TTS 执行与测试文件）：passed
- `git diff --check`：passed

## Final result

passed

---

# 声腔声纹工作台与配音路由迁移 QA — 2026-07-29

## 参考与比较输入

- 右侧信息密度参考：
  `/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-57d6a31b-857b-46a8-a678-0745eae41bbd.png`
  （2048×1152，用户真实项目状态）。
- 平台设置路由参考：
  `/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-ce54f52a-1f5d-47d5-abc8-134b5a628d3e.png`。
- 火候页去重参考：
  `/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-c6516081-81f6-4c95-ac6d-bfbdde69b250.png`。
- 实现全景：
  `<local-artifact>`。
- 声纹区域聚焦：
  `<local-artifact>`。
- 文本路由聚焦：
  `<local-artifact>`。
- 火候页去重：
  `<local-artifact>`。
- 同输入全景比较：
  `<local-artifact>`。
- 同输入局部比较：
  `<local-artifact>`。
- 验收视口：1280×720、device scale factor 1、`narrative_ember`、配音团队已生成的确定性前端演练状态。
  参考图分辨率更高，因此比较以信息层级、有效占用、视觉节奏和交互状态为准。

## 发现、修复与迭代

- P1 已修复：角色详情右侧下半区原本大面积空置。现在表演参数下方继续承载“声纹画像、匹配依据、
  试听边界、角色表达边界”四块真实角色资料，保持与选择音色、试听和批准动作处于同一滚动上下文。
- P1 已修复：配音文本任务缺少独立路由入口。平台设置的“文本智能”现在承载 PySide 对应的六类任务，
  每类均有主模型、三级后备链和 Temperature；保存会组成六条受控路由命令。
- P1 已修复：火候页同时暴露配音路由和 TTS 参数，形成重复入口。配音任务已从通用流程路由中过滤，
  四组 TTS 折叠参数也已移除，并保留明确的迁移提示；通用模型档案仍在火候页统一管理。
- P2 已修复：后端创建参数白名单与 PySide 实际环境变量名不完全一致。默认平台、模型、语速以及
  配音策略、预算、运行时、声音设计和文本智能参数均补齐保存/回载映射。
- Typography、颜色、边框、按钮和卡片继续使用现有 NIMO 纸面与朱砂 token，没有引入新的图形风格或资产。

## 交互与运行时证据

- 角色切换：从“旁白”切换到“林逐”后，标题、角色定位和 `画像匹配 77%` 同步更新。
- 档案展开：“查看完整档案”展开后显示“为什么匹配”和“音色设计简报”，可再次收起。
- 路由：六类任务均渲染主模型、后备 1–3 和温度；把“配音脚本生成”温度改为 `0.25` 后，
  保存反馈为“前端演练已接受 6 条路由”，字段值保持。
- 火候页：展开通用流程路由后没有配音任务或 TTS 四组参数，并显示迁移到
  “声腔 → 平台设置 → 文本智能”的唯一入口说明。
- 浏览器日志只有 Vite 连接和 React DevTools 提示；无 warning、error 或框架错误遮罩。
- `pnpm --dir clients/nimo-desktop build` 通过。
- `pnpm --dir clients/nimo-desktop test -- --run` 通过：50 files / 251 tests。
- `.venv/bin/python -m pytest tests/unit/test_api_ui_views_contract.py -q` 通过：11 tests。
- `.venv/bin/python -m py_compile novel_forge/api/routes/ui_views.py` 通过。

## Final result

passed

---

# UI Parity Design QA — 2026-07-22

## This slice

- **Passed:** the left rail uses a fixed shell with a compact, centred
  navigation track; the footer/world summary is centred on the same axis.
- **Passed:** 卷帙 keeps a stronger outer-tab tier than its nested tabs, using
  the shared compact type ramp.
- **Passed:** 章台 checkpoint now has two source-captured states: the inline
  checkpoint card and the separate, non-modal right-side `Qt.Tool` decision
  panel. The React counterpart is narrow, right-aligned, non-blurring and
  independently closable.
- **Passed:** the source capture runner can compose that otherwise separate
  native tool window into its deterministic window-frame image.

## Evidence

- PySide6 source: `prepared`, `running`, `checkpoint`, and
  `checkpoint-dialog` captured at `1440×900`, `narrative_ember`.
- React: `pnpm --filter @nimo/desktop check` and `test` — 34 files / 160 tests
  passed.
- React visual regression: the source checkpoint floating-panel screenshot
  passed after its baseline update.
- Browser QA: populated 卷帙 reader rendered without framework overlay;
  checkpoint close left its inline action state intact; a clean Chapter Studio
  tab had no console warnings or errors.

## Remaining acceptance blockers

- **P1:** every primary surface still needs same-fixture comparison across its
  named interaction states, especially 卷帙 graph tooltips/edge editing,
  机杼 error/resume/history/export, 火候 route/status variants, 章台 toolbar
  actions and 声腔 clone/script/room/delivery flows.
- **P1:** native Tauri/macOS screenshot comparison is blocked by missing Screen
  Recording permission; browser screenshots remain diagnostic only.
- **P2:** keyboard focus order, reduced-motion behaviour, overflow at compact
  desktop sizes and cross-theme captures need final matrix evidence.

Phase 1 is therefore still in progress; no page is marked fully 1:1 accepted
from visual similarity alone.

---

# Left rail compactness QA — 2026-07-22

## Comparison target

- Source visual truth: `/private/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-18adb27a-08c4-4059-ba84-be65b7dfb139.png`
  (366×1046). It establishes the existing NIMO logo, warm sidebar palette,
  navigation order, active treatment and world-summary content.
- User-directed adjustment: narrow the left navigation labels and centre the
  world-summary card content; these two changes intentionally supersede the
  source screenshot's wide active row and left-aligned footer copy.
- Implementation capture:
  `clients/nimo-desktop/tests/cross-theme-visual.spec.ts-snapshots/cross-theme-narrative-ember-dashboard-darwin.png`
  (1440×900, CSS viewport 1440×900, device scale factor 1,
  `narrative_ember`, populated dashboard).
- Full-view and focused-region comparison: the source rail and the
  implementation rail were rescaled to equal 900px height and reviewed in a
  single side-by-side composite. The source is a rail-only crop, so the
  assessment is deliberately scoped to sidebar geometry rather than the
  unrelated main canvas.

## Findings

No actionable P0, P1 or P2 differences remain for this requested sidebar
adjustment.

- Typography: the navigation now uses a deliberate 13px / 36px compact tier;
  the NIMO mark, page title and content typography remain visually dominant.
- Spacing and layout rhythm: the fixed rail is 252px at normal density and
  236px on the compact breakpoint; its navigation and footer tracks are 196px
  and centred. This removes the oversized active-row impression while adding
  canvas room.
- Colors and visual tokens: the existing sidebar gradient, accent border,
  active marker and theme-derived text colours are untouched.
- Image and asset fidelity: the existing `BrandLogo` is preserved; no logo,
  icon or raster asset was replaced.
- Copy and behavior: all six page labels, ARIA labels and the sidebar collapse
  control are unchanged. The current browser check switched from 卷帙 to 机杼
  via the compact navigation and confirmed the active state, page heading and
  non-blank main surface.

## Verification

- Browser/IAB: the local `projects` surface rendered without an error overlay;
  the compact rail measured 236px at the 1280×720 responsive viewport, its
  196px navigation labels measured 13px / 36px, and the footer's computed
  alignment was `center`.
- Type and unit checks: `pnpm --filter @nimo/desktop check` and
  `pnpm --filter @nimo/desktop test` passed (35 files / 162 tests).
- Visual and interaction regression: `pnpm --filter @nimo/desktop exec
  playwright test --workers=1` passed (130 tests). Cross-theme snapshots were
  refreshed for the intentional shared-shell geometry change.

## Follow-up polish

- P3: continue the source-by-source review of page-local tooling; this rail
  slice deliberately does not claim overall application 1:1 acceptance.

## Final result

passed

---

# 卷帙与主题切换验收 — 2026-07-26

## 结果

- 长篇卷帙显示完整的 8 个一级分类；基础设定首次进入即打开「故事规格」。
- 后端与 PySide 共享文档目录，章回、报告和治理产物不再被缩减为 3 个页签。
- 切换为「青墨」后，侧栏字体仍为 `19px`、字体族和尺寸不变；根元素不再使用全局主题过渡类。

## 验证

- 浏览器/IAB：`ink_jade` 下检查了完整一级/二级标签、故事规格文档和侧栏尺寸。
- `pnpm --filter @nimo/desktop check`、主题单测、卷帙交互测试及 9 个卷帙视觉状态均通过。
- `tests/unit/test_project_reader_catalog.py` 覆盖 API 返回的完整长篇目录与第 21 章、章节报告和治理文档。

---

# 卷帙 / 机杼复刻 QA — 2026-07-28

## 本轮通过项

- 1440×900 的页面壳层、顶栏、侧栏、一级/二级页签及机杼首屏几何已按
  PySide 同状态收敛；不再使用基准视口“紧凑密度例外”。
- PySide 截图工具与 React Mock Engine 共同读取
  `tools/ui-parity/fixtures/reader-workflow.json`。卷帙截图还会统一把所有
  嵌套滚动区归零，避免把本机 QSettings 的旧滚动位置当成基准。
- 卷帙 `spec.json` 已恢复源端文档工具栏、富视图/原始 JSON 切换、复制、
  路径能力禁用原因与文件状态栏；正文区域占据工作台主体，不再使用大块
  摘要卡制造无效留白。
- 机杼的启动、停止、恢复、清理历史均等待 Engine 确认；任务卡和流式详情
  读取 Engine JobView / task-stream 快照。模型级 `stream_end` 或
  `stream_error` 不再被误判为整个工作流结束或失败。
- SSE 重连按稳定 cursor 去重，跨模型流按流内序号和时间排序；事件以 32ms
  批量提交，正文视图保留最近 240 条、原始事件保留最近 500 条，避免长任务
  高频输出阻塞表单。
- 字体管理已进入火候设置和 Engine 持久层。卷帙、机杼、文档阅读、流式正文
  与代码/JSON 分别消费统一 UI、阅读、等宽字族及语义字号 token；页面局部
  不再硬编码独立大字号。

## 对照证据

- 卷帙同夹具合成图：
  `/private/tmp/nimo-projects-pair-source-parity-final.png`
- 机杼同夹具合成图：
  `/private/tmp/nimo-workflow-pair-updated.png`
- 流式详情实现截图：
  `/private/tmp/nimo-react-floating-stream-final.png`
- 浏览器运行日志：无 warning / error。
- TypeScript：42 个测试文件、216 个测试通过。
- Python Engine / UI view / job service：59 个定向测试通过；相关文件
  `py_compile` 与 Ruff 通过。

## 剩余验收阻断

- P1：大纲分析/应用/历史、角色与关系编辑仍有前端会话实现，尚未全部改为
  revision-aware Engine 命令，也未完成 Story Kernel 同步和下游陈旧标记。
- P1：短篇 AI 表单生成/润色在真实 Engine 中按能力禁用，尚未接入真实候选
  任务；导入导出尚未完成 Tauri 原生文件选择器与 Web 文件交换双实现。
- P1：尚未完成五主题全部状态矩阵与 Tauri 原生窗口复测，因此不能宣称达到
  0.75% 全屏差异阈值。
- P2：PySide `QTextBrowser` 在标题与徽章间保留了明显空白；React 按本轮
  “主题内容优先、避免过多留白”的要求保持更紧凑的内容流。这是有意差异，
  需在最终严格像素验收前确认产品优先级。

## Final result

blocked

---

# 卷帙「拟人化对比」富渲染验收 — 2026-07-29

## 参考与实现

- PySide 参考：`/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-33244d39-02af-4bce-91f5-50d913d57686.png`
- React 实现截图：`/private/tmp/nimo-reader-audit/humanize-diff-after.png`
- 并列比对：`/private/tmp/nimo-reader-audit/humanize-diff-comparison.png`
- 验收视口：1280×720（参考图按相同 720px 高度等比缩放后并列）；密度保持“版本对比 → 诊断 → 相似度 → 变更详情”的 PySide 顺序，并把统计区压缩为约 180px 的固定头部，为正文差异让出阅读高度。

## 核查结果

- 原先占据主体的 `additions`、`deletions`、`similarity_ratio`、`unified_diff` 原始键值表已替换为专用 `text_revision_diff` 渲染器；完整 JSON 仍通过“查看原始数据”保留。
- 版本对比保留 PySide 的“版本对比”、采纳/补丁/变更率诊断、相似度、增删计数、原稿→修订稿标签与“变更详情”层级。
- 差异正文使用朱红删除线与青绿新增底纹连续排版，正文宽度占满报告面板；变更统计收进原生折叠项，避免削减可读区域。
- 浏览器/IAB 已打开「卷帙 → 章节 → 报告 → 表达 → 拟人化对比」，确认各级页签、95.3% 分数、`<del>`/`<ins>` 行内差异以及“变更统计”展开可用；控制台无 warning/error。
- `pnpm --filter @nimo/desktop check`、`pnpm --filter @nimo/desktop test`（48 files / 239 tests）与生产构建均通过。

## Final result

passed

---

# 卷帙全标签 PySide 复刻审计 — 2026-07-29

## 覆盖结论

长篇项目的 8 个一级标签均已按 PySide `ProjectsPage` 的顺序和层级进入
React 阅读器；不以“有文件才显示标签”的方式缩减导航。当前从 Engine 读取
完整数据，并在需要交互的面板使用受控命令或显式的只读状态提示。

| PySide 标签 | 新 UI 对应 | 本轮验收 / 优化 |
| --- | --- | --- |
| 基础设定 | 故事规格、世界观、角色与实体、要素与风格 | 首个子页直接打开规格；保留富视图、原始 JSON、复制与来源状态。 |
| 资料检索 | 三份资料报告 | 保留报告、分析、校准的二级层级；来源、事实卡与正文共用文档渲染器。 |
| 叙事蓝图 | 可选节点时间线 | 阶段、主线、支线、角色弧光都可选；画布压缩到工作台高度内，不再横向截断。 |
| 章节设计矩阵 | 源端矩阵文档 | 用统一文档阅读器显示核心冲突、章节目标、钩子与原始数据入口。 |
| 章节大纲 | 阅读 / 润色工作台 | 保留多选章节、同步契约、延长全书；工作台填满主体高度，正文区不再留在上半屏。 |
| 章节 | 正文 / 报告、版本与修订 | 保留章节检索、草稿/终稿、四组报告和细粒度报告叶；拟人化对比改为 PySide 风格行内差异阅读。 |
| 治理 | 全书审修、初始化准入、一致性治理 | 保留三层分组，空报告仍以明确空状态呈现而非静默丢弃。 |
| 追踪 | 关系追踪、Token 追踪 | 关系演变/图谱/筛选仍在；Token 改为运行日志聚合，不再显示虚构的固定模型数据。 |

## 本轮修复

- Engine 现在把 PySide `collect_project_token_analytics()` 的日志聚合投影为
  `token-analytics` 虚拟文档，修复正式项目中“Token 页空白、Mock 却有内容”的
  数据边界问题。
- Token 偏好接口补齐默认价、单位和逐模型价格覆写；总览、模型、步骤、价格四页
  都来自同一份聚合数据与项目持久化偏好。
- 章节大纲、章节、报告、治理及 Token 工作台显式占满阅读器的可用高度，避免
  工作台缩在顶部而下方留下无意义空白。

## 当前审计证据

- `/private/tmp/nimo-reader-audit-20260729/01-foundation.png`
- `/private/tmp/nimo-reader-audit-20260729/04-blueprint.png`
- `/private/tmp/nimo-reader-audit-20260729/10-outline-final.png`
- `/private/tmp/nimo-reader-audit-20260729/03-humanize-diff.png`
- `/private/tmp/nimo-reader-audit-20260729/06-token-tracking-final.png`
- `pnpm --filter @nimo/desktop check`、定向 Vitest（48 files / 241 tests）、
  生产构建、13 个 Python API/UI/Token 定向测试和 Ruff 均通过。

## 已知的功能边界

- 角色、关系与支线编辑当前仍是明确标注的前端会话改动；PySide 对应的项目文件
  写回、Story Kernel 同步和 revision 冲突处理尚未通过新的 Engine 写命令完成。
  这些动作没有被伪装为已保存，后续应作为独立的写入链路迁移，而不是把本轮的
  只读/预览复刻误称为完整持久化。

## Final result

passed with documented write-path gap

---

# 卷帙「角色与实体」三栏工作台审计 — 2026-07-29

## 参考与验证

- 用户参考：`/var/folders/d9/4gvz34hs57b8658m2hpbt6ym0000gn/T/codex-clipboard-d7f161b3-39ea-43e6-a76f-bceb2b729abd.png`
- 优化前：`/private/tmp/nimo-character-audit-20260729/01-before.png`
- 优化后：`/private/tmp/nimo-character-audit-20260729/03-final.png`
- 验收视口：1280×720；三栏网格为 223 / 548 / 243 px。浏览器实测列表、正文和关系栏均为 `overflow-x: hidden`，网格与正文 `scrollWidth === clientWidth`。

## 修复与交互

- 三栏工作台现在使用一个裁切画布，三列只负责纵向阅读；长角色名、长关系类型和长正文不会再把中间档案横向拖动或露出重复间隙。
- 搜索和状态筛选合并成一行，减少列表顶部重复的垂直占用；窄窗口自动降为单列而非产生横向截断。
- 角色标题不再重复“定位/状态/时间线”三项；详情栏收束为“关系脉络”，移除尚未接入数据的登场章节占位和重复标签。
- 详情展开时，关系只在右栏展示；点击“隐藏详情”后，关系摘要自动回流到正文，已在浏览器中验证，信息不会丢失。
- 身份字段会在中等正文宽度下跨两列，避免长身份文本被挤成过窄的竖排卡片；宽工作区仍还原为五项并列概览。

## Final result

passed
