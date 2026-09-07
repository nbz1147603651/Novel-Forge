# 节拍类模板 (Beats)

故事节拍和规格转换模板，用于将规格说明转换为故事节拍。

## 模板列表

| 文件 | 用途 | 任务类型 |
|------|------|---------|
| `spec_to_beats.j2` | 规格到节拍 | `SPEC_ENRICH` / `BEATS` |
| `beats_to_draft.j2` | 节拍到草稿 | `DRAFT` |

## 模板说明

### spec_to_beats.j2
将规格说明转换为故事节拍序列。

**主要功能**：
- 解析规格说明
- 生成节拍序列
- 分配节拍时长
- 标记节拍类型

**节拍类型**：
- 开场 (Opening)
- 主题陈述 (Theme Stated)
- 设置 (Setup)
- 催化剂 (Catalyst)
- 争论 (Debate)
- 突破 (Break into Two)
- B故事 (B Story)
- 趣味游戏 (Fun and Games)
- 中点 (Midpoint)
- 坏人逼近 (Bad Guys Close In)
- 一切皆失 (All Is Lost)
- 黑夜时刻 (Dark Night of the Soul)
- 突破 (Break into Three)
- 结局 (Finale)
- 最终画面 (Final Image)

### beats_to_draft.j2
将故事节拍转换为章节草稿内容。

**主要功能**：
- 展开节拍描述
- 生成叙事内容
- 分配章节位置
- 整合节拍序列

## 节拍系统说明

### 什么是故事节拍?
故事节拍是构成故事的最小叙事单元，每个节拍推动故事向前发展。

### 节拍特点
- **可独立**: 每个节拍可以独立存在
- **有目的**: 每个节拍有明确的叙事目的
- **可组合**: 多个节拍组合成场景
- **有节奏**: 节拍之间有张力的起伏

### 节拍规划原则
1. 张力递增：整体故事走向高潮
2. 节奏变化：避免单调
3. 信息递进：逐步揭示关键信息
4. 情感曲线：匹配目标情绪

## 使用方式

```python
from novel_forge.prompts.registry import PromptRegistry
from novel_forge.core.constants import TaskType

registry = PromptRegistry()

# 规格到节拍
prompt = registry.render(
    TaskType.BEATS,
    spec="故事规格说明...",
    target_chapters=20,
    genre="玄幻"
)

# 节拍到草稿
prompt = registry.render(
    TaskType.DRAFT,
    beats=[...],
    style_profile={...},
    target_length=3000
)
```

## 相关模块

- **初始化模块**: `../initialization/` - 故事规格来源
- **写作模块**: `../writing/` - 使用节拍生成章节
- **规划模块**: `../planning/` - 大纲规划
