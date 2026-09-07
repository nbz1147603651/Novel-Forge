import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("./OverlaySurface", () => ({
  OverlaySurface: ({ children }: { readonly children: React.ReactNode }) => <div>{children}</div>,
}));

import { WorkflowErrorLogDialog } from "./WorkflowErrorLogDialog";

const acknowledgeErrors = async () => ({
  status: "acknowledged" as const,
  message: "已确认处理。",
  updatedErrorEntryIds: [],
});
const reopenErrors = async () => ({
  status: "reopened" as const,
  message: "已重新打开。",
  updatedErrorEntryIds: [],
});
const clearClosedErrors = async () => ({
  status: "cleared" as const,
  message: "已清理。",
  clearedTaskIds: [],
  clearedErrorEntryIds: [],
  retainedPendingErrorEntryIds: [],
});

describe("WorkflowErrorLogDialog", () => {
  it("renders a durable backend diagnostic with its run-log location", () => {
    const html = renderToStaticMarkup(
      <WorkflowErrorLogDialog
        entries={[{
          id: "failed-init",
          timeLabel: "2026-08-01 13:39:44",
          jobLabel: "长篇立项 · 眠咒",
          taskId: "task-17",
          taskLabel: "init_story_bible",
          attemptLabel: "2/2",
          errorMessage: "模型服务不可用",
          excerpt: "AuthenticationError: key rejected",
          logPath: "data/sleep-spell/logs/run-17",
          kindLabel: "任务失败",
          autoResolved: false,
          causeCode: "provider_unavailable",
          autoRepairState: "retryable",
          autoRepairExplanation: "模型服务未完成请求；这不是正文修复问题。",
          recommendedAction: "检查路由与网络后重试。",
          recoveryActionKinds: ["retry"],
        }, {
          id: "failed-init-retry",
          timeLabel: "2026-08-01 13:41:09",
          jobLabel: "长篇立项 · 眠咒",
          taskId: "task-17",
          taskLabel: "init_story_bible",
          attemptLabel: "2/2",
          errorMessage: "模型服务不可用",
          excerpt: "AuthenticationError: key rejected",
          logPath: "data/sleep-spell/logs/run-17-retry",
          kindLabel: "任务失败",
          autoResolved: false,
          causeCode: "provider_unavailable",
          autoRepairState: "retryable",
          autoRepairExplanation: "模型服务未完成请求；这不是正文修复问题。",
          recommendedAction: "检查路由与网络后重试。",
          recoveryActionKinds: ["retry"],
        }]}
        onAcknowledge={acknowledgeErrors}
        onClearClosed={clearClosedErrors}
        onClose={vi.fn()}
        onReopen={reopenErrors}
      />,
    );

    expect(html).toContain("任务流错误日志 · 2 条");
    expect(html).toContain("模型服务不可用");
    expect(html).toContain("data/sleep-spell/logs/run-17");
    expect(html).toContain("不删除完整运行日志");
    expect(html).toContain("待确认 2");
    expect(html).toContain("逐条选择（已选 0/2）");
    expect(html).toContain('aria-label="选择第 1 条模型 / 网络服务错误"');
    expect(html).toContain('aria-label="选择第 2 条模型 / 网络服务错误"');
    expect(html).toContain("可安全重试");
    expect(html).toContain("模型服务未完成请求；这不是正文修复问题。");
    expect(html).toContain("检查路由与网络后重试。");
    expect(html).toContain("可用恢复");
  });

  it("offers scoped cleanup only for closed diagnostics", () => {
    const html = renderToStaticMarkup(
      <WorkflowErrorLogDialog
        entries={[{
          id: "handled-error",
          timeLabel: "2026-08-01 13:39:44",
          jobLabel: "长篇立项 · 眠咒",
          taskId: "task-17",
          taskLabel: "init_story_bible",
          attemptLabel: "2/2",
          errorMessage: "模型服务不可用",
          excerpt: "",
          logPath: "data/sleep-spell/logs/run-17",
          kindLabel: "任务失败",
          autoResolved: false,
          acknowledgedAt: "2026-08-01T13:45:00Z",
        }, {
          id: "pending-error",
          timeLabel: "2026-08-01 13:41:09",
          jobLabel: "长篇立项 · 眠咒",
          taskId: "task-18",
          taskLabel: "init_story_bible",
          attemptLabel: "2/2",
          errorMessage: "另一个待确认错误",
          excerpt: "",
          logPath: "data/sleep-spell/logs/run-18",
          kindLabel: "任务失败",
          autoResolved: false,
        }]}
        onAcknowledge={acknowledgeErrors}
        onClearClosed={clearClosedErrors}
        onClose={vi.fn()}
        onReopen={reopenErrors}
      />,
    );

    expect(html).toContain("清理已闭环 (1)");
    expect(html).not.toContain("清理已闭环 (2)");
    expect(html).toContain("完整运行日志仍保留");
  });
});
