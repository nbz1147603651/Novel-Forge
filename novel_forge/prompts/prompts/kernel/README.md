# 规范类模板 (Canon)

规范和事实管理模板，用于提取和管理小说的规范信息（Canon）。

## 模板列表

| 文件 | 用途 | 任务类型 |
|------|------|---------|
| `extract_canon_delta.j2` | 提取规范增量 | `EXTRACT_CANON` |
| `extract_canon_delta_fragment.j2` | 分块提取 Canon 增量 | `EXTRACT_CANON` fragment |
| `extract_chapter_summary_exit.j2` | 提取章节摘要与出口状态 | `EXTRACT_CHAPTER_SUMMARY_EXIT` |
| `extract_character_state_deltas.j2` | 提取角色状态变化 | `EXTRACT_CHARACTER_STATE_DELTAS` |
| `extract_relationship_deltas.j2` | 提取关系变化 | `EXTRACT_RELATIONSHIP_DELTAS` |
| `extract_plot_thread_deltas.j2` | 提取情节线变化 | `EXTRACT_PLOT_THREAD_DELTAS` |
| `extract_creative_report.j2` | 提取章节创作报告 | `EXTRACT_CREATIVE_REPORT` |
| `extract_candidate_state_deltas.j2` | 抽取候选叙事状态变化 | `EXTRACT_CANDIDATE_STATE_DELTAS` |
| `adjudicate_final_state.j2` | 最终叙事状态裁判 | `ADJUDICATE_FINAL_STATE` |
| `extract_motifs.j2` | 提取母题 | `EXTRACT_MOTIFS` |
| `adjust_outline.j2` | 调整大纲 | `ADJUST_OUTLINE` |

## 模板说明

### extract_canon_delta.j2
从章节内容中提取规范变更（增量）。

**主要功能**：
- 识别新建立的规范
- 记录规范变更
- 追踪已建立的规则
- 保持规范一致性

**规范类型**：
- 世界观规则
- 角色能力边界
- 科技/魔法体系规则
- 社会结构设定
- 地理/时间设定

### extract_motifs.j2
提取和管理重复出现的主题/母题（Motifs）。

**主要功能**：
- 识别叙事母题
- 追踪母题出现位置
- 建立母题关联
- 分析母题发展

**母题类型**：
- 视觉母题（物品、场景）
- 听觉母题（音乐、声音）
- 主题母题（复仇、救赎）
- 象征母题（颜色、动物）

### adjust_outline.j2
根据规范更新调整大纲。

**主要功能**：
- 评估规范变更影响
- 调整大纲结构
- 重新安排情节点
- 同步大纲与规范

## 使用方式

```python
from novel_forge.prompts.registry import PromptRegistry
from novel_forge.core.constants import TaskType

registry = PromptRegistry()

# 提取规范增量
prompt = registry.render(
    TaskType.EXTRACT_CANON,
    content="章节内容...",
    existing_canon={...}
)

# 提取母题
prompt = registry.render(
    TaskType.EXTRACT_MOTIFS,
    chapters=[...],
    existing_motifs={...}
)

# 调整大纲
prompt = registry.render(
    TaskType.ADJUST_OUTLINE,
    current_outline={...},
    canon_updates=[...]
)
```

## 规范管理概念

### 什么是规范 (Canon)?
规范是小说的"真实"，包括：
- 已建立的世界规则
- 角色的能力和限制
- 已发生的事件
- 角色关系和状态

所有 Canon/Memory 抽取模板只从 FinalArtifact 对应的最终正文证据写事实。大纲目标、章节契约、source 投影、创作报告和中间稿只作为抽取方向、ID 复用或对照基线，不能替代正文证据。

### 为什么需要规范管理?
- 保持叙事一致性
- 避免自相矛盾
- 追踪伏笔回收
- 支撑后续写作

## 相关模块

- **初始化模块**: `../initialization/` - 初始规范来源
- **总结模块**: `../summary/` - 规范变更记录
- **检查模块**: `../checking/` - 一致性检查
