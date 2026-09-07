# 08 运行时核验与已执行优化

> **核验日期**：2026-08-01
>
> **基准**：仅以 `novel_forge/prompts/packs/zh/templates/` 的稳定中文包和实际注册表、格式契约、解析/校验调用点为准。`novel_forge/prompts/prompts/` 是兼容副本，不作为运行时结论的依据。

## 1. 可复现快照

| 项目 | 核验结果 |
|---|---:|
| `TaskType` 成员 | 145 |
| 注册 TaskType → 模板映射 | 142 |
| 格式契约 | 142 |
| 独立的已注册模板路径 | 129 |
| 运行时 `.j2` 文件 | 154 |
| 兼容副本 `.j2` 文件 | 152 |
| 两目录同路径但内容不同的 `.j2` | 53 |

154 个运行时模板由 129 个注册任务模板和 25 个支持模板组成：`_base/` 20、根级兼容宏 2、`_styles/` 1、`checking/` 未注册共享宏 2。原报告中“139 个契约全量”“`_base/` 17 个”“写作族 10 个运行时模板”等计数均不正确。

以下命令已通过：

```bash
.venv/bin/python scripts/verify_templates.py
.venv/bin/python scripts/lint_prompt_layers.py
.venv/bin/python scripts/audit_prompt_format_layers.py --all
.venv/bin/python scripts/verify_format_contracts.py
.venv/bin/python scripts/generate_prompt_index.py --check
.venv/bin/python scripts/audit_prompt_packs.py
```

英文稳定包的 `source-template-hash` 现已逐项复核并更新；此前整篇中文的
`tts/emotion_label.j2`、`tts/sound_design_extraction.j2` 已完成英文本地化，
`tts/generate_dubbing_script.j2` 的中文残留也已消除。因此
`scripts/audit_prompt_packs.py` 现在通过，中文运行时包与英文稳定包的维护检查均已闭合。

## 2. 对原结论的核验修正

| 原编号 | 当前结论 | 依据/处理 |
|---|---|---|
| G-01 | 已关闭 | 活跃的 `tts/generate_dubbing_script.j2` 已是连续 1–14。 |
| G-02 | 有效，已修复 | `beats_to_draft.j2` 的分段/整篇条件分支曾造成重复或跳号；现改为具名小节与无序追加规则。 |
| G-06 | 部分有效，已修复 | `plan_outline.j2` 和 `generate_config.j2` 的单次渲染中存在重复编号；现改为具名约束小节。`derive_editorial_contract.j2` 当前为连续 1–13，`adjudicate_final_state.j2` 为连续 1–22，原报告对此二者的跳号结论已过期。 |
| G-07 | 有效但建议应改写 | `draft_chapter.j2` 已调用 `render_pre_delivery_checklist()`；正确的后续动作是以实测渲染 token 与质量指标决定是否**去重**，不是“把自检移入一个已存在的宏”。 |
| G-08 | 有效，计数修正 | `WaveStep` 有 6 项告警式后置检查：cross-ref、pacing、scene anchor、字数、POV、`required_outcome`。是否升格为硬门是流水线策略改动，未在本次提示词修改中实施。 |
| G-10 | 有效，已修复 | `EmotionTag` 有 15 值，模板原来遗漏 `determined`、`playful`；已补齐。 |
| G-11 | 部分有效，已修复 | 示例数值缺免责声明，已补。解析器并非无范围校验：`build_narrator_profile_step.py` 会将速度和 modifier 钳制到合法范围，故不再建议重复增加范围校验。模板示例实际有 11 个顶层字段，而格式契约只要求其中 5 个。 |
| 01 / draft_scene | 原“缺少不提前兑现其他场景”结论不成立 | 活跃模板规则 1 已禁止提前写其他场景的 `owned_events`、`owned_revelations`、`owned_state_changes`。 |
| G-03 | 有效，已修复 | `normalize_gender_value()` 现在只接受男/女及其常见英文别名；`CharacterNormalizer` 与 StoryKernel 合并边界共同应用，未知值降为空并有回归测试。 |

## 3. 本次已执行的提示词优化

所有改动只澄清指令边界或可用词汇，不改变输出契约、模型路由、参数或故事事实。

| 模板 | 改动 | 目标 |
|---|---|---|
| `beats/beats_to_draft.j2` | 用“分段模式/整篇模式/共同表达要求”小节替代跨条件分支连续编号 | 消除模式间编号混淆，保留全部原有约束。 |
| `planning/plan_outline.j2` | 条件分支的第 4 条改为具名字段约束 | 防止单次 prompt 出现 4 个“4.”。 |
| `initialization/generate_config.j2` | 润色、长短篇、初始生成专属规则改为具名小节 | 防止同一次渲染中 9/10/11/12 等编号被重复使用。 |
| `tts/emotion_label.j2` | 补充 `determined`、`playful` | 与 `EmotionTag` 全集对齐，扩大可表达的情绪范围。 |
| `tts/build_narrator_profile.j2` | 为 JSON 示例添加“按作品重新推导、禁止照抄”说明 | 防止示例 modifier 数值被当作默认答案。 |
| `writing/patch_chapter.j2` | 明确精确匹配、原文顺序、不可重叠、最小修改范围 | 提高补丁能被确定性 patch executor 安全应用的概率。 |
| `packs/en/templates/tts/emotion_label.j2` | 全量英文本地化，并同步 15 值 EmotionTag | 让英文包不再向模型暴露中文指令或缺失情绪值。 |
| `packs/en/templates/tts/sound_design_extraction.j2` | 全量英文本地化，并同步 scene_intents 声景输入 | 保持英语输出语言与中文权威模板语义一致。 |
| 英文稳定包来源元数据 | 更新 24 个已复核的 source-template hash | 恢复跨语言包的可审计同步检查。 |
| `common/utils.py` / `normalizers.py` | 性别白名单规范化 | 阻止未知 LLM 性别值写入 Canon。 |

## 4. 尚待数据或架构决策的事项

- 温度 0.5 / 0.45 是否应下调，必须以同一批真实章节的保真失败率、重试次数、人工听感和质量评分作 A/B 判断；静态阅读不能证明 0.3 更优。
- 6 个 `require_native_structured_output=True` 任务没有 prompt-only 降级路径。这是模型能力与路由选择的显式架构约束；若要支持本地模型，应单独设计并测试降级语义。
- WAVE 告警是否改为硬门会改变恢复/重试/质量门行为，应在流水线层单独立项；不可仅通过加重措辞解决。
- 中文运行时包与旧兼容副本的 53 个内容分叉需要明确同步或废弃策略；不要在未决定兼容策略前机械同步。

## 5. 后续优先级

1. 为上述模板改动运行渲染、分层、契约检查和相关单元测试。
2. 采样测量 `draft_chapter` 的实际 prompt token、重试率及质量结果，再决定是否压缩重复宏。
3. 对 TTS 保真任务设计记录化 A/B，包含失败率、重试次数、人工审校与音频质量指标。
4. 在引入新语言包或修改中文权威模板时，同步审核对应英文翻译并更新 source-template hash；CI 继续以 `audit_prompt_packs.py` 守护。
