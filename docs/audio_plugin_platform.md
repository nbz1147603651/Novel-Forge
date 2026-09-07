# 能力驱动的音频插件平台

配音工作室以“平台深度适配 + 能力统一编排”为边界：平台适配器负责认证、原生字段、模型目录、限流和错误语义；章节流水线只依赖统一能力与数据契约。新增模型通常只需注册 manifest 和对应 sidecar/adapter，不应在章节流程中增加厂商分支。

## 生产流程

```text
配音脚本
  ├─ 中立声腔指令、语言 run、发音与重音
  └─ platform_extensions（仅适配器读取）
        ↓
AudioModelPlanner（目标、语言、硬件、隐私、用户覆盖、分数卡）
        ↓
TTS → ASR 核验 → 已知文本强制对齐 → SpeechTimeline
        ↓
按真实人声时间线解析 cue → 生成/复用 BGM、环境声、SFX
        ↓
MixPlan（独立 stem、事件、增益、淡入淡出、ducking）
        ↓
FFmpeg 单次 filtergraph 渲染 → 响度/削波/对齐质量报告
```

背景声不是在结尾简单拼接。系统先生成正式人声，再取得真实时间线；脚本中的段落、文本与语义锚点据此解析为绝对时间。声音资产保持独立轨道，由 `MixPlan` 决定进入、退出、优先级和在人声下的侧链压低，最后一次渲染和编码。

## 稳定能力接口与数据契约

主流程使用以下能力，不依赖模型名称：

- `speech_synthesis`、`voice_clone`、`voice_design`
- `asr`、`forced_alignment`、`vad`
- `sfx_generation`、`music_generation`、`soundscape_generation`
- `audio_render`、`quality_evaluation`

Provider 输出被转换为 `AlignmentResult`、`SpeechTimeline`、`AudioAssetRef`、`MixPlan` 和 `AudioQualityReport`。项目数据只保存中立字段；必须保留的厂商原生参数放入带命名空间的 `platform_extensions`。

## 插件 manifest

第三方描述文件名必须为 `*.audio-plugin.json`。设置页可配置一个或多个清单目录；加载清单不会导入目录中的 Python，也不会下载权重。

```json
{
  "schema_version": "1.0",
  "plugin_id": "example-aligner",
  "display_name": "Example Aligner",
  "provider_id": "example",
  "model_id": "example/aligner-1",
  "capabilities": {
    "services": ["forced_alignment"],
    "known_text_alignment": true,
    "granularity": ["character", "word"],
    "languages": ["zh", "en", "ja"],
    "offline": true,
    "confidence": true
  },
  "runtime": {
    "execution": "local_sidecar",
    "platforms": ["macos", "windows", "linux"],
    "accelerators": ["cpu", "cuda", "mps"],
    "memory_class": "medium",
    "endpoint_setting": "audio_example_base_url"
  },
  "quality": {
    "profile": "production",
    "quality_score": 0.85,
    "latency_score": 0.7,
    "priority": 80
  },
  "integration_status": "installed"
}
```

若插件使用新的 `endpoint_setting`，还需在 `Settings` 中声明同名设置；内置通用设置目前覆盖 Qwen3-ASR/ForcedAligner、WhisperX 和 Sherpa ONNX。

## Sidecar 协议

模型依赖留在独立运行时，主程序使用 HTTP 协议：

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/health` | 就绪状态、模型与运行时版本 |
| `GET` | `/capabilities` | 运行时实际能力和语言覆盖 |
| `POST` | `/self-test` | 固定样本安装校验 |
| `GET` | `/version` | 模型权重、运行时与协议版本 |
| `POST` | `/transcribe` | multipart 音频、`language`、可选 `expected_text` |
| `POST` | `/align` | multipart 音频、已知 `text`、`language` |
| `GET` | `/diagnostics` | 设备、峰值内存和失败原因 |

`/align` 的返回值至少包含 `items`、`words` 或 `tokens` 之一；每项使用 `text|word|token`、`start_ms|start`、`end_ms|end` 和可选 `confidence`。秒单位和毫秒单位都会被主程序标准化。

## 质量预设与高级覆盖

设置页提供快速试听、本地正式成片、极致精校、低配置离线、云端优先和隐私优先。预设是评分政策，不是写死的模型包。计划器综合：

- 语言和混读片段；
- 当前 TTS 平台以及逐阶段用户固定项；
- 是否允许云端、硬件加速器和内存预算；
- manifest 的质量、延迟、内存和语言声明；
- 当前设备与项目产生的 `ModelScorecard`。

计划包含主插件、三层回退、按语言路由、选择理由和告警。用户固定某阶段时，其他阶段仍保持自动选择。

## 平台字段适配

声腔脚本使用中立方向：情绪、语气、表达风格、能量、吐字、气声、张力、重音、发音、语言和空间效果等。每个平台的字段映射声明为：

- `native`：直接映射为平台请求字段或原生 instruct；
- `portable`：在本地音频后处理实现；
- `director_only`：保留为导演提示，当前平台不执行；
- `unsupported`：明确不支持。

例如 Qwen3-TTS 将中立方向汇成自然语言 `instruct`，MiniMax 保留其原生情绪、语言和声音效果字段。UI 会随当前平台展示支持等级，避免提供看似可调但实际被丢弃的参数。

### 平台原生层与公共闭环的边界

平台特有能力只在 adapter/sidecar 内执行，不进入公共流程分支：

- **MiniMax speech-2.8**：原生消费 `voice_setting`、`pronunciation_dict`、`language_boost`、`voice_modify`、语气词位置、词级字幕请求和可选水印；返回的 trace、provider status、时长、采样率、大小和不可见字符比例会归一为 `ProviderTakeEvidence`。
- **Qwen3-TTS**：CustomVoice 消费 speaker/language/`instruct`，VoiceDesign 消费声线描述和语种，Base 消费授权参考音频与转写。采样参数只允许 `temperature`、`top_p`、`max_new_tokens` 白名单进入 sidecar，其他平台扩展不会盲目透传。

两者之后完全共用同一条工业级闭环：

`take 负载校验 → 时长/语速客观门 → ASR 转写核验 → 已知文本强制对齐 → 坏段有界重生 → 真实人声时间线 → 声音资产解析/生成 → MixPlan → 一次编码多轨渲染 → 响度/削波/遮蔽/文本准确率质检`

修复只选择“转写错误超阈值”或“已对齐但覆盖不足”的片段。无 ASR/对齐证据的服务不可用不会引发盲目重生；新 take 未通过客观门时保留旧 take。`master` 预设的最终质量报告未通过时，产物可供试听和诊断，但不标记为正式成片。

## 应用级音频模型中心

模型权重是应用级资源，不是小说项目文件。应用级模型库按运行时分目录（例如 `models/audio/stable-audio/`）；源码运行时位于仓库根目录且已被 Git 忽略，冻结桌面版位于用户可写的 Novel Forge 应用数据目录。项目目录只保存生成的音频资产、模型 ID/版本和请求指纹。

- **应用直接托管**：Qwen3-TTS、Qwen3-ASR/ForcedAligner、Sherpa ONNX、Silero VAD 和 Stable Audio 的权重由“本机音频模型中心”下载。Hugging Face 快照、Release 压缩包和单文件分别使用不同安装策略；下载先进入 `.partial` 目录，文件清单校验通过后再原子切换。
- **sidecar 协同托管**：权重可以由应用下载后向 Qwen/Sherpa sidecar 注册本地路径，也可以由 WhisperX 这类运行时自行安装语言对齐包。应用通过统一端点读取版本、健康状态、已安装模型和自检结果。
- **云端托管**：MiniMax 等。应用只保存模型标识和凭据，不保留权重。

模型中心目前支持状态检测、许可确认、后台下载、文件校验、占用统计、打开目录、sidecar 注册/自检、项目引用检测和受保护删除。“当前平台模型”只列出已安装且被检测的本地模型；历史配置指向缺失模型时保留其 ID 和告警，仍允许用户切换到其他已安装模型，不会悄然改写选择。

运行时生命周期已与权重库分层：

- 每个运行时使用 `runtimes/<runtime>/versions/<version>` 独立 Python 环境，安装后先执行 import self-test，再原子更新 `current.json` 激活指针。旧版不会被覆盖；升级后重启失败会恢复旧指针和旧 sidecar。
- 受管 sidecar 记录 PID、期望运行状态、重启次数和日志。启动前检查端口：健康的已有 sidecar 可接管，非 sidecar 占用则拒绝覆盖。页面刷新会对 `should_run` 但已崩溃的进程执行有上限的自动恢复。
- HTTP Release/单文件下载使用持久化任务、`.part` 文件和 Range 请求；Hugging Face 快照保留原生缓存与 staging 目录，应用重启后继续。任务状态和已下载字节持久化到 `downloads/tasks/`。
- 模型库迁移先复制到 staging，再比对全部相对路径与 SHA256，最后原子切换并保留原目录。若新库之后发生任何模型变化，自动回滚会拒绝删除，避免丢失新数据。
- 每个变更使用带 PID 的跨进程锁文件。直接下载可配置 SHA256；带签名的条目在未提供可信公钥验证器时 fail closed。模型卡通过 `runtime_id` 及最小/最大运行时版本进入兼容性矩阵；不兼容或 sidecar 不健康的模型不进入平台可选列表。

当前 Qwen3-TTS 已提供完整的受管 sidecar 入口；WhisperX 和 Sherpa 已纳入独立环境、权重与兼容性管理，但在其 Novel Forge 专用 HTTP 包装器完成前，进程仍标记为外部托管，界面不会虚假显示“可启动”。

sidecar 模型管理协议扩展为：

- `GET /models`：返回 `plugin_id`、`model_id`、`local_path`、`size_bytes`和安装状态；
- `POST /models/install`：接收模型 ID、revision、许可确认及可选的应用本地路径；
- `POST /models/delete`：删除 sidecar 自行托管的模型；
- `POST /self-test`：对指定 `model_id` 执行最小真实推理。

可复用音频素材采用同样的分层原则：项目声音库默认只属于当前作品；批准后的素材可显式发布到应用声音库。章节匹配优先使用项目库，再使用应用库，并把实际引用的路径和来源写入章节声音解析报告。

## 本机基准与产物

“用当前章节校准”从已完成的人声中抽取最多 30 段，调用已配置的 ASR/对齐 sidecar，记录字符错误率、延迟、质量和诊断内存。分数卡随后进入该设备、项目和语言组合的计划评分。

每章可审计产物位于 `data/<project>/tts/`：

- `model_plans/chapter_<n>.json`
- `timelines/chapter_<n>.json`
- `mix_plans/chapter_<n>.json`
- `quality/chapter_<n>.json`
- `model_scorecards.json`

某个 sidecar 不可用时，同一运行中会缓存该失败，尝试 manifest 中的回退；强制对齐全部失败时降级为片段级时间线，不会丢失已经生成的人声。

## 扩展检查清单

1. 新增 manifest，准确声明语言、粒度、离线属性、内存和许可。
2. 实现统一 sidecar 协议，或在现有平台 adapter 中完成深度字段转换。
3. 为平台字段增加 `field_mapping` 配置，避免原生字段渗入中立脚本。
4. 增加契约、回退和混合语言测试。
5. 用真实章节运行 self-test 和本机基准，再提高默认优先级。
