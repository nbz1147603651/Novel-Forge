# Novel Forge 全面优化设计 Spec（v2.3 — 二次校正修正版）

> **日期**：2026-07-04 | **commit baseline**：`506e3502` | **状态**：v2.3（基于代码实测 + 二次反馈校正 §4.1 实施细节与正文数字）
> **当前本地 HEAD**：`f9a89c56`（领先 origin/main 1 commit，未 push）

---

## 0. v2.3 vs v2.2 的关键修正

| 修正项 | v2.2 错误 | v2.3 实测校正 |
|--------|----------|---------------|
| **§4.1 bug 场景** | "WAVE 失败 → fallback 到 DRAFT 文本 → 仍标 draft_done" | 错误：当前代码 `_recover_latest_draft`（line 791-816）**明确排除** `v0_draft.md`（line 799 注释"deliberately not recoverable"），且循环 range(10, 0, -1) 也跳过了 version 0。**真实可恢复路径**仅 `v1_wave.md` + legacy `v1+_edited.md`，**不会**回退到 raw DRAFT |
| **§4.1 真正问题** | （同上） | **legacy `v1+_edited.md` 也可作为 draft_done 恢复**（line 811-815），但 `performed_edits` 字段设为了 `ver`（如 1/2/.../10），**没有标志说明这是"已经编辑过"还是"未经 WAVE 处理"**。这导致：legacy 恢复的章节被认为"已完成 WAVE"，但实际上从未跑 WAVE |
| **§4.1 修复方案** | "加 `wave_completed` + `_review_resume_plan` 区分" | **保留 `wave_completed` 字段**，但需配套：(1) `_RecoveredDraft` 加 `source_kind: Literal["wave_md", "legacy_edited", "raw_draft"]` 字段，(2) `_recover_latest_draft` 显式标注来源（"wave_md" → wave_completed=True；"legacy_edited" → wave_completed=False；(3) 新增 `wave_existing_draft()` helper 函数，复用 `prepare_generate_context()` + `apply_wave()`（不重跑 DRAFT） |
| **§4.1 实施细节** | "_generate_and_wave 接受 skip_wave 参数" | 当前 `generate_chapter_prose` 是 DRAFT+WAVE 整体封装（`chapter_flow_generate.py:347-396`），**不存在** wave-only 调用。必须新增 `wave_existing_draft(runner, bundle, packet, bridge, plan, draft_text, chapter_number, trace)` 复用 DRAFT 上下文但跳过 `generate_draft()` |
| **baseline commit** | `ffbd9ce5` | **`506e3502`**（当前 origin/main HEAD）—— v2.3 改为正确基线 |
| **正文数字** | 大量硬编码（4392、4182、2887、3930、3310、4159+4057、1857、2801 等） | 全部标注"由 `code-stats.json` 在 S0 生成时填入"，不再作为事实断言 |

**修正理由**：v2.2 在 §4.1 的 bug 场景描述"raw DRAFT fallback"与代码实际行为不符。代码明确只恢复 `v1_wave.md` 和 legacy `v1+_edited.md`，**不会**回退到 `v0_draft.md`。真正的 bug 场景是 legacy 恢复路径丢失 WAVE 状态信息。

---

## 1. 项目背景与现状

**Novel Forge** 是 Python async 多模型小说写作系统（PySide6 + FastAPI + Typer）。规模数据全部由 S0 的 `scripts/generate_doc_stats.py` 在首次执行时生成，存档到 `docs/superpowers/stats/code-stats.json`。本 spec 不预设任何规模数字。

### 1.1 做得好的地方（保留不动）
- Facade 模式（`workspace/execution.py` 44 re-export + `chapter_flow.py` 74 re-export）零业务逻辑
- `PipelineStep[X,Y]` 框架统一 retry/escalation/tracing（步骤数由 code-stats.json 提供）
- `ChapterSourceSlice` bounded projection 真正落地
- 不可变数据流（`pipeline/long/` 范围内 frozen dataclass + replace 模式，命中数由 code-stats.json 提供）
- 异步隔离（每 worker 独立 loop）+ Hot-reload + sleep inhibitor
- **WAVE 双层防御**（v2.3 已校正理解）：`_ReviewResumePlan` 内存层 skip + `_recover_latest_draft` 紧急恢复（仅 `v1_wave.md` + legacy `v1+_edited.md`，**不**回退 raw DRAFT）

### 1.2 积累的技术债（按严重度，不含具体数字）
| 类别 | 描述 | 严重度 |
|------|------|--------|
| 严重 Bug（数据正确性） | 7 项（详见 Sprint 1） | 🔴 |
| 反向依赖（破坏分层） | 4 处（core/common/persistence/prompts 偷着依赖业务） | 🔴 |
| God Object | `LongProjectBundle`（非 frozen 23 字段）+ `GenerateContext`（20 字段） | 🔴 |
| God Function | `_run_quality_and_repairs`（行数由 code-stats.json 提供） | 🟠 |
| 巨型文件 | 多个 ≥ ~2000 行文件（具体列表与行数由 code-stats.json 提供） | 🟠 |
| 零测试模块 | 部分 service/stage 缺测试（具体列表由 S0 characterization 生成） | 🟠 |
| 任务类型 / 契约漂移 | 实际 enum 数与文档标称不一致 | 🟡 |
| 文档时差 | MANUAL / AGENTS / README 时差 > 7 天 | 🟡 |

---

## 2. 总体策略（5 阶段 / 8 周）

| 阶段 | 周期 | 主题 | 关键交付物 |
|------|------|------|------------|
| **S0** | 第 1 周 | **基线**（benchmark + characterization tests + code-stats.json） | 5 项 baseline JSON + 5 项 characterization tests + 1 份 code-stats.json |
| **S1** | 第 2-3 周 | **Bug 修复**（7 项 + 每项带 regression + characterization） | PR × 7 + 每 PR 配 2 测试 |
| **S2** | 第 4 周 | **God function 渐进拆分**（`_run_quality_and_repairs`） | 先抽 helper，再引入 RepairLane Protocol |
| **S3** | 第 5-7 周 | **God object 拆解**（LongProjectBundle mutation 分离 → 冻结） | `ChapterRuntimeState` + frozen bundle + 6 重验证 |
| **S4** | 第 8 周 | **注册表 + 文档**（保守化） | ADAPTER_REGISTRY + FormatContractRegistry + 文档自动生成 |

**原则**：
- 改动前必先有 baseline benchmark 或 characterization test
- 每次 mutation 分离必跑完整 6 阶段 pipeline 验证
- 公共 API 零变化
- 旧实现保留 ≥ 2 sprint
- 所有 PR 带 regression + characterization test
- CI lint 全过
- **所有验收数字由 S0 生成的 `code-stats.json` 提供**

---

## 3. Sprint 0：基线（前置）

### 3.0 code-stats.json 生成（**v2.3 新增前置**）
- **新增脚本**：`scripts/generate_doc_stats.py`
- **首次执行**：扫描代码库，生成 `docs/superpowers/stats/code-stats.json`
- **生成字段**（全部机器生成，不手写）：
  ```json
  {
    "generated_at": "<ISO timestamp>",
    "commit": "<current HEAD>",
    "novel_forge": {
      "py_files": <int>,
      "total_lines": <int>,
      "test_files": <int>
    },
    "task_type": {
      "enum_count": <int>,
      "contract_instantiations": <int>
    },
    "long_project_bundle": {
      "production_references": <int>,
      "fields": <int>
    },
    "giant_files": [
      {"path": "...", "lines": <int>},
      ...
    ],
    "untested_modules": [
      "...",
      ...
    ]
  }
  ```
- **CI 引用**：所有验收项、PR 描述、文档均从该文件读取数字

### 3.1 性能基线 benchmark
- **新增脚本**：`scripts/benchmark_pipeline_baseline.py`
- **测量 5 项关键路径**：
  1. 6 阶段长篇完整跑（总耗时 / token 数 / checkpoint 写入次数）
  2. WAVE 单次（耗时 / 输入输出比）
  3. 修复循环（continuity + causal + reading_power）（3 维度总耗时 / retry 次数）
  4. SQLite 读写（kernel）（单次 write / read / rollback 耗时）
  5. Memory context LRU（LRU hit rate / eviction count）
- **Mock 模式**：`MockAdapter` 跑固定 prompt，3 次取中位数
- **输出**：`docs/superpowers/baselines/<date>-baseline.json`（P50/P95，git 跟踪）
- **对比工具**：`scripts/benchmark_pipeline_baseline.py --compare-with baseline.json`

### 3.2 Characterization tests（5 项 + WAVE checkpoint 修正版）
| 路径 | 测试文件 | 钉死行为（v2.3 校正） |
|------|----------|----------------------|
| checkpoint/resume | `tests/characterization/test_review_checkpoint_resume.py` | `ReviewProgressState.completed_stage` 各值语义 + `wave_completed` 字段逻辑 + `_review_resume_plan` 计算 + `_recover_latest_draft` **仅**恢复 `v1_wave.md` 和 `v1+_edited.md`（**不**恢复 `v0_draft.md`） |
| repair fallback | `tests/characterization/test_repair_fallback_policies.py` | `RepairFailurePolicy` 各失败类型的 fallback action |
| cancel artifact preservation | `tests/characterization/test_cancel_job_artifact.py` | cancel 后 `_cancelling` 集合行为 + checkpoint 保留 |
| runtime reload | `tests/characterization/test_runtime_hot_reload.py` | `is_config_stale` 判定 + `reload_runtime_dependencies` 后 memory context 释放 |
| LongProjectBundle mutation surface | `tests/characterization/test_bundle_mutation_surface.py` | 哪些字段被运行时修改（`replan_context` / `character_bible` / `chapter_source_slice` / `stage_memory_builder` 注入） |

- **价值**：明确 mutation 边界与 WAVE 恢复边界，S1/S3 改动不会破坏隐式契约

---

## 4. Sprint 1：Bug 修复（7 项 + 每项配 2 测试）

### 4.1 WAVE 阶段 checkpoint 语义（v2.3 校正版）

**实测背景**（基于 `pipeline/long/chapter_flow_orchestrate.py` 与 `chapter_flow_generate.py` 阅读）：
- `ReviewProgressState.completed_stage` 注释"DRAFT + WAVE generate handoff completed"（line 123）
- `_ReviewResumePlan`（line 977-986）已有 `skip_draft: bool` 内部字段
- `_recover_latest_draft`（line 791-816）：
  - 优先 `chapter_wave_draft_path`（即 `v1_wave.md`）→ 视为 WAVE 完成
  - fallback 到 `chapter_draft_path(chapter, ver)` for `ver in range(10, 0, -1)` → legacy `v1+_edited.md` 恢复
  - **明确不恢复** `v0_draft.md`（line 799 注释 "Raw v0_draft.md is deliberately not recoverable"）
  - 恢复的 `_RecoveredDraft` 只携带 `(text, performed_edits)`，**没有**标注来源是否已经过 WAVE
- `generate_chapter_prose`（`chapter_flow_generate.py:347-396`）是 **DRAFT+WAVE 整体封装**，内部：`prepare_generate_context()` → `generate_draft()` → `apply_wave()`，**没有** wave-only 调用

**真正的 bug 场景**（v2.3 校正）：
- legacy 章节（pre-WAVE）走 `_recover_latest_draft` 的 fallback 路径，恢复 `v1+_edited.md`
- `_RecoveredDraft.performed_edits = ver`（如 1/2/.../10），**未标注**该文本是"legacy edited"还是"已经过 WAVE"
- 紧急 checkpoint 写 `completed_stage="draft_done"` + `current_text=legacy_text`
- Resume 时 `_review_resume_plan` 计算 `skip_draft=True`，从 `resume_progress.current_text` 恢复 legacy 文本进入 review
- **但** legacy 文本从未跑过 WAVE，**等同于跳过了 WAVE 的章节质量门槛**

**修复方案**（v2.3 详细实施）：

#### 4.1.1 扩展 `_RecoveredDraft` 数据类
```python
# novel_forge/pipeline/long/chapter_flow_orchestrate.py:774-777
@dataclasses.dataclass(frozen=True)
class _RecoveredDraft:
    text: str
    performed_edits: int
    source_kind: Literal["wave_md", "legacy_edited", "raw_draft"] = "legacy_edited"
    """v2.3 新增：标识恢复来源
    - wave_md: 从 chapter_wave_draft_path 恢复，已过 WAVE
    - legacy_edited: 从 chapter_draft_path(ver>=1) 恢复，未过 WAVE
    - raw_draft: 从 chapter_draft_path(ver=0) 恢复（v2.3 起允许，但需明确标注）
    """
```

#### 4.1.2 `_recover_latest_draft` 显式标注来源
```python
# novel_forge/pipeline/long/chapter_flow_orchestrate.py:791-816
def _recover_latest_draft(storage, layout, chapter_number) -> _RecoveredDraft | None:
    wave_path_fn = getattr(layout, "chapter_wave_draft_path", None)
    if callable(wave_path_fn):
        wave_text = _load_recoverable_text(storage, wave_path_fn(chapter_number))
        if wave_text is not None:
            return _RecoveredDraft(
                text=wave_text, performed_edits=1, source_kind="wave_md"
            )
    # Legacy: pre-WAVE vN_edited.md 快照（v >= 1）
    for ver in range(10, 0, -1):
        path = layout.chapter_draft_path(chapter_number, ver)
        text = _load_recoverable_text(storage, path)
        if text is not None:
            return _RecoveredDraft(
                text=text, performed_edits=ver, source_kind="legacy_edited"
            )
    # v2.3 新增：允许 raw DRAFT 恢复（场景：WAVE 失败且未落盘 v1_wave.md）
    raw_path = layout.chapter_draft_path(chapter_number, 0)
    raw_text = _load_recoverable_text(storage, raw_path)
    if raw_text is not None:
        return _RecoveredDraft(
            text=raw_text, performed_edits=0, source_kind="raw_draft"
        )
    return None
```

#### 4.1.3 紧急 checkpoint 携带 `wave_completed`
```python
# novel_forge/pipeline/long/chapter_flow_orchestrate.py:1095-1119
try:
    ...
except (asyncio.CancelledError, KeyboardInterrupt, ModelGatewayError):
    _recovered = _recover_latest_draft(context.storage, bundle.layout, chapter_number)
    if _recovered:
        try:
            save_review_progress(
                storage=context.storage,
                layout=bundle.layout,
                chapter_number=chapter_number,
                progress=_ReviewProgressState(
                    completed_stage="draft_done",
                    current_text=_recovered.text,
                    performed_edits=_recovered.performed_edits,
                    wave_completed=(_recovered.source_kind == "wave_md"),  # v2.3 新增
                ),
            )
            ...
```

#### 4.1.4 正常成功路径也携带 `wave_completed=True`
```python
# novel_forge/pipeline/long/chapter_flow_orchestrate.py:1121-1131
save_review_progress(
    ...,
    progress=_ReviewProgressState(
        completed_stage="draft_done",
        current_text=state.current_text,
        performed_edits=state.performed_edits,
        wave_completed=True,  # v2.3 新增：WAVE 已完成
    ),
)
```

#### 4.1.5 `ReviewProgressState` 加 `wave_completed` 字段
```python
# novel_forge/workspace/chapter_session_state.py:114-138
class ReviewProgressState(BaseModel):
    completed_stage: Literal["draft_done", "quality_done", ...]  # 不变
    wave_completed: bool = False  # v2.3 新增：仅 DRAFT 完成 vs DRAFT+WAVE 完成
    current_text: str
    ...
```

#### 4.1.6 `_review_resume_plan` 新增 `skip_wave` 计算
```python
# novel_forge/pipeline/long/chapter_flow_orchestrate.py:977-1006
@dataclasses.dataclass(frozen=True)
class _ReviewResumePlan:
    stage: str | None
    skip_draft: bool
    skip_wave: bool  # v2.3 新增
    skip_quality: bool
    skip_causal_repair: bool
    skip_repair: bool
    skip_extract: bool
    run_text_refinement: bool

def _review_resume_plan(
    resume_stage: str | None,
    wave_completed: bool = True,  # v2.3 新增参数
) -> _ReviewResumePlan:
    stage = str(resume_stage or "").strip() or None
    if stage not in _REVIEW_PROGRESS_STAGE_ORDER:
        stage = None
    completed = -1
    if stage is not None:
        completed = _REVIEW_PROGRESS_STAGE_ORDER.index(stage)
    skip_extract = completed >= _REVIEW_PROGRESS_STAGE_ORDER.index("canon_done")
    skip_draft = completed >= _REVIEW_PROGRESS_STAGE_ORDER.index("draft_done")
    return _ReviewResumePlan(
        stage=stage,
        skip_draft=skip_draft,
        skip_wave=skip_draft and wave_completed,  # v2.3 新增：仅 DRAFT+WAVE 都完成才跳 WAVE
        skip_quality=completed >= _REVIEW_PROGRESS_STAGE_ORDER.index("quality_done"),
        ...
    )
```

#### 4.1.7 新增 `wave_existing_draft()` helper（v2.3 关键实施细节）
- **文件**：`novel_forge/pipeline/long/chapter_flow_generate.py`（在 `generate_chapter_prose` 旁新增）
- **签名**：
  ```python
  async def wave_existing_draft(
      runner: Any,
      bundle: Any,
      packet: Any,
      bridge: Any,
      plan: Any,
      draft_text: str,
      chapter_number: int,
      trace: Any,
  ) -> WovenArtifacts:
      """Re-run WAVE on an existing draft text (DRAFT stage skipped).

      Reuses :func:`prepare_generate_context` to build the shared context
      (kernel composer, memory hints, canon context, focus ids) but
      bypasses :func:`generate_draft` and calls :func:`apply_wave` directly
      on the provided draft_text. Used by the review-resume path when
      skip_draft=True and skip_wave=False (legacy or raw_draft recovery).
      """
      runner = FlowContextAdapter(runner)
      ctx: GenerateContext = await prepare_generate_context(
          runner=runner,
          bundle=bundle,
          packet=packet,
          bridge=bridge,
          plan=plan,
          chapter_number=chapter_number,
          trace=trace,
      )
      return await apply_wave(ctx, draft_text)
  ```
- **实现要点**：
  1. 复用 `prepare_generate_context()`（不重算 kernel / memory / canon）
  2. 跳过 `generate_draft()`
  3. 直接 `apply_wave(ctx, draft_text)`
  4. 返回 `WovenArtifacts`（与 `generate_chapter_prose` 一致）

#### 4.1.8 `_generate_and_wave` 接受 `skip_wave` 参数
```python
# novel_forge/pipeline/long/chapter_flow_orchestrate.py:1008-1019
async def _generate_and_wave(
    *,
    runner: Any,
    context: ChapterExecutionContext,
    prepared: PreparedChapterArtifacts,
    trace: PipelineTrace,
    chapter_number: int,
    resume_progress: ReviewProgressState | None,
    skip_draft: bool,
    skip_wave: bool,  # v2.3 新增
    resume_stage: str | None,
    on_step: Any,
) -> _ReviewPhaseState:
    ...
    if skip_draft and skip_wave and resume_progress is not None:
        # 完整跳过 generate 阶段
        state.current_text = resume_progress.current_text
        state.performed_edits = resume_progress.performed_edits
    elif skip_draft and not skip_wave and resume_progress is not None:
        # 只跑 WAVE，复用 DRAFT 文本
        from novel_forge.pipeline.long.chapter_flow import wave_existing_draft
        _woven = await wave_existing_draft(
            runner, bundle, prepared.packet, prepared.bridge,
            prepared.plan, resume_progress.current_text, chapter_number, trace,
        )
        state.current_text = _woven.current_text
        state.performed_edits = _woven.performed_edits
    else:
        # 正常路径：跑 DRAFT + WAVE
        _gen_artifacts = await generate_chapter_prose(...)
        ...
```

#### 4.1.9 migration helper（保守兼容）
```python
# novel_forge/workspace/chapter_session_state.py
def migrate_old_checkpoint(state: ReviewProgressState) -> ReviewProgressState:
    """保守迁移：缺 wave_completed 字段 → 视为 True（避免误重跑 WAVE）"""
    if not hasattr(state, "wave_completed"):
        return state.model_copy(update={"wave_completed": True})
    return state
```

**测试矩阵**（v2.3 完整）：

| 测试文件 | 钉死行为 |
|----------|----------|
| `tests/integration/test_wave_checkpoint_resume.py` | WAVE 失败 → resume 重跑 WAVE 不重跑 DRAFT（调用 `wave_existing_draft`） |
| `tests/integration/test_legacy_recovery_resume.py` | legacy `v1+_edited.md` 恢复 → 标 `wave_completed=False` → resume 重跑 WAVE |
| `tests/integration/test_raw_draft_recovery.py` | v2.3 新增：raw `v0_draft.md` 恢复 → 标 `wave_completed=False` → resume 重跑 WAVE |
| `tests/unit/workspace/test_migrate_old_checkpoint.py` | 兼容路径：旧 checkpoint 缺 `wave_completed` → 视为 True |
| `tests/unit/pipeline/test_recovered_draft_source_kind.py` | `_RecoveredDraft.source_kind` 各值正确标注 |
| `tests/characterization/test_review_checkpoint_resume.py` | 完整钉死 `_recover_latest_draft` 仅恢复 `v1_wave.md` + `v1+_edited.md` + v2.3 新增的 `v0_draft.md` 行为 |

### 4.2 SQLite `rollback_to` 并发保护
- **文件**：`story_kernel/store.py:755-773`
- **修复**：
  1. `rollback_to` 入口 `async with self._project_lock:`
  2. rollback 前 `PRAGMA wal_checkpoint(TRUNCATE)`
  3. 新引擎 `_configure_engine` 前 `_reopen_after_replace()`
- **测试**：`tests/integration/test_kernel_concurrent_rollback.py` + `tests/characterization/test_kernel_lock_contract.py`

### 4.3 `is_transient` 双信号源统一
- **修复**：
  1. 删 `ModelGatewayError.__init__` 中 `context={"is_transient": is_transient}`
  2. 删 `router.py:1513` 正则嗅探"429"
- **测试**：
  - `tests/unit/gateway/test_router_is_transient.py`
  - `tests/characterization/test_is_transient_backward_compat.py`（旧 `context["is_transient"]` 读法 → 显式 deprecation warning）

### 4.4 Desktop worker disconnect race
- **修复**：抽公共 `safe_disconnect(signal, handler)` 检查 `signal.receivers() > 0`
- **测试**：`tests/desktop/test_jobs_disconnect_safety.py` + characterization test

### 4.5 `_persist_stage_artifact_safely` 静默失败
- **修复**：
  1. 失败入 `lost_artifacts` 桶，写 `manifest.json.warn`（**新增 key，不破坏现有 schema**）
  2. 同步发 `WARNING` 级别 obs 事件
  3. 桌面端读取 `manifest.json.warn` 展示提示
- **测试**：
  - `tests/integration/test_persist_artifact_failure.py`（让 OSError 触发）
  - `tests/unit/obs/test_lost_artifacts_event.py`

### 4.6 RuntimeServices `_MemoryLock` 跨 loop 死锁
- **修复**：
  1. `_ensure_memory_lock` 检测 `asyncio.get_running_loop()` 每次 loop 重建新 Lock
  2. Lock 绑定到 RuntimeServices 实例
  3. 文档明示"memory lock 是 per-event-loop"
- **测试**：
  - `tests/unit/workspace/test_runtime_memory_lock.py`
  - characterization test 钉死跨 loop 行为契约

### 4.7 Desktop cancel_job final 进度丢失
- **修复**：
  1. cancel_job 时把 `record` snapshot 保存到 `_cancelling`
  2. `_handle_finished` 在 `existing.status == FAILED` 时优先合并 fresh artifacts
- **测试**：
  - `tests/desktop/test_jobs_cancel_artifact_preservation.py`
  - characterization test

---

## 5. Sprint 2：`_run_quality_and_repairs` 渐进拆分

### 5.1 第一步：抽纯 helper（行为零变化）
- **抽出**（每个 ≤ 100 行）：
  - `_normalize_repair_results(results) -> QualityReport`
  - `_persist_repair_checkpoint(stage, results) -> None`
  - `_should_retry_repair(dimension, report) -> bool`
  - `_apply_repair_fallback(dimension, error) -> RepairReport`
- **结果**：函数主体行数（由 code-stats.json 提供）降至 ≤ 200 行（流程代码保留）
- **测试**：每 helper 单测 + 完整 `_run_quality_and_repairs` regression test

### 5.2 第二步：引入 `RepairLane` Protocol
- **设计**：
  ```python
  class RepairLane(Protocol):
      dimension: RepairDimension
      async def validate(self, ctx) -> ValidationReport: ...
      async def repair(self, ctx) -> RepairReport: ...
      async def recheck(self, ctx) -> RecheckReport: ...
      async def post_repair(self, ctx) -> PostRepairReport: ...

  REPAIR_LANES: dict[RepairDimension, RepairLane] = {}

  def register_lane(dimension: RepairDimension):
      def decorator(cls):
          REPAIR_LANES[dimension] = cls()
          return cls
      return decorator
  ```
- **策略类**：5 个（`ContinuityLane` / `CausalLane` / `ReadingPowerLane` / `KnowledgeBoundaryLane` / `AiFlavorLane`）—— 内部仍调现有 stages
- **新加 dimension 流程**：1 个新文件 + 1 行 `@register_lane` + `RepairExecutionOrder` 配置追加
- **测试**：
  - `tests/unit/pipeline/test_repair_lane_protocol.py`
  - `tests/characterization/test_quality_repair_full_run.py`
  - 5 个 lane 各 1 个单测

---

## 6. Sprint 3：`LongProjectBundle` 渐进拆解（关键风险控制）

**核心问题**（v2.3 校正）：`LongProjectBundle` 当前 `@dataclass`（**非 frozen**），生产引用数由 `code-stats.json` 提供。运行时 mutation 字段（`replan_context` / `upstream_revision_fingerprint` / `character_silence` / `backstory_reveals` / `style_golden_retriever` + `stage_memory_builder` 动态注入）需先识别再迁移。

**绝对不能**：直接 frozen 化 + 拆 stage bundle —— 会破坏 mutation 路径。

### 6.1 阶段一：识别所有 mutation 点（characterization）
- **新增** `tests/characterization/test_bundle_mutation_surface.py`
- 通过 mock + monkeypatch 捕获所有"写操作"
- **输出**：`docs/superpowers/notes/2026-07-04-bundle-mutation-map.md`（自动生成）

### 6.2 阶段二：抽出 `ChapterRuntimeState`（运行时 mutation 容器）
- **设计**：
  ```python
  # pipeline/long/services/chapter_runtime_state.py（新建）
  @dataclass
  class ChapterRuntimeState:
      """Runtime-mutable per-chapter state, owned by chapter_flow."""
      replan_context: Any | None = None
      upstream_revision_fingerprint: dict[str, Any] = field(default_factory=dict)
      character_silence: bool = False
      backstory_reveals: list[dict[str, Any]] = field(default_factory=list)
      style_golden_retriever: Any = None
      stage_memory_cache: dict[str, Any] = field(default_factory=dict)
  ```
- **迁移**：
  1. `LongProjectBundle` 中运行时 mutation 字段（由 mutation map 给出）移到 `ChapterRuntimeState`
  2. `stage_memory_builder` 的"动态挂字段"改为 `state.stage_memory_cache[k] = v`
  3. `chapter_flow.py` 创建 `ChapterRuntimeState`，与 `LongProjectBundle` 平行传递
- **测试**：各 mutation 字段各 1 测试 + 完整 6 阶段 pipeline 测试

### 6.3 阶段三：`LongProjectBundle` 冻结（6 重验证）
- **设计**：`@dataclass(frozen=True)`
- **验证清单**：

| 验证项 | 工具 | 通过标准 |
|--------|------|----------|
| 静态分析无 `bundle.x = ...` | `rg` 扫 `novel_forge/` 下所有 `.py` | 0 命中 |
| AST 检查无 mutation | `tests/unit/pipeline/test_bundle_is_frozen.py` 解析 AST | 任何 `bundle.field = ...` → `FrozenInstanceError` |
| mypy 通过 | `mypy novel_forge/` | 0 error |
| ruff 通过 | `ruff check novel_forge/` | 0 error |
| characterization test 覆盖旧 mutation | `tests/characterization/test_bundle_mutation_surface.py` | mutation 字段行为钉死 |
| 6 阶段 pipeline 回归 | `tests/integration/test_full_6phase_pipeline.py` | 完整 init → outline → 3 章节 → polish → humanize → finalize 通过 |

### 6.4 阶段四：按 6 阶段拆 stage-specific projections（可选）
- **前提**：6.1-6.3 稳定后
- **设计**：6 个 projection dataclass（`PlanningProjection` / `GenerateProjection` / ...）作为 `LongProjectBundle` 的视图
- **提供方式**：`bundle.planning_view()` 返回只读视图
- **旧调用方式**保留，**新代码鼓励**用 projection

### 6.5 `common/plot_guard.py` 重度反向依赖修复
- **实际依赖**（已验证）：`gateway.router` + `persistence.filesystem` + `persistence.models` + `pipeline.long.services.plot_milestones` + `pipeline.token_budget` + `prompts.builder`
- **修正**：整文件下沉到 `pipeline/long/services/plot_guard.py`
- **保留 stub**：`common/plot_guard.py` 改为 re-export 兼容层（保留 ≥ 2 sprint）
- **测试**：所有 `from novel_forge.common.plot_guard import ...` 调用方测试

### 6.6 `GenerateContext` 拆分
- **设计**：`DraftContext`（DRAFT-only）+ `WaveContext`（仅 DRAFT 产物 + 共享）
- **WAVE** 用已存在 `DraftArtifacts` 接续
- **测试**：与 LongProjectBundle 同步

---

## 7. Sprint 4：注册表 + 文档（保守化）

### 7.1 `ADAPTER_REGISTRY`（保守化）
- **设计**：
  ```python
  class ProviderAdapter(ABC):
      provider_name: ClassVar[str]
      default_settings_field: ClassVar[str]
      default_base_url: ClassVar[str | None] = None
      default_model: ClassVar[str | None] = None

      def __init_subclass__(cls, **kwargs):
          super().__init_subclass__(**kwargs)
          if hasattr(cls, "provider_name"):
              ADAPTER_REGISTRY.register(cls)

  ADAPTER_REGISTRY = AdapterRegistry()

  class ProviderConfig(BaseModel):
      """显式 provider 配置模型（与 Settings 分离）"""
      api_key: str = ""
      base_url: str | None = None
      default_model: str = ""
  ```
- **新加 provider 流程**：
  1. `gateway/adapters/cohere.py` 继承 `ProviderAdapter`，设 class var
  2. `core/config.py` 显式加 `cohere: ProviderConfig`
  3. `_ADAPTER_MODULES` 加 1 行 import
  4. `.env.example` 加 `NOVEL_FORGE_COHERE_API_KEY=`
  5. `_register_real_adapters` 改用 Registry 生成的列表（非硬编码）

### 7.2 `FormatContractRegistry` + CI 检查（保守化）
- **设计**：
  ```python
  class FormatContractRegistry:
      def register(self, contract: TaskFormatContract): ...
      def get(self, task_type: TaskType) -> TaskFormatContract: ...

  CONTRACT_REGISTRY = FormatContractRegistry()
  ```
- **CI 验证脚本**：`scripts/check_task_type_completeness.py`
  - 每个 `TaskType` 必须有：contract + prompt template + format contract + router route
  - 缺任何一项 → 报错
- **目标**：**CI 自动检查缺项**，不追求"一行接入"

### 7.3 property-based testing（v2.1 修正不变量）
- **关键修正**：`validate_text_output_contract` 实际签名 `(task_type, raw_content, extracted_text, *, min_chars=1)`，**会拒绝**：
  - 代码围栏（` ``` `）
  - Markdown 标题（`_strip_outer_markdown_fence` + `strip_leading_markdown_headings`）
  - JSON 容器（`_looks_like_json_container`）
  - prompt 泄露 token（`find_text_output_contract_violations`）
  - meta 开头（"说明："、"以下是" 等）
- **正确的不变量设计**：
  ```python
  # tests/property/test_format_contract_invariants.py
  from hypothesis import given, strategies as st

  legal_chinese_prose = st.text(
      alphabet=st.characters(
          whitelist_categories=("Lo", "Lu", "Nd"),
          blacklist_characters=("`", "{", "}", "[", "]", "<", ">"),
      ),
      min_size=20,
      max_size=5000,
  ).filter(lambda s: not s.startswith(("说明：", "以下是", "自检")))

  @given(task_type=st.sampled_from([t for t in TaskType if is_text_only(t)]),
         text=legal_chinese_prose)
  def test_text_only_contract_accepts_legal_prose(task_type, text):
      """合法 prose 必须通过 validate_text_output_contract"""
      result = validate_text_output_contract(
          task_type, raw_content=text, extracted_text=text, min_chars=20
      )
      assert isinstance(result, str)
      assert len(result) > 0

  @given(task_type=st.sampled_from([t for t in TaskType if is_text_only(t)]),
         prefix=st.sampled_from(["```json\n", "# 标题\n", '{"key":', "[1,2,3]"]))
  def test_text_only_contract_rejects_known_illegal(task_type, prefix):
      """明确非法样本必须抛 TextOutputContractError"""
      with pytest.raises(TextOutputContractError):
          validate_text_output_contract(
              task_type, raw_content=prefix + "正文", extracted_text=prefix + "正文"
          )
  ```
- **价值**：覆盖契约的"接受边界"与"拒绝边界"，而非"任意输入不抛"

### 7.4 文档（自动生成 + CI 校验）
- **修正**：**所有数字由 S0 生成的 `code-stats.json` 提供**
- **新增脚本**：
  - `scripts/generate_doc_stats.py` —— S0 已生成
  - `scripts/check_doc_consistency.py` —— 校验 `AGENTS.md` / `MANUAL.md` / `README.md` 中数字与 `code-stats.json` 一致
  - CI 必跑，**不通过则 PR 不能合**
- **生成内容**：见 §3.0
- **新增文档**：
  - `CONTRIBUTING.md`
  - `docs/dev-guide/architecture.md`
  - `docs/dev-guide/extension-guide.md`
  - `prompts/CHANGELOG.md`

---

## 8. 拆分巨型文件

| 优先级 | 文件 | 执行时机 |
|--------|------|----------|
| P0 | `init_coherence_v2.py` | S3 完成后 |
| P0 | `chapter_session_handlers.py` | S2 完成后 |
| P0 | `chapter_flow_review.py` | S2 god function 拆完后 |
| P1 | `format_contracts.py` | S4 引入 Registry 后 |
| P1 | `core/config.py` | S4 引入 ProviderConfig 后 |
| P1 | `document_renderer/{story_artifacts,reports}/__init__.py` | S4 期间 |
| P2 | `chapter_flow_finalize.py` | 沿用 finalize_persist/report 经验 |
| P2 | `core/utils/json.py` | 拆 `json/{coercers, validators, reducers}.py` |

具体行数与文件清单由 `code-stats.json` 在 S0 时生成。
**原则**：每个拆分 PR 必带 characterization test 验证拆分前后行为一致。

---

## 9. 明确划线（不属本 spec）

- 不重构 `memory/integration.py` 和 `memory/episodic.py`（独立项目）
- 不引入 OpenTelemetry 全链路 trace_id
- 不迁移 Anthropic 死依赖（需产品决策）
- 不做 `desktop/jobs/__init__.py` 进一步拆分
- 不重构 `core/schemas/` 27 个 Pydantic 模型
- **不追求**"每阶段最小权限"
- **不追求**"新 TaskType 一行接入"—— 改为"CI 自动检查缺项"

---

## 10. 风险评估（v2.3 调整后）

| 风险 | 评估 | 缓解 |
|------|------|------|
| LongProjectBundle 拆分破坏 mutation | 高 / 高 | 先 mutation 分离再 frozen，3 阶段渐进 + 6 重验证 |
| RepairLane 一次性行为重写 | 低 / 中 | 两步走：先抽 helper 再 Protocol 包一层 |
| ADAPTER_REGISTRY 动态 Settings | 中 / 中 | Registry 仅描述 metadata，Settings 显式维护 |
| 反向依赖修复牵动面 | 高 | 整文件下沉 `plot_guard.py`，≥ 2 sprint 兼容期 |
| 性能收益空泛 | 中 | S0 先补基线 benchmark，所有改动有量化对比 |
| 测试放后面 | 已修正 | 每 PR 同步带 regression + characterization test |
| 文档数字漂移 | 高 | v2.1 起全部脚本生成 + CI 校验 |
| 冻结验证不充分 | 中 | 6 重验证（rg/AST/mypy/ruff/characterization/regression） |
| property-based testing 误用 | 中 | 区分"接受合法"与"拒绝非法"，明确非法样本 |
| WAVE checkpoint bug 误诊 | v2.1 误 / v2.2 部分 / v2.3 已校正 | 实际是 legacy recovery 丢失 WAVE 状态；v2.3 完整实施 `_RecoveredDraft.source_kind` + `wave_expleted` + `wave_existing_draft()` helper |

---

## 11. 验收标准（v2.3 全部脚本化）

> **原则**：验收项不写具体数字，全部改为"CI 跑 X 脚本全过 + `code-stats.json` 数字校验"。

### 11.1 S0 验收
- [ ] `scripts/generate_doc_stats.py` 跑通，生成 `docs/superpowers/stats/code-stats.json`
- [ ] `scripts/benchmark_pipeline_baseline.py` 跑通，生成 `docs/superpowers/baselines/<date>-baseline.json`
- [ ] 5 项 characterization tests 全部通过
- [ ] baseline JSON + code-stats JSON 提交到 git

### 11.2 S1 验收
- [ ] 7 个严重 Bug 全部修复
- [ ] 每 bug 配 1 个 regression test + 1 个 characterization test
- [ ] 完整 6 阶段 pipeline 回归测试通过
- [ ] **S1 完成后跑 `scripts/benchmark_pipeline_baseline.py --compare-with baseline.json`，性能不下降**

### 11.3 S2 验收
- [ ] `_run_quality_and_repairs` 主体行数（具体值由 `code-stats.json` 提供）降至 ≤ 200 行流程代码
- [ ] 5 个 RepairLane 策略类就绪
- [ ] 5 项 characterization 覆盖原有 repair 行为

### 11.4 S3 验收
- [ ] `LongProjectBundle` 冻结化
- [ ] **6 重验证全过**：
  - [ ] `rg "\.bundle\.[a-z_]+ ?=" novel_forge/ --type py` 0 命中
  - [ ] `tests/unit/pipeline/test_bundle_is_frozen.py` 解析 AST 通过
  - [ ] `mypy novel_forge/` 0 error
  - [ ] `ruff check novel_forge/` 0 error
  - [ ] `tests/characterization/test_bundle_mutation_surface.py` mutation 字段行为钉死
  - [ ] `tests/integration/test_full_6phase_pipeline.py` 完整 6 阶段通过
- [ ] `ChapterRuntimeState` 字段迁移完成
- [ ] `common/plot_guard.py` 下沉完成，2 sprint 兼容期 stub 保留
- [ ] `GenerateContext` 拆 `DraftContext` + `WaveContext`

### 11.5 S4 验收
- [ ] `ADAPTER_REGISTRY` 注册所有现有 provider（数量由 `code-stats.json` 统计）
- [ ] `FormatContractRegistry` 就绪
- [ ] `scripts/check_task_type_completeness.py` 跑通：每个 `TaskType` 的 4 项（contract / prompt / format / route）全有
- [ ] property-based testing 跑通 `validate_text_output_contract` 的接受/拒绝两类不变量
- [ ] `CONTRIBUTING.md` + `docs/dev-guide/` + `prompts/CHANGELOG.md` 就绪
- [ ] `scripts/check_doc_consistency.py` 跑通：所有文档数字与 `code-stats.json` 一致

### 11.6 全局验收
- [ ] 所有现有测试文件通过（数量由 `pytest --collect-only -q` 统计）
- [ ] CI lint job 全过（`lint_prompt_layers` / `audit_prompt_format_layers` / `generate_prompt_index --check` / `verify_templates` / `verify_format_contracts`）
- [ ] `ruff check novel_forge/` + `mypy novel_forge/` 全过

---

## 12. 后续（独立项目，不属本 spec）

- `memory/integration.py` / `memory/episodic.py` 拆分
- OpenTelemetry trace_id 全链路
- 桌面 `pages/standalone/` 21 个文件统一注册表
- `pyproject.toml` 锁版本（`uv sync --frozen`）CI 升级
- Anthropic 死依赖清理（产品决策）
