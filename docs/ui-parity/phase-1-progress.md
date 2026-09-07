# Phase 1 UI Parity Progress

Last verified: 2026-08-31

2026-08-31 transition: `nimo` now starts React/Tauri with its managed Engine;
`nimo-t` remains an alias and `nimo-p` is the frozen PySide safety/compatibility
fallback. The detailed Phase 1 evidence below remains historical migration
evidence, not a requirement to duplicate new product work in PySide.

## Scope delivered on `codex/ui-parity-phase-1`

- 已同步 `main` 的版本化 Engine API、能力协商、任务观察/取消、章台
  `prepare_chapter` 恢复链与说话人复核链。页面继续保留最新的 PySide6 视觉/交互
  复刻，HTTP 模式不再混用页面级 Mock fallback。
- 主/后备入口已经固化：`nimo`（及兼容别名 `nimo-t`）启动新端，`nimo-p` 启动冻结后备端。本地
  默认由启动器管理回环 FastAPI 生命周期；同一 Tauri build 可在 document-start
  注入 HTTPS 云端 URL 与临时 bearer token，token 不进入 localStorage 或 bundle。
- React/Tauri 运行时地址不再依赖 `VITE_ENGINE_BASE_URL` 的构建期烘焙；本地与云端
  共用一个 HTTP adapter、同一份能力协商和错误表面。默认 CORS 同时覆盖 Vite 与
  Tauri production origins，远端仍受 trusted-host/local-only/token 三层边界保护。
- 卷帙「角色与实体 → 图谱」已针对真正的
  `CharacterGraphWidget` 重新核对。固定 `1280×720` 全页 source fixture
  （SHA-256 `34d7b5df65acd1d5d294412406fef214be22475e027a3de0960788c8368447d0`）
  与 920×540 populated/focused 组件夹具共同确认：主角/第二主角核心布局、其余节点从
  12 点钟开始的稳定环形布局、随实际画布尺寸变化的节点半径、关系线端点裁切、焦点
  淡化及激活关系上的流动点均来自 PySide6 源逻辑。React 现在以 `ResizeObserver` 读取
  实际 SVG 画布，复用相同的半径/核心节点公式；点击节点、关系线、画布空白处的
  聚焦/清焦，角色列表与图谱的选择分离也都有浏览器回归。全页仍遵循已记录的紧凑密度
  例外，且尚未完成同状态 Tauri 像素门槛，不能标记为跨应用视觉验收通过。
- 同一份三角色 fixture 现额外冻结 PySide6 的 hover-node 与 hover-edge：长角色卡在实际
  Qt 弹窗中为 `612×338`，关系卡为 `248×110`。React 图谱将这两种提示卡渲染为画布外的
  window-level portal，并按真实 DOM 尺寸在鼠标右下/左上之间翻转、贴边；因此长卡不会再被
  SVG 或 reader pane 裁切。浏览器回归覆盖节点/关系 hover、内容、尺寸、边界以及空白画布
  清除提示卡。该 Chromium 证据只说明实现可重复，仍不替代 native Tauri 同状态像素验收。
- Independent React + TypeScript + Tauri shell in `clients/nimo-desktop/`.
- Six primary surfaces: 案头、卷帙、机杼、火候、章台、声腔.
- Source SVG logo, a centered mark, collapsible/persistent navigation, five
  desktop themes, and theme-aware macOS window-icon plumbing.
- The common geometry now has one compact owner: `4/8/12/16/20/24px` spacing,
  `10/11/12/13/16/22/24px` type, and `28/32/36px` dense/common/readable
  controls. 案头与机杼 consume that contract at 1440×900, exposing the next
  content section without shrinking document prose or source-sized dialogs.
- UI-only session state; no Python imports, credential reads or persistence,
  provider calls, or project-file writes from the React client. A source-shaped
  API-Key field may hold a value only for the active form submission.
- Mock interaction paths for project management, readers, task flow,
  source-shaped error-log audit, stage-artifact envelopes, chapter checkpoints, audit/export/diff/memory,
  voice creation/review/post-production actions, a staged script segment editor,
  an asynchronous local voice-design brief workflow, consent-aware clone intent,
  and settings feedback.
- 声腔“供应商参考文件 ID”现有独立的源端 fixture：PySide6 的
  `show_text_input_dialog` 在不载入项目、供应商或媒体文件的前提下冻结为
  420×200 内容区。React 以受限
  `voice_dialog=clone-provider-file-id` URL 复现同一提示、自动焦点、空值禁用
  的“继续克隆”以及填入 ID 后的运行态转换；该 ID 只停留在打开弹窗的前端草案中。
  原端与 React 的并排审查已完成，但仍不能替代相同状态的原生 Tauri capture 和
  0.25% component threshold 验收。
- 最新 Tauri/macOS voice File-ID capture 已真实启动受限应用并取得
  `?__nimo_ui_parity=1&page=voice_studio&theme=narrative_ember&voice_dialog=clone-provider-file-id`
  的 1440×900 client frame（window-id capture，SHA-256
  `e5c4b070ddabb552fecd38aad3823ce2ed24dce6f68d11a41b97cfdd4b53baa0`）。
  裁到 420×200 组件后，QSS 回填后的严格差异为 0.1877 / 0.0273，仍未达到
  0.25% 像素阈值；结构、自动焦点、空值禁用与 ID 填写后的转换均已有浏览器与原生
  证据。该差异保持可见，不能宣称像素验收完成。
- 机杼首屏已按源端就绪态改为"创作入口 → 在制项目 → 模式选择 → 短篇/长篇
  表单 → 任务流 → 机杼关注"。短篇已拥有独立的 `short-workflow-session`：核心故事、
  参数与高级设定均可在紧凑主表单和字段弹窗间双向编辑；预设 CRUD、JSON 粘贴导入、
  只读导出、固定 AI 预览/札记、范围/必填校验与只读启动意向均有可逆交互。该 session
  不读写文件、不调用模型、不创建任务；长篇继续复用已有可交互预设工作台。
- 短篇工作流的**可逆表单交互**已与 PySide6 `ShortForm` 对齐；真实文件持久化、模型调用、
  job 创建和流订阅仍是明确的 Engine 命令边界：
  - 叙事要素偏好编辑面完整实现（92 个扩展要素卡 + 23 个题材预置 + 题材启发式映射）
    。React 默认折叠该 92 项面板并按需挂载，以避免首屏无谓布局；这是保持行为不变的
    有意密度/性能差异，不应误称为 PySide 的默认展开视觉状态。
  - 导出流程对齐源端两步选择（导出当前配置 / 导出模板 / 取消）
  - 步骤指示器 `StepIndicatorRow` 固化 11 步 `run_short` 契约并支持
    pending/active/done/failed；真实状态流等待 `subscribeTaskStream`。
  - 参数后缀对齐（目标字数、开始分段字数加"字"后缀）
  - 字段描述文案对齐（核心故事/高级设定区描述）
  - JSON 仅填入严格复刻 `_fill()` 的合并语义：未提供的字段不会退回默认值；叙事要素偏好
    同时按源库过滤未知/重复/no-op 项。
  - 创作札记按预设隔离：未绑定时显示提示；应用预览会更新当前预设，或在前端会话内自动
    建立同名绑定，再保存可恢复的完整快照。
  - 五状态基线冻结：空白态、已填态、导入态、校验失败态、启动意向态
- 卷帙首屏不再臆测选择首个项目：直接导航时复刻源端的空 reader 提示；选中
  长篇后才显示外层/内层分类与文档空态。reader 作为页面内滚动边界，避免长文档
  撑高窗口或遮挡全局状态栏。
- 卷帙的“章节 → 正文 → 入砚修”现复刻 `FinalRevisionWidget` 的局部会话链：选中
  文本后可选方向、查看明确标注为固定夹具而非模型输出的候选、预览保存差异、选择
  失效范围，并在未保存返回阅稿前确认。保存只写入当前前端会话，绝不触发项目文件或
  模型调用。相同两段正文、目标 4,216 字夹具的 PySide6 保存预览已冻结为 840×560 基线；浏览器回归
  覆盖候选接受、范围确认与未保存退出保护。原生 Tauri 同状态抓图与严格跨应用 diff
  仍待主机 Screen Recording 可用后完成。
- “角色与实体”不再只是 JSON 摘要：它在卷帙内复用同一份 typed `NarrativeToolsView`
  呈现角色档案与关系图谱；章台工具仍复用这个组件，避免两套角色编辑状态分叉。
- 卷帙图谱的可编辑行为已按 `CharacterGraphWidget` 收口到同一份前端会话：节点/边/
  画布右键菜单、节点边缘拖至目标角色、关系编辑与移除均有对应行为；反向重复连线会按
  源端的无向角色对更新，而不会制造第二条关系。关系编辑窗以 PySide6 实拍的
  `520×417` 几何、十种 canonical 类型与描述占位符为基线，并由浏览器回归锁定。
- “叙事蓝图”同样在卷帙内复用这一组件，以时间线、章节大纲和支线管理三种受控
  子视图替代静态 JSON 摘要；时间线可直接响应阶段、里程碑与支线节点选择。
- 章台的“叙事工具 → 支线管理”现复刻源端的新增、编辑确认、删除确认与刷新边界：
  章节范围、优先级和收束计划在一个纯函数本地 session 中一致更新，刷新会恢复只读
  Engine view。AI 润色与弧光转支线保留可见能力边界，前者不会伪造模型结果，后者在
  无可写命令/角色弧光契约时保持不可用。
- 章台的“叙事工具 → 拟人化库”已从工具栏意向升级为源端结构一致的本地会话：
  紧凑 ID/名称/来源/分类/严重度/命中表、过滤、启停、新建/编辑、命中样例、相似对、
  合并目标与二次确认、用户/导入条目删除、刷新、JSON 互换预览均复用一个纯函数 session。
  归档文件 I/O 仍明确留给受控 Engine 命令；第一阶段不伪造导入成功或写入项目库。
- 案头的“重建向量”已按源端收敛为确认边界：它明确只覆盖 zvec 记忆与表达通道
  语义索引，不会暗示可改写正文或大纲；确认后的队列回执只存在于 Mock 会话，留待
  EngineClient 以可取消后台作业接管。
- 案头“卷库总览”的唯一检索框现位于欢迎卡左侧的下沿空白区，紧接“起笔 / 去机杼”
  操作；下方筛选面只保留结果摘要与状态、篇幅筛选。这一调整复用既有 query/filter
  状态与共享控件 token，不引入第二套查询逻辑。浏览器回归断言其位置、移除旧输入框，
  并验证检索仍会过滤项目卡。
- 应用壳现将状态栏提升为窗口级底栏，与 PySide6 `QStatusBar` 同级；侧栏与内容
  区均在它上方结束，避免全屏文档改变侧栏高度。
- 火候页采用独立路由分包：轻量骨架即时可见，设置快照与页面模块在空闲期预取，模型/路由工作台仅在用户打开相应弹窗时加载。页面激活不触发任何通路检测。
- 火候模型状态已改为源结构一致的就地卡片队列：待检测、等待、检测中、成功、失败与不可调用均可在本地会话中复现，且不访问 Provider。
- 火候的可选“显示其余模型档案”现按非紧急 React transition 展开：首屏仍只呈现
  12 张卡，展开期间不会抢占取消、导航等即时操作；支持该能力的 WebView 会跳过屏外
  卡片的绘制/布局。跨页面的按钮按压、任务当前步骤与检测中状态也统一由
  `motion-system.css` 提供短促反馈，稳态 source fixture 不增加额外可见元素，且遵循
  系统“减少动效”。
- 火候检测的切页边界、两路手动队列、渐进模型卡、版本化后台诊断作业与验收预算见 [settings-connectivity-performance.md](settings-connectivity-performance.md)；它是后续 EngineClient 接入的唯一性能契约。
- 火候的“创作火候”现为源端同名的内联折叠表单，而非摘要弹窗：基础浮动、范围、自定义保护任务、范围校验与统一保存反馈均在会话内可操作。其后续短篇、长篇、质量/记忆与运行环境参数也按 PySide6 的分区顺序提供可编辑的延续面；真实受保护配置的原子提交仍留给 Phase 2 的 EngineClient。
- 模型档案新增/编辑共用一份源结构表单：供应商、模型、显示名称、遮掩 API Key、接口地址、提示与操作顺序均保持一致；常规模型管理将它嵌入工作台，受限 `settings_dialog=model-profile-add|edit` 夹具则冻结 PySide6 同尺寸的 480px 窗口。API Key 只保留在受控输入的当前会话，保存档案与浏览器持久化状态均不接收该值。
- 火候路由现在补回源端的默认模型边界：未指定主路由的任务明确继承默认模型，能力覆盖仍需在任务上显式指定主路由。SettingsPage 拥有唯一的纯本地 `ModelRoutingSessionState`，使内联路由矩阵、模型管理弹窗和折叠/重新展开共享同一份 profile、默认模型、任务草案与批量草案；组/子组“应用”会同步主路由、思考/多轮和备用链，但始终不覆盖各任务温度。该契约字段对旧只读 Engine adapter 保持可选，真实写入仍等待版本化受保护命令。
- 源端捕获器现可用同一单模型夹具冻结 idle/checking/success/failure 四态，并在连续捕获时正确恢复应用主题；命令见 [goldens/README.md](goldens/README.md)。
- 主机杼捕获器会等待源端 `is_ui_ready()`，并可注入稳定的 running job 夹具，避免将“正在准备机杼”临时态误作视觉基线。
- 火候捕获器同样等待 `SettingsPage.is_ui_ready()`；在延迟 hero/状态卡完成后才重绑
  固定 workspace snapshot，因此首屏基线包含真实运行参数而非“加载中”或空指标卡。
  1440×900 对照还收紧了首屏卡片间距，并保持待检测卡的中性边框与琥珀状态点分工；
  这提高可见主信息密度，但不复制旧端在极窄有效宽度下的右侧裁切。
- 新增 Tauri/macOS 原生内容帧捕获器：它以受限的 `__nimo_ui_parity=1`
  夹具查询启动临时应用，在仅该进程中去除窗口装饰并形成精确 client viewport，
  等待 WebView 首次渲染与两帧稳定后记录归一化参数与 SHA-256；不复用 Chromium
  截图，也不触及项目文件或 Provider。运行方法见
  [goldens/README.md](goldens/README.md)。
- 原生捕获状态不再在页面加载后通过 URL 替换注入，而是由 Tauri document-start
  脚本将经过约束的 query seed 写到 WebView；React 先读取该 seed，再读取 UI session，
  因而不会被上次保存的页面（例如章台）抢占 fixture。浏览器 URL fixture 保持优先，
  单测覆盖两条路径。当前主机的 Screen Recording 仍拒绝实际 native capture，故这项
  是验收链路修复而非已经取得的跨应用视觉结论。
- 原生捕获器先锁定它刚启动的 Tauri 进程 PID，再在该进程的同名窗口中选择面积最大的
  内容窗口；这避免用户同时运行 NIMO 时把另一会话的全屏/工具窗口误当成目标。它也支持
  decorated/plain-startup 诊断模式，但这些模式不能作为 1440×900 对等验收。
- `src-tauri/build.rs` 递归追踪 Vite `dist/` 资产，确保每一次原生构建都嵌入新哈希
  bundle；此前导致白色 WebView 的陈旧嵌入资产路径已由此消除。
- 原生捕获器还会在启动前拒绝早于 `dist/` 最新资产的 Tauri 二进制，并把二进制与
  最新 bundle 的纳秒时间戳写入 manifest。它将“旧嵌入 bundle 导致局部白屏”从肉眼
  排查提升为确定性的验收前置条件；标准构建顺序固定为 `pnpm ui:build` 后在
  `src-tauri/` 执行 `cargo build`，避免将旧 app 误当作当前 React 结果。
- 根组合层现在依赖可注入的 `EngineClient`，默认才使用 `MockEngineClient`；后续
  本地遗留 bridge 或云端 adapter 可以替换 transport，而不让页面形成第二条数据
  通路。
- 本轮范围审计移除了一个过早接入、且与现有 FastAPI 路由不兼容的本地遗留 adapter，
  因而常规启动再次严格使用 `MockEngineClient`。Phase 1 不连接 Python、不请求本机
  HTTP 服务；真实 transport 必须在 Phase 2 先定义版本化 DTO 映射、取消/恢复语义与
  脱敏边界后另行引入。
- 2026-07-18 对 `main` 的 `0c83eeaf` 再核实：`/api/v1/engine` 已有 capabilities、
  jobs、novel studio、voice studio 与 task-stream polling 的只读基础，但并不覆盖
  `EngineClient` 所需的 workspace/settings/workflow/reader/narrative-tools/stream
  subscription，且能力明确声明 commands/subscription 未实现。因此 Phase 1 继续
  严格使用 Mock；不恢复半成品 HTTP bridge。完整的端点映射与接入门槛见
  [engine-adoption-readiness.md](engine-adoption-readiness.md)。
- 卷帙 selected-reader capture 固定为“基础设定 → 故事规格”，消除了桌面 UI
  session 恢复上次标签造成的源图漂移；连续两次同夹具 capture 的 SHA-256 一致。
  项目选择器、读取器标题、外层/内层标签和内容 pane 已按源端 QWidget/QTabBar
  几何对齐，完整源端标签名保留给可访问名称并按源端字形尺度呈现省略。
- 章台现有一个独立的 `prepared` PySide6 source fixture：它在既有 workspace 夹具
  上绑定只读 `ChapterWorkspaceSnapshot`，冻结章节轨道、当前章方案、承接记忆与
  建议，而不触发章节读取、Provider 或文件写入。React 的同名 mock state 已对齐
  可见的章节标题、状态与方案文案；该 fixture 的完整命令及 SHA-256 manifest 见
  [goldens/README.md](goldens/README.md)。
- 章台“版本对比”现在复刻了完整的只读链路：当前第 5 章会先显示源端同义的
  “版本不足”边界；第 4 章打开 480×531 的两版选择器，同选版本保持对话框不变，
  成功后以独立产物页签展示 66.7% 相似度、+1/−1 统计与行内删除/新增高亮。
  两张 PySide6 基线都由相同的两版文本 fixture 采集；React 结果 pane 刻意随工作台
  宽度响应，而不把 PySide6 的固定 760px standalone capture 当成页面宽度约束。
  该流程仍只更新前端会话，真实草稿版本读取须等待完整只读 Engine DTO。
- 章台“导出设置”与“全书一致性审计”已从伪造的执行态收束到 PySide6 的真实对话框
  边界：导出以 520×712 source baseline 冻结格式、范围、目录意向和书名，再关闭并提交
  一份只存在于前端会话的 typed request；全书审计以 640×533 simple-mode baseline 冻结
  审计策略、范围、参数可见性和固定底栏，自定义范围不足两章时保留源端“章节不足”警告
  且不关闭原窗。高级审计参数会在提交边界按源端 QSpinBox/QDoubleSpinBox 范围归一化，
  不会让浏览器输入的无效数值泄漏给未来命令。两者都没有文件写入、模型调用、任务进度或
  完成态伪造；这些只能在 Phase 2 的显式命令与流式作业契约准备好后接入。
- 章台“清理失效章节”也已按同一原则回到源端的 480×394 配置窗：在对话框内只允许
  调整起始章，确认后立即关闭；不会伪造删除、Canon 回滚或后台进度。typed local request
  会把浏览器数值收束到源端 QSpinBox 的 1–最大章节范围，保持未来写命令的安全边界。
  它同样已有 PySide6 基线、浏览器快照和仅 `prepared` 状态下可达的原生 capture fixture。
- 章台“连跑策略”现在也以 480×353 的 source baseline 收紧：归档章节数量、两条处理
  分支、可独立切换的“删除旧正文”选项以及三枚固定尺寸操作按钮都来自同一 typed local
  request；选择后立即关闭，绝不伪造真正的删章、重跑或后台任务。它的初始选择态与两条
  分支均已由浏览器测试覆盖，并有受限 `prepared` capture fixture 等待 Tauri 逐像素复核。
- 章台“切换项目”已补齐固定运行态的 480×287 source baseline：它只在第 5 章正在生成时
  允许打开，保留源端的任务状态说明、后台继续提示与等宽确认/取消按钮。确认只记录当前
  前端会话的项目切换意向，不会中断任务、写入检查点或假装已改变项目；受限 native fixture
  也强制要求 `running` 状态，避免把此覆盖层错误投影到准备态。
- 章台“错误日志”不再只复用机杼的视觉外壳：它以同一条固定因果验证条目冻结 760×520
  PySide6 基线，保留审计文本、按钮状态和“标记已修复”后重新打开仍为已处理的语义。该状态
  仅保存于前端会话，不会删改运行日志或任务；受限 `prepared` fixture 等待 Tauri 逐像素复核。
- 章台项目选择、写作模式、裁决模式和工具操作已收束为源端同样的紧凑控制带，而非
  三张上下堆叠的卡片。`整章/场景级`、`全手动/AI 建议/本章自动/章节连跑` 均有
  独立可测试的会话状态；控制带缩短后，1440×900 首屏为章节轨道、方案与记忆
  多释放约 70px 的可视高度。
- 章台“记忆上下文”已由三个纵向摘要卡改为源端 `UnifiedMemoryPanel` 同构的
  七页签 inspector：概览、母题、关系、问题、追读力、护栏、控制。页签和卡片
  均从 `ChapterStudioView.memoryTabs` 的只读展示契约获取数据；默认概览保留
  待决策节点、承接要点、后续提示和评分，切换使用源端同样的 200ms 淡入并尊重
  系统“减少动效”设置。这样首屏能同时展示主工作区与记忆边界，而不让摘要卡
  挤占纵向空间。
- 章台 source capture 现覆盖 `prepared`、`running`、`checkpoint` 三态。运行态以
  固定的第 5 章准备任务冻结“正在执行 / 草稿生成 / 取消任务”呈现；决策态以固定
  的计划检查点冻结“AI 建议浮窗已打开”的主窗入口。浮窗本体仍由独立 dialog capture
  采集，避免将顶层 Qt Tool 是否被 `QWidget.render()` 包含误判为主窗布局差异。
- 章台 React 侧已将 `idle / running / checkpoint` 收束到
  `ChapterStudioActivityView`；捕获 URL 仅在组合层把固定 PySide6 三态投影到
  这个只读视图，页面不再以多个互相耦合的布尔变量拼装任务状态。当前章的上一章
  实际结果、本章目标与下一章预埋也从同一契约提供，取代了占位“章节罗盘”。
- “方案需确认”遵循源端的非阻塞 Tool 语义：主窗只显示定位入口，确认窗按需打开，
  可拖动标题栏，并复用共享 `OverlaySurface + usePanelDrag` 的 Escape、焦点恢复和
  指针拖动实现。没有新增第二套弹窗框架。
- Tauri/macOS 捕获器现在接受 `--chapter-studio-state prepared|running|checkpoint`，
  因而可为这三个同名读取状态生成独立原生帧；跨应用像素验收仍未声明完成。
- 同一捕获器还接受受限的 `--chapter-dialog checkpoint`：它只能与 checkpoint 主窗一同
  初始化“方案需确认”非阻塞浮窗。浏览器回归已将该整页状态冻结；原生帧及源端同尺寸
  组件对照仍作为 P1 几何验收门槛，不能以主窗截图代替。
- “流式详情”也已从工作流普通点击路径中抽出受限 `workflow_dialog=floating-stream`
  fixture：冻结的 `TaskStreamView` 同时携带按序事件与不可变的任务/模型/Token 摘要，
  而不是由组件从文案或计时器猜测。它复用任务观察与浮窗渲染器，保留思考折叠、调用/
  原始事件页签、Escape 与拖动；非模态 Tool 不再在打开时夺走工作台焦点。PySide6 与
  React 的 560×540 组件图已并排复核。其外壳现复用 PySide6 `Surface("elevated")`
  的 `bg.surface → bg.surface.elevated` 纸面渐变，并把原端 `border.warm` 纳入五主题
  token 投影；浏览器视觉测试也直接断言该渐变。严格像素 diff 仍拒绝（`0.6219`
  changed、`0.0385` mean delta），因此不宣称 P1 视觉验收完成。
- 机杼的“错误日志”与“确认停止任务”现同样拥有受限的
  `workflow_dialog=error-log|cancel` 直达夹具；夹具从同一 `WorkflowView` 冻结状态，
  不依赖点击或计时器。错误日志复刻源端的编号、待确认/自动恢复/标记修复审计状态；
  停止确认复刻源端 420×288 的窗口、断点恢复说明与右侧按钮组。它们共享
  `OverlaySurface` 的 Escape、焦点恢复和 Tab 限制，初始焦点落在对话框容器而非按钮，
  从而避免浏览器专有的蓝色默认按钮描边。源端并排图已复核；严格 diff 仍拒绝（错误日志
  `0.6591` changed / `0.0490` mean，停止确认 `0.1532` changed / `0.0288` mean），
  因此仍未声称跨应用验收。
- 源端 manifest 现同时记录请求的 `viewport` 与实际 `frame`；原生捕获器则按它刚
  启动的 Tauri 进程 PID 选择窗口，而不再按同名窗口面积猜测。这样在用户同时运行
  NIMO 时，capture 不会误将另一窗口的页面状态写成目标帧。2026-07-16 已以章台
  `running` 原生帧验证该隔离，manifest 含目标 `ownerPid`。
- 根据用户“主信息优先、整体更精致”的明确取舍，React 的共享 chrome 采用紧凑
  密度：顶部栏、导航与外层画布缩小约 20–30%，按钮主规格为 12px/32px；通用弹窗标题区
  固定为 19px 标题、12px 说明与 21px 内边距，减少标题 chrome 对正文视口的占用；章节正文、
  任务卡和文档渲染保持原有可读尺寸。页面过渡只使用低干扰淡入，弹窗维持 viewport
  固定定位，所有动效都尊重系统“减少动效”设置。此为记录在 `parity-decisions.md` 的有意视觉
  偏离，不将其误报为分辨率或截图误差。
- 卷帙报告和机杼阶段产物现在复用同一个富文档边界：Markdown 使用适合中文长文的
  78ch 阅读宽度、1.9 倍行高、分级标题、引用、表格、代码、强调与修订标记；JSON 同时提供
  结构视图和原始视图，并完整展示字段名、类型化值与集合计数。嵌套集合只在用户展开后挂载，
  避免大型本地产物或未来云端响应一次性撑大 DOM。两处入口的字段可见性、模式切换以及
  1440×900 / 900×600 视口边界均已通过浏览器检查。
- 卷帙的原始文档查看器进一步补齐 PySide6 `RichDocumentViewer` 的完整外壳：复制全文、
  富视图/原始 JSON 往返、文件大小、修改时间和顶层字段数都由同一个共享组件管理。
  `RenderDocumentView` 将展示 URI 与可操作的绝对 `localPath` 分离；虚拟/云端文档保持
  “在文件夹中显示”禁用，真实路径则由 Tauri 先 canonicalize 再交给 Finder、Explorer 或
  `xdg-open`，不允许 WebView 把任意显示文本直接当成本地命令参数。
- 长篇初始化已统一使用 `LongInitFormPanel` 与 `WorkflowAiAssistant`：字段编辑、AI 创意
  生成/润色、变更预览与创作札记均通过 Engine 的预设、草稿和历史命令读写；不再保留
  `workflow_dialog=preset-*` 的本地模拟夹具或第二份预设状态。
- 卷帙在项目切换前已补齐源端同顺序的未保存修改防线：终稿砚修、大纲润色预览、角色编辑
  都通过一个 transport-neutral 的 `ReaderUnsavedSession` 注册表上报自身的保存/放弃逻辑，
  父级只负责按源端顺序串行请求确认。通用 `SourceMessageDialog` 以 PySide6 440×147 基线
  收紧尺寸、紧凑圆角和等宽三操作按钮，且每个场景保留自己的“继续修订 / 继续查看 / 继续编辑”
  标签。角色工作台的回调亦已稳定化，避免编辑态和守卫状态互相触发渲染循环；所有保存仍仅在
  Mock 前端会话内生效。源端原生 question 图标不以手写资产伪造，等待 Tauri 同状态 capture
  做组件级比对。
- 卷帙的终稿砚修、章节大纲润色和角色档案现复刻 PySide6 `QTabWidget` 的会话保留语义：
  首次进入后会以隐藏而非卸载的方式留在当前项目的 reader 容器中，外层标签与内层标签各自
  保持最后选择。该边界只覆盖三个有未保存会话的工作台，普通文档仍按需渲染，因此不会为了
  复刻状态而把整套卷帙内容常驻。三条跨标签路径已纳入 Chromium 回归，并继续沿用共享的
  紧凑字体、分级圆角与主题令牌。

## Verification delivered

```bash
pnpm ui:test    # UI state/session/component pure-function tests
pnpm ui:check   # TypeScript
pnpm ui:build   # production Vite bundle
pnpm ui:visual  # Chromium interaction/visual checks
```

The visual suite covers all five themes. Its 25 cross-theme frames cover the
dashboard, chapter studio, running workflow, cancel confirmation, and voice
File-ID validation; the remaining cases cover dashboard, empty/selected 卷帙,
the primary workflow/chapter/voice states, the relationship graph, narrative timeline, expanded 创作火候 state, and the
source-shaped 1240×720 workflow-artifact reader and chapter-checkpoint dialogs, and two source-sized model-profile dialogs. Shared overlays are mounted at the document boundary so fixed dialog geometry remains viewport-relative even when the originating page is scrolled or participating in a transform animation. It also freezes source-shaped chapter running and checkpoint-main-window
frames, and asserts compact writing/decision modes, memory-inspector tabs, activity presentation and floating-checkpoint
drag behaviour. It also exercises the source-shaped subplot session plus the
humanize library's local create, duplicate, edit, import-preview and delete
paths, and proves the document-start native fixture beats a stale stored
desktop route. The shared document checks additionally lock JSON field labels,
lazy nested collections, source toolbar/status metadata, copy feedback,
structure/raw switching and compact artifact-dialog
containment. Test output directories are ignored; only intentional
snapshots are versioned.

The native capture runner has been built and verified to start the scoped
Tauri fixture, but this host currently denies macOS Screen Recording to the
capture process. Consequently no new native frame or cross-app verdict is
claimed here. Once permission is available, capture the exact source fixture,
normalize it to sRGB, then separate font rasterization drift from actual
geometry and component-state differences surface by surface; do not relax the
0.75% / 0.0075 thresholds.

`run_tauri_parity_comparison.py` now treats both a strict diff failure and a
missing PySide6/Tauri pair as a non-zero quality-gate result. A generated HTML
report is evidence for triage, never permission to treat missing captures as
accepted parity.

## Explicitly deferred (Phase 2 boundary)

- FastAPI/Tauri engine-client implementation and authenticated routing.
- Reads/writes to actual project files, model profiles, providers, exports or
  credentials.
- Binary/PDF/media document adapters beyond the implemented safe
  Markdown/JSON/plain-text renderer.
- Cross-app visual acceptance: PySide6 captures and Tauri/macOS captures at
  the same fixtures, states, themes and viewport, followed by a pixel diff.

Refer to `verification-matrix.md` for the exact acceptance status of each
surface. Chromium regression green is not equivalent to PySide6 visual sign-off.
