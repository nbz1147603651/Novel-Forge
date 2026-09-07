# 05 配音模块 — 全部 8 个提示词逐条分析

> **分析基准**：本模块 8 个模板在运行时目录 `packs/zh/templates/tts/` 与旧副本 `prompts/prompts/tts/` **全部存在内容分叉**（G-13）；本报告以运行时目录为准，旧版差异逐条标注
> 链路：`TTS_BUILD_NARRATOR_PROFILE → TTS_GENERATE_DUBBING_SCRIPT → TTS_ADJUDICATE_SCRIPT_SEGMENTS → TTS_REVIEW_DUBBING_SCRIPT → TTS_REWRITE_SPOKEN_TEXT → TTS_EMOTION_LABEL → TTS_SOUND_DESIGN → TTS_ADJUDICATE_VOICE_MATCH`
> 契约模式：8 个全部 FULL_OBJECT JSON；`require_native_structured_output=False`（本地综合校验兜底）
> 温度：脚本生成 0.5（config.py:1327，top_p 0.95）、口语改写 0.45（config.py:1339）、审校/裁决 0.0（config.py:1545）、旁白建档 0.2（config.py:1551）、声音设计 0.5（config.py:1557）、情绪标注 0.3（emotion_label.py:269）
> 解析/校验：`generate_script_step.py`（文本保真拼接校验）、`spoken_text_rewrite.py:236`（validate_spoken_rewrite）、`script_review.py`（平台能力过滤）

## 1. tts/build_narrator_profile.j2（TTS_BUILD_NARRATOR_PROFILE）

- **元信息**：FULL_OBJECT JSON；契约 required 5 键（voice_type/base_speed/emotional_range/narration_distance/style_keywords）；`_TTSBuildNarratorProfileResponse` Schema 11 字段（含 speed_range_low=0.8/high=1.2 默认、genre_adaptation、emotion_speed_modifiers/volume_modifiers、sample_narration_text、notes）；温度 0.2
- **诊断**：
  - 【格式】模板 JSON 示例含 11 个顶层字段，包含 `speed_range_low/speed_range_high`；契约 required 5 键 ⊂ 模板字段集，解析器对缺失值使用默认值 → 低风险
  - 【格式】约束条款「base_speed 必须在 0.5~2.0」「speed_range_low/high 必须在 0.5~2.0」有解析器兜底：`_parse_profile_from_json()` 会将速度和 modifier 钳制到合法范围；原报告“输出 3.0 也能通过”的结论不成立
  - 【质量】「体裁匹配/基调影响/叙述距离/情感动态」四组设计指南按题材给音色建议（言情温暖、悬疑低沉…）——引导充分；示例前现已明确“按作品重新推导、禁止照抄”，避免 modifier 表固定值压制差异化设计
  - 【创造】任务自由度好（6 个设计问题开放）；示例数值固定化是主要创造力风险点
- **建议**：已完成示例免责声明；保持 11 字段示例与解析器钳制逻辑同步（低）
- **修改片段示例**（创造力保护 + 格式对齐）：
  ```
  # 输出 JSON 结构（字段名与类型以系统注入契约为准；以下数值仅为格式示例，
  #  必须根据本作品的体裁、基调和叙事风格重新推导，禁止照抄示例值）
  {
    "voice_type": "沉稳的中年男声",
    "base_speed": 1.0,
    "emotional_range": "moderate",
    "narration_distance": "medium",
    "style_keywords": ["关键词1", "关键词2", "关键词3"],
    "speed_range_low": 0.8,   // 可选，默认 0.8
    "speed_range_high": 1.2,  // 可选，默认 1.2
    "genre_adaptation": {...},
    "emotion_speed_modifiers": {...},
    "emotion_volume_modifiers": {...},
    "sample_narration_text": "一段 50-100 字的旁白试读文本",
    "notes": "设计说明"
  }
  ```
- **优先级**：低（已修正）
- **创造力影响**：松绑（消除示例数值照抄风险）

## 2. tts/generate_dubbing_script.j2（TTS_GENERATE_DUBBING_SCRIPT）

- **元信息**：FULL_OBJECT JSON；契约 required 5 键（segments/bgm_suggestions/sfx_cues/soundscapes/scene_transitions）；温度 0.5 + top_p 0.95；`_validate_script_text_fidelity` 做逐字拼接校验（generate_script_step.py:2092）；**运行时版 390 行（旧版 316 行，已整体重写）**
- **诊断（运行时版）**：
  - 【稳定】**编号问题已修复**：运行时版任务列表为连续 1-14（平台原生导演指令 10 / 口语适配改写 11 / 引号不等于对白 12 / 按听感分句 13 / 事件音效要精准 14）——G-01 仅存在于旧副本；新版将「口语适配改写」提升为独立任务 11 并前置「平台原生导演指令」，text 保真指令权重显著提升 ✓
  - 【格式】约束列表重写为 15 条（忠实原文/角色匹配/情感准确/场景连贯/BGM 克制/旁白一致/叙事位置感知/可执行指令完整/内心独白/环境声克制/片段锚点/对白优先 ducking/表演动作克制/声线恒定/全书声音身份优先）——「对白优先：BGM 默认 ducking_db=8，环境声默认 ducking_db=5」是可执行的混音参数约束 ✓
  - 【格式】约束 7「叙事位置感知」从「导演思维」区移入约束区（第一章前 3 段旁白不高唤醒）——与 emotion_label 的 narrative_position 感知形成双保险 ✓
  - 【稳定】温度 0.5 + top_p 0.95 对「逐字保真」任务仍偏高（G-05，config 层未变）；新版虽加强指令，高温下模型「顺手修正」原文的风险仍在 → 建议 0.3 并 A/B
  - 【质量】「可执行指令完整：每个可朗读片段必须给出情绪、可演绎的语气和行动意图」——从「标注」到「可执行表演」的升级 ✓；「内心独白不主动降速」——反套路化 ✓
  - 【创造】「声线恒定：MiniMax 优先使用原生 emotion 枚举、停顿、力度和行动意图，不得用 pitch_override」——平台原生能力优先，表演自由度与平台能力对齐 ✓；「全书声音身份优先：严格遵守 audio_creative_bible」——声音身份连续性 ✓
- **建议**：
  1. 温度 0.5→0.3 A/B（中，config 层）
  2. 字幕拆段（18-42 字，任务 13）与合成器消费协议已核对无冲突（V-6）
  3. 旧副本同步（G-13）
- **优先级**：中（编号问题已在运行时版修复，剩余为温度与同步）
- **创造力影响**：中性

## 3. tts/adjudicate_script_segments.j2（TTS_ADJUDICATE_SCRIPT_SEGMENTS）

- **元信息**：FULL_OBJECT 2 键（decisions/summary）+ json_schema；温度 0.0（execution_script.py:1033）；max_retries=2
- **诊断**：
  - 【质量】说话人裁决规则极其完整：`segment_role` 枚举 4 值（dialogue/inner_thought/narration/ambiguous）、「判断标准是当前叙事时刻是否被可听见地说出，而不是是否带引号」「只被引用/命名/展示/回忆的文本属于 narration」——这是文本转配音最核心的语义裁决
  - 【稳定】**先验保留规则**（current_character_id 是生成模型的归属先验，局部证据不足时保留先验而非降级 ambiguous）——「不确定 ≠ 错误」的稳定性设计，避免大量无谓人工复核 ✓；「仅当局部证据与先验矛盾且无法唯一确定时才 ambiguous/unknown」——漏斗收敛 ✓
  - 【格式】`character_id 必须逐字来自 voice_team`「不得创建角色」——强引用约束 ✓；evidence 必须连续原文 ✓
  - 【创造】「不得输出新的对白、情绪、语速、音色或表演建议」——职责边界清晰 ✓
- **建议**：无（核对通过，标杆模板）
- **优先级**：无
- **创造力影响**：中性

## 4. tts/review_dubbing_script.j2（TTS_REVIEW_DUBBING_SCRIPT）

- **元信息**：FULL_OBJECT 4 键（decisions/reviewed_segment_count/overall_verdict/summary）+ json_schema；温度 0.0（generate_script_step.py:716）
- **诊断**：
  - 【质量】审校 6 个检查域（声音角色/情绪意图/可演性/TTS 稳定性/前后连贯/AI 痕迹候选）——「小说审查复用边界」章节明确「不得复制小说 Humanize 的段落改写策略」「humanize_candidates 只作辅助信号」——跨模块边界保护 ✓
  - 【稳定】决策规则详尽：verdict 枚举（unchanged/revise_performance/manual_review）、「evidence 必须逐字来自源段落」「recommended_tone_hint 要短可表演，禁止更有感情等空泛描述」「副语言每段最多 2 个且有证据」「禁止建议任何 speed/pitch/volume 数值」——审查输出全部可执行化 ✓
  - 【格式】`reviewed_segment_count 必须等于输入片段数`——覆盖率自证 ✓；`overall_verdict=needs_review 仅用于声音角色风险或覆盖不足`——语义精确 ✓
  - 【创造】「修正必须有逐字证据；无证据时返回 unchanged」——防审查过度干预 ✓
- **建议**：无（核对通过）
- **优先级**：无
- **创造力影响**：中性

## 5. tts/rewrite_spoken_text.j2（TTS_REWRITE_SPOKEN_TEXT）

- **元信息**：FULL_OBJECT `rewrites` + json_schema（item required：segment_index/spoken_text/confidence）；温度 0.45（spoken_text_rewrite.py:208）；`validate_spoken_rewrite`（长度比例 50%-130% + SequenceMatcher ≥ min_sequence_ratio + confidence ≥ min_confidence）
- **诊断**：
  - 【格式】约束 6「每条记录只能包含 segment_index、spoken_text 和 confidence」与 `_TTS_SPOKEN_REWRITE_ITEM_SCHEMA`（additional_properties=False）完全一致 ✓；约束 4 长度比例 50%-130% 与 policy 默认（min_length_ratio=0.5/max_length_ratio=1.3）一致 ✓
  - 【稳定】温度 0.45 对「严格等义改写」偏高（G-05）：SequenceMatcher 校验会拒绝大幅改写，高温徒增重试；建议 0.3
  - 【质量】「不需要改写的情况」五类列举（自然口语/已有标注/低于 8 字/信心不足/纯标点分隔符）——「场景分隔符 spoken_text 必须为空」与 script_integrity 的非可朗读过滤联动 ✓；「保留锚点：专有名词/数字/否定词/时间/因果必须原样或等义保留」——语义保真约束 ✓
  - 【创造】「改写仅针对可朗读性，不针对文学质量」「保留叙事节奏和氛围感」——职责边界明确 ✓；性别代词修正（10）是平台级硬约束但表达为「必须」，正确
- **建议**：温度 0.45→0.3 A/B（中）；句长限制软目标表述已正确（「软目标，不得为凑长度破坏自然意群」）无需改动
- **优先级**：中
- **创造力影响**：中性

## 6. tts/emotion_label.j2（TTS_EMOTION_LABEL）

- **元信息**：FULL_OBJECT `emotions` + json_schema（item required：segment_index/emotion/intensity；sub_emotion 可空）；温度 0.3（emotion_label.py:269）；max_tokens=max(512, len(batch)*96)；max_retries=1
- **诊断**：
  - 【格式】**核对结论**：模板枚举现为 `EmotionTag`（`tts/schemas.py:123`）的完整 15 值集合，已补 `determined`（坚定/决绝）与 `playful`（俏皮/戏谑）；模板备注层声明的回退已确认实现：`_coerce_emotion`（emotion_label.py:108，非法值→关键词推理）与 `_coerce_intensity`（:137，钳制 [0.0,1.0]）✓ 提示词与实现一致
  - 【稳定】「同一窗口内情绪应当有区分度」「连续多段不要全部标同一个情绪」与审校阶段的 emotion_differentiation 指标（目标 ≥0.15）联动 ✓；intensity 分层（平淡 0.2-0.4/对话 0.5-0.6/高潮 0.7-1.0）可机检 ✓
  - 【质量】「低于 0.5 的情绪标注对 TTS 表现力贡献有限，请谨慎使用」——反无效标注 ✓；「关键对白与冲突场景允许 0.7 以上，此类段落将获得多 Take 合成机会」——与 multi_take 联动 ✓
  - 【创造】sub_emotion 与 emotion 相同时省略——减少冗余标注；建议补充 determined/playful 两个枚举以对齐 EmotionTag（扩大表演自由度）
- **建议**：保持与 `EmotionTag` 的 15 值集合同步（低）
- **优先级**：低（已修正）
- **创造力影响**：松绑（恢复两个合法情绪维度）

## 7. tts/sound_design_extraction.j2（TTS_SOUND_DESIGN）

- **元信息**：FULL_OBJECT 4 键（sfx_cues/bgm_needs/soundscapes/scene_transitions）；温度 0.5（sound_design_extraction.py:206）
- **诊断**：
  - 【格式】契约 4 键与模板输出段一致 ✓；「历史手写示例仅供模板维护对照，运行时以注入 schema 为唯一来源」的注释——示例不再暴露给模型，防字段漂移的设计 ✓（优于 build_narrator_profile 的示例暴露）
  - 【稳定】约束 1「每个 cue 必须包含 segment_index 锚定（trigger_segment_index 或 start_segment_index）」——机检约束 ✓；约束 2「SFX ≤12 个」——数量上限 ✓；温度 0.5 对 cue sheet 生成属合理（声音设计有创意成分，但锚定字段必须精确）——可考虑 0.4
  - 【质量】SFX/BGM/soundscape 三分类的克制原则（「持续环境声属于 soundscape 而非 SFX」「不要为 BGM 要求人声或歌词」「所有资产描述不得包含人声/歌词/可辨识台词」）——生成安全约束完整 ✓；「BGM 优先通过 reuse_hint 引用已有资产」——资产复用防每章新造 ✓
  - 【创造】声音设计自由度好（mood_tags 中文标签可复用）
- **建议**：无系统性改动（可选：温度 0.5→0.4 微调）
- **优先级**：低
- **创造力影响**：中性

## 8. tts/adjudicate_voice_match.j2（TTS_ADJUDICATE_VOICE_MATCH）

- **元信息**：FULL_OBJECT 2 键（decisions/summary）+ json_schema（item：character_id/verdict/selected_voice_id/audition_voice_ids/confidence/reason）；verdict 枚举 approve/review/reject；温度 0.0
- **诊断**：
  - 【格式】「所有输出音色 ID 只能使用当前角色 candidate_voices 中出现的 ID，不得自造、跨角色或跨平台」——强引用约束 ✓。**重点核对结论**：`_TTS_VOICE_MATCH_ADJUDICATION_ITEM_SCHEMA` 的 required 含 `selected_voice_id`，与「review/reject 时留空」语义表面冲突；但实际调用点（`build_voice_team_step.py:670-690`）只传 `required_keys=("decisions", "summary")`（顶层键），**不启用 json_schema 硬校验**；本地解析按 verdict 分支处理（`build_voice_team_step.py:724`：`decision_invalid_output = bool(verdict == "approve" and not selected_voice_id)`）——reject/review 空 selected_voice_id 是预期合法行为 ✓。**唯一残余风险**：若未来切换到原生结构化输出路由（require_native_structured_output 目前为 False），schema required 会误伤 reject/review 分支——建议届时同步改为条件校验
  - 【质量】「不要把某一场的短期情绪固化为声纹身份」「同时比较年龄感/声线质地/吐字/节奏/身份地位/团队辨识度，不要只看性别」——选角导演思维正确 ✓；「reason 不超过两句，不模仿真实人物」——输出可控 ✓
  - 【稳定】0.0 温度 ✓；「证据不足时选择 review 并给出 2-3 个对比试听，而非强行 approve」——三态决策的漏斗设计 ✓
- **建议**：无模板改动（核对通过）；在 `format_contracts.py` 补充注释说明「selected_voice_id 的 required 仅对 approve 分支生效，reject/review 由本地解析器按分支校验」，防止未来切换 native structured output 时回归（低）
- **优先级**：低（核对通过，附未来路由切换风险提示）
- **创造力影响**：中性

---

## 配音模块优先级汇总表

| 模板 | 优先级 | 核心问题 | 建议动作 |
|------|--------|----------|----------|
| build_narrator_profile.j2 | 低 | 示例防照抄说明已补；解析器已有范围钳制 | 保持字段与范围同步 |
| generate_dubbing_script.j2 | 中 | 编号已在运行时版修复（G-01 关闭）；温度仍偏高 | 温度 A/B + 旧副本同步 |
| adjudicate_script_segments.j2 | 无 | - | 通过 |
| review_dubbing_script.j2 | 无 | - | 通过 |
| rewrite_spoken_text.j2 | 中 | 温度 0.45 与等义校验冲突 | 温度 A/B |
| emotion_label.j2 | 低 | 枚举已与 EmotionTag 全集对齐 | 保持同步 |
| sound_design_extraction.j2 | 低 | 温度可微调 | 可选 0.4 |
| adjudicate_voice_match.j2 | 低 | 核对通过；仅未来 native 路由有 schema 冲突风险 | schema 注释说明 + 路由切换时改条件校验 |

## 配音模块关键联动点（稳定性核心）

1. `generate_dubbing_script` 的 text 保真（模板约束 1/14）→ `_validate_script_text_fidelity` 逐字拼接校验 → 失败整批丢弃重试：**该链路的失败率 = 温度函数**（0.5 偏高风险）
2. `adjudicate_script_segments` 的先验保留 → 说话人漏斗收敛 → `script_review` 的 manual_review 通道：**人工复核量由先验保留规则控制**
3. `emotion_label` 枚举 → `emotion_inference` 确定性回退 → 合成层 emotion 标签：**非法枚举无回退时污染合成**
4. `adjudicate_voice_match` 的 verdict=reject → 音色库降级：**当前 prompt-only 路由安全（本地按分支校验）；未来切 native structured output 时必须同步改 schema 条件校验**
