import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LocaleProvider } from "../lib/i18n";
import { RunInsightStrip } from "./RunInsightStrip";

const insights = [
  {
    id: "intent",
    status: "success" as const,
    label: "后端标签不会覆盖 locale",
    summary: "用户意图约束已通过",
    detail: "人物与结局未被改写。",
    count: 0,
    artifactStepKey: "",
  },
  {
    id: "research",
    status: "skipped" as const,
    label: "研究与证据",
    summary: "本章无事实缺口",
    detail: "未发起 MCP 调用。",
    count: 0,
    artifactStepKey: "chapter_research",
  },
  {
    id: "revision",
    status: "rolled_back" as const,
    label: "修订与回滚",
    summary: "已保留安全版本",
    detail: "修订出现回归。",
    count: 1,
    artifactStepKey: "",
  },
  {
    id: "final_verify",
    status: "pending" as const,
    label: "最终验证",
    summary: "等待最终验证",
    detail: "",
    count: 0,
    artifactStepKey: "",
  },
];

describe("RunInsightStrip", () => {
  it("renders four localized Engine-owned status cards without internal payloads", () => {
    const html = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <RunInsightStrip insights={insights} />
      </LocaleProvider>,
    );

    expect(html).toContain('aria-label="Chapter run transparency"');
    expect(html).toContain("Intent Guard");
    expect(html).toContain("Research");
    expect(html).toContain("Rolled back");
    expect(html).toContain("Final Verify");
    expect(html).not.toContain("后端标签不会覆盖 locale");
  });
});
