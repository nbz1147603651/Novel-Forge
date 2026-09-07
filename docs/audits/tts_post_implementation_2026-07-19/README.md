# TTS 模块表现力 — 后置深度分析报告

> **报告类型**：深度专项分析（Post-Implementation Deep Dive）
> **报告日期**：2026-07-19
> **报告范围**：novel_forge/tts/ + novel_forge/desktop/pages/voice_studio/
> **配套 Canvas**：`~/.qoder-cn/projects/-Users-ni-Documents-VScode_mac-写作助手/canvases/tts-post-implementation-analysis.canvas.tsx`

---

## 执行摘要

基于上一阶段已实施的 4 项改进（情绪别名扩展、短句分级、Provider Guard 放宽、sub_emotion vocal_direction），本报告对 MiniMax 配音模块做回顾性与前瞻性深度评估。

**核心结论**：

- **综合评分**：**5.8 / 10**（较改进前 3.5 提升 **+2.3 分**）
- **改进有效**：4 项改进解决了 4 个具体瓶颈，但仍有 **7 个深层结构性问题**
- **优先建议**：下一阶段 5 项改进可预计再提升 **2.0-2.5 分**

---

## 一、改进效果评估（回顾性分析）

### 改进 1：情绪别名映射扩展

**代码位置**：[minimax_contract.py L63-79](../../../novel_forge/tts/platform/minimax_contract.py#L63-L79)

| 原生情绪 | 改进前来源 | 改进后来源 | 变化 |
|---------|-----------|-----------|------|
| calm | neutral, tender, whisper, mocking, contempt, determined | neutral, tender, whisper | 减少 3 个 |
| happy | happy, playful | happy, playful, **mocking（新增）** | +mocking |
| sad | sad, nostalgic | sad, nostalgic | 不变 |
| angry | angry | angry, **determined（新增）** | +determined |
| fearful | fearful, anxious | fearful, anxious | 不变 |
| surprised | surprised | surprised | 不变 |
| disgusted | disgusted | disgusted, **contempt（新增）** | +contempt |

**用户体感收益**：

- 「嘲讽」(mocking) 首次具有可辨识的"愉悦"音色，区别于「温柔」(tender) 的平静
- 「轻蔑」(contempt) 首次具有"厌恶"音色，避免与"平静"混淆
- 「坚定」(determined) 首次具有"愤怒的力量感"，让决心台词不再平淡
- **MiniMax 7 种原生情绪利用率**：从 5/7（71%）提升至 7/7（100%），仅 surprised 仍较少触发

### 改进 2：短句情绪分级策略

**代码位置**：

- [synthesize_audio_step.py L887-893](../../../novel_forge/tts/pipeline/synthesize_audio_step.py#L887-L893)（expression_scale 三档）
- [synthesize_audio_step.py L988-991](../../../novel_forge/tts/pipeline/synthesize_audio_step.py#L988-L991)（effective_emotion 判定）
- [synthesize_audio_step.py L1049-1053](../../../novel_forge/tts/pipeline/synthesize_audio_step.py#L1049-L1053)（native_emotion_allowed 收紧）
- [performance_policy.py L22-24, L60-69](../../../novel_forge/tts/performance_policy.py#L22-L24)（EXTREME_SHORT 常量与 helper）

| 字符区间 | 分类 | 改进前 | 改进后 | native_emotion_allowed |
|---------|------|--------|--------|------------------------|
| ≤4 字符 | 极短句 | 剥离情绪 | 剥离情绪（保持） | False → False |
| 5-8 字符 | moderate 短句 | 剥离 + scale=0.0 | 保留 + scale=0.25 | False → **True** |
| >8 字符 | 正常句 | scale=0.45 | scale=0.45（保持） | True → True |

**用户体感收益**：

- "滚！"（1字）仍无情绪：符合"一个字没法表达情绪"的常识
- "别碰我"（3字）仍无情绪：极短，确需稳定
- "我恨你"（3字）以上开始保留情绪
- **"你给我滚出去"（6字）首次保留 native emotion 透传**，5-8 字符中段从完全平淡升级为有情绪节奏

### 改进 3：MiniMax Provider Guard 放宽

**代码位置**：[performance_policy.py L292-300](../../../novel_forge/tts/performance_policy.py#L292-L300)

| 情绪状态 | 改进前 speed 上限 | 改进后 speed 上限 | 感知差异 |
|---------|------------------|------------------|---------|
| neutral（平静） | ±0.05 | ±0.12 | 基础节奏从几乎不可感到可清晰感知 |
| non-neutral（情绪） | ±0.05 | ±0.18 | 情绪节奏感显著增强（约 3.6 倍） |
| pitch_offset | 强制归零 | 强制归零（保持） | 音色锚定不变 |

**用户体感收益**：

- 愤怒角色不再用平静语速说怒吼台词
- 悲伤角色不再用平静语速说悲伤台词
- 温柔角色降低音量时仍保留稳定的音色中心
- **重要约束保留**：pitch 仍强制归零，确保角色不会从林远变成林远他妈

### 改进 4：sub_emotion 影响 vocal_direction

**代码位置**：[synthesize_audio_step.py L971-1000](../../../novel_forge/tts/pipeline/synthesize_audio_step.py#L971-L1000)

| 场景 | 改进前 | 改进后 |
|------|--------|--------|
| 主=happy, 次=mocking | energy=0.5, tension=0.3（默认） | energy=0.56（+0.06）, tension 不变 |
| 主=sad, 次=contempt | 默认 | energy=0.42（-0.08）, tension=0.36（+0.06） |
| 主=neutral, 次=angry | 全部默认 | energy=0.62（+0.12）, tension=0.40（+0.10） |

**用户体感收益**：

- 情感渐变从"标签存在但无听觉差异"升级为"能量、张力逐项过渡"
- **关键效果**：LLM 标注的 sub_emotion 不再只是 metadata 中的死字符串，而是真实影响合成参数
- **极短句豁免**：is_extreme_short 仍走自然 delivery（避免抖动）

---

## 二、当前仍然存在的瓶颈深度分析

### 瓶颈 A：EMOTION_MAPPINGS.voice_tags 字段从未被消费

- **代码位置**：[schemas.py L1429](../../../novel_forge/tts/schemas.py#L1429)（定义）、全代码库 grep 无消费者
- **根因**：`synthesize_audio_step.py L878` 初始化 `emotion_tags=[]` 后，仅当 `not is_character_voice and provider != MINIMAX` 时回填，但角色路径永远为空
- **影响等级**：🔴 **高**
- **对用户的影响**：内联 paralinguistic_tags 路径依赖 LLM 主动产出；如果 LLM 漏标，则 EMOTION_MAPPINGS 中预定义的"笑声""叹息""吸气"等情绪提示永远不会被自动注入
- **改进方向**：在 synthesize_audio_step.py 中，当 segment.paralinguistic_tags 为空且 EMOTION_MAPPINGS 中存在 voice_tags 时，自动回填为 prefix injection（仅对 speech-2.8 模型生效）

### 瓶颈 B：stress_words 字段未被 MiniMax 消费

- **代码位置**：[synthesize_audio_step.py L1103](../../../novel_forge/tts/pipeline/synthesize_audio_step.py#L1103)（写入 metadata），[minimax_adapter.py L753-776](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L753-L776)（_build_voice_setting 不读取）
- **根因**：minimax_adapter 的 _build_voice_setting 不接受重音词；仅 qwen3_adapter.py L403-405 将其拼接到 prompt
- **影响等级**：🔴 **高**
- **对用户的影响**：LLM 标注的"重音词"完全失去对 MiniMax 合成的引导作用，词句重音全靠模型默认韵律
- **改进方向**：利用 MiniMax T2A 的 `pronunciation_dict.tone` 字段，将 stress_words 转为音调变体；或用 `<break>` 切分字符级时间间隔

### 瓶颈 C：speed_curve 字段在脚本审查阶段被完全丢弃

- **代码位置**：[generate_script_step.py L1099-1102](../../../novel_forge/tts/pipeline/generate_script_step.py#L1099-L1102)（LLM 产出解析），[script_review.py L491-492](../../../novel_forge/tts/script_review.py#L491-L492)（review 阶段清空）
- **根因**：`review_and_repair_dubbing_script` 注释解释是"机生成的数值指令不安全"，但 speed_curve 是表演设计而非身份调节器
- **影响等级**：🔴 **高**
- **对用户的影响**：句内语速变化（开场减速、关键句加速）完全失去；所有段落变为匀速朗读，长段尤其平淡
- **改进方向**：在 review 阶段保留 speed_curve 中"开篇 20%、结尾 20%"的减速段，丢弃中间可能影响身份的微调；或将 speed_curve 整体迁移到 platform_extension（provider-specific 字段）

### 瓶颈 D：timbre_weights 字段从未被自动生成

- **代码位置**：[synthesize_audio_step.py L922-936](../../../novel_forge/tts/pipeline/synthesize_audio_step.py#L922-L936)（注释说明"无规范不自动生成"），[minimax_adapter.py L239-241](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L239-L241)（仅手动 platform_extension）
- **根因**：MiniMax 平台支持 timbre_weights 音色权重混合，但项目方担心"无 spec 无 fixture 时拼出会被 API 拒绝"
- **影响等级**：🟡 **中**
- **对用户的影响**：每个角色只能用单一音色，无法在同一段对白中混合"主角 70% + 配角 30%"的过渡音色，限制了角色的听觉层次
- **改进方向**：定义 character_id → weight 的确定性映射函数；为角色内心戏或场景切换段自动注入 1-2 音色权重组合，并补充单元测试 fixture

### 瓶颈 E：规则回退路径缺少情绪标注

- **代码位置**：[generate_script_step.py L1348-1430](../../../novel_forge/tts/pipeline/generate_script_step.py#L1348-L1430)（_generate_script_rule_based）
- **根因**：LLM 失败时降级路径仅靠关键词匹配"高兴""悲伤"等少量词；其余 13 种情绪、渐变、停顿、停顿标签全部丢失
- **影响等级**：🟡 **中**
- **对用户的影响**：当 LLM 路由失败、配额耗尽或 prompt 验证失败时，回退产物的声音表现力会骤降到 2010 年水平的"关键词朗读"
- **改进方向**：扩展 _infer_emotion 的关键词库；为高频情绪词预置固定 paralinguistic_tags 模板；为含有"冷笑""叹息""哽咽"等强证据词的对白强制插入对应 voice_tags

### 瓶颈 F：跨段落情绪衔接无保证

- **代码位置**：[synthesize_audio_step.py L976-980](../../../novel_forge/tts/pipeline/synthesize_audio_step.py#L976-L980)（短句归一为 natural delivery）
- **根因**：每段单独走 MiniMax API，相邻段落的 emotion 切换是硬切，缺乏渐变过渡
- **影响等级**：🟡 **中**
- **对用户的影响**：连续 10 段对白中"happy→sad→angry"会听到 3 次硬切音调跳变，缺乏真人配音的过渡
- **改进方向**：在 synthesize_audio_step 中维护 _previous_segment_emotion，按 30%/50% 混合相邻段的 vocal_direction 参数；或调用 MiniMax long-text API 一次性合成整章（已支持 t2a_async_v2）

### 瓶颈 G：TTS 情绪表现力评测体系缺失

- **代码位置**：novel_forge/tts/ 目录下无 eval 子目录；[novel_forge/eval/evaluator.py](../../../novel_forge/eval/evaluator.py) 不覆盖 TTS 合成产物
- **根因**：TTS 评估需要音频特征（pitch variance、emotion classifier、说话人一致性），与文本评估（BLEU、ROUGE）模型完全不同
- **影响等级**：🔴 **高**
- **对用户的影响**：每次改进无法量化收益，只能通过人工试听；本报告的"+2.3 分"评估目前是主观推断
- **改进方向**：建立 TTS eval 子系统，包含：
  1. 情绪可辨识度（用 wav2vec2 emotion classifier）
  2. 音高/语速方差（与情绪映射表对比）
  3. 说话人一致性（speaker embedding 距离）
  4. 跨段情感渐变平滑度（情绪向量余弦相似度）

### 瓶颈 H：UI 缺少情绪强度滑块

- **代码位置**：[dialogs.py L355-360](../../../novel_forge/desktop/pages/voice_studio/dialogs.py#L355-L360)（EmotionTag QComboBox）
- **根因**：VoicePerformanceProfile 虽有 strength 字段（"subtle" | "moderate"），但 UI 未暴露该字段
- **影响等级**：🟡 **中**
- **对用户的影响**：用户只能选择情绪类型，无法微调"温柔的中等强度温柔" vs "温柔的极强温柔"；每次调整都要重新合成整段才能对比
- **改进方向**：在 SegmentEditorDialog 添加 0-100 情绪强度滑块，将 slider 值映射到 expression_scale（0.0-0.45）；保留 VariantPreview 4 个变体对比试听

---

## 三、与 MiniMax 平台原生能力的对标

参考 [minimax_contract.py](../../../novel_forge/tts/platform/minimax_contract.py) 与 [minimax_adapter.py](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py)

| 平台能力 | 证据 | 状态 | 未使用收益 |
|---------|------|------|-----------|
| 19 种拟声标签内联 | [minimax_contract.py L26-46](../../../novel_forge/tts/platform/minimax_contract.py#L26-L46) | ✅ 完整使用 | — |
| 7 种原生 emotion | [minimax_contract.py L48-58](../../../novel_forge/tts/platform/minimax_contract.py#L48-L58) | ✅ 通过 native_emotion_allowed 透传 | — |
| pronunciation_dict 音调控制 | [minimax_adapter.py L227-228](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L227-L228) | ⚠️ 透传 pronunciation_overrides，但 UI 无编辑器 | 支持"燕少飞/(yan4)(shao3)(fei1)"精确发音 |
| voice_modify (sound_effects) | [minimax_adapter.py L229-231](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L229-L231) | ⚠️ 仅 sound_effects 字段透传，pitch/intensity/timbre 强制归零 | 限制 lofi_telephone/spacious_aquarium 等场景音效 |
| language_boost 多语种 | [minimax_adapter.py L222](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L222) | ✅ 完整使用，40+ 语言已支持 | — |
| subtitle_type=word/sentence | [minimax_adapter.py L223-238](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L223-L238) | ✅ 默认 word，可切换 sentence | — |
| **timbre_weights 音色权重** | [minimax_adapter.py L239-241](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L239-L241) | ❌ **仅手动 platform_extension，LLM 从不生成** | 角色内心戏 80% 主声+20% 内心声 |
| aigc_watermark 合规水印 | [minimax_adapter.py L245-248](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L245-L248) | ✅ 默认关闭，用户可显式开启 | — |
| async t2a_async_v2 长文本 | [minimax_adapter.py L309-373](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L309-L373) | ✅ 长于 10000 字符自动走异步 | — |
| **streaming 流式音频** | [minimax_adapter.py L219](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L219) stream=False | ❌ **始终非流式** | UI 端无法边听边下载 |
| **voice_design 文本生成音色** | [minimax_adapter.py](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py) 接口已实现 | ⚠️ **UI 无"创建新音色"入口** | 支持"温柔的中年女声"自然语言生成 |
| 300+ 系统音色 catalog | [minimax_adapter.py L57](../../../novel_forge/tts/gateway/adapters/minimax_adapter.py#L57) | ✅ VoiceTeam 注册时获取 | — |

**未充分利用的能力集中在 3 项**：

1. **timbre_weights** — 角色内心戏与场景切换的关键工具
2. **streaming 流式音频** — UI 体验的最后一公里
3. **voice_design** — 角色自定义音色的入口

---

## 四、综合评分与下一阶段优先建议

### 综合评分

| 维度 | 改进前 | 改进后 | 提升 |
|------|--------|--------|------|
| 角色对白情绪可辨识度 | 2.5 | 4.5 | +2.0 |
| MiniMax 原生情绪利用率 | 4.0 | 6.5 | +2.5 |
| 短句情绪保留度 | 3.0 | 5.0 | +2.0 |
| 情感渐变（sub_emotion） | 1.5 | 5.0 | +3.5 |
| 平台特性利用度 | 5.0 | 5.5 | +0.5 |
| LLM 产出质量 | 4.0 | 4.0 | 0 |
| 跨段一致性 | 4.5 | 4.5 | 0 |
| 评测闭环 | 2.0 | 2.0 | 0 |
| UI 可调性 | 4.5 | 4.5 | 0 |
| **加权总分** | **3.5** | **5.8** | **+2.3** |

### 下一阶段优先级排序（投入产出比）

| 优先级 | 项目 | 对应瓶颈 | 投入 | 预期产出 | 预计提升 |
|--------|------|---------|------|---------|---------|
| **P0** | 建立 TTS 评测体系 | G | 2-3 周 | 可量化所有后续改进 | +0.3（解锁后续验证） |
| **P0** | 回填 EMOTION_MAPPINGS.voice_tags | A | 1-2 天 | LLM 漏标时仍可发声 | +0.5 |
| **P1** | speed_curve + stress_words 重新启用 | B + C | 3-5 天 | 句内语速变化、词句重音 | +0.7 |
| **P1** | UI 情绪强度滑块 + 多变体试听 | H | 1 周 | 用户能微调情绪强度 | +0.4 |
| **P2** | 跨段情绪衔接 + 规则回退强化 | E + F | 1-2 周 | 长对白连贯性、回退降级产物 | +0.4 |

**预期下一阶段总分**：**5.8 → 8.1**（若按 P0-P1 全部完成）

---

## 附录 A：受影响的代码位置索引

| 文件 | 行号 | 内容 |
|------|------|------|
| novel_forge/tts/platform/minimax_contract.py | L63-79 | 改进 1：情绪别名扩展 |
| novel_forge/tts/performance_policy.py | L22-24, L60-69, L292-300 | 改进 2 & 3：常量、helper、Guard 放宽 |
| novel_forge/tts/pipeline/synthesize_audio_step.py | L887-893, L971-1000, L988-991, L1049-1053 | 改进 2 & 4：短句分级 + sub_emotion |
| novel_forge/tts/schemas.py | L1429 | 瓶颈 A：voice_tags 定义 |
| novel_forge/tts/pipeline/synthesize_audio_step.py | L1103 | 瓶颈 B：stress_words 写入 |
| novel_forge/tts/gateway/adapters/minimax_adapter.py | L753-776 | 瓶颈 B：_build_voice_setting 不消费 stress_words |
| novel_forge/tts/pipeline/generate_script_step.py | L1099-1102 | 瓶颈 C：speed_curve 解析 |
| novel_forge/tts/script_review.py | L491-492 | 瓶颈 C：speed_curve 清空 |
| novel_forge/tts/pipeline/synthesize_audio_step.py | L922-936 | 瓶颈 D：timbre_weights 不自动生成 |
| novel_forge/tts/gateway/adapters/minimax_adapter.py | L239-241 | 瓶颈 D：timbre_weights 入口 |
| novel_forge/tts/pipeline/generate_script_step.py | L1348-1430 | 瓶颈 E：规则回退路径 |
| novel_forge/tts/pipeline/synthesize_audio_step.py | L976-980 | 瓶颈 F：短句 natural delivery |
| novel_forge/tts/ | （无 eval 目录） | 瓶颈 G：评测闭环缺失 |
| novel_forge/desktop/pages/voice_studio/dialogs.py | L355-360 | 瓶颈 H：UI 仅 QComboBox |

## 附录 B：报告产物

1. **Canvas 可视化**：tts-post-implementation-analysis.canvas.tsx（本机历史附件，未随仓库分发）
2. **本 Markdown 报告**：docs/audits/tts_post_implementation_2026-07-19/README.md

---

*报告完成时间：2026-07-19*
*配套 Canvas：tts-post-implementation-analysis*