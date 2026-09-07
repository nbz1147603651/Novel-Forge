> 历史审计记录，保留当时的观察，不代表当前交付状态。当前状态见[文档导航](../../README.md)。

# 影视模块（映界）吸收 ComfyUI 开源架构优化报告

> 分析日期：2026-08-04 · 分析范围：`Reference/ComfyUI-master`（核心引擎 + API 节点系统 + MiniMax 节点）与 `novel_forge/film/`、`clients/nimo-desktop`（映界三形态）
> 目标：把成熟开源架构的核心设计吸收进影视模块，转移维护成本，重点优化 MiniMax H3 模型的工作流与操作逻辑，前后端统一。

---

## 一、结论先行

1. **ComfyUI 是本模块最值得吸收的开源参照系**：其「API 节点系统 + 通用异步客户端 + 节点图执行」三层设计，恰好覆盖影视模块当前最薄弱的三块——多平台生成参数链、任务轮询/进度/重试、可恢复工作流执行。
2. **MiniMax H3 存在 6 项实质差距**（其中 3 项是明确的参数链缺陷）：`ratio`/`seed`/`watermark` 已声明却未传到平台；分辨率硬编码 `1080P` 导致 H3 永远降级 768P；H3 Ref2VA 的多模态参考（视频/音频，上限 3+3）完全未启用。
3. **前后端统一缺口**：前端 `FilmStudioPage` 已具备九阶段壳层，但缺少 H3 高级参数面板、参考媒体分类型管理、排队/处理分阶段进度与实时价格展示；「只重跑失败节点」目前只是文案，后端无节点图级部分重执行。
4. **推荐分四批落地**（对应你既定的分批提交习惯）：Batch A 修复 H3 参数链 → Batch B 升级 H3 v2 多模态端点 → Batch C 吸收轮询客户端模式 → Batch D 工作流节点化 + 前端统一。

---

## 二、ComfyUI 核心架构吸收点分析

### 2.1 三层架构总览

| 层 | 关键模块 | 核心思想 | 对映界的吸收价值 |
|---|---|---|---|
| 节点图执行引擎 | `execution.py`、`comfy_execution/`（graph/caching/jobs/progress） | DAG 工作流、输入签名缓存（只重执行变化节点）、Hierarchical/LRU/RAM 压力缓存、IS_CHANGED 指纹 | 把 `RunPlanNode` 从「记录型」升级为「可执行型」，实现真正的部分重跑 |
| API 节点系统 | `comfy_api_nodes/`（`nodes_*.py` + `apis/*.py` + `util/`） | 每个闭源模型 = 一个声明式节点（schema + execute）+ 一组 Pydantic 请求模型 + 通用客户端 | H3/百炼/方舟适配器可直接对齐此模式，一套通用轮询客户端三平台复用 |
| 服务与应用层 | `server.py`、`api_server/`、`app/`、`blueprints/` | WebSocket 进度协议、队列、用户/前端管理、预置工作流 JSON | 排队/处理状态细分、预置「制片模板」机制 |

### 2.2 API 节点系统（最值得整体吸收的部分）

`comfy_api_nodes` 的通用客户端模式（`util/client.py`，1029 行）是本项目 `film/providers/*.py` 的成熟替代品：

- **`sync_op` / `poll_op` 双操作原语**：提交与轮询分离；轮询支持 `status_extractor`、`progress_extractor`、`price_extractor`；
- **状态归一化**：`COMPLETED_STATUSES` / `FAILED_STATUSES` / `QUEUED_STATUSES` 三组标准状态集合（`succeeded/success/completed/finished...`），避免每家平台各写一套映射；
- **重试策略**：指数退避 + 429 独立通道（`max_retries_on_rate_limit=16`）+ `Retry-After` 头尊重（上限 150s 防阻塞）；
- **UI 进度上报**：`_PollUIState` + 每秒 ticker，区分「排队中/处理中」两段时间轴，`estimated_duration` 预测总时长——前端可直接消费；
- **中断协作**：`sleep_with_interrupt` / `is_processing_interrupted`，用户取消时同步触发平台 `cancel_endpoint`；
- **实时价格**：`X-Comfy-Credits-Used` 响应头 + `price_badge` 声明式定价表达式（H3 按 resolution/duration/参考数分档）。

### 2.3 MiniMax H3 节点（本次优化的直接参照物）

`comfy_api_nodes/nodes_minimax.py` 的 Hailuo03 系列是本项目的黄金参照：

| 维度 | ComfyUI H3 节点（参照） | 映界现状 |
|---|---|---|
| 创建端点 | `POST /v2/video_generation` | `POST /video-generation-v2-create`（v2 端点族另一入口） |
| 请求体 | `{model, content[], resolution, duration, ratio, seed, aigc_watermark}` | `{model, prompt, duration, resolution, prompt_optimizer}` |
| 多模态 content | text / image_url / video_url / audio_url + role | 仅文本 + 图片（subject_reference） |
| 纵横比 | ratio（adaptive/16:9/4:3/1:1/3:4/9:16/21:9） | 未传（`aspect_ratio` 字段存在但提交时丢弃） |
| seed | 0–4294967295，control_after_generate | `seed` 字段存在但未传 |
| 水印 | aigc_watermark | `watermark` 字段存在但未传 |
| 参考输入 | 9 图 + 3 视频 + 3 音频（Autogrow 动态端口） | 仅图片（最多 9） |
| 输入验证 | 图片 ≥256px、纵横比 0.4–2.5、参考视频 FPS 23.976–60、2–15s、总时长 ≤15s | 无任何验证 |
| 结果下载 | download_url + backup_download_url 双 URL 重试 | 仅单 URL |
| 查询端点 | `GET /v2/query/video_generation/{task_id}` | `GET /query/video_generation?task_id=` |
| 轮询 | poll_interval=15、显式 failed 状态、进度上报 | 简单定时轮询、无 UI 进度 |
| 价格 | price_badge 表达式（768P $0.1287/s、2K $0.1859/s、>5 图每张 +$0.0572、视频参考按 2–15s 区间） | 固定价表，未按 H3 分档 |

---

## 三、影视模块现状评估

### 3.1 已吸收的开源/开放理念（保持）

- **FFmpeg + OTIO** 渲染/时间线（`rendering.py`、`FilmTimeline`）：维护成本已成功转移上游——**本报告延续此原则，不引入 ComfyUI 的 PyTorch 推理内核**（ComfyUI 的本地模型执行部分与本项目云 API 模式无关，仅吸收其「API 节点 + 客户端 + 工作流」骨架）；
- **幂等任务账本**（`film/jobs.py`）：idempotency_key 防重复计费，已优于 ComfyUI 的队列模型，保留；
- **平台能力目录**（`providers/catalog.py`）：单一事实源（含 H3 的 `aspect_ratios`、`reference_limits`、`resolutions`），但 **catalog 声明与 provider 提交之间出现断裂**——这是 H3 差距的根源；
- **平台 prompt 投影**（`platform_adapter.py`）：flat / h3_timeline 双渲染器注册表，新增平台 = JSON 配置 + 一个渲染器，数据驱动方向正确；
- **分镜可行域分配**（`storyboard.py`）：LLM 只建议节奏、确定性兜底，与漫画层共享。

### 3.2 差距清单（对照 ComfyUI 最佳实践）

| # | 差距 | 现状 | ComfyUI 参照 |
|---|---|---|---|
| G1 | H3 参数链断裂 | `aspect_ratio`/`seed`/`watermark` 未进入请求体 | Hailuo03 全参数透传 |
| G2 | 分辨率硬编码 | `pipeline.generate_shot` 固定 `resolution="1080P"`，H3 仅支持 768P/2K → 永远降级 768P；2K 需 Regenerate-2K 二次生成，完全缺失 | resolution 由 UI/模型契约决定 |
| G3 | 多模态参考未启用 | catalog 声明 `reference_limits {videos:3, audios:3}`，但 `FilmReferenceMedia` 无视频/音频类型 | content[] 多模态 + Autogrow 动态端口 |
| G4 | 轮询客户端原始 | 每 provider 手写状态映射；无排队/处理区分、无中断、无取消、无预计时长 | `poll_op` 通用原语 |
| G5 | 无输入验证 | H3 参考图尺寸/纵横比、视频 FPS/时长、总预算均无校验，坏参数打到平台才报错 | `validation_utils.py` 全套校验 |
| G6 | 下载无备份 URL | `media.py` 单 URL 下载 | backup_download_url 重试 |
| G7 | 前端无 H3 参数面 | `ShotInspector` 只有六维摄影语言 + 平台/模型；duration/resolution/ratio/seed/水印不可调 | DynamicCombo + slider + control_after_generate |
| G8 | 前端无参考媒体管理 | 参考图仅由上游身份锁隐式注入，不可见不可换；视频/音频参考无入口 | Autogrow 动态输入组 |
| G9 | 「只重跑失败节点」是文案 | `RunPlanNode` 是记录型 DAG；`generate_shot` 无节点级部分重执行（虽有幂等账本） | 输入签名缓存 + 缓存失效 |
| G10 | 无排队/处理进度细分 | 前端全屏 `busyLabel`，无预计时长、无排队/处理区分 | `_PollUIState` ticker + estimated_duration |
| G11 | 无实时价格反馈 | `estimated_cost_usd` 只在账本静态展示 | price_badge + 响应头价格 |
| G12 | 无工作流模板库 | 无「预置制片流程」导入/导出（OTIO 有导出） | blueprints/ 预置 JSON |

---

## 四、MiniMax H3 专项优化方案（本报告重点）

### 4.1 修复参数链（Batch A）

目标：`catalog → FilmGenerationRequest → minimax.py 请求体 → H3 平台` 全程一致。

1. `film/providers/base.py`：`FilmGenerationRequest` 已有 `aspect_ratio`/`seed`/`watermark` 字段，无需扩 schema（`seed` 上限需从 2^31 放宽到 2^32-1 以对齐 H3 范围）；
2. `film/providers/minimax.py`：`_submit_video` 补齐 `ratio`（由 `aspect_ratio` 映射，`adaptive` 需平台默认）、`seed`、`watermark`（`aigc_watermark`）；
3. `film/pipeline.py` `generate_shot`：分辨率改为按 catalog 推导——H3 模型取 `min(2K, style 需求)`，非 H3 保持 1080P 默认；时长按模型 durations 域钳位（H3: 4–15）；
4. 成本估算 `estimate_generation_cost` 按 H3 官方分档重写（768P $0.1287/s、2K $0.1859/s、>5 参考图加价、视频参考 2–15s 区间），与 price_badge 表达式对齐。

### 4.2 升级 H3 v2 多模态端点（Batch B）

对齐 ComfyUI `Hailuo03` 系列（`/v2/video_generation` + `/v2/query/video_generation/{task_id}` + content[] 数组）：

1. `film/providers/base.py`：`FilmGenerationMode` 增加 `MULTI_MODAL_REFERENCE_VIDEO`（Ref2VA）；`FilmReferenceMedia.kind` 扩展 `reference_video`/`reference_audio`；
2. `film/providers/minimax.py`：H3 模型统一走 v2 content 数组路径（`Hailuo03TextContent/ImageContent/VideoContent/AudioContent` 结构直接照抄 `comfy_api_nodes/apis/minimax.py`，Pydantic 模型可整体移植），旧 Hailuo 模型保留 v1/v2-create 兼容分支；
3. `film/pipeline.py` `_shot_generation_contract`：identity 参考图 ≤9 之外，允许 shot 级追加参考视频（≤3、单段 2–15s、总 ≤15s）与参考音频（≤3），来源接声画阶段的 TTS 音频资产与既有分镜素材；
4. `film/providers/minimax.py` `query`：支持 v2 路径查询与 `task.content.url` 返回；`materialize` 走 backup URL 重试；
5. 输入验证模块：移植 `validate_image_aspect_ratio`（0.4–2.5）、`validate_image_dimensions`（≥256）、参考视频 FPS（23.976–60）与总时长（≤15s）校验，失败在提交前报错并落 `job.error_message`。

### 4.3 轮询客户端通用化（Batch C）

在 `film/providers/` 下新增 `_client.py`，把 ComfyUI `poll_op` 模式提炼为项目通用原语（**不引入 aiohttp 依赖，沿用 httpx**）：

- `poll_task(provider, task, *, status_extractor, completed/failed/queued 状态集合, poll_interval, max_attempts, estimated_duration, cancel_endpoint)`；
- 轮询期间通过 `on_step("film_task_progress", {stage: queued|processing, elapsed_s, estimated_total_s})` 上报——`FilmProductionPipeline.on_step` 链路已具备，补上前端消费即可；
- 中断检测：接入 `FilmJobManager.cancel` 的 job 状态（前端取消 → provider 侧停止轮询并可调用平台取消端点）；
- 三平台 provider 统一改用它，删除各家手写映射。

### 4.4 工作流节点化（Batch D-后端）

把 `RunPlanNode`（已具备 node_id/depends_on/artifact_inputs/artifact_outputs/status/human_checkpoint/retry_limit）升级为可执行节点图：

- 新增 `film/workflow.py`：轻量 DAG 执行器（拓扑序 + 就绪判断 + 单节点重试 + 部分重跑），**不引入 ComfyUI 的 torch/缓存体系**，仅吸收其图执行思想；
- 生成/落盘/质检/渲染分别注册为节点（`RunPlanNode.kind` 扩展）；失败节点与其下游自动标记 `pending`，其余保持 `completed`——前端「只重跑失败节点」从文案变成事实；
- 与 `jobs.py` 幂等账本协同：节点执行前走 `acquire`，避免双层重复提交。

### 4.5 前端统一（Batch D-前端）

1. **H3 参数面板**（`ShotInspector`）：模型为 H3 时展开「生成参数」区——时长 slider（4–15s）、分辨率（768P/2K）、纵横比（adaptive/6 档，默认跟随 `FilmStyleLock.aspect_ratio`）、seed（可复现）、水印开关；参数随 `updateFilmShot` patch 持久化；
2. **参考媒体管理**：镜头检查器新增「参考输入」区，分类型展示/替换身份参考图、追加参考视频/音频（复用声画阶段落盘的音频资产），调用新增 `setFilmShotReferences` 命令；
3. **进度细分**：`busyLabel` 保留，另加任务状态行（排队中/处理中 + 已用时长 + 预计总时长 + 实时价格），消费 `film_task_progress` 事件；取消按钮不再依赖轮询超时；
4. **工作流视图**：`ProductionOverview` 的 runPlan 文本列表升级为可交互节点图（依赖连线、红/绿状态、点击单节点「重跑」）；
5. **预置制片模板**：仿 blueprints，在 `data/<project>/film/templates/` 提供可导入的「九阶段制片模板 JSON」（含默认路由、风格锁、渲染规格），命令栏增加「模板库」。

---

## 五、集成策略与边界（吸收 vs 不吸收）

| 吸收（转移维护成本） | 不吸收（明确边界） |
|---|---|
| API 节点声明模式（schema + execute + Pydantic 请求模型） | PyTorch 本地推理内核（`comfy/`、`nodes.py` 模型实现） |
| `poll_op` 客户端模式（重试/进度/中断/价格） | aiohttp 依赖（沿用 httpx） |
| H3 v2 content 数组与全套输入验证 | WebSocket 前端协议（本项目走 engine-contracts + FastAPI） |
| blueprints 预置模板思想 | 自定义节点管理器（`custom_nodes/` 生态） |
| 状态归一化常量集 | 本地模型加载/量化/显存管理 |
| 缓存中间件思想（静态资源缓存） | 队列持久化模型（已有幂等账本，更强） |

---

## 六、落地批次与验收（按批次提交）

> **实施状态（2026-08-08）：A–D 四批已全部落地并分 4 个提交合入 main**：
> `66089960`（A+B H3 专项）、`86899b4c`（C 轮询客户端）、`21eb269f`（D-后端 运行图 DAG）、`c3ab45bb`（D-前端 统一）。
> 验证：后端 film 142 项测试 + mypy 基线 + ruff 全绿；前端 tsc + vitest 460 项全绿。
>
> **收尾审计（2026-08-08，提交 `888d9560`）**：
> - **发现并修复**：`sync_run_plan` 原无调用点，运行图节点不会自动物化，前端节点图为空、「只重跑失败节点」不可用 → 已在 `get_or_bootstrap` 两条返回路径与 `poll_shot_task` 终态接入，并新增测试验证加载即物化、持久化。
> - **澄清非缺陷**：前端 patch 的 camelCase 键由 client 层自动转 snake_case（`mapKeysToSnakeCase`），与后端 `update_shot` 白名单对齐正常。
> - **预存失败确认**：全量单元有 31 项预存失败（topbar/statusbar/chapter_runner/tts 等），均不依赖 film 域、本次未触碰任何共享模块，已在改动前基线（HEAD~5）复现一致，与本改动无关。

| 批次 | 内容 | 涉及文件（主要） | 验收 |
|---|---|---|---|
| **A：H3 参数链修复** ✅ | ratio/seed/watermark 透传；分辨率/时长按模型域推导；H3 分档成本 | `film/providers/minimax.py`、`film/pipeline.py`、`film/platform_adapter.py` | 单元测试断言 H3 请求体含全参数；H3 不再被钳到 768P 硬编码路径 |
| **B：H3 v2 多模态端点** ✅ | content[] 请求；参考视频/音频流；输入验证；backup URL | `film/providers/base.py`、`minimax.py`、`pipeline.py`、`schemas.py` | 新测试覆盖 9图+3视频+3音频构造；非法输入提交前报错 |
| **C：轮询客户端通用化** ✅ | `poll_task` 原语；状态归一化；进度事件；取消协作 | 新增 `film/providers/_client.py`，三 provider 改造 | 三平台轮询共用一套原语；`film_task_progress` 事件被路由 |
| **D：工作流节点化 + 前端统一** ✅ | 可执行 DAG；部分重跑；前端 H3 参数面板/参考管理/进度/节点图 | 新增 `film/workflow.py`、`api/routes/film.py`、`engine-contracts`、`FilmStudioPage.tsx` | 单节点重跑只影响失败子图；前端全部控件接通 |

---

## 七、风险与取舍

1. **端点兼容**：`/video-generation-v2-create`（HuggingFace 模型卡 v2 端点族）与 `/v2/video_generation`（平台 Open Platform v2）并存，Batch B 采用「H3 走 v2 content 路径、旧模型走旧路径」双轨，先用配置开关灰度，观察实际计费与状态语义；
2. **参考视频/音频的上游供给**：参考音频可直接接声画阶段 TTS 产物；参考视频初期无稳定来源，先保留字段与验证，UI 视供给情况开放；
3. **节点图复杂度**：D 批次的 DAG 执行器保持「轻量 + 与幂等账本协同」，不引入 ComfyUI 的缓存键体系，避免过度设计；
4. **前端范围**：D 批次前端改动集中在 `FilmStudioPage.tsx` + engine-contracts，保持双端契约先行（先 Python API 测试，再 nimo 类型检查 + vitest）。
