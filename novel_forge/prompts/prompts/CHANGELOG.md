# 变更日志

所有重要的模板变更都会记录在此文件中。

## [2.2.0] - 2026-05-24

### Schema-first 输出契约

- 输出格式从模板宏迁移到 `TaskFormatContract` + `PromptBuilder` 统一注入
- 删除 `_output_formats.j2` 与 `_base/_output_formats.j2`，任务模板不再调用 `standard_json_output` / `standard_text_output`
- JSON 任务统一使用 strict contract、required/allowed keys、schema 校验和格式重试遥测
- TEXT 任务统一经过输出守卫，阻断 JSON-like、Markdown 结构、规划术语和 prompt leak

## [2.1.4] - 2026-05-23

### 格式契约与模板边界收敛

- 修正 `INVARIANTS.md`：`PATCH_CHAPTER` 明确为 JSON 补丁计划输出，顶层强制 `patches`
- 补齐 `BOOK_CONSISTENCY` 强制字段：`issues`、`repair_plan`、`summary`、`consistency_score`
- 所有声明 `required_top_level_keys` 的 JSON 任务统一开启 `enforce_required_keys`
- 删除无注册、无引用的阅读力宏与 `webnovel_style_guide.j2`
- 业务模板曾统一使用 `standard_json_output` / `standard_text_output` 输出边界；2.2.0 后已迁移为 Builder 注入契约

## [2.1.3] - 2026-05-14

### 提示词维护体系升级

#### 自动化治理
- 新增 `novel_forge/prompts/metadata.py`，统一收集模板注册、输出契约、层级标记和用途摘要
- 新增 `scripts/lint_prompt_layers.py`，校验注册任务模板是否具备备注层、指导层和格式层
- 新增 `scripts/audit_prompt_format_layers.py`，按 TaskType 审计输出类型、必填字段和格式边界来源
- 新增 `scripts/scaffold_prompt.py`，为新提示词生成三层结构骨架
- 新增 `scripts/generate_prompt_index.py`，由 registry、format contracts 与模板备注自动生成 `INDEX.md`

#### 模板标准化
- 为旧版裁判/状态/契约模板补齐 `【备注】` 元数据块
- 为 `beats_to_draft.j2` 补齐显式指导层标记
- 将 `critic_causal.j2`、`critic_continuity.j2`、`critic_strengths.j2` 收敛到共享 JSON 输出宏
- 为 `continuity_repair.j2`、`causal_repair_typed.j2` 增加共享 TEXT 输出格式约束

#### 契约修复
- `PATCH_CHAPTER` 的 `TaskFormatContract` 改为 JSON，并强制顶层 `patches` 字段，与实际模板和 `PatchStep` 解析逻辑一致
- `POLISH_SUBPLOT` 的 `TaskFormatContract` 改为 JSON，并强制顶层 `subplots` 字段；桌面支线润色消费者同步兼容新对象格式

## [2.1.2] - 2026-05-11

### 风格模块简化 + 初始化提示词优化

#### 删除硬编码风格模板
- 删除 `_styles/_registry.j2`、`_styles/_webnovel_style.j2`、`_styles/_literary_style.j2`（commit fa9bae3 中已移除，本次补录变更日志）
- 删除 `webnovel_style_guide.j2`（死代码，include 目标文件不存在，`ignore missing` 静默跳过）
- 风格规则改为由 `PROFILE_STYLE` 步骤通过 `style_profile_derive.j2` 从故事梗概动态推导

#### 初始化提示词优化
- `enrich_spec.j2`、`init_story_bible.j2`、`init_character_bible.j2`、`generate_config.j2`、`enrich_character.j2`、`introduce_character.j2` 六模板精简冗余
- 删除"需补全要素"等与 JSON schema 重复的章节
- 统一输出格式标题为 `## 输出`，builder.py 同步更新检测逻辑

#### 文档修复
- 更新 `prompts/AGENTS.md` 风格系统描述，移除过时的"动态风格发现"声明
- 修复 `_base/_default_style.j2` 使用方式注释
- 修复 `writing/README.md` 引用

## [2.1.1] - 2026-04-27

### 追读力模块优化版本

#### 评估模块 (evaluate_reading_power.j2) 增强
- 新增"角色驱动力"评估维度（strong/moderate/weak）
- 新增 `character_drive` 和 `character_drive_notes` 两个 JSON 输出字段

#### 修复模块 (repair_reading_power.j2) 增强
- 3个修复区各追加1条叙事技巧提示（条件渲染）

#### 代码同步更新
- `reading_power.py` ReadingPowerReport 新增 character_drive + character_drive_notes 字段
- `reading_power_eval_step.py` _parse_report() 新增字段解析逻辑

## [2.1.0] - 2026-04-21

### Phase 2 优化版本

#### 基础模块 (_base/) 增强
- 新增 `_render_element_focus` 宏：叙事要素焦点渲染
- 新增 `_render_canon_characters` 宏：典据角色预过滤
- 新增 `_calculate_word_constraints` 宏：字数约束计算
- 新增 `_render_style_profile_global` 宏：全局风格规范渲染

#### 规划模块 (planning/) 优化
- `plan_chapter.j2` 新增约束层级体系 (P0/P1/P2)

#### 写作模块 (writing/) 优化
- `draft_chapter.j2` 优化字数约束与要素重点
- `edit_chapter.j2` 优化要素执行检查逻辑
- `beats_to_draft.j2` 优化节拍转草稿流程
- `edit_draft.j2` 优化草稿编辑流程

#### 新增功能
- 典据预过滤辅助宏 (canon pre-filtering helper)
- P0/P1/P2 系统 preamble 模板
- Render-regression 测试套件

## [2.0.0] - 2026-03-30

### 重大重构

#### 目录结构重构
- 将 43 个模板从根目录移动到分类子目录
- 新增 8 个功能类别目录
- 更新 registry.py 支持新路径

#### 风格系统集成
- 新增 `_styles/_registry.j2` 风格注册中心
- 新增 `_styles/_webnovel_style.j2` 网文风格
- 新增 `_styles/_literary_style.j2` 文学风格
- 新增 `_base/_default_style.j2` 默认风格

#### 向后兼容
- 新增 `_quality_standards.j2` 包装器
- 新增 `_output_formats.j2` 包装器
- 新增 `_role_definitions.j2` 包装器

### 新增功能

#### 模板索引
- 新增 `INDEX.md` 完整模板索引
- 新增各类别 `README.md` 文档

#### 版本管理
- 新增 `VERSION.md` 版本管理文档
- 新增 `CHANGELOG.md` 变更日志

### 变更列表

| 类别 | 新增 | 修改 | 移动 |
|------|------|------|------|
| writing/ | 0 | 0 | 7 |
| planning/ | 0 | 0 | 6 |
| checking/ | 0 | 0 | 10 |
| initialization/ | 0 | 0 | 7 |
| summary/ | 0 | 0 | 4 |
| canon/ | 0 | 0 | 3 |
| compression/ | 0 | 0 | 4 |
| beats/ | 0 | 0 | 2 |
| _base/ | 1 | 3 | 0 |
| _styles/ | 3 | 0 | 0 |

### 迁移说明

v2.0.0 完全向后兼容，无需修改任何调用代码。

## [1.0.0] - 初始版本

### 初始版本
- 50+ 个模板文件
- 基本质量标准系统
- 基础模板组织结构
