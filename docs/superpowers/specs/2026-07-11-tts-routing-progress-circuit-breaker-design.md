# TTS 路由、进度与网关熔断韧性设计

**日期：** 2026-07-11
**状态：** 已审阅并修订，待编写实施计划
**关联事件：**

- `data/入梦破局/logs/20260711-101701_desktop-resolve-chapter-checkpoint_842e17e8`：第 2 章 `evaluate` 全路由失败。
- 第 1 章 Voice Studio 合成失败（截图中的 `SynthesisStatus` 闭包异常）：独立于上述章节运行，纳入本设计的合成收尾修复。

---

## 1. 已核验的事实与边界

### 1.1 第 2 章网关故障时间线

以下是日志中的本地时间（`python.log`，UTC+02:00；`events.jsonl` 记录为 UTC）：

| 时间 | 已验证事件 |
|---|---|
| 12:34:40 | `check_alignment`、`check_continuity`、`check_chapter` 同时以 MiniMax 为 primary 启动。 |
| 12:34:50 | 仅 `check_continuity` 与 `check_chapter` 出现约 10 秒的 `APITimeoutError`；二者开始尝试 fallback。 |
| 12:35:10 | `check_alignment` 在 MiniMax **成功**完成，不是超时。 |
| 12:35:29 | `check_continuity`、`check_chapter` 的全部 fallback 均超时，两个检查失败。 |
| 12:35:39–12:36:02 | `repair_continuity` 对 MiniMax 的同一逻辑请求连续完成三次底层重试并都连接超时；provider 熔断器因此打开。 |
| 12:36:13–12:36:35 | 其他 provider / model 的重试也依次触发 provider 熔断。 |
| 12:38:07–12:38:37 | `evaluate` 的各 route 先探测或被熔断器拒绝，随后全路由失败。 |

结论：根本外部现象是多个 provider 的连接/读取超时。日志不足以把它确定为“本机 DNS 问题”；可能是本机网络、代理、公共出口或多个 provider 的短时不可达，必须以网络诊断另行确认。

### 1.2 对原根因结论的修正

1. **不是“三个并行检查同时打爆 MiniMax”。** `check_alignment` 成功；MiniMax 的阈值被后续 `repair_continuity` 的单个逻辑请求内三次底层重试消耗。这是当前 provider breaker 按“尝试次数”计数的行为。
2. **provider breaker 会放大故障，但不是超时的来源。** 它按 provider 共享，且 `CircuitBreaker.record_failure()` 在每次底层失败时调用。故障发生后，后续任务会更快失败；它不能解释为什么所有 fallback 首先超时。
3. **`TaskTypeCircuitBreaker` 不能单独解决本事件。** 它在 `LLMService` 入口检查，并只在一个任务的终态失败后记录；provider breaker 更靠近 adapter，已能先拒绝请求。因此仅接线任务级 breaker，`evaluate` 仍可能被共享的 provider breaker 拒绝。
4. **`rate_limit=True` 是分类错误。** `router.py` 将任意 transient `ModelGatewayError` 当作限流。末路由的连接超时包装为 transient 后，最终错误被误报为限流；这不是 429 / quota 的证据。

### 1.3 TTS 与上述事件的关系

当前 `model_profiles.json` 的 `default_profile_id` 为 `volcengine_ark:deepseek-v4-flash`，两个 TTS LLM task 都没有独立路由。这是未来并发/配额风险，值得隔离。

但它**不是这次第 2 章故障的加重因素**：`voice_team.json` 的修改时间为 12:40:23，`chapter_001_script.json` 为 12:58:50，均晚于 12:34–12:38 的网关故障。因此本设计将 TTS 路由作为预防性改进，而非本次故障的因果归因。

### 1.4 截图所示合成失败是独立、确定的代码缺陷

第 1 章合成状态文件显示：151 个语音片段中 78 个已成功落盘、73 个未完成，且未生成 `chapter_full.mp3` 或最终结果 JSON。执行在 `await synth_step.run(...)` 之后、音频装配之前中断。

`execute_synthesize_chapter()` 已在模块级导入 `SynthesisStatus`，但在函数稍后的进度收尾块再次执行：

```python
from novel_forge.tts.schemas import SynthesisStatus, TTSProgressState
```

该函数内 import 使 `SynthesisStatus` 成为函数局部闭包变量；前面的集合推导式读取它时尚未绑定，因此抛出截图中的：

```text
cannot access free variable 'SynthesisStatus' where it is not associated ...
```

此外，当前检查点只保存成功片段，失败片段的 `error_message` 没有在异常前持久化，导致无法仅依赖项目产物诊断这 73 个 provider 失败原因。

---

## 2. 设计目标

1. **正确的网关故障语义：** 一个逻辑请求的内部重试不能独占 provider 熔断预算；日志能区分 timeout、rate limit、认证和熔断拒绝。
2. **受控的任务级隔离：** 任务级 breaker 用于反复终态失败的同一 TaskType，不冒充 provider 故障修复，也不在未审计的关键阶段直接跳过工作。
3. **TTS 路由逻辑隔离：** TTS 两个 LLM task 可独立选择 primary / fallback，并在火候页保存时不被误删。
4. **真实可见的 TTS 进度：** JSON 观察流与明确的阶段事件进入 Voice Studio；音频合成仍以片段状态为主。
5. **可恢复的 TTS 合成：** 修复 `SynthesisStatus` 作用域错误，持久化每次合成的完整结果与失败摘要，并把部分完成明确展示为“可续跑”而非成功。

---

## 3. 方案一：网关熔断计数与错误分类

### 3.1 分层职责

```text
LLMService
  └─ TaskTypeCircuitBreaker（可选：同一 TaskType 的重复终态失败）
       └─ ModelRouter
            └─ provider CircuitBreaker（provider / route 的可用性保护）
                 └─ 单个逻辑 route 的底层 retry loop
                      └─ adapter / network call
```

- **provider breaker** 仍是 provider 健康保护，不承诺在 provider 或网络不可达时让 `evaluate` 成功。
- **task breaker** 只防止一个已有稳定失败模式的 TaskType 重复消耗资源；它不能覆盖 provider breaker，也不能替代网络恢复。
- 在真实的多 provider 连接故障中，正确结果是返回带分类与可恢复建议的失败，而不是靠 task breaker 伪造一次成功。

### 3.2 Provider breaker：按逻辑 route 计数

修改 `ModelRouter` 的普通与流式路径，使每个 provider/route 的**完整 retry loop**最多调用一次 `record_failure()`：

- 中间 timeout / retry 仅记录 attempt telemetry，不改变 breaker 计数。
- route 所有重试耗尽后，才根据最终错误类别决定是否记录一次 provider failure。
- 成功仍调用 `record_success()`；一个成功只重置同一 provider breaker。
- HALF_OPEN 必须只允许一个 probe in flight；其余并发调用收到明确的 breaker-rejected 事件，避免恢复时瞬间涌入多个探测请求。

错误计数策略必须集中为一个可测试的 predicate，而不是散落在 `except` 分支中：

| 终态类别 | provider breaker |
|---|---|
| 连接/读取 timeout、连接失败、5xx | 每个逻辑 route 计一次失败 |
| 429 / quota | 计一次失败，并标记 `rate_limit` |
| 401/403、请求校验、内容过滤、格式/业务校验错误 | 不计 provider 健康失败 |
| breaker 已开启的拒绝 | 不重复计数 |

同时新增 `circuit_transition` 与 `circuit_rejected` 事件，至少包含 `provider`、`model`、`task`、`old_state`、`new_state`、`failure_category`、`logical_attempt_count` 和 `retry_after_s`。

### 3.3 rate-limit 分类

不要用 `is_transient` 推断限流。收集每条尝试的结构化错误类别与 HTTP status：

- 仅出现 `429`、明确 quota 信号或 `RateLimitError` 时，聚合结果才是 `is_rate_limit=True`。
- 纯 timeout / connect failure 为 `is_rate_limit=False`，recovery hint 应指向连接/网络诊断。
- 混合错误要保留 `failure_categories` 与每条 route 的最终类别，避免只用最后一个包装异常掩盖原因。

### 3.4 TaskTypeCircuitBreaker：保留但受控接线

现有 `TaskTypeCircuitBreaker` 可接入，但它是附加防线，不能作为本事件的唯一修复：

1. 在 `Settings` 增加 `task_circuit_breaker_enabled`、`task_circuit_breaker_threshold`、`task_circuit_breaker_recovery_s`；首次上线默认 **False**。
2. `ModelRouterBuilder` 在 profiles、legacy 与 mock 三条构建路径一致地构建/传递同一实例；`ModelRouter` 暴露只读属性。
3. `LLMService` 显式传入优先，否则从 router 取得该实例，避免修改所有构造点。
4. 在开启默认值前，审计并测试所有可能抛出 `TaskCircuitOpenError` 的长篇调用者。当前只有 init repair 路径有专门捕获；不能假设所有 DRAFT、校验与收尾阶段都能安全降级。
5. 首轮可仅在具备明确“保留当前结果/跳过”策略的非关键 repair task 上启用；完成调用者矩阵与回退策略后，才考虑默认全局开启。

### 3.5 测试

- 一个逻辑 route 的三次底层 timeout 重试只增加 **一次** provider failure。
- 三个独立、终态且可计数的 logical route failures 才开启 provider breaker。
- HALF_OPEN 同时到达两个请求时只允许一个 probe。
- `check_continuity` 的 task breaker 打开后，`evaluate` 不会因 **task breaker** 被拒绝；该断言在 provider breaker 关闭且网络可用的条件下验证。
- 纯 timeout、429/quota、认证错误、混合失败分别产生正确的 `is_rate_limit`、类别与 recovery hint。

---

## 4. 方案二：TTS 路由独立面板

### 4.1 范围与存储

Voice Studio 为以下 LLM task 提供 primary + 最多三个 ordered fallback：

| 任务 | 配置 key |
|---|---|
| 配音脚本生成 | `tts_generate_dubbing_script` |
| 旁白音色构建 | `tts_build_narrator_profile` |

配置仍写入同一份 `model_profiles.json`，因此是**逻辑隔离**而非物理隔离。读取/写入使用 `ProfilesConfig.load_or_import_profiles()`、`TaskRouteEntry` 与 `ProfilesConfig.save()`，不得直接拼接原始 JSON。

### 4.2 必要改动

1. 在 `DEFAULT_TASK_TIERS` 为上述 task 添加 `ModelTier.STANDARD`，作为未配置时的明确默认值。
2. Voice Studio 复用或抽取火候页的 route editor；只展示已配置、非 embedding 且 API key 可用的 profiles。
3. 保存时更新这两个 key 的 `routes` / `fallback_routes`，并保留其他 task 的配置；fallback 排序和去重遵循现有最多三条约束。
4. 火候页当前 `_VALID_ROUTE_KEYS` 只从可见 `ROUTING_GROUPS` 派生。若保持 TTS 不在火候页展示，必须显式把两个 TTS key 加入“已知但隐藏”的保留集合，否则火候页下一次保存会清除 Voice Studio 的路由。
5. 保存后让下一次 TTS worker 新建 `ModelRouterBuilder` 时读取新 profile 配置；文案应是“下一次任务生效”。正在运行的 worker 不热切换。

### 4.3 测试

- 无 TTS 路由时回退到当前 default profile；有 TTS route 时解析到指定 profile。
- Voice Studio 保存后，`ModelRouterBuilder` 读取同一配置并得到 primary / ordered fallback。
- 火候页加载并保存后，两个隐藏 TTS key 仍存在。
- profile 删除或改名时，TTS route 与 fallback 依照 `ProfilesConfig` 的既有清理规则同步更新。

---

## 5. 方案三：TTS 进度反馈

### 5.1 后端事件

`GenerateDubbingScriptStep` 和 `BuildNarratorProfileStep` 都继承 `PipelineStep`，但它们的构造函数目前不接受 `on_step`。应先扩展构造函数：

```python
def __init__(..., *, settings: Settings, on_step: StepEventCallback | None = None) -> None:
    super().__init__(router, builder, settings=settings, on_step=on_step)
```

随后在 `execution_tts.py` 的两个构造点传入同步的 `on_step_progress`。不要 `await on_step(...)`：`PipelineStep._on_step_event` 和 Desktop signal sink 都是同步 callback。

当前 JSON contract 在 `long_streaming_json_observation_enabled=True`（默认）且 router 支持 `stream_route()` 时会走 JSON observation stream，并通过 `llm_stream_start/delta/end/error` 转发。开关关闭或 provider 不支持时，仍必须有确定性的阶段事件：

| 阶段 | 事件 |
|---|---|
| 脚本开始 / LLM 调用 / 解析完成 / 规则兜底 / 完成 | `tts_script_start` / `tts_script_llm_call` / `tts_script_parsed` / `tts_script_rule_fallback` / `tts_script_done` |
| 旁白开始 / 画像完成 / 音色就绪或兜底 | `tts_narrator_*` |
| 合成开始 / 片段状态 / 装配开始 / 可续跑或完成 | `tts_synthesis_*`、既有 `segment_progress`、`tts_assembly_*` |

`SynthesizeAudioStep` 与 `AssembleAudioStep` 当前没有 router/builder，不能为了回调而伪造 `PipelineStep.__init__` 参数或修改基类。它们保持既有片段 callback；如需补 step 事件，在这两个类或 execution facade 增加独立、可选的同步 emitter。

### 5.2 前端：页面级 observation store

保持 Voice Studio 不接入 `DesktopJobManager` 的范围，使用页面私有的 `TaskObservationStore`：

1. 每个 TTS worker 启动时创建一个 `DesktopJobRecord(job_id, kind, label, project_id, status=RUNNING)`。
2. `step_progress` 到达时追加 `DesktopJobEvent(at=<UTC ISO string>, step=step, payload=data)`，再调用 `store.ingest_jobs([record])`。`DesktopJobEvent` 不含 `job_id` 字段，且 `at` 必须为字符串。
3. `TaskFocusPanel` 绑定该 store；`llm_stream_*` 事件既要进入 store，也可继续更新现有状态徽章，不能在状态徽章分支提前 return 而丢弃事件。
4. “展开详情”使用一个以该页面 store 为数据源的详情窗口；全局宠物/全局 job manager 集成保留为后续工作。
5. JSON observation 只作为只读预览，明确标注“结构化生成中”；最终脚本仍以校验并落盘后的 JSON 为准。

### 5.3 测试

- 两个 LLM TTS step 的 `on_step` 能转发 observation stream 和语义阶段事件。
- JSON observation 开关关闭时仍显示开始、解析、完成或兜底状态。
- offscreen 桌面测试验证一个正确形状的 `DesktopJobRecord` + `DesktopJobEvent` 能在 `TaskFocusPanel` 中显示增量文本。
- 现有 `test_tts_synthesize_step.py` 与 Voice Studio 测试无回归。

---

## 6. 方案四：TTS 合成收尾与可恢复性

1. 删除 `execute_synthesize_chapter()` 收尾块中的局部 `SynthesisStatus` import，仅局部导入 `TTSProgressState`，或统一使用模块级 import，消除闭包遮蔽。
2. `synth_step.run()` 返回后立即持久化可诊断的 synthesis snapshot：所有 `segment_results`、成功/失败/跳过计数、失败片段的 `error_message` 摘要、provider、script/voice-team hash 和时间戳。不得等待装配完成才保存。
3. 保留现有 78 个成功片段的断点续跑；重新运行只发送缺失或 fingerprint 不匹配的片段。
4. 允许生成部分音频供排查，但最终结果 `is_complete=False` 时，UI 必须显示“已完成 X/Y，可续跑；失败片段见详情”，不能作为“合成完成”处理。
5. 若装配本身失败，写入 `last_error` 和阶段标记，并保留 segment snapshot，避免把失败原因丢失在 worker 异常文本中。

### 6.1 测试

- 以已完成片段的 mock synthesis 结果运行完整 facade，断言不再出现 `SynthesisStatus` 的 `UnboundLocalError`，且装配阶段被调用。
- 混合成功/失败的 151（或缩小的 fixture）片段保存完整 snapshot；失败 `error_message` 可被恢复界面读取。
- 重新运行只请求缺失片段；所有必需片段完成后清理 progress checkpoint。
- `is_complete=False` 的结果不会触发成功 UI 状态。

---

## 7. 实施顺序

```text
阶段 1：正确性与可诊断性（先部署）
  ├─ provider breaker 按 logical route 计数 + single HALF_OPEN probe
  ├─ 结构化 failure category / rate-limit 修复 / breaker telemetry
  └─ TTS SynthesisStatus 作用域修复 + synthesis snapshot

阶段 2：受控隔离与配置
  ├─ TaskTypeCircuitBreaker 接线（默认关闭、先限于已审计的可降级调用者）
  ├─ TTS 默认 tier、隐藏 route key 保留与 Voice Studio 路由编辑
  └─ 路由持久化和新 worker 生效验证

阶段 3：TTS 反馈体验
  ├─ 两个 LLM step 的 on_step 构造函数与语义事件
  └─ Voice Studio 页面级 TaskFocusPanel / stream detail
```

阶段 1 与阶段 2 的后端准备可并行，但 TaskTypeCircuitBreaker 不得早于 provider 计数修复被宣称为本事件的解决方案。阶段 3 依赖阶段 2 的事件形状冻结。

---

## 8. 风险、回退与不做事项

| 风险 | 缓解 |
|---|---|
| provider breaker 计数变慢，短时故障会多尝试一次 logical call | 以明确的逻辑调用阈值、route telemetry 与单 probe 控制，而非按底层 retry 误开熔断。 |
| TaskTypeCircuitBreaker 抛出路径尚未全部审计 | 初始默认关闭，先覆盖具备明确 fallback 的调用者；保留环境开关。 |
| Voice Studio route 被火候页保存误删 | 将 TTS key 纳入已知隐藏 key，并添加跨页面持久化测试。 |
| JSON stream 预览影响 UI 流畅度 | 使用现有批量 delta 机制；预览只显示有限窗口，最终数据仍取落盘结果。 |

本轮不做：

- 不把 TTS worker 迁入 `DesktopJobManager`；页面级 store 足以满足当前预览需求。
- 不把“多 provider timeout”直接归因到某一家 provider、DNS 或 TTS 配额；需独立网络证据。
- 不为音频二进制合成伪造 LLM token stream；继续使用片段级进度与合成快照。

---

## 9. 验收标准

1. 单个 logical route 的三次 timeout 重试只占用一次 provider breaker 失败预算；HALF_OPEN 只有一个 probe。
2. 纯 timeout 不再标为 `rate_limit=True`；429/quota 仍被正确标记，且最终日志保留每 route 的错误类别。
3. Task breaker 打开某个可降级 task 时，另一个 task 在 provider 健康条件下仍可发起请求；真实 provider/network 故障不被误报为“隔离已解决”。
4. Voice Studio 保存的两个 TTS route 能被新建 worker 解析，且在火候页保存后仍被保留。
5. TTS LLM 阶段能显示 JSON observation（可用时）或可靠的阶段事件（不可用时）；合成阶段显示片段完成度。
6. 本次截图中的 `SynthesisStatus` 异常可由回归测试覆盖；失败片段详情和续跑状态持久化可见。
7. 相关单元、桌面 offscreen 和目标集成测试通过，再执行全量 `pytest -n auto -q`、`ruff check`、`mypy` 与既有 prompt 校验脚本。
