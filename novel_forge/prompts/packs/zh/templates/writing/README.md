# 写作类模板 (Writing)

核心写作任务模板，用于生成和编辑小说章节内容。

## 模板列表

| 文件 | 用途 | 任务类型 |
|------|------|---------|
| `draft_chapter.j2` | DRAFT 原稿生成 | `DRAFT_CHAPTER` |
| `draft_scene.j2` | 场景级草稿生成 | `DRAFT_SCENE` |
| `wave_chapter.j2` | 初稿成章：跨场景编织 | `WAVE_CHAPTER` |
| `edit_chapter.j2` | 章节编辑 | `EDIT_CHAPTER` |
| `bridge_chapter.j2` | 章节桥接 | `BRIDGE_CHAPTER` |
| `polish_chapter.j2` | 章节润色 | `POLISH_CHAPTER` |
| `patch_chapter.j2` | 章节修补 | `PATCH_CHAPTER` |
| `edit_draft.j2` | 草稿编辑 | `EDIT` |
| `evaluate_draft.j2` | 草稿评估 | `EVALUATE` |

## 模板说明

### draft_chapter.j2
生成章节 DRAFT 原稿，是 Generate 阶段前半段。只消费 `stage_cards.source` 的本章投影、`PlanArtifact.opening_bridge`、PlanArtifact 的 scene intents 和局部风格/声纹投影；BridgeArtifact 已在 Planning 阶段吸收到 Plan，不作为 DRAFT 独立输入。

**主要功能**：
- 应用写作风格（网文/文学/通用）
- 遵循 POV 角色锁定
- 保持与规范的一致性
- 支持题材自适应

**关键变量**：
- `chapter_number`: 章节编号
- `chapter_title`: 章节标题
- `pov_character`: POV 角色
- `genre`: 题材类型
- `style_profile`: 项目专属风格规范
- `plan`: 章节规划

### wave_chapter.j2
把 DRAFT 原稿编织成可审初稿。WAVE 只消费 DraftArtifact 文本、PlanArtifact 的 `cross_scene_intent`、`stage_cards.source.forbidden_reveal_boundaries` 和局部声纹/风格投影，补足跨场景过渡、引用回环和节奏曲线；不得新增场景、事实或确认式揭示。

### edit_chapter.j2
在草稿基础上进行精细编辑，提升文字质量和叙事效果。编辑只处理当前正文、报告 ticket、字数事务与必要 source 边界，不重新规划章节。

**主要功能**：
- 改进对话质量
- 优化节奏和情绪表达
- 保持代词一致性
- 提升感官多样性

### bridge_chapter.j2
生成章节之间的桥接内容，确保故事流畅过渡。

### polish_chapter.j2
对章节进行润色，提升整体文字质量。Polish 只消费已修正文、表达问题和风格投影，不能新增剧情事实、角色认知或事件 ledger。

### patch_chapter.j2
修补章节中的问题和漏洞。

## 使用方式

```python
from novel_forge.prompts.registry import PromptRegistry
from novel_forge.core.constants import TaskType

registry = PromptRegistry()

# 生成章节草稿
prompt = registry.render(
    TaskType.DRAFT_CHAPTER,
    style_profile={...},
    chapter_number=1,
    chapter_title="觉醒",
    pov_character="主角",
    genre="玄幻",
    plan={...},
    canon_context={...}
)
```

## 相关模块

- **基础模块**: `_base/_quality_standards.j2`
- **风格模块**: `_styles/_render_style_profile.j2`（LLM 动态推导，非静态模板）
- **规划模板**: `../planning/`
