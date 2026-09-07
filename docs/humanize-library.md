# 拟人化库 (Humanize Library)

写作规则参考项目及原始版权声明见[第三方声明](../THIRD_PARTY_NOTICES.md)。当前统一规则源位于 `novel_forge/core/domain/humanize_rule_catalog.py`。

> 版本: 2.1 | 更新: 2026-07-18 | 源码: `core/humanize_rule_catalog.py` + `memory/humanize_library_store.py` + `memory/humanize_retrieval.py`

## 1. 概述

拟人化库是一个**全局跨项目**的 AI 写作模式检测库。它收录了系统已知的所有 AI 写作痕迹（模板句式、聊天残留、宣传腔等），在章节生成流程的 Humanize 阶段自动召回匹配的模式，为 LLM 润色提供定向改写依据。

核心定位：

- **全局共享、可项目限域**：全局条目跨项目复用，`project_id` 条目只在对应项目生效
- **可扩展**：30 条内置模式 + 用户自定义 + 跨机器导入
- **双通道检测**：确定性全量扫描负责可执行规则，分段 BM25/Zvec 负责语义变体
- **完整性边界清晰**：正则、示例短语和关键词合取保证全章覆盖；语义规则由 LLM 复核，不宣称零漏检
- **统一规则源**：内置库元数据与小说/配音扫描共用 `humanize_rule_catalog.py`

## 2. 存储位置

默认路径：

```
${NOVEL_FORGE_STORAGE_ROOT}/_global/humanize_library/
├── library.db          # SQLite 主数据库（WAL 模式）
├── library.lock        # fcntl 跨进程锁文件
├── zvec_patterns/      # 可选 Zvec 模式向量索引
├── zvec_meta.json      # provider/model/dimension/签名
└── rebuild_progress.json  # 向量重建断点（临时）
```

路径解析规则（`Settings.humanize_library_resolved_path`）：

| 条件 | 解析结果 |
|------|----------|
| `humanize_library_path` 为空（默认） | `{storage_root}/_global/humanize_library` |
| 设为绝对路径 | 直接使用 |
| 设为相对路径 | 基于 `{storage_root}` 解析 |

### Schema 结构

SQLite 数据库包含三张表：

| 表 | 用途 |
|----|------|
| `entries` | 主表，每行一条模式（21 列） |
| `entries_fts` | FTS5 全文索引（pattern_id, pattern_name, category, keywords, notes） |
| `meta` | 元数据（schema_version, created_at） |

`entries` 表关键字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `pattern_id` | TEXT PK | 唯一标识（内置 ID / `lib_user_<8hex>` / `lib_imported_<8hex>`） |
| `pattern_name` | TEXT | 人类可读名称，如"显著性通胀" |
| `detection_method` | TEXT | `regex` / `llm_only` / `vector_only` / `mixed` |
| `severity` | TEXT | `critical` / `high` / `medium` / `low` |
| `source` | TEXT | `builtin` / `user` / `imported` |
| `enabled` | INTEGER | 1=启用，0=禁用 |
| `embedding_signature` | TEXT | 向量指纹（sha256），用于过期检测 |
| `vector_stale` | INTEGER | 1=向量需重建 |
| `hit_count` | INTEGER | 累计命中次数 |

## 3. 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    HumanizeLibrary                          │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────────┐ │
│  │ SQLite   │  │ FTS5     │  │ Zvec (optional)           │ │
│  │ entries  │  │ entries  │  │ HNSW/IVF/FLAT/RabitQ     │ │
│  │ (WAL)    │  │ unicode61│  │ vector similarity         │ │
│  └────┬─────┘  └────┬─────┘  └──────────┬────────────────┘ │
│       │              │                    │                  │
│  ┌────┴──────────────┴────────────────────┴──────────────┐ │
│  │              fcntl 跨进程锁 (library.lock)             │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │         EmbeddingSignature (sha256 向量指纹)           │ │
│  │   compute(provider, model, dimension) → hex digest    │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │         Schema Migration (版本链式升级)                │ │
│  │   meta.schema_version → _MIGRATIONS registry          │ │
│  └───────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

组件职责：

| 组件 | 文件 | 职责 |
|------|------|------|
| `HumanizeLibrary` | `humanize_library_store.py` | CRUD、锁、迁移、向量管理、导入导出 |
| `HumanizeLibraryRetriever` | `humanize_retrieval.py` | 全量位置扫描、分段 BM25/向量检索、候选去重 |
| `HumanizeLibraryEntry` | `core/schemas/humanize_library.py` | Pydantic schema，pattern_id 格式校验 |
| `seed_builtin_patterns()` | `humanize_library_store.py` | 内置模式种子（幂等） |
| `HUMANIZE_RULES` | `core/humanize_rule_catalog.py` | 小说、配音、持久化库共用的可执行规则源 |

## 4. Built-in 条目

共 **30 条**内置模式，定义在 `LIBRARY_BUILTIN_ENTRIES`：

### 26 条正则检测（`detection_method="regex"`，默认启用）

| pattern_id | 名称 | 分类 | 严重度 |
|------------|------|------|--------|
| `significance_inflation` | 显著性通胀 | 叙事轻重 | high |
| `promotional_language` | 宣传腔 | 宣传式描写 | medium |
| `ai_vocabulary` | AI 高频词汇 | AI 词汇 | medium |
| `negative_parallelism` | 否定式并列 | 模板句式 | high |
| `rule_of_three` | 三项列举 | 模板句式 | medium |
| `false_ranges` | 假范围 | 模板句式 | medium |
| `filler_phrases` | 填充短语 | 元语言 | high |
| `generic_conclusions` | 万能结尾 | 模板结尾 | high |
| `hollow_aspect_marker` | 空洞进行态 | 动作虚化 | medium |
| `em_dash_overuse` | 破折号滥用 | 标点习惯 | high |
| `quotation_mark_misuse` | 引号强调 | 标点习惯 | medium |
| `passive_subjectless` | 被动/无主语 | 句法虚化 | high |
| `persuasive_authority` | 说教权威腔 | 说教腔 | high |
| `vague_attribution` | 模糊归因 | 证据空泛 | medium |
| `challenge_future_template` | 挑战/未来模板 | 结构模板 | medium |
| `collaborative_artifact` | 协作对话残留 | 聊天残留 | critical |
| `knowledge_cutoff_disclaimer` | 知识截止声明 | 聊天残留 | critical |
| `sycophantic_tone` | 谄媚语气 | 聊天残留 | high |
| `markdown_formatting_residue` | Markdown/表情残留 | 格式残留 | high |
| `outline_heading_voice` | 结构性标题/提纲腔 | 格式残留 | medium |
| `diff_anchored_writing` | 改动叙述腔 | 元语言 | medium |
| `weak_verb_stacking` | 弱动词堆叠 | 叙事轻重 | high |
| `tautology_marker` | 抽象虚指三连 | 模板句式 | high |
| `binary_judgment_closing` | 二元判断收束 | 模板结尾 | high |
| `pronoun_disappearance_run` | 主语弱化句式 | 叙事轻重 | medium |
| `precise_timestamp_overuse` | 精确时长堆叠 | AI 习惯 | low |

### 4 条 LLM 语义检测（`detection_method="llm_only"`，默认禁用）

| pattern_id | 名称 | 分类 | 严重度 |
|------------|------|------|--------|
| `synonym_cycling` | 同义词循环 | 重复用词 | medium |
| `excessive_hedging` | 过度模糊化 | 元语言 | medium |
| `monotone_rhythm` | 节奏单调 | 句法模式 | low |
| `cross_chapter_template` | 跨章节结构模板 | 结构模板 | medium |

LLM 检测条目需要语义上下文才能判定，默认 `enabled=False`。可通过 CLI 或 Desktop 手动启用。

## 5. 召回流程

`HumanizeLibraryRetriever.retrieve(library, chapter_text, top_k)` 的完整流程：

```
chapter_text
    ├─ 确定性通道：逐段执行所有启用的 regex + 精确示例 + 关键词合取
    │               └─ 返回全部 occurrence（span_start/span_end/paragraph_index）
    └─ 语义通道：逐段 BM25 + Zvec（仅 llm_only/vector_only/mixed 或兼容旧用户条目）
                    └─ 每段 top_k → union fuse → sim_threshold → LLM 裁判

两路候选按 pattern_id + 精确位置去重。内置确定性候选还会与本地扫描结果做
同模式族重叠消歧，避免“否定式并列/二元判断收束”重复消耗裁判额度。
```

关键参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `top_k` | 30 | 每个文本窗口最多保留的语义候选；不截断确定性命中 |
| `sim_threshold` | 0.6 | 最低相似度阈值 |
| `cache_max` | 2048 | 嵌入缓存容量（LRU OrderedDict） |

Zvec 在首次可用时按 embedding provider/model/dimension 懒加载；索引缺失、模型变化、
新增条目或 `vector_stale=True` 时自动全量重建。没有已附着的向量索引时不会调用
embedder，避免“空索引却逐段付费嵌入”。

## 6. 降级路径

系统在各组件故障时自动降级，不阻断章节生成流程：

| 场景 | 行为 | 事件 |
|------|------|------|
| `humanize_library_enabled=False` | 跳过持久化库，使用统一目录中的本地确定性规则 | 无 |
| DB 文件损坏/不可打开 | Humanize 层回退到统一目录中的本地确定性规则 | `humanize_library.unavailable` |
| Zvec 不可用/搜索失败 | `vec_search()` 返回空列表，仅用 BM25 检索 | `humanize_library.degraded` |
| 向量签名不匹配 | 条目标记 `vector_stale=True`，`rebuild_vectors()` 时重建 | `humanize_library.rebuild_progress` |
| Embedder 调用失败 | 单批次标记 stale，继续处理后续批次 | `humanize_library.rebuild_progress` |
| 检索过程异常 | `retrieve()` 捕获所有异常，返回空列表 | `humanize_library.retrieval.failed` |
| fcntl 锁超时 | 抛出 `LibraryLockTimeoutError`（默认 30s 超时） | `humanize_library.lock_timeout` |
| Schema 版本超前 | 抛出 `LibrarySchemaVersionMismatchError`，需升级代码 | 无 |

所有降级路径的设计原则：**宁可少检测，不可阻断创作流程**。

## 7. CLI 使用

命令组：`novel-forge humanize-library`

### 列出条目

```bash
# 默认显示前 20 条
novel-forge humanize-library list

# 限制数量 + 过滤来源
novel-forge humanize-library list --limit 50 --source builtin

# JSON 输出
novel-forge humanize-library list --json
```

### 添加条目

```bash
novel-forge humanize-library add \
  --name "过度使用感叹号" \
  --severity medium \
  --category "标点习惯" \
  --keywords "感叹号,!!!,语气" \
  --example "这真是太棒了！！！"
```

新条目自动分配 `lib_user_<8hex>` ID。

### 删除条目

```bash
novel-forge humanize-library remove lib_user_a1b2c3d4
```

内置条目（`source=builtin`）不可删除，会抛出 `LibraryReadOnlyError`。

### 启用/禁用

```bash
# 启用 LLM 检测条目
novel-forge humanize-library enable synonym_cycling

# 禁用某条目
novel-forge humanize-library disable em_dash_overuse
```

### 查找重复

```bash
novel-forge humanize-library find-duplicates --threshold 0.85
```

### 合并条目

```bash
# 将 source_id 合并到 target（关键词/示例取并集，命中数相加），然后删除 source
novel-forge humanize-library merge lib_user_aaaa1111 lib_user_bbbb2222
```

### 导出/导入

```bash
# 导出为 tar.gz
novel-forge humanize-library export my_library.tar.gz

# 导入（三种策略：skip/overwrite/merge）
novel-forge humanize-library import my_library.tar.gz --strategy merge
```

### 统计信息

```bash
novel-forge humanize-library stats
```

输出示例：

```
┌──────────┬───────┐
│ 指标     │   值  │
├──────────┼───────┤
│ 总数     │    28 │
│ 已启用   │    25 │
│ 正则检测 │    21 │
│ LLM 检测 │     3 │
│ 用户定义 │     4 │
│ 导入     │     0 │
│ 向量过期 │     1 │
│ 最后更新 │ 2026-06-08T... │
└──────────┴───────┘
```

## 8. 配置

5 个环境变量（均通过 `NOVEL_FORGE_` 前缀注入 `Settings`）：

| 环境变量 | Settings 字段 | 默认值 | 说明 |
|----------|---------------|--------|------|
| `NOVEL_FORGE_HUMANIZE_LIBRARY_ENABLED` | `humanize_library_enabled` | `True` | 总开关 |
| `NOVEL_FORGE_HUMANIZE_LIBRARY_PATH` | `humanize_library_path` | `""` (空) | 自定义路径，空则用默认 `{storage_root}/_global/humanize_library` |
| `NOVEL_FORGE_HUMANIZE_LIBRARY_TOP_K` | `humanize_library_top_k` | `30` | 检索返回最大条目数 (1-200) |
| `NOVEL_FORGE_HUMANIZE_LIBRARY_SIM_THRESHOLD` | `humanize_library_sim_threshold` | `0.6` | 最低相似度阈值 (0.0-1.0) |
| `NOVEL_FORGE_HUMANIZE_LIBRARY_SEED_BUILTIN` | `humanize_library_seed_builtin` | `True` | 是否种子内置模式 |

`humanize_library_resolved_path` 是计算属性，根据上述配置解析出最终目录路径。

## 9. Desktop UI

在 Desktop 终稿面板（Final Revision）中集成拟人化库管理：

- **Dashboard 概览**：显示库统计（总数、启用数、向量过期数）
- **条目管理**：查看、添加、编辑、删除用户自定义条目
- **启用/禁用切换**：对 LLM 检测条目可手动启用
- **导入/导出**：通过文件对话框选择 tar.gz 进行跨机器迁移
- **向量重建**：触发 `rebuild_vectors()` 并显示进度

## 10. 跨机器迁移

### 导出

```python
lib = HumanizeLibrary.from_default_path()
blob = lib.export()  # tar.gz bytes，内含 library.json
Path("my_library.tar.gz").write_bytes(blob)
```

导出内容：`library.json`，包含 `schema_version` 和所有条目的完整 JSON。

### 导入

```python
lib = HumanizeLibrary.from_default_path()
blob = Path("my_library.tar.gz").read_bytes()
report = lib.import_archive(blob, merge_strategy="merge")
```

三种合并策略：

| 策略 | 遇到重复 pattern_id 时 |
|------|------------------------|
| `skip` | 跳过，保留本地版本 |
| `overwrite` | 覆盖本地版本（builtin 条目始终跳过），取 `max(hit_count)` |
| `merge` | 合并，`hit_count` 相加 |

`ImportReport` 返回详细统计：`imported` / `skipped` / `overwritten` / `merged` / `vector_stale_count`。

### 签名处理

导入条目的 `embedding_signature` 会保留。如果导入后更换了 embedding 模型，需运行 `rebuild_vectors()` 重建向量，旧签名的条目会被标记 `vector_stale=True`。

## 11. Schema 迁移

当前 Schema 版本：**2.0**（初始版本）。

迁移机制：

- `meta` 表存储 `schema_version`
- `_MIGRATIONS` 字典注册版本升级函数（当前为空，v2.0 是初始版本）
- `run_migrations()` 在库打开时自动执行
- 版本链式升级：`_build_version_chain(from, to)` 按序执行中间版本的迁移函数
- 如果磁盘版本比代码版本新，抛出 `LibrarySchemaVersionMismatchError`

`MigrationReport` 返回：`from_version` / `to_version` / `steps_applied` / `success` / `error`。

未来添加迁移只需：

```python
def _migrate_2_0_to_2_1(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE entries ADD COLUMN new_field TEXT DEFAULT ''")

_MIGRATIONS["2.1"] = _migrate_2_0_to_2_1
```

## 12. Observability 事件

所有事件通过 `obs.project_logger` 发出，可在 `logs/<run_id>/events.jsonl` 中查看：

| 事件名 | 触发时机 | 关键字段 |
|--------|----------|----------|
| `humanize_library.unavailable` | DB 不可打开 | `path`, `error_type`, `error_msg` |
| `humanize_library.initialized` | 库成功打开 | `path`, `total_entries`, `regex_count`, `llm_only_count` |
| `humanize_library.migration_applied` | Schema 迁移执行 | `from_version`, `to_version`, `entries_migrated` |
| `humanize_library.lock_acquired` | fcntl 锁获取成功 | `operation`, `duration_ms` |
| `humanize_library.lock_timeout` | 锁获取超时 | `operation`, `waited_ms` |
| `humanize_library.entry_added` | 新增条目 | `pattern_id`, `source`, `project_id` |
| `humanize_library.entry_bumped` | 命中计数更新 | `chapter`, `count` |
| `humanize_library.degraded` | Zvec 故障或库不健康 | `reason`, `fallback_mode` |
| `humanize_library.rebuild_progress` | 向量重建批次进度 | `processed`, `total`, `checkpoint_path` |
| `humanize_library.seed_completed` | 内置模式种子完成 | `added`, `skipped`, `total` |
| `humanize_library.retrieval.invoked` | 检索开始 | `query_sentences`, `top_k` |
| `humanize_library.retrieval.completed` | 检索完成 | `union_hits`, `duration_ms`, `normalization_method` |
| `humanize_library.retrieval.failed` | 检索异常 | `error_type`, `duration_ms`, `fallback` |

## 13. 已知限制

- **无自动过期**：条目不会因时间或命中频率自动禁用或删除
- **无倒数排名融合**：BM25 和向量分数使用 normalized union + max 融合，不使用倒数排名融合算法
- **无内嵌数据清洗**：不自动检测或移除条目中的敏感信息
- **LLM 检测条目默认关闭**：3 条 `llm_only` 模式需要手动启用，且每次检测消耗额外 LLM 调用
- **向量重建非实时**：更换 embedding 模型后需手动触发 `rebuild_vectors()`，期间向量检索降级为 BM25-only
- **单文件锁**：fcntl 锁粒度为整个数据库，高并发写入场景可能成为瓶颈
- **内置条目不可变**：`source=builtin` 的条目不能通过 `update()` 或 `remove()` 修改，只能禁用
