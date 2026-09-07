# 配音模块架构与扩展契约

## 1. 端到端闭环

配音主链只允许通过 `workspace/execution_tts.py` 进入：

```text
Desktop / API / post-archive hook
  → 旁白画像
  → 配音团队（角色 → provider-scoped voice_id）
  → 配音脚本（绑定终稿 source_text_hash）
  → 分段合成（请求指纹 + 章节级断点）
  → ASR / 强制对齐
  → 对齐失败片段定向重合成
  → BGM / SFX / soundscape 解析或生成
  → MixPlan + 多轨装配 + 字幕
  → 成片质量门
  → ChapterAudioResult + delivery_ready
```

核心产物分为三层：

- 项目级契约：`tts/voice_team.json`、`tts/narrator_profile.json`；
- 章节级可编辑产物：配音脚本、take manifest、声音 cue 解析；
- 章节级派生产物：片段音频、对齐时间轴、MixPlan、成片、质量报告和断点。

终稿、脚本、配音团队任一来源变化，都必须通过哈希或 staleness 清理使下游失效，不能静默复用旧音频。

## 2. 完成状态语义

`ChapterAudioResult.is_complete` 只表示所有必须发声的片段均已合成并完成装配。

`ChapterAudioResult.delivery_ready` 才表示可以最终交付，同时要求：

- 人声片段完整；
- 已生成成片文件；
- 成片质量报告通过；
- 剧情声音 cue 均解析为已批准资产。

`delivery_blocking_reasons` 是稳定的机器可读原因。UI、API 或导出流程不应自行重新推导交付条件。

所有外部消费必须经过 `tts/delivery.py::require_delivery_ready()`：

- Desktop 的 MP3、SRT 和全书 ZIP 导出；
- API `GET /tts/audio/{project}/{chapter}`（诊断工具可显式传 `include_incomplete=true`）；
- 章节归档后的自动配音完成状态；
- 持久化合成任务的最终成功状态。

播放器和配音室试听属于诊断/制作流程，可以播放未交付成片，但不得把它导出或标记为完成。

## 3. 断点恢复不变量

- 断点按章节保存到 `states/tts_progress/chapter_NNN.json`，章节之间不得覆盖；
- 旧版 `states/tts_progress.json` 只作为迁移读取源；
- 只有脚本哈希、配音团队哈希、provider 和片段请求指纹全部一致时才能复用；
- 每个片段音频原子落盘后，立即保存完整 `SynthesisResult`；
- 恢复时必须保留时长、费用、模型、音色、质量警告和 provider trace，不能只恢复文件路径；
- 音频缺失、为空或指纹变化时必须重新合成；
- 对白或内心独白缺少 ready cast 时必须显式失败，禁止降级成旁白音色。

## 4. 持久化合成任务

生产调用优先使用共享 JobService，而不是让 HTTP 请求持有整段合成生命周期：

- `POST /api/v1/tts/synthesize/jobs`：对已审核脚本执行合成；
- `POST /api/v1/tts/pipeline/jobs`：执行完整配音流水线；
- `GET /api/v1/jobs/{job_id}`：读取持久任务快照；
- `GET /api/v1/jobs/{job_id}/events`：订阅 SSE 进度；
- `POST /api/v1/jobs/{job_id}/cancel`：取消运行并保留章节分段断点；
- `POST /api/v1/jobs/{job_id}/resume`：恢复 StartupReconciler 标记为 retry_wait 的任务。

JobService 持久化原始 intent，control-plane 记录 WorkUnit/RunAttempt；章节断点保存已完成的
`SynthesisResult`。因此恢复任务会重建同一命令，但只重新调用尚未完成或指纹变化的片段。

任务只有在 `delivery_ready=true` 且交付文件仍存在时才进入 succeeded；否则进入 failed，
并通过 `tts_delivery_blocked` 事件公开阻塞原因。

## 5. 统一供应商故障策略

所有通过 `TTSAdapterRegistry` 创建的适配器都会由统一故障装饰层保护，覆盖合成、克隆、
音色设计、音色目录、能力发现和健康检查。稳定分类包括：

- `rate_limit`、`timeout`、`provider_unavailable`：可重试并计入供应商熔断；
- `authentication`、`content_filter`、`invalid_request`：不可盲目重试，不污染熔断计数；
- `circuit_open`：快速失败并返回建议等待时间；
- `segment_quality`、`internal`：允许片段级重试，但不把本地后处理失败归咎于供应商。

分类、`retryable`、`retry_after_s` 和 provider error code 会写入失败的 `SynthesisResult`，
避免 UI、API 或各平台适配器自行解析错误字符串。

## 6. 新平台接入

### 6.1 只有新模型、没有新协议

优先复用 `local` OpenAI-compatible transport，并提供 `*.audio-plugin.json` manifest。规划器会按 provider、能力、语种、质量档位和资源约束自动选择模型；不应修改合成流水线。

### 6.2 新鉴权或新 wire protocol

实现 `TTSProviderAdapter`，然后在应用启动时注册：

```python
from novel_forge.tts.gateway import register_tts_adapter
from acme_novel_forge.config import adapter_kwargs, resolve_model

register_tts_adapter(
    "acme",
    "acme_novel_forge.adapter",
    "AcmeTTSAdapter",
    # 扩展包自行读取其配置；无需向 Novel Forge 的 Settings 增加平台字段。
    kwargs_factory=adapter_kwargs,
    model_resolver=resolve_model,
    display_name="Acme Voice",
    default_models=("acme-voice-v1",),
    default_voice_id="acme-default",
    is_local=False,
)
```

注册完成后，provider id 会贯通：

- Pydantic 持久化 schema；
- workspace provider 校验；
- adapter 构造参数与模型解析；
- 本地资源调度/云端请求节流；
- `/tts/providers` API；
- Voice Studio 平台下拉框和 manifest 模型列表。

外部 provider id 只有在可信适配器完成注册后才会被 schema 接受，拼写错误仍会失败。

### 6.3 Manifest 要求

新平台至少提供一个 `SPEECH_SYNTHESIS` manifest，`provider_id` 必须与 transport 注册 id 一致。正式模型加 `formal` tag，快速试听模型加 `preview` tag。规划器会自动把当前平台的最佳匹配 manifest 固定到 TTS stage，无需再维护 provider → plugin 的硬编码映射。

## 7. 适配器契约

所有适配器必须：

- 精确声明 `capabilities`，UI 只按能力开放克隆、设计和系统音色；
- `synthesize()` 返回实际格式、模型、音色、费用、延迟和可审计 evidence；
- 网络失败抛出网关异常或明确失败，不返回空音频伪成功；
- 本地参考音频只在声明支持时接受；克隆必须带授权标志；
- `health_check()` 不抛异常并维护 `last_health_error`；
- `shutdown()` 释放 HTTP client、sidecar 或模型资源；
- 不在模块 import 时下载模型或加载大权重。

### 7.1 内置云端平台注意事项

#### 火山方舟·豆包语音

- provider id 为 `volcengine_ark`，面向已购买 Agent Plan 的用户；正式模型为
  `doubao-seed-tts-2.0`，请求头资源 ID 固定为 `seed-tts-2.0`。
- 使用 Agent Plan 专用 HTTP Chunked 接口
  `https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional`，通过
  `X-Api-Key` 传入 Agent Plan 个人版专属 Key。不再展示旧版 App ID + Access
  Token，也不会误走普通方舟 `/api/v3` 或 LLM `/api/plan/v3` 路径。
- 语速和响度映射到 `speech_rate` / `loudness_rate`；声腔导演指令进入
  `additions.context_texts`；音高映射到 `additions.post_process.pitch`。官方当前只消费
  `context_texts` 的第一项，所以适配器会先合并情绪、语气和表演方式，避免声腔丢失。
- 协议、Key 和地址以[方舟 Agent Plan 接入语音模型](https://www.volcengine.com/docs/82379/2516286)
  为准；请求字段以[HTTP Chunked/SSE 单向流式 V3](https://www.volcengine.com/docs/6561/1598757)
  为准；音色 ID 以[官方音色列表](https://www.volcengine.com/docs/6561/1257544)为准。

#### Xiaomi MiMo

- provider id 为 `mimo`，正式预置音色路由使用 `mimo-v2.5-tts`。
- 调用 `POST https://api.xiaomimimo.com/v1/chat/completions`，用 `api-key` 头鉴权。
  目标发声文本必须放在 `assistant` message，声腔与表演指令放在可选的
  `user` message。
- 非流式返回 WAV base64，适配器按实际 `wav` 格式入库；精确语速、增益与
  音高由公共本地后处理归一，避免自然语言指令与精确倍率重复生效。
- MiMo V2 旧模型已于 2026-06-30 下线，禁止在默认模型列表中恢复
  `mimo-v2-tts`。预置音色、音色设计与音色复刻是三个不同模型；当前声腔正式团队只
  公开能持久绑定的预置音色能力，不把一次性设计/复刻音频虚报为可复用 voice ID。
- 模型、音色、message 角色和格式要求以
  [MiMo V2.5 TTS 官方文档](https://mimo.mi.com/docs/zh-CN/usage-guide/speech-synthesis)为准。

## 8. 变更验收清单

每次修改配音架构至少验证：

1. `test_tts_adapter_registry.py`：注册、配置、模型解析与 event-loop 隔离；
2. `test_tts_synthesize_step.py`：指纹恢复、证据保留、缺失 cast、并发和重试；
3. `test_tts_production_hardening.py`：终稿来源、部分失败续跑、take 隔离；
4. `test_audio_plugin_platform.py`：manifest、规划、语种和本地/云端约束；
5. `test_project_staleness_cleanup.py`：章节变更和项目级 voice contract 失效；
6. `test_api_tts_routes.py`：HTTP 错误语义、进度读取、旁白-only；
7. Voice Studio desktop tests：平台切换、取消、shutdown 与 UI 状态。
8. `test_tts_delivery.py`：交付文件、失效装配和旧结果硬门禁；
9. `test_tts_failure_policy.py`：异常分类、不可重试错误和供应商熔断；
10. `test_tts_persistent_jobs.py`：JobKind、分段事件、最终交付门与恢复 intent。
