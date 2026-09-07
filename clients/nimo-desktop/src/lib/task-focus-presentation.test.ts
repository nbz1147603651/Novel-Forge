import { describe, expect, it } from "vitest";

import type { JobView } from "@nimo/engine-contracts";

import type { TaskStreamState } from "./task-stream";
import { deriveTaskFocusPresentation, isRedundantDisplayText } from "./task-focus-presentation";

const job: JobView = {
  id: "job-1",
  label: "长篇立项 · 青瓦梦起",
  projectId: "qingwa",
  state: "running",
  currentStep: "blueprint",
  stepLabel: "叙事蓝图 · 批次 3/7",
  progressPercent: 48,
  detail: "",
};

function stream(events: TaskStreamState["events"]): TaskStreamState {
  return {
    taskId: job.id,
    title: job.label,
    stepLabel: job.stepLabel ?? job.currentStep,
    stepId: job.currentStep,
    status: "streaming",
    jobState: "running",
    progressPercent: 48,
    events,
  };
}

describe("task focus presentation", () => {
  it("treats punctuation-only title differences as the same visible label", () => {
    expect(isRedundantDisplayText("一致性画像", "一致性画像")).toBe(true);
    expect(isRedundantDisplayText("一致性 · 画像", "一致性画像")).toBe(true);
    expect(isRedundantDisplayText("一致性画像", "冲突候选裁判")).toBe(false);
  });

  it("merges token deltas into one readable preview and character count", () => {
    const presentation = deriveTaskFocusPresentation(job, stream([
      { streamId: "s1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
      { streamId: "s1", sequence: 2, kind: "delta", segment: "content", text: "在雨里" },
    ]));

    expect(presentation.contentPreview).toBe("灰瓦在雨里");
    expect(presentation.progressCaption).toBe("5 字输出");
    expect(presentation.renderKindLabel).toBeNull();
  });

  it("keeps prose primary while exposing active reasoning as secondary state", () => {
    const presentation = deriveTaskFocusPresentation(job, stream([
      { streamId: "s1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦在雨里。" },
      { streamId: "s1", sequence: 2, kind: "delta", segment: "reasoning", text: "核对场景因果。" },
    ]));

    expect(presentation.activityLabel).toBe("思考中");
    expect(presentation.contentPreview).toBe("灰瓦在雨里。");
    expect(presentation.reasoningPreview).toBe("核对场景因果。");
  });

  it("uses the durable validation verdict and full snapshot length at a paused checkpoint", () => {
    const pausedJob: JobView = {
      ...job,
      state: "paused",
      decisions: [{
        id: "approve-plan",
        decisionId: "plan_checkpoint",
        label: "接受方案",
        description: "确认后继续。",
        requiresExplicitApproval: true,
        approvalVersion: "approval-v1",
      }],
    };
    const pausedStream: TaskStreamState = {
      ...stream([]),
      status: "paused",
      jobState: "paused",
      summary: { outputKind: "json", outputCharacters: 23_938 },
      events: [{
        streamId: "plan",
        sequence: 1,
        kind: "validation",
        segment: "content",
        outputKind: "json",
        text: '{"scene_intents":[{"summary":"截取预览"}',
        textMode: "snapshot",
        textLength: 23_938,
        textTruncated: true,
        validationStatus: "validated",
      }],
    };

    const presentation = deriveTaskFocusPresentation(pausedJob, pausedStream);
    expect(presentation.activityLabel).toBe("结果已校验");
    expect(presentation.progressCaption).toBe("等待你的确认");
    expect(presentation.contentLive).toBe(false);
    expect(presentation.contentSettled).toBe(true);
    expect(presentation.contentTextLength).toBe(23_938);
    expect(presentation.contentTextTruncated).toBe(true);
    expect(presentation.validationStatus).toBe("validated");
    expect(presentation.contentStatus).toBe("completed");
  });

  it("labels a stopped structured fragment without a verdict as unverified", () => {
    const pausedStream: TaskStreamState = {
      ...stream([]),
      status: "paused",
      jobState: "paused",
      summary: { outputKind: "json" },
      events: [{
        streamId: "plan",
        sequence: 1,
        kind: "stream_end",
        segment: "content",
        outputKind: "json",
        text: '{"verdict":"unknown"}',
        textMode: "snapshot",
      }],
    };

    const presentation = deriveTaskFocusPresentation({ ...job, state: "paused" }, pausedStream);
    expect(presentation.activityLabel).toBe("待核验");
    expect(presentation.contentStatus).toBe("unverified");
    expect(presentation.contentSettled).toBe(true);
    expect(presentation.tickerText).toContain("等待后端校验结论");
  });
});
