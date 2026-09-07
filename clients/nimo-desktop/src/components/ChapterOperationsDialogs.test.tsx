import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("./OverlaySurface", () => ({
  OverlaySurface: ({ children }: { readonly children: React.ReactNode }) => <div>{children}</div>,
}));

import { ChapterCleanDialog } from "./ChapterOperationsDialogs";

describe("ChapterCleanDialog", () => {
  it("previews the destructive range and preserved evidence", () => {
    const html = renderToStaticMarkup(
      <ChapterCleanDialog
        chapters={[
          { number: 1, title: "起", state: "completed", detail: "已完成" },
          { number: 2, title: "承", state: "needs_decision", detail: "待确认" },
          { number: 3, title: "转", state: "pending", detail: "待写" },
          { number: 4, title: "合", state: "pending", detail: "待写" },
        ]}
        defaultCutoff={2}
        maxChapter={4}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(html).toContain("预计影响第 2–4 章，共 3 章");
    expect(html).toContain("其中 1 章有已归档、进行中或待确认状态");
    expect(html).toContain("错误日志与费用证据仍保留");
  });
});
