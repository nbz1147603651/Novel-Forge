# 提示词模板版本管理

## 版本系统

### 版本格式
版本号格式：`主版本.次版本.修订版本`

- **主版本 (Major)**: 重大架构变更，不兼容的修改
- **次版本 (Minor)**: 新增功能，向后兼容
- **修订版本 (Patch)**: 修复问题，向后兼容

### 版本历史

#### v2.1.3 (2026-05-14)

**提示词维护体系升级版本**

- ✅ 新增提示词元数据目录与层级 lint：`metadata.py` + `scripts/lint_prompt_layers.py`
- ✅ 新增格式层审计：`scripts/audit_prompt_format_layers.py`
- ✅ 新增提示词脚手架：`scripts/scaffold_prompt.py`
- ✅ 新增自动索引生成：`scripts/generate_prompt_index.py`
- ✅ 注册任务模板统一具备备注层 / 指导层 / 格式层
- ✅ `PATCH_CHAPTER` 输出契约修正为 JSON `patches`
- ✅ `POLISH_SUBPLOT` 输出契约修正为 JSON `subplots`

#### v2.1.1 (2026-04-27)

**追读力模块优化版本**

- ✅ `evaluate_reading_power.j2` 新增"角色驱动力"评估维度（strong/moderate/weak）+ 2个JSON输出字段
- ✅ `repair_reading_power.j2` 3个修复区各追加1条叙事技巧提示（条件渲染）
- ✅ `reading_power.py` ReadingPowerReport 新增 character_drive + character_drive_notes 字段
- ✅ `reading_power_eval_step.py` _parse_report() 新增字段解析逻辑

#### v2.1.0 (2026-04-21)

**Phase 2 优化版本**

- ✅ 基础模块新增 4 个新宏
- ✅ 规划模块约束层级体系 (P0/P1/P2)
- ✅ 写作模块优化 (draft/edit/short templates)

#### v2.0.0 (2026-03-30)
**重大重构版本**

- ✅ 模板目录重构，按功能分类
- ✅ 风格系统集成（通用/网文/文学）
- ✅ 向后兼容包装器实现
- ✅ 模板索引系统建立

**变更内容**:
- 所有模板从根目录移动到分类子目录
- `registry.py` 更新支持新路径
- 新增 `_styles/` 风格模块
- 新增 `_base/` 基础模块

#### v1.0.0 (初始版本)
- 初始模板系统
- 50+ 个模板文件
- 基本质量标准系统

## 模板版本清单

### writing/ (v2.1.3)

| 模板 | 版本 | 状态 |
|------|------|------|
| draft_chapter.j2 | 2.1.0 | 稳定 |
| edit_chapter.j2 | 2.1.0 | 稳定 |
| bridge_chapter.j2 | 2.0.0 | 稳定 |
| polish_chapter.j2 | 2.0.0 | 稳定 |
| patch_chapter.j2 | 2.0.0 | 稳定 |
| edit_draft.j2 | 2.1.0 | 稳定 |
| evaluate_draft.j2 | 2.0.0 | 稳定 |

### planning/ (v2.1.3)

| 模板 | 版本 | 状态 |
|------|------|------|
| plan_chapter.j2 | 2.1.0 | 稳定 |
| plan_outline.j2 | 2.0.0 | 稳定 |
| plan_outline_batch.j2 | 2.0.0 | 稳定 |
| plan_outline_continue.j2 | 2.0.0 | 稳定 |
| short_blueprint.j2 | 2.0.0 | 稳定 |
| short_creative_summary.j2 | 2.0.0 | 稳定 |

### checking/ (v2.1.3)

| 模板 | 版本 | 状态 |
|------|------|------|
| check_chapter.j2 | 2.0.0 | 稳定 |
| check_alignment.j2 | 2.0.0 | 稳定 |
| continuity_eval.j2 | 2.0.0 | 稳定 |
| continuity_repair.j2 | 2.0.0 | 稳定 |
| causal_validate.j2 | 2.0.0 | 稳定 |
| critic_continuity.j2 | 2.0.0 | 稳定 |
| critic_causal.j2 | 2.0.0 | 稳定 |
| critic_character.j2 | 2.0.0 | 稳定 |
| critic_strengths.j2 | 2.0.0 | 稳定 |
| book_consistency.j2 | 2.0.0 | 稳定 |

### 其他类别 (v2.0.0)

| 类别 | 模板数 | 状态 |
|------|--------|------|
| initialization/ | 9 | 稳定 |
| summary/ | 5 | 稳定 |
| canon/ | 5 | 稳定 |
| compression/ | 5 | 稳定 |
| beats/ | 2 | 稳定 |

### _base/ (v2.1.0)

| 模板 | 版本 | 状态 |
|------|------|------|
| _render_element_focus.j2 | 2.1.0 | 稳定 |
| _render_canon_characters.j2 | 2.1.0 | 稳定 |
| _calculate_word_constraints.j2 | 2.1.0 | 稳定 |
| _render_style_profile_global.j2 | 2.1.0 | 稳定 |

## 风格模块版本

### v2.0.0

| 风格 | 标识符 | 状态 |
|------|--------|------|
| 通用 | default | 稳定 |
| 网文 | webnovel | 稳定 |
| 文学 | literary | 稳定 |

## 版本兼容性

### v2.0.0 兼容性说明

- ✅ Python API 完全兼容
- ✅ Jinja2 模板导入完全兼容
- ✅ 向后兼容包装器保持原有接口
- ✅ registry.py 无需修改调用代码

### 迁移指南 (v1.x → v2.0)

如果您使用的是 v1.x 版本：

1. **无需修改代码**: Python API 完全兼容
2. **模板路径变更**: 内部路径已更新，外部无感知
3. **推荐更新**: 建议更新以获得新功能

## 版本管理规则

### 1. 发布前检查
- [ ] 所有模板加载测试通过
- [ ] 提示词层级 lint 通过：`.venv/bin/python scripts/lint_prompt_layers.py`
- [ ] 格式层审计通过：`.venv/bin/python scripts/audit_prompt_format_layers.py --all`
- [ ] 自动索引未漂移：`.venv/bin/python scripts/generate_prompt_index.py --check`
- [ ] 关键模板渲染测试通过
- [ ] 向后兼容性测试通过
- [ ] 文档更新完成

### 2. 变更记录
每次修改模板需要：
- 在 CHANGELOG.md 中记录
- 更新相关 README.md
- 运行 `scripts/generate_prompt_index.py` 更新 INDEX.md

### 3. 版本号递增规则

| 变更类型 | 版本号递增 |
|---------|-----------|
| 新增模板 | 次版本 +1 |
| 修改模板逻辑 | 修订版本 +1 |
| 重构目录结构 | 主版本 +1 |
| 删除模板 | 主版本 +1 |

## 相关文件

- [INDEX.md](INDEX.md) - 模板索引
- [../README.md](../README.md) - 主 README
- [CHANGELOG.md](CHANGELOG.md) - 变更日志

## 反馈和支持

如发现问题或建议，请提交 Issue。
