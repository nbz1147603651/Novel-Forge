# Runtime Control Plane 渐进式实施计划

## 决策与约束（用户已确认）

- **SQLite 访问**：异步 SQLAlchemy 2.0，沿用 `StoryKernelStore` 模式（`AsyncEngine` + `async_sessionmaker` + WAL + FK）。为支持 sync 线程（JobService worker、Desktop Qt 线程）访问，`ControlPlaneStore` 内部持有专用 daemon loop 线程，sync 方法经 `asyncio.run_coroutine_threadsafe` 桥接。
- **第一阶段双写范围**：所有 17 种 `_WRITE_JOB_KINDS`（全量写类 job 双写 + 对账）。
- **迁移策略**：双写 → 对账 → 切换（新控制面负责重试/恢复/SSE，阶段执行器复用现有流水线）。
- **不替换现有文件工件**：`runtime_control.db` 只存元数据/哈希/路径；正文/JSON/音频仍按 SHA-256 内容寻址存文件系统。
- **全局 DB**：`storage_root / "runtime_control.db"`（跨项目，供容量调度与健康准入）。非项目级。

---

## 数据模型（阶段 0 定义，后续阶段填充）

```
WorkUnit          (用户意图)
  id, kind, project_id, priority(P0-P3), intent_payload_path,
  state(queued/running/retry_wait/waiting_human/committed/cancelled/failed),
  assurance(normal/degraded),  -- 降级与运行状态分离
  idempotency_key, created_at, updated_at, heartbeat_at

RunAttempt        (一次实际执行)
  id, work_unit_id FK, attempt_number, runtime_config_version,
  state(running/retry_wait/waiting_human/committed/cancelled/failed),
  error_kind, error_summary_json, started_at, ended_at, heartbeat_at

StageExecution    (阶段级执行记录)
  id, run_attempt_id FK, stage_name, task_type,
  input_artifact_hashes_json, idempotency_key, retry_budget, retries_used,
  output_artifact_hash, committed_version,
  state(running/done/failed/skipped), heartbeat_at, started_at, ended_at

EventLedger       (不可变事件流，append-only)
  id, run_attempt_id FK, stage_execution_id FK NULL,
  event_type, severity, payload_json, created_at, seq  -- 全局递增 seq

ArtifactManifest  (不可变工件清单)
  id, sha256, artifact_kind, project_id, scope,
  content_path, content_size, mime_type,
  source_artifact_hashes_json, parent_artifact_id,
  prompt_hash, template_version, task_type, route, model_params_json,
  response_hash, validation_result_json,
  created_at, created_by_stage_execution_id FK

ResourceVersion   (提交版本)
  id, work_unit_id FK, resource_kind(chapter/canon/report/tts_audio/voice_lib),
  project_id, resource_ref, version_number, committed_artifact_id FK,
  committed_at
```

---

## 阶段划分（每阶段完成即 git commit 暂存）

### 阶段 0：控制面骨架与数据模型

**目标**：建立 `novel_forge/control_plane/` 模块、ORM、Store、全局 DB、配置开关。不触碰现有运行路径。

**新增文件**：
- `novel_forge/control_plane/__init__.py` — 公共导出
- `novel_forge/control_plane/orm.py` — SQLAlchemy 2.0 ORM 模型（上述 6 表 + `ControlPlaneBase` declarative base）
- `novel_forge/control_plane/store.py` — `ControlPlaneStore`（仿 `StoryKernelStore`：`AsyncEngine`/`async_sessionmaker`/WAL/FK + daemon-loop sync 桥接 `SyncFacade`）
- `novel_forge/control_plane/schemas.py` — Pydantic DTO（`WorkUnitDTO`、`RunAttemptDTO`、`StageExecutionDTO`、`ArtifactManifestDTO`、`EventLedgerEntryDTO`、`ResourceVersionDTO`）
- `novel_forge/control_plane/enums.py` — `WorkUnitState`、`AssuranceLevel`、`StageState`、`Priority`（P0-P3）、`ResourceKind`
- `novel_forge/control_plane/health.py` — `RoutingHealthRegistry` 骨架（阶段 4 填充）
- `novel_forge/control_plane/factory.py` — `get_control_plane_store(settings)` 单例（仿 `api/deps._get_router_cached`，带 staleness 检查）

**修改文件**：
- `novel_forge/core/config.py` — 新增 `runtime_control_enabled: bool = True`、`runtime_control_db_path: Path | None`（默认 `storage_root / "runtime_control.db"`）、`runtime_control_heartbeat_timeout_s: int = 300`
- `novel_forge/persistence/models.py` — 不改（全局 DB 不在 ProjectLayout 内）

**测试**：
- `tests/unit/control_plane/test_store.py` — CRUD 每张表、WAL、FK 级联、daemon-loop sync 桥接在多线程下安全、`in_memory()` 工厂
- `tests/unit/control_plane/test_schemas.py` — DTO 序列化往返

**验证**：`.venv/bin/python -m pytest tests/unit/control_plane/ -q` + `ruff` + `mypy novel_forge/control_plane/`

**提交**：`feat(control_plane): add SQLite-backed control plane skeleton with ORM and store`

---

### 阶段 1：WorkUnit/Attempt 双写（影子记录）

**目标**：所有 17 种写类 job 在 `JobService` 生命周期中影子写入 WorkUnit + RunAttempt，不影响现有逻辑。现有 `task_flow_history.json` 保持不变。

**新增文件**：
- `novel_forge/control_plane/shadow.py` — `ShadowRecorder`：在 JobService 关键节点调用控制面（创建 WorkUnit/Attempt、更新 state/heartbeat、记录终态）。所有方法吞异常并 log（影子写入不得影响主流程）。
- `tests/unit/control_plane/test_shadow_recorder.py` — 双写不抛、终态同步、heartbeat 更新

**修改文件**：
- `novel_forge/app_service/job_service.py`：
  - `JobService.__init__` 接受可选 `control_plane: ControlPlaneStore | None`（默认从 factory 获取，`runtime_control_enabled=False` 时为 None）
  - `submit()`：写类 job 创建 WorkUnit + RunAttempt（attempt_number=1），设 `idempotency_key = f"{kind}:{project_id}:{payload_hash}"`
  - `_JobWorker._run_impl`：更新 heartbeat（每 step_progress 回调时）
  - `_handle_finished`：终态（SUCCEEDED→committed / FAILED→failed / PAUSED→waiting_human）同步到 RunAttempt
  - `_persist_terminal_job`：在写 task_flow_history.json 后同步终态到控制面
  - cancel 路径：RunAttempt → cancelled
- `novel_forge/desktop/jobs/manager.py`：`DesktopJobManager` 透传 control_plane（构造时传入 JobService）

**对账脚本**：
- `scripts/reconcile_control_plane.py` — 对比 `task_flow_history.json`（每项目）与 `runtime_control.db` 中终态 WorkUnit/Attempt，报告不一致。`--check` 模式 CI 可用，`--apply` 模式可修补缺失的控制面记录。

**测试**：
- `tests/unit/control_plane/test_job_service_shadow.py` — 模拟 submit/run/finish 全流程，断言控制面记录与 JobRecord 一致
- `tests/integration/test_control_plane_reconcile.py` — 构造 task_flow_history.json + DB 不一致场景，验证对账
- 现有 JobService 测试全部保持通过（影子写入不破坏）

**验证**：`.venv/bin/python -m pytest tests/unit/control_plane/ tests/unit/app_service/ -q` + `scripts/reconcile_control_plane.py --check`（空库无项目时应 PASS）

**提交**：`feat(control_plane): shadow-write WorkUnit/Attempt for all write-job kinds`

---

### 阶段 2：StageExecution + 不可变工件谱系清单

**目标**：每个流水线阶段登记 StageExecution（输入哈希、幂等键、输出哈希、提交版本）；补全 per-call prompt/response hash 与 EventLedger 增量记录。

**新增文件**：
- `novel_forge/control_plane/stage_recorder.py` — `StageRecorder`：在阶段开始/结束时登记 StageExecution + ArtifactManifest。复用 `source_artifacts.hash_payload` 和 `core.review_contracts.source_text_hash`。
- `novel_forge/control_plane/call_ledger.py` — `record_model_call(...)`：从 router event 提取 prompt_hash/response_hash/template_version/contract_id/route，写入 ArtifactManifest（`artifact_kind="model_call"`）。
- `tests/unit/control_plane/test_stage_recorder.py`
- `tests/unit/control_plane/test_call_ledger.py`

**修改文件**：
- `novel_forge/pipeline/long/services/source_artifacts.py`：
  - `persist_stage_artifact`（:1701）：登记 StageExecution + ArtifactManifest（复用已算的 `source_hashes`），不再仅写 JSON。失败时记 EventLedger（不吞——谱系是硬约束）。
  - `project_stage_source_cards`（:871）：返回值附带 `allowed_artifact_types`/`allowed_proposal_types` 元数据（阶段 5 Harness 用）。
  - `_persist_stage_artifact_safely`（chapter_flow_orchestrate.py:112）：改为不吞错——谱系写入失败应升级为 EventLedger `severity=error` 并影响 assurance。
- `novel_forge/obs/project_logger.py`：
  - `record_router_event`（:341）：计算并写入 `prompt_hash`（SHA-256 of messages）、`response_hash`（SHA-256 of response），写入 `template_version`、`contract_id`。
- `novel_forge/gateway/router.py`：
  - `api_call_start`/`api_call_done` event payload 增加 `prompt_hash`、`response_hash`、`contract_id`（从 LLMService 透传）。
  - `provider_health_snapshot`（:629）：同时返回给 `RoutingHealthRegistry`（阶段 4）。
- `novel_forge/pipeline/long/chapter_flow.py` 各 stage 调用点：在 `persist_stage_artifact` 前后调用 `StageRecorder.begin/end`。

**对账**：
- `scripts/reconcile_control_plane.py` 增加 `--stages` 模式：对比 `states/chapter_NNN_artifacts/*.json`（StageArtifact）与 DB 中 StageExecution。

**测试**：
- `tests/unit/control_plane/test_stage_lineage.py` — 6 阶段哈希链完整、previous_artifact_id 链、EventLedger 增量
- `tests/unit/gateway/test_router_event_hashes.py` — prompt/response hash 正确计算
- `tests/unit/pipeline/test_stage_artifact_persistence.py` — 现有测试保持通过

**验证**：`.venv/bin/python -m pytest tests/unit/control_plane/ tests/unit/pipeline/ tests/unit/gateway/ -q` + `ruff` + `mypy`

**提交**：`feat(control_plane): record StageExecution and immutable artifact lineage manifest`

---

### 阶段 3：启动 Reconciliation（崩溃恢复）

**目标**：JobService/Desktop/CLI 启动时扫描控制面，恢复中断的 WorkUnit。复用现有 `ReviewProgressState` checkpoint，不替换。

**新增文件**：
- `novel_forge/control_plane/reconciler.py` — `StartupReconciler`：
  - 扫描 `state IN (running, retry_wait)` 且 `heartbeat_at < now - timeout` 的 RunAttempt
  - 若关联 StageExecution 有有效输出（`output_artifact_hash` 存在且文件可读）→ 验证后标记 `committed`，发 EventLedger `reconcile_committed`
  - 无有效输出 → 按 WorkUnit.kind 与 `ReviewProgressState` 决定：可安全重试则 `retry_wait`；否则 `waiting_human`
  - 与现有 `ReviewProgressState` 对账：若 DB 说 `canon_done` 但 checkpoint 文件缺失，以文件为准并修正 DB
- `novel_forge/control_plane/resume.py` — `resume_work_unit(work_unit_id)`：重建 JobCommand 并提交 JobService
- `tests/unit/control_plane/test_reconciler.py`
- `tests/integration/test_crash_recovery.py` — 故障注入：模拟 worker 崩溃后重启，验证恢复

**修改文件**：
- `novel_forge/app_service/job_service.py`：
  - `__init__` 后调用 `StartupReconciler.reconcile()`（`runtime_control_enabled` 时）
  - `submit()` 增加幂等检查：同 `idempotency_key` 的 `committed` WorkUnit 直接返回历史结果
- `novel_forge/desktop/jobs/manager.py`：启动时展示待恢复 WorkUnit 列表（Qt signal `reconcile_pending`）
- `novel_forge/cli/chapter_runner.py`：CLI 模式同样走 reconciliation

**关键规则**：重放只恢复状态、验证并继续；若必须重新调用模型，只保证上下文一致，不保证逐字一致（与 Anthropic 经验一致）。

**测试**：
- `tests/integration/test_control_plane_crash_recovery.py` — DRAFT 后崩溃→恢复跳过 DRAFT/WAVE；canon_done 后崩溃→直接 commit；供应商全不可用→`degraded + retry_wait`
- `tests/unit/control_plane/test_idempotent_submit.py` — 重复提交同 idempotency_key

**验证**：`.venv/bin/python -m pytest tests/integration/test_control_plane_*.py tests/unit/control_plane/ -q`

**提交**：`feat(control_plane): startup reconciliation and crash recovery via control plane`

---

### 阶段 4：RoutingHealthRegistry + 容量调度

**目标**：把分散在 3 个 breaker 池（API 单例、JobService worker、TTS 调用）的健康状态提升为共享 `RoutingHealthRegistry`，加入优先级与容量保留。

**新增文件**：
- `novel_forge/control_plane/health.py`（填充阶段 0 骨架）— `RoutingHealthRegistry`：
  - 进程级单例（threading.Lock 守护），key = `(provider, task_type)`
  - 状态：`opened_until`、失败类别、可用并发、token 预算余量、`assurance(normal/degraded)`
  - 复用 `CircuitBreaker`/`TaskTypeCircuitBreaker`/`RateLimiterRegistry`/`SpendingTracker` 实例（注入而非重建）
  - `register_breaker(provider, breaker)` / `register_limiter(provider, limiter)`
  - `can_admit(priority, task_type, provider) -> AdmitDecision`
  - `record_failure(provider, task_type, kind)` / `record_success(...)`
- `novel_forge/control_plane/capacity.py` — `CapacityScheduler`：P0-P3 优先级队列，健康容量低于 P0/P1 通道时拒绝 P3
- `tests/unit/control_plane/test_health_registry.py`
- `tests/unit/control_plane/test_capacity_scheduler.py`

**修改文件**：
- `novel_forge/gateway/factory.py`：`ModelRouterBuilder._build_circuit_breakers`/`_build_task_circuit_breaker` 从 `RoutingHealthRegistry` 获取共享实例（若 registry 已初始化），否则保留 per-instance（向后兼容）
- `novel_forge/workspace/runtime.py`：`create_runtime_services` 注入共享 `RoutingHealthRegistry` 到新建 router
- `novel_forge/api/deps.py`：`_get_router_cached` 同样注入共享 registry
- `novel_forge/app_service/job_service.py`：submit 前调用 `CapacityScheduler.can_admit(priority)`；全供应商不可用时 WorkUnit → `degraded + retry_wait` 或 `waiting_human`，保留 checkpoint
- 优先级映射：P0=本地提交/恢复/验证归档；P1=正文生成/必要修复/核心 TTS；P2=规划/必要质检；P3=Critic/重复评估/润色/人味化/宏观审计/氛围音

**关键规则**：只有明确允许"降级归档"的确定性本地步骤才能继续提交；不能静默跳过 Canon 硬门。

**测试**：
- `tests/unit/control_plane/test_health_admission.py` — P3 在低健康时被拒；P0/P1 始终放行；全不可用→degraded
- `tests/integration/test_degraded_mode.py` — 供应商全断→WorkUnit degraded+retry_wait，checkpoint 保留

**验证**：`.venv/bin/python -m pytest tests/unit/control_plane/ tests/unit/gateway/ -q`

**提交**：`feat(control_plane): shared RoutingHealthRegistry with priority capacity reservation`

---

### 阶段 5：声明式 StageDefinition Harness 权限

**目标**：为每个阶段建立 `StageDefinition`，机械执行读写权限边界。先迁移只读/报告/修复，最后迁移 Canon 提交。

**新增文件**：
- `novel_forge/control_plane/stage_definitions.py` — `StageDefinition` frozen dataclass：`allowed_artifact_types`、`allowed_proposal_types`、`can_commit_directly`、`editable_text_window`、`required_validations`、`degradation_policy`、`max_retries`、`human_intervention_conditions`
- `novel_forge/control_plane/registry.py` — `STAGE_DEFINITIONS: dict[str, StageDefinition]`（6 阶段 + TTS 阶段）
- `novel_forge/control_plane/harness.py` — `StageHarness`：在 `PipelineStep.run()` 与 `_execute()` 之间校验输入工件类型、输出提案类型、提交权限、文本窗口边界
- `tests/unit/control_plane/test_stage_definitions.py`
- `tests/unit/control_plane/test_harness_enforcement.py`

**修改文件**：
- `novel_forge/pipeline/steps/base.py`：`PipelineStep.run()` 在 `_execute` 前后调用 `StageHarness.check_input/check_output`（若 stage 有定义）
- `novel_forge/pipeline/long/stages/finalize_persist.py`：Canon 提交（`_write_chapter_outcome_to_story_kernel` :257）必须经 Harness 校验所有硬门通过 + 链路水位匹配
- `novel_forge/workspace/execution_tts.py`：TTS 阶段经 Harness——配音脚本只能读 `committed` 章节；语音合成只能写音频衍生物，不回写正文

**关键规则**：
- Reviewer：只能提交报告提案
- Repair：只提交绑定原文哈希+目标窗口+保护片段的 patch 提案；越界→升级重新规划或人工确认
- Canon Writer：Finalize 全部硬门通过 + 水位匹配时才提交
- TTS：不能影响 Canon；音色库与章节音频独立写权限和锁

**测试**：
- `tests/unit/control_plane/test_harness_violations.py` — 越界读取、越界提交、跳过硬门均被拒
- `tests/unit/pipeline/test_finalize_harness.py` — Canon 提交在硬门未过时被拦截

**验证**：`.venv/bin/python -m pytest tests/unit/control_plane/ tests/unit/pipeline/ -q`

**提交**：`feat(control_plane): declarative StageDefinition harness with permission enforcement`

---

### 阶段 6：CI 架构约束检查 + RuntimeControlPlane 抽象

**目标**：把架构约束写进 CI；抽出 `RuntimeControlPlane` 供 Desktop/CLI/API 共用；`novel-forge serve` 后期提供。

**新增文件**：
- `novel_forge/control_plane/plane.py` — `RuntimeControlPlane`：聚合 `ControlPlaneStore` + `RoutingHealthRegistry` + `CapacityScheduler` + `StageHarness`，不依赖 FastAPI。Desktop/CLI/API 都调用它。
- `scripts/verify_control_plane_constraints.py` — CI lint（仿 `verify_format_contracts.py`）：
  - 阶段 DAG 无环
  - 每个 StageDefinition 输入输出契约完整
  - 能力清单和降级策略完整
  - 禁止执行阶段绕过 Harness 直接提交 Canon/章节正文/TTS 最终物
  - 禁止写作阶段读取完整原始 `project_spec` 等越界源文件
  - 每个可恢复阶段有输入哈希、幂等键
- `tests/integration/test_fault_injection.py` — 故障注入套件：模型响应后崩溃、提交后崩溃、重启恢复、断路器全开、TTS 分段重复提交
- `tests/integration/test_multi_client_consistency.py` — Desktop/API/CLI 对同一 WorkUnit 的一致性

**修改文件**：
- `.github/workflows/ci.yml`：lint job 增加 `python scripts/verify_control_plane_constraints.py`
- `novel_forge/api/app.py`：API 路由改为提交 WorkUnit 后返回 `202 + work_unit_id`，客户端经 SSE 观察（`chapter.py`/`tts.py` 从直接执行改为走控制面）
- `novel_forge/desktop/jobs/manager.py`：默认使用嵌入式 `RuntimeControlPlane`
- `novel_forge/cli/main.py`：`novel-forge serve` 命令（FastAPI 作为唯一 server，后期）

**测试**：
- `tests/integration/test_control_plane_fault_injection.py`
- `tests/integration/test_control_plane_multi_client.py`
- `scripts/verify_control_plane_constraints.py` 在现有项目上 PASS

**验证**：完整 CI lint + `.venv/bin/python -m pytest -n auto -q` 全量通过

**提交**：`feat(control_plane): CI architecture constraints + RuntimeControlPlane abstraction`

---

## 风险与回退

- **阶段 0-2** 完全是影子双写，`runtime_control_enabled=False` 即可完全回退，零风险。
- **阶段 3** 恢复逻辑与现有 checkpoint 并存，`StartupReconciler` 失败时 fallback 到现有行为。
- **阶段 4** 健康准入默认 `assurance=normal` 放行所有，逐步收紧。
- **阶段 5** Harness 先 advisory（log 警告），验证后再 enforce。
- **阶段 6** FastAPI 强制依赖最后才启用，Desktop 始终保留嵌入式模式。

## 执行顺序

按阶段 0→1→2→3→4→5→6 顺序推进，每阶段完成后：
1. 运行该阶段测试 + ruff + mypy
2. 运行相关 CI lint 脚本
3. `git add` + `git commit` 暂存
4. 报告阶段成果后再进入下一阶段