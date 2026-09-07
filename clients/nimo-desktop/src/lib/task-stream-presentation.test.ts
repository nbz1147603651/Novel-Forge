import { describe, expect, it } from "vitest";

import type { TaskStreamState } from "./task-stream";
import {
  displayStepLabel,
  formatInitLongStepLabel,
  taskStreamEventLabel,
  taskStreamStatusLabel,
} from "./task-stream-presentation";

describe("task stream presentation", () => {
  it("keeps source-facing status and segment labels transport-neutral", () => {
    expect(taskStreamStatusLabel("streaming")).toBe("输出中");
    expect(taskStreamStatusLabel("completed")).toBe("本批完成");
    expect(taskStreamStatusLabel("completed", "running")).toBe("任务仍在进行");
    expect(taskStreamEventLabel("reasoning")).toBe("思考");
    expect(taskStreamEventLabel("system")).toBe("系统");
  });

  it("formats adjudication batch detail with stage and verdict", () => {
    expect(
      formatInitLongStepLabel("adjudicate_init_conflict_candidates_73_80", {
        stage: "contract_coherence",
        batch: 7,
        batch_total: 7,
        issues: 0,
        verdict: "accept",
        max_parallel: 2,
      }),
    ).toBe("冲突候选裁判  ·  当前层：章节契约  ·  批次 7 / 7  ·  问题 0  ·  判定：通过");
  });

  it("formats claims extraction and recheck chunks", () => {
    expect(
      formatInitLongStepLabel("extract_init_coherence_claims", {
        artifact: "chapter_contracts",
        claims: 80,
        extraction_mode: "stream",
      }),
    ).toBe("一致性 Claims 抽取  ·  当前检查：章节契约  ·  已抽取 80 条");
    expect(
      formatInitLongStepLabel("init_coherence_recheck_chunk_start", {
        stage: "contract_coherence",
        chunk_index: 6,
        chunk_count: 12,
        focus_chapters: [25],
      }),
    ).toBe("一致性复查  ·  当前层：章节契约  ·  分块 6 / 12  ·  聚焦第 25 章");
  });

  it("passes engine-formatted Chinese labels through unchanged", () => {
    const stream: TaskStreamState = {
      taskId: "t-1",
      title: "长篇立项",
      stepLabel: "冲突候选裁判 · 当前层：章节契约 · 批次 3/7 · 判定：通过",
      stepId: "adjudicate_init_conflict_candidates_25_36",
      status: "streaming",
      jobState: "running",
      progressPercent: 40,
      events: [],
    };
    expect(displayStepLabel(stream)).toBe("冲突候选裁判 · 当前层：章节契约 · 批次 3/7 · 判定：通过");
  });

  it("falls back to the local formatter for raw engine keys", () => {
    const stream: TaskStreamState = {
      taskId: "t-1",
      title: "长篇立项",
      stepLabel: "adjudicate_init_conflict_candidates_25_36",
      stepId: "adjudicate_init_conflict_candidates_25_36",
      status: "streaming",
      jobState: "running",
      progressPercent: 40,
      events: [],
    };
    expect(displayStepLabel(stream)).toBe("冲突候选裁判");
  });
});
