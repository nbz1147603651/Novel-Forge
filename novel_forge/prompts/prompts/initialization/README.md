# 初始化类模板 (Initialization)

项目初始化和基础设定模板，用于创建小说项目的基础文档。

## 模板列表

| 文件 | 用途 | 任务类型 |
|------|------|---------|
| `init_story_bible.j2` | 初始化故事圣经 | `INIT_STORY_BIBLE` |
| `init_story_core_premise.j2` | 初始化故事核心前提 | `INIT_STORY_CORE_PREMISE` |
| `init_story_world_rules.j2` | 初始化世界规则 | `INIT_STORY_WORLD_RULES` |
| `init_story_themes_and_symbols.j2` | 初始化主题与象征 | `INIT_STORY_THEMES_AND_SYMBOLS` |
| `locations_field_backfill.j2` | 场景字段级定向补全 | `LOCATIONS_FIELD_BACKFILL` |
| `init_story_continuity_rules.j2` | 初始化连续性规则 | `INIT_STORY_CONTINUITY_RULES` |
| `init_character_bible.j2` | 初始化角色圣经 | `INIT_CHARACTER_BIBLE` |
| `init_character_roster.j2` | 初始化角色名单 | `INIT_CHARACTER_ROSTER` |
| `init_character_profile_batch.j2` | 批量初始化角色档案 | `INIT_CHARACTER_PROFILE_BATCH` |
| `init_character_relationship_matrix.j2` | 初始化角色关系矩阵 | `INIT_CHARACTER_RELATIONSHIP_MATRIX` |
| `init_character_arc_plan.j2` | 初始化角色弧光计划 | `INIT_CHARACTER_ARC_PLAN` |
| `init_creative_direction_candidates.j2` | 生成有界创意方向候选 | `INIT_CREATIVE_DIRECTION_CANDIDATES` |
| `init_creative_direction_select.j2` | 独立选择创意方向 | `INIT_CREATIVE_DIRECTION_SELECT` |
| `init_entity_registry.j2` | 初始化实体注册表 | `INIT_ENTITY_REGISTRY` |
| `reconcile_entities.j2` | 实体引用调和 | `RECONCILE_ENTITIES` |
| `init_narrative_contract.j2` | 初始化全书叙事契约 | `INIT_NARRATIVE_CONTRACT` |
| `init_knowledge_boundaries.j2` | 初始化角色知识边界 | `INIT_KNOWLEDGE_BOUNDARIES` |
| `enrich_spec.j2` | 丰富规格说明 | `SPEC_ENRICH` |
| `enrich_character.j2` | 丰富角色设定 | `ENRICH_CHARACTER` |
| `adjudicate_character_introduction.j2` | 新角色建档裁决 | `ADJUDICATE_CHARACTER_INTRODUCTION` |
| `introduce_character.j2` | 角色介绍生成 | `INTRODUCE_CHARACTER` |
| `generate_config.j2` | 生成配置文件 | `GENERATE_CONFIG` |
| `synthesize_init_research_dossier.j2` | 初始化资料包合成 | `SYNTHESIZE_INIT_RESEARCH_DOSSIER` |
| `synthesize_model_prior_research.j2` | 模型先验知识补充 | `SYNTHESIZE_MODEL_PRIOR_RESEARCH` |

## 模板说明

### init_story_bible.j2
专门用于初始化故事相关的设定。

**主要功能**：
- 设计故事背景
- 规划故事类型
- 建立故事规则

### init_character_bible.j2
初始化角色设定文档，详细定义每个角色。

**主要功能**：
- 定义角色基本信息
- 设计角色性格
- 规划角色弧线
- 建立角色关系

### init_entity_registry.j2 / init_narrative_contract.j2
初始化阶段负责生成后续 source artifacts 的权威源头：实体注册表维护 canonical entity、alias 和唯一 `entity_id`；叙事契约维护承诺/揭示顺序、认知边界和连续性协议。初始化模板可以读取完整输入，但产物必须能被下游投影成轻量 `ChapterSourceSlice`。

### split character init
`init_character_roster.j2` 是 canonical name 源头；profile、relationship matrix 和 arc plan 都必须逐字服从 roster，不得把旧名、别名或阶段称呼扩散为新角色。

### enrich_spec.j2
在已有规格基础上进行丰富和扩展。

**主要功能**：
- 添加细节设定
- 扩展世界观
- 深化规则定义

### enrich_character.j2
丰富角色设定，增加更多细节。

**主要功能**：
- 添加角色背景
- 设计角色动机
- 完善角色关系网

### introduce_character.j2
生成角色介绍文本，用于首次出场。

### generate_config.j2
生成项目的配置文件，包含所有设定参数。

## 使用方式

```python
from novel_forge.prompts.registry import PromptRegistry
from novel_forge.core.constants import TaskType

registry = PromptRegistry()

# 初始化故事圣经
prompt = registry.render(
    TaskType.INIT_STORY_BIBLE,
    premise="故事前提...",
    genre="玄幻",
    tone="紧张"
)

# 初始化角色
prompt = registry.render(
    TaskType.INIT_CHARACTER_BIBLE,
    characters=[
        {"name": "主角", "role": "英雄"},
        {"name": "反派", "role": "对手"}
    ]
)
```

## 输出契约

初始化类模板通常输出结构化 JSON；具体 required/allowed keys 由 `TaskFormatContract` 注入并校验。

## 相关模块

- **规划模块**: `../planning/` - 使用初始化结果进行规划
- **写作模块**: `../writing/` - 在写作中使用设定
