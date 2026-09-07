# 规划类模板 (Planning)

规划和蓝图生成模板，用于创建小说大纲和章节计划。

## 模板列表

| 文件 | 用途 | 任务类型 |
|------|------|---------|
| `plan_chapter.j2` | 章节规划 | `PLAN_CHAPTER` |
| `plan_chapter_scenes.j2` | 章节场景计划 | `PLAN_CHAPTER_SCENES` |
| `validate_scene_plan.j2` | 场景计划验证 | `VALIDATE_SCENE_PLAN` |
| `plan_outline.j2` | 大纲规划 | `PLAN_OUTLINE` |
| `plan_outline_batch.j2` | 批量大纲规划 | `PLAN_OUTLINE_BATCH` |
| `plan_outline_continue.j2` | 续写大纲规划 | `PLAN_OUTLINE_CONTINUE` |
| `plan_chapter_contracts.j2` | 逐章叙事契约拆分 | `PLAN_CHAPTER_CONTRACTS` |
| `derive_init_coherence_profile.j2` | 初始化一致性画像 | `DERIVE_INIT_COHERENCE_PROFILE` |
| `init_coherence_ontology.j2` | 初始化一致性本体 | `INIT_COHERENCE_ONTOLOGY` |
| `init_coherence_extraction_guide.j2` | 初始化一致性抽取指南 | `INIT_COHERENCE_EXTRACTION_GUIDE` |
| `init_coherence_conflict_rules.j2` | 初始化冲突规则 | `INIT_COHERENCE_CONFLICT_RULES` |
| `init_coherence_payoff_rules.j2` | 初始化伏笔兑现规则 | `INIT_COHERENCE_PAYOFF_RULES` |
| `refine_init_coherence_profile.j2` | 初始化一致性画像精炼 | `REFINE_INIT_COHERENCE_PROFILE` |
| `repair_init_artifact_patch.j2` | 初始化局部 JSON Patch 修复 | `REPAIR_INIT_ARTIFACT_PATCH` |
| `refine_init_artifacts_from_synopsis.j2` | 基于梗概精修初始化产物 | `REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS` |
| `blueprint_element_select.j2` | 叙事要素选择 | `BLUEPRINT_ELEMENT_SELECT` |
| `short_blueprint.j2` | 短篇蓝图 | `SHORT_BLUEPRINT` |
| `short_creative_summary.j2` | 短篇创作摘要 | `SHORT_CREATIVE_SUMMARY` |
| `polish_outline.j2` | 大纲润色 | `POLISH_OUTLINE` |
| `subplot_polish.j2` | 支线打磨 | `SUBPLOT_POLISH` |
| `ground_outline_research.j2` | 大纲资料校准 | `GROUND_OUTLINE_RESEARCH` |
| `plan_init_research_queries.j2` | 研究查询规划 | `PLAN_INIT_RESEARCH_QUERIES` |

## 模板说明

### plan_outline.j2
生成小说整体大纲，包含故事结构、章节安排和关键情节点。

**主要功能**：
- 规划故事弧线
- 设计章节结构
- 安排关键事件
- 设定节奏曲线

### plan_chapter.j2
为单个章节生成详细规划，包含场景划分、情节点和角色状态变化。长篇章节规划只消费 `stage_cards.source.chapter_contract`、BridgeArtifact 和章节薄片，不从全书级创作大包推断新剧情。

**主要功能**：
- 划分场景
- 规划情节点
- 设置悬念
- 定义角色状态变化

**关键变量**：
- `chapter_number`: 章节编号
- `outline`: 整体大纲
- `previous_chapter_summary`: 上章摘要
- `genre`: 题材类型

### plan_outline_batch.j2
批量生成多个章节的大纲规划。

### plan_outline_continue.j2
为续写的小说生成大纲规划。

### plan_chapter_contracts.j2
把全书叙事契约拆成 `ChapterContractIndexArtifact` 可消费的逐章契约。每章契约只写当前章执行目标、知识变化、禁提前推进、出口状态和完成标准，不复制人物大包或世界设定大包。

### blueprint_element_select.j2
根据题材与创作目标筛选叙事必要项与扩展项，供后续大纲与章节规划调用。

### short_blueprint.j2
生成短篇小说的蓝图，包含故事结构和高潮设计。

### short_creative_summary.j2
生成短篇创作的摘要文档。

## 使用方式

```python
from novel_forge.prompts.registry import PromptRegistry
from novel_forge.core.constants import TaskType

registry = PromptRegistry()

# 生成章节规划
prompt = registry.render(
    TaskType.PLAN_CHAPTER,
    chapter_number=1,
    outline={...},
    previous_chapter_summary="...",
    genre="玄幻"
)
```

## 相关模块

- **写作模块**: `../writing/` - 使用规划结果生成章节
- **检查模块**: `../checking/` - 验证规划与内容的一致性
