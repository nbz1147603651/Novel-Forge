# ArtifactCache 基准测试报告

> **Task**: T9 — ArtifactCache 基准测试 (验证是否需要加)
> **Date**: 2026-06-06
> **Decision**: **SKIP** — 现有缓存足够，不需要加新 ArtifactCache

---

## 1. 测试设置

### 现有缓存架构

| 层 | 实现 | 策略 |
|---|------|------|
| Storage 层 | `CachedFileSystemStorage` (`persistence/filesystem.py:324`) | LRU read-through, `mtime_ns + size` 验证, `deepcopy` 隔离, max 256 entries |
| Request 层 | `_ChapterDataCache` (`workspace/execution_io.py:17`) | per-chapter 请求内缓存, 缓存 `character_bible`/`style_profile`/`previous_chapter_ending` |
| Factory | `factory.py:9` | 默认启用 `CachedFileSystemStorage` (当 `storage_cache_enabled=True`) |

### 模拟的章节运行模式

每个章节模拟 7 个 pipeline 阶段 (对应 7 次 LLM 调用):

| 阶段 | 读取的文件 |
|------|-----------|
| 1. Planning | spec, story_bible, character_bible, outline, style_profile |
| 2. Bridge | outline, previous_chapter |
| 3. Draft | chapter_plan, state_packet, style_profile |
| 4. Quality check | draft, character_bible, style_profile |
| 5. Continuity repair | draft, character_bible, previous_chapter |
| 6. Extract | draft, character_bible |
| 7. Evaluate | draft, style_profile, outline |

文件大小模拟真实项目 (~2KB spec, ~8KB bible, ~6KB characters, ~10KB outline, ~3KB style, ~2KB plan, ~4KB state_packet)。

---

## 2. 测量结果

### 5 章节顺序运行

| 章节 | 读取次数 | 缓存命中 | 缓存未中 | 命中率 | 耗时 |
|------|---------|---------|---------|--------|------|
| Ch 1 (冷启动) | 19 | 12 | 7 | 63.2% | 1.8ms |
| Ch 2 (热) | 21 | 18 | 3 | 85.7% | 1.8ms |
| Ch 3 (热) | 21 | 18 | 3 | 85.7% | 1.7ms |
| Ch 4 (热) | 21 | 18 | 3 | 85.7% | 1.7ms |
| Ch 5 (热) | 21 | 18 | 3 | 85.7% | 1.7ms |
| **累计** | **103** | **84** | **19** | **81.6%** | **8.7ms** |

### 关键发现

1. **单章内命中率已 63.2%**: 同一章节运行中, `spec/bible/outline/style` 在不同 pipeline 阶段被重复读取, LRU 缓存命中。
2. **跨章命中率 85.7%**: 第 2+ 章的共享文件 (spec/bible/outline/style) 全部命中缓存, 仅 chapter-specific 文件 (plan/state_packet/draft) 未命中。
3. **deepcopy 隔离有效**: 测试验证修改返回值不会影响缓存数据。
4. **mtime 失效有效**: 文件修改后缓存自动失效。

---

## 3. 决策

### 决策门

```
Measured hit rate:  81.6%
Threshold:          30.0%
Decision:           skip
```

### 理由

1. **现有 `CachedFileSystemStorage` 已提供 81.6% 命中率**, 远超 30% 阈值。
2. **mtime + size 验证** 确保缓存一致性 — 文件修改后自动失效。
3. **deepcopy 隔离** 确保安全 — 返回值修改不影响缓存。
4. **LRU 256 entries** 足够覆盖典型项目 (通常 < 50 个文件)。
5. **`_ChapterDataCache`** 在 per-request 层面进一步减少重复读取。

### 不加新 ArtifactCache 的原因

- 新 dict cache 没有 mtime/deepcopy 失效策略, 可能引入 stale 数据 bug。
- 现有缓存已覆盖 81.6% 的读取, 边际收益低。
- 额外缓存层增加复杂度和维护成本。

---

## 4. 证据

- `.omo/evidence/perf-9-baseline.txt` — baseline 测量输出
- `.omo/evidence/perf-9-decision-gate.txt` — 完整测试输出含决策
- `tests/perf/test_artifact_cache_baseline.py` — 基准测试代码

---

## 5. 结论

**不需要添加新的 ArtifactCache 层。** 现有 `CachedFileSystemStorage` (mtime + size 验证, LRU 256, deepcopy 隔离) 已提供 81.6% 的缓存命中率, 足以支撑连续章节生成的性能需求。
