# 对齐评估超时失真分数优化方案

## 问题根因

当对齐评估（alignment check）在 CriticAgent 软超时机制下被取消时，系统无法区分"分数因超时而缺失/失真"与"分数确实很低"：
- 超时后 `alignment_result=None`，触发 fallback 路径（缓存查找或重跑 AlignmentStep）
- 若 fallback 也返回失真分数（0.0 或 1.8），该分数被当作真实分数持久化到 `state.alignment_report` 和磁盘
- 失真分数流入 `_enforce_alignment_threshold` 触发 `ConsistencyViolationError`，可能引发不必要的修复甚至章节重写
- `ReadingPowerReport` 已有 `evaluation_status`/`is_fallback`/`fallback_reason` 字段解决同类问题，但 `AlignmentReport` 完全缺失

## 方案：标记 + 豁免（镜像 ReadingPowerReport 模式）

### 1. Schema 层：AlignmentReport 增加评估状态字段
**文件**: `novel_forge/core/schemas/chapter.py` (行 39-54)

在 `AlignmentReport` 中增加三个字段，完全镜像 `ReadingPowerReport`（reading_power.py:132-144）：
```python
evaluation_status: str = Field(default="ok", description="评估状态：ok/fallback/timeout")
is_fallback: bool = Field(default=False, description="是否为兜底报告。兜底报告不应参与门禁硬阻断")
fallback_reason: str = Field(default="", description="兜底原因摘要")
```

### 2. CriticAgent：超时时标记 alignment 不可用
**文件**: `novel_forge/memory/critic.py` (行 656-662, 689-727)

CritiqueReport 已携带 `metadata["execution"]["checks_timed_out"]`，但 `to_alignment_report()` 和 `run_quality_checks` 都不读它。修改：
- 在 `CritiqueReport.to_alignment_report()` (行 251-264) 增加可选参数，让调用方传入超时标记；或在 `run_quality_checks` 中直接检查 metadata

### 3. Quality checks runner：超时检测 + fallback 标记
**文件**: `novel_forge/pipeline/long/stages/quality_checks_runner.py` (行 656-662)

当 `critique_report.alignment_result is None` 时，检查 `critique_report.metadata["execution"]["checks_timed_out"]` 是否包含 "alignment"：
- 若超时且缓存不可用且 `_task_alignment_step()` 也失败/超时 → 构造带 `is_fallback=True, evaluation_status="timeout", fallback_reason="..."` 的 AlignmentReport（保留 alignment_score=0.0 但标记为不可信）
- 若超时但 `_task_alignment_step()` 成功 → 使用真实结果（不标记 fallback）

### 4. 阈值强制：豁免 fallback 分数
**文件**: `novel_forge/pipeline/long/chapter_flow_finalize.py` (行 210-286, `_enforce_alignment_threshold`)

在函数开头检查 `alignment_report.is_fallback`：
- 若 `is_fallback=True` → 不 raise `ConsistencyViolationError`，改为发 `alignment_fallback_exempt` 步骤事件并 return（放行，保留当前文本）
- 日志记录豁免原因，让运行日志可追溯

### 5. Quality gate：豁免 fallback 分数
**文件**: `novel_forge/pipeline/quality_gate.py` (行 205-273, `check_alignment` / `_alignment_projection`)

在 `_alignment_projection` 中检查 `is_fallback`：
- 若 `is_fallback=True` → `passed=True`，message 标注"评估超时，已豁免门禁检查"
- 不影响 issues 的正常收集（missing_main_points 仍记录，但不作为 hard 阻断）

### 6. AlignmentStep：截断响应检测
**文件**: `novel_forge/pipeline/steps/alignment_step.py` (行 352-374, `_execute`)

在 `_normalize_alignment_payload` 之后，检测响应是否截断/异常：
- 若 `model_score` 来自 default（即 LLM 未返回 alignment_score 字段）且有 missing_main_points → 标记 `evaluation_status="degraded", is_fallback=True`
- 这防止"部分解码的迟到响应"产生 1.8 这种失真分数

### 7. 测试
**文件**: 新增 `tests/unit/test_alignment_fallback.py`

测试用例：
- `test_alignment_report_fallback_fields_default` - 默认值为 ok/False/""
- `test_alignment_timeout_exempts_threshold` - is_fallback=True 时 `_enforce_alignment_threshold` 不 raise
- `test_alignment_timeout_exempts_gate` - is_fallback=True 时 `check_alignment` 返回 passed=True
- `test_critic_timeout_propagates_to_alignment_report` - CritiqueReport 超时元数据传播到 AlignmentReport.is_fallback
- `test_alignment_step_truncated_response_marked_fallback` - 截断响应被标记为 degraded

## 不改动的部分
- 软超时机制本身（CriticAgent 的 timeout/extend 逻辑）保持不变
- `_should_skip_alignment_recheck` 缓存逻辑保持不变（它已有 `_ALIGNMENT_CACHE_MIN_SCORE=8.0` 守卫，拒绝低分缓存）
- moderate_floor 兜底逻辑保持不变（作为第二道防线）
- `alignment_repair_edit` / `recheck_alignment` 正常路径保持不变

## 预期效果
- 超时失真分数不再触发不必要的 ConsistencyViolationError → 不再触发不必要的修复/重写
- 报告文件中明确记录超时事实（`evaluation_status`/`fallback_reason`），便于排查
- 真实低分（非超时）仍正常触发修复，不影响质量门禁的有效性
- 与 ReadingPowerReport 的 fallback 模式一致，代码风格统一