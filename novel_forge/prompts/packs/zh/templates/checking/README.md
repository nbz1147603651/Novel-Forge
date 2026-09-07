# 检查类模板 (Checking)

质量检查和验证模板，用于检查内容的质量、一致性和连贯性。

## 模板列表

| 文件 | 用途 | 任务类型 |
|------|------|---------|
| `check_chapter.j2` | 章节质量检查 | `CHECK_CHAPTER` |
| `check_alignment.j2` | 大纲对齐检查 | `CHECK_ALIGNMENT` |
| `element_progress_arbiter.j2` | 要素灰区仲裁 | `ELEMENT_PROGRESS_ARBITER` |
| `extract_expression_observations.j2` | 表达通道观察抽取 | `EXTRACT_EXPRESSION_OBSERVATIONS` |
| `continuity_eval.j2` | 连贯性评估 | `CHECK_CONTINUITY` |
| `continuity_repair.j2` | 连贯性修复 | `REPAIR_CONTINUITY` |
| `causal_validate.j2` | 因果关系验证 | `VALIDATE_CAUSAL` |
| `causal_repair_typed.j2` | 因果链定向修复 | `REPAIR_CAUSAL` |
| `critic_continuity.j2` | 连贯性批评 | `CRITIC_CONTINUITY` |
| `critic_causal.j2` | 因果批评 | `CRITIC_CAUSAL` |
| `critic_character.j2` | 角色一致性批评 | `CRITIC_CHARACTER` |
| `critic_strengths.j2` | 优点识别 | `CRITIC_STRENGTHS` |
| `book_consistency.j2` | 全书一致性检查 | `BOOK_CONSISTENCY` |
| `book_consistency_verify.j2` | 全书审计逐章验证 | `BOOK_CONSISTENCY_VERIFY` |
| `book_editorial_audit.j2` | 全书编辑契约审计 | `BOOK_EDITORIAL_AUDIT` |
| `check_editorial.j2` | 章节编辑契约检查 | `CHECK_EDITORIAL` |
| `derive_editorial_contract.j2` | 推导全书编辑契约 | `DERIVE_EDITORIAL_CONTRACT` |
| `derive_editorial_character_voices.j2` | 推导角色声音规则 | `DERIVE_EDITORIAL_CHARACTER_VOICES` |
| `derive_editorial_element_directives.j2` | 推导叙事要素编辑指令 | `DERIVE_EDITORIAL_ELEMENT_DIRECTIVES` |
| `derive_editorial_structure.j2` | 推导结构节奏规则 | `DERIVE_EDITORIAL_STRUCTURE` |
| `derive_editorial_style_constraints.j2` | 推导风格硬约束 | `DERIVE_EDITORIAL_STYLE_CONSTRAINTS` |
| `extract_blueprint_holistic_claims.j2` | 提取蓝图整体一致性 Claims | `EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS` |
| `profile_style.j2` | 风格规范生成 | `PROFILE_STYLE` |
| `evaluate_reading_power.j2` | 追读力评估 | `EVALUATE_READING_POWER` |
| `repair_reading_power.j2` | 追读力修复 | `REPAIR_READING_POWER` |
| `repair_semantic_verify.j2` | 修复后语义验证 | `REPAIR_SEMANTIC_VERIFY` |
| `macro_guard_audit.j2` | 宏观护栏审计 | `MACRO_GUARD_AUDIT` |
| `guardrail_repair.j2` | 护栏修复 | `REPAIR_GUARDRAIL` |
| `humanize_scan.j2` | 人味扫描 | `HUMANIZE_SCAN` |
| `knowledge_boundary_audit.j2` | 知识边界审计 | `KNOWLEDGE_BOUNDARY_AUDIT` |
| `adjudicate_state_delta.j2` | 候选状态变化裁判 | `ADJUDICATE_STATE_DELTA` |
| `adjudicate_contract_completion.j2` | 章节契约完成度裁判 | `ADJUDICATE_CONTRACT_COMPLETION` |
| `adjudicate_fact_conflict.j2` | 事实冲突裁判 | `ADJUDICATE_FACT_CONFLICT` |
| `adjudicate_contract_coherence.j2` | 初始化契约自洽裁判 | `ADJUDICATE_CONTRACT_COHERENCE` |
| `adjudicate_blueprint_coherence.j2` | 初始化蓝图自洽裁判（后备） | `ADJUDICATE_BLUEPRINT_COHERENCE` |
| `adjudicate_outline_inheritance.j2` | 初始化大纲继承裁判（后备） | `ADJUDICATE_OUTLINE_INHERITANCE` |
| `extract_init_coherence_claims.j2` | 初始化一致性 Claims 抽取 | `EXTRACT_INIT_COHERENCE_CLAIMS` |
| `adjudicate_init_conflict_candidates.j2` | 初始化冲突候选裁判 | `ADJUDICATE_INIT_CONFLICT_CANDIDATES` |
| `repair_adjudicated_issue.j2` | 状态裁判问题修复 | `REPAIR_ADJUDICATED_ISSUE` |
| `structure_profile_derive.j2` | 结构档案推导 | - |
| `style_profile_derive.j2` | 风格档案推导 | - |

## 模板说明

### check_chapter.j2
检查章节质量，包括语法、表达、代词一致性和对话质量。

**检查项目**：
- 系统痕迹和元叙事
- 代词使用一致性
- 感官多样性
- 对话密度
- 说明腔禁止

### check_alignment.j2
检查章节内容与大纲规划的对齐程度。

**主要功能**：
- 验证情节点覆盖
- 检查节奏符合度
- 评估主线推进

### element_progress_arbiter.j2
对规则命中的灰区结果进行低频复判，输出统一的 `hit/weak/miss` 判定与证据理由。

### continuity_eval.j2
评估章节间的连贯性，包括时间、地点和角色状态。

### continuity_repair.j2
修复连贯性问题，提供修复建议。Repair 模板必须 ticket/window first：只修报告证据、定位窗口和验收条件覆盖的问题，source 只提供禁揭示与不可破坏边界。

### causal_validate.j2
验证事件的因果关系是否合理。

### causal_repair_typed.j2
按照问题类型执行因果链定向修复，输出可直接应用的修复结果。只能解释已发生/已允许的行为动机，不使用宏观创作规划补新主线。

### guardrail_repair.j2
根据护栏违规报告进行定向修复，严格保护主线推进点和对齐分。

**核心约束**：
- **主线推进点保护**：修复不得影响 chapter plan 中的主线推进点
- **对齐分保持约束**：修复后必须保持或提高对齐分数
- **修复范围限制**：不得修改包含主线推进点的段落

**修复策略**：
- 最小干预原则：仅修改与违规直接相关的部分
- 局部重写优先：避免全文重写
- 保持叙事连贯性
- 字数合规检查

### repair_reading_power.j2 / repair_adjudicated_issue.j2
追读力和裁判问题修复同样遵循 ticket/window first；章节契约、source 和硬事实状态只作验收边界，不是新增事实来源。

### critic_*.j2
批评代理模板，用于深度分析和评估：

| 模板 | 功能 |
|------|------|
| `critic_continuity.j2` | 跨章节连贯性分析 |
| `critic_causal.j2` | 因果链完整性分析 |
| `critic_character.j2` | 角色一致性分析 |
| `critic_strengths.j2` | 识别章节优点 |

### book_consistency.j2
检查全书范围内的一致性问题。

### profile_style.j2
根据项目要素与题材，生成结构化的风格规范模块。

## 使用方式

```python
from novel_forge.prompts.registry import PromptRegistry
from novel_forge.core.constants import TaskType

registry = PromptRegistry()

# 检查章节质量
result = registry.render(
    TaskType.CHECK_CHAPTER,
    content="章节内容...",
    canon_context={...}
)

# 检查连贯性
result = registry.render(
    TaskType.CHECK_CONTINUITY,
    previous_chapter="上一章内容...",
    current_chapter="当前章节..."
)
```

## 质量标准

所有检查类模板都遵循以下质量标准（定义在 `_base/_quality_standards.j2`）：
- 禁止系统痕迹
- 代词一致性
- 感官多样性
- 对话密度
- 说明腔禁止

## 相关模块

- **基础模块**: `_base/_quality_standards.j2` - 质量标准定义
- **输出契约**: `core/format_contracts.py` - Builder 统一注入格式契约
