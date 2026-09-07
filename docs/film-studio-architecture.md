# 映界：小说 × 声腔 × 影视一体化创作架构

“映界”是 Novel Forge 的商业影视创作工作台。它不把影视当作小说写完后的单次视频按钮，
而是让可跨媒介复用的信息尽可能在故事与角色初始化阶段产生，再由小说、声腔和影视分别投影。

## 上游优先的共同事实源

跨媒介资产的所有权遵循“越稳定、越昂贵、越难返工，越应该靠近上游”的原则：

| 共同信息 | 权威上游 | 小说消费 | 声腔消费 | 映界消费 |
| --- | --- | --- | --- | --- |
| 人物身份、弧光、语言习惯 | `character_bible.json` | 人物行动与对白 | 台词归属、表演语气 | 剧本角色与镜头行动 |
| 银幕身份锁 `visual_identity` | `CharacterProfile` | 稳定外貌描写 | 宣传图与角色卡 | 三视图、主体参考、跨镜身份 |
| 声学定位 `tts_voice_hints` | `CharacterProfile` | 声音意象 | 选声、克隆、声音设计 | 表演锁与音画同步提示 |
| 已批准 voice/model | `tts/voice_team.json` | 不反写故事事实 | 实际合成 | 成片角色声线继承 |
| 结构化场景 `locations` | `StoryBible` | 空间动线与连续性 | 环境声、混响与声场 | 美术、灯光、天气、道具、镜头轴线 |
| 文学风格与声音美学 | style / audio creative bible | 文体、意象、节奏 | 音色、音乐与混音 | 色彩、光线、镜头、声音论述 |
| 影视执行锁与审核结果 | `production_bible.json` | 可作为后续衍生版参考 | 配音重制可读取 | 直接权威源 |

`CharacterProfile.visual_identity` 包含面部锚点、轮廓、肢体语言、服装色板、标志道具、
连续性规则和禁止漂移项；`tts_voice_hints` 区分语言习惯 `voice` 与可匹配声音的音色、音域、
节奏、口音、情绪范围、发音备注。`StoryBible.locations` 把空间布局、材质、实景光、天气、
固定道具、环境声和连续性规则一次生成。这样声腔不需要从散文猜声场，映界也不需要从成稿
反推人物脸、服装和空间结构。

影视启动时，`FilmSourceProjector` 对上述源文件做带 revision 的有界投影，生成项目根目录下的
`production_bible.json`。刷新上游时保留用户已经锁定的选片和镜头决定，避免一次同步抹掉人工工作。

## 九阶段商业流程

1. **策划**：确认改编范围、制式、观众、故事来源与制作约束。
2. **剧本**：把章节/场景计划转成可拍摄场次；每场保留目标、冲突、转折、视觉钩子、声音钩子和来源引用。
3. **视觉开发**：角色身份设定表、场景环境表、关键道具、色彩与光线方案；先选片并锁定，再进入高成本镜头。
4. **分镜**：每场默认四类基础镜头，使用景别、角度、运动、光线、情绪、时间六轴摄影语言，并记录镜头、构图、焦点和时长。
5. **镜头生产**：依据首帧/尾帧、主体参考和平台能力选择 T2V、I2V、FLF、R2V 或 S2V；异步任务可查询、可恢复、可重试。
6. **声画**：继承声腔对白成片、环境声与音乐方向，建立对白、环境/音效、音乐轨。
7. **剪辑**：把已审核镜头写入画面轨，校验时长、转场、连续性和声画关系。
8. **合规**：查看合规报告和交付前检查状态。
9. **交付**：导出 OpenTimelineIO，保留镜头与上游来源 lineage，供 NLE/VFX/调色链继续使用。

长篇项目不把全部资产堆在一张画布。可操作层级固定为
`小说卷/章 → 影视单元/场次 → 镜头 → 版本化资产 → OTIO 时间线`：主工作台只负责阶段、场次、镜头调度，
角色、场景、道具等卡片点击后进入独立资产页，在下一层完成候选版本对比、平台/模型路由、QC、选片和锁定；
返回工作台后只带回已批准版本与依赖失效状态。

协作模式每推进一阶段停下等待用户；AI 自主模式可完成策划、剧本、资产规划与分镜，遇到真实
付费镜头生成时强制阻塞，必须获得明确授权。任何模式都不把“生成成功”视为“审核通过”；候选、
选中、锁定和 QC 状态分别持久化。

## 平台能力适配

能力目录由后端单一来源提供给 UI，避免界面展示平台不支持的组合。

- 阿里百炼：Wan 2.7 Image / Image Pro 顺序图集与编辑；Qwen Image 2.0 Pro；Wan 2.7 T2V、
  I2V/首尾帧/续写、R2V 多主体/分镜/声音参考。官方资料：
  [图像生成与编辑](https://help.aliyun.com/en/model-studio/wan-image-generation-and-editing-api-reference)、
  [文生视频](https://help.aliyun.com/en/model-studio/text-to-video-api-reference)、
  [图生视频](https://help.aliyun.com/en/model-studio/image-to-video-general-api-reference)、
  [视频生成与编辑](https://help.aliyun.com/en/model-studio/wan-video-to-video-api-reference)。
- MiniMax：Image-01 主体参考；Hailuo 2.3 / Fast；Hailuo 02 首尾帧；S2V-01 人物主体参考；
  声音继续沿用声腔中的 Speech 2.8 与 Music 3.0 资产。官方资料：
  [图像生成](https://platform.minimax.io/docs/guides/image-generation)、
  [视频生成](https://platform.minimax.io/docs/guides/video-generation)。
- 火山方舟：Seedream 5.0 / 4.5 用于文生图、参考图和成组顺序图；Seedance 2.0 / Fast 用于异步
  文生视频、图生视频、带音频生成和尾帧回传；受信任演员资产使用 `asset://` 引用。官方资料：
  [图片生成 API](https://api.volcengine.com/api-docs/view?action=ImageGenerations&serviceCode=ark&version=2024-01-01)、
  [视频生成任务](https://api.volcengine.com/api-explorer/?action=CreateContentsGenerationsTasks&groupName=视频生成API&serviceCode=ark&version=2024-01-01)、
  [查询视频任务](https://api.volcengine.com/api-docs/view?action=GetContentsGenerationsTask&serviceCode=ark&version=2024-01-01)。

供应商层统一为 `FilmGenerationRequest → FilmProviderTask`。平台原始响应被保留在 task 中，
业务层只消费统一的模式、状态、任务 id、候选 URL、错误和 trace id。

## 开源架构借鉴与落地

- [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO)：采用
  Timeline → Track → Clip 的剪辑交换模型，并把 `production_bible.json` 和 shot id 写入 metadata。
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI)：借鉴节点图、队列、history 和可恢复执行思想；
  映界的 `run_plan` 是有依赖和人工门槛的 DAG，但不把生成供应商与 UI 节点耦合。
- [Radix UI](https://www.radix-ui.com/primitives/docs/overview/introduction)：用无样式、可访问的 Select、Popover
  承担平台/模型选择和全局主题控制，视觉仍由 Nimo token 管理，后续可跟随上游升级而不重写交互语义。
- [TanStack Table](https://tanstack.com/table/latest)：镜头生产表使用无头表格内核承载列、行与选择状态，
  后续加入虚拟滚动、排序、过滤、批量生产时不需要推翻镜头数据模型。
- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)：借鉴脚本、素材、配音、字幕、
  合成的分层流水线；映界进一步增加角色/空间身份锁、专业分镜、选片审核和 OTIO 交付边界。
- `Reference/影视模块` 的短剧编剧、影视资产和分镜技能：吸收了可恢复阶段状态、角色/场景/道具资产、
  多视图身份锁、六维镜头语言、钩子与合规检查；参考目录保持只读，生产实现落在 `novel_forge/film`。

这些项目提供稳定的升级边界而非业务代码拷贝：Radix/TanStack/OTIO/可恢复 DAG 分别负责通用交互、
数据表、剪辑交换和运行编排；人物身份锁、声纹继承、场景空间连续、供应商路由与审核规则仍由 Novel Forge
拥有。映界沿用现有原子持久化、运行时依赖、FastAPI 契约和 Nimo 设计系统，避免引入第二套状态源。

## 持久化与恢复

```text
<project>/
├── production_bible.json
└── film/
    ├── studio_state.json
    └── exports/
        └── master_timeline.otio
```

`studio_state.json` 保存当前阶段、资产候选、人工选择、镜头、供应商任务、DAG、决策和时间线。
进程退出后可继续查询未完成任务；上游刷新通过 source revision 判断变化，并合并已锁定工作。

## API 边界

- `GET /api/v1/film/catalog`
- `GET|POST /api/v1/film/projects/{project_id}` / `bootstrap`
- `POST /advance`
- `POST /assets/{asset_id}/generate|select`
- `PATCH /shots/{shot_id}`
- `POST /shots/{shot_id}/generate`
- `POST /media/{target_id}/query`
- `POST /export-otio`

Nimo 桌面端通过 `packages/engine-contracts` 的类型化客户端调用这些边界；“映界”入口固定在“声腔”下方。


## 2026-09-07：画布导航可发现性修复

画布组件未被删除：此前仅在 planning 的工作台中可见，其他阶段（例如视觉资产）没有直接入口。现于九阶段栏旁固定提供“专家画布”，复用策划工作台并明确切换专家模式；同页重复打开也生效。专注模式继续隐藏阶段导航，退出恢复。只调整 React 导航，不更改 Engine 阶段、任务执行权限或 PySide。

验证：TypeScript 检查、3 个相关 Vitest 文件共 31 项测试通过；浏览器使用离线 Mock 完成画布入口、模式切换、全图适配与返回视觉阶段验证，错误/警告日志为空。未做真实模型调用或原生打包验收。依赖 `53fb375c`；回退本批代码与展示文档即可，不删除作品、图定义、候选或费用记录。
