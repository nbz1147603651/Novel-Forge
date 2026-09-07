# Runbook: LLM 结构化输出故障诊断与修复

**版本**: v1.0  
**创建日期**: 2026-06-07  
**适用范围**: Novel Forge 所有 TaskType 的 JSON/TEXT 输出违规、format retry 失败、all-routes-failed 场景

---

## 1. 引言

本 runbook 用于诊断和修复 LLM 结构化输出相关的故障。Novel Forge 的每个 TaskType 都有对应的 `TaskFormatContract`，定义了输出格式（JSON/TEXT）、必填字段和 JSON Schema。当 LLM 输出不符合契约时，系统会自动触发 format retry；当所有 retry 和所有路由 provider 都失败时，整个运行会崩溃。

### 1.1 三个典型错误概述

在「弈心锁玉」项目的长篇初始化运行（`20260607-024704_desktop-init-long_eb0c38d2`）中，我们遇到了三个由 `minimax/MiniMax-M3` 引发的级联错误：

| # | TaskType | 错误类型 | 根因 | 影响 |
|---|----------|----------|------|------|
| 1 | `plan_chapter_contracts` | JSON schema 违规 | LLM 输出了 schema 中未定义的 `source` 字段 | format retry 触发，自动恢复 |
| 2 | `extract_init_coherence_claims` | null 值违规 | `payoff_id` / `payoff_kind` 返回 `null` 而非 `string` | format retry 触发，自动恢复 |
| 3 | `repair_init_artifact_patch` | all-routes-failed | 4 个 provider 全部失败（400 bad request / timeout / context overflow） | **运行崩溃**，6 小时工作丢失 |

前两个错误通过 format retry 自动恢复，第三个错误导致整个运行终止。本 runbook 提供从诊断到修复的完整 SOP。

---

## 2. 何时使用本 runbook

当你遇到以下任一症状时，启动本 runbook：

- **症状 A**: Desktop 显示 `format_retry` 警告，任务输出 JSON 解析失败
- **症状 B**: `errors.jsonl` 中出现 `JSON schema validation failed` 错误
- **症状 C**: `errors.jsonl` 中出现 `all route attempts failed` 错误
- **症状 D**: `run_failed` 事件，类型为 `ModelGatewayError`
- **症状 E**: LLM 输出包含 `null` 值但 schema 要求非空类型
- **症状 F**: 多个 provider 连续返回 400/529/timeout，最终 all-routes-failed

**不需要本 runbook 的场景**：
- 纯网络错误（DNS 失败、连接拒绝）→ 检查网络配置
- API Key 无效 → 检查 `.env` 或 `model_profiles.json`
- 系统资源不足（OOM）→ 检查系统监控

---

## 3. 诊断流程

诊断遵循「从现象到根因」的五步法。每一步都产出明确的中间结论，用于决定下一步。

### 步骤 1: 查看 `errors.jsonl` 定位具体 TaskType

```bash
# 找到最新运行的日志目录
ls -lt data/<project_name>/logs/ | head -5

# 查看错误日志
cat data/<project_name>/logs/<run_id>/errors.jsonl | python3 -m json.tool
```

**关键信息提取**：
- `event` 字段：`step`（format retry）、`api_call_error`（provider 错误）、`run_failed`（运行崩溃）
- `data.task` 字段：出错的 TaskType
- `data.error_type` 字段：`ValueError`（schema 违规）、`InternalServerError`（provider 529）、`BadRequestError`（400）
- `data.error` 字段：具体错误描述

**输出**：确定出错的 TaskType 列表和错误类型分类。

### 步骤 2: 查看 `format_errors/*.json` 定位具体字段

```bash
# format_errors 目录在运行日志目录下
ls data/<project_name>/logs/<run_id>/format_errors/
```

每个 `format_errors/*.json` 文件包含：
- `task`: TaskType
- `contract_id`: 使用的 format contract ID
- `contract_mode`: `full_object` / `fragment_object` / `partial_object` / `text_only`
- `error`: 具体 schema 校验错误
- `required_keys`: 必填字段列表
- `missing_keys`: 缺失的字段
- `schema_errors`: JSON Schema 校验错误详情
- `raw_excerpt`: LLM 原始输出片段（前 2000 字符）

**关键判断**：
- `missing_keys` 非空 → LLM 漏了必填字段
- `schema_errors` 非空 → LLM 输出类型不匹配（如 `null` vs `string`）
- `raw_excerpt` 中出现 schema 未定义的字段 → LLM 输出了多余字段

### 步骤 3: 查看 `model_calls/*.json` 查看实际 LLM 输出

```bash
ls data/<project_name>/logs/<run_id>/model_calls/
# 文件名格式: <序号>_<task>_<status>.json
```

每个 `model_calls/*.json` 文件包含完整的 API 请求和响应：
- `request`: 发送给 LLM 的完整 prompt
- `response`: LLM 的原始返回
- `provider` / `model`: 使用的 provider 和模型
- `latency_ms` / `total_tokens`: 性能指标

**关键判断**：
- 响应是否为合法 JSON？→ 如果不是，可能是 markdown 包裹或截断
- 响应中是否包含错误字段？→ 对比 `format_contracts.py` 中的 schema 定义
- 请求 prompt 是否过长？→ 检查 `total_tokens` 和 provider 的上下文窗口限制

### 步骤 4: 用 `audit_routing_config.py` 检查路由

```bash
python3 scripts/audit_routing_config.py --project <project_name>
```

**检查项**：
- 出错 TaskType 路由到哪个 provider/model？
- fallback 链包含哪些 provider？
- `model_profiles.json` 是否覆盖了 `.env` 路由？
- 所有 fallback provider 的上下文窗口是否足够？

**输出**：完整路由路径表，包括主路由和 fallback 链。

### 步骤 5: 用 `lint_task_type_provider.py` 检查 provider 推荐

```bash
python3 scripts/lint_task_type_provider.py --task <TaskType>
```

**检查项**：
- 当前 provider 是否适合该 TaskType 的输出复杂度？
- 是否有更合适的 provider 推荐？
- provider 的能力 profile 是否与实际表现匹配？

**输出**：provider 适配性评估和推荐列表。

---

## 4. 修复 SOP

根据诊断结果，选择对应的修复路径。四类问题互不重叠，按优先级从高到低排列。

### 4.1 路由问题 → 改 `.env` + `model_profiles.json` + reload

**适用场景**：provider 返回 529（过载）、400（bad request）、timeout，或上下文窗口不足。

**修复步骤**：

1. **编辑 `.env`**：修改 `NOVEL_FORGE_TASK_ROUTING` 将出错 TaskType 路由到更稳定的 provider

   ```bash
   # 示例：将 repair_init_artifact_patch 从 minimax 改到 tongyi
   NOVEL_FORGE_TASK_ROUTING='{"repair_init_artifact_patch":"tongyi:qwen-max",...}'
   ```

2. **编辑 `model_profiles.json`**（如果存在且生效）：

   ```json
   {
     "routes": {
       "repair_init_artifact_patch": "tongyi:qwen-max"
     },
     "fallback_routes": {
       "repair_init_artifact_patch": ["deepseek:deepseek-v4-flash", "tencent:hunyuan-2.0-instruct-20251111"]
     }
   }
   ```

3. **热重载**（Desktop 运行时）：
   - 进入「火候」页面 → 点击「保存并测试」
   - 或调用 `RuntimeServices.reload_runtime_dependencies()`

4. **CLI 重启**：CLI 进程每次运行自动读取最新配置，无需额外操作。

**验证**：重新运行出错的任务，确认路由到新 provider 且不再报错。

### 4.2 Schema 问题 → 改 `core/schemas/` + 加 validator

**适用场景**：LLM 输出 `null` 值但 schema 要求非空、类型不匹配、字段缺失。

**修复步骤**：

1. **定位 schema 定义**：在 `novel_forge/core/schemas/` 中找到对应的 Pydantic 模型

2. **添加 `@model_validator(mode="before")`**：对边界情况做防御性 clamp

   ```python
   from pydantic import model_validator

   class MySchema(BaseModel):
       payoff_id: str = Field(..., min_length=1)
       payoff_kind: str = Field(..., min_length=1)

       @model_validator(mode="before")
       @classmethod
       def _normalize(cls, data: Any) -> Any:
           if isinstance(data, dict):
               # null → 默认值
               if data.get("payoff_id") is None:
                   data["payoff_id"] = "unknown"
               if data.get("payoff_kind") is None:
                   data["payoff_kind"] = "unspecified"
           return data
   ```

3. **更新 format contract**：在 `core/format_contracts.py` 中确认 `enforce_required_keys` 设置

4. **运行测试**：`pytest tests/unit/test_<schema_file>.py`

**验证**：用出错的 LLM 原始输出做单元测试，确认 validator 能正确处理边界情况。

### 4.3 提示词问题 → 改 `.j2` + 跑 `audit_prompt_field_coverage.py`

**适用场景**：LLM 持续输出 schema 中未定义的字段、漏掉必填字段、或输出格式不正确。

**修复步骤**：

1. **定位模板**：在 `novel_forge/prompts/prompts/` 中找到对应的 `.j2` 模板

2. **强化输出约束**：
   - 在 prompt 中明确列出所有必填字段
   - 添加「禁止输出额外字段」的约束
   - 对容易出错的字段添加类型说明（如 `"payoff_id": string, 不能为 null`）

3. **运行覆盖度审计**：

   ```bash
   python3 scripts/audit_prompt_field_coverage.py --task <TaskType>
   ```

   该脚本对比 prompt 模板中提到的字段和 `format_contracts.py` 中定义的 schema 字段，报告缺失覆盖。

4. **验证模板**：

   ```bash
   python3 scripts/verify_templates.py
   ```

**验证**：渲染模板后检查输出是否包含所有必填字段的约束说明。

### 4.4 上下文问题 → 改 `call_with_retry` pre-flight + 用 chunked/degraded repair

**适用场景**：provider 返回 `context length exceeded`、`user prompt is too large`、或 timeout。

**修复步骤**：

1. **检查 prompt 大小**：从 `model_calls/*.json` 的 `request` 字段估算 token 数

2. **添加 pre-flight 检查**：在 `call_with_retry` 之前检查 prompt 是否超过 provider 的上下文窗口

   ```python
   # 伪代码
   if estimate_tokens(prompt) > provider.max_context * 0.8:
       # 触发降级策略
       prompt = degrade_context(prompt, target_tokens=provider.max_context * 0.7)
   ```

3. **使用 chunked repair**：将大 payload 拆分为多个小块分别修复

   ```python
   # 伪代码
   chunks = split_payload(large_payload, max_chunk_size=50000)
   for chunk in chunks:
       result = repair_chunk(chunk)
       merged = merge_results(merged, result)
   ```

4. **配置 degraded repair**：在 `model_profiles.json` 中为修复任务配置专门的长上下文模型

**验证**：用最大可能的 payload 测试，确认不会触发 context overflow。

---

## 5. 案例复盘

### 案例 1: `max_reuse=0` — title_policy normalize 丢失

**日期**: 2026-06-07  
**Run ID**: `20260607-003434_desktop-init-long_6b8bb4e8`  
**TaskType**: `derive_editorial_structure`（`DERIVE_EDITORIAL_CONTRACT` 子任务）  
**Provider**: `minimax/MiniMax-M3`

#### 错误现象

LLM 输出 `"max_reuse": 0`，但 `TitlePolicy` schema 定义为 `Field(ge=1, le=10)`。Pydantic 校验失败：

```
ValidationError: title_policy.max_reuse
  Input should be greater than or equal to 1 [type=greater_than_equal, input_value=0]
```

#### 根因分析

`EditorialContractStep._execute` 构造 payload 时，对 `character_voices`、`theme_policies`、`revelation_ladder` 等字段都调用了 normalize 函数，唯独 `title_policy` 直接传递了 raw LLM 输出 `structure.get("title_policy", {})`。

数据流：
```
LLM 输出 {"max_reuse": 0}
  → _seeded_editorial_structure() 未覆盖（truthy 值）
  → payload 构造: structure.get("title_policy", {})  ← 缺少 normalize！
  → EditorialContract.model_validate(payload)
  → ValidationError: max_reuse=0 < ge=1
```

#### 修复方案

1. 在 `editorial/schemas.py` 中为 `TitlePolicy` 添加 `@model_validator(mode="before")`，将 `max_reuse` clamp 到 `[1, 10]`
2. 在 `editorial_contract_step.py` 中新增 `_normalize_title_policy()` 函数
3. payload 构造改为 `_normalize_title_policy(structure.get("title_policy", {}))`

**Fix commit**: `6c6696f9`

#### 教训

- 所有嵌套对象字段都必须有 normalize 函数，不能依赖 LLM 输出合法值
- 新增字段时，对照已有字段的 normalize 模式，确保一致性
- `@model_validator(mode="before")` 是最后一道防线，即使 normalize 遗漏也能 clamp

---

### 案例 2: `payoff_id` null — extract_init_coherence_claims schema 违规

**日期**: 2026-06-07  
**Run ID**: `20260607-024704_desktop-init-long_eb0c38d2`  
**TaskType**: `extract_init_coherence_claims`  
**Provider**: `minimax/MiniMax-M3`

#### 错误现象

LLM 输出中多个 claim 的 `payoff_id` 和 `payoff_kind` 字段为 `null`，但 schema 要求 `string` 类型：

```
JSON schema validation failed:
  $.claims[1].payoff_id: expected string, got null
  $.claims[1].payoff_kind: expected string, got null
  $.claims[3].payoff_id: expected string, got null
  $.claims[4].payoff_id: expected string, got null
```

#### 根因分析

`extract_init_coherence_claims` 的 format contract 使用 `fragment_object` 模式，`enforce_required_keys=True`。LLM 对部分 claim 的 `payoff_id` 字段返回了 `null`（可能表示「无对应回收」），但 schema 定义为 `string` 类型，不接受 `null`。

format retry 机制自动触发，最多 3 次重试。在重试 prompt 中明确指出 `payoff_id` 不能为 `null`，LLM 在后续重试中修正了输出。

#### 修复方案

**短期**（已生效）：format retry 自动恢复，无需人工干预。

**长期**（建议）：
1. 在 schema 中将 `payoff_id` 改为 `Optional[str]`，允许 `null` 表示「无回收」
2. 或在 prompt 中明确说明：「如果该 claim 暂无回收，`payoff_id` 填 `"none"` 而非 `null`」
3. 在 `audit_prompt_field_coverage.py` 中添加 null 值检测

#### 教训

- format retry 是有效的自动修复机制，大多数 schema 违规可以在 1-2 次重试内恢复
- 但 retry 消耗额外的 token 和时间，应在 schema 设计阶段就考虑边界情况
- `null` vs 空字符串 vs 特殊标记值（如 `"none"`）需要在 schema 和 prompt 中统一约定

---

### 案例 3: `repair_init_artifact_patch` — all-routes-failed 级联崩溃

**日期**: 2026-06-07  
**Run ID**: `20260607-024704_desktop-init-long_eb0c38d2`  
**TaskType**: `repair_init_artifact_patch`  
**Provider**: 4 个 provider 全部失败

#### 错误现象

运行在 `repair_init_artifact_patch` 步骤崩溃，错误信息：

```
ModelGatewayError: all route attempts failed for task=repair_init_artifact_patch
```

4 次路由尝试全部失败：

| 尝试 | Provider | Model | 错误 | 耗时 |
|------|----------|-------|------|------|
| 1 | minimax | MiniMax-M3 | `BadRequestError: invalid params, 400 (2013)` | 8.5s |
| 2 | (unknown) | (unknown) | timeout | 37.6s |
| 3 | deepseek | deepseek-v4-flash | `BadRequestError: context length exceeded (1181026 > 1048565 tokens)` | 5.2s |
| 4 | tencent | hunyuan-2.0-instruct-20251111 | `BadRequestError: user prompt is too large` | 6.4s |

总耗时约 6 小时，320 个步骤已完成，最终因修复步骤失败而终止。

#### 根因分析

`repair_init_artifact_patch` 的 prompt 包含了完整的 120 章 chapter contracts（约 1.8 MB），远超所有配置 provider 的上下文窗口限制：

- MiniMax-M3: 返回 400 bad request（可能内部 context overflow）
- DeepSeek-v4-flash: 明确报告 118 万 tokens 超过 104 万限制
- Tencent Hunyuan: 明确报告 `user prompt is too large`

fallback 链中所有 provider 都有类似的上下文限制，导致无一可用。

#### 修复方案

**短期**：
1. 在 `model_profiles.json` 中为 `repair_init_artifact_patch` 配置具有更大上下文窗口的 provider（如 `anthropic/claude-3-5-sonnet`，200K tokens）
2. 或减少修复 prompt 中的 chapter contracts 数量，只发送受影响的批次

**长期**（建议）：
1. 实现 chunked repair：将 120 章 contracts 拆分为多个批次（如每批 20 章），分别修复后合并
2. 在 `call_with_retry` 中添加 pre-flight token 估算，超过阈值时自动触发 degraded 模式
3. 在 `repair_safety.py` 中添加 context overflow 的专用错误分类，触发自动降级策略

#### 教训

- all-routes-failed 是最严重的故障模式，意味着所有配置的 provider 都无法处理当前请求
- 修复任务的 prompt 往往比正常任务大得多（需要包含原始文本 + 问题列表 + 修复指令），必须单独评估上下文需求
- fallback 链中的 provider 应该有**差异化**的上下文窗口，避免所有 provider 在同一阈值下同时失败
- 紧急 checkpoint 机制（`CancelledError` 时保存最新草稿）可以减轻崩溃损失，但不能替代预防

---

## 6. 工具清单

以下工具按诊断流程顺序排列，每个工具对应诊断流程中的一个步骤。

| 工具 | 路径 | 用途 | 对应步骤 |
|------|------|------|----------|
| `audit_routing_config.py` | `scripts/` | 审计 `.env` + `model_profiles.json` 路由配置，输出完整路由路径表 | 步骤 4 |
| `audit_prompt_field_coverage.py` | `scripts/` | 对比 prompt 模板字段与 format contract schema，报告缺失覆盖 | 步骤 5 + 修复 4.3 |
| `audit_prompt_format_layers.py` | `scripts/` | 审计每步输出格式、必填字段和格式边界来源 | 步骤 2 辅助 |
| `verify_templates.py` | `scripts/` | 验证 Jinja2 模板语法合法性 | 修复 4.3 后 |
| `verify_format_contracts.py` | `scripts/` | 验证 format contract 定义与 schema 一致性 | 修复 4.2 后 |
| `audit_contract_schema_depth.py` | `scripts/` | 审计 contract schema 深度，识别嵌套过深的字段 | 修复 4.2 辅助 |
| `lint_prompt_layers.py` | `scripts/` | 验证 prompt 元数据层和输出边界 | 修复 4.3 辅助 |
| `generate_prompt_index.py` | `scripts/` | 生成/检查 prompt INDEX.md | 文档维护 |
| `load_model_capability.py` | `scripts/`（规划中） | 加载并验证 `model_capability_profile.json`，评估 provider 能力匹配度 | 步骤 5 |
| `lint_task_type_provider.py` | `scripts/`（规划中） | 检查 TaskType 与 provider 的适配性，推荐更合适的 provider | 步骤 5 |

### 快速诊断命令

```bash
# 一键诊断：从错误日志到路由审计
cd /path/to/novel-forge
RUN_ID=$(ls -t data/<project>/logs/ | head -1)
echo "=== 错误日志 ===" && cat data/<project>/logs/$RUN_ID/errors.jsonl
echo "=== 路由审计 ===" && python3 scripts/audit_routing_config.py
echo "=== Prompt 覆盖 ===" && python3 scripts/audit_prompt_field_coverage.py --task <TaskType>
```

---

## 7. 进阶参考

### 7.1 相关架构文档

| 文档 | 路径 | 内容 |
|------|------|------|
| 修复统一架构 | `docs/repair_unified_architecture.md` | 修复循环的通用架构、安全策略、回退机制 |
| Pipeline 流程图 | `docs/pipeline_flowcharts.md` | 所有 pipeline 流程的详细链路图 |
| Format Contract 矩阵 | `docs/format_contract_matrix.md` | 所有 TaskType 的 format contract 定义 |
| AGENTS.md | `AGENTS.md` | 项目架构总览，含 exception hierarchy 和 repair 模块架构 |

### 7.2 关键代码文件

| 文件 | 路径 | 内容 |
|------|------|------|
| Format Contracts | `novel_forge/core/format_contracts.py` | TaskType → TaskFormatContract 映射 |
| Router | `novel_forge/gateway/router.py` | 路由决策、retry loop、all-routes-failed 处理 |
| Factory | `novel_forge/gateway/factory.py` | Provider 注册、model_profiles.json 解析、tier 默认映射 |
| Repair Safety | `novel_forge/pipeline/long/repair_safety.py` | 修复安全策略、错误分类、回退动作 |
| Repair Dimensions | `novel_forge/pipeline/long/repair_dimensions.py` | 修复维度协调策略（声明化） |
| Schemas | `novel_forge/core/schemas/` | 所有 Pydantic 数据模型定义 |
| Prompt Templates | `novel_forge/prompts/prompts/` | 所有 Jinja2 模板 |

### 7.3 错误分类速查

| 错误类型 | `error_type` | 常见原因 | 修复路径 |
|----------|-------------|----------|----------|
| Schema 违规 | `ValueError` | LLM 输出类型不匹配、缺失字段、多余字段 | 4.2 + 4.3 |
| Provider 过载 | `InternalServerError` (529) | provider 集群负载高 | 4.1 |
| 请求参数错误 | `BadRequestError` (400) | context overflow、invalid params | 4.1 + 4.4 |
| 超时 | `TimeoutError` | provider 响应慢、prompt 过大 | 4.1 + 4.4 |
| 全部路由失败 | `ModelGatewayError` | 所有 provider 都无法处理 | 4.1 + 4.4 |
| 认证失败 | `AuthenticationError` (401) | API Key 无效 | 检查 `.env` |
| 速率限制 | `RateLimitError` (429) | 请求频率过高 | 等待或降低频率 |

### 7.4 预防性措施

1. **路由配置定期审计**：每月运行 `audit_routing_config.py`，检查是否有 provider 被过度依赖
2. **Prompt 覆盖度检查**：每次新增或修改 TaskType 后，运行 `audit_prompt_field_coverage.py`
3. **Schema 边界测试**：为每个 schema 编写单元测试，覆盖 `null`、空字符串、超长值等边界情况
4. **Fallback 链差异化**：确保 fallback 链中的 provider 有不同的上下文窗口和能力特征
5. **监控 format retry 率**：如果某 TaskType 的 format retry 率持续 > 10%，说明 prompt 或 schema 需要优化
