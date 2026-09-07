# 生成式声音资产架构

从定稿正文、风格画像、配音指导、双对齐到商业母带的完整生产边界见[商业有声书声腔与音频交付架构](commercial_audiobook_pipeline.md)。

配音工作室把角色语音、背景音乐、环境声和短音效分成独立轨道。TTS 只负责旁白与对白；声音资产由本地素材库优先匹配，只有缺失提示才进入生成式音频 Provider。

## 目录与职责

```text
novel_forge/tts/sound_generation/
├── models/catalog.py              # 模型能力、许可提示、集成状态的唯一目录
├── model_manager.py                # Stable Audio 受管缓存：检测、下载、删除
├── providers/base.py              # Provider 接口
├── providers/minimax_music.py     # MiniMax Music 纯伴奏 API
├── providers/stable_audio_cli.py  # Stable Audio 3 本地 CLI
├── providers/registry.py          # 自动路由与配置绑定
├── schemas.py                     # 请求、生成结果、可审计章节报告
└── service.py                     # 先匹配、后生成、写入资产库的编排

应用级资源库（所有项目共享）
├── models/audio/                  # 源码运行：仓库根目录，已被 Git 忽略
│   ├── plugins/<plugin>/<revision>/ # Qwen / Sherpa / VAD 等经校验权重
│   ├── stable-audio/                # Stable Audio / Hugging Face 缓存
│   ├── inventory.json              # 安装版本、路径和许可确认
│   ├── runtimes/<runtime>/versions/ # 可升级/回滚的独立 Python 环境
│   ├── downloads/tasks/             # 可跨重启继续的下载任务
│   └── locks/                       # 模型、运行时和迁移的跨进程锁
└── assets/audio/
    ├── sounds/                   # 作者发布的可复用 BGM、环境声、SFX
    └── sound_library.json         # 应用资源库的标签、授权和生成溯源

data/<project>/tts/
├── assets/                        # 作者导入与可复用声音资产
│   └── generated/<kind>/          # 生成资产，与导入文件隔离
├── sound_library.json             # 可混音资产及审核/溯源信息
├── sound_resolutions/             # cue → 已批准资产的匹配报告
└── sound_generation/              # 每章生成尝试、失败与缓存记录
```

模型权重不写入项目目录。应用模型中心托管 Qwen、Sherpa、VAD 和 Stable Audio 权重，独立推理运行时仍与主进程隔离；项目只保存本作品的生成资产、所用模型 ID/版本和可复现的请求指纹。源码运行统一使用仓库根目录的 `models/`（已被 Git 忽略）；冻结桌面版使用用户可写的 Novel Forge 应用数据目录。两者都避免模型随项目切换或删除而丢失。

项目资产不会自动外溢。作者在“作品与应用声音资源库”中批准候选后，可显式点击“发布到应用库”；系统复制文件、标签、授权说明和生成溯源到应用库。之后章节解析先匹配当前项目资产，再匹配应用资产，保证作品专属声音优先，同时允许雨声、门响、通用氛围和已授权 BGM 跨项目复用。

## 默认路由

- BGM 与长环境声：第一阶段只在有 MiniMax Key 时使用 `music-2.6` 的无歌词模式；未配置时明确报告缺少 Key，不会悄然回退到本地音乐模型。
- 短 SFX：使用本地 Stable Audio `small-sfx`。
- `small-music`、ACE-Step 1.5 与 AudioCraft/MusicGen 已登记在模型目录中；前者留作下一阶段本地 BGM，后二者的适配器状态为 `adapter_required`。实现对应 Provider 后即可加入，无需修改章节流水线。

## 远程 TTS 限流

“最大并发合成数”只限制同时执行的片段数，不能代表云端服务商允许的请求速率。配音工作室会对远程 TTS 均匀排队，默认不超过 45 RPM，且重试也计入额度；服务商返回限流后，整条队列默认冷却 60 秒。可在“配音工作室 → 平台设置 → 输出与运行 → 合成性能与输出”调整：

```bash
NOVEL_FORGE_TTS_MAX_CONCURRENT_SYNTHESIS=4
NOVEL_FORGE_TTS_SYNTHESIS_REQUESTS_PER_MINUTE=45
NOVEL_FORGE_TTS_SYNTHESIS_RATE_LIMIT_COOLDOWN_S=60
```

若 Provider 套餐的 RPM 更低，应将 `SYNTHESIS_REQUESTS_PER_MINUTE` 设置为低于该限额的值，而不是单独提高或降低并发数。

`SoundGenerationRegistry` 根据声音角色、配置和可用 Key 选择 Provider。每个 Provider 仅返回字节与元数据，写文件、审核和缓存全部由 `SoundGenerationService` 统一处理。

## 本地 Small-SFX 管理

“配音工作室 → 平台设置 → 声音资产生成”复用了 Ollama 的管理交互：后台检测、模型卡片、下载状态、打开目录、选择存储位置和确认删除。第一阶段只暴露 `small-sfx`；模型目录在配置中作为 `HF_HOME` 传给 `stable-audio`，因此 UI 下载和生成 CLI 使用同一份应用级缓存，不随项目切换。

```bash
NOVEL_FORGE_SOUND_GENERATION_STABLE_AUDIO_COMMAND=stable-audio
NOVEL_FORGE_SOUND_GENERATION_STABLE_AUDIO_MODELS_DIR=~/.novel-forge/audio-models
NOVEL_FORGE_SOUND_GENERATION_HUGGINGFACE_TOKEN=hf_...
```

下载模型与安装运行时是两件事：应用会管理 Hugging Face 缓存，但不会把 PyTorch 或数 GB 的 Stable Audio 运行时打进桌面安装包。先在 [Stable Audio 3 官方仓库](https://github.com/Stability-AI/stable-audio-3) 安装运行时并将其 `stable-audio` 命令配置到设置页；再到 [Small-SFX 模型页](https://huggingface.co/stabilityai/stable-audio-3-small-sfx) 登录、接受 Stable Audio 与 Gemma 条款，并填写具有访问权限的只读 Hugging Face Token。删除仅会清理当前受管目录中该仓库的缓存，永不触碰已生成的章节声音资产。

## 审核、缓存与版权记录

每个生成资产写入 `SoundAsset`，包含 Provider、模型、提示词、生成请求哈希、生成时间、来源、听感审核状态和独立的商业使用状态。请求哈希相同且文件存在时直接复用；`pending` 或 `rejected` 资产不会进入正式混音。

听感审核与发行权不是同一件事：`approved` 只表示声音可用；商业/Master 交付和发布到应用库还要求 `commercial_use_status=cleared`，并记录非空的授权依据/条款备注。`review_required` 会在商业档形成硬阻断，`restricted` 永远不能进入商业交付。切换模型、账号套餐或素材来源后，应重新核对权利状态，不能沿用旧结论。

上层统一使用三种配音推进模式；平台设置是 API、后台任务和新项目的默认值，
Voice Studio 可为当前项目临时覆盖：

- `manual`（全人工）：不自动创建或批准声音资产，只使用作者已准备并批准的内容。
- `assisted`（AI 伴随，默认）：AI 生成缺失候选，但保留试听/批准关口。
- `autonomous`（全 AI 自主）：AI 生成并批准章节所需资产，继续混音与质检；任何未解析 cue 仍会阻止交付。

自主模式也不会越过说话人安全门：对白或内心独白无法唯一确认角色时，
流水线返回 `tts_speaker_review_required` 并保留原脚本，绝不将该台词静默改为旁白。

```bash
NOVEL_FORGE_TTS_AUTOMATION_MODE=assisted
```

`SOUND_GENERATION_ENABLED`、`AUTO_GENERATE`、`AUTO_APPROVE` 三个旧开关会由模式统一派生，
仅保留用于兼容旧配置和底层诊断。只有团队接受“生成后直接进入混音”的工作流时才使用
`autonomous`。发布前仍须根据实际 Provider、模型权重和素材许可证审查商业使用权利。

## 配音脚本专业审校

脚本生成后会依次经过确定性安全审校和独立路由的配音导演审校，再进入合成：

- 正文 `text` 永不在配音阶段改写，避免有声书与定稿、字幕和源哈希分叉。
- 复用小说 `HumanizeScanStep` 检查实际朗读文本；若短句 `spoken_text` 新增 AI 式重复、
  模板连接或确定性质量回归，自动撤回到源台词。正文本身的命中只记录建议，不在音频流水线偷改小说。
- 清除 AI 自动填写的语速、音量、音高和句内速度曲线。情绪不再通过“悲伤=减速”模拟；
  MiniMax Speech 2.8 使用受控的原生情绪枚举、官方语气词与停顿，数值参数只保留给人工试听后调整。
- 副语言动作必须有原文或导演描述证据，单句最多保留两个；不向 MiniMax 发送官方合约未列出的标签。
- 高置信可听事件（玻璃碎裂、敲门、开关门、来电、雷声、枪声、重物落地）
  自动补为短 SFX，用片段锚定和句内偏移定位；其他事件仍由 AI 建议或作者在声场编辑器中确认。
- `TTS_REVIEW_DUBBING_SCRIPT` 是独立模型任务，可在 Voice Studio「平台设置 → TTS 文本模型路由」
  单独选择主模型与回退链。它从声音角色、说话人、潜台词、情绪意图、可演性和 TTS 稳定性复核整章。
- 小说 `HumanizeScanStep` 只投影候选信号：助手/Markdown 残留等媒介无关问题可形成正文建议，
  三项列举、句长节奏、说教感等小说规则不会直接判成配音缺陷，也不会触发表演改写。
- 配音导演审校只自动应用有逐字证据的情绪、短语气提示和副语言修复；任何声音角色或说话人疑点
  都进入人工复核并加入合成安全门，绝不会把疑似对白静默交给旁白。

## 混音

完整模型编排、sidecar 契约和扩展方法见[能力驱动的音频插件平台](audio_plugin_platform.md)。

正式流程会先合成人声，再以 ASR 核验和已知文本强制对齐生成 `SpeechTimeline`。脚本中的段落、文本和语义 cue 随后才解析为绝对时间，BGM、环境声和一次性 SFX 因而进入真实的说话、停顿和转场位置。

声音资产仍采用“项目库优先、应用库其次、缺失再生成”。同一章内，不同名称或不同叙事功能的
BGM cue 不再默认命中同一首资产；只有同名主题/动机复现允许复用，否则选择其他已批准候选或生成新候选。

装配阶段先生成可审计的 `MixPlan`，保持 voice、BGM、soundscape、SFX 四类 stem 独立；FFmpeg 用同一个 filtergraph 完成延时、裁剪、淡入淡出、总线混合、人声侧链 ducking 和响度标准化，并只编码一次。旧式顺序混音仅作为渲染失败时的兼容回退。

正式母带先将上述多轨 filtergraph 渲染为 24-bit PCM premaster，第一遍 `loudnorm` 只测量综合响度、LRA、真峰值和阈值，第二遍使用实测参数编码最终 MP3。因此虽然是两遍母带，仍然只有一次有损编码，避免多次转码累积损失。商业档还从同一次 premaster 渲染导出 `voice`、`bed`、`sfx` 三类 24-bit WAV 分轨，并随有声书包交付，避免为分轨重复执行事件图或产生与 Master 不一致的素材版本。

一次性 SFX 若与真实人声区间重叠，MixPlan 会自动预留 2 dB 头部空间，并在渲染时使用独立的人声侧链进一步压低；位于停顿和转场区间的 SFX 保持脚本增益。这使“门响、爆炸、掌声”等元素依据实际人声位置动态融合，而不是静态叠加。
