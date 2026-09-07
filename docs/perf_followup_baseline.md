# 后续章节性能基准口径

本轮优化以减少冗余 LLM 调用和重复 artifact 读取为主要验收目标，不承诺固定墙钟时间。

## 采集命令

```bash
.venv/bin/python scripts/benchmark_chapter_pipeline.py --project-root data/<project_id>
```

如果没有稳定真实项目，可先使用 mock provider 跑一章，再对该项目日志执行上述脚本。

## 重点指标

- `report_refreshes.requested` / `report_refreshes.reused` / `report_refreshes.refresh_required`
- `llm_calls.started` / `llm_calls.succeeded` / `llm_calls.failed`
- `tokens.prompt` / `tokens.completion` / `tokens.total`
- `storage_reads.load_json` / `storage_writes.save_json`
- `repair_rounds`
- `phase_timings.review` / `phase_timings.repair` / `phase_timings.finalize`

## 验收标准

- 正文与 context hash 未变时，alignment / continuity / causal / reading_power 报告优先复用。
- 正文或新式 context hash 变化时，报告必须刷新，不允许绕过归档前硬门禁。
- mock provider 下重复报告刷新和重复反序列化次数应低于优化前同等流程。
- 真实 provider 只作为延迟参考，验收以冗余调用和 IO 计数下降为准。
