# 总结类模板 (Summary)

总结和审计模板，用于生成章节、卷和故事弧的摘要，以及进行卷级别的审计。

## 模板列表

| 文件 | 用途 | 任务类型 |
|------|------|---------|
| `summarize_chapter.j2` | 章节总结 | `SUMMARIZE_CHAPTER` |
| `summarize_volume.j2` | 卷总结 | `SUMMARIZE_VOLUME` |
| `summarize_arc.j2` | 弧线总结 | `SUMMARIZE_ARC` |
| `summarize_scene.j2` | 场景级高密度摘要 | `SUMMARIZE_SCENE` |
| `volume_audit.j2` | 卷审计 | `VOLUME_AUDIT` |

## 模板说明

### summarize_chapter.j2
生成章节摘要，用于记录章节内容和情节点。

**主要功能**：
- 提取关键情节点
- 记录角色状态变化
- 总结悬念和伏笔
- 记录规范变化

**输出内容**：
- 章节主要事件
- 角色状态变化
- 规范更新
- 伏笔记录

### summarize_volume.j2
生成卷级别摘要，整合多个章节的内容。

**主要功能**：
- 整合章节摘要
- 识别卷主题
- 记录卷内弧线
- 提取卷内高潮

### summarize_arc.j2
生成故事弧（Arc）级别的总结。

**主要功能**：
- 追踪弧线发展
- 记录弧线转折点
- 评估弧线完成度
- 关联角色弧线

### volume_audit.j2
对整卷内容进行审计，识别问题和改进点。

**主要功能**：
- 审计节奏一致性
- 检查伏笔回收
- 评估角色发展
- 识别结构问题

## 使用方式

```python
from novel_forge.prompts.registry import PromptRegistry
from novel_forge.core.constants import TaskType

registry = PromptRegistry()

# 生成章节摘要
prompt = registry.render(
    TaskType.SUMMARIZE_CHAPTER,
    chapter_content="章节内容...",
    chapter_number=1,
    previous_summary="上一章摘要..."
)

# 卷审计
prompt = registry.render(
    TaskType.VOLUME_AUDIT,
    volume_chapters=[...],
    volume_theme="卷主题"
)
```

## 输出契约

总结类模板通常输出结构化的 JSON；具体 required/allowed keys 由 `TaskFormatContract` 注入并校验：

```json
{
  "chapter_number": 1,
  "summary": "章节摘要内容",
  "key_events": ["事件1", "事件2"],
  "character_changes": {...},
  "canon_updates": [...],
  "foreshadowing": [...]
}
```

## 相关模块

- **写作模块**: `../writing/` - 章节内容来源
- **检查模块**: `../checking/` - 使用摘要进行检查
- **规范模块**: `../canon/` - 规范变更记录
