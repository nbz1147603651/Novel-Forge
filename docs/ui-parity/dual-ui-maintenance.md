# NIMO 主 UI 与 PySide 后备维护契约

最后更新：2026-08-31

当前决策：NIMO React/Tauri 已成为默认桌面和唯一的新产品 UI 开发目标。PySide6
冻结为安全、兼容、回归参照和应急后备；旧 Phase 1 的“双端并行实现”要求不再用于
要求新功能先落 PySide。共享 Engine 权限、数据完整性和恢复修复仍须在受影响时保持
后备端真实可用。

## 调整后的 Phase 1 目标

Phase 1 不再以“网页端已有六个页面”为完成条件，而是建立一层可长期共存的
历史 Phase 1 建立了**视觉兼容层**：PySide6 可独立运行、React/Tauri 可独立演进，两端对同一
EngineClient 视图与同一命名夹具给出等价的界面状态。只有在同夹具、同主题、同
视口的 Tauri/macOS 像素报告通过后，某一表面才可标记为 `accepted`。

这保留了未来云端部署的路径，但不提前把云端、Provider、凭据或项目文件耦合进
新 UI。

## 当前两端的职责与差异

| 维度 | 现有 PySide6 | React/Tauri 新端 | 共存规则 |
| --- | --- | --- | --- |
| 产品行为 | 冻结的安全/兼容/应急后备；不新增产品表面 | 默认生产 UI；Mock 仅用于确定性夹具，HTTP adapter 用于真实 Engine 读写与任务观察 | 两端不得绕过 `EngineClient`；新交互只在 NIMO 实现，共享安全修复按影响同步后备端 |
| 视觉实现 | QSS、QWidget、QTabWidget、信号/槽 | 主题 token、共享 React 组件、CSS、受控状态 | 先测量源端组件矩形、字体、状态，再修改共享原语；不另造视觉体系 |
| 数据边界 | 直接使用 Python workspace 与本地存储 | `@nimo/engine-contracts` 的 typed view model | 页面不读 Python 文件、环境变量、模型路由或密钥 |
| 任务/流输出 | 后台任务、Qt 信号、桌面任务观察 | `TaskStreamEvent` 和统一的流/弹窗 renderer | 流协议只维护一份语义；展示组件不持有第二套业务状态 |
| 文档、图谱与时间线 | Python 渲染组件与读写工具 | `ContentRenderer`、`NarrativeToolsWorkbench`、`NarrativeVisualization` | 先复用现有新端组件，再增加状态；避免按页面复制同一种 renderer |
| 主题与品牌 | 五套语义主题、主题切换的 NIMO 标志 | 同名五套 token、同一源 SVG、主题感知原生图标 | token 名称和 SVG 来源不可分叉；缺 token 时补投影而非页面内写色值 |

## 已建立的维护边界

1. **单一引擎入口。** `App` 在组合根接收 `EngineClient` /
   `EngineCommandClient`。页面、任务观察和数据加载都经由该实例；Mock 与 HTTP
   只是两个 adapter。HTTP 模式启动时先协商 `/api/v1/engine/capabilities`，失败或
   版本不兼容会显式阻断，不会静默退回 Mock。完整能力与缺口见
   [engine-adoption-readiness.md](engine-adoption-readiness.md)。
2. **夹具也是契约。** PySide6 卷帙捕获显式固定到“基础设定 → 故事规格”，不再
   恢复本机上次标签位置。连续两次源端 capture 的 SHA-256 必须相同，才能成为
   React/Tauri 的比较依据。
3. **共享原语优先。** Shell、主题、按钮、标题、对话框、文档 renderer、任务流
   renderer 和叙事工具均只维护一个新端实现；页面 CSS 仅能记录可追溯的源端局部
   几何差异。
4. **不做双写。** Mock 状态只用于展示与交互回归；它不写项目文件。真实命令由
   HTTP Engine adapter 统一提交，持久化、授权、路由和任务流仍在 Python 后端。
5. **验收状态不可拔高。** Chromium 快照只防止新端回归；它不是 PySide6 对等的
   证据。`surface-index.yml` 的 `accepted` 只能由同夹具 Tauri/macOS 报告推进。

## 本轮已推进的兼容层

- 卷帙的项目选择器、阅读标题、两层标签与内容 pane 按 PySide6 实际几何映射；
  外层和内层标签宽度以 QTabBar 源矩形为依据。
- 卷帙标签保留完整的源端文本与无障碍名称，在 Chromium 中按源端有效字形尺寸
  自然省略，避免“视觉截断文本”和“业务名称”分成两份常量。
- `capture_pyside_goldens.py` 不再受本机卷帙 UI session 污染；读取器样本可重复。
- 根组合层从直接调用 `mockEngineClient` 改为依赖注入，为本地遗留 adapter 与未来
  云端 adapter 预留不改页面的替换点。
- 卷帙、机杼、火候、章台和声腔均按路由拆分；导航悬停或键盘聚焦时才预热目标
  模块，不再在案头空闲后无条件拉取所有重页面与火候数据。生产构建的首屏业务
  chunk 从约 412 kB 收敛到约 156 kB，React runtime 保持独立缓存。

## 推进顺序与退出条件

### P0：先完成可比的桌面骨架

1. 固化 app shell、案头、卷帙空/已选读取器的同状态源图与 Tauri/macOS 图；
   修复共享壳和页面局部差异，不把全局问题藏到页面 CSS。
2. 对机杼 running、火候默认/创作火候、章台 prepared/checkpoint、声腔 script
   以同样方式收敛。每次优先复用已有流、对话框、文档和叙事组件。
3. 取得 macOS 原生截图权限后，为每个 P0 状态生成像素报告。默认 1440×900
   静态页阈值为 0.75%；未达阈值不标记 `accepted`。

### P1：以组件状态矩阵收敛深层交互

1. 按 `surface-index.yml` 的顺序处理流式窗口、任务/章节对话框、模型路由、连接
   卡、文档、图谱、时间线和声腔弹窗。
2. 每个新增状态先补 PySide6 命名夹具，再给 Mock EngineClient 同名视图，最后做
   Tauri component capture；对话框阈值为 0.25%。
3. 只有既有共享组件不能表达源端状态时才扩展 view model；禁止为单个页面新建
   平行的流、文档或图渲染器。

### Engine 扩展：扩充 adapter，而非重写页面

1. `LegacyLocalEngineClient` 同时承担本地与云端 HTTP transport；差异仅来自运行时
   URL、token 和后端能力，不在页面内建立第二套 Cloud client。
2. 云端负责授权、租户隔离、策略提示词、模型分发/路由和任务协调；客户端承担编辑、
   渲染和可本地执行的轻量计算。
3. 新增命令必须有版本、授权范围、可取消任务流与回退语义，并同时验证旧端与新端；
   不能通过在 React 页面中调用 Python 或 Provider 来“快速接通”。

## 每次双端功能变更的最小流程

1. 先更新后端权威行为与 `@nimo/engine-contracts` 的 view/command（若确有新的共享语义）。
2. 用已有共享 React 组件实现；若必须新增组件，登记到
   `workbench-component-index.yml`。
3. 添加单元/交互测试、Chromium 回归和必要的原生对比；记录任何批准偏差到
   `fidelity-ledger.md`。
4. 如果变更影响安全、权限、数据完整性或恢复，再同步 PySide 后备投影及其回归；
   不为普通产品功能复制页面或状态。
5. 在 `surface-index.yml` 和 `verification-matrix.md` 更新证据，未完成原生比较时
   保持 `foundation` 或 `partial`。

## 启动与部署边界

- `nimo` 固定进入 React/Tauri；`nimo-t` 是同一入口的兼容别名；`nimo-p` 固定进入冻结的 PySide6 后备端。
- 本地默认：`nimo` 启动回环 FastAPI Engine，等待 `/health` 就绪后启动 Tauri，
  并在客户端退出时只关闭自己创建的后端进程。
- 外部/云端：`nimo --backend-url https://... --no-backend`。远端必须显式关闭
  `NOVEL_FORGE_API_LOCAL_ONLY`，配置可信 Host、精确 CORS origin 与访问 token。
- `NIMO_ENGINE_BASE_URL`、`NIMO_ENGINE_ACCESS_TOKEN` 仅通过进程环境进入 Tauri
  document-start 内存配置；不得写入前端持久化或 `VITE_*` 构建产物。
