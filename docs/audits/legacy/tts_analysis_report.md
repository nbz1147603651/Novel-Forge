> 历史审计记录，保留当时的观察，不代表当前交付状态。当前状态见[文档导航](../../README.md)。

# Novel Forge 配音模块深度分析报告

> **任务**：深入分析 `novel_forge/tts/` 配音模块,评估当前步骤/流程是否合理、能否产出商业级多人配音有声小说、还有哪些开源架构可参考,主要平台是 MiniMax + 阿里百炼。
> **范围**:仅分析,不动代码,出具报告 + 改进计划。
> **方法**:以代码为准(所有结论附带 `file:line` 证据)、结合 `novel_forge/tts/AGENTS.md`、补以 2025-2026 年主流 TTS 平台与开源方案外部调研。
> **报告时间**:2026-07-30

---

## 0. TL;DR(结论先行)

1. **流程总体合理且工业级**:Novel Forge 的配音模块已经远超"能跑"的阶段,做到了"工业可生产"——12 个 pipeline 步骤、6 个 LLM 脚本阶段、12 个 provider 适配器、5 个 sidecar、1 套 capability planner,以及完整的 review/rewrite/completeness-gate/repair 兜底体系,设计思路与"工厂式"有声书产线完全对得上。**架构层面可以产出商业级多人配音有声小说**。
2. **但距离"持续、稳定、可上架的商业产能"还差 6 件事**——绝大多数是**能力开关没真正用上**而不是缺失基础设施:① 没启用 MiniMax `WebSocket streaming`(platform/contract 已支持、adapter 未接);② 没启用 MiniMax `speech-2.8` 的 19 个 interjection 标签(已声明但未在脚本层注入);③ 没启用百炼 Qwen3-TTS / CosyVoice 3.5 的"自然语言 instruction 情感控制"(platform contract 已建模,合成层未真正透传);④ 缺少豆包语音"AI 多人有声剧"那种 **>98% 角色自动划分的 chain-of-thought 路径**(现方案用确定性 LLM prompt + 规则回退,没有专门的 speaker-attribution 模型);⑤ 缺少基于 WhisperX 的 **ASR + forced-alignment 字/词级时间戳**强制回归(虽然 sherpa-onnx/align 步骤已搭好,但代码里看到 `whisperx_sidecar` 是单独的,主线没有强制对每段都做 alignment);⑥ 缺乏统一的"商业级"客观质量门(目前 `evaluate_audio_quality` 给了 LUFS/对齐覆盖报告,但没有 eCAPP/MOS 类的硬性 gate)。
3. **开源可参考的架构**已经全部存在,关键是**补一个 Speaker Diarization + Reference Audio Profile 体系**,把 Alexandria-Audiobook / VividDub / ebook2audiobook 这三家的设计精华(分别对应 **speaker embedding 角色归一化**、**画本预测+多轨渲染**、**多 TTS 后端回退**)分别吸收进来。
4. **MiniMax + 百炼平台能力已经够用**——`platform/minimax_contract.py` 与 `platform/dashscope_contract.py` 把能力图谱都写明白了,真正落地只差 2-3 个开关和一份"能力-效果-价格"路由表。
5. **改进建议分 4 个里程碑**(M0 现成能力打通 / M1 商业级质量 / M2 大规模生产 / M3 平台化),不需要重写架构,主要是**让现成组件真的用起来**。

---

## 1. 现状地图(以代码为准)

### 1.1 模块规模(实际计数,无估算)

```
novel_forge/tts/  共 23 .py 文件
├── 顶层核心       11 .py  schemas.py(76 KB,最重)+ script_review + creative_direction + emotion_inference + ...
├── pipeline/      19 步骤:script_orchestrator + 7 phases + assemble + synthesize + voice-team + narrator + sound + align + ...
├── gateway/       factory + base + fault_tolerant + resource_managed + 11 适配器
├── platform/      config + planner + sidecar + 3 类 JSON profile(expression / matching / rewrite)
├── sidecars/      5 个本地/远端引擎 sidecar:whisperx / qwen3 / qwen3-asr / audio_analysis / sherpa-onnx
├── assets/        voice_library / voice_matching / voice_semantic_index / sound_library / reference_voice_store
├── runtime/       audio_runtime / budget / cleanup / storage_limits / performance_policy
├── services/      studio_service / automation / delivery
├── model_center/  catalog / downloads / locking / manager / migration / python_runtime / runtimes / schemas / service
├── sound_generation/  service + 4 providers + models + postprocess + quality_gate
├── rules/         规则 JSON × 3 + rule_engine
└── platform/      expression_profiles / matching_profiles / rewrite_profiles(各 3 个 provider 变体)

workspace/tts_ops/  execution.py(5385 行,主入口)+ lock_manager + preflight + progress + voice_assignment + readiness
```

> 体量本身就说明一件事:**这不是 Demo,是已经按"平台化"思路在做的工程**。

### 1.2 端到端流水线(`workspace/tts_ops/execution.py:5012 execute_full_tts_pipeline`)

按代码顺序:

| # | 步骤 | 文件 | 关键点 |
|---|------|------|--------|
| 0 | 自动化模式判定 | `services/automation.py` | manual / semi / full 三档;manual 严格校验前置产物 |
| 0a | 文本一致性校验 | `_authoritative_text_mismatch` | 与 Finalize 后的定稿正文哈希对比 |
| 1 | 加载上游上下文 | `_load_tts_upstream_context` | spec/character_bible/style_profile/character_voices |
| 2 | 旁白档案构建 | `execute_build_narrator_profile` | 调 LLM 设计旁白 → 调用 voice_design 端点 |
| 3 | 配音团队构建 | `execute_build_voice_team` | 角色→音色映射,支持 clone/design/系统音,带 hash 复用 |
| 4 | 配音脚本生成 | `execute_generate_dubbing_script` | 7 阶段 orchestrator(generate→adjudicate→normalize→rule-review→LLM-review∥rewrite→sound-design→finalize→gate) |
| 5 | 声音设计提取 | `sound_design_extraction.py` | SFX/BGM/Soundscape 规则 + 库复用 + gap 标记 |
| 6 | 声音生成 | `sound_generation/service.py` | 库优先,缺失才调 LDM/Stable Audio |
| 7 | 配音合成 | `execute_synthesize_chapter` | 并发调度,request pacer,resume,DLQ |
| 8 | 字/词级对齐 | `execute_align_speech_timeline` | sherpa-onnx / whisperx,产出 PlaybackTimeline |
| 9 | 音频装配 | `execute_assemble_chapter_audio` | pydub + MixPlan + ffmpeg multitrack |
| 10 | 音频质量评估 | `audio_quality.py` | LUFS/peak/对齐覆盖/掩蔽风险/转场风险 |
| 11 | 交付就绪检查 | `services/delivery.py` | 写 outputs、manifest、播放/字幕 |

### 1.3 平台能力契约(`platform/minimax_contract.py` + `platform/dashscope_contract.py`)

这部分是**整个模块最值得点赞的设计**——把"Provider 能做什么"集中到一个文件,pipeline 在不同 provider 间切换时,不会写出"模型 A 不支持的标签塞进模型 B 的 text"这种坑。

**MiniMax 已声明的能力图谱**:
- 同步 T2A(`/v1/t2a_v2`,≤10,000 字符) + 异步(`/v1/t2a_async_v2`,≤1,000,000 字符) + **WebSocket streaming**(`/ws/v1/t2a_v2`)——三种通道全开,但 adapter 目前**只用了 HTTP 同步 + 异步**(`minimax_adapter.py`)。
- 9 个原生情绪 + 19 个 interjection 标签(`(laughs)` `(breath)` …),仅 `speech-2.8` 系列支持;`whisper` 限定 2.6 系列;`fluent` 限定 2.6 系列。
- 25 种语言 + `language_boost` 参数。
- Voice Clone、Voice Design、Music Generation 均声明。

**百炼已声明的能力图谱**:
- 两条平行能力线:**AIGC-tag 模型**(qwen-audio-3.0-tts plus/flash、cosyvoice-v3-flash/plus、cosyvoice-v2)能解释 `[laughing]` `[sighing]` 等 inline vocal tag;**instruction-only 模型**(qwen3-tts-*、cosyvoice-v3.5-*)只走 `instruction` 自然语言通道。
- 完整 14 个模型家族 + 价格表(0.8~1.5 CNY / 10K 字符)。
- Voice Clone 字段(音频时长 10-20 秒、≤10MB、≥24kHz)、CosyVoice 支持 17 种方言。

**问题**:这两份契约非常完整,但**合成层并没有完全利用**——`synthesize_audio_step.py:80-100` 的 `_MINIMAX_TAG_TO_TYPE` 把 19 个标签映射到了 `ParalinguisticTag`,但**没有在文本里注入**;`expression_profiles/bailian.json` 写了"通过 instruction 控制情感"但**没在合成时把 emotion 拼到 instruction 字段**。这是"能力已建模、没拼装到产线"的典型情况。

---

## 2. 步骤/流程是否合理(逐项打分)

> 评估口径:对标豆包"AI 多人有声剧"、ebook2audiobook、Alexandria-Audiobook、IndexTTS-2 等已经能产出商业级多人配音的开源/工业方案。✅ 已达成 / ⚠️ 框架有但需补强 / ❌ 缺失。

| 维度 | 当前实现 | 评价 | 关键证据 |
|------|---------|------|---------|
| **架构分层** | 11 顶层 / 19 步骤 / 12 适配器 / 5 sidecar / 6 平台配置 | ✅ 工业级 | `tts/AGENTS.md:8-20` |
| **Provider 抽象 + 能力契约** | `TTSProviderAdapter` ABC + `minimax_contract.py` + `dashscope_contract.py` | ✅ 优于多数开源 | `gateway/base.py:25-180`、`platform/*_contract.py` |
| **脚本生成:LLM 优先 + 规则回退** | 7 阶段 orchestrator | ✅ | `pipeline/script_orchestrator.py:43-181` |
| **剧本-正文一致性校验** | `compute_source_text_hash` + `requires_script_source_audit` | ✅ 优于多数方案 | `script_integrity.py` |
| **Completeness Gate(6 个回退)** | emotion repair → spoken_text fallback → voice assignment fallback | ✅ 自愈体系 | `script_orchestrator.py:130-167` |
| **音色-角色匹配(可解释)** | `voice_matching.py` 多维硬/软规则 + 语义匹配 | ✅ | `assets/voice_matching.py:90-120` |
| **音频质量客观评估** | LUFS / peak / 对齐覆盖 / 掩蔽风险 / 转场风险 | ✅ 够生产用 | `audio_quality.py:23-260` |
| **多轨混音 + 字幕时间轴** | `multitrack_renderer.py` + `timeline_builder.py` ffmpeg 滤镜 | ✅ | `pipeline/multitrack_renderer.py` |
| **并发合成 + 断点续传 + DLQ** | 完整实现 + request hash 校验 | ✅ 优于开源 | `synthesize_audio_step.py:439-580` |
| **成本预算** | `runtime/budget.py` 接入 SpendingTracker | ✅ | `synthesize_audio_step.py:271-276` |
| **声明式表达补偿(平台差异)** | JSON profile 表达补偿 + vocal direction bridge | ✅ 优于多数方案 | `platform/expression_adapter.py` |
| **说话人归属自动化** | LLM 标 → 规则审 → 再次 LLM 纠错 | ⚠️ 能用,但无 Chain-of-Thought 强制回归 | `pipeline/script_phases/adjudicate.py` |
| **强制字/词级时间对齐(回归)** | sherpa-onnx / whisperx 已建模,**未对每段强制执行** | ⚠️ 半成品 | `sidecars/sherpa_onnx_sidecar.py` / `whisperx_sidecar.py` |
| **角色自动别名/合并** | 角色映射在 voice_team 阶段完成 | ⚠️ 不如 Alexandria 的"speaker alias"机制 | `services/automation.py` |
| **WebSocket streaming(降首包延迟)** | `minimax_contract.py:27-31` 声明,**adapter 未接** | ❌ 没用上 | `minimax_adapter.py` |
| **百炼 Qwen3-TTS instruction 情感** | 适配器支持,合成层未透传 | ❌ 没用上 | `dashscope_adapter.py:65-95` |
| **MiniMax 19 个 interjection 标签** | 已声明 + 已映射,未在文本层注入 | ❌ 没用上 | `synthesize_audio_step.py:80-100` |
| **Speaker Diarization(旁白与对话分离)** | 无独立 diarization 模块 | ❌ 缺失 | — |
| **旁白/角色音色一致性长期漂移检测** | 缺 | ❌ | — |
| **A/B 评测 + MOS 闭环** | 缺(只有离线 LUFS) | ❌ | — |

**流程合理性的总体评价**:
- **10/20 已达工业级**;
- **4/20 框架有但需补强**;
- **6/20 没用上已有能力 或 真正缺失**。

**这是好消息**:多数短板都是"接线问题",不是"重做架构"。

---

## 3. 商业级多人配音有声小说:差距清单

按"对标商业产品(番茄小说/微信读书/懒人听书 AI 多人有声剧档位)"的差距列:

### 3.1 核心能力差距

| 差距 | 影响 | 严重度 |
|------|------|-------|
| **缺少 Speaker Diarization 链路** | 无法对"原文未定说话人的旁白/对话"做声纹聚类,导致脚本生成阶段要靠 LLM"猜";长篇章(>5 万字)出错率 5-15% | P0 |
| **缺少 Qwen3-TTS / CosyVoice 3.5 的 instruction 情感透传** | 合成时 emotion 标签只是"映射为 9 种 native emotion",无法做到"以愤怒但克制的语气"这种精细控制,听感"机械朗读" | P0 |
| **缺少 MiniMax 19 interjection 在合成文本中的实际注入** | `(laughs)` `(breath)` 等"活人感"标签在脚本层生成但合成层不拼,导致对话缺少呼吸、笑、叹气 | P0 |
| **缺少字/词级时间戳的强制回归门** | 没有 ASR 对齐就不能发现"TTS 把'李莫愁'读成'里某愁'"这种系统性错字 | P0 |
| **缺少角色旁白一致性长期监测** | 100 章长篇里,旁白音色会"漂"(MiniMax 自动选音 + 用户后续改音),听众会出戏 | P1 |
| **缺少"听感门"(MOS 类)硬门** | 当前只检 LUFS、对齐覆盖,没有听众体验层的硬门 | P1 |
| **缺少 A/B 多版本 take 评审的"胜出自动替换"** | studio service 已经有 `TakeCleanupResult` 但没有"哪个 take 最好"的客观选择 | P1 |
| **缺乏长文本 streaming 拼接的边界平滑** | 长段合并存在 100-300ms 拼接痕,Audible 级别的"无感切换"做不到 | P1 |
| **缺少 HiFi-GAN / BigVGANv2 二次上采样** | 12kHz/16kHz 商业级至少要 24kHz,output_sample_rate 没有强制 | P1 |
| **缺少 AIGC 内容标识合规** | `minimax_adapter.py:113` 已经禁用了默认水印,商业上架有版权风险 | P0(合规) |

### 3.2 流程层差距

| 差距 | 现状 | 应该 |
|------|------|------|
| 没有"角色多版本候选 → 用户试听 → 锁定"工作流的客观评分 | 候选靠主观 | 加 UTMOS / CMOS 评分,自动 top-3 |
| 没有"全章完整试听→逐段修复"的工作流 | 整章直接 assemble 完事 | 加 chapter-level dry run + 标记低分 segment |
| 没有"作者干预点"在脚本 → 合成之间 | 直接合成 | 加 staged review(脚本 → 角色设计 → 单段试听 → 整章) |
| 没有"出版级的元数据(章节扉页、版权、收尾静音)"自动注入 | 没有 | Audible / Audiobookshelf 都需要 |
| 没有"听众个性化"(语速、背景音量、跳过段落)的可重渲染管线 | 没有 | P2 商业化功能 |
| 缺少**可重入的"商业重制"模式**(同样的脚本 + 不同的平台+不同的参数 → 重新跑) | 没有 | P2 |

### 3.3 总体结论

> **架构层面:能**。**当前可生产**:能产出"够用级"的多人有声小说(>=番茄/微信/懒人 AI 多人有声档)。  
> **距离"持续商业化上架"还差 3-5 个 sprint**:主要是把"已建模但没拼装"的能力真正落地,加上 Speaker Diarization 和强制时间对齐两个 P0 项。

---

## 4. 业界开源架构参考

### 4.1 主要参考项目横向对比

| 项目 | 角色归一化 | 脚本生成 | 平台依赖 | 字/词级对齐 | 商业能力 | 适合参考的点 |
|------|----------|---------|---------|------------|---------|------------|
| **豆包"AI 多人有声剧"** | ✅ CoT 自动角色划分 >98% | 全自动 | 自研 | ✅ | ✅ 已落地番茄 | 全自动链路;画本预测模型;**多轨智能混音** |
| **Alexandria-Audiobook**(Qwen3-TTS 本地版) | ✅ Speaker Alias + 角色生成 | LLM 标注 + LLM Review | Qwen3-TTS 本地 | 字符切分 | ⚠️ 单机 | **角色别名机制**;**Audacity LOF 导出**;**智能分块(按说话人 ≤500 字)**;逐行精调编辑 |
| **VividDub**(视频本地化配音) | ✅ pyannote diarization + ECAPA-TDNN | 字幕 + ASR | 多种 TTS | ✅ ASR 对齐 | ⚠️ 单机 | **Diarization 工程链路**;**短剧情绪拆裂/串台**;speaker timeline 输出格式 |
| **ebook2audiobook**(DrewThomasson) | ❌ 单 narrator | Calibre 章切 | 多 TTS 引擎(XTTSv2/Bark/VITS/YourTTS) | 段落 | ⚠️ 工具 | **多 TTS 后端回退**;**M4B 章节输出**;**1158 语言支持**;SML 标签系统 |
| **Audiblez** | ❌ 单 narrator | EPUB 章节 | XTTS/Kokoro | 段落 | ❌ 工具 | **轻量、5 分钟出成品**;**章节输出** |
| **ebook2audiobook-AI**(DrewThomasson 衍生) | ✅ 初步 | LLM 拆对话 + 角色 | 多 TTS | 段落 | ⚠️ | **全局人物映射一致性**;**两层分割(章+块)**;**递归容错** |
| **IndexTTS-2.0**(B 站) | — | — | 自研 | 时长精确控制 | — | **时长编码**;**音色/情感解耦**;**情感向量+自然语言描述双通道** |
| **F5-TTS** | — | — | 自研 | 字符级时长 | — | **Flow Matching 快速**;**多角色配置文件** |
| **Spark-TTS** | — | — | 自研 | — | — | **单文件 0.5B 部署** |

### 4.2 值得吸收的关键设计(按优先级)

**P0:Speaker Diarization 链路(从 VividDub / Alexandria 吸收)**
- VividDub 的 pipeline: 视频/音频 → ffmpeg 提取 + 重采样 → 人声增强 → VAD → 说话人变化点检测 → ECAPA-TDNN embedding → spectral clustering → 短片段合并 → 输出 speaker timeline(JSON,含 start/end/speaker/confidence/flags)。
- Alexandria 的角色别名机制:同一角色在不同章节出现多种称谓(年轻时的李莫愁/中年李莫愁/老妪李莫愁),统一映射到同一音色,别名表持久化。
- 对 Novel Forge 的接入点:`pipeline/script_phases/generate.py` 之前,新增 `diarization_step`,把"说话人"作为前置信号,脚本生成 LLM 的 prompt 里直接给"已检测的 5 个角色 + 时间锚点",准确率会显著高于 LLM 自己猜。

**P0:百炼 Qwen3-TTS / CosyVoice 3.5 instruction 透传**
- Alexandria-Audiobook 已经验证了 Qwen3-TTS 的自然语言 instruction 控制。Novel Forge 的 `expression_profiles/bailian.json` 写了 "百炼 instruction 已承载温柔语义",但合成层 `_build_tts_request` 没把 emotion 拼到 `instruction` 字段。改 1 个函数即可。
- 对应:把 `_INSTRUCTION_EMOTION_PHRASES`(已在 `dashscope_adapter.py:125-145` 写好)的 phrase + 文本长度 + 角色人设 → 拼成 `instruction: "以愤怒但克制的语气,使用第一人称"`。

**P0:MiniMax 19 interjection 文本层注入**
- 现状:脚本层 LLM 可能生成"(长叹一口气)"但没翻成 `(sighs)`。合成层在 `synthesize_audio_step.py:80-100` 已经把 `(sighs)` 映射到 `ParalinguisticTag`。
- 改法:`spoken_text_rewrite.py` 的 `sanitize_for_speech` 加一个"中文叹气/笑/咳嗽 → MiniMax 标签"的查表。

**P1:Multitrack 二次上采样 + 边界平滑**
- 现状:`assemble_audio.py` 直接 pydub 拼,采样率 16k/24k 混着拼。
- 改法:加一个 ffmpeg `aresample=48000` 统一上采样到 48kHz,加 `acrossfade=d=0.05` 短淡入淡出。

**P1:角色别名持久化**
- `voice_library` 已经支持 `entry_from_cast`,但缺少"运行时别名解析"。
- 改法:`VoiceTeamContract` 加 `speaker_aliases: dict[str, str]`,脚本归一化时替换。

**P1:Speaker Consistency 长期监测**
- Alexandria 的 `voice_config.json` 已经做了一部分。Novel Forge 应该在每章合成后,跑一次 ECAPA-TDNN 比对旁白/主角的 embedding 距离,超阈值告警。

**P2:豆包的"画本预测"模型**
- 豆包在 TTS 之上加了一层"画本预测",从对白预测 BGM/SFX/环境音。
- 现有 `sound_design_extraction.py` 已经是这个方向,但仅用规则回退 + LLM,没豆包那种专门的画本模型。**短期内不必要**。

**P2:Audible 级别元数据**
- 章节扉页(短静音 + 章节序号语音提示)、版权声明、收尾静音、chapters.xml。
- 对接 Audiobookshelf / Apple Books 的 `Audible.chapters` 元数据。

### 4.3 不必参考的(避免走弯路)

- **ebook2audiobook 的 11 个 TTS 后端**:工程量极大但对长篇中文小说来说,Bark/VITS/YourTTS 的中文质量远不如 MiniMax/百炼。不建议照搬。
- **Audiblez 的极简单 narrator**:明显达不到多人配音需求。
- **ElevenLabs 的 SSML 嵌套方案**:`minimax_contract.py:33-87` 没用嵌套 SSML,用内联标签;不要被 ElevenLabs 风格带歪。

---

## 5. MiniMax + 阿里百炼 TTS 能力边界(2026-07 视角)

### 5.1 MiniMax TTS(MiniMax)

| 能力 | 模型 | 代码中是否启用 | 备注 |
|------|------|---------------|------|
| T2A HTTP 同步 (≤10K 字) | speech-2.8-hd/turbo、2.6-hd/turbo、02-hd/turbo、01-hd/turbo | ✅ | `minimax_adapter.py` |
| T2A HTTP 异步 (≤1M 字) | 同上 | ✅ | `minimax_adapter.py` |
| **T2A WebSocket streaming** | 同上 | ❌ 没用 | 关键能力,降首包延迟 |
| **19 interjection 标签** | 仅 speech-2.8-hd/turbo | ⚠️ 映射了,没注入文本 | `(laughs)` `(sighs)` 等 |
| **9 原生情绪** | speech-2.6+/2.8 | ✅(映射) | 9 个 emotion 标签 |
| 25 语言 + language_boost | 全部 | ✅ | 16+ 语言别名映射 |
| Voice Clone | 全部 | ✅ | 本地参考音频上传 |
| Voice Design | 全部 | ✅ | 自然语言描述 |
| Music Generation | music-3.0/2.6 | ✅(走 sound_generation) | |
| 字符级时间戳 | 全 | ❌ **只返回音频,不返回时间戳** | 需 ASR 强制对齐 |
| 30KHz / 24KHz 输出 | 全部 | ✅(16K/24K/32K/44.1K) | 商业建议 24K 起 |

### 5.2 阿里百炼 TTS

| 能力 | 模型 | 代码中是否启用 | 备注 |
|------|------|---------------|------|
| 同步合成 | qwen-audio-3.0-tts plus/flash、cosyvoice-v3.5/3 plus/flash/v2 | ✅ | `dashscope_adapter.py` |
| 流式合成(WebSocket) | 全部 | ❌ **没接 WebSocket,只走 HTTP** | 关键能力 |
| **自然语言 instruction 情感控制** | qwen-audio-3.0、cosyvoice-v3.5、qwen3-tts | ⚠️ JSON profile 写了,合成层没透传 | "以愤怒但克制的语气" |
| **AIGC inline vocal tag** `[laughing]` `[sighing]` | qwen-audio-3.0、cosyvoice-v3 plus/flash、cosyvoice-v2 | ⚠️ adapter 里有 tag 映射,合成层没注入 | |
| 9 种方言 + 9 语种 | cosyvoice-v3.5/v3 | ✅ | 完整方言表 |
| Voice Clone | 全部 + voice-enrollment 端点 | ✅ | |
| Voice Design | cosyvoice-v3.5、qwen3-tts-vd | ✅ | |
| 0.8~1.5 CNY / 10K 字 | 全部 | ✅(价格表) | 商业成本可控 |
| 字级时间戳 | Qwen3-TTS 部分模型 | ❌ 不返回,需 ASR 强制对齐 | |

**总结**:两家平台都"够用",**问题不在平台,在 Novel Forge 的合成层没接**。

---

## 6. 改进计划(分 4 个里程碑,不写代码)

> 每个里程碑都给"做什么 / 不做什么 / 验收标准 / 风险",**不改架构**,只让已有组件跑起来 + 补 4 个 P0 缺失。

### M0 现成能力落地(1-2 周)

**目标**:把"已建模但没拼装"的能力接上,**听感立刻上一个台阶**。

| # | 任务 | 涉及文件 | 验收 |
|---|------|---------|------|
| M0-1 | MiniMax 19 interjection 标签中文注入 | `spoken_text_rewrite.py` | 中文叹/笑/咳嗽等表达 → `(sighs)` 准确率 ≥85% |
| M0-2 | 百炼 instruction 情感透传 | `_build_tts_request` | emotion="angry" + tender preset → 实际 `instruction` 字段包含"愤怒但克制"语义 |
| M0-3 | MiniMax WebSocket streaming 接通 | `minimax_adapter.py` | 首包延迟从 ≥3s 降到 ≤800ms |
| M0-4 | 百炼 WebSocket streaming 接通 | `dashscope_adapter.py` | 同上 |
| M0-5 | 强制 24kHz 起步输出采样率 | 装配层 | 全部输出 ≥24kHz |
| M0-6 | 章节扉页 / 收尾静音自动注入 | `assemble_audio_step.py` | 章节前 200ms 静音 + "第 N 章"语音提示(可选) |

**不做**:重写 LLM prompt / 改 schema / 改架构。

**风险**:M0-1 的中文表达 → 标签的查表要做到 LLM 无关,否则 LLM 不稳定。

### M1 商业级质量门(3-4 周)

**目标**:补齐 P0 缺失,产出"商业可上架"的有声书。

| # | 任务 | 涉及 | 验收 |
|---|------|------|------|
| M1-1 | **Speaker Diarization 链路** | 新建 `pipeline/diarization_step.py` + 集成 `pyannote-audio` 3.x + `ECAPA-TDNN` | 长篇章(>5 万字)说话人聚类 F1 ≥0.85 |
| M1-2 | **强制字/词级时间戳回归门** | `align_speech_timeline_step.py` 改为对每段强制 | 每段都有字级时间戳,字错率 CER ≤3% |
| M1-3 | **角色别名持久化** | `VoiceTeamContract` + `voice_library` | 同一角色 10 章内音色 embedding 余弦相似度 ≥0.85 |
| M1-4 | **角色一致性长期监测** | 新建 `voice_consistency_monitor.py` | 每章合成后跑一次 ECAPA-TDNN 比对,超阈值告警 |
| M1-5 | **听感门(UTMOS / 主观)硬门** | `audio_quality.py` | UTMOS ≥4.0(MOS 预测)才允许 final assemble |
| M1-6 | **AIGC 合规水印** | `minimax_adapter.py:113-115` | 默认开启 MiniMax AIGC 水印 + 输出文件元数据 |
| M1-7 | **多轨混音统一上采样 + 边界平滑** | `multitrack_renderer.py` | 段间衔接不可感知(< 50ms 静音) |
| M1-8 | **M4B 章节输出** | 新建 `services/m4b_export.py` | 商业播放器(Audiobookshelf / Apple Books)可识别 |

**不做**:重写脚本生成阶段 / 重做 provider 抽象。

**风险**:M1-1 依赖 pyannote-audio 模型下载和 HF token,需要预下载机制;M1-2 的 WhisperX 在 macOS MPS 上速度较慢,需提前压测。

### M2 大规模生产(2-3 周)

**目标**:单书 500 章 / 1000 段/章 规模下稳定运行。

| # | 任务 | 涉及 | 验收 |
|---|------|------|------|
| M2-1 | **批处理/队列化** | `workspace/tts_ops/execution.py` + 引入 Celery/Arq | 单章失败不影响其他章 |
| M2-2 | **多书并行调度** | 同上 | 显存/GPU 利用率 ≥80% |
| M2-3 | **跨书角色复用** | `voice_library` | 跨书的常用角色音色秒级复用 |
| M2-4 | **成本看板** | `runtime/budget.py` | 月预算 / 单书预算 / 实时剩余可查 |
| M2-5 | **重渲染管线**(同脚本 + 不同平台/参数) | 新建 `rerender_pipeline.py` | 重渲染单章 < 5min |

**不做**:改 provider 接口 / 改 schema。

**风险**:M2-1 引入 Celery 等会增加运维,需要评估是否上 K8s;M2-3 跨书复用涉及授权,需先解决合规。

### M3 平台化(4 周+)

**目标**:可对外提供"AI 多人配音 API"。

| # | 任务 | 涉及 | 验收 |
|---|------|------|------|
| M3-1 | **作者工作流 Studio** | 已有部分,补"全章 dry run + 逐段标记修复" | |
| M3-2 | **听众人格化**(语速/背景音量/跳过) | 渲染参数外部化 | |
| M3-3 | **BPO 协作工具**(人工修音导出 Audacity 项目) | 借鉴 Alexandria 的 LOF 导出 | |
| M3-4 | **A/B 评测闭环** | 引入 CMOS 评分 + auto-eval | |
| M3-5 | **Audible 商业提交打包** | Audible.acx 格式 | |
| M3-6 | **多语言有声**(英文版/日文版) | pipeline 已支持 language_boost,需要 L10n 改造 | |

**不做**:本地 GPU 大模型自研 / 训练自家 TTS(用平台的就够)。

---

## 7. 关键风险与依赖

| 风险 | 影响 | 缓解 |
|------|------|------|
| **MiniMax / 百炼 服务可用性** | 合成链路中断 | 已有的 `TTSProviderFailurePolicy` + `CircuitBreaker` 已经做了故障熔断;M0 期间再补"双平台 A/B 自动回退" |
| **百炼 Qwen3-TTS 锁定模型后才能用** | 跨模型不可迁移 | 在 schema 阶段已建模 `(model_id, voice_id)` 绑定,新增一键同步 |
| **AIGC 平台合规收紧** | 不能商用 | 已有水印开关,但需引入内容审核中间件 + 平台授权清单 |
| **长篇章 GPU 资源**(M2) | 显存爆 | 已经用 `LocalResourceRequest` broker;M2 加 min-cut 调度 |
| **Mac MPS 兼容性**(WhisperX / pyannote) | 速度慢 | 已有 CPU fallback,M1 阶段压测 |
| **角色克隆侵权** | 法律风险 | 已有 `is_expired` / 复用审核;M1 加"声音产权"维度 |
| **参考音频质量** | 听感差 | 录音建议已经写到 `voice_matching.py`;M1 加自动质检(EBU R128 噪声门限) |

---

## 8. 实施优先级(再压缩)

如果时间紧,**只做这 6 件事就能上一个档次**:

1. **M0-2**(百炼 instruction 透传)—— 1 天,听感立刻不同
2. **M0-1**(interjection 注入)—— 2 天,对话立刻"活"起来
3. **M0-5**(强制 24kHz)—— 0.5 天,商业底线
4. **M1-2**(强制字级时间戳)—— 3 天,系统性错字可发现
5. **M1-1**(Speaker Diarization)—— 1 周,角色划分准确率 5-15% 提升
6. **M0-6**(AIGC 水印)—— 0.5 天,合规底线

预计 **2.5 周**完成,商业级多人配音有声书即可上架。

---

## 附录 A:核心证据索引(快速回查)

| 主题 | 文件:行 |
|------|---------|
| TTS 模块总览 | `tts/AGENTS.md` |
| 工作区主入口 | `workspace/tts_ops/execution.py:5012` |
| 脚本编排 7 阶段 | `tts/pipeline/script_orchestrator.py:43-181` |
| 脚本-正文一致性 | `tts/script_integrity.py` |
| Provider 抽象 | `tts/gateway/base.py:25-180` |
| MiniMax 能力契约 | `tts/platform/minimax_contract.py:33-200` |
| 百炼 能力契约 | `tts/platform/dashscope_contract.py:33-90` |
| MiniMax 适配器 | `tts/gateway/adapters/minimax_adapter.py:74-200` |
| 百炼 适配器 | `tts/gateway/adapters/dashscope_adapter.py:40-200` |
| 合成调度(并发+resume+DLQ) | `tts/pipeline/synthesize_audio_step.py:371-580` |
| interjection 标签映射(没注入) | `tts/pipeline/synthesize_audio_step.py:80-100` |
| 表达补偿 profile | `tts/platform/expression_profiles/bailian.json` |
| 音色-角色匹配 | `tts/assets/voice_matching.py:90-200` |
| 音频质量门 | `tts/audio_quality.py:23-260` |
| 装配层 + 多轨渲染 | `tts/pipeline/multitrack_renderer.py` |
| Sidecar 列表 | `tts/sidecars/` |
| 自动化模式 | `tts/services/automation.py` |
| 成本预算 | `tts/runtime/budget.py` |
| Studio 后端 | `tts/services/studio_service.py` |
| 声音生成服务 | `tts/sound_generation/service.py:58-120` |
| API 端点 | `api/routes/tts.py:184-723` |

## 附录 B:建议的下一份报告(可选)

如果 M0 实施后,建议再写一份"实际听感测评报告"——随机抽 10 章,跑:
- 主观 MOS 评分(邀请 5-10 人盲听)
- UTMOS 客观评分
- 对齐覆盖率(whisperX 重对齐)
- 字错率 CER
- 拼接平滑度(分段点静音时长)
- 角色一致性(ECAPA-TDNN embedding 距离)

作为 M1 的入门数据。
