# Novel Forge — Workspace Instructions

Novel Forge 是一个 **多模型协作小说创作系统**，Python 异步架构，支持短篇/长篇 AI 辅助创作。  
Stack: Python 3.11+, asyncio, Pydantic v2, React/Tauri NIMO, FastAPI, Typer, Jinja2, pytest. PySide6 is a frozen fallback only.

---

## Build & Test

```bash
# 安装依赖
pip install -e ".[dev]"

# 运行测试（asyncio_mode = "auto"，无需手动管理事件循环）
pytest                            # 全量
pytest tests/unit/                # 单元测试
pytest tests/integration/         # 集成测试
pytest -m regression              # 回归测试

# 并行测试（推荐）
pytest -n auto -q                 # 全量并行
pytest tests/unit/ -n auto -q     # 单元测试并行
pytest tests/integration/ -n 2 -q # 集成测试并行（限制 2 worker）

# Lint + 类型检查
ruff check .                      # line-length = 100
mypy novel_forge/                 # strict 模式

# 打包桌面应用
python scripts/build_desktop.py

# 仅构建 PySide6 兼容后备端
python scripts/build_pyside_desktop.py

# 验证模板和格式契约
python scripts/verify_templates.py
python scripts/verify_format_contracts.py
```

---

## Architecture

分层结构：

| 层 | 路径 | 说明 |
|----|------|------|
| CLI | `novel_forge/cli/` | Typer 命令组 (short/long/chapter/maintenance/system) |
| Primary desktop | `clients/nimo-desktop/` | React + TypeScript + Tauri, Engine contracts only |
| Desktop fallback | `novel_forge/desktop/` | Frozen PySide6 compatibility/emergency client |
| API | `novel_forge/api/` | FastAPI，外部集成入口 |
| Pipeline | `novel_forge/pipeline/` | 步骤编排（short/long 两条主链路） |
| Memory | `novel_forge/memory/` | 情景记忆、母题追踪、多粒度摘要 |
| Core | `novel_forge/core/` | 配置、异常、Format Contracts、缓存 |
| Gateway | `novel_forge/gateway/` | 模型路由、11 个 Provider 适配器 |
| Workspace | `novel_forge/workspace/` | 执行门面、RuntimeServices 单例 |
| Prompts | `novel_forge/prompts/` | PromptBuilder、68 个 Jinja2 模板 |

**章节 Pipeline 主链路**（长篇）：  
`StatePacket → Bridge → Plan → Draft → Edit → Alignment → ContinuityEval → ContinuityRepair → CausalValidation → CausalRepair → Extract → Persist → Evaluate`

完整架构见 [README.md](../README.md) | 详细管道分析见 [docs/pipeline_analysis_report.md](../docs/pipeline_analysis_report.md)

---

## Key Conventions

### PipelineStep 模式
```python
# 所有 Step 继承泛型 ABC，_execute 为异步
class MyStep(PipelineStep[MyInput, MyOutput]):
    async def _execute(self, input_data: MyInput) -> MyOutput: ...
```
自动具备：retry（2次）、token escalation、tracing、logging。

### Frozen Dataclass — 必须用 `dataclasses.replace()`
```python
# ❌ 直接赋值会 FrozenInstanceError
state.field = new_value

# ✅ 正确
import dataclasses
state = dataclasses.replace(state, field=new_value)
```
受影响的关键类：`PendingChapterReviewState`、`TaskFormatContract` 等所有 `@dataclass(frozen=True)`。

### Format Contracts
- 每个 `TaskType` 对应一个 `TaskFormatContract`（`core/format_contracts.py`）
- `enforce_required_keys=True` 时，缺少必填 JSON key → **自动 retry**
- 契约仅定义，不得在运行时修改

### 配置
- 所有配置通过 `NOVEL_FORGE_*` 环境变量注入，Pydantic BaseSettings
- `task_routing` 格式：`'{"DRAFT_CHAPTER":"openai:gpt-4o","EDIT_CHAPTER":"deepseek:deepseek-chat,thinking"}'`
- 修复任务优先使用 `repair_model` 指定的长上下文模型

### 异常层次
```
NovelForgeException
└── NovelForgeError
    ├── ConsistencyViolationError  → 触发章节 Replan（最多 2 次）
    ├── ModelGatewayError
    ├── StorageError
    └── PipelineError
```

---

## Desktop 特有注意事项

- Worker 线程：每个 Job 独立 `asyncio.run()` 事件循环，**不共享主线程 loop**
- UI 更新必须通过 Qt 信号/槽，不得跨线程直接操作 Widget
- `shutdown()` 方法：各 Page 必须在关闭时断开 worker 信号、停止定时器
- 详见 [docs/bug_review_full_stack.md](../docs/bug_review_full_stack.md)

---

## Prompts & Templates

- Jinja2 `.j2` 模板位于 `novel_forge/prompts/prompts/`，按 TaskType 分目录
- 新模板须与对应 `TaskFormatContract` 对齐
- 风格模板位于 `_styles/`，运行时自动发现（default/literary/webnovel）

---

## 数据目录结构

```
data/<project_name>/
  spec.json, story_bible.json, character_bible.json, style_profile.json
  outline.json, story_kernel/ ← 内核状态（SQLite + JSON 快照）
  chapters/              ← 已归档章节
  drafts/                ← 草稿
  plans/                 ← 章节计划, element_progress.json
  reports/               ← 创作/评估/连贯性/对齐/因果报告
  states/                ← 状态包
  exports/               ← 导出文件
  logs/<run_id>/         ← summary.json, events.jsonl, model_calls/
  memory/                ← 情景记忆、母题、摘要
```

---

## 参考文档

| 主题 | 文档 |
|------|------|
| 用户手册 & CLI 参考 | [MANUAL.md](../MANUAL.md) |
| 完整功能说明 | [README.md](../README.md) |
| 文件结构说明 | [docs/FILE_STRUCTURE.md](../docs/FILE_STRUCTURE.md) |
| Format Contract 矩阵 | [docs/format_contract_matrix.md](../docs/format_contract_matrix.md) |
| Pipeline 架构分析 | [docs/pipeline_analysis_report.md](../docs/pipeline_analysis_report.md) |
| Causal Repair 模式 | [docs/causal_repair_analysis.md](../docs/causal_repair_analysis.md) |
| Bug 分析全栈 | [docs/bug_review_full_stack.md](../docs/bug_review_full_stack.md) |
| 写作工作流矩阵 | [docs/writing_workflow_info_matrix.md](../docs/writing_workflow_info_matrix.md) |
