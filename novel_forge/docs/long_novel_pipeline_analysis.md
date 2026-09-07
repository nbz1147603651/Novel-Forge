# Novel Forge 长篇小说创作链路 — 多模块整合架构分析报告

> **分析日期**: 2026-04-23  
> **分析范围**: 全书连跑（AutoChapterRunner）功能的全链路多模块整合  
> **总体评估**: **良好（有改进空间）**

---

## 1. 执行摘要

### 1.1 总体评估结论

Novel Forge 的长篇小说创作链路采用 **6 层分层 Pipeline 架构**，在模块职责划分、数据流设计、断点续传机制方面表现优秀。全书连跑功能具备完善的 checkpoint 机制、PlotGuard 剧情偏离检测和全局修复预算控制。但在 Canon 状态跨章传递、记忆系统可靠性、以及大规模项目的性能扩展性方面存在需要关注的风险点。

### 1.2 关键发现（5 条）

1. **Canon 状态传递机制正确但依赖运行时引用**：`prepare_long_project()` 每章重新 load CanonState，确保状态是最新的，但全书连跑时 bundle 是每章新建的，依赖文件系统原子性保证一致性。

2. **断点续传覆盖完整但存在时序窗口风险**：ReviewProgressState 实现 4 级 checkpoint（draft_done → quality_done → repair_done → canon_done），但 Canon merge 成功后、memory update 前崩溃会导致 Canon 已更新但记忆未索引的不一致状态。

3. **修复循环决策框架设计精良**：`decide_repair_continuation()` 实现 5 种 verdict，配合 `long_global_repair_budget` 全局预算和跨维度回归检测，有效防止无限循环。

4. **记忆更新非阻塞设计是双刃剑**：`_trigger_memory_update()` 失败不阻塞章节生成，保证连跑进度，但可能导致后续章节缺少上下文参考。

5. **全书连跑完全串行执行**：`AutoChapterRunner` 使用 for 循环逐章生成，无并发优化，200+ 章节项目的总执行时间会非常长。

### 1.3 建议优先级排序

| 优先级 | 类别 | 数量 | 说明 |
|--------|------|------|------|
| P0 | 立即修复 | 2 | Canon-Memory 时序窗口、断点续传跳过逻辑加固 |
| P1 | 短期优化 | 3 | 记忆更新重试策略增强、大规模内存增长控制、PlotGuard 决策日志 |
| P2 | 中期规划 | 3 | 章节级并发探索、向量存储分层、Canon 压缩机制优化 |
| P3 | 长期愿景 | 2 | Pipeline 步骤级并行化、增量式 Canon 状态管理 |

---

## 2. 架构概览

### 2.1 6 层分层架构

```
┌──────────────────────────────────────────────────────────────────┐
│ Layer 1: Desktop UI / CLI Layer                                   │
│  - cli/commands/run_chapter.py (CLI 入口)                         │
│  - cli/chapter_runner.py (ChapterRunner, AutoChapterRunner)       │
│  - desktop/pages/workflow_*.py (桌面 UI 页面)                     │
├──────────────────────────────────────────────────────────────────┤
│ Layer 2: Execution Runners Layer                                  │
│  - workspace/execution_runners.py (execute_run_chapter)           │
│  - workspace/runtime.py (RuntimeServices)                         │
│  - workspace/chapter_session_handlers.py (会话处理)               │
├──────────────────────────────────────────────────────────────────┤
│ Layer 3: Pipeline Orchestration Layer                             │
│  - pipeline/chapter_runner.py (ChapterRunner 核心编排器)          │
│  - pipeline/long/loop.py (run_long_chapter)                       │
│  - pipeline/long/preflight.py (prepare_long_project)              │
├──────────────────────────────────────────────────────────────────┤
│ Layer 4: Chapter Flow Layer (核心)                                │
│  - pipeline/long/chapter_flow.py (execute_chapter_pipeline)       │
│    ├── prepare_chapter_plan()                                     │
│    ├── review_chapter_draft()                                     │
│    └── finalize_chapter_result()                                  │
├──────────────────────────────────────────────────────────────────┤
│ Layer 5: Pipeline Steps Layer (原子操作)                           │
│  - pipeline/steps/*.py (21 个 Step 类)                            │
│  - pipeline/long/stages/*.py (规划、草稿、质检、修复)              │
├──────────────────────────────────────────────────────────────────┤
│ Layer 6: Core/Canon/Memory/Prompts Layer                          │
│  - core/schemas/*.py (数据模型)                                   │
│  - canon/*.py (典据系统)                                          │
│  - memory/*.py (记忆系统)                                         │
│  - prompts/*.py + prompts/prompts/*.j2 (Prompt 构建)             │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 全书连跑调用链路图

```
CLI: novel-forge run-chapter --project-id X --chapter N --auto
    │
    ├─ run_chapter() [cli/commands/run_chapter.py]
    │   └─ _run_auto_mode()
    │       ├─ 加载 outline total_chapters
    │       └─ 创建 AutoChapterRunner(start=N, end=total)
    │
    └─ AutoChapterRunner.run() [cli/chapter_runner.py:274-413]
        │
        ├─ _load_auto_run_progress()  ← 断点续传
        ├─ 创建 PlotGuardHandler
        │
        └─ for chapter in range(start, end+1):
            │
            ├─ [断点检查] chapter.md + exit_state + creative_report 都存在？
            │   ├─ 是 → continue (跳过)
            │   └─ 否 → 继续生成
            │
            ├─ ChapterRunner.run(chapter) [cli/chapter_runner.py:58-149]
            │   └─ _execution.execute_run_chapter() [workspace/execution_runners.py:170-253]
            │       ├─ 获取 memory_context
            │       ├─ 创建 runner (pipeline/chapter_runner.py)
            │       └─ runner.run_chapter()
            │           └─ run_long_chapter() [pipeline/long/loop.py:112-247]
            │               ├─ prepare_long_project() ← 每章重新 load CanonState
            │               ├─ introduce_new_characters() ← 前置角色建档
            │               ├─ StoryMemoryManager.build_packet() ← 构建上下文
            │               ├─ execute_chapter_pipeline() [pipeline/long/chapter_flow.py:1535-1689]
            │               │   │
            │               │   ├─ Phase 1: prepare_chapter_plan()
            │               │   │   ├─ memory.build_packet() ← CanonRetriever.get_context()
            │               │   │   ├─ compress_prompt_context()
            │               │   │   └─ generate_bridge_and_plan()
            │               │   │
            │               │   ├─ Phase 2: review_chapter_draft()
            │               │   │   ├─ generate_draft_and_edit() ← 草稿 + N轮编辑
            │               │   │   ├─ run_opening_guard_patch() ← 开场门禁修补
            │               │   │   ├─ run_quality_checks() ← 对齐 + 连续性 + 章节修复
            │               │   │   ├─ run_continuity_repair_v2() ← 连续性修复编排
            │               │   │   ├─ alignment_repair_edit() ← 对齐修复（1轮）
            │               │   │   ├─ run_self_repetition_check() ← 自我重复检测
            │               │   │   ├─ run_pronoun_check() ← 代词检查
            │               │   │   ├─ run_causal_repair_v2() ← 因果链修复编排
            │               │   │   └─ run_reading_power_repair_v2() ← 追读力修复编排
            │               │   │
            │               │   └─ Phase 3: finalize_chapter_result()
            │               │       ├─ [可选 polish] apply_polish_pass()
            │               │       ├─ persist_results()
            │               │       │   ├─ _clean_and_validate_chapter_text() ← 文本守卫
            │               │       │   ├─ merge_and_persist_outcome() ← Canon merge + persist
            │               │       │   ├─ invalidate_downstream_generated_artifacts()
            │               │       │   ├─ evaluate_chapter_text()
            │               │       │   ├─ _finalize_volume_if_needed()
            │               │       │   └─ StrandTracker.record()
            │               │       └─ 构建 ChapterResult
            │               │
            │               ├─ enrich_introduced_characters() ← 后置角色精化
            │               ├─ auto_register_from_creative_report() ← 新角色建档
            │               ├─ _trigger_memory_update() ← 记忆索引（非阻塞重试）
            │               ├─ _emit_budget_status() ← 预算状态
            │               └─ _check_style_trend() ← 风格趋势检测
            │
            ├─ _save_auto_run_progress() ← 每章完成后写盘
            │
            ├─ plot_guard_handler.handle_major_deviation()
            │   ├─ free 模式 → 直接通过
            │   ├─ ai_judge 模式 → LLM 判断（accept/adjust/pause/rollback）
            │   └─ pause_for_human → 返回 False → break 循环
            │
            └─ asyncio.sleep(cooldown) ← 章间冷却
        │
        └─ [全书审计] BookConsistencyStep (>=2章时触发)
```

### 2.3 核心数据载体流转图

| 数据载体 | 生产者 | 消费者 | 持久化位置 | 生命周期 |
|---------|--------|--------|-----------|---------|
| `LongProjectBundle` | `prepare_long_project()` | 全流程 | 内存（每章新建） | 单章 |
| `ChapterStatePacket` | `StoryMemoryManager.build_packet()` | plan, draft, quality | `states/chapter_{n}_state_packet.json` | 单章 |
| `ChapterBridge` | `generate_bridge_and_plan()` | draft, quality | `plans/chapter_{n}_bridge.json` | 单章 |
| `ChapterPlan` | `generate_bridge_and_plan()` | draft, quality | `plans/chapter_{n}_plan.json` | 单章 |
| `ChapterOutcome` | `extract_and_validate()` | finalize, canon merge | `states/chapter_{n}_canon_outcome.json` (临时) | 单章 |
| `CanonDelta` | `ExtractCanonDeltaStep` | `CanonMerger.merge()` | `canon/canon_current.json` | 全书累积 |
| `ReviewProgressState` | `save_review_progress()` | `review_chapter_draft()` resume | `states/chapter_{n}_review_progress.json` | 单章 |
| `CanonState` | `merge_and_persist_outcome()` | `CanonRetriever.get_context()` | `canon/canon_current.json` + `canon/canon_v{N}.json` | 全书累积 |

### 2.4 依赖注入关系图

```
RuntimeServices (workspace/runtime.py)
    │
    ├─ storage: FileSystemStorage
    ├─ router: ModelRouter
    ├─ builder: PromptBuilder
    ├─ settings: UnifiedSettings
    │
    └─ chapter_runner() 创建 pipeline/chapter_runner.py::ChapterRunner
        │
        ├─ router, builder, storage, settings, config, memory_context
        │
        └─ run_chapter() → run_long_chapter()
            │
            └─ 创建 ChapterExecutionContext
                │
                ├─ storage, router, builder, settings, config
                ├─ merger: CanonMerger (单例)
                ├─ rules: ConsistencyRules (单例)
                ├─ retriever: CanonRetriever
                ├─ memory_context
                └─ 多个 function-level callbacks
                    │
                    └─ execute_chapter_pipeline(context, bundle, trace, memory)
```

---

## 3. 模块间数据流分析

### 3.1 数据载体传递路径评估

#### ✅ 设计优秀的方面

1. **单一写入者原则**：每个核心数据载体仅由一个模块负责创建
   - `ChapterStatePacket` 仅由 `StoryMemoryManager.build_packet()` 创建
   - `ChapterBridge` 和 `ChapterPlan` 仅由 `generate_bridge_and_plan()` 创建
   - `CanonDelta` 仅由 `ExtractCanonDeltaStep` 提取

2. **持久化时机合理**：关键载体在生成后立即持久化
   - `ChapterStatePacket` → `persist_chapter_state_packet()` [chapter_flow.py:541]
   - `ChapterBridge` → `persist_bridge()` [canon_ops.py]
   - ReviewProgressState → 每个阶段完成后保存 [chapter_flow.py:779, 1083, 1176, 1378]

3. **Hash 一致性验证**：`persist_results()` 在文本清理后重新 stamp report hash [finalize.py:458-479]，确保报告与文本版本匹配

#### ⚠️ 需要注意的方面

1. **CanonState 跨章传递依赖文件系统原子性**
   - 每章调用 `prepare_long_project()` 重新 load `canon_current.json` [preflight.py:106]
   - 依赖 `atomic_write_text()` 保证写入原子性 [canon/store.py:64]
   - **风险**：如果章 N 的 Canon merge 成功但章 N+1 的 load 发生在 merge 完成前（理论上不可能，因为是串行），会导致状态丢失

2. **ChapterOutcome 的临时持久化**
   - `extract_and_validate()` 保存 `chapter_canon_outcome.json` [chapter_flow.py:1374-1377]
   - 完成后被删除 [chapter_flow.py:1449-1453]
   - **设计合理**：仅用于断点续传，完成后清理避免残留

3. **ReviewProgressState 与 ReviewDraft 的数据冗余**
   - `ReviewProgressState.current_text` 与 `chapter_review_draft.md` 保存相同内容
   - **可接受**：checkpoint 使用 JSON 格式便于快速加载，draft 文件用于人工查看和恢复

### 3.2 Canon 和 Memory 读写时机评估

#### Canon 读写时机

| 时机 | 操作 | 文件位置 | 评估 |
|------|------|---------|------|
| 章节开始前 | `canon_store.load()` | preflight.py:106 | ✅ 正确 |
| Bridge/Plan 生成 | `CanonRetriever.get_context()` | context.py (via build_packet) | ✅ 正确 |
| 章节完成后 | `merge_and_persist_outcome()` | finalize.py:500-507 | ✅ 正确 |
| 卷末 | `apply_volume_compaction()` | finalize.py:604-620 | ✅ 正确 |
| 强制重生成 | `canon_store.rollback_to()` | preflight.py:109 | ✅ 正确 |

#### Memory 读写时机

| 时机 | 操作 | 文件位置 | 评估 |
|------|------|---------|------|
| 章节开始前 | `StageMemoryBuilder.collect_*()` | stage_memory_builder.py | ✅ 正确 |
| 章节完成后 | `memory_ctx.finalize_chapter_memory()` | loop.py:68 | ✅ 正确 |
| 强制重生成 | `memory_ctx.invalidate_chapter_memory()` | finalize.py:536 | ✅ 正确 |

**关键观察**：Memory 更新在 `run_long_chapter()` 的末尾调用 [loop.py:237-242]，在 Canon merge 之后。这确保了 Memory 索引的是最终持久化的章节文本。

### 3.3 发现的问题与建议

#### 问题 1: Memory 更新失败后的 Canon 不一致

**描述**：`_trigger_memory_update()` 失败后，Canon 已更新但 Memory 未索引。后续章节通过 `CanonRetriever.get_context()` 获取的 Canon 上下文包含新状态，但 `EpisodicMemory.search_by_semantic()` 无法检索到该章的事件。

**影响**：中等。后续章节仍可通过 Canon 上下文获取关键信息，但缺少语义增强的历史事件检索。

**建议**：
- 在 `_trigger_memory_update()` 彻底失败后，写入一个标记文件（如 `states/chapter_{N}_memory_pending.json`）
- 下一章开始时检查该标记，如有则尝试补充索引
- 或在 Desktop UI 中提供"手动触发记忆索引"按钮

#### 问题 2: ReviewProgressState 不包含 CanonDelta

**描述**：`canon_done` checkpoint 保存了所有 report，但未保存 `ChapterOutcome`。如果 pipeline 在 `canon_done` checkpoint 保存后、`finalize_chapter_result()` 调用前崩溃，恢复时会重新执行 `extract_and_validate()`，导致一次额外的 LLM 调用。

**影响**：低。仅影响成本和执行时间，不影响正确性（LLM 提取的 CanonDelta 应保持一致）。

**建议**：考虑在 `canon_done` checkpoint 中保存 `ChapterOutcome` 的序列化数据，避免重新调用 LLM。

---

## 4. 状态管理一致性分析

### 4.1 CanonState 生命周期图

```
项目初始化
    │
    ├─ init_long_project()
    │   └─ canon_store.init(project_id)
    │       └─ CanonState(project_id, current_chapter=0)
    │           └─ save(canon_current.json)
    │
章节 N 生成
    │
    ├─ prepare_long_project(chapter=N)
    │   ├─ stale_chapter_cutoff 检查 [preflight.py:88-92]
    │   ├─ canon_store.load() → canon_current.json
    │   ├─ 检查 canon_state.current_chapter >= N?
    │   │   ├─ 是 + force_regenerate → rollback_to(N-1)
    │   │   └─ 是 + !force → ValueError
    │   └─ 返回 LongProjectBundle(canon_state=...)
    │
    ├─ execute_chapter_pipeline()
    │   └─ finalize_chapter_result()
    │       └─ persist_results()
    │           ├─ save_text(chapter_N.md) ← 先保存正文
    │           ├─ merge_and_persist_outcome()
    │           │   ├─ state_tracker.merge_outcome(canon_state, outcome)
    │           │   ├─ canon_store.save(new_state, snapshot=True)
    │           │   │   ├─ save(canon_current.json) ← 原子写入
    │           │   │   └─ save(canon_vN.json) ← 快照
    │           │   └─ save(chapter_N_exit_state.json)
    │           └─ invalidate_downstream_generated_artifacts(N)
    │               └─ 删除 N+1 之后的下游产物
    │
    └─ _trigger_memory_update()
        └─ memory_ctx.finalize_chapter_memory()
```

### 4.2 断点续传路径覆盖表

| 中断点 | 续传机制 | 覆盖程度 | 恢复行为 | 评估 |
|--------|---------|---------|---------|------|
| 全书连跑章节跳过 | AutoChapterRunner 文件检查 | 文件级 | 跳过已完成章节 | ✅ 完善 |
| Draft 生成中 | Emergency checkpoint | draft_done | 恢复最新草稿 | ✅ 完善 |
| Draft 完成后 | ReviewProgressState | draft_done | 跳过 draft 生成 | ✅ 完善 |
| Quality 检查中 | ReviewProgressState | draft_done | 重新执行 quality | ⚠️ 部分 |
| Quality 完成后 | ReviewProgressState | quality_done | 跳过 quality + continuity repair | ✅ 完善 |
| Causal 修复中 | ReviewProgressState | quality_done | 重新执行 causal repair | ⚠️ 部分 |
| Causal 修复完成后 | ReviewProgressState | repair_done | 跳过 causal repair | ✅ 完善 |
| Canon 提取中 | ReviewProgressState | repair_done | 重新执行 extract | ⚠️ 部分 |
| Canon 提取完成后 | ReviewProgressState | canon_done | 跳过 extract | ✅ 完善 |
| Canon merge 后 | 无专门机制 | - | Canon 已持久化，可安全恢复 | ✅ 安全 |
| Memory update 后 | 无 checkpoint | - | Memory 更新幂等，可安全重试 | ✅ 安全 |

### 4.3 全书连跑跨章节状态传递

**关键路径分析**：

```
章 N 完成
    │
    ├─ CanonState 更新 (canon_current.json)
    │   └─ current_chapter = N
    │
    └─ 章 N+1 开始
        │
        └─ prepare_long_project(chapter=N+1)
            ├─ canon_store.load() ← 读取 canon_current.json
            │   └─ current_chapter = N ✅ 正确
            │
            └─ StoryMemoryManager.build_packet()
                └─ CanonRetriever.get_context(canon_state, N+1)
                    ├─ 加载 N 章的 exit_state ✅
                    ├─ 加载近期事件 ✅
                    ├─ 加载活跃伏笔 ✅
                    └─ 加载活跃关系和情节线 ✅
```

**评估**：跨章节状态传递机制设计正确。每章重新 load CanonState 确保读到最新状态。

### 4.4 记忆系统持久化验证

#### EpisodicMemory 持久化

| 组件 | 持久化方式 | 触发时机 | 恢复能力 |
|------|-----------|---------|---------|
| `_index` (内存索引) | `save_to_disk()` → `project_memory.json` | 章节完成后 | ✅ 完整 |
| `_chapter_events` | 同上 | 同上 | ✅ 完整 |
| 向量存储 (Zvec) | 自动持久化 (Arrow IPC) | 每次 add 后 | ✅ 完整 |
| MotifTracker | `save_to_disk()` → `project_memory.json` | 章节完成后 | ✅ 完整 |

#### 发现的问题

**问题**: `MemoryContext.save_to_disk()` 在 CLI 模式和 Desktop 模式下的调用时机不同
- CLI 模式：在 `_trigger_memory_update()` 内部调用
- Desktop 模式：可能在 session handler 中调用

**影响**：低。两种模式最终都会调用保存。

### 4.5 发现的问题与风险点

| 风险点 | 严重程度 | 发生概率 | 影响范围 | 描述 |
|--------|---------|---------|---------|------|
| Canon-Memory 时序窗口 | 中 | 低 | 单章 | Memory 更新失败后，后续章节缺少语义检索 |
| ReviewProgressState 不包含 Outcome | 低 | 中 | 单章 | 恢复时重新调用 LLM 提取 Canon |
| 全书连跑进度文件竞争 | 低 | 极低 | 全书 | 多进程同时运行同一项目时可能覆盖进度 |

---

## 5. 依赖注入与组件生命周期

### 5.1 依赖注入图谱

```
RuntimeServices
    │
    ├─ storage: FileSystemStorage ← 全局单例
    ├─ router: ModelRouter ← 全局单例
    ├─ builder: PromptBuilder ← 全局单例
    ├─ settings: UnifiedSettings ← 全局单例
    │
    └─ 每章创建
        │
        ├─ ChapterRunner (pipeline/chapter_runner.py)
        │   ├─ CanonMerger ← 单例 (每章复用)
        │   ├─ ConsistencyRules ← 单例 (每章复用)
        │   ├─ CanonRetriever ← 单例 (每章复用)
        │   └─ memory_context ← 每章获取
        │
        └─ ChapterExecutionContext
            ├─ 上述所有依赖
            └─ function-level callbacks
```

### 5.2 组件创建/销毁时序

| 组件 | 创建时机 | 销毁时机 | 生命周期 | 评估 |
|------|---------|---------|---------|------|
| `RuntimeServices` | CLI 启动 | CLI 退出 | 应用级 | ✅ 合理 |
| `ChapterRunner` | 每章 | 每章完成 | 章节级 | ✅ 合理 |
| `CanonMerger` | ChapterRunner 初始化 | ChapterRunner 销毁 | 章节级 | ✅ 无状态，可复用 |
| `ConsistencyRules` | ChapterRunner 初始化 | ChapterRunner 销毁 | 章节级 | ✅ 无状态，可复用 |
| `MemoryContext` | 每章获取 | 每章完成 | 章节级 | ✅ 合理 |
| `LongProjectBundle` | 每章 | 每章完成 | 章节级 | ✅ 合理 |

### 5.3 循环依赖检查

**未发现循环依赖**。依赖方向清晰：

```
CLI → Execution → Pipeline → Steps → Core/Canon/Memory
```

### 5.4 发现的问题与建议

**问题**: `_FlowProxy` 适配器增加间接层 [chapter_flow.py:329-422]

**描述**：`_FlowProxy` 将 `ChapterExecutionContext` 包装为 `runner` 对象，提供统一的属性访问接口。这种设计增加了代码复杂度，但好处是允许 helper 函数使用统一的 `runner._xxx` 访问模式。

**建议**：保持现状。虽然增加间接层，但确实简化了 helper 函数的实现。

---

## 6. 错误处理与恢复能力

### 6.1 断点续传机制评估

#### AutoChapterRunner 断点续传

**文件**: `cli/chapter_runner.py:298-321`

```python
if not self.force and layout.chapter_path(chapter_number).exists():
    _exit_state_ok = layout.chapter_exit_state_path(chapter_number).exists()
    _creative_report_ok = layout.creative_report_path(chapter_number).exists()
    if _exit_state_ok and _creative_report_ok:
        # 跳过
```

**评估**: ✅ 设计合理。验证 3 个文件确保章节完整生成，避免跳过不完整的章节。

#### ReviewProgressState 断点续传

**文件**: `chapter_flow.py:693-699`

```python
_STAGE_ORDER = ("draft_done", "quality_done", "repair_done", "canon_done")
_skip_draft = _resume_stage in _STAGE_ORDER      # 任何阶段都跳过
_skip_quality = _resume_stage in _STAGE_ORDER[1:]  # quality_done+ 跳过
_skip_repair = _resume_stage in _STAGE_ORDER[2:]   # repair_done+ 跳过
_skip_extract = _resume_stage in _STAGE_ORDER[3:]  # canon_done 跳过
```

**评估**: ✅ 设计合理。4 级 checkpoint 覆盖主要中断点。

### 6.2 Emergency checkpoint 完整性

**文件**: `chapter_flow.py:751-777`

```python
except (asyncio.CancelledError, KeyboardInterrupt, ModelGatewayError):
    _recovered = _recover_latest_draft(context.storage, bundle.layout, chapter_number)
    if _recovered:
        save_review_progress(
            storage=context.storage,
            layout=bundle.layout,
            chapter_number=chapter_number,
            progress=_ReviewProgressState(
                completed_stage="draft_done",
                current_text=_recovered,
                performed_edits=0,
            ),
        )
```

**评估**: ✅ 设计合理。捕获异常后尝试恢复最新草稿并保存 checkpoint。

### 6.3 全书连跑中断恢复场景矩阵

| 中断场景 | 恢复机制 | 数据丢失风险 | 恢复行为 |
|---------|---------|-------------|---------|
| 章 N 生成中 (draft) | Emergency checkpoint | 最多丢失当前编辑轮次 | 恢复最新草稿，跳过 draft 生成 |
| 章 N 生成中 (quality) | ReviewProgressState (draft_done) | 无 | 重新执行 quality checks |
| 章 N 生成中 (causal) | ReviewProgressState (quality_done) | 无 | 重新执行 causal repair |
| 章 N 生成中 (extract) | ReviewProgressState (repair_done) | 无 | 重新执行 extract |
| 章 N 完成后, 章 N+1 开始前 | AutoChapterRunner 跳过 | 无 | 跳过章 N |
| 全书连跑被中断 | AutoChapterRunner + ReviewProgressState | 最多丢失当前章 | 从断点继续 |
| Canon merge 后崩溃 | 文件系统原子性 | 无 | Canon 已持久化，安全恢复 |
| Memory update 后崩溃 | Memory 持久化 | 无 | Memory 已持久化，安全恢复 |

### 6.4 发现的问题与改进建议

#### 改进建议 1: Canon merge 后增加 checkpoint

**当前状态**：Canon merge 成功后立即保存 `canon_current.json`，但 `ReviewProgressState` 的 `canon_done` checkpoint 在之后保存。如果 merge 成功但 checkpoint 保存失败，恢复时会重新执行 `extract_and_validate()`。

**建议**：在 `merge_and_persist_outcome()` 成功后立即保存一个轻量级 checkpoint（仅包含 `canon_merged=True` 标记），避免重新调用 LLM。

---

## 7. 性能与扩展性分析

### 7.1 串行执行瓶颈识别

#### 每章 LLM 调用次数估算

| 阶段 | 调用次数 | 说明 |
|------|---------|------|
| Planning | 1-2 | Bridge + Plan (可能分批) |
| Draft | 1 | 初始草稿 |
| Edit | N | N 轮编辑 (默认 2) |
| Opening Guard | 0-1 | 可选修补 |
| Quality Checks | 3-4 | Alignment + Continuity + Chapter Repair |
| Continuity Repair | 0-M | M 轮修复 (默认 2) |
| Alignment Repair | 0-1 | 可选修复 |
| Self Repetition | 0 | CPU 计算 |
| Pronoun Check | 0-1 | 可选 |
| Causal Validation | 1 | 校验 |
| Causal Repair | 0-K | K 轮修复 (默认 2) |
| Reading Power Repair | 0-L | L 轮修复 |
| Extract Canon | 1-2 | 提取 + 可选重试 |
| Evaluate | 1 | 评估 |
| Polish | 0-1 | 可选 |
| **总计** | **10-25** | 取决于修复轮次 |

**评估**：在合理范围内。但 200 章项目可能需要 2000-5000 次 LLM 调用，总执行时间会很长。

### 7.2 内存占用模式分析

#### EpisodicMemory 内存增长

| 组件 | 每章增长 | 100 章预估 | 200 章预估 | 风险 |
|------|---------|-----------|-----------|------|
| `_index` | ~5KB/章 | ~500KB | ~1MB | ✅ 低 |
| `_chapter_events` | ~100B/章 | ~10KB | ~20KB | ✅ 低 |
| `_outline_index` | 固定 | 固定 | 固定 | ✅ 低 |
| `_critique_index` | ~200B/章 | ~20KB | ~40KB | ✅ 低 |
| 向量存储 (Zvec) | ~50KB/章 | ~5MB | ~10MB | ⚠️ 中 |

**评估**：内存增长可控。Zvec 的 `memory_limit_mb=512` 足够支持 10000+ 章节。

#### CanonState 内存增长

| 字段 | 每章增长 | 100 章预估 | 200 章预估 | 风险 |
|------|---------|-----------|-----------|------|
| `characters` | 角色状态更新 | ~50KB | ~100KB | ✅ 低 |
| `timeline` | 事件追加 | ~100KB | ~200KB | ✅ 低 |
| `foreshadowing` | 伏笔更新 | ~20KB | ~40KB | ✅ 低 |
| `chapter_summaries` | 摘要追加 | ~50KB | ~100KB | ✅ 低 |
| `banned_phrases` | 短语累积 (max 30) | ~2KB | ~2KB | ✅ 低 |

**评估**：CanonState 增长可控。但有卷末压缩机制 (`apply_chapter_compaction`) 归档 inactive 角色和压缩近期事件。

### 7.3 向量存储规模评估

**ZvecVectorStore 配置**:
- `memory_limit_mb=512`
- 索引类型: HNSW (默认)
- 度量: COSINE

**规模估算**:
- 每章向量数: ~10-20 (章节级 + 场景级)
- 每向量大小: 768 或 1536 维 (取决于 embedding 模型)
- 每章存储: ~50-100KB
- 512MB 可支持: ~5000-10000 章

**评估**: ✅ 足够支持大型项目。

### 7.4 修复循环预算控制

#### 全局修复预算

**配置**: `long_global_repair_budget` (默认 3)

**应用逻辑** [chapter_flow.py:879-921]:
```python
_global_budget = getattr(context.settings, "long_global_repair_budget", 3)
_global_rounds_used = 0

# 当连续性修复用尽且全局预算>0时，压缩因果修复轮次
if _repair_exhausted and _global_budget > 0:
    _causal_max_rounds = min(_causal_max_rounds, max(0, _global_budget - 1))
```

**评估**: ✅ 设计合理。防止无限修复循环。

### 7.5 大规模项目（200+章）预测

| 指标 | 100 章 | 200 章 | 500 章 |
|------|-------|-------|-------|
| LLM 调用次数 | 1000-2500 | 2000-5000 | 5000-12500 |
| CanonState 大小 | ~300KB | ~600KB | ~1.5MB |
| 向量存储大小 | ~5-10MB | ~10-20MB | ~25-50MB |
| Snapshot 文件数 | 100 | 200 | 500 |
| 预估执行时间 | 2-5 小时 | 4-10 小时 | 10-25 小时 |

**评估**: 200+ 章项目在技术上可行，但执行时间较长。建议考虑：
1. 章节级并发（独立章节可并行生成）
2. 向量存储分层（热/温/冷数据）
3. Canon 状态增量加载（仅加载活跃部分）

---

## 8. 质量控制评估

### 8.1 各检查点阈值合理性

| 检查点 | 阈值 | 来源 | 可配置 | 评估 |
|--------|------|------|--------|------|
| Alignment | `long_alignment_threshold` (7.0) | Settings | ✅ | 合理 |
| Continuity Repair | `long_continuity_repair_threshold` (8.5) | Settings | ✅ | 合理 |
| Causal Repair | 内部阈值 | decisions.py | ⚠️ | 建议可配置 |
| Reading Power | `critical_score_threshold` (3.0), `warning_score_threshold` (5.0) | StyleProfile | ✅ | 合理 |

### 8.2 修复循环终止条件

**三重保护机制**:

1. **Max Rounds**: 各修复循环有最大轮次限制
2. **Stagnation Detection**: `_STAGNATION_SCORE_ELTA = 0.3` 检测分数停滞
3. **Exhaustion**: 全局修复预算用尽

**评估**: ✅ 设计合理，不会无限循环。

### 8.3 语义漂移检测有效性

**文件**: `core/utils/semantic_drift.py`

**机制**: 比较修复前后的文本，检测：
- POV 角色行为变化
- 角色知识状态变化
- 情节逻辑变化

**评估**: ✅ 有效。但仅检测高严重度漂移，中等漂移可能被忽略。

### 8.4 PlotGuard 判断逻辑

**文件**: `common/plot_guard.py`, `cli/plot_guard.py`

**3 种模式**:
- `free`: 直接通过
- `ai_judge`: LLM 判断
- `strict/normal`: 手动模式

**AI Judge 决策类型**:
- `continue` → 继续
- `continue_with_constraints` → 带约束继续
- `adjust_outline_fast/smart` → 调整大纲
- `pause_for_human` → 暂停
- `rollback_and_regen` → 回滚重生成

**评估**: ✅ 设计合理。但 `ai_judge` 模式的输入数据（对齐报告、连贯性报告、因果报告等）可能过多，导致 LLM 上下文过长。

### 8.5 全书一致性审计

**触发条件**: `len(self.completed_chapters) >= 2`

**审计范围**: `chapter_range=sorted(self.completed_chapters)`

**失败处理**: `try/except` 不中断流程

**评估**: ✅ 设计合理。但审计结果未反馈到后续生成中（仅用于报告）。

---

## 9. 风险矩阵

| 风险项 | 严重程度 | 发生概率 | 影响范围 | 缓解措施 |
|--------|---------|---------|---------|---------|
| Memory 更新失败导致后续章节缺少上下文 | 中 | 低 | 单章 | 已有重试机制，建议增加补充索引 |
| Canon 状态传递依赖文件系统原子性 | 低 | 极低 | 全书 | atomic_write_text 保证原子性 |
| 大规模项目执行时间过长 | 中 | 高 | 全书 | 建议探索章节级并发 |
| PlotGuard AI Judge 上下文过长 | 低 | 中 | 单章 | 建议压缩输入数据 |
| 修复循环在边界情况下过度修复 | 低 | 低 | 单章 | 已有三重保护机制 |
| 全书连跑多进程竞争 | 低 | 极低 | 全书 | _project_lock 保护 |
| Canon 状态无限增长 | 低 | 低 | 全书 | 卷末压缩机制 |

---

## 10. 改进建议优先级清单

### P0 (立即修复)

1. **Canon-Memory 时序窗口补充机制**
   - 在 `_trigger_memory_update()` 彻底失败后写入标记文件
   - 下一章开始时检查并补充索引

2. **断点续传跳过逻辑加固**
   - 在 AutoChapterRunner 跳过章节时，验证 `review_progress.json` 是否已清理
   - 避免残留的 checkpoint 导致后续单章运行时的混淆

### P1 (短期优化)

3. **记忆更新重试策略增强**
   - 当前重试策略: `(1.0, 2.0)` 秒指数退避
   - 建议: 增加重试次数到 3 次，总等待时间不超过 10 秒

4. **大规模内存增长控制**
   - EpisodicMemory 增加定期清理机制（如每 50 章清理过期索引）
   - CanonState 压缩时机优化

5. **PlotGuard 决策日志**
   - 记录每次 AI Judge 的输入摘要和决策结果
   - 便于后续分析和优化 prompt

### P2 (中期规划)

6. **章节级并发探索**
   - 对于已规划好大纲的章节，探索并发生成的可能性
   - 需要解决 Canon 状态并发访问问题

7. **向量存储分层**
   - 热数据（近 10 章）: 内存索引
   - 温数据（10-100 章）: Zvec HNSW
   - 冷数据（100+ 章）: Zvec IVF 或磁盘索引

8. **Canon 压缩机制优化**
   - 当前仅卷末压缩
   - 建议: 支持定期压缩（如每 20 章）

### P3 (长期愿景)

9. **Pipeline 步骤级并行化**
   - `extract_and_validate` ∥ `evaluate_chapter_text` 已实现
   - 探索更多可并行的步骤组合

10. **增量式 Canon 状态管理**
    - 当前每次 merge 产生完整快照
    - 建议: 支持增量快照，减少存储开销

---

## 附录

### A. 详细调用链路

见 2.2 节全书连跑调用链路图。

### B. 数据载体字段对照表

| 载体 | 关键字段 | 字段数 | 最大尺寸 |
|------|---------|-------|---------|
| `ChapterStatePacket` | canon_context, known_characters, previous_ending | ~30 | ~20KB |
| `ChapterBridge` | bridge_summary, causal_link, opening_location | ~15 | ~5KB |
| `ChapterPlan` | scene_intents, opening_contract, closing_contract | ~20 | ~10KB |
| `ChapterOutcome` | canon_delta, chapter_exit_state, creative_report | ~10 | ~15KB |
| `CanonState` | characters, timeline, foreshadowing, relationships | ~15 | ~300KB (100章) |
| `ReviewProgressState` | current_text, reports, completed_stage | ~10 | ~50KB |

### C. 测试覆盖率统计

| 模块 | 测试文件 | 覆盖率 |
|------|---------|-------|
| decisions.py | tests/unit/test_decisions.py | 高 |
| chapter_flow.py | tests/integration/test_chapter_pipeline.py | 中 |
| canon/store.py | tests/unit/test_canon_store.py | 高 |
| canon/merger.py | tests/unit/test_canon_merger.py | 高 |
| memory/episodic.py | tests/unit/test_episodic_memory.py | 中 |

### D. 参考文件清单

**核心流程** (P0):
- `novel_forge/pipeline/long/chapter_flow.py`
- `novel_forge/cli/chapter_runner.py`
- `novel_forge/pipeline/long/preflight.py`
- `novel_forge/pipeline/long/loop.py`
- `novel_forge/pipeline/long/stages/finalize.py`

**状态与数据** (P1):
- `novel_forge/core/schemas/canon.py`
- `novel_forge/story_kernel/store.py`
- `novel_forge/story_kernel/merger.py`
- `novel_forge/workspace/chapter_session_state.py`
- `novel_forge/pipeline/long/execution_models.py`

**决策与质量** (P2):
- `novel_forge/pipeline/long/decisions.py`
- `novel_forge/common/plot_guard.py`
- `novel_forge/pipeline/long/stages/continuity_repair.py`
- `novel_forge/pipeline/long/stages/causal_repair.py`

**记忆与性能** (P3):
- `novel_forge/memory/episodic.py`
- `novel_forge/pipeline/long/services/stage_memory_builder.py`
- `novel_forge/memory/integration.py`
- `novel_forge/core/context.py`

---

**报告完成**。本分析基于代码审查和架构探索，实际风险和建议的优先级可能需要根据具体使用场景调整。
