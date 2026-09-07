# AI 味质量守门系统

> M1 + M2 + M3 + M3.5 + M4 + M5 架构改进（2026-06-30）。解决《山风与归人2》编辑评估中暴露的全部四类质量问题，并把 AI 味纳入 evaluator 后处理 advisory 与离线校准闭环；LLM-as-Judge prompt/rubric 暂不接 hard gate。

## 背景

《山风与归人2》编辑评估（6.5/10）发现：

| 问题 | 数量 | 根因 |
|---|---|---|
| 弱动词堆叠 | Ch1/9/14/18/20/21/24 多处 | HumanizeScanStep 24 条规则中没有形态向检测器 |
| 「某种」三连 | Ch21 L31/L121/L133 | 同上 |
| 二元判断收束 | Ch21 L157-159 | 同上 |
| **双句号** | Ch20 等 **15 章共 55 处** | 没有章节级格式预筛 |
| **字数崩塌** | Ch18（3740 字）、Ch20（3593 字） | 没有章节级长度守门 |
| **AI 味未量化** | 整书评分不可比较 | 没有 QualityGate 第八维 |
| **关键前史展开不及时** | 网络暴力前史到 Ch18 才首次显形 | 没有"前史时间窗"约束机制 |
| **对话量与沉默设定冲突** | Ch3/Ch20 等 5 章节违反 50-70% | dialogue_ratio 是单值字段，没有"主角-配角"细分逻辑 |

六期交付：

1. **M1 — 5 条形态向 Humanize 规则 + ChapterQualityPrescreen** — 在生成后立刻捕获
2. **M2 — QualityGate.check_ai_flavor() + RepairDimension.AI_FLAVOR** — 把 AI 味变成正式质量维度
3. **M3 — StyleGoldenRetriever** — 检索本项目高分段落注入 prompt，让 LLM 看到"好是什么样"
4. **M3.5 — PromptBuilder 注入项目级高分正例** — 让 DRAFT/WAVE 生产路径吃到检索收益
5. **M4 — BackstoryRevealSpec + dialogue_ratio 拆分 + character_silence** — 把 spec 层的结构性冲突显式化
6. **M5 — EvalReport.ai_flavor_advisory + calibration 工具** — 只观测不改分，先积累分布证据

---

## M3.5 — StyleGoldenRetriever 注入 PromptBuilder（已完成）

### 1. 职责

把 `StyleGoldenRetriever`（M3 的检索能力）变成 DRAFT_CHAPTER / WAVE_CHAPTER 阶段的**生产路径收益**：prompt 渲染时根据当前 scene_intent 自动检索本项目高分段落，作为"项目级高分段落参考"注入到模板里，让 LLM 看到"这个项目里好是什么样"。

### 2. 数据流

```
prepare_long_project() 创建 LongProjectBundle.style_golden_retriever (M3 实例)
    │
    ▼
draft.py / wave.py 构造生成上下文:
    ├─ scene_intent   ←  _primary_scene_intent(plan) 扁平化 SceneIntent
    └─ style_golden_retriever
    │
    ▼
PromptBuilder.render(DRAFT_CHAPTER / WAVE_CHAPTER, context)
    │
    ├─ _with_common_optional_defaults 注入 defaults
    ├─ _resolve_style_golden_examples 调 retriever.retrieve_for_scene()
    └─ 模板里 {{ render_style_golden_examples(...) }}
    │
    ▼
最终 prompt 中多了 P2 "项目级高分段落参考" 章节
```

### 3. API 与接入点

**新模块**：`novel_forge/prompts/style_golden_helpers.py`

```python
def render_style_golden_examples(
    style_golden_examples: list[dict[str, Any]] | None = None,
    *,
    title: str = "### 项目级高分段落参考（请学习笔触，禁止逐字复用）",
) -> str:
    """Render as a markdown block. Empty list / None returns "" so the
    call site can remain unconditional."""
```

注册为 `PromptRegistry._globals` —— 这样模板可作为全局函数调用：
```jinja
{{ render_style_golden_examples(style_golden_examples | default([])) }}
```

### 4. 实现要点

- **全局函数而非 jinja macro**：跨文件 `from ... import macro` 在 jinja2 中不传播 context（macro 拿到的是 macro 自己的 scope）。改用注册到 `_globals` 的 Python 函数 —— 接受参数，零 context 传递问题。
- **`scene_intent` 单值而非 list**：SceneIntent 是 plan 里的列表，但检索一次只关心当前场景；whole-chapter DRAFT/WAVE 用首场景作为章节笔触锚点，scene-level DRAFT_SCENE 由 `build_scene_draft_context()` 按当前场景覆盖检索词。
- **空列表/无 retriever 静默通过**：模板调用永远是 `render_style_golden_examples(style_golden_examples | default([]))`，失败时只是少了一段，不影响主流程。
- **失败 graceful degrade**：`_resolve_style_golden_examples` 捕获 retriever 异常，记录 warning 后返回空列表。
- **集成到 `LongProjectBundle`**：`prepare_long_project()` 基于项目根目录和 `_chapter_meta_cache.json` 创建 `StyleGoldenRetriever`；缓存缺失、索引为空或初始化失败时降级为 `None` / 空段，不影响旧项目。
- **P2 约束**：模板把正例明确标为 P2，只学习句法节奏、细节密度、过渡与笔触；不得复写原句，不得压过 P0/P1 契约。

### 5. 端到端 demo（实际跑过的输出）

```
=== M3.5 demo on 山风与归人2 ===
Indexed chapters: [1, 2, 4, 5, 8, 9, 10, 11, 15, 16, 18, 22, 23]
Total golden passages: 586

Scene: {emotional_tone: "克制", setting: "云南风眼小镇老宅",
        pov_keywords: "沈鹿溪", scene_action: "沈鹿溪站在门槛边..."}

Retrieved 3 golden passages:
  - Ch9  (#113, score=9.71): 上午的阳光没有完全透出来，云层一直压着，
    但沈鹿溪没有再躺回去。她端着摄像机在民宿内外走了一圈...
  - Ch10 (#21,  score=9.93): 老人拄着拐杖走进来，站在门槛边...
  - Ch8  (#46,  score=9.50): 房间不大，朝北的窗户对着内院。
    窗台上放着一只小风铃——是她刚来小镇时在集市上买的，铜制的...
```

最终 DRAFT_CHAPTER / WAVE_CHAPTER prompt 中嵌入了完整的"项目级高分段落参考"章节，按 chapter + score 标注。

### 6. M3.5 文件清单

| 类型 | 路径 |
|---|---|
| 新增模块 | `novel_forge/prompts/style_golden_helpers.py` |
| 改动 PromptBuilder | `novel_forge/prompts/builder.py`（defaults + helper） |
| 改动 PromptRegistry | `novel_forge/prompts/registry.py`（注册全局函数） |
| 改动 LongProjectBundle | `novel_forge/pipeline/long/preflight.py`（+ retriever 字段 + 自动实例化） |
| 改动 draft 阶段 | `novel_forge/pipeline/long/stages/draft.py`（+ _primary_scene_intent + draft_context） |
| 改动 wave 阶段 | `novel_forge/pipeline/long/stages/wave.py`（+ scene_intent + retriever） |
| 改动 scene-level | `novel_forge/pipeline/long/services/scene_writing.py`（DRAFT_SCENE 按当前场景覆盖 scene_intent） |
| 改动模板 | `novel_forge/prompts/prompts/writing/*.j2` + `novel_forge/prompts/packs/zh/templates/writing/*.j2` |
| 新增单元测试 | `tests/unit/test_prompt_builder_style_golden.py` |
| 新增集成测试 | `tests/integration/test_m35_style_golden_integration.py` |

合计 **22 个专项测试/断言**（11 unit + 10 integration + 1 scene-level wiring）。

### 7. 与 M5 的边界

M3.5 让生成端吃到正例；M5 让评价端的 ai_flavor 维度进 LLM-as-Judge rubric。先 M3.5 再 M5 是合理的顺序 —— 否则容易出现"评价变严了，但生成端还没吃到正例"的错位。

---

## M1 — 确定性检测层（已完成）

### 1. 形态向 Humanize 规则

**新增的 5 条规则**

| pattern_id | 名称 | severity | 触发条件 |
|---|---|---|---|
| `weak_verb_stacking` | 弱动词堆叠 | high | `感到/觉得/意识到/似乎/仿佛/不禁/不由得/情不自禁` 在 40 字内出现 |
| `tautology_marker` | 抽象虚指三连 | high | 「某种...某种...某种」在 30 字跨度内出现 3 次 |
| `binary_judgment_closing` | 二元判断收束 | high | 「X 不是 Y，而是/是/正是 Z」作为句末结构 |
| `pronoun_disappearance_run` | 主语弱化句式 | medium | 「她没有...她没有...她没有」三次连续 |
| `precise_timestamp_overuse` | 精确时长堆叠 | low | 「三分二十七秒」「00:03:27」类精确时间 |

### 2. ChapterQualityPrescreen

| 类型 | severity | 检测方式 | 修复路径 |
|---|---|---|---|
| `length_floor` | critical | 字数 < 4000 | FULLTEXT_REWRITE |
| `length_ceiling` | medium | 字数 > 12000 | 拆分或裁剪 |
| `double_punctuation` | high | 双句号 / 双顿号 / `？"` 等异类配对 | regex 静态修复 |
| `repeated_char` | high | 三连以上重复字 | regex 静态修复 |
| `empty_tail` | medium | 章末只剩空白行 | 人工补钩子 |

### 3. scripts/post_format_check.py

```bash
# 离线扫描 + 自动修复
python scripts/post_format_check.py --project-root data/<project>
python scripts/post_format_check.py --project-root data/<project> --apply-fixes --in-place
```

### 4. M1 在《山风与归人2》上的实际效果

| 维度 | 修复前 | 修复后 |
|---|---|---|
| 双句号 | 15 章共 55 处 | 0 处 |
| 重复字 | 2 处 | 0 处 |
| 双引号配对 | 2 处（异类） | 0 处 |
| 字数崩塌 | Ch18、Ch20 | 仍 critical（需人工扩写） |

---

## M2 — QualityGate 第八维 + Repair Dimension（已完成）

### 1. QualityGate.check_ai_flavor()

**新增方法**：`novel_forge/pipeline/quality_gate.py`

```python
from novel_forge.pipeline.quality_gate import QualityGate

gate = QualityGate()
result = gate.check_ai_flavor(humanize_report, threshold=8.0)

# result.dimension == "ai_flavor"
# result.score ∈ [0, 10]
# result.passed = (score >= threshold)
# result.details = {
#     "hit_count": int,
#     "by_pattern_id": dict[str, int],
#     "has_critical": bool,
#     "penalty": float,
# }
```

### 评分公式

```
penalty = sum(severity_weight * confidence for hit in hits)
severity_weight = {"critical": 4.0, "high": 2.0, "medium": 1.0, "low": 0.5}
score = max(0, 10 - penalty)
if any critical hit: score = min(score, 5.0)   # critical 封顶
```

- 默认阈值 **8.0**，与计划 §2.2.4 的 rubric v3 一致
- 任意 critical 命中（合作残留、知识截止声明等）→ 分数封顶 5.0 → 必 fail
- 输入兼容：`HumanizeReport` / `list[HumanizePatternHit]` / `dict[pattern_hits]` / mock 对象

### 2. wiring 到 chapter_flow_review

**新增文件**：`novel_forge/pipeline/long/chapter_flow_review.py`

`_build_quality_gate` 现在调用 `_build_ai_flavor_report(review.current_text)` 现场跑 `HumanizeScanStep.prescreen_text`（纯 regex，无 LLM 调用），然后 `gate.check_ai_flavor(...)`。

**位置**：在 `check_revelation_density` 之后、`check_reading_power` 之前。

**配置开关**：`settings.long_ai_flavor_gate_enabled`（默认 True；False 则跳过）

### 3. RepairDimension.AI_FLAVOR

**新增枚举值**：`novel_forge/pipeline/long/repair_safety.py:33`

```python
class RepairDimension(str, Enum):
    CONTINUITY = "continuity"
    CAUSAL = "causal"
    READING_POWER = "reading_power"
    KNOWLEDGE_BOUNDARY = "knowledge_boundary"
    CONTRACT = "contract"
    GENERIC = "generic"
    AI_FLAVOR = "ai_flavor"  # M2 新增
```

让 repair 编排器可以把 ai_flavor gate 失败路由到 typed repair 路径（`FULLTEXT_REWRITE` 策略）。

---

## M3 — StyleGoldenRetriever（已完成）

### 1. 职责

从本项目已写章节中检索高评分（≥ 9.0）的段落，作为正例注入 draft_chapter.j2，让 LLM 看到"这个项目里好是什么样"而非泛泛的"请写得克制"。

### 2. API

**新增模块**：`novel_forge/memory/style_golden_retriever.py`

```python
from pathlib import Path
from novel_forge.memory.style_golden_retriever import StyleGoldenRetriever

retriever = StyleGoldenRetriever(
    project_dir=Path("data/山风与归人2"),
    eval_cache_path=Path("data/山风与归人2/_chapter_meta_cache.json"),
    threshold=9.0,
    min_paragraph_chars=60,
    max_paragraph_chars=280,
    max_per_chapter=1,
)

# Direct query
passages = retriever.retrieve_for_query("风 铃声 穿堂", max_results=3)

# Or via scene_intent dict
passages = retriever.retrieve_for_scene(
    {"emotional_tone": "克制", "setting": "云南小镇", "pov_keywords": "沈鹿溪 陈屿"},
    max_results=3,
)
```

### 3. 实现要点

- **复用现有 BM25**：`novel_forge/memory/retrieval.bm25_scores` — 无新依赖
- **零外部 IO**：索引在 `__init__` 时构建一次并缓存
- **多样性**：`max_per_chapter=1` 防止反复取样高分章节
- **优雅降级**：空 query 或 BM25 异常时，回退到按章节顺序取前 N 个

---

## M4 — 背景前史时间窗 + 沉默设定调和（已完成）

### 1. BackstoryRevealSpec — 结构化前史展开约束

**新增字段**：`novel_forge/core/schemas/spec.py`

替代自由文本 `extra_instructions` 中关于"网络暴力前史"的段落 —— 现在用结构化字段强制约束：

```python
from novel_forge.core.schemas.spec import BackstoryRevealSpec, StorySpec

spec = StorySpec(
    title="示例",
    theme="被听见不等于被审判",
    backstory_reveals=[
        BackstoryRevealSpec(
            topic="主角网络暴力前史",
            required_first_appearance=5,
            min_word_count=300,
            reveal_mode="flashback",
            triggers=["声音", "录音", "麦克风"],
        ),
        BackstoryRevealSpec(
            topic="陈屿大学风能课题",
            required_first_appearance=8,
            min_word_count=200,
            reveal_mode="dialogue_snippet",
        ),
    ],
)
```

字段含义：

- `topic`: 背景主题标识符
- `required_first_appearance`: 必须首次出现的章节号
- `min_word_count`: 首次出现时的最低字数（默认 200）
- `reveal_mode`: 建议的展开形式（flashback / third_party_expose / dialogue_snippet / object_trigger）
- `triggers`: 触发该背景信息展开的情境关键词

**向后兼容**：旧 spec.json 无此字段时 `spec.backstory_reveals == []`，所有 gate 自动通过。

### 2. QualityGate.check_backstory_reveals()

**新增方法**：`novel_forge/pipeline/quality_gate.py`

```python
gate = QualityGate()
result = gate.check_backstory_reveals(
    backstory_reveals=spec.backstory_reveals,
    chapter_number=21,
    backstory_report=type("R", (), {"cumulative": {
        "主角网络暴力前史": {"current_word_count": 250},
    }})(),
)

# result.dimension == "backstory_reveal"
# result.passed = (overdue == 0)
# result.details = {
#     "satisfied": [...],
#     "pending": [...],
#     "overdue": [...],
#     "due_count": int,
#     "grace_chapters": 2,
# }
```

每个 reveal 三种状态：
- **not_yet**: 当前章节 < deadline（静默通过）
- **pending**: deadline 到了但 current_word_count < min_word_count
- **satisfied**: 已达 min_word_count
- **overdue**: chapter_number > deadline + 2（grace）仍未达 min_word_count → **gate 失败**

生产接线：`prepare_long_project()` 只把 `character_silence` 和 `backstory_reveals`
作为有界字段投影进 `LongProjectBundle`，不把全量 `spec.json` 暴露给运行阶段；
`chapter_flow_review._build_quality_gate()` 会基于 topic 或同段多 trigger 命中扫描历史章节 +
当前章，构建保守的确定性 cumulative report 后调用 `check_backstory_reveals()`。

### 3. dialogue_ratio 拆分 + character_silence

**新增模块**：`novel_forge/pipeline/long/services/style_metrics.py`

```python
from novel_forge.pipeline.long.services.style_metrics import (
    compute_silence_aware_dialogue_ratio,
    split_dialogue_ratio,
)

# 拆分对话
split = split_dialogue_ratio(text, protagonist_names=["沈鹿溪"])
# split.protagonist_dialogue_chars, split.non_protagonist_dialogue_chars,
# split.unknown_speaker_dialogue_chars

# 计算沉默感知对话比
result = compute_silence_aware_dialogue_ratio(
    text,
    protagonist_names=["沈鹿溪"],
    character_silence=spec.character_silence,
)
# result = {
#     "total_ratio":           float,  # legacy 全章对话占比
#     "protagonist_ratio":     float,  # 主角对话占比
#     "non_protagonist_ratio": float,  # 配角对话占比
#     "effective_ratio":       float,  # 沉默模式下等于非主角；否则等于 total
# }
```

**结构性冲突的解决方案**：当 `character_silence=True` 时，`effective_ratio` 等于 `non_protagonist_ratio`。这意味着 spec.dialogue_ratio="high" 在沉默模式下不再惩罚主角的沉默——它只要求配角说够 50-70%。

生产接线：`compute_style_metrics()` 同时记录 legacy 全章对话比例和 gate-facing
effective ratio；`style_metrics_to_quality_check()` 在沉默模式下使用非主角对话比例
评分，同时在 details 中保留 `total_dialogue_ratio_pct` 供诊断。

### 4. 端到端 demo 验证（实际跑过的输出）

```python
# 加载真实山风与归人2 spec —— 向后兼容
spec = StorySpec.model_validate(json.loads(open("data/山风与归人2/spec.json").read()))
# spec.backstory_reveals == [], spec.character_silence == False

# Ch21 dialogue analysis 揭示结构性事实
text = open("data/山风与归人2/chapters/chapter_021.md").read()
result = compute_silence_aware_dialogue_ratio(
    text, protagonist_names=["沈鹿溪"], character_silence=False,
)
# total_ratio:           77.53%   ← 全章对话确实够
# protagonist_ratio:     0.0%     ← 主角完全没说话
# non_protagonist_ratio: 30.36%   ← 配角承担对话
```

### 5. spec example 文件

`data/spec_examples/spec_with_backstory_window.example.json` —— 完整示例 spec，演示：
- `character_silence: true`
- 3 个结构化的 `backstory_reveals`（覆盖网络暴力前史、风能课题、离婚协议）
- 与《山风与归人2》的真实约束问题对应

---

## M5 — rubric v3 advisory + calibration 工具（已完成，advisory-only）

### 1. 设计取舍（按上一轮 review 建议）

不立即把 ai_flavor 设为 hard gate。原因是：

- 生成端（M3.5）刚能稳定吃到正例；评分端突然变严会形成"评价变严了，生成端还没来得及吸收"的错位。
- Hard gate 一旦接入，会反向 force 章节修复（走 `RepairDimension.AI_FLAVOR`），容易把风格偏好硬编码成不可解释的惩罚。

所以 M5 的策略是：**advisory-only + 校准分布**。advisory 块落到 `EvalReport.ai_flavor_advisory` 字段，但不进 `scores` / `overall_score` / `passed` 的计算路径。

### 2. 改动点

**EvalReport schema**（`novel_forge/core/schemas/eval_schema.py`）

新增 `ai_flavor_advisory: dict[str, Any]` 字段，默认空 dict —— 完全向后兼容旧报告。

**DraftEvaluator**（`novel_forge/eval/evaluator.py`）

```python
class DraftEvaluator:
    RUBRIC_VERSION: str = "2026-06-30.evaluate_draft.v3"  # bumped from v2

    @staticmethod
    def _build_ai_flavor_advisory(humanize_report) -> dict:
        """Deterministic advisory block — does NOT touch scores or passed."""
```

`evaluate(..., humanize_report=...)` 或 `evaluate(..., ai_flavor_hits=...)` 会在解析成功、JSON 重试成功、默认 fallback 三条返回路径上填充 advisory。没有传入来源时保持 `ai_flavor_advisory == {}`；传入空 hits 时写入 `hit_count=0 / deterministic_score=10.0` 的空 advisory，便于校准脚本区分"未采样"和"已采样且干净"。原始 `humanize_report` / `ai_flavor_hits` 不会进入 prompt context，避免把内部报告对象泄漏给 `evaluate_draft.j2`。

advisory 形状：
```python
{
  "hit_count": int,
  "critical_count": int,
  "by_pattern_id": dict[str, int],
  "by_severity": dict[str, int],   # always has all 4 keys, 0 if empty
  "deterministic_score": float,    # 0..10, critical caps at 5.0
  "evidence": [{"pattern_id", "severity", "confidence", "evidence_quote"}],
  "rubric_version": str,
}
```

计算公式与 `QualityGate.check_ai_flavor()` 完全相同：severity weight 相同、confidence clamp 到 `0..1`、critical 命中把分数封顶到 `5.0`。结果只填 advisory 字段，不参与 `compute_overall()` 平均。

### 3. 校准工具：`scripts/calibrate_ai_flavor_distribution.py`

不修改任何 rubric、不接 hard gate、纯观察用途：

```bash
# 单项目
python scripts/calibrate_ai_flavor_distribution.py --project-root data/山风与归人2

# 多项目
python scripts/calibrate_ai_flavor_distribution.py --project-root data

# JSON 输出
python scripts/calibrate_ai_flavor_distribution.py --json > calibration.json
```

未回填时的输出样例（实测）：
```
=== 山风与归人2 ===
  Reports scanned: 24  (with advisory: 0)
  Note: no eval reports with ai_flavor_advisory

Reminder: this is calibration only. The advisory block DOES NOT modify scores...
```

> 注意：真实的山风与归人2 eval cache 是 v2 时代的产物，没有 advisory 字段。**这恰恰证明 advisory 是非破坏性新增**。需要历史校准时，先用 backfill 工具回填。

### 4. 回填工具：`scripts/backfill_ai_flavor_advisory.py`

默认 dry-run，不修改任何章节或评分；只在传 `--in-place` 时，把已有 `chapter_NNN_humanize.json` 派生出的 advisory 写回 `chapter_NNN_eval.json`：

```bash
# dry-run：统计可回填章节
python scripts/backfill_ai_flavor_advisory.py --project-root data/山风与归人2

# 实际写入 advisory；不改 scores / overall_score / passed
python scripts/backfill_ai_flavor_advisory.py --project-root data/山风与归人2 --in-place

# 已有 advisory 默认跳过，需要重算才显式 overwrite
python scripts/backfill_ai_flavor_advisory.py --project-root data/山风与归人2 --in-place --overwrite
```

这个脚本解决旧 eval cache 没有 advisory 的问题，让 calibration 可以基于历史章节积累样本，而不是依赖手工 backfill。

《山风与归人2》dry-run 实测：
```
=== 山风与归人2 ===
  Reports: 24 | would_update: 24 | updated: 0 | existing: 0 | missing_humanize: 0 | errors: 0
```

### 5. 升级到 rubric v4（hard gate）的触发条件

校准完成后，根据下面的信号决定是否把 advisory 升级为正式 score 维度：

1. **样本覆盖**：至少 `30` 个有 advisory 的章节，且覆盖 `3+` 个项目；纳入统计的项目 advisory 覆盖率应达到 `80%+`，避免只看手动 backfill 的个别章节。
2. **人工分层后的分离度**：把章节分成"可接受"与"需修复"两组后，可接受组 `p25 >= 8`，需修复组 `p75 <= 4`。这两个分位数必须来自不同标注组，不能对同一个总体分布同时判断。
3. **中段不过度拥挤**：若大多数章节落在 `5-8` 的灰区，说明规则更像风格偏好而非质量故障，不应升级为 hard gate。
4. **跨项目稳定性**：Top patterns 在 `3+` 项目中重复出现，而不是单一项目的叙事风格特征。

### 6. M5 文件清单

| 类型 | 路径 |
|---|---|
| 改动 EvalReport | `novel_forge/core/schemas/eval_schema.py` |
| 改动 evaluator | `novel_forge/eval/evaluator.py`（+helper、+RUBRIC_VERSION v3） |
| 新增脚本 | `scripts/calibrate_ai_flavor_distribution.py` |
| 新增脚本 | `scripts/backfill_ai_flavor_advisory.py` |
| 新增单元测试 | `tests/unit/test_evaluator_ai_flavor_advisory.py`（18 个） |
| 新增单元测试 | `tests/unit/test_calibrate_ai_flavor_distribution.py`（18 个） |
| 新增单元测试 | `tests/unit/test_backfill_ai_flavor_advisory.py`（10 个） |

### 7. 模板侧不改动

`prompts/prompts/writing/evaluate_draft.j2`（v2 模板）和 `packs/zh/templates/writing/evaluate_draft.j2` 保持 v2 不动 —— 因为 advisory 是 evaluator 后处理而非 prompt 注入。如果未来要把 advisory 反馈到 prompt 里做"自我批评"，才需要扩展模板。

---

## M1+M2+M3+M3.5+M4+M5 文件清单

### 新增模块

| 路径 |
|---|
| `novel_forge/pipeline/steps/chapter_quality_prescreen.py` |
| `novel_forge/memory/style_golden_retriever.py` |
| `novel_forge/prompts/style_golden_helpers.py` |

### 新增脚本 + example

| 路径 |
|---|
| `scripts/post_format_check.py` |
| `scripts/backfill_ai_flavor_advisory.py` |
| `scripts/calibrate_ai_flavor_distribution.py` |
| `data/spec_examples/spec_with_backstory_window.example.json` |

### 改动代码

| 路径 | 改动 |
|---|---|
| `novel_forge/pipeline/steps/humanize_scan_step.py` | +5 形态向检测规则 |
| `novel_forge/core/schemas/humanize.py` | +5 HUMANIZE_PATTERN_IDS |
| `novel_forge/core/schemas/humanize_library.py` | +6 LIBRARY_BUILTIN_ENTRIES |
| `novel_forge/core/schemas/spec.py` | +`BackstoryRevealSpec` + `backstory_reveals` + `character_silence` |
| `novel_forge/pipeline/quality_gate.py` | +`check_ai_flavor()` + `check_backstory_reveals()` |
| `novel_forge/pipeline/long/preflight.py` | 投影 `character_silence` / `backstory_reveals` / `style_golden_retriever` 到运行期 bundle |
| `novel_forge/pipeline/long/chapter_flow_review.py` | +`_build_ai_flavor_report()` + backstory/style gate wiring |
| `novel_forge/pipeline/long/repair_safety.py` | +`RepairDimension.AI_FLAVOR` |
| `novel_forge/pipeline/long/services/style_metrics.py` | +`DialogueSpeakerSplit` + silence-aware effective ratio/report details |
| `novel_forge/pipeline/long/stages/draft.py` | +`_primary_scene_intent` + 注入 `scene_intent` / `style_golden_retriever` 到 draft_context |
| `novel_forge/pipeline/long/stages/wave.py` | 注入 `scene_intent` / `style_golden_retriever` 到 wave_context |
| `novel_forge/pipeline/long/services/scene_writing.py` | DRAFT_SCENE 按当前场景覆盖 `scene_intent` |
| `novel_forge/prompts/builder.py` | +`style_golden_*` defaults + `_resolve_style_golden_examples` |
| `novel_forge/prompts/registry.py` | 注册 `render_style_golden_examples` 为全局函数 |
| `novel_forge/prompts/prompts/writing/draft_chapter.j2` | +P2 风格正例块 |
| `novel_forge/prompts/prompts/writing/wave_chapter.j2` | +P2 风格正例块 |
| `novel_forge/prompts/packs/zh/templates/writing/draft_chapter.j2` | +`render_style_golden_examples` 调用 |
| `novel_forge/prompts/packs/zh/templates/writing/wave_chapter.j2` | +`render_style_golden_examples` 调用 |
| `novel_forge/core/schemas/eval_schema.py` | +`EvalReport.ai_flavor_advisory` 字段（M5，非破坏性新增） |
| `novel_forge/eval/evaluator.py` | +`_build_ai_flavor_advisory()` + `RUBRIC_VERSION` v3 + advisory stamping |
| `scripts/calibrate_ai_flavor_distribution.py` | 离线校准脚本（M5，advisory-only） |
| `scripts/backfill_ai_flavor_advisory.py` | 从 humanize report 安全回填 eval advisory（默认 dry-run） |

### 新增测试

| 路径 | 测试数 |
|---|---|
| `tests/unit/test_chapter_quality_prescreen.py` | 32 |
| `tests/unit/test_ai_flavor_rules.py` | 24 |
| `tests/unit/test_quality_gate_ai_flavor.py` | 17 |
| `tests/unit/test_chapter_flow_ai_flavor_wiring.py` | 7 |
| `tests/unit/test_ai_flavor_repair_dimension.py` | 6 |
| `tests/unit/test_style_golden_retriever.py` | 13 |
| `tests/unit/test_prompt_builder_style_golden.py` | 11 |
| `tests/unit/test_backstory_reveal_spec.py` | 15 |
| `tests/unit/test_quality_gate_backstory.py` | 13 |
| `tests/unit/test_dialogue_ratio_split.py` | 13 |
| `tests/unit/test_style_metrics.py` | +2 |
| `tests/unit/test_evaluator_ai_flavor_advisory.py` | 18 |
| `tests/unit/test_calibrate_ai_flavor_distribution.py` | 18 |
| `tests/unit/test_backfill_ai_flavor_advisory.py` | 10 |
| `tests/regression/test_shanfeng_prescreen_clears.py` | 28 |
| `tests/integration/test_m35_style_golden_integration.py` | 10 |
| `tests/unit/test_scene_writing_mode.py` | +1 |

### 改动测试期望

| 路径 | 原因 |
|---|---|
| `tests/unit/test_humanize_library_schema.py` | count 24 → 30 |
| `tests/unit/test_humanize_library_store.py` | 同上 |

合计 **238 个新增专项测试/断言 + 改动 2 个测试期望**，全部通过；不破坏任何现有测试。

---

## 后续里程碑

| 里程碑 | 状态 |
|---|---|
| M1 — 确定性检测层 | ✅ 完成 |
| M2 — QualityGate 第八维 + Repair Dimension | ✅ 完成 |
| M3 — StyleGoldenRetriever 模块 | ✅ 完成 |
| M3.5 — prompt 模板注入高分正例 | ✅ 完成 |
| M4 — 背景前史时间窗 + 沉默设定调和 | ✅ 完成 |
| M5 — rubric v3 advisory 块 + calibration 工具 | ✅ 完成（advisory-only，不接 hard gate） |

---

## 关键测试覆盖一览

| 测试文件 | 数量 | 覆盖范围 |
|---|---|---|
| `tests/unit/test_ai_flavor_rules.py` | 24 | 5 条新规则 hit/no-hit + 山风 Ch21 样例 |
| `tests/unit/test_chapter_quality_prescreen.py` | 32 | 5 种检测项 + 山风 Ch20 回归 + apply_format_fixes |
| `tests/unit/test_quality_gate_ai_flavor.py` | 17 | 评分公式、critical 封顶、阈值覆盖、真实 HumanizeReport |
| `tests/unit/test_chapter_flow_ai_flavor_wiring.py` | 7 | AI flavor wiring、backstory gate wiring、silence-aware style gate wiring |
| `tests/unit/test_ai_flavor_repair_dimension.py` | 6 | enum 值稳定性 + 向后兼容 |
| `tests/unit/test_style_golden_retriever.py` | 13 | 索引、阈值、多样性、fallback |
| `tests/unit/test_prompt_builder_style_golden.py` | 11 | PromptBuilder defaults、retriever 调用、空值/配置异常降级 |
| `tests/integration/test_m35_style_golden_integration.py` | 10 | DRAFT/WAVE prompt 注入、preflight 自动实例化 retriever |
| `tests/unit/test_backstory_reveal_spec.py` | 15 | schema 验证、JSON round-trip、向后兼容 |
| `tests/unit/test_quality_gate_backstory.py` | 13 | pending/satisfied/overdue 三态、grace window、score 公式 |
| `tests/unit/test_dialogue_ratio_split.py` | 13 | speaker attribution、protagonist 匹配、silence-aware effective ratio |
| `tests/unit/test_style_metrics.py` | +2 | silence-aware report/details 生产路径 |
| `tests/unit/test_evaluator_ai_flavor_advisory.py` | 18 | advisory helper、schema、evaluate 成功/重试/fallback stamping、公式一致性 |
| `tests/unit/test_calibrate_ai_flavor_distribution.py` | 18 | advisory 分布统计、项目发现、JSON/文本 CLI 输出 |
| `tests/unit/test_backfill_ai_flavor_advisory.py` | 10 | dry-run/in-place 回填、overwrite、缺 humanize、项目过滤 |
| `tests/unit/test_scene_writing_mode.py` | +1 | DRAFT_SCENE 当前场景 style_golden scene_intent |
| `tests/regression/test_shanfeng_prescreen_clears.py` | 28 | 端到端不变量：24 章零格式 hit + Ch18/20/Ch6 仍 critical |

**全部 238 个新增专项测试/断言通过**；不破坏 8247 个现有测试。
